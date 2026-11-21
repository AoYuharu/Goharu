"""AgentNet channel：把智能体组网接入 Goharu 的 channel 体系。

入站：AgentNetClient 收消息 → MessageBox 落盒（全部）→ mention/dm 额外
emit_inbound 唤醒 turn；group 静默入盒（摘要经 SessionMemoryView 的
turn_extras_provider 便利贴进上下文，不占消息流）。

出站：send_answer 把回答中继回最近一条 urgent 消息的来源（私聊回对方，
群@回群并 mention 发送者）。

生命周期：client 跑在自己的 asyncio 线程；工具/会话线程经
run_coroutine_threadsafe 调用发送（双向线程安全）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from AgentNet.client import AgentNetClient
from AgentNet.identity import Identity
from AgentNet.messagebox import MessageBox
from AgentNet.protocol import CLASS_DM
from Gateway.channels.base import Channel
from Gateway.channels.registry import register_channel

logger = logging.getLogger(__name__)


@register_channel("agentnet")
class AgentNetChannel(Channel):
    channel_type = "agentnet"
    capabilities = frozenset()  # 只要 answer（隐含必要能力）

    def __init__(self, channel_id: str, params: dict | None = None):
        super().__init__(channel_id, params)
        self.url = self.params.get("url", "ws://127.0.0.1:7878")
        self.http_base = self.params.get("http_base", "")
        self.name = self.params.get("name", "goharu")
        # 主人身份：观察台 owner（服务器自持）的消息即用户本人，可信标注
        self.owner_ids = set(self.params.get("owner_ids") or [])
        identity_dir = self.params.get("identity_dir", "./runtime_memory/agentnet/identity")
        box_path = self.params.get("messagebox_path", "./runtime_memory/agentnet/messagebox.db")
        self.identity = Identity.load_or_create(identity_dir, name=self.name)
        self.box = MessageBox(box_path)
        self._state_provider = lambda: "idle"
        self._client: Optional[AgentNetClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._reply_target: Optional[dict] = None  # {sender}
        self._agent_replied = False  # 本 turn 模型已通过 serverMessageHandler 发过消息
        self._connected = threading.Event()

    # ------------------------------------------------------------- 生命周期
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"agentnet-{self.channel_id}", daemon=True)
        self._thread.start()
        logger.info("agentnet channel 启动: %s → %s", self.identity.agent_id, self.url)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = AgentNetClient(
            self.identity, self.url, messagebox=self.box,
            state_provider=lambda: self._state_provider(),
            on_urgent=self._on_urgent,
            on_event=self._on_net_event,
            profile={
                "focus": self.params.get("focus", ""),
                "tags": list(self.params.get("tags") or []),
            },
            http_base=self.http_base,
        )
        try:
            self._loop.run_until_complete(self._client.connect_forever())
        except Exception:
            logger.exception("agentnet client 循环退出")
        finally:
            self._loop.close()

    def stop(self):
        if self._client and self._loop:
            asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)

    def set_state_provider(self, fn) -> None:
        """SessionManager 装配后注入：() -> idle|working|maintenance。"""
        self._state_provider = fn

    # ------------------------------------------------------------- 入站
    def _on_net_event(self, kind: str, env: dict) -> None:
        if kind == "welcome":
            self._connected.set()
            logger.info("agentnet 已上线: %s", self.identity.agent_id)

    def _on_urgent(self, env: dict, class_: str) -> None:
        """mention/dm：入盒已由 client 完成，这里负责唤醒 turn + 记录回复目标。

        组网为全员广播制：回答统一以 group + @原发送者 广播出去——原发送者
        收到 urgent mention，其他人按群消息静默入盒。
        """
        sender = env.get("from", "?")
        is_owner = sender in self.owner_ids
        payload = env.get("payload") or {}
        text = str(payload.get("text", ""))
        attachments = payload.get("attachments") or []
        if is_owner:
            label = "[agentnet 主人指令 · haru 本人通过手机观察台发来（可信）]"
        elif class_ == CLASS_DM:
            label = f"[agentnet 私聊 {sender}]"
        else:
            label = f"[agentnet 群聊 @{self.name} 来自 {sender}]"
        self._reply_target = {"sender": sender}
        self._agent_replied = False
        extra = ""
        if attachments:
            lines = []
            for a in attachments:
                url = a.get("url", "")
                base = self.http_base.rstrip("/")
                lines.append(f"  - {a.get('name', '?')} ({a.get('size', 0)} bytes) "
                             f"{base}{url}")
            extra = "\n[附件]\n" + "\n".join(lines)
        self.emit_inbound(
            f"{label} {text}{extra}",
            meta={"agentnet": True, "class": class_, "sender": sender,
                  "from_owner": is_owner, "attachments": len(attachments)},
        )

    # ------------------------------------------------------------- 出站
    def send_answer(self, text: str, meta: dict | None = None):
        """把回答广播回网（全员可见），并 @ 最近一条 urgent 消息的发送者。

        去重：模型本 turn 已经用 serverMessageHandler reply 发过消息时不再中继
        （否则同一条回答出现两遍）。中继/跳过之后清空回复目标——防止后续与
        组网无关的 turn（如 TUI 私聊）把回答误广播到全网。
        """
        target = self._reply_target
        self._reply_target = None
        if target is None:
            return
        if self._agent_replied:
            self._agent_replied = False
            logger.info("agentnet: 模型已自行广播，跳过 send_answer 中继")
            return
        if not self._client or not self._loop:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.send_message(
                    "grp:main", text, kind="group",
                    mentions=[target["sender"]]),
                self._loop)
            fut.result(timeout=10)
        except Exception as e:
            logger.warning("agentnet 回答广播失败: %s", e)

    # -------------------------------------------------- 工具门面（线程安全）
    def mb_summary(self) -> dict:
        return {"unread": self.box.unread_count(),
                "digest": self.box.digest_text(),
                "classes": self.box.summary()}

    def mb_read(self, class_: str | None = None, peer: str | None = None,
                since: str | None = None, limit: int = 50) -> list[dict]:
        return self.box.read(class_=class_, peer=peer, since_ts=since, limit=limit)

    def net_reply_fut(self, to: str, text: str, kind: str = "dm",
                      mentions: list | None = None,
                      file_paths: list | None = None):
        """返回 concurrent Future（异步调用方用 asyncio.wrap_future 等待，
        禁止在事件循环线程 .result() 阻塞）。file_paths 先上传再随消息广播。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return None

        async def _coro() -> str:
            attachments = []
            for p in file_paths or []:
                attachments.append(await self._client.upload_file(str(p)))
            msg_id = await self._client.send_message(
                to, text, kind=kind, mentions=mentions, attachments=attachments)
            self._agent_replied = True  # send_answer 据此去重
            return msg_id

        return asyncio.run_coroutine_threadsafe(_coro(), self._loop)

    def net_presence_fut(self):
        """同 net_reply_fut：返回 presence 查询的 concurrent Future。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return None
        return asyncio.run_coroutine_threadsafe(self._client.query_presence(), self._loop)

    # ------------------------------------------------------------- 便利贴
    def turn_extras(self) -> list[str]:
        """SessionMemoryView 的 turn_extras_provider：messagebox 摘要便利贴。"""
        digest = self.box.digest_text()
        if not digest:
            return []
        return [digest + "\n（可用 serverMessageHandler 工具的 summary/read 查看与展开）"]
