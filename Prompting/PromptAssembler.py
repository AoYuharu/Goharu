import json

from Memory.ToolCall import ToolCall
from Prompting.PromptDocument import PromptDocument
from Prompting.PromptLoader import PromptLoader
from Prompting.PromptSection import PromptSection

SOUL_SECTION_PROMPT = "以下是稳定角色设定 SOUL.md，请将其视为高优先级的长期风格与行为边界指导。"
USER_PROFILE_PROMPT = "以下是当前用户画像 USER.md。它是主用户画像来源；若与 MEMORY.md 中的用户信息冲突，优先参考 USER.md。"
LONGTERM_MEMORY_PROMPT = "以下是共享长期记忆索引 MEMORY.md，请将其中信息视为长期上下文，仅在相关时使用。"
LONGTERM_MEMORY_BACKGROUND_PROMPT = "以下是共享长期记忆索引 MEMORY.md，仅作为补充背景，不是用户画像主来源。"
TOOL_DIRECTORY_PROMPT = "以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。"

REFLECTION_QUESTION_PROMPT = "以下是用户的原始问题，请围绕它判断当前信息是否已经足够。"
REFLECTION_HISTORY_PROMPT = "以下是最近对话与工具调用历史的结构化 JSON。"
REFLECTION_TASK_PROMPT = """
请判断：
1. 当前信息是否足以回答原始问题？
2. 是否存在明显错误、遗漏或不合理的工具使用？
3. 下一步建议：
- 如果信息不足，请明确输出短语“需要继续调用工具”，并说明原因
- 如果信息足够，请明确输出短语“可以给出最终回答”

你必须原样保留以下两个短语之一：
- 需要继续调用工具
- 可以给出最终回答

请用简洁、理性的自然语言回答，不要省略上述关键短语。
""".strip()

DAY_SUMMARY_CONTRACT_PROMPT = """
你的任务是把“某一天已经过期的对话记录”压缩成可以写入长期记忆系统的严格 JSON。
请只保留长期有价值、可复用、可检索的信息，忽略寒暄和一次性噪声。

请严格输出且仅输出 JSON，格式必须为：
{
  "profile_updates": {"字段": "值"},
  "important_facts": ["事实1", "事实2"],
  "conversation_summary": "不超过 120 字的摘要",
  "topics": [
    {
      "slug": "topic-slug",
      "title": "Topic Title",
      "action": "create",
      "summary": "该话题的长期摘要",
      "keywords": ["关键词1", "关键词2"],
      "facts": ["关键事实1", "关键事实2"],
      "open_questions": ["未解决问题"]
    }
  ]
}

约束：
1. action 只能是 create、update、ignore 之一
2. topics 中只保留值得进入长期主题记忆的话题
3. 不要输出重复事实
4. 不要输出 markdown 代码块
5. 不要在 JSON 前后添加任何解释文字
6. 输出结果必须可以被 json.loads 直接解析
""".strip()

TOPIC_MERGE_CONTRACT_PROMPT = """
你的任务是识别高度重叠、应该合并的 topic 文档，并输出严格 JSON。
如果没有需要合并的 topic，也必须输出合法 JSON。

请严格输出且仅输出 JSON，格式必须为：
{
  "merge_groups": [
    {
      "canonical_slug": "保留的 slug",
      "merged_slugs": ["待合并 slug"],
      "title": "合并后的标题",
      "summary": "合并后的摘要",
      "keywords": ["关键词1", "关键词2"],
      "facts": ["需要补充保留的事实"],
      "open_questions": ["合并后仍未解决的问题"]
    }
  ]
}

约束：
1. 只有在两个或更多 topic 明显属于同一长期主题时才输出 merge group
2. canonical_slug 不能同时出现在 merged_slugs 中
3. 如果没有任何应该合并的 topic，输出 {"merge_groups": []}
4. 不要输出 markdown 代码块
5. 不要在 JSON 前后添加任何解释文字
6. 输出结果必须可以被 json.loads 直接解析
""".strip()

REVIEW_TURN_PROMPT = "请根据以上内容，输出本轮用户画像复盘 JSON。"


class PromptAssembler:
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
            kind="system",
            title="soul",
            content=f"{SOUL_SECTION_PROMPT}\n\n{soul_text}",
            metadata={"section_name": "soul"},
            cache_control={"type": "ephemeral"},  # 缓存 SOUL
        )

    def _build_user_profile_section(self, user_profile_markdown, enable_cache=True):
        user_text = self._normalize_text(user_profile_markdown)
        if not user_text:
            return None
        section_kwargs = {
            "kind": "system",
            "title": "user_profile",
            "content": f"{USER_PROFILE_PROMPT}\n\n{user_text}",
            "metadata": {"section_name": "user_profile"},
        }
        if enable_cache:
            section_kwargs["cache_control"] = {"type": "ephemeral"}
        return PromptSection(**section_kwargs)

    def _build_memory_section(self, memory_markdown, background_only=False, enable_cache=True):
        memory_text = self._normalize_text(memory_markdown)
        if not memory_text:
            return None
        prompt = LONGTERM_MEMORY_BACKGROUND_PROMPT if background_only else LONGTERM_MEMORY_PROMPT
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
        return PromptSection(
            kind="system",
            title="tool_directory",
            content=f"{TOOL_DIRECTORY_PROMPT}\n\n{self._json_text(tool_definitions)}",
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

        tool_directory_section = self._build_tool_directory_section(tool_definitions)
        if tool_directory_section is not None:
            document.add_system(tool_directory_section)

        # 处理历史对话：除最新一轮外都缓存
        history_list = list(history or [])
        if history_list:
            # 找到最后一个用户消息的位置，作为最新一轮的起点
            last_user_idx = -1
            for i in range(len(history_list) - 1, -1, -1):
                record = history_list[i]
                role = record.get("role") if isinstance(record, dict) else getattr(record, "role", None)
                if role == "user":
                    last_user_idx = i
                    break

            # 最新一轮之前的所有消息都缓存
            for i, record in enumerate(history_list):
                enable_cache = (last_user_idx >= 0 and i < last_user_idx)
                section = self.record_to_section(record, enable_cache=enable_cache)
                if section is not None:
                    document.add_conversation(section)
        else:
            document.extend_conversation(self.record_to_section(record) for record in (history or []))

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

        document.extend_conversation([
            PromptSection(
                kind="user",
                title="original_question",
                content=f"{REFLECTION_QUESTION_PROMPT}\n\n{self._normalize_text(question)}",
                metadata={"section_name": "original_question"},
            ),
            PromptSection(
                kind="user",
                title="recent_history",
                content=f"{REFLECTION_HISTORY_PROMPT}\n\n{self._json_text(history or [])}",
                metadata={"section_name": "recent_history"},
            ),
            PromptSection(
                kind="user",
                title="reflection_task",
                content=REFLECTION_TASK_PROMPT,
                metadata={"section_name": "reflection_task"},
            ),
        ])
        return document

    def build_day_summary_document(self, history, old_memory):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("summarizer"))
        document.add_system(PromptSection(
            kind="system",
            title="day_summary_contract",
            content=DAY_SUMMARY_CONTRACT_PROMPT,
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
        document.add_system(PromptSection(
            kind="system",
            title="topic_merge_contract",
            content=TOPIC_MERGE_CONTRACT_PROMPT,
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

    def build_review_document(self, turn_context, user_profile_markdown="", memory_markdown=""):
        document = PromptDocument()
        document.extend_system(self._load_agent_system_sections("reviewer"))

        user_profile_text = self._normalize_text(user_profile_markdown)
        memory_text = self._normalize_text(memory_markdown)

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
            content=REVIEW_TURN_PROMPT,
            metadata={"section_name": "review_task"},
        ))
        return document
