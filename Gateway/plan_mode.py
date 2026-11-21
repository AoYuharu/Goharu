"""计划模式 —— 主 agent 的只读运行时闸门（Gateway 产品层装配件）。

计划模式是一个 per-session 状态：进入后主 agent 被硬性限制为只读。闸门
`PlanModeGate` 包装共享的 `InProcessToolRuntime`，每个 session 各持一个实例
（enabled 只读本 session 状态，不动共享 runtime 内部——多 session 不串台）。

只读命令策略复用 Agent/SubAgent.py 的 `SubAgentConfig.is_command_allowed`，
与 explore/plan 子代理同一套白名单（BASE_READONLY_COMMANDS + find + 禁止模式）。
"""

import json
import logging

logger = logging.getLogger(__name__)

# 计划模式允许的工具白名单（只读探索 + 规划 + 交互 + 退出确认）
PLAN_MODE_ALLOWED_TOOLS = {
    "Read", "Grep", "Glob", "run_cmd",
    "tasks_update", "tasks_read", "AskUser", "ExitPlanMode",
}


def is_plan_mode_command(text) -> bool:
    """判断入站文本是否为手动进入计划模式的命令（/plan 或「进入计划模式」）。"""
    if not text:
        return False
    t = str(text).strip()
    if t.lower() == "/plan":
        return True
    return "进入计划模式" in t


def _err_json(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


class PlanModeGate:
    """包装共享工具运行时，计划模式开启时只放行白名单工具 + 只读 run_cmd。

    契约与 InProcessToolRuntime 一致：`last_tool_definitions`（LLM 每次 step 读取）、
    `list_tools()`（create_agent 装配时调用）、`call_tool()`（硬执行点）。
    `__getattr__` 把其余属性（background_tasks/close 等）透传给底层运行时。
    """

    def __init__(self, base_runtime):
        self._base = base_runtime
        self.enabled = False

    @property
    def last_tool_definitions(self):
        defs = self._base.last_tool_definitions
        if not self.enabled:
            return defs
        return [d for d in defs if d.get("name") in PLAN_MODE_ALLOWED_TOOLS]

    @last_tool_definitions.setter
    def last_tool_definitions(self, value):
        self._base.last_tool_definitions = value

    async def list_tools(self):
        await self._base.list_tools()
        return self.last_tool_definitions

    async def call_tool(self, name, arguments=None):
        if self.enabled:
            if name not in PLAN_MODE_ALLOWED_TOOLS:
                logger.warning("plan mode: 拒绝工具 %s（只读白名单外）", name)
                return _err_json(
                    f"计划模式只读：工具 '{name}' 不可用。允许的只读工具："
                    f"{sorted(PLAN_MODE_ALLOWED_TOOLS)}。如需写文件/改代码，"
                    f"请先 ExitPlanMode 让用户确认退出计划模式。"
                )
            if name == "run_cmd":
                cmd = str((arguments or {}).get("cmd", "") or "")
                from Agent.SubAgent import SubAgentConfig
                if not SubAgentConfig.is_command_allowed("plan", cmd):
                    logger.warning("plan mode: 拒绝 run_cmd（非只读命令）: %s", cmd)
                    return _err_json(
                        f"计划模式只读：命令 '{cmd}' 不在只读白名单。"
                        f"仅允许 ls/git status/log/diff/find/wc/sort 等只读命令。"
                    )
        return await self._base.call_tool(name, arguments)

    def __getattr__(self, name):
        return getattr(self._base, name)
