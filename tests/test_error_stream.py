"""HaruNet 错误流：error_report 验签入库 + observer /api/errors + 页面标记。"""

import asyncio
import json
import socket
import sys
import tempfile
import time
import urllib.request
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
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_conn(client, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.connected:
            return True
        await asyncio.sleep(0.05)
    return False


async def main_async(root: Path):
    from AgentNet.client import AgentNetClient
    from AgentNet.identity import Identity
    from AgentNet.protocol import canonical_bytes, make_envelope
    from AgentNet.server.hub import HubServer
    from AgentNet.server.observer import ObserverServer

    port, oport = free_port(), free_port()
    a = Identity.load_or_create(root / "err_a", name="alpha")
    wl = root / "wl_err.json"
    wl.write_text(json.dumps({"agents": {a.agent_id: {"name": "alpha",
                                                      "pubkey": a.pubkey_b64}}}),
                  encoding="utf-8")
    hub = HubServer("127.0.0.1", port, wl, root / "hub_state_err")
    obs = ObserverServer(hub.message_log, hub.presence, host="127.0.0.1",
                         port=oport, file_store=hub.files, whitelist=hub.whitelist,
                         error_log=hub.errors)
    obs.start()
    serve = asyncio.create_task(hub.serve())
    await asyncio.sleep(0.3)
    ca = AgentNetClient(a, f"ws://127.0.0.1:{port}", heartbeat_seconds=3600)
    ta = asyncio.create_task(ca.connect_forever())

    def get_errors(after=0):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{oport}/api/errors?after={after}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))["errors"]

    try:
        check("上报端上线", await wait_conn(ca))
        rec = {
            "id": "err_stream_1", "fingerprint": "fp1", "ts": "2026-11-24T12:00:00",
            "scope": "agent", "severity": "error", "blocking": "all_turns",
            "cause": "config", "source": "llm", "session": "default",
            "message": "quota exceeded", "traceback": "", "attempts": 0,
            "status": "new", "wake_status": "none", "updated_ts": "2026-11-24T12:00:00",
        }
        await ca.send_error_report(rec)
        deadline = time.time() + 5
        rows = []
        while time.time() < deadline:
            rows = await asyncio.to_thread(get_errors)
            if rows:
                break
            await asyncio.sleep(0.05)
        check("错误记录入库", len(rows) == 1 and rows[0]["error_id"] == "err_stream_1",
              str(rows))
        if rows:
            check("观察台字段齐全", rows[0]["blocking"] == "all_turns"
                  and rows[0]["cause"] == "config"
                  and rows[0]["agent_name"] == "alpha", str(rows[0]))

        rec2 = dict(rec, status="woken", wake_status="repairing", attempts=1,
                    message="quota exceeded (updated)")
        await ca.send_error_report(rec2)
        deadline = time.time() + 5
        while time.time() < deadline:
            rows = await asyncio.to_thread(get_errors)
            if rows and rows[0].get("wake_status") == "repairing":
                break
            await asyncio.sleep(0.05)
        check("同 id upsert 更新状态", len(rows) == 1
              and rows[0]["wake_status"] == "repairing"
              and "updated" in rows[0]["message"], str(rows))

        # 伪造签名被拒（直接 WS 发 error_report）
        import websockets
        from AgentNet.protocol import dumps

        bad = Identity.load_or_create(root / "err_bad", name="bad")
        env = make_envelope(sender=a.agent_id, to="server", type_="error_report",
                            payload={"record": dict(rec, id="err_forged")})
        env["sig"] = bad.sign(canonical_bytes(env))
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(dumps(env))
            await asyncio.sleep(0.2)
        rows = await asyncio.to_thread(get_errors)
        check("伪造签名不入库", all(r["error_id"] != "err_forged" for r in rows),
              str([r["error_id"] for r in rows]))

        page = (ROOT / "AgentNet" / "server" / "observer_page.html").read_text(
            encoding="utf-8")
        check("页面含错误 tab", 'data-f="errors"' in page and "renderErrors" in page
              and "/api/errors" in page)
    finally:
        await ca.close()
        ta.cancel()
        serve.cancel()
        for t in (ta, serve):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


def main():
    root = Path(tempfile.mkdtemp(prefix="error_stream_test_"))
    asyncio.run(main_async(root))
    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
