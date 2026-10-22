"""调度：工作时段 + 随机抖动 + 峰谷口径。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..domain.constants import PEAK_WINDOWS_WEEKDAY
from ..domain.policy import is_peak_hour
from ..infra.logger import setup

log = setup("bili_scroll.scheduler")


def parse_windows(spec: list[str] | str) -> list[tuple[int, int]]:
    """['12:00-14:00','18:00-09:00'] -> [(720,840),(1080,540)]"""
    if isinstance(spec, str):
        spec = [x.strip() for x in spec.split(",") if x.strip()]
    out: list[tuple[int, int]] = []
    for item in spec or []:
        try:
            start_s, end_s = item.split("-")
            sh, sm = (int(x) for x in start_s.strip().split(":"))
            eh, em = (int(x) for x in end_s.strip().split(":"))
            out.append((sh * 60 + sm, eh * 60 + em))
        except (ValueError, AttributeError):
            continue
    return out


def in_windows(dt: datetime, windows: list[tuple[int, int]]) -> bool:
    minutes = dt.hour * 60 + dt.minute
    for start, end in windows:
        if start <= end:
            if start <= minutes < end:
                return True
        else:
            if minutes >= start or minutes < end:
                return True
    return False


def _window_end(dt: datetime, windows: list[tuple[int, int]]) -> datetime:
    minutes = dt.hour * 60 + dt.minute
    for start, end in windows:
        if start <= end and start <= minutes < end:
            return dt.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if start > end and (minutes >= start or minutes < end):
            if minutes >= start:
                return (dt + timedelta(days=1)).replace(
                    hour=end // 60, minute=end % 60, second=0, microsecond=0
                )
            return dt.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    return dt


def next_run_at(
    *,
    windows: list[tuple[int, int]],
    jitter_minutes: int,
    now: datetime | None = None,
    peak_aware: bool = True,
    peak_windows: tuple[tuple[int, int], ...] = PEAK_WINDOWS_WEEKDAY,
    max_lookahead_days: int = 3,
) -> datetime | None:
    """在允许的时段内随机取一个时间点，避开高峰。找不到返回 None。"""
    now = now or datetime.now()
    cursor = now + timedelta(minutes=1)

    for _ in range(max_lookahead_days * 24 * 60):
        if in_windows(cursor, windows):
            if peak_aware and is_peak_hour(cursor, peak_windows):
                cursor += timedelta(minutes=1)
                continue

            window_end = _window_end(cursor, windows)
            span = int((window_end - cursor).total_seconds() // 60)
            if span <= 0:
                cursor += timedelta(minutes=1)
                continue

            offset = random.randint(0, max(0, span - 1))
            candidate = cursor + timedelta(minutes=offset)
            if jitter_minutes > 0:
                candidate += timedelta(minutes=random.randint(-jitter_minutes, jitter_minutes))
            if candidate <= now:
                cursor += timedelta(minutes=1)
                continue
            if peak_aware and is_peak_hour(candidate, peak_windows):
                cursor = candidate + timedelta(minutes=1)
                continue
            return candidate

        cursor += timedelta(minutes=1)

    return None
