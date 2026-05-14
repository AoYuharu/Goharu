from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.json_parser import JSONParser

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


class SummarizeAgent(LargeLanguageModel):
    def __init__(self):
        super().__init__()
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()

    def _query_json(self, messages):
        while True:
            ans = self.query(messages)
            try:
                return JSONParser.parse_with_retry(ans)
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
