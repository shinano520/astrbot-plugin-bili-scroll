"""评论生成：先归纳风格，再生成，最后自检。宁可不出手，也不发机器人腔。"""

from __future__ import annotations

import random
from typing import Any

from ..infra.logger import setup
from .constants import (
    BANNED_COMMENT_PHRASES,
    COMMENT_MAX_LEN,
    COMMENT_MAX_REWRITES,
    COMMENT_MIN_LEN,
    COMMENT_SELF_CHECK_THRESHOLD,
    COMMENT_STYLE_SAMPLE_SIZE,
)
from .models import Video
from .policy import validate_comment

log = setup("bili_scroll.style")


def dedupe_bots(samples: list[str]) -> list[str]:
    """去掉模板化/重复的评论。"""
    seen_exact: set[str] = set()
    seen_prefix: set[str] = set()
    out: list[str] = []

    for raw in samples:
        text = (raw or "").strip()
        if not text:
            continue
        if text in seen_exact:
            continue
        prefix = text[:6]
        if prefix and prefix in seen_prefix:
            continue
        seen_exact.add(text)
        if prefix:
            seen_prefix.add(prefix)
        out.append(text)

    return out


def append_at(text: str, users: list[str], probability: float, max_at: int = 0) -> str:
    """按概率在评论末尾追加 @。

    max_at=0 表示不限制：你勾了几个就 @ 几个（默认行为）。
    传正数才截断，但永远按配置名单来，不会 @ 到名单外的人。
    """
    if not text or not users or probability <= 0:
        return text
    if random.random() >= probability:
        log.info("[@] 本次未命中概率（%.0f%%），不 @", probability * 100)
        return text

    clean = [u.strip().lstrip("@") for u in users if u and u.strip()]
    if not clean:
        return text

    if max_at and max_at > 0 and len(clean) > max_at:
        picked = random.sample(clean, max_at)
        log.info("[@] 名单 %d 人，按上限只取 %d 人：%s", len(clean), max_at, "、".join(picked))
    else:
        picked = list(clean)
        log.info("[@] 名单 %d 人，全部 @：%s", len(picked), "、".join(picked))

    ats = " ".join(f"@{u}" for u in picked)
    # B 站服务端按文本解析 @，末尾留空格更保险（否则最后一个昵称可能被吞）
    return f"{text} {ats} "


async def make_comment(
    video: Video,
    *,
    bili: Any,
    llm: Any,
    reason: str,
    umo: str | None = None,
    max_rewrites: int = COMMENT_MAX_REWRITES,
    threshold: float = COMMENT_SELF_CHECK_THRESHOLD,
    sample_size: int = COMMENT_STYLE_SAMPLE_SIZE,
) -> str | None:
    """返回合格评论，或 None（宁可不发）。"""
    log.info("[评论] 开始 bvid=%s", video.bvid)
    try:
        raw_samples = await bili.get_comments(video.bvid, limit=sample_size)
    except Exception as e:
        log.info("拉评论失败，跳过评论：%s", e)
        return None

    samples = dedupe_bots(raw_samples or [])
    log.info("[评论] 原始 %d 条，去重后 %d 条", len(raw_samples or []), len(samples))
    if len(samples) < 5:
        log.info("评论样本不足（%d 条），不生成评论", len(samples))
        return None

    try:
        style = await llm.induce_style(samples, umo=umo)
    except Exception as e:
        log.info("风格归纳失败：%s", e)
        style = {}

    for attempt in range(max_rewrites + 1):
        log.info("[评论] 第 %d 次生成", attempt + 1)
        try:
            text = await llm.write_comment(video, style, reason, umo=umo)
        except Exception as e:
            log.info("生成评论失败（第 %d 次）：%s", attempt + 1, e)
            continue

        log.info("[评论] 生成长度=%d 内容=%r", len(text or ""), (text or "")[:80])

        ok, why = validate_comment(
            text,
            min_len=COMMENT_MIN_LEN,
            max_len=COMMENT_MAX_LEN,
            banned=BANNED_COMMENT_PHRASES,
        )
        if not ok:
            log.info("评论校验未通过（%s），重写", why)
            continue

        try:
            score = await llm.self_check(text, style, umo=umo)
        except Exception:
            score = 0.0

        if score >= threshold:
            return text
        log.info("自检 %.2f < %.2f，重写", score, threshold)

    log.info("重写次数用尽，放弃评论（宁可不发）")
    return None
