import json
from pathlib import Path

from Memory.ToolCall import ToolCall
from Prompting.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Prompting.PromptSection import PromptSection


class PromptAssembler:
    """提示词组装器 - 从外部文件加载所有提示词"""

    def __init__(self, prompt_loader=None):
        self.prompt_loader = prompt_loader or PromptLoader()
        self._prompts_cache = {}
        self._prompts_dir = Path(__file__).parent.parent / "prompts" / "system"

    def _load_prompt(self, filename: str) -> str:
        """从文件加载提示词并缓存"""
        if filename not in self._prompts_cache:
            prompt_path = self._prompts_dir / filename
            try:
                self._prompts_cache[filename] = prompt_path.read_text(encoding='utf-8')
            except FileNotFoundError:
                raise FileNotFoundError(f"提示词文件未找到: {prompt_path}")
        return self._prompts_cache[filename]

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
        prompt = self._load_prompt("soul_section.md")
        return PromptSection(
            kind="system",
            title="soul",
            content=f"{prompt}\n\n{soul_text}",
            metadata={"section_name": "soul"},
            cache_control={"type": "ephemeral"},  # 缓存 SOUL
        )

    def _build_user_profile_section(self, user_profile_markdown, enable_cache=True):
        user_text = self._normalize_text(user_profile_markdown)
        if not user_text:
            return None
        prompt = self._load_prompt("user_profile.md")
        section_kwargs = {
            "kind": "system",
            "title": "user_profile",
            "content": f"{prompt}\n\n{user_text}",
            "metadata": {"section_name": "user_profile"},
        }
        if enable_cache:
            section_kwargs["cache_control"] = {"type": "ephemeral"}
        return PromptSection(**section_kwargs)

    def _build_memory_section(self, memory_markdown, background_only=False, enable_cache=True):
        memory_text = self._normalize_text(memory_markdown)
        if not memory_text:
            return None
        prompt_file = "memory_background.md" if background_only else "memory.md"
        prompt = self._load_prompt(prompt_file)
        section_kwargs = {
            "kind": "system",
            "title": "long_term_memory",
            "content": f"{prompt}\n\n{memory_text}",
            "metadata": {"section_name": "long_term_memory"},
        }
        if enable_cache:
            section_kwargs["cache_control"] = {"type": "ephemeral"}
        return PromptSection(**section_kwargs)

    def _build_tool_directory_section(self, tool_definitions):
        if not tool_definitions:
            return None
        prompt = self._load_prompt("tool_directory.md")
        return PromptSection(
            kind="system",
            title="tool_directory",
            content=f"{prompt}\n\n{self._json_text(tool_definitions)}",
            metadata={"section_name": "tool_directory"},
            cache_control={"type": "ephemeral"},  # 缓存工具定义
        )

    @staticmethod
    def record_to_section(record, enable_cache=False):
        if isinstance(record, ToolCall):
            section = record.to_section()
            if enable_cache:
                return PromptSection(
                    kind=section.kind,
                    title=section.title,
                    content=section.content,
                    metadata=section.metadata,
                    cache_control={"type": "ephemeral"},
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
            section_kwargs = {
                "kind": section.kind,
                "title": section.title,
                "content": section.content,
                "metadata": metadata,
            }
            if enable_cache:
                section_kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**section_kwargs)

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
            section_kwargs = {
                "kind": "tool_result",
                "title": str(record.get("name") or record.get("tool_name") or "tool_result"),
                "content": content,
                "metadata": metadata,
            }
            if enable_cache:
                section_kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**section_kwargs)

        if role in {"user", "assistant", "system"}:
            metadata = {}
            if timestamp:
                metadata["timestamp"] = timestamp
            section_kwargs = {
                "kind": role,
                "title": role,
                "content": content,
                "metadata": metadata,
            }
            if enable_cache:
                section_kwargs["cache_control"] = {"type": "ephemeral"}
            return PromptSection(**section_kwargs)

        raise ValueError(f"Unsupported history role: {role}")

    def build_actor_document(
        self,
        history,
        soul_markdown="",
        user_profile_markdown="",
        memory_markdown="",
        extra_system_prompt=None,
        legacy_system_prompt=None,
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
                kind="system",
                title="legacy_actor_system_prompt",
                content=legacy_prompt,
                metadata={"section_name": "legacy_actor_system_prompt"},
            ))

        memory_section = self._build_memory_section(memory_markdown, enable_cache=True)
        if memory_section is not None:
            document.add_system(memory_section)

        # Actor 路径仅缓存稳定 system 前缀；所有历史消息保持原顺序但不打缓存标记。
        for record in history or []:
            section = self.record_to_section(record)
            if section is not None:
                document.add_conversation(section)

        extra_prompt = self._normalize_text(extra_system_prompt)
        if extra_prompt:
            document.add_conversation(PromptSection(
                kind="system",
                title="extra_instruction",
                content=extra_prompt,
                metadata={"section_name": "extra_instruction"},
            ))

        return document

    def build_reflection_document(self, question, history, memory_markdown="", soul_markdown=""):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("reflection"))

        soul_section = self._build_soul_section(soul_markdown)
        if soul_section is not None:
            document.add_system(soul_section)

        memory_section = self._build_memory_section(memory_markdown)
        if memory_section is not None:
            document.add_system(memory_section)

        reflection_question_prompt = self._load_prompt("reflection_question.md")
        reflection_history_prompt = self._load_prompt("reflection_history.md")
        reflection_task_prompt = self._load_prompt("reflection_task.md")

        document.extend_conversation([
            PromptSection(
                kind="user",
                title="original_question",
                content=f"{reflection_question_prompt}\n\n{self._normalize_text(question)}",
                metadata={"section_name": "original_question"},
            ),
            PromptSection(
                kind="user",
                title="recent_history",
                content=f"{reflection_history_prompt}\n\n{self._json_text(history or [])}",
                metadata={"section_name": "recent_history"},
            ),
            PromptSection(
                kind="user",
                title="reflection_task",
                content=reflection_task_prompt,
                metadata={"section_name": "reflection_task"},
            ),
        ])
        return document

    def build_day_summary_document(self, history, old_memory):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))

        day_summary_contract_prompt = self._load_prompt("day_summary_contract.md")
        document.add_system(PromptSection(
            kind="system",
            title="day_summary_contract",
            content=day_summary_contract_prompt,
            metadata={"section_name": "day_summary_contract"},
        ))
        document.extend_conversation([
            PromptSection(
                kind="user",
                title="day_history",
                content="以下是某一天的历史对话：\n\n" + self._json_text(history or []),
                metadata={"section_name": "day_history"},
            ),
            PromptSection(
                kind="user",
                title="long_term_memory_index",
                content="以下是当前长期记忆索引与 topic 元数据：\n\n" + self._json_text(old_memory or {}),
                metadata={"section_name": "long_term_memory_index"},
            ),
            PromptSection(
                kind="user",
                title="summary_task",
                content="请根据以上内容，生成该日的长期记忆摘要 JSON。",
                metadata={"section_name": "summary_task"},
            ),
        ])
        return document

    def build_topic_merge_document(self, memory_index, topic_docs):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))

        topic_merge_contract_prompt = self._load_prompt("topic_merge_contract.md")
        document.add_system(PromptSection(
            kind="system",
            title="topic_merge_contract",
            content=topic_merge_contract_prompt,
            metadata={"section_name": "topic_merge_contract"},
        ))
        document.extend_conversation([
            PromptSection(
                kind="user",
                title="memory_index",
                content="以下是当前 MEMORY.md 索引：\n\n" + self._json_text(memory_index or {}),
                metadata={"section_name": "memory_index"},
            ),
            PromptSection(
                kind="user",
                title="topic_documents",
                content="以下是当前所有 topic 文档的结构化内容：\n\n" + self._json_text(topic_docs or []),
                metadata={"section_name": "topic_documents"},
            ),
            PromptSection(
                kind="user",
                title="merge_task",
                content="请输出 topic 合并建议 JSON。",
                metadata={"section_name": "merge_task"},
            ),
        ])
        return document

    def build_context_compact_document(self, conversation, system_prompt=None):
        """构建上下文压缩 prompt 文档 — 按 9 点框架摘要对话历史"""
        document = PromptDocument()
        # 复用 summarizer 的 system sections
        document.extend_system(self._load_agent_system_sections("summarizer"))

        compact_prompt = self._load_prompt("context_compact.md")
        document.add_system(PromptSection(
            kind="system",
            title="context_compact_contract",
            content=compact_prompt,
            metadata={"section_name": "context_compact_contract"},
        ))

        sections = [
            PromptSection(
                kind="user",
                title="conversation_history",
                content="以下是需要压缩的对话历史：\n\n" + self._json_text(conversation),
                metadata={"section_name": "conversation_history"},
            ),
        ]

        if system_prompt:
            sections.append(PromptSection(
                kind="user",
                title="current_system_prompt",
                content="当前系统提示词（仅做背景参考，不需要重复摘要）：\n\n" + system_prompt,
                metadata={"section_name": "current_system_prompt"},
            ))

        sections.append(PromptSection(
            kind="user",
            title="compact_task",
            content="请按上述 9 点框架输出结构化摘要，直接作为最终输出。",
            metadata={"section_name": "compact_task"},
        ))

        document.extend_conversation(sections)
        return document

    def build_review_document(self, turn_context, user_profile_markdown="", memory_markdown=""):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("reviewer"))

        user_profile_text = self._normalize_text(user_profile_markdown)
        memory_text = self._normalize_text(memory_markdown)
        review_turn_prompt = self._load_prompt("review_turn.md")

        document.extend_conversation([
            PromptSection(
                kind="user",
                title="turn_transcript",
                content="以下是本轮 turn transcript 的结构化 JSON：\n\n" + self._json_text(turn_context or []),
                metadata={"section_name": "turn_transcript"},
            ),
            PromptSection(
                kind="user",
                title="current_user_profile",
                content="以下是当前 USER.md 内容：\n\n" + (user_profile_text or "(empty)"),
                metadata={"section_name": "current_user_profile"},
            ),
        ])

        if memory_text:
            document.add_conversation(PromptSection(
                kind="user",
                title="long_term_memory_background",
                content="以下是当前 MEMORY.md 内容，仅作补充背景：\n\n" + memory_text,
                metadata={"section_name": "long_term_memory_background"},
            ))

        document.add_conversation(PromptSection(
            kind="user",
            title="review_task",
            content=review_turn_prompt,
            metadata={"section_name": "review_task"},
        ))
        return document
