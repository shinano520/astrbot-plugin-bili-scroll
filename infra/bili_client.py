"""B 站客户端。封装 bilibili-api-python，补上它没封装的二级评论与音频流。"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import aiohttp

from bilibili_api import comment as bcomment
from bilibili_api import search as bsearch
from bilibili_api import user as buser
from bilibili_api import video as bvideo
from bilibili_api.comment import CommentResourceType, OrderType
from bilibili_api.search import SearchObjectType
from bilibili_api.utils.aid_bvid_transformer import aid2bvid, bvid2aid
from bilibili_api.utils.network import Credential

from ..domain.models import CheckResult, Comment, Video
from ..infra.logger import mask, setup

log = setup("bili_scroll.bili")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"
SUB_REPLY_URL = "https://api.bilibili.com/x/v2/reply/reply"
REPLY_LIST_URL = "https://api.bilibili.com/x/v2/reply/wbi/main"
FOLLOWINGS_URL = "https://api.bilibili.com/x/relation/followings"
PLAYER_V2_URL = "https://api.bilibili.com/x/player/v2"


class BiliError(Exception):
    pass


class NotLoggedIn(BiliError):
    pass


class RateLimited(BiliError):
    pass


def parse_cookie(cookie_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class BiliClient:
    """所有 B 站调用都走这里。失败抛出明确异常，由上层决定重试或跳过。"""

    def __init__(self, cookie: str, rate_limit: float = 2.0):
        self._cookie_raw = cookie or ""
        self._cookies = parse_cookie(self._cookie_raw)
        self._min_interval = 1.0 / max(0.1, rate_limit)
        self._last_call = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": UA,
                    "Referer": REFERER,
                    "Cookie": self._cookie_raw,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    @property
    def credential(self) -> Credential:
        c = self._cookies
        return Credential(
            sessdata=c.get("SESSDATA"),
            bili_jct=c.get("bili_jct"),
            buvid3=c.get("buvid3"),
            buvid4=c.get("buvid4"),
            dedeuserid=c.get("DedeUserID"),
            ac_time_value=c.get("ac_time_value"),
        )

    @property
    def uid(self) -> str:
        return self._cookies.get("DedeUserID", "")

    async def check_login(self) -> CheckResult:
        try:
            await self._throttle()
            info = await buser.get_self_info(self.credential)
            name = info.get("name") or info.get("uname") or "?"
            mid = info.get("mid") or info.get("uid") or "?"
            return CheckResult("登录", True, f"{name} (uid={mid})")
        except Exception as e:
            return CheckResult("登录", False, mask(str(e)))

    async def search(self, keyword: str, days: int = 7, limit: int = 20) -> list[Video]:
        await self._throttle()
        try:
            raw = await bsearch.search_by_type(
                keyword=keyword,
                search_type=SearchObjectType.VIDEO,
                time_range=days,
                page_size=min(50, limit),
            )
        except Exception as e:
            raise BiliError(f"搜索失败: {mask(str(e))}") from e

        videos: list[Video] = []
        for item in (raw.get("result") or [])[:limit]:
            bvid = item.get("bvid") or ""
            if not bvid:
                continue
            title = _strip_tags(item.get("title") or "")
            author = item.get("author") or ""
            pubdate = int(item.get("pubdate") or 0)
            duration = _parse_duration(item.get("duration") or "0:00")
            desc = _strip_tags(item.get("description") or "")
            videos.append(
                Video(
                    bvid=bvid,
                    title=title,
                    author=author,
                    pubdate=pubdate,
                    duration=duration,
                    desc=desc,
                    keywords=_extract_keywords(title, item.get("tag") or ""),
                )
            )
        return videos

    async def get_video(self, bvid: str) -> Video:
        await self._throttle()
        v = bvideo.Video(bvid=bvid, credential=self.credential)
        info = await v.get_info()
        return Video(
            bvid=bvid,
            title=info.get("title", ""),
            author=(info.get("owner") or {}).get("name", ""),
            pubdate=int(info.get("pubdate", 0)),
            duration=int(info.get("duration", 0)),
            desc=info.get("desc", ""),
            keywords=_extract_keywords(info.get("title", ""), info.get("tname", "")),
        )

    async def get_subtitle(self, bvid: str) -> str:
        """取字幕原文。没有字幕返回空串。

        走 /x/player/v2 直连（不用 bilibili-api 的 get_subtitle，
        那个要 cid 且返回结构不稳定，实测拿不到）。
        流程：bvid -> info 拿 cid -> player/v2 拿 subtitles[] -> 下载字幕 json。
        """
        await self._throttle()
        try:
            v = bvideo.Video(bvid=bvid, credential=self.credential)
            info = await v.get_info()
            pages = info.get("pages") or []
            cid = pages[0].get("cid") if pages else None
            if not cid:
                log.info("%s 拿不到 cid", bvid)
                return ""
        except Exception as e:
            log.info("%s 拿视频信息失败: %s", bvid, e)
            return ""

        # 1) player/v2 拿字幕列表
        subs: list[dict] = []
        try:
            session = await self._get_session()
            params = {"aid": bvid2aid(bvid), "cid": cid}
            async with session.get(PLAYER_V2_URL, params=params) as resp:
                if resp.status != 200:
                    log.info("%s player/v2 HTTP %s", bvid, resp.status)
                    return ""
                payload = await resp.json(content_type=None)
            if (payload or {}).get("code") != 0:
                log.info("%s player/v2 code=%s", bvid, payload.get("code"))
                return ""
            subs = (
                ((payload or {}).get("data") or {}).get("subtitle") or {}
            ).get("subtitles") or []
        except Exception as e:
            log.info("%s 拉字幕列表失败: %s", bvid, e)
            return ""

        if not subs:
            return ""

        # 2) 优先中文
        chosen = None
        for s in subs:
            if (s.get("lan") or "").startswith("zh"):
                chosen = s
                break
        chosen = chosen or subs[0]

        url = chosen.get("subtitle_url") or ""
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url

        # 3) 下载字幕正文
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    return ""
                body_json = await resp.json(content_type=None)
        except Exception as e:
            log.info("%s 下载字幕失败: %s", bvid, e)
            return ""

        segs = (body_json or {}).get("body") or []
        text = "".join((seg.get("content") or "") for seg in segs).strip()
        if text:
            log.info("%s 字幕 %d 字（共 %d 条轨）", bvid, len(text), len(subs))
        return text

    async def get_at_messages(self, limit: int = 20) -> list[dict]:
        """拉「有人 @ 我」的消息（B 站消息中心）。

        返回 [{rpid, bvid, content, author, root, parent}]。
        """
        await self._throttle()
        try:
            session = await self._get_session()
            async with session.get(
                "https://api.bilibili.com/x/msgfeed/at",
                params={"pn": 1, "ps": max(1, min(50, limit))},
            ) as resp:
                if resp.status != 200:
                    return []
                payload = await resp.json(content_type=None)
        except Exception as e:
            log.info("拉 @ 消息失败：%s", truncate(str(e)))
            return []

        if (payload or {}).get("code") != 0:
            log.info("拉 @ 消息 code=%s", (payload or {}).get("code"))
            return []

        out: list[dict] = []
        for it in ((payload.get("data") or {}).get("items")) or []:
            item = it.get("item") or {}
            reply = it.get("reply") or {}
            content = (
                ((reply.get("content") or {}).get("message"))
                or item.get("source_content")
                or ""
            )
            aid = item.get("business_id") or item.get("oid") or it.get("business_id")
            rpid = reply.get("rpid") or reply.get("rpid_str") or it.get("id")
            if not aid or not content:
                continue
            try:
                bvid = aid2bvid(int(aid))
            except Exception:
                continue
            from_mid = str(((it.get("user") or {}).get("mid")) or "")
            if str(getattr(self, "uid", "") or "") and from_mid == str(self.uid):
                continue
            out.append(
                {
                    "rpid": str(rpid or ""),
                    "bvid": bvid,
                    "content": str(content),
                    "author": str(((it.get("user") or {}).get("nickname")) or ""),
                    "root": str(reply.get("root") or reply.get("root_str") or ""),
                    "parent": str(reply.get("parent") or reply.get("parent_str") or ""),
                }
            )
        return out

    async def get_comments(self, bvid: str, limit: int = 30) -> list[str]:
        """拉一级热评正文，用于风格归纳与机器人评论剔除。"""
        aid = bvid2aid(bvid)
        await self._throttle()
        try:
            raw = await bcomment.get_comments(
                oid=aid,
                type_=CommentResourceType.VIDEO,
                page_index=1,
                order=OrderType.LIKE,
                credential=self.credential,
            )
        except Exception:
            return []

        out: list[str] = []
        for r in ((raw or {}).get("replies") or [])[:limit]:
            content = ((r.get("content") or {}).get("message") or "").strip()
            if content:
                out.append(content)
        return out

    async def get_sub_replies(self, bvid: str, root_rpid: str) -> list[Comment]:
        """拉某条评论下的楼中楼。bilibili-api 没封装，直连接口。"""
        aid = bvid2aid(bvid)
        session = await self._get_session()
        await self._throttle()
        params = {"oid": aid, "type": 1, "root": root_rpid, "ps": 20, "pn": 1}
        try:
            async with session.get(SUB_REPLY_URL, params=params) as resp:
                if resp.status != 200:
                    return []
                payload = await resp.json(content_type=None)
        except Exception:
            return []

        if (payload or {}).get("code") != 0:
            return []

        data = payload.get("data") or {}
        out: list[Comment] = []
        my_mid = str(getattr(self, "uid", "") or "")
        for r in data.get("replies") or []:
            member = r.get("member") or {}
            mid = str(member.get("mid") or "")
            # ⚠️ 必须排除自己发的那条，否则会「自己回自己」并无限递归
            if my_mid and mid == my_mid:
                continue
            c = r.get("content") or {}
            out.append(
                Comment(
                    rpid=str(r.get("rpid") or ""),
                    bvid=bvid,
                    content=(c.get("message") or "").strip(),
                    author=(member.get("uname") or ""),
                    is_direct_reply_to_me=True,
                )
            )
        return out

    async def find_comment(self, bvid: str, rpid: str, root_rpid: str | None = None) -> bool:
        """复核：这条评论是否公开可见。查不到返回 False（不等于没发出去）。"""
        if not rpid or rpid == "unknown":
            return False
        aid = bvid2aid(bvid)
        session = await self._get_session()
        await self._throttle()
        params: dict[str, Any] = {"oid": aid, "type": 1, "ps": 20, "pn": 1}
        if root_rpid:
            params["root"] = root_rpid
        url = SUB_REPLY_URL if root_rpid else REPLY_LIST_URL
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return False
                payload = await resp.json(content_type=None)
        except Exception:
            return False

        def walk(node: Any) -> bool:
            if isinstance(node, dict):
                if str(node.get("rpid") or "") == str(rpid):
                    return True
                return any(walk(v) for v in node.values())
            if isinstance(node, list):
                return any(walk(v) for v in node)
            return False

        return walk(payload.get("data") or {})

    async def post_comment(
        self, bvid: str, text: str, root: str | None = None, parent: str | None = None
    ) -> tuple[str, str | None]:
        """发评论或回复。返回 (rpid, root_rpid)。

        直连 /x/v2/reply/add，而不是 bilibili-api 的 send_comment：
        后者返回体里没有 rpid / reply，导致楼中楼永远查不到子评论。
        """
        aid = bvid2aid(bvid)
        await self._throttle()

        csrf = ""
        try:
            csrf = str(getattr(self.credential, "bili_jct", "") or "")
        except Exception:
            csrf = ""

        form = {
            "oid": str(aid),
            "type": "1",
            "message": text,
            "plat": "1",
            "csrf": csrf,
        }
        if root:
            form["root"] = str(int(root))
        if parent:
            form["parent"] = str(int(parent))

        try:
            session = await self._get_session()
            async with session.post(
                "https://api.bilibili.com/x/v2/reply/add", data=form
            ) as resp:
                payload = await resp.json(content_type=None)
        except Exception as e:
            raise BiliError(f"发评论失败: {mask(str(e))}") from e

        code = (payload or {}).get("code")
        if code != 0:
            raise BiliError(
                f"发评论失败 code={code} msg={mask(str((payload or {}).get('message')))}"
            )

        data = (payload or {}).get("data") or {}
        rpid = str(data.get("rpid") or data.get("rpid_str") or "")
        root_out = str(root) if root else None

        members: list | None = None
        try:
            reply = data.get("reply") or {}
            members = ((reply.get("content") or {}).get("members")) or None
        except Exception:
            members = None

        if not rpid:
            # 极少数情况接口没给 rpid，回查一次兜底
            try:
                rpid, members = await self._find_own_recent_comment(bvid, text)
            except Exception as e:
                log.info("回查刚发的评论失败：%s", truncate(str(e)))

        if "@" in (text or ""):
            if members:
                names = [str((m or {}).get("uname") or "") for m in members]
                log.info("评论已发 rpid=%s，真的 @ 到 %d 人：%s", rpid, len(members), "、".join(names))
            elif members is not None:
                log.warning("评论已发 rpid=%s，但 @ 没生效（members 为空）", rpid)
            else:
                log.info("评论已发 rpid=%s（@ 是否生效请到 B 站页面确认）", rpid)
        else:
            log.info("评论已发 rpid=%s", rpid)

        return rpid or "unknown", root_out

    async def _find_own_recent_comment(self, bvid: str, text: str) -> tuple[str, list | None]:
        """回查评论区，找自己刚发的那条，返回 (rpid, members)。"""
        aid = bvid2aid(bvid)
        await self._throttle()
        session = await self._get_session()
        async with session.get(
            "https://api.bilibili.com/x/v2/reply",
            params={"oid": aid, "type": 1, "sort": 0, "ps": 20, "pn": 1},
        ) as resp:
            if resp.status != 200:
                return "", None
            payload = await resp.json(content_type=None)
        if (payload or {}).get("code") != 0:
            return "", None

        replies = ((payload.get("data") or {}).get("replies")) or []
        want = (text or "").strip()[:25]
        for x in replies:
            member = x.get("member") or {}
            if str(member.get("mid") or "") != str(self.uid or ""):
                continue
            msg = str(((x.get("content") or {}).get("message")) or "").strip()
            if want and not msg.startswith(want[:15]):
                continue
            return str(x.get("rpid") or ""), ((x.get("content") or {}).get("members")) or None
        return "", None

    async def like(self, bvid: str, like: bool = True) -> bool:
        aid = bvid2aid(bvid)
        await self._throttle()
        try:
            from bilibili_api import video_operation

            await video_operation.like(aid=aid, status=like, credential=self.credential)
            return True
        except Exception:
            return False

    async def coin(self, bvid: str, count: int = 1) -> bool:
        aid = bvid2aid(bvid)
        await self._throttle()
        try:
            from bilibili_api import video_operation

            await video_operation.coin(aid=aid, num=count, credential=self.credential)
            return True
        except Exception:
            return False

    async def get_followings(self, limit: int = 100) -> list[dict]:
        """拉自己关注的人。返回 [{mid, uname, mutual}]，mutual 表示是否互关。"""
        if not self.uid:
            return []
        session = await self._get_session()
        out: list[dict] = []
        page = 1
        while len(out) < limit and page <= 5:
            await self._throttle()
            params = {"vmid": self.uid, "pn": page, "ps": 50, "order": "desc"}
            try:
                async with session.get(FOLLOWINGS_URL, params=params) as resp:
                    if resp.status != 200:
                        break
                    payload = await resp.json(content_type=None)
            except Exception:
                break

            if (payload or {}).get("code") != 0:
                break

            data = payload.get("data") or {}
            items = data.get("list") or []
            if not items:
                break

            for it in items:
                attribute = int(it.get("attribute") or 0)
                # B 站 attribute：1 悄悄关注 / 2 单向关注 / 6 互相关注 / 128 拉黑
                # 互关只有 6，别用 2
                out.append(
                    {
                        "mid": str(it.get("mid") or ""),
                        "uname": it.get("uname") or "",
                        "mutual": attribute == 6,
                        "attribute": attribute,
                    }
                )
                if len(out) >= limit:
                    break
            page += 1

        # 互关的排前面，同一类按名字排，方便挑人
        out.sort(key=lambda x: (not x.get("mutual"), x.get("uname") or ""))
        return out

    async def get_audio_url(self, bvid: str) -> str:
        await self._throttle()
        v = bvideo.Video(bvid=bvid, credential=self.credential)
        info = await v.get_player_info()
        dash = (info or {}).get("dash") or {}
        audios = dash.get("audio") or []
        if not audios:
            return ""
        audios.sort(key=lambda a: int(a.get("bandwidth") or 0))
        return audios[0].get("baseUrl") or audios[0].get("base_url") or ""

    async def download_audio(self, bvid: str, dest) -> bool:
        url = await self.get_audio_url(bvid)
        if not url:
            return False
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
            return True
        except Exception:
            return False


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_duration(text: str) -> int:
    try:
        parts = [int(p) for p in str(text).split(":")]
    except (ValueError, AttributeError):
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _extract_keywords(title: str, extra: str) -> tuple[str, ...]:
    text = f"{title} {extra}"
    tokens = re.split(r"[\s,，、|/【】\[\]（）()#＃]+", text)
    seen: list[str] = []
    for t in tokens:
        t = t.strip()
        if len(t) < 2 or len(t) > 12:
            continue
        if t.isdigit():
            continue
        if t not in seen:
            seen.append(t)
        if len(seen) >= 8:
            break
    return tuple(seen)
