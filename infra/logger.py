"""日志：敏感字段脱敏 + 环形缓冲（供 WebUI 实时日志页读）。"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import datetime

MAX_LOG_BODY = 120
RING_CAPACITY = 800

_SENSITIVE_PATTERNS = (
    re.compile(r"(SESSDATA=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(bili_jct=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(DedeUserID=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(buvid3=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*)[^\s,}\"']+", re.IGNORECASE),
    re.compile(r"(token[\"']?\s*[:=]\s*)[^\s,}\"']+", re.IGNORECASE),
)


def mask(text: str) -> str:
    """把 Cookie / token 一类的东西替换成 ***。"""
    if not text:
        return ""
    out = str(text)
    for pat in _SENSITIVE_PATTERNS:
        out = pat.sub(r"\1***", out)
    return out


def truncate(text: str, limit: int = MAX_LOG_BODY) -> str:
    body = mask(text or "").replace("\n", " ")
    if len(body) <= limit:
        return body
    return body[:limit] + "…"


class RingBuffer:
    """线程安全的日志环形缓冲。"""

    def __init__(self, capacity: int = RING_CAPACITY):
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()

    def append(self, record: logging.LogRecord) -> None:
        try:
            name = (record.name or "").replace("bili_scroll", "").lstrip(".") or "core"
            msg = mask(record.getMessage())
            if record.exc_info:
                msg = f"{msg} | {record.exc_info[1]}"
            with self._lock:
                self._seq += 1
                self._buf.append(
                    {
                        "seq": self._seq,
                        "t": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                        "lv": record.levelname,
                        "src": name,
                        "msg": msg,
                    }
                )
        except Exception:
            pass

    def since(self, after: int = 0, limit: int = 300) -> list[dict]:
        with self._lock:
            items = [x for x in self._buf if x["seq"] > int(after)]
        if len(items) > limit:
            items = items[-limit:]
        return items

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


RING = RingBuffer()


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        RING.append(record)


def setup(name: str = "bili_scroll") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    # 给父 logger 挂环形缓冲（只挂一次），子 logger 全部汇进来
    root = logging.getLogger("bili_scroll")
    if not any(isinstance(h, _RingHandler) for h in root.handlers):
        ring = _RingHandler()
        ring.setLevel(logging.DEBUG)
        root.addHandler(ring)
        root.setLevel(logging.DEBUG)

    return logger
