#!/usr/bin/env python3
"""haru 部署验收：无头驱动 gateway_entry.py，发一条消息，必须拿到真实模型回复。

用法: venv/bin/python deploy_acceptance.py ["消息内容"]
"""
import json
import subprocess
import sys
import threading
import time

MESSAGE = sys.argv[1] if len(sys.argv) > 1 else "你好，请用一句话介绍你自己，并告诉我你现在运行在什么设备上。"
TIMEOUT = 180

proc = subprocess.Popen(
    ["venv/bin/python", "-u", "Gateway/gateway_entry.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", bufsize=1,
)

state = {"ready": False, "answer": None, "error": None, "done": False}


def reader():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(f"[non-json] {line[:120]}", flush=True)
            continue
        if msg.get("method") == "event":
            etype = msg["params"].get("type")
            payload = msg["params"].get("payload") or {}
            if etype == "gateway.ready":
                state["ready"] = True
                print("[event] gateway.ready", flush=True)
            elif etype == "agent.ready":
                tools = payload.get("tools") or []
                print(f"[event] agent.ready (tools={len(tools)})", flush=True)
            elif etype == "agent.answer":
                state["answer"] = payload.get("answer", "")
                state["done"] = True
            elif etype == "message.complete":
                state["answer"] = state["answer"] or payload.get("answer", "")
                state["done"] = True
            elif etype in ("agent.error", "gateway.init_error"):
                state["error"] = payload.get("message", "unknown error")
                state["done"] = True
            elif etype in ("agent.step", "agent.llm_requesting", "tool.call"):
                detail = payload.get("tool", "") if etype == "tool.call" else ""
                print(f"[event] {etype} {detail}", flush=True)


threading.Thread(target=reader, daemon=True).start()

t0 = time.time()
while not state["ready"] and time.time() - t0 < 60:
    time.sleep(0.2)
if not state["ready"]:
    print("FAIL: 60s 内未收到 gateway.ready", flush=True)
    proc.kill()
    sys.exit(1)

req = {"jsonrpc": "2.0", "method": "agent.send_message",
       "params": {"message": MESSAGE, "session_id": "deploy_acceptance"}, "id": "1"}
proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
proc.stdin.flush()
print(f"[sent] {MESSAGE}", flush=True)

while not state["done"] and time.time() - t0 < TIMEOUT:
    time.sleep(0.2)

proc.kill()
if state["error"]:
    print(f"\nFAIL: {state['error']}", flush=True)
    sys.exit(1)
if not state["answer"]:
    print(f"\nFAIL: {TIMEOUT}s 内未拿到回复", flush=True)
    sys.exit(1)

print(f"\n=== 验收通过：模型回复 ===\n{state['answer']}", flush=True)
