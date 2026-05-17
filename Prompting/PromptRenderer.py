import json

from Core.PromptDocument import PromptDocument


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

    @staticmethod
    def _normalize_native_blocks(content):
        if not isinstance(content, list):
            return None
        normalized = []
        for block in content:
            if isinstance(block, dict):
                normalized.append(dict(block))
            elif isinstance(block, str) and block.strip():
                normalized.append({"type": "text", "text": block})
            elif hasattr(block, "model_dump"):
                normalized.append(block.model_dump())
            else:
                raise ValueError(f"Unsupported native content block: {block!r}")
        return normalized

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
        if isinstance(raw_content, list):
            content = self._normalize_native_blocks(raw_content)
        else:
            content = self._stringify_content(raw_content).strip()
        if not content and section.kind != "tool_result":
            return None

        msg_id = section.metadata.get("id")
        if msg_id:
            if isinstance(content, str):
                content = f"[ID:{msg_id}] {content}"
            elif isinstance(content, list):
                content = [{"type": "text", "text": f"[ID:{msg_id}] "}] + list(content)

        if section.kind in {"system", "user", "assistant"}:
            message = {
                "role": section.kind,
                "content": content,
            }
            if section.cache_control:
                message["cache_control"] = section.cache_control
            return message

        if section.kind == "tool_call":
            if not isinstance(content, list):
                raise ValueError("tool_call sections must use native content blocks")
            return {
                "role": "assistant",
                "content": content,
            }

        if section.kind == "tool_result":
            if isinstance(content, list):
                blocks = content
            else:
                blocks = []
                tool_use_id = section.metadata.get("tool_use_id")
                if tool_use_id:
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": bool(section.metadata.get("is_error", False)),
                    })
                elif content:
                    blocks.append({"type": "text", "text": content})
            return {
                "role": "user",
                "content": blocks,
            }

        raise ValueError(f"Unsupported prompt section kind: {section.kind}")
