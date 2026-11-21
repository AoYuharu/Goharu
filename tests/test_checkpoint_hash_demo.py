"""Checkpoint 哈希保真实验（真实 Gateway 子进程 + 真实 LLM）。

流程：让模型写一段代码 → 记哈希 H1 → 让模型改它 → 记哈希 H2
→ 让模型回退 → 断言哈希 == H1 → 再撤销回退（redo）→ 断言哈希 == H2。

运行：python tests/test_checkpoint_hash_demo.py
"""

import hashlib
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
PROBE_REL = "runtime_memory/temp/hash_demo.py"
PROBE_ABS = REPO / "runtime_memory" / "temp" / "hash_demo.py"
CKPT_DIR = REPO / "runtime_memory" / "checkpoints"

responses = {}
events = []
stderr_buf = []


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
            responses[obj["id"]] = obj
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
        if req_id in responses:
            return responses[req_id]
        time.sleep(0.1)
    return None


def _wait_answer(timeout=300, after=0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in events[after:]:
            if e.get("params", {}).get("type") == "agent.answer":
                return e["params"]["payload"].get("answer", "")
        time.sleep(0.5)
    return None


def _sha() -> str:
    return hashlib.sha256(PROBE_ABS.read_bytes()).hexdigest()[:16]


def _manifests():
    out = []
    for d in CKPT_DIR.iterdir():
        mp = d / "manifest.json"
        if d.is_dir() and mp.exists():
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except ValueError:
                pass
    out.sort(key=lambda m: m.get("seq") or 0)
    return out


def _probe_seqs():
    """含探针文件的 turn checkpoint：(create_seq, modify_seq)。"""
    create_seq = modify_seq = None
    for m in _manifests():
        if m.get("kind") != "turn":
            continue
        for f in m.get("files", []):
            if "hash_demo" in f["path"]:
                if f["op"] == "create" and create_seq is None:
                    create_seq = m["seq"]
                elif f["op"] == "modify":
                    modify_seq = m["seq"]
    return create_seq, modify_seq


def main():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "Gateway/gateway_entry.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, cwd=str(REPO), env=env,
    )
    threading.Thread(target=_reader, args=(proc,), daemon=True).start()
    threading.Thread(target=_stderr_drain, args=(proc,), daemon=True).start()

    ok = True
    try:
        resp = _rpc(proc, 1, "model.get_status", timeout=240)
        if resp is None:
            print("FAIL: gateway 初始化失败")
            return 1

        def say(req_id, msg):
            marker = len(events)
            _rpc(proc, req_id, "agent.send_message", {"message": msg})
            ans = _wait_answer(timeout=300, after=marker)
            print(f"  模型回复: {str(ans)[:80]}")
            time.sleep(3)  # 等 finally 里的 end_turn 落盘
            return ans

        # T1: 写代码
        print("T1: 让模型写一段 fizzbuzz 代码…")
        say(2, f"这是自动化实验。请用 Write 工具创建文件 {PROBE_REL}，"
               "写一个 fizzbuzz(n) 函数（1 到 n，3 的倍数 Fizz、5 的倍数 Buzz、"
               "15 的倍数 FizzBuzz）。写完回复“完成”，别做其他事。")
        if not PROBE_ABS.exists():
            print("FAIL: T1 文件未创建")
            return 1
        h1 = _sha()
        print(f"  H1 = {h1}")

        # T2: 改代码
        print("T2: 让模型修改它（加 main 入口）…")
        say(3, f"继续实验：请用 Edit 工具给 {PROBE_REL} 末尾加一个 "
               'if __name__ == "__main__": 入口，调用 fizzbuzz(20)。'
               "改完回复“完成”，别做其他事。")
        h2 = _sha()
        print(f"  H2 = {h2}")
        if h2 == h1:
            print("FAIL: T2 修改未生效（H2 == H1）")
            return 1

        create_seq, modify_seq = _probe_seqs()
        print(f"  入账: create=cp#{create_seq} modify=cp#{modify_seq}")
        if modify_seq is None:
            print("FAIL: 未找到 modify checkpoint")
            return 1

        # T3: 回退 T2 → 应回到 H1
        print(f"T3: 让模型回退到 cp#{modify_seq - 1}（撤销 T2 修改）…")
        say(4, f"继续实验：刚才对 {PROBE_REL} 的修改需要撤销。请调用 "
               f"RollbackCheckpoint(seq={modify_seq - 1}, confirm=true) 回退它。"
               "完成回复“已回退”。")
        h3 = _sha()
        print(f"  回退后哈希 = {h3}")
        r1 = h3 == h1
        print(f"  {'PASS' if r1 else 'FAIL'}: 回退保真（H3 {'==' if r1 else '!='} H1）")
        ok = ok and r1

        # T4: redo —— 撤销 T3 的 rollback checkpoint → 应回到 H2
        rb_seqs = [m["seq"] for m in _manifests() if m.get("kind") == "rollback"]
        if rb_seqs:
            rb_seq = max(rb_seqs)
            print(f"T4: 让模型撤销回退（redo，回退 cp#{rb_seq}）…")
            say(5, f"最后一步：刚才的回退本身也生成了 checkpoint（seq={rb_seq}）。"
                   f"请调用 RollbackCheckpoint(seq={rb_seq - 1}, confirm=true) 撤销这次回退。"
                   "完成回复“已重做”。")
            h4 = _sha()
            print(f"  redo 后哈希 = {h4}")
            r2 = h4 == h2
            print(f"  {'PASS' if r2 else 'FAIL'}: redo 保真（H4 {'==' if r2 else '!='} H2）")
            ok = ok and r2

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        PROBE_ABS.unlink(missing_ok=True)
        if not ok and stderr_buf:
            print(f"\n[stderr 末尾]\n{''.join(stderr_buf)[-400:]}")

    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
