"""Agent Loop 子系统独立接口测试 —— 外部 harness 视角。

用剧本化假 LLM 驱动真实 ActorAgent 循环，验证 Agent.api（create_agent/run_turn）：
- 工具调用流：tool_use → 真实执行 → 结果回写 → 最终回答
- 事件回调序列（tool_call / tool_result / answer / step）
- memory 注入（MemoryBridge）与 memory=None（内存态）两种模式
- max_steps 封顶、LLM 异常错误路径
- llm 注入不构造 LLMCore；llm=None 时回退 Goharu 路径

运行：python test_agent_loop_standalone.py
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Agent.api import create_agent
from Agent.ActorAgent import ActorAgent
from Tools.api import create_tools, close_tools

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

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def query(self, messages, on_retry=None, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.script:
            raise RuntimeError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_tool_call(name="Sleep", args=None, call_id="call_1"):
    return FakeResponse(
        [{"type": "tool_use", "id": call_id, "name": "Sleep",
          "input": args or {"ms": 5}}],
        stop_reason="tool_use",
    )


def make_answer(text):
    return FakeResponse([{"type": "text", "text": text}], stop_reason="end_turn")


async def _make_agent(llm, memory=None, events=None, max_steps=8, tools=None):
    tools = tools or await create_tools(
        modules=["Tools.builtin.sleep_tool", "Tools.builtin.glob_tool"]
    )
    return await create_agent(
        tools=tools, llm=llm, memory=memory, max_steps=max_steps,
        on_event=(lambda etype, payload: events.append((etype, payload)))
        if events is not None else None,
    ), tools


def main():
    print("=" * 60)
    print("Agent Loop 独立接口测试（create_agent / run_turn）")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. 工具调用流：tool_use → 执行 → 最终回答
    # ------------------------------------------------------------------
    print("\n[1] 工具调用流（memory=None 内存态）")
    events = []
    llm = ScriptedLLM([
        make_tool_call(),
        make_answer("已完成 Sleep 调用。"),
    ])
    agent, tools = asyncio.run(_make_agent(llm, events=events))
    check("注入 llm 时不构造 LLMCore", agent.actor.core is None)

    reply = asyncio.run(agent.run_turn("帮我暂停一下"))
    check("run_turn 返回最终回答", reply == "已完成 Sleep 调用。", reply)
    check("LLM 被调用 2 次（工具步 + 回答步）", len(llm.calls) == 2)
    check("原生工具定义已传给 LLM",
          bool(llm.calls[0]["kwargs"].get("tools")),
          str(llm.calls[0]["kwargs"].keys()))

    etypes = [e for e, _ in events]
    check("事件序列含 tool_call → tool_result → answer",
          etypes.index("tool_call") < etypes.index("tool_result")
          < etypes.index("answer"), str(etypes))
    tool_events = [p for e, p in events if e == "tool_call"]
    check("tool_call 事件含工具名与参数",
          tool_events and tool_events[0]["tool"] == "Sleep"
          and tool_events[0]["arguments"] == {"ms": 5}, str(tool_events))

    ctx = agent.working.get_context()
    roles = [m["role"] for m in ctx]
    check("工作记忆含 user/assistant/tool 结果往返",
          roles[0] == "user" and "assistant" in roles and len(ctx) >= 3,
          str(roles))

    # ------------------------------------------------------------------
    # 2. memory=MemoryBridge（检索注入 + 持久化）
    # ------------------------------------------------------------------
    print("\n[2] memory=MemoryBridge")
    from Memory.bridge import create_memory
    tmp = Path("runtime_memory/temp/agent_loop_test")
    shutil.rmtree(tmp, ignore_errors=True)
    empty_model = tmp / "empty_model"
    empty_model.mkdir(parents=True)
    memory = create_memory(overrides={
        "memory.root_dir": str(tmp / "mem"),
        "memory.retrieval.embedding_model_path": str(empty_model),
        "memory.retrieval.use_reranker": False,
    })
    llm2 = ScriptedLLM([make_answer("记住你了。")])
    agent2, _ = asyncio.run(_make_agent(llm2, memory=memory, tools=tools))
    reply2 = asyncio.run(agent2.run_turn("我喜欢喝冰美式咖啡"))
    check("MemoryBridge 模式回复正常", reply2 == "记住你了。")
    ctx2 = memory.get_context()
    check("MemoryBridge 双写 user+assistant",
          any(m["content"] == "我喜欢喝冰美式咖啡" for m in ctx2)
          and any("记住你了" in str(m.get("content", "")) for m in ctx2),
          str(len(ctx2)))
    memory.close()

    # ------------------------------------------------------------------
    # 3. max_steps 封顶
    # ------------------------------------------------------------------
    print("\n[3] max_steps 封顶")
    llm3 = ScriptedLLM([make_tool_call(call_id=f"c{i}") for i in range(10)])
    agent3, _ = asyncio.run(_make_agent(llm3, max_steps=2, tools=tools))
    reply3 = asyncio.run(agent3.run_turn("死循环任务"))
    check("max_steps=2 后返回封顶提示", "最大步数" in reply3, reply3)
    check("LLM 恰好被调 2 次", len(llm3.calls) == 2, str(len(llm3.calls)))

    # ------------------------------------------------------------------
    # 4. LLM 异常错误路径
    # ------------------------------------------------------------------
    print("\n[4] 错误路径")
    events4 = []
    llm4 = ScriptedLLM([RuntimeError("boom")])
    agent4, _ = asyncio.run(_make_agent(llm4, events=events4, tools=tools))
    reply4 = asyncio.run(agent4.run_turn("会失败的任务"))
    check("LLM 异常 → 优雅错误返回", reply4.startswith("Error:"), reply4[:120])
    check("error 事件已发出",
          any(e == "error" for e, _ in events4), str([e for e, _ in events4]))

    # ------------------------------------------------------------------
    # 5. llm=None 回退 Goharu 路径（仅构造检查，不打 API）
    # ------------------------------------------------------------------
    print("\n[5] llm=None 回退")
    from Agent.api import _ListWorkingMemory
    actor5 = ActorAgent(tools, _ListWorkingMemory())  # 不传 llm
    check("llm=None 时构造 LLMCore 单例", actor5.core is not None)

    # ------------------------------------------------------------------
    # 6. 安全沙箱：危险命令经 agent 路径被拦
    # ------------------------------------------------------------------
    print("\n[6] 安全沙箱：危险命令拦截")
    sec_config = {"tools": {"security": {
        "enabled": True,
        "dangerous_commands": ["rm -rf"],
        "require_confirmation": ["git status"],
        "allow_confirmation": True,
    }}}
    cmd_tools = asyncio.run(create_tools(modules=["Tools.builtin.core_tools"]))
    events6 = []
    llm6 = ScriptedLLM([
        FakeResponse([{"type": "tool_use", "id": "c1", "name": "run_cmd",
                       "input": {"cmd": "rm -rf /"}}], stop_reason="tool_use"),
        make_answer("命令被拦截了。"),
    ])
    agent6 = asyncio.run(create_agent(
        tools=cmd_tools, llm=llm6, config=sec_config,
        on_event=lambda e, p: events6.append((e, p)),
    ))
    reply6 = asyncio.run(agent6.run_turn("删除根目录"))
    check("危险命令后仍得到回答", reply6 == "命令被拦截了。")
    tr = [p for e, p in events6 if e == "tool_result"]
    blocked = tr and any(
        marker in str(tr[0].get("preview", "")).lower()
        for marker in ("error", "blocked", "⛔", "拒", "错误", "dangerous")
    )
    check("危险命令被执行层拦截", bool(blocked), str(tr)[:200])

    # ------------------------------------------------------------------
    # 7. on_confirm：灰名单命令的确认回调（拒绝 / 放行）
    # ------------------------------------------------------------------
    print("\n[7] on_confirm 确认回调")
    confirm_calls = []

    llm7a = ScriptedLLM([
        FakeResponse([{"type": "tool_use", "id": "c2", "name": "run_cmd",
                       "input": {"cmd": "git status"}}], stop_reason="tool_use"),
        make_answer("done-deny"),
    ])
    agent7a = asyncio.run(create_agent(
        tools=cmd_tools, llm=llm7a, config=sec_config,
        on_confirm=lambda cmd, desc: confirm_calls.append(cmd) or False,
    ))
    reply7a = asyncio.run(agent7a.run_turn("看看 git 状态"))
    check("确认回调被调用", confirm_calls == ["git status"], str(confirm_calls))
    check("回调拒绝 → 命令未执行", reply7a == "done-deny")
    ctx7a = agent7a.working.get_context()
    denied = any("error" in str(m.get("content", "")).lower()
                 or "拒" in str(m.get("content", ""))
                 for m in ctx7a)
    check("拒绝结果写入记忆", denied, str([str(m.get("content"))[:60] for m in ctx7a]))

    llm7b = ScriptedLLM([
        FakeResponse([{"type": "tool_use", "id": "c3", "name": "run_cmd",
                       "input": {"cmd": "git status"}}], stop_reason="tool_use"),
        make_answer("done-approve"),
    ])
    agent7b = asyncio.run(create_agent(
        tools=cmd_tools, llm=llm7b, config=sec_config,
        on_confirm=lambda cmd, desc: True,
    ))
    reply7b = asyncio.run(agent7b.run_turn("看看 git 状态"))
    ctx7b = agent7b.working.get_context()
    approved_ran = any("exit_code" in str(m.get("content", ""))
                       or "On branch" in str(m.get("content", ""))
                       or "分支" in str(m.get("content", ""))
                       for m in ctx7b)
    check("回调放行 → 命令真实执行", approved_ran,
          str([str(m.get("content"))[:60] for m in ctx7b]))

    # ------------------------------------------------------------------
    # 8. max_tool_retries 透传
    # ------------------------------------------------------------------
    print("\n[8] max_tool_retries 透传")
    llm8 = ScriptedLLM([make_answer("ok")])
    agent8 = asyncio.run(create_agent(
        tools=cmd_tools, llm=llm8, config=sec_config, max_tool_retries=5,
    ))
    captured = {}
    orig_act = agent8.actor.act

    async def spy_act(*a, **kw):
        captured.update(kw)
        return await orig_act(*a, **kw)

    agent8.actor.act = spy_act
    asyncio.run(agent8.run_turn("随便聊聊"))
    check("max_retries=5 透传到 act", captured.get("max_retries") == 5,
          str(captured.get("max_retries")))

    # ------------------------------------------------------------------
    # 9. 文本格式工具调用打捞
    # ------------------------------------------------------------------
    print("\n[9] 文本打捞")
    events9 = []
    llm9 = ScriptedLLM([
        FakeResponse([{"type": "text",
                       "text": '[TOOL_CALL] {tool => "Sleep", args => { --ms 5 }} [/TOOL_CALL]'}],
                     stop_reason="end_turn"),
        make_answer("打捞执行完毕。"),
    ])
    agent9 = asyncio.run(create_agent(
        tools=cmd_tools, llm=llm9, config=sec_config,
        on_event=lambda e, p: events9.append((e, p)),
    ))
    reply9 = asyncio.run(agent9.run_turn("打捞测试"))
    salvaged = [p for e, p in events9 if e == "tool_call" and p.get("tool") == "Sleep"]
    check("文本工具调用被打捞并执行", bool(salvaged), str(events9))
    check("打捞后得到最终回答", reply9 == "打捞执行完毕。")

    # ------------------------------------------------------------------
    # 10. 未知响应类型的防御分支
    # ------------------------------------------------------------------
    print("\n[10] 未知响应类型防御")
    llm10 = ScriptedLLM([make_answer("unused")])
    agent10 = asyncio.run(create_agent(tools=cmd_tools, llm=llm10, config=sec_config))

    async def weird_act(**kw):
        return {"type": "totally_unknown"}

    agent10.actor.act = weird_act
    reply10 = asyncio.run(agent10.run_turn("触发未知类型"))
    check("未知类型 → 跳出循环返回封顶提示", "最大步数" in reply10, reply10[:120])

    # ------------------------------------------------------------------
    # 11. AgentDelegate 子代理：注入 llm 接线
    # ------------------------------------------------------------------
    print("\n[11] AgentDelegate 子代理（subagent_llm 注入）")
    sub_tools = asyncio.run(create_tools(
        modules=["Tools.builtin.sleep_tool", "Tools.builtin.agent_delegate"],
    ))
    # 子代理假 LLM：直接文本收尾（无工具调用 → 一轮结束）
    sub_llm = ScriptedLLM([make_answer("子代理探索完成：找到 3 个文件。")] * 5)
    agent11 = asyncio.run(create_agent(
        tools=sub_tools, llm=ScriptedLLM([make_answer("ok")]),
        config=sec_config, subagent_llm=sub_llm,
    ))
    del agent11  # create_agent 已完成接线

    from Tools.registry import registry as _reg
    raw11 = asyncio.run(_reg.dispatch(
        "AgentDelegate", {"agent_type": "explore", "task": "测试子代理接线"},
    ))
    check("AgentDelegate 返回 JSON", isinstance(raw11, str) and len(raw11) > 2)
    data11 = json.loads(raw11) if raw11.strip().startswith("{") else {}
    check("子代理用了注入的假 LLM 并正常收尾",
          "子代理探索完成" in json.dumps(data11, ensure_ascii=False)
          or "子代理探索完成" in raw11,
          raw11[:200])
    check("假 LLM 被调用（未走 LLMCore）", len(sub_llm.calls) >= 1)

    asyncio.run(close_tools(tools))
    asyncio.run(close_tools(cmd_tools))
    from Tools.config_proxy import reset_config
    reset_config()
    shutil.rmtree("runtime_memory/temp/agent_loop_test", ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
