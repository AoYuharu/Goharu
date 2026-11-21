"""AgentNet 真实组网端到端验收（需要 hub 在线：159.75.26.204:6000）。

流程：拉起真实 Gateway（agentnet channel 上线）→ tester 智能体经公网 hub
给 goharu 发私聊 → goharu 唤醒并走真实 LLM 回答 → 回答经 channel
send_answer 中继回 tester → 断言 tester 收到非空回复 + presence 可见双端。

运行：python tests/test_agentnet_live_e2e.py
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).parent.parent
HUB_URL = "ws://159.75.26.204:6000"

responses = []
events = []
stderr_buf = []

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


def _reader(proc):
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in obj:
            responses.append(obj)
        elif obj.get("method") == "event":
            events.append(obj)


def _stderr_drain(proc):
    for line in proc.stderr:
        stderr_buf.append(line)
        del stderr_buf[:-50]


def _rpc(proc, req_id, method, params=None, timeout=180):
    req = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params is not None:
        req["params"] = params
    proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for r in responses:
            if r.get("id") == req_id:
                return r
        time.sleep(0.1)
    return None


def main():
    from AgentNet.client import AgentNetClient
    from AgentNet.identity import Identity

    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "Gateway/gateway_entry.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, cwd=str(REPO), env=env,
    )
    threading.Thread(target=_reader, args=(proc,), daemon=True).start()
    threading.Thread(target=_stderr_drain, args=(proc,), daemon=True).start()

    try:
        resp = _rpc(proc, 1, "model.get_status", timeout=240)
        daemon_live = any("existing gateway detected" in ln for ln in stderr_buf)
        form = "代理" if daemon_live else "子进程"
        check((resp is not None and "result" in resp) or daemon_live,
              f"gateway 可用（{form}；常驻守护已在服务）")

        # tester 智能体上线（公网 hub）
        tester = Identity.load_or_create(str(REPO / "runtime_memory/agentnet/tester_identity"),
                                         name="tester")
        goharu_id = Identity.load_or_create(
            str(REPO / "runtime_memory/agentnet/identity")).agent_id

        received = []
        loop = asyncio.new_event_loop()

        def _tester_thread():
            asyncio.set_event_loop(loop)
            client = AgentNetClient(tester, HUB_URL, heartbeat_seconds=30,
                                    on_urgent=lambda e, c: received.append((e, c)))
            loop.create_task(client.connect_forever())
            holder["client"] = client
            try:
                loop.run_forever()
            finally:
                loop.close()

        holder: dict = {}
        t = threading.Thread(target=_tester_thread, daemon=True)
        t.start()
        time.sleep(3)
        client = holder.get("client")
        check(client is not None and client.connected, "tester 经公网上线")

        # tester → goharu 私聊；等待回答（对端 gateway 若离线，消息进 mailbox，
        # 上线拉取后回答——等待窗口放宽）
        async def _send():
            await client.send_message(
                goharu_id, "组网测试：请只回复「组网成功」四个字。", kind="dm")
        asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=10)

        deadline = time.time() + 300
        while time.time() < deadline and not received:
            time.sleep(1)
        check(bool(received), "tester 收到 goharu 的中继回答")
        if received:
            text = received[0][0].get("payload", {}).get("text", "")
            print(f"  goharu 回答: {text[:100]}")
            check(bool(text.strip()), "回答非空")

        # presence：消息闭环后 goharu 必然已在线，此处校验状态机可见性
        goharu_online = False
        deadline = time.time() + 60
        while time.time() < deadline and not goharu_online:
            try:
                async def _query():
                    return await client.query_presence()
                presence = asyncio.run_coroutine_threadsafe(_query(), loop).result(timeout=15)
                goharu_online = bool(presence.get(goharu_id, {}).get("online"))
            except Exception:
                pass
            if not goharu_online:
                time.sleep(2)
        check(goharu_online, "presence 可见 goharu 在线")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not all(ok for ok, _ in results) and stderr_buf:
            print(f"\n[gateway stderr 末尾]\n{''.join(stderr_buf)[-600:]}")

    failed = [n for ok, n in results if not ok]
    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
