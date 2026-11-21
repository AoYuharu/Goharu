"""浏览器 LLM 抽取开关 — browser-use MCP server 的 LLM 配置管理。

browser-use 是独立子进程，用不了 Goharu 的 LLMCore；它的 extract_content /
retry_with_browser_use_agent 需要自己 config 里的 llm 配置
（~/.config/browseruse/config.json，UUID 键 DB 风格）。本模块提供开关：
开启 = 把 Goharu 当前模型的 key + OpenAI 兼容端点写进去；关闭 = 移除。
写完后由调用方重连 browser-use MCP server 使其生效。
"""

import json
import logging
import os
from pathlib import Path

from configurationLoader import config

logger = logging.getLogger(__name__)

CONFIG_FILE = Path.home() / ".config" / "browseruse" / "config.json"
PLACEHOLDER_KEY = "your-openai-api-key-here"
# MiniMax 的 OpenAI 兼容端点（Goharu 主链路走的是 /anthropic 端点，不能复用）
MINIMAX_OPENAI_BASE_URL = "https://api.minimaxi.com/v1"


def _load() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_llm_entry(data: dict) -> dict:
    """取 default=true 的 llm 条目；没有则取第一个；都没有则新建一个。"""
    llm = data.setdefault("llm", {})
    for entry in llm.values():
        if isinstance(entry, dict) and entry.get("default"):
            return entry
    for entry in llm.values():
        if isinstance(entry, dict):
            return entry
    import uuid
    entry = {"id": uuid.uuid4().hex, "default": True}
    llm[entry["id"]] = entry
    return entry


def get_status() -> dict:
    entry = _default_llm_entry(_load())
    key = entry.get("api_key") or ""
    enabled = bool(key) and key != PLACEHOLDER_KEY
    return {
        "enabled": enabled,
        "model": entry.get("model") if enabled else None,
        "base_url": entry.get("base_url") if enabled else None,
    }


def set_enabled(enabled: bool) -> dict:
    """写入/移除 llm 配置，返回最新状态。调用方负责重连 MCP server。"""
    data = _load()
    entry = _default_llm_entry(data)
    if enabled:
        key_env = str(config.get("model.large-language-model.api_key_env", "ANTHROPIC_API_KEY") or "ANTHROPIC_API_KEY")
        api_key = os.getenv(key_env, "")
        if not api_key:
            raise RuntimeError(f"环境变量 {key_env} 未设置，无法开启浏览器 LLM 抽取")
        entry["api_key"] = api_key
        entry["base_url"] = MINIMAX_OPENAI_BASE_URL
        entry["model"] = config.get("model.large-language-model.model", "MiniMax-M2")
        logger.info("browser LLM extraction enabled (model=%s)", entry["model"])
    else:
        entry.pop("api_key", None)
        entry.pop("base_url", None)
        logger.info("browser LLM extraction disabled")
    _save(data)
    return get_status()
