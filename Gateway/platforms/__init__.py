"""
平台适配器模块
"""

from .base import BasePlatformAdapter, MessageEvent, MessageType, Platform, SendResult

__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "MessageType",
    "Platform",
    "SendResult",
]
