from Agent.LargeLanguageModel import LargeLanguageModel

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


class ReflectionAgent(LargeLanguageModel):
    def __init__(self):
        super().__init__()
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()

    def reflect(self, question, history, memory_markdown="", soul_markdown=""):
        document = self.prompt_assembler.build_reflection_document(
            question=question,
            history=history,
            memory_markdown=memory_markdown,
            soul_markdown=soul_markdown,
        )
        messages = self.prompt_renderer.render_document(document)
        return self.query(messages)
