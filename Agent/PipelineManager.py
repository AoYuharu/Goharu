"""
PipelineManager — Memory pipeline trigger orchestration.

替换 detectOverflow() + post_turn_review() 的旧机制。
实现 TencentDB 模式的轮次计数、warmup 加速、空闲超时、L2延迟、L3阈值。

Trigger model:
  L1: 每N轮（默认5轮），warmup: 1→2→4→8→5，空闲超时10分钟
  L2: L1完成后90秒延迟 + 周期性轮询最长1小时
  L3: 每50条新L1原子 / 首次提取 / LLM显式请求
"""

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from configurationLoader import config

logger = logging.getLogger(__name__)


class PipelineManager:
    """Memory pipeline trigger orchestrator (TencentDB-aligned)."""

    def __init__(self, memory_manager=None):
        self.memory_manager = memory_manager
        self.enabled = bool(config.get("memory.pipeline_manager.enabled", True))

        # L1 config
        self.l1_turn_interval = max(1, int(config.get("memory.pipeline_manager.l1.turn_interval", 5)))
        self.l1_warmup_enabled = bool(config.get("memory.pipeline_manager.l1.warmup_enabled", True))
        self.l1_idle_timeout = float(config.get("memory.pipeline_manager.l1.idle_timeout_seconds", 600))

        # L2 config
        self.l2_delay_after_l1 = float(config.get("memory.pipeline_manager.l2.delay_after_l1_seconds", 90))
        self.l2_poll_max = float(config.get("memory.pipeline_manager.l2.poll_max_seconds", 3600))

        # L3 config
        self.l3_new_atom_threshold = max(1, int(config.get("memory.pipeline_manager.l3.new_atom_threshold", 50)))

        # State
        self._lock = threading.Lock()
        self._turn_count = 0
        self._warmup_threshold = 1  # starts at 1, doubles until reaches l1_turn_interval
        self._last_activity = time.time()
        self._new_l1_atoms_since_l3 = 0
        self._l2_timer = None
        self._idle_timer = None
        self._l1_in_progress = False
        self._l2_in_progress = False
        self._l3_in_progress = False
        self._shutting_down = False

        # Lazy-loaded sub-components
        self._scene_extractor = None
        self._persona_generator = None
        self._scene_file_manager = None
        self._summarizer = None

    # ------------------------------------------------------------------
    # Properties (lazy)
    # ------------------------------------------------------------------

    @property
    def scene_file_manager(self):
        if self._scene_file_manager is None:
            from Memory.scene_blocks.SceneFileManager import SceneFileManager
            self._scene_file_manager = SceneFileManager()
        return self._scene_file_manager

    @property
    def scene_extractor(self):
        if self._scene_extractor is None:
            from Memory.pipeline.SceneExtractor import SceneExtractor
            self._scene_extractor = SceneExtractor(scene_file_manager=self.scene_file_manager)
        return self._scene_extractor

    @property
    def persona_generator(self):
        if self._persona_generator is None:
            from Memory.pipeline.PersonaGenerator import PersonaGenerator
            self._persona_generator = PersonaGenerator(scene_file_manager=self.scene_file_manager)
        return self._persona_generator

    @property
    def summarizer(self):
        if self._summarizer is None:
            from Agent.SummarizerAgent import SummarizeAgent
            self._summarizer = SummarizeAgent()
        return self._summarizer

    # ------------------------------------------------------------------
    # Public API — called by gateway_entry
    # ------------------------------------------------------------------

    def notify_activity(self):
        """重置空闲定时器（每次用户消息时调用）。"""
        with self._lock:
            self._last_activity = time.time()
        self._restart_idle_timer()

    def on_turn_start(self):
        """每轮对话开始时的检查：触发 L1/L2 相关操作。"""
        if not self.enabled:
            return

        # 检查空闲超时 → 触发 L1
        elapsed = time.time() - self._last_activity
        if elapsed >= self.l1_idle_timeout:
            logger.info("PipelineManager: idle timeout (%.0fs), triggering L1", elapsed)
            self._run_l1()

        # 检查轮次计数 → 触发 L1
        self._turn_count += 1
        effective_threshold = self._get_effective_threshold()
        if self._turn_count >= effective_threshold:
            logger.info(
                "PipelineManager: turn threshold reached (%d/%d), triggering L1",
                self._turn_count, effective_threshold,
            )
            self._run_l1()
            self._turn_count = 0

    def on_turn_end(self, turn_context=None):
        """每轮对话结束后的操作：递增计数、检查 L3。

        L2 的延迟触发由 L1 完成后自动调度，不过这里也做一次检查。
        """
        if not self.enabled:
            return

        # 检查 L3 触发
        self._maybe_trigger_l3()

    def reset_warmup(self):
        """重置 warmup 状态（例如会话清除后）。"""
        with self._lock:
            self._warmup_threshold = 1
            self._turn_count = 0
            self._new_l1_atoms_since_l3 = 0
        logger.info("PipelineManager: warmup reset (threshold=1)")

    def shutdown(self):
        """优雅关闭：取消所有定时器。"""
        self._shutting_down = True
        self._cancel_timer("_l2_timer")
        self._cancel_timer("_idle_timer")
        logger.info("PipelineManager: shutdown complete")

    # ------------------------------------------------------------------
    # Warmup algorithm
    # ------------------------------------------------------------------

    def _get_effective_threshold(self):
        """Warmup: 1 → 2 → 4 → 8 → steady_state (l1_turn_interval)."""
        if not self.l1_warmup_enabled:
            return self.l1_turn_interval
        if self._warmup_threshold >= self.l1_turn_interval:
            return self.l1_turn_interval
        return self._warmup_threshold

    def _advance_warmup(self):
        """推进 warmup 阶段。"""
        if self._warmup_threshold >= self.l1_turn_interval:
            return
        old = self._warmup_threshold
        self._warmup_threshold = min(self._warmup_threshold * 2, self.l1_turn_interval)
        logger.info(
            "PipelineManager: warmup advanced %d → %d (steady=%d)",
            old, self._warmup_threshold, self.l1_turn_interval,
        )

    # ------------------------------------------------------------------
    # L1: Memory atom extraction
    # ------------------------------------------------------------------

    def _run_l1(self):
        """执行 L1：从工作记忆中提取记忆原子。"""
        if self._l1_in_progress:
            logger.debug("PipelineManager: L1 already in progress, skipping")
            return
        self._l1_in_progress = True
        try:
            logger.info("PipelineManager: L1 extraction started")
            self._do_l1_extraction()
            self._advance_warmup()
            logger.info("PipelineManager: L1 extraction completed, scheduling L2")
            self._schedule_l2()
        except Exception as e:
            logger.warning("PipelineManager: L1 extraction failed: %s", e)
        finally:
            self._l1_in_progress = False

    def _get_existing_atoms_for_dedup(self, limit=80):
        """查询当前活跃的 L1 原子，供 summarizer 了解已有知识避免重复提取。"""
        try:
            from Memory.repositories.L1Repository import L1Repository
            l1_repo = L1Repository()
            atoms = l1_repo.list_atoms(status="active", limit=limit)
            return [{
                "atom_type": a.get("atom_type", "fact"),
                "subject": a.get("subject", ""),
                "slot": a.get("slot", ""),
                "canonical_text": a.get("canonical_text", ""),
            } for a in atoms]
        except Exception as e:
            logger.debug("PipelineManager: failed to load existing atoms for dedup: %s", e)
            return []

    def _do_l1_extraction(self):
        """实际执行 L1 提取：从工作记忆中提取原子（不再写 MEMORY.md）。"""
        mm = self.memory_manager
        if mm is None:
            return

        existing_atoms = self._get_existing_atoms_for_dedup()

        expired_days = mm.working.list_expired_days()
        if not expired_days:
            messages = mm.get_context()
            if not messages:
                return
            self._extract_from_current_messages(messages, existing_atoms)
            return

        # 处理过期日
        target_day = expired_days[0]
        day_payload = mm.working.read_day(target_day)
        if not day_payload:
            return

        history = day_payload.get("messages", [])
        if not history:
            mm.working.delete_day(target_day)
            return

        logger.info("PipelineManager: summarizing day %s (%d messages)", target_day, len(history))
        summary = self.summarizer.summarize_day(history, {}, existing_atoms=existing_atoms)
        mm.working.delete_day(target_day)

        # DB pipeline: feed atoms to L1
        if self._db_pipeline_enabled():
            db_atoms = summary.get("atoms") or []
            if db_atoms:
                self._ingest_atoms(db_atoms)

    def _extract_from_current_messages(self, messages, existing_atoms=None):
        """从当前对话消息中提取 L1 原子（当无过期天数时）。"""
        mm = self.memory_manager
        if mm is None:
            return

        try:
            recent = messages[-40:] if len(messages) > 40 else messages
            summary = self.summarizer.summarize_day(recent, {}, existing_atoms=existing_atoms)
            db_atoms = summary.get("atoms") or []
            if db_atoms and self._db_pipeline_enabled():
                self._ingest_atoms(db_atoms)
        except Exception as e:
            logger.warning("PipelineManager: current message extraction failed: %s", e)

    def _ingest_atoms(self, atoms):
        """将原子导入 L1 数据库（复用 MemoryIngestionService）。"""
        mm = self.memory_manager
        if mm is None:
            return
        try:
            from Memory.pipeline.MemoryIngestionService import MemoryIngestionService
            service = MemoryIngestionService()
            turn_id = None
            l0_repo = getattr(mm, "l0_repo", None)
            if l0_repo:
                turn = l0_repo.get_or_create_turn(
                    session_id=mm.working.session_id,
                    day_key=datetime.now().strftime("%Y-%m-%d"),
                )
                if turn:
                    turn_id = turn["id"]
            if turn_id:
                service.ingest_turn_atoms(turn_id, atom_payload=atoms)
                self._new_l1_atoms_since_l3 += len(atoms)
                logger.info(
                    "PipelineManager: ingested %d atoms (L3 counter: %d/%d)",
                    len(atoms), self._new_l1_atoms_since_l3, self.l3_new_atom_threshold,
                )
        except Exception as e:
            logger.warning("PipelineManager: atom ingestion failed: %s", e)

    # ------------------------------------------------------------------
    # L2: Scene extraction
    # ------------------------------------------------------------------

    def _schedule_l2(self):
        """L1 完成后，安排 L2 延迟触发。"""
        self._cancel_timer("_l2_timer")
        with self._lock:
            timer = threading.Timer(self.l2_delay_after_l1, self._run_l2)
            timer.daemon = True
            self._l2_timer = timer
        timer.start()
        logger.info("PipelineManager: L2 scheduled in %.0fs", self.l2_delay_after_l1)

    def _run_l2(self):
        """执行 L2：使用 LLM 从未分配原子中提取场景。"""
        if self._l2_in_progress or self._shutting_down:
            return
        self._l2_in_progress = True
        try:
            logger.info("PipelineManager: L2 scene extraction started")
            self._do_l2_extraction()
            logger.info("PipelineManager: L2 scene extraction completed")
        except Exception as e:
            logger.warning("PipelineManager: L2 extraction failed: %s", e)
        finally:
            self._l2_in_progress = False

    def _do_l2_extraction(self):
        """实际执行 L2 提取：SceneExtractor 从 L1 未分配原子中生成场景。"""
        try:
            from Memory.repositories.L1Repository import L1Repository
            l1_repo = L1Repository()
            unassigned = l1_repo.list_atoms(only_unassigned=True, limit=100)
            if not unassigned:
                logger.info("PipelineManager: no unassigned L1 atoms, skipping L2")
                return

            atom_ids = [a["id"] for a in unassigned]
            logger.info("PipelineManager: found %d unassigned atoms for L2", len(atom_ids))
            self.scene_extractor.extract_scenes(atom_ids)
        except Exception as e:
            logger.warning("PipelineManager: L2 extraction error: %s", e)

    # ------------------------------------------------------------------
    # L3: Persona generation
    # ------------------------------------------------------------------

    def _maybe_trigger_l3(self):
        """检查 L3 触发条件。"""
        if self._l3_in_progress:
            return
        if self._shutting_down:
            return

        # 条件1：每50条新L1原子
        if self._new_l1_atoms_since_l3 >= self.l3_new_atom_threshold:
            logger.info(
                "PipelineManager: L3 triggered by atom threshold (%d >= %d)",
                self._new_l1_atoms_since_l3, self.l3_new_atom_threshold,
            )
            self._run_l3()

    def _run_l3(self):
        """执行 L3：生成或更新 persona.md。"""
        if self._l3_in_progress or self._shutting_down:
            return
        self._l3_in_progress = True
        try:
            logger.info("PipelineManager: L3 persona generation started")
            self._do_l3_generation()
        except Exception as e:
            logger.warning("PipelineManager: L3 generation failed: %s", e)
        finally:
            self._l3_in_progress = False

    def _do_l3_generation(self):
        """实际执行 L3：PersonaGenerator 深读变更场景生成 persona.md。"""
        # 获取自上次生成以来有变化的场景
        scenes = self.scene_file_manager.list_scenes()
        changed_slugs = []
        for sc in scenes:
            slug = sc.get("slug")
            if not slug:
                continue
            meta = sc
            if meta.get("status") == "active":
                changed_slugs.append(slug)

        if not changed_slugs:
            logger.info("PipelineManager: no active scenes for L3, skipping")
            return

        existing_persona = self.persona_generator.read_persona()
        logger.info(
            "PipelineManager: L3 processing %d scenes, existing persona: %s",
            len(changed_slugs),
            "yes" if existing_persona else "no",
        )
        self.persona_generator.generate_persona(changed_slugs, existing_persona)
        self._new_l1_atoms_since_l3 = 0

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate_legacy_l2_to_scene_blocks(self):
        """一次性迁移：将 SQLite l2_scenes 转换为 scene_blocks/*.md。"""
        from Memory.repositories.L2Repository import L2Repository
        from Memory.repositories.L1Repository import L1Repository

        marker_path = config.get("memory.migration.marker_path", "./runtime_memory/.migration_v2_done")
        if os.path.exists(marker_path):
            logger.info("PipelineManager: migration already done, skipping")
            return 0

        l2_repo = L2Repository()
        l1_repo = L1Repository()
        scenes = l2_repo.list_scenes(status=None)
        migrated = 0

        for scene in scenes:
            slug = scene.get("slug")
            if not slug:
                continue
            # 读取成员
            members = l2_repo.list_members(scene["id"])
            atom_ids = [m["atom_id"] for m in members]
            meta = {
                "slug": slug,
                "title": scene.get("title") or slug,
                "keywords": scene.get("keywords") or [],
                "importance": float(scene.get("importance", 0.5)),
                "status": scene.get("status", "active"),
                "atom_ids": atom_ids,
                "member_count": len(atom_ids),
                "created_at": scene.get("created_at", datetime.now().isoformat()),
                "updated_at": scene.get("updated_at") or datetime.now().isoformat(),
            }
            body = (
                f"# Summary\n\n{scene.get('summary') or '_(migrated from SQLite)_'}\n\n"
                f"## Key Facts\n\n- (migrated from SQLite l2_scenes)\n\n"
                f"## Timeline\n\n- Created: {meta['created_at']}\n"
                f"- Updated: {meta['updated_at']}\n\n"
                f"## Open Questions\n\n- (to be filled)\n"
            )
            try:
                self.scene_file_manager.write_scene(meta, body)
                migrated += 1
            except Exception as e:
                logger.warning("PipelineManager: failed to migrate scene %s: %s", slug, e)

        # 写入标记
        Path(marker_path).parent.mkdir(parents=True, exist_ok=True)
        Path(marker_path).write_text(f"migrated_at={datetime.now().isoformat()}\ncount={migrated}")
        logger.info("PipelineManager: migrated %d scenes to scene_blocks", migrated)
        return migrated

    def migrate_legacy_l3_to_persona(self):
        """一次性迁移：从 USER.md 生成初始 persona.md。"""
        persona_path = config.get("memory.persona.path", "./runtime_memory/persona.md")
        if os.path.exists(persona_path):
            logger.info("PipelineManager: persona.md already exists, skipping migration")
            return

        user_path = config.get("memory.user.path", "./runtime_memory/USER.md")
        if not os.path.exists(user_path):
            logger.info("PipelineManager: no USER.md to migrate")
            return

        user_content = Path(user_path).read_text(encoding="utf-8")
        # 简单转换
        persona_content = (
            "# User Persona\n\n"
            "## Core Identity\n\n"
            + user_content +
            "\n\n## Skills & Knowledge\n\n_(to be generated from conversations)_\n\n"
            "## Preferences & Constraints\n\n_(to be generated from conversations)_\n\n"
            "## Goals & Motivations\n\n_(to be generated from conversations)_\n\n"
        )
        try:
            self.persona_generator.write_persona(persona_content)
            logger.info("PipelineManager: migrated USER.md → persona.md")
        except Exception as e:
            logger.warning("PipelineManager: persona migration failed: %s", e)

    # ------------------------------------------------------------------
    # Timer helpers
    # ------------------------------------------------------------------

    def _restart_idle_timer(self):
        self._cancel_timer("_idle_timer")
        with self._lock:
            timer = threading.Timer(self.l1_idle_timeout, self._on_idle_timeout)
            timer.daemon = True
            self._idle_timer = timer
        timer.start()

    def _on_idle_timeout(self):
        logger.info("PipelineManager: idle timeout, triggering L1")
        self._run_l1()

    def _cancel_timer(self, attr_name):
        with self._lock:
            timer = getattr(self, attr_name, None)
            if timer is not None:
                timer.cancel()
                setattr(self, attr_name, None)

    @staticmethod
    def _db_pipeline_enabled():
        return bool(config.get("memory.pipeline.db_enabled", True))
