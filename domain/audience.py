"""推送决策：纯函数，不发消息、不查库。"""

from __future__ import annotations

from datetime import datetime

from .models import Profile, PushDecision, Subscriber, Video
from .policy import (
    compute_push_probability,
    cooldown_factor,
    feedback_multiplier,
    should_skip_by_guard,
)


def decide_push(
    video: Video,
    sub: Subscriber,
    profile: Profile,
    *,
    base_prob: float,
    cooldown_hours: int,
    last_sent_at: int | None,
    sent_today: int,
    now_ts: int,
    now_dt: datetime,
) -> PushDecision:
    """综合五个因素给出推送决策。"""
    skip, reason = should_skip_by_guard(
        scope=sub.scope,
        sent_today=sent_today,
        last_sent_at=last_sent_at,
        now_ts=now_ts,
        now_dt=now_dt,
    )
    if skip:
        return PushDecision(should_send=False, probability=0.0, reason=reason)

    match = profile.match(video.keywords)
    if match <= 0.0:
        return PushDecision(should_send=False, probability=0.0, reason="no_match")

    cd = cooldown_factor(last_sent_at, now_ts, cooldown_hours)
    mult = feedback_multiplier(profile.score)
    p = compute_push_probability(base_prob, match, cd, mult)

    return PushDecision(
        should_send=p > 0.0,
        probability=p,
        matched_keywords=profile.matched_keywords(video.keywords),
        reason="ok",
    )
