"""日志：敏感字段脱敏。"""

from __future__ import annotations

import logging
import re

MAX_LOG_BODY = 120

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


def setup(name: str = "bili_scroll") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
