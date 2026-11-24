"""QQBot channel —— 腾讯 QQ Bot API v2（WebSocket 网关 + REST 发消息）。

协议复刻自 tencent-connect/openclaw-qqbot 与其 SDK（qqbot-nodejs）：
- 鉴权：POST https://bots.qq.com/app/getAppAccessToken（AppID+AppSecret →
  access_token，Authorization 前缀 "QQBot "，提前 5 分钟刷新）
- 收：GET /gateway 拿 wss → op10 HELLO → op2 IDENTIFY（或 op6 RESUME）→
  op0 事件分发；心跳 op1 / op11；每个带 s 的帧更新 last_seq
- 发：POST /v2/{users|groups}/{openid}/messages，被动回复带 msg_id +
  自增 msg_seq 去重；主动消息不带 msg_id（额度极低，慎用）

能力映射（Channel 接口）：
- send_answer   → 文本/markdown 消息（5000 字自动切分）
- send_thinking → C2C 输入状态（msg_type=6 input_notify）；群聊忽略
- emit_inbound  → C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE
                  （群聊默认只在 @机器人 时上送，require_mention 可关）

出站路由：channel 记住"最近活跃会话"（C2C openid 或 group_openid +
msg_id + 时间），send_answer 回复到该会话——被动回复优先（带 msg_id），
msg_id 过期（C2C 30min / 群 5min）降级主动消息。

params（config.yaml sessions[].channels[].params 或环境变量）：
  app_id        QQ Bot AppID（或 QQBOT_APP_ID）
  app_secret    AppSecret（或 QQBOT_CLIENT_SECRET）
  sandbox       true 走沙箱环境（默认 false）
  markdown      true 用 msg_type=2 markdown（需平台权限，默认 false 纯文本）
  require_mention  群聊只在 @机器人 时上送（默认 true）
  peers_path    已知会话持久化文件（默认 runtime_memory/channels/qqbot_<channel_id>_peers.json）

主动消息：channel 把每个来过的会话（C2C openid / group_openid）持久化到
peers_path，`send_proactive(text)` 向最近会话发主动消息（不带 msg_id）。
注意 QQ 平台主动消息额度极低（免费档每用户/群每月约 4 条），且 48h
未交互的用户可能拒收——主动推送只适合高价值通知，不能当推送系统用。
"""

import asyncio
import json
import logging
import os
import threading
import time

from Gateway.channels.base import ASK_PENDING_TTL, Channel
from Gateway.channels.registry import register_channel

logger = logging.getLogger(__name__)

API_BASE = "https://api.sgroup.qq.com"
API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

INTENT_GROUP_AND_C2C = 1 << 25   # C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE
INTENT_INTERACTION = 1 << 26     # INTERACTION_CREATE（按钮回调，预留）

TEXT_CHUNK_LIMIT = 5000          # 单条消息字符上限
MSG_ID_TTL = {"c2c": 1800, "group": 300}   # 被动 msg_id 有效期（秒）
_RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]

# 富媒体 file_type（QQ Bot API v2）：只自动识别图片/视频，其余一律按文件
# 发送——不发语音气泡（2026-10-31 用户决定：mp3 等音频按 file_type=4 送）
_MEDIA_TYPE_BY_EXT = {
    1: {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},   # 图片
    2: {".mp4", ".mov", ".avi", ".mkv"},                     # 视频
}
MEDIA_FILE_LIMIT = 5 * 1024 * 1024   # base64 直传单文件上限（更大需分片，暂不支持）


class QQBotClient:
    """QQ Bot API v2 最小客户端：token 管理 + REST 发消息。"""

    def __init__(self, app_id, app_secret, sandbox=False, http=None,
                 trust_env=False):
        self.app_id = str(app_id)
        self.app_secret = str(app_secret)
        self.api_base = API_BASE_SANDBOX if sandbox else API_BASE
        self._trust_env = trust_env   # QQ 是国内 API，默认不走系统代理（易抽风）
        self._http = http            # 可注入假 client（测试）；None 时惰性建
        self._token = None
        self._token_expire_at = 0.0
        self._msg_seq = 0

    async def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=30, trust_env=self._trust_env)
        return self._http

    async def get_token(self):
        # 提前 5 分钟刷新（与官方 SDK 同策略）；expires_in 可能是字符串
        if self._token and time.time() < self._token_expire_at - 300:
            return self._token
        http = await self._get_http()
        r = await http.post(TOKEN_URL, json={
            "appId": self.app_id, "clientSecret": self.app_secret,
        })
        data = r.json()
        self._token = data["access_token"]
        self._token_expire_at = time.time() + int(data.get("expires_in", 7200))
        logger.info("QQBot: access token refreshed (expires_in=%s)",
                    data.get("expires_in"))
        return self._token

    async def _headers(self):
        return {"Authorization": f"QQBot {await self.get_token()}"}

    async def get_gateway_url(self):
        http = await self._get_http()
        r = await http.get(f"{self.api_base}/gateway", headers=await self._headers())
        return r.json()["url"]

    def _next_msg_seq(self):
        # 同一 msg_id 多次回复必须变化 msg_seq，否则平台去重
        self._msg_seq = (self._msg_seq + 1) % 65536
        if self._msg_seq == 0:
            self._msg_seq = 1
        return self._msg_seq

    async def send_text(self, scene, target_openid, text, msg_id=None,
                        markdown=False):
        """发文本/markdown 消息。scene ∈ {"c2c", "group"}。

        markdown 可取 True/False/"auto"："auto" 先试原生 markdown
        （msg_type=2，需平台权限），被拒则降级为去语法纯文本。
        """
        http = await self._get_http()
        url = (f"{self.api_base}/v2/users/{target_openid}/messages" if scene == "c2c"
               else f"{self.api_base}/v2/groups/{target_openid}/messages")
        use_markdown = markdown in (True, "auto")
        results = []
        for chunk in _chunk_text(text, TEXT_CHUNK_LIMIT):
            body = ({"markdown": {"content": chunk}, "msg_type": 2} if use_markdown
                    else {"content": chunk, "msg_type": 0})
            body["msg_seq"] = self._next_msg_seq()
            if msg_id:
                body["msg_id"] = msg_id
            r = await http.post(url, json=body, headers=await self._headers())
            try:
                data = r.json()
            except Exception:
                data = {"http_status": r.status_code, "raw": r.text[:200]}
            if (markdown == "auto" and isinstance(data, dict) and data.get("code")
                    and use_markdown):
                # 无 markdown 权限 → 去语法纯文本重发
                logger.info("QQBot markdown rejected (%s), fallback plain",
                            data.get("code"))
                use_markdown = False
                body = {"content": strip_markdown(chunk), "msg_type": 0,
                        "msg_seq": self._next_msg_seq()}
                if msg_id:
                    body["msg_id"] = msg_id
                r = await http.post(url, json=body, headers=await self._headers())
                try:
                    data = r.json()
                except Exception:
                    data = {"http_status": r.status_code, "raw": r.text[:200]}
            if (isinstance(data, dict) and data.get("code") and msg_id):
                # 被动回复被拒（配额耗尽/msg_id 失效）→ 降级主动消息重发一次
                logger.info("QQBot passive reply rejected (%s), retry proactive",
                            data.get("code"))
                body = ({"markdown": {"content": chunk}, "msg_type": 2}
                        if use_markdown else {"content": chunk, "msg_type": 0})
                body["msg_seq"] = self._next_msg_seq()
                r = await http.post(url, json=body, headers=await self._headers())
                try:
                    data = r.json()
                except Exception:
                    data = {"http_status": r.status_code, "raw": r.text[:200]}
            results.append(data)
            if isinstance(data, dict) and data.get("code"):
                logger.warning("QQBot send failed: %s", data)
        return results

    async def send_typing(self, user_openid):
        """C2C 输入状态（"对方正在输入…"）。"""
        http = await self._get_http()
        body = {
            "msg_type": 6,
            "input_notify": {"input_type": 1, "input_second": 60},
            "msg_seq": self._next_msg_seq(),
        }
        try:
            await http.post(
                f"{self.api_base}/v2/users/{user_openid}/messages",
                json=body, headers=await self._headers(),
            )
        except Exception as e:
            logger.debug("QQBot typing notify failed: %s", e)

    async def send_with_keyboard(self, scene, target_openid, text, keyboard,
                                 msg_id=None):
        """发带 Inline Keyboard 的消息。markdown 优先，失败退纯文本。"""
        http = await self._get_http()
        url = (f"{self.api_base}/v2/users/{target_openid}/messages" if scene == "c2c"
               else f"{self.api_base}/v2/groups/{target_openid}/messages")

        def _body(msg_type, content_field, content):
            b = {"msg_type": msg_type, content_field: content,
                 "keyboard": keyboard, "msg_seq": self._next_msg_seq()}
            if msg_id:
                b["msg_id"] = msg_id
            return b

        # 先 markdown（openclaw 生产验证的组合），报错再退纯文本
        r = await http.post(url, json=_body(2, "markdown", {"content": text}),
                            headers=await self._headers())
        try:
            data = r.json()
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("code"):
            logger.info("QQBot keyboard(markdown) rejected %s, retry plain: %s",
                        data.get("code"), data)
            r = await http.post(url, json=_body(0, "content", text),
                                headers=await self._headers())
        return r

    async def acknowledge_interaction(self, interaction_id, code=0):
        """应答按钮回调（PUT /interactions/{id}）。不 ack 平台会重推。"""
        http = await self._get_http()
        try:
            await http.put(f"{self.api_base}/interactions/{interaction_id}",
                           json={"code": code}, headers=await self._headers())
        except Exception as e:
            logger.debug("QQBot interaction ack failed: %s", e)

    async def send_media(self, scene, target_openid, file_type, url=None,
                         file_data_b64=None, file_name=None):
        """两段式第一步：上传富媒体拿 file_info（TTL 约 1 小时）。"""
        http = await self._get_http()
        base = (f"{self.api_base}/v2/users/{target_openid}" if scene == "c2c"
                else f"{self.api_base}/v2/groups/{target_openid}")
        body = {"file_type": file_type, "srv_send_msg": False}
        if url:
            body["url"] = url
        elif file_data_b64:
            body["file_data"] = file_data_b64
        if file_name:
            body["file_name"] = file_name
        r = await http.post(f"{base}/files", json=body, headers=await self._headers())
        try:
            return r.json()
        except Exception:
            return {"http_status": r.status_code, "raw": r.text[:200]}

    async def send_media_message(self, scene, target_openid, file_info,
                                 caption="", msg_id=None):
        """两段式第二步：发携带 media 的消息（msg_type=7）。"""
        http = await self._get_http()
        url = (f"{self.api_base}/v2/users/{target_openid}/messages" if scene == "c2c"
               else f"{self.api_base}/v2/groups/{target_openid}/messages")
        body = {"msg_type": 7, "media": {"file_info": file_info},
                "msg_seq": self._next_msg_seq()}
        if caption:
            body["content"] = caption
        if msg_id:
            body["msg_id"] = msg_id
        r = await http.post(url, json=body, headers=await self._headers())
        try:
            return r.json()
        except Exception:
            return {"http_status": r.status_code, "raw": r.text[:200]}

    async def close(self):
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None


def _chunk_text(text, limit):
    text = str(text)
    if len(text) <= limit:
        return [text]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def strip_markdown(text: str) -> str:
    """markdown → 适合 QQ 纯文本渲染的干净文本（去语法保留内容）。

    QQ 原生 markdown 需平台权限；无权限时 msg_type=2 被拒，降级用本函数。
    """
    import re
    t = str(text)
    t = re.sub(r"```[a-zA-Z0-9]*\n?", "", t)            # 代码围栏标记
    t = re.sub(r"```", "", t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)                # 行内代码
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)        # 标题
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)          # 粗体
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", t)  # 斜体
    t = re.sub(r"~~([^~\n]+)~~", r"\1", t)              # 删除线
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[\1]", t)   # 图片 → [ alt ]
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)      # 链接 → 文字
    t = re.sub(r"^>\s?", "", t, flags=re.M)             # 引用
    t = re.sub(r"^(\s*)[-*+]\s+", r"\1· ", t, flags=re.M)  # 无序列表 → ·
    t = re.sub(r"^(\s*)\d+\.\s+", r"\1", t, flags=re.M)    # 有序列表 → 裸文本
    t = re.sub(r"^ {0,3}(-{3,}|\*{3,}|_{3,})\s*$", "————", t, flags=re.M)  # 分隔线
    return t.strip()


@register_channel("qqbot")
class QQBotChannel(Channel):
    """QQ Bot channel：WS 收 + REST 发，独立 asyncio 线程承载。"""

    channel_type = "qqbot"
    capabilities = frozenset({"thinking", "text_output", "file"})

    def __init__(self, channel_id, params=None):
        super().__init__(channel_id, params)
        self.app_id = self.params.get("app_id") or os.getenv("QQBOT_APP_ID", "")
        self.app_secret = (
            self.params.get("app_secret") or os.getenv("QQBOT_CLIENT_SECRET", "")
        )
        self.sandbox = bool(self.params.get("sandbox", False))
        # markdown：true=强制原生 md / false=纯文本 / "auto"=先试原生，
        # 无权限自动降级去语法纯文本（默认）
        self.markdown = self.params.get("markdown", "auto")
        self.require_mention = bool(self.params.get("require_mention", True))
        # 入站优先级：QQ 消息默认 high（插队 + 打断当前步骤，用户要求）；
        # 参数可覆写为 "normal" 恢复排队行为
        self.inbound_priority = str(self.params.get("priority", "high")).lower()

        self.client = QQBotClient(self.app_id, self.app_secret, self.sandbox)
        self._loop = None
        self._thread = None
        self._stop_event = None
        self._ws_factory = None          # 测试注入：async callable(url) → ws
        # 最近活跃会话：{"scene", "id", "msg_id", "ts"}
        self._last_conversation = None
        self._session_id = None
        self._last_seq = None
        self._last_error = ""            # 最近一次 ws 断开原因（health() 展示用）
        # 已知会话持久化（主动消息的寻址依据）
        self._peers_path = self.params.get(
            "peers_path",
            f"runtime_memory/channels/qqbot_{self.channel_id}_peers.json",
        )
        self._peers = self._load_peers()
        if self._last_conversation is None:
            self._last_conversation = self._latest_peer()

    # ------------------------------------------------------------------
    # 已知会话（known peers）持久化
    # ------------------------------------------------------------------

    def _load_peers(self):
        try:
            with open(self._peers_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_peers(self):
        try:
            import os as _os
            _os.makedirs(
                _os.path.dirname(self._peers_path) or ".", exist_ok=True,
            )
            with open(self._peers_path, "w", encoding="utf-8") as f:
                json.dump(self._peers, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("QQBot peers save failed: %s", e)

    def _record_peer(self, scene, target_id, msg_id=None, extra=None):
        """入站时登记/刷新会话（主动消息的寻址依据）。"""
        key = f"{scene}:{target_id}"
        peer = {
            "scene": scene, "id": target_id,
            "msg_id": msg_id, "ts": time.time(),
        }
        if extra:
            peer.update(extra)
        self._peers[key] = peer
        self._last_conversation = dict(peer)
        self._save_peers()

    def _latest_peer(self):
        """最近的已知会话（进程重启后的主动消息寻址回退）。"""
        if not self._peers:
            return None
        return max(self._peers.values(), key=lambda p: p.get("ts", 0))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self):
        if not self.app_id or not self.app_secret:
            logger.error("QQBot channel %s: 缺少 app_id/app_secret，未启动",
                         self.channel_id)
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"qqbot-{self.channel_id}",
        )
        self._thread.start()
        logger.info("QQBot channel %s started (sandbox=%s)",
                    self.channel_id, self.sandbox)

    def stop(self):
        if self._stop_event:
            self._stop_event.set()
        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self.client.close(), self._loop)
            except Exception:
                pass

    def health(self) -> dict:
        """QQ Bot 连通状态：缺凭据 / online / connecting / error。"""
        if not self.app_id or not self.app_secret:
            return {"ok": False, "state": "not_configured",
                    "detail": "缺少 QQBOT_APP_ID / QQBOT_CLIENT_SECRET"}
        if self._session_id:
            return {"ok": True, "state": "online", "detail": "已连接 QQ 官方 WebSocket"}
        if self._thread and self._thread.is_alive():
            return {"ok": False, "state": "connecting",
                    "detail": self._last_error or "连接中…"}
        return {"ok": False, "state": "error",
                "detail": self._last_error or "未启动"}

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_forever())
        except Exception as e:
            logger.error("QQBot ws loop crashed: %s", e)
        finally:
            try:
                self._loop.run_until_complete(self.client.close())
            except Exception:
                pass
            self._loop.close()

    # ------------------------------------------------------------------
    # WebSocket 接收循环（指数退避无限重连）
    # ------------------------------------------------------------------

    async def _ws_forever(self):
        attempt = 0
        while not (self._stop_event and self._stop_event.is_set()):
            try:
                await self._connect_once()
                attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                self._last_error = str(e)
                logger.warning("QQBot ws disconnected (%s), reconnect in %ss",
                               e, delay)
                attempt += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

    async def _connect_once(self):
        if self._ws_factory is not None:
            connect = self._ws_factory
        else:
            import websockets
            connect = websockets.connect

        gateway_url = await self.client.get_gateway_url()
        async with connect(gateway_url) as ws:
            hello = json.loads(await ws.recv())          # op=10 HELLO
            interval = hello["d"]["heartbeat_interval"] / 1000

            if self._session_id and self._last_seq is not None:
                await ws.send(json.dumps({"op": 6, "d": {
                    "token": f"QQBot {await self.client.get_token()}",
                    "session_id": self._session_id, "seq": self._last_seq}}))
            else:
                await ws.send(json.dumps({"op": 2, "d": {
                    "token": f"QQBot {await self.client.get_token()}",
                    "intents": INTENT_GROUP_AND_C2C | INTENT_INTERACTION,
                    "shard": [0, 1]}}))

            hb = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                async for raw in ws:
                    p = json.loads(raw)
                    if p.get("s") is not None:
                        self._last_seq = p["s"]           # 每个带 s 的帧都记
                    op = p.get("op")
                    if op == 0:                            # DISPATCH
                        t = p.get("t")
                        if t == "READY":
                            self._session_id = p["d"]["session_id"]
                            logger.info("QQBot READY, session=%s", self._session_id)
                        elif t == "RESUMED":
                            logger.info("QQBot RESUMED")
                        elif t == "C2C_MESSAGE_CREATE":
                            self._on_c2c_message(p["d"])
                        elif t == "GROUP_AT_MESSAGE_CREATE":
                            self._on_group_message(p["d"])
                        elif t == "INTERACTION_CREATE":
                            self._on_interaction(p["d"])
                    elif op == 7:                          # RECONNECT（保留 session）
                        logger.info("QQBot server requested reconnect")
                        break
                    elif op == 9:                          # INVALID_SESSION
                        if not p.get("d"):                 # 不可恢复：清 session 重 IDENTIFY
                            self._session_id = None
                            self._last_seq = None
                            self.client._token = None
                        await asyncio.sleep(3)
                        break
            finally:
                hb.cancel()

    async def _heartbeat(self, ws, interval):
        while True:
            await asyncio.sleep(interval)
            await ws.send(json.dumps({"op": 1, "d": self._last_seq}))

    # ------------------------------------------------------------------
    # 出站（session → QQ）：轮发被调，回复到最近活跃会话
    # ------------------------------------------------------------------

    def _schedule(self, coro):
        if self._loop is None:
            logger.warning("QQBot channel %s not started, outbound dropped",
                           self.channel_id)
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def send_answer(self, text, meta=None):
        conv = self._last_conversation
        if conv is None:
            logger.warning("QQBot: 无活跃会话，answer 无法路由（等待用户先发起）")
            return
        # 工具使用摘要：作为 answer 的前缀行（不单独发消息）
        summary = (meta or {}).get("tool_summary")
        if summary:
            text = f"🔧 工具：{summary}\n\n{text}"
        # 被动 msg_id 过期降级主动消息
        msg_id = conv.get("msg_id")
        ttl = MSG_ID_TTL.get(conv["scene"], 300)
        if msg_id and time.time() - conv.get("ts", 0) > ttl:
            msg_id = None
        self._schedule(self.client.send_text(
            conv["scene"], conv["id"], text,
            msg_id=msg_id, markdown=self.markdown,
        ))

    def send_text_output(self, text, meta=None):
        """模型的中间文本块 → 也作为消息送达（用户要求全量 text 转发）。"""
        conv = self._last_conversation
        if conv is None:
            return
        msg_id = conv.get("msg_id")
        ttl = MSG_ID_TTL.get(conv["scene"], 300)
        if msg_id and time.time() - conv.get("ts", 0) > ttl:
            msg_id = None
        self._schedule(self.client.send_text(
            conv["scene"], conv["id"], text,
            msg_id=msg_id, markdown=self.markdown,
        ))

    def send_thinking(self, text, meta=None):
        # thinking 内容不转发到 QQ（用户只要 TUI 里 assistant 的正文）——
        # text=None（spinner 语义）时仅发 C2C typing 指示
        conv = self._last_conversation
        if text is None and conv and conv["scene"] == "c2c":
            self._schedule(self.client.send_typing(conv["id"]))

    def send_file(self, path_or_url, caption="", meta=None,
                  force_type=None) -> bool:
        """发文件/图片/视频/语音到最近活跃会话（本机 ≤5MB base64 直传或公网 URL）。

        force_type（"image"/"video"/"file"）可覆盖按扩展名的推断。
        """
        conv = self._last_conversation
        if conv is None:
            logger.warning("QQBot send_file: 无活跃会话")
            return False

        from pathlib import Path
        import base64
        src = str(path_or_url).strip()
        is_url = src.lower().startswith(("http://", "https://"))
        file_name, file_data, url = None, None, None
        ext = Path(src.split("?")[0]).suffix.lower()
        _FORCE = {"image": 1, "video": 2, "file": 4}
        if force_type and str(force_type).lower() in _FORCE:
            file_type = _FORCE[str(force_type).lower()]
        else:
            file_type = next((ft for ft, exts in _MEDIA_TYPE_BY_EXT.items()
                              if ext in exts), 4)
        if is_url:
            url = src
        else:
            p = Path(src)
            if not p.is_file():
                logger.warning("QQBot send_file: 文件不存在 %s", src)
                return False
            if p.stat().st_size > MEDIA_FILE_LIMIT:
                logger.warning("QQBot send_file: %s 超过 %dMB base64 直传上限",
                               src, MEDIA_FILE_LIMIT // (1024 * 1024))
                return False
            file_data = base64.b64encode(p.read_bytes()).decode("ascii")
            file_name = p.name

        msg_id = conv.get("msg_id")
        ttl = MSG_ID_TTL.get(conv["scene"], 300)
        if msg_id and time.time() - conv.get("ts", 0) > ttl:
            msg_id = None

        async def _deliver():
            info = await self.client.send_media(
                conv["scene"], conv["id"], file_type, url=url,
                file_data_b64=file_data, file_name=file_name,
            )
            if not isinstance(info, dict) or not info.get("file_info"):
                logger.warning("QQBot send_file: /files 失败 %s", info)
                return
            await self.client.send_media_message(
                conv["scene"], conv["id"], info["file_info"],
                caption=caption, msg_id=msg_id,
            )

        self._schedule(_deliver())
        return True

    # ------------------------------------------------------------------
    # 提问三范式：单选/多选原生按钮，填空与批量走基类文本退化
    # ------------------------------------------------------------------

    _NATIVE_MAX_OPTIONS = 10   # 键盘行数上限保守值，超出退文本

    def _ask_conversation_key(self, msg=None, meta=None):
        conv = self._last_conversation
        if conv:
            return f"{conv['scene']}:{conv['id']}"
        return None

    def confirm_request(self, confirm_id, command, description="", timeout=300):
        """高危命令确认：原生 ✅/❌ 回调按钮（无会话时退文本 y/n）。"""
        conv = self._last_conversation
        if conv is None:
            super().confirm_request(confirm_id, command, description, timeout)
            return

        def btn(id_, label, data, style):
            return {
                "id": id_,
                "render_data": {"label": label, "visited_label": label, "style": style},
                "action": {"type": 1, "data": data,
                           "permission": {"type": 2}, "click_limit": 1},
                "group_id": f"confirm-{confirm_id[:24]}",
            }

        keyboard = {"content": {"rows": [{"buttons": [
            btn("allow", "✅ 允许", f"confirm:{confirm_id}:1", 1),
            btn("deny", "❌ 拒绝", f"confirm:{confirm_id}:0", 0),
        ]}]}}
        text = (
            f"⚠️ 高危命令确认（{int(timeout)}s 内有效）\n"
            f"{command[:800]}\n{description[:300]}")
        self._qq_confirm = {"id": str(confirm_id),
                            "expires": time.time() + int(timeout or 300) + 30}
        self._schedule(self.client.send_with_keyboard(
            conv["scene"], conv["id"], text, keyboard,
            msg_id=self._passive_msg_id()))

    def ask_questions(self, request_id, questions, meta=None):
        """入口：单题且有选项 → 原生按钮；其余（填空/批量/超限）退文本。"""
        if len(questions) != 1:
            super().ask_questions(request_id, questions, meta)
            return
        q = questions[0]
        labels = []
        for o in q.get("options") or []:
            labels.append(o.get("label", "") if isinstance(o, dict) else str(o))
        labels = [l for l in labels if l]
        if not labels or len(labels) > self._NATIVE_MAX_OPTIONS:
            super().ask_questions(request_id, questions, meta)
            return
        if q.get("multi_select"):
            self._ask_native_multi(request_id, q.get("question", ""), labels)
        else:
            self._ask_native_single(request_id, q.get("question", ""), labels)

    def _passive_msg_id(self):
        """新鲜 msg_id 才作被动回复凭证（C2C 30min / 群 5min）。"""
        conv = self._last_conversation
        if not conv:
            return None
        ttl = MSG_ID_TTL.get(conv["scene"], 300)
        if conv.get("msg_id") and time.time() - conv.get("ts", 0) <= ttl:
            return conv["msg_id"]
        return None

    def _ask_native_single(self, request_id, question, labels):
        """单选：每选项一个回调按钮，同 group 互斥，点即 resolve。"""
        def btn(i, label):
            return {
                "id": f"opt{i}",
                "render_data": {"label": label[:20], "visited_label": f"已选：{label[:16]}",
                                "style": 1 if i == 1 else 0},
                "action": {"type": 1,
                           "data": f"askuser:{request_id}:{i}",
                           "permission": {"type": 2}, "click_limit": 1},
                "group_id": f"ask-{request_id[:24]}",
            }

        rows = [ {"buttons": [btn(i, l) for i, l in enumerate(labels, 1)][r:r + 3]}
                 for r in range(0, len(labels), 3) ]
        keyboard = {"content": {"rows": rows}}
        text = f"{question}\n（请点按钮选择）"
        self._qq_ask = {
            "request_id": request_id, "labels": labels, "multi": False,
            "selected": [], "expires": time.time() + ASK_PENDING_TTL,
        }
        conv = self._last_conversation
        if conv is None:
            logger.warning("QQBot ask_single: 无活跃会话，退文本")
            super().ask_single(question, labels, request_id)
            return
        self._schedule(self.client.send_with_keyboard(
            conv["scene"], conv["id"], text, keyboard, msg_id=self._passive_msg_id()))

    def _ask_native_multi(self, request_id, question, labels):
        """多选：选项按钮切换勾选（visited_label 即视觉反馈）+ 确认/重选。"""
        def opt_btn(i, label):
            return {
                "id": f"opt{i}",
                "render_data": {"label": label[:20],
                                "visited_label": f"✓ {label[:18]}", "style": 0},
                "action": {"type": 1,
                           "data": f"askuser:{request_id}:t:{i}",
                           "permission": {"type": 2}, "click_limit": 10},
            }

        def act_btn(id_, label, data, style):
            return {
                "id": id_,
                "render_data": {"label": label, "style": style},
                "action": {"type": 1, "data": data,
                           "permission": {"type": 2}, "click_limit": 3},
            }

        opt_rows = [ {"buttons": [opt_btn(i, l) for i, l in enumerate(labels, 1)][r:r + 3]}
                     for r in range(0, len(labels), 3) ]
        opt_rows.append({"buttons": [
            act_btn("done", "✅ 提交", f"askuser:{request_id}:done", 1),
            act_btn("reset", "↺ 重选", f"askuser:{request_id}:reset", 0),
        ]})
        keyboard = {"content": {"rows": opt_rows}}
        text = f"{question}\n（多选：点按钮勾选/取消，选完点「✅ 提交」）"
        self._qq_ask = {
            "request_id": request_id, "labels": labels, "multi": True,
            "selected": [], "expires": time.time() + ASK_PENDING_TTL,
        }
        conv = self._last_conversation
        if conv is None:
            logger.warning("QQBot ask_multi: 无活跃会话，退文本")
            super().ask_multi(question, labels, request_id)
            return
        self._schedule(self.client.send_with_keyboard(
            conv["scene"], conv["id"], text, keyboard, msg_id=self._passive_msg_id()))

    def _on_interaction(self, d):
        """INTERACTION_CREATE：ack 后按 button_data 路由 ask 答案 / 高危确认。"""
        data = d.get("data") or {}
        resolved = data.get("resolved") or {}
        button_data = str(resolved.get("button_data", ""))
        self._schedule(self.client.acknowledge_interaction(d.get("id", "")))

        if button_data.startswith("confirm:"):
            conf = getattr(self, "_qq_confirm", None)
            if not conf or time.time() > conf["expires"]:
                return
            parts = button_data.split(":", 2)
            if len(parts) != 3 or parts[1] != conf["id"]:
                return
            self._qq_confirm = None
            resolve = getattr(self, "_resolve_confirmation", None)
            if resolve is not None:
                resolve(parts[1], parts[2] == "1")
            return

        if not button_data.startswith("askuser:"):
            return
        ask = getattr(self, "_qq_ask", None)
        if not ask or time.time() > ask["expires"]:
            return
        _, request_id, action = button_data.split(":", 2)
        if request_id != ask["request_id"]:
            return
        if action == "reset":
            ask["selected"] = []
            return
        if action == "done":
            if not ask["selected"]:
                return   # 空选不允许提交
            questions = [{"question": "", "multi_select": True}] if ask["multi"] else [{}]
            # multi_select 必然是批量契约；单选走单问契约
            self._qq_ask = None
            if ask["multi"]:
                self._dispatch_answer(request_id,
                                      [{"question": "", "options": ask["labels"],
                                        "multi_select": True}],
                                      [list(ask["selected"])])
            else:
                self._dispatch_answer(request_id, [{}], [ask["selected"][0]
                                                          if ask["selected"] else None])
            return
        # 选项 toggle / 单选直选
        if action.startswith("t:"):
            try:
                idx = int(action[2:])
            except ValueError:
                return
            label = ask["labels"][idx - 1] if 1 <= idx <= len(ask["labels"]) else None
            if label is None:
                return
            if label in ask["selected"]:
                ask["selected"].remove(label)
            else:
                ask["selected"].append(label)
            return
        try:
            idx = int(action)
        except ValueError:
            return
        label = ask["labels"][idx - 1] if 1 <= idx <= len(ask["labels"]) else None
        if label is None:
            return
        self._qq_ask = None
        self._dispatch_answer(request_id, [{}], [label])

    # ------------------------------------------------------------------
    # 入站：事件 → ChannelMessage
    # ------------------------------------------------------------------

    def _on_c2c_message(self, d):
        openid = (d.get("author") or {}).get("user_openid", "")
        content = str(d.get("content", "")).strip()
        if not openid or not content:
            return
        self._record_peer("c2c", openid, msg_id=d.get("id"))
        self.emit_inbound(content, meta={
            "scene": "c2c", "user": openid, "msg_id": d.get("id"),
        }, priority=self.inbound_priority)

    def _on_group_message(self, d):
        group_openid = d.get("group_openid", "")
        author = d.get("author") or {}
        member = author.get("member_openid", "")
        content = str(d.get("content", "")).strip()
        if not group_openid or not content:
            return
        if self.require_mention:
            mentions = d.get("mentions") or []
            if not any(m.get("is_you") for m in mentions):
                return
            # 清洗 @ 标记（<@openid> / <@!openid>）
            import re
            content = re.sub(r"<@!?\w+>", "", content).strip()
            if not content:
                return
        self._record_peer("group", group_openid, msg_id=d.get("id"),
                          extra={"last_user": member})
        self.emit_inbound(content, meta={
            "scene": "group", "group": group_openid, "user": member,
            "msg_id": d.get("id"),
        }, priority=self.inbound_priority)

    # ------------------------------------------------------------------
    # 主动消息
    # ------------------------------------------------------------------

    def send_proactive(self, text, scene=None, target=None):
        """主动推送（不带 msg_id）。

        scene/target 缺省用最近已知会话。返回是否已调度发送。
        注意平台额度：免费档每用户/群每月约 4 条，48h 未交互可能拒收。
        """
        if scene and target:
            conv = {"scene": scene, "id": target}
        else:
            conv = self._last_conversation or self._latest_peer()
        if conv is None:
            logger.warning("QQBot: 无任何已知会话，主动消息无法寻址")
            return False
        self._schedule(self.client.send_text(
            conv["scene"], conv["id"], text, msg_id=None,
            markdown=self.markdown,
        ))
        logger.info("QQBot proactive → %s:%s (%d chars)",
                    conv["scene"], conv["id"], len(str(text)))
        return True
