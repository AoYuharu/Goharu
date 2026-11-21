"""最小示例：外部 harness 如何接入 Goharu 的 Tools 与 Memory 两个独立子系统。

离线可跑：不依赖 Goharu 的 Agent/Gateway 层，也不调真实 LLM——
用 fake_llm 演示接线位置。把它换成你的真实模型调用（OpenAI/Anthropic/本地）
就是一个完整的最小 agent harness。

运行（在 Goharu 仓库根目录）：python examples/standalone_demo.py
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

# 外部 harness 的唯一前置条件：把 Tools/ 与 Memory/ 所在目录放进 sys.path。
# 本示例位于仓库 examples/ 下，因此加入仓库根。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from Tools.api import create_tools, close_tools
from Memory.bridge import create_memory

DEMO_DIR = Path("runtime_memory/temp/standalone_demo")


# ---------------------------------------------------------------------------
# 1) 假模型：演示 harness 主循环的接线位置。真实场景换成你的 LLM API 调用。
#    约定：返回以 "TOOL:" 开头的字符串表示要调用工具，否则为最终回复。
# ---------------------------------------------------------------------------
def fake_llm(messages: list[dict]) -> str:
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if "状态" in last_user:
        return "TOOL:run_cmd"  # 让假模型在问到"状态"时调用工具
    return f"我记住了：{last_user[:30]}"


def main():
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 2) 接入记忆子系统（dict 配置；root_dir 一个键派生全部子路径）
    #    embedding_model_path 指向空目录 → 向量腿快速降级，demo 不加载大模型
    # ------------------------------------------------------------------
    empty_model = DEMO_DIR / "empty_model"
    empty_model.mkdir(parents=True, exist_ok=True)
    memory = create_memory(overrides={
        "memory.root_dir": str(DEMO_DIR / "memory"),
        "memory.retrieval.embedding_model_path": str(empty_model),
    })

    # ------------------------------------------------------------------
    # 3) 接入工具子系统（只加载需要的内置模块）
    # ------------------------------------------------------------------
    tools = asyncio.run(
        create_tools(
            config={"tools": {"background_timeout": 60}},
            modules=["Tools.builtin.core_tools", "Tools.builtin.sleep_tool"],
        )
    )
    tool_defs = asyncio.run(tools.list_tools())
    print(f"[harness] 已加载 {len(tool_defs)} 个工具:",
          ", ".join(d["name"] for d in tool_defs[:6]), "...")

    # ------------------------------------------------------------------
    # 4) 最小 ReAct 循环：append → retrieve → 组装 messages → 模型 → 工具
    # ------------------------------------------------------------------
    conversation = ["我喜欢喝冰美式咖啡", "检查一下 git 状态"]
    for user_msg in conversation:
        memory.append({"role": "user", "content": user_msg})

        # 检索相关长期记忆（首轮为空；数据块由宿主拼进 prompt）
        pack = memory.retrieve()
        memory_block = json.dumps(pack, ensure_ascii=False)

        # 组装喂给模型的消息：系统提示 + 记忆块 + 对话历史
        messages = [
            {"role": "system", "content": "你是助手。可用工具见 tools 定义。"},
            {"role": "system", "content": f"相关记忆：{memory_block}"},
            *memory.get_context(),
        ]

        reply = fake_llm(messages)  # ← 真实 harness 在这里调用 LLM（带 tool_defs）

        if reply.startswith("TOOL:"):
            tool_name = reply[5:]
            result = asyncio.run(
                tools.call_tool(tool_name, {"cmd": "git status"})
            )
            print(f"[tool] {tool_name} -> {str(result.content)[:80]}")
            reply = f"工具已执行，结果摘要：{str(result.content)[:40]}"

        print(f"[assistant] {reply}")
        memory.append({"role": "assistant", "content": reply})

    # ------------------------------------------------------------------
    # 5) 长期记忆：手动触发 L1 原子入库（无需 LLM 的启发式提取）
    # ------------------------------------------------------------------
    atoms = memory.ingest_atoms(None)
    print(f"[memory] L1 原子入库 {len(atoms)} 条")
    pack = memory.retrieve("用户喜欢喝什么咖啡")
    print(f"[memory] 检索命中 {len(pack['memories'])} 条:",
          [m.get("text", "")[:20] for m in pack["memories"]])

    # ------------------------------------------------------------------
    # 6) 收尾：释放资源（Windows 下 close 释放 SQLite 文件锁）
    # ------------------------------------------------------------------
    asyncio.run(close_tools(tools))
    memory.close()
    shutil.rmtree(DEMO_DIR, ignore_errors=True)
    print("[harness] demo 完成，临时目录已清理")


if __name__ == "__main__":
    main()
