"""MemoryBridge — Memory 系统的独立使用入口（桥接层）。

主项目（Goharu）内：Gateway 亦经本桥接层装配（create_memory），
行为门面委托给注入共享 db 组件的 MemoryManager——行为只有一份实现。
MemoryConfig 无 overrides 时自动回退全局 config.yaml。

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
        # 行为门面复用 MemoryManager 唯一实现（注入共享 db 通路的组件），
        # bridge 只做装配，不平行实现 add_atom / memory_stats / 检索日志
        from Memory.MemoryManager import MemoryManager
        self._manager = MemoryManager(
            cfg=self._cfg,
            working=self.working,
            retriever=self.retriever,
            persona_generator=self.persona,
        )
        self._llm = llm
        self._ingestion = None
        self._scene_extractor = None

    # ------------------------------------------------------------------
    # Working memory (L0)
    # ------------------------------------------------------------------

    def append(self, message):
        """追加一条消息（dict 或 ToolCall）到工作记忆。"""
        self._manager.append(message)

    def get_context(self):
        """获取保留期内的全部工作记忆消息。"""
        return self._manager.get_context()

    def clear(self):
        """清空当前会话的工作记忆。"""
        self._manager.clear_context()

    def clear_context(self):
        """clear() 的别名（与 MemoryManager 命名对齐）。"""
        self._manager.clear_context()

    @property
    def hybrid_retriever(self):
        """MemoryManager 命名对齐的别名（memory_tools 等宿主按此名访问）。"""
        return self.retriever

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

    def retrieve_prompt_memory(self, messages=None):
        """ActorAgent 兼容接口（结构化接口）：自动查询检索 + query_text +
        memory_stats（便利贴数据源）+ 检索日志，与 MemoryManager 行为一致。

        messages: 可选，自定义查询上下文（多 session 时由 SessionMemoryView
        传入本 session 的上下文；None = 用本 bridge 的工作记忆）。
        """
        return self._manager.retrieve_prompt_memory(messages=messages)

    def get_persona(self):
        """读取 persona.md 全文，不存在返回 None。"""
        return self.persona.read_persona()

    def get_persona_markdown(self):
        """get_persona() 的别名（与 MemoryManager 命名对齐，异常安全）。"""
        return self._manager.get_persona_markdown()

    def add_atom(self, atom_type, subject, canonical_text, slot="", salience=0.6):
        """模型主动写入 L1 记忆原子（memory_add 工具路径，含去重与索引）。"""
        return self._manager.add_atom(
            atom_type, subject, canonical_text, slot=slot, salience=salience
        )

    def restore_l1_atom(self, old_row, atom_id):
        """回退专用（Checkpoint 唯一触达 SQL 的通道）：把 atom 恢复到 old_row。

        old_row=None → 物理删除（逆 insert）；否则完整写回 + fts/bm25 侧表同步。
        返回恢复前的行（供回退方生成 redo 记录）。SQL 只在本模块内执行。
        """
        from Memory.repositories.L1Repository import L1Repository
        return L1Repository(self.db).restore_atom(old_row, atom_id)

    def warmup_embedding(self):
        """预热 embedding 模型（触发惰性加载，避免首轮检索卡顿）。

        降级链保护：embedding 不可用时静默返回 False，不抛异常。
        """
        try:
            if not self.embedding.available:
                return False
            self.embedding.encode("warmup")
            return True
        except Exception as exc:
            logger.info("MemoryBridge: embedding warmup skipped: %s", exc)
            return False

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
            from Memory.repositories.L1Repository import L1Repository
            from Memory.repositories.L2Repository import L2Repository
            # 必须显式注入共享 db 的仓储：SceneExtractor 缺省会按全局 config
            # 自建仓储——overrides 指向独立目录时写操作会漏到宿主库
            self._scene_extractor = SceneExtractor(
                scene_file_manager=self.scene_files,
                l1_repo=L1Repository(self.db),
                l2_repo=L2Repository(self.db),
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

    def close(self):
        """释放资源：关闭当前线程的 SQLite 连接（释放文件锁）。

        独立使用/测试退出前务必调用，否则 Windows 上 memory.db 会被占用。
        """
        self.db.close()


def create_memory(overrides=None, llm=None, session_id=None):
    """工厂函数：等价于 MemoryBridge(overrides, llm, session_id)。"""
    return MemoryBridge(overrides=overrides, llm=llm, session_id=session_id)
