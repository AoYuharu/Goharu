"""
Gateway 模块 - 多平台消息路由与会话管理

参考 hermes-agent 的 Gateway 架构设计

架构组件：
- GatewayRunner: 总协调器，负责初始化和协调各组件
- AdapterManager: 管理平台适配器的生命周期
- MessageRouter: 消息路由和命令处理
- AgentBridge: 连接 Gateway 和 Agent 系统
- SessionStore: 会话存储和管理
- BasePlatformAdapter: 平台适配器基类
"""

from .gateway_runner import GatewayRunner
from .session import SessionSource, SessionStore, SessionContext
from .platforms.base import BasePlatformAdapter, MessageEvent, MessageType, Platform
from .adapter_manager import AdapterManager
from .message_router import MessageRouter
from .agent_bridge import AgentBridge

__all__ = [
    "GatewayRunner",
    "SessionSource",
    "SessionStore",
    "SessionContext",
    "BasePlatformAdapter",
    "MessageEvent",
    "MessageType",
    "Platform",
    "AdapterManager",
    "MessageRouter",
    "AgentBridge",
]
