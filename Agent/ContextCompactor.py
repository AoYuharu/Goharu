"""
上下文超限自动摘要 Agent

当对话历史 token 数接近模型上限时，将整个对话历史按 9 点框架
压缩为一条 user prompt，注入回会话中，避免硬截断丢失信息。
"""

from Agent.LargeLanguageModel import LargeLanguageModel
from Agent.TokenEstimator import TokenEstimator
from Prompting.PromptAssembler import PromptAssembler
from Prompting.PromptRenderer import PromptRenderer


class ContextCompactor(LargeLanguageModel):
    """上下文压缩器 — 超限时将历史摘要为一条 user 消息"""

    def __init__(self):
        super().__init__()
        self.prompt_assembler = PromptAssembler()
        self.prompt_renderer = PromptRenderer()
        self.token_estimator = TokenEstimator()

    def compact(self, messages, system_prompt=None) -> str:
        """
        将消息列表压缩为一条结构化的 user prompt。

        Args:
            messages: 完整的对话消息列表
            system_prompt: 可选的当前系统提示词文本，帮助理解上下文

        Returns:
            格式化的摘要文本，以 [上下文摘要] 开头
        """
        # 只取非 system 消息做摘要（system 消息本身已在每轮重建时注入）
        conversation = [m for m in messages if m.get("role") != "system"]
        if not conversation:
            return "[上下文摘要] 无可压缩的对话内容。"

        document = self.prompt_assembler.build_context_compact_document(
            conversation, system_prompt=system_prompt
        )
        prompt_messages = self.prompt_renderer.render_document(document)

        result = self.query(prompt_messages)
        return str(result or "").strip()

    def estimate_tokens(self, messages) -> int:
        """估算消息列表的 token 数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += self.token_estimator.estimate(str(block.get("text", "")))
            else:
                total += self.token_estimator.estimate(str(content))
        return total
