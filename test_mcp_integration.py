"""MCP 集成测试：真实 stdio server（FastMCP）→ manager 桥接 → registry 调用 → 热卸载。"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DEMO_SERVER = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.tool()
def greet(name: str) -> str:
    """Greet someone."""
    return f"hello {name}"

mcp.run()
'''

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


async def main():
    from Tools.mcp.manager import MCPClientManager
    from Tools.registry import registry

    # 独立实例，避免污染全局单例状态
    manager = MCPClientManager.__new__(MCPClientManager)
    manager._servers = {}
    manager._runtime = None
    manager._lock = asyncio.Lock()
    manager._initialized = True

    server_script = Path(tempfile.mkdtemp()) / "demo_server.py"
    server_script.write_text(DEMO_SERVER, encoding="utf-8")

    # 1. 连接 stdio server
    tools = await manager.connect(
        "demo",
        {"transport": "stdio", "command": sys.executable, "args": [str(server_script)]},
        persist=False,
    )
    check("桥接两个工具", sorted(tools) == ["mcp__demo__add", "mcp__demo__greet"], tools)

    entry = registry.get_entry("mcp__demo__add")
    check("registry 已注册", entry is not None)
    check("group=mcp", entry.group == "mcp")
    check("schema 透传", entry.arguments_schema.get("properties", {}).get("a", {}).get("type") == "integer",
          entry.arguments_schema)

    # 2. 通过 registry 正式 dispatch（含 schema 校验）
    raw = await registry.dispatch("mcp__demo__add", {"a": 2, "b": 3})
    check("调用 add(2,3)=5", "5" in raw, raw)

    raw = await registry.dispatch("mcp__demo__greet", {"name": "goharu"})
    check("调用 greet", "hello goharu" in raw, raw)

    # 3. 缺必填参数 → registry 层校验拦截
    raw = await registry.dispatch("mcp__demo__add", {"a": 1})
    check("缺参被拦截", "Missing required argument" in raw, raw)

    # 4. server 状态
    servers = manager.list_servers()
    check("list_servers 状态", servers["demo"]["status"] == "connected", servers)

    # 5. 热卸载：工具从 registry 消失
    ok = await manager.disconnect("demo", persist=False)
    check("disconnect 返回 True", ok)
    check("工具已卸载", registry.get_entry("mcp__demo__add") is None)

    raw = await registry.dispatch("mcp__demo__add", {"a": 1, "b": 1})
    check("卸载后调用报 Unknown tool", "Unknown tool" in raw, raw)

    # 6. 连接不存在的命令 → failed 状态、异常上抛
    try:
        await manager.connect("bad", {"transport": "stdio", "command": "definitely_not_exist_cmd_xyz"}, persist=False)
        check("坏 server 抛异常", False)
    except Exception:
        check("坏 server 抛异常", True)
    check("坏 server 状态 failed", manager.list_servers()["bad"]["status"] == "failed")

    await manager.close()


asyncio.run(main())
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
