import asyncio
import json
from dataclasses import dataclass

from configurationLoader import config
from Tools.loader import load_builtin_tools
from Tools.registry import registry


@dataclass
class ToolResult:
    content: object


def _normalize_tool_definition(tool):
    if isinstance(tool, dict):
        schema = (
            tool.get("inputSchema")
            or tool.get("arguments_schema")
            or tool.get("schema")
            or {}
        )
        return {
            "name": str(tool.get("name") or "").strip(),
            "description": str(tool.get("description") or "").strip(),
            "inputSchema": dict(schema) if isinstance(schema, dict) else {},
        }

    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "arguments_schema", None)
    return {
        "name": str(getattr(tool, "name", "") or "").strip(),
        "description": str(getattr(tool, "description", "") or "").strip(),
        "inputSchema": dict(schema) if isinstance(schema, dict) else {},
    }


class InProcessToolRuntime:
    """
    In-process tool runtime - 所有工具通过registry直接调用
    不再支持MCP，所有工具必须通过Tools.registry注册
    """
    def __init__(self, group=None, modules=None):
        self.group = group
        self.modules = modules
        self.runtime_name = "in_process"
        self.status_label = "in_process"
        self.last_tool_definitions = []
        self._initialized = False

    async def initialize(self):
        load_builtin_tools(self.modules)
        self._initialized = True
        return self

    async def list_tools(self):
        if not self._initialized:
            await self.initialize()
        self.last_tool_definitions = [
            _normalize_tool_definition(tool) for tool in registry.list_definitions(group=self.group)
        ]
        return list(self.last_tool_definitions)

    async def call_tool(self, name, arguments=None):
        if not self._initialized:
            await self.initialize()
        timeout = config.get("tools.timeout", 60)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = None
        # AgentDelegate 有自己的超时机制，不在此处覆盖
        if name == "AgentDelegate":
            timeout = None
        try:
            if timeout:
                result = await asyncio.wait_for(registry.dispatch(name, arguments or {}), timeout=timeout)
            else:
                result = await registry.dispatch(name, arguments or {})
            return ToolResult(content=result)
        except asyncio.TimeoutError:
            return ToolResult(
                content=json.dumps({"error": f"Tool '{name}' timed out after {timeout}s"}, ensure_ascii=False)
            )

    async def close(self):
        return None


def create_tool_runtime(runtime_name=None):
    """
    创建工具运行时

    注意：MCP支持已移除，所有工具必须通过Tools.registry注册
    """
    selected_runtime = str(runtime_name or config.get("tools.runtime", "in_process")).strip().lower()
    if selected_runtime == "in_process":
        return InProcessToolRuntime()
    raise ValueError(f"Unsupported tool runtime: {selected_runtime}. Only 'in_process' is supported.")
