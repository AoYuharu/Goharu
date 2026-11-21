"""LLM 访问层门面 —— 外部 harness 的模型调用入口。

职责（只有一件）：可靠地把 messages 发给模型并拿回统一格式的响应。
内建：provider 抽象（Anthropic/OpenAI 兼容）、auto 探测、指数退避重试、
用量统计（含 prompt cache 命中率所需的 cache_read/cache_write 细分）。

与 AgentLoop（Agent/api.py）的关系：
- LLM 层 = "怎么调模型"（传输 + 韧性）
- AgentLoop = "用模型输出驱动工具循环"（决策）
- 连接点：create_llm 的产出对象直接满足 create_agent(llm=...) 的注入契约。

最小接入：

    from Agent.llm import create_llm

    llm = create_llm({
        "provider": "anthropic_compatible",      # 或 "openai_compatible" / "auto"
        "model": "claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",       # 读哪个环境变量拿 key
        "base_url": "https://api.anthropic.com",  # 可选
        "max_tokens": 8192, "temperature": 0.7,
        "max_retries": 10, "api_timeout": 120,
        "connect_timeout": 15, "max_total_seconds": 300,
    })
    resp = llm.query([{"role": "user", "content": "你好"}])
    # 带 tools 时返回 NormalizedResponse（.content 块 / .stop_reason / .usage）；
    # 不带 tools 时返回纯文本 str。
"""

import logging

from Agent.LLMCore import LLMCore
from Agent.providers.base import NormalizedResponse

logger = logging.getLogger(__name__)

__all__ = ["create_llm", "reload_global_llm", "LLMClient", "NormalizedResponse"]


class LLMClient:
    """非单例 LLM 客户端：config dict 直注，与 Goharu 全局配置解耦。

    同时满足 AgentLoop 的 llm 注入契约（query 方法 + 响应形态）。
    """

    def __init__(self, config: dict):
        if not isinstance(config, dict) or not config:
            raise ValueError(
                "create_llm 需要配置 dict，至少包含 provider/model/api_key_env"
            )
        self._core = LLMCore(llm_config=config)
        logger.info("LLMClient created: provider=%s model=%s",
                    self._core.provider_name, config.get("model"))

    def query(self, messages, on_retry=None, **kwargs):
        """发送消息。tools= 传入工具定义时返回 NormalizedResponse，否则返回 str。

        on_retry(attempt, max, backoff_seconds, phase) 在重试退避时被回调，
        phase 为 "backoff"（等待中）或 "requesting"（重新发起）。
        """
        return self._core.generate(messages, on_retry=on_retry, **kwargs)

    @property
    def provider_name(self) -> str:
        return self._core.provider_name

    @property
    def supports_native_tools(self) -> bool:
        return self._core.provider_supports_native_tools

    def get_last_usage(self):
        """最近一次调用的用量：input/output/cache_read/cache_creation tokens。"""
        return getattr(self._core, "_last_usage", None)

    def get_last_thinking(self):
        """最近一次调用的 thinking 内容（若有）。"""
        return getattr(self._core, "_last_thinking", None)

    def get_token_size(self, text: str) -> int:
        """token 估算。"""
        return self._core.get_token_size(text)

    def reload(self, new_config: dict):
        """热切换配置（换模型/换 key 不重建对象）。返回 (ok, error)。"""
        return self._core.reload(new_llm_config=new_config)


def create_llm(config: dict) -> LLMClient:
    """创建 LLM 客户端。

    config 键（扁平一层）：
      provider       "anthropic_compatible" / "openai_compatible" / "auto"（探测）
      model          模型名
      api_key_env    读取 API key 的环境变量名
      base_url       可选，自定义端点
      api_timeout    可选，read 超时秒数（默认 120）
      connect_timeout 可选，TCP/TLS 握手超时秒数（默认 15，防代理挂死拖死整条链）
      max_total_seconds 可选，重试循环总时限秒数（默认 300，超时即放弃重试抛错）
      max_tokens     默认输出上限（默认 1024）
      temperature    默认 0.7
      top_p          默认 0.9
      max_retries    API 重试上限（默认 10，指数退避 1/2/4/8s 封顶）
    """
    return LLMClient(config)


def reload_global_llm():
    """热重载 Goharu 全局 LLMCore 单例（model.set_config 换模型/key 后用）。

    返回 (ok, error)。封装 LLMCore().reload()，宿主不再直接 import LLMCore。
    """
    return LLMCore().reload()
