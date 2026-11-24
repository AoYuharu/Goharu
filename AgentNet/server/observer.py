"""观察台 HTTP 服务：手机端视图（消息流水 + 状态机 + 附件）。

stdlib ThreadingHTTPServer，零额外依赖。端点：
  GET  /                → 移动端页面
  GET  /api/messages    → ?limit=100&after=<id>&filter=all|mention
  GET  /api/presence    → {agents: {id: {name, state, online, last_seen, profile}}}
  GET  /api/health      → {ok}
  POST /api/files       → 智能体上传附件（ed25519 签名头鉴权）
  POST /api/send        → 观察台输入框：以 owner 身份广播（必须配置 token）
  GET  /files/<id>      → 下载附件
token 非空时要求 ?token=<token> 或 X-Token 头（页面自动从 URL 透传）。
上传鉴权头：X-Agent-Id / X-Timestamp（unix 秒）/ X-Signature，
签名内容 = "agent_id\\ntimestamp\\nsha256hex(body)"，时间窗 ±300s。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from AgentNet.identity import Identity

logger = logging.getLogger(__name__)

PAGE = (Path(__file__).with_name("observer_page.html")).read_text(encoding="utf-8")

UPLOAD_MAX_BYTES = 2 * 1024 * 1024 * 1024   # 2GB（流式落盘，不进内存）
UPLOAD_TS_WINDOW = 300


class ObserverServer:
    def __init__(self, message_log, presence, host: str = "0.0.0.0",
                 port: int = 6001, token: str = "",
                 file_store=None, whitelist=None, on_send=None,
                 on_control=None, pending_asks=None, on_answer=None,
                 on_floor=None, floor_snapshot=None):
        self.message_log = message_log
        self.presence = presence
        self.token = token or ""
        self.file_store = file_store
        self.whitelist = whitelist
        self.on_send = on_send  # hub.submit_owner_message(text, mentions, attachments)
        self.on_control = on_control  # hub.submit_owner_control(target, action, name)
        self.pending_asks = pending_asks  # hub.pending_asks_snapshot() -> list
        self.on_answer = on_answer  # hub.submit_answer(request_id, answer) -> dict
        self.on_floor = on_floor  # hub.submit_floor_request(action, target, persona, note)
        self.floor_snapshot = floor_snapshot  # hub.floor_snapshot() -> dict|None
        self._httpd = ThreadingHTTPServer((host, port), self._make_handler())
        self._thread = None

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # 静默访问日志（防刷屏）
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self, qs: dict) -> bool:
                if not outer.token:
                    return True
                return (qs.get("token", [""])[0] == outer.token
                        or self.headers.get("X-Token", "") == outer.token)

            def do_GET(self):
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                try:
                    if url.path == "/":
                        self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                        return
                    if not self._authed(qs):
                        self._send(401, b'{"ok":false,"error":"token required"}')
                        return
                    if url.path == "/api/health":
                        self._send(200, json.dumps({"ok": True}).encode())
                        return
                    if url.path == "/api/presence":
                        data = {"agents": outer.presence.snapshot()}
                        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
                        return
                    if url.path == "/api/requests":
                        data = {"requests": outer.pending_asks() if outer.pending_asks else []}
                        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
                        return
                    if url.path == "/api/floor":
                        data = {"floor": outer.floor_snapshot() if outer.floor_snapshot else None}
                        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
                        return
                    if url.path == "/api/messages":
                        rows = outer.message_log.recent(
                            limit=int(qs.get("limit", ["100"])[0]),
                            after_id=int(qs.get("after", ["0"])[0]),
                            filter_=qs.get("filter", ["all"])[0])
                        self._send(200, json.dumps(
                            {"messages": rows}, ensure_ascii=False).encode("utf-8"))
                        return
                    if url.path.startswith("/files/"):
                        self._serve_file(url.path[len("/files/"):])
                        return
                    self._send(404, b'{"ok":false,"error":"not found"}')
                except Exception as e:
                    logger.exception("observer 请求失败: %s", url.path)
                    self._send(500, json.dumps({"ok": False, "error": str(e)}).encode())

            def _serve_file(self, file_id: str):
                if outer.file_store is None:
                    self._send(404, b'{"ok":false,"error":"file store disabled"}')
                    return
                p = outer.file_store.path(file_id)
                if p is None:
                    self._send(404, b'{"ok":false,"error":"file not found"}')
                    return
                import mimetypes

                mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
                inline = mime.startswith(("image/", "video/", "audio/"))
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                if inline:
                    self.send_header("Content-Disposition", "inline")
                else:
                    self.send_header("Content-Disposition",
                                     f"attachment; filename*=UTF-8''{file_id}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                try:
                    if url.path == "/api/send":
                        self._handle_send(qs)
                        return
                    if url.path == "/api/answer":
                        self._handle_answer(qs)
                        return
                    if url.path == "/api/control":
                        self._handle_control(qs)
                        return
                    if url.path == "/api/floor":
                        self._handle_floor(qs)
                        return
                    if url.path == "/api/files":
                        self._handle_upload(qs)
                        return
                    self._send(404, b'{"ok":false,"error":"not found"}')
                except Exception as e:
                    logger.exception("observer POST 失败: %s", url.path)
                    self._send(500, json.dumps({"ok": False, "error": str(e)}).encode())

            def _handle_control(self, qs: dict):
                """中控动作：清除目标智能体的会话/记忆（仅 token 持有人）。"""
                if outer.on_control is None:
                    self._send(503, b'{"ok":false,"error":"control disabled"}')
                    return
                if not outer.token or not self._authed(qs):
                    self._send(403, b'{"ok":false,"error":"token required for control"}')
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 4096:
                    self._send(413, b'{"ok":false,"error":"bad size"}')
                    return
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    self._send(400, b'{"ok":false,"error":"bad json"}')
                    return
                target = str(req.get("target") or "")
                action = str(req.get("action") or "")
                if action not in ("clear_context", "clear_memory", "clear_messagebox",
                                  "rename", "security"):
                    self._send(400, b'{"ok":false,"error":"unknown action"}')
                    return
                name = str(req.get("name") or "").strip() or None
                if action == "rename" and not name:
                    self._send(400, b'{"ok":false,"error":"name required for rename"}')
                    return
                extra = None
                if action == "security":
                    # 规则清单透传（端侧应用 + 写回各自 config.yaml）；allow_confirmation
                    # 为可选三态开关。校验宽松但限量，防超大载荷。
                    extra = {}
                    for key in ("dangerous_commands", "require_confirmation"):
                        if key in req:
                            val = req[key]
                            if (not isinstance(val, list) or len(val) > 100
                                    or any(not isinstance(x, str) or len(x) > 200
                                           for x in val)):
                                self._send(400, b'{"ok":false,"error":"bad rules list"}')
                                return
                            extra[key] = val
                    if "allow_confirmation" in req:
                        extra["allow_confirmation"] = bool(req["allow_confirmation"])
                    if not extra:
                        self._send(400, b'{"ok":false,"error":"no security fields"}')
                        return
                result = outer.on_control(target, action, name=name, extra=extra)
                code = 200 if result.get("ok") else 500
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"))
                logger.info("观察台中控: %s → %s", action, target)

            def _handle_floor(self, qs: dict):
                """话语权切换（仅 token 持有人）：set/clear，hub 广播全网。"""
                if outer.on_floor is None:
                    self._send(503, b'{"ok":false,"error":"floor disabled"}')
                    return
                if not outer.token or not self._authed(qs):
                    self._send(403, b'{"ok":false,"error":"token required for floor"}')
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 4096:
                    self._send(413, b'{"ok":false,"error":"bad size"}')
                    return
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    self._send(400, b'{"ok":false,"error":"bad json"}')
                    return
                action = str(req.get("action") or "")
                if action not in ("set", "clear"):
                    self._send(400, b'{"ok":false,"error":"unknown action"}')
                    return
                result = outer.on_floor(
                    action,
                    str(req.get("target") or ""),
                    str(req.get("persona") or "main"),
                    str(req.get("note") or ""))
                code = 200 if result.get("ok") else 500
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"))
                logger.info("观察台话语权: %s target=%s persona=%s",
                            action, req.get("target"), req.get("persona"))

            def _handle_answer(self, qs: dict):
                """观察台作答：把答案回送给挂起询问的发起智能体（须 token）。"""
                if outer.on_answer is None:
                    self._send(503, b'{"ok":false,"error":"answer disabled"}')
                    return
                if not outer.token or not self._authed(qs):
                    self._send(403, b'{"ok":false,"error":"token required for answer"}')
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 65536:
                    self._send(413, b'{"ok":false,"error":"bad size"}')
                    return
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    self._send(400, b'{"ok":false,"error":"bad json"}')
                    return
                request_id = str(req.get("request_id") or "")
                if not request_id:
                    self._send(400, b'{"ok":false,"error":"request_id required"}')
                    return
                result = outer.on_answer(request_id, req.get("answer"))
                code = 200 if result.get("ok") else 500
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"))
                logger.info("观察台作答: %s", request_id)

            def _handle_send(self, qs: dict):
                if outer.on_send is None:
                    self._send(503, b'{"ok":false,"error":"send disabled"}')
                    return
                # 写接口强制 token：开放观察台不给注入通道
                if not outer.token or not self._authed(qs):
                    self._send(403, b'{"ok":false,"error":"token required for send"}')
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > 65536:
                    self._send(413, b'{"ok":false,"error":"bad size"}')
                    return
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    self._send(400, b'{"ok":false,"error":"bad json"}')
                    return
                text = str(req.get("text") or "").strip()
                mentions = [str(m) for m in (req.get("mentions") or []) if m]
                if not text:
                    self._send(400, b'{"ok":false,"error":"text required"}')
                    return
                result = outer.on_send(text, mentions=mentions or None)
                code = 200 if result.get("ok") else 500
                self._send(code, json.dumps(result, ensure_ascii=False).encode("utf-8"))
                logger.info("观察台指令: %s (mentions=%s)", text[:60], mentions)

            def _handle_upload(self, qs: dict):
                if outer.file_store is None or outer.whitelist is None:
                    self._send(503, b'{"ok":false,"error":"upload disabled"}')
                    return
                agent_id = self.headers.get("X-Agent-Id", "")
                ts_raw = self.headers.get("X-Timestamp", "")
                sig = self.headers.get("X-Signature", "")
                record = outer.whitelist.agent(agent_id) if agent_id else None
                if record is None:
                    self._send(403, b'{"ok":false,"error":"unknown agent"}')
                    return
                try:
                    ts = int(ts_raw)
                except ValueError:
                    self._send(403, b'{"ok":false,"error":"bad timestamp"}')
                    return
                if abs(time.time() - ts) > UPLOAD_TS_WINDOW:
                    self._send(403, b'{"ok":false,"error":"timestamp expired"}')
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > UPLOAD_MAX_BYTES:
                    self._send(413, b'{"ok":false,"error":"bad size"}')
                    return
                # 流式落盘：1MB 分块读 body 进临时文件，边读边算 sha256——
                # 2GB 附件也不进内存（旧实现 read() 整收，25MB 上限即因此而来）
                import hashlib
                import tempfile

                tmp = tempfile.NamedTemporaryFile(
                    mode="wb", delete=False, dir=str(outer.file_store.dir),
                    prefix=".upload_", suffix=".part")
                sha = hashlib.sha256()
                remaining = length
                try:
                    while remaining > 0:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ConnectionError("body truncated")
                        tmp.write(chunk)
                        sha.update(chunk)
                        remaining -= len(chunk)
                    tmp.close()
                except Exception:
                    tmp.close()
                    Path(tmp.name).unlink(missing_ok=True)
                    raise
                signed = f"{agent_id}\n{ts_raw}\n{sha.hexdigest()}".encode()
                if not Identity.verify(record["pubkey"], signed, sig):
                    Path(tmp.name).unlink(missing_ok=True)
                    self._send(403, b'{"ok":false,"error":"bad signature"}')
                    return
                name = unquote(self.headers.get("X-Filename", "") or
                               qs.get("name", ["file"])[0])
                meta = outer.file_store.commit(name, Path(tmp.name),
                                               sha.hexdigest(), length)
                logger.info("附件上传: %s ← %s (%d bytes)", meta["id"], agent_id, length)
                self._send(200, json.dumps(meta, ensure_ascii=False).encode("utf-8"))

        return Handler

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="agentnet-observer", daemon=True)
        self._thread.start()
        host, port = self._httpd.server_address
        logger.info("观察台监听 %s:%d（token=%s）", host, port,
                    "on" if self.token else "off")
