import json
import os
import re

from configurationLoader import config
from Memory.ToolCall import ToolCall


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

    @classmethod
    def _strip_json_fence(cls, text):
        stripped = text.strip()
        match = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        return stripped

    def get_token_size(self, text):
        if self.tokenizer is not None:
            tokens = self.tokenizer(text)
            return len(tokens["input_ids"])
        return len(self._stringify_content(text).split())

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

    def _prepare_anthropic_messages(self, messages):
        system_blocks = []
        remote_messages = []

        for raw_message in messages:
            message = self._normalize_provider_message(raw_message)
            role = message.get("role")
            content = self._stringify_content(message.get("content", "")).strip()
            if not content:
                continue

            if role == "system":
                # 构建系统消息块，支持缓存
                block = {"type": "text", "text": content}

                # 如果消息包含 cache_control，添加到块中
                cache_control = message.get("cache_control")
                if cache_control:
                    block["cache_control"] = cache_control

                system_blocks.append(block)
                continue

            if role in {"user", "assistant"}:
                remote_messages.append({"role": role, "content": content})
                continue

            if role == "tool":
                tool_name = message.get("name") or "unknown_tool"
                remote_messages.append({
                    "role": "user",
                    "content": f"工具调用结果（{tool_name}）:\n{content}",
                })

        if not remote_messages:
            remote_messages.append({"role": "user", "content": "请继续。"})

        return system_blocks, remote_messages

    def _generate_local_hf(self, messages, **gen_kwargs):
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
    def _extract_response_text(response):
        text_parts = []
        thinking_parts = []

        for block in getattr(response, "content", None) or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    # MiniMax 的 thinking 字段可能包含工具调用
                    thinking_parts.append(block.get("thinking", ""))
                continue
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
            elif getattr(block, "type", None) == "thinking":
                thinking_parts.append(getattr(block, "thinking", ""))

        response_text = "\n".join(part for part in text_parts if part).strip()
        if response_text:
            return response_text

        # 如果没有 text 内容，尝试从 thinking 中提取
        thinking_text = "\n".join(part for part in thinking_parts if part).strip()
        if thinking_text:
            return thinking_text

        for attribute_name in ("text", "completion"):
            fallback_text = getattr(response, attribute_name, None)
            if isinstance(fallback_text, str) and fallback_text.strip():
                return fallback_text.strip()

        return ""

    def _generate_anthropic_compatible(self, messages, **gen_kwargs):
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

        last_response = None
        for _ in range(2):
            last_response = self.client.messages.create(**request_kwargs)
            response_text = self._extract_response_text(last_response)
            if response_text:
                return self._strip_json_fence(response_text)
            request_kwargs["temperature"] = 0

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
