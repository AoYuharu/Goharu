"""Provider 工厂与 auto 探测。"""

import logging

from Agent.providers.anthropic_provider import AnthropicProvider
from Agent.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

_PROVIDERS = {
    AnthropicProvider.name: AnthropicProvider,
    OpenAIProvider.name: OpenAIProvider,
}

# auto 模式的探测顺序：先 OpenAI，失败后退 Anthropic
_AUTO_PROBE_ORDER = [OpenAIProvider.name, AnthropicProvider.name]


def get_provider(name):
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unsupported LLM provider: {name}. "
            f"Supported: {', '.join(sorted(_PROVIDERS))}, auto"
        )
    return cls()


def resolve_provider(name, llm_config):
    """解析 provider。name 为 auto/空时按顺序探测（最小真实调用），返回 (provider, client)。

    显式指定 provider 时直接创建 client，不探测。
    """
    if name and name != "auto":
        provider = get_provider(name)
        return provider, provider.create_client(llm_config)

    errors = []
    for candidate in _AUTO_PROBE_ORDER:
        provider = get_provider(candidate)
        try:
            client = provider.create_client(llm_config)
            provider.probe(client, llm_config)
            logger.info("LLM provider auto-detected: %s", candidate)
            return provider, client
        except Exception as exc:
            logger.warning(
                "LLM provider probe failed for %s: %s: %s",
                candidate, type(exc).__name__, exc,
            )
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "LLM provider auto-detection failed for all protocols. "
        + " | ".join(errors)
    )

