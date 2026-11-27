"""只读目录容量统计工具 DiskUsage。

边界：单根路径、逻辑文件大小、同次扫描硬链接去重、不跟随符号链接/
Windows reparse point、Top-N 聚合、权限错误显式返回、协作式取消。
"""

import asyncio
import heapq
import json
import logging
import os
import stat as stat_module
import threading
import time

from Tools.registry import registry

logger = logging.getLogger(__name__)

_REPARSE_POINT = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse_stat(st) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_POINT)


def _is_link_entry(entry, st) -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    return _is_reparse_stat(st)


def _error_result(message: str) -> dict:
    return {"error": message}


def _scan_tree(root: str, depth: int, top_n: int, stop_event: threading.Event) -> dict:
    started = time.time()
    entries: dict[str, dict] = {}
    seen_inodes: set[tuple[int, int]] = set()
    largest: list[tuple[int, str]] = []
    permission_errors = 0
    other_errors = 0
    error_samples: list[dict] = []
    skipped_links = 0
    file_count = 0
    dir_count = 0
    hardlink_deduped = 0
    total_bytes = 0
    cancelled = False

    def add_error(path: str, exc: OSError):
        nonlocal permission_errors, other_errors
        if isinstance(exc, PermissionError):
            permission_errors += 1
        else:
            other_errors += 1
        if len(error_samples) < 10:
            error_samples.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})

    def add_file(path: str, size: int, bucket: str, st):
        nonlocal total_bytes, file_count, hardlink_deduped
        ino = int(getattr(st, "st_ino", 0) or 0)
        dev = int(getattr(st, "st_dev", 0) or 0)
        if ino:
            key = (dev, ino)
            if key in seen_inodes:
                hardlink_deduped += 1
                return
            seen_inodes.add(key)
        total_bytes += size
        file_count += 1
        entry = entries.setdefault(bucket, {"path": bucket, "bytes": 0, "files": 0})
        entry["bytes"] += size
        entry["files"] += 1
        item = (size, path)
        if len(largest) < top_n:
            heapq.heappush(largest, item)
        elif size > largest[0][0]:
            heapq.heapreplace(largest, item)

    stack: list[tuple[str, int, str]] = [(root, 0, ".")]
    scanned_entries = 0
    while stack:
        if stop_event.is_set():
            cancelled = True
            break
        dirpath, dir_depth, bucket = stack.pop()
        dir_count += 1 if dirpath != root else 0
        try:
            with os.scandir(dirpath) as it:
                for entry in it:
                    scanned_entries += 1
                    if scanned_entries % 128 == 0 and stop_event.is_set():
                        cancelled = True
                        break
                    try:
                        st = os.stat(entry.path, follow_symlinks=False)
                        if _is_link_entry(entry, st):
                            skipped_links += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            child_depth = dir_depth + 1
                            child_bucket = entry.path if child_depth <= depth else bucket
                            stack.append((entry.path, child_depth, child_bucket))
                        elif entry.is_file(follow_symlinks=False):
                            add_file(entry.path, int(st.st_size), bucket, st)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        add_error(entry.path, exc)
        except FileNotFoundError:
            continue
        except OSError as exc:
            add_error(dirpath, exc)
        if cancelled:
            break

    ordered_entries = sorted(entries.values(), key=lambda x: (-x["bytes"], x["path"]))[:top_n]
    ordered_files = [{"path": p, "bytes": s} for s, p in sorted(largest, reverse=True)[:top_n]]
    return {
        "path": root,
        "total_bytes": total_bytes,
        "file_count": file_count,
        "dir_count": dir_count,
        "size_basis": "logical",
        "symlink_policy": "not_followed",
        "hardlink_policy": "counted_once_per_scan_when_ino_available",
        "hardlink_deduped": hardlink_deduped,
        "skipped_links": skipped_links,
        "complete": not cancelled and permission_errors == 0 and other_errors == 0,
        "cancelled": cancelled,
        "permission_errors": permission_errors,
        "other_errors": other_errors,
        "error_samples": error_samples,
        "entries": ordered_entries,
        "largest_files": ordered_files,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def _top_level_dirs(root: str) -> list[str]:
    dirs: list[str] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    st = os.stat(entry.path, follow_symlinks=False)
                    if _is_link_entry(entry, st):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.path)
                except OSError:
                    continue
    except OSError:
        return []
    return dirs


def _root_error(path: str) -> str | None:
    expanded = os.path.expanduser(path or "")
    if not expanded:
        return "path 不能为空"
    root = os.path.abspath(expanded)
    if not os.path.exists(root):
        return f"路径不存在: {path}"
    if not os.path.isdir(root):
        return f"路径不是目录: {path}"
    try:
        st = os.lstat(root)
        if os.path.islink(root) or _is_reparse_stat(st):
            return "根路径不能是符号链接或 Windows reparse point；请传真实目录"
    except OSError as exc:
        return f"无法读取根路径: {exc}"
    return None


async def DiskUsage(path: str, top_n: int = 20, depth: int = 1, _subagent: bool = False) -> str:
    """只读扫描一个目录树，返回逻辑大小、Top-N 聚合与覆盖状态。"""
    error = _root_error(path)
    if error:
        return json.dumps(_error_result(error), ensure_ascii=False)
    root = os.path.abspath(os.path.expanduser(path))
    top_level = _top_level_dirs(root)
    if not _subagent and len(top_level) >= 2:
        sample = "、".join(top_level[:3])
        return json.dumps({
            "error": "广域或多子树扫描不能由主 Agent 直接执行",
            "delegation_required": True,
            "subtrees": top_level[:50],
            "hint": (
                "请按非重叠目录树调用多个 AgentDelegate(agent_type='Explore')，"
                f"每个任务用 DiskUsage 扫描一个子树（例如：{sample}），"
                "主 Agent 只合并结果，不要改用 run_cmd 绕过。"
            ),
        }, ensure_ascii=False)
    top_n = max(1, min(100, int(top_n)))
    depth = max(1, min(3, int(depth)))
    stop_event = threading.Event()
    logger.info("DiskUsage start path=%s depth=%d top_n=%d", root, depth, top_n)
    try:
        result = await asyncio.to_thread(_scan_tree, root, depth, top_n, stop_event)
    except asyncio.CancelledError:
        stop_event.set()
        logger.info("DiskUsage cancelled path=%s", root)
        raise
    logger.info(
        "DiskUsage done path=%s bytes=%d files=%d complete=%s",
        root, result["total_bytes"], result["file_count"], result["complete"],
    )
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="DiskUsage",
    description=(
        "ROUTING: The main Agent MUST NOT call this directly for whole-drive, broad, or "
        "multi-subtree scans; first partition into NON-OVERLAPPING Explore tasks, and each "
        "Explore calls DiskUsage on its assigned root. Direct main-agent use is only for one "
        "known narrow directory or a spot-check of a delegated aggregate. "
        "Read-only directory-tree size scanner. Scans ONE existing directory root and returns "
        "logical file-size total, depth 1-3 aggregate entries, Top-N largest files, permission "
        "errors, and cancellation state. Does not follow symlinks or Windows reparse points; "
        "hard links are counted once per scan when the file index is available. For broad or multi-tree disk scans, call this "
        "through multiple Explore agents on NON-OVERLAPPING roots. Use it instead of ad-hoc "
        "du/Python scans. Size is logical st_size, not exact physical allocated space."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "One existing directory root to scan. Must not be a symlink/reparse point.",
            },
            "top_n": {
                "type": "integer",
                "description": "Maximum aggregate entries and largest files to return. Clamped to 1..100.",
                "default": 20,
            },
            "depth": {
                "type": "integer",
                "description": "Directory aggregation depth from the root. Clamped to 1..3.",
                "default": 1,
            },
        },
        "required": ["path"],
    },
    handler=DiskUsage,
    group="filesystem",
)
