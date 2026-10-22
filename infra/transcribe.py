"""本地转写。faster-whisper 懒加载，失败即降级为跳过。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..domain.constants import TRANSCRIBE_ITEM_TIMEOUT, TRANSCRIBE_MAX_DURATION
from ..infra.logger import setup

log = setup("bili_scroll.transcribe")


class Transcriber:
    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model = None
        self._load_failed = False

    def _lazy_load(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            log.info("转写模型已加载：%s", self.model_size)
            return True
        except Exception as e:
            log.info("转写模型加载失败，降级为跳过无字幕视频：%s", e)
            self._load_failed = True
            return False

    async def transcribe(self, audio_path: Path, duration: int = 0) -> str:
        if not self._lazy_load():
            return ""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._sync_transcribe, audio_path),
                timeout=TRANSCRIBE_ITEM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.info("转写超时（%ss），放弃", TRANSCRIBE_ITEM_TIMEOUT)
            return ""
        except Exception as e:
            log.info("转写失败：%s", e)
            return ""

    def _sync_transcribe(self, audio_path: Path) -> str:
        segments, _info = self._model.transcribe(str(audio_path), language="zh", vad_filter=True)
        return "".join(seg.text for seg in segments).strip()


async def get_text(
    *,
    bvid: str,
    subtitle: str,
    duration: int,
    bili,
    store,
    transcriber: Transcriber | None,
    enabled: bool,
    max_duration: int = TRANSCRIBE_MAX_DURATION,
    work_dir: Path | None = None,
) -> str:
    """有字幕用字幕，没字幕按配置转写。返回文本，失败返回空串。"""
    if subtitle and subtitle.strip():
        return subtitle

    if not enabled or transcriber is None:
        return ""

    try:
        cached = await store.get_transcription(bvid)
        if cached:
            return cached
    except Exception:
        pass

    if duration > max_duration:
        log.info("%s 时长 %ss 超过上限，跳过转写", bvid, duration)
        return ""

    work_dir = work_dir or Path("/tmp")
    audio_path = work_dir / f"{bvid}.m4a"

    try:
        ok = await bili.download_audio(bvid, audio_path)
        if not ok:
            return ""
        text = await transcriber.transcribe(audio_path, duration)
        if not text:
            return ""
        try:
            await store.save_transcription(bvid, duration, text, transcriber.model_size)
        except Exception:
            pass
        return text
    finally:
        try:
            if audio_path.exists():
                audio_path.unlink()
        except OSError:
            pass
