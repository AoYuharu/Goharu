import json
import logging
import time

logger = logging.getLogger(__name__)

from Memory.MemoryDB import MemoryDB
from Memory.memory_config import resolve_config
from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.retrieval.EmbeddingService import EmbeddingService
from Memory.retrieval.BM25Encoder import BM25Encoder
from Memory.retrieval.RerankerService import RerankerService


class HybridRetriever:
    def __init__(self, db=None, l1_repo=None, l2_repo=None,
                 embedding_service=None, reranker=None, bm25_encoder=None,
                 scene_file_manager=None, persona_generator=None, cfg=None):
        cfg = resolve_config(cfg)
        self.db = db or MemoryDB(cfg=cfg)
        self.l1_repo = l1_repo or L1Repository(self.db)
        self.l2_repo = l2_repo or L2Repository(self.db)
        self.embedding_service = embedding_service or EmbeddingService(self.db, cfg=cfg)
        self.bm25 = bm25_encoder or BM25Encoder()
        self.reranker = reranker or RerankerService(cfg=cfg)
        self.fts_k = int(cfg.get("memory.retrieval.fts_top_k", 8))
        self.embedding_k = int(cfg.get("memory.retrieval.embedding_top_k", 8))
        self.bm25_k = int(cfg.get("memory.retrieval.bm25_top_k", 8))
        self.bm25_enabled = cfg.get("memory.retrieval.bm25_enabled", True)
        self.final_k = int(cfg.get("memory.retrieval.final_top_k", 6))
        self.default_query_window = int(cfg.get("memory.retrieval.query_window_messages", 6))
        self._cfg = cfg
        self._scene_file_manager = scene_file_manager
        self._persona_generator = persona_generator
        # BM25 IDF 缓存：条目数变化或超过 60s 才重建
        self._bm25_idf_count = -1
        self._bm25_idf_time = 0.0

    @property
    def scene_file_manager(self):
        if self._scene_file_manager is None:
            from Memory.scene_blocks.SceneFileManager import SceneFileManager
            self._scene_file_manager = SceneFileManager(cfg=self._cfg)
        return self._scene_file_manager

    @property
    def persona_generator(self):
        if self._persona_generator is None:
            from Memory.pipeline.PersonaGenerator import PersonaGenerator
            self._persona_generator = PersonaGenerator(cfg=self._cfg)
        return self._persona_generator

    def retrieve(self, query, top_k=None):
        from Core.timing import timeit

        query = str(query or "").strip()
        final_k = int(top_k or self.final_k)
        if not query:
            return {"profile_summary": self._read_persona_compact_summary(), "memories": [], "scenes": []}

        candidate_lists = []

        with timeit("memory.retrieve.fts"):
            fts_candidates = self._fts_search(query, top_k=max(final_k, self.fts_k))
        candidate_lists.append(fts_candidates)

        with timeit("memory.retrieve.embedding"):
            embedding_candidates = self._embedding_search(query, top_k=max(final_k, self.embedding_k))
        candidate_lists.append(embedding_candidates)

        if self.bm25_enabled:
            with timeit("memory.retrieve.bm25"):
                bm25_candidates = self._bm25_search(query, top_k=max(final_k, self.bm25_k))
            candidate_lists.append(bm25_candidates)

        with timeit("memory.retrieve.rrf_rerank"):
            fused = self._rrf_fuse(candidate_lists)
            reranked = self.reranker.rerank(query, fused, top_k=final_k)

        with timeit("memory.retrieve.persona"):
            profile = self._read_persona_compact_summary()

        memories = [item for item in reranked if item.get("record_type") == "l1"]
        scenes = [item for item in reranked if item.get("record_type") == "l2"]
        return {
            "profile_summary": profile,
            "memories": memories[:final_k],
            "scenes": scenes[:final_k],
        }

    def _read_persona_compact_summary(self):
        """从 persona.md 读取精简摘要；读不到返回空串。"""
        try:
            summary = self.persona_generator.get_persona_summary()
            if summary and summary != "(empty persona)":
                return summary
        except Exception:
            pass
        return ""

    def build_query_from_messages(self, messages):
        # 仅取最近的 user 角色文本（用户实际发出的内容），
        # 跳过 assistant 回复与工具结果，避免它们主导检索方向。
        user_texts = []
        for item in reversed(list(messages or [])):
            if (item.get("role") or "").strip() != "user":
                continue
            text = self._extract_message_text(item)
            if text:
                user_texts.append(text)
                if len(user_texts) >= self.default_query_window:
                    break
        user_texts.reverse()  # 恢复时间正序
        return "\n".join(user_texts).strip()

    @staticmethod
    def _extract_message_text(item):
        content = item.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return "\n".join(p for p in parts if p).strip()
        return ""

    # FTS5 空格分词做隐式 AND，中文功能词（的/是/什么…）会导致全句匹配失败，
    # 查询侧分词后过滤掉这些停用词，只保留实义 token。
    _FTS_STOPWORDS = frozenset(
        "的 了 是 在 有 和 与 及 或 也 就 都 还 很 更 最 被 把 对 从 到 于 之 为 而 且 但 如果 呢 吗 吧 啊 哦 呀 这 那 我 你 他 她 它 我们 你们 他们 什么 怎么 为什么 哪些 哪 谁 一个 一些 有点 一下 这个 那个 这些 那些 可以 应该 需要 请 帮 让 上 下 中 内 外".split()
    )

    def _fts_search(self, query, top_k=8):
        if not self.db.fts_enabled:
            return []
        # 查询侧分词 + 去停用词（与建索引侧 tokenize_for_fts 分词一致，
        # 但查询丢弃功能词避免隐式 AND 全句失配）
        tokens = [
            t for t in self.db.tokenize_for_fts(query).split()
            if t not in self._FTS_STOPWORDS and len(t.strip()) > 0
        ]
        if not tokens:
            return []
        # 每个 token 加双引号：FTS5 MATCH 对裸词做语法解析，
        # 含 "exit_code"、"a: b" 等 token 会被误认为列过滤语法直接报错
        tokenized_query = " ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        try:
            rows = self.db.query_all(
                """
                SELECT record_type, source_id, content
                FROM fts_memory
                WHERE fts_memory MATCH ?
                LIMIT ?
                """,
                (tokenized_query, int(top_k)),
            )
        except Exception as e:
            logger.warning("HybridRetriever: FTS query failed (%s), skipping FTS leg", e)
            return []
        candidates = []
        for rank, row in enumerate(rows, start=1):
            candidate = self._hydrate_candidate(row["record_type"], row["source_id"])
            if candidate is None:
                continue
            candidate["fts_rank"] = rank
            candidates.append(candidate)
        return candidates

    def _embedding_search(self, query, top_k=8):
        from Core.timing import timeit

        with timeit("memory.retrieve.embedding.encode"):
            query_vector = self.embedding_service.encode(query, prompt_name="query")
        if not query_vector:
            return []
        rows = self.db.query_all(
            "SELECT source_table, source_id, vector_blob FROM embeddings WHERE model_name = ?",
            (self.embedding_service.model_name,),
        )
        # 先算分再 hydrate：避免对全表每行做一次 get_atom/get_scene SQL（N+1），
        # 向量也直接解码行内 blob，不再走 get_embedding 的二次查询
        scored = []
        for row in rows:
            try:
                vector = json.loads(bytes(row["vector_blob"]).decode("utf-8"))
            except Exception:
                continue
            similarity = self.embedding_service.cosine_similarity(query_vector, vector)
            if similarity <= 0.0:
                continue
            scored.append((similarity, row["source_table"], row["source_id"]))
        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        with timeit("memory.retrieve.embedding.hydrate"):
            for rank, (similarity, source_table, source_id) in enumerate(scored[: int(top_k)], start=1):
                candidate = self._hydrate_candidate(source_table, source_id)
                if candidate is None:
                    continue
                candidate["embedding_similarity"] = similarity
                candidate["embedding_rank"] = rank
                results.append(candidate)
        return results

    def _bm25_search(self, query, top_k=8):
        """Sparse BM25 vector search using stored term-frequency vectors.

        IDF 按语料缓存：条目数变化或超过 60s 才重建，避免每次查询全量重算。
        """
        entries = self.db.get_all_bm25_entries()
        if not entries:
            return []

        now = time.time()
        if (
            self._bm25_idf_count != len(entries)
            or now - self._bm25_idf_time > 60
        ):
            all_doc_vectors = []
            for entry in entries:
                tf = self.db.loads_json(entry["term_freq_json"], default={})
                if tf:
                    all_doc_vectors.append(tf)
            if not all_doc_vectors:
                return []
            self.bm25.fit_idf(all_doc_vectors)
            self._bm25_idf_count = len(entries)
            self._bm25_idf_time = now

        query_vec = self.bm25.encode_query(query)
        if not query_vec:
            return []

        # Score every document
        scored = []
        for entry in entries:
            doc_vec = self.db.loads_json(entry["term_freq_json"], default={})
            if not doc_vec:
                continue
            s = self.bm25.score(query_vec, doc_vec)
            if s <= 0.0:
                continue
            candidate = self._hydrate_candidate(entry["source_table"], entry["source_id"])
            if candidate is None:
                continue
            candidate["bm25_score"] = s
            scored.append(candidate)

        scored.sort(key=lambda item: item.get("bm25_score", 0.0), reverse=True)
        results = []
        for rank, candidate in enumerate(scored[:int(top_k)], start=1):
            candidate["bm25_rank"] = rank
            results.append(candidate)
        return results

    def _rrf_fuse(self, candidate_lists, constant=60):
        merged = {}
        for candidates in candidate_lists:
            for rank, candidate in enumerate(candidates, start=1):
                key = (candidate.get("record_type"), candidate.get("source_id"))
                entry = merged.setdefault(key, dict(candidate))
                entry["score"] = float(entry.get("score") or 0.0) + 1.0 / (constant + rank)
        results = list(merged.values())
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results

    def _hydrate_candidate(self, record_type, source_id):
        normalized = "l1" if str(record_type) == "l1" else "l2" if str(record_type) == "l2" else str(record_type)
        if normalized == "l1":
            atom = self.l1_repo.get_atom(source_id)
            if atom is None:
                return None
            return {
                "record_type": "l1",
                "source_id": atom["id"],
                "title": atom.get("subject") or atom.get("slot") or atom.get("atom_type") or "memory",
                "text": atom.get("canonical_text") or "",
                "summary": atom.get("canonical_text") or "",
                "metadata": atom,
                "score": float(atom.get("salience") or 0.0),
            }
        if normalized == "l2":
            # 优先从 scene_blocks/*.md 读取完整内容作为 source of truth
            scene = self.l2_repo.get_scene(source_id)
            if scene is None:
                return None
            slug = scene.get("slug") or ""
            hydrated = self._hydrate_l2_from_scene_file(slug)
            if hydrated is not None:
                return hydrated
            # Fallback to SQLite content
            return {
                "record_type": "l2",
                "source_id": scene["id"],
                "title": scene.get("title") or scene.get("slug") or "scene",
                "text": scene.get("summary") or "",
                "summary": scene.get("summary") or "",
                "keywords": scene.get("keywords") or [],
                "metadata": scene,
                "score": float(scene.get("importance") or 0.0),
            }

    def _hydrate_l2_from_scene_file(self, slug):
        """从 scene_blocks/{slug}.md 读取场景内容。

        Returns:
            dict or None: hydrated candidate dict, or None if file doesn't exist.
        """
        if not slug:
            return None
        try:
            meta, body = self.scene_file_manager.read_scene(slug)
            if meta is None:
                return None
            return {
                "record_type": "l2",
                "source_id": slug,  # use slug as source_id
                "title": meta.get("title") or slug,
                "text": body or "",
                "summary": meta.get("summary") or (body or "").split("\n\n")[0][:500],
                "keywords": meta.get("keywords") or [],
                "metadata": dict(meta),
                "score": float(meta.get("importance") or 0.0),
            }
        except Exception:
            return None
        return None

    # 排名信号统一由 _rrf_fuse 的 1/(60+rank) 提供，不再叠加 1/rank，
    # 避免同一排名被双重计分。score 的语义现在是：salience 先验 + 多路 RRF 排名。
