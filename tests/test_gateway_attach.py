"""TUI attach 端到端测试：独立常驻网关 + 代理对接 + TUI 退出后网关存活。

场景（真实进程对撞，端口用 config 的 gateway.rpc_port=8791）：
1. 守护 A 启动（stdin 挂起）→ TuiRpcServer 监听 8791
2. TUI 侧进程 B 启动（模拟 TUI spawn gateway_entry）→ 探测到 A → 代理模式
   → 自动发 tui.attach → A 应答 attached:true
3. B 的 stdin 发普通 RPC（model.get_status）→ 应答经代理回到 B 的 stdout
4. B 的 stdin 关闭（模拟 TUI 退出）→ B 退出，A 的 PID 不变且 RPC 正常
5. 只有显式 gateway.shutdown 才停止 A

前置：8791 端口空闲（无运行中的守护）。运行：python tests/test_gateway_attach.py
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
PORT = 8791

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


def env_clean():
    e = dict(os.environ)
    e.pop("ANTHROPIC_AUTH_TOKEN", None)
    e["NO_PROXY"] = "api.deepseek.com,bots.qq.com,api.sgroup.qq.com"
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def wait_port_open(port, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def reader(proc, sink, stop):
    for line in proc.stdout:
        sink.append(line.decode("utf-8", errors="replace").rstrip())
        if stop.is_set():
            break


def wait_response(sink, pred, timeout=30):
    deadline = time.time() + timeout
    i0 = len(sink)
    while time.time() < deadline:
        for line in sink[i0:]:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if pred(obj):
                return obj
        time.sleep(0.1)
    return None


def rpc(method):
    with socket.create_connection(("127.0.0.1", PORT), timeout=3) as sock:
        req = {"jsonrpc": "2.0", "method": method, "params": {}, "id": "test"}
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))


# 前置：端口必须空闲（否则已有守护，本测试无法进行）
pre = wait_port_open(PORT, timeout=0.5)
if pre:
    print("SKIP/FAIL: 端口 8791 已被占用（有守护在跑？），无法测试 attach")
    sys.exit(1)

# ── 1. 守护 A ───────────────────────────────────────────────────────
daemon = subprocess.Popen(
    [sys.executable, "-u", "Gateway/gateway_entry.py", "--daemon"],
    cwd=ROOT, env=env_clean(),
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
)
daemon_out = []
d_stop = threading.Event()
threading.Thread(target=reader, args=(daemon, daemon_out, d_stop), daemon=True).start()

check(wait_port_open(PORT, timeout=120), "守护 A 启动并监听 8791")
check(wait_response(daemon_out, lambda o: o.get("params", {}).get("type") == "gateway.ready",
                    timeout=30) is not None, "守护 A 发出 gateway.ready")

# ── 2. TUI 侧进程 B（模拟 TUI spawn gateway_entry）─────────────────
proxy = subprocess.Popen(
    [sys.executable, "-u", "Gateway/gateway_entry.py"],
    cwd=ROOT, env=env_clean(),
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
)
proxy_out = []
p_stop = threading.Event()
threading.Thread(target=reader, args=(proxy, proxy_out, p_stop), daemon=True).start()

attach = wait_response(proxy_out,
                       lambda o: o.get("params", {}).get("type") == "gateway.ready",
                       timeout=15)
check(attach is not None, "B 探测到守护 → 代理模式 → attach 成功并收到 gateway.ready")
check(daemon.poll() is None and proxy.poll() is None, "A 与 B 都存活（无新实例双跑）")

# ── 3. 经代理发普通 RPC ────────────────────────────────────────────
proxy.stdin.write((json.dumps({
    "jsonrpc": "2.0", "method": "model.get_status", "id": "m1",
}) + "\n").encode("utf-8"))
proxy.stdin.flush()
resp = wait_response(proxy_out, lambda o: o.get("id") == "m1", timeout=20)
check(resp is not None and "model" in resp.get("result", {}),
      "普通 RPC 经代理往返（model.get_status）")

# 事件改道验证：发一条消息触发事件流（agent.thinking 等）到对接 socket
proxy.stdin.write((json.dumps({
    "jsonrpc": "2.0", "method": "agent.send_message",
    "params": {"message": "attach 测试：请只回一个字", "session_id": "default"},
    "id": "m2",
}) + "\n").encode("utf-8"))
proxy.stdin.flush()
check(wait_response(proxy_out, lambda o: o.get("id") == "m2", timeout=20) is not None,
      "send_message 经代理受理")
ev = wait_response(proxy_out,
                   lambda o: o.get("method") == "event"
                   and o.get("params", {}).get("type") in ("agent.thinking", "agent.step", "token.stats"),
                   timeout=60)
check(ev is not None, "事件流已改道到对接的 TUI（代理 stdout 收到事件）")

# ── 4. TUI 关闭只退出代理；显式 shutdown 才停止守护 ─────────────────
proxy.stdin.close()
check(proxy.wait(timeout=15) == 0, "代理 B 随 TUI 关闭退出（exit 0）")
time.sleep(0.5)
check(daemon.poll() is None, "TUI 关闭后守护 A 仍存活")
ping = rpc("gateway.ping")
check(ping.get("result", {}).get("pid") == daemon.pid,
      "TUI 关闭后 gateway.ping 正常且 PID 不变")
rpc("gateway.shutdown")
check(daemon.wait(timeout=30) == 0, "显式 gateway.shutdown 正常停止守护")

d_stop.set()
p_stop.set()

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    for p_ in (daemon, proxy):
        if p_.poll() is None:
            p_.kill()
    sys.exit(1)
