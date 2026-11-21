"""计划模式工具 —— 主 agent 的只读规划状态机。

EnterPlanMode / ExitPlanMode 两个 core 工具。进入/退出不是提示词自觉，而是
硬性的运行时状态：监听器把状态变更回调给 Gateway（对齐 task_tools.set_change_listener
/ question_manager.set_notifier 的注入模式），Gateway 据此切换 session 的只读闸门。

- EnterPlanMode：主 agent 判断任务复杂时调用，无阻塞，进入只读计划模式。
- ExitPlanMode：阻塞（用户问询）。读当前任务清单 → 问"清单是否充分"（按钮单选）
  → 充分则退出计划模式；需补充则再收一段补充文本，保持计划模式并返回给 agent 修订。

ExitPlanMode 在 tools.background_exempt_tools 白名单内（永不转后台/超时），
与 AskUser 同款阻塞语义。无 notifier（独立 harness）时优雅降级返回 unavailable。
"""

import json
import logging

from Tools.config_proxy import get_config
from Tools.question_manager import DEFAULT_TIMEOUT, question_manager
from Tools.registry import registry

logger = logging.getLogger(__name__)

# 状态变更监听器（由 Tools.api.create_tools(on_plan_mode=...) 注入）
_listener = None


def set_listener(fn):
    """注入计划模式状态回调 fn(action: str, payload: dict)。
    action ∈ {"enter", "exit_approved", "exit_supplement"}。"""
    global _listener
    _listener = fn
    logger.info("Plan mode tools: listener set")


def _notify(action, payload):
    if _listener is None:
        return
    try:
        _listener(action, payload)
    except Exception as e:
        logger.warning("plan mode listener failed (%s): %s", action, e)


def _ask_timeout():
    return int(get_config().get("tools.ask_user.timeout_seconds", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)


def enter_plan_mode() -> str:
    """进入只读计划模式（无阻塞）。"""
    _notify("enter", {})
    logger.info("EnterPlanMode: 进入计划模式")
    return json.dumps({
        "status": "entered",
        "message": (
            "已进入只读计划模式。现在只允许只读探索（Read/Grep/Glob/run_cmd 只读）"
            "与规划工具（tasks_update/tasks_read/AskUser/ExitPlanMode）。"
            "先探索理解现状，用 tasks_update 起草计划清单，最后调 ExitPlanMode 让用户确认。"
        ),
    }, ensure_ascii=False)


def exit_plan_mode() -> str:
    """退出计划模式：读清单 → 问用户是否充分 → 确认退出 / 收集补充。"""
    # 1. 读当前清单
    try:
        from Tools.builtin import task_tools
        raw_tasks = task_tools.tasks_read()
        tasks_obj = json.loads(raw_tasks)
        rendered = tasks_obj.get("rendered", "（任务清单为空）")
    except Exception:
        rendered = "（任务清单为空）"

    if not question_manager.has_notifier:
        logger.info("ExitPlanMode 无前端可用，降级返回")
        return json.dumps({
            "status": "unavailable",
            "message": "当前前端不支持交互确认，请直接在正文中展示计划清单并询问用户是否充分",
        }, ensure_ascii=False)

    timeout = _ask_timeout()
    question = (
        f"计划清单如下：\n{rendered}\n\n"
        "清单是否充分？"
    )
    options = [
        {"label": "充分，开始执行"},
        {"label": "需要补充"},
    ]

    # 2. 单选问询
    logger.info("ExitPlanMode: 询问清单是否充分")
    raw = question_manager.request(
        {"questions": [{"question": question, "options": options, "multi_select": False}]},
        timeout=timeout,
    )
    if raw is None:
        logger.info("ExitPlanMode: 用户超时/取消")
        return json.dumps({
            "status": "timeout",
            "message": f"用户 {timeout} 秒内未响应，请按当前清单继续（视同确认）或稍后再确认",
        }, ensure_ascii=False)

    ans = str(raw)
    if "充分" in ans or "开始执行" in ans or "开始" in ans or "够了" in ans:
        _notify("exit_approved", {})
        logger.info("ExitPlanMode: 用户确认充分，退出计划模式")
        return json.dumps({
            "status": "approved",
            "message": "用户确认计划充分。已退出计划模式，开始按清单逐项执行",
        }, ensure_ascii=False)

    # 3. 需要补充（或用户直接输入了补充内容）
    if "补充" in ans or "需要" in ans:
        logger.info("ExitPlanMode: 用户选择需要补充，追问补充内容")
        raw2 = question_manager.request(
            {"questions": [{"question": "请补充你的要求（直接回复文本）"}]},
            timeout=timeout,
        )
        supplement = (raw2 or "").strip()
    else:
        supplement = ans.strip()

    _notify("exit_supplement", {"supplement": supplement})
    logger.info("ExitPlanMode: 用户补充内容（len=%d）", len(supplement))
    return json.dumps({
        "status": "supplement",
        "supplement": supplement,
        "message": (
            "用户要求补充，尚未确认退出计划模式。请根据补充内容修订计划清单"
            "（tasks_update），完成后再次调用 ExitPlanMode 确认。"
        ),
    }, ensure_ascii=False)


registry.register(
    name="EnterPlanMode",
    description=(
        "Enter read-only plan mode. Use when facing a complex/multi-step task: BEFORE "
        "exploring or changing anything, call this tool to enter plan mode, which "
        "restricts you to read-only tools (Read/Grep/Glob/read-only run_cmd) plus "
        "planning tools (tasks_update/tasks_read/AskUser/ExitPlanMode). While in plan "
        "mode, explore the codebase, draft an ordered task checklist with tasks_update, "
        "and finally call ExitPlanMode to confirm the checklist with the user."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=enter_plan_mode,
    group="core",
)

registry.register(
    name="ExitPlanMode",
    description=(
        "Exit plan mode. Reads your drafted task checklist, presents it to the user, and "
        "asks whether it is sufficient. If the user confirms, plan mode ends and you may "
        "start executing. If the user wants to supplement, you receive their feedback and "
        "must revise the checklist (tasks_update) then call ExitPlanMode again. Never exit "
        "plan mode or start modifying files without calling this tool and getting approval."
    ),
    arguments_schema={"type": "object", "properties": {}},
    handler=exit_plan_mode,
    group="core",
)
