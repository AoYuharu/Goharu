"""MemoryBridge — Memory 系统的独立使用入口（桥接层）。

主项目（Goharu）内：Gateway 仍走 MemoryManager + Agent.PipelineManager，
本桥接层不改变其接线；MemoryConfig 无 overrides 时自动回退全局 config.yaml。

独立使用（其他项目 / 脚本）：

    from Memory.bridge import MemoryBridge

    memory = MemoryBridge(
        overrides={                       # 扁平点号键，优先级高于 config.yaml
            "memory.root_dir": "./my_memory",
            "memory.retrieval.use_reranker": False,
        },
        llm=my_llm,                       # 可选：有 query(messages)->str 方法的对象
    )
    memory.append({"role": "user", "content": "我喜欢喝美式咖啡"})
    pack = memory.retrieve("用户喜欢什么咖啡")
    # pack == {"memories": [...], "scenes": [...], "profile_summary": "..."}

L1/L2/L3 管线（extract_scenes / generate_persona）需要 LLM，
未注入 llm 且宿主环境无 Agent 层时会抛出带说明的 RuntimeError。
"""

import logging
from datetime import datetime

from Memory.MemoryDB import MemoryDB
from Memory.memory_config import resolve_config
from Memory.repositories.L0Repository import L0Repository
from Memory.retrieval.EmbeddingService import EmbeddingService
from Memory.retrieval.HybridRetriever import HybridRetriever
from Memory.retrieval.RerankerService import RerankerService
from Memory.scene_blocks.SceneFileManager import SceneFileManager
from Memory.WorkingMemory import WorkingMemory

logger = logging.getLogger(__name__)


class MemoryBridge:
    """把 Memory 各组件用共享实例装配好，对外暴露最小接口。"""

    def __init__(self, overrides=None, llm=None, session_id=None):
        cfg_dict = dict(overrides or {})
        if session_id is not None:
            cfg_dict["memory.session_id"] = str(session_id)
        # root_dir 派生：override 了 root_dir 时，其下的子路径键若未显式给出，
        # 一律跟随 root_dir，而不是回退到全局 config.yaml 里的项目路径
        root = cfg_dict.get("memory.root_dir")
        if root:
            from pathlib import Path
            root_path = Path(root)
            derived = {
                "memory.sqlite.path": root_path / "memory.db",
                "memory.daily.dir": root_path / "daily",
                "memory.scene_blocks.dir": root_path / "scene_blocks",
                "memory.persona.path": root_path / "persona.md",
                "memory.migration.marker_path": root_path / ".migration_v2_done",
            }
            for key, value in derived.items():
                cfg_dict.setdefault(key, str(value))
        self._cfg = resolve_config(cfg_dict)

        # 共享一条 SQLite 连接通路，所有仓储/索引共用同一个 MemoryDB
        self.db = MemoryDB(cfg=self._cfg)
        self.l0_repo = L0Repository(self.db)
        self.embedding = EmbeddingService(self.db, cfg=self._cfg)
        self.scene_files = SceneFileManager(cfg=self._cfg)
        self.working = WorkingMemory(cfg=self._cfg, l0_repo=self.l0_repo)

        from Memory.pipeline.PersonaGenerator import PersonaGenerator
        self.persona = PersonaGenerator(
            scene_file_manager=self.scene_files, llm=llm, cfg=self._cfg,
        )
        self.retriever = HybridRetriever(
            db=self.db,
            embedding_service=self.embedding,
            reranker=RerankerService(cfg=self._cfg),
            scene_file_manager=self.scene_files,
            persona_generator=self.persona,
            cfg=self._cfg,
        )
        self._llm = llm
        self._ingestion = None
        self._scene_extractor = None

    # ------------------------------------------------------------------
    # Working memory (L0)
    # ------------------------------------------------------------------

    def append(self, message):
        """追加一条消息（dict 或 ToolCall）到工作记忆。"""
        self.working.append(message)

    def get_context(self):
        """获取保留期内的全部工作记忆消息。"""
        return self.working.get_messages()

    def clear(self):
        """清空当前会话的工作记忆。"""
        self.working.clear_all()

    # ------------------------------------------------------------------
    # Retrieval (L1 + L2 + L3)
    # ------------------------------------------------------------------

    def retrieve(self, query=None, top_k=None):
        """混合检索（FTS5 + embedding + BM25 → RRF → rerank）。

        query 为 None 时自动从最近工作记忆构建查询。
        返回 {"memories": [...], "scenes": [...], "profile_summary": str}。
        """
        if query is None:
            query = self.retriever.build_query_from_messages(self.get_context())
        return self.retriever.retrieve(query, top_k=top_k)

    def get_persona(self):
        """读取 persona.md 全文，不存在返回 None。"""
        return self.persona.read_persona()

    # ------------------------------------------------------------------
    # Pipeline (L1 ingest / L2 scenes / L3 persona)
    # ------------------------------------------------------------------

    @property
    def ingestion(self):
        if self._ingestion is None:
            from Memory.pipeline.MemoryIngestionService import MemoryIngestionService
            self._ingestion = MemoryIngestionService(
                l0_repo=self.l0_repo,
                embedding_service=self.embedding,
            )
        return self._ingestion

    @property
    def scene_extractor(self):
        if self._scene_extractor is None:
            from Memory.pipeline.SceneExtractor import SceneExtractor
            self._scene_extractor = SceneExtractor(
                scene_file_manager=self.scene_files,
                embedding_service=self.embedding,
                llm=self._llm,
                cfg=self._cfg,
            )
        return self._scene_extractor

    def ingest_atoms(self, atoms):
        """把 L1 记忆原子写入数据库（含 n-gram 去重 + embedding 索引）。

        atoms: [{"atom_type": "fact", "subject": ..., "canonical_text": ...}, ...]
        返回成功入库的原子列表。
        """
        turn = self.l0_repo.get_or_create_turn(
            session_id=self.working.session_id,
            day_key=datetime.now().strftime("%Y-%m-%d"),
        )
        if not turn:
            return []
        return self.ingestion.ingest_turn_atoms(turn["id"], atom_payload=atoms)

    def extract_scenes(self):
        """L2：从所有未分配场景的 L1 原子中提取场景（需要 LLM）。"""
        from Memory.repositories.L1Repository import L1Repository
        unassigned = L1Repository(self.db).list_atoms(only_unassigned=True, limit=100)
        if not unassigned:
            logger.info("MemoryBridge: no unassigned atoms, skipping L2")
            return []
        return self.scene_extractor.extract_scenes([a["id"] for a in unassigned])

    def generate_persona(self):
        """L3：基于全部活跃场景生成/更新 persona.md（需要 LLM）。"""
        slugs = [
            sc["slug"] for sc in self.scene_files.list_scenes()
            if sc.get("slug") and sc.get("status") == "active"
        ]
        if not slugs:
            logger.info("MemoryBridge: no active scenes, skipping L3")
            return None
        return self.persona.generate_persona(slugs, self.persona.read_persona())


def create_memory(overrides=None, llm=None, session_id=None):
    """工厂函数：等价于 MemoryBridge(overrides, llm, session_id)。"""
    return MemoryBridge(overrides=overrides, llm=llm, session_id=session_id)
