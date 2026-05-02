import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from Tools.registry import registry


SKIPPED_DIRS = {".git", ".cache", "__pycache__", ".venv", "venv", "node_modules"}


@dataclass
class FileLockState:
    readers: set[str] = field(default_factory=set)
    writer: str | None = None


class FileAccessState:
    def __init__(self):
        self.read_ranges: dict[str, list[tuple[int, int]]] = {}
        self.locks: dict[str, FileLockState] = {}
        self.state_lock = asyncio.Lock()

    @staticmethod
    def normalize_path(path: str) -> str:
        return str(Path(path).expanduser().resolve())

    def _lock_for(self, file_key: str) -> FileLockState:
        lock_state = self.locks.get(file_key)
        if lock_state is None:
            lock_state = FileLockState()
            self.locks[file_key] = lock_state
        return lock_state

    async def acquire_read(self, file_key: str, actor_id: str):
        async with self.state_lock:
            lock_state = self._lock_for(file_key)
            if lock_state.writer is not None:
                return {
                    "error": "文件正在写入中",
                    "blocked_by": f"{lock_state.writer}正在写入中",
                }
            lock_state.readers.add(actor_id)
            return None

    async def release_read(self, file_key: str, actor_id: str):
        async with self.state_lock:
            lock_state = self._lock_for(file_key)
            lock_state.readers.discard(actor_id)

    async def acquire_write(self, file_key: str, actor_id: str):
        async with self.state_lock:
            lock_state = self._lock_for(file_key)
            if lock_state.writer is not None:
                return {
                    "error": "文件正在写入中",
                    "blocked_by": f"{lock_state.writer}正在写入中",
                }
            other_readers = sorted(reader for reader in lock_state.readers if reader != actor_id)
            if other_readers:
                return {
                    "error": "文件正在读取中",
                    "blocked_by": f"{', '.join(other_readers)}正在读取中",
                }
            lock_state.writer = actor_id
            return None

    async def release_write(self, file_key: str, actor_id: str):
        async with self.state_lock:
            lock_state = self._lock_for(file_key)
            if lock_state.writer == actor_id:
                lock_state.writer = None

    async def record_read_range(self, file_key: str, start_line: int, end_line: int):
        async with self.state_lock:
            ranges = self.read_ranges.setdefault(file_key, [])
            ranges.append((start_line, end_line))
            ranges.sort()
            merged = []
            for current_start, current_end in ranges:
                if not merged or current_start > merged[-1][1] + 1:
                    merged.append((current_start, current_end))
                else:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], current_end))
            self.read_ranges[file_key] = merged

    async def range_is_read(self, file_key: str, start_line: int, end_line: int) -> bool:
        async with self.state_lock:
            return any(
                read_start <= start_line and end_line <= read_end
                for read_start, read_end in self.read_ranges.get(file_key, [])
            )

    async def insertion_point_is_read(self, file_key: str, line: int) -> bool:
        async with self.state_lock:
            for read_start, read_end in self.read_ranges.get(file_key, []):
                if read_start <= line <= read_end or line in (read_start - 1, read_end + 1):
                    return True
            return False


_file_state = FileAccessState()


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _error(message: str, **extra) -> str:
    data = {"error": message}
    data.update(extra)
    return _json(data)


def _coerce_line(value, default=None):
    if value is None:
        return default
    return int(value)


def _detect_newline(raw_text: str) -> str:
    crlf = raw_text.count("\r\n")
    lf = raw_text.count("\n") - crlf
    cr = raw_text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf > 0:
        return "\r\n"
    if cr > lf and cr > 0:
        return "\r"
    if lf > 0:
        return "\n"
    return "\n"


def _read_file_text(path_obj: Path):
    raw = path_obj.read_text(encoding="utf-8")
    newline = _detect_newline(raw)
    has_trailing_newline = raw.endswith(("\n", "\r"))
    return raw.splitlines(), newline, has_trailing_newline


def _write_file_text(path_obj: Path, lines: list[str], newline: str, has_trailing_newline: bool):
    text = newline.join(lines)
    if has_trailing_newline and lines:
        text += newline
    path_obj.write_text(text, encoding="utf-8", newline="")


def _split_content_lines(content: str | None) -> list[str]:
    if content is None:
        return []
    return content.splitlines()


def _is_probably_text_file(path_obj: Path) -> bool:
    try:
        with path_obj.open("rb") as file_obj:
            chunk = file_obj.read(4096)
    except OSError:
        return False
    return b"\x00" not in chunk


def _iter_target_files(path_obj: Path):
    if path_obj.is_file():
        if _is_probably_text_file(path_obj):
            yield path_obj
        return
    if not path_obj.is_dir():
        return
    for root, dirs, files in os.walk(path_obj):
        dirs[:] = [dirname for dirname in dirs if dirname not in SKIPPED_DIRS]
        root_path = Path(root)
        for filename in files:
            file_path = root_path / filename
            if _is_probably_text_file(file_path):
                yield file_path


async def Grep(pattern: str, path: str = ".", case_sensitive: bool = True, max_results: int = 100) -> str:
    if pattern == "":
        return _error("pattern 不能为空")
    max_results = max(1, int(max_results))
    target_path = Path(path).expanduser().resolve()
    if not target_path.exists():
        return _error("path 不存在", path=str(target_path))

    needle = pattern if case_sensitive else pattern.lower()
    matches = []
    truncated = False

    for file_path in _iter_target_files(target_path):
        file_key = _file_state.normalize_path(str(file_path))
        blocked = await _file_state.acquire_read(file_key, "Grep")
        if blocked is not None:
            continue
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as file_obj:
                for line_number, line in enumerate(file_obj, start=1):
                    content = line.rstrip("\r\n")
                    haystack = content if case_sensitive else content.lower()
                    if needle in haystack:
                        matches.append(
                            {
                                "file": str(file_path),
                                "line": line_number,
                                "content": content,
                            }
                        )
                        if len(matches) >= max_results:
                            truncated = True
                            break
            if truncated:
                break
        finally:
            await _file_state.release_read(file_key, "Grep")

    return _json({"matches": matches, "count": len(matches), "truncated": truncated})


async def Read(path: str, start_line: int = 1, end_line: int | None = None, actor_id: str = "agent") -> str:
    path_obj = Path(path).expanduser().resolve()
    if not path_obj.is_file():
        return _error("path 不是文件", path=str(path_obj))

    start_line = _coerce_line(start_line, 1)
    end_line = _coerce_line(end_line)
    if start_line < 1:
        return _error("start_line 必须大于等于 1")
    if end_line is not None and end_line < start_line:
        return _error("end_line 必须大于等于 start_line")

    file_key = _file_state.normalize_path(str(path_obj))
    blocked = await _file_state.acquire_read(file_key, actor_id)
    if blocked is not None:
        return _json(blocked)

    try:
        lines, _, _ = _read_file_text(path_obj)
        total_lines = len(lines)
        actual_end = total_lines if end_line is None else min(end_line, total_lines)
        if start_line > total_lines:
            content = []
            actual_end = total_lines
        else:
            content = [
                {"line": line_number, "text": lines[line_number - 1]}
                for line_number in range(start_line, actual_end + 1)
            ]
        await _file_state.record_read_range(file_key, start_line, actual_end)
    finally:
        await _file_state.release_read(file_key, actor_id)

    return _json(
        {
            "file": str(path_obj),
            "start_line": start_line,
            "end_line": actual_end,
            "total_lines": total_lines,
            "content": content,
        }
    )


async def Write(
    path: str,
    content: str,
    actor_id: str = "agent",
) -> str:
    """Create a new file with the given content"""
    path_obj = Path(path).expanduser().resolve()

    if path_obj.exists():
        return _error("文件已存在，请使用 Edit 工具修改", path=str(path_obj))

    file_key = _file_state.normalize_path(str(path_obj))
    blocked = await _file_state.acquire_write(file_key, actor_id)
    if blocked is not None:
        return _json(blocked)

    try:
        # 创建父目录（如果不存在）
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        lines = _split_content_lines(content)
        newline = "\n"  # 默认使用 LF
        has_trailing_newline = True
        _write_file_text(path_obj, lines, newline, has_trailing_newline)
    finally:
        await _file_state.release_write(file_key, actor_id)

    return _json(
        {
            "ok": True,
            "file": str(path_obj),
            "lines": len(_split_content_lines(content)),
        }
    )


async def Edit(
    path: str,
    old_string: str,
    new_string: str,
    actor_id: str = "agent",
) -> str:
    """Edit file by replacing old_string with new_string (patch-style)"""
    path_obj = Path(path).expanduser().resolve()
    if not path_obj.is_file():
        return _error("path 不是文件", path=str(path_obj))

    if not old_string:
        return _error("old_string 不能为空")

    if old_string == new_string:
        return _error("old_string 和 new_string 相同，无需修改")

    file_key = _file_state.normalize_path(str(path_obj))

    # 检查是否已读取文件（补丁式需要读取整个文件）
    blocked = await _file_state.acquire_write(file_key, actor_id)
    if blocked is not None:
        return _json(blocked)

    try:
        lines, newline, has_trailing_newline = _read_file_text(path_obj)
        original_text = newline.join(lines)
        if has_trailing_newline and lines:
            original_text += newline

        # 查找 old_string
        if old_string not in original_text:
            return _error(
                "old_string 在文件中不存在",
                hint="请先用 Read 工具查看文件内容，确保 old_string 完全匹配"
            )

        # 检查唯一性
        occurrences = original_text.count(old_string)
        if occurrences > 1:
            return _error(
                f"old_string 在文件中出现 {occurrences} 次，不唯一",
                hint="请提供更多上下文使 old_string 唯一，或使用更具体的匹配内容"
            )

        # 执行替换
        new_text = original_text.replace(old_string, new_string, 1)
        new_lines = new_text.splitlines()

        # 检测新文本是否有尾随换行符
        new_has_trailing = new_text.endswith(("\n", "\r\n", "\r"))

        _write_file_text(path_obj, new_lines, newline, new_has_trailing)
    finally:
        await _file_state.release_write(file_key, actor_id)

    return _json(
        {
            "ok": True,
            "file": str(path_obj),
            "old_length": len(old_string),
            "new_length": len(new_string),
        }
    )


registry.register(
    name="Grep",
    description=(
        "Search text in files and return matching file paths, 1-based line numbers, and line content. "
        "Required: pattern (text to search). Optional: path (file or directory, default '.'), "
        "case_sensitive (default true), max_results (default 100). "
        "Grep is read-only and does not grant Edit permission."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Text pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search in, default '.'"},
            "case_sensitive": {"type": "boolean", "description": "Case sensitive search, default true"},
            "max_results": {"type": "integer", "description": "Maximum number of results, default 100"},
        },
        "required": ["pattern"],
    },
    handler=Grep,
    group="file",
)

registry.register(
    name="Read",
    description=(
        "Read file content by 1-based line range [start_line, end_line]. "
        "Required: path. Optional: start_line (default 1), end_line (default: read to end), actor_id (default 'agent'). "
        "Returns file path, total lines, and line-by-line content. "
        "After successful read, the range is recorded. Edit can only modify ranges that have been Read first."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "start_line": {"type": "integer", "description": "Start line number (1-based), default 1"},
            "end_line": {"type": "integer", "description": "End line number (1-based), inclusive; omit to read to end"},
            "actor_id": {"type": "string", "description": "Actor identifier, default 'agent'"},
        },
        "required": ["path"],
    },
    handler=Read,
    group="file",
)

registry.register(
    name="Write",
    description=(
        "Create a new file with the given content. "
        "Required: path, content. "
        "IMPORTANT: This tool can ONLY create new files. If the file already exists, it will fail. "
        "To modify existing files, use Read + Edit instead. "
        "The file will be created with UTF-8 encoding and LF line endings by default."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to create (must not exist)"},
            "content": {"type": "string", "description": "File content to write"},
            "actor_id": {"type": "string", "description": "Actor identifier, default 'agent'"},
        },
        "required": ["path", "content"],
    },
    handler=Write,
    group="file",
)

registry.register(
    name="Edit",
    description=(
        "Edit file by replacing old_string with new_string (patch-style, like git diff). "
        "Required: path, old_string, new_string. "
        "IMPORTANT: "
        "1. You must Read the file first to see its content. "
        "2. old_string must exist in the file and be unique (appear exactly once). "
        "3. old_string must match EXACTLY (including whitespace, indentation, line breaks). "
        "4. If old_string appears multiple times, provide more context to make it unique. "
        "5. Grep does not grant Edit permission - you must use Read. "
        "This is a safer way to edit files as it ensures you know what you're changing."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to modify"},
            "old_string": {"type": "string", "description": "Exact string to find and replace (must be unique in file)"},
            "new_string": {"type": "string", "description": "New string to replace old_string with"},
            "actor_id": {"type": "string", "description": "Actor identifier, default 'agent'"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    handler=Edit,
    group="file",
)
