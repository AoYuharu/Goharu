import json
import logging
from pathlib import Path

from Core.ToolCall import ToolCall
from Core.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Core.PromptSection import PromptSection

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "Skills"


def _scan_skills():
    skills_text = ""
    if not _SKILLS_DIR.is_dir():
        return skills_text
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
            skills_text += f"- {name}：{desc}\n"
        except Exception as exc:
            logger.warning(f"Failed to read skill {skill_file}: {exc}")
    return skills_text


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

    # ── 静态引导语 ─────────────────────────────────────────────────

    _SOUL_GUIDE = "以下是稳定角色设定 SOUL.md，请将其视为高优先级的长期风格与行为边界指导。"
    _L3_PROFILE_GUIDE = "以下是用户画像摘要（L3/persona.md）。这是最权威的用户建模上下文，优先级高于其他记忆。"
    _RETRIEVED_MEMORIES_GUIDE = "以下是检索到的相关记忆原子（L1）。请在相关时使用，注意这些是检索召回结果，可能不完全精确。"
    _RETRIEVED_SCENES_GUIDE = "以下是检索到的相关场景上下文（L2）。请用于理解当前对话的主题背景和长期任务状态。"
    _TOOL_DIRECTORY_GUIDE = "以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。"

    _SKILLS_GUIDE = (
        "## Skills 预置技能\n"
        "Skills 是针对特殊需求的预置任务范式（SOP）。"
        "当用户的需求无法由现有工具链直接完成（例如做一道菜、执行某个特定领域流程）时，"
        "应调用 Skill 工具加载对应的技能说明获取完整指导。"
        "面对包装后的大任务、工具无法一步到位时，先尝试用 Skill 检索是否有匹配的预置范式。\n"
        "### 可用技能列表\n"
    ) + _scan_skills()

    # ── Section builders ────────────────────────────────────────────

    def _build_soul_section(self, soul_markdown):
        soul_text = self._normalize_text(soul_markdown)
        if not soul_text:
            return None
        return PromptSection(
            kind="system", title="soul",
            content=f"{self._SOUL_GUIDE}\n\n{soul_text}",
            metadata={"section_name": "soul"},
            cache_control={"type": "ephemeral"},
        )

    def _build_l3_profile_summary_section(self, profile_summary):
        summary_text = self._normalize_text(profile_summary)
        if not summary_text:
            return None
        return PromptSection(
            kind="system", title="user_profile_compact",
            content=f"{self._L3_PROFILE_GUIDE}\n\n{summary_text}",
            metadata={"section_name": "user_profile_compact"},
            cache_control={"type": "ephemeral"},
        )

    def _build_retrieved_memories_section(self, retrieved_memories):
        if not retrieved_memories:
            return None
        lines = []
        for item in retrieved_memories:
            title = item.get("title") or "memory"
            text = item.get("text") or ""
            lines.append(f"- [{title}] {text}")
        content = self._RETRIEVED_MEMORIES_GUIDE + "\n\n" + "\n".join(lines)
        return PromptSection(
            kind="system", title="retrieved_memories",
            content=content, metadata={"section_name": "retrieved_memories"},
            cache_control={"type": "ephemeral"},
        )

    def _build_retrieved_scenes_section(self, retrieved_scenes):
        if not retrieved_scenes:
            return None
        lines = []
        for item in retrieved_scenes:
            title = item.get("title") or "scene"
            summary = item.get("summary") or ""
            keywords = ", ".join(item.get("keywords") or [])
            lines.append(f"- [{title}] {summary}" + (f" (keywords: {keywords})" if keywords else ""))
        content = self._RETRIEVED_SCENES_GUIDE + "\n\n" + "\n".join(lines)
        return PromptSection(
            kind="system", title="retrieved_scenes",
            content=content, metadata={"section_name": "retrieved_scenes"},
            cache_control={"type": "ephemeral"},
        )

    def _build_skills_section(self):
        skills_text = self._SKILLS_GUIDE.strip()
        if not skills_text:
            return None
        return PromptSection(
            kind="system", title="skills",
            content=skills_text, metadata={"section_name": "skills"},
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
            if record.get("id"):
                metadata["id"] = record.get("id")
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
        document.extend_system(self._load_agent_system_sections("actor"))

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

