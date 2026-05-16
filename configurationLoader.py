import yaml
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

class Configuration:
    _instance = None

    def __new__(cls, path=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 支持通过环境变量指定配置文件
            if path is None:
                path = os.getenv("CONFIG_FILE", "./config.yaml")
            cls._instance._load(path)
        return cls._instance

    def _load(self, path):
        self.path = Path(path)
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f)




    def get(self, key_path, default=None):
        keys = key_path.split(".")
        val = self.data
        for k in keys:
            val = val.get(k)
            if val is None:
                return default
        return val

    def set(self, key_path, value):
        """设置配置项（支持点号分隔的路径），返回旧值"""
        keys = key_path.split(".")
        val = self.data
        for k in keys[:-1]:
            if k not in val:
                val[k] = {}
            val = val[k]
        last_key = keys[-1]
        old_value = val.get(last_key)
        val[last_key] = value
        return old_value

    def save(self):
        """将当前配置写回 YAML 文件"""
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def make_model_config(self):
        """创建类型化的 ModelConfig"""
        from Core.Config import ModelConfig
        llm = self.get("model.large-language-model", {}) or {}
        local = llm.get("local_hf", {}) or {}
        return ModelConfig(
            provider=llm.get("provider", "local_hf"),
            model_name=llm.get("model", ""),
            api_key_env=llm.get("api_key_env", ""),
            base_url=llm.get("base_url", ""),
            max_tokens=llm.get("max_tokens", 1024),
            temperature=float(llm.get("temperature", 0.7)),
            top_p=float(llm.get("top_p", 0.9)),
            use_native_tools=llm.get("use_native_tools", True),
            api_timeout=int(llm.get("api_timeout", 120)),
            hf_model_path=local.get("path", ""),
            hf_device_map=local.get("device_map", "cuda"),
            hf_trust_remote_code=local.get("trust_remote_code", True),
            hf_load_in_4bit=local.get("load_in_4bit", True),
            hf_bnb_4bit_quant_type=local.get("bnb_4bit_quant_type", "nf4"),
            hf_bnb_4bit_compute_dtype=local.get("bnb_4bit_compute_dtype", "float16"),
            hf_bnb_4bit_use_double_quant=local.get("bnb_4bit_use_double_quant", True),
            hf_max_new_tokens=local.get("max_new_tokens", 1024),
            hf_repetition_penalty=float(local.get("repetition_penalty", 1.1)),
        )

    def make_agent_config(self):
        """创建类型化的 AgentConfig"""
        from Core.Config import AgentConfig
        return AgentConfig(
            maxDepth=int(self.get("agent.maxDepth", 200)),
            max_context_messages=int(self.get("agent.max_context_messages", 20)),
            micro_compact_enabled=self.get("agent.micro_compact.enabled", True),
            micro_compact_age_threshold_hours=int(self.get("agent.micro_compact.age_threshold_hours", 1)),
            micro_compact_keep_tool_results=int(self.get("agent.micro_compact.keep_tool_results", 5)),
            context_compact_enabled=self.get("agent.context_compact.enabled", True),
            context_compact_threshold_tokens=int(self.get("agent.context_compact.threshold_tokens", 80000)),
            context_compact_trigger_before_step=self.get("agent.context_compact.trigger_before_step", True),
            snip_enabled=self.get("agent.snip.enabled", True),
            snip_reminder_threshold_tokens=int(self.get("agent.snip.reminder_threshold_tokens", 40000)),
            background_watch_window=int(self.get("agent.background_watch_window", 1800)),
            background_max_reactivations=int(self.get("agent.background_max_reactivations", 3)),
        )

    def make_memory_config(self):
        """创建类型化的 MemoryConfig"""
        from Core.Config import MemoryConfig
        return MemoryConfig(
            root_dir=self.get("memory.root_dir", "./runtime_memory"),
            sqlite_enabled=self.get("memory.sqlite.enabled", True),
            sqlite_path=self.get("memory.sqlite.path", "./runtime_memory/memory.db"),
            retrieval_enabled=self.get("memory.retrieval.enabled", False),
            retrieval_fts_top_k=int(self.get("memory.retrieval.fts_top_k", 8)),
            retrieval_embedding_top_k=int(self.get("memory.retrieval.embedding_top_k", 8)),
            retrieval_final_top_k=int(self.get("memory.retrieval.final_top_k", 6)),
            retrieval_query_window_messages=int(self.get("memory.retrieval.query_window_messages", 6)),
            retrieval_use_reranker=self.get("memory.retrieval.use_reranker", False),
            retrieval_embedding_model_name=self.get("memory.retrieval.embedding_model_name", "Qwen3-Embedding-0.6B"),
            retrieval_embedding_model_path=self.get("memory.retrieval.embedding_model_path", "./models/embedding_model/Qwen3-Embedding-0.6B"),
            retrieval_bm25_enabled=self.get("memory.retrieval.bm25_enabled", True),
            retrieval_bm25_top_k=int(self.get("memory.retrieval.bm25_top_k", 8)),
            retrieval_embedding_provider=self.get("memory.retrieval.embedding_provider", "local"),
            retrieval_embedding_base_url=self.get("memory.retrieval.embedding_base_url", ""),
            retrieval_embedding_api_key_env=self.get("memory.retrieval.embedding_api_key_env", ""),
            retrieval_embedding_dimensions=int(self.get("memory.retrieval.embedding_dimensions", 768)),
            retrieval_embedding_timeout=int(self.get("memory.retrieval.embedding_timeout", 30)),
            retrieval_reranker_model_path=self.get("memory.retrieval.reranker_model_path", "./models/rerank_model/bge-reranker-v2-m3"),
            pipeline_db_enabled=self.get("memory.pipeline.db_enabled", True),
            prompt_use_legacy_memory_markdown=self.get("memory.prompt.use_legacy_memory_markdown", True),
            export_daily_json_enabled=self.get("memory.export.daily_json_enabled", True),
            session_id=self.get("memory.session_id", "default"),
            daily_dir=self.get("memory.daily.dir", "./runtime_memory/daily"),
            retention_days=max(1, int(self.get("memory.daily.retention_days", 7))),
            index_path=self.get("memory.index.path", "./runtime_memory/MEMORY.md"),
            user_path=self.get("memory.user.path", "./runtime_memory/USER.md"),
            user_review_enabled=self.get("memory.user.review_enabled", True),
            user_review_interval=int(self.get("memory.user.review_interval", 10)),
            topic_dir=self.get("memory.topic.dir", "./runtime_memory/topic"),
            topic_merge_every_n=int(self.get("memory.topic.merge_every_n_summaries", 3)),
            topic_merge_min_count=int(self.get("memory.topic.merge_min_count", 4)),
        )

    def make_tool_config(self):
        """创建类型化的 ToolConfig"""
        from Core.Config import ToolConfig
        return ToolConfig(
            background_timeout=int(self.get("tools.background_timeout", 120)),
            result_budget_max_single=int(self.get("tools.result_budget.max_single_tokens", 8000)),
            result_budget_max_batch=int(self.get("tools.result_budget.max_batch_tokens", 24000)),
            result_budget_cache_dir=self.get("tools.result_budget.cache_dir", "./runtime_memory/tool_cache"),
            security_enabled=self.get("tools.security.enabled", True),
            security_allow_confirmation=self.get("tools.security.allow_confirmation", False),
            timeout=int(self.get("tools.timeout", 60)),
            runtime=self.get("tools.runtime", "in_process"),
        )

config = Configuration()