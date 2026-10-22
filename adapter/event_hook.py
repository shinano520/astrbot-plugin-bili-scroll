"""消息事件钩子：收集反馈、识别订阅/退订。"""

from __future__ import annotations

import json
import time
from typing import Any

from ..domain.constants import DEFAULT_OPT_IN_PHRASES, DEFAULT_OPT_OUT_PHRASES
from ..domain.feedback import classify_by_rules, is_opt_in, is_opt_out
from ..domain.models import FeedbackKind
from ..domain.policy import feedback_weight
from ..domain.profile import apply_feedback, should_update
from ..infra.logger import setup, truncate

log = setup("bili_scroll.hook")


class EventHook:
    def __init__(self, *, store: Any, llm: Any, cfg: dict):
        self.store = store
        self.llm = llm
        self.cfg = cfg

    async def handle_message(
        self,
        *,
        scope: str,
        target_id: str,
        text: str,
        umo: str | None = None,
        at_bot: bool = True,
    ) -> str | None:
        """返回需要立即回复的文本（订阅/退订确认），否则 None。

        群聊订阅必须 @ 机器人（at_bot=True）才生效，
        否则群里任何人说一句「订阅b站视频」就打开了推送。
        退订不受限：谁都能随时退。
        """
        body = (text or "").strip()
        if not body:
            return None

        opt_in_phrases = tuple(self.cfg.get("opt_in_phrases") or DEFAULT_OPT_IN_PHRASES)
        opt_out_phrases = tuple(self.cfg.get("opt_out_phrases") or DEFAULT_OPT_OUT_PHRASES)

        # 退订：任何时候都认
        if is_opt_out(body, opt_out_phrases):
            await self.store.upsert_subscriber(scope, target_id, opted_in=False)
            log.info("%s:%s 已退订", scope, target_id)
            return "行，不发了。"

        # 订阅：群聊必须 @ 机器人
        if is_opt_in(body, opt_in_phrases):
            if scope == "group" and not at_bot:
                log.info("群 %s 的订阅被忽略（没 @ 机器人）", target_id)
                return "要订阅的话，@我 再说一句「订阅b站视频」。"
            await self.store.upsert_subscriber(scope, target_id, opted_in=True)
            log.info("%s:%s 已订阅", scope, target_id)
            return "行，刷到有意思的发你。"

        await self._collect_feedback(scope, target_id, body, umo)
        return None

    async def _collect_feedback(
        self, scope: str, target_id: str, text: str, umo: str | None
    ) -> None:
        try:
            window = await self.store.pending_window(scope, target_id, int(time.time()))
        except Exception:
            return
        if not window:
            return

        kind = classify_by_rules(text)
        if kind is None:
            try:
                kind = await self.llm.classify_feedback(text, window.get("bvid", ""), umo=umo)
            except Exception as e:
                log.info("反馈分类失败，按中性处理：%s", truncate(str(e)))
                kind = FeedbackKind.NEUTRAL

        weight = feedback_weight(kind)
        try:
            await self.store.close_window(int(window["id"]), kind.value, weight)
        except Exception as e:
            log.info("关闭反馈窗口失败：%s", e)
            return

        if not should_update(kind):
            return

        try:
            bvid = window.get("bvid") or ""
            keywords = await self._keywords_for(bvid)
            if not keywords:
                return
            profile = await self.store.get_profile(scope, target_id)
            updates = apply_feedback(profile, keywords, kind)
            for kw, _w in updates:
                await self.store.add_weight(scope, target_id, [kw], weight / 2.0)
            await self.store.trim_profile(scope, target_id, 50)
            log.info("%s:%s 反馈 %s，关键词 %s", scope, target_id, kind.value, keywords)
        except Exception as e:
            log.info("更新画像失败：%s", e)

    async def _keywords_for(self, bvid: str) -> tuple[str, ...]:
        try:
            async with self.store.db.execute(
                "SELECT keywords FROM seen WHERE bvid=?", (bvid,)
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            return ()
        if not row or not row["keywords"]:
            return ()
        try:
            values = json.loads(row["keywords"])
        except (json.JSONDecodeError, TypeError):
            return ()
        return tuple(str(v) for v in values if v)


async def expire_windows(store: Any) -> int:
    """把过期未回复的窗口记为 silence。"""
    now = int(time.time())
    try:
        rows = await store.expire_windows(now)
    except Exception:
        return 0
    count = 0
    for r in rows:
        try:
            await store.close_window(
                int(r["id"]), FeedbackKind.SILENCE.value, feedback_weight(FeedbackKind.SILENCE)
            )
            count += 1
        except Exception:
            continue
    return count
