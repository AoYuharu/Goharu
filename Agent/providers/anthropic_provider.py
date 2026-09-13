"""
Anthropic Messages API provider。

请求：中立消息 → messages + 顶层 system 块数组（cache_control 下沉到 text 块）。
响应：原生 response → NormalizedResponse（content 块 dict 化）。
"""

import logging
import os

from Agent.providers.base import (
    LLMProvider,
    NormalizedResponse,
    ProviderRequest,
    normalize_incoming_message,
    stringify_content,
)

logger = logging.getLogger(__name__)


def _normalize_content_blocks(role, content):
    if not isinstance(content, list):
        raise TypeError(f"{role} message content must be a string or native block list")

    normalized = []
    for block in content:
        if not isinstance(block, dict) and hasattr(block, "model_dump"):
            block = block.model_dump()
        if isinstance(block, str):
            if block.strip():
                normalized.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            raise TypeError(f"Unsupported native content block: {block!r}")

        block_type = block.get("type")
        if block_type == "text":
            text = stringify_content(block.get("text", "")).strip()
            if text:
                normalized.append({**block, "text": text})
        elif block_type == "thinking":
            thinking = stringify_content(block.get("thinking", "")).strip()
            if thinking:
                normalized.append({**block, "thinking": thinking})
        elif block_type in ("tool_use", "tool_result"):
            normalized.append(dict(block))
        else:
            raise ValueError(f"Unsupported native block type: {block_type}")

    return normalized


class AnthropicProvider(LLMProvider):
    name = "anthropic_compatible"

    def create_client(self, llm_config):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is required for provider anthropic_compatible. "
                "Install dependencies from requirements.txt in the active Python environment."
            ) from exc

        api_key_env = llm_config.get("api_key_env")
        if not api_key_env:
            raise ValueError("Missing model.large-language-model.api_key_env in config.yaml")

        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ValueError(
                f"Environment variable {api_key_env} is required for provider anthropic_compatible"
            )

        client_kwargs = {"api_key": api_key, "max_retries": 0}
        base_url = llm_config.get("base_url") or os.getenv("ANTHROPIC_BASE_URL")
        if base_url:
            client_kwargs["base_url"] = base_url

        api_timeout = llm_config.get("api_timeout", 120)
        if api_timeout:
            client_kwargs["timeout"] = float(api_timeout)

        return Anthropic(**client_kwargs)

    def probe(self, client, llm_config):
        response = client.messages.create(
            model=llm_config.get("model"),
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        # 同 OpenAI 侧：200 包装的业务错误 SDK 不会抛，须显式校验响应形态
        if not getattr(response, "content", None) and getattr(response, "stop_reason", None) is None:
            raise RuntimeError(
                "Anthropic probe got a non-message response (no content, no stop_reason)"
            )

    def _prepare_messages(self, messages):
        system_blocks = []
        remote_messages = []

        for raw_message in messages:
            message = normalize_incoming_message(raw_message)
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                content_str = stringify_content(content).strip()
                if not content_str:
                    continue
                block = {"type": "text", "text": content_str}
                cache_control = message.get("cache_control")
                if cache_control:
                    block["cache_control"] = cache_control
                system_blocks.append(block)
                continue

            if role not in {"user", "assistant"}:
                raise ValueError(
                    f"Unsupported message role for anthropic_compatible provider: {role}"
                )

            if isinstance(content, list):
                normalized_content = _normalize_content_blocks(role, content)
                if normalized_content:
                    cache_control = message.get("cache_control")
                    if cache_control is not None:
                        for block in reversed(normalized_content):
                            if block.get("type") == "text":
                                block["cache_control"] = cache_control
                                break
                    remote_messages.append({"role": role, "content": normalized_content})
                continue

            if isinstance(content, str):
                content_str = stringify_content(content).strip()
                if content_str:
                    cache_control = message.get("cache_control")
                    if cache_control is not None:
                        remote_messages.append({
                            "role": role,
                            "content": [{"type": "text", "text": content_str, "cache_control": cache_control}],
                        })
                    else:
                        remote_messages.append({"role": role, "content": content_str})
                continue

            raise TypeError(
                f"{role} message content must be a string or native block list, got {type(content).__name__}"
            )

        if not remote_messages:
            system_blocks.append({
                "type": "text",
                "text": "There is no pending user message. Continue with your current task.",
            })
            remote_messages.append({"role": "user", "content": "Continue."})

        return system_blocks, remote_messages

    def build_request(self, client, messages, params, tools=None, tool_choice=None):
        system_blocks, remote_messages = self._prepare_messages(messages)

        wire = dict(params)
        wire["messages"] = remote_messages
        if system_blocks:
            wire["system"] = system_blocks
        if tools is not None:
            wire["tools"] = tools
        if tool_choice:
            wire["tool_choice"] = tool_choice

        return ProviderRequest(wire=wire, call=lambda: client.messages.create(**wire))

    def normalize_response(self, raw):
        content = []
        for block in getattr(raw, "content", None) or []:
            if isinstance(block, dict):
                content.append(dict(block))
            elif hasattr(block, "model_dump"):
                content.append(block.model_dump())
            else:
                content.append({"type": "text", "text": str(block)})

        usage = None
        raw_usage = getattr(raw, "usage", None)
        if raw_usage is not None:
            usage = {
                "input_tokens": getattr(raw_usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(raw_usage, "output_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            }

        return NormalizedResponse(
            content=content,
            stop_reason=getattr(raw, "stop_reason", None),
            usage=usage,
            raw=raw,
        )
