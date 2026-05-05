import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastmcp import FastMCP

from Tools.loader import load_builtin_tools
from Tools.registry import registry

# ❗ 彻底关闭 logging（避免任何 stdout 污染）
logging.basicConfig(level=logging.CRITICAL)

load_builtin_tools()
mcp = FastMCP("shell-tools")


@mcp.tool()
def run_cmd(cmd: str) -> str:
    """Run shell command"""
    return registry.dispatch_sync("run_cmd", {"cmd": cmd})


@mcp.tool()
def getKnowledge(query: str) -> str:
    """Get knowledge from local files (stub implementation)"""
    return registry.dispatch_sync("getKnowledge", {"query": query})


@mcp.tool()
def Grep(
    pattern: str,
    path: str = ".",
    case_sensitive: bool = True,
    max_results: int = 100,
) -> str:
    """查询文本文件内容，返回匹配文件路径、1-based 行号和单行内容。"""
    return registry.dispatch_sync(
        "Grep",
        {
            "pattern": pattern,
            "path": path,
            "case_sensitive": case_sensitive,
            "max_results": max_results,
        },
    )


@mcp.tool()
def Read(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    actor_id: str = "agent",
) -> str:
    """读取指定文件的 1-based 闭区间行范围。"""
    arguments = {"path": path, "start_line": start_line, "actor_id": actor_id}
    if end_line is not None:
        arguments["end_line"] = end_line
    return registry.dispatch_sync("Read", arguments)


@mcp.tool()
def Write(
    path: str,
    content: str,
    actor_id: str = "agent",
) -> str:
    """创建新文件（文件必须不存在）。"""
    return registry.dispatch_sync("Write", {"path": path, "content": content, "actor_id": actor_id})


@mcp.tool()
def Edit(
    path: str,
    old_string: str,
    new_string: str,
    actor_id: str = "agent",
) -> str:
    """补丁式修改文件：用 new_string 替换 old_string（必须先 Read）。"""
    return registry.dispatch_sync(
        "Edit",
        {
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "actor_id": actor_id,
        },
    )


mcp.run(show_banner=False, log_level="CRITICAL")
