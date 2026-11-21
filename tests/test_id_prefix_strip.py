"""消息 ID 模仿前缀的剥离测试（Core/text_utils + PromptRenderer）。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from Core.text_utils import strip_id_prefixes
from Core.PromptSection import PromptSection
from Prompting.PromptRenderer import PromptRenderer

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS: " if cond else "FAIL: ") + name)


# 1. 各种变体
cases = [
    ("[ID:msg_ab12cd34] 你好", "你好"),
    ("[ID=msg_ab12cd34] 你好", "你好"),
    ("[ID：msg_ab12cd34] 你好", "你好"),
    ("[ id = msg_AB12CD34 ] 你好", "你好"),
    ("[ID=msg_ab12]\n\n[ID=msg_cd34]\n\n结论", "结论"),
    ("[ID:msg_ab12] [ID=msg_cd34]结论", "结论"),
]
for raw, expected in cases:
    check(strip_id_prefixes(raw) == expected, f"剥离 {raw[:28]!r}")

check(strip_id_prefixes("正常文本 [ID=msg_x]") == "正常文本 [ID=msg_x]", "非开头不误伤")
check(strip_id_prefixes("") == "", "空串安全")

# 2. 渲染器清掉旧脏历史（assistant 文本块 + 字符串两种形态）
renderer = PromptRenderer()
list_section = PromptSection(
    kind="assistant",
    content=[
        {"type": "text", "text": "[ID=msg_deadbeef]\n\n历史脏文本"},
        {"type": "tool_use", "id": "call_1", "name": "X", "input": {}},
    ],
    metadata={"id": "msg_canonical"},
)
rendered = renderer.render_section(list_section)
texts = [b.get("text") for b in rendered["content"] if b.get("type") == "text"]
check(all("msg_deadbeef" not in (t or "") for t in texts), "assistant 列表块清掉模仿前缀")
check(any("历史脏文本" in (t or "") for t in texts), "真实文本保留")
check(not any("msg_canonical" in (t or "") for t in texts), "渲染器不再向模型暴露消息 ID")
check(not any("[ID" in (t or "") for t in texts), "输出中无自动 ID 前缀")

str_section = PromptSection(
    kind="assistant",
    content="[ID:msg_deadbeef] 纯文本脏历史",
    metadata={"id": "msg_canonical2"},
)
rendered = renderer.render_section(str_section)
check("msg_deadbeef" not in rendered["content"], "assistant 字符串清掉模仿前缀")
check("纯文本脏历史" in rendered["content"], "字符串真实内容保留")

# 3. user 内容不被改写（不在本次修复范围）
user_section = PromptSection(kind="user", content="[ID=msg_keep] 用户输入", metadata={"id": "msg_u"})
rendered = renderer.render_section(user_section)
check("[ID=msg_keep]" in rendered["content"], "user 内容保持原样")

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'FAIL' if failed else 'PASS'} ({len(results) - len(failed)}/{len(results)})")
raise SystemExit(1 if failed else 0)
