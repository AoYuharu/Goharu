"""AgentSession —— 单会话运行时（多 session 体系的核心）。

从旧 GatewaySession 演进：turn 循环（批处理合并/micro-compact/auto-compact/
token stats/watchdog/后台任务 drain/中断）整体保留，变化只有两点：

1. **记忆经 SessionMemoryView**：L0 按 session 隔离，金字塔共享
2. **出站经 broadcast**：旧 write_json 直发 TUI 改为按 capability 轮发
   本 session 挂载的全部 channel（send_answer/thinking/tool_use/tool_result/event）

每个 session 有独立的 pending 队列、processing 锁、中断标记、看门狗状态；
LLM（LLMCore 单例）、工具运行时、后台任务管理器、PipelineManager 为进程级共享。
"""

import asyncio
import faulthandler
import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from Core.Message import CoreMessage, MessageSource
from Core.LifecycleLog import lifecycle
from Core.timing import timeit
from Core.TokenEstimator import TokenEstimator
from Tools.api import tool_process_tracker
from configurationLoader import config
from Gateway.plan_mode import is_plan_mode_command
from Gateway.names import EXEC_LABEL, MAINT_LABEL
from Prompting.reminders import PLAN_MODE_REMINDER

logger = logging.getLogger(__name__)


class AgentSession:
    """单会话运行时：消息队列 + turn 循环 + channel 轮发。"""

    def __init__(self, session_id, memory_view, actor, pipeline_manager,
                 bg_tasks, ctx_manager, loop=None, checkpoint=None):
        self.session_id = str(session_id)
        self.memory = memory_view            # SessionMemoryView
        self.actor = actor                   # Agent.api.StandaloneAgent.actor
        self.pipeline_manager = pipeline_manager
        self.bg_tasks = bg_tasks             # 共享 BackgroundTaskManager
        self.ctx_manager = ctx_manager       # Prompting ContextManager
        self.loop = loop                     # 主事件循环（processing 线程复用）
        self._ckpt = checkpoint              # 共享 CheckpointManager（可 None）
        self._turn_outcome = None

        self.channels = []                   # 挂载的 channel（N:1 绑定）

        # 统一队列：message + command 按 FIFO 处理
        self.pending_items: list = []
        self._processing = False
        self._processing_lock = threading.Lock()
        self._batch_lock = threading.Lock()
        self._interrupt_event = threading.Event()
        self._micro_compacted = False
        self._bg_reactivation_queued = False
        self._turn_count = 0
        self._watchdog_state = None
        self._watchdog_thread_started = False
        # 出站路由：最近一条用户消息的来源 channel（None=尚无消息，全量广播）
        self._active_channel = None

        # 计划模式（只读运行时状态）：闸门由 SessionManager 装配
        self.plan_mode = False
        self.plan_gate = None

        # 网络话语权入队关卡（SessionManager 装配；未装配 = 无话语权功能）。
        # 拦截在 enqueue 前端（channel 之前），任何 channel 的入站都先过它。
        self._floor_state = None         # Gateway.floor.FloorState
        self._floor_agent_id = ""
        self._floor_arch_dispatch = None # fn(question, reminder) -> str|None
        self._floor_queue: list = []     # (text, origin_ch, reminder)
        self._floor_q_lock = threading.Lock()
        self._floor_worker_running = False
        self._floor_turns = 0            # 当前话语权持有期的 arch 轮数
        self._floor_epoch = 0            # 快照 epoch（变化即重置计数）

    def set_floor_context(self, state, agent_id: str, arch_dispatch=None) -> None:
        """SessionManager 装配：话语权快照 + 本机网络身份 + arch 派发函数。"""
        self._floor_state = state
        self._floor_agent_id = str(agent_id or "")
        self._floor_arch_dispatch = arch_dispatch
        logger.info("session %s: floor gate 装配（agent=%s, arch=%s）",
                    self.session_id, self._floor_agent_id,
                    "on" if arch_dispatch else "off")

    def _floor_gate(self, msg) -> str:
        """入队关卡：process（照常）/ handled（已答复或静默）/ dispatch（交 arch）。"""
        snap = self._floor_state.snapshot()
        if not snap.held:
            return "process"
        if msg.channel_type == "architect":
            return "process"   # Arch 桥的合成消息不受话语权影响
        if snap.holder_agent_id == self._floor_agent_id:
            if snap.persona == "arch" and self._floor_arch_dispatch is not None:
                return "dispatch"
            return "process"
        # 话语权在别处（绝对优先）：组网广播副本静默；本地直连输入给透明提示
        if msg.channel_type == "agentnet":
            return "handled"
        self._floor_notice(
            msg,
            f"🎙 话语权当前在 {snap.holder_name or snap.holder_agent_id}"
            f"（{'arch' if snap.persona == 'arch' else '主'}），本条消息未处理。"
            "可在观察台继续对话，或用 FloorControl 收回话语权。")
        return "handled"

    def _floor_origin_channel(self, msg):
        for ch in self.channels:
            if ch.channel_type == msg.channel_type and ch.channel_id == msg.channel_id:
                return ch
        return None

    def _floor_send(self, origin, text: str) -> None:
        """话语权回复：来源 channel 优先；失效兜底全广播；TUI 常驻旁观副本。"""
        sent = False
        if origin is not None:
            try:
                origin.send_answer(text, {"floor": True})
                sent = True
            except Exception:
                logger.warning("session %s: floor 回复发送失败（%s）",
                               self.session_id, origin, exc_info=True)
        targets = ([ch for ch in self.channels if ch.channel_type == "tui"]
                   if sent else list(self.channels))
        for ch in targets:
            if ch is origin:
                continue
            try:
                ch.send_answer(text, {"floor": True})
            except Exception:
                pass

    def _floor_notice(self, msg, text: str) -> None:
        self._floor_send(self._floor_origin_channel(msg), text)

    def _floor_dispatch(self, msg) -> None:
        """话语权=arch：消息入队 + daemon worker 串行派发（enqueue 立即返回）。"""
        snap = self._floor_state.snapshot()
        if snap.epoch != self._floor_epoch:
            self._floor_epoch = snap.epoch
            self._floor_turns = 0
        self._floor_turns += 1
        reminder = None
        if (self._floor_turns > 3
                or (snap.since and time.time() - snap.since > 600)):
            reminder = (
                f"你已经长期持有了 {EXEC_LABEL} 的聊天能力，请注意，如果你不再必要，"
                f"应该切换回 {EXEC_LABEL} 发言（调用 FloorControl action='release'，"
                "或 handoff 回主执行人格）。")
        origin = self._floor_origin_channel(msg)
        with self._floor_q_lock:
            self._floor_queue.append((str(msg.text), origin, reminder))
            if len(self._floor_queue) > 8:
                self._floor_queue.pop(0)   # 积压丢弃最旧
            if self._floor_worker_running:
                return
            self._floor_worker_running = True
        threading.Thread(target=self._floor_worker, daemon=True,
                         name=f"floor-arch-{self.session_id}").start()

    def _floor_worker(self) -> None:
        while True:
            with self._floor_q_lock:
                if not self._floor_queue:
                    self._floor_worker_running = False
                    return
                text, origin, reminder = self._floor_queue.pop(0)
            self._floor_send(origin, f"（{MAINT_LABEL}接手，正在思考…）")
            reply = None
            try:
                reply = self._floor_arch_dispatch(text, reminder)
            except Exception:
                logger.exception("session %s: arch 话语权派发异常", self.session_id)
            if not reply:
                self._floor_send(origin, "（arch 暂时不可用——可能正忙于其他任务。）")
            else:
                self._floor_send(origin, f"【{MAINT_LABEL}】{reply}")

    # ------------------------------------------------------------------
    # channel 挂载与轮发（broadcast）
    # ------------------------------------------------------------------

    def attach_channel(self, channel):
        """挂载 channel（N:1——channel 实例专属本 session）。"""
        self.channels.append(channel)
        logger.info("session %s: attached %s", self.session_id, channel)

    def _target_channels(self, capability):
        """出站路由：跟随"最近一条用户消息的来源 channel"，TUI 常驻副本。

        - 事件（capability="event"）保持能力广播——TUI 旁观全部对话靠它
        - 无消息历史（_active_channel=None）→ 全量能力广播（旧行为兜底）
        - 路由目标全部因 capability 过滤掉（如来源 channel 掉线）→ 全量兜底，
          答案绝不丢
        """
        if capability == "event" or self._active_channel is None:
            return [ch for ch in self.channels
                    if not capability or capability in ch.capabilities]
        atype, aid = self._active_channel
        targets = [
            ch for ch in self.channels
            if (ch.channel_type == atype and ch.channel_id == aid)
            or ch.channel_type == "tui"      # TUI 常驻：任意通道的消息都在 TUI 有一份
        ]
        routed = [ch for ch in targets
                  if not capability or capability in ch.capabilities]
        if not routed:
            return [ch for ch in self.channels
                    if not capability or capability in ch.capabilities]
        return routed

    def _send(self, capability, method, *args, **kwargs):
        """按路由目标轮发；单 channel 异常隔离（只记日志）。"""
        for ch in self._target_channels(capability):
            try:
                getattr(ch, method)(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    "session %s: %s.%s failed: %s",
                    self.session_id, ch, method, e,
                )

    def _broadcast_event(self, event_type, payload):
        self._send("event", "send_event", event_type, payload)

    def _broadcast_answer(self, text, meta=None):
        self._send(None, "send_answer", text, meta)   # answer 是隐含必要能力

    def _broadcast_thinking(self, text=None, meta=None):
        self._send("thinking", "send_thinking", text, meta)

    def _broadcast_tool_use(self, name, args, call_id, meta=None):
        self._send("tool_use", "send_tool_use", name, args, call_id, meta)

    def _broadcast_tool_result(self, name, result, call_id, is_error=False, meta=None):
        self._send("tool_result", "send_tool_result",
                   name, result, call_id, is_error, meta)

    # ------------------------------------------------------------------
    # 入站：channel 消息入队
    # ------------------------------------------------------------------

    def enqueue(self, msg):
        """channel 入站唯一入口（msg: ChannelMessage）。

        按用户要求给消息附加来源标注：[来自 {channel_type}]。
        挂起中的 ask 问题优先拦截：来源 channel 把消息消费为答案，
        不进 agent 队列（AskUser 的文本作答路径）。

        高优先级（msg.priority == "high"）：
        插到队首 + 打断当前轮（同 ESC 语义——当前步骤立即中断），
        随后马上把这条消息作为新的一轮分析。
        """
        for ch in self.channels:
            if ch.channel_type == msg.channel_type and ch.channel_id == msg.channel_id:
                try:
                    if ch.try_consume_as_confirmation(msg):
                        logger.info(
                            "session %s: 消息被 %s 消费为高危命令确认（不进队列）",
                            self.session_id, ch)
                        return
                    if ch.try_consume_as_answer(msg):
                        logger.info(
                            "session %s: 消息被 %s 消费为 ask 答案（不进队列）",
                            self.session_id, ch)
                        return
                except Exception as e:
                    logger.warning("consume_as_answer/confirmation failed: %s", e)
                break
        # 手动进入计划模式（「进入计划模式」/「/plan」）→ 切只读状态，消息照常入队
        if is_plan_mode_command(msg.text):
            self.set_plan_mode(True)
        # 文本升级指令（QQ 等无斜杠命令面板的 channel 也可用）
        if msg.text.strip() in ("/upgrade", "更新智能体", "升级智能体"):
            self.queue_command("upgrade", {}, req_id=f"{msg.channel_id}")
            self._start_processing()
            return

        # 网络话语权关卡（channel 之前）：持有期间只有持有者响应主人；
        # 持有者=arch 时消息改投 Architect 派发链（channel-agnostic）
        if self._floor_state is not None:
            decision = self._floor_gate(msg)
            if decision == "handled":
                return
            if decision == "dispatch":
                self._floor_dispatch(msg)
                return

        source = msg.channel_type
        meta_info = ""
        if msg.meta:
            brief = ", ".join(f"{k}={v}" for k, v in list(msg.meta.items())[:3])
            meta_info = f"（{brief}）"
        annotated = f"[来自 {source}{meta_info}] {msg.text}"
        # 活跃 channel 跟随：最近一条用户消息的来源即出站路由目标。
        # 只认真实挂载的 channel 实例——architect 等合成来源不抢路由。
        if any(ch.channel_type == msg.channel_type and ch.channel_id == msg.channel_id
               for ch in self.channels):
            new_active = (msg.channel_type, msg.channel_id)
            prev_type = self._active_channel[0] if self._active_channel else None
            if self._active_channel != new_active:
                self._active_channel = new_active
                if prev_type and prev_type != msg.channel_type:
                    annotated += (
                        f"\n[系统提示] User has switched chat channel from "
                        f"{prev_type} to {msg.channel_type}. You don't need to "
                        f"react. This is a reminder to hint you that the user is "
                        f"now using {msg.channel_type} channel talking with you. "
                        f"You only need to mind the situation."
                    )
                logger.info("session %s: active channel → %s#%s",
                            self.session_id, msg.channel_type, msg.channel_id)
        # 他源消息（QQ/http…）实时推给 TUI 渲染用户气泡；
        # tui 自己发的消息前端已本地回显，不再重复
        if source != "tui":
            self._broadcast_event("user.message",
                                  {"text": annotated, "channel": source})

        # 更新四态进行中：新消息挂起（不打断、不启动新轮）。
        # 确认/答案已在入口被 channel 拦截消费，不受此封锁影响。
        if self._update_blocked():
            self.queue_message(annotated, req_id=f"{msg.channel_id}")
            logger.info("session %s: 更新进行中，消息挂起: %s",
                        self.session_id, msg.text[:30])
            return

        if str(getattr(msg, "priority", "normal")).lower() == "high":
            with self._batch_lock:
                self.pending_items.insert(0, {
                    "type": "message", "data": annotated,
                    "req_id": f"{msg.channel_id}", "priority": "high",
                })
            if self._processing:
                # 打断当前轮：actor 在下一个检查点（步骤边界/工具执行前）
                # 立即返回 interrupted，然后处理队首的这条高优先级消息
                self._interrupt_event.set()
                try:
                    tool_process_tracker.kill_all()
                except Exception:
                    pass
                logger.info("session %s: 高优先级消息插入并打断当前轮",
                            self.session_id)
                self._send("text_output", "send_text_output",
                           "⚡ 收到高优先级消息，已打断当前任务转入处理",
                           {"kind": "priority"})
            self._start_processing()
            return

        self.queue_message(annotated, req_id=f"{msg.channel_id}")
        self._start_processing()

    def dispatch_question(self, request_id, payload):
        """AskUser 提问分发：event 能力 channel 走事件面板（TUI 原状），
        其余 channel 走 ask 三范式（基类文本退化兜底）。"""
        questions = (payload or {}).get("questions", [])
        for ch in self.channels:
            try:
                if "event" in ch.capabilities:
                    ch.send_event("tool.user_question_request", {
                        "request_id": request_id, "questions": questions})
                else:
                    ch.ask_questions(request_id, questions)
            except Exception as e:
                logger.warning("session %s: dispatch_question to %s failed: %s",
                               self.session_id, ch, e)

    def queue_message(self, message: str, req_id=""):
        with self._batch_lock:
            self.pending_items.append({"type": "message", "data": message, "req_id": req_id})
            logger.info("session %s: queued message (pending=%d)",
                        self.session_id, len(self.pending_items))

    def queue_command(self, command_name: str, params: dict, req_id=""):
        with self._batch_lock:
            self.pending_items.append(
                {"type": "command", "name": command_name, "params": params, "req_id": req_id}
            )

    def _update_blocked(self) -> bool:
        """更新四态机中段（gathering/awaiting_confirm/updating）→ 封锁新消息。"""
        try:
            from Architect.hub import get_default_hub
            return get_default_hub().is_update_active()
        except Exception:
            return False

    def get_pending_messages(self):
        """取出全部 pending 消息（command 留在队列等批次间执行）。"""
        with self._batch_lock:
            msgs, reqs, remaining = [], [], []
            for item in self.pending_items:
                if item["type"] == "message":
                    msgs.append(item["data"])
                    reqs.append(item["req_id"])
                else:
                    remaining.append(item)
            self.pending_items = remaining
            return msgs, reqs

    def drain_pending_items(self):
        with self._batch_lock:
            items = list(self.pending_items)
            self.pending_items.clear()
            return items

    def has_pending(self) -> bool:
        with self._batch_lock:
            return len(self.pending_items) > 0

    def interrupt(self):
        """ESC 中断：标记 + 杀掉正在跑的工具进程。"""
        self._interrupt_event.set()
        try:
            tool_process_tracker.kill_all()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 计划模式（只读运行时状态）
    # ------------------------------------------------------------------

    def set_plan_mode(self, active: bool):
        """进入/退出计划模式：切只读闸门 + 写 reminder 状态 + 广播事件。"""
        self.plan_mode = bool(active)
        if self.plan_gate is not None:
            self.plan_gate.enabled = self.plan_mode
        try:
            self.actor.set_plan_mode(self.plan_mode)
        except Exception:
            pass
        self._broadcast_event("plan.mode", {"active": self.plan_mode})
        self._send("text_output", "send_text_output",
                   "🔒 已进入只读计划模式" if self.plan_mode else "🔓 已退出计划模式，开始执行",
                   {"kind": "plan_mode"})

    def on_plan_mode_action(self, action, payload):
        """计划模式状态回调（SessionManager 路由进入）。"""
        payload = payload or {}
        if action == "enter":
            self.set_plan_mode(True)
        elif action == "exit_approved":
            self.set_plan_mode(False)
        elif action == "exit_supplement":
            # 用户要求补充：保持计划模式，仅记录补充内容
            logger.info("session %s: plan mode exit_supplement: %s",
                        self.session_id, str(payload.get("supplement", ""))[:80])

    def _plan_mode_trailing(self):
        """计划模式期间每一步工具循环的常驻 reminder（纯追加，不破坏缓存前缀）。"""
        if not self.plan_mode:
            return []
        return [f"<system-reminder>\n{PLAN_MODE_REMINDER}\n</system-reminder>"]

    # ------------------------------------------------------------------
    # processing 驱动（每 session 独立线程）
    # ------------------------------------------------------------------

    def _start_processing(self):
        if not self._processing:
            if self._processing_lock.acquire(blocking=False):
                threading.Thread(
                    target=self._process_queued_messages, daemon=True,
                    name=f"session-{self.session_id}",
                ).start()

    def _release_processing(self):
        try:
            self._processing_lock.release()
        except Exception:
            pass

    def _process_queued_messages(self):
        """后台处理：FIFO 排空队列（消息批处理；命令在批次间执行）。"""
        async def _execute_command(cmd_item):
            name = cmd_item["name"]
            try:
                if name == "compact":
                    await self.compact_session()
                elif name == "clear_session":
                    await self.clear_session()
                elif name == "upgrade":
                    from Architect.hub import get_default_hub
                    get_default_hub().start_update(source="manual", force=True)
                else:
                    logger.warning("Unknown command: %s", name)
                    self._broadcast_event("command.complete", {
                        "command": name, "success": False,
                        "error": f"Unknown command: {name}"})
                    return
                self._broadcast_event("token.stats", self.calculate_token_stats())
                self._broadcast_event("command.complete",
                                      {"command": name, "success": True})
                logger.info("Command '%s' executed successfully", name)
            except Exception as exc:
                logger.error("Command '%s' failed: %s", name, exc)
                self._broadcast_event("command.complete",
                                      {"command": name, "success": False,
                                       "error": str(exc)})

        try:
            while self.has_pending():
                items = self.drain_pending_items()
                if not items:
                    break

                last_answer = None
                i = 0
                while i < len(items):
                    item = items[i]
                    if item["type"] == "command":
                        self._run_sync(_execute_command(item))
                        i += 1
                    else:
                        msg_items = []
                        while i < len(items) and items[i]["type"] == "message":
                            msg_items.append(items[i])
                            i += 1
                        combined = "\n".join(m["data"] for m in msg_items)
                        if len(msg_items) > 1:
                            self._broadcast_event("user.batch", {
                                "count": len(msg_items),
                                "combined": combined[:100]})
                        last_answer = self._run_sync(self.process_message(combined))
                        self._broadcast_event("token.stats", self.calculate_token_stats())
                        self._broadcast_event("message.complete",
                                              {"answer": last_answer})

                # ── 批次后后台任务 watcher：答案发出后等后台结果，到了就唤醒 ──
                if self.has_pending():
                    continue
                if last_answer is None:
                    continue
                watch_window = int(config.get("agent.background_watch_window", 1800))
                max_reactivations = int(config.get("agent.background_max_reactivations", 3))
                task_mgr = self.bg_tasks

                if watch_window > 0 and task_mgr.reactivation_count < max_reactivations:
                    start_watch = time.time()

                    async def _bg_watcher():
                        while (time.time() - start_watch) < watch_window:
                            if self.has_pending():
                                return True
                            if task_mgr.has_pending():
                                bg_results = task_mgr.drain_pending()
                                task_mgr.increment_reactivation()
                                logger.info(
                                    "[bg-task] Post-answer reactivation #%d: %d results arrived",
                                    task_mgr.reactivation_count, len(bg_results),
                                )
                                task_mgr.inject_into_memory(self.memory, bg_results)
                                if not self._bg_reactivation_queued:
                                    synthetic_msg = (
                                        f"[System: background results arrived after previous answer] "
                                        f"{len(bg_results)} background task(s) completed. "
                                        f"Please review and respond if needed."
                                    )
                                    self.memory.append(CoreMessage.context_only(
                                        MessageSource.REACTIVATION, synthetic_msg,
                                    ).to_dict())
                                    with self._batch_lock:
                                        self.pending_items.append({
                                            "type": "message",
                                            "data": synthetic_msg,
                                            "req_id": "bg_reactivation",
                                        })
                                return True
                            await asyncio.sleep(2)
                        return False

                    self._run_sync(_bg_watcher())
        except Exception as e:
            logger.error("session %s processing loop error: %s",
                         self.session_id, e, exc_info=True)
        finally:
            self._processing = False
            self._release_processing()
            # 处理期间又来的新消息：重新驱动
            if self.has_pending():
                self._start_processing()

    def _run_sync(self, coro):
        """在 session 所属事件循环上同步等待一个协程。"""
        if self.loop is not None and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result()
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # 后台任务回调（SessionManager 的全局回调路由到这里）
    # ------------------------------------------------------------------

    def notify_bg_complete(self, task_id, bg_result):
        """后台任务完成：状态面板事件 + 空闲时合成唤醒消息。"""
        self._broadcast_event("task.background.status",
                              self.bg_tasks.get_status_summary())
        from Core.ToolCall import ToolCall
        failed = bool(bg_result.error) or ToolCall.classify_is_error(
            bg_result.tool_name, str(bg_result.result)
        )
        self._broadcast_event("task.background.update", {
            "task_id": task_id,
            "tool": bg_result.tool_name,
            "status": "error" if failed else "success",
            "timed_out": bool(getattr(bg_result, "timed_out", False)),
            "error": (str(bg_result.error)[:200] if bg_result.error else ""),
        })
        if self._processing:
            return
        if self._bg_reactivation_queued:
            return
        self._bg_reactivation_queued = True
        synthetic_msg = (
            f"[System: background results arrived after previous answer] "
            f"Background task(s) completed. Please review and respond if needed."
        )
        self.memory.append(CoreMessage.context_only(
            MessageSource.REACTIVATION, synthetic_msg,
        ).to_dict())
        with self._batch_lock:
            self.pending_items.append({
                "type": "message", "data": synthetic_msg,
                "req_id": "bg_reactivation",
            })
        self._start_processing()

    def notify_bg_start(self, task_id, meta):
        self._broadcast_event("task.background.update", {
            "task_id": task_id,
            "tool": meta.get("tool_name", "?"),
            "args": meta.get("args", ""),
            "status": "running",
            "started_at": meta.get("submitted_at", 0),
        })

    # ------------------------------------------------------------------
    # turn 循环
    # ------------------------------------------------------------------

    async def process_message(self, message: str) -> str:
        """处理一条（或一批合并的）用户消息，返回最终回答。"""
        self._processing = True
        self._micro_compacted = False
        self._turn_tools = []          # 本轮工具调用统计（供 answer 附带摘要）

        # 重置本轮的工具调用历史板与触及文件跟踪（供 Verify 上下文收敛用）
        try:
            from Tools.touched_files import reset as reset_touched_files
            reset_touched_files()
            from Tools.tool_call_board import reset_main_calls
            reset_main_calls()
        except Exception:
            pass

        # checkpoint：turn 开始（git 基线 + 并入 turn 间隙事件）；
        # 同步调用廉价（git 调用有 10s 超时），失败内部降级不抛
        if self._ckpt:
            try:
                self._ckpt.begin_turn(self.session_id, self._turn_count + 1)
            except Exception as e:
                logger.warning("checkpoint begin_turn failed: %s", e)

        # spinner（思考开始）
        self._broadcast_thinking(None)

        # PipelineManager: notify activity & check triggers
        if self.pipeline_manager:
            try:
                with timeit("gateway.process.notify_activity"):
                    self.pipeline_manager.notify_activity()
            except Exception as e:
                logger.warning(f"PipelineManager.notify_activity failed: {e}")

        if self.pipeline_manager:
            try:
                def _on_l1_start():
                    self._broadcast_event("agent.l1_extracting", {})
                with timeit("gateway.process.on_turn_start"):
                    self.pipeline_manager.on_turn_start(before_l1_callback=_on_l1_start)
            except Exception as e:
                logger.warning(f"PipelineManager.on_turn_start failed: {e}")

        try:
            self._interrupt_event.clear()

            # 合并第一批待处理消息
            pending_msgs, pending_reqs = self.get_pending_messages()
            if "bg_reactivation" in pending_reqs:
                self._bg_reactivation_queued = False
            all_messages = [message] + pending_msgs
            combined = "\n".join(all_messages)

            self.memory.append({"role": "user", "content": combined})
            self._turn_count += 1
            self.actor.reset_retrieval_cache()
            lifecycle(
                "══ 第 {} 轮 ══".format(self._turn_count),
                f'用户消息: "{combined[:80]}"',
                level=1,
            )

            if len(all_messages) > 1:
                self._broadcast_event("user.batch", {
                    "count": len(all_messages),
                    "combined": combined[:100]})

            step = 0
            max_steps = config.get("agent.maxDepth", 8)
            overflow_retried = False

            def emit_token_stats_from_build(build_result):
                """复用 actor 主构建的结果发 token 统计。"""
                token_stats = self.calculate_token_stats_from_messages(
                    build_result["messages"]
                )
                self._broadcast_event("token.stats", token_stats)

            while step < max_steps:
                step += 1

                # 中途合并新到的消息
                pending_msgs, pending_reqs = self.get_pending_messages()
                if pending_msgs:
                    pending_text = "\n".join(pending_msgs)
                    logger.info(
                        "Merging %d pending message(s) into context at step %d",
                        len(pending_msgs), step,
                    )
                    if "bg_reactivation" in pending_reqs:
                        self._bg_reactivation_queued = False
                    if "bg_reactivation" not in pending_reqs:
                        self.memory.append({"role": "user", "content": f"[新消息] {pending_text}"})
                    combined += f"\n{pending_text}"
                    self._broadcast_event("user.merge", {
                        "count": len(pending_msgs),
                        "merged": pending_text[:100]})
                    self._broadcast_thinking(None)

                # Drain completed background tasks before each step
                self._drain_background_results()

                logger.info("step %d: bg drained, entering micro-compact check", step)
                self._arm_step_watchdog(step)

                # Micro-compact: collapse older tool results (once per turn)
                if (
                    not self._micro_compacted
                    and config.get("agent.micro_compact.enabled", True)
                ):
                    with timeit("gateway.step.micro_compact"):
                        messages = self.memory.get_context()
                        age_h = float(config.get("agent.micro_compact.age_threshold_hours", 1))
                        keep_n = int(config.get("agent.micro_compact.keep_tool_results", 5))
                        min_chars = int(config.get("agent.micro_compact.min_content_chars", 120))
                        compacted, folded = self.ctx_manager.micro_compact(
                            messages,
                            age_threshold_hours=age_h,
                            keep_tool_results=keep_n,
                            min_content_chars=min_chars,
                        )
                        if compacted is not messages:
                            self.memory.clear_context()
                            for msg in compacted:
                                self.memory.append(msg)
                            logger.info(
                                "Micro-compact: folded %d older tool results (threshold=%sh, keep=%d, min_chars=%d)",
                                folded, age_h, keep_n, min_chars,
                            )
                            self._broadcast_event("context.micro_compact",
                                                  {"removed_results": folded})
                        self._micro_compacted = True

                # Auto context-compact：上次真实用量（或估算）超阈值时，调 LLM 前先压缩
                if config.get("agent.context_compact.auto_enabled", True):
                    threshold = int(config.get("agent.context_compact.auto_threshold_tokens", 800000) or 800000)
                    usage = self.actor.get_last_usage() or {}
                    current = (
                        int(usage.get("input_tokens") or 0)
                        + int(usage.get("cache_read_input_tokens") or 0)
                        + int(usage.get("cache_creation_input_tokens") or 0)
                    )
                    if current <= 0:
                        current = self._estimate_tokens(json.dumps(
                            self.memory.get_context(), ensure_ascii=False, default=str))
                    if current >= threshold:
                        logger.warning(
                            "auto compact: context ~%d tokens >= threshold %d，LLM 调用前自动压缩",
                            current, threshold,
                        )
                        self._broadcast_event("context.auto_compact",
                                              {"reason": "threshold", "tokens": current})
                        await self.compact_session()

                self._broadcast_event("agent.step", {"step": step})
                self._broadcast_event("agent.llm_requesting", {})

                current_step = step

                see_config = config.get("ui.see", {})
                show_thinking = see_config.get("thinking", True)
                show_text = see_config.get("text", True)

                def _on_tool_call(tool_name, tool_args, call_id="", *args):
                    self._turn_tools.append(str(tool_name))
                    self._broadcast_tool_use(tool_name, tool_args, str(call_id),
                                             meta={"step": current_step})

                def _on_thinking(thinking_text):
                    if not show_thinking:
                        return
                    self._broadcast_thinking(str(thinking_text).strip(),
                                             meta={"step": current_step})

                def _on_text(text_content):
                    text_out = str(text_content).strip()
                    if not text_out:
                        return
                    if show_text:
                        self._broadcast_event("agent.text_content", {
                            "step": current_step, "text": text_out})
                    # 聊天型渠道（QQ 等）：中间文本块也送达（用户要求全量 text 转发）
                    self._send("text_output", "send_text_output", text_out,
                               {"step": current_step})

                def _on_tool_result(tool_name, call_id, result_text):
                    self._broadcast_tool_result(tool_name, str(result_text), str(call_id))

                def _on_retry(attempt, max_retries, backoff, phase="backoff"):
                    self._broadcast_event("agent.retry", {
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "backoff": backoff,
                        "phase": phase,
                        "step": current_step})

                logger.info("Actor step %d/%d starting LLM call...", step, max_steps)
                with timeit(f"gateway.step{step}.actor_act"):
                    actor_response = await self.actor.act(
                        on_tool_call_start=_on_tool_call,
                        on_thinking=_on_thinking,
                        on_tool_result=_on_tool_result,
                        on_text=_on_text,
                        on_retry=_on_retry,
                        _interrupt_check=lambda: self._interrupt_event.is_set(),
                        on_built=emit_token_stats_from_build,
                        tool_result_trailing_texts=lambda: self._plan_mode_trailing(),
                    )

                # act() 期间完成的后台任务立即 drain（不等下一步）
                self._drain_background_results()

                response_type = actor_response.get("type")

                if response_type == "answer":
                    if self.bg_tasks.has_pending():
                        bg_results = self._drain_background_results()
                        if bg_results:
                            logger.info(
                                "Background tasks completed during answer, injecting %d result(s) and continuing",
                                len(bg_results),
                            )
                            continue

                    answer = actor_response.get("answer", "")
                    lifecycle("轮结束", f"answer {len(answer)} 字", level=2)
                    # 工具使用摘要经 meta 交给 channel 自行渲染（如 QQ 前缀行）
                    tool_meta = {}
                    if self._turn_tools:
                        counts = {}
                        for t in self._turn_tools:
                            counts[t] = counts.get(t, 0) + 1
                        tool_meta["tool_summary"] = "、".join(
                            f"{n}×{c}" if c > 1 else n for n, c in counts.items())
                    self._broadcast_answer(answer, tool_meta)
                    self._turn_outcome = "answer"

                    # PipelineManager: on_turn_end (L3 check)
                    if self.pipeline_manager:
                        ctx = self.memory.get_context()
                        turn_slice = ctx[-24:] if len(ctx) > 24 else ctx
                        try:
                            with timeit("gateway.process.on_turn_end"):
                                self.pipeline_manager.on_turn_end(turn_slice)
                        except Exception as e:
                            logger.warning(f"PipelineManager.on_turn_end failed: {e}")

                    return answer

                elif response_type == "tool":
                    pass

                elif response_type == "tool_batch":
                    pass

                elif response_type == "tool_backgrounded":
                    has_bg = actor_response.get("has_backgrounded", False)
                    if has_bg:
                        self._broadcast_event("task.background.started", {
                            "tool_calls": actor_response.get("tool_calls", [])})
                        # 聊天型渠道可见性：告知用户工具转后台、结果稍后送达
                        bg_names = sorted({
                            str(tc.get("tool_name", "?")).replace("mcp__", "")
                            for tc in actor_response.get("tool_calls", [])
                        })
                        self._send("text_output", "send_text_output",
                                   f"⏳ {'、'.join(bg_names)} 转后台执行，结果稍后自动送达",
                                   {"kind": "bg_started"})

                elif response_type == "continue":
                    pass

                elif response_type == "interrupted":
                    self._interrupt_event.clear()
                    tool_process_tracker.kill_all()
                    self.memory.append(CoreMessage.context_only(
                        MessageSource.INTERRUPT,
                        "Request interrupted by user",
                    ).to_dict())
                    self._broadcast_event("agent.interrupted",
                                          {"message": "请求已被用户中断"})
                    lifecycle("轮结束", "被用户中断（工具执行期间）", level=2)
                    # 终态文本也必须送达到每个 channel（非 TUI channel 没有事件面板）
                    self._broadcast_answer("请求已被用户中断。")
                    self._turn_outcome = "interrupted"
                    return "请求已被用户中断。"

                elif response_type == "error":
                    error_msg = actor_response.get("error", "Unknown error")
                    # 上下文超限兜底：压缩后重试当前 turn（每轮只试一次）
                    if "context window" in str(error_msg) and not overflow_retried:
                        overflow_retried = True
                        logger.warning("context overflow: force compact_session and retry turn")
                        self._broadcast_event("context.auto_compact",
                                              {"reason": "api_400_context_overflow"})
                        await self.compact_session()
                        continue
                    logger.error(f"Actor error: {error_msg}")
                    lifecycle("轮结束", f"错误: {error_msg}", level=2)
                    self._broadcast_answer(f"Error: {error_msg}")
                    self._turn_outcome = "error"
                    return f"Error: {error_msg}"

                else:
                    logger.warning(f"Unknown actor response type: {response_type}")
                    logger.warning(f"Response: {actor_response}")
                    break

            logger.warning("Max steps (%d) reached for session %s", max_steps, self.session_id)
            lifecycle("轮结束", f"达到最大步数 {max_steps}", level=2)
            max_steps_text = "I apologize, but I've reached the maximum number of steps. Please try rephrasing your question."
            self._broadcast_answer(max_steps_text)
            self._turn_outcome = "max_steps"
            return max_steps_text

        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e) or "(no message)"
            logger.error(f"Error processing message: {err_type}: {err_msg}", exc_info=True)
            self._broadcast_event("agent.error",
                                  {"message": f"Agent异常 [{err_type}]: {err_msg}"})
            raise
        finally:
            # checkpoint：turn 结束收口（finalize 重哈希 + git 对账 + 落盘）。
            # 放 finally 保证 5 条出路径全覆盖；to_thread 避免 git subprocess
            # 阻塞事件循环；内部全吞异常，绝不破坏 finally。
            if self._ckpt:
                try:
                    await asyncio.to_thread(
                        self._ckpt.end_turn, self._turn_outcome or "error")
                except Exception as e:
                    logger.warning("checkpoint end_turn failed: %s", e)
                self._turn_outcome = None
            self._disarm_step_watchdog()
            self._processing = False
            # 复位后台唤醒去重标志（轮结束即允许下一批空闲完成重新排队）
            self._bg_reactivation_queued = False

    def _drain_background_results(self):
        """收后台结果 → 注入本 session 记忆 → 广播事件。"""
        bg_results = self.bg_tasks.drain_pending()
        if not bg_results:
            return []
        self.bg_tasks.inject_into_memory(self.memory, bg_results)
        self._broadcast_event("task.background.completed", {
            "count": len(bg_results),
            "task_ids": [r.task_id for r in bg_results],
        })
        # 聊天型渠道可见性：后台结果已回到会话（下一轮回答可见）
        done_names = sorted({
            str(r.tool_name).replace("mcp__", "") for r in bg_results
        })
        self._send("text_output", "send_text_output",
                   f"📥 后台任务完成：{'、'.join(done_names)}（结果已入上下文）",
                   {"kind": "bg_done"})
        logger.info("Drained %d background task result(s) into memory", len(bg_results))
        return bg_results

    # ------------------------------------------------------------------
    # 会话管理命令
    # ------------------------------------------------------------------

    async def clear_session(self):
        """清空本 session 的会话记忆（金字塔不动）。"""
        if self.memory:
            self.memory.clear_context()
        if self.pipeline_manager:
            self.pipeline_manager.reset_warmup()
        try:
            import Tools.builtin.task_tools as task_tools
            task_tools.tasks_clear()
        except Exception:
            pass
        self.set_plan_mode(False)

    async def compact_session(self):
        """LLM 摘要旧历史：最近 keep_recent_turns 个用户 turn 原文保留，更早的压成一条摘要。
        摘要生成失败/为空时不动历史（避免清库后上下文永久丢失）。"""
        if not self.memory:
            return False

        messages = self.memory.get_context()
        if not messages:
            return False

        def _is_turn_start(msg):
            # 真实用户输入与工具结果同为 role=user，靠 content 形态区分
            if not isinstance(msg, dict) or msg.get("role") != "user":
                return False
            content = msg.get("content")
            if isinstance(content, str):
                return True
            if isinstance(content, list):
                return not any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
            return False

        keep_turns = int(config.get("agent.context_compact.keep_recent_turns", 4) or 4)
        turn_starts = [i for i, m in enumerate(messages) if _is_turn_start(m)]
        if len(turn_starts) <= keep_turns:
            logger.info(
                "Compact skipped: only %d user turn(s) (<= keep_recent_turns=%d)",
                len(turn_starts), keep_turns,
            )
            return False
        cut = turn_starts[-keep_turns]
        old_messages, recent_messages = messages[:cut], messages[cut:]

        history_lines = []
        for msg in old_messages:
            role = msg.get("role", "unknown") if isinstance(msg, dict) else "unknown"
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        t = item.get("type", "")
                        if t == "text":
                            parts.append(item.get("text", ""))
                        elif t == "tool_use":
                            parts.append(f"[调用工具: {item.get('name', '?')}]")
                        elif t == "tool_result":
                            rc = item.get("content", "")
                            parts.append(f"[工具结果: {str(rc)[:200]}]")
                    else:
                        parts.append(str(item))
                content = " ".join(parts)
            content = str(content)[:2000]
            history_lines.append(f"[{role}] {content}")

        history_text = "\n".join(history_lines)

        compact_prompt = (
            "你是一个对话摘要助手。请用中文对以下对话进行精炼摘要，保留关键信息：\n"
            "1. 用户的主要问题和需求\n"
            "2. 助手给出的关键回答和结论\n"
            "3. 任何重要的决策、代码修改或文件操作\n"
            "4. 未解决的问题或待办事项\n\n"
            "请用200字以内完成摘要，直接输出摘要文本，不要加任何前缀或标记。\n\n"
            f"=== 对话记录 ===\n{history_text}\n=== 结束 ==="
        )

        try:
            # actor 即 LLM 访问点（LLMCore 单例路径）
            summary = self.actor.query([{"role": "user", "content": compact_prompt}])
            if not summary or not str(summary).strip():
                logger.warning("Compact summary empty; history left untouched")
                return False
        except Exception as e:
            logger.warning(f"Compact summary generation failed; history left untouched: {e}")
            return False

        self.memory.clear_context()
        self.memory.append({
            "role": "system",
            "content": f"[对话摘要 - {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{str(summary).strip()}"
        })
        for msg in recent_messages:
            self.memory.append(msg)
        logger.info(
            f"Session compacted: {len(messages)} messages → summary ({len(str(summary).strip())} chars)"
            f" + kept last {len(recent_messages)} messages ({keep_turns} turns)"
        )
        return True

    def get_history_payload(self):
        """agent.get_history 的回放序列化（tool_use/tool_result 配对回填）。

        存储的 assistant 文本可能带有 LLM 模仿的 [ID:msg_xxx] 前缀，回放前
        统一剥离（与 ActorAgent 实时输出同一规则）。
        """
        from Core.text_utils import strip_id_prefixes as _strip_id_prefixes

        history = self.memory.get_context()
        serialized = []
        pending_tools = {}

        for msg in history:
            if not isinstance(msg, dict):
                continue
            if CoreMessage.is_context_only(msg):
                continue
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")

            if isinstance(content, str):
                if content.strip():
                    serialized.append({
                        "role": role, "kind": "text",
                        "text": _strip_id_prefixes(content), "timestamp": ts,
                    })
                continue
            if not isinstance(content, list):
                continue

            for item in content:
                if not isinstance(item, dict):
                    continue
                block_type = item.get("type")
                if block_type == "text":
                    text = item.get("text", "")
                    if text.strip():
                        serialized.append({
                            "role": role, "kind": "text",
                            "text": _strip_id_prefixes(text), "timestamp": ts,
                        })
                elif block_type == "thinking":
                    thinking = item.get("thinking", "")
                    if thinking.strip():
                        serialized.append({
                            "role": role, "kind": "thinking",
                            "text": _strip_id_prefixes(thinking)[:4000], "timestamp": ts,
                        })
                elif block_type == "tool_use":
                    tool_input = item.get("input", {})
                    if isinstance(tool_input, dict):
                        args_brief = ", ".join(
                            (str(v)[:24] + "…") if len(str(v)) > 24 else str(v)
                            for v in tool_input.values()
                        )
                    else:
                        args_brief = str(tool_input)[:40]
                    entry = {
                        "role": "assistant", "kind": "tool",
                        "tool": item.get("name", "?"),
                        "args": args_brief,
                        "args_full": json.dumps(tool_input, ensure_ascii=False, indent=2)[:2000],
                        "result": None,
                        "is_error": False,
                        "timestamp": ts,
                    }
                    serialized.append(entry)
                    pending_tools[item.get("id", "")] = entry
                elif block_type == "tool_result":
                    result_content = item.get("content", "")
                    if isinstance(result_content, str):
                        result_text = result_content
                    elif isinstance(result_content, list):
                        result_text = "\n".join(
                            rc.get("text", "")
                            for rc in result_content
                            if isinstance(rc, dict) and rc.get("type") == "text"
                        )
                    else:
                        result_text = str(result_content)
                    entry = pending_tools.pop(item.get("tool_use_id", ""), None)
                    if entry is not None:
                        entry["result"] = result_text[:4000]
                        entry["is_error"] = bool(item.get("is_error"))
                    else:
                        serialized.append({
                            "role": role, "kind": "tool",
                            "tool": "?", "args": "",
                            "result": result_text[:4000],
                            "is_error": bool(item.get("is_error")),
                            "timestamp": ts,
                        })

        for entry in pending_tools.values():
            entry["result"] = "(no result — session interrupted)"

        # 只回放今天的内容；今天没有则回退最近 20 条
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        today_entries = [e for e in serialized if str(e.get("timestamp", "")).startswith(today_prefix)]
        return today_entries if today_entries else serialized[-20:]

    # ------------------------------------------------------------------
    # token 统计
    # ------------------------------------------------------------------

    def _estimate_tokens(self, text):
        return TokenEstimator().estimate(text)

    @staticmethod
    def _stringify_token_content(content):
        if isinstance(content, str):
            return content
        if content is None:
            return ""

        # image 块的 base64 会把估算抬高数万 token，换成定长占位
        def _mask_images(node):
            if isinstance(node, list):
                return [_mask_images(b) for b in node]
            if isinstance(node, dict):
                if node.get("type") == "image":
                    return {"type": "text", "text": "[image]" + "x" * 5900}
                return {k: _mask_images(v) for k, v in node.items()}
            return node

        if isinstance(content, list):
            content = _mask_images(content)
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except TypeError:
            return str(content)

    def _estimate_rendered_messages(self, messages):
        if not self.actor:
            return {"current_tokens": 0}

        prepared = self.actor.prepare_messages_for_provider(messages)

        if prepared is not None:
            system_blocks, remote_messages = prepared
            parts = []
            for block in system_blocks:
                text = self._stringify_token_content(block.get("text", "")).strip()
                if not text:
                    continue
                parts.append(f"system: {text}")
            for message in remote_messages:
                role = message.get("role", "user")
                content = self._stringify_token_content(message.get("content", "")).strip()
                if content:
                    parts.append(f"{role}: {content}")
            current_tokens = self._estimate_tokens("\n".join(parts)) if parts else 0
            return {"current_tokens": current_tokens}

        current_tokens = self._estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str))
        return {"current_tokens": current_tokens}

    def calculate_token_stats(self, extra_system_prompt=None):
        """下一次组装请求的紧凑 token 统计（缓存命中率取真实 usage）。"""
        if not self.actor:
            return {"current_tokens": 0, "prompt_cache_ratio": 0.0}
        snapshot = self.actor.build_messages_with_document(extra_system_prompt)
        return self.calculate_token_stats_from_messages(snapshot["messages"])

    def calculate_token_stats_from_messages(self, messages):
        """基于已组装好的 messages 估算 token（不重新构建提示词）。"""
        stats = self._estimate_rendered_messages(messages)
        current_tokens = stats["current_tokens"]

        usage = self.actor.get_last_usage() or {}
        uncached_input = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        total_input = uncached_input + cache_read + cache_write
        prompt_cache_ratio = (cache_read / total_input) if total_input > 0 else 0.0
        return {
            "current_tokens": current_tokens,
            "prompt_cache_ratio": prompt_cache_ratio,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "uncached_input_tokens": uncached_input,
        }

    # ------------------------------------------------------------------
    # 卡死看门狗
    # ------------------------------------------------------------------

    def _arm_step_watchdog(self, step: int):
        """step 超时未完成时由独立线程周期性 dump 全部线程栈。

        绝不用 faulthandler.dump_traceback_later + cancel——cancel 会 join
        内部 watchdog 线程，若它正在 dump（需要 GIL）而本线程持有 GIL 等待
        join，直接死锁。
        """
        timeout = float(config.get("agent.step_watchdog_seconds", 150) or 150)
        if timeout <= 0:
            return
        try:
            self._watchdog_state = {"step": step, "armed_at": time.time(), "timeout": timeout}
            if not self._watchdog_thread_started:
                self._watchdog_thread_started = True
                threading.Thread(
                    target=self._step_watchdog_loop, daemon=True,
                    name=f"step-watchdog-{self.session_id}",
                ).start()
        except Exception as e:
            logger.warning("step watchdog arm failed: %s", e)

    def _disarm_step_watchdog(self):
        try:
            self._watchdog_state = {"step": None, "armed_at": None, "timeout": 0.0}
        except Exception:
            pass

    def _step_watchdog_loop(self):
        from Gateway.heartbeat import beat
        log_base = str(config.get("logging.base_dir", "./runtime_memory/logs"))
        mem_base = str(Path(log_base).parent)
        not_ready_after = float(config.get(
            "architect_hub.guardian.not_ready_seconds", 600) or 600)
        not_ready_since = None
        while True:
            time.sleep(5)
            state = self._watchdog_state or {}
            armed_at = state.get("armed_at")
            beat(self.session_id, step=state.get("step"),
                 armed_since=armed_at, base_dir=mem_base)
            if not armed_at:
                not_ready_since = None
                continue
            if time.time() - armed_at < float(state.get("timeout") or 150.0):
                continue
            try:
                log_dir = Path(config.get("logging.base_dir", "./runtime_memory/logs")) / "gateway"
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(log_dir / "watchdog.log", "a", encoding="utf-8") as fh:
                    fh.write(
                        f"\n===== session {self.session_id} step {state.get('step')} "
                        f"watchdog TIMEOUT at {datetime.now().isoformat()} =====\n"
                    )
                    fh.flush()
                    faulthandler.dump_traceback(file=fh)
            except Exception as e:
                logger.warning("step watchdog dump failed: %s", e)
            self._watchdog_state["armed_at"] = time.time()
            # 不就绪累计计时：step 持续卡死超阈值 → 唤醒 Architect（守护职责）。
            # 单 step 超时只 dump；持续卡死才是"主智能体长时间不就绪"
            if not_ready_since is None:
                not_ready_since = time.time()
            stuck = time.time() - not_ready_since
            if not_ready_after > 0 and stuck >= not_ready_after:
                not_ready_since = time.time()   # 重置累计窗口；hub 冷却兜底
                self._wake_architect_guardian(state, stuck)

    def _wake_architect_guardian(self, state, stuck_seconds: float):
        """不就绪唤醒：fire-and-forget 守护线程（dispatch 长耗时，绝不阻塞看门狗）。"""
        try:
            from Architect.hub import get_default_hub
            from Architect.wake import KIND_GUARDIAN, REASON_FATAL, WakeRequest
            req = WakeRequest(
                reason=REASON_FATAL, kind=KIND_GUARDIAN, priority=0,
                context={
                    "trigger": "step_not_ready",
                    "session": self.session_id,
                    "step": state.get("step"),
                    "stuck_seconds": int(stuck_seconds),
                })
            threading.Thread(
                target=get_default_hub().request_wake, args=(req,),
                daemon=True, name=f"architect-wake-{self.session_id}",
            ).start()
        except Exception as e:
            logger.warning("architect guardian wake failed: %s", e)

    # ------------------------------------------------------------------

    def shutdown(self):
        """会话收尾（当前无 per-session 资源，预留）。"""
        self._disarm_step_watchdog()
