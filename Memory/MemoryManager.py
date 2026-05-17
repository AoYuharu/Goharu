import os
from datetime import datetime
from pathlib import Path

from configurationLoader import config
from Memory.WorkingMemory import WorkingMemory
from Memory.retrieval.HybridRetriever import HybridRetriever




class MemoryManager:
    """记忆管理器 — 统一 facade。

    金字塔体系：L0 working → L1 atoms → L2 scene_blocks/*.md → L3 persona.md。
    编排逻辑在 Agent.PipelineManager 中。
    """

    def __init__(self):
        self.working = WorkingMemory()
        self._hybrid_retriever = None
        self._persona_generator = None

    @property
    def hybrid_retriever(self):
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever()
        return self._hybrid_retriever

    @property
    def persona_generator(self):
        if self._persona_generator is None:
            from Memory.pipeline.PersonaGenerator import PersonaGenerator
            self._persona_generator = PersonaGenerator()
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

    # ── SOUL.md（角色定义，不属于金字塔，始终注入） ──────────────────

    def get_soul_markdown(self):
        root = config.get("memory.root_dir", "./runtime_memory")
        soul_path = Path(root) / "SOUL.md"
        try:
            if soul_path.exists():
                return soul_path.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    # ── Pyramid: L0-L3 retrieval ────────────────────────────────────

    def retrieve_prompt_memory(self):
        """Query-time hybrid retrieval: L1 atoms + L2 scenes + L3 persona summary."""
        messages = self.get_context()
        retriever = self.hybrid_retriever
        query = retriever.build_query_from_messages(messages)
        pack = retriever.retrieve(query)
        pack["query_text"] = query
        self._dump_retrieval_log(query, pack)
        return pack

    def get_persona_markdown(self):
        try:
            return self.persona_generator.read_persona()
        except Exception:
            return None

    # ── Logging ─────────────────────────────────────────────────────

    def _dump_retrieval_log(self, query, pack):
        log_dir = config.get("memory.root_dir", "./runtime_memory")
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
