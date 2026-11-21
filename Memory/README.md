# Goharu Memory — 即拆即用的记忆子系统

L0→L1→L2→L3 金字塔记忆：工作记忆 + 混合检索（FTS5 + embedding + BM25 → RRF）。
设计目标：任何 agent harness 不依赖 Goharu 的 Gateway/Agent 层即可接入。

## 依赖安装

- **最小运行**：Python 3.10+ 标准库即可（sqlite3 内置）——L0 工作记忆 + FTS5 检索 + L1 启发式原子全部可用，检索自动退化为 FTS5+BM25。
- 按需安装（pip 一律走国内源）：

```bash
pip install jieba -i https://pypi.tuna.tsinghua.edu.cn/simple                 # 中文分词（BM25/FTS 增强）
pip install requests -i https://pypi.tuna.tsinghua.edu.cn/simple              # 远程 embedding（openai_compatible provider）
pip install sentence-transformers -i https://pypi.tuna.tsinghua.edu.cn/simple # 本地向量检索（含 torch，约 2GB+）
pip install transformers -i https://pypi.tuna.tsinghua.edu.cn/simple          # reranker（use_reranker=true 时）
```

## 金字塔模型

| 层 | 内容 | 存储 | 是否需要 LLM |
|---|---|---|---|
| L0 | 原始对话消息（工作记忆） | SQLite `l0_messages` + daily JSON | 否 |
| L1 | 记忆原子（事实/偏好/事件…） | SQLite + FTS5 + embedding 索引 | 启发式提取不需要；LLM 提取更准 |
| L2 | 场景块（L1 原子的聚合上下文） | `scene_blocks/*.md` + SQLite 索引 | 需要 |
| L3 | 用户画像（persona） | `persona.md` + SQLite | 需要 |

## 快速接入（外部 harness）

```python
from Memory.bridge import create_memory

memory = create_memory(
    overrides={                          # 扁平点号键 dict，全部可选
        "memory.root_dir": "./my_memory",  # 一个键派生全部子路径
    },
    llm=my_llm,                          # 可选：带 query(messages)->str 的对象
)

# 写路径：每轮对话追加
memory.append({"role": "user", "content": "我喜欢喝冰美式咖啡"})
memory.append({"role": "assistant", "content": "好的，记住了。"})

# 读路径：拼 prompt 前检索相关长期记忆
pack = memory.retrieve("用户喜欢什么咖啡")
# pack == {"profile_summary": str, "memories": [...], "scenes": [...]}
# memories[i] = {"record_type": "l1", "source_id": ..., "title": ..., "text": ..., "score": ...}
# scenes[i]   = {"record_type": "l2", ...}（场景块内容）

memory.close()  # 退出前调用：释放 SQLite 文件锁（Windows 必须）
```

> 唯一硬性要求：把 `Memory/` 所在目录（即 Goharu 仓库根）放到 `sys.path`。
> 纯 stdlib + sqlite3 即可跑通基础链路（检索退化为 FTS5+BM25）。

## 公共 API（`MemoryBridge` / `create_memory`）

| 接口 | 签名 | 说明 |
|---|---|---|
| 工作记忆写入 | `append(message)` | dict（`{"role", "content"}`）或 ToolCall；自动加 id/时间戳，双写 SQLite + daily JSON |
| 工作记忆读取 | `get_context() -> list[dict]` | 保留期内的全部消息（默认 7 天） |
| 清空工作记忆 | `clear()` | 清空当前会话 |
| 混合检索 | `retrieve(query=None, top_k=None) -> dict` | query=None 时从最近消息自动构建查询 |
| 读取画像 | `get_persona() -> str \| None` | persona.md 全文 |
| L1 入库 | `ingest_atoms(atoms) -> list` | atoms=None 时对当日消息做启发式提取；显式 `[{"atom_type", "subject", "canonical_text"}]` 直接入库（含 n-gram 去重） |
| L2 场景提取 | `extract_scenes() -> list[slug]` | 聚合未分配原子 → scene_blocks/*.md；**需要 LLM** |
| L3 画像生成 | `generate_persona() -> str \| None` | 无活跃场景返回 None；**需要 LLM** |
| 关闭 | `close()` | 关闭当前线程 SQLite 连接 |

## LLM 注入契约

L2/L3 流水线需要 LLM。注入对象只需一个方法：

```python
class MyLLM:
    def query(self, messages: list[dict]) -> str:
        ...  # messages = [{"role": "system"|"user", "content": str}]
```

- 在 Goharu 仓库内且未注入：自动回退 `Agent.LargeLanguageModel`（真实模型）。
- 独立环境且未注入：调用 L2/L3 时抛带说明的 `RuntimeError`。
- 注入的 llm 返回空串：接口优雅返回空结果，不崩溃。

## 流水线：自动 vs 手动

- **Goharu 自动模式**：`Agent/PipelineManager` 按 turn 间隔/延迟/阈值自动触发 L1/L2/L3（配置 `memory.pipeline_manager.*`），不在本模块内。
- **外部手动模式**：`ingest_atoms()` → `extract_scenes()` → `generate_persona()` 自行按需调用（如每 N 轮或会话结束时）。

## 降级链（无重型依赖也能用）

| 组件 | 可用条件 | 不可用时 |
|---|---|---|
| FTS5 全文检索 | SQLite 内置 | 始终可用 |
| BM25 | jieba 可选（无则简单分词） | 可 `bm25_enabled: false` 关掉 |
| embedding 向量腿 | 本地 sentence-transformers 模型存在，或配置远程 `openai_compatible` provider | encode 返回 []，检索自动退化为 FTS5+BM25 |
| reranker | `use_reranker: true` + bge 模型 | 默认关闭，无需配置 |

> 测试/轻量部署想快速禁用向量腿：把 `memory.retrieval.embedding_model_path`
> 指向一个存在但不含模型文件的空目录即可（加载立即失败并进入降级，
> 不会触发网络下载）。

## 配置键（overrides dict，扁平点号键）

| 键 | 默认 | 说明 |
|---|---|---|
| `memory.root_dir` | `./runtime_memory` | 根目录；**设它一个即可派生下列全部路径** |
| `memory.sqlite.path` | `<root>/memory.db` | SQLite 文件 |
| `memory.daily.dir` | `<root>/daily` | daily JSON 目录 |
| `memory.daily.retention_days` | `7` | 工作记忆保留天数 |
| `memory.export.daily_json_enabled` | `true` | daily JSON 双写开关 |
| `memory.session_id` | `default` | 会话隔离标识 |
| `memory.scene_blocks.dir` | `<root>/scene_blocks` | L2 场景块目录 |
| `memory.persona.path` | `<root>/persona.md` | L3 画像文件 |
| `memory.retrieval.fts_top_k` | `8` | FTS5 腿候选数 |
| `memory.retrieval.embedding_top_k` | `8` | 向量腿候选数 |
| `memory.retrieval.bm25_top_k` | `8` | BM25 腿候选数 |
| `memory.retrieval.bm25_enabled` | `true` | BM25 腿开关 |
| `memory.retrieval.final_top_k` | `6` | RRF 融合后返回条数 |
| `memory.retrieval.query_window_messages` | `2` | 自动构建查询时取的最近用户消息条数 |
| `memory.retrieval.use_reranker` | `false` | reranker 开关 |
| `memory.retrieval.embedding_model_name` | `Qwen3-Embedding-0.6B` | 向量模型名 |
| `memory.retrieval.embedding_model_path` | `./models/embedding_model/Qwen3-Embedding-0.6B` | 本地模型路径 |
| `memory.retrieval.embedding_provider` | `local` | `local` / `openai_compatible` |
| `memory.retrieval.embedding_base_url` | `""` | 远程 provider 的 base URL |
| `memory.retrieval.embedding_api_key_env` | `""` | 读取 API key 的环境变量名 |
| `memory.retrieval.embedding_dimensions` | `768` | 向量维度（远程可自定义） |
| `memory.retrieval.embedding_timeout` | `30` | 远程请求超时（秒） |

## 宿主边界（不属于本模块）

- **prompt 注入位置**：本模块只返回记忆数据块（`retrieve` 的 dict），把记忆贴到
  哪条消息、如何保持 prompt cache 前缀稳定，是宿主 assembler 的职责
  （Goharu 侧见 `Prompting/PromptAssembler.py` 的缓存断点契约）。
- **自动流水线调度**：`Agent/PipelineManager`（Goharu 侧）。
- **L0 写时机**：宿主在每轮对话后调用 `append`。
