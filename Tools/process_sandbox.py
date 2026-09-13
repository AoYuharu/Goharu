"""Windows Job Object 沙箱：为每条 run_cmd 命令套一层 OS 级进程能力边界。

职责（与权限系统分工）：
- 权限系统（Tools/security.py + SubAgent 黑名单）决定「这条命令要不要执行」
- 本模块决定「就算执行了，这个子进程最多能碰哪些系统能力」

非 Windows 平台为 no-op（Linux/macOS 需 bubblewrap/seccomp 之类机制，不在本模块范围）。

限制项（保守，避免误伤正常开发命令）：
- UI 限制：禁止访问父进程窗口句柄、读写剪贴板、创建/切换桌面、
  访问全局原子表、改系统参数/显示设置、关机注销
- 资源限制：
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE —— 最后一个 Job 句柄关闭时，内核杀光
  Job 内残留进程树（兜住「命令结束后留下孤儿进程」）
  JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION —— 未处理异常直接终止
"""

import ctypes
import logging
from ctypes import wintypes

from Tools.platform_utils import is_windows

logger = logging.getLogger(__name__)

# ---------------- Win32 常量 ----------------
CREATE_SUSPENDED = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004

# SetInformationJobObject 的 JobObjectInfoClass
JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9

# UI 限制位（JOBOBJECT_BASIC_UI_RESTRICTIONS.UIRestrictionsClass）
JOB_OBJECT_UILIMIT_HANDLES = 0x00000001
JOB_OBJECT_UILIMIT_READCLIPBOARD = 0x00000002
JOB_OBJECT_UILIMIT_WRITECLIPBOARD = 0x00000004
JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS = 0x00000008
JOB_OBJECT_UILIMIT_DISPLAYSETTINGS = 0x00000010
JOB_OBJECT_UILIMIT_GLOBALATOMS = 0x00000020
JOB_OBJECT_UILIMIT_DESKTOP = 0x00000040
JOB_OBJECT_UILIMIT_EXITWINDOWS = 0x00000080

_UI_LIMIT_FLAGS = (
    JOB_OBJECT_UILIMIT_HANDLES
    | JOB_OBJECT_UILIMIT_READCLIPBOARD
    | JOB_OBJECT_UILIMIT_WRITECLIPBOARD
    | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
    | JOB_OBJECT_UILIMIT_DISPLAYSETTINGS
    | JOB_OBJECT_UILIMIT_GLOBALATOMS
    | JOB_OBJECT_UILIMIT_DESKTOP
    | JOB_OBJECT_UILIMIT_EXITWINDOWS
)

# 资源限制位（JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags）
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

_LIMIT_FLAGS = (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ---------------- Win32 结构体 ----------------
class _JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
    ]


# ---------------- kernel32 绑定（仅 Windows） ----------------
_kernel32 = None


def _ensure_kernel32():
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    if not is_windows():
        return None
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE

    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    k.SetInformationJobObject.restype = wintypes.BOOL

    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL

    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL

    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
    k.Thread32First.restype = wintypes.BOOL

    k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
    k.Thread32Next.restype = wintypes.BOOL

    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenThread.restype = wintypes.HANDLE

    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD

    _kernel32 = k
    return k


def _create_job_object():
    """创建并配置一个 Job Object，返回句柄；失败返回 None。"""
    k = _ensure_kernel32()
    if k is None:
        return None

    handle = k.CreateJobObjectW(None, None)
    if not handle:
        logger.warning("Job Object 沙箱：CreateJobObjectW 失败 (err=%d)", ctypes.get_last_error())
        return None

    ui = _JOBOBJECT_BASIC_UI_RESTRICTIONS()
    ui.UIRestrictionsClass = _UI_LIMIT_FLAGS
    if not k.SetInformationJobObject(
        handle, JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui)
    ):
        logger.warning("Job Object 沙箱：设置 UI 限制失败 (err=%d)", ctypes.get_last_error())
        k.CloseHandle(handle)
        return None

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _LIMIT_FLAGS
    if not k.SetInformationJobObject(
        handle, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        logger.warning("Job Object 沙箱：设置资源限制失败 (err=%d)", ctypes.get_last_error())
        k.CloseHandle(handle)
        return None

    return handle


def _resume_process(pid: int) -> bool:
    """恢复挂起进程（CREATE_SUSPENDED）的所有线程。"""
    k = _ensure_kernel32()
    if k is None:
        return False

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE:
        return False

    resumed = False
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(_THREADENTRY32)
        if not k.Thread32First(snap, ctypes.byref(te)):
            return False
        while True:
            if te.th32OwnerProcessID == pid:
                h = k.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                if h:
                    k.ResumeThread(h)
                    k.CloseHandle(h)
                    resumed = True
            if not k.Thread32Next(snap, ctypes.byref(te)):
                break
    finally:
        k.CloseHandle(snap)

    return resumed


class CommandJob:
    """每条命令一个 Job Object；非 Windows 或创建失败时降级为 no-op。"""

    def __init__(self):
        self._handle = _create_job_object() if is_windows() else None

    @property
    def active(self) -> bool:
        return self._handle is not None

    @property
    def creation_flags(self) -> int:
        """需要额外 OR 进 Popen.creationflags 的位（Windows 为 CREATE_SUSPENDED）。"""
        return CREATE_SUSPENDED if self.active else 0

    def attach(self, popen) -> bool:
        """把已挂起子进程 assign 进 Job 并恢复；失败时 kill 进程并返回 False。"""
        if not self.active:
            return True  # 无沙箱环境，视为成功（普通执行）

        k = _ensure_kernel32()
        if k is None:
            return False
        try:
            handle = int(getattr(popen, "_handle", 0))
            if not handle:
                return False

            if not k.AssignProcessToJobObject(self._handle, handle):
                logger.error(
                    "Job Object 沙箱：AssignProcessToJobObject 失败 (err=%d)",
                    ctypes.get_last_error(),
                )
                popen.kill()
                return False

            if not _resume_process(popen.pid):
                logger.error("Job Object 沙箱：恢复挂起进程失败 (pid=%d)", popen.pid)
                popen.kill()
                return False

            logger.debug("Job Object 沙箱：命令进程已进入沙箱 (pid=%d)", popen.pid)
            return True
        except Exception as exc:
            logger.error("Job Object 沙箱：attach 异常 %s", exc)
            try:
                popen.kill()
            except Exception:
                pass
            return False

    def close(self):
        """关闭 Job 句柄；KILL_ON_JOB_CLOSE 会同步杀光 Job 内残留进程树。"""
        if self._handle is None:
            return
        k = _ensure_kernel32()
        if k is not None:
            k.CloseHandle(self._handle)
        self._handle = None
