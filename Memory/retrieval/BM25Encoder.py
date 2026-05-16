"""BM25 sparse vector encoder using jieba for Chinese word segmentation.

Provides TF-based document encoding for storage and IDF-weighted query
encoding for search, with sparse dot-product scoring.
"""

import math


class BM25Encoder:
    """Encodes text as sparse BM25-weighted vectors using jieba tokenization.

    encode_document(text) -> dict[str, float]   # TF vector (for storage)
    encode_query(text)    -> dict[str, float]   # IDF-weighted vector (for search)
    score(q_vec, d_vec)   -> float              # sparse inner product
    """

    def __init__(self):
        self._jieba = None
        self._term_idf = {}    # term -> IDF value
        self._doc_count = 0

    # ------------------------------------------------------------------
    # jieba lazy-init
    # ------------------------------------------------------------------

    def _get_jieba(self):
        if self._jieba is None:
            try:
                import jieba
                self._jieba = jieba
            except ImportError:
                self._jieba = False
        return self._jieba if self._jieba else None

    def _tokenize(self, text):
        """Segment text into tokens.  Uses jieba when available; otherwise
        falls back to whitespace splitting."""
        jieba_mod = self._get_jieba()
        if jieba_mod:
            try:
                return list(jieba_mod.cut_for_search(str(text or "")))
            except Exception:
                pass
        return str(text or "").split()

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def encode_document(self, text):
        """Return a TF (term-frequency) sparse vector for a document.

        The returned dict maps token -> normalised term frequency
        (count / total_tokens).  This vector is suitable for storage
        in the ``bm25_sparse`` table.
        """
        tokens = self._tokenize(text)
        if not tokens:
            return {}
        tf = {}
        for token in tokens:
            tf[token] = tf.get(token, 0.0) + 1.0
        doc_len = len(tokens)
        return {k: v / doc_len for k, v in tf.items()}

    def encode_query(self, text):
        """Return an IDF-weighted sparse vector for a query.

        Must call :meth:`fit_idf` first to populate the term-IDF table.
        Falls back to raw term frequency if IDF has not been computed.
        """
        tokens = self._tokenize(text)
        if not tokens:
            return {}
        qtf = {}
        for token in tokens:
            qtf[token] = qtf.get(token, 0.0) + 1.0
        q_len = len(tokens)
        result = {}
        for term, freq in qtf.items():
            idf = self._term_idf.get(term, 1.0)
            result[term] = idf * (freq / q_len)
        return result

    # ------------------------------------------------------------------
    # IDF building
    # ------------------------------------------------------------------

    def fit_idf(self, doc_vectors):
        """Build the internal IDF table from a list of document sparse vectors.

        Parameters
        ----------
        doc_vectors : list[dict[str, float]]
            Sparse TF vectors from previously stored documents.
        """
        self._doc_count = len(doc_vectors)
        if self._doc_count == 0:
            self._term_idf = {}
            return

        df = {}  # document frequency per term
        for vec in doc_vectors:
            for term in vec:
                df[term] = df.get(term, 0) + 1

        N = float(self._doc_count)
        self._term_idf = {}
        for term, n in df.items():
            # Smooth IDF (BM25-style)
            self._term_idf[term] = math.log((N - n + 0.5) / (n + 0.5) + 1.0)

    def invalidate_idf(self):
        """Clear the cached IDF table so it will be rebuilt next time."""
        self._term_idf = {}
        self._doc_count = 0

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def score(query_vec, doc_vec):
        """Sparse inner-product score between a query vector and doc vector."""
        if not query_vec or not doc_vec:
            return 0.0
        total = 0.0
        # Iterate over the shorter vector for efficiency
        if len(query_vec) <= len(doc_vec):
            for term, q_weight in query_vec.items():
                total += q_weight * doc_vec.get(term, 0.0)
        else:
            for term, d_weight in doc_vec.items():
                total += query_vec.get(term, 0.0) * d_weight
        return total
