#!/usr/bin/env python3
"""绝对仿真：复现 Goharu 每轮记忆注入的完整链路，输出与真实运行时逐字节一致的注入文本。

复现的链路（与 ActorAgent.act 首步完全一致，检索逻辑零重写，直接调用真实类）：

    working.get_context()                              → 最近 retention_days 天消息
      └─ HybridRetriever.build_query_from_messages()   → 取最近 query_window_messages 条 user 文本拼 query
           └─ HybridRetriever.retrieve(query)          → FTS5 ‖ embedding ‖ BM25 → RRF → rerank
                └─ PromptAssembler._build_memory_context()  → <system-reminder> 注入块

用法：
    # 1) 用当前真实上下文（L0 working memory）模拟"现在这一轮"
    python scripts/simulate_memory_injection.py --live

    # 2) 手动喂一条用户消息（模拟上下文只有这一条）
    python scripts/simulate_memory_injection.py --message "帮我看看坦克大战的 bug"

    # 3) 手动喂多条历史（JSON 数组 [{"role","content"}, ...]）
    python scripts/simulate_memory_injection.py --messages history.json

    # 4) 跳过 query 构建，直接给定检索 query
    python scripts/simulate_memory_injection.py --query "坦克大战 关卡设计"

    # 5) 展示检索内部每一步（FTS/embedding/BM25 各腿命中 + RRF + rerank）
    python scripts/simulate_memory_injection.py --live --trace

    # 6) 跳过 embedding 腿（快速只看 FTS5+BM25；结果 ≠ 真实运行时）
    python scripts/simulate_memory_injection.py --query "..." --skip-embedding

    # 7) 把完整检索包导出为 JSON 便于 diff 对比
    python scripts/simulate_memory_injection.py --live --dump-json pack.json

注意：首次运行会加载 Qwen3-Embedding-0.6B 模型（~1.2GB），需几分钟；
加载后同进程内复用。--skip-embedding 可绕过（但结果不含向量腿）。
"""
import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure:
        _reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 相对路径（./runtime_memory、./models/...、./prompts/...）都基于 cwd，
# 真实运行时 cwd 即 repo root，仿真必须一致。
import os
os.chdir(REPO_ROOT)

from Memory.retrieval.HybridRetriever import HybridRetriever  # noqa: E402
from Prompting.PromptAssembler import PromptAssembler  # noqa: E402


def disable_embedding():
    """模拟本地 embedding 模型加载失败（走真实降级路径，encode 返回 []）。

    与运行时"模型加载失败 → 向量腿禁用"完全一致，而非 monkeypatch 方法。
    """
    from Memory.retrieval.EmbeddingService import EmbeddingService
    EmbeddingService._local_failed = True
    EmbeddingService._model = None


def build_messages_from_args(args):
    """构造 messages 列表（模拟 working memory 上下文）。"""
    if args.messages:
        data = json.loads(Path(args.messages).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            sys.exit("[错误] --messages 文件必须是 JSON 数组")
        messages = []
        for i, item in enumerate(data):
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                messages.append({
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                })
            else:
                sys.exit(f"[错误] --messages 第 {i} 项必须是 str 或 dict")
        return messages
    if args.message:
        return [{"role": "user", "content": args.message}]
    return None


def load_live_messages():
    from Memory.MemoryManager import MemoryManager
    mgr = MemoryManager()
    messages = mgr.get_context()
    return messages, mgr


def fmt_candidate(c, leg=""):
    rt = c.get("record_type", "?")
    sid = c.get("source_id", "?")
    title = c.get("title", "?")
    text = (c.get("text") or c.get("summary") or "")[:60]
    score = c.get("score", 0.0)
    tag = f" [{leg}]" if leg else ""
    return f"  {rt}:{sid} | {title} | {text!r} | score={score:.4f}{tag}"


def trace_retrieval(retriever, query, final_k):
    print("\n" + "=" * 70)
    print("检索内部 trace（各腿命中 → RRF 融合 → rerank）")
    print("=" * 70)

    fts_k = retriever.fts_k
    emb_k = retriever.embedding_k
    bm25_k = retriever.bm25_k

    print(f"\n[FTS5] top_k={max(final_k, fts_k)}")
    fts = retriever._fts_search(query, top_k=max(final_k, fts_k))
    if not fts:
        print("  （无命中）")
    for c in fts:
        print(fmt_candidate(c, f"fts_rank={c.get('fts_rank')}"))

    print(f"\n[Embedding] top_k={max(final_k, emb_k)}")
    emb = retriever._embedding_search(query, top_k=max(final_k, emb_k))
    if not emb:
        print("  （无命中或向量腿不可用）")
    for c in emb:
        print(fmt_candidate(c, f"sim={c.get('embedding_similarity', 0):.4f}"))

    if retriever.bm25_enabled:
        print(f"\n[BM25] top_k={max(final_k, bm25_k)}")
        bm = retriever._bm25_search(query, top_k=max(final_k, bm25_k))
        if not bm:
            print("  （无命中）")
        for c in bm:
            print(fmt_candidate(c, f"bm25={c.get('bm25_score', 0):.4f}"))
    else:
        bm = []

    candidate_lists = [fts, emb] + ([bm] if retriever.bm25_enabled else [])
    print("\n[RRF 融合]")
    fused = retriever._rrf_fuse(candidate_lists)
    for c in fused:
        print(fmt_candidate(c))

    print(f"\n[Rerank] enabled={retriever.reranker.enabled}, top_k={final_k}")
    reranked = retriever.reranker.rerank(query, fused, top_k=final_k)
    for c in reranked:
        print(fmt_candidate(c, f"rerank={c.get('rerank_score', 0):.4f}"))


def print_pack(pack):
    profile = (pack.get("profile_summary") or "").strip()
    memories = pack.get("memories") or []
    scenes = pack.get("scenes") or []

    print("\n" + "=" * 70)
    print("检索结果（retrieve() 原始返回包）")
    print("=" * 70)

    if profile:
        print("\n── L3 用户画像（profile_summary）──")
        print(profile)
    else:
        print("\n── L3 用户画像：无 ──")

    print(f"\n── L1 记忆原子（memories，{len(memories)} 条）──")
    if not memories:
        print("  （无）")
    for i, m in enumerate(memories, 1):
        meta = m.get("metadata") or {}
        print(f"  [{i}] id={m.get('source_id')} type={meta.get('atom_type', '?')} "
              f"salience={meta.get('salience', '?')} score={m.get('score', 0):.4f}")
        print(f"      subject={meta.get('subject') or '-'} slot={meta.get('slot') or '-'}")
        print(f"      title={m.get('title')}")
        print(f"      text={m.get('text')}")

    print(f"\n── L2 场景（scenes，{len(scenes)} 条）──")
    if not scenes:
        print("  （无）")
    for i, s in enumerate(scenes, 1):
        print(f"  [{i}] slug={s.get('source_id')} score={s.get('score', 0):.4f}")
        print(f"      title={s.get('title')}")
        print(f"      keywords={s.get('keywords')}")
        print(f"      summary={(s.get('summary') or '')[:200]}")


def main():
    ap = argparse.ArgumentParser(description="绝对仿真 Goharu 每轮记忆注入链路")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--live", action="store_true", help="用当前真实 L0 上下文模拟本轮")
    src.add_argument("--message", help="手动喂一条用户消息")
    src.add_argument("--messages", help="手动喂多条历史（JSON 数组文件）")
    src.add_argument("--query", help="直接给定检索 query（跳过 build_query）")
    ap.add_argument("--trace", action="store_true", help="展示检索内部各腿命中明细")
    ap.add_argument("--skip-embedding", action="store_true", help="禁用 embedding 腿（快速模式，非真实结果）")
    ap.add_argument("--dump-json", help="把完整检索包导出为 JSON 文件")
    args = ap.parse_args()

    # ── 构造 query + retriever ──────────────────────────────────────
    if args.live:
        messages, mgr = load_live_messages()
        retriever = mgr.hybrid_retriever
        print(f"[输入] 当前真实上下文（L0 working memory）：{len(messages)} 条消息")
        query = retriever.build_query_from_messages(messages)
        if args.skip_embedding:
            disable_embedding()
        pack = retriever.retrieve(query)
        pack["memory_stats"] = mgr._collect_memory_stats()
    elif args.query:
        retriever = HybridRetriever()
        query = args.query
        if args.skip_embedding:
            disable_embedding()
        print(f"[输入] 直接给定 query")
        pack = retriever.retrieve(query)
    else:
        messages = build_messages_from_args(args)
        if not messages:
            ap.error("必须指定 --live / --message / --messages / --query 之一")
        retriever = HybridRetriever()
        print(f"[输入] 手动上下文：{len(messages)} 条消息")
        query = retriever.build_query_from_messages(messages)
        if args.skip_embedding:
            disable_embedding()
        pack = retriever.retrieve(query)

    if args.skip_embedding:
        print("[警告] --skip-embedding：embedding 腿已禁用，结果 ≠ 真实运行时（真实含向量腿）")

    # ── 输出检索 query ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("检索 Query")
    print("=" * 70)
    print(query if query else "(empty)")

    # ── trace ────────────────────────────────────────────────────────
    if args.trace:
        trace_retrieval(retriever, query, retriever.final_k)

    # ── 原始返回包 ───────────────────────────────────────────────────
    print_pack(pack)

    # ── 最终注入文本块（与真实运行时逐字节一致）──────────────────────
    assembler = PromptAssembler()

    print("\n" + "=" * 70)
    print("persona.md 全文 → system 前缀（_build_persona_section，断点1覆盖）")
    print("=" * 70)
    persona_text = None
    try:
        persona_text = retriever.persona_generator.read_persona()
    except Exception:
        pass
    persona_section = assembler._build_persona_section(persona_text)
    if persona_section is not None:
        print(persona_section.content)
    else:
        print("(None —— persona.md 不存在或为空，本轮无前缀注入)")

    print("\n" + "=" * 70)
    print("per-turn 注入文本块（_build_turn_context 输出：便利贴 + 记忆 + 技能，贴最新用户消息）")
    print("=" * 70)
    injected = assembler._build_turn_context(pack, {})
    if injected:
        print(injected)
    else:
        print("(None —— 本轮无注入)")

    # ── 导出 JSON ────────────────────────────────────────────────────
    if args.dump_json:
        Path(args.dump_json).write_text(
            json.dumps(pack, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[已导出] 检索包 → {args.dump_json}")


if __name__ == "__main__":
    main()
