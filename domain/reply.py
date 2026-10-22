"""楼中楼回复：三条铁律的判定与生成。"""

from __future__ import annotations

from typing import Any

from ..infra.logger import setup
from .constants import REPLY_MAX_LEN, REPLY_MAX_PER_ROUND
from .models import Comment

log = setup("bili_scroll.reply")

REFUSE_PATTERNS = {
    "code": ("源码", "代码发", "给我代码", "开源地址给我"),
    "work": ("帮我写", "帮我做", "代做", "帮我搞"),
    "promise": ("以后都", "每天帮我", "保证", "一直帮我"),
    "quarrel": ("你是不是", "胡说", "垃圾", "就这水平"),
    "privacy": ("你叫什么", "住哪", "微信号", "账号密码", "手机号"),
}

USAGE_HINTS = (
    "怎么用",
    "怎么装",
    "如何用",
    "插件",
    "指令",
    "命令",
    "在哪",
    "怎么开",
    "配置",
)


def is_refused(text: str) -> tuple[bool, str]:
    body = (text or "").strip()
    for kind, patterns in REFUSE_PATTERNS.items():
        if any(p in body for p in patterns):
            return True, kind
    return False, ""


def is_usage_question(text: str) -> bool:
    body = (text or "").strip()
    return any(h in body for h in USAGE_HINTS)


def should_reply(
    comment: Comment, *, require_usage: bool = True, max_len: int = REPLY_MAX_LEN
) -> tuple[bool, str]:
    """三条铁律：1 直接回在我下面 2 问的是插件用法 3 一两句话能答清。"""
    if not comment.is_direct_reply_to_me:
        return False, "not_direct"

    refused, why = is_refused(comment.content)
    if refused:
        return False, f"refused:{why}"

    if require_usage and not is_usage_question(comment.content):
        return False, "not_usage"

    if len(comment.content) > 200:
        return False, "too_long_question"

    return True, "ok"


async def run_reply_round(
    *,
    bili: Any,
    llm: Any,
    store: Any,
    video_title_map: dict[str, str] | None = None,
    umo: str | None = None,
    max_per_round: int = REPLY_MAX_PER_ROUND,
    max_len: int = REPLY_MAX_LEN,
    since_days: int = 7,
) -> int:
    """返回本轮实际回复条数。"""
    replied = 0
    titles = video_title_map or {}

    try:
        own = await store.list_own_comments(since_days=since_days)
    except Exception as e:
        log.info("读取自己评论失败：%s", e)
        return 0

    for oc in own:
        if replied >= max_per_round:
            break
        if oc.get("replied"):
            continue

        rpid = oc.get("rpid") or ""
        bvid = oc.get("bvid") or ""
        if not rpid or rpid == "unknown" or not bvid:
            continue

        try:
            replies = await bili.get_sub_replies(bvid, rpid)
        except Exception as e:
            log.info("拉楼中楼失败 %s: %s", rpid, e)
            continue

        handled = False
        for c in replies:
            ok, _why = should_reply(c, max_len=max_len)
            if not ok:
                continue

            try:
                body = await llm.write_reply(c, titles.get(bvid, ""), umo=umo)
            except Exception as e:
                log.info("生成回复失败：%s", e)
                continue

            if not body or len(body) > max_len:
                continue

            try:
                new_rpid, root = await bili.post_comment(bvid, body, root=rpid, parent=c.rpid)
                await store.save_own_comment(new_rpid, root, bvid, body)
                replied += 1
                handled = True
                log.info("已回复 %s 下的 %s", rpid, c.rpid)
            except Exception as e:
                log.info("发送回复失败：%s", e)
                continue

            if replied >= max_per_round:
                break

        if handled:
            try:
                await store.mark_replied(rpid)
            except Exception:
                pass

    return replied


async def verify_comments(*, bili: Any, store: Any, limit: int = 20) -> int:
    """复核公开可见性。返回核对条数。"""
    checked = 0
    try:
        pending = await store.list_unverified()
    except Exception:
        return 0

    for oc in pending[:limit]:
        rpid = oc.get("rpid") or ""
        bvid = oc.get("bvid") or ""
        if not rpid or not bvid:
            continue
        try:
            found = await bili.find_comment(bvid, rpid, oc.get("root_rpid"))
        except Exception:
            continue
        try:
            await store.set_verified(rpid, 1 if found else -1)
        except Exception:
            pass
        checked += 1

    return checked
