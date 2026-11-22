"""系统探测层 —— 裸机环境信息采集（哑代码：纯 stdlib + 命令正则，零 LLM）。

设计：每个探测函数返回 dict，失败字段记 None；汇总时 None 字段进 gaps
清单，交给 Arch 智能体补漏。跨 Windows/Linux，所有命令调用带超时，
任何异常不穿透。

字段路径即 identity.yaml 的结构：
  device / os / cpu / memory / gpu / storage / network / runtime /
  services / peripherals / security / status
"""

import logging
import os
import platform
import re
import shutil
import socket
import subprocess

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
_CMD_TIMEOUT = 8


def _run(cmd, timeout=_CMD_TIMEOUT):
    """跑命令返回 stdout 文本；失败返回 None。命令不存在/超时都归一。

    编码链：先 utf-8，失败回退 gbk（Windows 中文系统的系统工具输出是 GBK，
    直接 utf-8 解会得到乱码——2026-11-22 实测 os.name/interfaces 翻车）。
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            return None
        raw = proc.stdout or b""
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _which(name):
    return shutil.which(name)


def _ps(script, timeout=_CMD_TIMEOUT):
    """Windows PowerShell 探测（-NoProfile 加速）；结果去首尾空白。"""
    if not IS_WINDOWS:
        return None
    out = _run(["powershell", "-NoProfile", "-Command", script], timeout)
    return out.strip() if out else None


def _re1(pattern, text, flags=0):
    """正则取第一捕获组，不中返回 None。"""
    if not text:
        return None
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


# ----------------------------------------------------------------------
# 1. 设备身份
# ----------------------------------------------------------------------

def probe_device() -> dict:
    return {
        "hostname": platform.node() or None,
        "arch": platform.machine() or None,          # x86_64 / AMD64 / ARM64
        "device_type": _device_type(),
    }


def _device_type():
    if IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_SystemEnclosure).ChassisTypes -join ','")
        if out:
            # 9/10/14 = laptop 系；3/4/5/6/7 = desktop 系
            if re.search(r"\b(8|9|10|11|14|30|31|32)\b", out):
                return "laptop"
            return "desktop"
        return None
    out = _run(["cat", "/sys/class/dmi/id/chassis_type"])
    if out:
        t = out.strip()
        return "laptop" if t in ("8", "9", "10", "11", "14", "30", "31", "32") else "desktop"
    return None


# ----------------------------------------------------------------------
# 2. 操作系统
# ----------------------------------------------------------------------

def probe_os() -> dict:
    if IS_WINDOWS:
        caption = _ps("(Get-CimInstance Win32_OperatingSystem).Caption")
        build = _ps("(Get-CimInstance Win32_OperatingSystem).BuildNumber")
        return {
            "name": caption,
            "version": platform.version() or None,
            "kernel": build,                        # Windows 用 build 当内核等价物
        }
    name = None
    os_release = _run(["cat", "/etc/os-release"])
    if os_release:
        name = _re1(r'PRETTY_NAME="([^"]+)"', os_release)
    return {
        "name": name or platform.system(),
        "version": platform.release() or None,
        "kernel": platform.release() or None,
    }


# ----------------------------------------------------------------------
# 3. CPU
# ----------------------------------------------------------------------

def probe_cpu() -> dict:
    model = None
    flags = None
    if IS_WINDOWS:
        model = _ps("(Get-CimInstance Win32_Processor).Name")
        # 指令集：Windows 无可靠轻量途径，留 gap 给 Arch
    else:
        cpuinfo = _run(["cat", "/proc/cpuinfo"])
        if cpuinfo:
            model = _re1(r"model name\s*:\s*(.+)", cpuinfo)
            fl = _re1(r"flags\s*:\s*(.+)", cpuinfo)
            if fl:
                flags = [f for f in ("avx2", "avx512f", "neon", "sve")
                         if f in fl.split()]
    return {
        "model": model.strip() if model else None,
        "cores_physical": os.cpu_count(),           # 逻辑核数（物理核另探）
        "threads": os.cpu_count(),
        "instruction_sets": flags,                  # 常见指令集子集；None=未探到
    }


# ----------------------------------------------------------------------
# 4. 内存
# ----------------------------------------------------------------------

def probe_memory() -> dict:
    if IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        total = int(out.strip()) if out and out.strip().isdigit() else None
        avail = _ps("(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory")
        swap = _ps("(Get-CimInstance Win32_PageFileUsage).AllocatedBaseSize")
        return {
            "total_bytes": total,
            "available_bytes": (int(avail.strip()) * 1024
                                if avail and avail.strip().isdigit() else None),
            "swap_mb": (int(swap.strip())
                        if swap and swap.strip().isdigit() else None),
        }
    meminfo = _run(["cat", "/proc/meminfo"])
    if not meminfo:
        return {"total_bytes": None, "available_bytes": None, "swap_mb": None}
    total_kb = _re1(r"MemTotal:\s*(\d+)", meminfo)
    avail_kb = _re1(r"MemAvailable:\s*(\d+)", meminfo)
    swap_kb = _re1(r"SwapTotal:\s*(\d+)", meminfo)
    return {
        "total_bytes": int(total_kb) * 1024 if total_kb else None,
        "available_bytes": int(avail_kb) * 1024 if avail_kb else None,
        "swap_mb": int(swap_kb) // 1024 if swap_kb else None,
    }


# ----------------------------------------------------------------------
# 5. GPU
# ----------------------------------------------------------------------

def probe_gpu() -> dict:
    nvidia = _which("nvidia-smi")
    if nvidia:
        out = _run([nvidia, "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader"])
        if out:
            gpus = []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpus.append({"model": parts[0], "vram": parts[1],
                                 "driver": parts[2]})
            return {"count": len(gpus), "gpus": gpus,
                    "driver": gpus[0]["driver"] if gpus else None}
    if IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_VideoController) | "
                  "Select-Object -ExpandProperty Name")
        if out:
            names = [l.strip() for l in out.strip().splitlines() if l.strip()]
            return {"count": len(names),
                    "gpus": [{"model": n} for n in names], "driver": None}
        return {"count": 0, "gpus": [], "driver": None}
    out = _run(["lspci"]) if _which("lspci") else None
    if out:
        vgas = [l for l in out.splitlines() if re.search(r"VGA|3D controller", l)]
        return {"count": len(vgas),
                "gpus": [{"model": _re1(r":\s*(.+)$", v)} for v in vgas],
                "driver": None}
    return {"count": None, "gpus": [], "driver": None}


# ----------------------------------------------------------------------
# 6. 存储
# ----------------------------------------------------------------------

def probe_storage() -> dict:
    disks = []
    if IS_WINDOWS:
        out = _ps("Get-PSDrive -PSProvider FileSystem | "
                  "Select-Object Name,Used,Free | ConvertTo-Csv -NoTypeInformation")
        if out:
            for line in out.strip().splitlines()[1:]:
                m = re.match(r'"?(\w+)"?,"?(\d*)"?,.*?"(\d+)"?', line)
                if m:
                    root = f"{m.group(1)}:\\"
                    usage = shutil.disk_usage(root)
                    disks.append({"mount": root, "total_bytes": usage.total,
                                  "free_bytes": usage.free, "fstype": None})
    else:
        for mount in ("/", "/home", "/data"):
            if os.path.isdir(mount):
                try:
                    usage = shutil.disk_usage(mount)
                except OSError:
                    continue
                disks.append({"mount": mount, "total_bytes": usage.total,
                              "free_bytes": usage.free, "fstype": None})
        lsblk = _run(["lsblk", "-f"]) if _which("lsblk") else None
        if lsblk:
            for d in disks:
                m = re.search(rf"\S+\s+\S+\s+(\S+).*{re.escape(d['mount'])}\s*$",
                              lsblk, re.M)
                if m:
                    d["fstype"] = m.group(1)
    return {"disks": disks}


# ----------------------------------------------------------------------
# 7. 网络
# ----------------------------------------------------------------------

def probe_network() -> dict:
    result = {"hostname_ip": None, "interfaces": [], "dns": None,
              "gateway": None, "internet": None}
    try:
        result["hostname_ip"] = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass
    if IS_WINDOWS:
        out = _run(["ipconfig", "/all"], timeout=15)
        if out:
            # 中/英 Windows 字段名都匹配（"DNS 服务器" / "DNS Servers"）
            result["dns"] = re.findall(r"DNS[^\n:：]*[:：]\s*([\d.]+)", out)
            result["gateway"] = _re1(
                r"(?:Default Gateway|默认网关)[^\n:：]*[:：]\s*([\d.]+)", out)
            result["interfaces"] = re.findall(
                r"(?:adapter|适配器)\s+([^:]+):", out)[:8]
    else:
        out = _run(["ip", "-brief", "addr"]) if _which("ip") else None
        if out:
            result["interfaces"] = [
                {"name": l.split()[0],
                 "ip": (_re1(r"(\d+\.\d+\.\d+\.\d+/\d+)", l) or "").split("/")[0],
                 "up": "UP" in l}
                for l in out.splitlines() if l.strip()]
        route = _run(["ip", "route", "show", "default"]) if _which("ip") else None
        if route:
            result["gateway"] = _re1(r"default via\s+([\d.]+)", route)
        resolv = _run(["cat", "/etc/resolv.conf"])
        if resolv:
            result["dns"] = re.findall(r"nameserver\s+([\d.]+)", resolv)
    result["internet"] = _check_internet()
    return result


def _check_internet():
    try:
        socket.create_connection(("223.5.5.5", 53), timeout=3).close()
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# 8. 运行环境
# ----------------------------------------------------------------------

def probe_runtime() -> dict:
    def _ver(cmd, pattern):
        if not _which(cmd[0]):
            return None
        out = _run(cmd)
        v = _re1(pattern, out) if out else None
        return v.rstrip(".") if v else None

    return {
        "python": platform.python_version(),
        "node": _ver(["node", "--version"], r"v?([\d.]+)"),
        "java": _ver(["java", "-version"], r'version "([^"]+)"'),
        "docker": _ver(["docker", "--version"], r"version ([\d.]+)"),
        "git": _ver(["git", "--version"], r"version ([\d.]+)"),
        "ffmpeg": _ver(["ffmpeg", "-version"], r"version ([\d.\-]+)"),
    }


# ----------------------------------------------------------------------
# 9. 系统服务
# ----------------------------------------------------------------------

def probe_services() -> dict:
    if IS_WINDOWS:
        return {"service_manager": "services.msc",
                "container_runtime": "docker" if _which("docker") else None}
    sm = None
    if _which("systemctl"):
        sm = "systemd"
    elif _which("service"):
        sm = "sysvinit"
    elif _which("openrc"):
        sm = "openrc"
    return {
        "service_manager": sm,
        "container_runtime": ("docker" if _which("docker")
                              else "podman" if _which("podman") else None),
    }


# ----------------------------------------------------------------------
# 10. 外设
# ----------------------------------------------------------------------

def probe_peripherals() -> dict:
    if IS_WINDOWS:
        cam = _ps("(Get-PnpDevice -Class CAMERA -ErrorAction SilentlyContinue"
                  " | Measure-Object).Count")
        audio = _ps("(Get-PnpDevice -Class AudioEndpoint -ErrorAction "
                    "SilentlyContinue | Measure-Object).Count")
        return {
            "camera": (int(cam.strip()) > 0 if cam and cam.strip().isdigit()
                       else None),
            "audio": (int(audio.strip()) > 0
                      if audio and audio.strip().isdigit() else None),
            "usb_count": None,                    # 留 gap（Get-PnpDevice USB 太慢）
        }
    video = _run(["sh", "-c", "ls /dev/video* 2>/dev/null"])
    snd = _run(["sh", "-c", "ls /dev/snd 2>/dev/null"])
    usb = _run(["lsusb"]) if _which("lsusb") else None
    return {
        "camera": bool(video and video.strip()),
        "audio": bool(snd and snd.strip()),
        "usb_count": len(usb.strip().splitlines()) if usb else None,
    }


# ----------------------------------------------------------------------
# 11. 安全状态
# ----------------------------------------------------------------------

def probe_security() -> dict:
    import getpass
    secure_boot = None
    if IS_WINDOWS:
        sb = _ps("try { Confirm-SecureBootUEFI } catch { 'n/a' }")
        secure_boot = (sb.strip() == "True" if sb else None)
        admin = _ps("([Security.Principal.WindowsPrincipal][Security.Principal."
                    "WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal"
                    ".WindowsBuiltInRole]::Administrator)")
        is_admin = admin.strip() == "True" if admin else None
    else:
        sb = _run(["mokutil", "--sb-state"]) if _which("mokutil") else None
        if sb:
            secure_boot = "enabled" in sb.lower()
        is_admin = (os.geteuid() == 0) if hasattr(os, "geteuid") else None
    return {
        "user": getpass.getuser(),
        "is_admin": is_admin,
        "secure_boot": secure_boot,
    }


# ----------------------------------------------------------------------
# 12. 运行状态（当前负载快照）
# ----------------------------------------------------------------------

def probe_status() -> dict:
    load = None
    if hasattr(os, "getloadavg"):
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            pass
    elif IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_Processor).LoadPercentage")
        load = int(out.strip()) if out and out.strip().isdigit() else None
    mem = probe_memory()
    mem_pct = None
    if mem.get("total_bytes") and mem.get("available_bytes") is not None:
        mem_pct = round(100 * (1 - mem["available_bytes"] / mem["total_bytes"]), 1)
    return {
        "cpu_load": load,
        "memory_used_pct": mem_pct,
    }


# ----------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------

PROBE_TABLE = [
    ("device", probe_device),
    ("os", probe_os),
    ("cpu", probe_cpu),
    ("memory", probe_memory),
    ("gpu", probe_gpu),
    ("storage", probe_storage),
    ("network", probe_network),
    ("runtime", probe_runtime),
    ("services", probe_services),
    ("peripherals", probe_peripherals),
    ("security", probe_security),
    ("status", probe_status),
]


def run_probe() -> tuple:
    """跑全部探测。返回 (identity: dict, gaps: list[str])。

    gaps 的元素是 "category.field" 路径——值为 None 或关键字段缺失的字段。
    """
    identity = {}
    gaps = []
    for name, fn in PROBE_TABLE:
        try:
            result = fn() or {}
        except Exception as exc:
            logger.warning("probe %s crashed: %s", name, exc)
            result = {}
        identity[name] = result
        for key, value in result.items():
            if value is None:
                gaps.append(f"{name}.{key}")
    logger.info("probe done: %d categories, %d gaps", len(identity), len(gaps))
    return identity, gaps
