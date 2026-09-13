import logging
import re
import time as _time_module

from configurationLoader import config
from Agent.providers import resolve_provider
from Agent.providers.base import NormalizedResponse, extract_text, extract_thinking
from Core.LifecycleLog import lifecycle

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

        self.llm_config = config.get("model.large-language-model", {}) or {}
        configured_provider = self.llm_config.get("provider", "auto")
        self._last_thinking = None
        self._last_usage = None

        try:
            self.provider, self.client = resolve_provider(configured_provider, self.llm_config)
            self.provider_name = self.provider.name
        except Exception:
            self.__class__._instance = None
            raise

        self.__class__._initialized = True
        logger.info("LLM provider resolved: %s (configured: %s)", self.provider_name, configured_provider)

    def reload(self):
        """热重载 provider/client（模型切换后立即生效，无需重启）。

        重新读取 config.yaml 的 model.large-language-model 配置并重建 provider 与 client。
        失败时保留旧配置，返回 (False, 错误信息)，不影响正在运行的服务。
        """
        try:
            new_config = config.get("model.large-language-model", {}) or {}
            configured_provider = new_config.get("provider", "auto")
            provider, client = resolve_provider(configured_provider, new_config)
        except Exception as e:
            logger.error("LLM reload failed: %s: %s", type(e).__name__, e)
            return False, str(e)
        self.llm_config = new_config
        self.provider = provider
        self.client = client
        self.provider_name = provider.name
        logger.info("LLM provider reloaded: %s", self.provider_name)
        return True, None

    @property
    def provider_supports_native_tools(self):
        return getattr(self.provider, "supports_native_tools", False)

    @staticmethod
    def _is_retryable_error(error):
        """判断 API 错误是否可重试（5xx / 429 rate limit）"""
        status = getattr(error, "status_code", None)
        if status is not None:
            return status >= 500 or status == 429
        msg = str(error).lower()
        return any(
            kw in msg
            for kw in (
                "server error", "internal error", "rate limit", "timeout",
                "connection error", "connecterror", "remoteprotocolerror",
                "503", "502", "500", "504", "429",
            )
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
    def _extract_usage(response, fallback_usage=None):
        """Extract usage dict from a NormalizedResponse (or legacy native response)."""
        if fallback_usage is not None:
            return fallback_usage
        if isinstance(response, NormalizedResponse):
            return response.usage
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage
        return {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        }

    def generate(self, messages, on_retry=None, **gen_kwargs):
        self._last_thinking = None
        self._last_usage = None

        params = {
            "model": gen_kwargs.pop("model", self.llm_config.get("model")),
            "max_tokens": gen_kwargs.pop(
                "max_tokens",
                gen_kwargs.pop("max_new_tokens", self.llm_config.get("max_tokens", 1024)),
            ),
            "temperature": gen_kwargs.pop("temperature", self.llm_config.get("temperature", 0.7)),
            "top_p": gen_kwargs.pop("top_p", self.llm_config.get("top_p", 0.9)),
        }
        tools = gen_kwargs.pop("tools", None)
        tool_choice = gen_kwargs.pop("tool_choice", None)

        request = self.provider.build_request(
            self.client, messages, params, tools=tools, tool_choice=tool_choice
        )

        tool_names = ""
        if tools:
            tool_names = ", ".join(t.get("name", "?") for t in tools[:6])
            if len(tools) > 6:
                tool_names += f" (+{len(tools) - 6} more)"

        logger.info(
            "API request: provider=%s model=%s messages=%d tools=%d [%s] max_tokens=%d temp=%.2f",
            self.provider_name, params["model"], len(request.wire.get("messages", [])),
            len(tools) if tools else 0, tool_names,
            params["max_tokens"], params["temperature"],
        )

        try:
            from Agent.LLMResponseLogger import llm_response_logger
            call_id = llm_response_logger.log_request(
                messages=request.wire.get("messages"),
                system_blocks=request.wire.get("system", []),
                tools=request.wire.get("tools"),
                model=params["model"],
                max_tokens=params["max_tokens"],
                temperature=params["temperature"],
                top_p=params["top_p"],
            )
        except Exception:
            call_id = None

        t_start = _time_module.time()
        last_response = None
        lifecycle(
            "LLM 请求发出",
            f"{self.provider_name}/{params['model']} messages={len(request.wire.get('messages', []))} tools={len(tools) if tools else 0}",
            level=2,
        )
        try:
            max_api_retries = int(self.llm_config.get("max_retries", 10))
            max_empty_retries = 1
            api_attempt = 0
            empty_retries = 0
            while True:
                try:
                    raw_response = request.call()
                except Exception as api_error:
                    if api_attempt < max_api_retries and self._is_retryable_error(api_error):
                        api_attempt += 1
                        # 指数退避 1,2,4,8 封顶；重试期间回调 on_retry 让 TUI 实时显示进度
                        backoff = min(8, 2 ** (api_attempt - 1))
                        if on_retry is not None:
                            try:
                                on_retry(api_attempt, max_api_retries, backoff, "backoff")
                            except Exception:
                                pass
                        logger.warning(
                            "API retry %d/%d after %ds: %s",
                            api_attempt, max_api_retries, backoff,
                            type(api_error).__name__,
                        )
                        lifecycle(
                            "LLM 重试",
                            f"{api_attempt}/{max_api_retries} 退避 {backoff}s 错误={type(api_error).__name__}",
                            level=2,
                        )
                        _time_module.sleep(backoff)
                        if on_retry is not None:
                            try:
                                on_retry(api_attempt, max_api_retries, backoff, "requesting")
                            except Exception:
                                pass
                        continue
                    raise

                t_elapsed = (_time_module.time() - t_start) * 1000
                if call_id is not None:
                    try:
                        llm_response_logger.log_response(call_id, raw_response)
                    except Exception:
                        pass

                last_response = self.provider.normalize_response(raw_response)
                self._last_usage = last_response.usage
                usage_str = ""
                if self._last_usage:
                    usage_str = (
                        f" in={self._last_usage.get('input_tokens', 0)} "
                        f"out={self._last_usage.get('output_tokens', 0)} "
                        f"cache_read={self._last_usage.get('cache_read_input_tokens', 0)}"
                    )
                lifecycle(
                    "LLM 响应收到",
                    f"{t_elapsed:.0f}ms stop={last_response.stop_reason or '?'}{usage_str}",
                    level=2,
                )

                if tools is not None:
                    logger.info(
                        "API response: %.0fms stop=%s%s",
                        t_elapsed, last_response.stop_reason or "?", usage_str,
                    )
                    return last_response

                self._last_thinking = extract_thinking(last_response)
                response_text = extract_text(last_response)
                if response_text:
                    logger.info(
                        "API response: %.0fms text_len=%d%s",
                        t_elapsed, len(response_text), usage_str,
                    )
                    return self._strip_json_fence(response_text)

                empty_retries += 1
                if empty_retries > max_empty_retries:
                    break
                request.wire["temperature"] = 0
                logger.debug("API empty response, retrying with temperature=0")
        except Exception as exc:
            t_elapsed = (_time_module.time() - t_start) * 1000
            logger.error(
                "API error after %.0fms: %s", t_elapsed, type(exc).__name__,
                exc_info=True,
            )
            raise

        raise ValueError(
            f"{self.provider_name} provider returned no text content: "
            f"{last_response.model_dump() if last_response is not None else None}"
        )
