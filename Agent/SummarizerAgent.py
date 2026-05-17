import logging

from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.json_parser import JSONParser

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer

_logger = logging.getLogger(__name__)


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
                _logger.warning(
                    "JSON解析失败，重新生成摘要: %s", str(ans)[:200]
                )
                continue

    def summarize_day(self, history, old_memory=None, existing_atoms=None):
        _logger.info(
            "对过期 daily 记忆进行摘要 (history_len=%d)",
            len(history) if isinstance(history, list) else 0,
        )
        document = self.prompt_assembler.build_day_summary_document(
            history, old_memory, existing_atoms=existing_atoms,
        )
        messages = self.prompt_renderer.render_document(document)
        return self._query_json(messages)
