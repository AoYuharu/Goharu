"""更新四态状态机契约测试：门控 / 状态流转 / 确认链 / 主会话封锁。

直接运行：python tests/test_update_flow.py
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import Architect.update_flow as uf  # noqa: E402
from Architect import create_architect_hub, WakeRequest  # noqa: E402
from Architect.audit import AuditPipeline  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


class FakeUpgrader:
    def __init__(self, *a, **kw):
        pass

    def fetch_staging(self):
        self_tmp = tempfile.mkdtemp()
        return {"ok": True, "staging_dir": self_tmp}

    def diff_summary(self):
        return "diff --stat: 2 files changed"

    def cleanup(self):
        return {"ok": True}


def make_hub(confirm=True, merges=None, dispatch_content="VERDICT: PASS"):
    pushes = []
    hub = create_architect_hub(
        config={"architect_hub": {
            "wake": {"cooldown_seconds": {}},
            "upgrade": {"confirm_timeout_seconds": 5, "quiet_seconds": 0},
        }},
        dispatch=lambda k, t: {"content": dispatch_content},
        merge_fn=merges.append if merges is not None else None,
    )
    hub.set_hero_bridge(notify_user=pushes.append)
    hub.set_runtime_hooks(
        idle_probe=lambda: True,
        confirm_fn=lambda cmd, desc, timeout: confirm,
    )
    hub.audit_pipeline = AuditPipeline(
        contract_tests=["a.py"],
        run_test=lambda t, c, to: {"test": t, "ok": True},
        dispatch=lambda k, t: {"content": dispatch_content},
    )
    return hub, pushes


def wait_done(hub, timeout=10):
    flow = hub._update_flow
    deadline = time.time() + timeout
    while time.time() < deadline:
        if flow and flow.state == "done":
            return flow
        time.sleep(0.05)
    return flow


def main():
    import Architect.upgrader as upmod
    orig_upgrader = upmod.Upgrader
    upmod.Upgrader = FakeUpgrader   # UpdateFlow 在函数内 from ... import，需替换模块属性

    print("== 门控 ==")
    hub, _ = make_hub()
    r = hub.start_update(source="auto", context={"behind": "1"}, force=False)
    check("落后不足 → below_threshold", r["status"] == "below_threshold")

    hub2, _ = make_hub()
    hub2.set_runtime_hooks(idle_probe=lambda: False, confirm_fn=None)
    r = hub2.start_update(source="auto", context={"behind": "5"}, force=False)
    check("主会话忙 → deferred", r["status"] == "deferred")

    print("== 完整流：确认通过 ==")
    merges = []
    hub3, pushes = make_hub(confirm=True, merges=merges)
    states = []
    orig_set = uf.UpdateFlow._set
    uf.UpdateFlow._set = lambda self, s: (states.append(s), orig_set(self, s))[1]
    try:
        r = hub3.start_update(source="manual", force=True,
                              context={"ref": "origin/master", "behind": "3",
                                       "remote_head": "abc123"})
        check("手动指令直接启动", r["status"] == "started")
        flow = wait_done(hub3)
    finally:
        uf.UpdateFlow._set = orig_set
    check("状态序列完整（四态）",
          states == ["gathering", "awaiting_confirm", "updating", "done"])
    check("outcome=success", flow.outcome == "success")
    check("merge_fn 被调用", len(merges) == 1)
    check("用户收到开始/计划/完毕通知",
          any("开始更新" in p for p in pushes)
          and any("等待确认" in p or "更新计划已写好" in p for p in pushes)
          and any("更新完毕" in p for p in pushes))
    check("终态后不再 active", not hub3.is_update_active())

    print("== UpdateFlow 复用代理提供器 ==")
    captured = {}

    class CaptureUpgrader(FakeUpgrader):
        def __init__(self, *a, **kw):
            captured.update(kw)

    provider = lambda: ("http://127.0.0.1:7893", ["github.com"])
    hubp, _ = make_hub(confirm=True)
    hubp.set_proxy_provider(provider)
    upmod.Upgrader = CaptureUpgrader
    try:
        hubp.start_update(source="manual", force=True)
        wait_done(hubp)
    finally:
        upmod.Upgrader = FakeUpgrader
    check("UpdateFlow 传入 proxy_provider",
          captured.get("proxy_provider") is provider)

    print("== 确认拒绝 ==")
    merges2 = []
    hub4, pushes4 = make_hub(confirm=False, merges=merges2)
    hub4.start_update(source="manual", force=True)
    flow4 = wait_done(hub4)
    check("拒绝 → cancelled", flow4.outcome == "cancelled")
    check("拒绝 → 不合并", merges2 == [])

    print("== 审计失败 ==")
    hub5, pushes5 = make_hub(dispatch_content="VERDICT: FAIL")
    hub5.start_update(source="manual", force=True)
    flow5 = wait_done(hub5)
    check("审计 FAIL → failed", flow5.outcome == "failed")
    check("审计 FAIL → 不进确认阶段", not any("等待确认" in p for p in pushes5))

    print("== 忙时互斥 ==")
    hub6, _ = make_hub()
    hub6._update_flow = type("F", (), {"active": True, "state": "gathering"})()
    r = hub6.start_update(source="manual", force=True)
    check("更新中再启动 → busy", r["status"] == "busy")

    print("== 主会话封锁 ==")
    from Gateway.session import AgentSession
    from Gateway.channels.base import Channel, ChannelMessage

    class FakeCh(Channel):
        channel_type = "qqbot"

        def send_answer(self, text, meta=None):
            pass

    import Architect.hub as hub_mod
    fake_hub = type("H", (), {"is_update_active": staticmethod(lambda: True)})()
    orig = hub_mod.get_default_hub
    hub_mod.get_default_hub = lambda: fake_hub
    try:
        s = __import__("Gateway.session", fromlist=["AgentSession"]).AgentSession(
            "default", None, None, None, None, None)
        s._start_processing = lambda: (_ for _ in ()).throw(
            AssertionError("更新期间不应启动处理"))
        s.attach_channel(FakeCh("qq_main"))
        s.enqueue(ChannelMessage(text="在吗", channel_type="qqbot",
                                 channel_id="qq_main"))
        check("更新期间消息挂起不中断",
              len(s.pending_items) == 1 and not s._interrupt_event.is_set())
    finally:
        hub_mod.get_default_hub = orig

    upmod.Upgrader = orig_upgrader
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
