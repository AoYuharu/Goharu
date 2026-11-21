"""Smoke 测试：直接以 stdio 方式拉起 browser-use MCP server，验证握手与 tools/list。"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio

BROWSER_USE_PYTHON = r"D:\MyProject\Programming\AgentDevelop\browser-use\.venv\Scripts\python.exe"


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=BROWSER_USE_PYTHON,
        args=["-m", "browser_use.mcp"],
        env={"ANONYMIZED_TELEMETRY": "False"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            print(f"tools ({len(tools.tools)}):")
            for t in tools.tools:
                print(f"  - {t.name}")
            # 不实际调用 browser 工具（会拉起 Chrome），smoke 到 list_tools 为止


if __name__ == "__main__":
    asyncio.run(main())
