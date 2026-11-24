"""Gateway 侧错误分类策略：把异常文本映射到统一错误记录的 blocking/cause。

Core 只存字段，不做语义判断；"这次失败是配置还是外部、是否阻塞全部轮次"
属于宿主策略，集中在这里，避免散落到各 except。
"""

from __future__ import annotations

_CONFIG_PATTERNS = (
    "apikey", "api key", "authentication", "unauthorized", "401",
    "invalid key", "invalid x-api-key", "forbidden", "permission",
    "insufficient", "quota", "billing", "payment", "402", "余额",
    "not found", "404", "model_not_found", "invalid_request", "400",
    "base_url", "endpoint", "no such model",
)
_EXTERNAL_PATTERNS = (
    "timeout", "timed out", "connection", "connecterror", "network",
    "dns", "proxy", "ssl", "tls", "unreachable", "reset by peer",
    "502", "503", "504", "overloaded", "rate limit", "429",
)


def classify_llm_error(message: str) -> tuple[str, str, bool]:
    """LLM/Provider 类错误 → (blocking, cause, retryable)。

    鉴权/配额/模型/URL 属于配置问题，会阻塞全部轮次；
    网络/限流/5xx 属于外部问题，也可能让所有轮次失败；
    其余按单轮失败处理。
    """
    low = str(message or "").lower()
    if any(p in low for p in _CONFIG_PATTERNS):
        return "all_turns", "config", False
    if any(p in low for p in _EXTERNAL_PATTERNS):
        return "all_turns", "external", True
    return "request", "runtime", False


def classify_startup_error(message: str) -> tuple[str, str]:
    """启动期致命错误 → (blocking=fatal, cause)。"""
    low = str(message or "").lower()
    if any(p in low for p in _CONFIG_PATTERNS):
        return "fatal", "config"
    if any(p in low for p in _EXTERNAL_PATTERNS):
        return "fatal", "external"
    return "fatal", "runtime"
