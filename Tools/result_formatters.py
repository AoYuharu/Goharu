"""工具结果格式化层 — 所有工具结果的统一 map 工序。

handler 返回 JSON 字符串（数据通道），本模块负责把它转成模型可读的中文
结构化文本（文本通道）。每个工具一个翻译模板；无法识别的结构回退到
美化 JSON，绝不丢数据（payload 字段如 content/stdout 必须完整保留）。

接入点：InProcessToolRuntime.call_tool 在 registry.dispatch 返回后调用
format_tool_result()。
"""

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── 工具函数 ─────────────────────────────────────────────────────────────


def _pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _error_text(data: dict) -> str:
    """统一错误渲染：❌ + 错误信息 + 可选 hint/path 等附加字段"""
    lines = [f"❌ 错误：{data.get('error')}"]
    for key, value in data.items():
        if key == "error":
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


# ── 各工具翻译模板 ────────────────────────────────────────────────────────


def _fmt_read(data: dict) -> str:
    header = (
        f"已读取 {data.get('file')}"
        f"（第 {data.get('start_line')}-{data.get('end_line')} 行，"
        f"共 {data.get('total_lines')} 行）:"
    )
    lines = [
        f"{item.get('line')}\t{item.get('text', '')}"
        for item in data.get("content") or []
    ]
    out = header + "\n" + "\n".join(lines)
    # 视图状态提醒（部分视图/已读完/越界）必须以自然语言出现在文本通道，
    # 防止模型把"读到上限"误认为"文件结束"
    notice = data.get("notice")
    if notice:
        out += "\n" + notice
    return out


def _fmt_write(data: dict) -> str:
    return f"已创建文件 {data.get('file')}（{data.get('lines')} 行）"


def _fmt_edit(data: dict) -> str:
    old_len = data.get("old_length", "?")
    new_len = data.get("new_length", "?")
    return f"已修改 {data.get('file')}（替换 {old_len} 字符 → {new_len} 字符）"


def _fmt_grep(data: dict) -> str:
    count = data.get("count", 0)
    suffix = "（结果过多已截断）" if data.get("truncated") else ""
    header = f"找到 {count} 处匹配{suffix}:"
    lines = [
        f"  {m.get('file')}:{m.get('line')}: {m.get('content', '')}"
        for m in data.get("matches") or []
    ]
    return header + ("\n" + "\n".join(lines) if lines else "")


def _fmt_glob(data: dict) -> str:
    num = data.get("numFiles", 0)
    suffix = "（结果过多已截断）" if data.get("truncated") else ""
    header = f"找到 {num} 个文件{suffix}（{data.get('durationMs', 0)}ms）:"
    files = [f"  {name}" for name in data.get("filenames") or []]
    return header + ("\n" + "\n".join(files) if files else "")


def _fmt_run_cmd(data: dict) -> str:
    exit_code = data.get("exit_code")
    parts = [f"退出码: {exit_code}"]
    timed_out = data.get("timed_out")
    interrupted = data.get("interrupted")
    if timed_out:
        parts.append("（命令超时已终止）")
    if interrupted:
        parts.append("（命令被中断）")
    stdout = (data.get("stdout") or "").strip()
    stderr = (data.get("stderr") or "").strip()
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if not stdout and not stderr and exit_code == 0 and not timed_out and not interrupted:
        parts.append("（命令执行成功，无输出）")
    return "\n".join(parts)


def _fmt_agent_delegate(data: dict) -> str:
    status = data.get("status", "unknown")
    duration = data.get("total_duration_ms")
    header = f"[子智能体 {status}] {data.get('agent_type', '')} agent"
    if duration is not None:
        header += f"（耗时 {duration}ms）"
    content = data.get("content") or ""
    return header + "\n\n" + str(content)


# 工具名 → 翻译模板。没列出的工具走默认（美化 JSON）。
_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "Read": _fmt_read,
    "Write": _fmt_write,
    "Edit": _fmt_edit,
    "Grep": _fmt_grep,
    "Glob": _fmt_glob,
    "run_cmd": _fmt_run_cmd,
    "AgentDelegate": _fmt_agent_delegate,
}


# ── 统一入口 ─────────────────────────────────────────────────────────────


def format_tool_result(tool_name: str, raw: Any) -> str:
    """map 工序：handler 返回值 → 模型可读文本。

    - 非 JSON 字符串（Sleep、background_status 等本身就是人话）→ 原样放行
    - JSON 含 error 字段 → 统一错误渲染
    - 有专属模板 → 用模板
    - 其余 → 美化 JSON（兜底，不丢数据）
    """
    if not isinstance(raw, str):
        raw = str(raw)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw

    if not isinstance(data, dict):
        return raw if isinstance(data, str) else _pretty_json(data)

    if "error" in data:
        return _error_text(data)

    formatter = _FORMATTERS.get(tool_name)
    if formatter is None:
        return _pretty_json(data)

    try:
        return formatter(data)
    except Exception as exc:
        logger.warning("format_tool_result(%s) 模板渲染失败，回退原始 JSON: %s", tool_name, exc)
        return _pretty_json(data)
