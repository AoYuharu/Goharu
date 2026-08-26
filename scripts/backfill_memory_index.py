"""
记忆检索索引回填/修复脚本。

用途：
1. 将 scene_blocks/*.md 全量同步到 SQLite 检索索引
   （l2_scenes 行、成员关系、L1 原子 scene_id 回填、FTS/BM25/embedding）
2. 修复 L1 原子索引：补齐缺失的 embedding/BM25，
   并清除 64 维 hash 兜底向量（与真实向量余弦比较会产生噪声分数）

用法：python scripts/backfill_memory_index.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from Memory.MemoryDB import MemoryDB
from Memory.pipeline.SceneExtractor import SceneExtractor
from Memory.repositories.L1Repository import L1Repository
from Memory.retrieval.BM25Encoder import BM25Encoder
from Memory.retrieval.EmbeddingService import EmbeddingService


def main():
    db = MemoryDB()
    l1_repo = L1Repository(db)
    emb = EmbeddingService(db)
    bm25 = BM25Encoder()

    # ── 1. L2 场景：文件 → SQLite 索引全量同步 ──
    extractor = SceneExtractor()
    synced = extractor._sync_sqlite_from_disk()
    print(f"[L2] scenes synced to sqlite: {synced}")

    # ── 2. 确定目标向量维度（以当前可用的真实编码器为准）──
    probe = emb.encode("dimension probe")
    target_dim = len(probe)
    print(f"[emb] active encoder dim: {target_dim}")
    if target_dim == 64:
        print("[emb] WARNING: only hash fallback available, aborting re-embed")
        return

    # ── 3. L1 原子：清除 hash 向量 + 补齐/重嵌 ──
    db.execute("DELETE FROM embeddings WHERE dim != ?", (target_dim,))
    atoms = l1_repo.list_atoms(status="active", limit=1000)
    re_embedded = 0
    for atom in atoms:
        existing = emb.get_embedding("l1", atom["id"])
        if existing and len(existing) == target_dim:
            continue
        emb.store_embedding("l1", atom["id"], atom.get("canonical_text") or "")
        re_embedded += 1
    print(f"[L1] atoms re-embedded: {re_embedded}/{len(atoms)}")

    # ── 4. L1 原子：补齐缺失的 BM25 条目 ──
    bm25_fixed = 0
    for atom in atoms:
        row = db.query_one(
            "SELECT 1 FROM bm25_sparse WHERE source_table='l1' AND source_id=?",
            (atom["id"],),
        )
        if row:
            continue
        tf_vec = bm25.encode_document(atom.get("canonical_text") or "")
        if tf_vec:
            db.replace_bm25_entry("l1", atom["id"], tf_vec, len(tf_vec))
            bm25_fixed += 1
    print(f"[L1] bm25 entries backfilled: {bm25_fixed}")

    # ── 5. 汇总 ──
    stats = {}
    for table, col in (("l1_memory_atoms", "status"), ("l2_scenes", "status")):
        rows = db.query_all(f"SELECT {col}, COUNT(*) AS n FROM {table} GROUP BY {col}")
        stats[table] = [(r[col], r["n"]) for r in rows]
    emb_counts = db.query_all("SELECT source_table, dim, COUNT(*) AS n FROM embeddings GROUP BY source_table, dim")
    stats["embeddings"] = [(r["source_table"], r["dim"], r["n"]) for r in emb_counts]
    bm25_counts = db.query_all("SELECT source_table, COUNT(*) AS n FROM bm25_sparse GROUP BY source_table")
    stats["bm25_sparse"] = [(r["source_table"], r["n"]) for r in bm25_counts]
    assigned = db.query_one(
        "SELECT COUNT(*) AS n FROM l1_memory_atoms WHERE status='active' AND scene_id IS NOT NULL"
    )
    stats["atoms_with_scene"] = assigned["n"]
    print("[summary]", stats)


if __name__ == "__main__":
    main()
