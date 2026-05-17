"""类型化配置数据类 — 提供类型安全的配置访问，替代各处 config.get("key.path") 的裸字符串用法。

用法:
    from configurationLoader import config
    model_cfg = config.make_model_config()
    agent_cfg = config.make_agent_config()
    memory_cfg = config.make_memory_config()
    tool_cfg = config.make_tool_config()
"""

from dataclasses import dataclass


@dataclass
class ModelConfig:
    provider: str = "anthropic_compatible"
    model_name: str = ""
    api_key_env: str = ""
    base_url: str = ""
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.9
    api_timeout: int = 120


@dataclass
class AgentConfig:
    maxDepth: int = 200
    max_context_messages: int = 20
    micro_compact_enabled: bool = True
    micro_compact_age_threshold_hours: int = 1
    micro_compact_keep_tool_results: int = 5
    context_compact_enabled: bool = True
    context_compact_threshold_tokens: int = 80000
    context_compact_trigger_before_step: bool = True
    snip_enabled: bool = True
    snip_reminder_threshold_tokens: int = 40000
    background_watch_window: int = 1800
    background_max_reactivations: int = 3


@dataclass
class MemoryConfig:
    root_dir: str = "./runtime_memory"
    sqlite_enabled: bool = True
    sqlite_path: str = "./runtime_memory/memory.db"
    retrieval_enabled: bool = False
    retrieval_fts_top_k: int = 8
    retrieval_embedding_top_k: int = 8
    retrieval_final_top_k: int = 6
    retrieval_query_window_messages: int = 6
    retrieval_use_reranker: bool = False
    retrieval_embedding_model_name: str = "Qwen3-Embedding-0.6B"
    retrieval_embedding_model_path: str = "./models/embedding_model/Qwen3-Embedding-0.6B"
    retrieval_bm25_enabled: bool = True
    retrieval_bm25_top_k: int = 8
    retrieval_embedding_provider: str = "local"
    retrieval_embedding_base_url: str = ""
    retrieval_embedding_api_key_env: str = ""
    retrieval_embedding_dimensions: int = 768
    retrieval_embedding_timeout: int = 30
    retrieval_reranker_model_path: str = "./models/rerank_model/bge-reranker-v2-m3"
    pipeline_db_enabled: bool = True
    export_daily_json_enabled: bool = True
    session_id: str = "default"
    daily_dir: str = "./runtime_memory/daily"
    retention_days: int = 7


@dataclass
class ToolConfig:
    background_timeout: int = 120
    result_budget_max_single: int = 8000
    result_budget_max_batch: int = 24000
    result_budget_cache_dir: str = "./runtime_memory/tool_cache"
    security_enabled: bool = True
    security_allow_confirmation: bool = False
    timeout: int = 60
    runtime: str = "in_process"
