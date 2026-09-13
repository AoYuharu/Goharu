"""
平台抽象工具模块

使用策略模式封装所有平台相关的操作，对外提供统一接口。
文件中每个函数对应一个平台差异点，所有位置通过这个模块调用，
而非直接内联 sys.platform 检查。
"""

import os
import sys
import signal
import subprocess


# ==================== 平台检测 ====================

def is_windows() -> bool:
    return sys.platform == 'win32'


def is_linux() -> bool:
    return sys.platform == 'linux'


def is_macos() -> bool:
    return sys.platform == 'darwin'


def get_platform_name() -> str:
    """返回人类可读的平台名，可用于日志和协议字段"""
    if is_windows():
        return 'Windows'
    elif is_macos():
        return 'macOS'
    elif is_linux():
        return 'Linux'
    return sys.platform


def get_environment_hint(for_subagent: bool = False) -> str:
    """生成注入 Agent 系统提示词的当前运行环境说明块。

    让模型直接知道自己在什么系统上该用什么命令，
    转译层（cmd_translate）只是兜底，不替代本机命令直觉。
    """
    import platform

    os_name = f"{get_platform_name()} ({platform.system()} {platform.release()})"
    cwd = os.getcwd()
    if is_windows():
        lines = [
            f"- 操作系统: {os_name}，Shell: bash（Git Bash，不是 cmd.exe）",
            f"- 当前工作目录: {cwd}",
            "- 路径: Windows 盘符；bash 下可用 POSIX 风格路径（如 /e/Goharu）或 E:/Goharu；Python 命令为 python（通常无 python3）",
            "- 常用命令: ls/cp/mv/rm/grep/which/ps 等标准 POSIX 工具（bash 原生支持，无需翻译）",
            "- 多行 Python 脚本请用 heredoc（python <<'EOF' ... EOF）或写临时 .py 文件，避免复杂的 python -c 引号转义",
        ]
        if for_subagent:
            lines.append(
                "- 你只允许只读命令（ls/wc/sort/git 只读子命令等），可附带 2>/dev/null；"
                "powershell 不可用；读写/搜索文件一律用 Read/Edit/Grep/Glob 工具"
            )
    else:
        shell = os.environ.get("SHELL", "/bin/sh")
        lines = [
            f"- 操作系统: {os_name}，Shell: {shell}",
            f"- 当前工作目录: {cwd}",
            "- 常用命令: ls/cp/mv/rm/grep/which/ps 等标准 POSIX 工具",
        ]
        if for_subagent:
            lines.append(
                "- 你只允许只读命令（ls/wc/sort/git 只读子命令等），可附带 2>/dev/null；"
                "读写/搜索文件一律用 Read/Edit/Grep/Glob 工具"
            )
    return "## 运行环境（当前机器）\n\n" + "\n".join(lines)


def resolve_bash_path() -> str:
    """返回用于执行 run_cmd 的 shell 路径。

    Windows 上返回 Git Bash 的 bash.exe 完整路径（bash 才是模型命令的稳定执行环境，
    cmd.exe 的引号/换行规则与 python -c 等多行命令不兼容）；POSIX 返回 'bash'。

    找不到 Git Bash 时抛 RuntimeError（等价于 Claude Code 在 Windows 上对 git-bash 的硬依赖）。
    """
    if not is_windows():
        return "bash"
    for env_name in ("GOHARU_BASH_PATH", "CLAUDE_CODE_GIT_BASH_PATH"):
        p = os.environ.get(env_name)
        if p and os.path.isfile(p):
            return p
    candidates = [
        r"D:\Git\bin\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        candidates.append(os.path.join(localappdata, r"Programs\Git\bin\bash.exe"))
    for p in candidates:
        if os.path.isfile(p):
            return p
    import shutil
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    raise RuntimeError(
        "Git Bash 未找到。请安装 Git for Windows（https://git-scm.com/downloads/win），"
        "或设置环境变量 GOHARU_BASH_PATH 指向 bash.exe"
    )


# ==================== 进程管理 ====================

def kill_process_tree(pid: int, force: bool = True) -> bool:
    """
    跨平台强杀进程及其子进程树。
    返回是否成功。

    Windows: 使用 taskkill /F /T /PID
    Linux/macOS: 先 kill SIGKILL，再等待收割
    """
    try:
        if is_windows():
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(pid)],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return True
        else:
            # Unix: 尝试以进程组方式杀死
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # 可能进程组不存在，直接杀单个进程
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            return True
    except Exception:
        return False


def kill_pid_list(pids: list) -> None:
    """批量杀死进程列表中的所有 PID"""
    for pid in pids:
        kill_process_tree(pid)


# ==================== 子进程创建标记 ====================

def get_subprocess_creation_flags() -> int:
    """
    返回 subprocess.Popen 需要的 platform-appropriate creationflags。

    Windows: CREATE_NEW_PROCESS_GROUP（允许 taskkill 子进程树）
    Linux/macOS: 0
    """
    if is_windows():
        try:
            return subprocess.CREATE_NEW_PROCESS_GROUP
        except AttributeError:
            return 0
    return 0


# ==================== subprocess 环境 ====================

def get_subprocess_env() -> dict:
    """返回 subprocess 使用的环境变量，包含跨平台通用的必要设置"""
    env = os.environ.copy()
    # PYTHONUNBUFFERED 强制 Python 子进程无缓冲输出
    env['PYTHONUNBUFFERED'] = '1'
    # 强制 Python 子进程使用 UTF-8 输出，避免 Windows 上 cp936 / UTF-8 编码不一致导致中文乱码
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


# ==================== 系统信息收集 ====================

def collect_system_info(target: str = "all") -> dict:
    """
    跨平台收集系统信息，返回字典。
    支持: all / cpu / gpu / memory
    """
    results: dict = {}

    if target in ("all", "cpu"):
        results["cpu"] = _get_cpu_info()

    if target in ("all", "gpu"):
        results["gpu"] = _get_gpu_info()

    if target in ("all", "memory"):
        results["memory"] = _get_memory_info()

    return results


def _run_cmd_output(cmd: str, timeout: float = 10) -> str:
    """Run a shell command and return stdout as string, or empty string on failure."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, errors='replace'
        )
        return r.stdout.strip() or r.stderr.strip()
    except Exception:
        return ""


def _get_cpu_info() -> str:
    """跨平台获取 CPU 信息"""
    if is_windows():
        output = _run_cmd_output(
            "wmic cpu get Name,NumberOfCores,MaxClockSpeed /format:list"
        )
        if output:
            return output

    # Linux / macOS
    try:
        with open('/proc/cpuinfo', 'r') as f:
            lines = f.readlines()
        info_parts = [line.strip() for line in lines
                      if any(k in line for k in ('model name', 'cpu cores', 'siblings', 'MHz'))]
        if info_parts:
            return '\n'.join(info_parts)
    except Exception:
        pass

    # macOS fallback
    output = _run_cmd_output("sysctl -n machdep.cpu.brand_string")
    return output if output else "CPU info unavailable"


def _get_gpu_info() -> str:
    """跨平台获取 GPU 信息"""
    # nvidia-smi 在所有平台都可使用（如果安装了 NVIDIA 驱动）
    output = _run_cmd_output("nvidia-smi -L")
    if output and ("GPU" in output or "NVIDIA" in output):
        return output

    # Linux fallback: lspci
    if not is_windows():
        output = _run_cmd_output("lspci | grep -i vga")
        if output and "grep" not in output.lower():
            return output

    # macOS fallback
    if is_macos():
        output = _run_cmd_output("system_profiler SPDisplaysDataType | grep Chipset")
        if output:
            return output

    return "No GPU found"


def _get_memory_info() -> str:
    """跨平台获取内存信息"""
    if is_windows():
        output = _run_cmd_output('systeminfo | findstr /C:"Total Physical Memory"')
        if output:
            return output

    # Linux: /proc/meminfo
    if is_linux():
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        return line.strip()
        except Exception:
            pass

    # macOS
    if is_macos():
        output = _run_cmd_output("sysctl hw.memsize")
        if output:
            return output

    return "Memory info unavailable"


# ==================== stdout 初始化 ====================

def setup_stdio_encoding():
    """在必要时设置 stdout/stderr 的 UTF-8 编码"""
    if is_windows():
        import codecs
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
        return old_stdout, old_stderr
    return sys.stdout, sys.stderr
