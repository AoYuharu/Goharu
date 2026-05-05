import asyncio
import os
from Gateway.platforms.qq_adapter import QQAdapter
from Gateway.platforms.base import MessageEvent

# 简单的 echo 处理器
async def echo_handler(event: MessageEvent):
    print(f"[Echo] Received: {event.text}")
    return f"Echo: {event.text}"

async def main():
    config = {
        "app_id": "102839705",
        "client_secret": "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5",
        "sandbox": False,
        "markdown_support": False
    }
    
    adapter = QQAdapter(config)
    adapter.set_message_handler(echo_handler)
    
    success = await adapter.connect()
    if success:
        print("[Test] QQ Adapter connected, waiting for messages...")
        # 保持运行
        while True:
            await asyncio.sleep(1)
    else:
        print("[Test] Failed to connect")

if __name__ == "__main__":
    asyncio.run(main())
