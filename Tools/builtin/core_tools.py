import subprocess
import re
import json

from Tools.registry import registry
from Tools.security import check_command_safety
from Tools.tool_process_tracker import tool_process_tracker
from Tools.platform_utils import (
    get_subprocess_env,
    get_subprocess_creation_flags,
    is_windows,
    kill_process_tree,
)

# 导入 file_tools 以注册文件操作工具
import Tools.builtin.file_tools

# 导入 pdf_tools 以注册 PDF 解析工具
import Tools.builtin.pdf_tools

# 导入 knowledge_tools 以注册知识管理工具
from Tools.builtin.knowledge_tools import (
    add_insight,
    read_insight,
    list_insights,
    add_module,
    read_module,
    list_modules
)


async def run_cmd(cmd: str) -> str:
    """
    Execute shell command with strict file operation restrictions and security checks.
    Blocks dangerous commands and guides to use dedicated tools.
    """
    import asyncio
    cmd_lower = cmd.lower().strip()

    # 🔒 安全检查：拦截危险命令
    is_safe, error_message = check_command_safety(cmd)
    if not is_safe:
        return json.dumps({"error": error_message}, ensure_ascii=False)

    # 检测 echo 命令用于文件操作
    # 匹配: echo ... > file, echo ... >> file
    if re.search(r'\becho\b.*?(?:>>?)\s*\S+', cmd_lower):
        return json.dumps({"error": """File operation with 'echo' is not allowed.

You attempted to use 'echo' for file operations, which is prohibited.

Please use the dedicated tools instead:
- To CREATE a new file: Use the Write tool
  Example: {"tool": "Write", "arguments": {"path": "file.txt", "content": "your content here"}}

- To MODIFY an existing file: Use Read + Edit tools
  Example:
  1. {"tool": "Read", "arguments": {"path": "file.txt"}}
  2. {"tool": "Edit", "arguments": {"path": "file.txt", "old_string": "old text", "new_string": "new text"}}

These tools are safer, provide better error handling, and ensure you know what you're changing.
DO NOT retry with run_cmd. Use the dedicated tools."""}, ensure_ascii=False)

    # 检测其他被禁止的文件操作命令
    forbidden_patterns = [
        (r'\btype\b.*?\|', 'type with pipe'),  # type file.txt | ...
        (r'\bcat\b', 'cat'),
        (r'\bhead\b', 'head'),
        (r'\btail\b', 'tail'),
        (r'\bsed\b', 'sed'),
        (r'\bawk\b', 'awk'),
    ]

    for pattern, cmd_name in forbidden_patterns:
        if re.search(pattern, cmd_lower):
            return json.dumps({"error": f"""Using '{cmd_name}' for file operations is not allowed.

Please use the dedicated tools instead:
- To READ a file: Use the Read tool
- To SEARCH in files: Use the Grep tool
- To MODIFY a file: Use Read + Edit tools

DO NOT retry with run_cmd. Use the dedicated tools."""}, ensure_ascii=False)

    # 执行命令（带超时，默认30s；平台适配的进程组确保超时能杀死子进程树）
    env = get_subprocess_env()
    # Windows: 统一使用 UTF-8 编码，避免 cp936 与 Python 子进程 UTF-8 输出不一致导致中文乱码
    # chcp 65001 强制 cmd.exe 使用 UTF-8 代码页；env 中 PYTHONUTF8=1 强制 Python 子进程用 UTF-8
    actual_cmd = cmd
    if is_windows() and cmd.strip():
        actual_cmd = f'chcp 65001 >nul 2>nul && {cmd}'
    try:
        p = subprocess.Popen(
            actual_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=get_subprocess_creation_flags(),
        )
        tool_process_tracker.register(p.pid)
        try:
            stdout, stderr = await asyncio.to_thread(p.communicate, timeout=480)
            return json.dumps({
                "exit_code": p.returncode,
                "stdout": (stdout or "").strip(),
                "stderr": (stderr or "").strip(),
                "timed_out": False,
                "interrupted": False,
            }, ensure_ascii=False)
        except asyncio.CancelledError:
            kill_process_tree(p.pid)
            try:
                stdout, stderr = await asyncio.to_thread(p.communicate, timeout=5)
            except Exception:
                stdout, stderr = "", ""
            return json.dumps({
                "exit_code": -1,
                "stdout": (stdout or "").strip(),
                "stderr": (stderr or "").strip(),
                "timed_out": False,
                "interrupted": True,
            }, ensure_ascii=False)
        finally:
            tool_process_tracker.unregister(p.pid)
    except subprocess.TimeoutExpired:
        kill_process_tree(p.pid)
        try:
            stdout, stderr = await asyncio.to_thread(p.communicate, timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                stdout, stderr = await asyncio.to_thread(p.communicate, timeout=2)
            except Exception:
                stdout, stderr = "", ""
        except Exception:
            stdout, stderr = "", ""
        return json.dumps({
            "exit_code": -1,
            "stdout": (stdout or "").strip(),
            "stderr": (stderr or "").strip(),
            "timed_out": True,
            "interrupted": False,
        }, ensure_ascii=False)




registry.register(
    name="run_cmd",
    description=(
        "Execute a shell command. STRICTLY for system-level operations that CANNOT be done with dedicated tools. "
        "Commands time out after 8m. Returns JSON with exit_code/stdout/stderr/timed_out/interrupted.\n\n"
        "ALLOWED (examples): 'python script.py' / 'pytest test_xxx.py' / 'pip install pkg' / "
        "'git add file && git commit -m msg' / 'gh pr create ...' / 'mkdir new_dir' / 'ls' / 'dir'.\n\n"
        "BLOCKED — file reading/writing: echo >, echo >>, cat, head, tail, sed, awk, type | . "
        "These MUST use dedicated tools instead: Read for reading, Write for creating, Edit for modifying, Grep for searching, Glob for listing files.\n\n"
        "BLOCKED — dangerous: shutdown, rm -rf, format, diskpart, del /s, reg delete, sudo, chmod 777, and any destructive system commands. "
        "Also BLOCKED: any command that attempts to bypass the blocked-file-ops restriction (e.g. 'python -c \"open(...).write(...)\"'). "
        "The security layer will reject these at runtime — do NOT retry with variations."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": (
                    "The shell command to execute. Use the native shell for the current OS (cmd.exe on Windows, bash on Unix).\n\n"
                    "ALLOWED: python/pytest/pip/git/gh/mkdir/ls/dir/cd/mv/cp/docker/npm/npx/git-lfs and similar system tools — "
                    "anything that genuinely needs a shell because no dedicated tool exists.\n\n"
                    "BLOCKED — use dedicated tools instead:\n"
                    "  echo ... > file  →  Use Write\n"
                    "  echo ... >> file →  Use Edit\n"
                    "  cat / head / tail →  Use Read\n"
                    "  sed / awk         →  Use Edit\n"
                    "  grep / rg         →  Use Grep\n"
                    "  find / ls pattern →  Use Glob\n"
                    "  type file | ...   →  Use Read\n\n"
                    "BLOCKED — dangerous (no dedicated tool to handle, MUST reject):\n"
                    "  shutdown, reboot, rm -rf, format, diskpart, del /s, reg delete, sudo, chmod 777, "
                    "dd, mkfs, and any command that could damage the system. Do NOT attempt these."
                ),
            },
            "run_background": {
                "type": "boolean",
                "description": (
                    "If true, run the command in the background and return immediately. "
                    "Use this for servers, daemons, or any command that runs indefinitely "
                    "(e.g., 'python server.py', 'npm run dev', 'uvicorn'). "
                    "Results will be injected when the command completes."
                ),
                "default": False
            },
        },
        "required": ["cmd"],
    },
    handler=run_cmd,
    group="core",
)


def background_status() -> str:
    """Query the status of all background tasks (both running and completed).

    Returns a human-readable report showing:
    - Running tasks: tool name, input parameters, task ID, start time
    - Pending tasks: completed tasks awaiting result injection
    - Reactivation count
    """
    from Agent.BackgroundTaskManager import BackgroundTaskManager
    return BackgroundTaskManager().get_detailed_status()


registry.register(
    name="background_status",
    description=(
        "Query the status of all background tasks — both actively running and "
        "completed-but-pending-injection. Returns a human-readable report with "
        "tool name, arguments, task ID, and start/elapsed time for each task. "
        "Use this to check what background work is in progress or awaiting "
        "result injection at the next step boundary."
    ),
    arguments_schema={
        "type": "object",
        "properties": {},
    },
    handler=background_status,
    group="core",
)


# 导入 system_info_tool 以注册 get_system_info 工具
try:
    import Tools.builtin.system_info_tool
except ImportError as e:
    print(f"Warning: Failed to import system_info_tool: {e}")
    pass


# ==================== 知识管理工具注册 ====================

registry.register(
    name="AddInsight",
    description="Add a research insight/innovation point to the knowledge base. Records core insights, methods, and experimental design ideas from papers for reuse.",
    arguments_schema={
        "type": "object",
        "properties": {
            "paper_id": {"type": "string", "description": "Paper ID (e.g. 'paper_001')."},
            "title": {"type": "string", "description": "Insight title, concise (e.g. 'Depth-wise connections improve representation')."},
            "description": {"type": "string", "description": "Detailed description including principle and implementation."},
            "impact": {"type": "string", "description": "Significance and contribution to the field."},
            "category": {
                "type": "string",
                "description": "Category: method / architecture / training / evaluation / general.",
                "enum": ["method", "architecture", "training", "evaluation", "general"]
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags for retrieval (optional)."
            },
            "related_papers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Related paper IDs (optional)."
            }
        },
        "required": ["paper_id", "title", "description", "impact"]
    },
    handler=lambda **kwargs: json.dumps(add_insight(**kwargs), ensure_ascii=False),
    group="knowledge"
)

registry.register(
    name="ReadInsight",
    description="Read all insights for a given paper. Returns JSON with insight data.",
    arguments_schema={
        "type": "object",
        "properties": {
            "paper_id": {"type": "string", "description": "Paper ID (e.g. 'paper_001')."}
        },
        "required": ["paper_id"]
    },
    handler=lambda paper_id: json.dumps(read_insight(paper_id), ensure_ascii=False),
    group="knowledge"
)

registry.register(
    name="ListInsights",
    description="List all insights, optionally filtered by category and tags.",
    arguments_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Filter by category: method/architecture/training/evaluation/general."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filter by tags (optional)."
            },
            "limit": {"type": "integer", "description": "Max results.", "default": 50}
        }
    },
    handler=lambda **kwargs: json.dumps(list_insights(**kwargs), ensure_ascii=False),
    group="knowledge"
)

registry.register(
    name="AddModule",
    description="Add a technical module to the knowledge base. Records reusable module implementations (only use when paper provides a GitHub link).",
    arguments_schema={
        "type": "object",
        "properties": {
            "paper_id": {"type": "string", "description": "Paper ID (e.g. 'paper_001')."},
            "module_name": {"type": "string", "description": "Module name (e.g. 'Depth-Connections')."},
            "category": {
                "type": "string",
                "description": "Category: Core_Mechanisms / Efficiency / Architecture.",
                "enum": ["Core_Mechanisms", "Efficiency", "Architecture"]
            },
            "principle": {"type": "string", "description": "Core principle in one sentence."},
            "description": {"type": "string", "description": "Detailed description."},
            "formula": {"type": "string", "description": "Key formula (optional)."},
            "complexity": {"type": "string", "description": "Time complexity (optional)."},
            "code_path": {"type": "string", "description": "Code path relative to module directory (optional)."},
            "github_url": {"type": "string", "description": "GitHub repo URL (optional)."},
            "use_cases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Application scenarios (optional)."
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dependent modules (optional)."
            }
        },
        "required": ["paper_id", "module_name", "category", "principle", "description"]
    },
    handler=lambda **kwargs: json.dumps(add_module(**kwargs), ensure_ascii=False),
    group="knowledge"
)

registry.register(
    name="ReadModule",
    description="Read module details for a paper. Returns JSON with module data.",
    arguments_schema={
        "type": "object",
        "properties": {
            "paper_id": {"type": "string", "description": "Paper ID (e.g. 'paper_001')."},
            "module_name": {"type": "string", "description": "Module name (e.g. 'Depth-Connections')."}
        },
        "required": ["paper_id", "module_name"]
    },
    handler=lambda paper_id, module_name: json.dumps(
        read_module(paper_id, module_name), ensure_ascii=False
    ),
    group="knowledge"
)

registry.register(
    name="ListModules",
    description="List all modules, optionally filtered by category or paper.",
    arguments_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Filter by category: Core_Mechanisms/Efficiency/Architecture."},
            "paper_id": {"type": "string", "description": "Filter by paper ID (optional)."},
            "limit": {"type": "integer", "description": "Max results.", "default": 50}
        }
    },
    handler=lambda **kwargs: json.dumps(list_modules(**kwargs), ensure_ascii=False),
    group="knowledge"
)
