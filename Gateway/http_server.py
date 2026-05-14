"""
HTTP API Server for ACP (Agent Communication Protocol)
提供 HTTP 接口让外部 Agent 与 TableHelper 通信
"""

import asyncio
import json
from typing import Optional
from aiohttp import web

from .platforms.acp_adapter import ACPAdapter
from .message_router import MessageRouter


class ACPHttpServer:
    """ACP HTTP API 服务器"""

    def __init__(self, message_router: MessageRouter, host: str = "127.0.0.1", port: int = 8765):
        """
        初始化 HTTP 服务器

        Args:
            message_router: 消息路由器
            host: 监听地址
            port: 监听端口
        """
        self.message_router = message_router
        self.host = host
        self.port = port
        self.acp_adapter = ACPAdapter()
        self.app = web.Application()
        self.runner = None
        self.site = None

        # 注册路由
        self.app.router.add_post("/api/message", self.handle_message)
        self.app.router.add_get("/api/health", self.handle_health)
        self.app.router.add_get("/api/status", self.handle_status)

        print(f"[ACPHttpServer] Initialized on {host}:{port}")

    async def start(self):
        """启动 HTTP 服务器"""
        await self.acp_adapter.connect()

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        print(f"[ACPHttpServer] Started on http://{self.host}:{self.port}")
        print(f"[ACPHttpServer] API Endpoints:")
        print(f"  POST /api/message - Send message to agent")
        print(f"  GET  /api/health  - Health check")
        print(f"  GET  /api/status  - Server status")

    async def stop(self):
        """停止 HTTP 服务器"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        await self.acp_adapter.disconnect()
        print("[ACPHttpServer] Stopped")

    async def handle_message(self, request: web.Request) -> web.Response:
        """
        处理消息请求

        POST /api/message
        Body: {
            "user_id": "claude_code",
            "user_name": "Claude Code",
            "text": "你好",
            "chat_id": "optional",
            "chat_type": "private"
        }

        Returns:
            {
                "success": true,
                "response": "agent response text",
                "session_id": "session_xxx"
            }
        """
        try:
            # 解析请求
            data = await request.json()
            user_id = data.get("user_id", "unknown")
            user_name = data.get("user_name", "Unknown User")
            text = data.get("text", "")
            chat_id = data.get("chat_id")
            chat_type = data.get("chat_type", "private")

            if not text:
                return web.json_response(
                    {"success": False, "error": "text is required"},
                    status=400
                )

            print(f"[ACPHttpServer] Received message from {user_name} ({user_id}): {text}")

            # 创建消息事件
            event = self.acp_adapter.create_message_event(
                user_id=user_id,
                user_name=user_name,
                text=text,
                chat_id=chat_id,
                chat_type=chat_type,
            )

            # 路由消息到 Agent
            response = await self.message_router.route_message(event)

            print(f"[ACPHttpServer] Agent response: {response[:100] if response else 'None'}...")

            # 返回响应
            return web.json_response({
                "success": True,
                "response": response or "No response",
                "user_id": user_id,
            })

        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            print(f"[ACPHttpServer] Error handling message: {e}")
            import traceback
            traceback.print_exc()
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )

    async def handle_health(self, request: web.Request) -> web.Response:
        """
        健康检查

        GET /api/health

        Returns:
            {"status": "ok"}
        """
        return web.json_response({"status": "ok"})

    async def handle_status(self, request: web.Request) -> web.Response:
        """
        服务器状态

        GET /api/status

        Returns:
            {
                "status": "running",
                "adapter": "acp",
                "connected": true
            }
        """
        return web.json_response({
            "status": "running",
            "adapter": "acp",
            "connected": self.acp_adapter.is_connected,
            "host": self.host,
            "port": self.port,
        })
