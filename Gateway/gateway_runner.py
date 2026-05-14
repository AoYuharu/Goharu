"""
Gateway 运行器 - 协调各组件，提供统一的启动入口

职责：
- 初始化各个组件（SessionStore, AdapterManager, Router, AgentBridge）
- 协调组件之间的交互
- 提供启动和停止接口
"""

import asyncio
from typing import Optional, Dict

from Agent.ActorAgent import ActorAgent
from Agent.ReflectionAgent import ReflectionAgent
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
from configurationLoader import config

from .session import SessionStore
from .adapter_manager import AdapterManager
from .message_router import MessageRouter
from .agent_bridge import AgentBridge
from .http_server import ACPHttpServer


class GatewayRunner:
    """
    Gateway 运行器

    职责：
    - 协调 SessionStore、AdapterManager、MessageRouter、AgentBridge
    - 提供统一的启动和停止接口
    """

    def __init__(self, gateway_config: Optional[Dict] = None):
        """
        初始化 Gateway

        Args:
            gateway_config: Gateway 配置（如果为 None，从 config.yaml 读取）
        """
        if gateway_config is None:
            gateway_config = config.get("gateway", {})

        self.config = gateway_config
        self._running = False

        # 组件（延迟初始化）
        self.session_store: Optional[SessionStore] = None
        self.adapter_manager: Optional[AdapterManager] = None
        self.message_router: Optional[MessageRouter] = None
        self.agent_bridge: Optional[AgentBridge] = None
        self.http_server: Optional[ACPHttpServer] = None

        # Agent 组件
        self.memory_manager: Optional[MemoryManager] = None
        self.actor: Optional[ActorAgent] = None
        self.reflector: Optional[ReflectionAgent] = None
        self.runtime = None

    async def start(self) -> None:
        """启动 Gateway"""
        print("[Gateway] Starting...")

        # 1. 初始化 Session Store
        session_config = self.config.get("session", {})
        self.session_store = SessionStore(
            storage_path=session_config.get("storage_path", "./runtime_memory/gateway/sessions.json"),
            group_sessions_per_user=session_config.get("group_sessions_per_user", True),
            thread_sessions_per_user=session_config.get("thread_sessions_per_user", False),
            reset_mode=session_config.get("reset_mode", "idle"),
            reset_at_hour=session_config.get("reset_at_hour", 4),
            reset_idle_minutes=session_config.get("reset_idle_minutes", 1440),
        )
        print("[Gateway] SessionStore initialized")

        # 2. 初始化 Agent 组件
        self.memory_manager = MemoryManager()
        self.runtime = create_tool_runtime(config.get("tools.runtime", "in_process"))
        await self.runtime.initialize()
        await self.runtime.list_tools()  # populate last_tool_definitions

        self.actor = ActorAgent(self.runtime, self.memory_manager)
        self.reflector = ReflectionAgent()
        print("[Gateway] Agent components initialized")

        # 3. 初始化 AgentBridge
        self.agent_bridge = AgentBridge(
            self.actor,
            self.reflector,
            self.memory_manager,
            self.session_store,
        )
        print("[Gateway] AgentBridge initialized")

        # 4. 初始化 MessageRouter
        self.message_router = MessageRouter(
            self.session_store,
            self.agent_bridge.process_message,
        )
        print("[Gateway] MessageRouter initialized")

        # 5. 初始化 AdapterManager
        platforms_config = self.config.get("platforms", {})
        self.adapter_manager = AdapterManager(platforms_config)

        # 6. 启动所有适配器
        await self.adapter_manager.start_all(self.message_router.route_message)

        # 7. 启动 HTTP API 服务器（用于 ACP）
        acp_config = self.config.get("acp", {})
        acp_enabled = acp_config.get("enabled", True)
        if acp_enabled:
            acp_host = acp_config.get("host", "127.0.0.1")
            acp_port = acp_config.get("port", 8765)
            self.http_server = ACPHttpServer(self.message_router, acp_host, acp_port)
            await self.http_server.start()
            print(f"[Gateway] ACP HTTP Server started on http://{acp_host}:{acp_port}")

        self._running = True
        connected_count = len(self.adapter_manager.adapters)
        print(f"[Gateway] Started with {connected_count} platform(s)")

    async def stop(self) -> None:
        """停止 Gateway"""
        print("[Gateway] Stopping...")
        self._running = False

        # 停止 HTTP 服务器
        if self.http_server:
            await self.http_server.stop()

        # 停止所有适配器
        if self.adapter_manager:
            await self.adapter_manager.stop_all()

        # 关闭 runtime
        if self.runtime:
            await self.runtime.close()

        print("[Gateway] Stopped")

    async def run_forever(self) -> None:
        """持续运行 Gateway"""
        try:
            while self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n[Gateway] Received interrupt signal")
        finally:
            await self.stop()


async def main():
    """Gateway 主函数"""
    gateway = GatewayRunner()

    try:
        await gateway.start()
        await gateway.run_forever()
    except Exception as e:
        print(f"[Gateway] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
