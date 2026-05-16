from Memory.MemoryDB import MemoryDB
from Memory.repositories.L1Repository import L1Repository
from Memory.repositories.L2Repository import L2Repository
from Memory.repositories.L3Repository import L3Repository
from Memory.retrieval.EmbeddingService import EmbeddingService
from Memory.retrieval.BM25Encoder import BM25Encoder
from Memory.retrieval.RerankerService import RerankerService
from configurationLoader import config


class HybridRetriever:
    def __init__(self, db=None, l1_repo=None, l2_repo=None, l3_repo=None, embedding_service=None, reranker=None, bm25_encoder=None):
        self.db = db or MemoryDB()
        self.l1_repo = l1_repo or L1Repository(self.db)
        self.l2_repo = l2_repo or L2Repository(self.db)
        self.l3_repo = l3_repo or L3Repository(self.db)
        self.embedding_service = embedding_service or EmbeddingService(self.db)
        self.bm25 = bm25_encoder or BM25Encoder()
        self.reranker = reranker or RerankerService()
        self.fts_k = int(config.get("memory.retrieval.fts_top_k", 8))
        self.embedding_k = int(config.get("memory.retrieval.embedding_top_k", 8))
        self.bm25_k = int(config.get("memory.retrieval.bm25_top_k", 8))
        self.bm25_enabled = config.get("memory.retrieval.bm25_enabled", True)
        self.final_k = int(config.get("memory.retrieval.final_top_k", 6))
        self.default_query_window = int(config.get("memory.retrieval.query_window_messages", 6))

    def retrieve(self, query, top_k=None):
        query = str(query or "").strip()
        final_k = int(top_k or self.final_k)
        if not query:
            return {"profile_summary": self.l3_repo.build_compact_summary(), "memories": [], "scenes": []}

        candidate_lists = []

        fts_candidates = self._fts_search(query, top_k=max(final_k, self.fts_k))
        candidate_lists.append(fts_candidates)

        embedding_candidates = self._embedding_search(query, top_k=max(final_k, self.embedding_k))
        candidate_lists.append(embedding_candidates)

        if self.bm25_enabled:
            bm25_candidates = self._bm25_search(query, top_k=max(final_k, self.bm25_k))
            candidate_lists.append(bm25_candidates)

        fused = self._rrf_fuse(candidate_lists)
        reranked = self.reranker.rerank(query, fused, top_k=final_k)

        memories = [item for item in reranked if item.get("record_type") == "l1"]
        scenes = [item for item in reranked if item.get("record_type") == "l2"]
        return {
            "profile_summary": self.l3_repo.build_compact_summary(),
            "memories": memories[:final_k],
            "scenes": scenes[:final_k],
        }

    def build_query_from_messages(self, messages):
        relevant = list(messages or [])[-self.default_query_window :]
        parts = []
        for item in relevant:
            content = item.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(content))
        return "\n".join(part for part in parts if part).strip()

    def _fts_search(self, query, top_k=8):
        if not self.db.fts_enabled:
            return []
        rows = self.db.query_all(
            """
            SELECT record_type, source_id, content
            FROM fts_memory
            WHERE fts_memory MATCH ?
            LIMIT ?
            """,
            (query, int(top_k)),
        )
        candidates = []
        for rank, row in enumerate(rows, start=1):
            candidate = self._hydrate_candidate(row["record_type"], row["source_id"])
            if candidate is None:
                continue
            candidate["fts_rank"] = rank
            candidate["score"] = candidate.get("score", 0.0) + self._rank_to_score(rank)
            candidates.append(candidate)
        return candidates

    def _embedding_search(self, query, top_k=8):
        query_vector = self.embedding_service.encode(query, prompt_name="query")
        if not query_vector:
            return []
        rows = self.db.query_all(
            "SELECT source_table, source_id, vector_blob FROM embeddings WHERE model_name = ?",
            (self.embedding_service.model_name,),
        )
        scored = []
        for row in rows:
            vector = self.embedding_service.get_embedding(row["source_table"], row["source_id"])
            similarity = self.embedding_service.cosine_similarity(query_vector, vector)
            if similarity <= 0.0:
                continue
            candidate = self._hydrate_candidate(row["source_table"], row["source_id"])
            if candidate is None:
                continue
            candidate["embedding_similarity"] = similarity
            scored.append(candidate)
        scored.sort(key=lambda item: item.get("embedding_similarity", 0.0), reverse=True)
        results = []
        for rank, candidate in enumerate(scored[: int(top_k)], start=1):
            candidate["embedding_rank"] = rank
            candidate["score"] = candidate.get("score", 0.0) + self._rank_to_score(rank)
            results.append(candidate)
        return results

    def _bm25_search(self, query, top_k=8):
        """Sparse BM25 vector search using stored term-frequency vectors.

        Loads all BM25 entries from the database, builds IDF from the
        document collection, encodes the query, and scores every document.
        """
        entries = self.db.get_all_bm25_entries()
        if not entries:
            return []

        # Build IDF from all stored document vectors
        all_doc_vectors = []
        for entry in entries:
            tf = self.db.loads_json(entry["term_freq_json"], default={})
            if tf:
                all_doc_vectors.append(tf)

        if not all_doc_vectors:
            return []

        self.bm25.fit_idf(all_doc_vectors)
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
            candidate["score"] = candidate.get("score", 0.0) + self._rank_to_score(rank)
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
            scene = self.l2_repo.get_scene(source_id)
            if scene is None:
                return None
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
        return None

    @staticmethod
    def _rank_to_score(rank):
        if rank <= 0:
            return 0.0
        return 1.0 / rank
