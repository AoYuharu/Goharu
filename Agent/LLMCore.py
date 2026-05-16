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

        self.model = None
        self.tokenizer = None
        self.client = None
        self.torch = None

        self.llm_config = config.get("model.large-language-model", {}) or {}
        self.provider = self.llm_config.get("provider", "local_hf")
        self._last_thinking = None
        self._last_usage = None

        try:
            if self.provider == "local_hf":
                self._init_local_hf()
            elif self.provider == "anthropic_compatible":
                self._init_anthropic_compatible()
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception:
            self.__class__._instance = None
            raise

        self.__class__._initialized = True

    def _init_local_hf(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch = torch
        local_config = self.llm_config.get("local_hf", {}) or {}
        model_path = local_config.get("path")
        if not model_path:
            raise ValueError("Missing model.large-language-model.local_hf.path in config.yaml")

        quantization_config = None
        if local_config.get("load_in_4bit", True):
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type=local_config.get("bnb_4bit_quant_type", "nf4"),
                bnb_4bit_compute_dtype=self._resolve_torch_dtype(
                    local_config.get("bnb_4bit_compute_dtype", "float16")
                ),
                bnb_4bit_use_double_quant=local_config.get("bnb_4bit_use_double_quant", True),
            )

        self.model = AutoModelForCausalLM.from_pretrained(
            model_path,
            quantization_config=quantization_config,
            device_map=local_config.get("device_map", "cuda"),
            trust_remote_code=local_config.get("trust_remote_code", True),
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            trust_remote_code=local_config.get("trust_remote_code", True),
        )
        from Agent.TokenEstimator import TokenEstimator
        TokenEstimator().set_tokenizer(self.tokenizer)

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
        base_url = self.llm_config.get("base_url")
        if base_url:
            client_kwargs["base_url"] = base_url

        # 设置 HTTP 超时避免 API 调用无限挂死
        api_timeout = self.llm_config.get("api_timeout", 120)
        if api_timeout:
            client_kwargs["timeout"] = float(api_timeout)

        self.client = Anthropic(**client_kwargs)

    def _resolve_torch_dtype(self, dtype_name):
        if self.torch is None:
            import torch

            self.torch = torch

        dtype_map = {
            "float16": self.torch.float16,
            "bfloat16": self.torch.bfloat16,
            "float32": self.torch.float32,
        }
        if dtype_name not in dtype_map:
            raise ValueError(f"Unsupported torch dtype: {dtype_name}")
        return dtype_map[dtype_name]

    @staticmethod
    def _normalize_provider_message(message):
        if isinstance(message, ToolCall):
            return message.to_prompt_message()

        if isinstance(message, dict):
            # 如果 content 已经是 Anthropic 原生块数组格式，保持原样
            content = message.get("content")
            if isinstance(content, list):
                return dict(message)
            tool_call = ToolCall.from_record(message)
            if tool_call is not None:
                return tool_call.to_prompt_message()
            return dict(message)

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
        return any(kw in msg for kw in ("server error", "internal error", "rate limit", "timeout", "503", "502", "500", "504", "429"))

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

    def _prepare_local_messages(self, messages):
        prepared = []
        for raw_message in messages:
            message = self._normalize_provider_message(raw_message)
            role = message.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue

            prepared_message = {
                "role": role,
                "content": self._stringify_content(message.get("content", "")),
            }
            if role == "tool" and message.get("name"):
                prepared_message["name"] = message.get("name")
            prepared.append(prepared_message)
        return prepared

    @staticmethod
    def _try_convert_legacy_content(role, content_str):
        """将旧版文本格式的工具调用/工具结果转换为 Anthropic 块数组。

        assistant 消息: 如果以 { 或 [ 开头，尝试解析为工具调用 → [tool_use 块]
        user 消息:    如果以 [ 开头，尝试解析为工具结果数组 → [tool_result 块]
        不匹配则返回原字符串。
        """
        stripped = content_str.strip()
        if not stripped:
            return None

        if role == "assistant":
            if stripped.startswith(("{", "[")):
                # 1. 检查是否已是序列化的原生 tool_use 块数组 → 直接提取保留 ID
                try:
                    parsed = json.loads(stripped)
                except (TypeError, json.JSONDecodeError):
                    parsed = None
                if isinstance(parsed, list):
                    native_blocks = []
                    for item in parsed:
                        if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name"):
                            native_blocks.append(item)
                        elif isinstance(item, dict) and item.get("type") == "text":
                            native_blocks.append(item)
                    if native_blocks:
                        return native_blocks
                # 2. 尝试通过 ToolCall 解析旧格式
                tool_calls = ToolCall.try_all_from_text(stripped)
                if tool_calls:
                    return [tc.to_anthropic_tool_use() for tc in tool_calls]
            return stripped

        if role == "user":
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except (TypeError, json.JSONDecodeError):
                    return stripped

                if isinstance(parsed, list):
                    blocks = []
                    for item in parsed:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            blocks.append(item)
                    if blocks:
                        return blocks

            return stripped

        return stripped

    @staticmethod
    def _align_converted_ids(remote_messages):
        """修正从旧格式转换时的 tool_use_id 对齐问题。

        当 assistant 消息被转换为 tool_use 块，但后续 user 消息的 tool_result
        使用了不同的原始 ID（如 call_function_xxx）时，将 tool_use 的 id
        替换为对应 tool_result 中的 tool_use_id，确保配对正确。
        """
        for i in range(len(remote_messages) - 1):
            cur = remote_messages[i]
            nxt = remote_messages[i + 1]
            if cur.get("role") != "assistant" or nxt.get("role") != "user":
                continue
            cur_content = cur.get("content")
            nxt_content = nxt.get("content")
            if not isinstance(cur_content, list) or not isinstance(nxt_content, list):
                continue
            # Find tool_use blocks in assistant and tool_result blocks in user
            tool_use_blocks = [b for b in cur_content if isinstance(b, dict) and b.get("type") == "tool_use"]
            tool_result_blocks = [b for b in nxt_content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if not tool_use_blocks or not tool_result_blocks:
                continue
            # Check if IDs are already aligned (tool_use.id == any tool_result.tool_use_id)
            result_ids = {b["tool_use_id"] for b in tool_result_blocks if "tool_use_id" in b}
            already_aligned = any(b.get("id") in result_ids for b in tool_use_blocks)
            if not already_aligned and len(tool_use_blocks) <= len(tool_result_blocks):
                # Replace generated tool_use IDs with the actual tool_result tool_use_ids
                for j, tu_block in enumerate(tool_use_blocks):
                    if j < len(tool_result_blocks):
                        tu_block["id"] = tool_result_blocks[j]["tool_use_id"]
            elif not already_aligned and len(tool_use_blocks) == len(tool_result_blocks):
                for j, tu_block in enumerate(tool_use_blocks):
                    tu_block["id"] = tool_result_blocks[j]["tool_use_id"]

    def _prepare_anthropic_messages(self, messages):
        system_blocks = []
        remote_messages = []

        for raw_message in messages:
            message = self._normalize_provider_message(raw_message)
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                # 构建系统消息块，支持缓存
                content_str = self._stringify_content(content).strip()
                if not content_str:
                    continue

                block = {"type": "text", "text": content_str}

                cache_control = message.get("cache_control")
                if cache_control:
                    block["cache_control"] = cache_control

                system_blocks.append(block)
                continue

            if role in {"user", "assistant"}:
                # 支持原生格式的 content（可以是字符串或块数组）
                if isinstance(content, list):
                    # 原生格式：content 是块数组
                    filtered_content = []
                    for block in content:
                        # 将 Pydantic/SDK 对象转为纯 dict（保留原生 tool_use_id）
                        if not isinstance(block, dict) and hasattr(block, "model_dump"):
                            block = block.model_dump()
                        if isinstance(block, dict):
                            if block.get("type") == "text" and block.get("text", "").strip():
                                filtered_content.append(block)
                            elif block.get("type") in {"tool_use", "tool_result"}:
                                filtered_content.append(block)
                        elif isinstance(block, str) and block.strip():
                            filtered_content.append({"type": "text", "text": block})

                    if filtered_content:
                        remote_messages.append({"role": role, "content": filtered_content})
                else:
                    # 文本格式：尝试转换为原生块格式
                    content_str = self._stringify_content(content).strip()
                    if content_str:
                        converted = self._try_convert_legacy_content(role, content_str)
                        if isinstance(converted, list):
                            remote_messages.append({"role": role, "content": converted})
                        elif converted is not None:
                            remote_messages.append({"role": role, "content": converted})
                continue

            if role == "tool":
                # 向后兼容：将旧的 tool 角色转换为 user 消息
                tool_name = message.get("name") or "unknown_tool"
                content_str = self._stringify_content(content).strip()
                if content_str:
                    remote_messages.append({
                        "role": "user",
                        "content": f"工具调用结果（{tool_name}）:\n{content_str}",
                    })

        if not remote_messages:
            remote_messages.append({"role": "user", "content": "Continue."})

        # 修正从旧格式转换时的 tool_use_id 对齐问题
        self._align_converted_ids(remote_messages)

        return system_blocks, remote_messages

    def _generate_local_hf(self, messages, **gen_kwargs):
        self._last_thinking = None
        self._last_usage = None
        prepared_messages = self._prepare_local_messages(messages)
        text = self.tokenizer.apply_chat_template(
            prepared_messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(
            config.get("model.large-language-model.local_hf.device_map", "cuda")
        )

        generation_kwargs = {
            "max_new_tokens": gen_kwargs.pop(
                "max_new_tokens",
                self.llm_config.get("local_hf", {}).get("max_new_tokens", self.llm_config.get("max_tokens", 1024)),
            ),
            "do_sample": gen_kwargs.pop("do_sample", True),
            "temperature": gen_kwargs.pop("temperature", self.llm_config.get("temperature", 0.7)),
            "top_p": gen_kwargs.pop("top_p", self.llm_config.get("top_p", 0.9)),
            "repetition_penalty": gen_kwargs.pop(
                "repetition_penalty",
                self.llm_config.get("local_hf", {}).get("repetition_penalty", 1.1),
            ),
            **gen_kwargs,
        }

        with self.torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                **generation_kwargs,
            )

        response = self.tokenizer.decode(
            outputs[0][inputs.input_ids.shape[-1]:],
            skip_special_tokens=True,
        ).strip()
        return self._strip_json_fence(response)

    @staticmethod
    def _extract_usage(response):
        """Extract usage dict from Anthropic-compatible response."""
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
        """
        Extract text content from Anthropic response.
        Returns the combined text from all 'text' blocks.
        """
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
        """
        Extract thinking content from Anthropic response.
        Returns the combined thinking from all 'thinking' blocks, or None.
        """
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

        # 使用系统块格式（支持缓存）
        if system_blocks:
            request_kwargs["system"] = system_blocks

        # 支持工具定义（Anthropic 原生格式）
        tools = gen_kwargs.pop("tools", None)
        if tools is not None:
            request_kwargs["tools"] = tools

        # 支持 tool_choice 参数
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

        # 记录API请求
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
                    # 瞬态错误重试（HTTP 5xx, 429 rate limit）
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
                # 记录API响应
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

                # 如果使用了工具，返回完整的响应对象（包含 tool_use 块）
                if tools:
                    stop_reason = getattr(last_response, "stop_reason", "?")
                    logger.info(
                        "API response: %.0fms stop=%s%s",
                        t_elapsed, stop_reason, usage_str,
                    )
                    return last_response

                # 否则提取文本内容和 thinking（向后兼容）
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
        if self.provider == "local_hf":
            return self._generate_local_hf(messages, **gen_kwargs)
        if self.provider == "anthropic_compatible":
            return self._generate_anthropic_compatible(messages, **gen_kwargs)
        raise ValueError(f"Unsupported LLM provider: {self.provider}")
