from Agent.LargeLanguageModel import LargeLanguageModel
import json

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


class SummarizeAgent(LargeLanguageModel):
    def __init__(self):
        super().__init__()
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()

    @staticmethod
    def _coerce_json(text):
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return cleaned

    def _query_json(self, messages):
        while True:
            ans = self.query(messages)

            try:
                return json.loads(ans)
            except Exception:
                try:
                    return json.loads(self._coerce_json(ans))
                except Exception:
                    print("[-]JSON解析失败，重新生成摘要:" + ans)
                    continue

    def summarize_day(self, history, old_memory):
        print("[*]对过期 daily 记忆进行摘要:" + str(history))
        document = self.prompt_assembler.build_day_summary_document(history, old_memory)
        messages = self.prompt_renderer.render_document(document)
        return self._query_json(messages)

    def merge_topics(self, memory_index, topic_docs):
        print("[*]正在分析 topic 是否需要合并...")
        document = self.prompt_assembler.build_topic_merge_document(memory_index, topic_docs)
        messages = self.prompt_renderer.render_document(document)
        return self._query_json(messages)

    def summarize(self, history, old_memory):
        return self.summarize_day(history, old_memory)
