"""
中央化的 Token 估算器

所有 token 估算和显示格式化都必须通过此类，确保项目内 token 计算行为一致。

- 估算：优先使用注入的 HuggingFace tokenizer，否则回退到字符估算
- 显示：count < 1000 用数字，>= 1000 用 X.Xk 格式
"""


class TokenEstimator:
    """单例 Token 估算器，统一入口"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self.__class__._initialized:
            return
        self._tokenizer = None
        self.__class__._initialized = True

    def set_tokenizer(self, tokenizer):
        """注入 HuggingFace tokenizer 实例"""
        self._tokenizer = tokenizer

    def estimate(self, text) -> int:
        """估算文本的 token 数量"""
        if self._tokenizer is not None:
            try:
                tokens = self._tokenizer(str(text))
                return len(tokens["input_ids"])
            except Exception:
                pass
        return self._estimate_fallback(text)

    @staticmethod
    def _estimate_fallback(text) -> int:
        """字符级回退估算：适用于中英文混合文本（~3 字符/token）"""
        return len(str(text)) // 3 + 1

    @staticmethod
    def format(count: int) -> str:
        """格式化 token 数量用于显示

        - 不足 1k: 直接显示数字（如 523）
        - 1k 以上: 以 k 为单位（如 1.5k, 12.3k）
        """
        if count < 1000:
            return str(count)
        return f"{count / 1000:.1f}k"
