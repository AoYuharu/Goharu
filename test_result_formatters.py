"""测试工具结果 format 层 + verify nudge（不经 LLM，纯本地）。

覆盖：
1. 各工具 formatter 模板渲染（Read/Write/Edit/Grep/Glob/run_cmd/AgentDelegate）
2. 错误结果统一渲染
3. 非 JSON 结果原样放行（Sleep / background_status）
4. 无模板工具回退美化 JSON
5. verify nudge：3 次写操作触发提醒，verify 调用清零，失败写操作不计数
"""
import asyncio
import json

from Tools.runtime import InProcessToolRuntime
from Tools.result_formatters import format_tool_result
from Tools.verify_nudge import verify_nudge

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


print("== 1. formatter 模板 ==")

read_json = json.dumps({
    "file": "E:\\foo.py", "start_line": 1, "end_line": 2, "total_lines": 50,
    "content": [{"line": 1, "text": "import os"}, {"line": 2, "text": "print(1)"}],
}, ensure_ascii=False)
out = format_tool_result("Read", read_json)
check("Read header", "已读取 E:\\foo.py（第 1-2 行，共 50 行）" in out)
check("Read 保留内容", "1\timport os" in out and "2\tprint(1)" in out)

out = format_tool_result("Write", json.dumps({"ok": True, "file": "a.py", "lines": 42}))
check("Write", out == "已创建文件 a.py（42 行）")

out = format_tool_result("Edit", json.dumps({"ok": True, "file": "a.py", "old_length": 10, "new_length": 25}))
check("Edit", "已修改 a.py（替换 10 字符 → 25 字符）" in out)

grep_json = json.dumps({"matches": [{"file": "a.py", "line": 7, "content": "def foo():"}], "count": 1, "truncated": False}, ensure_ascii=False)
out = format_tool_result("Grep", grep_json)
check("Grep", "找到 1 处匹配" in out and "a.py:7: def foo():" in out)

glob_json = json.dumps({"filenames": ["x.py", "y.py"], "numFiles": 2, "truncated": False, "durationMs": 5}, ensure_ascii=False)
out = format_tool_result("Glob", glob_json)
check("Glob", "找到 2 个文件" in out and "x.py" in out)

cmd_json = json.dumps({"exit_code": 1, "stdout": "out text", "stderr": "err text", "timed_out": False, "interrupted": False}, ensure_ascii=False)
out = format_tool_result("run_cmd", cmd_json)
check("run_cmd 退出码", "退出码: 1" in out)
check("run_cmd 保留 stdout/stderr", "out text" in out and "err text" in out)

ad_json = json.dumps({"status": "success", "agent_type": "explore", "total_duration_ms": 1234, "content": "报告正文"}, ensure_ascii=False)
out = format_tool_result("AgentDelegate", ad_json)
check("AgentDelegate", "[子智能体 success] explore agent（耗时 1234ms）" in out and "报告正文" in out)

print("== 2. 错误/非JSON/兜底 ==")

out = format_tool_result("Write", json.dumps({"error": "文件已存在", "path": "a.py"}, ensure_ascii=False))
check("error 渲染", out.startswith("❌ 错误：文件已存在") and "path: a.py" in out)

out = format_tool_result("Sleep", "Slept for 1000ms (1.00s)")
check("非 JSON 原样放行", out == "Slept for 1000ms (1.00s)")

out = format_tool_result("get_system_info", json.dumps({"cpu": "Intel", "gpu": "NVIDIA"}, ensure_ascii=False))
check("无模板回退美化 JSON", '"cpu": "Intel"' in out)

print("== 3. verify nudge ==")

verify_nudge._mutation_count = 0  # 重置单例状态
ok_result = "已创建文件 a.py（10 行）"
err_result = "❌ 错误：文件已存在"

for i in range(2):
    nudge = verify_nudge.after_tool_call("Write", {"path": "x"}, ok_result)
    check(f"第 {i+1} 次写不提醒", nudge is None)

nudge = verify_nudge.after_tool_call("Edit", {"path": "x"}, "已修改 x（替换 1 字符 → 2 字符）")
check("第 3 次写触发提醒", nudge is not None and "AgentDelegate" in nudge and "verify" in nudge)

nudge = verify_nudge.after_tool_call("Write", {"path": "x"}, ok_result)
check("阈值以上继续提醒", nudge is not None)

nudge = verify_nudge.after_tool_call("AgentDelegate", {"agent_type": "verify", "task": "验收"}, "{}")
check("verify 调用清零", nudge is None and verify_nudge._mutation_count == 0)

nudge = verify_nudge.after_tool_call("AgentDelegate", {"agent_type": "explore", "task": "探索"}, "{}")
check("explore 调用不清零", nudge is None and verify_nudge._mutation_count == 0)

nudge = verify_nudge.after_tool_call("Write", {"path": "x"}, err_result)
check("失败写操作不计数", verify_nudge._mutation_count == 0)

nudge = verify_nudge.after_tool_call("Read", {}, "已读取 x")
check("非写操作不影响", nudge is None and verify_nudge._mutation_count == 0)

print("== 4. verify 硬闸门 ==")

verify_nudge._mutation_count = 0
verify_nudge._gate_fires = 0
check("无修改时闸门放行", verify_nudge.gate_final_answer() is None)

verify_nudge._mutation_count = 3
gate = verify_nudge.gate_final_answer()
check("达阈值闸门拦截", gate is not None and "AgentDelegate" in gate)
verify_nudge._mutation_count = 3
gate = verify_nudge.gate_final_answer()
check("第二次仍拦截", gate is not None)
verify_nudge._mutation_count = 3
check("超过 max_gate_fires 放行防死循环", verify_nudge.gate_final_answer() is None)

verify_nudge._mutation_count = 0
verify_nudge._gate_fires = 0
verify_nudge.after_tool_call("Write", {"path": "x"}, "已创建文件 x（1 行）")
verify_nudge.after_tool_call("AgentDelegate", {"agent_type": "verify", "task": "t"}, "{}")
check("verify 运行后闸门与计数一起清零", verify_nudge._gate_fires == 0 and verify_nudge._mutation_count == 0)

print("== 5. 子智能体每轮重申 ==")

from Agent.SubAgent import SubAgentConfig
check("verify reminder 存在", "VERDICT" in SubAgentConfig.CRITICAL_REMINDERS["verify"])
check("explore reminder 存在", "只读" in SubAgentConfig.CRITICAL_REMINDERS["explore"])
check("plan reminder 存在", "只读" in SubAgentConfig.CRITICAL_REMINDERS["plan"])

print("== 6. runtime 接线 ==")

runtime = InProcessToolRuntime()
out = asyncio.run(runtime.call_tool("Sleep", {"ms": 1}))
check("runtime Sleep 通路", "Slept for 1ms" in out.content)

verify_nudge._mutation_count = 2  # 预置：再写 1 次即达阈值
out = asyncio.run(runtime.call_tool("Write", {"path": "./runtime_memory/_nudge_test_tmp.txt", "content": "hello"}))
check("runtime Write 格式化", "已创建文件" in out.content)
check("runtime nudge 夹带", "NOTE: 你已连续完成" in out.content)

import os
os.remove("./runtime_memory/_nudge_test_tmp.txt")

print(f"\n结果: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
