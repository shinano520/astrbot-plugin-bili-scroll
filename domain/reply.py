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
    comment: Comment,
    *,
    scope: str = "related",
    max_len: int = REPLY_MAX_LEN,
) -> tuple[bool, str]:
    """判定要不要回。

    三条底线：
    1. 直接回在我那条评论下面
    2. 不属于六类拒答（要代码/作品/承诺/吵架/隐私）
    3. 一两句话能答清（长度可控）

    scope=related（默认）：聊视频内容、聊话题、问插件用法都回。
    scope=usage_only：只回问插件用法的。
    """
    if not comment.is_direct_reply_to_me:
        return False, "not_direct"

    # 只要对方直接回复了大肥鱼的评论，就回一条。
    # 拒答只保留真正危险的类别（要代码/作品/承诺/吵架/隐私），
    # scope/length 这类"礼貌性筛选"不再阻断回复。
    refused, why = is_refused(comment.content)
    if refused:
        return False, f"refused:{why}"

    if scope == "usage_only" and not is_usage_question(comment.content):
        return False, "not_usage"

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
    scope: str = "related",
) -> int:
    """返回本轮实际回复条数。"""
    replied = 0
    titles = video_title_map or {}

    # ── 先处理「有人在别的视频 @ 大肥鱼」──
    # 这类互动优先级最高：用户主动叫它，它必须回应。
    try:
        ats = await bili.get_at_messages(limit=20)
        log.info("@ 大肥鱼的消息：%d 条", len(ats or []))
        for a in ats or []:
            if replied >= max_per_round:
                break
            c = Comment(
                rpid=str(a.get("rpid") or ""),
                bvid=str(a.get("bvid") or ""),
                content=str(a.get("content") or ""),
                author=str(a.get("author") or ""),
                is_direct_reply_to_me=True,
            )
            if not c.content or not c.bvid:
                continue
            ok, why = should_reply(c, scope=scope, max_len=max_len)
            log.info("  @ 消息 %s by %s → %s（%s）", c.rpid, c.author or "?", "要回" if ok else "不回", why)
            if not ok:
                continue
            try:
                body = await llm.write_reply(c, titles.get(c.bvid, ""), umo=umo)
            except Exception as e:
                log.info("  生成 @ 回复失败：%s", e)
                continue
            if not body or len(body) > max_len:
                continue
            root = str(a.get("root") or "") or c.rpid
            parent = c.rpid
            try:
                new_rpid, root_out = await bili.post_comment(
                    c.bvid, body, root=root, parent=parent
                )
                await store.save_own_comment(new_rpid, root_out, c.bvid, body)
                replied += 1
                log.info("  已回复 @ 消息 %s（新 rpid=%s）", c.rpid, new_rpid)
            except Exception as e:
                log.info("  发送 @ 回复失败：%s", e)
    except Exception as e:
        log.info("处理 @ 消息失败：%s", e)

    try:
        own = await store.list_own_comments(since_days=since_days)
    except Exception as e:
        log.info("读取自己评论失败：%s", e)
        return replied

    for oc in own:
        if replied >= max_per_round:
            break
        if oc.get("replied"):
            continue

        rpid = oc.get("rpid") or ""
        bvid = oc.get("bvid") or ""
        if not rpid or rpid == "unknown" or not bvid:
            # 以前这里静默 continue，导致"评论发了但楼中楼永远不回"且日志无痕。
            log.info(
                "跳过一条自己评论（rpid=%s bvid=%s）—— 缺少可用 rpid，无法查楼中楼",
                rpid or "空",
                bvid or "空",
            )
            continue

        try:
            replies = await bili.get_sub_replies(bvid, rpid)
            log.info("查楼中楼 rpid=%s，收到 %d 条回复", rpid, len(replies or []))
        except Exception as e:
            log.info("拉楼中楼失败 %s: %s", rpid, e)
            continue

        handled = False
        for c in replies:
            ok, why = should_reply(c, scope=scope, max_len=max_len)
            log.info(
                "楼中楼 %s by %s → %s（%s）",
                c.rpid,
                getattr(c, "author", "") or "?",
                "要回" if ok else "不回",
                why,
            )
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
