import asyncio
import json
import logging
from dataclasses import dataclass

from Tools.config_proxy import get_config
from Tools.background_tasks import BackgroundTaskManager
from Tools.loader import load_builtin_tools
from Tools.registry import registry
from Tools.result_formatters import format_tool_result
from Tools.verify_nudge import verify_nudge
from Tools.loop_guard import loop_guard

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
        # BackgroundTaskManager 句柄由 Tools.api.create_tools 装配时挂载
        self.background_tasks = None  # Optional[BackgroundTaskManager]
        self._initialized = False

    async def initialize(self):
        load_builtin_tools(self.modules)
        self._initialized = True
        return self

    @staticmethod
    def _background_timeout_for(name: str):
        """解析工具的前台等待超时：精确名 > 前缀通配（"mcp__*"）> 全局默认。

        MCP 远程调用（境外 HTTP 等）常超 25s 全局线，通配给更宽的窗口，
        避免被过早转后台（用户看到的是"没反应"）。
        """
        overrides = get_config().get("tools.background_timeout_by_tool", {}) or {}
        if name in overrides:
            return overrides[name]
        for key, val in overrides.items():
            k = str(key)
            if k.endswith("*") and name.startswith(k[:-1]):
                return val
        return get_config().get("tools.background_timeout", 120)

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

        # 白名单工具豁免后台化：AskUser 这类"阻塞等用户"的工具按设计可以
        # 一直等下去，25s 转后台反而把提问面板打断了
        if name in get_config().get("tools.background_exempt_tools", ["AskUser"]):
            result = await registry.dispatch(name, args)
            return ToolResult(content=await self._finalize_success(name, args, result))

        # BackgroundTaskManager 已归入 Tools/（background_tasks.py），始终可用
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

        background_timeout = self._background_timeout_for(name)
        if not isinstance(background_timeout, (int, float)) or background_timeout <= 0:
            result = await dispatch_task
            return ToolResult(content=await self._finalize_success(name, args, result))

        try:
            result = await asyncio.wait_for(
                asyncio.shield(dispatch_task), timeout=background_timeout
            )
            return ToolResult(content=await self._finalize_success(name, args, result))
        except asyncio.TimeoutError:
            # 自动转后台的任务带硬超时：到点仍未完成则终止并注入超时说明
            #（非静默）。显式 run_background 的长任务（服务器等）不设限。
            # 子代理工具（AgentDelegate）同样豁免硬取消——它们有自己的
            # 迭代预算自终止，150s 定时器只会误杀合法的 verify 长跑
            # （2026-10-18 事故：474s 成功收官的 verify 被砍 + 重复派发）。
            no_cancel = get_config().get(
                "tools.background_no_cancel_tools", ["AgentDelegate"]
            )
            if name in no_cancel:
                max_duration = 0.0
            else:
                max_duration = float(
                    get_config().get("tools.background_max_duration_seconds", 150) or 150
                )
            task_id = task_mgr.track_task(
                name, dispatch_task, desc, expire_after_seconds=max_duration
            )
            logger.info(
                "Tool '%s' exceeded background_timeout (%ss), tracked as background (task #%d, max %s)",
                name, background_timeout, task_id,
                f"{max_duration:.0f}s" if max_duration > 0 else "no-limit",
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

    async def _finalize_success(self, name, args, result):
        """dispatch 成功完成后的统一收口：ReloadTools 之后刷新工具定义缓存
        （ActorAgent 每步只读 last_tool_definitions，不刷新则新定义不进 prompt），
        然后走后处理。后台化分支（立即返回/超时转后台）不经此路径。"""
        if name == "ReloadTools":
            await self.list_tools()
        return self._post_process(name, args, result)

    @staticmethod
    def _post_process(name, args, result):
        """工具结果的后处理工序（map 层）：
        1. format — JSON → 模型可读的中文结构化文本
        2. nudge — 行为模式检测，需要时在结果末尾夹带提醒
        """
        if get_config().get("tools.result_format.enabled", True):
            result = format_tool_result(name, result)
        nudges = [
            verify_nudge.after_tool_call(name, args or {}, result),
            loop_guard.after_tool_call(name, args or {}, result),
        ]
        for nudge in nudges:
            if nudge:
                result = result + "\n\n" + nudge
        return result

    async def close(self):
        return None


def create_tool_runtime(runtime_name=None):
    """
    创建工具运行时

    注意：MCP支持已移除，所有工具必须通过Tools.registry注册
    """
    selected_runtime = str(runtime_name or get_config().get("tools.runtime", "in_process")).strip().lower()
    if selected_runtime == "in_process":
        return InProcessToolRuntime()
    raise ValueError(f"Unsupported tool runtime: {selected_runtime}. Only 'in_process' is supported.")
