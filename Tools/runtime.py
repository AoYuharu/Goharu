import asyncio
import json
import logging
from dataclasses import dataclass

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

        # 统一后台化入口：任何工具都可以传 run_background=True
        # runtime 层剥离该参数并立即 track_task，工具 handler 不感知
        run_background = args.pop("run_background", False)

        from Agent.BackgroundTaskManager import BackgroundTaskManager  # 懒加载，避免循环依赖
        task_mgr = BackgroundTaskManager()
        desc = f"{name}({json.dumps(args, ensure_ascii=False)[:200]})"

        dispatch_task = asyncio.create_task(registry.dispatch(name, args))

        if run_background:
            task_id = task_mgr.track_task(name, dispatch_task, desc)
            logger.info(
                "Tool '%s' launched in background (task #%d)",
                name, task_id,
            )
            return ToolResult(
                content=json.dumps(
                    {
                        "backgrounded": True,
                        "task_id": task_id,
                        "tool": name,
                        "message": f"Tool launched in background (task #{task_id}). Results will be injected when ready.",
                    },
                    ensure_ascii=False,
                )
            )

        background_timeout = config.get("tools.background_timeout", 120)
        if not isinstance(background_timeout, (int, float)) or background_timeout <= 0:
            result = await dispatch_task
            return ToolResult(content=result)

        try:
            result = await asyncio.wait_for(
                asyncio.shield(dispatch_task), timeout=background_timeout
            )
            return ToolResult(content=result)
        except asyncio.TimeoutError:
            task_id = task_mgr.track_task(name, dispatch_task, desc)
            logger.info(
                "Tool '%s' exceeded background_timeout (%ss), tracked as background (task #%d)",
                name, background_timeout, task_id,
            )
            return ToolResult(
                content=json.dumps(
                    {
                        "backgrounded": True,
                        "task_id": task_id,
                        "tool": name,
                        "message": f"Tool is still running in background (task #{task_id}). Results will be injected when ready.",
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
