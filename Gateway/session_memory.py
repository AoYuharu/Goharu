"""SessionMemoryView —— 多 session 的记忆视图适配器（耦合点）。

每个 AgentSession 一个实例：
- **L0 会话记忆隔离**：自有 WorkingMemory（session_id 区分；L0 SQLite 本就有
  session_id 列，daily JSON 非 default session 落到 daily/<session_id>/ 子目录）。
- **金字塔共享**：检索（L1/L2）、persona（L3）、memory_stats 全部代理到
  进程级共享的 MemoryBridge——各 session 共享同一个记忆金字塔。
- **FIFO 窗口**：get_context 只返回最近 max_messages 条（默认 10）；更早的
  消息仍留在 L0/daily 中（L1 提取每 turn 都在跑，内容已进金字塔，不丢失）。

对 ActorAgent 暴露的就是它的结构化接口契约（append/get_context/
retrieve_prompt_memory/get_persona_markdown），actor 无感。
"""

import logging
from pathlib import Path

from Memory.memory_config import MemoryConfig
from Memory.WorkingMemory import WorkingMemory

logger = logging.getLogger(__name__)


class SessionMemoryView:
    """单 session 的记忆视图：L0 隔离 + 共享 pyramid + FIFO 窗口。"""

    def __init__(self, session_id: str, shared_bridge, max_messages: int = 10):
        self.session_id = str(session_id)
        self._bridge = shared_bridge
        self._turn_extras_provider = None   # () -> list[str]，宿主注入
        # 注意：0 是合法值（不限窗口），不能用 `or` 吞掉（曾把 0 变 10 引发
        # FIFO 裸切配对 → 400 事故）
        self.max_messages = int(max_messages)

        overrides = {"memory.session_id": self.session_id}
        if self.session_id != "default":
            # 非 default session 的 daily JSON 落到子目录，避免与同日期的
            # default session 写同一个文件；default 保持原路径（向后兼容）
            base_dir = shared_bridge._cfg.get(
                "memory.daily.dir", "./runtime_memory/daily"
            )
            overrides["memory.daily.dir"] = str(Path(base_dir) / self.session_id)
        # 共享 bridge 的 l0_repo（同一条 MemoryDB 通路，session_id 列隔离）
        self.working = WorkingMemory(
            cfg=MemoryConfig(overrides), l0_repo=shared_bridge.l0_repo,
        )

    # ── ActorAgent 结构化接口 ────────────────────────────────────────

    def append(self, message):
        self.working.append(message)

    def get_context(self):
        """本 session 的会话记忆，FIFO 窗口（最近 max_messages 条）。

        窗口裁剪必须经 loop_messages.truncate_history——裸切片可能把
        assistant(tool_use) 切在窗外、user(tool_result) 留在窗内，
        制造孤立 tool_result 打爆 API。
        """
        messages = self.working.get_messages()
        if self.max_messages > 0 and len(messages) > self.max_messages:
            from Prompting.loop_messages import truncate_history
            return truncate_history(messages, self.max_messages)
        return messages

    def clear_context(self):
        self.working.clear_all()

    def retrieve_prompt_memory(self):
        """共享金字塔检索；查询从本 session 的上下文构建。

        turn_extras_provider（宿主注入，如 messagebox 摘要）的产出经
        retrieval_pack.extra_reminders 透传给 assembler 渲染为便利贴。
        """
        pack = self._bridge.retrieve_prompt_memory(messages=self.get_context())
        extras = None
        if self._turn_extras_provider is not None:
            try:
                extras = self._turn_extras_provider() or None
            except Exception:
                extras = None
        if extras:
            pack = dict(pack or {})
            pack["extra_reminders"] = extras
        return pack

    def set_turn_extras_provider(self, fn):
        """注入每轮额外便利贴提供者：() -> list[str]（如 messagebox 摘要）。"""
        self._turn_extras_provider = fn

    def get_persona_markdown(self):
        """共享 L3 persona（ActorAgent 静态前缀注入用，hasattr 守卫）。"""
        return self._bridge.get_persona_markdown()

    # ── PipelineManager 触点 ─────────────────────────────────────────

    @property
    def l0_repo(self):
        return self._bridge.l0_repo
