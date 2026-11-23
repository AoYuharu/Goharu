#!/usr/bin/env python3
"""端到端验收：真起 Gateway 子进程，发一条需要工具的消息，验证「成功回复 + 调用了工具」。

用法：cd <repo> && python3 e2e_gateway_test.py
依赖：仓库已安装（venv + 依赖 + .env 填好 key）。
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

CWD = os.getcwd()
VENV_PY = (os.path.join(CWD, "venv", "Scripts", "python.exe")
           if os.name == "nt" else os.path.join(CWD, "venv", "bin", "python"))
PROMPT = "请用 Read 工具读取当前目录的 requirements.txt 文件，然后告诉我它的第一行内容是什么。必须真的调用工具读取，不要凭猜测。"


def main() -> int:
    if not os.path.exists(VENV_PY):
        print("FAIL: venv 不存在，未安装")
        return 2
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHON"] = VENV_PY

    proc = subprocess.Popen(
        [VENV_PY, "-u", os.path.join(CWD, "Gateway", "gateway_entry.py")],
        cwd=CWD, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    q: "queue.Queue[dict]" = queue.Queue()

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                q.put(json.loads(line))
            except Exception:
                pass

    threading.Thread(target=reader, daemon=True).start()

    # 记录 stderr（崩溃时排查）
    stderr_buf = []
    def er():
        for line in proc.stderr:
            stderr_buf.append(line.rstrip())
            if len(stderr_buf) > 100:
                stderr_buf.pop(0)
    threading.Thread(target=er, daemon=True).start()

    # 等 gateway 就绪
    ready = False
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            msg = q.get(timeout=2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if msg.get("method") == "event":
            t = msg["params"]["type"]
            if t in ("gateway.ready", "agent.ready"):
                ready = True
                break
            if t == "gateway.init_error":
                print("FAIL: gateway.init_error:", msg["params"]["payload"])
                proc.kill()
                return 1
    if not ready:
        print("FAIL: gateway 未就绪")
        print("\n".join(stderr_buf[-20:]))
        proc.kill()
        return 1
    print("[ready] gateway 已就绪")

    # 发消息
    req = {"jsonrpc": "2.0", "method": "agent.send_message",
           "params": {"message": PROMPT, "session_id": "default"}, "id": "1"}
    proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
    proc.stdin.flush()

    tool_calls = []
    answer = None
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            msg = q.get(timeout=3)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if msg.get("method") != "event":
            continue
        p = msg["params"]
        t = p["type"]
        pl = p.get("payload", {}) or {}
        if t == "tool.call":
            tool_calls.append(pl.get("tool", "?"))
            print(f"  [tool.call] {pl.get('tool')}")
        elif t == "agent.answer":
            answer = pl.get("answer", "")
            if answer:
                break
        elif t == "message.complete":
            if pl.get("answer"):
                answer = pl["answer"]
            break
        elif t == "agent.error":
            print("  [agent.error]", pl.get("message"))

    proc.kill()
    print("\n=== 验收结果 ===")
    print("调用的工具:", tool_calls or "（无）")
    print("回复:", (answer or "").strip()[:300] if answer else "（无）")
    ok_tool = bool(tool_calls)
    ok_reply = bool(answer and answer.strip())
    if ok_tool and ok_reply:
        print("VERDICT: PASS（有回复 + 调用了工具）")
        return 0
    print(f"VERDICT: FAIL（回复={ok_reply}, 工具={ok_tool}）")
    if stderr_buf:
        print("--- stderr 尾部 ---")
        print("\n".join(stderr_buf[-15:]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
