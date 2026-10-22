"""偏好画像：权重更新、衰减、匹配。"""

from __future__ import annotations

from .constants import (
    PROFILE_DECAY_DAYS,
    PROFILE_MANUAL_DELTA,
    PROFILE_MAX_KEYWORDS,
    PROFILE_NEGATIVE_DELTA,
    PROFILE_POSITIVE_DELTA,
)
from .models import FeedbackKind, Profile


def delta_for_feedback(kind: FeedbackKind) -> float:
    if kind in (FeedbackKind.POSITIVE, FeedbackKind.FOLLOWUP):
        return PROFILE_POSITIVE_DELTA
    if kind == FeedbackKind.NEGATIVE:
        return PROFILE_NEGATIVE_DELTA
    return 0.0


def should_update(kind: FeedbackKind) -> bool:
    return delta_for_feedback(kind) != 0.0


def apply_feedback(
    profile: Profile, keywords: tuple[str, ...], kind: FeedbackKind
) -> list[tuple[str, float]]:
    """返回需要写库的 (keyword, 新权重) 列表。纯计算。"""
    delta = delta_for_feedback(kind)
    if delta == 0.0 or not keywords:
        return []

    updated: list[tuple[str, float]] = []
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        new_weight = profile.entries.get(kw, 0.0) + delta
        profile.entries[kw] = new_weight
        updated.append((kw, new_weight))
    return updated


def apply_manual_preference(
    profile: Profile, keyword: str
) -> tuple[str, float] | None:
    kw = (keyword or "").strip()
    if not kw:
        return None
    new_weight = profile.entries.get(kw, 0.0) + PROFILE_MANUAL_DELTA
    profile.entries[kw] = new_weight
    return kw, new_weight


def decay_days() -> int:
    return PROFILE_DECAY_DAYS


def max_keywords() -> int:
    return PROFILE_MAX_KEYWORDS
