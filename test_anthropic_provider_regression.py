"""Anthropic provider 回归测试：迁移后行为与原 LLMCore._prepare_anthropic_messages 一致。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from Agent.providers.anthropic_provider import AnthropicProvider
from Core.ToolCall import ToolCall

P = AnthropicProvider()
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


# ── 1. system 抽取 + cache_control 保留 ──
sys_blocks, remote = P._prepare_messages([
    {"role": "system", "content": "S1", "cache_control": {"type": "ephemeral"}},
    {"role": "system", "content": ""},
    {"role": "system", "content": "S2"},
    {"role": "user", "content": "hi"},
])
check("system 抽取两块", [b["text"] for b in sys_blocks] == ["S1", "S2"], sys_blocks)
check("system cache_control 挂载", sys_blocks[0].get("cache_control") == {"type": "ephemeral"})
check("空 system 跳过", len(sys_blocks) == 2)
check("user 字符串原样", remote == [{"role": "user", "content": "hi"}], remote)

# ── 2. 消息级 cache_control 下沉到最后一个 text 块 ──
_, remote = P._prepare_messages([
    {"role": "user", "content": [
        {"type": "text", "text": "a"},
        {"type": "tool_result", "tool_use_id": "t", "content": "r", "is_error": False},
        {"type": "text", "text": "b"},
    ], "cache_control": {"type": "ephemeral"}},
])
blocks = remote[0]["content"]
check("cache 落在最后 text 块", blocks[2].get("cache_control") == {"type": "ephemeral"}, blocks)
check("tool_result 不带 cache", "cache_control" not in blocks[1])

# ── 3. 字符串 content + cache_control → 包装为块 ──
_, remote = P._prepare_messages([
    {"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}},
])
check("字符串带 cache → 块", remote[0]["content"] == [
    {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}], remote)

# ── 4. 块规范化：空文本剔除、thinking 保留、未知类型报错 ──
_, remote = P._prepare_messages([
    {"role": "assistant", "content": [
        {"type": "text", "text": "  "},
        {"type": "thinking", "thinking": "t"},
        {"type": "tool_use", "id": "x", "name": "n", "input": {}},
    ]},
])
check("空 text 剔除", [b["type"] for b in remote[0]["content"]] == ["thinking", "tool_use"], remote)

try:
    P._prepare_messages([{"role": "user", "content": [{"type": "image", "data": "..."}]}])
    check("未知块类型报错", False)
except ValueError:
    check("未知块类型报错", True)

# ── 5. 非法 role / 空消息兜底 ──
try:
    P._prepare_messages([{"role": "tool", "content": "x"}])
    check("非法 role 报错", False)
except ValueError:
    check("非法 role 报错", True)

_, remote = P._prepare_messages([])
check("空消息兜底 Continue.", remote == [{"role": "user", "content": "Continue."}], remote)

# ── 6. ToolCall 实例入口规范化 ──
_, remote = P._prepare_messages([
    {"role": "user", "content": "q"},
    ToolCall(tool_name="Read", arguments={"path": "a.txt"}),
])
check("ToolCall → assistant tool_use", remote[-1]["role"] == "assistant"
      and remote[-1]["content"][0]["type"] == "tool_use"
      and remote[-1]["content"][0]["name"] == "Read", remote[-1])


# ── 7. normalize_response：伪 anthropic 对象 ──
class _Block:
    def __init__(self, **kw):
        self._d = kw
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return dict(self._d)


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 3
    cache_read_input_tokens = 7


class _Resp:
    content = [_Block(type="text", text="hi"), _Block(type="tool_use", id="t", name="n", input={})]
    stop_reason = "tool_use"
    usage = _Usage()


resp = P.normalize_response(_Resp())
check("块 dict 化", resp.content[0] == {"type": "text", "text": "hi"}, resp.content)
check("stop_reason 透传", resp.stop_reason == "tool_use")
check("usage 提取", resp.usage["cache_read_input_tokens"] == 7, resp.usage)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
