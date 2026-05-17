import json
import logging
import os
import re
import time as _time_module

from configurationLoader import config
from Core.ToolCall import ToolCall

logger = logging.getLogger(__name__)


class LLMCore:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self.__class__._initialized:
            return

        self.client = None
        self.llm_config = config.get("model.large-language-model", {}) or {}
        self.provider = self.llm_config.get("provider", "anthropic_compatible")
        self._last_thinking = None
        self._last_usage = None

        try:
            if self.provider != "anthropic_compatible":
                raise ValueError(
                    f"Unsupported LLM provider: {self.provider}. "
                    "Only anthropic_compatible is supported."
                )
            self._init_anthropic_compatible()
        except Exception:
            self.__class__._instance = None
            raise

        self.__class__._initialized = True

    def _init_anthropic_compatible(self):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is required for provider anthropic_compatible. "
                "Install dependencies from requirements.txt in the active Python environment."
            ) from exc

        api_key_env = self.llm_config.get("api_key_env")
        if not api_key_env:
            raise ValueError("Missing model.large-language-model.api_key_env in config.yaml")

        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ValueError(
                f"Environment variable {api_key_env} is required for provider anthropic_compatible"
            )

        client_kwargs = {"api_key": api_key}
        base_url = self.llm_config.get("base_url") or os.getenv("ANTHROPIC_BASE_URL")
        if base_url:
            client_kwargs["base_url"] = base_url

        api_timeout = self.llm_config.get("api_timeout", 120)
        if api_timeout:
            client_kwargs["timeout"] = float(api_timeout)

        self.client = Anthropic(**client_kwargs)

    @staticmethod
    def _normalize_provider_message(message):
        if isinstance(message, ToolCall):
            return message.to_record()

        if isinstance(message, dict):
            normalized = dict(message)
            content = normalized.get("content")
            if isinstance(content, list):
                normalized["content"] = list(content)
            return normalized

        raise TypeError("LLM messages must be dict or ToolCall")

    @staticmethod
    def _stringify_content(content):
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

    @staticmethod
    def _is_retryable_error(error):
        """判断 API 错误是否可重试（5xx / 429 rate limit）"""
        status = getattr(error, "status_code", None)
        if status is not None:
            return status >= 500 or status == 429
        msg = str(error).lower()
        return any(
            kw in msg
            for kw in ("server error", "internal error", "rate limit", "timeout", "503", "502", "500", "504", "429")
        )

    @classmethod
    def _strip_json_fence(cls, text):
        stripped = text.strip()
        match = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        return stripped

    def get_token_size(self, text):
        from Agent.TokenEstimator import TokenEstimator
        return TokenEstimator().estimate(text)

    @staticmethod
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
                text = LLMCore._stringify_content(block.get("text", "")).strip()
                if text:
                    normalized.append({**block, "text": text})
            elif block_type == "thinking":
                thinking = LLMCore._stringify_content(block.get("thinking", "")).strip()
                if thinking:
                    normalized.append({**block, "thinking": thinking})
            elif block_type == "tool_use":
                normalized.append(dict(block))
            elif block_type == "tool_result":
                normalized.append(dict(block))
            else:
                raise ValueError(f"Unsupported native block type: {block_type}")

        return normalized

    def _prepare_anthropic_messages(self, messages):
        system_blocks = []
        remote_messages = []

        for raw_message in messages:
            message = self._normalize_provider_message(raw_message)
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                content_str = self._stringify_content(content).strip()
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
                normalized_content = self._normalize_content_blocks(role, content)
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
                content_str = self._stringify_content(content).strip()
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
            remote_messages.append({"role": "user", "content": "Continue."})

        return system_blocks, remote_messages

    @staticmethod
    def _extract_usage(response, fallback_usage=None):
        """Extract usage dict from Anthropic-compatible response."""
        if fallback_usage is not None:
            return fallback_usage
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        }

    @staticmethod
    def _extract_response_text(response):
        text_parts = []
        content = getattr(response, "content", None)

        for block in content or []:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
            else:
                block_type = getattr(block, "type", None)
                if block_type == "text":
                    text_parts.append(getattr(block, "text", ""))

        return "\n".join(part for part in text_parts if part).strip()

    @staticmethod
    def _extract_thinking_text(response):
        thinking_parts = []
        content = getattr(response, "content", None)

        for block in content or []:
            if isinstance(block, dict):
                if block.get("type") == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
            else:
                if getattr(block, "type", None) == "thinking":
                    thinking_parts.append(getattr(block, "thinking", ""))

        combined = "\n".join(part for part in thinking_parts if part).strip()
        return combined or None

    def _generate_anthropic_compatible(self, messages, **gen_kwargs):
        self._last_thinking = None
        self._last_usage = None
        system_blocks, remote_messages = self._prepare_anthropic_messages(messages)

        request_kwargs = {
            "model": gen_kwargs.pop("model", self.llm_config.get("model")),
            "max_tokens": gen_kwargs.pop(
                "max_tokens",
                gen_kwargs.pop("max_new_tokens", self.llm_config.get("max_tokens", 1024)),
            ),
            "temperature": gen_kwargs.pop("temperature", self.llm_config.get("temperature", 0.7)),
            "top_p": gen_kwargs.pop("top_p", self.llm_config.get("top_p", 0.9)),
            "messages": remote_messages,
        }

        if system_blocks:
            request_kwargs["system"] = system_blocks

        tools = gen_kwargs.pop("tools", None)
        if tools is not None:
            request_kwargs["tools"] = tools

        tool_choice = gen_kwargs.pop("tool_choice", None)
        if tool_choice:
            request_kwargs["tool_choice"] = tool_choice

        last_response = None
        tool_names = ""
        if tools:
            tool_names = ", ".join(t.get("name", "?") for t in tools[:6])
            if len(tools) > 6:
                tool_names += f" (+{len(tools) - 6} more)"

        msg_count = len(remote_messages)
        sys_blocks_count = len(system_blocks)
        logger.info(
            "API request: model=%s messages=%d system_blocks=%d tools=%d [%s] max_tokens=%d temp=%.2f",
            request_kwargs["model"], msg_count, sys_blocks_count,
            len(tools) if tools else 0, tool_names,
            request_kwargs["max_tokens"], request_kwargs["temperature"],
        )

        try:
            from Agent.LLMResponseLogger import llm_response_logger
            call_id = llm_response_logger.log_request(
                messages=remote_messages,
                system_blocks=system_blocks,
                tools=request_kwargs.get("tools"),
                model=request_kwargs.get("model"),
                max_tokens=request_kwargs.get("max_tokens"),
                temperature=request_kwargs.get("temperature"),
                top_p=request_kwargs.get("top_p"),
            )
        except Exception:
            call_id = None

        t_start = _time_module.time()
        try:
            for retry_i in range(2):
                try:
                    last_response = self.client.messages.create(**request_kwargs)
                except Exception as api_error:
                    if self._is_retryable_error(api_error):
                        logger.warning(
                            "API retry %d/1: %s",
                            retry_i + 1,
                            type(api_error).__name__,
                        )
                        _time_module.sleep(1.0)
                        last_response = self.client.messages.create(**request_kwargs)
                    else:
                        raise

                t_elapsed = (_time_module.time() - t_start) * 1000
                if call_id is not None:
                    try:
                        llm_response_logger.log_response(call_id, last_response)
                    except Exception:
                        pass

                self._last_usage = self._extract_usage(last_response)
                usage_str = ""
                if self._last_usage:
                    usage_str = (
                        f" in={self._last_usage.get('input_tokens', 0)} "
                        f"out={self._last_usage.get('output_tokens', 0)} "
                        f"cache_read={self._last_usage.get('cache_read_input_tokens', 0)}"
                    )

                if tools is not None:
                    stop_reason = getattr(last_response, "stop_reason", "?")
                    logger.info(
                        "API response: %.0fms stop=%s%s",
                        t_elapsed, stop_reason, usage_str,
                    )
                    return last_response

                self._last_thinking = self._extract_thinking_text(last_response)
                response_text = self._extract_response_text(last_response)
                if response_text:
                    logger.info(
                        "API response: %.0fms text_len=%d%s",
                        t_elapsed, len(response_text), usage_str,
                    )
                    return self._strip_json_fence(response_text)
                request_kwargs["temperature"] = 0
                logger.debug("API empty response, retrying with temperature=0")
        except Exception as exc:
            t_elapsed = (_time_module.time() - t_start) * 1000
            logger.error(
                "API error after %.0fms: %s", t_elapsed, type(exc).__name__,
                exc_info=True,
            )
            raise

        response_payload = None
        if hasattr(last_response, "model_dump"):
            response_payload = last_response.model_dump()
        elif hasattr(last_response, "dict"):
            response_payload = last_response.dict()

        raise ValueError(
            "Anthropic-compatible provider returned no text content: "
            f"{self._stringify_content(response_payload or last_response)}"
        )

    def generate(self, messages, **gen_kwargs):
        if self.provider != "anthropic_compatible":
            raise ValueError(
                f"Unsupported LLM provider: {self.provider}. "
                "Only anthropic_compatible is supported."
            )
        return self._generate_anthropic_compatible(messages, **gen_kwargs)
