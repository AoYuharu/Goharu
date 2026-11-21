"""
OpenAI Chat Completions API provider。

请求：中立消息 → OpenAI wire（system 内联合并；tool_use → assistant.tool_calls；
tool_result → 独立 role:"tool" 消息；thinking/cache_control 丢弃）。
响应：ChatCompletion → NormalizedResponse（finish_reason / usage 归一化）。
"""

import json
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

_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


def _split_block_message(content):
    """块列表消息 → (text_parts, tool_use_blocks, tool_result_blocks)。"""
    text_parts, tool_uses, tool_results = [], [], []
    for block in content:
        if not isinstance(block, dict) and hasattr(block, "model_dump"):
            block = block.model_dump()
        if isinstance(block, str):
            if block.strip():
                text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = stringify_content(block.get("text", "")).strip()
            if text:
                text_parts.append(text)
        elif block_type == "thinking":
            logger.debug("openai_compatible: dropping thinking block")
        elif block_type == "tool_use":
            tool_uses.append(block)
        elif block_type == "tool_result":
            tool_results.append(block)
        else:
            raise ValueError(f"Unsupported native block type: {block_type}")
    return text_parts, tool_uses, tool_results


class OpenAIProvider(LLMProvider):
    name = "openai_compatible"

    def create_client(self, llm_config):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required for provider openai_compatible. "
                "Install dependencies from requirements.txt in the active Python environment."
            ) from exc

        api_key_env = llm_config.get("api_key_env")
        if not api_key_env:
            raise ValueError("Missing model.large-language-model.api_key_env in config.yaml")

        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ValueError(
                f"Environment variable {api_key_env} is required for provider openai_compatible"
            )

        client_kwargs = {"api_key": api_key, "max_retries": 0}
        base_url = llm_config.get("base_url")
        if base_url:
            client_kwargs["base_url"] = base_url

        api_timeout = llm_config.get("api_timeout", 120)
        if api_timeout:
            import httpx

            client_kwargs["timeout"] = httpx.Timeout(
                timeout=float(api_timeout),
                connect=float(llm_config.get("connect_timeout", 15)),
                write=float(llm_config.get("write_timeout", 30)),
                pool=float(llm_config.get("pool_timeout", 10)),
            )

        return OpenAI(**client_kwargs)

    def probe(self, client, llm_config):
        completion = client.chat.completions.create(
            model=llm_config.get("model"),
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        # 部分网关把业务错误包在 HTTP 200 里（如智谱：{"code":500,"msg":"404 NOT_FOUND"}），
        # SDK 不会抛异常。choices 为空说明这不是一次真正的补全——探测必须失败，
        # 否则 auto 模式会误选 OpenAI 协议去打 Anthropic 兼容路由。
        if not getattr(completion, "choices", None):
            data = completion.model_dump() if hasattr(completion, "model_dump") else {}
            raise RuntimeError(
                f"OpenAI probe got a non-completion response: "
                f"code={data.get('code')} msg={data.get('msg')}"
            )

    # ── 请求翻译 ─────────────────────────────────────────────

    def _translate_messages(self, messages):
        system_parts = []
        wire_messages = []
        cache_dropped = False

        def flush_system():
            if system_parts:
                wire_messages.append({"role": "system", "content": "\n\n".join(system_parts)})
                system_parts.clear()

        for raw_message in messages:
            message = normalize_incoming_message(raw_message)
            role = message.get("role")
            content = message.get("content", "")
            if message.get("cache_control") is not None and not cache_dropped:
                cache_dropped = True
                logger.debug("openai_compatible: cache_control dropped (provider caches automatically)")

            if role == "system":
                content_str = stringify_content(content).strip()
                if content_str:
                    system_parts.append(content_str)
                continue

            if role not in {"user", "assistant"}:
                raise ValueError(
                    f"Unsupported message role for openai_compatible provider: {role}"
                )

            flush_system()

            if isinstance(content, str):
                content_str = stringify_content(content).strip()
                if content_str:
                    wire_messages.append({"role": role, "content": content_str})
                continue

            if not isinstance(content, list):
                raise TypeError(
                    f"{role} message content must be a string or native block list, got {type(content).__name__}"
                )

            text_parts, tool_uses, tool_results = _split_block_message(content)

            if role == "assistant":
                if not text_parts and not tool_uses:
                    continue  # 只有 thinking 的 assistant 消息在 OpenAI 侧无对应物
                wire_msg = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_uses:
                    wire_msg["tool_calls"] = [
                        {
                            "id": tu.get("id"),
                            "type": "function",
                            "function": {
                                "name": tu.get("name"),
                                "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False),
                            },
                        }
                        for tu in tool_uses
                    ]
                wire_messages.append(wire_msg)
            else:  # user
                # tool_result 块逐个拆成独立 role:"tool" 消息，保持顺序；
                # 剩余 text 合并为一条 user 消息紧随其后
                for tr in tool_results:
                    wire_messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id"),
                        "content": stringify_content(tr.get("content", "")),
                    })
                if text_parts:
                    wire_messages.append({"role": "user", "content": "\n".join(text_parts)})

        flush_system()
        return wire_messages

    @staticmethod
    def _translate_tools(tools):
        return [
            {
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    @staticmethod
    def _translate_tool_choice(tool_choice):
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
            return {"type": "function", "function": {"name": tool_choice.get("name")}}
        return tool_choice

    def build_request(self, client, messages, params, tools=None, tool_choice=None):
        wire = dict(params)
        wire["messages"] = self._translate_messages(messages)
        if tools is not None:
            wire["tools"] = self._translate_tools(tools)
        if tool_choice:
            wire["tool_choice"] = self._translate_tool_choice(tool_choice)

        return ProviderRequest(wire=wire, call=lambda: client.chat.completions.create(**wire))

    # ── 响应归一化 ────────────────────────────────────────────

    def normalize_response(self, raw):
        data = raw.model_dump() if hasattr(raw, "model_dump") else raw
        if not isinstance(data, dict):
            raise TypeError(f"Unexpected OpenAI response type: {type(data).__name__}")

        choices = data.get("choices") or []
        if not choices and (data.get("success") is False or (data.get("code") and data.get("msg"))):
            # HTTP 200 里包着的业务错误必须抛错，而不是解析成空响应——
            # 空响应会让上层反复重试并最终冲出优雅报错保护区，触发崩溃重启
            raise RuntimeError(
                f"LLM upstream returned an application error over HTTP 200: "
                f"code={data.get('code')} msg={data.get('msg')}"
            )
        content = []
        stop_reason = None
        if choices:
            choice = choices[0]
            message = choice.get("message") or {}
            text = message.get("content")
            if text:
                content.append({"type": "text", "text": text})
            for tc in message.get("tool_calls") or []:
                func = tc.get("function") or {}
                raw_args = func.get("arguments") or ""
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {"value": parsed_args}
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "openai_compatible: tool_call arguments JSON parse failed, name=%s raw=%r",
                        func.get("name"), raw_args[:200],
                    )
                    parsed_args = {"__parse_error__": raw_args}
                content.append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": func.get("name"),
                    "input": parsed_args,
                })
            finish = choice.get("finish_reason")
            stop_reason = _FINISH_REASON_MAP.get(finish, finish)

        usage = None
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            cached = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            usage = {
                "input_tokens": raw_usage.get("prompt_tokens", 0),
                "output_tokens": raw_usage.get("completion_tokens", 0),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cached or 0,
            }

        return NormalizedResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
            raw=raw,
        )
