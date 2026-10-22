"""刷 B 站插件入口。移植自 c1overzz/dsh-bili-scroll。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Image, Plain

from .adapter.affection import AffectionReader
from .adapter.commands import Commands, scope_of, umo_of
from .adapter.event_hook import EventHook, expire_windows
from .adapter.notifier import Notifier
from .adapter.scheduler import next_run_at, parse_windows
from .adapter.web_page import WebPage
from .domain.constants import (
    CMD_ROOT,
    DEFAULT_KEYWORDS,
    DEFAULT_OPT_IN_PHRASES,
    DEFAULT_OPT_OUT_PHRASES,
    NEUTRAL_PERSONA,
    REPLY_MAX_PER_ROUND,
)
from .domain.models import CheckResult, RoundCost, Video
from .domain.reply import run_reply_round, verify_comments
from .domain.style import append_at, make_comment
from .infra import qr_login
from .infra.bili_client import BiliClient
from .infra.crypto import CryptoError, load_cookie, load_or_create_key, save_cookie
from .infra.llm_client import LlmClient
from .infra.logger import setup, truncate
from .infra.storage import Storage
from .infra.transcribe import Transcriber
from .service.push_service import PushService
from .service.report_service import build_report, read_today
from .service.scroll_service import RoundContext, ScrollService

PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"

log = setup("bili_scroll")


def _event_at_bot(event: Any) -> bool:
    """消息里是否 @ 了机器人自己（@全体 不算）。"""
    try:
        self_id = str(event.get_self_id() or "")
        if not self_id:
            return False
        for comp in event.get_messages() or []:
            if comp.__class__.__name__ != "At":
                continue
            qq = str(getattr(comp, "qq", "") or "")
            if qq and qq == self_id:
                return True
    except Exception:
        pass
    return False


def _extract_prompt(persona: Any) -> str:
    """从不同形态的 persona 对象里取出系统提示词。"""
    if persona is None:
        return ""
    if isinstance(persona, str):
        return persona.strip()
    if isinstance(persona, dict):
        for key in ("prompt", "system_prompt", "text"):
            value = persona.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for attr in ("prompt", "system_prompt", "text"):
        value = getattr(persona, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class BiliScrollPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.context = context
        self.cfg = config or {}

        self.data_dir = Path(self._get("data_dir") or DATA_DIR)
        self.out_dir = self.data_dir / "out"
        self.work_dir = self.data_dir / "tmp"

        self.store: Storage | None = None
        self.bili: BiliClient | None = None
        self.llm: LlmClient | None = None
        self.transcriber: Transcriber | None = None
        self.notifier: Notifier | None = None
        self.push: PushService | None = None
        self.hook: EventHook | None = None
        self.affection: AffectionReader | None = None
        self.commands: Commands | None = None
        self.web: WebPage | None = None

        self._ctx: RoundContext | None = None
        self._running = False
        self._login_running = False
        self._scheduler_task: asyncio.Task | None = None
        self._janitor_task: asyncio.Task | None = None
        self._next_run: datetime | None = None

        # WebUI 扫码登录：必须在 __init__ 注册，Web 服务器启动时才会挂载
        self.web = WebPage(self)
        self._register_web_apis()

    # ── 配置读取 ──────────────────────────────────────────────
    def _get(self, key: str, default: Any = None) -> Any:
        try:
            return self.cfg.get(key, default)
        except Exception:
            return default

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self._get(key, default))
        except (TypeError, ValueError):
            return default

    def _flag(self, key: str, default: bool) -> bool:
        v = self._get(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "\u662f")
        return bool(v)

    # ── 生命周期 ──────────────────────────────────────────────
    async def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.store = Storage(self.data_dir / "bili_scroll.db")
        await self.store.open()

        key = load_or_create_key(self.data_dir)
        cookie = ""
        try:
            cookie = load_cookie(self.data_dir, key)
        except CryptoError as e:
            log.warning("读取 Cookie 失败：%s", e)

        cfg_cookie = (self._get("cookie") or "").strip()
        if cfg_cookie and cfg_cookie != cookie:
            try:
                save_cookie(self.data_dir, cfg_cookie, key)
                cookie = cfg_cookie
                log.info("已保存新的 Cookie")
            except Exception as e:
                log.warning("保存 Cookie 失败：%s", e)

        if not cookie:
            log.warning("没有 B 站 Cookie，插件功能不可用。请在 WebUI 里填入。")

        self.bili = BiliClient(cookie, rate_limit=self._num("bili_rate_limit", 2.0))
        self.llm = LlmClient(
            self.context,
            persona=await self._load_persona(),
            max_calls=int(self._num("max_llm_calls_per_round", 20)),
        )
        self.notifier = Notifier(self.context)
        self.affection = AffectionReader(self.context)

        if self._flag("transcribe_enable", False):
            self.transcriber = Transcriber(self._get("transcribe_model", "base") or "base")

        self.commands = Commands(self)
        self.push = PushService(store=self.store, notifier=self.notifier, cfg=self._cfg_dict())
        self.hook = EventHook(store=self.store, llm=self.llm, cfg=self._cfg_dict())

        if self._flag("schedule_enable", False):
            self._scheduler_task = asyncio.create_task(self._schedule_loop())

        self._janitor_task = asyncio.create_task(self._janitor_loop())

        log.info(
            "刷B站插件已加载：cookie=%s 调度=%s 转写=%s WebUI=%s",
            "\u6709" if cookie else "\u65e0",
            self._flag("schedule_enable", False),
            bool(self.transcriber),
            "已注册",
        )

    def _register_web_apis(self) -> None:
        """注册 WebUI 扫码登录端点。访问需 AstrBot 后台登录态。"""
        routes = (
            ("page", self.web.page, ["GET"], "B站面板页"),
            ("qr", self.web.qr, ["GET"], "生成登录二维码"),
            ("poll", self.web.poll, ["GET"], "轮询扫码状态"),
            ("status", self.web.status, ["GET"], "登录与配额状态"),
            ("followings", self.web.followings, ["GET"], "拉互关列表"),
            ("at-users", self.web.at_users, ["POST"], "保存 @ 名单"),
            ("config", self.web.config_get, ["GET"], "读取插件设置"),
            ("config", self.web.config_save, ["POST"], "保存插件设置"),
            ("personas", self.web.personas, ["GET"], "人设列表"),
            ("bootstrap", self.web.bootstrap, ["POST"], "免密换取 token"),
        )
        for endpoint, handler, methods, desc in routes:
            try:
                self.context.register_web_api(
                    f"/astrbot_plugin_bili_scroll/{endpoint}", handler, methods, desc
                )
            except Exception as e:
                log.warning("注册 Web API 失败 %s: %s", endpoint, e)

    def reload_bili_client(self, cookie: str) -> None:
        """换 Cookie 后重建客户端。"""
        old = self.bili
        self.bili = BiliClient(cookie, rate_limit=self._num("bili_rate_limit", 2.0))
        if old is not None:
            try:
                asyncio.create_task(old.close())
            except Exception:
                pass
        log.info("B 站客户端已用新 Cookie 重建")

    async def terminate(self) -> None:
        for task in (self._scheduler_task, self._janitor_task):
            if task and not task.done():
                task.cancel()
        if self.bili:
            await self.bili.close()
        if self.store:
            await self.store.close()
        log.info("刷B站插件已卸载")

    def _cfg_dict(self) -> dict:
        return {
            "keywords": self._get("keywords") or list(DEFAULT_KEYWORDS),
            "search_days": int(self._num("search_days", 7)),
            "quota_per_round": int(self._num("quota_per_round", 3)),
            "max_per_day": int(self._num("max_per_day", 5)),
            "transcribe_enable": self._flag("transcribe_enable", False),
            "push_private_enable": self._flag("push_private_enable", True),
            "push_group_enable": self._flag("push_group_enable", False),
            "push_private_probability": self._num("push_private_probability", 0.6),
            "push_group_probability": self._num("push_group_probability", 0.3),
            "push_cooldown_hours": int(self._num("push_cooldown_hours", 6)),
            "feedback_window_minutes": int(self._num("feedback_window_minutes", 30)),
            "opt_in_phrases": self._get("opt_in_phrases") or list(DEFAULT_OPT_IN_PHRASES),
            "opt_out_phrases": self._get("opt_out_phrases") or list(DEFAULT_OPT_OUT_PHRASES),
        }

    async def _load_persona(self) -> str:
        pm = getattr(self.context, "persona_manager", None)

        # 1. 优先用配置里明确选的人设卡
        picked = (self._get("persona") or "").strip()
        if picked and pm is not None:
            text = await self._persona_by_id(pm, picked)
            if text:
                log.info("使用人设卡：%s", picked)
                return text

        # 2. 回退：默认人设
        if pm is not None:
            for name in ("get_default_persona_v3", "get_default_persona"):
                fn = getattr(pm, name, None)
                if fn is None:
                    continue
                try:
                    persona = fn()
                    if asyncio.iscoroutine(persona):
                        persona = await persona
                    text = _extract_prompt(persona)
                    if text:
                        log.info("使用默认人设（%s）", name)
                        return text
                except Exception as e:
                    log.debug("读默认人设失败 %s: %s", name, e)

        # 3. 再看旧字段
        source = (self._get("persona_source") or "astrbot").strip()
        if source == "file":
            path = self._get("persona_file") or ""
            try:
                return Path(path).read_text(encoding="utf-8").strip() or NEUTRAL_PERSONA
            except Exception as e:
                log.info("读人设文件失败，走中性：%s", e)
        return NEUTRAL_PERSONA

    async def _persona_by_id(self, pm: Any, persona_id: str) -> str:
        """按 id 找人设，兼容不同版本的 AstrBot。"""
        for name in ("get_persona_v3", "get_persona"):
            fn = getattr(pm, name, None)
            if fn is None:
                continue
            try:
                persona = fn(persona_id)
                if asyncio.iscoroutine(persona):
                    persona = await persona
                text = _extract_prompt(persona)
                if text:
                    return text
            except Exception:
                continue

        # 兜底：遍历列表自己找
        for name in ("get_all_personas_v3", "get_all_personas", "personas"):
            try:
                value = getattr(pm, name, None)
                personas = value() if callable(value) else value
                if asyncio.iscoroutine(personas):
                    personas = await personas
                for item in personas or []:
                    target_id = getattr(item, "persona_id", None) or (
                        item.get("persona_id") if isinstance(item, dict) else None
                    )
                    if str(target_id) == str(persona_id):
                        text = _extract_prompt(item)
                        if text:
                            return text
            except Exception:
                continue
        return ""

    # ── 指令 ──────────────────────────────────────────────────
    @filter.command(CMD_ROOT)
    async def bili(self, event: AstrMessageEvent):
        """刷B站总指令。"""
        msg = (event.message_str or "").strip()
        action = msg[len(CMD_ROOT) :].strip() if msg.startswith(CMD_ROOT) else msg
        try:
            reply = await self.commands.handle(action, event)
        except Exception as e:
            log.exception("指令执行失败")
            reply = f"出错了：{truncate(str(e))}"
        yield event.plain_result(reply)

    async def cmd_run(self, event: AstrMessageEvent) -> str:
        if self._running:
            return "已经有一轮在跑了，等它跑完。"
        asyncio.create_task(self._run_round(umo_of(event)))
        return "开始跑，跑完把结果发你。"

    async def cmd_login(self, event: AstrMessageEvent) -> str:
        """扫码登录：发二维码图片，轮询扫码状态。"""
        if self._login_running:
            return "正在等扫码，别重复发。"

        self._login_running = True
        try:
            try:
                key, _url, png = await qr_login.generate_qr()
            except Exception as e:
                log.exception("生成二维码失败")
                return f"生成二维码失败：{truncate(str(e))}"

            try:
                await event.send(
                    event.chain_result(
                        [
                            Plain(
                                "手机 B 站扫这个码。确认时选「记住我 / 长期」，别选 24 小时。"
                            ),
                            Image.fromBytes(png),
                        ]
                    )
                )
            except Exception as e:
                log.warning("发二维码失败：%s", e)
                return f"二维码生成了但发不出去：{truncate(str(e))}"

            code, cookie = await qr_login.wait_for_scan(key, timeout_seconds=180)

            if code == qr_login.ST_OK and cookie:
                key_bytes = load_or_create_key(self.data_dir)
                save_cookie(self.data_dir, cookie, key_bytes)
                self.bili = BiliClient(
                    cookie, rate_limit=self._num("bili_rate_limit", 2.0)
                )
                check = await self.bili.check_login()
                if check.ok:
                    log.info("B 站登录成功：%s", check.detail)
                    return f"登录成功：{check.detail}"
                return f"拿到 Cookie 但验证没过：{check.detail}"

            return f"登录没完成：{qr_login.status_text(code)}"
        finally:
            self._login_running = False

    async def cmd_report(self, event: AstrMessageEvent) -> str:
        text = read_today(self.out_dir)
        if not text:
            return "今天还没有报告。发 /bili 跑 试试。"
        return truncate(text, 1500)

    async def cmd_stop(self, event: AstrMessageEvent) -> str:
        if not self._running or self._ctx is None:
            return "现在没有在跑的一轮。"
        self._ctx.stop()
        return "好，让它停在下一步。"

    async def cmd_selfcheck(self, event: AstrMessageEvent) -> str:
        checks: list[CheckResult] = []
        checks.append(await self.bili.check_login())

        try:
            vids = await self.bili.search("AI", days=7, limit=1)
            checks.append(
                CheckResult("搜索", bool(vids), vids[0].title[:30] if vids else "没返回结果")
            )
        except Exception as e:
            checks.append(CheckResult("搜索", False, truncate(str(e))))

        try:
            vids = await self.bili.search("AI", days=7, limit=3)
            has = False
            for v in vids:
                if await self.bili.get_subtitle(v.bvid):
                    has = True
                    break
            checks.append(
                CheckResult("字幕", has, "至少一条有字幕" if has else "最近几条都没字幕")
            )
        except Exception as e:
            checks.append(CheckResult("字幕", False, truncate(str(e))))

        try:
            text = await self.llm._chat("只回一个字：好", "在吗")
            checks.append(CheckResult("模型", bool(text), text[:20]))
        except Exception as e:
            checks.append(CheckResult("模型", False, truncate(str(e))))

        lines = ["自检结果："]
        for c in checks:
            lines.append(f"{'✅' if c.ok else '❌'} {c.name}：{c.detail}")
        return "\n".join(lines)

    async def cmd_status(self, event: AstrMessageEvent) -> str:
        picked, rounds = await self.store.quota_today()
        total = int(self._num("max_per_day", 5))
        nxt = self._next_run.strftime("%m-%d %H:%M") if self._next_run else "未排期"
        cost = self.llm.cost if self.llm else RoundCost()
        return (
            f"今日已挑 {picked}/{total} 条，跑了 {rounds} 轮\n"
            f"下次执行：{nxt}\n"
            f"{cost.render()}"
        )

    async def cmd_subscribe(self, event: AstrMessageEvent) -> str:
        scope, target = scope_of(event)
        if not target:
            return "识别不到会话。"
        await self.store.upsert_subscriber(scope, target, opted_in=True)
        return "行，刷到有意思的发你。"

    async def cmd_unsubscribe(self, event: AstrMessageEvent) -> str:
        scope, target = scope_of(event)
        if not target:
            return "识别不到会话。"
        await self.store.upsert_subscriber(scope, target, opted_in=False)
        return "行，不发了。"

    # ── 消息钩子（反馈收集）───────────────────────────────────
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        try:
            text = (event.message_str or "").strip()
            if not text or text.startswith("/") or text.startswith(CMD_ROOT):
                return
            scope, target = scope_of(event)
            if not target:
                return
            reply = await self.hook.handle_message(
                scope=scope,
                target_id=target,
                text=text,
                umo=umo_of(event),
                at_bot=_event_at_bot(event),
            )
            if reply:
                yield event.plain_result(reply)
        except Exception:
            log.debug("消息钩子处理失败", exc_info=True)

    # ── 一轮执行 ──────────────────────────────────────────────
    async def _run_round(self, umo: str | None = None) -> None:
        if self._running:
            return
        self._running = True
        self._ctx = RoundContext()
        started = time.monotonic()
        try:
            if not self.bili or not self.bili.uid:
                await self._safe_send(umo, "还没登录 B 站，先让管理员填 Cookie。")
                return

            self.llm.reset_cost()

            service = ScrollService(
                bili=self.bili,
                llm=self.llm,
                store=self.store,
                cfg=self._cfg_dict(),
                transcriber=self.transcriber,
                work_dir=self.work_dir,
            )
            result = await service.run_round(self._ctx, umo=umo)

            detail = str(self._get("report_detail", "full"))
            build_report(
                result.picked,
                result.cost,
                detail,
                self.out_dir,
                no_subtitle=result.no_subtitle,
                stopped=result.stopped,
                error=result.error,
            )

            video_index = {}
            for v in result.picked:
                video = await self._video_for(v.bvid)
                if video is not None:
                    video_index[v.bvid] = video
            if video_index:
                await self.push.dispatch(result.picked, video_index)

            if self._flag("comment_enable", False) and result.picked:
                await self._maybe_comment(result.picked, umo)

            if self._flag("reply_enable", False):
                await run_reply_round(
                    bili=self.bili,
                    llm=self.llm,
                    store=self.store,
                    umo=umo,
                    max_per_round=int(self._num("reply_max_per_round", REPLY_MAX_PER_ROUND)),
                    scope=str(self._get("reply_scope") or "related"),
                )

            summary = (
                f"这一轮挑了 {len(result.picked)} 条"
                f"（跳过 {result.skipped}，无字幕 {result.no_subtitle}），"
                f"耗时 {result.elapsed:.0f}s"
            )
            if result.stopped:
                summary += "，被手动停止"
            if result.error:
                summary += f"，异常：{result.error}"
            await self._safe_send(umo, summary)

        except Exception:
            log.exception("跑一轮失败")
            await self._safe_send(umo, "这一轮出错了，详情看日志。")
        finally:
            self._running = False
            self._ctx = None
            log.info("一轮结束，用时 %.1fs", time.monotonic() - started)

    async def _video_for(self, bvid: str) -> Video | None:
        try:
            async with self.store.db.execute(
                "SELECT title,author,pubdate,keywords FROM seen WHERE bvid=?", (bvid,)
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            return None
        if not row:
            return None
        try:
            kws = tuple(json.loads(row["keywords"] or "[]"))
        except Exception:
            kws = ()
        return Video(
            bvid=bvid,
            title=row["title"] or "",
            author=row["author"] or "",
            pubdate=int(row["pubdate"] or 0),
            keywords=kws,
        )

    async def _maybe_comment(self, verdicts, umo: str | None) -> None:
        max_per_round = int(self._num("comment_max_per_round", 1))
        done = 0
        for v in verdicts:
            if done >= max_per_round:
                break
            try:
                if await self.store.commented_bvid(v.bvid):
                    continue
            except Exception:
                pass
            video = await self._video_for(v.bvid)
            if video is None:
                continue
            text = await make_comment(
                video,
                bili=self.bili,
                llm=self.llm,
                reason=v.reason,
                umo=umo,
                max_rewrites=int(self._num("comment_max_rewrites", 2)),
                threshold=self._num("comment_style_threshold", 0.7),
                sample_size=int(self._num("comment_style_sample_size", 30)),
            )
            if not text:
                continue

            # 按概率 @ 指定用户
            if self._flag("at_enable", False):
                users = list(self._get("at_users") or [])
                text = append_at(text, users, self._num("at_probability", 0.5))

            try:
                rpid, root = await self.bili.post_comment(video.bvid, text)
                await self.store.save_own_comment(rpid, root, video.bvid, text)
                done += 1
            except Exception as e:
                log.info("发评论失败：%s", e)

    async def _safe_send(self, umo: str | None, text: str) -> None:
        if not umo:
            log.info("%s", text)
            return
        try:
            await self.notifier.send_text(umo, text)
        except Exception:
            log.info("%s", text)

    # ── 调度循环 ──────────────────────────────────────────────
    async def _schedule_loop(self) -> None:
        windows = parse_windows(
            self._get("schedule_windows") or ["12:00-14:00", "18:00-09:00"]
        )
        jitter = int(self._num("schedule_jitter_minutes", 30))
        peak_aware = self._flag("schedule_peak_aware", True)

        while True:
            try:
                nxt = next_run_at(windows=windows, jitter_minutes=jitter, peak_aware=peak_aware)
                self._next_run = nxt
                if nxt is None:
                    log.info("找不到可用时间窗，1 小时后重算")
                    await asyncio.sleep(3600)
                    continue

                wait = max(1.0, (nxt - datetime.now()).total_seconds())
                log.info("下次执行：%s（%.0f 分钟后）", nxt.strftime("%m-%d %H:%M"), wait / 60)
                await asyncio.sleep(wait)
                await self._run_round(None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("调度循环异常，60 秒后重试")
                await asyncio.sleep(60)

    async def _janitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(300)
                n = await expire_windows(self.store)
                if n:
                    log.info("把 %d 个过期窗口记为沉默", n)
                await verify_comments(bili=self.bili, store=self.store)
                await self.store.decay_profiles(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("清理任务异常", exc_info=True)
