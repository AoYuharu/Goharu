"""Tools 子系统对外门面 —— 外部 agent harness 的唯一入口。

最小接入：

    import asyncio
    from Tools.api import create_tools, close_tools

    async def main():
        tools = await create_tools(
            config={"tools": {"background_timeout": 120}},   # dict 即可，扁平/嵌套键都支持
            modules=["Tools.builtin.core_tools", "Tools.builtin.file_tools"],
        )
        defs = await tools.list_tools()                       # [{name, description, inputSchema}]
        result = await tools.call_tool("run_cmd", {"command": "git status"})
        print(result.content)                                 # 字符串（JSON 或格式化文本）
        await close_tools(tools)

    asyncio.run(main())

在 Goharu 仓库内使用时行为不变：不传 config 即回退到全局 configurationLoader。
"""

import logging
import threading

from Tools.config_proxy import set_config, get_config, reset_config
from Tools.loader import load_builtin_tools
from Tools.registry import registry, ToolRegistry
from Tools.runtime import InProcessToolRuntime, ToolResult, create_tool_runtime
from Tools.security import (
    CommandSecurityChecker,
    check_command_safety,
    get_security_checker,
)
from Tools.confirmation_manager import confirmation_manager
from Tools.tool_process_tracker import tool_process_tracker
from Tools.cmd_translate import translate_command
from Tools.background_tasks import BackgroundTaskManager, BackgroundResult

logger = logging.getLogger(__name__)

__all__ = [
    "create_tools",
    "close_tools",
    "set_config",
    "get_config",
    "reset_config",
    "registry",
    "ToolRegistry",
    "InProcessToolRuntime",
    "ToolResult",
    "load_builtin_tools",
    "create_tool_runtime",
    # 安全沙箱组件（run_cmd 内联链：guard → classify → confirm → sandbox → tracker）
    "CommandSecurityChecker",
    "check_command_safety",
    "get_security_checker",
    "confirmation_manager",
    "tool_process_tracker",
    "translate_command",
    # 后台任务管理（run_background / background_timeout 的承载者）
    "BackgroundTaskManager",
    "BackgroundResult",
    "reconnect_mcp_server",
]


async def create_tools(
    config=None,
    modules: list[str] | None = None,
    enable_mcp: bool = False,
    on_task_change=None,
    on_confirm_needed=None,
    on_question=None,
    on_tool_output=None,
    on_subagent_event=None,
    on_plan_mode=None,
    on_file_change=None,
    memory=None,
) -> InProcessToolRuntime:
    """创建并初始化工具运行时（外部 harness 用）。

    Args:
        config: 配置 dict（扁平点号键或嵌套 dict 均可）或带 .get() 的对象；
                None 时回退 Goharu 全局 configurationLoader（仓库内行为不变）。
        modules: 要加载的内置工具模块列表；None 时读配置 tools.builtin_modules。
                 模块在 import 时把工具注册进全局 registry。
        enable_mcp: True 时按配置 tools.mcp.servers 连接 MCP server 并把其工具
                    桥接进 registry（命名 mcp__<server>__<tool>）。需要 mcp、
                    httpx 依赖；未安装会抛带说明的 ImportError。
        on_task_change: 任务清单变化回调 fn(tasks)（task_tools 监听器）
        on_confirm_needed: 危险命令确认通知 fn(confirm_id, command, description)
        on_question: AskUser 提问通知 fn(request_id, payload)
        on_tool_output: 工具流式输出订阅 fn(tool_name, stream, text)
        on_subagent_event: 子代理事件回调 fn(payload)（started/activity/finished）
        on_plan_mode: 计划模式状态回调 fn(action, payload)，action ∈
                      {"enter", "exit_approved", "exit_supplement"}（plan_mode_tools 监听器）
        on_file_change: 文件变更审计回调 fn(path, op, old_bytes, new_bytes, source)，
                        op ∈ {"create","modify","delete"}；写类工具落盘后触发
                        （file_change_hook 监听器，供 Checkpoint 等审计子系统接线）
        memory: 可选，记忆对象（Memory.bridge.create_memory() 的产出）——
                注入 memory_search / memory_add 工具；需有 hybrid_retriever
                与 add_atom 方法。
        —— 以上 hook 均为可选；None = 不接线。宿主确认/提问的"应答"仍走
        confirmation_manager.resolve / question_manager.resolve。

    Returns:
        已初始化的 InProcessToolRuntime。核心方法：
        - await runtime.list_tools() -> [{name, description, inputSchema}]
        - await runtime.call_tool(name, arguments) -> ToolResult(.content 为字符串)
        - runtime.last_tool_definitions -> 最近一次 list_tools 结果（喂给 LLM 用）
        - runtime.background_tasks -> BackgroundTaskManager 单例句柄
          （drain_pending / inject_into_memory / register_callback / has_pending）
    """
    if config is not None:
        set_config(config)

    runtime = InProcessToolRuntime(modules=modules)
    await runtime.initialize()

    # 模块级单例注入收口：宿主的回调经 facade 进入，禁止直接操作内部模块
    if on_task_change is not None:
        from Tools.builtin import task_tools
        task_tools.set_change_listener(on_task_change)
    if on_confirm_needed is not None:
        confirmation_manager.set_notifier(on_confirm_needed)
    if on_question is not None:
        from Tools.question_manager import question_manager
        question_manager.set_notifier(on_question)
    if on_tool_output is not None:
        from Tools.tool_output_stream import tool_output_bus
        tool_output_bus.subscribe(on_tool_output)
    if on_subagent_event is not None:
        from Tools.builtin import agent_delegate
        agent_delegate.set_output_callback(on_subagent_event)
    if on_plan_mode is not None:
        from Tools.builtin import plan_mode_tools
        plan_mode_tools.set_listener(on_plan_mode)
    if on_file_change is not None:
        from Tools.file_change_hook import set_file_change_listener
        set_file_change_listener(on_file_change)
    if memory is not None:
        from Tools.builtin import memory_tools
        memory_tools.set_memory_manager(memory)

    runtime.background_tasks = BackgroundTaskManager()

    if enable_mcp:
        try:
            from Tools.mcp.manager import mcp_manager
        except ImportError as e:
            raise ImportError(
                "enable_mcp=True 需要可选依赖 'mcp' 和 'httpx'："
                "pip install mcp httpx"
            ) from e
        mcp_manager.attach_runtime(runtime)
        try:
            await mcp_manager.start()
            logger.info("create_tools: MCP enabled, servers=%s", list(mcp_manager.list_servers()))
        except Exception as e:
            # MCP server 连不上不应拖垮整个工具运行时（透明化原则：优雅降级）
            logger.error("create_tools: MCP startup failed (non-critical): %s", e)

    # MCP 桥接工具已进 registry，刷新一次让 last_tool_definitions 含它们
    await runtime.list_tools()

    logger.info(
        "create_tools: initialized with %d tool(s)", len(runtime.last_tool_definitions)
    )
    return runtime


async def close_tools(runtime: InProcessToolRuntime) -> None:
    """关闭运行时：断开所有 MCP server（如启用过）并释放资源。"""
    try:
        from Tools.mcp.manager import mcp_manager
        await mcp_manager.close()
    except ImportError:
        pass
    await runtime.close()


def reconnect_mcp_server(name: str, timeout: float = 30):
    """重连一个已配置的 MCP server（独立子进程类 server 改配置后需重连生效）。

    MCP connect 是 async 的，本函数在独立线程中跑 asyncio.run，最多等
    timeout 秒。返回 (ok, error)。
    """
    import asyncio
    from Tools.mcp.manager import mcp_manager

    spec = mcp_manager.get_server_spec(name)
    if spec is None:
        return False, f"server '{name}' not configured"

    result_box = {}

    def _reconnect():
        try:
            asyncio.run(mcp_manager.connect(name, spec, persist=False))
            result_box["ok"] = True
        except Exception as exc:
            result_box["ok"] = False
            result_box["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_reconnect, daemon=True, name=f"mcp-reconnect-{name}")
    t.start()
    t.join(timeout=timeout)
    if "ok" not in result_box:
        return False, "timeout"
    return result_box["ok"], result_box.get("err")
