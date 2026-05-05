"""
Standalone QQ Bot Message Echo Test
Tests message receiving and sending without Agent dependencies
"""

import asyncio
import aiohttp
import httpx
from datetime import datetime
import json
import re

# QQ Bot configuration
API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
MSG_TYPE_TEXT = 0

INTENT_DIRECT_MESSAGE = 1 << 12
INTENT_GROUP_AT_MESSAGE = 1 << 25
INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30


class Platform:
    QQBOT = "qqbot"


class MessageType:
    TEXT = "text"
    MIXED = "mixed"


class SessionSource:
    def __init__(self, platform, chat_id, chat_type, user_id, user_name, message_id=None, chat_name=None):
        self.platform = platform
        self.chat_id = chat_id
        self.chat_type = chat_type
        self.user_id = user_id
        self.user_name = user_name
        self.message_id = message_id
        self.chat_name = chat_name


class MessageEvent:
    def __init__(self, source, text, message_type, images, documents, timestamp, raw_data):
        self.source = source
        self.text = text
        self.message_type = message_type
        self.images = images
        self.documents = documents
        self.timestamp = timestamp
        self.raw_data = raw_data


class SendResult:
    def __init__(self, success, message_id=None, error=None):
        self.success = success
        self.message_id = message_id
        self.error = error


class SimpleQQAdapter:
    def __init__(self, app_id, client_secret):
        self.app_id = app_id
        self.client_secret = client_secret
        self.access_token = None
        self.ws = None
        self.session = None
        self.http_client = None
        self.last_seq = None
        self.message_handler = None
        self.heartbeat_task = None
        self.receive_task = None

    def set_message_handler(self, handler):
        self.message_handler = handler

    async def connect(self):
        try:
            print("[QQ] Connecting...")

            # Get access token
            async with httpx.AsyncClient() as client:
                response = await client.post(TOKEN_URL, json={
                    "appId": self.app_id,
                    "clientSecret": self.client_secret
                })
                if response.status_code != 200:
                    print(f"[ERROR] Token fetch failed: {response.text}")
                    return False
                data = response.json()
                self.access_token = data["access_token"]
                print(f"[SUCCESS] Token obtained")

            # Create HTTP client
            self.http_client = httpx.AsyncClient(timeout=30.0)

            # Get gateway URL
            response = await self.http_client.get(
                f"{API_BASE}/gateway",
                headers={"Authorization": f"QQBot {self.access_token}"}
            )
            gateway_url = response.json().get("url", "wss://api.sgroup.qq.com/websocket")
            print(f"[QQ] Gateway: {gateway_url}")

            # Connect WebSocket
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(gateway_url)

            # Receive Hello
            hello = await self.ws.receive_json()
            if hello.get("op") != 10:
                print(f"[ERROR] Expected Hello, got op={hello.get('op')}")
                return False

            heartbeat_interval = hello["d"]["heartbeat_interval"]
            print(f"[QQ] Hello received, heartbeat: {heartbeat_interval}ms")

            # Send Identify
            intents = INTENT_GROUP_AT_MESSAGE | INTENT_PUBLIC_GUILD_MESSAGES | INTENT_DIRECT_MESSAGE
            await self.ws.send_json({
                "op": 2,
                "d": {
                    "token": f"QQBot {self.access_token}",
                    "intents": intents,
                    "shard": [0, 1],
                    "properties": {
                        "$os": "Linux",
                        "$browser": "TableHelper",
                        "$device": "TableHelper"
                    }
                }
            })
            print(f"[QQ] Identify sent")

            # Start heartbeat
            self.heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(heartbeat_interval / 1000.0)
            )

            # Start receive loop
            self.receive_task = asyncio.create_task(self._receive_loop())

            print(f"[SUCCESS] Connected!")
            return True

        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def disconnect(self):
        print("[QQ] Disconnecting...")
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.receive_task:
            self.receive_task.cancel()
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        if self.http_client:
            await self.http_client.aclose()
        print("[QQ] Disconnected")

    async def send(self, chat_id, content, is_group=False):
        try:
            endpoint = f"/v2/groups/{chat_id}/messages" if is_group else f"/v2/users/{chat_id}/messages"
            url = f"{API_BASE}{endpoint}"

            response = await self.http_client.post(
                url,
                json={"content": content, "msg_type": MSG_TYPE_TEXT},
                headers={
                    "Authorization": f"QQBot {self.access_token}",
                    "Content-Type": "application/json"
                }
            )

            if response.status_code != 200:
                return SendResult(False, error=f"{response.status_code} {response.text}")

            data = response.json()
            return SendResult(True, message_id=data.get("id"))

        except Exception as e:
            return SendResult(False, error=str(e))

    async def _heartbeat_loop(self, interval):
        try:
            while True:
                await asyncio.sleep(interval)
                if self.ws:
                    await self.ws.send_json({"op": 1, "d": self.last_seq})
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self):
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(msg.data)
                    await self._dispatch(payload)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[ERROR] Receive loop: {e}")

    async def _dispatch(self, payload):
        op = payload.get("op")

        if op == 0:  # Event
            event_type = payload.get("t")
            data = payload.get("d", {})

            if "s" in payload:
                self.last_seq = payload["s"]

            if event_type == "READY":
                user = data.get("user", {})
                print(f"[QQ] Bot ready: {user.get('username')}")

            elif event_type == "C2C_MESSAGE_CREATE":
                await self._handle_dm(data)

            elif event_type == "GROUP_AT_MESSAGE_CREATE":
                await self._handle_group(data)

        elif op == 9:
            print(f"[ERROR] Invalid Session: {payload}")
            if self.ws:
                await self.ws.close()

        elif op == 11:
            pass  # Heartbeat ACK

    async def _handle_dm(self, data):
        try:
            author = data.get("author", {})
            user_id = author.get("id")
            user_name = author.get("username")
            content = data.get("content", "").strip()
            message_id = data.get("id")

            if not content or not self.message_handler:
                return

            source = SessionSource(
                platform=Platform.QQBOT,
                chat_id=user_id,
                chat_type="dm",
                user_id=user_id,
                user_name=user_name,
                message_id=message_id
            )

            event = MessageEvent(
                source=source,
                text=content,
                message_type=MessageType.TEXT,
                images=[],
                documents=[],
                timestamp=datetime.now(),
                raw_data=data
            )

            await self.message_handler(event)

        except Exception as e:
            print(f"[ERROR] Handle DM: {e}")

    async def _handle_group(self, data):
        try:
            author = data.get("author", {})
            user_id = author.get("id")
            user_name = author.get("username")
            group_id = data.get("group_openid")
            content = data.get("content", "").strip()
            message_id = data.get("id")

            # Remove bot mention
            content = re.sub(r'<@!\w+>', '', content).strip()

            if not content or not self.message_handler:
                return

            source = SessionSource(
                platform=Platform.QQBOT,
                chat_id=group_id,
                chat_type="group",
                user_id=user_id,
                user_name=user_name,
                message_id=message_id
            )

            event = MessageEvent(
                source=source,
                text=content,
                message_type=MessageType.TEXT,
                images=[],
                documents=[],
                timestamp=datetime.now(),
                raw_data=data
            )

            await self.message_handler(event)

        except Exception as e:
            print(f"[ERROR] Handle group: {e}")


async def main():
    print("=" * 60)
    print("QQ Bot Message Echo Test")
    print("=" * 60)
    print()

    adapter = SimpleQQAdapter(
        app_id="102839705",
        client_secret="wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"
    )

    async def echo_handler(event: MessageEvent):
        print(f"\n[Received] From: {event.source.user_name} ({event.source.user_id})")
        print(f"[Received] Chat: {event.source.chat_id} ({event.source.chat_type})")
        print(f"[Received] Text: {event.text}")

        reply = f"Echo: {event.text}"
        is_group = event.source.chat_type == "group"

        print(f"[Sending] Reply to {event.source.chat_id}")
        result = await adapter.send(event.source.chat_id, reply, is_group)

        if result.success:
            print(f"[SUCCESS] Sent! ID: {result.message_id}")
        else:
            print(f"[FAIL] Error: {result.error}")

    adapter.set_message_handler(echo_handler)

    try:
        success = await adapter.connect()

        if success:
            print()
            print("=" * 60)
            print("Bot is listening for messages!")
            print("Send a message to the bot to test echo functionality")
            print("Press Ctrl+C to stop")
            print("=" * 60)
            print()

            while True:
                await asyncio.sleep(1)
        else:
            print("[FAIL] Connection failed")

    except KeyboardInterrupt:
        print("\n[Test] Interrupted")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        await adapter.disconnect()

    print("\nTest complete")


if __name__ == "__main__":
    asyncio.run(main())
