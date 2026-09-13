"""
Provider 协议与归一化响应载体。

每个 provider 实现三件事：
- create_client(llm_config)  -> SDK client
- build_request(client, messages, params, tools, tool_choice) -> ProviderRequest
- normalize_response(raw)    -> NormalizedResponse
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class NormalizedResponse:
    """跨 provider 统一的响应形态。content 为中立块 dict 列表。"""

    content: List[dict]
    stop_reason: Optional[str]  # 归一化为 end_turn | max_tokens | tool_use | 其他原样
    usage: Optional[Dict[str, int]]  # input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens
    raw: Any = None

    def model_dump(self):
        return {
            "content": self.content,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
        }


@dataclass
class ProviderRequest:
    """一次调用的完整载体：wire 为可序列化的请求 dict（供日志），call 执行真实请求。"""

    wire: Dict[str, Any]
    call: Callable[[], Any]


def normalize_incoming_message(message):
    """入口规范化：ToolCall 实例 → record dict；dict 浅拷贝；其余报错。"""
    from Core.ToolCall import ToolCall

    if isinstance(message, ToolCall):
        return message.to_record()
    if isinstance(message, dict):
        normalized = dict(message)
        content = normalized.get("content")
        if isinstance(content, list):
            normalized["content"] = list(content)
        return normalized
    raise TypeError("LLM messages must be dict or ToolCall")


def stringify_content(content):
    """任意内容 → 安全 utf-8 字符串。"""
    if isinstance(content, str):
        return content.encode("utf-8", errors="replace").decode("utf-8")
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, default=str).encode(
            "utf-8", errors="replace"
        ).decode("utf-8")
    except TypeError:
        return str(content).encode("utf-8", errors="replace").decode("utf-8")


def extract_text(response: NormalizedResponse) -> str:
    parts = [b.get("text", "") for b in response.content if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def extract_thinking(response: NormalizedResponse) -> Optional[str]:
    parts = [b.get("thinking", "") for b in response.content if b.get("type") == "thinking"]
    combined = "\n".join(p for p in parts if p).strip()
    return combined or None


class LLMProvider:
    """Provider 基类。supports_native_tools=False 的 provider 不允许带 tools 调用。"""

    name = "base"
    supports_native_tools = True

    def create_client(self, llm_config: dict):
        raise NotImplementedError

    def probe(self, client, llm_config: dict):
        """最小真实调用，用于 auto 模式下探测该协议是否可用。失败抛异常。"""
        raise NotImplementedError

    def build_request(self, client, messages, params, tools=None, tool_choice=None) -> ProviderRequest:
        raise NotImplementedError

    def normalize_response(self, raw) -> NormalizedResponse:
        raise NotImplementedError
