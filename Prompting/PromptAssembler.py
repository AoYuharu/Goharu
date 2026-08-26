import json
import logging
from pathlib import Path

from Core.ToolCall import ToolCall
from Core.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Core.PromptSection import PromptSection

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
    """提示词组装器 — 金字塔记忆体系（SOUL + L1/L2检索 + L3 persona 摘要）。"""

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

    def _build_soul_section(self, soul_markdown):
        soul_text = self._normalize_text(soul_markdown)
        if not soul_text:
            return None
        return PromptSection(
            kind="system", title="soul",
            content=_xml("soul_definition", soul_text, priority="high"),
            metadata={"section_name": "soul"},
            cache_control={"type": "ephemeral"},
        )

    def _build_l3_profile_summary_section(self, profile_summary):
        summary_text = self._normalize_text(profile_summary)
        if not summary_text:
            return None
        return PromptSection(
            kind="system", title="user_profile_compact",
            content=_xml("persona", summary_text, source="l3_pipeline", priority="highest"),
            metadata={"section_name": "user_profile_compact"},
            cache_control={"type": "ephemeral"},
        )

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
        content = _xml("retrieved_memories", "\n".join(items_xml),
            type="l1", count=str(len(retrieved_memories)))
        return PromptSection(
            kind="system", title="retrieved_memories",
            content=content, metadata={"section_name": "retrieved_memories"},
            cache_control={"type": "ephemeral"},
        )

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
        content = _xml("retrieved_scenes", "\n".join(items_xml),
            type="l2", count=str(len(retrieved_scenes)))
        return PromptSection(
            kind="system", title="retrieved_scenes",
            content=content, metadata={"section_name": "retrieved_scenes"},
            cache_control={"type": "ephemeral"},
        )

    def _build_skills_section(self):
        skills_xml = _scan_skills()
        if not skills_xml:
            return None
        return PromptSection(
            kind="system", title="skills",
            content=_xml("available_skills", skills_xml),
            metadata={"section_name": "skills"},
            cache_control={"type": "ephemeral"},
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
        self, history, soul_markdown="",
        extra_system_prompt=None, tool_definitions=None, retrieval_pack=None,
    ):
        """构建 actor prompt 文档。

        金字塔体系注入顺序：actor system → SOUL → L3 persona →
        L1 检索记忆 → L2 检索场景 → skills → 对话历史。
        """
        document = PromptDocument()
        for section in self._load_agent_system_sections("actor"):
            document.add_system(PromptSection(
                kind=section.kind, title=section.title,
                content=_xml("actor_role", section.content),
                metadata=section.metadata,
                cache_control=section.cache_control,
            ))

        soul_section = self._build_soul_section(soul_markdown)
        if soul_section is not None:
            document.add_system(soul_section)

        # L3 persona summary (from persona.md)
        if retrieval_pack:
            profile_summary = retrieval_pack.get("profile_summary")
            if profile_summary:
                profile_section = self._build_l3_profile_summary_section(profile_summary)
                if profile_section is not None:
                    document.add_system(profile_section)

            # L1 memories
            retrieved_mems = retrieval_pack.get("memories") or []
            mem_section = self._build_retrieved_memories_section(retrieved_mems)
            if mem_section is not None:
                document.add_system(mem_section)

            # L2 scenes
            retrieved_scenes = retrieval_pack.get("scenes") or []
            scene_section = self._build_retrieved_scenes_section(retrieved_scenes)
            if scene_section is not None:
                document.add_system(scene_section)

        skills_section = self._build_skills_section()
        if skills_section is not None:
            document.add_system(skills_section)

        # Find the last user message — everything before it gets cached
        last_user_idx = -1
        if history:
            for i in range(len(history) - 1, -1, -1):
                role = (history[i].get("role") or "").strip() if isinstance(history[i], dict) else ""
                if role == "user":
                    last_user_idx = i
                    break

        for i, record in enumerate(history or []):
            enable_cache = (last_user_idx >= 0 and i < last_user_idx)
            section = self.record_to_section(record, enable_cache=enable_cache)
            if section is not None:
                document.add_conversation(section)

        extra_prompt = self._normalize_text(extra_system_prompt)
        if extra_prompt:
            document.add_conversation(PromptSection(
                kind="system", title="extra_instruction", content=extra_prompt,
                metadata={"section_name": "extra_instruction"},
            ))

        return document

    def build_day_summary_document(self, history, old_memory=None, existing_atoms=None):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))

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
                          content="请根据任务A（日总结）的格式，生成该日的长期记忆摘要 JSON。",
                          metadata={"section_name": "summary_task"}))
        document.extend_conversation(sections)
        return document

    def build_context_compact_document(self, conversation, system_prompt=None):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))

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
            content="请根据任务C（上下文压缩）的 9 点框架输出结构化摘要，直接作为最终输出。",
            metadata={"section_name": "compact_task"},
        ))
        document.extend_conversation(sections)
        return document

