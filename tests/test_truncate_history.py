"""Unit tests for ActorAgent._truncate_history tool_use/tool_result pairing safety."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Agent.ActorAgent import ActorAgent


def msg_user(text):
    return {"role": "user", "content": text}


def msg_tool_use(name="Read", call_id="c1"):
    return {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}]}


def msg_tool_result(call_id="c1"):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]}


def main():
    failures = 0

    def check(cond, label):
        nonlocal failures
        print(("PASS" if cond else "FAIL") + f": {label}")
        if not cond:
            failures += 1

    # 1. 短于上限 → 原样返回
    h = [msg_user("a"), msg_user("b")]
    check(ActorAgent._truncate_history(h, 20) == h, "short history unchanged")

    # 2. max<=0 → 不截断
    h = [msg_user(str(i)) for i in range(50)]
    check(len(ActorAgent._truncate_history(h, 0)) == 50, "max=0 disables truncation")

    # 3. 正常截断保留最近 N 条
    h = [msg_user(str(i)) for i in range(50)]
    t = ActorAgent._truncate_history(h, 10)
    check(len(t) == 10 and t[0]["content"] == "40", "keeps last N messages")

    # 4. 截断点落在 tool_use/tool_result 之间 → 首部孤立 tool_result 被丢弃
    h = [msg_user("q1"), msg_tool_use(call_id="c9"), msg_tool_result("c9"), msg_user("q2")]
    t = ActorAgent._truncate_history(h, 2)  # 窗口 = [tool_result, q2] → tool_result 孤立
    check(len(t) == 1, "orphan tool_result dropped")
    check(t[0]["content"] == "q2", "first kept message is plain user message")

    # 4b. 窗口恰好从 tool_use 开始 → 配对完整，不应再丢
    t = ActorAgent._truncate_history(h, 3)
    check(len(t) == 3, "window starting at tool_use keeps intact pair")

    # 5. 截断后保留完整的 tool_use + tool_result 配对
    h = [msg_user("old"), msg_user("q1"), msg_tool_use(call_id="c9"), msg_tool_result("c9")]
    t = ActorAgent._truncate_history(h, 3)
    has_use = any(
        isinstance(m.get("content"), list) and any(b.get("type") == "tool_use" for b in m["content"])
        for m in t
    )
    has_result = any(
        isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
        for m in t
    )
    check(has_use and has_result, "intact pair survives when cut before tool_use")

    print("RESULT:", "FAIL" if failures else "PASS")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
