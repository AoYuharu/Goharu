"""SubAgent tool result budget integration test. Run: python test_subagent_budget.py"""
import json
from Agent.SubAgent import SubAgent
import Tools.builtin.core_tools  # noqa: F401 注册工具

sa = SubAgent.__new__(SubAgent)
sa.agent_type = "explore"
sa.allowed_tools = ["Read", "Grep", "Glob", "run_cmd"]
from Tools.registry import registry
sa.tools_registry = registry

blocks = [{
    "id": "call_1",
    "tool": "run_cmd",
    "args": {"cmd": "dir /s /b E:\\Goharu\\tui-ts\\node_modules"},
}]
results = sa._execute_tools_safely(blocks)
r = results[0]
print("is_error:", r["is_error"])
print("result length:", len(r["result"]))
truncated = "[TRUNCATED]" in r["result"]
print("truncated to cache file:", truncated)
if truncated:
    head = json.loads(json.dumps(r["result"])) if False else r["result"]
    print("head:", head.splitlines()[0])
    print("file ref:", [l for l in head.splitlines() if "Full output saved" in l])
assert truncated, "大结果未被预算截断"
print("SUBAGENT BUDGET OK")
