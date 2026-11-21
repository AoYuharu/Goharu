import dataclasses
import json
import logging
from pathlib import Path

from Core.ToolCall import ToolCall
from Core.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Prompting.reminders import TurnReminders
from Core.PromptSection import PromptSection

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "Skills"


def _get_git_status_text():
    """惰性获取 git 状态文本（Goharu 特有注入）。

    独立使用 Prompting 包时无 Tools 依赖：返回 None，调用方跳过该 section。
    """
    try:
        from Tools.git_status_provider import get_git_status_text
    except ImportError:
        return None
    return get_git_status_text()


def _xml(tag, content, **attrs):
    """Wrap content in an XML tag with optional attributes."""
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items() if v is not None)
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


def _scan_skills_items():
    """返回 [(name, xml_item), ...]；供三态技能便利贴按名求增量。"""
    # 复用 skill_tool 的元数据解析（YAML frontmatter 优先，回退旧"前两行"约定）；
    # 此前这里只按旧约定取前两行，frontmatter 技能的名字全被解析成 "---"
    try:
        from Tools.builtin.skill_tool import _parse_skill_meta
    except ImportError:
        return []  # 独立使用 Prompting 包时无 Tools 依赖：技能便利贴为空

    items = []
    if not _SKILLS_DIR.is_dir():
        return items
    for subdir in sorted(_SKILLS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        skill_file = subdir / "skill.md"
        if not skill_file.is_file():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            if not content.strip():
                continue
            name, desc = _parse_skill_meta(content, subdir.name)
            items.append((name, f'<skill name="{name}">\n{desc}\n</skill>'))
        except Exception as exc:
            logger.warning(f"Failed to read skill {skill_file}: {exc}")
    return items


class PromptAssembler:
    """提示词组装器 — 金字塔记忆体系（L1/L2检索 + L3 persona 摘要）。"""

    def __init__(self, prompt_loader=None, include_git_status=True, include_skills=True, include_persona=True):
        self.prompt_loader = prompt_loader or PromptLoader()
        # Goharu 特有注入项；独立部署（Prompting.api facade）默认关闭
        self.include_git_status = include_git_status
        self.include_skills = include_skills
        # include_persona=True：persona.md 全文作为静态 system section 注入（Goharu 模式）；
        # False：persona 摘要走 per-turn profile_summary 注入（facade 独立部署，无 persona.md 文件）
        self.include_persona = include_persona
        # git_status 启动快照：会话全程冻结进静态前缀，保证前缀逐字节稳定。
        # 此前每轮取新值，agent 每写一个文件 git status 就变 → system 前缀中部
        # 变化 → 整个缓存打穿（2026-10-16 实测：写一个 tmp 脚本 = 25.8k 全量
        # 重计费）。后续变化量走每轮便利贴（_build_turn_info_block）。
        self._git_status_snapshot = None
        if include_git_status:
            try:
                from Tools.git_status_provider import GitStatusProvider
                self._git_status_snapshot = GitStatusProvider.get_status_sync()
            except Exception as exc:  # 含 ImportError（独立部署无 Tools）
                logger.warning("git_status startup snapshot failed: %s", exc)

    @staticmethod
    def _json_text(value):
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _normalize_text(value):
        return str(value or "").strip()

    def _load_agent_system_sections(self, agent_key):
        sections = []
        sections.extend(self.prompt_loader.load_system_sections("shared"))
        sections.extend(self.prompt_loader.load_system_sections(agent_key))
        return sections

    def _build_persona_section(self, persona_text):
        """persona.md 全文的静态 system section（Goharu 模式，include_persona=True）。

        persona 是缓慢变化的长期画像（L3），放在 system 前缀末尾作为静态部分：
        persona.md 不变则前缀逐字节稳定（断点1 缓存覆盖它）；
        persona.md 变化则前缀变化、缓存失效重建——这是用户明确接受的代价
        （L3 画像变化频率远低于 per-turn 检索结果）。
        """
        text = self._normalize_text(persona_text)
        if not text:
            return None
        return PromptSection(
            kind="system", title="persona",
            content=_xml("persona", text, source="l3_persona"),
            metadata={"section_name": "persona"},
        )

    def _build_l3_profile_summary_section(self, profile_summary):
        summary_text = self._normalize_text(profile_summary)
        if not summary_text:
            return None
        return _xml("persona", summary_text, source="l3_pipeline", priority="highest")

    def _build_retrieved_memories_section(self, retrieved_memories):
        if not retrieved_memories:
            return None
        items_xml = []
        for item in retrieved_memories:
            title = item.get("title") or "memory"
            text = item.get("text") or ""
            sid = item.get("source_id", "")
            score = item.get("score")
            items_xml.append(_xml("memory", text,
                id=str(sid),
                score=f"{score:.4f}" if score is not None else None,
                title=title))
        return _xml("retrieved_memories", "\n".join(items_xml),
            type="l1", count=str(len(retrieved_memories)))

    def _build_retrieved_scenes_section(self, retrieved_scenes):
        if not retrieved_scenes:
            return None
        items_xml = []
        for item in retrieved_scenes:
            slug = item.get("source_id") or item.get("title") or "scene"
            summary = item.get("summary") or ""
            score = item.get("score")
            items_xml.append(_xml("scene", summary,
                slug=str(slug),
                score=f"{score:.4f}" if score is not None else None))
        return _xml("retrieved_scenes", "\n".join(items_xml),
            type="l2", count=str(len(retrieved_scenes)))

    def _build_memory_context(self, retrieval_pack):
        """把 per-turn 检索结果拼成一个可贴在最新用户消息上的文本块。

        不再作为 system section 注入前缀中部——检索结果每 turn 都会变，
        放在前缀里会导致整个 prompt cache 前缀失效（写入 2.7 倍于读取）。
        贴在最新用户消息上则位于全部缓存断点之后，随 turn 增量累积。
        """
        if not retrieval_pack:
            return None
        parts = []
        # Goharu 模式（include_persona=True）persona 全文走 system 前缀，
        # per-turn 不再注入 profile_summary；facade 模式（False）保留摘要注入
        if not self.include_persona:
            persona = self._build_l3_profile_summary_section(retrieval_pack.get("profile_summary"))
            if persona:
                parts.append(persona)
        memories = self._build_retrieved_memories_section(retrieval_pack.get("memories") or [])
        if memories:
            parts.append(memories)
        scenes = self._build_retrieved_scenes_section(retrieval_pack.get("scenes") or [])
        if scenes:
            parts.append(scenes)
        if not parts:
            return None
        return (
            "<system-reminder>\n"
            "Memory context retrieved for the current turn "
            "(re-injected every turn; older copies in the conversation history are outdated):\n"
            + "\n".join(parts) + "\n"
            "</system-reminder>"
        )

    def _build_skills_reminder(self, engine):
        """技能列表便利贴：full/sparse/delta 三态（TurnReminders 引擎节流）。

        首轮全量（内容永久留在该消息上）；无变化一行稀疏；有增删只列增量。
        贴在最新用户消息上——不占用 prompt cache 的 system 断点。
        """
        reminder = engine.skills_reminder(_scan_skills_items())
        if reminder is None:
            return None
        return f"<system-reminder>\n{reminder[1]}\n</system-reminder>"

    def _build_turn_info_block(self, engine, retrieval_pack, deliver_reminders=False):
        """环境/记忆状态便利贴：当前时间 + 跨天通知 + 记忆库规模 + git_status 变化量。

        deliver_reminders=True 时才把到点的定时提醒取出来贴（取即确认投递）。
        """
        stats = (retrieval_pack or {}).get("memory_stats")
        parts = []
        for reminder in (
            engine.plan_mode_reminder(),
            engine.datetime_reminder(),
            engine.date_change_reminder(),
            engine.memory_stats_reminder(stats),
        ):
            if reminder is not None:
                parts.append(reminder[1])
        # 通用额外便利贴（宿主注入：messagebox 摘要等——纯文本列表，模板无关）
        for extra in (retrieval_pack or {}).get("extra_reminders") or []:
            parts.append(str(extra))
        git_delta = self._build_git_status_delta()
        if git_delta:
            parts.append(git_delta)
        if deliver_reminders:
            due_reminders = self._build_due_reminders_block()
            if due_reminders:
                parts.append(due_reminders)
        if not parts:
            return None
        return "<system-reminder>\n" + "\n\n".join(parts) + "\n</system-reminder>"

    def _build_due_reminders_block(self):
        """定时提醒便利贴：认领所有已到点的提醒（schedule_reminder 工具写入）。

        只**认领**不消费——真正消费在 commit_pending_reminders()（LLM 请求成功后）。
        这样装配成功但请求失败重试时，提醒不会被提前删掉。同一轮重复装配复用
        已认领的条目，不会重复注入。
        """
        try:
            from Core.ReminderStore import get_reminder_store
        except Exception:  # 独立部署无 Core：静默跳过
            return None
        store = get_reminder_store()
        pending = getattr(self, "_pending_reminder_claims", None)
        if pending:
            due = pending
        else:
            try:
                due = store.claim_due()
            except Exception:
                return None
            if not due:
                return None
            self._pending_reminder_claims = due
        lines = ["# 定时提醒（已到点）"]
        for item in due:
            stamp = str(item.get("fire_at", ""))[:16].replace("T", " ")
            repeat = item.get("repeat_seconds")
            tag = f"每 {int(repeat)} 秒重复" if repeat else "一次性"
            lines.append(f"- [{stamp} | {tag}] {item.get('text', '')}")
        return "\n".join(lines)

    def commit_pending_reminders(self):
        """投递成功确认：把本轮认领的提醒正式消费（一次性删除 / 周期推进）。"""
        pending = getattr(self, "_pending_reminder_claims", None)
        if not pending:
            return 0
        self._pending_reminder_claims = None
        try:
            from Core.ReminderStore import get_reminder_store
            return get_reminder_store().ack_due(pending)
        except Exception:
            return 0

    def rollback_pending_reminders(self):
        """投递失败/中断：释放认领，让提醒在下一轮重新出现。"""
        pending = getattr(self, "_pending_reminder_claims", None)
        if not pending:
            return 0
        self._pending_reminder_claims = None
        try:
            from Core.ReminderStore import get_reminder_store
            return get_reminder_store().release_due(pending)
        except Exception:
            return 0

    def _can_inject_turn_context(self, history):
        """本轮上下文能否真正贴到最新用户消息上。

        工具循环中间步骤的最后一条是 tool_result（content 是块列表），per-turn
        块无处可贴。提前判定，避免「已消费但没贴出」的静默丢失。
        """
        for record in reversed(history or []):
            if not isinstance(record, dict):
                continue
            if (record.get("role") or "").strip() != "user":
                continue
            try:
                section = self.record_to_section(record)
            except Exception:
                return False
            return section is not None and section.kind == "user" and isinstance(section.content, str)
        return False

    def _build_turn_context(self, retrieval_pack, reminder_state=None, deliver_reminders=False):
        """本轮上下文块：便利贴（时间/记忆状态/技能）+ 检索记忆，贴最新用户消息。

        reminder_state: 调用方跨 turn 持有的 dict（与 memory_contexts 同生命周期）；
        None 时无状态退化（date_change 不触发、技能每轮全量——facade 安全默认）。
        deliver_reminders: 是否把到点的定时提醒取出来投递。默认 False——只有确认
        这一块真的能贴到用户消息上时才由调用方置 True（见 _can_inject_turn_context）。
        """
        engine = TurnReminders(reminder_state)
        parts = []
        info = self._build_turn_info_block(engine, retrieval_pack, deliver_reminders)
        if info:
            parts.append(info)
        memory_context = self._build_memory_context(retrieval_pack)
        if memory_context:
            parts.append(memory_context)
        if self.include_skills:
            skills_reminder = self._build_skills_reminder(engine)
            if skills_reminder:
                parts.append(skills_reminder)
        return "\n\n".join(parts)

    def _build_git_status_section(self):
        """将启动快照的 Git 状态注入为 system section（会话内逐字节稳定）。"""
        text = self._git_status_snapshot
        if not text:
            text = _get_git_status_text()  # 快照失败时退化为旧实时行为
            self._git_status_snapshot = text
        if not text:
            return None  # 独立部署无 Tools：跳过 git_status section
        return PromptSection(
            kind="system", title="git_status",
            content=_xml("git_status", text + "\n(snapshot at session start; changes appear in per-turn reminders)"),
            metadata={"section_name": "git_status"},
        )

    def _build_git_status_delta(self):
        """git_status 较启动快照的变化量（per-turn 便利贴；无变化返回 None）。"""
        if not self.include_git_status or not self._git_status_snapshot:
            return None
        fresh = _get_git_status_text()
        if not fresh or fresh.startswith("Git status unavailable") or fresh == self._git_status_snapshot:
            return None
        return "# git_status（较会话启动时已变化）\n" + fresh

    # ── 对话记录转 Section ──────────────────────────────────────────

    @staticmethod
    def record_to_section(record, enable_cache=False):
        if isinstance(record, ToolCall):
            section = record.to_section()
            if enable_cache:
                return PromptSection(
                    kind=section.kind, title=section.title, content=section.content,
                    metadata=section.metadata, cache_control={"type": "ephemeral"},
                )
            return section

        if not isinstance(record, dict):
            raise TypeError("Prompt history record must be dict or ToolCall")

        if record.get("message_type") == "tool_call":
            tool_call = ToolCall.from_record(record)
            if tool_call is None:
                raise ValueError("Structured tool_call record is invalid")
            content = record.get("content")
            if not isinstance(content, list):
                content = [tool_call.to_anthropic_tool_use()]
            metadata = {
                "tool_name": tool_call.tool_name,
                "arguments": dict(tool_call.arguments),
            }
            if record.get("timestamp"):
                metadata["timestamp"] = record.get("timestamp")
            if record.get("id"):
                metadata["id"] = record.get("id")
            kwargs = dict(kind="tool_call", title=tool_call.tool_name, content=content, metadata=metadata)
            if enable_cache:
                kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**kwargs)

        role = str(record.get("role") or "").strip()
        content = record.get("content", "")
        timestamp = record.get("timestamp")

        if role == "tool":
            raise ValueError("Legacy role='tool' messages are no longer supported")

        if role in {"user", "assistant", "system"}:
            metadata = {}
            if timestamp:
                metadata["timestamp"] = timestamp
            msg_id = record.get("id")
            if msg_id:
                metadata["id"] = msg_id
            if role in {"user", "assistant"} and isinstance(content, str):
                # 不再向模型暴露消息 ID（原为 Snip 按 ID 删消息服务；Snip 已移除）
                tag = "user_message" if role == "user" else "assistant_message"
                content = _xml(tag, content)
            kwargs = dict(kind=role, title=role, content=content, metadata=metadata)
            if enable_cache:
                kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**kwargs)

        raise ValueError(f"Unsupported history role: {role}")

    # ── Document builders ───────────────────────────────────────────

    def build_actor_document(
        self, history,
        extra_system_prompt=None, tool_definitions=None, retrieval_pack=None,
        memory_contexts=None, persona_text=None, reminder_state=None,
    ):
        """构建 actor prompt 文档。

        金字塔体系注入顺序：actor system（含角色定义）→ git_status → persona.md
        全文 → 对话历史。L3 persona 全文作为静态 system section 放在断点1之前
        （变化频率低，缓存代价可接受）；L1 检索记忆 + L2 检索场景以 system-reminder
        形式贴在用户消息上（当前轮结果贴最新消息；历史轮按消息 id 记账贴回原消息，
        前缀跨 turn 逐字节稳定）；技能列表只贴最新一条。

        memory_contexts: 用户消息 id → 记忆上下文块（调用方跨 turn 持有的记账表）。
        persona_text: persona.md 全文（Goharu 模式注入 system 前缀；facade 不传）。
        """
        document = PromptDocument()
        for section in self._load_agent_system_sections("actor"):
            document.add_system(PromptSection(
                kind=section.kind, title=section.title,
                content=_xml("actor_role", section.content),
                metadata=section.metadata,
                cache_control=section.cache_control,
            ))

        if self.include_git_status:
            git_status_section = self._build_git_status_section()
            if git_status_section is not None:
                document.add_system(git_status_section)

        if self.include_persona:
            persona_section = self._build_persona_section(persona_text)
            if persona_section is not None:
                document.add_system(persona_section)

        # ── 收敛缓存断点到 ≤4（Anthropic 硬限制）──
        # 断点语义：一个断点会缓存从会话开头到该块为止的全部内容。
        # 只需两个断点：
        #   1) 最后一个 system 块 —— 覆盖整个静态 system 前缀
        #   2) 最后一个可缓存的历史轮 —— 覆盖所有累积的历史对话
        # per-turn 检索内容贴在最新用户消息上（两个断点之后），不破坏前缀。
        if document.system_sections:
            last_sys = len(document.system_sections) - 1
            for i in range(last_sys):
                document.system_sections[i] = dataclasses.replace(
                    document.system_sections[i], cache_control=None
                )
            document.system_sections[last_sys] = dataclasses.replace(
                document.system_sections[last_sys],
                cache_control={"type": "ephemeral"},
            )

        # Find the last user message — everything before it is the cacheable history prefix
        last_user_idx = -1
        if history:
            for i in range(len(history) - 1, -1, -1):
                role = (history[i].get("role") or "").strip() if isinstance(history[i], dict) else ""
                if role == "user":
                    last_user_idx = i
                    break

        # 只给最后一个可缓存历史轮打断点（覆盖其之前全部历史），不再逐条打点
        for i, record in enumerate(history or []):
            enable_cache = (last_user_idx >= 1 and i == last_user_idx - 1)
            section = self.record_to_section(record, enable_cache=enable_cache)
            if section is not None:
                document.add_conversation(section)

        # ── per-turn 注入（位于全部缓存断点之后，不破坏前缀缓存）──
        # 记忆上下文 + 技能列表合成一个"本轮上下文块"，记账到当轮最新用户
        # 消息 id；历史轮按记账表贴回原消息（Claude Code 的 system-reminder
        # 常驻历史模式），前缀跨 turn 逐字节稳定。没有 id 的消息退化为只贴
        # 最新一条。工具循环中最后一条是 tool_result，注入在 turn 开始时
        # 已完成，不受影响。
        if memory_contexts is None:
            memory_contexts = {}

        # 只有最后一条用户消息能承载文本时，per-turn 块才真的贴得出去；
        # 工具循环的中间步骤最后一条是 tool_result，此时不投递定时提醒，
        # 让提醒留到下一轮（否则会被消费掉却贴不出来，静默丢失）。
        can_inject = self._can_inject_turn_context(history)
        turn_context = self._build_turn_context(
            retrieval_pack, reminder_state, deliver_reminders=can_inject
        )

        if turn_context:
            last_user_id = None
            for i in range(len(history) - 1, -1, -1):
                record = history[i] if isinstance(history[i], dict) else {}
                if (record.get("role") or "").strip() == "user":
                    last_user_id = record.get("id")
                    break
            if last_user_id:
                memory_contexts[last_user_id] = turn_context
            elif document.conversation_sections:
                last = document.conversation_sections[-1]
                if last.kind == "user" and isinstance(last.content, str):
                    document.conversation_sections[-1] = dataclasses.replace(
                        last, content=turn_context + "\n\n" + last.content
                    )

        for i, section in enumerate(document.conversation_sections):
            mid = section.metadata.get("id") if section.metadata else None
            if (
                section.kind == "user"
                and isinstance(section.content, str)
                and mid in memory_contexts
            ):
                document.conversation_sections[i] = dataclasses.replace(
                    section,
                    content=memory_contexts[mid] + "\n\n" + section.content,
                )

        extra_prompt = self._normalize_text(extra_system_prompt)
        if extra_prompt:
            document.add_conversation(PromptSection(
                kind="system", title="extra_instruction", content=extra_prompt,
                metadata={"section_name": "extra_instruction"},
            ))

        return document

    def build_day_summary_document(self, history, old_memory=None, existing_atoms=None):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("day_summary"))

        sections = [
            PromptSection(kind="user", title="day_history",
                          content="以下是某一天的历史对话：\n\n" + self._json_text(history or []),
                          metadata={"section_name": "day_history"}),
        ]
        if existing_atoms:
            sections.append(PromptSection(kind="user", title="existing_atoms",
                          content="以下是已存在的记忆原子（避免输出重复内容）：\n\n" + self._json_text(existing_atoms),
                          metadata={"section_name": "existing_atoms"}))
        sections.append(PromptSection(kind="user", title="summary_task",
                          content="请生成该日的长期记忆摘要 JSON。",
                          metadata={"section_name": "summary_task"}))
        document.extend_conversation(sections)
        return document

    def build_context_compact_document(self, conversation, system_prompt=None):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("context_compact"))

        sections = [
            PromptSection(kind="user", title="conversation_history",
                          content="以下是需要压缩的对话历史：\n\n" + self._json_text(conversation),
                          metadata={"section_name": "conversation_history"}),
        ]
        if system_prompt:
            sections.append(PromptSection(
                kind="user", title="current_system_prompt",
                content="当前系统提示词（仅做背景参考，不需要重复摘要）：\n\n" + system_prompt,
                metadata={"section_name": "current_system_prompt"},
            ))
        sections.append(PromptSection(
            kind="user", title="compact_task",
            content="请按照 9 点框架输出结构化摘要，直接作为最终输出。",
            metadata={"section_name": "compact_task"},
        ))
        document.extend_conversation(sections)
        return document

