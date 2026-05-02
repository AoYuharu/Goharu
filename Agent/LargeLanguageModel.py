from Agent.LLMCore import LLMCore


class LargeLanguageModel:
    def __init__(self):
        self.core = LLMCore()

    def getTokenSize(self, text):
        return self.core.get_token_size(text)

    def query(self, messages):
        return self.core.generate(messages)
