"""定时提醒工具 — 到点后把一句话注入 Agent 的上下文。

三个工具：
- schedule_reminder：新增一次性（at / in_seconds）或周期（every_seconds）提醒
- list_reminders：列出全部提醒
- cancel_reminder：按 id 删除

投递方式：提醒到期后由 PromptAssembler 在**下一轮**装配上下文时取出，包进
<system-reminder> 贴到用户消息上——不会主动发消息给用户，也不会打断当前回合。
存储见 Core/ReminderStore.py（runtime_memory/scheduled_reminders.json）。
"""

import json

from Core.ReminderStore import get_reminder_store
from Tools.registry import registry


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _fmt_item(item):
    return {
        "id": item.get("id"),
        "text": item.get("text"),
        "fire_at": item.get("fire_at"),
        "repeat_seconds": item.get("repeat_seconds"),
        "fired_count": item.get("fired_count", 0),
    }


def schedule_reminder(text: str = "", at: str = "", in_seconds: int = None,
                      every_seconds: int = None) -> str:
    """新增一条定时提醒。"""
    try:
        item = get_reminder_store().add(
            text=text,
            at=at or None,
            in_seconds=in_seconds,
            every_seconds=every_seconds,
        )
        return _json({
            "status": "success",
            "reminder": _fmt_item(item),
            "note": "到期后会在下一轮对话的上下文中出现（不是主动发消息）",
        })
    except Exception as exc:
        return _json({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


def list_reminders() -> str:
    """列出全部定时提醒。"""
    try:
        items = get_reminder_store().list_all()
        return _json({
            "status": "success",
            "count": len(items),
            "reminders": [_fmt_item(it) for it in items],
        })
    except Exception as exc:
        return _json({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


def cancel_reminder(reminder_id: int = 0) -> str:
    """按 id 删除一条定时提醒。"""
    try:
        if not reminder_id:
            return _json({"status": "error", "error": "reminder_id 必填"})
        ok = get_reminder_store().cancel(int(reminder_id))
        return _json({
            "status": "success" if ok else "not_found",
            "reminder_id": int(reminder_id),
            "removed": ok,
        })
    except Exception as exc:
        return _json({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


registry.register(
    name="schedule_reminder",
    description=(
        "Schedule a reminder that injects one sentence into your context when the time comes. "
        "Supports one-shot (absolute 'at' or relative 'in_seconds') and recurring "
        "('every_seconds', minimum 60) reminders. Persisted across restarts. "
        "Delivery happens on the NEXT turn: the reminder appears as a <system-reminder> "
        "attached to the user message; it does NOT proactively message the user. "
        "Use when the user says 'remind me at X', 'every morning tell me Y', or you need "
        "a future checkpoint."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The sentence to inject into context when due.",
            },
            "at": {
                "type": "string",
                "description": "Absolute local time: 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD', or 'HH:MM' (today, or tomorrow if already past).",
                "default": "",
            },
            "in_seconds": {
                "type": "integer",
                "description": "Relative delay in seconds from now (one-shot).",
            },
            "every_seconds": {
                "type": "integer",
                "description": "Recurring interval in seconds (>=60). First fire = now + interval.",
            },
        },
        "required": ["text"],
    },
    handler=schedule_reminder,
    group="utility",
)

registry.register(
    name="list_reminders",
    description="List all scheduled reminders with id, text, next fire time and repeat interval. Read-only.",
    arguments_schema={"type": "object", "properties": {}},
    handler=list_reminders,
    group="utility",
)

registry.register(
    name="cancel_reminder",
    description="Delete a scheduled reminder by id. Side effect: the reminder will never fire again.",
    arguments_schema={
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "integer",
                "description": "Reminder id from schedule_reminder / list_reminders.",
            },
        },
        "required": ["reminder_id"],
    },
    handler=cancel_reminder,
    group="utility",
)
