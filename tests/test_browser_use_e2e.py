"""E2E 测试：config.yaml 配置的 browser-use MCP server → manager.start() → registry 真实导航。

会打开一个可见的 Chrome 窗口（browser-use 默认 headless=False）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {str(detail)[:300]}")


async def main():
    from Tools.mcp.manager import mcp_manager
    from Tools.registry import registry

    # 1. 启动时连接（读 config.yaml tools.mcp.servers）
    await mcp_manager.start()
    servers = mcp_manager.list_servers()
    check("browser-use 已连接", servers.get("browser-use", {}).get("status") == "connected", servers)
    if servers.get("browser-use", {}).get("status") != "connected":
        print("  error:", servers.get("browser-use", {}).get("error"))
        return

    tools = servers["browser-use"]["tools"]
    check("工具数 = 16", len(tools) == 16, tools)
    check("navigate 已注册", "mcp__browser_use__browser_navigate" in tools)

    # 2. 真实导航（首次会拉起 Chrome）
    raw = await registry.dispatch(
        "mcp__browser_use__browser_navigate", {"url": "https://example.com"}
    )
    check("导航成功", "example.com" in raw.lower(), raw)

    # 3. 取页面状态
    raw = await registry.dispatch("mcp__browser_use__browser_get_state", {})
    check("get_state 返回 URL", "example.com" in raw.lower(), raw)
    check("get_state 返回元素", "interactive_elements" in raw, raw)
    print("  --- get_state 前 500 字 ---")
    print(" ", raw[:500].replace("\n", "\n  "))

    # 4. 点击页面上的链接（example.com 唯一链接 → iana.org），验证索引点击链路
    raw = await registry.dispatch("mcp__browser_use__browser_click", {"index": 5})
    print("  click result:", raw[:200])

    await mcp_manager.close()


asyncio.run(main())
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
