"""跨主 Agent 与子 Agent 共享的工具调用历史板（状态版）。

提供主 Agent 与 Verify 子 Agent 互查对方工具调用历史的机制：
- 主 Agent 本轮的工具调用 → 注入 Verify 上下文（让 Verify 看到主 Agent 做了什么）
- Verify 子 Agent 的工具调用 → 随 Verify 报告返回给主 Agent（让主 Agent 审计 Verify 实际执行了什么）

全部内存态、进程内共享：主 Agent 侧按 turn 重置，子 Agent 侧按 agent_id 归属。
"""

import threading

_lock = threading.Lock()

_main_calls: list[dict] = []
_subagent_calls: dict[str, list[dict]] = {}


def _clip(text, n: int) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= n else compact[: n - 1] + "…"


def record_main_call(tool: str, args: str, result: str, is_error: bool = False,
                     backgrounded: bool = False) -> None:
    """记录主 Agent 本轮的一次工具调用。"""
    with _lock:
        _main_calls.append({
            "tool": tool,
            "args": _clip(args, 160),
            "result": _clip(result, 120),
            "is_error": is_error,
            "backgrounded": backgrounded,
        })


def record_subagent_call(agent_id: str, tool: str, args: str, is_error: bool = False) -> None:
    """记录某个子 Agent 的一次工具调用。"""
    with _lock:
        _subagent_calls.setdefault(agent_id, []).append({
            "tool": tool,
            "args": _clip(args, 100),
            "is_error": is_error,
        })


def get_main_calls() -> list[dict]:
    with _lock:
        return list(_main_calls)


def get_subagent_calls(agent_id: str) -> list[dict]:
    with _lock:
        return list(_subagent_calls.get(agent_id, []))


def reset_main_calls() -> None:
    with _lock:
        _main_calls.clear()


def drop_subagent(agent_id: str) -> None:
    with _lock:
        _subagent_calls.pop(agent_id, None)


def render_board(calls: list[dict], title: str) -> str:
    """把调用列表渲染成可读的文本状态版。"""
    lines = [f"## {title}"]
    if not calls:
        lines.append("(无工具调用记录)")
        return "\n".join(lines)
    for i, c in enumerate(calls, 1):
        mark = "✗" if c.get("is_error") else "✓"
        bg = " [后台]" if c.get("backgrounded") else ""
        args = c.get("args") or ""
        result = c.get("result") or ""
        parts = [f"{i}. {c['tool']}"]
        if args:
            parts.append(f" {args}")
        parts.append(f" {mark}{bg}")
        if result:
            parts.append(f" → {result}")
        lines.append("".join(parts))
    return "\n".join(lines)
