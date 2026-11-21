"""最小示例：外部 harness 接入完整 Agent Loop（工具 + 记忆 + 上下文管线）。

离线可跑：用剧本化假 LLM 演示完整 ReAct 循环（模型决定调工具 → 执行 → 模型
总结回答）。把 ScriptedLLM 换成你的真实模型客户端即成完整 harness——契约只有
一个方法：query(messages, on_retry=None, tools=..., tool_choice=...) -> 带
.content 块（text/tool_use/thinking）与 .stop_reason 的响应。

运行（在 Goharu 仓库根目录）：python examples/agent_loop_demo.py
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Agent.api import create_agent
from Memory.bridge import create_memory
from Tools.api import close_tools, create_tools

DEMO_DIR = Path("runtime_memory/temp/agent_loop_demo")


class FakeResponse:
    """结构化接口 LLM 响应（对齐 Anthropic Messages 返回结构）。"""

    def __init__(self, blocks, stop_reason="end_turn", usage=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = usage


class ScriptedLLM:
    """剧本化假 LLM：第一轮调 Glob 工具，第二轮基于工具结果回答。"""

    def __init__(self):
        self.step = 0

    def query(self, messages, on_retry=None, tools=None, **kwargs):
        self.step += 1
        print(f"  [llm] 第 {self.step} 次调用：{len(messages)} 条消息, "
              f"{len(tools or [])} 个工具可用")
        if self.step == 1:
            return FakeResponse(
                [{"type": "thinking", "thinking": "用户想看文件，用 Glob"},
                 {"type": "tool_use", "id": "call_1", "name": "Glob",
                  "input": {"pattern": "Tools/*.py"}}],
                stop_reason="tool_use",
            )
        return FakeResponse(
            [{"type": "text", "text": "找到了这些文件（见上方工具结果）。"}],
            stop_reason="end_turn",
        )


async def main():
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    empty_model = DEMO_DIR / "empty_model"  # 向量腿快速降级，demo 不加载大模型
    empty_model.mkdir(exist_ok=True)

    tools = await create_tools(modules=["Tools.builtin.glob_tool",
                                        "Tools.builtin.sleep_tool"])
    memory = create_memory(overrides={
        "memory.root_dir": str(DEMO_DIR / "memory"),
        "memory.retrieval.embedding_model_path": str(empty_model),
    })
    agent = await create_agent(
        tools=tools,
        llm=ScriptedLLM(),
        memory=memory,
        on_event=lambda etype, payload: print(f"  [event] {etype}:",
                                              str(payload)[:90]),
        max_steps=8,
    )

    print("[user] 看看 Tools 目录有什么文件")
    reply = await agent.run_turn("看看 Tools 目录有什么文件")
    print(f"[assistant] {reply}")

    # 记忆已持久化：手动触发 L1 原子入库（启发式，无需 LLM）后可检索
    atoms = memory.ingest_atoms(None)
    pack = memory.retrieve("用户刚才问了什么")
    print(f"[memory] L1 原子入库 {len(atoms)} 条；检索命中 "
          f"{len(pack['memories'])} 条记忆, {len(pack['scenes'])} 个场景")

    memory.close()
    await close_tools(tools)
    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    print("[demo] 完成，临时目录已清理")


if __name__ == "__main__":
    asyncio.run(main())
