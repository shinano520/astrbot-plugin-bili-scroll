"""WebUI 扫码登录页。注册在 AstrBot 的 /api 下，沿用后台登录态。"""

from __future__ import annotations

import base64
from typing import Any

from ..infra import qr_login
from ..infra.crypto import load_or_create_key, save_cookie
from ..infra.logger import setup, truncate

log = setup("bili_scroll.web")

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
    min-height:260px; background:#fff; border-radius:12px; padding:16px; position:relative;
  }
  .qrbox img{width:228px; height:228px; image-rendering:pixelated; display:block}
  .placeholder{color:#888; font-size:13px; text-align:center; padding:40px 12px}
  .status{
    display:flex; align-items:center; gap:8px; margin-top:16px;
    font-size:13px; color:var(--sub); min-height:22px;
  }
  .dot{width:8px; height:8px; border-radius:50%; background:var(--sub); flex:none}
  .dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.err{background:var(--err)}
  .dot.pulse{animation:pulse 1.2s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .actions{display:flex; gap:10px; margin-top:18px}
  button{
    flex:1; padding:10px 14px; border-radius:10px; border:1px solid var(--line);
    background:#22262f; color:var(--text); font-size:13px; cursor:pointer; transition:.15s;
  }
  button:hover:not(:disabled){background:#2a2f3a; border-color:#39404d}
  button.primary{background:var(--accent); border-color:var(--accent); color:#fff}
  button.primary:hover:not(:disabled){background:#3d7aec}
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

  <div class="qrbox" id="qrbox">
    <div class="placeholder">点下面的按钮生成二维码</div>
  </div>

  <div class="status"><span class="dot" id="dot"></span><span id="statusText">未开始</span></div>

  <div class="actions">
    <button class="primary" id="btnGen">生成二维码</button>
    <button id="btnCheck" disabled>我已扫码，查状态</button>
  </div>

  <div class="result" id="result"></div>

  <div class="tip">
    手机 B 站扫码后，确认时请选 <b>「记住我 / 长期」</b>。
    选 24 小时的话明天就得重扫。
  </div>
</div>

<script>
(function(){
  var btnGen = document.getElementById('btnGen');
  var btnCheck = document.getElementById('btnCheck');
  var qrbox = document.getElementById('qrbox');
  var dot = document.getElementById('dot');
  var statusText = document.getElementById('statusText');
  var result = document.getElementById('result');

  var currentKey = '';
  var timer = null;
  var polling = false;

  var BASE = '/api/astrbot_plugin_bili_scroll/';

  function setStatus(text, cls, pulse){
    statusText.textContent = text;
    dot.className = 'dot' + (cls ? ' ' + cls : '') + (pulse ? ' pulse' : '');
  }

  function showResult(text, ok){
    result.textContent = text;
    result.className = 'result ' + (ok ? 'ok' : 'err');
  }

  function hideResult(){
    result.className = 'result';
    result.textContent = '';
  }

  function stopTimer(){
    if (timer) { clearInterval(timer); timer = null; }
  }

  function genQr(){
    stopTimer();
    hideResult();
    btnGen.disabled = true;
    btnCheck.disabled = true;
    setStatus('正在生成…', 'warn', true);
    qrbox.innerHTML = '<div class="placeholder">生成中…</div>';

    fetch(BASE + 'qr', { credentials: 'same-origin' })
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (!d.ok) { throw new Error(d.error || '生成失败'); }
        currentKey = d.key;
        qrbox.innerHTML = '<img alt="二维码" src="data:image/png;base64,' + d.image + '">';
        setStatus('等待扫码', 'warn', true);
        btnCheck.disabled = false;
        startPolling();
      })
      .catch(function(e){
        setStatus('生成失败', 'err');
        qrbox.innerHTML = '<div class="placeholder">生成失败<br>' + e.message + '</div>';
        showResult('生成失败：' + e.message, false);
      })
      .finally(function(){ btnGen.disabled = false; });
  }

  function startPolling(){
    stopTimer();
    timer = setInterval(checkOnce, 2500);
  }

  function checkOnce(){
    if (!currentKey || polling) { return; }
    polling = true;
    fetch(BASE + 'poll?key=' + encodeURIComponent(currentKey), { credentials: 'same-origin' })
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (!d.ok) { return; }
        var code = d.code;
        if (code === 0) {
          stopTimer();
          setStatus('登录成功', 'ok');
          btnCheck.disabled = true;
          showResult('登录成功：' + (d.detail || '已保存凭证'), true);
          qrbox.innerHTML = '<div class="placeholder" style="color:#3ecf8e;font-size:15px">✓ 登录成功</div>';
        } else if (code === 86090) {
          setStatus('已扫码，请在手机上确认', 'warn', true);
        } else if (code === 86038) {
          stopTimer();
          setStatus('二维码已过期', 'err');
          btnCheck.disabled = true;
          showResult('二维码过期了，重新生成一个', false);
        } else {
          setStatus('等待扫码…', 'warn', true);
        }
      })
      .catch(function(){})
      .finally(function(){ polling = false; });
  }

  btnGen.addEventListener('click', genQr);
  btnCheck.addEventListener('click', function(){
    setStatus('查询中…', 'warn', true);
    checkOnce();
  });
})();
</script>
</body>
</html>
"""


class WebPage:
    """三个端点：page / qr / poll。全部走 AstrBot 的登录态。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, str] = {}

    async def page(self):
        try:
            from quart import Response

            return Response(PAGE_HTML, mimetype="text/html")
        except Exception as e:
            log.info("返回页面失败：%s", e)
            return PAGE_HTML

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

        # 已经登录过且 key 不在待处理里：直接回报成功
        if key not in self._pending:
            payload = {"ok": True, "code": 86038, "detail": ""}
            return jsonify(payload) if jsonify else payload

        try:
            code, cookie = await qr_login.poll_once(key)
        except Exception as e:
            log.info("轮询失败：%s", e)
            payload = {"ok": False, "error": truncate(str(e))}
            return jsonify(payload) if jsonify else payload

        detail = ""
        if code == qr_login.ST_OK:
            if cookie:
                try:
                    key_bytes = load_or_create_key(self.plugin.data_dir)
                    save_cookie(self.plugin.data_dir, cookie, key_bytes)
                    self.plugin.reload_bili_client(cookie)
                    check = await self.plugin.bili.check_login()
                    detail = check.detail if check.ok else f"Cookie 已存但校验失败：{check.detail}"
                    log.info("WebUI 扫码登录成功：%s", detail)
                except Exception as e:
                    detail = f"保存失败：{truncate(str(e))}"
            else:
                detail = "扫码成功但没取到 Cookie"
            self._pending.pop(key, None)

        payload = {"ok": True, "code": code, "detail": detail}
        return jsonify(payload) if jsonify else payload
