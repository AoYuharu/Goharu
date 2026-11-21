import subprocess
import re
import json
import threading

from Tools.registry import registry
from Tools.security import check_command_safety
from Tools.cmd_translate import translate_command
from Tools.tool_process_tracker import tool_process_tracker
from Tools.tool_output_stream import tool_output_bus
from Tools.process_sandbox import CommandJob
from Tools.platform_utils import (
    get_subprocess_env,
    get_subprocess_creation_flags,
    is_windows,
    kill_process_tree,
    resolve_bash_path,
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


# 前台命令硬超时（秒）。已后台化的命令不受此限（见 run_cmd 等待循环）。
FOREGROUND_CMD_TIMEOUT_S = 480


async def run_cmd(cmd: str, description: str = "") -> str:
    """
    Execute shell command with strict file operation restrictions and security checks.
    Blocks dangerous commands; require_confirmation 命令弹窗询问用户后才执行。
    """
    import asyncio
    import time
    from Tools.security import get_security_checker
    from Tools.confirmation_manager import confirmation_manager
    cmd_lower = cmd.lower().strip()

    # 🔒 安全检查：三态分类
    safety = get_security_checker().classify_command(cmd)
    if safety == "blocked":
        is_safe, error_message = check_command_safety(cmd)
        return json.dumps({"error": error_message or "Command blocked by security policy"}, ensure_ascii=False)
    if safety == "confirm":
        # 需要用户确认：阻塞本工具线程，等待 TUI 用户选择 Yes/No
        desc = description.strip() or f"需要执行可能具有破坏性的命令: {cmd}"
        approved = confirmation_manager.request(cmd, desc)
        if not approved:
            return json.dumps({
                "error": "用户拒绝了该命令的执行 (user denied confirmation)",
                "user_denied": True,
            }, ensure_ascii=False)
        # 用户批准 → 继续执行

    # 检测 echo 命令用于文件操作：echo ... > file / >> file
    # 三个精度要求（2026-10-16 误拦事故：echo 当分隔符 + 2>/dev/null 被误判成写文件）：
    # 1) echo 与重定向必须同属一个简单命令（不跨 ; | && 边界）——
    #    `ls 2>/dev/null; echo "---"; find 2>/dev/null` 里 echo 只是输出分隔符
    # 2) > /dev/null 是丢弃输出不是写文件，放行
    # 3) 先剥掉引号段，防止引号内的 "echo >" 字样误报
    _cmd_stripped = re.sub(r'"[^"]*"|\'[^\']*\'', '', cmd_lower)
    if re.search(r'\becho\b[^;|&]*(?:>>?)\s*(?!/dev/null\b)\S+', _cmd_stripped):
        return json.dumps({"error": """File operation with 'echo' is not allowed.

You attempted to use 'echo' for file operations, which is prohibited.

Please use the dedicated tools instead:
- To CREATE a new file: Use the Write tool
  Example: {"tool": "Write", "arguments": {"path": "file.txt", "content": "your content here"}}

- To MODIFY an existing file: Use Read + Edit tools
  Example:
  1. {"tool": "Read", "arguments": {"path": "file.txt"}}
  2. {"tool": "Edit", "arguments": {"path": "file.txt", "old_string": "old text", "new_string": "new text"}}

(Note: `echo ... > /dev/null`, and echo used as an output separator between sub-commands, are allowed.)

These tools are safer, provide better error handling, and ensure you know what you're changing.
DO NOT retry with run_cmd. Use the dedicated tools."""}, ensure_ascii=False)

    # 读侧文本流命令（cat/head/tail/sed/awk/type|、管道组合）放行：限制的核心
    # 目的是控制工具结果体量，超长输出由 ToolResultBudget 截断兜底，短输出
    # 直接查看无需拒绝。仍保留写侧拦截：shell 改写文件必须走 Edit 工具。
    forbidden_patterns = [
        (r'\bsed\b[^|;&]*\s(-i[^ \t-]|-i\b|--in-place)', 'sed in-place edit (-i/--in-place)'),
    ]

    for pattern, cmd_name in forbidden_patterns:
        if re.search(pattern, cmd_lower):
            return json.dumps({"error": f"""Using '{cmd_name}' is not allowed — it modifies files in place.

File modifications MUST go through the Edit tool (Read then Edit), so changes are
diffable and permission-checked.

DO NOT retry with run_cmd. Use Read + Edit instead."""}, ensure_ascii=False)

    # 🌐 跨平台转译：异系统命令（如 Windows 上的 ls）自动转为本机等价命令
    translated_cmd, translated_from = translate_command(cmd)
    if translated_from:
        # 转译结果也要过安全检查（如 rm 转译后可能是 del）
        is_safe, error_message = check_command_safety(translated_cmd)
        if not is_safe:
            return json.dumps({"error": error_message}, ensure_ascii=False)

    # 执行命令（带超时，默认30s；平台适配的进程组确保超时能杀死子进程树）
    env = get_subprocess_env()
    sandbox = CommandJob()
    # Windows: 统一走 Git Bash——bash 才兼容多行 `python -c`、heredoc 与标准 POSIX 工具；
    # cmd.exe 会 mangling 引号/换行（多行 python -c 静默空输出）。env 中 PYTHONUTF8=1 强制
    # Python 子进程用 UTF-8，避免中文乱码。
    if is_windows():
        try:
            bash = resolve_bash_path()
        except RuntimeError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        popen_args = [bash, "-c", translated_cmd]
        use_shell = False
    else:
        popen_args = translated_cmd
        use_shell = True
    try:
        p = subprocess.Popen(
            popen_args,
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            encoding='utf-8',
            errors='replace',
            bufsize=1,  # 行缓冲，实时流式输出
            env=env,
            creationflags=get_subprocess_creation_flags() | sandbox.creation_flags,
            # POSIX 下必须让子进程自立进程组：否则 kill_process_tree 的
            # killpg(getpgid(pid)) 会把 agent 自己所在的进程组一起杀掉
            start_new_session=not is_windows(),
        )
        # 显式后台化的任务不进 ESC 可杀名单——后台任务按设计可长期运行，
        # 用户想停用 TUI 的 [x]（cancel_task 精确点杀）；ESC 只杀前台步骤
        # （2026-10-15 事故：ESC 无差别 kill_all 屠杀了合法后台 du 任务）
        _tracked_now = False
        try:
            from Tools.background_tasks import BackgroundTaskManager
            _tracked_now = BackgroundTaskManager().is_tracked(asyncio.current_task())
        except Exception:
            _tracked_now = False
        if not _tracked_now:
            tool_process_tracker.register(p.pid)
        if not sandbox.attach(p):
            tool_process_tracker.unregister(p.pid)
            sandbox.close()
            return json.dumps({
                "error": "命令进程未能进入安全沙箱，已终止",
            }, ensure_ascii=False)
        translated_meta = {"translated_from": translated_from} if translated_from else {}

        # 逐行读取 stdout/stderr 并实时广播（让 TUI 能看到安装/编译等长命令的实时输出）
        out_lines, err_lines = [], []
        def _reader(stream, sink, tag):
            try:
                for line in iter(stream.readline, ''):
                    sink.append(line)
                    tool_output_bus.publish(line.rstrip('\n'), tag, cmd)
            except Exception:
                pass
        t_out = threading.Thread(target=_reader, args=(p.stdout, out_lines, 'stdout'), daemon=True)
        t_err = threading.Thread(target=_reader, args=(p.stderr, err_lines, 'stderr'), daemon=True)
        t_out.start()
        t_err.start()

        def _collect():
            out = "".join(out_lines).strip()
            err = "".join(err_lines).strip()
            return out, err

        try:
            # 前台硬超时 FOREGROUND_CMD_TIMEOUT_S；一旦任务被转入后台
            #（显式 run_background 或超时自动提升），进程豁免该超时——
            # 服务器/守护进程不应被定时器杀死（否则会出现"宣布启动成功后
            # 进程悄悄被杀"的事故）。
            from Tools.background_tasks import BackgroundTaskManager
            deadline = time.monotonic() + FOREGROUND_CMD_TIMEOUT_S
            while True:
                tracked = BackgroundTaskManager().is_tracked(asyncio.current_task())
                if tracked:
                    # 超时自动转后台的瞬间（此刻才被 track），从 ESC 可杀名单移出
                    tool_process_tracker.unregister(p.pid)
                slice_s = 30.0 if tracked else max(0.1, min(30.0, deadline - time.monotonic()))
                try:
                    await asyncio.to_thread(p.wait, slice_s)
                    break
                except subprocess.TimeoutExpired:
                    if tracked:
                        continue
                    if time.monotonic() >= deadline:
                        raise
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            stdout, stderr = _collect()
            return json.dumps({
                "exit_code": p.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "interrupted": False,
                **translated_meta,
            }, ensure_ascii=False)
        except asyncio.CancelledError:
            kill_process_tree(p.pid)
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            stdout, stderr = _collect()
            return json.dumps({
                "exit_code": -1,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "interrupted": True,
            }, ensure_ascii=False)
        finally:
            tool_process_tracker.unregister(p.pid)
            sandbox.close()
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
        "Foreground commands time out after 8m; backgrounded commands (run_background=true or auto-promoted) are NOT killed by timeout and may run indefinitely. "
        "Returns JSON with exit_code/stdout/stderr/timed_out/interrupted.\n\n"
        "ALLOWED (examples): 'python script.py' / 'pytest test_xxx.py' / 'pip install pkg' / "
        "'git add file && git commit -m msg' / 'gh pr create ...' / 'mkdir new_dir' / 'ls' / 'dir'.\n\n"
        "BLOCKED — shell file writes: echo > / >> (file creation via echo), sed -i / --in-place. "
        "File changes MUST use dedicated tools: Write for creating, Edit for modifying (requires prior Read).\n\n"
        "ALLOWED — read-side text commands: cat, head, tail, grep, sed (filter), awk, sort, wc and pipes are fine. "
        "Prefer Read/Grep/Glob for whole-file reads and searches; oversized command output is auto-truncated.\n\n"
        "BLOCKED — dangerous: shutdown, rm -rf, format, diskpart, del /s, reg delete, sudo, chmod 777, and any destructive system commands. "
        "Also BLOCKED: any command that attempts to bypass the blocked-file-ops restriction (e.g. 'python -c \"open(...).write(...)\"'). "
        "The security layer will reject these at runtime — do NOT retry with variations.\n\n"
        "PowerShell is NOT available — do NOT use powershell/pwsh. "
        "The shell is bash (Git Bash on Windows), NOT cmd.exe — use POSIX syntax: ls/cp/mv/rm/grep/find/... "
        "Residual Windows cmd commands (dir/copy/del/set/...) are auto-translated to their POSIX equivalents."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": (
                    "The shell command to execute. The shell is bash (Git Bash on Windows), NOT cmd.exe — "
                    "use POSIX syntax (ls/cp/mv/rm/grep/find/...). Windows cmd commands (dir/copy/del/...) are auto-translated. "
                    "PowerShell is NOT available.\n\n"
                    "ALLOWED: python/pytest/pip/git/gh/mkdir/ls/dir/cd/mv/cp/docker/npm/npx/git-lfs and similar system tools — "
                    "anything that genuinely needs a shell because no dedicated tool exists.\n\n"
                    "BLOCKED — shell file writes (use dedicated tools):\n"
                    "  echo ... > file    →  Use Write\n"
                    "  echo ... >> file   →  Use Edit\n"
                    "  sed -i / --in-place →  Use Read + Edit\n\n"
                    "ALLOWED — read-side text commands (output auto-truncated when too long):\n"
                    "  cat / head / tail, grep / rg, sed (filter), awk, sort, wc, pipes (|)\n"
                    "  For whole-file reads prefer Read; for searches prefer Grep/Glob.\n\n"
                    "BLOCKED — dangerous (no dedicated tool to handle, MUST reject):\n"
                    "  shutdown, reboot, rm -rf, format, diskpart, del /s, reg delete, sudo, chmod 777, "
                    "dd, mkfs, and any command that could damage the system. Do NOT attempt these.\n\n"
                    "IMPORTANT — multi-line / complex Python logic:\n"
                    "  bash now handles multi-line `python -c \"...\"` correctly, but to avoid quoting pitfalls "
                    "prefer a heredoc (`python <<'EOF' ... EOF`) or Write a temporary .py script "
                    "(e.g. tmp_xxx.py) and run `python tmp_xxx.py`, then read its output."
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
            "description": {
                "type": "string",
                "description": (
                    "REQUIRED when running a risky/destructive command (del/rmdir/taskkill/reg/rm etc.): "
                    "one short sentence explaining WHY this command is needed and what it will affect. "
                    "Shown to the user in the confirmation prompt."
                ),
                "default": ""
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
    from Tools.background_tasks import BackgroundTaskManager
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
