"""SessionMemoryView 契约测试：L0 隔离 / 金字塔共享 / FIFO 窗口。

- 两 session 的会话记忆互不可见（get_context 隔离）
- 共享 pyramid：写入共享 bridge 的 L1 原子，任一 session 可检索
- FIFO：max_messages=3 时 append 5 条，get_context 只留最新 3 条
- daily JSON：default session 路径不变（向后兼容），非 default 落到子目录
- retrieve_prompt_memory：查询从本 session 上下文构建（带 query_text/memory_stats）

运行：python tests/test_session_memory.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from Memory.bridge import create_memory
from Gateway.session_memory import SessionMemoryView

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("PASS" if cond else "FAIL") + f": {name}")


TMP = Path(tempfile.mkdtemp(prefix="session_memory_test_"))
EMPTY_MODEL_DIR = TMP / "empty_model"   # 非法模型目录 → embedding 快速降级（纯 FTS5+BM25）

bridge = create_memory(overrides={
    "memory.root_dir": str(TMP / "mem"),
    "memory.retrieval.embedding_model_path": str(EMPTY_MODEL_DIR),
    "memory.retrieval.use_reranker": False,
})

s1 = SessionMemoryView("default", bridge, max_messages=0)   # default：不限窗口
s2 = SessionMemoryView("qq_a", bridge, max_messages=3)
s3 = SessionMemoryView("qq_b", bridge, max_messages=3)

# ── 1. L0 隔离 ───────────────────────────────────────────────────────
s2.append({"role": "user", "content": "session A 的消息"})
s2.append({"role": "assistant", "content": "A 的回答"})
check(len(s2.get_context()) == 2, "s2 上下文有 2 条")
check(len(s3.get_context()) == 0, "s3 看不到 s2 的消息（L0 隔离）")
check(len(s1.get_context()) == 0, "default 看不到 qq_a 的消息（L0 隔离）")

# ── 2. FIFO 窗口 ─────────────────────────────────────────────────────
for i in range(5):
    s2.append({"role": "user", "content": f"msg {i}"})
ctx = s2.get_context()
check(len(ctx) == 3, f"FIFO：5 条入队只留 3 条（实际 {len(ctx)}）")
check(ctx[0]["content"] == "msg 2" and ctx[-1]["content"] == "msg 4",
      "FIFO：保留的是最新 3 条")
# 底层工作记忆仍保留全量（L1 提取来源不丢）
check(len(s2.working.get_messages()) == 7,
      "出队不删底层：WorkingMemory 全量保留（提取链完整）")

# ── 3. daily JSON 路径 ───────────────────────────────────────────────
check("qq_a" in str(s2.working.daily_dir), "非 default session daily 落子目录")
check("qq_a" not in str(s1.working.daily_dir) and "default" not in str(s1.working.daily_dir),
      "default session daily 路径不变（向后兼容）")

# ── 4. 金字塔共享 ────────────────────────────────────────────────────
bridge.ingest_atoms([{
    "atom_type": "fact", "subject": "用户",
    "canonical_text": "用户喜欢喝冰美式咖啡",
}])
# 查询从本 session 上下文构建——先给 s3 一条相关上下文
s3.append({"role": "user", "content": "我喜欢喝什么咖啡？"})
pack = s3.retrieve_prompt_memory()
titles = [m.get("title", "") for m in pack.get("memories", [])]
check(any("冰美式" in str(t) for t in titles) or any(
    "冰美式" in str(m.get("text", "")) for m in pack.get("memories", [])),
    "s3 能检索到共享 pyramid 里的 L1 原子")
check("query_text" in pack, "retrieve_prompt_memory 带 query_text")
check("memory_stats" in pack, "retrieve_prompt_memory 带 memory_stats")

# ── 5. get_persona_markdown 透传 ─────────────────────────────────────
check(hasattr(s1, "get_persona_markdown"), "persona 透传接口存在（Actor 静态前缀用）")

# ── 6. max_messages=0 语义（2026-10-31 事故：0 被 or 吞成 10）──
s4 = SessionMemoryView("zz", bridge, max_messages=0)
for i in range(15):
    s4.append({"role": "user", "content": f"m{i}"})
check(len(s4.get_context()) == 15, "max_messages=0 = 不限窗口（0 不被 or 吞掉）")

# ── 7. FIFO 窗口不产生开头孤立 tool_result（裸切片事故回归）──
s5 = SessionMemoryView("zz2", bridge, max_messages=4)
pair_a = {"role": "assistant", "content": [{"type": "tool_use", "id": "p1", "name": "R", "input": {}}]}
pair_b = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "p1", "content": "x"}]}
for m in ({"role": "user", "content": "q1"}, pair_a, pair_b, {"role": "user", "content": "q2"}, {"role": "user", "content": "q3"}):
    s5.append(m)
ctx5 = s5.get_context()
check(len(ctx5) <= 4, "FIFO 窗口生效")
check(not (ctx5 and isinstance(ctx5[0].get("content"), list) and any(
    b.get("type") == "tool_result" for b in ctx5[0]["content"] if isinstance(b, dict))),
    "FIFO 切点落在配对中间时，开头不留孤立 tool_result")

bridge.close()

failed = [n for ok, n in results if not ok]
print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})")
if failed:
    print("FAILED:", *failed, sep="\n  ")
    sys.exit(1)
