import dataclasses
import json
import logging
from pathlib import Path

from Core.ToolCall import ToolCall
from Core.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Core.PromptSection import PromptSection
from Tools.git_status_provider import get_git_status_text

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "Skills"


def _xml(tag, content, **attrs):
    """Wrap content in an XML tag with optional attributes."""
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items() if v is not None)
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


def _scan_skills():
    items = []
    if not _SKILLS_DIR.is_dir():
        return ""
    for subdir in sorted(_SKILLS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        skill_file = subdir / "skill.md"
        if not skill_file.is_file():
            continue
        try:
            lines = [l.strip() for l in skill_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            if not lines:
                continue
            name = lines[0].lstrip("#").strip()
            desc = lines[1] if len(lines) > 1 else ""
            items.append(f'<skill name="{name}">\n{desc}\n</skill>')
        except Exception as exc:
            logger.warning(f"Failed to read skill {skill_file}: {exc}")
    return "\n".join(items)


class PromptAssembler:
    """提示词组装器 — 金字塔记忆体系（L1/L2检索 + L3 persona 摘要）。"""

    def __init__(self, prompt_loader=None):
        self.prompt_loader = prompt_loader or PromptLoader()

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
            keywords = ", ".join(item.get("keywords") or [])
            score = item.get("score")
            items_xml.append(_xml("scene", summary,
                slug=str(slug),
                score=f"{score:.4f}" if score is not None else None,
                keywords=keywords or None))
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

    def _build_skills_reminder(self):
        """技能列表的当前轮 system-reminder 文本（对齐 Claude Code：贴在最新用户消息上）。

        不使用静态 system 前缀——技能列表贴近当前轮注入，显著性更高，
        且不占用 prompt cache 的 system 断点。
        """
        skills_xml = _scan_skills()
        if not skills_xml:
            return None
        return (
            "<system-reminder>\n"
            "The following skills are available for use with the Skill tool:\n\n"
            f"{skills_xml}\n\n"
            "When a skill matches the user's request, this is a BLOCKING REQUIREMENT: "
            "invoke the Skill tool BEFORE generating any other response about the task.\n"
            "</system-reminder>"
        )

    def _build_git_status_section(self):
        """将当前 Git 状态注入为 system section。"""
        text = get_git_status_text()
        return PromptSection(
            kind="system", title="git_status",
            content=_xml("git_status", text),
            metadata={"section_name": "git_status"},
        )

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
                tag = "user_message" if role == "user" else "assistant_message"
                content = _xml(tag, content, id=msg_id or None)
            kwargs = dict(kind=role, title=role, content=content, metadata=metadata)
            if enable_cache:
                kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**kwargs)

        raise ValueError(f"Unsupported history role: {role}")

    # ── Document builders ───────────────────────────────────────────

    def build_actor_document(
        self, history,
        extra_system_prompt=None, tool_definitions=None, retrieval_pack=None,
        memory_contexts=None,
    ):
        """构建 actor prompt 文档。

        金字塔体系注入顺序：actor system（含角色定义）→ git_status →
        对话历史。L3 persona + L1 检索记忆 + L2 检索场景以 system-reminder
        形式贴在用户消息上（当前轮结果贴最新消息；历史轮按消息 id 记账
        贴回原消息，前缀跨 turn 逐字节稳定）；技能列表只贴最新一条。

        memory_contexts: 用户消息 id → 记忆上下文块（调用方跨 turn 持有的记账表）。
        """
        document = PromptDocument()
        for section in self._load_agent_system_sections("actor"):
            document.add_system(PromptSection(
                kind=section.kind, title=section.title,
                content=_xml("actor_role", section.content),
                metadata=section.metadata,
                cache_control=section.cache_control,
            ))

        git_status_section = self._build_git_status_section()
        if git_status_section is not None:
            document.add_system(git_status_section)

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

        turn_parts = []
        memory_context = self._build_memory_context(retrieval_pack)
        if memory_context:
            turn_parts.append(memory_context)
        skills_reminder = self._build_skills_reminder()
        if skills_reminder:
            turn_parts.append(skills_reminder)
        turn_context = "\n\n".join(turn_parts)

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

