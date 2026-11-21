"""task_tools 任务清单工具测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Tools.builtin import task_tools

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


def main():
    task_tools._reset_for_test()
    notified = []
    task_tools.set_change_listener(lambda tasks: notified.append(list(tasks)))

    print("[1] tasks_update 全量写入")
    r = json.loads(task_tools.tasks_update([
        {"id": 1, "subject": "读代码", "status": "completed"},
        {"id": 2, "subject": "写实现", "status": "in_progress"},
        {"id": 3, "subject": "验收", "status": "pending"},
    ]))
    check("写入成功", r.get("status") == "success")
    check("渲染含进度", "1/3 完成" in r.get("rendered", ""), r.get("rendered", "")[:60])
    check("变更监听器被通知", len(notified) == 1 and len(notified[0]) == 3)

    print("[2] tasks_read 读回")
    r = json.loads(task_tools.tasks_read())
    check("读回 3 项", len(r.get("tasks", [])) == 3)
    check("状态保留", r["tasks"][1]["status"] == "in_progress")

    print("[3] 校验非法输入")
    r = json.loads(task_tools.tasks_update([{"subject": "", "status": "pending"}]))
    check("空 subject 被拒", r.get("status") == "error")
    r = json.loads(task_tools.tasks_update([{"subject": "x", "status": "doing"}]))
    check("非法 status 被拒", r.get("status") == "error", str(r)[:80])
    r = json.loads(task_tools.tasks_update([
        {"id": 1, "subject": "a", "status": "pending"},
        {"id": 1, "subject": "b", "status": "pending"},
    ]))
    check("重复 id 被拒", r.get("status") == "error")
    r = json.loads(task_tools.tasks_update("not-a-list"))
    check("非数组被拒", r.get("status") == "error")

    print("[4] tasks_clear")
    task_tools.tasks_clear()
    r = json.loads(task_tools.tasks_read())
    check("清空后为空", r.get("tasks") == [])
    check("清空也通知", notified[-1] == [])

    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
