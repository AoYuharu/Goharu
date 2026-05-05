"""
Glob 工具 - 快速文件模式匹配
仿照 Claude Code 的 GlobTool 实现
"""
import os
import time
from pathlib import Path
from typing import Optional

from Tools.registry import registry


# 跳过的目录（版本控制系统和常见的缓存目录）
SKIPPED_DIRS = {
    ".git", ".svn", ".hg", ".bzr", ".jj", ".sl",  # VCS
    ".cache", "__pycache__", ".venv", "venv", "node_modules",  # 缓存和依赖
    ".pytest_cache", ".mypy_cache", ".tox",  # Python 工具缓存
}

# 默认结果限制
DEFAULT_LIMIT = 100


def glob_search(pattern: str, path: str = ".", limit: int = DEFAULT_LIMIT) -> dict:
    """
    使用 glob 模式搜索文件

    Args:
        pattern: glob 模式（如 "**/*.py", "src/**/*.ts"）
        path: 搜索的根目录
        limit: 最大返回文件数

    Returns:
        包含文件列表、数量、是否截断等信息的字典
    """
    start_time = time.time()

    # 规范化路径
    search_path = Path(path).expanduser().resolve()

    if not search_path.exists():
        return {
            "error": f"路径不存在: {path}",
            "filenames": [],
            "numFiles": 0,
            "truncated": False,
            "durationMs": 0,
        }

    if not search_path.is_dir():
        return {
            "error": f"路径不是目录: {path}",
            "filenames": [],
            "numFiles": 0,
            "truncated": False,
            "durationMs": 0,
        }

    # 使用 pathlib 的 glob 功能
    try:
        matches = []

        # 递归搜索匹配的文件
        for match in search_path.glob(pattern):
            # 跳过目录
            if not match.is_file():
                continue

            # 检查路径中是否包含需要跳过的目录
            parts = match.relative_to(search_path).parts
            if any(part in SKIPPED_DIRS for part in parts):
                continue

            matches.append(match)

            # 达到限制则停止
            if len(matches) >= limit:
                break

        # 按修改时间排序（最新的在前）
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # 转换为相对路径字符串
        filenames = [str(match.relative_to(search_path)) for match in matches[:limit]]

        truncated = len(matches) > limit
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "filenames": filenames,
            "numFiles": len(filenames),
            "truncated": truncated,
            "durationMs": duration_ms,
        }

    except Exception as e:
        return {
            "error": f"搜索失败: {str(e)}",
            "filenames": [],
            "numFiles": 0,
            "truncated": False,
            "durationMs": int((time.time() - start_time) * 1000),
        }


async def Glob(pattern: str, path: Optional[str] = None) -> str:
    """
    快速文件模式匹配工具

    Args:
        pattern: glob 模式（如 "**/*.py", "src/**/*.ts"）
        path: 搜索的根目录（可选，默认为当前目录）

    Returns:
        JSON 格式的搜索结果
    """
    import json

    if not pattern:
        return json.dumps({"error": "pattern 参数不能为空"}, ensure_ascii=False)

    # 默认使用当前目录
    search_path = path if path else "."

    result = glob_search(pattern, search_path)

    return json.dumps(result, ensure_ascii=False)


# 注册工具
registry.register(
    name="Glob",
    description="""Fast file pattern matching tool that works with any codebase size.

Usage:
- Supports glob patterns like "**/*.py" or "src/**/*.ts"
- Returns matching file paths sorted by modification time (newest first)
- Use this tool when you need to find files by name patterns
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, use the Task tool instead

Pattern Examples:
- "**/*.py" - Find all Python files recursively
- "src/**/*.ts" - Find all TypeScript files in src directory
- "test_*.py" - Find all test files in current directory
- "**/*.{js,ts}" - Find all JavaScript and TypeScript files (note: Python glob may not support brace expansion)

Important Notes:
- Results are limited to 100 files by default to prevent context bloat
- Files are sorted by modification time (most recently modified first)
- Automatically excludes version control directories (.git, .svn, etc.) and cache directories
- Returns relative paths from the search directory
- If results are truncated, consider using a more specific pattern or path

Performance:
- Fast for any codebase size (uses native filesystem operations)
- More efficient than recursive grep for finding files by name
- Suitable for exploratory searches and file discovery""",
    arguments_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match files against (e.g., '**/*.py', 'src/**/*.ts')",
            },
            "path": {
                "type": "string",
                "description": "The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter 'undefined' or 'null' - simply omit it for the default behavior. Must be a valid directory path if provided.",
            },
        },
        "required": ["pattern"],
    },
    handler=Glob,
    group="file",
)
