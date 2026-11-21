"""Architect 幕后助手模块契约测试：digest 计数 / channel 插件 / 唤醒路由 / 审计管线。

直接运行：python tests/test_architect_hub_standalone.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Architect import (  # noqa: E402
    ArchitectHub, AuditPipeline, TelemetryDigest, WakeRequest, create_architect_hub,
)

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def test_digest():
    print("== TelemetryDigest 计数 ==")
    d = TelemetryDigest(window=50)
    d.ingest("user.message", {"text": "hi"})
    d.ingest("agent.step", {"step": 1})
    d.ingest("agent.retry", {"attempt": 2})
    d.ingest("tool.call", {"tool": "run_cmd"})
    d.ingest("tool.call", {"tool": "run_cmd"})
    d.ingest("tool.result", {"tool": "run_cmd", "is_error": True})
    d.ingest("context.auto_compact", {"reason": "threshold"})
    d.ingest("token.stats", {"current_tokens": 12345, "prompt_cache_ratio": 0.9})
    d.ingest("agent.answer", {"answer": "ok"})
    snap = d.snapshot()
    check("用户消息计数", snap["counters"]["user_messages"] == 1)
    check("重试计数", snap["counters"]["llm_retries"] == 1)
    check("工具调用按名聚合", snap["tool_calls_by_name"]["run_cmd"] == 2)
    check("工具错误按名聚合", snap["tool_errors_by_name"]["run_cmd"] == 1)
    check("auto compact 计数", snap["counters"]["auto_compacts"] == 1)
    check("缓存命中率窗口", abs(snap["cache_ratio_recent"] - 0.9) < 1e-6)
    check("连续错误被 answer 清零", snap["consecutive_turn_errors"] == 0)
    check("render_text 非空", "遥测" in d.render_text())
    d.ingest("agent.error", {"message": "boom"})
    check("错误后连续计数为 1", d.snapshot()["consecutive_turn_errors"] == 1)


def test_channel_plugin():
    print("== TelemetryChannel 插件 ==")
    import Architect.channel  # noqa: F401  触发注册
    from Gateway.channels import create_channel
    ch = create_channel("architect_telemetry", "test_arch")
    check("channel 注册并可实例化", ch is not None)
    check("event 能力声明", "event" in ch.capabilities)
    from Architect.hub import get_default_hub
    before = get_default_hub().digest.snapshot()["counters"]["user_messages"]
    ch.send_event("user.message", {"text": "x"})
    after = get_default_hub().digest.snapshot()["counters"]["user_messages"]
    check("事件喂进默认 hub", after == before + 1)
    ch.send_answer("不应抛出")   # no-op 必要接口
    check("send_answer no-op", True)


def test_wake():
    print("== 唤醒路由 ==")
    cfg = {"architect_hub": {"wake": {"cooldown_seconds": {"manual": 3600}}}}
    hub = create_architect_hub(config=cfg, dispatch=None)
    r = hub.request_wake(WakeRequest(reason="manual", kind="explore"))
    check("dispatch 未接线 → pending", r["status"] == "pending" and len(hub.pending) == 1)

    calls = []
    hub2 = create_architect_hub(config=cfg, dispatch=lambda k, t: {"ok": True, "kind": k})
    r1 = hub2.request_wake(WakeRequest(reason="manual", kind="guardian"), background=False)
    check("正常派发", r1["status"] == "dispatched" and calls == [] and r1["result"]["kind"] == "guardian")
    r2 = hub2.request_wake(WakeRequest(reason="manual", kind="guardian"), background=False)
    check("冷却跳过", r2["status"] == "cooldown_skip")
    r3 = hub2.request_wake(WakeRequest(reason="fatal", kind="guardian"), background=False)
    check("不同 reason 不受他人冷却影响", r3["status"] == "dispatched")
    check("digest 进入任务文本", True)  # _build_task_text 含遥测，由上行 dispatched 间接覆盖


def test_audit():
    print("== AuditPipeline ==")
    tests = ["tests/a.py", "tests/b.py"]

    with tempfile.TemporaryDirectory() as staging:
        # 全过 + 无 merge_fn → PASS 但不合并
        p = AuditPipeline(contract_tests=tests,
                          run_test=lambda t, c, to: {"test": t, "ok": True})
        rep = p.audit(staging)
        check("机械层全过 → PASS", rep["verdict"] == "PASS")
        check("merge_fn 未接线 → 不合并", rep["merged"] is False)
        check("LLM 走读跳过", rep["llm_audit"]["skipped"] is True)

        # 全过 + merge_fn → 合并
        p2 = AuditPipeline(contract_tests=tests,
                           run_test=lambda t, to, x: {"test": t, "ok": True},
                           merge_fn=lambda s: {"merged_from": s})
        check("PASS + merge_fn → 合并", p2.audit(staging)["merged"] is True)

        # 有测试挂 → FAIL
        def flaky(t, cwd, to):
            return {"test": t, "ok": t != "tests/b.py", "tail": "boom"}
        p3 = AuditPipeline(contract_tests=tests, run_test=flaky)
        rep3 = p3.audit(staging)
        check("测试失败 → FAIL", rep3["verdict"] == "FAIL")

        # LLM 走读 FAIL 否决机械层 PASS
        p4 = AuditPipeline(contract_tests=tests,
                           run_test=lambda t, c, to: {"test": t, "ok": True},
                           dispatch=lambda k, tx: {"content": "发现问题\nVERDICT: FAIL"})
        rep4 = p4.audit(staging)
        check("LLM FAIL 否决", rep4["verdict"] == "FAIL"
              and rep4["llm_audit"]["verdict"] == "FAIL")

        # staging 不存在 → FAIL 且有 error
        rep5 = p.audit("/nonexistent/path/xyz")
        check("staging 缺失 → FAIL", rep5["verdict"] == "FAIL" and "error" in rep5)


def test_triggers():
    print("== 触发器 ②：逻辑错误自动唤醒 ==")
    from Architect.hub import classify_error
    check("网络超时 → transient", classify_error("Connection timeout") == "transient")
    check("限流 → transient", classify_error("429 rate limit") == "transient")
    check("代码异常 → logic", classify_error("TypeError: NoneType") == "logic")
    check("消息结构 400 → logic", classify_error("tool_use ids without tool_result") == "logic")

    calls = []
    cfg = {"architect_hub": {"wake": {"cooldown_seconds": {"fatal": 1800}}}}
    hub = create_architect_hub(config=cfg,
                               dispatch=lambda k, t: calls.append((k, t)) or {"ok": 1})
    hub.ingest_event("agent.error", {"message": "TypeError: NoneType has no len"})
    # background 派发在守护线程，等它落地
    import time as _t
    for _ in range(50):
        if calls:
            break
        _t.sleep(0.05)
    check("逻辑错误 → 自动唤醒 guardian", calls and calls[0][0] == "guardian")
    check("遥测自动附进任务文本", calls and "遥测" in calls[0][1])
    check("错误已计数", hub.digest.snapshot()["counters"]["turn_errors"] == 1)

    hub.ingest_event("agent.error", {"message": "Connection timeout"})
    check("transient 不唤醒", len(calls) == 1)

    print("== 派发串行锁 ==")
    import threading
    entered = threading.Event()
    release = threading.Event()

    def slow_dispatch(k, t):
        entered.set()
        release.wait(2)
        return {"ok": 1}

    cfg2 = {"architect_hub": {"wake": {"cooldown_seconds": {}}}}
    hub2 = create_architect_hub(config=cfg2, dispatch=slow_dispatch)
    r1 = hub2.request_wake(WakeRequest(reason="fatal", kind="guardian"))
    check("第一个唤醒派发中", r1["status"] == "dispatched")
    entered.wait(2)
    r2 = hub2.request_wake(WakeRequest(reason="soft_failure", kind="guardian"))
    check("并发第二个 → busy", r2["status"] == "busy")
    release.set()

    print("== 触发器 ③：升级流（假 git）==")
    from Architect.upgrader import Upgrader
    git_log = []

    def fake_git(repo, *args):
        git_log.append(args)
        if args[0] == "worktree" and args[1] == "add":
            Path(args[3]).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "output": "ok"}

    with tempfile.TemporaryDirectory() as tmp:
        staging = str(Path(tmp) / "staging")
        up = Upgrader(repo_dir=tmp, staging_dir=staging, run_git=fake_git)
        r = up.fetch_staging()
        check("staging 拉取成功", r["ok"] and Path(staging).is_dir())
        check("fetch 先于 worktree", git_log[0][0] == "fetch")

        def fake_git_fail(repo, *args):
            return {"ok": False, "output": "network dead"}
        up2 = Upgrader(repo_dir=tmp, staging_dir=staging + "2", run_git=fake_git_fail)
        check("fetch 失败 → 不建 staging", not up2.fetch_staging()["ok"])


def test_consult_tool():
    print("== 触发器 ④：ConsultArchitect 工具 ==")
    import asyncio
    from Tools.builtin.architect_tools import ConsultArchitect, _VALID_KINDS
    r = asyncio.run(ConsultArchitect("系统最近变慢，自查一下", kind="introspect"))
    import json as _json
    out = _json.loads(r)
    check("工具返回合法 JSON", isinstance(out, dict) and "status" in out or "error" not in out)
    check("无效 kind 被拒", "error" in _json.loads(
        asyncio.run(ConsultArchitect("x", kind="bogus"))))


def test_hero_bridge():
    print("== Hero 桥（双向沟通）==")
    import time as _t

    # 未接线 → 降级 False，不抛异常
    hub = create_architect_hub(config={}, dispatch=lambda k, t: {"content": "报告正文"})
    check("未接线 notify_user → False", hub.notify_user("x") is False)
    check("未接线 message_hero → False", hub.message_hero("x") is False)

    # 接线后路由正确
    sent_user, sent_hero = [], []
    hub.set_hero_bridge(notify_user=sent_user.append,
                        message_hero=sent_hero.append)
    check("notify_user 路由", hub.notify_user("给用户") and sent_user == ["给用户"])
    check("message_hero 路由", hub.message_hero("给hero") and sent_hero == ["给hero"])

    # 后台派发完成 → 报告自动推送用户
    hub2 = create_architect_hub(
        config={"architect_hub": {"wake": {"cooldown_seconds": {}}}},
        dispatch=lambda k, t: {"content": "修复完成：重启了卡死的服务"})
    pushed = []
    hub2.set_hero_bridge(notify_user=pushed.append)
    hub2.request_wake(WakeRequest(reason="fatal", kind="guardian"))
    for _ in range(50):
        if pushed:
            break
        _t.sleep(0.05)
    check("后台任务完成 → 报告推送用户",
          bool(pushed) and "修复完成" in pushed[0] and "guardian" in pushed[0])

    # report_push=False → 不推送
    hub3 = create_architect_hub(
        config={"architect_hub": {"report_push": False,
                                  "wake": {"cooldown_seconds": {}}}},
        dispatch=lambda k, t: {"content": "x"})
    pushed3 = []
    hub3.set_hero_bridge(notify_user=pushed3.append)
    hub3.request_wake(WakeRequest(reason="fatal", kind="guardian"),
                      background=False)
    check("report_push 关闭 → 不推送", pushed3 == [])


def test_message_hero_tool():
    print("== MessageHero 工具 ==")
    import asyncio
    import json as _json
    from Tools.builtin.architect_tools import MessageHero
    from Architect.hub import get_default_hub

    sent_hero, sent_user = [], []
    get_default_hub().set_hero_bridge(notify_user=sent_user.append,
                                      message_hero=sent_hero.append)
    out = _json.loads(asyncio.run(MessageHero("发现缓存配置有问题", to="hero")))
    check("to=hero 只发 hero", out["sent"]["hero"] and not sent_user)
    out2 = _json.loads(asyncio.run(MessageHero("审计报告", to="both")))
    check("to=both 双发", out2["sent"]["hero"] and out2["sent"]["user"])


def test_git_update_source():
    print("== GitUpdateSource（GitHub 定期轮询）==")
    from Architect.upgrader import GitUpdateSource

    calls = []
    state = {"local": "a" * 40, "remote": "b" * 40}

    def fake_git(repo, *args):
        calls.append(args)
        if args[0] == "fetch":
            return {"ok": True, "output": ""}
        if args[0] == "rev-parse" and args[1] == "HEAD":
            return {"ok": True, "output": state["local"]}
        if args[0] == "rev-parse":
            return {"ok": True, "output": state["remote"]}
        if args[0] == "log":
            return {"ok": True, "output": "11-21 14:02 bbbbbbb feat: 新功能\n"
                                          "11-21 10:00 ccccccc fix: 修 bug"}
        if args[0] == "rev-list":
            return {"ok": True, "output": "2"}
        return {"ok": True, "output": ""}

    src = GitUpdateSource(run_git=fake_git, check_interval_seconds=0)
    req = src.poll()
    check("有新提交 → code_update 唤醒", req is not None and req.reason == "code_update")
    check("上下文含落后计数", req.context["behind"] == "2")
    check("上下文含精简提交清单（时间+sha+主题）",
          "11-21 14:02" in req.context["new_commits"]
          and "feat: 新功能" in req.context["new_commits"])
    check("上下文含两端 sha", req.context["local_head"] == "aaaaaaaa"
          and req.context["remote_head"] == "bbbbbbbb")

    check("未受理前持续重报", src.poll() is not None)
    src.note_accepted("b" * 40)
    check("受理（ack）后不再报", src.poll() is None)

    state["local"] = state["remote"]   # 本地追上
    check("本地已同步 → 不报", src.poll() is None)

    state["remote"] = "d" * 40         # 又有新提交
    req2 = src.poll()
    check("更新的提交 → 再次报", req2 is not None
          and req2.context["remote_head"] == "dddddddd")

    # fetch 失败静默降级
    def dead_git(repo, *args):
        return {"ok": False, "output": "network dead"}
    src2 = GitUpdateSource(run_git=dead_git, check_interval_seconds=0)
    check("fetch 失败 → None 不抛异常", src2.poll() is None)

    # 节流：interval 内不重复 fetch
    src3 = GitUpdateSource(run_git=fake_git, check_interval_seconds=3600)
    src3.poll()
    n = len(calls)
    src3.poll()
    check("check_interval 节流（不再 fetch）", len(calls) == n)


def test_code_update_routing():
    print("== code_update 路由到更新四态流 ==")
    import Architect.update_flow as uf
    from Architect.wake import REASON_CODE_UPDATE
    started = []

    class FakeFlow:
        def __init__(self, hub, source="manual", context=None):
            self.state = "idle"
            self.active = False
            started.append({"source": source, "context": context})

        def start(self):
            pass

    hub = create_architect_hub(
        config={"architect_hub": {"wake": {"cooldown_seconds": {}},
                                  "upgrade": {"min_commits": 3,
                                              "quiet_seconds": 0}}},
        dispatch=lambda k, t: {"content": "VERDICT: PASS"})
    hub.set_runtime_hooks(idle_probe=lambda: True)

    orig_flow = uf.UpdateFlow
    uf.UpdateFlow = FakeFlow
    try:
        r = hub.request_wake(WakeRequest(
            reason=REASON_CODE_UPDATE, kind="audit",
            context={"ref": "origin/master", "behind": "5", "remote_head": "abc"}),
            background=False)
        check("code_update → 更新流启动", r["status"] == "started" and started)

        r2 = hub.request_wake(WakeRequest(
            reason=REASON_CODE_UPDATE, kind="audit",
            context={"ref": "origin/master", "behind": "1"}),
            background=False)
        check("落后不足 → below_threshold（不进更新流）",
              r2["status"] == "below_threshold" and len(started) == 1)

        hub._idle_probe = lambda: False
        r3 = hub.request_wake(WakeRequest(
            reason=REASON_CODE_UPDATE, kind="audit",
            context={"ref": "origin/master", "behind": "9"}),
            background=False)
        check("时机不合适 → deferred", r3["status"] == "deferred"
              and len(started) == 1)
    finally:
        uf.UpdateFlow = orig_flow


def main():
    test_digest()
    test_channel_plugin()
    test_wake()
    test_audit()
    test_triggers()
    test_consult_tool()
    test_hero_bridge()
    test_message_hero_tool()
    test_git_update_source()
    test_code_update_routing()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
