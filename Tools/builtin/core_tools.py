import subprocess
import re
import json

from Tools.registry import registry
from Tools.security import check_command_safety
from Tools.tool_process_tracker import tool_process_tracker
from Tools.platform_utils import (
    get_subprocess_env,
    get_system_encoding,
    get_subprocess_creation_flags,
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

    # 执行命令（带超时，默认30s；平台适配的进程组确保超时能杀死子进程树）
    env = get_subprocess_env()
    sys_encoding = get_system_encoding()
    try:
        p = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            encoding=sys_encoding,
            errors='replace',
            env=env,
            creationflags=get_subprocess_creation_flags(),
        )
        tool_process_tracker.register(p.pid)
        try:
            stdout, stderr = await asyncio.to_thread(p.communicate, timeout=30)
            output = (stdout or "").strip()
            if not output:
                output = (stderr or "").strip()
            return output if output else "(no output)"
        except asyncio.CancelledError:
            kill_process_tree(p.pid)
            raise
        finally:
            tool_process_tracker.unregister(p.pid)
    except subprocess.TimeoutExpired:
        kill_process_tree(p.pid)
        try:
            stdout, stderr = await asyncio.to_thread(p.communicate, timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            await asyncio.to_thread(p.communicate)
        partial = ((stdout or "") + (stderr or "")).strip()
        msg = "ERROR: Command timed out after 30s."
        if partial:
            msg += f"\nPartial output:\n{partial}"
        return msg




registry.register(
    name="run_cmd",
    description="Execute a shell command. ONLY for system operations that dedicated file tools (Write/Read/Edit/Grep) cannot handle. Use the native shell for the current platform.",
    arguments_schema={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Shell command. Use the native shell for the current OS. Examples (cross-platform): 'python script.py', 'pip install requests', 'mkdir new_folder'. Use 'ls' / 'dir' depending on platform. BLOCKED: shutdown, rm -rf, format, diskpart, del /s, reg delete, sudo. BLOCKED file ops: echo >, cat, sed, awk, type | (use Write/Read/Edit/Grep instead).",
            },
        },
        "required": ["cmd"],
    },
    handler=run_cmd,
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
