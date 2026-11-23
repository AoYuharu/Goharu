#!/usr/bin/env python3
"""goharu 命令行入口（跨 Windows / Linux / macOS，纯标准库）。

用法：
  goharu                      确保网关在跑（没起就后台拉起守护）→ 起 TUI attach
  goharu tui                  同上
  goharu gateway start        后台拉起网关守护（幂等；已在跑则跳过）
  goharu gateway stop         优雅停网关（RPC gateway.shutdown，兜底按 pid 杀）
  goharu gateway restart      停 + 起
  goharu gateway status       探活，打印 pid / 是否守护 / sessions
  goharu gateway log [N]      看守护日志末尾 N 行（默认 40）

关 TUI 不会关网关（config gateway.tui_attach_shutdown=false）；要停用 `gateway stop`。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
VENV_PY = (os.path.join(REPO, "venv", "Scripts", "python.exe")
           if IS_WIN else os.path.join(REPO, "venv", "bin", "python"))
RUNTIME = os.path.join(REPO, "runtime_memory")
PIDFILE = os.path.join(RUNTIME, "gateway.pid")
LOGFILE = os.path.join(RUNTIME, "logs", "gateway_daemon.log")
GATEWAY = os.path.join(REPO, "Gateway", "gateway_entry.py")
DEFAULT_PORT = 8791


def _python() -> str:
    return VENV_PY if os.path.exists(VENV_PY) else sys.executable


def _rpc_port() -> int:
    """从 config.yaml 轻解析 gateway.rpc_port（正则，不依赖 yaml）。"""
    try:
        txt = open(os.path.join(REPO, "config.yaml"), encoding="utf-8").read()
        m = re.search(r"(?m)^\s*rpc_port:\s*(\d+)", txt)
        return int(m.group(1)) if m else DEFAULT_PORT
    except Exception:
        return DEFAULT_PORT


def _rpc(method: str, params: dict | None = None, timeout: float = 3.0):
    """向网关 TCP JSON-RPC 发一条请求，返回 result（dict）或 None。"""
    port = _rpc_port()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            req = {"jsonrpc": "2.0", "method": method,
                   "params": params or {}, "id": "cli"}
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
            return json.loads(line).get("result") if line.strip() else None
    except Exception:
        return None


def _ping():
    """探活：返回 gateway.ping 的 result，或 None。"""
    return _rpc("gateway.ping", timeout=1.5)


def _alive() -> bool:
    r = _ping()
    return bool(r and r.get("ok"))


def _read_pid():
    try:
        return int(open(PIDFILE).read().strip())
    except Exception:
        return None


def _write_pid(pid: int) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    with open(PIDFILE, "w", encoding="utf-8") as f:
        f.write(str(pid))


def _remove_pid() -> None:
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


def gateway_start(wait: int = 60) -> bool:
    r = _ping()
    if r and r.get("ok"):
        print(f"[+] 网关已在运行（pid {r.get('pid')}）")
        return True
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    log = open(LOGFILE, "a", encoding="utf-8")
    kwargs: dict = {}
    if IS_WIN:
        # Windows：分离进程，关掉本终端也不影响守护
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True   # 脱离当前会话，关终端不挂
    p = subprocess.Popen(
        [_python(), "-u", GATEWAY, "--daemon"],
        cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        **kwargs,
    )
    _write_pid(p.pid)
    print(f"[+] 正在后台拉起网关守护（pid {p.pid}），等待就绪…")
    deadline = time.time() + wait
    while time.time() < deadline:
        rr = _ping()
        if rr and rr.get("ok"):
            print(f"[+] 网关已就绪（pid {rr.get('pid')}）")
            return True
        if p.poll() is not None:
            print(f"[x] 网关进程已退出（exit {p.returncode}），看日志: {LOGFILE}")
            return False
        time.sleep(1)
    print(f"[!] 等待超时，网关可能仍在初始化（冷加载 embedding 较慢），看: {LOGFILE}")
    return False


def gateway_stop(wait: int = 15) -> bool:
    if not _alive() and _read_pid() is None:
        print("[+] 网关未在运行")
        return True
    print("[+] 正在停止网关…")
    _rpc("gateway.shutdown", timeout=5)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not _alive():
            _remove_pid()
            print("[+] 网关已停止")
            return True
        time.sleep(0.5)
    pid = _read_pid()
    if pid:
        print(f"[!] RPC 停不掉，按 pid {pid} 强杀")
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True)
            else:
                os.kill(pid, 15)
        except Exception as e:
            print(f"[x] 强杀失败: {e}")
            return False
        _remove_pid()
        print("[+] 已强杀")
        return True
    print("[x] 停止失败（网关无响应且无 pidfile）")
    return False


def gateway_status() -> int:
    r = _ping()
    if r and r.get("ok"):
        print(f"[+] 网关运行中  pid={r.get('pid')}  daemon={r.get('daemon')}"
              f"  sessions={r.get('sessions')}")
        return 0
    print("[+] 网关未运行")
    return 1


def gateway_log(n: int = 40) -> int:
    if not os.path.exists(LOGFILE):
        print(f"[+] 暂无守护日志（{LOGFILE}）")
        return 0
    lines = open(LOGFILE, encoding="utf-8", errors="replace").read().splitlines()
    for ln in lines[-n:]:
        print(ln)
    return 0


def tui() -> int:
    """goharu 默认：确保网关在跑（没起就拉起守护），再起 TUI attach 上去。"""
    if not _alive():
        print("[!] 网关未运行，先拉起守护…")
        gateway_start()
    # run_tui_ts.py → node TUI → 它 spawn 的 gateway_entry 发现端口被占 → 自动降为 proxy attach
    return subprocess.call([_python(), os.path.join(REPO, "run_tui_ts.py")], cwd=REPO)


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "tui":
        return tui()
    if args[0] == "gateway":
        sub = args[1] if len(args) > 1 else "status"
        if sub == "start":
            return 0 if gateway_start() else 1
        if sub == "stop":
            return 0 if gateway_stop() else 1
        if sub == "restart":
            gateway_stop()
            return 0 if gateway_start() else 1
        if sub == "status":
            return gateway_status()
        if sub == "log":
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 40
            return gateway_log(n)
        print(f"未知 gateway 子命令: {sub}（可用 start/stop/restart/status/log）")
        return 2
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
