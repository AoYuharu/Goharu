"""_salvage_text_tool_calls 测试：真实日志样本 + 合成格式。"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import json

from Agent.ActorAgent import _salvage_text_tool_calls

failed = 0


def check(name, cond, extra=""):
    global failed
    if not cond:
        failed += 1
    print(f"{'PASS' if cond else 'FAIL'} {name}{' — ' + extra if extra else ''}")


# 真实样本 1（call_0004）
d1 = json.load(open("runtime_memory/logs/api/20260831_233624/call_0004_response.json", encoding="utf8"))
t1 = [b["text"] for b in d1["response"]["content"] if b["type"] == "text"]
blocks, rest = _salvage_text_tool_calls(t1)
check("real sample 1", blocks[0]["name"] == "ListInsights" and blocks[0]["input"] == {"limit": 5}, str(blocks[0]["input"]))
check("real sample 1 remaining empty", rest == [])

# 真实样本 2（call_0005，带 </minimax:tool_call> 尾巴）
d2 = json.load(open("runtime_memory/logs/api/20260831_233624/call_0005_response.json", encoding="utf8"))
t2 = [b["text"] for b in d2["response"]["content"] if b["type"] == "text"]
blocks2, rest2 = _salvage_text_tool_calls(t2)
check("real sample 2", blocks2[0]["name"] == "ListInsights" and blocks2[0]["input"] == {"limit": 1}, str(blocks2[0]["input"]))
check("real sample 2 remaining empty", rest2 == [])

# 格式 B：minimax 原生 JSON
t3 = ['<minimax:tool_call>\n{"name": "Grep", "arguments": {"pattern": "foo", "path": "src"}}\n</minimax:tool_call>']
blocks3, rest3 = _salvage_text_tool_calls(t3)
check("minimax json", blocks3[0]["name"] == "Grep" and blocks3[0]["input"]["pattern"] == "foo")

# 普通文本不受影响
check("plain text passthrough", _salvage_text_tool_calls(["正常回答，没有工具调用"]) is None)

# 文本 + 工具混合：保留非工具文本
blocks5, rest5 = _salvage_text_tool_calls([
    '让我查一下。\n[TOOL_CALL]\n{tool => "Read", args => {\n  --path config.yaml\n  --limit 50\n}}\n[/TOOL_CALL]'
])
check("mixed text+tool", blocks5[0]["name"] == "Read" and blocks5[0]["input"] == {"path": "config.yaml", "limit": 50})
check("mixed remaining kept", rest5 == ["让我查一下。"])

print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'}")
raise SystemExit(failed)
