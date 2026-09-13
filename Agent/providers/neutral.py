"""
中立消息格式（Neutral Message Format）—— Goharu 内部消息规范。

内部流通的消息一律为 dict，形状如下：

    {"role": "system" | "user" | "assistant", "content": str | list[Block]}
    可选: "cache_control": {"type": "ephemeral"}   # 仅 Anthropic wire 生效，其他 provider 丢弃

Block 只允许四种类型：

    {"type": "text",        "text": str}
    {"type": "thinking",    "thinking": str}
    {"type": "tool_use",    "id": str, "name": str, "input": dict}
    {"type": "tool_result", "tool_use_id": str, "content": str, "is_error": bool}

Wire 语义约定（翻译器负责落实）：
- system 消息：Anthropic 抽取为顶层 system 块数组；OpenAI 合并为内联 role:"system" 消息
- cache_control：Anthropic 下沉到对应消息最后一个 text 块；OpenAI 直接丢弃（其缓存为自动）
- 配对约束：assistant 的每个 tool_use 必须由后续 user 消息中等同 tool_use_id 的
  tool_result 配对。该约束在中立层由 ActorAgent._sanitize_messages_for_api 维护，
  翻译器假定输入已满足配对完整性。
- 工作记忆 / SQLite 历史存储的即为本格式，两个 provider 共享同一份历史。
"""

from typing import Any, Dict, Literal, Optional, TypedDict


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


class ThinkingBlock(TypedDict):
    type: Literal["thinking"]
    thinking: str


class ToolUseBlock(TypedDict):
    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]


class ToolResultBlock(TypedDict):
    type: Literal["tool_result"]
    tool_use_id: str
    content: str
    is_error: bool


Block = Any  # TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


class NeutralMessage(TypedDict, total=False):
    role: str  # "system" | "user" | "assistant"
    content: Any  # str | List[Block]
    cache_control: Optional[Dict[str, str]]
