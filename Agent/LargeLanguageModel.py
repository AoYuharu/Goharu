from Agent.LLMCore import LLMCore


class LargeLanguageModel:
    def __init__(self):
        self.core = LLMCore()

    def getTokenSize(self, text):
        return self.core.get_token_size(text)

    def query(self, messages, on_retry=None, **kwargs):
        return self.core.generate(messages, on_retry=on_retry, **kwargs)

    def get_last_thinking(self):
        """获取最近一次 API 调用返回的 thinking 内容（若无则返回 None）"""
        return getattr(self.core, "_last_thinking", None)

    def get_last_usage(self):
        """获取最近一次 API 调用返回的 usage 信息（若无则返回 None）"""
        return getattr(self.core, "_last_usage", None)

    def prepare_messages_for_provider(self, messages):
        """把渲染后的 messages 转成 provider 发送形态 (system_blocks, remote_messages)。

        供宿主做发送前 token 估算（替代穿透 core.provider._prepare_messages）。
        无 provider（如 llm 注入路径 self.core=None）时返回 None，调用方回退粗估。
        """
        provider = getattr(getattr(self, "core", None), "provider", None)
        prepare = getattr(provider, "_prepare_messages", None)
        if callable(prepare):
            return prepare(messages)
        return None
