"""Architect ErrorHook 规则引擎 + hub 唤醒/修复集成测试。"""

import sys
import tempfile
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
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def wait(cond, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def record(**over):
    base = {
        "id": "err_test", "fingerprint": "fp_test", "scope": "agent",
        "severity": "error", "blocking": "all_turns", "cause": "config",
        "source": "Gateway.session", "message": "quota exceeded",
        "traceback": "", "attempts": 0,
    }
    base.update(over)
    return base


class FakeGuard:
    def __init__(self):
        self.snapshots = 0
        self.restored = 0
        self.finished = 0
        self.verify_ok = True

    def snapshot(self):
        self.snapshots += 1
        return {"ok": True, "ref": "x"}

    def verify(self):
        return {"ok": self.verify_ok, "error": "" if self.verify_ok else "self-check failed"}

    def restore(self, snap):
        self.restored += 1
        return {"ok": True}

    def finish(self, snap):
        self.finished += 1


def main():
    from Architect.error_hooks import ErrorHookEngine
    from Architect.hub import ArchitectHub

    tmp = Path(tempfile.mkdtemp(prefix="error_hooks_test_"))
    engine = ErrorHookEngine(tmp / "hooks.json", tmp / "state.json")

    print("\n[1] 内置安全底线")
    rules = {r["id"]: r for r in engine.list_rules()}
    check("内置规则存在", {"builtin:fatal_process", "builtin:all_turns",
                        "builtin:runtime_logic", "builtin:repair_failed"} <= set(rules),
          str(list(rules)))
    for rid in list(rules):
        if rules[rid]["protected"]:
            try:
                engine.remove_rule(rid)
                ok = False
            except PermissionError:
                ok = True
            check(f"{rid} 不可删除" if rid == "builtin:fatal_process" else
                  f"{rid} protected", ok)
    try:
        engine.update_rule("builtin:all_turns", enabled=False)
        check("受保护规则不可禁用", False)
    except PermissionError:
        check("受保护规则不可禁用", True)

    print("\n[2] 匹配、冷却、次数")
    fatal = record(blocking="fatal", cause="config")
    hit = engine.evaluate(fatal)
    check("fatal 命中", hit and hit["rule"]["id"] == "builtin:fatal_process",
          str(hit))
    engine.mark_wake(fatal["fingerprint"], hit["rule"]["id"])
    check("冷却内不再命中", engine.evaluate(fatal) is None)
    # 另一个 fingerprint 的自定义规则
    custom = engine.add_rule({
        "name": "channel network", "match": {"scope": "channel", "cause": "external"},
        "action": {"cooldown_seconds": 0, "max_attempts": 1, "repair": False},
    })
    ch = record(id="err_ch", fingerprint="fp_ch", scope="channel",
                blocking="channel", cause="external", message="ws down")
    hit2 = engine.evaluate(ch)
    check("自定义规则命中", hit2 and hit2["rule"]["id"] == custom["id"], str(hit2))
    engine.mark_wake(ch["fingerprint"], custom["id"])
    check("超过 max_attempts 不再命中", engine.evaluate(ch) is None)
    engine.update_rule(custom["id"], enabled=False)
    check("自定义规则可禁用", engine.evaluate(
        dict(ch, fingerprint="fp_ch2")) is None)
    check("自定义规则可删除", engine.remove_rule(custom["id"]))

    print("\n[3] Hub 唤醒 + 修复护栏")
    dispatch_calls = []
    statuses = []

    def fake_dispatch(kind, text):
        dispatch_calls.append((kind, text))
        return {"type": "answer", "content": "fixed", "status": "success"}

    hub = ArchitectHub(
        {"architect_hub": {"error_hooks": {"path": str(tmp / "hub_hooks.json")}}},
        dispatch=fake_dispatch)
    guard = FakeGuard()
    hub.set_error_hooks(engine=engine,
                        evidence_fn=lambda rec: {"gateway": "log tail"},
                        status_fn=lambda eid, **f: statuses.append((eid, f)),
                        repair_guard=guard)
    rec = record(id="err_fix", fingerprint="fp_fix")
    ok = hub.ingest_error(rec)
    check("hub 受理错误唤醒", ok)
    check("上报 dispatched", wait(lambda: any(k == "repair" for k, _ in dispatch_calls)),
          str(dispatch_calls))
    check("修复前快照", wait(lambda: guard.snapshots == 1), str(guard.snapshots))
    check("修复成功标记", wait(lambda: any(
        f.get("wake_status") == "repair_done" for _, f in statuses)), str(statuses))
    check("成功清理快照", guard.finished == 1 and guard.restored == 0)
    check("任务文本含错误与修复约束", dispatch_calls and "quota exceeded" in dispatch_calls[0][1]
          and "直接改变原始代码" in dispatch_calls[0][1], str(dispatch_calls)[:400])

    print("\n[4] 修复失败回滚")
    dispatch_calls.clear()
    statuses.clear()
    guard2 = FakeGuard()
    guard2.verify_ok = False
    hub.set_error_hooks(engine=engine, repair_guard=guard2,
                        status_fn=lambda eid, **f: statuses.append((eid, f)))
    rec2 = record(id="err_bad", fingerprint="fp_bad", message="bad repair")
    ok = hub.ingest_error(rec2)
    check("失败案例受理", ok)
    check("失败触发回滚", wait(lambda: guard2.restored == 1), str(guard2.restored))
    check("失败状态回写", wait(lambda: any(
        f.get("wake_status") == "repair_failed" for _, f in statuses)), str(statuses))
    check("失败不清理快照", guard2.finished == 0)

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
