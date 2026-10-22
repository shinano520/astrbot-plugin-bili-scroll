"""领域策略：判重、配额、反馈系数、好感度决策。全是纯函数。"""

from __future__ import annotations

from datetime import datetime

from .constants import (
    FEEDBACK_MULTIPLIER_MAX,
    FEEDBACK_MULTIPLIER_MIN,
    FEEDBACK_WEIGHTS,
    HARD_LIMITS,
    PROBABILITY_CAP,
)
from .models import FeedbackKind


def cooldown_factor(last_sent_at: int | None, now: int, cooldown_hours: int) -> float:
    """距上次发送的冷却系数，0..1。没发过返回 1。"""
    if last_sent_at is None:
        return 1.0
    elapsed_hours = max(0.0, (now - last_sent_at) / 3600.0)
    if cooldown_hours <= 0:
        return 1.0
    return max(0.0, min(1.0, elapsed_hours / cooldown_hours))


def feedback_multiplier(score: float) -> float:
    """累计反馈分 → 系数。0 分返回 1.0，正分放大，负分压制。"""
    # score 以 10 为满档映射到 [MIN, MAX]
    normalized = max(-10.0, min(10.0, score)) / 10.0  # -1..1
    if normalized >= 0:
        return 1.0 + normalized * (FEEDBACK_MULTIPLIER_MAX - 1.0)
    return 1.0 + normalized * (1.0 - FEEDBACK_MULTIPLIER_MIN)


def clamp_probability(p: float) -> float:
    return max(0.0, min(PROBABILITY_CAP, p))


def compute_push_probability(
    base_prob: float,
    match: float,
    cooldown: float,
    multiplier: float,
) -> float:
    """P = 基础概率 × 偏好匹配 × 冷却衰减 × 反馈系数。"""
    return clamp_probability(base_prob * match * cooldown * multiplier)


def in_quiet_hours(now: datetime, quiet_start: int, quiet_end: int) -> bool:
    """判断是否在免打扰时段。支持跨零点。"""
    hour = now.hour
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= hour < quiet_end
    return hour >= quiet_start or hour < quiet_end


def is_peak_hour(now: datetime, peak_windows: tuple[tuple[int, int], ...]) -> bool:
    """判断是否为 DeepSeek 高峰时段。周末全天不算高峰。"""
    if now.weekday() >= 5:  # 周六周日
        return False
    hour = now.hour
    return any(start <= hour < end for start, end in peak_windows)


def daily_limit_for(scope: str) -> int:
    """每日推送上限。硬编码，不读配置。"""
    if scope == "group":
        return HARD_LIMITS["group_per_day"]
    return HARD_LIMITS["private_per_day"]


def should_skip_by_guard(
    *,
    scope: str,
    sent_today: int,
    last_sent_at: int | None,
    now_ts: int,
    now_dt: datetime,
    quiet_start: int = HARD_LIMITS["quiet_start"],
    quiet_end: int = HARD_LIMITS["quiet_end"],
) -> tuple[bool, str]:
    """返回 (是否跳过, 原因)。"""
    if sent_today >= daily_limit_for(scope):
        return True, "daily_limit"

    cooldown_h = HARD_LIMITS["cooldown_hours"]
    if last_sent_at is not None and (now_ts - last_sent_at) < cooldown_h * 3600:
        return True, "cooldown"

    if in_quiet_hours(now_dt, quiet_start, quiet_end):
        return True, "quiet_hours"

    return False, ""


def feedback_weight(kind: FeedbackKind) -> float:
    return FEEDBACK_WEIGHTS.get(kind.value, 0.0)


def affection_actions(
    affection: int,
    *,
    like_threshold: int,
    coin_threshold: int,
    comment_threshold: int,
    coin_count: int,
) -> dict[str, int | bool]:
    """好感度 → 动作。"""
    return {
        "like": affection >= like_threshold,
        "coin": coin_count if affection >= coin_threshold else 0,
        "comment": affection >= comment_threshold,
    }


def has_oral_element(text: str) -> bool:
    """检查是否含至少一个口语元素：梗、语气词、省略号、反问。

    用于防止机器人腔。语气词表故意保守，避免误判。
    """
    particles = ("啊", "吧", "嘛", "诶", "哎", "咦", "嘿", "呀", "咯", "噢", "哦")
    if any(p in text for p in particles):
        return True
    if "..." in text or "…" in text:
        return True
    if "?" in text or "？" in text:
        return True
    # 常见网络梗/口语缩写
    slang = ("典", "绷", "属实", "这波", "离谱", "绝了", "好家伙", "笑死", "真行", "就这")
    if any(s in text for s in slang):
        return True
    return False


def validate_comment(
    text: str,
    *,
    min_len: int,
    max_len: int,
    banned: tuple[str, ...],
    require_oral: bool = True,
) -> tuple[bool, str]:
    """评论发出前的最后一道校验。返回 (通过, 原因)。"""
    body = (text or "").strip()
    if not body:
        return False, "empty"
    if len(body) < min_len:
        return False, "too_short"
    if len(body) > max_len:
        return False, "too_long"
    for phrase in banned:
        if phrase in body:
            return False, f"banned:{phrase}"
    if require_oral and not has_oral_element(body):
        return False, "no_oral_element"
    return True, "ok"
