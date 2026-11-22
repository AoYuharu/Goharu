"""AgentNet 客户端 SDK（薄客户端）。

职责边界：identity 握手、连接管理（自动重连/心跳）、离线消息拉取、
消息入盒 + 分类回调。"怎么回复"永远不在 SDK 里——那是宿主智能体的事。

接线（Goharu 侧）：AgentNetChannel 持有本客户端，
- on_urgent(mention/dm) → session.enqueue() 唤醒 turn
- group → 只静默入 MessageBox
- 状态自报 state_provider 映射 session._processing（working/idle）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import websockets

from .identity import Identity
from .messagebox import MessageBox
from .protocol import (
    CLASS_DM,
    CLASS_GROUP,
    CLASS_MENTION,
    canonical_bytes,
    classify,
    dumps,
    make_envelope,
    parse,
)

logger = logging.getLogger(__name__)

OnUrgent = Callable[[dict, str], Any]           # (envelope, class) mention/dm 立即唤醒
OnEvent = Callable[[str, dict], Any]            # 通用事件（welcome/presence_state/...）
StateProvider = Callable[[], str]               # 返回 idle|working|maintenance


def default_device_profile() -> dict:
    """本机设备属性（握手档案用，观察台展示）。"""
    return {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": platform.python_version(),
    }


class AgentNetClient:
    def __init__(
        self,
        identity: Identity,
        url: str,
        messagebox: Optional[MessageBox] = None,
        state_provider: Optional[StateProvider] = None,
        on_urgent: Optional[OnUrgent] = None,
        on_message: Optional[OnUrgent] = None,
        on_event: Optional[OnEvent] = None,
        on_control: Optional[Callable[[dict], Any]] = None,
        heartbeat_seconds: int = 30,
        reconnect_max_seconds: int = 60,
        profile: Optional[dict] = None,
        http_base: str = "",
    ):
        self.identity = identity
        self.url = url
        self.box = messagebox
        self.http_base = (http_base or "").rstrip("/")
        # 档案：{focus: 侧重方向文本, tags: [...], device: {...}}——握手时上报
        prof = dict(profile or {})
        prof.setdefault("device", default_device_profile())
        self._profile = prof
        self._state_provider = state_provider or (lambda: "idle")
        self._on_urgent = on_urgent
        # on_message：全量消息回调（env, class）——设置了就替代 on_urgent 的分类分发，
        # 由宿主自行决定各类消息的处理（AgentNetChannel 的"关注期"模型靠它实现）
        self._on_message = on_message
        self._on_event = on_event
        self._on_control = on_control
        self._heartbeat_seconds = heartbeat_seconds
        self._reconnect_max = reconnect_max_seconds

        self._ws: Any = None
        self._connected = asyncio.Event()
        self._closed = False
        self._presence_waiter: Optional[asyncio.Future] = None
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------- 连接
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def connect_forever(self) -> None:
        """重连循环（退避 1s→max）。close() 后退出。"""
        backoff = 1
        while not self._closed:
            try:
                await self._run_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("agentnet 连接断开: %s（%ds 后重连）", e, backoff)
                self._connected.clear()
                if not self._closed:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._reconnect_max)

    async def _run_once(self) -> None:
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            await self._handshake()
            self._connected.set()
            logger.info("agentnet 已连接: %s (%s)", self.url, self.identity.agent_id)
            hb = asyncio.create_task(self._heartbeat_loop())
            try:
                await self._recv_loop()
            finally:
                hb.cancel()
                self._connected.clear()
                self._ws = None

    async def _handshake(self) -> None:
        env = make_envelope(
            sender=self.identity.agent_id, to="server", type_="hello",
            payload={
                "agent_id": self.identity.agent_id,
                "name": self.identity.name,
                "pubkey": self.identity.pubkey_b64,
                "profile": self._profile,
            },
        )
        env["sig"] = self.identity.sign(canonical_bytes(env))
        await self._send(env)
        reply = parse(await self._ws.recv())
        if reply["type"] == "auth_error":
            raise ConnectionError(f"握手被拒: {reply['payload'].get('reason')}")
        if reply["type"] != "welcome":
            raise ConnectionError(f"握手应答异常: {reply['type']}")
        self._emit_event("welcome", reply)
        # 上线即拉离线消息
        await self._send(make_envelope(
            sender=self.identity.agent_id, to="server", type_="mailbox_pull"))

    async def _recv_loop(self) -> None:
        async for raw in self._ws:
            try:
                env = parse(raw)
            except ValueError as e:
                logger.warning("agentnet 收到非法信封: %s", e)
                continue
            self._dispatch(env)

    def _dispatch(self, env: dict) -> None:
        t = env["type"]
        if t == "message":
            self._handle_message(env)
        elif t == "control":
            if self._on_control:
                try:
                    self._on_control(env)
                except Exception:
                    logger.exception("agentnet on_control 回调异常")
        elif t == "presence_state":
            if self._presence_waiter is not None and not self._presence_waiter.done():
                self._presence_waiter.set_result(env["payload"].get("agents", {}))
            self._emit_event("presence_state", env)
        else:
            self._emit_event(t, env)

    def _handle_message(self, env: dict) -> None:
        class_ = classify(env, self.identity.agent_id)
        if self._on_message is not None:
            # 全量回调模式：宿主自定各类消息处理（入盒也在宿主侧做）
            try:
                self._on_message(env, class_)
                return
            except Exception:
                logger.exception("agentnet on_message 回调异常")
                return
        if self.box is not None:
            peer = env["to"] if env.get("kind") == CLASS_GROUP else env["from"]
            payload = env.get("payload") or {}
            self.box.add(env.get("id"), class_, peer, env["from"],
                         str(payload.get("text", "")), env.get("ts"),
                         attachments=payload.get("attachments") or None)
        if class_ in (CLASS_MENTION, CLASS_DM) and self._on_urgent:
            try:
                self._on_urgent(env, class_)
            except Exception:
                logger.exception("agentnet on_urgent 回调异常")

    def _emit_event(self, kind: str, env: dict) -> None:
        if self._on_event:
            try:
                self._on_event(kind, env)
            except Exception:
                logger.exception("agentnet on_event 回调异常")

    # ------------------------------------------------------------- 发送
    async def _send(self, env: dict) -> None:
        env["sig"] = self.identity.sign(canonical_bytes(env))
        async with self._write_lock:
            if self._ws is None:
                raise ConnectionError("agentnet 未连接")
            await self._ws.send(dumps(env))

    async def send_message(self, to: str, text: str,
                           kind: str = "dm", mentions: Optional[list[str]] = None,
                           attachments: Optional[list] = None) -> str:
        """发送消息，返回 envelope id。attachments 为 upload_file() 的返回列表。"""
        payload: dict = {"text": text}
        if attachments:
            payload["attachments"] = attachments
        env = make_envelope(
            sender=self.identity.agent_id, to=to, type_="message",
            kind=kind, mentions=mentions, payload=payload)
        await self._send(env)
        return env["id"]

    async def upload_file(self, path: str) -> dict:
        """上传附件到 hub HTTP 端点，返回 {id, name, size, sha256, url}。

        鉴权：ed25519 签名头（不占用 WS 通道）。需 http_base 已配置。
        """
        if not self.http_base:
            raise RuntimeError("未配置 http_base，无法上传附件")
        p = Path(path).expanduser()
        data = await asyncio.to_thread(p.read_bytes)
        sha = hashlib.sha256(data).hexdigest()
        ts = str(int(time.time()))
        signed = f"{self.identity.agent_id}\n{ts}\n{sha}".encode()
        url = f"{self.http_base}/api/files?name={urllib.parse.quote(p.name)}"
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "X-Agent-Id": self.identity.agent_id,
            "X-Timestamp": ts,
            "X-Signature": self.identity.sign(signed),
            "X-Filename": urllib.parse.quote(p.name),
            "Content-Type": "application/octet-stream",
        })

        def _do() -> dict:
            import json

            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return dict(await asyncio.to_thread(_do))

    async def query_presence(self, timeout: float = 10) -> dict:
        """查状态机快照：{agent_id: {state, last_seen, name}}。"""
        if self._presence_waiter is not None and not self._presence_waiter.done():
            raise RuntimeError("已有进行中的 presence 查询")
        self._presence_waiter = asyncio.get_running_loop().create_future()
        try:
            await self._send(make_envelope(
                sender=self.identity.agent_id, to="server", type_="presence_query"))
            return await asyncio.wait_for(self._presence_waiter, timeout)
        finally:
            self._presence_waiter = None

    async def update_profile(self, focus: Optional[str] = None,
                             tags: Optional[list] = None) -> None:
        """更新自己的档案并上报 hub（focus/tags 任一给出才更新对应字段）。"""
        if focus is not None:
            self._profile["focus"] = str(focus)
        if tags is not None:
            self._profile["tags"] = [str(t) for t in tags]
        await self._send(make_envelope(
            sender=self.identity.agent_id, to="server", type_="profile_update",
            payload={"profile": self._profile}))

    # ------------------------------------------------------------- 心跳
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._send(make_envelope(
                    sender=self.identity.agent_id, to="server", type_="presence_update",
                    payload={"state": self._state_provider()}))
            except Exception as e:
                logger.warning("agentnet 心跳失败: %s", e)
                return

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
