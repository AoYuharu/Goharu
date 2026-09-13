import logging

import config

logger = logging.getLogger(__name__)

_FTS_STOPWORDS = frozenset(
    "的 了 是 在 有 和 与 及 或 也 就 都 还 很 更 最 被 把 对 从 到 于 之 为 而 且 但 如果 呢 吗 吧 啊 哦 呀 这 那 我 你 他 她 它 我们 你们 他们 什么 怎么 为什么 哪些 哪 谁 一个 一些 有点 一下 这个 那个 这些 那些 可以 应该 需要 请 帮 让 上 下 中 内 外".split()
)


class Retriever:
    """FTS5-BM25 + embedding cosine → RRF fusion over capability atoms."""

    def __init__(self, db, embedder):
        self.db = db
        self.embedder = embedder

    def search(self, query, top_k=None):
        return self._search(query, top_k, agent_id=None)

    def search_in_agent(self, agent_id, query, top_k=None):
        return self._search(query, top_k, agent_id=agent_id)

    def _search(self, query, top_k, agent_id=None):
        query = str(query or "").strip()
        final_k = int(top_k or config.FINAL_TOP_K)
        if not query:
            return []

        candidate_lists = []
        candidate_lists.append(self._fts_search(query, max(final_k, config.FTS_TOP_K), agent_id))
        candidate_lists.append(self._embedding_search(query, max(final_k, config.EMB_TOP_K), agent_id))

        fused = self._rrf_fuse(candidate_lists, config.RRF_K)
        return fused[:final_k]

    def _fts_search(self, query, top_k, agent_id=None):
        if not self.db.fts_enabled:
            return []
        tokens = [
            t for t in self.db.tokenize_for_fts(query).split()
            if t not in _FTS_STOPWORDS and len(t.strip()) > 0
        ]
        if not tokens:
            return []
        tokenized_query = " ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        try:
            if agent_id is not None:
                rows = self.db.query_all(
                    """
                    SELECT agent_id, capability_id
                    FROM agent_capability_fts
                    WHERE agent_capability_fts MATCH ? AND agent_id = ?
                    ORDER BY bm25(agent_capability_fts)
                    LIMIT ?
                    """,
                    (tokenized_query, agent_id, int(top_k)),
                )
            else:
                rows = self.db.query_all(
                    """
                    SELECT agent_id, capability_id
                    FROM agent_capability_fts
                    WHERE agent_capability_fts MATCH ?
                    ORDER BY bm25(agent_capability_fts)
                    LIMIT ?
                    """,
                    (tokenized_query, int(top_k)),
                )
        except Exception as e:
            logger.warning("Retriever: FTS query failed (%s), skipping FTS leg", e)
            return []

        candidates = []
        for rank, row in enumerate(rows, start=1):
            hydrated = self._hydrate(row["agent_id"], row["capability_id"])
            if hydrated is None:
                continue
            hydrated["fts_rank"] = rank
            candidates.append(hydrated)
        return candidates

    def _embedding_search(self, query, top_k, agent_id=None):
        query_vector = self.embedder.encode_query(query)
        if not query_vector:
            return []
        entries = self.db.list_embeddings(self.embedder.model_name, agent_id=agent_id)
        if not entries:
            return []

        scored = []
        for entry_agent_id, capability_id, vector in entries:
            similarity = self.embedder.cosine(query_vector, vector)
            if similarity <= 0.0:
                continue
            scored.append((similarity, entry_agent_id, capability_id))
        scored.sort(key=lambda item: item[0], reverse=True)

        candidates = []
        for rank, (similarity, entry_agent_id, capability_id) in enumerate(scored[: int(top_k)], start=1):
            hydrated = self._hydrate(entry_agent_id, capability_id)
            if hydrated is None:
                continue
            hydrated["embedding_similarity"] = round(similarity, 4)
            hydrated["embedding_rank"] = rank
            candidates.append(hydrated)
        return candidates

    def _hydrate(self, agent_id, capability_id):
        capability = self.db.get_capability(agent_id, capability_id)
        if capability is None:
            return None
        agent = self.db.get_agent(agent_id)
        return {
            "agent_id": agent_id,
            "agent_name": agent["name"] if agent else "",
            "agent_summary": agent.get("summary") if agent else None,
            "capability_id": capability_id,
            "capability_name": capability["name"],
            "description": capability["description"],
            "version": capability["version"],
        }

    def _rrf_fuse(self, candidate_lists, constant=60):
        merged = {}
        for candidates in candidate_lists:
            for rank, candidate in enumerate(candidates, start=1):
                key = (candidate["agent_id"], candidate["capability_id"])
                entry = merged.setdefault(key, dict(candidate))
                entry["score"] = float(entry.get("score") or 0.0) + 1.0 / (constant + rank)
        results = list(merged.values())
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results
