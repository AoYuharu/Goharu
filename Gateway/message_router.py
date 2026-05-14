"""
消息路由器 - 负责消息的路由和分发逻辑

职责：
- 消息鉴权
- 命令解析和处理
- 会话状态检查
- 消息路由到 Agent
"""

import asyncio
from typing import Optional, Callable, Awaitable

from .platforms.base import MessageEvent, SessionSource
from .session import SessionStore, SessionEntry


# 消息处理器类型
MessageProcessor = Callable[[MessageEvent, SessionEntry], Awaitable[Optional[str]]]


class MessageRouter:
    """消息路由器"""

    def __init__(
        self,
        session_store: SessionStore,
        message_processor: MessageProcessor,
    ):
        """
        初始化路由器

        Args:
            session_store: 会话存储
            message_processor: 消息处理器（实际的 Agent 处理逻辑）
        """
        self.session_store = session_store
        self.message_processor = message_processor
        self._active_sessions = {}  # session_key -> asyncio.Event

    async def route_message(self, event: MessageEvent) -> Optional[str]:
        """
        路由消息

        Args:
            event: 消息事件

        Returns:
            Optional[str]: 响应内容
        """
        source = event.source
        print(f"[Router] Received message from {source.user_name} ({source.user_id})")
        print(f"[Router] Chat: {source.chat_id} ({source.chat_type})")
        print(f"[Router] Text: {event.text}")

        # 1. 用户鉴权（当前允许所有用户）

        # 2. 命令拦截
        if event.text.startswith("/"):
            print(f"[Router] Handling command: {event.text}")
            return await self._handle_command(event)

        # 3. 获取/创建 Session
        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key
        print(f"[Router] Session key: {session_key}")

        # 4. 检查会话忙碌状态
        if session_key in self._active_sessions:
            print(f"[Router] Session {session_key} is busy")
            return self._handle_busy_session(session_key)

        # 5. 执行消息处理
        try:
            print(f"[Router] Processing message...")
            self._active_sessions[session_key] = asyncio.Event()
            response = await self.message_processor(event, session_entry)

            # 更新会话时间戳
            self.session_store.touch_session(session_key)

            print(f"[Router] Response generated: {response[:100] if response else 'None'}...")
            return response
        except Exception as e:
            print(f"[Router] Error processing message: {e}")
            import traceback
            traceback.print_exc()
            return "抱歉，处理消息时出错了。"
        finally:
            if session_key in self._active_sessions:
                del self._active_sessions[session_key]

    def _handle_busy_session(self, session_key: str) -> str:
        """
        处理忙碌的会话

        Args:
            session_key: 会话键

        Returns:
            str: 响应内容
        """
        print(f"[Router] Session {session_key} is busy")
        return "⏳ 正在处理上一条消息，请稍候..."

    async def _handle_command(self, event: MessageEvent) -> Optional[str]:
        """
        处理命令

        Args:
            event: 消息事件

        Returns:
            Optional[str]: 响应内容
        """
        command = event.text.split()[0].lower()

        if command == "/help":
            return self._get_help_text()

        elif command == "/new" or command == "/reset":
            return self._reset_session(event.source)

        elif command == "/status":
            return self._get_session_status(event.source)

        return None

    def _get_help_text(self) -> str:
        """获取帮助文本"""
        return """可用命令：
/help - 显示帮助
/new - 开始新对话
/reset - 重置当前会话
/status - 查看会话状态"""

    def _reset_session(self, source: SessionSource) -> str:
        """重置会话"""
        session_key = self.session_store._generate_session_key(source)
        new_entry = self.session_store.reset_session(session_key)
        if new_entry:
            return "✓ 已开始新对话"
        else:
            return "✗ 重置失败"

    def _get_session_status(self, source: SessionSource) -> str:
        """获取会话状态"""
        session_entry = self.session_store.get_or_create_session(source)
        return f"""会话状态：
Session ID: {session_entry.session_id}
创建时间: {session_entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}
更新时间: {session_entry.updated_at.strftime('%Y-%m-%d %H:%M:%S')}"""
