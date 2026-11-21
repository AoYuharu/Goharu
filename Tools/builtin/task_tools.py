"""
任务清单工具 — 复杂任务的 Plan-Execute-Verify 载体。

模型把复杂任务拆解成结构化清单（tasks_update 全量写入），执行中逐项更新状态，
TUI 底部实时显示进度。设计对齐 claude-code 的 TodoWrite：每次调用全量替换，
避免增量更新的不一致问题。

TUI 通知通过 set_change_listener 注入（保持 Tools 解耦）；
未注入时静默跳过，不影响工具本身功能。
"""
import json
import logging
import threading

from Tools.registry import registry

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_tasks = []  # [{"id": int, "subject": str, "status": "pending"|"in_progress"|"completed"}]
_change_listener = None

_VALID_STATUS = {"pending", "in_progress", "completed"}


def set_change_listener(listener):
    """由 Gateway 在初始化后注入：任务列表变化时回调 listener(tasks)。"""
    global _change_listener
    _change_listener = listener
    logger.info("Task tools: change listener set")


def _notify():
    listener = _change_listener
    if listener is None:
        return
    try:
        listener(list(_tasks))
    except Exception as e:
        logger.warning("task change listener failed: %s", e)


def _render():
    if not _tasks:
        return "（任务清单为空）"
    icon = {"pending": "○", "in_progress": "◐", "completed": "●"}
    lines = [f"{icon.get(t['status'], '?')} #{t['id']} [{t['status']}] {t['subject']}" for t in _tasks]
    done = sum(1 for t in _tasks if t["status"] == "completed")
    return f"任务进度：{done}/{len(_tasks)} 完成\n" + "\n".join(lines)


def tasks_update(tasks):
    with _lock:
        if not isinstance(tasks, list):
            return json.dumps({"status": "error", "error": "tasks 必须是数组"}, ensure_ascii=False)
        normalized = []
        seen_ids = set()
        for i, item in enumerate(tasks):
            if not isinstance(item, dict):
                return json.dumps({"status": "error", "error": f"第 {i} 项不是对象"}, ensure_ascii=False)
            subject = str(item.get("subject") or "").strip()
            if not subject:
                return json.dumps({"status": "error", "error": f"第 {i} 项缺少 subject"}, ensure_ascii=False)
            status = str(item.get("status") or "pending").strip()
            if status not in _VALID_STATUS:
                return json.dumps(
                    {"status": "error", "error": f"第 {i} 项 status 非法（须为 pending/in_progress/completed）"},
                    ensure_ascii=False,
                )
            try:
                task_id = int(item.get("id") or i + 1)
            except (TypeError, ValueError):
                return json.dumps({"status": "error", "error": f"第 {i} 项 id 非法"}, ensure_ascii=False)
            if task_id in seen_ids:
                return json.dumps({"status": "error", "error": f"id {task_id} 重复"}, ensure_ascii=False)
            seen_ids.add(task_id)
            normalized.append({"id": task_id, "subject": subject, "status": status})

        _tasks.clear()
        _tasks.extend(normalized)
        logger.info("tasks_update: %d 项（%d 完成）", len(_tasks),
                    sum(1 for t in _tasks if t["status"] == "completed"))
        result = _render()
    _notify()
    return json.dumps({"status": "success", "tasks": _tasks, "rendered": result}, ensure_ascii=False)


def tasks_read():
    with _lock:
        return json.dumps({"status": "success", "tasks": list(_tasks), "rendered": _render()},
                          ensure_ascii=False)


def tasks_clear():
    """清空任务清单（用户清空会话 / 新任务开始时由 Gateway 调用）。"""
    with _lock:
        _tasks.clear()
    _notify()


registry.register(
    name="tasks_update",
    description=(
        "任务清单管理（复杂任务的拆解与进度跟踪）。面对需要多步骤的复杂任务时，"
        "先用本工具把任务拆解成有序的子任务清单，然后逐项执行并更新状态"
        "（pending → in_progress → completed）。每次调用全量替换整个清单。"
        "规则：同一时间只有一项 in_progress；完成一项立即更新，不要攒到最后批量更新；"
        "执行中发现新子任务可随时插入。"
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "任务序号（从 1 开始）"},
                        "subject": {"type": "string", "description": "一句话描述该子任务"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "pending 待办 / in_progress 进行中 / completed 已完成",
                        },
                    },
                    "required": ["subject", "status"],
                },
                "description": "完整任务清单（全量替换）",
            },
        },
        "required": ["tasks"],
    },
    handler=tasks_update,
    group="core",
)

registry.register(
    name="tasks_read",
    description="读取当前任务清单（长会话中上下文被压缩后，用本工具找回任务进度）。",
    arguments_schema={"type": "object", "properties": {}},
    handler=tasks_read,
    group="core",
)


def _reset_for_test():
    global _change_listener
    with _lock:
        _tasks.clear()
    _change_listener = None
