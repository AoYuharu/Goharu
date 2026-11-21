"""OpenAI provider 翻译器单元测试（纯函数，无需网络）。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Agent.providers.openai_provider import OpenAIProvider

P = OpenAIProvider()
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


# ── 1. system 抽取 + 合并 + cache_control 丢弃 ──
msgs = [
    {"role": "system", "content": "sys-A", "cache_control": {"type": "ephemeral"}},
    {"role": "system", "content": "sys-B"},
    {"role": "user", "content": "hello"},
]
wire = P._translate_messages(msgs)
check("system 合并为一条", wire[0] == {"role": "system", "content": "sys-A\n\nsys-B"}, wire[:1])
check("system 后接 user", wire[1] == {"role": "user", "content": "hello"}, wire[1:])
check("cache_control 不出现在 wire", "cache_control" not in json.dumps(wire))

# ── 2. assistant text + tool_use 混合 ──
msgs = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me think"},
        {"type": "text", "text": "我来读文件"},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "a.txt"}},
    ]},
]
wire = P._translate_messages(msgs)
asst = wire[1]
check("assistant role", asst["role"] == "assistant")
check("text 保留", asst["content"] == "我来读文件", asst)
check("thinking 已丢弃", "let me think" not in json.dumps(wire))
tc = asst["tool_calls"][0]
check("tool_calls 结构", tc["type"] == "function" and tc["id"] == "toolu_1"
      and tc["function"]["name"] == "Read"
      and json.loads(tc["function"]["arguments"]) == {"path": "a.txt"}, tc)

# ── 3. assistant 纯 tool_use（content 为 None）──
msgs = [{"role": "assistant", "content": [
    {"type": "tool_use", "id": "t1", "name": "A", "input": {}},
    {"type": "tool_use", "id": "t2", "name": "B", "input": {"x": 1}},
]}]
wire = P._translate_messages(msgs)
check("无文本 content=None", wire[0]["content"] is None, wire[0])
check("两个 tool_calls", [t["id"] for t in wire[0]["tool_calls"]] == ["t1", "t2"])

# ── 4. user 混合 tool_result×2 + text → tool, tool, user ──
msgs = [{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "ok-1", "is_error": False},
    {"type": "tool_result", "tool_use_id": "t2", "content": "ok-2", "is_error": True},
    {"type": "text", "text": "继续"},
]}]
wire = P._translate_messages(msgs)
check("拆分三条", len(wire) == 3, wire)
check("tool 顺序与 id", [m.get("tool_call_id") for m in wire[:2]] == ["t1", "t2"], wire)
check("tool role", all(m["role"] == "tool" for m in wire[:2]))
check("尾部 user 文本", wire[2] == {"role": "user", "content": "继续"}, wire[2:])

# ── 5. 纯 tool_result 的 user 消息不残留空 user ──
msgs = [{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "r", "is_error": False},
]}]
wire = P._translate_messages(msgs)
check("只产出 role:tool", len(wire) == 1 and wire[0]["role"] == "tool", wire)

# ── 6. 只有 thinking 的 assistant 消息被整条跳过 ──
msgs = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]},
    {"role": "user", "content": "q2"},
]
wire = P._translate_messages(msgs)
check("thinking-only assistant 跳过", [m["role"] for m in wire] == ["user", "user"], wire)

# ── 7. tools / tool_choice 翻译 ──
tools = [{"name": "Read", "description": "read file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
wt = P._translate_tools(tools)
check("tools → function", wt[0]["type"] == "function"
      and wt[0]["function"]["parameters"]["properties"]["path"]["type"] == "string", wt)
wc = P._translate_tool_choice({"type": "tool", "name": "Read"})
check("tool_choice 映射", wc == {"type": "function", "function": {"name": "Read"}}, wc)

# ── 8. 响应归一化：text + tool_calls + finish/usage ──
raw = {
    "choices": [{
        "message": {
            "content": "好的",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "Read", "arguments": '{"path": "a.txt"}'},
            }],
        },
        "finish_reason": "tool_calls",
    }],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20,
              "prompt_tokens_details": {"cached_tokens": 80}},
}
resp = P.normalize_response(raw)
check("text 块", resp.content[0] == {"type": "text", "text": "好的"}, resp.content)
check("tool_use 块", resp.content[1]["type"] == "tool_use"
      and resp.content[1]["input"] == {"path": "a.txt"}, resp.content)
check("finish_reason 归一化", resp.stop_reason == "tool_use", resp.stop_reason)
check("usage 映射", resp.usage == {"input_tokens": 100, "output_tokens": 20,
      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 80}, resp.usage)

check("finish length→max_tokens", P.normalize_response(
    {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}
).stop_reason == "max_tokens")
check("finish stop→end_turn", P.normalize_response(
    {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
).stop_reason == "end_turn")

# ── 9. tool_call arguments 坏 JSON 兜底 ──
resp = P.normalize_response({"choices": [{"message": {
    "content": None,
    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "W", "arguments": "{bad"}}],
}, "finish_reason": "tool_calls"}]})
check("坏 JSON 兜底", resp.content[0]["input"].get("__parse_error__") == "{bad", resp.content)

# ── 10. 往返一致性：翻译结果可被 ActorAgent 的 sanitize 接受 ──
from Agent.ActorAgent import ActorAgent

neutral_history = [
    {"role": "user", "content": "读文件"},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "a"}},
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "内容", "is_error": False},
    ]},
]
wire = P._translate_messages(neutral_history)
check("wire 角色序列合法", [m["role"] for m in wire] == ["user", "assistant", "tool"], wire)
# assistant tool_calls 与 tool 消息 id 配对
check("配对完整", wire[1]["tool_calls"][0]["id"] == wire[2]["tool_call_id"])
# sanitize 对中立历史无副作用（已配对）
cleaned = ActorAgent._sanitize_messages_for_api([dict(m) for m in neutral_history])
check("sanitize 不误删", len(cleaned) == 3, str(len(cleaned)))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
