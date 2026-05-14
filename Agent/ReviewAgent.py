from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.json_parser import JSONParser

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


class ReviewAgent(LargeLanguageModel):
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
                print("[-]JSON解析失败，重新生成 review:" + ans)
                continue

    def review_turn(self, turn_context, user_profile_context="", memory_markdown=""):
        print("[*]对本轮用户画像进行复盘:")
        document = self.prompt_assembler.build_review_document(
            turn_context=turn_context,
            user_profile_markdown=user_profile_context,
            memory_markdown=memory_markdown,
        )
        messages = self.prompt_renderer.render_document(document)
        return self._query_json(messages)
