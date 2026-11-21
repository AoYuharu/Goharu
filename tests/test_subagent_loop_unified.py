"""子代理循环统一（SubAgent → ActorAgent 单一引擎）离线测试。

假 LLM + 假 registry 驱动真实 SubAgent.execute()（内部走真实 ActorAgent.act()），
锁死：
1. thinking 结构化回传回归（2026-10-18 事故：截断续写把 thinking 压平 → 400）
2. 工具白名单拒绝：allowed_tools 外的工具绝不执行
3. run_cmd 命令策略：白名单外命令被拦
4. 每轮倒计时 + CRITICAL_REMINDERS 注入（tool_result 消息尾部）
5. 迭代上限 → partial；verify → VERDICT 解析
6. 缓存断点：system 恒带断点，断点推进到最后一条消息，前缀跨步稳定

运行：python tests/test_subagent_loop_unified.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Agent.SubAgent import SubAgent, SubAgentConfig

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


class FakeResponse:
    """结构化接口 LLM 响应：.content 块列表 + .stop_reason + .usage"""

    def __init__(self, blocks, stop_reason="end_turn", usage=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = usage


class ScriptedLLM:
    """剧本化假 LLM：query(messages, on_retry=None, tools=..., ...) 按序返回。"""

    supports_native_tools = True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def query(self, messages, on_retry=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "kwargs": kwargs})
        if not self.script:
            raise RuntimeError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeEntry:
    def __init__(self, name, handler=None):
        self.name = name
        self.description = f"fake {name}"
        self.arguments_schema = {"type": "object", "properties": {}}
        self.handler = handler


class FakeRegistry:
    """最小 registry 契约：get_entry / async dispatch"""

    def __init__(self, names):
        self.entries = {n: FakeEntry(n) for n in names}
        self.dispatched = []

    def get_entry(self, name):
        return self.entries.get(name)

    async def dispatch(self, name, args):
        self.dispatched.append((name, dict(args or {})))
        return f"ok:{name}"


def tool_use_resp(name, args=None, call_id="call_1"):
    return FakeResponse(
        [{"type": "tool_use", "id": call_id, "name": name, "input": args or {}}],
        stop_reason="tool_use",
    )


def answer_resp(text):
    return FakeResponse([{"type": "text", "text": text}], stop_reason="end_turn")


def make_sub(agent_type, llm, registry, task="测试任务"):
    sub = SubAgent(
        agent_type=agent_type, task=task, agent_id=f"{agent_type}_testunify",
        tools_registry=registry, output_callback=None, shared_state={}, llm=llm,
    )
    sub._log_dir = Path(tempfile.mkdtemp(prefix="subagent_test_"))
    return sub


def last_user_text_blocks(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                return [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def main():
    print("=" * 60)
    print("子代理循环统一测试（SubAgent → ActorAgent 单一引擎）")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. thinking 结构化回传回归（2026-10-18 400 事故）
    # ------------------------------------------------------------------
    print("\n[1] thinking 结构化回传（max_tokens 截断续写）")
    llm = ScriptedLLM([
        FakeResponse(
            [{"type": "thinking", "thinking": "我在思考中途被截断"},
             {"type": "text", "text": "部分答"}],
            stop_reason="max_tokens",
        ),
        answer_resp("这是完整答案。"),
    ])
    sub = make_sub("explore", llm, FakeRegistry(["Read", "Grep", "Glob", "run_cmd"]))
    result = sub.execute()
    check("截断续写后正常收官", result["status"] == "success", result)
    # 续写请求的 messages 里，上一轮 assistant 必须以结构化 thinking 块回传
    second = llm.calls[1]["messages"]
    asst = [m for m in second if m.get("role") == "assistant"]
    has_structured_thinking = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "thinking" for b in m["content"] if isinstance(b, dict))
        for m in asst
    )
    check("截断 assistant 以结构化 thinking 块回传", has_structured_thinking,
          json.dumps(second, ensure_ascii=False, default=str)[:400])

    # ------------------------------------------------------------------
    # 2. 工具白名单拒绝
    # ------------------------------------------------------------------
    print("\n[2] 工具白名单（allowed_tools 之外绝不执行）")
    registry = FakeRegistry(["Read", "Grep", "Glob", "run_cmd", "Write", "Edit"])
    llm = ScriptedLLM([
        tool_use_resp("Write", {"path": "x.txt", "content": "hack"}),
        answer_resp("好的我放弃了。"),
    ])
    sub = make_sub("explore", llm, registry)
    result = sub.execute()
    check("白名单外工具未被 dispatch", registry.dispatched == [], str(registry.dispatched))
    check("白名单外调用以错误 tool_result 反馈", result["status"] == "success", result)
    feedback = last_user_text_blocks(llm.calls[1]["messages"])
    # 拦截可能在两层：guard 名单模糊匹配（未找到匹配工具）或适配器白名单（不允许）
    check("反馈文本含拦截提示",
          any(("未找到匹配工具" in b.get("text", "")) or ("不允许" in b.get("text", ""))
              for b in feedback), str(feedback)[:200])

    # ------------------------------------------------------------------
    # 3. run_cmd 命令策略
    # ------------------------------------------------------------------
    print("\n[3] run_cmd 命令策略（只读白名单）")
    registry = FakeRegistry(["Read", "Grep", "Glob", "run_cmd"])
    llm = ScriptedLLM([
        tool_use_resp("run_cmd", {"cmd": "rm -rf /"}),
        tool_use_resp("run_cmd", {"cmd": "ls -la"}, call_id="call_2"),
        answer_resp("命令检查完成。"),
    ])
    sub = make_sub("explore", llm, registry)
    result = sub.execute()
    check("违规命令未被 dispatch",
          [c for c in registry.dispatched if c[0] == "run_cmd"] == [("run_cmd", {"cmd": "ls -la"})],
          str(registry.dispatched))
    check("只读命令正常执行", result["status"] == "success", result)

    # ------------------------------------------------------------------
    # 4. 倒计时 + CRITICAL_REMINDERS 每轮注入
    # ------------------------------------------------------------------
    print("\n[4] 每轮倒计时与提醒注入")
    llm = ScriptedLLM([
        tool_use_resp("Read", {"path": "a.py"}),
        answer_resp("完成。"),
    ])
    sub = make_sub("explore", llm, FakeRegistry(["Read", "Grep", "Glob", "run_cmd"]))
    sub.execute()
    texts = " ".join(b.get("text", "") for b in last_user_text_blocks(llm.calls[1]["messages"]))
    check("tool_result 尾部注入倒计时", "剩余可执行次数" in texts, texts[:200])
    reminder = SubAgentConfig.CRITICAL_REMINDERS.get("explore", "")
    check("tool_result 尾部注入 CRITICAL_REMINDER", reminder[:20] in texts, texts[:200])

    # ------------------------------------------------------------------
    # 5. 迭代上限 → partial
    # ------------------------------------------------------------------
    print("\n[5] 迭代上限")
    n_iter = sub._get_max_iterations()
    llm = ScriptedLLM([
        tool_use_resp("Read", {"path": f"f{i}.py"}, call_id=f"c{i}")
        for i in range(n_iter)
    ])
    registry = FakeRegistry(["Read", "Grep", "Glob", "run_cmd"])
    sub2 = make_sub("explore", llm, registry)
    result = sub2.execute()
    check(f"满 {n_iter} 轮工具调用后 partial",
          result["status"] == "partial" and result["warning"] == "Reached max iterations",
          result)
    check("每轮都真实执行了工具", len(registry.dispatched) == n_iter, str(len(registry.dispatched)))

    # ------------------------------------------------------------------
    # 6. verify VERDICT 解析
    # ------------------------------------------------------------------
    print("\n[6] verify VERDICT 解析")
    llm = ScriptedLLM([
        tool_use_resp("Read", {"path": "t.py"}),
        answer_resp("Checks Performed: 真实运行了测试。\nVERDICT: PASS"),
    ])
    registry = FakeRegistry(["Read", "Grep", "Glob", "run_cmd", "WriteTestFile"])
    sub3 = make_sub("verify", llm, registry)
    result = sub3.execute()
    check("verify 结果带 PASS 判定", result.get("verdict") == "PASS", result.get("verdict"))

    # ------------------------------------------------------------------
    # 7. 缓存断点：system 恒带断点 + 推进到最后一条消息 + 前缀稳定
    # ------------------------------------------------------------------
    print("\n[7] 缓存断点与前缀稳定")
    llm = ScriptedLLM([
        tool_use_resp("Read", {"path": "a.py"}),
        tool_use_resp("Read", {"path": "b.py"}, call_id="c2"),
        answer_resp("完成。"),
    ])
    sub4 = make_sub("explore", llm, FakeRegistry(["Read", "Grep", "Glob", "run_cmd"]))
    sub4.execute()
    m1, m2 = llm.calls[0]["messages"], llm.calls[1]["messages"]
    check("首条 system 恒带断点",
          m1[0].get("cache_control") == {"type": "ephemeral"} and m2[0].get("cache_control") == {"type": "ephemeral"})
    bp1 = [i for i, m in enumerate(m1) if m.get("cache_control")]
    bp2 = [i for i, m in enumerate(m2) if m.get("cache_control")]
    check("断点推进到最后一条消息", bp1 == [0, len(m1) - 1] and bp2 == [0, len(m2) - 1],
          f"{bp1} / {bp2}")

    def _content_of(m):
        stripped = {k: v for k, v in m.items() if k != "cache_control"}
        return json.dumps(stripped, sort_keys=True, ensure_ascii=False, default=str)

    # 断点标记随最新消息前移（MiniMax 按内容匹配前缀，标记移动不影响命中）；
    # 内容本身（剥离标记后）必须跨步逐字节稳定
    prefix_stable = all(
        _content_of(m1[i]) == _content_of(m2[i]) for i in range(len(m1))
    )
    check("历史前缀内容跨步逐字节稳定（剥离断点标记）", prefix_stable)

    print("\n" + "=" * 60)
    if failed:
        print(f"RESULT: FAIL ({passed}/{passed + failed})")
        sys.exit(1)
    print(f"RESULT: PASS ({passed}/{passed + failed})")


if __name__ == "__main__":
    main()
