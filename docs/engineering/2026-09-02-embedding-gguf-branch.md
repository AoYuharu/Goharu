# llama-cpp-python 在本机全版本崩溃：Embedding GGUF 分支排障记录

> 日期：2026-09-02 ｜ 涉及：`Memory/retrieval/EmbeddingService.py`、`config.yaml`、`memory.db`
> 环境：Windows 11（Hyper-V 虚拟化标识开启）+ AMD Ryzen 9 7945HX + Anaconda Python 3.12

## 目标

为记忆检索的 embedding 启用本地 GGUF 分支（llama-cpp-python），替代 PyTorch 首次加载 11.3s 的 SentenceTransformer 路径。

## 排查过程与结论

| 尝试 | 结果 |
|---|---|
| 清华源装 llama-cpp-python | 无 cp312 预编译 wheel，源码编译失败（无 MSVC） |
| abetlen 官方 CPU wheel 索引（0.3.35 / 0.3.19 / 0.3.18） | 装上但 `llama_backend_init()` **access violation reading 0x0**——ctypes 直接调 llama.dll 同样崩，ggml.dll 系单独调用正常，判定 wheel 与本机环境二进制不兼容 |
| 0.2.90 / 0.3.2（不崩） | llama.cpp 太旧，`unknown model architecture: 'qwen3'` / embeddinggemma 同样不支持 |
| HF 直连 / hf-mirror 下模型 | 均不通；**ModelScope 镜像可用**（`modelscope.cn/models/<org>/<name>/resolve/master/<file>`） |
| GitHub release 二进制（llama-server 方案） | GitHub 被墙，ghproxy 系仅 ghfast.top 通 HTML 但 API 403 |

**结论**：llama.cpp 路线在本机不可行——能跑的旧版不支持新模型架构，支持新架构的版本全部崩溃。放弃 GGUF 分支，Qwen3-Embedding-0.6B（SentenceTransformer）扶正为本地主力。

## 落地的修复

1. `EmbeddingService` fallback 链三层 `except: pass` 全部改为 `logger.warning` 带原因（这次排障最大的教训：**静默降级让 GGUF 分支死了几个月无人知晓**）。
2. `_encode_local_gguf` 兼容 llama-cpp 新旧两种返回结构（`{"data":[{embedding}]}` / `{"embedding"}`）。
3. GGUF 文件名改为可配置 `memory.retrieval.embedding_gguf_file`。
4. `config.yaml` 的 `embedding_dimensions: 768` → 1024（Qwen3 实际维度；原值纯属误导）。
5. memory.db 清理：删除 2 条 64 维 hash 假向量，回填 5 条缺失 embedding 的 L1 原子。终态 128 条向量全部 1024 维。

## 遗留

- `models/embedding_model/` 下两个 GGUF（gemma 313MB + Qwen3 610MB）在本机无法使用，留待清理或将来换机使用。
- 若将来要重启 GGUF 路线：优先试 llama.cpp 官方 `llama-server` 二进制（OpenAI 兼容 `/v1/embeddings`，EmbeddingService 的 remote 分支可直接对接，进程外运行还有崩溃隔离的好处）。
