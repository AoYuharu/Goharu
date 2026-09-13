# -*- coding: utf-8 -*-
"""后台任务超时豁免 + 失败注入醒目化的回归测试。

覆盖事故场景：run_cmd 后台启动的服务器进程在 8 分钟硬超时被杀，
且被杀结果以普通成功格式注入导致模型无视。
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

import Tools.builtin.core_tools as core_tools
from Agent.BackgroundTaskManager import BackgroundTaskManager, BackgroundResult
from Core.ToolCall import ToolCall

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}")


def test_foreground_timeout_still_kills():
    """前台命令超过硬超时仍应被杀（原行为保留）。"""
    print("[1] 前台超时仍杀进程")
    orig = core_tools.FOREGROUND_CMD_TIMEOUT_S
    core_tools.FOREGROUND_CMD_TIMEOUT_S = 3
    try:
        t0 = time.monotonic()
        result = asyncio.run(core_tools.run_cmd("python -c \"import time; time.sleep(60)\""))
        elapsed = time.monotonic() - t0
        data = json.loads(result)
        check("前台 60s sleep 被 3s 超时杀掉", data["timed_out"] is True and data["exit_code"] == -1)
        check("超时生效快(<%ds)" % 30, elapsed < 30)
    finally:
        core_tools.FOREGROUND_CMD_TIMEOUT_S = orig


def test_background_exempt_from_timeout():
    """被 BackgroundTaskManager track 的任务，进程豁免硬超时。"""
    print("[2] 后台任务豁免硬超时")
    orig = core_tools.FOREGROUND_CMD_TIMEOUT_S
    core_tools.FOREGROUND_CMD_TIMEOUT_S = 3
    mgr = BackgroundTaskManager()
    try:
        async def main():
            dispatch = asyncio.ensure_future(
                core_tools.run_cmd("python -c \"import time; time.sleep(60)\"")
            )
            task_id = mgr.track_task("run_cmd", dispatch, "test bg exempt")
            check("is_tracked 识别已跟踪任务", mgr.is_tracked(dispatch))
            # 等超过前台硬超时(3s)，进程若被豁免则仍在跑
            await asyncio.sleep(6)
            check("超过前台超时后任务仍在运行", not dispatch.done())
            mgr.cancel_task(task_id)
            result = await dispatch
            return json.loads(result)

        data = asyncio.run(main())
        check("取消后返回 interrupted", data.get("interrupted") is True)
    finally:
        core_tools.FOREGROUND_CMD_TIMEOUT_S = orig
        mgr._tracked_tasks.clear()


def test_is_tracked_false_for_untracked():
    print("[3] is_tracked 对未跟踪任务返回 False")
    mgr = BackgroundTaskManager()
    check("未跟踪任务 -> False", not mgr.is_tracked(object()))


def test_inject_marks_classified_failure():
    """run_cmd 正常返回但内容为失败时，注入消息必须标 FAILED。"""
    print("[4] 失败结果注入醒目化")
    mgr = BackgroundTaskManager()
    captured = []

    class FakeMemory:
        def append(self, msg):
            captured.append(msg)

    timeout_json = json.dumps({
        "exit_code": -1, "stdout": "", "stderr": "",
        "timed_out": True, "interrupted": False,
    }, ensure_ascii=False)
    ok_json = json.dumps({
        "exit_code": 0, "stdout": "ok", "stderr": "",
        "timed_out": False, "interrupted": False,
    }, ensure_ascii=False)

    results = [
        BackgroundResult(task_id=901, tool_name="run_cmd", result=timeout_json,
                         description="启动语音服务器", completed_at=time.time()),
        BackgroundResult(task_id=902, tool_name="run_cmd", result=ok_json,
                         description="快速命令", completed_at=time.time()),
    ]
    BackgroundTaskManager.inject_into_memory(FakeMemory(), results)

    fail_msg = next(m for m in captured if "901" in str(m))
    ok_msg = next(m for m in captured if "902" in str(m))
    fail_text = str(fail_msg)
    ok_text = str(ok_msg)
    check("超时结果标 FAILED", "[task-background #901 FAILED]" in fail_text)
    check("FAILED 含醒目警告", "不要假设它仍然存活" in fail_text)
    check("成功结果不标 FAILED", "FAILED" not in ok_text and "completed]" in ok_text)
    check("分类器: timed_out -> error", ToolCall.classify_is_error("run_cmd", timeout_json))
    check("分类器: exit_code=0 -> ok", not ToolCall.classify_is_error("run_cmd", ok_json))


if __name__ == "__main__":
    test_foreground_timeout_still_kills()
    test_background_exempt_from_timeout()
    test_is_tracked_false_for_untracked()
    test_inject_marks_classified_failure()
    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
