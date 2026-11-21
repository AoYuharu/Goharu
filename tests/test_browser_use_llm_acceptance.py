"""最终验收：真实 LLM 通过 GatewaySession 自主使用 browser-use 工具并给出回复。

会打开可见 Chrome。运行时间可能较长（LLM 多步 ReAct）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TASK = "用浏览器打开 https://example.com ，看看页面上有什么内容和可交互元素，然后告诉我这个页面是干什么的。"


async def main():
    from Gateway.gateway_entry import GatewaySession

    session = GatewaySession()
    await session.initialize()
    try:
        reply = await session.process_message(TASK)
        print("\n===== 最终回复 =====")
        print(reply)
        ok = bool(reply and reply.strip())
        print(f"\n===== 验收{'通过' if ok else '失败'}：{'得到模型回复' if ok else '回复为空'} =====")
        sys.exit(0 if ok else 1)
    finally:
        await session.shutdown()


asyncio.run(main())
