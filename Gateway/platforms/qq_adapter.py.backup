"""
QQ 平台适配器

基于 QQ Bot 官方 API v2
参考 hermes-agent 的 QQAdapter 实现
"""

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any

import aiohttp
import httpx

from .base import (
    BasePlatformAdapter,
    Platform,
    MessageEvent,
    MessageType,
    SessionSource,
    SendResult,
)


# QQ Bot API 配置
API_BASE = "https://api.sgroup.qq.com"
SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"  # Token 获取端点
# 注意：QQ Bot API v2 使用 QQBot {token} 格式进行认证

# 消息类型
MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_MEDIA = 7

# Intent 配置（参考 hermes-agent）
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MEMBERS = 1 << 1
INTENT_DIRECT_MESSAGE = 1 << 12  # 私聊消息
INTENT_GROUP_AT_MESSAGE = 1 << 25  # 群聊@消息
INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30  # 频道消息


class QQAdapter(BasePlatformAdapter):
    """
    QQ Bot 官方 API v2 适配器

    功能：
    - WebSocket 连接接收消息
    - REST API 发送消息
    - 支持私聊和群聊
    - 支持 Markdown 格式
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, Platform.QQBOT)

        # 认证信息
        self._app_id = os.getenv("QQ_APP_ID") or self.get_config("app_id")
        self._client_secret = os.getenv("QQ_CLIENT_SECRET") or self.get_config("client_secret")

        # 支持直接使用 Bot Token (格式: appId.token)
        self._bot_token = os.getenv("QQ_BOT_TOKEN") or self.get_config("bot_token")

        # 是否使用沙箱环境
        self._use_sandbox = self.get_config("sandbox", False)
        self._api_base = SANDBOX_API_BASE if self._use_sandbox else API_BASE

        if not self._app_id:
            raise ValueError("QQ_APP_ID is required")

        # 如果没有 bot_token，则需要 client_secret 来获取 access_token
        if not self._bot_token and not self._client_secret:
            raise ValueError("Either QQ_BOT_TOKEN or QQ_CLIENT_SECRET is required")

        print(f"[QQAdapter] Using {'SANDBOX' if self._use_sandbox else 'PRODUCTION'} environment")
        print(f"[QQAdapter] API Base: {self._api_base}")

        # WebSocket 连接
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

        # HTTP 客户端 (REST API)
        self._http_client: Optional[httpx.AsyncClient] = None

        # Token 缓存
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        # 心跳状态
        self._last_seq: Optional[int] = None
        self._last_heartbeat_ack: float = 0.0

    async def connect(self) -> bool:
        """连接 QQ Bot Gateway"""
        try:
            print(f"[QQAdapter] Connecting to QQ Bot...")

            # 1. 获取或构建 Token
            if self._bot_token:
                # 使用预设的 Bot Token
                self._access_token = self._bot_token
                print(f"[QQAdapter] Using Bot Token")
            else:
                # 通过 API 获取 Access Token
                await self._fetch_access_token()

            # 2. 创建 HTTP 客户端
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=100),
            )

            # 3. 获取 Gateway URL
            gateway_url = await self._fetch_gateway_url()
            print(f"[QQAdapter] Gateway URL: {gateway_url}")

            # 4. 建立 WebSocket 连接
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(gateway_url)

            # 5. 接收 Hello 消息
            hello = await self._ws.receive_json()
            if hello.get("op") != 10:
                raise Exception(f"Expected Hello (op=10), got {hello.get('op')}")

            heartbeat_interval = hello["d"]["heartbeat_interval"]
            print(f"[QQAdapter] Received Hello, heartbeat interval: {heartbeat_interval}ms")

            # 6. 发送 Identify (鉴权)
            # QQ Bot 正确的 Token 格式: QQBot {token}（参考 hermes-agent）
            identify_token = f"QQBot {self._access_token}"
            print(f"[QQAdapter] Sending Identify with token format: QQBot {{token}}")
            print(f"[QQAdapter] Token (first 20 chars): {self._access_token[:20]}...")

            identify_payload = {
                "op": 2,  # Identify
                "d": {
                    "token": identify_token,
                    "intents": self._calculate_intents(),
                    "shard": [0, 1],
                    "properties": {
                        "$os": "Windows",
                        "$browser": "TableHelper",
                        "$device": "TableHelper",
                    },
                }
            }
            print(f"[QQAdapter] Identify payload: op=2, intents={identify_payload['d']['intents']}")
            print(f"[QQAdapter] Full identify payload: {identify_payload}")
            await self._ws.send_json(identify_payload)
            print(f"[QQAdapter] Identify sent, waiting for response...")

            # 7. 启动心跳任务
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(heartbeat_interval / 1000.0)
            )

            # 8. 启动消息接收循环
            self._receive_task = asyncio.create_task(self._receive_loop())

            self._mark_connected()
            print(f"[QQAdapter] Connected successfully")
            return True

        except Exception as e:
            print(f"[QQAdapter] Connection failed: {e}")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        print(f"[QQAdapter] Disconnecting...")

        # 取消任务
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # 关闭连接
        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._mark_disconnected()
        print(f"[QQAdapter] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        **kwargs
    ) -> SendResult:
        """
        发送消息到 QQ

        Args:
            chat_id: 聊天ID (用户ID或群ID)
            content: 消息内容
            **kwargs:
                - is_group: 是否群聊
                - markdown: 是否使用 Markdown 格式

        Returns:
            SendResult: 发送结果
        """
        try:
            # 判断消息类型
            is_group = kwargs.get("is_group", False)
            use_markdown = kwargs.get("markdown", self.get_config("markdown_support", False))

            # 构建 endpoint
            if is_group:
                endpoint = f"/v2/groups/{chat_id}/messages"
            else:
                endpoint = f"/v2/users/{chat_id}/messages"

            # 构建消息体
            payload = {
                "content": content,
                "msg_type": MSG_TYPE_MARKDOWN if use_markdown else MSG_TYPE_TEXT,
            }

            # 发送 HTTP 请求
            headers = {
                "Authorization": f"QQBot {self._access_token}",  # 使用 QQBot 格式
                "Content-Type": "application/json",
            }

            url = f"{self._api_base}{endpoint}"
            response = await self._http_client.post(url, json=payload, headers=headers)

            if response.status_code != 200:
                error_msg = f"QQ send failed: {response.status_code} {response.text}"
                print(f"[QQAdapter] {error_msg}")
                return SendResult(success=False, error=error_msg)

            data = response.json()
            message_id = data.get("id")

            return SendResult(success=True, message_id=message_id)

        except Exception as e:
            error_msg = f"Send error: {e}"
            print(f"[QQAdapter] {error_msg}")
            return SendResult(success=False, error=error_msg)

    async def send_typing(self, chat_id: str) -> bool:
        """发送"正在输入"状态（QQ 不支持）"""
        return False

    # ==================== 内部方法 ====================

    def _calculate_intents(self) -> int:
        """计算 Intent 值（参考 hermes-agent）"""
        # (1 << 25) | (1 << 30) | (1 << 12)
        # = GROUP_AT_MESSAGE | PUBLIC_GUILD_MESSAGES | DIRECT_MESSAGE
        intents = INTENT_GROUP_AT_MESSAGE | INTENT_PUBLIC_GUILD_MESSAGES | INTENT_DIRECT_MESSAGE
        return intents

    async def _fetch_access_token(self) -> None:
        """获取 Access Token"""
        # 检查缓存
        if self._access_token and time.time() < self._token_expires_at:
            return

        # 使用正确的 Token URL
        url = TOKEN_URL
        payload = {
            "appId": self._app_id,
            "clientSecret": self._client_secret,
        }

        print(f"[QQAdapter] Fetching access token from: {url}")
        print(f"[QQAdapter] Payload: appId={self._app_id}, clientSecret=***")

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            print(f"[QQAdapter] Response status: {response.status_code}")
            print(f"[QQAdapter] Response body: {response.text}")

            if response.status_code != 200:
                raise Exception(f"Failed to get access token: {response.status_code} - {response.text}")

            data = response.json()
            self._access_token = data["access_token"]
            expires_in = int(data.get("expires_in", 7200))  # 转换为整数
            self._token_expires_at = time.time() + expires_in - 60  # 提前1分钟刷新

            print(f"[QQAdapter] Access token obtained, expires in {expires_in}s")

    async def _fetch_gateway_url(self) -> str:
        """获取 Gateway URL"""
        url = f"{self._api_base}/gateway"
        headers = {
            "Authorization": f"QQBot {self._access_token}",  # 使用 QQBot 格式
        }

        print(f"[QQAdapter] Fetching gateway URL from: {url}")

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            print(f"[QQAdapter] Response status: {response.status_code}")

            if response.status_code != 200:
                print(f"[QQAdapter] Failed to get gateway URL, using default")
                # 根据环境返回不同的默认 gateway
                if self._use_sandbox:
                    return "wss://sandbox.api.sgroup.qq.com/websocket"
                else:
                    return "wss://api.sgroup.qq.com/websocket"

            data = response.json()
            gateway_url = data.get("url", "wss://api.sgroup.qq.com/websocket")
            return gateway_url

            if response.status_code != 200:
                print(f"[QQAdapter] Failed to get gateway URL, using default")
                return "wss://api.sgroup.qq.com/websocket"

            data = response.json()
            gateway_url = data.get("url", "wss://api.sgroup.qq.com/websocket")
            return gateway_url

    async def _heartbeat_loop(self, interval: float) -> None:
        """心跳循环"""
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws:
                    await self._ws.send_json({
                        "op": 1,  # Heartbeat
                        "d": self._last_seq,
                    })
                    # print(f"[QQAdapter] Heartbeat sent, seq={self._last_seq}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[QQAdapter] Heartbeat error: {e}")

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        try:
            print(f"[QQAdapter] Receive loop started")
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(msg.data)
                    await self._dispatch_payload(payload)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print(f"[QQAdapter] WebSocket closed by server")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"[QQAdapter] WebSocket error")
                    break
                else:
                    print(f"[QQAdapter] Unknown message type: {msg.type}")
        except asyncio.CancelledError:
            print(f"[QQAdapter] Receive loop cancelled")
            pass
        except Exception as e:
            print(f"[QQAdapter] Receive loop error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print(f"[QQAdapter] Receive loop ended, disconnecting...")
            await self.disconnect()

    async def _dispatch_payload(self, payload: Dict[str, Any]) -> None:
        """分发 WebSocket 消息"""
        op = payload.get("op")
        print(f"[QQAdapter] Received payload: op={op}")

        if op == 0:  # Dispatch (事件)
            event_type = payload.get("t")
            data = payload.get("d", {})
            print(f"[QQAdapter] Event: {event_type}")

            # 更新序列号
            if "s" in payload:
                self._last_seq = payload["s"]

            # 处理不同事件类型
            if event_type == "C2C_MESSAGE_CREATE":
                # 私聊消息
                await self._handle_dm_message(data)

            elif event_type == "GROUP_AT_MESSAGE_CREATE":
                # 群聊@消息
                await self._handle_group_message(data)

            elif event_type == "READY":
                # 连接就绪
                user_info = data.get("user", {})
                print(f"[QQAdapter] Bot ready: {user_info.get('username')}")

        elif op == 9:  # Invalid Session
            print(f"[QQAdapter] Invalid Session! Authentication failed.")
            print(f"[QQAdapter] Payload: {payload}")
            # 关闭连接
            if self._ws:
                await self._ws.close()

        elif op == 11:  # Heartbeat ACK
            self._last_heartbeat_ack = time.time()
            # print(f"[QQAdapter] Heartbeat ACK received")

    async def _handle_dm_message(self, data: Dict[str, Any]) -> None:
        """处理私聊消息"""
        try:
            # 提取消息信息
            author = data.get("author", {})
            user_id = author.get("id")
            user_name = author.get("username")
            content = data.get("content", "").strip()
            message_id = data.get("id")

            if not content:
                return

            # 构建 SessionSource
            source = SessionSource(
                platform=Platform.QQBOT,
                chat_id=user_id,  # 私聊用 user_id 作为 chat_id
                chat_type="dm",
                user_id=user_id,
                user_name=user_name,
                message_id=message_id,
            )

            # 处理附件 (图片)
            images = []
            for attachment in data.get("attachments", []):
                if attachment.get("content_type", "").startswith("image/"):
                    images.append(attachment["url"])

            # 创建 MessageEvent
            event = MessageEvent(
                source=source,
                text=content,
                message_type=MessageType.TEXT if not images else MessageType.MIXED,
                images=images,
                documents=[],
                timestamp=datetime.now(),
                raw_data=data,
            )

            # 调用消息处理器
            await self._handle_message(event)

        except Exception as e:
            print(f"[QQAdapter] Error handling DM message: {e}")

    async def _handle_group_message(self, data: Dict[str, Any]) -> None:
        """处理群聊@消息"""
        try:
            # 提取消息信息
            author = data.get("author", {})
            user_id = author.get("id")
            user_name = author.get("username")
            group_id = data.get("group_openid")
            content = data.get("content", "").strip()
            message_id = data.get("id")

            if not content:
                return

            # 移除 @机器人 的部分
            # QQ 的 @ 格式: <@!bot_id>
            content = self._remove_bot_mention(content)

            # 构建 SessionSource
            source = SessionSource(
                platform=Platform.QQBOT,
                chat_id=group_id,
                chat_type="group",
                user_id=user_id,
                user_name=user_name,
                chat_name=data.get("group_name"),
                message_id=message_id,
            )

            # 处理附件
            images = []
            for attachment in data.get("attachments", []):
                if attachment.get("content_type", "").startswith("image/"):
                    images.append(attachment["url"])

            # 创建 MessageEvent
            event = MessageEvent(
                source=source,
                text=content,
                message_type=MessageType.TEXT if not images else MessageType.MIXED,
                images=images,
                documents=[],
                timestamp=datetime.now(),
                raw_data=data,
            )

            # 调用消息处理器
            await self._handle_message(event)

        except Exception as e:
            print(f"[QQAdapter] Error handling group message: {e}")

    def _remove_bot_mention(self, content: str) -> str:
        """移除消息中的 @机器人 部分"""
        import re
        # 移除 <@!bot_id> 格式的 mention
        content = re.sub(r'<@!\w+>', '', content)
        return content.strip()
