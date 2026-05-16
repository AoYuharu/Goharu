from configurationLoader import config
from Memory.LongTermMemory import LongTermMemory
from Memory.UserProfileMemory import UserProfileMemory
from Memory.WorkingMemory import WorkingMemory
from Memory.retrieval.HybridRetriever import HybridRetriever
from Memory.repositories.L3Repository import L3Repository


class MemoryManager:
    """记忆管理器 — 统一 facade。

    编排逻辑（溢出检测、日总结、话题合并、用户画像复盘）已移至
    Agent.MemoryOrchestrator，MemoryManager 不再 import Agent 包中的任何内容。
    """

    def __init__(self):
        self.working = WorkingMemory()
        self.long_term = LongTermMemory()
        self.user_profile = UserProfileMemory()
        self.retrieval_enabled = bool(config.get("memory.retrieval.enabled", False))
        self.use_legacy_memory_markdown = bool(config.get("memory.prompt.use_legacy_memory_markdown", True))
        self._hybrid_retriever = None
        self._l3_repo = None

    @property
    def hybrid_retriever(self):
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever()
        return self._hybrid_retriever

    @property
    def l3_repo(self):
        if self._l3_repo is None:
            self._l3_repo = L3Repository()
        return self._l3_repo

    def append(self, message):
        self.working.append(message)

    def clear_context(self):
        """清空当前会话上下文"""
        self.working.clear_today()

    def get_context(self):
        return self.working.get_messages()

    def get_context_size(self):
        return self.working.size()

    def get_turn_messages_since(self, start_index):
        return self.working.get_messages_since(start_index)

    def get_memory_markdown(self):
        return self.long_term.read_memory_markdown()

    def get_soul_markdown(self):
        return self.long_term.read_soul_markdown()

    def get_user_profile_markdown(self):
        return self.user_profile.read_markdown()

    def get_longterm_context(self):
        context = self.long_term.read()
        context["user_profile_markdown"] = self.get_user_profile_markdown()
        return context

    def retrieve_prompt_memory(self):
        """Query-time hybrid retrieval: L1 atoms + L2 scenes + L3 profile summary.

        Returns:
            dict with keys: profile_summary, memories, scenes, query_text
        """
        messages = self.get_context()
        retriever = self.hybrid_retriever
        query = retriever.build_query_from_messages(messages)
        pack = retriever.retrieve(query)
        pack["query_text"] = query
        return pack

    def get_profile_compact_summary(self):
        """Compact L3 user profile summary for prompt injection."""
        return self.l3_repo.build_compact_summary()
