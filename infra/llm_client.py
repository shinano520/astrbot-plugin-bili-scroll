"""LLM 适配层。只调用 AstrBot 已配置的 provider，不新增任何 Key。"""

from __future__ import annotations

import json
from typing import Any

from ..domain import prompt as P
from ..domain.constants import (
    BANNED_COMMENT_PHRASES,
    COMMENT_MAX_LEN,
    NEUTRAL_PERSONA,
    REPLY_MAX_LEN,
)
from ..domain.models import Comment, FeedbackKind, RoundCost, Verdict, Video
from ..infra.logger import mask, truncate


class LlmError(Exception):
    pass


class LlmClient:
    """包一层 AstrBot provider，负责 prompt 拼装与结果解析。"""

    def __init__(self, context: Any, persona: str = NEUTRAL_PERSONA, max_calls: int = 20):
        self._context = context
        self._persona = persona or NEUTRAL_PERSONA
        self._max_calls = max_calls
        self.cost = RoundCost()

    def reset_cost(self) -> None:
        self.cost = RoundCost()

    def _provider(self, umo: str | None = None):
        try:
            if umo:
                p = self._context.get_using_provider(umo)
                if p:
                    return p
        except Exception:
            pass
        try:
            return self._context.get_using_provider()
        except Exception as e:
            raise LlmError(f"没有可用的 LLM provider: {e}") from e

    async def _chat(self, system_prompt: str, user_prompt: str, umo: str | None = None) -> str:
        if self.cost.llm_calls >= self._max_calls:
            raise LlmError(f"本轮 LLM 调用已达上限 {self._max_calls}")

        provider = self._provider(umo)
        if provider is None:
            raise LlmError("provider 为空")

        try:
            resp = await provider.text_chat(
                system_prompt=system_prompt,
                contexts=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            raise LlmError(f"LLM 调用失败: {mask(str(e))}") from e

        self.cost.llm_calls += 1
        _accumulate_usage(self.cost, resp)

        text = getattr(resp, "completion_text", "") or ""
        return text.strip()

    async def judge(self, video: Video, umo: str | None = None) -> Verdict:
        user = P.build_judge_prompt(
            title=video.title,
            author=video.author,
            subtitle=video.subtitle,
            desc=video.desc,
        )
        raw = await self._chat(P.JUDGE_SYSTEM, user, umo)
        obj = P.safe_json_parse(raw)

        if not obj:
            return Verdict(
                bvid=video.bvid,
                worth=False,
                reason=f"判读输出无法解析: {truncate(raw, 80)}",
                one_liner="",
            )

        worth = bool(obj.get("worth", False))
        reason = str(obj.get("reason") or "").strip()[:200]
        one_liner = str(obj.get("one_liner") or "").strip()[:60]

        return Verdict(
            bvid=video.bvid,
            worth=worth,
            reason=reason or ("值得" if worth else "不值得"),
            one_liner=one_liner,
        )

    async def induce_style(self, samples: list[str], umo: str | None = None) -> dict:
        if not samples:
            return {}
        user = P.build_style_prompt(samples)
        raw = await self._chat(P.STYLE_SYSTEM, user, umo)
        obj = P.safe_json_parse(raw)
        if not obj:
            return {}
        cleaned: dict[str, Any] = {}
        for key in ("tone", "length", "punctuation", "emoji", "pattern"):
            if obj.get(key):
                cleaned[key] = str(obj[key])[:120]
        slang = obj.get("slang")
        if isinstance(slang, list):
            cleaned["slang"] = [str(s)[:20] for s in slang[:6]]
        avoid = obj.get("avoid")
        if isinstance(avoid, list):
            cleaned["avoid"] = [str(s)[:30] for s in avoid[:6]]
        return cleaned

    async def write_comment(
        self, video: Video, style: dict, reason: str, umo: str | None = None
    ) -> str:
        system = P.build_comment_system(self._persona, BANNED_COMMENT_PHRASES)
        user = P.build_comment_user(
            title=video.title,
            style_json=json.dumps(style or {}, ensure_ascii=False),
            reason=reason,
        )
        text = await self._chat(system, user, umo)
        return _clean_comment(text)

    async def self_check(self, text: str, style: dict, umo: str | None = None) -> float:
        user = P.build_self_check_prompt(
            text=text, style_json=json.dumps(style or {}, ensure_ascii=False)
        )
        raw = await self._chat(P.SELF_CHECK_SYSTEM, user, umo)
        obj = P.safe_json_parse(raw)
        try:
            return max(0.0, min(1.0, float(obj.get("score", 0.0))))
        except (TypeError, ValueError):
            return 0.0

    async def write_reply(
        self, comment: Comment, video_title: str = "", umo: str | None = None
    ) -> str:
        user = P.build_reply_prompt(comment.content, video_title)
        text = await self._chat(P.REPLY_SYSTEM, user, umo)
        body = text.strip().strip('"').strip("\u201c\u201d").strip()
        if len(body) > REPLY_MAX_LEN:
            body = body[:REPLY_MAX_LEN]
        return body

    async def classify_feedback(
        self, reply_text: str, video_title: str, umo: str | None = None
    ) -> FeedbackKind:
        user = P.build_feedback_prompt(reply_text, video_title)
        raw = await self._chat(P.FEEDBACK_SYSTEM, user, umo)
        obj = P.safe_json_parse(raw)
        kind = str(obj.get("kind") or "").strip().lower()
        mapping = {
            "positive": FeedbackKind.POSITIVE,
            "followup": FeedbackKind.FOLLOWUP,
            "neutral": FeedbackKind.NEUTRAL,
            "negative": FeedbackKind.NEGATIVE,
        }
        return mapping.get(kind, FeedbackKind.NEUTRAL)


def _clean_comment(text: str) -> str:
    body = (text or "").strip()
    for quote in ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019"):
        body = body.strip(quote)
    body = body.replace("\n", " ").strip()
    return body[:COMMENT_MAX_LEN]


def _accumulate_usage(cost: RoundCost, resp: Any) -> None:
    usage = getattr(resp, "usage", None) or {}
    if isinstance(usage, dict):
        try:
            cost.input_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            cost.output_tokens += int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        except (TypeError, ValueError):
            pass
    elif usage:
        try:
            cost.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            cost.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        except (TypeError, ValueError):
            pass
