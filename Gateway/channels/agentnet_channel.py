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
import time
from pathlib import Path
from typing import Optional

from AgentNet.client import AgentNetClient
from AgentNet.identity import Identity
from AgentNet.messagebox import MessageBox
from AgentNet.protocol import CLASS_DM, CLASS_GROUP
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
        # 主人身份：组网唯一人类用户（观察台 owner）。其消息绝对可信。
        self.owner_ids = set(self.params.get("owner_ids") or [])
        # 安静期自动阅读：群内未@消息超过该秒数无新消息时，合并导入上下文
        self.quiet_seconds = float(self.params.get("quiet_seconds", 120))
        self._quiet_check_interval = 10.0
        identity_dir = self.params.get("identity_dir", "./runtime_memory/agentnet/identity")
        box_path = self.params.get("messagebox_path", "./runtime_memory/agentnet/messagebox.db")
        self.identity = Identity.load_or_create(identity_dir, name=self.name)
        self._restore_saved_profile()   # 自我更新的档案优先于 config 默认
        self.box = MessageBox(box_path)
        self._state_provider = lambda: "idle"
        self._can_auto_read = lambda: True       # SessionManager 注入（会话空闲才自动读群）
        self._control_handler = None             # SessionManager 注入（中控动作执行）
        self._client: Optional[AgentNetClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._reply_target: Optional[dict] = None  # {sender}
        self._agent_replied = False  # 本 turn 模型已通过 serverMessageHandler 发过消息
        self._connected = threading.Event()
        self._persist_profile()

    def _profile_path(self) -> Path:
        return Path(self.params.get("identity_dir",
                                    "./runtime_memory/agentnet/identity")) / "profile.json"

    def _restore_saved_profile(self) -> None:
        """启动时恢复自我更新的档案（profile.json 优先于 config 装配值）。"""
        import json

        try:
            saved = json.loads(self._profile_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if saved.get("focus"):
            self.params["focus"] = saved["focus"]
        if saved.get("tags"):
            self.params["tags"] = saved["tags"]
        if saved.get("name"):
            self.name = self.name or saved["name"]

    def _persist_profile(self) -> None:
        """端侧身份资料落盘（新机器加入组网时随握手上报）。"""
        import json

        profile = {
            "agent_id": self.identity.agent_id,
            "name": self.name,
            "pubkey": self.identity.pubkey_b64,
            "focus": self.params.get("focus", ""),
            "tags": list(self.params.get("tags") or []),
        }
        self._profile_path().write_text(
            json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------- 生命周期
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"agentnet-{self.channel_id}", daemon=True)
        self._thread.start()
        threading.Thread(target=self._quiet_watcher,
                         name=f"agentnet-quiet-{self.channel_id}",
                         daemon=True).start()
        logger.info("agentnet channel 启动: %s → %s", self.identity.agent_id, self.url)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = AgentNetClient(
            self.identity, self.url, messagebox=self.box,
            state_provider=lambda: self._state_provider(),
            on_urgent=self._on_urgent,
            on_event=self._on_net_event,
            on_control=self._on_control,
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

    def set_can_auto_read(self, fn) -> None:
        """SessionManager 注入：() -> bool——会话空闲（无 turn 进行中）时才自动读群。"""
        self._can_auto_read = fn

    def set_control_handler(self, fn) -> None:
        """SessionManager 注入中控动作执行器：fn(action: str) -> str（返回执行摘要）。"""
        self._control_handler = fn

    # ------------------------------------------------------------- 入站
    def _on_net_event(self, kind: str, env: dict) -> None:
        if kind == "welcome":
            self._connected.set()
            logger.info("agentnet 已上线: %s", self.identity.agent_id)

    def _on_control(self, env: dict) -> None:
        """中控消息：仅接受主人身份发出的动作，其余来源丢弃并记日志。"""
        sender = env.get("from", "")
        if sender not in self.owner_ids:
            logger.warning("拒绝非主人中控动作: from=%s payload=%s",
                           sender, env.get("payload"))
            return
        action = str((env.get("payload") or {}).get("action", ""))
        logger.info("执行主人中控动作: %s (from %s)", action, sender)
        if action == "clear_messagebox":
            self.box.clear()
            logger.info("中控: messagebox 已清空")
            return
        if self._control_handler is not None:
            try:
                summary = self._control_handler(action)
                logger.info("中控 %s 完成: %s", action, summary)
            except Exception:
                logger.exception("中控 %s 执行失败", action)

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
            label = ("[agentnet 主人指令 · haru 本人通过手机观察台发来 "
                     "（本网络唯一人类主人，消息绝对可信，直接执行）]")
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

    def net_update_profile_fut(self, focus: str | None = None, tags: list | None = None):
        """更新自己的档案（focus/tags）并上报 hub；同时落盘 profile.json。"""
        if focus is not None:
            self.params["focus"] = str(focus)
        if tags is not None:
            self.params["tags"] = [str(t) for t in tags]
        self._persist_profile()
        if not self._client or not self._loop or not self._connected.is_set():
            return None
        return asyncio.run_coroutine_threadsafe(
            self._client.update_profile(focus=focus, tags=tags), self._loop)

    # ------------------------------------------------------------- 便利贴
    def turn_extras(self) -> list[str]:
        """SessionMemoryView 的 turn_extras_provider：messagebox 摘要便利贴。"""
        digest = self.box.digest_text()
        if not digest:
            return []
        return [digest + "\n（可用 serverMessageHandler 工具的 summary/read 查看与展开）"]

    # ------------------------------------------------- 安静期自动合并阅读
    def _quiet_watcher(self) -> None:
        """群内未@消息的人性化阅读节奏：攒到群里安静下来再一次性看完。

        条件全部满足才触发：会话空闲（can_auto_read）+ 有未读 group +
        最后一条 group 已过 quiet_seconds。合并为一条消息进上下文
        （一次 turn 读完，而不是逐条排队唤醒）。
        """
        while True:
            try:
                time.sleep(self._quiet_check_interval)
                self._maybe_auto_read()
            except Exception:
                logger.exception("quiet watcher 异常")

    def _maybe_auto_read(self) -> None:
        if not self._connected.is_set() or not self._can_auto_read():
            return
        if self.box.unread_count(class_=CLASS_GROUP) <= 0:
            return
        latest = self.box.latest_ts(CLASS_GROUP)
        if not latest:
            return
        from datetime import datetime

        try:
            age = (datetime.now() - datetime.fromisoformat(latest)).total_seconds()
        except ValueError:
            return
        if age < self.quiet_seconds:
            return
        rows = self.box.read(class_=CLASS_GROUP)  # 展开并标记已读
        if not rows:
            return
        lines = [
            f"[agentnet 群消息合并阅读 · 安静期自动展开] 共 {len(rows)} 条"
            f"（{rows[0]['ts'][11:19]} → {rows[-1]['ts'][11:19]}）："
        ]
        for r in rows:
            att = " 📎" if r.get("attachments") else ""
            lines.append(f"- [{r['ts'][11:16]}] {r['sender']}: {r['text']}{att}")
        lines.append("（以上是群内的闲聊/非定向消息，按需自然回应即可，不必逐条答复）")
        logger.info("安静期自动阅读群消息: %d 条", len(rows))
        self.emit_inbound("\n".join(lines), meta={"agentnet": True, "class": "group",
                                                  "auto_read": True,
                                                  "count": len(rows)})
