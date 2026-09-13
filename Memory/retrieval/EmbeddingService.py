import json
import logging
import math
import os
import threading
from datetime import datetime
from pathlib import Path

from Memory.MemoryDB import MemoryDB
from Memory.memory_config import resolve_config

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Embedding service with pluggable providers.

    降级链（init 时确定远程，local 在首次使用时探测）：
    1. ``provider == "openai_compatible"`` 且 api_key + base_url 有效 → 远程 HTTP
    2. 本地 sentence_transformers 可用且模型加载成功 → 本地模型
    3. 都不可用 → 向量腿整体禁用（encode 返回 []，检索退化为 FTS5+BM25），
       不再写入 hash 假向量污染索引
    """

    # 类级缓存：所有实例共享同一份本地模型，避免重复加载 1.19GB 权重。
    # 也避免多实例并发加载（并发加载正是此前 CUDA 原生崩溃的诱因之一）。
    _model = None
    _local_failed = False
    _load_lock = threading.Lock()

    def __init__(self, db=None, cfg=None):
        cfg = resolve_config(cfg)
        self.db = db or MemoryDB(cfg=cfg)
        self.model_name = cfg.get(
            "memory.retrieval.embedding_model_name",
            "Qwen3-Embedding-0.6B",
        )
        self.model_path = Path(
            cfg.get(
                "memory.retrieval.embedding_model_path",
                "./models/embedding_model/Qwen3-Embedding-0.6B",
            )
        )
        self.provider = cfg.get("memory.retrieval.embedding_provider", "local")
        self.base_url = cfg.get("memory.retrieval.embedding_base_url", "")
        self.api_key_env = cfg.get("memory.retrieval.embedding_api_key_env", "")
        self.dimensions = int(cfg.get("memory.retrieval.embedding_dimensions", 768))
        self.timeout = int(cfg.get("memory.retrieval.embedding_timeout", 30))

        self._active_provider = self._resolve_provider()
        logger.info("EmbeddingService: provider=%s (model=%s)", self._active_provider, self.model_name)

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat()

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    def _resolve_provider(self):
        if self.provider == "openai_compatible":
            api_key = self._get_api_key()
            if self.base_url and api_key:
                return "openai_compatible"
        return "local"

    def _get_api_key(self):
        if not self.api_key_env:
            return None
        return os.getenv(self.api_key_env) or ""

    # ------------------------------------------------------------------
    # Remote HTTP embedding (OpenAI-compatible)
    # ------------------------------------------------------------------

    def _encode_remote(self, texts):
        """Send one or more texts to a remote /embeddings endpoint."""
        import requests

        api_key = self._get_api_key()
        url = f"{self.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": texts,
            "model": self.model_name,
        }
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions

        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [[float(v) for v in item["embedding"]] for item in items]

    # ------------------------------------------------------------------
    # Local SentenceTransformer
    # ------------------------------------------------------------------

    def _load_sentence_transformer(self):
        cls = type(self)
        if cls._model is not None:
            return cls._model
        if cls._local_failed:
            return None
        with cls._load_lock:
            if cls._model is not None:
                return cls._model
            if cls._local_failed:
                return None
            try:
                from sentence_transformers import SentenceTransformer

                model_path = str(self.model_path)
                # 强制 CPU：CUDA 路径在 torch2.8/transformers4.57 下加载该模型会触发原生访问冲突（0xC0000005）
                if Path(model_path).exists():
                    cls._model = SentenceTransformer(model_path, device="cpu")
                else:
                    cls._model = SentenceTransformer(self.model_name, device="cpu")
            except Exception as e:
                cls._local_failed = True
                cls._model = None
                logger.warning(
                    "EmbeddingService: 本地模型不可用 (%s) — 向量检索腿禁用，"
                    "检索退化为 FTS5+BM25；如需向量检索请配置远程 embedding 或安装模型",
                    e,
                )
        return cls._model

    @property
    def available(self):
        """是否存在真实可用的向量后端（远程或本地）。"""
        if self._active_provider == "openai_compatible":
            return True
        return self._load_sentence_transformer() is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, text, prompt_name=None):
        """Encode a single text into a float vector.

        无可用向量后端时返回 []（调用方据此跳过向量腿）。"""
        text = str(text or "").strip()
        if not text:
            return []
        text = self._truncate_for_local(text)

        # 1) Remote
        if self._active_provider == "openai_compatible":
            try:
                vectors = self._encode_remote([text])
                if vectors:
                    return vectors[0]
                logger.warning("EmbeddingService: remote returned no vectors, falling back to local")
            except Exception as e:
                logger.warning("EmbeddingService: remote embedding failed (%s), falling back to local", e)

        # 2) Local SentenceTransformer
        model = self._load_sentence_transformer()
        if model is not None:
            try:
                kwargs = {}
                if prompt_name:
                    kwargs["prompt_name"] = prompt_name
                vector = model.encode(text, **kwargs)
                if hasattr(vector, "tolist"):
                    vector = vector.tolist()
                return [float(item) for item in vector]
            except Exception as e:
                logger.warning("EmbeddingService: SentenceTransformer encode failed (%s)", e)

        # 3) 无可用后端：不写假向量，由调用方跳过向量腿
        return []

    def encode_batch(self, texts):
        """Encode multiple texts into float vectors.

        Remote provider sends a single batch request.  Local provider
        encodes texts one by one. 无后端的条目返回 [] 占位。"""
        clean = [str(t or "").strip() for t in (texts or [])]
        if not clean:
            return []

        # 1) Remote batch
        if self._active_provider == "openai_compatible":
            try:
                return self._encode_remote(clean)
            except Exception as e:
                logger.warning("EmbeddingService: remote batch failed (%s), falling back to local", e)

        # 2) Local SentenceTransformer batch
        model = self._load_sentence_transformer()
        if model is not None:
            try:
                batch_vectors = model.encode(clean)
                if hasattr(batch_vectors, "tolist"):
                    batch_vectors = batch_vectors.tolist()
                return [[float(item) for item in vec] for vec in batch_vectors]
            except Exception as e:
                logger.warning("EmbeddingService: SentenceTransformer batch encode failed (%s)", e)

        return [[] for _ in clean]

    def store_embedding(self, source_table, source_id, text, prompt_name=None):
        vector = self.encode(text, prompt_name=prompt_name)
        if not vector:
            return []  # 无后端：不落库，避免假向量污染索引
        payload = json.dumps(vector).encode("utf-8")
        self.db.execute(
            "DELETE FROM embeddings WHERE source_table = ? AND source_id = ? AND model_name = ?",
            (str(source_table), int(source_id), self.model_name),
        )
        self.db.execute(
            """
            INSERT INTO embeddings(source_table, source_id, model_name, vector_blob, dim, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(source_table),
                int(source_id),
                self.model_name,
                payload,
                len(vector),
                self.now(),
            ),
        )
        return vector

    def get_embedding(self, source_table, source_id):
        row = self.db.query_one(
            "SELECT vector_blob FROM embeddings WHERE source_table = ? AND source_id = ? AND model_name = ?",
            (str(source_table), int(source_id), self.model_name),
        )
        if row is None:
            return None
        try:
            return json.loads(bytes(row["vector_blob"]).decode("utf-8"))
        except Exception:
            return None

    def cosine_similarity(self, left, right):
        if not left or not right:
            return 0.0
        if len(left) != len(right):
            # 维度不一致说明混入了旧版 hash 兜底向量（64 维，已停止写入），
            # 截断比较会产生无意义分数，直接判为不相关
            return 0.0
        size = min(len(left), len(right))
        if size == 0:
            return 0.0
        numerator = sum(float(left[i]) * float(right[i]) for i in range(size))
        left_norm = math.sqrt(sum(float(left[i]) ** 2 for i in range(size)))
        right_norm = math.sqrt(sum(float(right[i]) ** 2 for i in range(size)))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _truncate_for_local(text, max_chars=512):
        """Truncate input to fit within the embedding model's context window."""
        return str(text)[:max_chars]
