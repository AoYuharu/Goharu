"""SessionManager —— 多 session 的装配根与路由器。

职责：
- 装配进程级共享服务（MemoryBridge pyramid / Tools runtime / ContextManager /
  PipelineManager 单例）
- 按 config.yaml `sessions:` 显式创建 AgentSession 并挂载 channel（N:1）
- channel → session 的入站路由；全局工具事件（任务清单/确认/提问/流式输出/
  子代理）路由到目标 session 广播
- 无 `sessions:` 配置时退化为单会话（default + tui），行为与旧 Gateway 一致
"""

import logging
import os
import threading

from Core.LifecycleLog import lifecycle
from Core.timing import timeit
from Memory.bridge import create_memory
from Prompting.api import create_context_manager
from Tools.api import create_tools, close_tools
from configurationLoader import config

from Gateway.channels import create_channel, load_plugins
from Gateway.channels.base import ChannelMessage
from Gateway.session import AgentSession
from Gateway.session_memory import SessionMemoryView
from Gateway.plan_mode import PlanModeGate

logger = logging.getLogger(__name__)


class SessionManager:
    """多 session 装配根：共享服务 + session 生命周期 + 消息路由。"""

    def __init__(self, tui_transport=None, loop=None):
        self._tui_transport = tui_transport  # TUI 的 JSON-RPC 写出函数（write_json）
        self.loop = loop

        # 进程级共享服务
        self.memory = None           # MemoryBridge（共享金字塔）
        self.runtime = None
        self.bg_tasks = None
        self.ctx_manager = None
        self.pipeline_manager = None
        self.checkpoint = None       # CheckpointManager（可审计+可回退）

        self.sessions: dict = {}     # session_id → AgentSession
        self._channel_session: dict = {}  # channel 实例 → session_id
        self.default_session_id = "default"

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _session_configs(self):
        """读 config.yaml sessions:；无配置退化为单会话（default + tui）。

        隐式 default session 不带 FIFO 上限（max_messages=0，保持旧行为）；
        显式配置的 session 默认 max_messages=10（多 session 模型）。
        """
        cfgs = config.get("sessions", None)
        if not cfgs:
            return [{
                "id": "default",
                "max_messages": 0,
                "channels": [{"type": "tui", "channel_id": "tui_main"}],
            }]
        return cfgs

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    async def start(self):
        """装配共享服务 → 逐 session 实例化 → channel bind+start。"""
        # ── 共享记忆金字塔 ──
        self.memory = create_memory()
        logger.info("Memory initialized (via Memory.bridge)")
        lifecycle("启动: MemoryManager", "初始化完成")

        # embedding 预热（守护线程，不阻塞主链路）
        def _warm_embedding():
            with timeit("startup.embedding.warmup"):
                ok = self.memory.warmup_embedding()
                logger.info("embedding warmup done, ok=%s", ok)

        threading.Thread(target=_warm_embedding, name="embedding-warmup",
                         daemon=True).start()
        lifecycle("启动: embedding 预热", "后台线程")

        # ── CheckpointManager（turn 级审计/回退，进程级单例）──
        # 事件出口复用全局广播路由；repo_root 可由配置覆盖（默认 cwd）
        if config.get("checkpoint.enabled", True):
            from Checkpoint.api import create_checkpoint_manager
            self.checkpoint = create_checkpoint_manager(
                config=config,
                memory=self.memory,
                repo_root=config.get("checkpoint.repo_root", None),
                event_sink=self._broadcast_global,
            )
            logger.info("CheckpointManager initialized")
            lifecycle("启动: CheckpointManager", "初始化完成")

            # Memory 写路径审计缝（L1 原子 + scene/persona 文件写）
            from Memory import change_events
            change_events.set_l1_listener(
                lambda op, atom_id, old_row, new_row:
                    self.checkpoint.on_memory_event("l1", op, atom_id, old_row, new_row))
            change_events.set_memory_file_listener(self.checkpoint.on_file_event)

            # 审计/回退工具（ListCheckpoints/ShowCheckpoint/RollbackCheckpoint）句柄
            from Tools.builtin import checkpoint_tools
            checkpoint_tools.set_checkpoint_handle(self.checkpoint)

        # ── PipelineManager（金字塔编排，进程级单例）──
        from Agent.PipelineManager import PipelineManager
        self.pipeline_manager = PipelineManager(memory_manager=self.memory)
        logger.info("PipelineManager initialized")
        lifecycle("启动: PipelineManager", "初始化完成")

        try:
            migrated_l2 = self.pipeline_manager.migrate_legacy_l2_to_scene_blocks()
            if migrated_l2 > 0:
                logger.info("Migration: %d L2 scenes migrated to scene_blocks", migrated_l2)
            self.pipeline_manager.migrate_legacy_l3_to_persona()
        except Exception as e:
            logger.warning("Migration failed (non-critical): %s", e)

        # ── 工具运行时（hooks 把全局工具事件路由到目标 session 广播）──
        self.runtime = await create_tools(
            enable_mcp=True,
            memory=self.memory,
            on_task_change=lambda tasks: self._on_tasks_updated(tasks),
            on_confirm_needed=lambda confirm_id, cmd, description: self._dispatch_confirmation(
                confirm_id, cmd, description),
            on_question=lambda request_id, payload: self._dispatch_question(
                request_id, payload),
            on_tool_output=lambda line, stream, source: self._broadcast_global(
                "tool.output", {"line": line, "stream": stream, "cmd": source}),
            on_subagent_event=lambda payload: self._broadcast_global(
                "subagent.update", payload),
            on_plan_mode=lambda action, payload: self._dispatch_plan_mode(
                action, payload),
            on_file_change=(self.checkpoint.on_file_event if self.checkpoint else None),
        )
        self.bg_tasks = self.runtime.background_tasks
        lifecycle("启动: 工具运行时", "初始化完成")
        logger.info("Tool runtime initialized")
        lifecycle("启动: 工具加载", f"{len(self.runtime.last_tool_definitions)} 个工具")

        # 后台任务完成/开始 → 路由到 session（Phase 1：default session）
        self.bg_tasks.register_callback(self._on_bg_complete)
        self.bg_tasks.register_start_callback(self._on_bg_start)

        # ── 上下文管理器（压缩阶梯工具，session 共享实例）──
        self.ctx_manager = create_context_manager()

        # ── 外部 channel 插件 ──
        load_plugins(config.get("channels.plugins", []))

        # ── 逐 session 实例化 ──
        from Agent.api import create_agent
        for scfg in self._session_configs():
            sid = str(scfg.get("id", "default"))
            # 隐式 default 配置自带 max_messages=0（旧行为）；
            # 显式配置缺省给 10（多 session FIFO 模型）
            max_messages = scfg.get("max_messages", 10)
            view = SessionMemoryView(sid, self.memory, max_messages=max_messages)
            gate = PlanModeGate(self.runtime)
            agent = await create_agent(tools=gate, memory=view)
            session = AgentSession(
                sid, view, agent.actor, self.pipeline_manager,
                self.bg_tasks, self.ctx_manager, loop=self.loop,
                checkpoint=self.checkpoint,
            )
            session.plan_gate = gate
            for ch_cfg in scfg.get("channels", []) or []:
                params = dict(ch_cfg.get("params") or {})
                if ch_cfg.get("type") == "tui" and self._tui_transport is not None:
                    params["transport"] = self._tui_transport
                ch = create_channel(
                    ch_cfg["type"],
                    ch_cfg.get("channel_id", ch_cfg["type"]),
                    params,
                )
                session.attach_channel(ch)
                ch.bind(session.enqueue)
                # AskUser 答案回送（channel 文本/按钮作答 → question_manager 放行工具线程）
                from Tools.question_manager import question_manager
                ch.bind_answer(
                    lambda rid, ans, _resolve=question_manager.resolve: _resolve(rid, ans))
                # 高危命令确认：channel 按钮/文本作答 → confirmation_manager 放行/拒绝
                from Tools.confirmation_manager import confirmation_manager
                ch.bind_confirm(
                    lambda cid, ok, _resolve=confirmation_manager.resolve: _resolve(cid, ok))
                # SendFile 工具接线：有 "file" 能力的 channel 注册为该 type 的发送器
                if "file" in ch.capabilities:
                    from Tools.builtin import channel_tools

                    def _sender(path_or_url, caption="", _ch=ch, channel_id=None,
                                file_type=None):
                        if channel_id and _ch.channel_id != channel_id:
                            return False, f"channel_id 不匹配（本实例 {_ch.channel_id}）"
                        ok = _ch.send_file(path_or_url, caption,
                                           force_type=file_type)
                        return ok, ("已受理发送" if ok else
                                    "发送失败（无活跃会话/文件不存在/超 5MB）")

                    channel_tools.register_channel_sender(ch.channel_type, _sender)
                self._channel_session[id(ch)] = sid
                try:
                    ch.start()
                except Exception as e:
                    logger.error("channel %s start failed: %s", ch, e)
            self.sessions[sid] = session
            logger.info("session '%s' ready (max_messages=%s, channels=%s)",
                        sid, max_messages,
                        [f"{c.channel_type}#{c.channel_id}" for c in session.channels])

        # ── AgentNet 组网 channel（可选，挂到 default session）──
        if config.get("agentnet.enabled", False):
            try:
                session = self.sessions[self.default_session_id]
                ch = create_channel("agentnet", "agentnet_main", {
                    "url": config.get("agentnet.url", "ws://127.0.0.1:7878"),
                    "http_base": config.get("agentnet.http_base", ""),
                    # 环境变量优先：多机部署时机器专属身份名不必进 git
                    "name": os.environ.get("AGENTNET_NAME")
                    or config.get("agentnet.name", "goharu"),
                    "identity_dir": config.get(
                        "agentnet.identity_dir", "./runtime_memory/agentnet/identity"),
                    "messagebox_path": config.get(
                        "agentnet.messagebox_path", "./runtime_memory/agentnet/messagebox.db"),
                    "focus": config.get("agentnet.focus", ""),
                    "tags": config.get("agentnet.tags", []),
                    "owner_ids": list(config.get("agentnet.owner_ids") or []),
                    "quiet_seconds": float(config.get("agentnet.quiet_seconds", 120)),
                })
                session.attach_channel(ch)
                ch.bind(session.enqueue)
                ch.start()
                # 工具句柄 + 状态自报（任一会话处理中即 working）+ 摘要便利贴
                from Tools.builtin import agentnet_tools
                agentnet_tools.set_agentnet_handle(ch)
                ch.set_state_provider(
                    lambda: "working" if any(s._processing for s in self.sessions.values())
                    else "idle")
                ch.set_can_auto_read(
                    lambda: not any(s._processing for s in self.sessions.values()))
                ch.set_control_handler(self._agentnet_control)
                session.memory.set_turn_extras_provider(ch.turn_extras)
                logger.info("agentnet channel ready: %s → %s",
                            ch.identity.agent_id, ch.url)
                lifecycle("启动: AgentNet channel", f"{ch.identity.agent_id} → {ch.url}")
            except Exception as e:
                logger.error("agentnet channel 启动失败（不阻断其余装配）: %s", e)

        # ── Architect ↔ Hero 双向沟通桥（Phase 1：default session）──
        # notify_user：Architect 报告 → default session 的全部 channel 广播
        # message_hero：Architect 消息 → Hero 会话队列（下一轮处理，标注来源）
        try:
            from Architect.hub import get_default_hub
            default_session = self.sessions.get(self.default_session_id)
            if default_session is not None:
                def _message_hero(text, _s=default_session):
                    _s.enqueue(ChannelMessage(
                        text=str(text), channel_type="architect",
                        channel_id="architect_hub"))

                get_default_hub().set_hero_bridge(
                    notify_user=lambda text: default_session._broadcast_answer(
                        f"🔧 {text}"),
                    message_hero=_message_hero,
                )
                # 更新四态机的运行时钩子：空闲探测 / 确认链 / 结束解锁
                from Tools.confirmation_manager import confirmation_manager
                get_default_hub().set_runtime_hooks(
                    idle_probe=lambda: not default_session._processing,
                    confirm_fn=confirmation_manager.request,
                    on_update_done=default_session._start_processing,
                )
                # 拉式唤醒源：定期检查 GitHub 新提交（audit 流把关）
                if config.get("architect_hub.upgrade.enabled", False):
                    from Architect.upgrader import GitUpdateSource
                    up_cfg = config.get("architect_hub.upgrade", {})
                    get_default_hub().register_wake_source(GitUpdateSource(
                        repo_dir=up_cfg.get("repo_dir", "."),
                        remote=up_cfg.get("remote", "origin"),
                        branch=up_cfg.get("branch", "master"),
                        check_interval_seconds=up_cfg.get(
                            "check_interval_seconds", 1800),
                    ))
                    get_default_hub().start_scheduler()
        except Exception as e:
            logger.warning("architect hero bridge wiring failed: %s", e)

        # ── Bootstrap：裸机首次部署（identity 缺失才跑，daemon 线程）──
        if config.get("architect_hub.bootstrap.enabled", True):
            try:
                from Architect.bootstrap import bootstrap_async
                bootstrap_async(
                    hub=get_default_hub(),
                    config=dict(config.data) if hasattr(config, "data") else {},
                )
            except Exception as e:
                logger.warning("bootstrap hook failed (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # AgentNet 中控（观察台按钮 → hub control 消息 → agentnet channel）
    # ------------------------------------------------------------------

    def _agentnet_control(self, action: str) -> str:
        """主人中控动作执行（agentnet channel 在收到 owner control 后调用）。"""
        logger.info("agentnet 中控执行: %s", action)
        if action == "clear_context":
            for s in self.sessions.values():
                s.memory.clear_context()
            return "全部会话历史已清空"
        if action == "clear_memory":
            from Memory.repositories.L1Repository import L1Repository
            repo = L1Repository(self.memory.db)
            atoms = repo.list_atoms(status="active")
            for a in atoms:
                repo.deactivate_atom(a["id"])
            persona_path = self.memory.persona.persona_path
            if persona_path.exists():
                persona_path.unlink()
            scene_files = self.memory.scene_files
            scenes_deleted = 0
            for sc in list(scene_files.list_scenes()):
                if scene_files.delete_scene(sc["slug"]):
                    scenes_deleted += 1
            return (f"记忆已清空（L1 {len(atoms)} 条置 inactive、"
                    f"persona 重置、scene {scenes_deleted} 个删除）")
        if action == "clear_messagebox":
            return "messagebox 已由 channel 清空"
        return f"未知动作: {action}"

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def get_session(self, session_id=None) -> AgentSession:
        sid = str(session_id or self.default_session_id)
        session = self.sessions.get(sid)
        if session is None:
            raise KeyError(f"未知 session: '{sid}'（已装配: {sorted(self.sessions)}）")
        return session

    @property
    def default_session(self) -> AgentSession:
        return self.sessions[self.default_session_id]

    def route_from_channel(self, msg: ChannelMessage):
        """channel 入站路由（channel 已 N:1 绑定，经 bind 直达 session，
        此路径用于需要按 channel_id 显式路由的场景）。"""
        for session in self.sessions.values():
            for ch in session.channels:
                if ch.channel_id == msg.channel_id and ch.channel_type == msg.channel_type:
                    session.enqueue(msg)
                    return
        logger.warning("no session bound for %s#%s, message dropped",
                       msg.channel_type, msg.channel_id)

    def _broadcast_global(self, event_type, payload):
        """全局工具事件（确认/提问/子代理/任务清单/流式输出）。

        Phase 1 路由策略：有正在处理的 session 则发给它（事件多半由它的
        turn 触发），否则发 default session。
        """
        target = None
        for session in self.sessions.values():
            if session._processing:
                target = session
                break
        if target is None:
            target = self.sessions.get(self.default_session_id)
        if target is not None:
            target._broadcast_event(event_type, payload)

    def _on_tasks_updated(self, tasks):
        """任务清单变化：TUI 走事件面板；聊天型渠道推送渲染后的计划文本。"""
        self._broadcast_global("tasks.updated", {"tasks": tasks})
        if not tasks:
            return
        icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}
        lines = ["📋 当前计划："]
        for t in tasks:
            mark = icon.get(t.get("status"), "•")
            lines.append(f"{mark} {t.get('subject', '')}")
        text = "\n".join(lines)
        target = None
        for session in self.sessions.values():
            if session._processing:
                target = session
                break
        if target is None:
            target = self.sessions.get(self.default_session_id)
        if target is None:
            return
        for ch in target.channels:
            if "text_output" in ch.capabilities:
                try:
                    ch.send_text_output(text, {"kind": "plan"})
                except Exception as e:
                    logger.warning("plan push to %s failed: %s", ch, e)

    def _dispatch_confirmation(self, confirm_id, cmd, description):
        """危险命令确认路由：event 能力 channel 走事件面板（TUI 原状），
        其余 channel 走 confirm_request（未覆写的自动退化为文本 y/n 问答）。"""
        self._broadcast_global("tool.confirm_request", {
            "confirm_id": confirm_id, "cmd": cmd, "description": description})
        target = None
        for session in self.sessions.values():
            if session._processing:
                target = session
                break
        if target is None:
            target = self.sessions.get(self.default_session_id)
        if target is None:
            return
        for ch in target.channels:
            if "event" in ch.capabilities:
                continue    # TUI 已通过事件面板收到
            try:
                ch.confirm_request(confirm_id, cmd, description)
            except Exception as e:
                logger.warning("confirm_request to %s failed: %s", ch, e)

    def _dispatch_question(self, request_id, payload):
        """AskUser 提问路由：event 能力 channel 走事件面板（TUI 原状），
        其余 channel 走 ask 三范式（未实现的范式在基类退化为文本问答）。"""
        target = None
        for session in self.sessions.values():
            if session._processing:
                target = session
                break
        if target is None:
            target = self.sessions.get(self.default_session_id)
        if target is not None:
            target.dispatch_question(request_id, payload)

    def _dispatch_plan_mode(self, action, payload):
        """计划模式状态回调路由：挑正在处理的 session（fallback default）切闸门。"""
        target = None
        for session in self.sessions.values():
            if session._processing:
                target = session
                break
        if target is None:
            target = self.sessions.get(self.default_session_id)
        if target is not None:
            target.on_plan_mode_action(action, payload)

    def _on_bg_complete(self, task_id, bg_result):
        session = self.sessions.get(self.default_session_id)
        if session is not None:
            session.notify_bg_complete(task_id, bg_result)

    def _on_bg_start(self, task_id, meta):
        session = self.sessions.get(self.default_session_id)
        if session is not None:
            session.notify_bg_start(task_id, meta)

    # ------------------------------------------------------------------

    async def shutdown(self):
        for session in self.sessions.values():
            try:
                session.shutdown()
            except Exception:
                pass
            for ch in session.channels:
                try:
                    ch.stop()
                except Exception:
                    pass
        if self.pipeline_manager:
            self.pipeline_manager.shutdown()
        if self.runtime:
            try:
                await close_tools(self.runtime)
            except Exception as e:
                logger.warning("tools runtime close failed: %s", e)
