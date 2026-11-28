"""architect 子代理契约测试：类型注册 / 工具与命令权限 / 记忆塔隔离。

直接运行：python tests/test_architect_subagent.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Agent.SubAgent import SubAgentConfig  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def test_registration():
    print("== 类型注册 ==")
    prompt = SubAgentConfig.get_system_prompt("architect")
    check("architect 提示词加载", bool(prompt) and "Goharu" in prompt)
    tools = SubAgentConfig.get_allowed_tools("architect")
    check("工具含 Write/Edit/run_cmd",
          {"Write", "Edit", "run_cmd", "Read", "Grep", "Glob"} <= set(tools))
    check("工具含 AgentDelegate", "AgentDelegate" in tools)
    check("工具含 RequestRestart", "RequestRestart" in tools)
    check("CRITICAL_REMINDERS 有 architect",
          "architect" in SubAgentConfig.CRITICAL_REMINDERS)
    reminder = SubAgentConfig.CRITICAL_REMINDERS["architect"]
    check("提醒含重启/模型下载/LED 硬约束",
          "RequestRestart" in reminder and "只按明确指令下载" in reminder
          and "LED 探测只读" in reminder)


def test_command_policy():
    print("== 命令策略 ==")
    ok = SubAgentConfig.is_command_allowed
    # architect 宽黑名单：工作命令放行（灰名单确认由 run_cmd 内部链负责）
    check("architect 放行 git status", ok("architect", "git status"))
    check("architect 放行 python 测试", ok("architect", "python tests/test_x.py"))
    check("architect 放行 del（留给确认链）", ok("architect", "del foo.tmp"))
    check("architect 放行 mkdir", ok("architect", "mkdir tmp_dir"))
    check("architect 拦 shell 重定向写", not ok("architect", "echo x > a.txt"))
    check("architect 拦 cat 写", not ok("architect", "cat > a.txt"))
    check("architect 放行 2>/dev/null", ok("architect", "ls 2>/dev/null"))
    check("architect 拦长 sleep", not ok("architect", "sleep 30"))
    # 回归：explore/plan 只读性不受 architect 影响
    check("explore 仍拦 ls > file", not ok("explore", "ls > file"))
    check("explore 仍拦 rm", not ok("explore", "rm -rf x"))
    check("explore 放行 git log", ok("explore", "git log --oneline"))
    # 回归：verify 黑名单与 sleep 上限不变
    check("verify 放行 pytest", ok("verify", "python -m pytest tests/"))
    check("verify 拦长 sleep", not ok("verify", "sleep 60"))
    check("verify 拦重定向", not ok("verify", "echo x > a.txt"))


def test_delegate_policy():
    print("== Architect 仅委托 Explore ==")
    from Agent.SubAgent import _SubToolRuntime

    schema = {
        "type": "object",
        "properties": {"agent_type": {"type": "string", "enum": ["explore", "plan"]}},
    }

    class Entry:
        name = "AgentDelegate"
        description = "delegate"
        arguments_schema = schema

    class Registry:
        def __init__(self):
            self.dispatched = []

        def get_entry(self, name):
            return Entry() if name == "AgentDelegate" else None

        async def dispatch(self, name, args):
            self.dispatched.append((name, dict(args)))
            return "ok"

    registry = Registry()
    runtime = _SubToolRuntime("architect", ["AgentDelegate"], registry)
    definition = runtime.last_tool_definitions[0]
    check("Architect 看到的 enum 仅 Explore",
          definition["inputSchema"]["properties"]["agent_type"]["enum"] == ["explore"])
    check("registry 原 schema 未被修改",
          schema["properties"]["agent_type"]["enum"] == ["explore", "plan"])
    result = asyncio.run(runtime.call_tool(
        "AgentDelegate", {"agent_type": "Explore", "task": "只读调查"}))
    check("Explore 大小写不敏感放行", result == "ok" and len(registry.dispatched) == 1)
    for bad_type in ("plan", "verify", "architect", "", None):
        result = asyncio.run(runtime.call_tool(
            "AgentDelegate", {"agent_type": bad_type, "task": "越权"}))
        check(f"拒绝 {bad_type!r} 委托",
              "只能委托只读 Explore" in json.loads(result)["error"])
    check("越权调用零 dispatch", len(registry.dispatched) == 1)
    explore_runtime = _SubToolRuntime(
        "explore", SubAgentConfig.get_allowed_tools("explore"), registry)
    result = asyncio.run(explore_runtime.call_tool(
        "AgentDelegate", {"agent_type": "explore", "task": "递归"}))
    check("Explore 不能继续递归委托", "不允许" in result and len(registry.dispatched) == 1)

    print("== RequestRestart 仅 architect 可用 ==")
    class RestartEntry:
        name = "RequestRestart"
        description = "restart"
        arguments_schema = {"type": "object", "properties": {}}

    class RestartRegistry(Registry):
        def get_entry(self, name):
            return RestartEntry() if name == "RequestRestart" else None

    rr = RestartRegistry()
    arch_rt = _SubToolRuntime("architect", ["RequestRestart"], rr)
    check("architect 可调用 RequestRestart",
          asyncio.run(arch_rt.call_tool("RequestRestart", {"reason": "t"})) == "ok"
          and len(rr.dispatched) == 1)
    for agent_type, tools in (("explore", SubAgentConfig.get_allowed_tools("explore")),
                              ("plan", SubAgentConfig.get_allowed_tools("plan")),
                              ("verify", SubAgentConfig.get_allowed_tools("verify"))):
        rt = _SubToolRuntime(agent_type, list(tools) + ["RequestRestart"], rr)
        result = asyncio.run(rt.call_tool("RequestRestart", {"reason": "越权"}))
        check(f"{agent_type} 调 RequestRestart 被拒且零 dispatch",
              "不允许" in result and len(rr.dispatched) == 1)


def test_memory_tower_isolation():
    print("== 记忆塔隔离 ==")
    from Memory.bridge import create_memory

    tmp = Path(tempfile.mkdtemp(prefix="arch_test_"))
    empty_model = tmp / "empty_model"
    empty_model.mkdir()

    def make(root):
        return create_memory(overrides={
            "memory.root_dir": str(root),
            "memory.retrieval.embedding_model_path": str(empty_model),
        })

    tower_a = make(tmp / "a")
    tower_b = make(tmp / "b")
    try:
        tower_a.append({"role": "user", "content": "Goharu 的缓存断点上限是 4"})
        tower_a.add_atom("fact", "Goharu缓存", "断点上限4个", slot="cache")
        pack_b = tower_b.retrieve("缓存断点")
        check("塔 B 检索不到塔 A 的原子",
              not (pack_b.get("memories") or []))
        pack_a = tower_a.retrieve("缓存断点")
        check("塔 A 能检索到自己的原子",
              bool(pack_a.get("memories")))
    finally:
        tower_a.close()
        tower_b.close()


def test_architect_memory_degraded():
    print("== architect_memory 降级 ==")
    from Agent import architect_memory

    # 空 transcript 归档：优雅返回
    architect_memory.archive_transcript([])
    check("空 transcript 归档不抛异常", True)
    # 空 pack 格式化：返回空串
    check("空 pack 格式化为空串", architect_memory.format_experience_block(None) == "")
    check("无命中 pack 格式化为空串",
          architect_memory.format_experience_block({"memories": [], "scenes": []}) == "")
    block = architect_memory.format_experience_block({
        "memories": [{"subject": "缓存", "text": "断点上限4"}],
        "scenes": [{"title": "缓存架构", "text": "前缀逐字节稳定"}],
    })
    check("有命中 pack 格式化含经验块标题", "记忆塔" in block and "缓存" in block)


if __name__ == "__main__":
    test_registration()
    test_command_policy()
    test_delegate_policy()
    test_memory_tower_isolation()
    test_architect_memory_degraded()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
