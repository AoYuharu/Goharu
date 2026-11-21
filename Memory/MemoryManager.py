import os
from datetime import datetime
from pathlib import Path

from Memory.memory_config import resolve_config
from Memory.WorkingMemory import WorkingMemory
from Memory.retrieval.HybridRetriever import HybridRetriever

try:
    from Core.LogManager import get_logger
    logger = get_logger(__name__)
except ImportError:  # 独立使用 Memory 包时无 Core 依赖
    import logging
    logger = logging.getLogger(__name__)


class MemoryManager:
    """记忆管理器 — 统一 facade。

    金字塔体系：L0 working → L1 atoms → L2 scene_blocks/*.md → L3 persona.md。
    编排逻辑在 Agent.PipelineManager 中。
    """

    def __init__(self, cfg=None, working=None, retriever=None, persona_generator=None):
        self._cfg = resolve_config(cfg)
        self.working = working or WorkingMemory(cfg=self._cfg)
        self._hybrid_retriever = retriever
        self._persona_generator = persona_generator

    @property
    def hybrid_retriever(self):
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever(cfg=self._cfg)
        return self._hybrid_retriever

    @property
    def persona_generator(self):
        if self._persona_generator is None:
            from Memory.pipeline.PersonaGenerator import PersonaGenerator
            self._persona_generator = PersonaGenerator(cfg=self._cfg)
        return self._persona_generator

    # ── 快捷属性（供 PipelineManager 内部使用） ──

    @property
    def l0_repo(self):
        return self.working.l0_repo

    # ── Working memory ──────────────────────────────────────────────

    def append(self, message):
        self.working.append(message)

    def clear_context(self):
        self.working.clear_all()

    def get_context(self):
        return self.working.get_messages()

    # ── Pyramid: L0-L3 retrieval ────────────────────────────────────

    def retrieve_prompt_memory(self, messages=None):
        """Query-time hybrid retrieval: L1 atoms + L2 scenes + L3 persona summary.

        messages: 可选，用于构建查询的上下文消息（默认取本管理器的工作记忆）。
        多 session 场景下由 SessionMemoryView 传入本 session 的上下文。
        """
        if messages is None:
            messages = self.get_context()
        retriever = self.hybrid_retriever
        query = retriever.build_query_from_messages(messages)
        pack = retriever.retrieve(query)
        pack["query_text"] = query
        pack["memory_stats"] = self._collect_memory_stats()
        self._dump_retrieval_log(query, pack)

        # 可读检索日志：每轮一条，含 query 摘要 + 各层命中数 + 命中标题
        memories = pack.get("memories") or []
        scenes = pack.get("scenes") or []
        atom_titles = ", ".join(
            str(m.get("title", "?"))[:40] for m in memories[:5]
        )
        scene_titles = ", ".join(
            str(s.get("title", "?"))[:40] for s in scenes[:3]
        )
        logger.info(
            "检索：query=%.80s | L1=%d[%s] | L2=%d[%s] | persona=%s",
            (query or "(empty)"),
            len(memories), atom_titles,
            len(scenes), scene_titles,
            "yes" if pack.get("profile_summary") else "no",
        )
        return pack

    def get_persona_markdown(self):
        try:
            return self.persona_generator.read_persona()
        except Exception:
            return None

    def add_atom(self, atom_type, subject, canonical_text, slot="", salience=0.6):
        """模型主动写入 L1 记忆原子（memory_add 工具路径）。

        含 n-gram 去重 + FTS/BM25/embedding 索引；去重命中返回 None。
        不改动 turn 的 pipeline_state，不干扰 PipelineManager 自动提取。
        """
        from Memory.pipeline.MemoryIngestionService import MemoryIngestionService
        from Memory.repositories.L0Repository import L0Repository
        # 必须用 hybrid_retriever 的同一条 db 通路（cfg overrides 指向独立目录时，
        # working.l0_repo 可能按全局配置落了另一个库——Memory/CLAUDE.md 不变量 2）
        db = self.hybrid_retriever.db
        l0_repo = L0Repository(db)
        turn = l0_repo.get_or_create_turn(
            session_id=self.working.session_id,
            day_key=datetime.now().strftime("%Y-%m-%d"),
        )
        if not turn:
            return None
        ingestion = MemoryIngestionService(
            l0_repo=l0_repo,
            embedding_service=self.hybrid_retriever.embedding_service,
        )
        return ingestion.store_manual_atom(
            {
                "atom_type": atom_type,
                "subject": subject,
                "slot": slot,
                "canonical_text": canonical_text,
                "salience": salience,
            },
            source_turn_id=turn["id"],
        )

    def _collect_memory_stats(self):
        """记忆库规模 + persona 新鲜度（每轮检索时顺带算，COUNT(*) + 文件 mtime，开销可忽略）。

        供 Prompting 便利贴渲染"有多少记忆可查 / persona 是否可能过期"，降低记忆幻觉。
        失败返回 None（便利贴静默跳过，不影响检索主链路）。
        """
        try:
            db = self.hybrid_retriever.db
            l1 = db.scalar(
                "SELECT COUNT(*) FROM l1_memory_atoms WHERE status='active'", default=0
            ) or 0
            l2 = db.scalar(
                "SELECT COUNT(*) FROM l2_scenes WHERE status='active'", default=0
            ) or 0
            age = None
            try:
                path = self.persona_generator.persona_path
                if path.exists():
                    age = (
                        datetime.now().timestamp() - path.stat().st_mtime
                    ) / 86400.0
            except Exception:
                pass
            return {
                "l1_atoms": int(l1),
                "l2_scenes": int(l2),
                "persona_age_days": age,
            }
        except Exception:
            return None

    # ── Logging ─────────────────────────────────────────────────────

    def _dump_retrieval_log(self, query, pack):
        log_dir = self._cfg.get("memory.root_dir", "./runtime_memory")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "retrieval_log.txt")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memories = pack.get("memories") or []
        scenes = pack.get("scenes") or []
        profile = pack.get("profile_summary") or ""

        lines = [
            f"{'='*60}",
            f"[{now}] 检索日志",
            f"{'='*60}",
            f"Query: {query if query else '(empty)'}",
            "",
        ]
        if profile:
            lines.append("--- L3 用户画像 ---")
            lines.append(profile)
            lines.append("")

        lines.append(f"--- L1 记忆原子 ({len(memories)}条) ---")
        for i, mem in enumerate(memories, 1):
            title = mem.get("title", "?")
            text = mem.get("text", "")
            score = mem.get("score", 0.0)
            src = mem.get("source_id", "?")
            lines.append(f"  [{i}] {title}  (score={score:.4f}, id={src})")
            lines.append(f"      {text}")
        lines.append("")

        lines.append(f"--- L2 场景 ({len(scenes)}条) ---")
        for i, scene in enumerate(scenes, 1):
            title = scene.get("title", "?")
            summary = scene.get("summary", "")
            score = scene.get("score", 0.0)
            kw = scene.get("keywords", [])
            lines.append(f"  [{i}] {title}  (score={score:.4f}, keywords={kw})")
            lines.append(f"      {summary}")

        lines.append("")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
