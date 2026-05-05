from Agent.LargeLanguageModel import LargeLanguageModel

from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer
from Prompting.PromptSection import PromptSection
from Prompting.PromptDocument import PromptDocument


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

    def review_answer(self, question: str, answer: str, file_state_context: dict, memory_markdown="", soul_markdown=""):
        """
        审核 Actor 的答案

        Args:
            question: 用户的原始问题
            answer: Actor 给出的答案
            file_state_context: FileStateManager 提供的上下文（文件内容 + 工具调用记录）
            memory_markdown: 长期记忆
            soul_markdown: 角色定义

        Returns:
            审核结果字符串，包含"答案可以接受"或"答案需要改进"
        """
        document = PromptDocument()

        # 添加角色定义
        if soul_markdown:
            document.add_system(PromptSection(
                kind="system",
                title="soul",
                content=soul_markdown,
                metadata={"section_name": "soul"},
            ))

        # 添加长期记忆
        if memory_markdown:
            document.add_system(PromptSection(
                kind="system",
                title="memory",
                content=f"以下是长期记忆 MEMORY.md：\n\n{memory_markdown}",
                metadata={"section_name": "memory"},
            ))

        # 添加审核任务说明
        review_instruction = """你是一个答案审核专家。你的任务是评估 Actor 给出的答案是否充分、准确地回答了用户的问题。

## 审核标准

1. **完整性**：答案是否完整回答了用户的所有问题点
2. **准确性**：答案是否基于实际的工具调用结果，而不是编造
3. **清晰性**：答案是否清晰易懂，逻辑连贯
4. **相关性**：答案是否紧扣问题，没有偏题

## 审核输出

你必须在回复中明确包含以下短语之一：
- **"答案可以接受"** - 如果答案质量良好，可以展示给用户
- **"答案需要改进"** - 如果答案存在明显问题，需要 Actor 重新回答

如果答案需要改进，请具体说明问题所在和改进建议。"""

        document.add_system(PromptSection(
            kind="system",
            title="review_instruction",
            content=review_instruction,
            metadata={"section_name": "review_instruction"},
        ))

        # 添加用户问题
        document.add_conversation(PromptSection(
            kind="user",
            title="original_question",
            content=f"## 用户的原始问题\n\n{question}",
            metadata={"section_name": "original_question"},
        ))

        # 添加工具调用记录
        tool_calls_summary = file_state_context.get("tool_calls_summary", "（无工具调用）")
        document.add_conversation(PromptSection(
            kind="user",
            title="tool_calls",
            content=f"## 工具调用记录\n\n{tool_calls_summary}",
            metadata={"section_name": "tool_calls"},
        ))

        # 添加文件内容
        files_summary = file_state_context.get("files_summary", "（未读取文件）")
        document.add_conversation(PromptSection(
            kind="user",
            title="files_content",
            content=f"## 读取的文件内容\n\n{files_summary}",
            metadata={"section_name": "files_content"},
        ))

        # 添加 Actor 的答案
        document.add_conversation(PromptSection(
            kind="user",
            title="actor_answer",
            content=f"## Actor 给出的答案\n\n{answer}",
            metadata={"section_name": "actor_answer"},
        ))

        # 添加审核任务
        document.add_conversation(PromptSection(
            kind="user",
            title="review_task",
            content='请基于以上信息，审核 Actor 的答案是否可以接受。记住必须明确输出"答案可以接受"或"答案需要改进"。',
            metadata={"section_name": "review_task"},
        ))

        messages = self.prompt_renderer.render_document(document)
        return self.query(messages)
