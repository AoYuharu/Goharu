import json

from Memory.ToolCall import ToolCall
from Prompting.PromptDocument import PromptDocument


class PromptRenderer:
    @staticmethod
    def _stringify_content(content):
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except TypeError:
            return str(content)

    def render_document(self, document: PromptDocument):
        return self.render_sections(document.all_sections())

    def render_sections(self, sections):
        messages = []
        for section in sections or []:
            rendered = self.render_section(section)
            if rendered is not None:
                messages.append(rendered)
        return messages

    def render_section(self, section):
        raw_content = section.content
        # 保留原生 Anthropic 列表块（避免 JSON 序列化后流失 tool_use_id）
        if isinstance(raw_content, list):
            content = raw_content
        else:
            content = self._stringify_content(raw_content).strip()
        if not content and section.kind != "tool_result":
            return None

        if section.kind in {"system", "user", "assistant"}:
            message = {
                "role": section.kind,
                "content": content,
            }
            # 传递 cache_control（用于 Anthropic Prompt Caching）
            if section.cache_control:
                message["cache_control"] = section.cache_control
            return message

        if section.kind == "tool_call":
            tool_name = section.metadata.get("tool_name")
            arguments = section.metadata.get("arguments")
            if isinstance(tool_name, str) and isinstance(arguments, dict):
                return ToolCall(tool_name=tool_name, arguments=arguments).to_prompt_message()

            tool_call = ToolCall.try_from_text(content)
            if tool_call is not None:
                return tool_call.to_prompt_message()

            return {
                "role": "assistant",
                "content": content,
            }

        if section.kind == "tool_result":
            tool_name = section.metadata.get("tool_name") or section.metadata.get("name")
            message = {
                "role": "tool",
                "content": content,
            }
            if tool_name:
                message["name"] = str(tool_name)
            return message

        raise ValueError(f"Unsupported prompt section kind: {section.kind}")
