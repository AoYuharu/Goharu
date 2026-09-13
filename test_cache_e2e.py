"""E2E 验证缓存修复：驱动 gateway 发两轮消息，检查 token.stats 新字段与跨 turn 缓存命中。"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).parent
# 隔离配置：独立的 memory root / logs，不干扰正在运行的会话
os.environ["CONFIG_FILE"] = str(PROJECT / "config_e2e_cache.yaml")
err_log = open(PROJECT / "_e2e_gateway_stderr.log", "w", encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, str(PROJECT / "Gateway" / "gateway_entry.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err_log,
    text=True, encoding="utf-8", cwd=PROJECT,
)

events = []
token_stats = []


def reader():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        params = msg.get("params") or {}
        etype = params.get("type")
        if etype:
            events.append(etype)
            if etype == "token.stats":
                token_stats.append(params.get("payload", {}))


t = threading.Thread(target=reader, daemon=True)
t.start()


def send_message(text, req_id):
    req = {"jsonrpc": "2.0", "id": req_id, "method": "agent.send_message",
           "params": {"message": text, "session_id": "default"}}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()


def wait_complete(timeout=300):
    deadline = time.time() + timeout
    base = len([e for e in events if e == "message.complete"])
    while time.time() < deadline:
        if len([e for e in events if e == "message.complete"]) > base:
            return True
        if proc.poll() is not None:
            print(f"gateway exited rc={proc.returncode}", flush=True)
            return False
        time.sleep(1)
    return False


print("turn 1: sending...", flush=True)
send_message("用一句话回答：1+1等于几？", 1)
# 700s：新进程首次检索要冷加载 embedding 模型（实测 ~310s；检索关闭时无需）
ok1 = wait_complete(timeout=700)
print(f"turn 1 complete={ok1}", flush=True)

print("turn 2: sending...", flush=True)
send_message("再算一个：2+2等于几？还是一句话。", 2)
ok2 = wait_complete(timeout=300)
print(f"turn 2 complete={ok2}", flush=True)

print("turn 3: sending...", flush=True)
send_message("最后一题：3+3等于几？一句话。", 3)
ok3 = wait_complete(timeout=300)
print(f"turn 3 complete={ok3}", flush=True)

proc.terminate()
time.sleep(1)

print("\n=== token.stats payloads ===")
for s in token_stats:
    print(json.dumps(s, ensure_ascii=False))
