from contextlib import AsyncExitStack
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


def _normalize_tool_result_content(result):
    content = getattr(result, "content", result)
    if not isinstance(content, list):
        return content

    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if text is None:
                text = item.get("content")
            parts.append(str(text if text is not None else item))
            continue
        text = getattr(item, "text", None)
        parts.append(str(text if text is not None else item))

    merged = "\n".join(part for part in parts if str(part).strip())
    return merged if merged else "(no output)"


class InProcessToolRuntime:
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
        """
        调用工具（沙箱化）

        工具调用失败不会抛出异常，而是返回错误信息
        """
        if not self._initialized:
            await self.initialize()

        try:
            result = await registry.dispatch(name, arguments or {})
            return ToolResult(content=result)
        except Exception as e:
            # 沙箱化：捕获所有异常，返回错误信息而不是崩溃
            error_msg = f"工具执行异常: {type(e).__name__}: {str(e)}"
            return ToolResult(content={"error": error_msg, "tool": name, "arguments": arguments})

    async def close(self):
        return None


class MCPToolRuntime:
    def __init__(self, command=None, args=None):
        self.command = command or config.get("mcp.executor")
        self.args = list(args if args is not None else (config.get("mcp.args") or []))
        self.runtime_name = "mcp"
        self.status_label = "mcp"
        self.last_tool_definitions = []
        self._initialized = False
        self._exit_stack = None
        self.session = None

    async def initialize(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        self._exit_stack = AsyncExitStack()
        server_parameters = StdioServerParameters(
            command=self.command,
            args=self.args,
        )
        read, write = await self._exit_stack.enter_async_context(stdio_client(server_parameters))
        self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self._initialized = True
        return self

    async def list_tools(self):
        if not self._initialized:
            await self.initialize()
        tools_result = await self.session.list_tools()
        tools = getattr(tools_result, "tools", tools_result)
        self.last_tool_definitions = [_normalize_tool_definition(tool) for tool in (tools or [])]
        return list(self.last_tool_definitions)

    async def call_tool(self, name, arguments=None):
        """
        调用MCP工具（沙箱化）

        工具调用失败不会抛出异常，而是返回错误信息
        """
        if not self._initialized:
            await self.initialize()

        try:
            result = await self.session.call_tool(name, arguments or {})
            return ToolResult(content=_normalize_tool_result_content(result))
        except Exception as e:
            # 沙箱化：捕获所有异常，返回错误信息而不是崩溃
            error_msg = f"MCP工具执行异常: {type(e).__name__}: {str(e)}"
            return ToolResult(content={"error": error_msg, "tool": name, "arguments": arguments})

    async def close(self):
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self.session = None
        self._initialized = False


def create_tool_runtime(runtime_name=None):
    selected_runtime = str(runtime_name or config.get("tools.runtime", "in_process")).strip().lower()
    if selected_runtime == "in_process":
        return InProcessToolRuntime()
    if selected_runtime == "mcp":
        return MCPToolRuntime()
    raise ValueError(f"Unsupported tool runtime: {selected_runtime}")
