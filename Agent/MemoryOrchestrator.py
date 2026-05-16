"""
MemoryOrchestrator - 记忆编排器

将记忆的溢出检测、日总结、话题合并、用户画像复盘等编排逻辑从 MemoryManager 中抽离。
MemoryManager 保持纯 CRUD，不再依赖 Agent 包，从而打破 Agent ↔ Memory 的循环依赖。
"""

import logging

from Agent.SummarizerAgent import SummarizeAgent
from Agent.ReviewAgent import ReviewAgent
from configurationLoader import config

logger = logging.getLogger(__name__)


class MemoryOrchestrator:
    """编排记忆的周期性维护操作：溢出检测、日总结、话题合并、用户画像复盘。

    所有方法仅依赖 MemoryManager（CRUD 接口）和 SummarizeAgent/ReviewAgent，
    MemoryManager 本身不再 import Agent 包中的任何内容。
    """

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager
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
        self.sqlite_enabled = bool(config.get("memory.sqlite.enabled", True))
        self.db_pipeline_enabled = bool(config.get("memory.pipeline.db_enabled", True))
        self._ingestion_service = None
        self._scene_aggregation = None
        self._profile_abstraction = None

    @property
    def ingestion_service(self):
        if self._ingestion_service is None:
            from Memory.pipeline.MemoryIngestionService import MemoryIngestionService
            self._ingestion_service = MemoryIngestionService()
        return self._ingestion_service

    @property
    def scene_aggregation(self):
        if self._scene_aggregation is None:
            from Memory.pipeline.SceneAggregationService import SceneAggregationService
            self._scene_aggregation = SceneAggregationService()
        return self._scene_aggregation

    @property
    def profile_abstraction(self):
        if self._profile_abstraction is None:
            from Memory.pipeline.ProfileAbstractionService import ProfileAbstractionService
            self._profile_abstraction = ProfileAbstractionService()
        return self._profile_abstraction

    # ── Overflow / 日总结 ──────────────────────────────────────────

    def detectOverflow(self):
        mm = self.memory_manager
        expired_days = mm.working.list_expired_days()
        if not expired_days:
            return []

        logger.info(
            "Overflow detected: %d expired day(s): %s",
            len(expired_days), ", ".join(expired_days[:5]),
        )
        events = []
        summary_events = self._summarize_and_store(expired_days[0])
        events.extend(summary_events)
        if summary_events:
            merge_events = self._maybe_merge_topics()
            events.extend(merge_events)
            logger.info(
                "Memory maintenance complete: %d events: %s",
                len(events), "; ".join(events),
            )
        return events

    def _summarize_and_store(self, target_day=None):
        mm = self.memory_manager
        if target_day is None:
            expired_days = mm.working.list_expired_days()
            if not expired_days:
                return []
            target_day = expired_days[0]

        day_payload = mm.working.read_day(target_day)
        if not day_payload:
            return []

        history = day_payload.get("messages", [])
        if not history:
            mm.working.delete_day(target_day)
            return [f"Deleted empty expired day: {target_day}"]

        logger.info("Summarizing expired day %s (%d messages)...", target_day, len(history))
        summary = self.summarizer.summarize_day(history, mm.get_longterm_context())
        logger.info(
            "Day summary received: topics=%d atoms=%d",
            len(summary.get("topics", []) or []),
            len(summary.get("atoms", []) or []),
        )
        mm.long_term.update(summary, source_day=target_day)
        mm.working.delete_day(target_day)

        events = [
            f"Summarized expired day: {target_day}",
            f"Updated long-term memory from daily summary: {target_day}",
        ]

        # DB-backed pipeline: feed LLM summary atoms into L1 + L2
        if self.sqlite_enabled and self.db_pipeline_enabled:
            db_atoms = summary.get("atoms") or []
            if db_atoms:
                turn_id = None
                if mm.working.l0_repo:
                    turn = mm.working.l0_repo.get_or_create_turn(
                        session_id=mm.working.session_id,
                        day_key=target_day,
                    )
                    if turn:
                        turn_id = turn["id"]
                if turn_id:
                    self.ingestion_service.ingest_turn_atoms(turn_id, atom_payload=db_atoms)
                    self.scene_aggregation.consolidate()
                    events.append(f"DB pipeline: ingested L1 atoms and L2 scenes from day {target_day}")

        return events

    def _maybe_merge_topics(self):
        mm = self.memory_manager
        index = mm.long_term.read_index()
        topics = mm.long_term.read_topics_metadata()
        metadata = index.get("metadata", {})
        summaries_since_merge = int(metadata.get("summaries_since_merge", 0) or 0)

        if summaries_since_merge < self.merge_every_n_summaries:
            return []
        if len(topics) < self.merge_min_count:
            return []

        topic_docs = []
        for topic in topics:
            topic_doc = mm.long_term.read_topic(topic["slug"])
            if topic_doc is not None:
                topic_docs.append(topic_doc)

        logger.info("Checking topic merge: %d topics, %d summaries since last merge",
                     len(topics), summaries_since_merge)
        merge_payload = self.summarizer.merge_topics(index, topic_docs)
        if merge_payload.get("merge_groups"):
            mm.long_term.merge_topics(merge_payload)
            merged_groups = len(merge_payload.get("merge_groups") or [])
            logger.info("Merged %d topic group(s)", merged_groups)
            return [f"Merged related topic groups: {merged_groups}"]

        mm.long_term.rebuild_memory_index(
            last_topic_merge_at=mm.long_term.current_timestamp(),
            summaries_since_merge=0,
        )
        return ["Checked topic merge threshold and rebuilt memory index"]

    # ── 用户画像复盘 ──────────────────────────────────────────────

    def post_turn_review(self, turn_context):
        mm = self.memory_manager
        if not self.review_enabled:
            return []
        if not turn_context:
            return []
        if not mm.user_profile.should_review():
            return ["Skipped user profile review (not yet at review interval)"]

        user_profile_markdown = mm.get_user_profile_markdown()
        review_payload = self.reviewer.review_turn(
            turn_context=turn_context,
            user_profile_context=user_profile_markdown,
            memory_markdown=mm.get_memory_markdown(),
        )
        source_turn = turn_context[0].get("timestamp") if isinstance(turn_context[0], dict) else ""
        apply_result = mm.user_profile.apply(review_payload, source_turn=source_turn)

        events = [f"Reviewed turn and refreshed user profile: {apply_result['path']}"]
        if apply_result.get("updated"):
            events.append("Applied user profile updates from current turn")
        else:
            events.append("No user profile changes extracted from current turn")

        # DB-backed pipeline: also write profile updates into L3 canonical rows
        if self.sqlite_enabled and self.db_pipeline_enabled:
            profile_result = self.profile_abstraction.apply_review_payload(review_payload)
            if profile_result.get("updated"):
                events.append("DB pipeline: applied profile updates to L3 rows")

        return events
