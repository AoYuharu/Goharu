from Agent.SummarizerAgent import SummarizeAgent
from Agent.ReviewAgent import ReviewAgent
from Memory.LongTermMemory import LongTermMemory
from Memory.UserProfileMemory import UserProfileMemory
from Memory.WorkingMemory import WorkingMemory
from configurationLoader import config


class MemoryManager:
    def __init__(self):
        self.working = WorkingMemory()
        self.long_term = LongTermMemory()
        self.user_profile = UserProfileMemory()
        self.summarizer = SummarizeAgent()
        self.reviewer = ReviewAgent()
        self.review_enabled = bool(config.get("memory.user.review_enabled", True))
        self.merge_every_n_summaries = max(
            1,
            int(config.get("memory.topic.merge_every_n_summaries", 3)),
        )
        self.merge_min_count = max(
            2,
            int(config.get("memory.topic.merge_min_count", 4)),
        )

    def append(self, message):
        self.working.append(message)

    def clear_context(self):
        """清空当前会话上下文"""
        self.working.clear_today()

    def detectOverflow(self):
        expired_days = self.working.list_expired_days()
        if not expired_days:
            return []

        events = []
        summary_events = self._summarize_and_store(expired_days[0])
        events.extend(summary_events)
        if summary_events:
            events.extend(self._maybe_merge_topics())
        return events

    def _summarize_and_store(self, target_day=None):
        if target_day is None:
            expired_days = self.working.list_expired_days()
            if not expired_days:
                return []
            target_day = expired_days[0]

        day_payload = self.working.read_day(target_day)
        if not day_payload:
            return []

        history = day_payload.get("messages", [])
        if not history:
            self.working.delete_day(target_day)
            return [f"Deleted empty expired day: {target_day}"]

        summary = self.summarizer.summarize_day(history, self.get_longterm_context())
        self.long_term.update(summary, source_day=target_day)
        self.working.delete_day(target_day)
        return [
            f"Summarized expired day: {target_day}",
            f"Updated long-term memory from daily summary: {target_day}",
        ]

    def _maybe_merge_topics(self):
        index = self.long_term.read_index()
        topics = self.long_term.read_topics_metadata()
        metadata = index.get("metadata", {})
        summaries_since_merge = int(metadata.get("summaries_since_merge", 0) or 0)

        if summaries_since_merge < self.merge_every_n_summaries:
            return []
        if len(topics) < self.merge_min_count:
            return []

        topic_docs = []
        for topic in topics:
            topic_doc = self.long_term.read_topic(topic["slug"])
            if topic_doc is not None:
                topic_docs.append(topic_doc)

        merge_payload = self.summarizer.merge_topics(index, topic_docs)
        if merge_payload.get("merge_groups"):
            self.long_term.merge_topics(merge_payload)
            merged_groups = len(merge_payload.get("merge_groups") or [])
            return [f"Merged related topic groups: {merged_groups}"]

        self.long_term.rebuild_memory_index(
            last_topic_merge_at=self.long_term.current_timestamp(),
            summaries_since_merge=0,
        )
        return ["Checked topic merge threshold and rebuilt memory index"]

    def get_context(self):
        return self.working.get_messages()

    def get_context_size(self):
        return self.working.size()

    def get_turn_messages_since(self, start_index):
        return self.working.get_messages_since(start_index)

    def post_turn_review(self, turn_context):
        if not self.review_enabled:
            return []
        if not turn_context:
            return []
        if not self.user_profile.should_review():
            return ["Skipped user profile review (not yet at review interval)"]

        user_profile_markdown = self.get_user_profile_markdown()
        review_payload = self.reviewer.review_turn(
            turn_context=turn_context,
            user_profile_context=user_profile_markdown,
            memory_markdown=self.get_memory_markdown(),
        )
        source_turn = turn_context[0].get("timestamp") if isinstance(turn_context[0], dict) else ""
        apply_result = self.user_profile.apply(review_payload, source_turn=source_turn)

        events = [f"Reviewed turn and refreshed user profile: {apply_result['path']}"]
        if apply_result.get("updated"):
            events.append("Applied user profile updates from current turn")
        else:
            events.append("No user profile changes extracted from current turn")
        return events

    def get_prompt_context(self, base_system_prompt=None, extra_system_prompt=None, tool_definitions=None):
        from Prompting.PromptAssembler import PromptAssembler
        from Prompting.PromptRenderer import PromptRenderer

        assembler = PromptAssembler()
        renderer = PromptRenderer()
        document = assembler.build_actor_document(
            history=self.get_context(),
            soul_markdown=self.get_soul_markdown(),
            user_profile_markdown=self.get_user_profile_markdown(),
            memory_markdown=self.get_memory_markdown(),
            extra_system_prompt=extra_system_prompt,
            legacy_system_prompt=base_system_prompt,
            tool_definitions=tool_definitions,
        )
        return renderer.render_document(document)

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
