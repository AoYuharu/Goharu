"""
平台适配器基类 - 定义统一的平台接口

参考 hermes-agent 的 BasePlatformAdapter 设计
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List, Callable, Awaitable, Dict, Any

_logger = logging.getLogger(__name__)


class Platform(Enum):
    """支持的平台类型"""
    QQBOT = "qqbot"
    LOCAL = "local"  # 用于测试


class MessageType(Enum):
    """消息类型"""
    TEXT = "text"
    MIXED = "mixed"


@dataclass
class SessionSource:
    """消息来源标识 - 用于生成 session_key"""
    platform: Platform          # 平台类型
    chat_id: str               # 聊天ID (群ID/用户ID)
    chat_type: str             # 聊天类型: "dm", "group", "channel"
    user_id: Optional[str] = None      # 用户ID
    user_name: Optional[str] = None    # 用户名
    chat_name: Optional[str] = None    # 群名/频道名
    thread_id: Optional[str] = None    # 线程ID (论坛话题)
    guild_id: Optional[str] = None     # 服务器ID (Discord/Slack)
    message_id: Optional[str] = None   # 消息ID


@dataclass
class MessageEvent:
    """统一的消息事件结构"""
    source: SessionSource       # 消息来源
    text: str                  # 消息文本
    message_type: MessageType  # 消息类型
    images: List[str]          # 图片URL列表
    documents: List[tuple]     # 文档列表 (url, filename)
    timestamp: datetime        # 时间戳
    raw_data: Optional[Dict[str, Any]] = None  # 原始数据


@dataclass
class SendResult:
    """发送结果"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


# 消息处理器类型
MessageHandler = Callable[[MessageEvent], Awaitable[Optional[str]]]


class BasePlatformAdapter(ABC):
    """
    平台适配器基类

    所有平台适配器必须继承此类并实现抽象方法
    """

    def __init__(self, config: Dict[str, Any], platform: Platform):
        """
        初始化适配器

        Args:
            config: 平台配置
            platform: 平台类型
        """
        self.config = config
        self.platform = platform
        self._connected = False
        self._message_handler: Optional[MessageHandler] = None

    # ==================== 抽象方法（子类必须实现） ====================

    @abstractmethod
    async def connect(self) -> bool:
        """
        连接到平台

        Returns:
            bool: 连接是否成功
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开与平台的连接"""
        pass

    @abstractmethod
    async def send(
        self,
        chat_id: str,
        content: str,
        **kwargs
    ) -> SendResult:
        """
        发送消息

        Args:
            chat_id: 聊天ID
            content: 消息内容
            **kwargs: 平台特定参数

        Returns:
            SendResult: 发送结果
        """
        pass

    # ==================== 通用方法（子类可选覆盖） ====================

    async def send_typing(self, chat_id: str) -> bool:
        """
        发送"正在输入"状态

        Args:
            chat_id: 聊天ID

        Returns:
            bool: 是否成功
        """
        # 默认实现：不支持
        return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        **kwargs
    ) -> SendResult:
        """
        编辑消息

        Args:
            chat_id: 聊天ID
            message_id: 消息ID
            content: 新内容
            **kwargs: 平台特定参数

        Returns:
            SendResult: 编辑结果
        """
        # 默认实现：不支持
        return SendResult(success=False, error="edit_message not supported")

    async def delete_message(
        self,
        chat_id: str,
        message_id: str
    ) -> bool:
        """
        删除消息

        Args:
            chat_id: 聊天ID
            message_id: 消息ID

        Returns:
            bool: 是否成功
        """
        # 默认实现：不支持
        return False

    async def send_image(
        self,
        chat_id: str,
        url: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> SendResult:
        """
        发送图片

        Args:
            chat_id: 聊天ID
            url: 图片URL
            caption: 图片说明
            **kwargs: 平台特定参数

        Returns:
            SendResult: 发送结果
        """
        # 默认实现：不支持
        return SendResult(success=False, error="send_image not supported")

    async def send_voice(
        self,
        chat_id: str,
        audio_data: bytes,
        **kwargs
    ) -> SendResult:
        """
        发送语音

        Args:
            chat_id: 聊天ID
            audio_data: 音频数据
            **kwargs: 平台特定参数

        Returns:
            SendResult: 发送结果
        """
        # 默认实现：不支持
        return SendResult(success=False, error="send_voice not supported")

    # ==================== 消息处理器管理 ====================

    def set_message_handler(self, handler: MessageHandler) -> None:
        """
        设置消息处理器（由 GatewayRunner 注入）

        Args:
            handler: 消息处理函数
        """
        self._message_handler = handler

    async def _handle_message(self, event: MessageEvent) -> None:
        """
        内部消息处理方法

        Args:
            event: 消息事件
        """
        _logger.info("Handling message from %s", event.source.user_id)
        if self._message_handler:
            try:
                _logger.debug("Calling message handler...")
                response = await self._message_handler(event)
                _logger.debug("Handler returned: %s", str(response)[:100] if response else "None")
                if response:
                    _logger.info("Sending response to %s", event.source.chat_id)
                    await self.send(event.source.chat_id, response)
                    _logger.debug("Response sent")
                else:
                    _logger.debug("No response to send")
            except Exception as e:
                _logger.error("Error handling message: %s", e, exc_info=True)
        else:
            _logger.warning("No message handler set!")

    # ==================== 连接状态管理 ====================

    def _mark_connected(self) -> None:
        """标记为已连接"""
        self._connected = True

    def _mark_disconnected(self) -> None:
        """标记为已断开"""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    # ==================== 工具方法 ====================

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置键（支持点号分隔的嵌套键）
            default: 默认值

        Returns:
            配置值
        """
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value
