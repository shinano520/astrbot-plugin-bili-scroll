"""指令处理。"""

from __future__ import annotations

from typing import Any

from ..domain.constants import (
    CMD_REPORT,
    CMD_RUN,
    CMD_SELFCHECK,
    CMD_STATUS,
    CMD_STOP,
    CMD_SUBSCRIBE,
    CMD_UNSUBSCRIBE,
)

HELP_TEXT = """用法：/bili 跑|报告|停|自检|状态|订阅|退订

跑       立刻刷一轮
报告     看今天的日报
停       停掉正在跑的一轮
自检     检查登录/搜索/字幕/模型
状态     配额、下次执行、token 用量
订阅     接收推荐
退订     不再接收"""


class Commands:
    """不直接操作 Star，只把请求转给 main 里的服务集合。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def handle(self, action: str, event: Any) -> str:
        act = (action or "").strip()

        if not act:
            return HELP_TEXT

        if act == CMD_RUN:
            return await self.plugin.cmd_run(event)
        if act == CMD_REPORT:
            return await self.plugin.cmd_report(event)
        if act == CMD_STOP:
            return await self.plugin.cmd_stop(event)
        if act == CMD_SELFCHECK:
            return await self.plugin.cmd_selfcheck(event)
        if act == CMD_STATUS:
            return await self.plugin.cmd_status(event)
        if act == CMD_SUBSCRIBE:
            return await self.plugin.cmd_subscribe(event)
        if act == CMD_UNSUBSCRIBE:
            return await self.plugin.cmd_unsubscribe(event)

        return HELP_TEXT


def scope_of(event: Any) -> tuple[str, str]:
    """从事件里取 (scope, target_id)。"""
    try:
        gid = event.get_group_id()
        if gid:
            return "group", str(gid)
    except Exception:
        pass
    try:
        sid = event.get_sender_id()
        if sid:
            return "private", str(sid)
    except Exception:
        pass
    return "private", ""


def umo_of(event: Any) -> str:
    try:
        return event.unified_msg_origin or ""
    except Exception:
        return ""
