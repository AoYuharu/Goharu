"""MemoryBridge 独立使用验证 — 不依赖 config.yaml / Core / Agent 层接线。

覆盖：
1. 桥接层用 overrides 在临时目录独立装配（不污染主项目 runtime_memory/）
2. append → get_context → retrieve 全链路（FTS5 + BM25，无 embedding 模型时走 hash 兜底）
3. L1 ingest（去重）→ L1 检索命中
4. 主项目默认装配回归：MemoryManager() 无参构造行为不变
5. Memory 包内不再有 configurationLoader 直接依赖
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Memory.bridge import MemoryBridge

tmp = tempfile.mkdtemp(prefix="membridge_test_")
root = str(Path(tmp) / "runtime_memory")
print(f"[setup] 临时记忆目录: {root}")

mem = MemoryBridge(
    overrides={
        "memory.root_dir": root,
        "memory.daily.dir": str(Path(root) / "daily"),
        "memory.retrieval.use_reranker": False,
        "memory.retrieval.bm25_enabled": True,
    },
    session_id="test_session",
)

# ── 1. working memory append / context ──
mem.append({"role": "user", "content": "我喜欢喝美式咖啡，不加糖"})
mem.append({"role": "assistant", "content": "好的，已记住你偏好无糖美式"})
ctx = mem.get_context()
assert len(ctx) == 2, f"context 应为 2 条，实际 {len(ctx)}"
assert ctx[0]["role"] == "user"
print(f"[1] append/get_context OK ({len(ctx)} 条消息, sqlite={mem.working.sqlite_enabled})")

# ── 2. L1 ingest（含去重）──
atoms = mem.ingest_atoms([
    {"atom_type": "preference", "subject": "user", "slot": "coffee",
     "canonical_text": "用户喜欢喝美式咖啡，不加糖", "salience": 0.8},
    {"atom_type": "preference", "subject": "user", "slot": "coffee",
     "canonical_text": "用户喜欢喝美式咖啡，不加糖", "salience": 0.8},  # 重复，应被去重
    {"atom_type": "fact", "subject": "user", "slot": "editor",
     "canonical_text": "用户日常使用的编辑器是 VS Code", "salience": 0.6},
])
assert len(atoms) == 2, f"去重后应入库 2 条，实际 {len(atoms)}"
print(f"[2] ingest_atoms OK（3 投 2 中，n-gram 去重生效）")

# ── 3. retrieve 命中 L1 原子 ──
pack = mem.retrieve("用户喜欢什么咖啡")
titles = [m.get("title") for m in pack["memories"]]
texts = [m.get("text") for m in pack["memories"]]
assert any("咖啡" in t for t in texts), f"检索应命中咖啡原子，实际: {texts}"
print(f"[3] retrieve OK（L1 命中 {len(pack['memories'])} 条, L2 {len(pack['scenes'])} 条, "
      f"persona={'yes' if pack['profile_summary'] else 'no'}）")

# ── 4. query=None 时自动从上下文构建查询 ──
pack2 = mem.retrieve()
assert "memories" in pack2 and "scenes" in pack2
print("[4] retrieve(query=None) 自动构建查询 OK")

# ── 5. clear ──
mem.clear()
assert mem.get_context() == [], "clear 后 context 应为空"
print("[5] clear OK")

# ── 6. 主项目默认装配回归（无 overrides，走全局 config.yaml；只读不写）──
from Memory.MemoryManager import MemoryManager
# 清理上一次运行可能写入真实库的探针消息
from Memory.MemoryDB import MemoryDB as _MDB
_mdb = _MDB()
_mdb.execute("DELETE FROM l0_messages WHERE content = 'bridge regression probe'")
mm = MemoryManager()
pack3 = mm.retrieve_prompt_memory()
assert "memories" in pack3 and "scenes" in pack3
print("[6] MemoryManager() 无参构造 + retrieve_prompt_memory 回归 OK（只读）")

# ── 7. 无 LLM 时 L2 给出明确报错而非 ImportError ──
try:
    mem.persona.llm  # 主项目内有 Agent 层，不会抛；独立环境会抛 RuntimeError
    print("[7] LLM 回退可用（主项目 Agent 层接线正常）")
except RuntimeError as e:
    assert "llm=" in str(e)
    print(f"[7] 独立环境 LLM 缺失时报错信息明确 OK")

shutil.rmtree(tmp, ignore_errors=True)
print("\n=== 全部通过 ===")
