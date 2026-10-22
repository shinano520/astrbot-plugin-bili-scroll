"""WebUI 端点：左侧面板（登录 / 设置 / @ 名单）。

页面在 pages/bili_scroll/index.html，通过 AstrBot 的 bridge 调用这些接口。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..infra import qr_login
from ..infra.crypto import load_or_create_key, save_cookie
from ..infra.logger import setup, truncate

log = setup("bili_scroll.web")

# 设置页的分组与顺序
CONFIG_GROUPS: list[tuple[str, list[str]]] = [
    (
        "基础",
        ["cookie", "persona", "admin_username", "admin_password", "keywords",
         "search_days", "max_per_day", "quota_per_round", "report_detail"],
    ),
    (
        "调度",
        ["schedule_enable", "schedule_windows", "schedule_jitter_minutes",
         "schedule_peak_aware"],
    ),
    (
        "推送",
        ["push_private_enable", "push_group_enable", "push_private_probability",
         "push_group_probability", "push_cooldown_hours", "feedback_window_minutes",
         "opt_in_phrases", "opt_out_phrases"],
    ),
    (
        "评论",
        ["comment_enable", "comment_max_per_round", "comment_style_learning",
         "comment_style_sample_size", "comment_style_threshold",
         "comment_max_rewrites", "at_enable", "at_probability"],
    ),
    ("回复", ["reply_enable", "reply_max_per_round", "reply_scope"]),
    ("转写", ["transcribe_enable", "transcribe_model"]),
    (
        "好感度",
        ["affection_enable", "affection_like_threshold", "affection_coin_threshold"],
    ),
    ("运行", ["max_llm_calls_per_round", "bili_rate_limit", "data_dir"]),
]

# 需要下拉的字段
ENUM_FIELDS = {
    "reply_scope": ["related", "usage_only"],
    "report_detail": ["full", "brief", "none"],
    "transcribe_model": ["tiny", "base", "small"],
}

FIELD_LABELS = {
    "cookie": "B 站 Cookie",
    "persona": "使用哪个人设",
    "admin_username": "后台账号",
    "admin_password": "后台密码",
    "keywords": "关注关键词",
    "search_days": "搜索最近几天",
    "max_per_day": "每天最多挑几条",
    "quota_per_round": "每轮最多挑几条",
    "report_detail": "日报详细程度",
    "schedule_enable": "开启时段自动刷",
    "schedule_windows": "允许执行的时间窗",
    "schedule_jitter_minutes": "时段内随机抖动（分钟）",
    "schedule_peak_aware": "避开高峰时段",
    "push_private_enable": "允许私聊推送",
    "push_group_enable": "允许群聊推送",
    "push_private_probability": "私聊推送概率",
    "push_group_probability": "群聊推送概率",
    "push_cooldown_hours": "两次推送最小间隔（小时）",
    "feedback_window_minutes": "反馈窗口（分钟）",
    "opt_in_phrases": "订阅触发短语",
    "opt_out_phrases": "退订触发短语",
    "comment_enable": "自动评论",
    "comment_max_per_round": "每轮最多发几条评论",
    "comment_style_learning": "学习评论区风格",
    "comment_style_sample_size": "风格采样条数",
    "comment_style_threshold": "风格自检分数线",
    "comment_max_rewrites": "最多重写几次",
    "at_enable": "评论时 @ 用户",
    "at_probability": "@ 的概率",
    "reply_enable": "回复楼中楼",
    "reply_max_per_round": "每轮最多回几条",
    "reply_scope": "回复范围",
    "transcribe_enable": "本地转写无字幕视频",
    "transcribe_model": "Whisper 模型",
    "affection_enable": "按好感度投币/点赞",
    "affection_like_threshold": "点赞所需好感度",
    "affection_coin_threshold": "投币所需好感度",
    "max_llm_calls_per_round": "单轮 LLM 调用上限",
    "bili_rate_limit": "B 站 API 每秒上限",
    "data_dir": "数据目录",
}


def _load_schema() -> dict:
    path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.info("读 schema 失败：%s", e)
        return {}

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>刷B站 · 扫码登录</title>
<style>
  :root{
    --bg:#0f1115; --panel:#181b22; --line:#272b34;
    --text:#e6e8ee; --sub:#9aa3b2; --accent:#4f8cff;
    --ok:#3ecf8e; --warn:#f5a623; --err:#ff5c5c;
  }
  *{box-sizing:border-box}
  body{
    margin:0; min-height:100vh; background:var(--bg); color:var(--text);
    font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    display:flex; align-items:center; justify-content:center; padding:24px;
  }
  .card{
    width:100%; max-width:420px; background:var(--panel); border:1px solid var(--line);
    border-radius:16px; padding:28px; box-shadow:0 20px 60px rgba(0,0,0,.45);
  }
  h1{font-size:18px; margin:0 0 4px; font-weight:600}
  .sub{color:var(--sub); font-size:13px; margin-bottom:20px}
  .qrbox{
    display:flex; align-items:center; justify-content:center;
    min-height:260px; background:#fff; border-radius:12px; padding:16px;
  }
  .qrbox img{width:228px; height:228px; image-rendering:pixelated; display:block}
  .placeholder{color:#888; font-size:13px; text-align:center; padding:40px 12px}
  .status{display:flex; align-items:center; gap:8px; margin-top:16px; font-size:13px; color:var(--sub); min-height:22px}
  .dot{width:8px; height:8px; border-radius:50%; background:var(--sub); flex:none}
  .dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.err{background:var(--err)}
  .dot.pulse{animation:pulse 1.2s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .actions{display:flex; gap:10px; margin-top:18px}
  button{flex:1; padding:10px 14px; border-radius:10px; border:1px solid var(--line); background:#22262f; color:var(--text); font-size:13px; cursor:pointer; transition:.15s}
  button:hover:not(:disabled){background:#2a2f3a; border-color:#39404d}
  button.primary{background:var(--accent); border-color:var(--accent); color:#fff}
  button:disabled{opacity:.45; cursor:not-allowed}
  .tip{margin-top:16px; font-size:12px; color:var(--sub); line-height:1.7}
  .tip b{color:var(--warn)}
  .result{display:none; margin-top:16px; padding:12px; border-radius:10px; font-size:13px}
  .result.ok{display:block; background:rgba(62,207,142,.12); color:var(--ok); border:1px solid rgba(62,207,142,.3)}
  .result.err{display:block; background:rgba(255,92,92,.12); color:var(--err); border:1px solid rgba(255,92,92,.3)}
</style>
</head>
<body>
<div class="card">
  <h1>扫码登录 B 站</h1>
  <div class="sub">登录后插件才能搜索、读字幕、发评论</div>
  <div class="qrbox" id="qrbox"><div class="placeholder">点下面的按钮生成二维码</div></div>
  <div class="status"><span class="dot" id="dot"></span><span id="statusText">未开始</span></div>
  <div class="actions">
    <button class="primary" id="btnGen">生成二维码</button>
    <button id="btnCheck" disabled>查状态</button>
  </div>
  <div class="result" id="result"></div>
  <div class="tip">手机 B 站扫码后，确认时请选 <b>「记住我 / 长期」</b>。选 24 小时的话明天就得重扫。</div>
</div>
<script>
(function(){
  var BASE = '/api/plug/astrbot_plugin_bili_scroll/';
  var btnGen=document.getElementById('btnGen'), btnCheck=document.getElementById('btnCheck');
  var qrbox=document.getElementById('qrbox'), dot=document.getElementById('dot');
  var statusText=document.getElementById('statusText'), result=document.getElementById('result');
  var currentKey='', timer=null, polling=false;
  function setStatus(t,c,p){ statusText.textContent=t; dot.className='dot'+(c?' '+c:'')+(p?' pulse':''); }
  function showResult(t,ok){ result.textContent=t; result.className='result '+(ok?'ok':'err'); }
  function hideResult(){ result.className='result'; result.textContent=''; }
  function stopTimer(){ if(timer){clearInterval(timer);timer=null;} }
  function api(p){ return fetch(BASE+p,{credentials:'same-origin'}).then(function(r){return r.json();}); }
  function genQr(){
    stopTimer(); hideResult(); btnGen.disabled=true; btnCheck.disabled=true;
    setStatus('正在生成…','warn',true);
    qrbox.innerHTML='<div class="placeholder">生成中…</div>';
    api('qr').then(function(d){
      if(!d.ok) throw new Error(d.error||'生成失败');
      currentKey=d.key;
      qrbox.innerHTML='<img alt="二维码" src="data:image/png;base64,'+d.image+'">';
      setStatus('等待扫码','warn',true); btnCheck.disabled=false; startPolling();
    }).catch(function(e){
      setStatus('生成失败','err');
      qrbox.innerHTML='<div class="placeholder">生成失败<br>'+e.message+'</div>';
      showResult('生成失败：'+e.message,false);
    }).finally(function(){ btnGen.disabled=false; });
  }
  function startPolling(){ stopTimer(); timer=setInterval(checkOnce,2500); }
  function checkOnce(){
    if(!currentKey||polling) return;
    polling=true;
    api('poll?key='+encodeURIComponent(currentKey)).then(function(d){
      if(!d.ok) return;
      if(d.code===0){
        stopTimer(); setStatus('登录成功','ok'); btnCheck.disabled=true;
        showResult('登录成功：'+(d.detail||'已保存凭证'),true);
        qrbox.innerHTML='<div class="placeholder" style="color:#3ecf8e;font-size:15px">✓ 登录成功</div>';
      } else if(d.code===86090){ setStatus('已扫码，请在手机上确认','warn',true); }
      else if(d.code===86038){ stopTimer(); setStatus('二维码已过期','err'); btnCheck.disabled=true; showResult('二维码过期了，重新生成一个',false); }
      else { setStatus('等待扫码…','warn',true); }
    }).catch(function(){}).finally(function(){ polling=false; });
  }
  btnGen.addEventListener('click',genQr);
  btnCheck.addEventListener('click',function(){ setStatus('查询中…','warn',true); checkOnce(); });
})();
</script>
</body>
</html>
"""


def _normalize(value: Any, ftype: str) -> Any:
    """把配置值整理成前端好用的形态。"""
    if ftype == "list":
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [v.strip() for v in value.splitlines() if v.strip()]
        return []
    if ftype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if ftype in ("int", "float"):
        try:
            return int(value) if ftype == "int" else float(value)
        except (TypeError, ValueError):
            return 0
    if value is None:
        return ""
    return str(value)


class WebPage:
    """所有端点都走 AstrBot 后台登录态。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, str] = {}

    # ── 页面 ──────────────────────────────────────────────────
    async def page(self):
        try:
            from quart import Response

            return Response(PAGE_HTML, mimetype="text/html")
        except Exception as e:
            log.info("返回页面失败：%s", e)
            return PAGE_HTML

    # ── 二维码 ────────────────────────────────────────────────
    async def qr(self):
        try:
            from quart import jsonify
        except Exception:
            jsonify = None

        try:
            key, _url, png = await qr_login.generate_qr()
            self._pending[key] = "pending"
            b64 = base64.b64encode(png).decode("ascii")
            payload = {"ok": True, "key": key, "image": b64}
        except Exception as e:
            log.exception("生成二维码失败")
            payload = {"ok": False, "error": truncate(str(e))}

        return jsonify(payload) if jsonify else payload

    async def poll(self):
        try:
            from quart import jsonify, request

            key = (request.args.get("key") or "").strip()
        except Exception:
            jsonify = None
            key = ""

        if not key:
            payload = {"ok": False, "error": "missing key"}
            return jsonify(payload) if jsonify else payload

        if key not in self._pending:
            payload = {"ok": True, "code": 86038, "detail": ""}
            return jsonify(payload) if jsonify else payload

        try:
            code, cookie, note = await qr_login.poll_once(key)
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e))}
            return jsonify(payload) if jsonify else payload

        detail = note or ""
        if code == qr_login.ST_OK:
            if cookie:
                try:
                    key_bytes = load_or_create_key(self.plugin.data_dir)
                    save_cookie(self.plugin.data_dir, cookie, key_bytes)
                    self.plugin.reload_bili_client(cookie)
                    check = await self.plugin.bili.check_login()
                    if check.ok:
                        detail = check.detail
                        log.info("WebUI 扫码登录成功：%s", detail)
                    else:
                        detail = f"Cookie 已存但校验失败：{check.detail}"
                        log.warning(detail)
                except Exception as e:
                    detail = f"保存失败：{truncate(str(e))}"
                    log.exception("保存 Cookie 失败")
            else:
                detail = note or "扫码成功但没取到 Cookie"
            self._pending.pop(key, None)

        # detail 为空时给个中文状态，前端好显示
        if not detail and code != qr_login.ST_OK:
            detail = qr_login.status_text(code)

        payload = {"ok": True, "code": code, "detail": detail}
        return jsonify(payload) if jsonify else payload

    # ── 免密引导：用配置里的后台账号换 token ──────────────────
    async def bootstrap(self):
        """页面启动时调用。用插件配置里的后台账号换一个 token，用户不用手输。"""
        try:
            from quart import jsonify, request

            referer = request.headers.get("Referer", "") or ""
            host = request.headers.get("Host", "") or ""
            # 只接受同域发起的请求（挡掉直接 curl）
            if host and referer and host not in referer:
                payload = {"ok": False, "error": "来源不合法"}
                return jsonify(payload) if jsonify else payload
        except Exception:
            jsonify = None

        username = str(self.plugin._get("admin_username") or "").strip()
        password = str(self.plugin._get("admin_password") or "").strip()
        if not username or not password:
            payload = {"ok": False, "error": "没配后台账号"}
            return jsonify(payload) if jsonify else payload

        token = ""
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(
                    "http://127.0.0.1:6185/api/auth/login",
                    json={"username": username, "password": password},
                ) as resp:
                    data = await resp.json(content_type=None)
            token = ((data or {}).get("data") or {}).get("token") or ""
        except Exception as e:
            payload = {"ok": False, "error": f"登录失败：{truncate(str(e))}"}
            return jsonify(payload) if jsonify else payload

        if not token:
            payload = {"ok": False, "error": "账号或密码不对"}
        else:
            payload = {"ok": True, "token": token}

        return jsonify(payload) if jsonify else payload

    # ── 设置读写 ──────────────────────────────────────────────
    async def config_get(self):
        try:
            from quart import jsonify
        except Exception:
            jsonify = None

        try:
            schema = _load_schema()
            groups = []
            for title, keys in CONFIG_GROUPS:
                fields = []
                for key in keys:
                    meta = schema.get(key, {}) or {}
                    value = self.plugin._get(key, meta.get("default"))
                    ftype = meta.get("type", "string")
                    if key in ENUM_FIELDS:
                        ftype = "enum"
                    fields.append(
                        {
                            "key": key,
                            "label": FIELD_LABELS.get(key, meta.get("description", key)),
                            "hint": meta.get("hint", ""),
                            "type": ftype,
                            "value": _normalize(value, ftype),
                            "options": ENUM_FIELDS.get(key, []),
                        }
                    )
                groups.append({"title": title, "fields": fields})
            payload = {"ok": True, "groups": groups}
        except Exception as e:
            log.exception("读配置失败")
            payload = {"ok": False, "error": truncate(str(e))}

        return jsonify(payload) if jsonify else payload

    async def config_save(self):
        try:
            from quart import jsonify, request

            body = await request.get_json(silent=True) or {}
        except Exception:
            return {"ok": False, "error": "无法读取请求体"}

        cfg = body.get("config") or {}
        if not isinstance(cfg, dict):
            payload = {"ok": False, "error": "config 必须是对象"}
            return jsonify(payload) if jsonify else payload

        saved = 0
        try:
            for key, value in cfg.items():
                if key == "persona_file":
                    continue
                try:
                    self.plugin.cfg[key] = value
                    saved += 1
                except Exception:
                    continue

            save = getattr(self.plugin.cfg, "save_config", None)
            if callable(save):
                save()

            # 关键项变化时热重载
            if "persona" in cfg:
                try:
                    self.plugin.llm._persona = await self.plugin._load_persona()
                except Exception as e:
                    log.info("重载人设失败：%s", e)

            log.info("配置已保存 %d 项", saved)
            payload = {"ok": True, "saved": saved}
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e))}

        return jsonify(payload) if jsonify else payload

    # ── 人设列表 ──────────────────────────────────────────────
    async def personas(self):
        try:
            from quart import jsonify
        except Exception:
            jsonify = None

        items: list[dict] = []
        try:
            pm = getattr(self.plugin.context, "persona_manager", None)
            if pm is not None:
                for name in ("get_all_personas_v3", "get_all_personas"):
                    fn = getattr(pm, name, None)
                    if fn is None:
                        continue
                    personas = fn()
                    if hasattr(personas, "__await__"):
                        personas = await personas
                    for p in personas or []:
                        pid = getattr(p, "persona_id", None) or (
                            p.get("persona_id") if isinstance(p, dict) else None
                        )
                        pname = getattr(p, "name", None) or (
                            p.get("name") if isinstance(p, dict) else None
                        )
                        if pid:
                            items.append({"id": str(pid), "name": str(pname or pid)})
                    if items:
                        break
            payload = {"ok": True, "items": items}
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e)), "items": []}

        return jsonify(payload) if jsonify else payload

    # ── 状态 ──────────────────────────────────────────────────
    async def status(self):
        try:
            from quart import jsonify
        except Exception:
            jsonify = None

        payload: dict = {"ok": True, "logged_in": False}
        try:
            logged_in = bool(self.plugin.bili and self.plugin.bili.uid)
            payload["logged_in"] = logged_in

            if logged_in:
                check = await self.plugin.bili.check_login()
                payload["logged_in"] = check.ok
                payload["name"] = check.detail if check.ok else ""
                payload["uid"] = self.plugin.bili.uid

            picked, rounds = await self.plugin.store.quota_today()
            payload["picked"] = picked
            payload["rounds"] = rounds
            payload["max_per_day"] = int(self.plugin._num("max_per_day", 5))
            payload["at_users"] = list(self.plugin._get("at_users") or [])
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e))}

        return jsonify(payload) if jsonify else payload

    # ── 互关列表 ──────────────────────────────────────────────
    async def followings(self):
        try:
            from quart import jsonify
        except Exception:
            jsonify = None

        try:
            if not self.plugin.bili or not self.plugin.bili.uid:
                payload = {"ok": False, "error": "还没登录"}
            else:
                items = await self.plugin.bili.get_followings(limit=100)
                payload = {"ok": True, "items": items}
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e))}

        return jsonify(payload) if jsonify else payload

    # ── 保存 @ 名单 ───────────────────────────────────────────
    async def at_users(self):
        try:
            from quart import jsonify, request

            body = await request.get_json(silent=True) or {}
            users = body.get("at_users") or []
            if not isinstance(users, list):
                users = []
            clean = [str(u).strip() for u in users if str(u).strip()]

            self.plugin.cfg["at_users"] = clean
            save = getattr(self.plugin.cfg, "save_config", None)
            if callable(save):
                save()

            log.info("已更新 @ 名单：%s", clean)
            payload = {"ok": True, "at_users": clean}
        except Exception as e:
            payload = {"ok": False, "error": truncate(str(e))}
            jsonify = None

        return jsonify(payload) if jsonify else payload
