"""
ACP (Agent Communication Protocol) Adapter
允许外部 Agent（如 Claude Code）通过 HTTP API 与 TableHelper Agent 通信
"""

from typing import Optional
from datetime import datetime

from .base import BasePlatformAdapter, MessageEvent, SessionSource, Platform, MessageType


class ACPAdapter(BasePlatformAdapter):
    """ACP 协议适配器 - 通过 HTTP API 接收消息"""

    def __init__(self, config: dict = None):
        """初始化 ACP 适配器"""
        super().__init__(config or {}, Platform.LOCAL)
        print("[ACPAdapter] Initialized")

    async def connect(self) -> bool:
        """
        连接到 ACP 服务（实际上是被动接收，所以直接返回 True）

        Returns:
            bool: 是否连接成功
        """
        self._mark_connected()
        print("[ACPAdapter] Connected (passive mode)")
        return True

    async def disconnect(self):
        """断开连接"""
        self._mark_disconnected()
        print("[ACPAdapter] Disconnected")

    async def send(self, chat_id: str, content: str, **kwargs):
        """
        发送消息（ACP 模式下不需要主动发送，由 HTTP 响应返回）

        Args:
            chat_id: 聊天 ID
            content: 消息内容

        Returns:
            SendResult: 发送结果
        """
        from .base import SendResult
        # ACP 模式下，消息通过 HTTP 响应返回，不需要主动发送
        print(f"[ACPAdapter] Message prepared for response: {content[:100]}...")
        return SendResult(success=True)

    def create_message_event(
        self,
        user_id: str,
        user_name: str,
        text: str,
        chat_id: Optional[str] = None,
        chat_type: str = "private",
    ) -> MessageEvent:
        """
        创建消息事件（供 HTTP API 调用）

        Args:
            user_id: 用户 ID
            user_name: 用户名
            text: 消息文本
            chat_id: 聊天 ID（可选）
            chat_type: 聊天类型（private/group）

        Returns:
            MessageEvent: 消息事件
        """
        source = SessionSource(
            platform=self.platform,
            user_id=user_id,
            user_name=user_name,
            chat_id=chat_id or user_id,
            chat_type=chat_type,
        )

        return MessageEvent(
            source=source,
            text=text,
            message_type=MessageType.TEXT,
            images=[],
            documents=[],
            timestamp=datetime.now(),
        )

    async def start_receiving(self):
        """
        开始接收消息（ACP 模式下由 HTTP 服务器处理，这里不需要实现）
        """
        print("[ACPAdapter] Passive receiving mode - waiting for HTTP requests")
        # 不需要主动轮询，由 HTTP 服务器调用 create_message_event
