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


# ── 通用自然语言渲染（所有未登记模板的工具的统一兜底） ─────────────────────

# 这些键通常承载"主正文"，优先直出，其余字段作为补充说明
_BODY_KEYS = ("message", "result", "content", "text", "summary", "output", "answer")


def _scalar_text(value: Any) -> str:
    """标量值 → 自然语言片段（布尔转中文，避免 True/False 的刻板感）。"""
    if value is None:
        return "无"
    if value is True:
        return "是"
    if value is False:
        return "否"
    return str(value)


def _render_block(value: Any, depth: int = 0) -> str:
    """把 dict/list 递归渲染成缩进的自然语言行，不用 JSON 括号和引号。"""
    pad = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, val in value.items():
            if val is None or val == "" or val == [] or val == {}:
                continue
            if isinstance(val, (dict, list)):
                lines.append(f"{pad}{key}：")
                lines.append(_render_block(val, depth + 1))
            else:
                lines.append(f"{pad}{key}：{_scalar_text(val)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(_render_block(item, depth + 1))
            else:
                lines.append(f"{pad}- {_scalar_text(item)}")
        return "\n".join(lines)
    return f"{pad}{_scalar_text(value)}"


def _fmt_generic(tool_name: str, data: dict) -> str:
    """通用模板：任意工具返回的 JSON → 模型易读的自然语言文本。

    规则：
    1. 有主正文字段（message/result/content/text/...）→ 直出正文，
       其余字段作为补充说明
    2. 否则逐字段渲染，嵌套结构缩进展开，列表用「-」分点
    3. 全空 → 明确说"执行完成，无返回内容"
    """
    body_key = next((k for k in _BODY_KEYS if k in data and data[k] not in (None, "", [], {})), None)
    parts = []
    if body_key:
        body = data[body_key]
        parts.append(body if isinstance(body, str) else _render_block(body))
        rest = {k: v for k, v in data.items() if k != body_key}
    else:
        rest = data

    if rest:
        rendered = _render_block(rest)
        if rendered:
            parts.append(rendered)

    if not parts:
        return f"{tool_name} 执行完成，没有返回内容。"
    return "\n".join(parts)


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


def _human_bytes(value) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_disk_usage(data: dict) -> str:
    lines = [
        f"目录 {data.get('path')} 的逻辑大小为 {_human_bytes(data.get('total_bytes'))}"
        f"（文件 {data.get('file_count')}，目录 {data.get('dir_count')}，耗时 {data.get('elapsed_ms')}ms）。"
    ]
    coverage = "完整" if data.get("complete") else "不完整"
    lines.append(
        f"覆盖状态：{coverage}；权限错误 {data.get('permission_errors')}，"
        f"其他错误 {data.get('other_errors')}，取消：{'是' if data.get('cancelled') else '否'}。"
    )
    lines.append(
        "统计口径：文件逻辑大小（非精确物理占用）；符号链接/重解析点未跟随；"
        f"硬链接在文件索引可用时同次扫描只计一次（跳过重复 {data.get('hardlink_deduped')} 个）；"
        f"跳过链接 {data.get('skipped_links')} 个。"
    )
    entries = data.get("entries") or []
    if entries:
        lines.append("目录聚合 Top-N：")
        for item in entries:
            lines.append(f"  - {item.get('path')}：{_human_bytes(item.get('bytes'))}（{item.get('files')} 个文件）")
    files = data.get("largest_files") or []
    if files:
        lines.append("最大文件 Top-N：")
        for item in files:
            lines.append(f"  - {item.get('path')}：{_human_bytes(item.get('bytes'))}")
    samples = data.get("error_samples") or []
    if samples:
        lines.append("错误样例：")
        for item in samples:
            lines.append(f"  - {item.get('path')}：{item.get('error')}")
    return "\n".join(lines)


# 工具名 → 翻译模板。没列出的工具走默认（美化 JSON）。
_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "Read": _fmt_read,
    "Write": _fmt_write,
    "Edit": _fmt_edit,
    "Grep": _fmt_grep,
    "Glob": _fmt_glob,
    "run_cmd": _fmt_run_cmd,
    "AgentDelegate": _fmt_agent_delegate,
    "DiskUsage": _fmt_disk_usage,
}


# ── 统一入口 ─────────────────────────────────────────────────────────────


def format_tool_result(tool_name: str, raw: Any) -> str:
    """map 工序：handler 返回值 → 模型可读文本。

    - 非 JSON 字符串（Sleep、inspect 等本身就是人话/标签语言）→ 原样放行
    - JSON 含 error 字段 → 统一错误渲染
    - 有专属模板 → 用模板
    - 其余 → 通用自然语言模板（不丢数据，嵌套结构缩进展开）
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
        return _fmt_generic(tool_name, data)

    try:
        return formatter(data)
    except Exception as exc:
        logger.warning("format_tool_result(%s) 模板渲染失败，回退原始 JSON: %s", tool_name, exc)
        return _pretty_json(data)
