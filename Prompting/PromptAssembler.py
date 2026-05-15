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
    """启动时扫描 Skills/ 目录，读取每个 skill.md 的标题和描述，构建注入文本。"""
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
    """提示词组装器 - 从外部文件加载系统提示词，静态引导语直接写死在代码中"""

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

    # ── 静态引导语（直接写死，不读文件） ──────────────────────────────

    _SOUL_GUIDE = "以下是稳定角色设定 SOUL.md，请将其视为高优先级的长期风格与行为边界指导。"
    _USER_PROFILE_GUIDE = "以下是当前用户画像 USER.md。它是主用户画像来源；若与 MEMORY.md 中的用户信息冲突，优先参考 USER.md。"
    _MEMORY_GUIDE = "以下是共享长期记忆索引 MEMORY.md，请将其中信息视为长期上下文，仅在相关时使用。"
    _MEMORY_BACKGROUND_GUIDE = "以下是共享长期记忆索引 MEMORY.md，仅作为补充背景，不是用户画像主来源。"
    _TOOL_DIRECTORY_GUIDE = "以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。"

    # Skills 用法指导 + 启动时扫描的预置技能列表
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

    def _build_user_profile_section(self, user_profile_markdown, enable_cache=True):
        user_text = self._normalize_text(user_profile_markdown)
        if not user_text:
            return None
        kwargs = dict(kind="system", title="user_profile",
                      content=f"{self._USER_PROFILE_GUIDE}\n\n{user_text}",
                      metadata={"section_name": "user_profile"})
        if enable_cache:
            kwargs["cache_control"] = {"type": "ephemeral"}
        return PromptSection(**kwargs)

    def _build_memory_section(self, memory_markdown, background_only=False, enable_cache=True):
        memory_text = self._normalize_text(memory_markdown)
        if not memory_text:
            return None
        guide = self._MEMORY_BACKGROUND_GUIDE if background_only else self._MEMORY_GUIDE
        kwargs = dict(kind="system", title="long_term_memory",
                      content=f"{guide}\n\n{memory_text}",
                      metadata={"section_name": "long_term_memory"})
        if enable_cache:
            kwargs["cache_control"] = {"type": "ephemeral"}
        return PromptSection(**kwargs)

    def _build_tool_directory_section(self, tool_definitions):
        if not tool_definitions:
            return None
        return PromptSection(
            kind="system", title="tool_directory",
            content=f"{self._TOOL_DIRECTORY_GUIDE}\n\n{self._json_text(tool_definitions)}",
            metadata={"section_name": "tool_directory"},
            cache_control={"type": "ephemeral"},
        )

    def _build_skills_section(self):
        skills_text = self._SKILLS_GUIDE.strip()
        if not skills_text:
            return None
        return PromptSection(
            kind="system", title="skills",
            content=skills_text,
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

        tool_call = ToolCall.from_record(record)
        if tool_call is not None:
            section = tool_call.to_section()
            metadata = dict(section.metadata)
            if record.get("timestamp"):
                metadata["timestamp"] = record.get("timestamp")
            if record.get("id"):
                metadata["id"] = record.get("id")
            kwargs = dict(kind=section.kind, title=section.title,
                          content=section.content, metadata=metadata)
            if enable_cache:
                kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**kwargs)

        role = str(record.get("role") or "").strip()
        content = record.get("content", "")
        timestamp = record.get("timestamp")

        if role == "tool":
            metadata = {
                "tool_name": record.get("tool_name") or record.get("name"),
                "name": record.get("name"),
            }
            if timestamp:
                metadata["timestamp"] = timestamp
            if record.get("id"):
                metadata["id"] = record.get("id")
            kwargs = dict(kind="tool_result",
                          title=str(record.get("name") or record.get("tool_name") or "tool_result"),
                          content=content, metadata=metadata)
            if enable_cache:
                kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**kwargs)

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
        self, history, soul_markdown="", user_profile_markdown="",
        memory_markdown="", extra_system_prompt=None, legacy_system_prompt=None,
        tool_definitions=None,
    ):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("actor"))

        soul_section = self._build_soul_section(soul_markdown)
        if soul_section is not None:
            document.add_system(soul_section)

        user_profile_section = self._build_user_profile_section(user_profile_markdown, enable_cache=True)
        if user_profile_section is not None:
            document.add_system(user_profile_section)

        legacy_prompt = self._normalize_text(legacy_system_prompt)
        if legacy_prompt:
            document.add_system(PromptSection(
                kind="system", title="legacy_actor_system_prompt", content=legacy_prompt,
                metadata={"section_name": "legacy_actor_system_prompt"},
            ))

        memory_section = self._build_memory_section(memory_markdown, enable_cache=True)
        if memory_section is not None:
            document.add_system(memory_section)

        skills_section = self._build_skills_section()
        if skills_section is not None:
            document.add_system(skills_section)

        for record in history or []:
            section = self.record_to_section(record)
            if section is not None:
                document.add_conversation(section)

        extra_prompt = self._normalize_text(extra_system_prompt)
        if extra_prompt:
            document.add_conversation(PromptSection(
                kind="system", title="extra_instruction", content=extra_prompt,
                metadata={"section_name": "extra_instruction"},
            ))

        return document

    def build_day_summary_document(self, history, old_memory):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))
        document.extend_conversation([
            PromptSection(kind="user", title="day_history",
                          content="以下是某一天的历史对话：\n\n" + self._json_text(history or []),
                          metadata={"section_name": "day_history"}),
            PromptSection(kind="user", title="long_term_memory_index",
                          content="以下是当前长期记忆索引与 topic 元数据：\n\n" + self._json_text(old_memory or {}),
                          metadata={"section_name": "long_term_memory_index"}),
            PromptSection(kind="user", title="summary_task",
                          content="请根据任务A（日总结）的格式，生成该日的长期记忆摘要 JSON。",
                          metadata={"section_name": "summary_task"}),
        ])
        return document

    def build_topic_merge_document(self, memory_index, topic_docs):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))
        document.extend_conversation([
            PromptSection(kind="user", title="memory_index",
                          content="以下是当前 MEMORY.md 索引：\n\n" + self._json_text(memory_index or {}),
                          metadata={"section_name": "memory_index"}),
            PromptSection(kind="user", title="topic_documents",
                          content="以下是当前所有 topic 文档的结构化内容：\n\n" + self._json_text(topic_docs or []),
                          metadata={"section_name": "topic_documents"}),
            PromptSection(kind="user", title="merge_task",
                          content="请根据任务B（话题合并）的格式，输出 topic 合并建议 JSON。",
                          metadata={"section_name": "merge_task"}),
        ])
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

    def build_review_document(self, turn_context, user_profile_markdown="", memory_markdown=""):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("reviewer"))

        user_text = self._normalize_text(user_profile_markdown)
        memory_text = self._normalize_text(memory_markdown)

        document.extend_conversation([
            PromptSection(kind="user", title="turn_transcript",
                          content="以下是本轮 turn transcript 的结构化 JSON：\n\n" + self._json_text(turn_context or []),
                          metadata={"section_name": "turn_transcript"}),
            PromptSection(kind="user", title="current_user_profile",
                          content="以下是当前 USER.md 内容：\n\n" + (user_text or "(empty)"),
                          metadata={"section_name": "current_user_profile"}),
        ])

        if memory_text:
            document.add_conversation(PromptSection(
                kind="user", title="long_term_memory_background",
                content="以下是当前 MEMORY.md 内容，仅作补充背景：\n\n" + memory_text,
                metadata={"section_name": "long_term_memory_background"},
            ))

        document.add_conversation(PromptSection(
            kind="user", title="review_task",
            content="请根据以上内容，输出本轮用户画像复盘 JSON。",
            metadata={"section_name": "review_task"},
        ))
        return document
