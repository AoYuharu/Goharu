"""Prompting/loop_messages.py 契约测试：主/子 Agent 共用的消息组装规则。

重点锁死 2026-10-17 事故：assistant tool_use 之后的 user 消息中，
tool_result 块必须位于最前面（DeepSeek Anthropic 兼容端点的硬约束）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Prompting.loop_messages import (
    build_tool_result_message,
    sanitize_messages_for_api,
    truncate_history,
)

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS: " if cond else "FAIL: ") + name)


def tr(id_):
    return {"type": "tool_result", "tool_use_id": id_, "content": "ok", "is_error": False}


# 1. tool_result 必须在最前面，提示文本只能追加在后
msg = build_tool_result_message([tr("a"), tr("b")], ["倒计时提示", "", None, "REMINDER"])
types = [b["type"] for b in msg["content"]]
check(msg["role"] == "user", "role 为 user")
check(types == ["tool_result", "tool_result", "text", "text"], "tool_result 块全部位于文本之前")
check(msg["content"][0]["tool_use_id"] == "a", "tool_result 顺序保持")
check(msg["content"][1]["tool_use_id"] == "b", "第二个 tool_result 保持")
check(all(b.get("text") for b in msg["content"] if b["type"] == "text"), "空文本被跳过")

# 2. 无尾随文本时只含 tool_result
msg2 = build_tool_result_message([tr("x")])
check([b["type"] for b in msg2["content"]] == ["tool_result"], "无文本时不混入 text 块")

# 3. truncate_history 丢弃首部孤立 tool_result（配对的 tool_use 已被截掉）
history = [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "X", "input": {}}]},
    {"role": "user", "content": [tr("t1")]},
    {"role": "assistant", "content": "done"},
]
trimmed = truncate_history(history, 2)  # 切点落在 tool_result → 必须丢弃
check([m["role"] for m in trimmed] == ["assistant"], "孤立 tool_result 被丢弃")
check(
    all(
        not (isinstance(m.get("content"), list) and any(
            b.get("type") == "tool_result" for b in m["content"] if isinstance(b, dict)
        ))
        for m in trimmed
    ),
    "截断结果首条不含 tool_result",
)

# 4. sanitize_messages_for_api 移除孤立 tool_result 块 / 删除未配对 tool_use 消息
dirty = [
    {"role": "user", "content": [tr("orphan")]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "u1", "name": "X", "input": {}}]},
    {"role": "user", "content": "no result"},
]
cleaned = sanitize_messages_for_api([dict(m) for m in dirty])
check(len(cleaned) == 1, "孤立 tool_result 消息与未配对 tool_use 均被清理")
check(cleaned[0]["role"] == "user", "清理后剩余普通 user 消息")

# 5. 完整配对不被破坏
paired = [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "u2", "name": "X", "input": {}}]},
    {"role": "user", "content": [tr("u2"), {"type": "text", "text": "note"}]},
]
check(len(sanitize_messages_for_api([dict(m) for m in paired])) == 3, "完整配对保持不变")

# ── 5. 防御循环不得误杀已配对的开头 assistant（2026-10-31 400 事故）──
tu = {"role": "assistant", "content": [{"type": "tool_use", "id": "k1", "name": "Read", "input": {}}]}
tr_k = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "k1", "content": "ok"}]}
after = {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
seq = sanitize_messages_for_api([dict(tu), dict(tr_k), dict(after)])
check(len(seq) == 3 and seq[0]["role"] == "assistant",
      "开头是已配对的 assistant(tool_use)+tool_result：sanitize 不拆散")

tu_orphan = {"role": "assistant", "content": [{"type": "tool_use", "id": "k2", "name": "Read", "input": {}}]}
seq2 = sanitize_messages_for_api([dict(tu_orphan), dict(after)])
check(len(seq2) == 1 and seq2[0]["role"] == "assistant",
      "开头孤立 assistant(tool_use)（结果缺失）仍被防御删除")

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'FAIL' if failed else 'PASS'} ({len(results) - len(failed)}/{len(results)})")
raise SystemExit(1 if failed else 0)

# ── 5. 防御循环不得误杀已配对的开头 assistant（2026-10-31 400 事故）──
tu = {"role": "assistant", "content": [{"type": "tool_use", "id": "k1", "name": "Read", "input": {}}]}
tr_k = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "k1", "content": "ok"}]}
after = {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
seq = sanitize_messages_for_api([dict(tu), dict(tr_k), dict(after)])
check(len(seq) == 3 and seq[0]["role"] == "assistant",
      "开头是已配对的 assistant(tool_use)+tool_result：sanitize 不拆散")

tu_orphan = {"role": "assistant", "content": [{"type": "tool_use", "id": "k2", "name": "Read", "input": {}}]}
seq2 = sanitize_messages_for_api([dict(tu_orphan), dict(after)])
check(len(seq2) == 1 and seq2[0]["role"] == "assistant",
      "开头孤立 assistant(tool_use)（结果缺失）仍被防御删除")
