"""
Gateway 模块 - 会话管理

架构组件：
- SessionStore: 会话存储和管理
- BasePlatformAdapter: 平台适配器基类
"""

from .session import SessionSource, SessionStore, SessionContext
from .platforms.base import BasePlatformAdapter, MessageEvent, MessageType, Platform

__all__ = [
    "SessionSource",
    "SessionStore",
    "SessionContext",
    "BasePlatformAdapter",
    "MessageEvent",
    "MessageType",
    "Platform",
]
