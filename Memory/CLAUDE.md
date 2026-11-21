# Memory/ 维护者文档

本目录是**即拆即用的记忆子系统**。外部接入只看 `bridge.py` + `README.md`；
本文件记录内部结构与改动时必须遵守的不变量。

## 架构

```
写入：append → WorkingMemory ─双写→ L0Repository(SQLite l0_messages/l0_turns)
                                └→ daily/<date>.json（tmp+replace 原子写）
L1：   ingest_atoms → MemoryIngestionService（n-gram 去重 + embedding 索引）
L2：   extract_scenes → SceneExtractor（LLM）→ scene_blocks/*.md + L2Repository
L3：   generate_persona → PersonaGenerator（LLM）→ persona.md
读取：retrieve → HybridRetriever（FTS5 ‖ embedding ‖ BM25 → RRF → reranker）
```

装配关系：`MemoryBridge`（共享一条 MemoryDB 通路）是唯一装配门面——
Goharu 主程序（Gateway）与外部 harness 都走 `create_memory()`；
bridge 内部把共享 db 组件注入 `MemoryManager` 并委托行为（实现只有一份）。
自动管线编排在 `Agent/PipelineManager`（不在本目录）。

## 不变量（改动前必读）

1. **本目录禁止 import Agent./Gateway. 的模块**（LLM 只允许构造注入 `llm=`；
   仓库内回退 `Agent.LargeLanguageModel` 必须是**惰性 import** 且带说明性报错）。
2. **配置只走 `memory_config.resolve_config`**：接受 dict / MemoryConfig / None
   （None 回退全局 config.yaml）。新增配置键给默认值并同步 README 配置表。
   ⚠️ 教训（2026-10-10 真实事故）：构造管线组件（SceneExtractor 等）必须**显式
   注入共享 db 的仓储**——其缺省 `L1Repository()` 会按全局 config 自建连接，
   overrides 指向独立目录时写操作会静默落到宿主生产库。bridge.py 已修复，
   新增管线组件时同样遵守"仓储由装配方注入"。
3. **MemoryDB 连接是线程本地的**（`threading.local`）：跨线程使用各自建连；
   新增进程级资源要考虑 WAL 下的文件锁（Windows 不 close 删不掉文件）。
4. **降级链不许抛异常穿透**：embedding/reranker/jieba 任一不可用都要静默降级
   （encode 返回 []），检索至少保留 FTS5+BM25。向量腿失败**禁止写假向量**。
5. **daily JSON 原子写**：临时文件 + os.replace；retention 按天裁剪。
6. **检索返回结构稳定**：`{profile_summary, memories[], scenes[]}`，条目含
   `record_type/source_id/title/text/score`——外部 harness 依赖此契约。
7. **prompt 注入不在本模块**：retrieve 只出数据块；贴到 prompt 的哪个位置、
   缓存断点策略是宿主 assembler 的职责（Goharu 的缓存铁律见根 CLAUDE.md）。
8. **审计缝 `change_events.py`**：L1 的 upsert/deactivate、scene 文件写/删、
   persona 写必须 emit 变更事件（listener 默认 no-op，宿主装配期注入——
   不得 import Checkpoint）。`L1Repository.restore_atom(old_row, atom_id)` 是
   回退的唯一 SQL 通道（三表同步：l1_memory_atoms + fts_memory + bm25_sparse），
   经 `bridge.restore_l1_atom` 暴露；新增 L1 写路径必须同步接 emit。

## 接口契约测试

`tests/test_memory_standalone.py`（tests/ 目录）是外部 harness 视角的接口契约测试，
覆盖 append/get_context/clear/ingest_atoms/retrieve/extract_scenes/
generate_persona/get_persona/close 全部公共接口。改动任何公共接口后必须跑。

embedding 在测试中通过指向空目录快速降级（避免加载 1.19GB 模型）；
FakeLLM（`query(messages)->str`）验证 LLM 注入链路不打真实 API。
