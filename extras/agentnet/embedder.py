import math
from pathlib import Path

import config


class Embedder:
    """Lazy-loaded Qwen3-Embedding-0.6B wrapper.

    Model is loaded on first use (so the service can boot without the model
    present). If the model is unavailable, ``available`` is False and the vector
    leg of retrieval is skipped — FTS5 still works.
    """

    def __init__(self, model_dir=None, model_name=None):
        self.model_dir = Path(model_dir or config.MODEL_DIR)
        self.model_name = model_name or config.MODEL_NAME
        self._model = None
        self._failed = False

    def _load(self):
        if self._model is not None:
            return self._model
        if self._failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            if self.model_dir.exists():
                self._model = SentenceTransformer(str(self.model_dir))
            else:
                self._model = SentenceTransformer(self.model_name)
        except Exception:
            self._failed = True
            self._model = None
        return self._model

    @property
    def available(self):
        return self._load() is not None

    def encode(self, text, prompt_name=None):
        model = self._load()
        if model is None:
            return []
        text = str(text or "").strip()
        if not text:
            return []
        try:
            kwargs = {}
            if prompt_name:
                kwargs["prompt_name"] = prompt_name
            vector = model.encode(text[:512], **kwargs)
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            return [float(x) for x in vector]
        except Exception:
            return []

    def encode_description(self, text):
        return self.encode(text)

    def encode_query(self, text):
        return self.encode(text, prompt_name="query")

    @staticmethod
    def cosine(a, b):
        if not a or not b or len(a) != len(b):
            return 0.0
        n = sum(float(a[i]) * float(b[i]) for i in range(len(a)))
        la = math.sqrt(sum(float(x) ** 2 for x in a))
        lb = math.sqrt(sum(float(x) ** 2 for x in b))
        if la == 0.0 or lb == 0.0:
            return 0.0
        return n / (la * lb)
