"""B 站扫码登录。生成二维码 → 轮询状态 → 拿 Cookie。"""

from __future__ import annotations

import asyncio
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import aiohttp

from ..infra.logger import setup

log = setup("bili_scroll.qrlogin")

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

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

# 登录必需保留的 Cookie 键
KEEP_KEYS = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "buvid3",
    "buvid4",
)


class QrLoginError(Exception):
    pass


def _cookies_from_url(url: str) -> str:
    """从成功回调的 url 里提取 Cookie 串。"""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
    except Exception:
        return ""

    parts: list[str] = []
    for key in KEEP_KEYS:
        values = params.get(key)
        if values and values[0]:
            parts.append(f"{key}={values[0]}")
    return "; ".join(parts)


async def generate_qr(session: aiohttp.ClientSession | None = None) -> tuple[str, str, bytes]:
    """生成二维码。返回 (qrcode_key, 扫码链接, png 字节)。"""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            timeout=aiohttp.ClientTimeout(total=20),
        )
    try:
        async with session.get(GENERATE_URL) as resp:
            if resp.status != 200:
                raise QrLoginError(f"生成二维码失败 HTTP {resp.status}")
            payload = await resp.json(content_type=None)
    finally:
        if own_session:
            await session.close()

    data = (payload or {}).get("data") or {}
    key = data.get("qrcode_key") or ""
    url = data.get("url") or ""
    if not key or not url:
        raise QrLoginError(f"二维码接口返回异常: {payload}")

    png = await asyncio.to_thread(_render_png, url)
    return key, url, png


def _render_png(content: str) -> bytes:
    """把链接渲染成二维码 PNG。放在线程里跑，避免阻塞事件循环。"""
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
) -> tuple[int, str]:
    """轮询一次。返回 (状态码, cookie 串)。"""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            timeout=aiohttp.ClientTimeout(total=20),
        )
    try:
        async with session.get(POLL_URL, params={"qrcode_key": key}) as resp:
            if resp.status != 200:
                return -1, ""
            payload = await resp.json(content_type=None)
    except Exception as e:
        log.info("轮询异常：%s", e)
        return -1, ""
    finally:
        if own_session:
            await session.close()

    data = (payload or {}).get("data") or {}
    code = int(data.get("code", -1))

    if code == ST_OK:
        cookie = _cookies_from_url(data.get("url") or "")

        # 有些情况下 url 里没有全部字段，从 Set-Cookie 补
        return ST_OK, cookie

    return code, ""


async def wait_for_scan(
    key: str,
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 2.0,
    on_status=None,
) -> tuple[int, str]:
    """一直轮询直到成功/过期/超时。返回 (最终状态码, cookie)。"""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_code = ST_WAITING

    while asyncio.get_event_loop().time() < deadline:
        code, cookie = await poll_once(key)

        if code == ST_OK:
            return ST_OK, cookie
        if code == ST_EXPIRED:
            return ST_EXPIRED, ""

        if code != last_code and on_status is not None:
            try:
                await on_status(code)
            except Exception:
                pass
        last_code = code

        await asyncio.sleep(interval_seconds)

    return ST_EXPIRED, ""


def status_text(code: int) -> str:
    if code == ST_OK:
        return "登录成功"
    return STATUS_TEXT.get(code, f"未知状态（{code}）")
