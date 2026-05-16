from pathlib import Path

from configurationLoader import config


class RerankerService:
    def __init__(self):
        self.enabled = bool(config.get("memory.retrieval.use_reranker", False))
        self.model_path = Path(
            config.get(
                "memory.retrieval.reranker_model_path",
                "./models/rerank_model/bge-reranker-v2-m3",
            )
        )
        self._tokenizer = None
        self._model = None

    def rerank(self, query, candidates, top_k=None):
        if not candidates:
            return []
        scored = []
        for candidate in candidates:
            candidate = dict(candidate)
            base_score = float(candidate.get("score") or 0.0)
            if self.enabled:
                rerank_score = self._score_pair(query, candidate.get("text") or candidate.get("summary") or "")
            else:
                rerank_score = self._fallback_score(query, candidate.get("text") or candidate.get("summary") or "")
            candidate["rerank_score"] = rerank_score
            candidate["score"] = base_score + rerank_score
            scored.append(candidate)
        scored.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        if top_k is not None:
            return scored[: int(top_k)]
        return scored

    def _score_pair(self, query, text):
        query = str(query or "").strip()
        text = str(text or "").strip()
        if not query or not text:
            return 0.0
        try:
            model, tokenizer = self._load_model()
            if model is None or tokenizer is None:
                return self._fallback_score(query, text)
            import torch

            features = tokenizer(
                [query],
                [text],
                padding=True,
                truncation=True,
                return_tensors="pt",
                max_length=512,
            )
            with torch.no_grad():
                logits = model(**features).logits
            score = float(logits.squeeze().item())
            return score
        except Exception:
            return self._fallback_score(query, text)

    def _load_model(self):
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            model_name_or_path = str(self.model_path) if self.model_path.exists() else str(self.model_path.name)
            self._tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name_or_path)
        except Exception:
            self._tokenizer = None
            self._model = None
        return self._model, self._tokenizer

    @staticmethod
    def _fallback_score(query, text):
        query_tokens = {token for token in str(query or "").lower().split() if token}
        text_tokens = {token for token in str(text or "").lower().split() if token}
        if not query_tokens or not text_tokens:
            return 0.0
        overlap = len(query_tokens & text_tokens)
        return float(overlap) / max(len(query_tokens), 1)
