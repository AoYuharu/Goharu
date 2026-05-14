"""
Agent 桥接器 - 连接 Gateway 和现有的 Agent 系统

职责：
- 将 Gateway 消息转换为 Agent 可处理的格式
- 调用现有的 Agent 处理逻辑
- 构建会话上下文提示
"""

from typing import Optional

from Agent.ActorAgent import ActorAgent
from Agent.ReflectionAgent import ReflectionAgent
from Memory.MemoryManager import MemoryManager

from .platforms.base import MessageEvent
from .session import SessionEntry, SessionContext, build_session_context_prompt, is_shared_session


class AgentBridge:
    """Agent 桥接器"""

    def __init__(
        self,
        actor: ActorAgent,
        reflector: ReflectionAgent,
        memory_manager: MemoryManager,
        session_store,
    ):
        """
        初始化桥接器

        Args:
            actor: ActorAgent 实例
            reflector: ReflectionAgent 实例
            memory_manager: MemoryManager 实例
            session_store: SessionStore 实例
        """
        self.actor = actor
        self.reflector = reflector
        self.memory_manager = memory_manager
        self.session_store = session_store

    async def process_message(
        self,
        event: MessageEvent,
        session_entry: SessionEntry,
    ) -> Optional[str]:
        """
        处理消息

        Args:
            event: 消息事件
            session_entry: 会话条目

        Returns:
            Optional[str]: 响应内容
        """
        print(f"[AgentBridge] Processing message from session {session_entry.session_key}")

        # 1. 构建会话上下文
        context = self._build_session_context(event.source, session_entry)
        print(f"[AgentBridge] Session context built")

        # 2. 生成上下文提示（可以注入到系统提示中）
        context_prompt = build_session_context_prompt(context)
        print(f"[AgentBridge] Context prompt: {context_prompt[:100]}...")

        # 3. 调用现有的 Agent 处理逻辑
        try:
            print(f"[AgentBridge] Calling run_agent...")
            from Agent.agent_loop import run_agent

            result = await run_agent(
                self.actor,
                self.reflector,
                event.text,
                self.memory_manager,
            )

            print(f"[AgentBridge] Agent result: {result}")
            final_answer = result.get("final_answer")
            print(f"[AgentBridge] Final answer: {final_answer[:100] if final_answer else 'None'}...")
            return final_answer

        except Exception as e:
            print(f"[AgentBridge] Error processing message: {e}")
            import traceback
            traceback.print_exc()
            return f"抱歉，处理消息时出错：{str(e)}"

    def _build_session_context(
        self,
        source,
        session_entry: SessionEntry,
    ) -> SessionContext:
        """
        构建会话上下文

        Args:
            source: 消息来源
            session_entry: 会话条目

        Returns:
            SessionContext: 会话上下文
        """
        return SessionContext(
            source=source,
            session_key=session_entry.session_key,
            session_id=session_entry.session_id,
            created_at=session_entry.created_at,
            updated_at=session_entry.updated_at,
            connected_platforms=[],  # 由 GatewayRunner 填充
            shared_multi_user_session=is_shared_session(
                source,
                self.session_store.group_sessions_per_user,
                self.session_store.thread_sessions_per_user,
            ),
        )
