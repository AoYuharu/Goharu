import json
from dataclasses import dataclass

from Core.PromptSection import PromptSection


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict

    @staticmethod
    def _canonical_payload(tool_name, arguments):
        return {
            "tool": str(tool_name),
            "arguments": dict(arguments),
        }

    @classmethod
    def _from_payload(cls, payload):
        if not isinstance(payload, dict):
            return None

        tool_name = payload.get("tool") or payload.get("name")
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = payload.get("parameters")
        if arguments is None:
            arguments = payload.get("input")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        if not isinstance(arguments, dict):
            return None

        return cls(tool_name=tool_name.strip(), arguments=dict(arguments))

    @classmethod
    def from_record(cls, record):
        if isinstance(record, cls):
            return record
        if not isinstance(record, dict):
            return None

        if record.get("message_type") == "tool_call":
            tool_name = record.get("tool_name")
            arguments = record.get("arguments")
            if isinstance(tool_name, str) and tool_name.strip() and isinstance(arguments, dict):
                return cls(tool_name=tool_name.strip(), arguments=dict(arguments))

        content = record.get("content")
        if record.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                tool_call = cls.from_anthropic_tool_use(block)
                if tool_call is not None:
                    return tool_call

        return None

    @staticmethod
    def is_record(record) -> bool:
        return ToolCall.from_record(record) is not None

    def to_payload(self):
        return self._canonical_payload(self.tool_name, self.arguments)

    def to_record(self, timestamp=None, tool_use_id=None):
        content = [self.to_anthropic_tool_use(tool_use_id=tool_use_id)]
        record = {
            "role": "assistant",
            "message_type": "tool_call",
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "content": content,
        }
        if timestamp is not None:
            record["timestamp"] = timestamp
        return record

    def to_section(self, message_id=None, tool_use_id=None):
        content = [self.to_anthropic_tool_use(tool_use_id=tool_use_id)]
        metadata = {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
        }
        if message_id:
            metadata["id"] = message_id
        return PromptSection(
            kind="tool_call",
            title=self.tool_name,
            content=content,
            metadata=metadata,
        )

    @classmethod
    def from_anthropic_tool_use(cls, tool_use_block):
        """
        从 Anthropic 原生 tool_use 块创建 ToolCall

        Args:
            tool_use_block: Anthropic API 返回的 tool_use 块
                {
                    "type": "tool_use",
                    "id": "toolu_xxx",
                    "name": "tool_name",
                    "input": {...}
                }
        """
        if not isinstance(tool_use_block, dict):
            return None

        if tool_use_block.get("type") != "tool_use":
            return None

        tool_name = tool_use_block.get("name")
        arguments = tool_use_block.get("input")

        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        if not isinstance(arguments, dict):
            return None

        return cls(tool_name=tool_name.strip(), arguments=dict(arguments))

    def to_anthropic_tool_use(self, tool_use_id=None):
        """
        转换为 Anthropic 原生 tool_use 块格式

        Args:
            tool_use_id: 工具调用的唯一ID，如果为None则自动生成

        Returns:
            {
                "type": "tool_use",
                "id": "toolu_xxx",
                "name": "tool_name",
                "input": {...}
            }
        """
        import uuid
        if tool_use_id is None:
            tool_use_id = f"toolu_{uuid.uuid4().hex[:24]}"

        return {
            "type": "tool_use",
            "id": tool_use_id,
            "name": self.tool_name,
            "input": dict(self.arguments),
        }

    @staticmethod
    def create_anthropic_tool_result(tool_use_id, content, is_error=False):
        """
        创建 Anthropic 原生 tool_result 块

        Args:
            tool_use_id: 对应的 tool_use 的 ID
            content: 工具执行结果（字符串或字典）
            is_error: 是否为错误结果

        Returns:
            {
                "type": "tool_result",
                "tool_use_id": "toolu_xxx",
                "content": "...",
                "is_error": false
            }
        """
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(content),
            "is_error": is_error,
        }

    @staticmethod
    def classify_is_error(tool_name, result_text):
        """Parse result_text to determine if the tool execution resulted in an error.

        Handles structured JSON results (run_cmd with exit_code/timed_out/interrupted),
        generic {"error": ...} patterns, and plain-text error prefixes.
        """
        try:
            parsed = json.loads(result_text)
            if isinstance(parsed, dict):
                if "exit_code" in parsed:
                    if parsed.get("exit_code", 0) != 0:
                        return True
                    if parsed.get("timed_out", False):
                        return True
                    if parsed.get("interrupted", False):
                        return True
                    return False
                if "error" in parsed and parsed["error"]:
                    return True
                if parsed.get("backgrounded"):
                    return False
        except (json.JSONDecodeError, TypeError):
            pass
        if isinstance(result_text, str):
            if result_text.startswith("ERROR:"):
                return True
            if result_text.startswith('{"error":'):
                return True
        return False
