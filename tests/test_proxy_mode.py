"""proxy 模式（TUI spawn 的 gateway 退化为代理）契约测试。

回归背景（2026-11-25）：daemon 的 _serve_client 曾用 conn.settimeout(300)，
TUI 空闲 300s 后 recv 超时（socket.timeout 是 OSError 子类）被静默吞掉，
proxy↔daemon 连接被掐断；proxy 主线程仍阻塞在 stdin 读，变僵尸，直到 TUI
下次发请求 sendall 失败才退出（exit=0），被 TUI 误报 "crashed unexpectedly"。

修复：
1. 移除 daemon 的 300s recv 超时（空闲不再断开）。
2. proxy 用 stdin/socket 双读线程，任一方向断开立即退出（不再僵尸）。
3. TUI 对 exit=0 静默（见 GatewayClient.ts）。

本文件覆盖第 2 点，并验证 TUI 关闭只断开 proxy，不向 daemon 发停止请求。

运行：python tests/test_proxy_mode.py
"""
import importlib.util
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:200]}")


def load_gateway_entry():
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        spec = importlib.util.spec_from_file_location(
            "gentry_proxy_test", ROOT / "Gateway" / "gateway_entry.py")
        ge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ge)
    finally:
        os.chdir(cwd)
    return ge


class _BlockingStdin:
    """永不 EOF 的 stdin（模拟 TUI 空闲，无输入）。"""
    def __iter__(self):
        return self

    def __next__(self):
        threading.Event().wait()  # 永远阻塞


class _EmptyStdin:
    """立即 EOF 的 stdin（模拟 TUI 关闭）。"""
    def __iter__(self):
        return iter(())


def _fake_daemon(port_holder, close_after_attach, received, shutdown_event):
    """假守护：接受连接，回 attached=true；close_after_attach 则随后断开，
    否则保持连接读后续（tui.shutdown）。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder["port"] = srv.getsockname()[1]
    conn, _ = srv.accept()
    conn.settimeout(3)
    try:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        received.append(data.decode("utf-8", errors="replace").strip())
        conn.sendall(b'{"jsonrpc":"2.0","result":{"attached":true},"id":"attach"}\n')
        if close_after_attach:
            time.sleep(0.2)
            conn.close()
            return
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        received.append(data.decode("utf-8", errors="replace").strip())
        if "tui.shutdown" in received[-1]:
            shutdown_event.set()
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        srv.close()


def _wait_port(port_holder, timeout=3.0):
    deadline = time.time() + timeout
    while "port" not in port_holder and time.time() < deadline:
        time.sleep(0.02)
    return port_holder.get("port")


def test_proxy_exits_on_daemon_disconnect(ge):
    print("\n[1] proxy 在 daemon 断开后立即退出（不僵尸等待 stdin）")
    port_holder, received = {}, []
    t = threading.Thread(
        target=_fake_daemon,
        args=(port_holder, True, received, threading.Event()),
        daemon=True)
    t.start()
    port = _wait_port(port_holder)
    check("假守护已监听", port is not None)

    old_stdin = sys.stdin
    sys.stdin = _BlockingStdin()
    result = {}

    def run():
        result["code"] = ge._run_proxy_mode(port)

    rt = threading.Thread(target=run, daemon=True)
    start = time.time()
    rt.start()
    rt.join(timeout=5)
    elapsed = time.time() - start
    sys.stdin = old_stdin

    check("proxy 在 daemon 断开后 5s 内退出", not rt.is_alive(),
          f"仍存活 elapsed={elapsed:.1f}s")
    check("退出码为 0", result.get("code") == 0, str(result))
    check("退出足够快（<3s，未傻等 stdin）", elapsed < 3, f"{elapsed:.1f}s")


def test_proxy_detaches_on_tui_close(ge):
    print("\n[2] TUI 关闭（stdin EOF）→ proxy 退出，但不请求停止 daemon")
    port_holder, received = {}, []
    shutdown_event = threading.Event()
    t = threading.Thread(
        target=_fake_daemon,
        args=(port_holder, False, received, shutdown_event),
        daemon=True)
    t.start()
    port = _wait_port(port_holder)
    check("假守护已监听", port is not None)

    old_stdin = sys.stdin
    sys.stdin = _EmptyStdin()
    result = {}

    def run():
        result["code"] = ge._run_proxy_mode(port)

    rt = threading.Thread(target=run, daemon=True)
    rt.start()
    rt.join(timeout=5)
    sys.stdin = old_stdin

    check("proxy 退出", not rt.is_alive())
    check("退出码为 0", result.get("code") == 0, str(result))
    check("proxy 未发 tui.shutdown", not shutdown_event.wait(timeout=0.2)
          and all("tui.shutdown" not in line for line in received), str(received))


def test_legacy_shutdown_request_only_detaches(ge):
    print("\n[3] 旧 proxy 的 tui.shutdown 也只 detach，不停止 gateway")
    server = ge.TuiRpcServer(0)
    check("RPC server 启动", server.start())
    port = server._server.getsockname()[1]
    ge._bg_reader_running = True
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.settimeout(3)

    def request(obj):
        sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        while True:
            data = b""
            while b"\n" not in data:
                data += sock.recv(4096)
            response = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            if response.get("id") == obj["id"]:
                return response

    try:
        attached = request({"jsonrpc": "2.0", "method": "tui.attach", "id": "a"})
        check("attach 成功", attached.get("result", {}).get("attached") is True)
        response = request({"jsonrpc": "2.0", "method": "tui.shutdown", "id": "bye"})
        check("旧停止请求被降级为 detach",
              response.get("result", {}).get("detached") is True
              and response.get("result", {}).get("shutting_down") is False,
              str(response))
        check("旧停止请求不改变网关主循环", ge._bg_reader_running is True)
        sock.close()
        time.sleep(0.2)
        check("attached socket 断开不改变网关主循环", ge._bg_reader_running is True)
    finally:
        try:
            sock.close()
        except OSError:
            pass
        server._server.close()


def main():
    ge = load_gateway_entry()
    test_proxy_exits_on_daemon_disconnect(ge)
    test_proxy_detaches_on_tui_close(ge)
    test_legacy_shutdown_request_only_detaches(ge)
    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
