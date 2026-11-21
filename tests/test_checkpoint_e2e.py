"""Checkpoint 端到端验收：真实 Gateway 子进程 + 真实 LLM。

流程：
1. spawn Gateway/gateway_entry.py（JSON-RPC over stdio，剥离 harness 的
   ANTHROPIC_* 环境变量——harness 泄漏的 token 会导致 provider 401）
2. 等 initialize 完成，验证三个 checkpoint 工具已注册
3. 消息1：让模型用 Write 创建探针文件 → 断言文件落盘 + checkpoint manifest
   记录该文件（source=hook:Write）+ TUI 侧收到 checkpoint.created 事件
4. 消息2：让模型自主 ListCheckpoints → RollbackCheckpoint(confirm=true)
   → 断言探针文件被回退删除，且生成 kind=rollback 的新 checkpoint

注意：本测试打真实 LLM（约 2 轮对话），并写入真实 runtime_memory
（测试对话会进 L0/L1——内容已标注为测试探针）。

运行：python tests/test_checkpoint_e2e.py
"""

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
PROBE_REL = "runtime_memory/temp/e2e_ckpt_probe.txt"
PROBE_ABS = REPO / "runtime_memory" / "temp" / "e2e_ckpt_probe.txt"
CKPT_DIR = REPO / "runtime_memory" / "checkpoints"

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


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


def _wait_answer(proc, timeout=300, after=0):
    """等待 after 下标之后新到的 agent.answer 事件（answer 即 turn 结束）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in events[after:]:
            if e.get("params", {}).get("type") == "agent.answer":
                return e["params"]["payload"].get("answer", "")
        time.sleep(0.5)
    return None


def _events_of(event_type):
    return [e["params"]["payload"] for e in events
            if e.get("params", {}).get("type") == event_type]


def _manifests():
    out = []
    if not CKPT_DIR.is_dir():
        return out
    for d in CKPT_DIR.iterdir():
        mp = d / "manifest.json"
        if d.is_dir() and mp.exists():
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except ValueError:
                pass
    out.sort(key=lambda m: m.get("seq") or 0)
    return out


def main():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANTHROPIC_")}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "Gateway/gateway_entry.py"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, cwd=str(REPO), env=env,
    )
    threading.Thread(target=_reader, args=(proc,), daemon=True).start()
    threading.Thread(target=_stderr_drain, args=(proc,), daemon=True).start()

    try:
        # 1. 初始化（facade 全装配）
        resp = _rpc(proc, 1, "model.get_status", timeout=240)
        init_err = next((p for p in _events_of("gateway.init_error")), None)
        check(resp is not None and "result" in resp,
              f"gateway 初始化成功（init_error={init_err}）")
        if resp is None:
            print("初始化失败，终止", "".join(stderr_buf)[-500:])
            return

        # 2. checkpoint 工具已注册
        resp = _rpc(proc, 2, "tools.list", timeout=60)
        names = [t.get("name") for t in resp["result"].get("tools", [])]
        check("ListCheckpoints" in names and "RollbackCheckpoint" in names
              and "ShowCheckpoint" in names,
              "checkpoint 三工具已注册")

        baseline_seqs = {m["seq"] for m in _manifests()}

        # 3. 消息1：Write 探针文件
        _rpc(proc, 3, "agent.send_message", {
            "message": (
                f"这是一个自动化测试。请用 Write 工具创建文件 {PROBE_REL}，"
                "内容就一行：hello-checkpoint。创建成功后回复“已创建”两个字即可，"
                "不要做其他任何事。"
            )})
        answer1 = _wait_answer(proc, timeout=300)
        check(answer1 is not None, f"消息1收到模型回复: {str(answer1)[:60]}")
        check(PROBE_ABS.exists(), "探针文件已落盘")

        # turn 落盘 finalize 在 finally 里，answer 事件后很快完成——稍等
        time.sleep(3)
        new_manifests = [m for m in _manifests() if m["seq"] not in baseline_seqs]
        turn_m = next((m for m in new_manifests if m.get("kind") == "turn"), None)
        check(turn_m is not None, "消息1生成 turn checkpoint manifest")
        probe_op = None
        if turn_m:
            probe_op = next(
                (f for f in turn_m.get("files", []) if "e2e_ckpt_probe" in f["path"]),
                None)
        check(probe_op is not None and probe_op["op"] == "create"
              and probe_op["source"].startswith("hook:"),
              f"探针文件以 hook 源入账（op={probe_op and probe_op['op']}）")
        check(len(_events_of("checkpoint.created")) >= 1,
              "TUI 收到 checkpoint.created 事件")
        target_seq = turn_m["seq"] if turn_m else None

        # 4. 消息2：模型自主回退
        marker = len(events)
        _rpc(proc, 4, "agent.send_message", {
            "message": (
                f"继续测试：刚才创建的 {PROBE_REL} 是误操作。"
                "请先用 ListCheckpoints 查看最近的 checkpoint，找到记录该文件创建的"
                f"那个 turn checkpoint（其 seq 应为 {target_seq}），然后调用 "
                f"RollbackCheckpoint(seq={target_seq - 1 if target_seq else 0}, confirm=true) "
                "把它撤销。完成后回复“已回退”。"
            )})
        answer2 = _wait_answer(proc, timeout=300, after=marker)
        check(answer2 is not None, f"消息2收到模型回复: {str(answer2)[:60]}")
        time.sleep(3)
        check(not PROBE_ABS.exists(), "回退后探针文件被删除")
        rb = [m for m in _manifests()
              if m["seq"] not in baseline_seqs and m.get("kind") == "rollback"]
        check(len(rb) >= 1, "生成 kind=rollback 的 checkpoint（redo 能力）")
        check(len(_events_of("checkpoint.rollback.completed")) >= 1,
              "TUI 收到 checkpoint.rollback.completed 事件")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        PROBE_ABS.unlink(missing_ok=True)
        if stderr_buf:
            print(f"\n[gateway stderr 末尾]\n{''.join(stderr_buf)[-400:]}")

    failed = [n for ok, n in results if not ok]
    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
