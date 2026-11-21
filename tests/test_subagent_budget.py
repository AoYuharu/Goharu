"""SubAgent tool result budget integration test. Run: python test_subagent_budget.py

预算截断已内建于共享引擎（ActorAgent._apply_tool_result_budget），
本测试经真实 SubAgent.execute() 驱动：假 LLM 发起一次大输出的 run_cmd，
断言回传给模型的 tool_result 消息被 [TRUNCATED] 截断。
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import json
import Tools.builtin.core_tools  # noqa: F401 注册工具

from Agent.SubAgent import SubAgent
from Tools.registry import registry


class FakeResponse:
    def __init__(self, blocks, stop_reason="end_turn", usage=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = usage


class ScriptedLLM:
    supports_native_tools = True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def query(self, messages, on_retry=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "kwargs": kwargs})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


llm = ScriptedLLM([
    FakeResponse([{"type": "tool_use", "id": "call_1", "name": "run_cmd",
                   "input": {"cmd": "find /e/Goharu/tui-ts/node_modules -type f"}}],
                 stop_reason="tool_use"),
    FakeResponse([{"type": "text", "text": "done"}], stop_reason="end_turn"),
])

sa = SubAgent(
    agent_type="explore", task="budget test",
    agent_id="explore_budgettest", tools_registry=registry,
    output_callback=None, shared_state={}, llm=llm,
)
result = sa.execute()
assert result["status"] == "success", result

# 第二次 LLM 调用收到的 tool_result 消息
tool_msg = None
for m in llm.calls[1]["messages"]:
    if m.get("role") == "user" and isinstance(m.get("content"), list):
        if any(b.get("type") == "tool_result" for b in m["content"] if isinstance(b, dict)):
            tool_msg = m
assert tool_msg, "未找到 tool_result 消息"
tr_block = next(b for b in tool_msg["content"]
                if isinstance(b, dict) and b.get("type") == "tool_result")
r = tr_block.get("content")
if isinstance(r, list):
    r = "\n".join(b.get("text", "") for b in r if isinstance(b, dict))
print("result length:", len(r))
truncated = "[TRUNCATED]" in r
print("truncated to cache file:", truncated)
if truncated:
    print("head:", r.splitlines()[0])
    print("file ref:", [l for l in r.splitlines() if "Full output saved" in l])
assert truncated, "大结果未被预算截断"
print("SUBAGENT BUDGET OK")
