"""推送编排：遍历订阅者，算概率，发，记窗口。"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any

from ..domain.audience import decide_push
from ..domain.models import DispatchResult, Verdict
from ..infra.logger import setup

log = setup("bili_scroll.push")


class PushService:
    def __init__(self, *, store: Any, notifier: Any, cfg: dict):
        self.store = store
        self.notifier = notifier
        self.cfg = cfg

    async def dispatch(
        self, verdicts: list[Verdict], video_index: dict[str, Any]
    ) -> DispatchResult:
        result = DispatchResult()

        if not verdicts:
            return result

        try:
            subs = await self.store.list_subscribers()
        except Exception as e:
            log.info("读取订阅者失败：%s", e)
            return result

        if not subs:
            log.info("没有订阅者，跳过推送")
            return result

        now_dt = datetime.now()
        window_min = int(self.cfg.get("feedback_window_minutes", 30))

        for sub in subs:
            is_group = sub.scope == "group"
            if is_group and not self.cfg.get("push_group_enable", False):
                continue
            if not is_group and not self.cfg.get("push_private_enable", True):
                continue

            base_prob = float(
                self.cfg.get(
                    "push_group_probability" if is_group else "push_private_probability",
                    0.3 if is_group else 0.6,
                )
            )
            cooldown_h = int(self.cfg.get("push_cooldown_hours", 6))

            try:
                sent_today = await self.store.sent_today(sub.scope, sub.target_id)
                last_sent = await self.store.last_sent_at(sub.scope, sub.target_id)
                profile = await self.store.get_profile(sub.scope, sub.target_id)
            except Exception as e:
                log.info("读取 %s 状态失败：%s", sub.target_id, e)
                continue

            for v in verdicts:
                video = video_index.get(v.bvid)
                if video is None:
                    continue

                decision = decide_push(
                    video,
                    sub,
                    profile,
                    base_prob=base_prob,
                    cooldown_hours=cooldown_h,
                    last_sent_at=last_sent,
                    sent_today=sent_today,
                    now_ts=int(time.time()),
                    now_dt=now_dt,
                )

                if not decision.should_send:
                    result.skipped_guard += 1
                    break

                if random.random() >= decision.probability:
                    result.skipped_roll += 1
                    continue

                ok = await self.notifier.send_video(
                    scope=sub.scope,
                    target_id=sub.target_id,
                    video=video,
                    verdict=v,
                )
                if not ok:
                    continue

                try:
                    await self.store.log_push(
                        v.bvid,
                        sub.scope,
                        sub.target_id,
                        decision.probability,
                        window_min * 60,
                    )
                    await self.store.incr_push_quota(sub.scope, sub.target_id)
                except Exception as e:
                    log.info("记推送日志失败：%s", e)

                result.sent += 1
                break

        return result
