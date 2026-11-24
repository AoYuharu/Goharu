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
  goharu uninstall [选项]     卸载：停服务 + 杀 gateway/TUI（含目录已删的幽灵守护）
                              + 删 venv/node_modules/dist/数据/.env + 全局命令
                              --keep-data 保留 runtime_memory/ 与 models/
                              --stop-only 只停进程/服务，不删任何文件
                              --dry-run   只列出将杀/将删的内容
                              --yes       跳过确认（非交互环境必须）

关 TUI 不会关网关（config gateway.tui_attach_shutdown=false）；要停用 `gateway stop`。
"""
from __future__ import annotations

import json
import os
import re
import shutil
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
    return _rpc("gateway.ping", timeout=3.0)


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
        # 各 channel 连通/配置状态（哪些配通了哪些失败）
        rows = _rpc("channels.health", timeout=3.0)
        if rows is not None:
            print("通道状态：")
            if not rows:
                print("  （未挂载 channel）")
            for c in rows:
                mark = "✓" if c.get("ok") else "✗"
                detail = c.get("detail") or c.get("state")
                print(f"  {mark} {c.get('type')}#{c.get('channel_id')}"
                      f"（{c.get('name')}）  {c.get('state')}  {detail}")
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


# ── uninstall（跨平台唯一实现；uninstall.sh / uninstall.bat 只是薄封装）──────
# 只认"解释器进程 + 某个独立参数以脚本名结尾"：避免误杀命令行里恰好提到这些
# 文件名的 shell（tail 日志、编辑器、甚至正在跑卸载的 bash 自己）
_SCRIPT_TOKEN_RE = re.compile(
    r'(?:^|[\\/])(?:gateway_entry\.py|run_tui_ts\.py)$'
    r'|[\\/]tui-ts[\\/]dist[\\/][^"/]+\.js$')
_EXE_RE = re.compile(r"^(python[\w.]*|node|python[\d.]*\.exe|node\.exe)$", re.I)


def _is_target(exe_name: str, tokens: list[str]) -> bool:
    if not _EXE_RE.match(exe_name or ""):
        return False
    return any(_SCRIPT_TOKEN_RE.search(t) for t in tokens)


def _find_processes() -> list[tuple[int, str]]:
    """找本机所有 gateway/TUI 进程（含 cwd 已被删除的幽灵守护）。返回 [(pid, 简述)]。

    不依赖 ps/pkill/wmic（最小镜像和 Win11 24H2 都没有）：Linux 扫 /proc，
    Windows 走 PowerShell CIM。排除自身与父进程。
    """
    out: list[tuple[int, str]] = []
    skip = {os.getpid(), os.getppid()}
    if IS_WIN:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.Name)`t$($_.CommandLine)\" }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
        except Exception as e:
            print(f"[!] 进程扫描失败: {e}")
            return out
        for line in r.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3 or not parts[0].strip().isdigit():
                continue
            pid, name, cmdline = int(parts[0]), parts[1], parts[2]
            if pid in skip:
                continue
            # 命令行粗切分为参数（去引号），只做 token 级匹配
            tokens = [t.strip('"\' ') for t in re.findall(r'"[^"]*"|\S+', cmdline)]
            if _is_target(name, tokens):
                out.append((pid, cmdline[:120]))
        return out
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in skip:
            continue
        try:
            raw = open(f"/proc/{pid}/cmdline", "rb").read()
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
        except OSError:
            continue
        tokens = [t for t in raw.split(b"\0") if t]
        if not tokens:
            continue
        try:
            toks = [t.decode("utf-8", "replace") for t in tokens]
        except Exception:
            continue
        if _is_target(exe, toks):
            out.append((pid, " ".join(toks)[:120]))
    return out


def _kill_pid(pid: int, sig: int) -> None:
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, sig)
    except Exception:
        pass


def _stop_processes(dry_run: bool = False) -> list[tuple[int, str]]:
    """停掉全部 gateway/TUI：先 RPC 优雅停 → SIGTERM 多轮收敛 → SIGKILL 兜底。

    多轮是因为 TUI 在 gateway 退出瞬间会 re-spawn 子进程，单轮快照杀不干净。
    返回本次发现过的进程列表。
    """
    found_all: dict[int, str] = {}
    procs = _find_processes()
    for pid, cmd in procs:
        found_all.setdefault(pid, cmd)
    if dry_run:
        return list(found_all.items())
    # 1) 优雅：RPC shutdown（守护在监听时有效；幽灵守护同样响应）
    if procs:
        _rpc("gateway.shutdown", timeout=5)
        deadline = time.time() + 8
        while time.time() < deadline and _find_processes():
            time.sleep(0.5)
    # 2) 强制：TERM → 等待 → KILL，最多 5 轮直到没有匹配进程
    for _round in range(5):
        procs = _find_processes()
        if not procs:
            break
        for pid, cmd in procs:
            found_all.setdefault(pid, cmd)
            _kill_pid(pid, 15)
        deadline = time.time() + 6
        while time.time() < deadline and _find_processes():
            time.sleep(0.5)
        for pid, _ in _find_processes():
            _kill_pid(pid, 9)
        time.sleep(1)
    return list(found_all.items())


def _systemd_unit_present() -> bool:
    if IS_WIN or not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "list-unit-files"], capture_output=True,
                           text=True, timeout=15)
    except Exception:
        return False
    return "goharu" in (r.stdout or "")


def _stop_systemd() -> str | None:
    """停用并移除 systemd user 服务（仅 Linux 且存在时）。"""
    if not _systemd_unit_present():
        return None
    for cmd in (["systemctl", "--user", "stop", "goharu"],
                ["systemctl", "--user", "disable", "goharu"]):
        subprocess.run(cmd, capture_output=True)
    unit = os.path.expanduser("~/.config/systemd/user/goharu.service")
    try:
        os.remove(unit)
    except OSError:
        pass
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return "goharu.service"


def _tsc_artifacts() -> list[str]:
    out: list[str] = []
    for sub in ("src", "test"):
        base = os.path.join(REPO, "tui-ts", sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith((".js", ".js.map", ".d.ts", ".d.ts.map")):
                    out.append(os.path.join(root, f))
    return out


def _clean_rc_snippet(rc: str) -> bool:
    """删 rc 文件里 install.py 追加的「# Goharu 全局命令」+ 紧随的 export PATH 行。"""
    try:
        lines = open(rc, encoding="utf-8").read().splitlines()
    except OSError:
        return False
    out, skip, changed = [], False, False
    for ln in lines:
        if skip:
            skip = False
            if ln.startswith("export PATH=") and ".local/bin" in ln:
                changed = True
                continue
        if ln.strip() == "# Goharu 全局命令":
            skip = changed = True
            continue
        out.append(ln)
    if changed:
        open(rc, "w", encoding="utf-8").write("\n".join(out) + "\n")
    return changed


def _remove_global_command() -> list[str]:
    """移除全局 goharu 命令：POSIX 启动器 + rc PATH 片段；Windows 启动器 + 用户 PATH 项。"""
    removed: list[str] = []
    home = os.path.expanduser("~")
    if IS_WIN:
        bin_dir = os.path.join(home, ".goharu", "bin")
        launcher = os.path.join(bin_dir, "goharu.cmd")
        if os.path.exists(launcher):
            os.remove(launcher)
            removed.append(launcher)
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            parts = [p for p in cur.split(";") if p and p.rstrip("\\").lower() != bin_dir.rstrip("\\").lower()]
            if len(parts) != len([p for p in cur.split(";") if p]):
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
                removed.append("用户 PATH 中的 " + bin_dir)
            winreg.CloseKey(key)
            try:
                import ctypes
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 0, 1000, None)
            except Exception:
                pass
        except Exception as e:
            print(f"[!] 清理用户 PATH 失败: {e}")
        try:
            os.rmdir(bin_dir)   # 仅在已空时成功
        except OSError:
            pass
        return removed
    launcher = os.path.join(home, ".local", "bin", "goharu")
    if os.path.exists(launcher):
        os.remove(launcher)
        removed.append(launcher)
    for rc in (".bashrc", ".zshrc", ".profile"):
        p = os.path.join(home, rc)
        if os.path.exists(p) and _clean_rc_snippet(p):
            removed.append(f"{p} 中的 PATH 片段")
    return removed


def _remove_venv() -> str | None:
    """删 venv；Windows 上若当前解释器就在 venv 里，推迟到进程退出后再删。"""
    venv = os.path.join(REPO, "venv")
    if not os.path.isdir(venv):
        return None
    if IS_WIN and os.path.normcase(sys.executable).startswith(os.path.normcase(venv)):
        # 运行中的 python.exe 不能删自己：分离一个 cmd 等本进程退出后 rmdir
        subprocess.run(f'cmd /c "timeout /t 3 /nobreak >nul & rmdir /s /q "{venv}""',
                       shell=True, creationflags=subprocess.DETACHED_PROCESS
                       | subprocess.CREATE_NEW_PROCESS_GROUP, capture_output=True)
        return f"{venv}（已安排退出后删除）"
    shutil.rmtree(venv, ignore_errors=True)
    return venv if os.path.isdir(venv) is False else f"{venv}（部分文件未能删除）"


def uninstall(argv: list[str]) -> int:
    keep_data = any(a in ("--keep-data",) for a in argv)
    stop_only = "--stop-only" in argv
    dry_run = "--dry-run" in argv
    assume_yes = "--yes" in argv or "-y" in argv
    for a in argv:
        if a.startswith("-") and a not in ("--keep-data", "--all", "--purge", "--stop-only",
                                            "--dry-run", "--yes", "-y", "-h", "--help"):
            print(f"未知参数: {a}")
            return 2
        if a in ("-h", "--help"):
            print(__doc__)
            return 0

    print("[*] Goharu 卸载" + ("（预演，不做任何改动）" if dry_run else ""))
    # 1) 进程
    procs = _stop_processes(dry_run=dry_run)
    if procs:
        print(f"[+] {'将终止' if dry_run else '已终止'} gateway/TUI 进程 {len(procs)} 个:")
        for pid, cmd in procs:
            print(f"      pid {pid}  {cmd}")
    else:
        print("[+] 无运行中的 gateway/TUI 进程")
    # 2) systemd
    unit = _stop_systemd() if not dry_run else (
        "goharu.service" if _systemd_unit_present() else None)
    if unit:
        print(f"[+] {'将移除' if dry_run else '已移除'} systemd 服务 {unit}")
    if stop_only:
        print("[+] --stop-only：未删除任何文件")
        return 0

    # 3) 待删清单
    targets: list[str] = [os.path.join(REPO, "venv"),
                          os.path.join(REPO, "tui-ts", "node_modules"),
                          os.path.join(REPO, "tui-ts", "dist")]
    if not keep_data:
        targets += [os.path.join(REPO, "runtime_memory"), os.path.join(REPO, "models"),
                    os.path.join(REPO, ".env")]
    existing = [t for t in targets if os.path.exists(t)]
    arts = _tsc_artifacts()
    print(f"[+] {'将删除' if dry_run else '删除'}依赖产物与数据:")
    for t in existing:
        print(f"      {t}")
    if arts:
        print(f"      tui-ts/src|test 下 {len(arts)} 个 tsc 产物文件")
    if keep_data:
        print("      （--keep-data：保留 runtime_memory/ 与 models/）")

    if dry_run:
        print("[+] 全局命令 ~/.local/bin/goharu（或 Windows ~/.goharu/bin）与 rc/PATH 片段将一并清理")
        print("[+] 预演结束，未做任何改动")
        return 0

    # 4) 确认（删数据不可逆）
    if not assume_yes:
        if not sys.stdin.isatty():
            print("[x] 非交互环境请加 --yes 确认删除")
            return 1
        try:
            ans = input("      以上内容将永久删除（记忆/密钥不可恢复）。继续？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if ans not in ("y", "yes"):
            print("[+] 已取消")
            return 0

    # 5) 执行
    v = _remove_venv()
    if v:
        print(f"[+] venv: {v}")
    for t in existing:
        if t.endswith("venv"):
            continue
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)
        elif os.path.exists(t):
            try:
                os.remove(t)
            except OSError:
                pass
        print(f"[+] 已删除 {t}" if not os.path.exists(t) else f"[!] 未能完全删除 {t}")
    for f in arts:
        try:
            os.remove(f)
        except OSError:
            pass
    if arts:
        print(f"[+] 已清除 {len(arts)} 个 tsc 产物")
    for r in _remove_global_command():
        print(f"[+] 已移除 {r}")
    print(f"[+] 卸载完成。仓库代码目录保留在 {REPO}（如需彻底删除: rm -rf）")
    return 0


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
    if args[0] == "uninstall":
        return uninstall(args[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
