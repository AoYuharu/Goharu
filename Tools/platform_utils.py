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
import locale
from typing import Tuple


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


def get_subprocess_silent_flags() -> int:
    """
    返回静默创建进程的标记（无窗口）。

    Windows: CREATE_NO_WINDOW
    Linux/macOS: 0
    """
    if is_windows():
        try:
            return subprocess.CREATE_NO_WINDOW
        except AttributeError:
            return 0
    return 0


# ==================== 编码 ====================

def get_system_encoding() -> str:
    """返回平台合适的系统编码"""
    encoding = locale.getpreferredencoding(do_setlocale=False)
    return encoding or ('utf-8')


def ascii_filename(filename: str) -> str:
    """
    返回去除 emoji 等特殊字符的安全文件名。
    适用于 Windows 不兼容的字符场景。
    """
    result: list[str] = []
    for ch in filename:
        if ord(ch) < 128:
            result.append(ch)
        elif ch.isascii():
            result.append(ch)
        else:
            result.append('_')
    safe = ''.join(result).strip()
    return safe or 'untitled'


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


def get_effective_shell() -> Tuple[str, bool]:
    """
    返回执行 shell 命令所需的信息。
    返回 (executable, use_shell)：
      Linux/macOS: (None, True) — 使用 /bin/sh
      Windows: (None, True) — 使用 cmd.exe
    """
    # subprocess 的 shell=True 会自动选择当前平台的 shell:
    #  Windows → cmd.exe, Unix → /bin/sh
    return (None, True)


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
