import json
import re
from dataclasses import dataclass

from Prompting.PromptSection import PromptSection


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
            arguments = payload.get("input")  # 支持 Anthropic 格式
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        if not isinstance(arguments, dict):
            return None

        return cls(tool_name=tool_name.strip(), arguments=dict(arguments))

    @staticmethod
    def _extract_balanced_object(text, start_index):
        depth = 0
        in_string = False
        escape = False
        for index in range(start_index, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_index:index + 1]
        return None

    @classmethod
    def _try_from_minimax_syntax(cls, text):
        """
        支持 MiniMax 模型的特殊格式（容错机制）
        Format: <minimax:tool_call>{"tool": "...", "arguments": {...}}</minimax:tool_call>
        """
        match = re.search(
            r'<minimax:tool_call>\s*(\{.*?\})\s*</minimax:tool_call>',
            text,
            re.DOTALL
        )
        if match:
            try:
                payload = json.loads(match.group(1))
                tool_call = cls._from_payload(payload)
                if tool_call is not None:
                    return tool_call
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        return None

    @classmethod
    def try_from_text(cls, text):
        """
        从文本中提取工具调用

        支持的格式：
        1. 标准 JSON（推荐）: {"tool": "...", "arguments": {...}}
        2. MiniMax 标签（容错）: <minimax:tool_call>{"tool": "...", ...}</minimax:tool_call>
        """
        if not isinstance(text, str):
            return None

        stripped = text.strip()
        if not stripped:
            return None

        # 优先尝试 MiniMax 特殊格式（某些模型会自动添加）
        minimax_call = cls._try_from_minimax_syntax(stripped)
        if minimax_call is not None:
            return minimax_call

        # 标准 JSON 格式解析
        candidates = [stripped]

        # 提取 markdown 代码块中的 JSON（容错）
        candidates.extend(match.group(1).strip() for match in re.finditer(
            r"```(?:json|JSON)?\s*(\{.*?\})\s*```",
            stripped,
            re.DOTALL,
        ))

        # 提取所有可能的 JSON 对象
        for match in re.finditer(r"\{", stripped):
            candidate = cls._extract_balanced_object(stripped, match.start())
            if candidate is not None and (
                ('"tool"' in candidate and '"arguments"' in candidate)
                or ('"name"' in candidate and '"parameters"' in candidate)
                or ('"name"' in candidate and '"input"' in candidate)  # 支持 Anthropic 格式
            ):
                candidates.append(candidate)

        # 尝试解析每个候选
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                payload = json.loads(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            tool_call = cls._from_payload(payload)
            if tool_call is not None:
                return tool_call

        return None

    @classmethod
    def try_all_from_text(cls, text):
        """
        从文本中提取所有工具调用（支持多个工具调用）

        返回: List[ToolCall] 或 None（如果没有找到任何工具调用）
        """
        if not isinstance(text, str):
            return None

        stripped = text.strip()
        if not stripped:
            return None

        tool_calls = []

        # 1. 尝试解析为JSON数组 [{"tool": "...", "arguments": {...}}, ...]
        try:
            # 查找JSON数组
            array_match = re.search(r'\[\s*\{.*?\}\s*\]', stripped, re.DOTALL)
            if array_match:
                array_text = array_match.group(0)

                # 修复 Windows 路径中的反斜杠问题
                # 将单个反斜杠（不是有效的转义序列）替换为双反斜杠
                array_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', array_text)

                payloads = json.loads(array_text)
                if isinstance(payloads, list):
                    for payload in payloads:
                        tool_call = cls._from_payload(payload)
                        if tool_call is not None:
                            tool_calls.append(tool_call)
                    if tool_calls:
                        return tool_calls
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        # 2. 提取所有独立的JSON对象
        seen = set()
        for match in re.finditer(r"\{", stripped):
            candidate = cls._extract_balanced_object(stripped, match.start())
            if candidate is None:
                continue
            if candidate in seen:
                continue
            if not (('"tool"' in candidate and '"arguments"' in candidate)
                    or ('"name"' in candidate and '"parameters"' in candidate)
                    or ('"name"' in candidate and '"input"' in candidate)):  # 支持 Anthropic 格式
                continue

            seen.add(candidate)
            try:
                # 修复反斜杠问题
                fixed_candidate = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', candidate)
                payload = json.loads(fixed_candidate)
                tool_call = cls._from_payload(payload)
                if tool_call is not None:
                    tool_calls.append(tool_call)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        return tool_calls if tool_calls else None

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

            content_call = cls.try_from_text(record.get("content", ""))
            if content_call is not None:
                return content_call
            return None

        if record.get("role") == "assistant":
            return cls.try_from_text(record.get("content", ""))

        return None

    @staticmethod
    def is_record(record) -> bool:
        return ToolCall.from_record(record) is not None

    def to_payload(self):
        return self._canonical_payload(self.tool_name, self.arguments)

    def to_json_text(self):
        return json.dumps(self.to_payload(), ensure_ascii=False)

    def to_record(self, timestamp=None):
        record = {
            "role": "assistant",
            "message_type": "tool_call",
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "content": self.to_json_text(),
        }
        if timestamp is not None:
            record["timestamp"] = timestamp
        return record

    def to_prompt_message(self):
        return {
            "role": "assistant",
            "content": self.to_json_text(),
        }

    def to_section(self):
        return PromptSection(
            kind="tool_call",
            title=self.tool_name,
            content=self.to_json_text(),
            metadata={
                "tool_name": self.tool_name,
                "arguments": dict(self.arguments),
            },
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
