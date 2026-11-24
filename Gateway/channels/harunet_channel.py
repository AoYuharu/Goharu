"""HaruNet channel：把 HaruNet 组网接入 Goharu 的 channel 体系。

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
from AgentNet.protocol import CLASS_DM, CLASS_GROUP, CLASS_MENTION
from Gateway.channels.base import Channel
from Gateway.channels.registry import register_channel
from Gateway.floor import FloorState
from Gateway.names import NET_NAME

logger = logging.getLogger(__name__)


@register_channel("harunet")
class HaruNetChannel(Channel):
    channel_type = "harunet"
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
        # 主人消息快速兜底：主人不@的群消息不立即唤醒，但安静期缩短到该秒数
        self.owner_quiet_seconds = float(self.params.get("owner_quiet_seconds", 15))
        self._quiet_check_interval = 10.0
        # 合并消化：urgent 到达后去抖窗口（离线批量消息会在这个窗口内聚合成一条）
        self._urgent_debounce = float(self.params.get("urgent_debounce_seconds", 1.2))
        # 群消息体量阈值：超过则交压缩智能体摘要，否则按时间序内联进上下文
        self._group_inline_max_chars = int(self.params.get("group_inline_max_chars", 1500))
        # 关注期：被 @/私聊/主人说话后进入，期内所有新消息都唤醒（且续期）；
        # 超时无消息自动退出，回到"@才醒 + 安静期自动阅读"
        self.attention_seconds = float(self.params.get("attention_seconds", 300))
        self._attentive_until = 0.0
        self._attentive_reported = False  # 上次上报的关注期状态（翻转才推，防刷）
        # 乒乓刹车：连续 N 轮纯同事对话（无主人参与）后，同事闲聊不再唤醒
        self._agent_pingpong_limit = int(self.params.get("agent_pingpong_limit", 3))
        self._agent_streak = 0
        # 群体意识：peer agent_id → 名字（presence_state 广播维护，用于@对象标注）
        self._peer_names: dict = {}
        # 网络话语权本地快照（floor_state 事件同步；SessionManager 装配给入队关卡）
        self.floor = FloorState()
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
        self._env_writer = None                 # SessionManager 注入：写 .env（密码持久化）
        self._thread: Optional[threading.Thread] = None
        self._reply_target: Optional[dict] = None  # {sender}
        self._agent_replied = False  # 本 turn 模型已通过 harunet_reply 发过消息
        self._connected = threading.Event()
        self._urgent_buf: list = []              # 待合并的 urgent 消息（mention/dm）
        self._urgent_lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._summarize_fn = None                # SessionManager 注入的压缩器 fn(name, lines)->str
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
            # 与 focus/tags 一致：自我更新的档案优先于 config 装配值——
            # 主人改过的名字重启后必须保留，不能被 config 默认值顶回去
            self.name = saved["name"]

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

    def _apply_rename(self, name: str) -> None:
        """应用新名字：运行时身份 + 落盘。hub 侧白名单/presence 由 hub 自己改。"""
        self.name = name
        self.identity.name = name     # client 持有同一 Identity 引用，握手即用新名
        self.params["name"] = name
        self._persist_profile()

    # ------------------------------------------------------------- 生命周期
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"harunet-{self.channel_id}", daemon=True)
        self._thread.start()
        threading.Thread(target=self._quiet_watcher,
                         name=f"harunet-quiet-{self.channel_id}",
                         daemon=True).start()
        logger.info("HaruNet channel 启动: %s → %s", self.identity.agent_id, self.url)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            from Tools.security import get_security_checker
            security = get_security_checker().snapshot()
        except Exception:
            security = None   # 无 Tools 环境（纯协议库测试）不上报
        try:
            import Architect.hub  # noqa: F401——arch 可被转交话语权的宿主标记
            hosts_architect = True
        except Exception:
            hosts_architect = False
        self._client = AgentNetClient(
            self.identity, self.url, messagebox=self.box,
            state_provider=lambda: self._state_provider(),
            attentive_provider=lambda: time.time() < self._attentive_until,
            on_message=self._on_any_message,
            on_event=self._on_net_event,
            on_control=self._on_control,
            password=self.params.get("password", ""),
            reject_backoff_seconds=int(
                self.params.get("reject_backoff_seconds", 600) or 600),
            profile={
                "focus": self.params.get("focus", ""),
                "tags": list(self.params.get("tags") or []),
                "security": security,
                "hosts_architect": hosts_architect,
            },
            http_base=self.http_base,
        )
        try:
            self._loop.run_until_complete(self._client.connect_forever())
        except Exception:
            logger.exception("HaruNet client 循环退出")
        finally:
            self._loop.close()

    def stop(self):
        if self._client and self._loop:
            asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)

    def health(self) -> dict:
        """HaruNet 连通状态：online / offline（含最近失败原因）。"""
        if self._connected.is_set():
            return {"ok": True, "state": "online", "detail": f"已连接 {self.url}"}
        err = (self._client.last_error if self._client else "") or "尚未连接（等待重试）"
        return {"ok": False, "state": "offline", "detail": err}

    def set_state_provider(self, fn) -> None:
        """SessionManager 装配后注入：() -> idle|working|maintenance。"""
        self._state_provider = fn

    def set_can_auto_read(self, fn) -> None:
        """SessionManager 注入：() -> bool——会话空闲（无 turn 进行中）时才自动读群。"""
        self._can_auto_read = fn

    def set_control_handler(self, fn) -> None:
        """SessionManager 注入中控动作执行器：fn(action: str) -> str（返回执行摘要）。"""
        self._control_handler = fn

    def set_summarize_fn(self, fn) -> None:
        """SessionManager 注入压缩器（arch 角色）：fn(agent_name, lines) -> str。"""
        self._summarize_fn = fn

    # ------------------------------------------------- 关注期状态上报（观察台）
    def _push_presence_async(self) -> None:
        """关注期/状态翻转即时上报 hub（fire-and-forget，不阻塞当前线程）。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return

        def _swallow(f) -> None:
            try:
                f.exception()
            except Exception:
                pass

        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.push_presence(), self._loop)
            fut.add_done_callback(_swallow)
        except Exception:
            logger.warning("即时 presence 上报失败", exc_info=True)

    def _report_presence_if_changed(self) -> None:
        """关注期状态翻转（进入/退出）时推一次 presence——观察台实时可见。"""
        attentive = time.time() < self._attentive_until
        if attentive == self._attentive_reported:
            return
        self._attentive_reported = attentive
        logger.info("关注期状态翻转: attentive=%s", attentive)
        self._push_presence_async()

    # ------------------------------------------------------------- 入站
    def _on_net_event(self, kind: str, env: dict) -> None:
        if kind == "welcome":
            self._connected.set()
            logger.info("HaruNet 已上线: %s", self.identity.agent_id)
            self.floor.update((env.get("payload") or {}).get("floor"))
        elif kind == "presence_state":
            payload = env.get("payload") or {}
            agents = payload.get("agents") or {}
            for aid, info in agents.items():
                name = (info or {}).get("name")
                if aid and name:
                    self._peer_names[aid] = str(name)
            self.floor.update(payload.get("floor"))
        elif kind == "floor_state":
            snap = self.floor.update((env.get("payload") or {}).get("floor"))
            logger.info("话语权更新: %s（%s）",
                        snap.holder_name or "无", snap.persona if snap.held else "-")
        elif kind == "disconnected":
            self._connected.clear()
            logger.info("HaruNet 已离线: %s", self.identity.agent_id)

    def _fmt_mentions(self, mentions: list) -> str:
        """mentions（agent_id 列表）→ '@名字1 @名字2'（名字缺失用短 id）。"""
        out = []
        for m in mentions or []:
            out.append("@" + self._peer_names.get(m, str(m)[:8]))
        return " ".join(out)

    # 每条群消息必跟的 reminder（主人要求英文原句，三态全覆盖）：
    # @我 → 必须回复；@别人 → 明确旁观 + 确有帮助再轻参与；无@ → 广播择优参与
    _NOTE_FOR_ME = "This Message is specifically sent for you, you need to reply it"
    _NOTE_BROADCAST = ("This is a broadcast message sent from user, react to it "
                       "and participate in the current talking only if you could "
                       "confirm that you are helpful or necessary to reply")

    def _note_for_other(self, mentions: list) -> str:
        return ("This Message is specifically sent for "
                f"{self._fmt_mentions(mentions)}, Not For You. "
                "But You can do some slight thinking or analysis if you confirm "
                "that you are helpful or necessary to participate in their "
                "conversation.")

    def _mention_note(self, mentions: list) -> str:
        """群消息 @对象 reminder（每条必跟）：@我 → 必须回复；@别人 → 明确旁观；
        无@（同事线路防御）→ 广播。"""
        if not mentions:
            return self._NOTE_BROADCAST
        if self.identity.agent_id in mentions:
            return self._NOTE_FOR_ME
        return self._note_for_other(mentions)

    def _owner_line_note(self, mentions: list) -> str:
        """主人群消息 reminder（每条必跟）：@我 → 必须回复；无@ → 广播；
        @别人 → 明确旁观。"""
        if not mentions:
            return self._NOTE_BROADCAST
        if self.identity.agent_id in mentions:
            return self._NOTE_FOR_ME
        return self._note_for_other(mentions)

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
        if action == "rename":
            # 主人改名：端侧身份即时更新 + 落盘（重启后 profile 名优先于 config）
            new_name = str((env.get("payload") or {}).get("name", "")).strip()
            if not new_name:
                logger.warning("中控 rename 缺少 name，忽略")
                return
            self._apply_rename(new_name)
            logger.info("中控: 已改名为 %s", new_name)
            return
        if action == "security":
            # 观察台改安全规则：运行时即时生效 + 尽力写回 config.yaml，
            # 新规则快照随 profile 上报（观察台详情页"安全策略"区展示它）
            payload = env.get("payload") or {}
            from Tools.security import get_security_checker
            result = get_security_checker().apply_rules(
                dangerous_commands=payload.get("dangerous_commands"),
                require_confirmation=payload.get("require_confirmation"),
                allow_confirmation=payload.get("allow_confirmation"))
            logger.info("中控: 安全策略已更新（persisted=%s）", result.get("persisted"))
            self._report_profile()
            return
        if action == "answer":
            # 观察台作答回送：解开挂起的 AskUser
            rid = str((env.get("payload") or {}).get("request_id", ""))
            resolve = getattr(self, "_resolve_answer", None)
            if rid and resolve is not None:
                try:
                    resolve(rid, (env.get("payload") or {}).get("answer"))
                    logger.info("中控: AskUser 已作答 %s", rid)
                except Exception:
                    logger.exception("中控 answer 解析失败: %s", rid)
            return
        if action == "confirm_result":
            # 观察台高危命令批准回送：解开挂起的确认
            cid = str((env.get("payload") or {}).get("confirm_id", ""))
            resolve = getattr(self, "_resolve_confirmation", None)
            if cid and resolve is not None:
                try:
                    resolve(cid, bool((env.get("payload") or {}).get("approved")))
                    logger.info("中控: 高危命令 %s → %s",
                                cid, "批准" if (env.get("payload") or {}).get("approved") else "拒绝")
                except Exception:
                    logger.exception("中控 confirm_result 解析失败: %s", cid)
            return
        if self._control_handler is not None:
            try:
                summary = self._control_handler(action)
                logger.info("中控 %s 完成: %s", action, summary)
            except Exception:
                logger.exception("中控 %s 执行失败", action)

    def _on_any_message(self, env: dict, class_: str) -> None:
        """全量消息入口（client on_message）：入盒 + 关注期判定。

        - mention/dm → 唤醒 + 进入关注期（续期）
        - 关注期内的普通群消息 → 也唤醒（续期）
        - 其余群消息（含主人不@的群消息）→ 只入盒（摘要计数，安静期兜底阅读；
          主人消息走更短的 owner_quiet_seconds 快速阅读）
        """
        sender = env.get("from", "?")
        payload = env.get("payload") or {}
        if self.box is not None:
            peer = env["to"] if env.get("kind") == CLASS_GROUP else sender
            self.box.add(env.get("id"), class_, peer, sender,
                         str(payload.get("text", "")), env.get("ts"),
                         attachments=payload.get("attachments") or None,
                         mentions=env.get("mentions") or None)
        is_owner = sender in self.owner_ids
        in_attention = time.time() < self._attentive_until
        if is_owner:
            self._agent_streak = 0   # 主人说话重置乒乓计数
        urgent = (class_ in (CLASS_MENTION, CLASS_DM)
                  or (class_ == CLASS_GROUP and in_attention))
        # 网络话语权（绝对优先）：持有期间只有持有者响应老板消息
        snap = self.floor.snapshot()
        if snap.held:
            if snap.holder_agent_id != self.identity.agent_id:
                return   # 话语权在别处：照常入盒，静默不唤醒
            if not is_owner:
                return   # 我持有话语权：只响应主人，同事消息不打断对话
            urgent = True   # 持有期间主人所有消息都是对你说
        # 乒乓刹车：关注期内的同事普通群聊，连续 N 轮无主人参与后不再唤醒
        if urgent and not is_owner and class_ == CLASS_GROUP:
            if self._agent_streak >= self._agent_pingpong_limit:
                logger.info("乒乓刹车：同事闲聊不再唤醒（streak=%d）", self._agent_streak)
                return
            self._agent_streak += 1
        if not urgent:
            return
        # 已被唤醒的消息直接从收件箱标记已读（内容会进合并上下文）
        self.box.mark_read_by_msg_id(env.get("id"))
        self._attentive_until = time.time() + self.attention_seconds
        self._report_presence_if_changed()   # 进关注期 → 即时上报观察台
        self._on_urgent(env, class_)

    def _on_urgent(self, env: dict, class_: str) -> None:
        """mention/dm 到达：不逐条唤醒——去抖窗口内聚合，按时间序合并成一条进上下文。

        组网为全员广播制：回答统一以 group + @原发送者 广播出去——原发送者
        收到 urgent mention，其他人按群消息静默入盒。
        """
        sender = env.get("from", "?")
        is_owner = sender in self.owner_ids
        payload = env.get("payload") or {}
        item = {
            "sender": sender,
            "is_owner": is_owner,
            "class": class_,
            "text": str(payload.get("text", "")),
            "attachments": payload.get("attachments") or [],
            "mentions": env.get("mentions") or [],
            "ts": env.get("ts") or "",
        }
        with self._urgent_lock:
            # 去重旗标只在 flush（新 turn 起点）重置——turn 进行中到达的
            # 消息不得重置它，否则同一轮回答会被中继两次（双广播事故）
            self._urgent_buf.append(item)
            # 回复目标：主人消息优先，否则取最新消息的发送者
            if is_owner or self._reply_target is None or not self._reply_target.get("is_owner"):
                self._reply_target = {"sender": sender, "is_owner": is_owner}
            if self._flush_timer is None or not self._flush_timer.is_alive():
                self._flush_timer = threading.Timer(self._urgent_debounce, self._flush_urgent)
                self._flush_timer.daemon = True
                self._flush_timer.start()

    def _flush_urgent(self) -> None:
        """把去抖窗口内聚合的 urgent 消息（+可内联的群消息）合并成一条入站。"""
        with self._urgent_lock:
            items = self._urgent_buf
            self._urgent_buf = []
            self._flush_timer = None
            # 只有"会话空闲时"的 flush 才是新 turn 起点，才重置去重旗标；
            # turn 进行中到来的消息批量 flush 会被会话合并进当前 turn，
            # 若此时重置会把 dedupe 打穿（双广播 race）
            if items and self._can_auto_read():
                self._agent_replied = False
        if not items:
            return
        owners = [i for i in items if i["is_owner"]]
        # 三类分区：真正@我/私聊我的 = 重点；主人在群里说话 = 群体动态（旁观）；
        # 同事群聊 = 关注期内顺带接收
        directed = [i for i in items if i["class"] != CLASS_GROUP]
        owner_chat = [i for i in items if i["class"] == CLASS_GROUP and i["is_owner"]]
        attentive_chat = [i for i in items if i["class"] == CLASS_GROUP and not i["is_owner"]]
        t0, t1 = items[0]["ts"][11:19] or "?", items[-1]["ts"][11:19] or "?"
        parts = [
            f"[{NET_NAME} 组网消息整理 · {len(items)} 条"
            f"{'（含主人消息）' if owners else ''}（{t0} → {t1}）]"
        ]
        snap = self.floor.snapshot()
        if snap.held and snap.holder_agent_id == self.identity.agent_id:
            parts.append("🎙 你正持有 HaruNet 话语权：主人在和你直接对话"
                         + ("（arch 人格）。" if snap.persona == "arch" else "。")
                         + "回复会广播到群聊。")
        if directed:
            parts.append("【@你的消息 · ⚡重点】你被点名，已进入关注期，需处理或回应"
                         "（按时间顺序）：")
            for i in directed:
                who = f"{i['sender']}（主人）" if i["is_owner"] else i["sender"]
                att = f" 📎{len(i['attachments'])}个附件" if i["attachments"] else ""
                parts.append(f"- [{i['ts'][11:16] or '?'}] {who}: {i['text']}{att}"
                             f"【{self._NOTE_FOR_ME}】")
        if owner_chat:
            parts.append("【主人消息 · 群体动态】主人在群里说话，留意是否在和你交流"
                         "（按时间顺序）：")
            for i in owner_chat:
                parts.append(f"- [{i['ts'][11:16] or '?'}] {i['sender']}（主人）"
                             f"{self._owner_line_note(i['mentions'])}: {i['text']}")
        if attentive_chat:
            parts.append("【群消息 · 关注期内顺带接收】你在关注期所以能实时看到，"
                         "留意与你相关的内容即可（按时间顺序）：")
            for i in attentive_chat:
                parts.append(f"- [{i['ts'][11:16] or '?'}] {i['sender']}"
                             f"{self._mention_note(i['mentions'])}: {i['text']}")
        parts.extend(self._consume_group_rows())
        parts.append("提示：三类消息区别对待——【@你的消息】是重点（被点名，"
                     "需处理或回应）；【主人消息·群体动态】和【关注期内群消息】是"
                     "旁观察看：主人在和别的智能体对话、或话题与你无关时，不要插话"
                     "不要回复，看完即可。回复请用 harunet_reply。")
        if not owners:
            parts.append("（同事消息：只有确实需要回应或行动时才用 reply 回复；"
                         "不要发送纯确认/待命/寒暄类消息，避免与同事形成反弹循环。）")
        # 自动中继策略：只有主人直接@我/私聊我触发的 turn 才把回答自动广播回网；
        # 主人在群里和别人说话（群体动态）不自动中继——旁观智能体的内心戏
        # 绝不能让全网看到；纯同事触发的 turn 必须用 reply 工具显式回应
        owner_directed = any(i["is_owner"] and i["class"] != CLASS_GROUP
                             for i in items)
        with self._urgent_lock:
            if self._reply_target is not None:
                self._reply_target["auto_relay"] = owner_directed
        first_owner = owners[0] if owners else None
        meta = {"harunet": True, "class": items[0]["class"],
                "sender": (first_owner or items[-1])["sender"],
                "from_owner": bool(owners), "merged": len(items)}
        logger.info("组网消息合并入站: %d 条（owner=%s）", len(items), bool(owners))
        self.emit_inbound("\n".join(parts), meta=meta)

    def _consume_group_rows(self) -> list[str]:
        """读出未读群消息并分流：小体量内联进本 turn；大体量交压缩智能体。

        返回要追加到合并消息里的文本行。
        """
        rows = self.box.read(class_=CLASS_GROUP)  # 取出并标记已读
        if not rows:
            return []
        def _note(r: dict) -> str:
            if r["sender"] in self.owner_ids:
                return self._owner_line_note(r.get("mentions") or [])
            return self._mention_note(r.get("mentions") or [])

        lines = [f"- [{r['ts'][11:16]}] {r['sender']}"
                 f"{'（主人）' if r['sender'] in self.owner_ids else ''}"
                 f"{_note(r)}: {r['text']}"
                 for r in rows]
        snap = self.floor.snapshot()
        floor_line = []
        if snap.held and snap.holder_agent_id != self.identity.agent_id:
            floor_line = [f"🎙 The floor is held by @{snap.holder_name or snap.holder_agent_id}"
                          " — this conversation is not for you; stay silent unless "
                          "explicitly asked."]
        total = sum(len(l) for l in lines)
        if total <= self._group_inline_max_chars:
            return floor_line + [f"【群消息】（{len(rows)} 条，按时间顺序）"] + lines
        # 大体量：压缩智能体后台摘要，完成后补送进消息队列
        logger.info("群消息 %d 条共 %d 字，交压缩智能体摘要", len(rows), total)
        threading.Thread(target=self._summarize_groups,
                         args=(rows, lines, total), daemon=True).start()
        return [f"【群消息】{len(rows)} 条（约 {total} 字，较多）正在压缩摘要，稍后补送"]

    def _summarize_groups(self, rows: list, lines: list, total: int) -> None:
        """大体量群消息的压缩摘要（arch 智能体角色：关注与接收方相关的信息）。"""
        summary = None
        if self._summarize_fn is not None:
            try:
                summary = self._summarize_fn(self.name, lines)
            except Exception:
                logger.exception("群消息压缩失败，降级为截断原文")
        if not summary:
            # 降级：没有压缩器或压缩失败时给截断原文，消息不丢
            joined = "\n".join(lines)
            summary = joined[: self._group_inline_max_chars] + "\n…（截断）"
        t0, t1 = rows[0]["ts"][11:16], rows[-1]["ts"][11:16]
        text = (
            f"[HaruNet 群消息摘要（{len(rows)} 条 {t0}→{t1}，压缩后）]\n{summary}\n"
            "提示：以上为群消息摘要——关注可能与你有关的内容即可；"
            "不重要且不相关的可以不回复。"
        )
        self.emit_inbound(text, meta={"harunet": True, "class": "group",
                                      "summarized": True, "count": len(rows)})

    # ------------------------------------------------------------- 出站
    def send_answer(self, text: str, meta: dict | None = None):
        """把回答广播回网（全员可见），并 @ 最近一条 urgent 消息的发送者。

        去重：模型本 turn 已经用 harunet_reply 发过消息时不再中继
        （否则同一条回答出现两遍）。中继/跳过之后清空回复目标——防止后续与
        组网无关的 turn（如 TUI 私聊）把回答误广播到全网。

        meta.floor=true：话语权对话回复（arch 派发/透明提示），不依赖
        _reply_target 的私聊中继语义，直接广播。
        """
        with self._urgent_lock:
            target = self._reply_target
            self._reply_target = None
            agent_replied = self._agent_replied
            if agent_replied:
                self._agent_replied = False
        if meta and meta.get("floor"):
            self._broadcast_text(text)
            return
        if target is None:
            return
        if agent_replied:
            logger.info("HaruNet: 模型已自行广播，跳过 send_answer 中继")
            return
        # 纯同事触发的 turn 不自动中继（防反弹循环）：要回应必须用 reply 工具
        if not target.get("auto_relay", True):
            logger.info("HaruNet: 同事触发的回答不自动中继（防乒乓）")
            return
        self._broadcast_text(text, mentions=[target["sender"]])

    def _broadcast_text(self, text: str, mentions: list | None = None) -> None:
        if not self._client or not self._loop:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.send_message(
                    "grp:main", text, kind="group", mentions=mentions),
                self._loop)
            fut.result(timeout=10)
        except Exception as e:
            logger.warning("HaruNet 回答广播失败: %s", e)

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
            with self._urgent_lock:
                self._agent_replied = True  # send_answer 据此去重
            return msg_id

        return asyncio.run_coroutine_threadsafe(_coro(), self._loop)

    def net_presence_fut(self):
        """同 net_reply_fut：返回 presence 查询的 concurrent Future。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return None
        return asyncio.run_coroutine_threadsafe(self._client.query_presence(), self._loop)

    def set_env_writer(self, fn) -> None:
        """SessionManager 注入 .env 写入器：fn(key, value)（harunet_set_password 用）。"""
        self._env_writer = fn

    def net_set_password_fut(self, password: str):
        """运行时换接入密码：更新 client + 写回 .env + 立即重连（一步到位）。"""
        password = str(password or "")
        self.params["password"] = password
        if self._env_writer is not None:
            try:
                self._env_writer("HARUNET_PASSWORD", password)
            except Exception:
                logger.warning("接入密码写回 .env 失败（仅本次运行生效）", exc_info=True)
        if not self._client or not self._loop:
            return None

        async def _apply():
            self._client.set_password(password)
            await self._client.kick_reconnect()

        return asyncio.run_coroutine_threadsafe(_apply(), self._loop)

    def net_reconnect_fut(self):
        """主动重连：打断当前退避等待，立即重新握手（工具/TUI 可触发）。"""
        if not self._client or not self._loop:
            return None
        return asyncio.run_coroutine_threadsafe(
            self._client.kick_reconnect(), self._loop)

    def net_set_floor_fut(self, action: str, target: str = "",
                          persona: str = "main", note: str = ""):
        """切换网络话语权（floor_handoff/floor_release 工具调用）：set|clear，hub 广播全网。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return None
        return asyncio.run_coroutine_threadsafe(
            self._client.set_floor(action, target=target, persona=persona,
                                   note=note), self._loop)

    def net_error_report_fut(self, record: dict):
        """把统一错误记录上报 hub 错误流（观察台）；未连接时静默丢弃。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return None
        try:
            return asyncio.run_coroutine_threadsafe(
                self._client.send_error_report(record), self._loop)
        except Exception:
            logger.debug("错误记录上报调度失败", exc_info=True)
            return None

    def _report_profile(self) -> None:
        """把最新 profile（含安全规则快照）上报 hub：更新内存档案并触发广播。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return
        try:
            from Tools.security import get_security_checker
            self._client._profile["security"] = get_security_checker().snapshot()
        except Exception:
            pass
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.update_profile(), self._loop)
            def _done(f):
                try:
                    f.result()
                    logger.info("profile 已上报 hub（含安全规则快照）")
                except Exception:
                    logger.exception("profile 上报 hub 失败")
            fut.add_done_callback(_done)
        except Exception:
            logger.exception("profile 上报 hub 调度失败")

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

    # ------------------------------------------------- 观察台交互（AskUser/高危确认）
    def _push_ask(self, request_id: str, kind: str, data: dict) -> bool:
        """登记到 hub 挂起表（观察台渲染按钮作答）。失败返回 False，调用方退化文本。"""
        if not self._client or not self._loop or not self._connected.is_set():
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._client.submit_ask(request_id, kind, data), self._loop)
            fut.result(timeout=10)
            return True
        except Exception:
            logger.exception("组网询问登记 hub 失败，退化文本问答")
            return False

    def ask_questions(self, request_id: str, questions: list, meta: dict | None = None):
        """AskUser 提问：推观察台按钮面板；同时保留本地挂起（网内文本作答仍可消费）。"""
        modes = []
        for q in questions:
            labels = [o.get("label", "") if isinstance(o, dict) else str(o)
                       for o in (q.get("options") or [])]
            labels = [l for l in labels if l]
            modes.append("multi" if q.get("multi_select") else ("single" if labels else "text"))
        self._set_ask_pending(request_id, questions, modes, meta)
        if self._push_ask(request_id, "question", {"questions": questions}):
            logger.info("AskUser 已推观察台: %s (%d 题)", request_id, len(questions))
            return
        super().ask_questions(request_id, questions, meta)

    def confirm_request(self, confirm_id: str, command: str,
                        description: str = "", timeout: int = 300):
        """高危命令确认：推观察台允许/拒绝按钮；失败退化文本 y/n。"""
        self._pending_confirm = {
            "id": str(confirm_id),
            "expires": time.time() + max(10, int(timeout or 300)) + 30,
        }
        if self._push_ask(str(confirm_id), "confirm",
                          {"confirm_id": str(confirm_id), "cmd": command,
                           "description": description, "timeout": int(timeout or 300)}):
            logger.info("高危确认已推观察台: %s", confirm_id)
            return
        super().confirm_request(confirm_id, command, description, timeout)

    # ------------------------------------------------------------- 便利贴
    def turn_extras(self) -> list[str]:
        """SessionMemoryView 的 turn_extras_provider：messagebox 摘要便利贴。"""
        digest = self.box.digest_text()
        if not digest:
            return []
        return [digest + "\n（可用 harunet_summary / harunet_read 查看与展开）"]

    # ------------------------------------------------- 安静期自动合并阅读
    def _quiet_watcher(self) -> None:
        """群内未@消息的人性化阅读节奏：攒到群里安静下来再一次性看完。

        条件全部满足才触发：会话空闲（can_auto_read）+ 有未读 group +
        最后一条 group 已过安静期（主人消息用更短的 owner_quiet_seconds）。
        合并为一条消息进上下文（一次 turn 读完，而不是逐条排队唤醒）。
        """
        while True:
            try:
                time.sleep(self._quiet_check_interval)
                self._maybe_auto_read()
                # 关注期超时退场检测：翻转即推给 hub（观察台实时可见）
                self._report_presence_if_changed()
            except Exception:
                logger.exception("quiet watcher 异常")

    def _maybe_auto_read(self) -> None:
        if not self._connected.is_set() or not self._can_auto_read():
            return
        if self.box.unread_count(class_=CLASS_GROUP) <= 0:
            return
        from datetime import datetime

        # 主人消息快速兜底：有未读主人群消息时用更短的安静期（主人不@不立即
        # 唤醒，但约 owner_quiet_seconds 内合并阅读），否则用普通安静期
        owner_latest = self.box.latest_ts_from(self.owner_ids, CLASS_GROUP)
        if owner_latest:
            latest = owner_latest
            threshold = self.owner_quiet_seconds
        else:
            latest = self.box.latest_ts(CLASS_GROUP)
            threshold = self.quiet_seconds
        if not latest:
            return
        try:
            age = (datetime.now() - datetime.fromisoformat(latest)).total_seconds()
        except ValueError:
            return
        if age < threshold:
            return
        # 与 urgent 合流共用分流：小体量内联，大体量交压缩智能体
        owner_flag = "（含主人消息）" if owner_latest else ""
        parts = [f"[HaruNet 群消息 · 安静期自动阅读{owner_flag}]"]
        parts.extend(self._consume_group_rows())
        parts.append("提示：以上为群内非定向消息——注意每条@的是谁：主人在和别的"
                     "智能体对话、或话题与你无关时，旁观即可不要插话；确实与你"
                     "相关或被问到时才用 harunet_reply 回复。")
        logger.info("安静期自动阅读触发")
        self.emit_inbound("\n".join(parts), meta={"harunet": True, "class": "group",
                                                  "auto_read": True})
