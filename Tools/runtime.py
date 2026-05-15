import asyncio
import json
import logging
from dataclasses import dataclass

from Agent.BackgroundTaskManager import BackgroundTaskManager
from configurationLoader import config
from Tools.loader import load_builtin_tools
from Tools.registry import registry

logger = logging.getLogger(__name__)


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
        args = arguments or {}
        background_timeout = config.get("tools.background_timeout", 120)
        if not isinstance(background_timeout, (int, float)) or background_timeout <= 0:
            background_timeout = None

        try:
            if background_timeout:
                result = await asyncio.wait_for(
                    registry.dispatch(name, args), timeout=background_timeout
                )
            else:
                result = await registry.dispatch(name, args)
            return ToolResult(content=result)
        except asyncio.TimeoutError:
            # Move to background instead of failing
            task_mgr = BackgroundTaskManager()
            desc = f"{name}({json.dumps(args, ensure_ascii=False)[:200]})"

            async def _bg_work():
                return await registry.dispatch(name, args)

            task_id = task_mgr.submit(name, _bg_work(), desc)
            logger.info(
                "Tool '%s' exceeded background_timeout (%ss), moved to background (task #%d)",
                name, background_timeout, task_id,
            )
            return ToolResult(
                content=json.dumps(
                    {
                        "backgrounded": True,
                        "task_id": task_id,
                        "tool": name,
                        "message": f"Task moved to background (task #{task_id})",
                    },
                    ensure_ascii=False,
                )
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
