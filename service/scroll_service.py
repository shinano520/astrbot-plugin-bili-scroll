"""一轮刷的主流程。任何单条失败都不能中断整轮。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from ..domain.constants import DEFAULT_KEYWORDS, ROUND_TIMEOUT_SECONDS, TRANSCRIBE_PER_ROUND
from ..domain.models import RoundResult, Verdict, Video
from ..infra.logger import setup, truncate
from ..infra.transcribe import get_text

log = setup("bili_scroll.scroll")


class RoundContext:
    def __init__(self) -> None:
        self.cancel = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def stop(self) -> None:
        self.cancel.set()


class ScrollService:
    def __init__(
        self,
        *,
        bili: Any,
        llm: Any,
        store: Any,
        cfg: dict,
        transcriber: Any = None,
        work_dir: Path | None = None,
    ):
        self.bili = bili
        self.llm = llm
        self.store = store
        self.cfg = cfg
        self.transcriber = transcriber
        self.work_dir = work_dir or Path("/tmp")

    async def run_round(
        self, ctx: RoundContext | None = None, umo: str | None = None
    ) -> RoundResult:
        ctx = ctx or RoundContext()
        started = time.monotonic()
        result = RoundResult()

        keyword_list = self.cfg.get("keywords") or list(DEFAULT_KEYWORDS)
        search_days = int(self.cfg.get("search_days", 7))
        quota_round = int(self.cfg.get("quota_per_round", 3))
        quota_total = int(self.cfg.get("max_per_day", 5))

        picked_today, _ = await self.store.quota_today()
        remaining = max(0, quota_total - picked_today)
        if remaining <= 0:
            result.error = "今日配额已用完"
            return result

        target = min(quota_round, remaining)

        candidates: list[Video] = []
        for kw in keyword_list:
            if ctx.cancelled:
                break
            try:
                found = await self.bili.search(kw, days=search_days, limit=20)
                candidates.extend(found)
            except Exception as e:
                log.info("搜索 %s 失败：%s", kw, truncate(str(e)))

        uniq: dict[str, Video] = {}
        for v in candidates:
            uniq.setdefault(v.bvid, v)

        fresh: list[Video] = []
        for v in uniq.values():
            try:
                if await self.store.is_seen(v.bvid):
                    continue
            except Exception:
                pass
            fresh.append(v)

        log.info(
            "搜索到 %d 条，去重后 %d 条，新视频 %d 条", len(candidates), len(uniq), len(fresh)
        )

        transcribed = 0
        for video in fresh:
            if ctx.cancelled:
                result.stopped = True
                break
            if len(result.picked) >= target:
                break
            if time.monotonic() - started > ROUND_TIMEOUT_SECONDS:
                result.error = "本轮超时"
                break

            try:
                text = await self._get_text(video, transcribed)
                if not text:
                    result.no_subtitle += 1
                    await self._mark_skip(video, "无字幕")
                    continue
                if len(text) != len(video.subtitle or ""):
                    transcribed += 1

                video = Video(
                    bvid=video.bvid,
                    title=video.title,
                    author=video.author,
                    pubdate=video.pubdate,
                    duration=video.duration,
                    desc=video.desc,
                    subtitle=text,
                    keywords=video.keywords,
                )

                verdict = await self.llm.judge(video, umo=umo)
                if verdict.worth:
                    result.picked.append(verdict)
                else:
                    result.skipped += 1

                await self.store.mark_seen(
                    verdict,
                    {
                        "title": video.title,
                        "author": video.author,
                        "pubdate": video.pubdate,
                        "keywords": list(video.keywords),
                    },
                )
            except Exception as e:
                log.info("处理 %s 失败：%s", video.bvid, truncate(str(e)))
                result.skipped += 1
                continue

        try:
            await self.store.incr_quota(picked=len(result.picked), rounds=1)
        except Exception:
            pass

        result.cost = getattr(self.llm, "cost", result.cost)
        result.elapsed = time.monotonic() - started
        return result

    async def _get_text(self, video: Video, transcribed_count: int) -> str:
        if video.subtitle and video.subtitle.strip():
            return video.subtitle

        enable = bool(self.cfg.get("transcribe_enable", False))
        if not enable or self.transcriber is None:
            return ""
        if transcribed_count >= TRANSCRIBE_PER_ROUND:
            return ""

        try:
            return await get_text(
                bvid=video.bvid,
                subtitle=video.subtitle,
                duration=video.duration,
                bili=self.bili,
                store=self.store,
                transcriber=self.transcriber,
                enabled=enable,
                work_dir=self.work_dir,
            )
        except Exception as e:
            log.info("转写 %s 失败：%s", video.bvid, e)
            return ""

    async def _mark_skip(self, video: Video, why: str) -> None:
        try:
            await self.store.mark_seen(
                Verdict(bvid=video.bvid, worth=False, reason=why),
                {
                    "title": video.title,
                    "author": video.author,
                    "pubdate": video.pubdate,
                    "keywords": list(video.keywords),
                },
            )
        except Exception:
            pass
