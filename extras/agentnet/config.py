import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("AGENTNET_DB", str(BASE_DIR / "agentnet.db"))
MODEL_DIR = os.getenv("AGENTNET_MODEL_DIR", str(BASE_DIR / "models" / "Qwen3-Embedding-0.6B"))
MODEL_NAME = os.getenv("AGENTNET_MODEL_NAME", "Qwen3-Embedding-0.6B")

FTS_TOP_K = int(os.getenv("AGENTNET_FTS_K", "10"))
EMB_TOP_K = int(os.getenv("AGENTNET_EMB_K", "10"))
FINAL_TOP_K = int(os.getenv("AGENTNET_FINAL_K", "10"))
RRF_K = int(os.getenv("AGENTNET_RRF_K", "60"))

# Curator（能力原子管理员）所用的小模型：复用 MiniMax 的 anthropic 兼容端点
CURATOR_BASE_URL = os.getenv("AGENTNET_LLM_BASE_URL", "https://api.minimaxi.com/anthropic")
CURATOR_MODEL = os.getenv("AGENTNET_LLM_MODEL", "MiniMax-M2.7")
CURATOR_API_KEY_ENV = os.getenv("AGENTNET_LLM_KEY_ENV", "ANTHROPIC_API_KEY")
CURATOR_TOP_K = int(os.getenv("AGENTNET_CURATOR_K", "8"))
