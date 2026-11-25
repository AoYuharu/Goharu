"""系统探测层：按 L1→L5 同步采集裸机环境（纯 stdlib，零 LLM）。

保留 identity 的原始类别字段，并在 identity["probe_report"] 中记录逐字段
四态证据。只有 unknown 进入 gaps；False、0、空列表都是有效探测结果。
所有命令带超时，单类别异常不会中断后续层。
"""

import glob
import logging
import os
import platform
import re
import shutil
import socket
import subprocess

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
IS_MACOS = platform.system() == "Darwin"
_CMD_TIMEOUT = 8

LAYER_TITLES = {
    "L1": "系统与硬件基础",
    "L2": "软件与中间件",
    "L3": "设备与通信接口",
    "L4": "感知能力",
    "L5": "物理行为能力",
}


def _run(cmd, timeout=_CMD_TIMEOUT):
    """跑只读命令并返回文本；不存在、非零退出、超时统一返回 None。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            return None
        # java -version 等工具把版本写到 stderr。
        raw = proc.stdout or proc.stderr or b""
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("probe command failed: %s: %s", cmd[0], exc)
        return None


def _which(name):
    return shutil.which(name)


def _ps(script, timeout=_CMD_TIMEOUT):
    """Windows PowerShell 探测（-NoProfile 加速）；结果去首尾空白。"""
    if not IS_WINDOWS:
        return None
    out = _run(["powershell", "-NoProfile", "-Command", script], timeout)
    return out.strip() if out else None


def _sysctl(name):
    if not IS_MACOS or not _which("sysctl"):
        return None
    out = _run(["sysctl", "-n", name])
    return out.strip() if out else None


def _re1(pattern, text, flags=0):
    """正则取第一捕获组，不中返回 None。"""
    if not text:
        return None
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _as_int(value):
    return int(value.strip()) if value and value.strip().isdigit() else None


# L1：系统与硬件基础 -------------------------------------------------------

def probe_device() -> dict:
    return {
        "hostname": platform.node() or None,
        "arch": platform.machine() or None,
        "device_type": _device_type(),
    }


def _device_type():
    if IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_SystemEnclosure).ChassisTypes -join ','")
        if out:
            if re.search(r"\b(8|9|10|11|14|30|31|32)\b", out):
                return "laptop"
            return "desktop"
        return None
    if IS_MACOS:
        model = _sysctl("hw.model")
        if model:
            if "MacBook" in model:
                return "laptop"
            if model.startswith(("iMac", "Macmini", "MacPro", "MacStudio")):
                return "desktop"
        return None
    out = _run(["cat", "/sys/class/dmi/id/chassis_type"])
    if out:
        value = out.strip()
        return ("laptop" if value in ("8", "9", "10", "11", "14", "30", "31", "32")
                else "desktop")
    return None


def probe_os() -> dict:
    if IS_WINDOWS:
        caption = _ps("(Get-CimInstance Win32_OperatingSystem).Caption")
        build = _ps("(Get-CimInstance Win32_OperatingSystem).BuildNumber")
        return {
            "name": caption,
            "version": platform.version() or None,
            "kernel": build,
        }
    if IS_MACOS:
        return {
            "name": "macOS",
            "version": platform.mac_ver()[0] or None,
            "kernel": platform.release() or None,
        }
    name = None
    os_release = _run(["cat", "/etc/os-release"])
    if os_release:
        name = _re1(r'PRETTY_NAME="([^"]+)"', os_release)
    return {
        "name": name or platform.system() or None,
        "version": platform.release() or None,
        "kernel": platform.release() or None,
    }


def probe_cpu() -> dict:
    model = None
    flags = None
    physical = None
    threads = os.cpu_count()
    if IS_WINDOWS:
        model = _ps("(Get-CimInstance Win32_Processor).Name")
        physical = threads  # 保留原字段语义；Windows 规则只确认逻辑核数。
    elif IS_MACOS:
        model = _sysctl("machdep.cpu.brand_string")
        physical = _as_int(_sysctl("hw.physicalcpu")) or threads
        threads = _as_int(_sysctl("hw.logicalcpu")) or threads
        features = _sysctl("machdep.cpu.features")
        if features:
            flags = [f for f in ("avx2", "avx512f", "neon", "sve")
                     if f in features.lower().split()]
    else:
        cpuinfo = _run(["cat", "/proc/cpuinfo"])
        if cpuinfo:
            model = _re1(r"model name\s*:\s*(.+)", cpuinfo)
            fl = _re1(r"flags\s*:\s*(.+)", cpuinfo)
            if fl:
                flags = [f for f in ("avx2", "avx512f", "neon", "sve")
                         if f in fl.split()]
        physical = threads  # 保留原字段语义；通用规则只确认逻辑核数。
    return {
        "model": model.strip() if model else None,
        "cores_physical": physical,
        "threads": threads,
        "instruction_sets": flags,
    }


def probe_memory() -> dict:
    if IS_WINDOWS:
        total = _as_int(_ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"))
        avail_kb = _as_int(_ps("(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"))
        swap = _as_int(_ps("(Get-CimInstance Win32_PageFileUsage).AllocatedBaseSize"))
        return {
            "total_bytes": total,
            "available_bytes": avail_kb * 1024 if avail_kb is not None else None,
            "swap_mb": swap,
        }
    if IS_MACOS:
        total = _as_int(_sysctl("hw.memsize"))
        page_size = _as_int(_sysctl("hw.pagesize"))
        available = None
        vm_stat = _run(["vm_stat"]) if _which("vm_stat") else None
        if vm_stat and page_size:
            pages = 0
            matched = False
            for label in ("free", "inactive", "speculative"):
                value = _re1(rf"Pages {label}:\s*(\d+)", vm_stat)
                if value:
                    pages += int(value)
                    matched = True
            if matched:
                available = pages * page_size
        swap_usage = _sysctl("vm.swapusage")
        swap_text = _re1(r"total = ([\d.]+)M", swap_usage)
        return {
            "total_bytes": total,
            "available_bytes": available,
            "swap_mb": int(float(swap_text)) if swap_text else None,
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
        "swap_mb": int(swap_kb) // 1024 if swap_kb is not None else None,
    }


def probe_gpu() -> dict:
    nvidia = _which("nvidia-smi")
    if nvidia:
        out = _run([nvidia, "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader"])
        if out:
            gpus = []
            for line in out.strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 3:
                    gpus.append({"model": parts[0], "vram": parts[1],
                                 "driver": parts[2]})
            return {"count": len(gpus), "gpus": gpus,
                    "driver": gpus[0]["driver"] if gpus else None}
    if IS_WINDOWS:
        out = _ps("(Get-CimInstance Win32_VideoController) | "
                  "Select-Object -ExpandProperty Name")
        if out:
            names = [line.strip() for line in out.strip().splitlines() if line.strip()]
            return {"count": len(names),
                    "gpus": [{"model": name} for name in names], "driver": None}
        return {"count": None, "gpus": None, "driver": None}
    if IS_MACOS:
        out = (_run(["system_profiler", "SPDisplaysDataType"])
               if _which("system_profiler") else None)
        if out is not None:
            names = re.findall(r"Chipset Model:\s*(.+)", out)
            return {"count": len(names),
                    "gpus": [{"model": name.strip()} for name in names],
                    "driver": None}
        return {"count": None, "gpus": None, "driver": None}
    out = _run(["lspci"]) if _which("lspci") else None
    if out:
        vgas = [line for line in out.splitlines()
                if re.search(r"VGA|3D controller", line)]
        return {"count": len(vgas),
                "gpus": [{"model": _re1(r":\s*(.+)$", line)} for line in vgas],
                "driver": None}
    return {"count": None, "gpus": None, "driver": None}


def probe_storage() -> dict:
    disks = []
    if IS_WINDOWS:
        out = _ps("Get-PSDrive -PSProvider FileSystem | "
                  "Select-Object Name,Used,Free | ConvertTo-Csv -NoTypeInformation")
        if out:
            for line in out.strip().splitlines()[1:]:
                match = re.match(r'"?(\w+)"?,"?(\d*)"?,.*?"(\d+)"?', line)
                if match:
                    root = f"{match.group(1)}:\\"
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
        if IS_MACOS:
            diskutil = _run(["diskutil", "info", "/"]) if _which("diskutil") else None
            fstype = _re1(r"(?:File System Personality|Type \(Bundle\)):\s*(.+)",
                          diskutil)
            if fstype and disks:
                disks[0]["fstype"] = fstype
        else:
            lsblk = _run(["lsblk", "-f"]) if _which("lsblk") else None
            if lsblk:
                for disk in disks:
                    match = re.search(
                        rf"\S+\s+\S+\s+(\S+).*{re.escape(disk['mount'])}\s*$",
                        lsblk, re.M)
                    if match:
                        disk["fstype"] = match.group(1)
    return {"disks": disks}


def probe_security() -> dict:
    import getpass

    secure_boot = None
    if IS_WINDOWS:
        sb = _ps("try { Confirm-SecureBootUEFI } catch { 'n/a' }")
        secure_boot = (sb.strip() == "True" if sb and sb.strip() != "n/a" else None)
        admin = _ps("([Security.Principal.WindowsPrincipal][Security.Principal."
                    "WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal"
                    ".WindowsBuiltInRole]::Administrator)")
        is_admin = admin.strip() == "True" if admin else None
    else:
        if not IS_MACOS:
            sb = _run(["mokutil", "--sb-state"]) if _which("mokutil") else None
            if sb:
                secure_boot = "enabled" in sb.lower()
        is_admin = (os.geteuid() == 0) if hasattr(os, "geteuid") else None
    return {
        "user": getpass.getuser(),
        "is_admin": is_admin,
        "secure_boot": secure_boot,
    }


def probe_status(mem=None) -> dict:
    load = None
    if hasattr(os, "getloadavg"):
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            pass
    elif IS_WINDOWS:
        load = _as_int(_ps("(Get-CimInstance Win32_Processor).LoadPercentage"))
    mem = mem if mem is not None else probe_memory()
    mem_pct = None
    if mem.get("total_bytes") and mem.get("available_bytes") is not None:
        mem_pct = round(100 * (1 - mem["available_bytes"] / mem["total_bytes"]), 1)
    return {"cpu_load": load, "memory_used_pct": mem_pct}


# L2：软件与中间件 ---------------------------------------------------------

def probe_runtime() -> dict:
    def _ver(cmd, pattern):
        if not _which(cmd[0]):
            return None
        out = _run(cmd)
        version = _re1(pattern, out) if out else None
        return version.rstrip(".") if version else None

    return {
        "python": platform.python_version(),
        "node": _ver(["node", "--version"], r"v?([\d.]+)"),
        "java": _ver(["java", "-version"], r'version "([^"]+)"'),
        "docker": _ver(["docker", "--version"], r"version ([\d.]+)"),
        "git": _ver(["git", "--version"], r"version ([\d.]+)"),
        "ffmpeg": _ver(["ffmpeg", "-version"], r"version ([\d.\-]+)"),
    }


def probe_services() -> dict:
    if IS_WINDOWS:
        return {"service_manager": "services.msc",
                "container_runtime": "docker" if _which("docker") else None}
    if IS_MACOS:
        sm = "launchd" if _which("launchctl") else None
    elif _which("systemctl"):
        sm = "systemd"
    elif _which("service"):
        sm = "sysvinit"
    elif _which("openrc"):
        sm = "openrc"
    else:
        sm = None
    return {
        "service_manager": sm,
        "container_runtime": ("docker" if _which("docker")
                              else "podman" if _which("podman") else None),
    }


# L3：设备与通信接口 -------------------------------------------------------

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
            result["dns"] = re.findall(r"DNS[^\n:：]*[:：]\s*([\d.]+)", out)
            result["gateway"] = _re1(
                r"(?:Default Gateway|默认网关)[^\n:：]*[:：]\s*([\d.]+)", out)
            result["interfaces"] = re.findall(
                r"(?:adapter|适配器)\s+([^:]+):", out)[:8]
    elif IS_MACOS:
        out = _run(["ifconfig"]) if _which("ifconfig") else None
        if out:
            interfaces = []
            current = None
            for line in out.splitlines():
                match = re.match(r"^([^:\s]+): flags=", line)
                if match:
                    current = {"name": match.group(1), "ip": None, "up": "UP" in line}
                    interfaces.append(current)
                elif current:
                    ip = _re1(r"^\s*inet\s+(\d+\.\d+\.\d+\.\d+)", line)
                    if ip:
                        current["ip"] = ip
            result["interfaces"] = interfaces
        route = _run(["route", "-n", "get", "default"]) if _which("route") else None
        if route:
            result["gateway"] = _re1(r"gateway:\s*([\d.]+)", route)
        resolver = _run(["scutil", "--dns"]) if _which("scutil") else None
        if resolver:
            result["dns"] = sorted(set(re.findall(
                r"nameserver\[\d+\]\s*:\s*([\d.]+)", resolver)))
    else:
        out = _run(["ip", "-brief", "addr"]) if _which("ip") else None
        if out:
            result["interfaces"] = [
                {"name": line.split()[0],
                 "ip": (_re1(r"(\d+\.\d+\.\d+\.\d+/\d+)", line) or "").split("/")[0],
                 "up": "UP" in line}
                for line in out.splitlines() if line.strip()]
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


def _windows_serial_ports():
    """注册表只读快路径；键不存在即确认没有串口。"""
    import winreg

    ports = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            index = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                ports.append(value)
                index += 1
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.debug("serial registry enumeration failed: %s", exc)
        return None
    return sorted(ports)


def probe_communication() -> dict:
    """只枚举 USB/串口，不打开设备、不发送数据。"""
    if IS_WINDOWS:
        usb = _ps("(Get-PnpDevice -Class USB -ErrorAction SilentlyContinue"
                  " | Measure-Object).Count", timeout=15)
        return {"usb_count": _as_int(usb),
                "serial_ports": _windows_serial_ports()}
    if IS_MACOS:
        usb = (_run(["system_profiler", "SPUSBDataType"])
               if _which("system_profiler") else None)
        return {
            "usb_count": (len(re.findall(r"Product ID:", usb))
                          if usb is not None else None),
            "serial_ports": sorted(glob.glob("/dev/cu.*")),
        }
    usb = _run(["lsusb"]) if _which("lsusb") else None
    serial_ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
                          + glob.glob("/dev/ttyS*"))
    return {
        "usb_count": len(usb.strip().splitlines()) if usb else None,
        "serial_ports": serial_ports,
    }


# L4：感知能力 -------------------------------------------------------------

def probe_peripherals() -> dict:
    if IS_WINDOWS:
        cam = _ps("(Get-PnpDevice -Class CAMERA -ErrorAction SilentlyContinue"
                  " | Measure-Object).Count")
        audio = _ps("(Get-PnpDevice -Class AudioEndpoint -ErrorAction "
                    "SilentlyContinue | Measure-Object).Count")
        return {
            "camera": int(cam.strip()) > 0 if cam and cam.strip().isdigit() else None,
            "audio": (int(audio.strip()) > 0
                      if audio and audio.strip().isdigit() else None),
        }
    if IS_MACOS:
        cam = (_run(["system_profiler", "SPCameraDataType"])
               if _which("system_profiler") else None)
        audio = (_run(["system_profiler", "SPAudioDataType"])
                 if _which("system_profiler") else None)
        return {
            "camera": (bool(re.search(r"Model ID:|Camera Type:", cam))
                       if cam is not None else None),
            "audio": (bool(re.search(r"Input Source:|Output Source:|Manufacturer:", audio))
                      if audio is not None else None),
        }
    return {
        "camera": bool(glob.glob("/dev/video*")),
        "audio": bool(glob.glob("/dev/snd/*")),
    }


# L5：物理行为能力 ---------------------------------------------------------

def probe_actuation() -> dict:
    """只发现 GPIO 候选节点，绝不打开、写入或驱动设备。"""
    if IS_WINDOWS or IS_MACOS:
        return {"gpio_chips": None, "control_candidates": None}
    chips = sorted(glob.glob("/dev/gpiochip*"))
    return {
        "gpio_chips": chips,
        "control_candidates": ([{"type": "gpio", "device": path} for path in chips]
                               if chips else None),
    }


# 分层汇总 -----------------------------------------------------------------

PROBE_TABLE = [
    ("L1", "device", probe_device, ("hostname", "arch", "device_type")),
    ("L1", "os", probe_os, ("name", "version", "kernel")),
    ("L1", "cpu", probe_cpu,
     ("model", "cores_physical", "threads", "instruction_sets")),
    ("L1", "memory", probe_memory, ("total_bytes", "available_bytes", "swap_mb")),
    ("L1", "gpu", probe_gpu, ("count", "gpus", "driver")),
    ("L1", "storage", probe_storage, ("disks",)),
    ("L1", "security", probe_security, ("user", "is_admin", "secure_boot")),
    ("L1", "status", probe_status, ("cpu_load", "memory_used_pct")),
    ("L2", "runtime", probe_runtime,
     ("python", "node", "java", "docker", "git", "ffmpeg")),
    ("L2", "services", probe_services, ("service_manager", "container_runtime")),
    ("L3", "network", probe_network,
     ("hostname_ip", "interfaces", "dns", "gateway", "internet")),
    ("L3", "communication", probe_communication, ("usb_count", "serial_ports")),
    ("L4", "peripherals", probe_peripherals, ("camera", "audio")),
    ("L5", "actuation", probe_actuation, ("gpio_chips", "control_candidates")),
]

_CATEGORY_EVIDENCE = {
    "device": "platform and chassis metadata",
    "os": "platform and OS metadata",
    "cpu": "platform and CPU metadata",
    "memory": "OS memory counters",
    "gpu": "GPU read-only enumeration",
    "storage": "disk_usage and filesystem metadata",
    "security": "OS security primitives",
    "status": "OS load and memory counters",
    "runtime": "executable version queries",
    "services": "service and container executable discovery",
    "network": "socket and OS network configuration",
    "communication": "USB and serial read-only enumeration",
    "peripherals": "camera and audio read-only enumeration",
    "actuation": "GPIO device-node discovery",
}

_RUNTIME_COMMANDS = {"node": "node", "java": "java", "docker": "docker",
                     "git": "git", "ffmpeg": "ffmpeg"}


def _field_state(category, key, value, result):
    if value is not None:
        return "verified"
    if category == "actuation":
        # Windows/macOS 没有通用 GPIO 快路径，不能把“未探测”误报为“不适用”。
        return "unknown" if IS_WINDOWS or IS_MACOS else "not_applicable"
    if category == "runtime" and key in _RUNTIME_COMMANDS:
        if not _which(_RUNTIME_COMMANDS[key]):
            return "unavailable"
    if category == "services" and key == "container_runtime":
        if not _which("docker") and not _which("podman"):
            return "unavailable"
    if category == "gpu" and key == "driver":
        if result.get("count") == 0 or IS_MACOS:
            return "not_applicable"
    return "unknown"


def _evidence(category, status, error=None):
    source = _CATEGORY_EVIDENCE[category]
    if error:
        return f"{source}; probe raised {error}"
    if status == "verified":
        return f"{source}; value verified"
    if status == "unavailable":
        return f"{source}; capability or executable not present"
    if status == "not_applicable":
        return f"{source}; capability not applicable on this device"
    return f"{source}; not confirmed by read-only rules"


def run_probe() -> tuple:
    """同步执行 L1→L5，返回 (identity, gaps)。

    probe_report[Lx].fields 以 identity 字段路径为键；只有 unknown 进入 gaps。
    单类别异常会把该类别预期字段记为 unknown，并继续后续层。
    """
    identity = {}
    gaps = []
    probe_report = {
        layer: {"title": title, "fields": {}}
        for layer, title in LAYER_TITLES.items()
    }
    layer_gaps = {layer: 0 for layer in LAYER_TITLES}

    for layer, name, fn, expected_fields in PROBE_TABLE:
        error = None
        try:
            # status 复用 L1 已完成的内存探测，避免重复慢命令。
            result = (fn(identity.get("memory")) if name == "status" else fn()) or {}
        except Exception as exc:
            error = type(exc).__name__
            logger.warning("probe %s crashed: %s", name, exc)
            result = {}
        for key in expected_fields:
            result.setdefault(key, None)
        identity[name] = result

        for key, value in result.items():
            path = f"{name}.{key}"
            status = "unknown" if error else _field_state(name, key, value, result)
            probe_report[layer]["fields"][path] = {
                "status": status,
                "evidence": _evidence(name, status, error),
            }
            if status == "unknown":
                gaps.append(path)
                layer_gaps[layer] += 1

    for layer in LAYER_TITLES:
        logger.info("probe %s done: %d unknown fields", layer, layer_gaps[layer])
    identity["probe_report"] = probe_report
    logger.info("probe done: %d categories, %d gaps", len(identity) - 1, len(gaps))
    return identity, gaps
