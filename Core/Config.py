"""类型化配置数据类 — 提供类型安全的配置访问，替代各处 config.get("key.path") 的裸字符串用法。

用法:
    from configurationLoader import config
    model_cfg = config.make_model_config()
    agent_cfg = config.make_agent_config()
    memory_cfg = config.make_memory_config()
    tool_cfg = config.make_tool_config()
"""

from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    provider: str = "local_hf"
    model_name: str = ""
    api_key_env: str = ""
    base_url: str = ""
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.9
    use_native_tools: bool = True
    api_timeout: int = 120
    # local_hf
    hf_model_path: str = ""
    hf_device_map: str = "cuda"
    hf_trust_remote_code: bool = True
    hf_load_in_4bit: bool = True
    hf_bnb_4bit_quant_type: str = "nf4"
    hf_bnb_4bit_compute_dtype: str = "float16"
    hf_bnb_4bit_use_double_quant: bool = True
    hf_max_new_tokens: int = 1024
    hf_repetition_penalty: float = 1.1


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
    daily_dir: str = "./runtime_memory/daily"
    retention_days: int = 7
    index_path: str = "./runtime_memory/MEMORY.md"
    user_path: str = "./runtime_memory/USER.md"
    user_review_enabled: bool = True
    user_review_interval: int = 10
    topic_dir: str = "./runtime_memory/topic"
    topic_merge_every_n: int = 3
    topic_merge_min_count: int = 4


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
