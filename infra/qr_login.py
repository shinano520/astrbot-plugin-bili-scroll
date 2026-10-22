"""B 站扫码登录。按原插件 lib/login.mjs 的实测路径实现。

关键（原插件踩过的坑）：
  ① 生成：GET /x/passport-login/web/qrcode/generate?source=main-fe-header
  ② 轮询：GET /x/passport-login/web/qrcode/poll?qrcode_key=...&source=main-fe-header
  ③ ⭐ 凭证在轮询响应的 **Set-Cookie 头**里；成功时 data.url 里没有 SESSDATA
     （那是别的域的游戏票，别在 url 里找）
  ④ 落成本项目的 Cookie 串：SESSDATA / bili_jct / buvid3 / DedeUserID
  ⑤ buvid3 拿不到就从 /x/frontend/finger/spi 补一张
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from urllib.parse import urlencode

import aiohttp

from ..infra.logger import setup

log = setup("bili_scroll.qrlogin")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

GEN_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
SOURCE = "main-fe-header"

# 轮询状态码
ST_OK = 0
ST_WAITING = 86101
ST_SCANNED = 86090
ST_EXPIRED = 86038

STATUS_TEXT = {
    ST_WAITING: "还没扫",
    ST_SCANNED: "扫了，等手机上点确认",
    ST_EXPIRED: "二维码过期了",
}

# Set-Cookie 里要保留的字段
WANT_COOKIES = {
    "SESSDATA": "SESSDATA",
    "bili_jct": "bili_jct",
    "DedeUserID": "DedeUserID",
    "buvid3": "buvid3",
    "buvid4": "buvid4",
}

# 输出 Cookie 串时的顺序
OUT_ORDER = ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "buvid4")


class QrLoginError(Exception):
    pass


def _headers() -> dict:
    return {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}


def pick_cookies(set_cookie_list: list[str]) -> dict[str, str]:
    """从 Set-Cookie 数组里取出需要的字段（按原插件的 pickCookies 逻辑）。"""
    jar: dict[str, str] = {}
    for raw in set_cookie_list or []:
        first = str(raw).split(";")[0] or ""
        idx = first.find("=")
        if idx <= 0:
            continue
        name = first[:idx].strip()
        value = first[idx + 1 :].strip()
        if name in WANT_COOKIES:
            jar[WANT_COOKIES[name]] = value
    return jar


def build_cookie_string(jar: dict[str, str]) -> str:
    parts = []
    for key in OUT_ORDER:
        v = (jar or {}).get(key)
        if v:
            parts.append(f"{key}={v}")
    return "; ".join(parts)


async def _ensure_buvid3(jar: dict[str, str]) -> dict[str, str]:
    """缺 buvid3 就补一张指纹（失败不影响登录）。"""
    if jar.get("buvid3"):
        return jar
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(SPI_URL, headers=_headers()) as resp:
                data = await resp.json(content_type=None)
        b3 = ((data or {}).get("data") or {}).get("b_3")
        if b3:
            jar["buvid3"] = b3
    except Exception as e:
        log.info("补 buvid3 失败（不影响登录）：%s", e)
    return jar


async def generate_qr(session: aiohttp.ClientSession | None = None) -> tuple[str, str, bytes]:
    """生成二维码。返回 (qrcode_key, 扫码链接, png 字节)。"""
    url = f"{GEN_URL}?{urlencode({'source': SOURCE})}"
    own = session is None
    if own:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    try:
        async with session.get(url, headers=_headers()) as resp:
            if resp.status != 200:
                raise QrLoginError(f"生成二维码失败 HTTP {resp.status}")
            payload = await resp.json(content_type=None)
    finally:
        if own:
            await session.close()

    data = (payload or {}).get("data") or {}
    key = data.get("qrcode_key") or ""
    scan_url = data.get("url") or ""
    if not key or not scan_url:
        raise QrLoginError(
            f"二维码接口返回异常：{(payload or {}).get('message') or '没有 qrcode_key'}"
        )

    png = await asyncio.to_thread(_render_png, scan_url)
    return key, scan_url, png


def _render_png(content: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def poll_once(
    key: str, session: aiohttp.ClientSession | None = None
) -> tuple[int, str, str]:
    """轮询一次。返回 (状态码, cookie 串, 备注)。

    ⭐ 凭证在 Set-Cookie 头里，不在 data.url 里。
    """
    url = f"{POLL_URL}?{urlencode({'qrcode_key': key, 'source': SOURCE})}"
    own = session is None
    if own:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
    try:
        async with session.get(url, headers=_headers(), allow_redirects=False) as resp:
            set_cookies: list[str] = []
            try:
                set_cookies = list(resp.headers.getall("Set-Cookie", []))
            except Exception:
                raw = resp.headers.get("Set-Cookie")
                if raw:
                    set_cookies = [raw]
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = {}
    except Exception as e:
        log.info("轮询异常：%s", e)
        return -1, "", str(e)
    finally:
        if own:
            await session.close()

    data = (payload or {}).get("data") or {}
    try:
        code = int(data.get("code", -1))
    except (TypeError, ValueError):
        code = -1

    if code == ST_OK:
        jar = pick_cookies(set_cookies)
        if not jar.get("SESSDATA"):
            # 原插件也遇到过：登录成功但响应里没 SESSDATA
            log.warning("登录成功但 Set-Cookie 里没有 SESSDATA，收到 %d 条 cookie", len(set_cookies))
            return ST_OK, "", "登录成功但响应里没有 SESSDATA"
        jar = await _ensure_buvid3(jar)
        return ST_OK, build_cookie_string(jar), ""

    return code, "", (payload or {}).get("message") or ""


async def wait_for_scan(
    key: str,
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 2.0,
) -> tuple[int, str, str]:
    """一直轮询到成功/过期/超时。返回 (最终状态码, cookie, 备注)。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds
    last = ST_WAITING

    while loop.time() < deadline:
        code, cookie, note = await poll_once(key)
        if code == ST_OK:
            return ST_OK, cookie, note
        if code == ST_EXPIRED:
            return ST_EXPIRED, "", note
        if code != last:
            log.info("扫码状态：%s", STATUS_TEXT.get(code, code))
            last = code
        await asyncio.sleep(interval_seconds)

    return ST_EXPIRED, "", "超时"


def status_text(code: int) -> str:
    if code == ST_OK:
        return "登录成功"
    return STATUS_TEXT.get(code, f"未知状态（{code}）")
