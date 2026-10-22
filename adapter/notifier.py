"""消息出口。主动推送走 Context.send_message，不依赖事件上下文。"""

from __future__ import annotations

from typing import Any

from ..domain.models import Verdict, Video
from ..infra.logger import setup, truncate

log = setup("bili_scroll.notifier")


def build_umo(scope: str, target_id: str, platform: str = "aiocqhttp") -> str:
    """拼 AstrBot 的 unified message origin。"""
    kind = "GroupMessage" if scope == "group" else "FriendMessage"
    return f"{platform}:{kind}:{target_id}"


class Notifier:
    def __init__(self, context: Any, platform: str = "aiocqhttp"):
        self.context = context
        self.platform = platform

    async def send_text(self, umo: str, text: str) -> bool:
        try:
            from astrbot.core.message.message_event_result import MessageChain

            chain = MessageChain().message(text)
            return bool(await self.context.send_message(umo, chain))
        except Exception as e:
            log.info("发送失败 %s: %s", umo, truncate(str(e)))
            return False

    async def send_video(
        self, *, scope: str, target_id: str, video: Video, verdict: Verdict
    ) -> bool:
        umo = build_umo(scope, target_id, self.platform)
        text = format_video_message(video, verdict)
        return await self.send_text(umo, text)


def format_video_message(video: Video, verdict: Verdict) -> str:
    parts = [f"刷到个还行的：{video.title}"]
    if verdict.one_liner:
        parts.append(verdict.one_liner)
    elif verdict.reason:
        parts.append(verdict.reason)
    parts.append(f"https://www.bilibili.com/video/{video.bvid}")
    return "\n".join(parts)
