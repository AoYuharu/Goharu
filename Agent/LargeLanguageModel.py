from Agent.LLMCore import LLMCore


class LargeLanguageModel:
    def __init__(self):
        self.core = LLMCore()

    def getTokenSize(self, text):
        return self.core.get_token_size(text)

    def query(self, messages, **kwargs):
        return self.core.generate(messages, **kwargs)

    def get_last_thinking(self):
        """获取最近一次 API 调用返回的 thinking 内容（若无则返回 None）"""
        return getattr(self.core, "_last_thinking", None)

    def get_last_usage(self):
        """获取最近一次 API 调用返回的 usage 信息（若无则返回 None）"""
        return getattr(self.core, "_last_usage", None)
