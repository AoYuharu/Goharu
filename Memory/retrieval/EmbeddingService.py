import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path

from Memory.MemoryDB import MemoryDB
from configurationLoader import config


class EmbeddingService:
    """Embedding service with pluggable providers.

    Provider resolution (determined at init-time):
    1. ``provider == "openai_compatible"`` and valid api_key + base_url → remote HTTP
    2. Otherwise → local GGUF via llama-cpp-python
    3. Final fallback → :meth:`_hash_embedding`
    """

    def __init__(self, db=None):
        self.db = db or MemoryDB()
        self.model_name = config.get(
            "memory.retrieval.embedding_model_name",
            "Qwen3-Embedding-0.6B",
        )
        self.model_path = Path(
            config.get(
                "memory.retrieval.embedding_model_path",
                "./models/embedding_model/Qwen3-Embedding-0.6B",
            )
        )
        self.provider = config.get("memory.retrieval.embedding_provider", "local")
        self.base_url = config.get("memory.retrieval.embedding_base_url", "")
        self.api_key_env = config.get("memory.retrieval.embedding_api_key_env", "")
        self.dimensions = int(config.get("memory.retrieval.embedding_dimensions", 768))
        self.timeout = int(config.get("memory.retrieval.embedding_timeout", 30))

        self._model = None          # SentenceTransformer (legacy fallback)
        self._gguf_model = None     # llama-cpp Llama instance
        self._active_provider = self._resolve_provider()

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
    # Local GGUF embedding (llama-cpp-python)
    # ------------------------------------------------------------------

    def _load_gguf(self):
        if self._gguf_model is not None:
            return self._gguf_model

        from llama_cpp import Llama
        from huggingface_hub import hf_hub_download

        model_dir = Path("./models/embedding_model")
        model_dir.mkdir(parents=True, exist_ok=True)
        gguf_filename = "embeddinggemma-300m-qat-Q8_0.gguf"
        gguf_path = model_dir / gguf_filename

        if not gguf_path.exists():
            hf_hub_download(
                repo_id="ggml-org/embeddinggemma-300m-qat-q8_0-GGUF",
                filename=gguf_filename,
                local_dir=str(model_dir),
            )

        self._gguf_model = Llama(
            model_path=str(gguf_path),
            embedding=True,
            verbose=False,
        )
        return self._gguf_model

    def _encode_local_gguf(self, text):
        model = self._load_gguf()
        result = model.create_embedding(text)
        emb = result.get("embedding", [])
        # create_embedding returns either a flat list or list of lists
        if emb and isinstance(emb[0], list):
            emb = emb[0]
        return [float(v) for v in emb]

    # ------------------------------------------------------------------
    # Legacy SentenceTransformer (backward compat, last local fallback)
    # ------------------------------------------------------------------

    def _load_sentence_transformer(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            model_path = str(self.model_path)
            if Path(model_path).exists():
                self._model = SentenceTransformer(model_path)
            else:
                self._model = SentenceTransformer(self.model_name)
        except Exception:
            self._model = None
        return self._model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, text, prompt_name=None):
        """Encode a single text into a float vector."""
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
            except Exception:
                pass

        # 2) Local GGUF
        try:
            return self._encode_local_gguf(text)
        except Exception:
            pass

        # 3) Legacy SentenceTransformer (backward compat)
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
            except Exception:
                pass

        # 4) Hash fallback
        return self._hash_embedding(text)

    def encode_batch(self, texts):
        """Encode multiple texts into float vectors.

        Remote provider sends a single batch request.  Local provider
        encodes texts one by one.
        """
        clean = [str(t or "").strip() for t in (texts or [])]
        if not clean:
            return []

        # 1) Remote batch
        if self._active_provider == "openai_compatible":
            try:
                return self._encode_remote(clean)
            except Exception:
                pass

        # 2) Local GGUF (one-by-one, model has small context window)
        results = []
        for t in clean:
            t_short = self._truncate_for_local(t)
            if not t_short:
                results.append([])
                continue
            try:
                results.append(self._encode_local_gguf(t_short))
            except Exception:
                pass

        if len(results) == len(clean):
            return results

        # 3) Legacy SentenceTransformer batch
        model = self._load_sentence_transformer()
        if model is not None:
            try:
                remaining = [clean[i] for i in range(len(clean)) if i >= len(results)]
                batch_vectors = model.encode(remaining)
                if hasattr(batch_vectors, "tolist"):
                    batch_vectors = batch_vectors.tolist()
                results.extend([float(item) for item in vec] for vec in batch_vectors)
            except Exception:
                pass

        # 4) Hash fallback for any remaining
        while len(results) < len(clean):
            results.append(self._hash_embedding(clean[len(results)]))
        return results

    def store_embedding(self, source_table, source_id, text, prompt_name=None):
        vector = self.encode(text, prompt_name=prompt_name)
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

    @staticmethod
    def _hash_embedding(text, dim=64):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        numbers = list(digest)
        vector = []
        for index in range(dim):
            value = numbers[index % len(numbers)] / 255.0
            vector.append((value * 2.0) - 1.0)
        norm = math.sqrt(sum(item * item for item in vector)) or 1.0
        return [item / norm for item in vector]
