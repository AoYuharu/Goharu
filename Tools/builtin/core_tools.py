import subprocess
import re

from Tools.registry import registry
from Tools.security import check_command_safety


def run_cmd(cmd: str) -> str:
    """
    Execute shell command with strict file operation restrictions and security checks.
    Blocks dangerous commands and guides to use dedicated tools.
    """
    cmd_lower = cmd.lower().strip()

    # 🔒 安全检查：拦截危险命令
    is_safe, error_message = check_command_safety(cmd)
    if not is_safe:
        return error_message

    # 检测 echo 命令用于文件操作
    # 匹配: echo ... > file, echo ... >> file
    if re.search(r'\becho\b.*?(?:>>?)\s*\S+', cmd_lower):
        return """ERROR: File operation with 'echo' is not allowed.

You attempted to use 'echo' for file operations, which is prohibited.

Please use the dedicated tools instead:
- To CREATE a new file: Use the Write tool
  Example: {"tool": "Write", "arguments": {"path": "file.txt", "content": "your content here"}}

- To MODIFY an existing file: Use Read + Edit tools
  Example:
  1. {"tool": "Read", "arguments": {"path": "file.txt"}}
  2. {"tool": "Edit", "arguments": {"path": "file.txt", "old_string": "old text", "new_string": "new text"}}

These tools are safer, provide better error handling, and ensure you know what you're changing.
DO NOT retry with run_cmd. Use the dedicated tools."""

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
            return f"""ERROR: Using '{cmd_name}' for file operations is not allowed.

Please use the dedicated tools instead:
- To READ a file: Use the Read tool
- To SEARCH in files: Use the Grep tool
- To MODIFY a file: Use Read + Edit tools

DO NOT retry with run_cmd. Use the dedicated tools."""

    # 执行命令
    output = subprocess.getoutput(cmd)
    return output if output.strip() else "(no output)"


def getKnowledge(query: str) -> str:
    return f"Knowledge about '{query}' is not implemented yet."


def makeClaudeSession(path: str) -> str:
    """
    在指定目录下打开 tmux 窗口并执行 happy 命令。

    当用户说"在某个目录下开始工作"时调用此工具。
    """
    import subprocess

    path = path.strip()
    if not path:
        return "Error: path cannot be empty"

    # 检查 tmux 是否可用
    tmux_check = subprocess.run(["which", "tmux"], capture_output=True)
    if tmux_check.returncode != 0:
        return "Error: tmux is not installed"

    # 检查目录是否存在
    import os
    if not os.path.isdir(path):
        return f"Error: directory '{path}' does not exist"

    # 获取绝对路径
    abs_path = os.path.abspath(path)

    # 构建命令：在指定目录打开 tmux 窗口并执行 happy
    # -c "cd to path" 确保在正确的目录
    # happy 命令需要在新的 shell 中执行以加载环境
    cmd = f'tmux new-window -c "{abs_path}" "happy"'

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return f"Success: Opened tmux session in '{abs_path}' and started happy"
        else:
            return f"Error: {result.stderr or result.stdout}"
    except Exception as e:
        return f"Error: {str(e)}"


def closeClaudeSession(window_name: str = None) -> str:
    """
    关闭 tmux 窗口。

    当用户说"关闭工作"、"结束工作"、"关闭窗口"等时调用此工具。
    如果指定 window_name 则关闭指定窗口，否则关闭当前窗口。
    """
    import subprocess

    if window_name:
        cmd = f'tmux kill-window -t "{window_name}"'
    else:
        cmd = 'tmux kill-window'

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            target = window_name or "current window"
            return f"Success: Closed tmux {target}"
        else:
            return f"Error: {result.stderr or result.stdout}"
    except Exception as e:
        return f"Error: {str(e)}"


registry.register(
    name="run_cmd",
    description="""Run shell command on Windows (cmd.exe).

⚠️ WARNING: This tool should be used SPARINGLY and ONLY for system operations.

🔒 SECURITY RESTRICTIONS:

1. DANGEROUS COMMANDS (BLOCKED):
   - System shutdown/restart: shutdown, reboot, poweroff
   - Destructive deletion: rm -rf, del /s /q, format
   - Disk operations: fdisk, diskpart, dd
   - Registry modifications: reg delete, reg add
   - Privilege escalation: sudo, runas
   - These commands will be REJECTED with a security error

2. FILE OPERATIONS (BLOCKED):
   - This tool will REJECT any echo, cat, head, tail, sed, awk commands
   - Attempting to use these commands will return an error message
   - You MUST use dedicated tools (Write, Read, Edit, Grep) for file operations
   - There are NO exceptions to this rule

3. CONFIRMATION REQUIRED (CONFIGURABLE):
   - Some commands may require explicit user confirmation
   - Check config.yaml tools.security settings for details

Why Use Dedicated Tools:
- Write tool: Creates new files safely (prevents accidental overwrites)
- Read tool: Reads files and grants Edit permission
- Edit tool: Patch-style editing (ensures you know what you're changing)
- Grep tool: Searches files efficiently
- These tools provide better error messages, safety checks, and user experience

Allowed Use Cases for run_cmd:
- System operations: dir, mkdir, copy, move (non-destructive)
- Check file existence: if exist file.txt echo exists
- Run scripts: python script.py, powershell -File script.ps1
- Process management: tasklist (read-only)
- Network operations: ping, ipconfig (read-only)

IMPORTANT - Windows Environment:
- This is a Windows system, use Windows command syntax
- DO NOT use Unix commands: ls, cat, printf, touch, rm, etc.
- DO NOT use heredoc syntax (<<EOF)

Before using run_cmd, ask yourself:
1. Can I use Write, Read, Edit, or Grep instead? (If yes, use them)
2. Is this a system operation that dedicated tools cannot handle? (If no, don't use run_cmd)
3. Am I trying to work around the file operation restrictions? (If yes, STOP and use dedicated tools)
4. Is this command potentially dangerous? (If yes, it will be blocked)""",
    arguments_schema={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Windows shell command (ONLY for safe system operations, NOT for file operations or dangerous commands)",
            },
        },
        "required": ["cmd"],
    },
    handler=run_cmd,
    group="core",
)

registry.register(
    name="getKnowledge",
    description="Get knowledge from local files (stub implementation)",
    arguments_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要检索的知识查询",
            },
        },
        "required": ["query"],
    },
    handler=getKnowledge,
    group="core",
)

registry.register(
    name="makeClaudeSession",
    description="""在指定目录下打开 tmux 窗口并执行 happy 命令。

当用户说"在某个目录下开始工作"、"我要开始工作了"、"开始工作吧"等类似表达时，调用此工具。

参数：
- path: 工作目录的绝对路径

示例调用：
{"tool": "makeClaudeSession", "arguments": {"path": "/root/TableHelper"}}""",
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "工作目录的绝对路径",
            },
        },
        "required": ["path"],
    },
    handler=makeClaudeSession,
    group="core",
)

registry.register(
    name="closeClaudeSession",
    description="""关闭 tmux 窗口。

当用户说"关闭工作"、"结束工作"、"关闭窗口"等类似表达时，调用此工具。

参数：
- window_name (可选): 要关闭的窗口名称，不指定则关闭当前窗口

示例调用：
{"tool": "closeClaudeSession", "arguments": {}}
{"tool": "closeClaudeSession", "arguments": {"window_name": "work"}}""",
    arguments_schema={
        "type": "object",
        "properties": {
            "window_name": {
                "type": "string",
                "description": "要关闭的 tmux 窗口名称（可选，不指定则关闭当前窗口）",
            },
        },
    },
    handler=closeClaudeSession,
    group="core",
)
