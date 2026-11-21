"""计划模式测试：PlanModeGate 只读闸门 + plan_mode_tools 状态机 + reminder + 触发检测。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Gateway.plan_mode import PlanModeGate, is_plan_mode_command, PLAN_MODE_ALLOWED_TOOLS
from Prompting.reminders import TurnReminders, PLAN_MODE_REMINDER
from Tools.builtin import plan_mode_tools
from Tools.builtin import task_tools
from Tools.question_manager import question_manager

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


class FakeRuntime:
    """伪造共享工具运行时：只实现 PlanModeGate 依赖的契约。"""

    def __init__(self):
        self.last_tool_definitions = [
            {"name": "Read"}, {"name": "Grep"}, {"name": "Glob"},
            {"name": "run_cmd"}, {"name": "Write"}, {"name": "Edit"},
            {"name": "tasks_update"}, {"name": "tasks_read"},
            {"name": "AskUser"}, {"name": "ExitPlanMode"},
        ]
        self.calls = []

    async def list_tools(self):
        return self.last_tool_definitions

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return {"ok": True, "name": name}


def test_is_plan_mode_command():
    print("[1] is_plan_mode_command 触发检测")
    check("/plan 识别", is_plan_mode_command("/plan"))
    check("「进入计划模式」识别", is_plan_mode_command("进入计划模式"))
    check("普通消息不识别", not is_plan_mode_command("帮我写个脚本"))
    check("空文本不识别", not is_plan_mode_command(""))


def test_plan_mode_reminder():
    print("[2] TurnReminders.plan_mode_reminder")
    check("无标志返回 None", TurnReminders({}).plan_mode_reminder() is None)
    r = TurnReminders({"plan_mode": True}).plan_mode_reminder()
    check("有标志返回 reminder", r is not None and r[0] == "plan_mode" and r[1] == PLAN_MODE_REMINDER)


def test_plan_mode_gate():
    print("[3] PlanModeGate 只读闸门")
    base = FakeRuntime()
    gate = PlanModeGate(base)

    names = [d["name"] for d in gate.last_tool_definitions]
    check("disabled 全透传", "Write" in names and "Edit" in names)

    gate.enabled = True
    names = [d["name"] for d in gate.last_tool_definitions]
    check("enabled 只含白名单", set(names) == PLAN_MODE_ALLOWED_TOOLS, str(names))
    check("enabled 剔除写工具", "Write" not in names and "Edit" not in names)

    r = asyncio.run(gate.call_tool("Write", {}))
    check("拒绝白名单外工具", isinstance(r, str) and "error" in json.loads(r), str(r)[:80])

    r = asyncio.run(gate.call_tool("run_cmd", {"cmd": "git status"}))
    check("run_cmd 只读放行", isinstance(r, dict) and r.get("name") == "run_cmd", str(r))

    r = asyncio.run(gate.call_tool("run_cmd", {"cmd": "echo x > y.txt"}))
    check("run_cmd 重定向拒绝", isinstance(r, str) and "error" in json.loads(r), str(r)[:80])

    r = asyncio.run(gate.call_tool("run_cmd", {"cmd": "rm -rf /"}))
    check("run_cmd 危险命令拒绝", isinstance(r, str) and "error" in json.loads(r), str(r)[:80])

    r = asyncio.run(gate.call_tool("Read", {"path": "x"}))
    check("白名单工具放行", isinstance(r, dict), str(r))

    gate.enabled = False
    r = asyncio.run(gate.call_tool("Write", {}))
    check("disabled 恢复透传", isinstance(r, dict), str(r))


def test_plan_mode_tools():
    print("[4] plan_mode_tools 状态机")
    events = []
    plan_mode_tools.set_listener(lambda action, payload: events.append((action, payload)))

    r = json.loads(plan_mode_tools.enter_plan_mode())
    check("enter 返回 entered", r.get("status") == "entered", str(r))
    check("enter 触发监听", events and events[-1][0] == "enter", str(events))

    task_tools._reset_for_test()
    task_tools.tasks_update([
        {"id": 1, "subject": "读代码", "status": "pending"},
        {"id": 2, "subject": "写实现", "status": "pending"},
    ])

    # 充分分支
    answers = ["充分，开始执行"]

    def fake_approve(request_id, payload):
        ans = answers.pop(0) if answers else None
        if ans is not None:
            question_manager.resolve(request_id, ans)

    question_manager.set_notifier(fake_approve)
    r = json.loads(plan_mode_tools.exit_plan_mode())
    check("充分→approved", r.get("status") == "approved", str(r))
    check("触发 exit_approved", events[-1][0] == "exit_approved", str(events))

    # 补充分支（第一次问询"需要补充"，第二次收补充文本）
    answers = ["需要补充", "还要支持并发"]

    def fake_supplement(request_id, payload):
        ans = answers.pop(0) if answers else None
        if ans is not None:
            question_manager.resolve(request_id, ans)

    question_manager.set_notifier(fake_supplement)
    r = json.loads(plan_mode_tools.exit_plan_mode())
    check("补充→supplement", r.get("status") == "supplement", str(r))
    check("补充文本回传", r.get("supplement") == "还要支持并发", str(r))
    check("触发 exit_supplement", events[-1][0] == "exit_supplement", str(events))

    # 无 notifier 优雅降级
    question_manager.set_notifier(None)
    r = json.loads(plan_mode_tools.exit_plan_mode())
    check("无 notifier 降级 unavailable", r.get("status") == "unavailable", str(r))

    plan_mode_tools.set_listener(None)


def main():
    test_is_plan_mode_command()
    test_plan_mode_reminder()
    test_plan_mode_gate()
    test_plan_mode_tools()
    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
