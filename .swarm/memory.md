# Memory 代理经验档案

> 管辖：`Memory/`。章程：`Memory/CLAUDE.md`。契约测试：`tests/test_memory_standalone.py`。

## 领域知识（长期沉淀）

- 检索三路 RRF：FTS5 + embedding + BM25；降级链不许抛异常穿透，向量腿失败禁写假向量
- MemoryDB 连接线程本地；Windows 上不 close() 删不掉文件（bridge 已补 close）
- **2026-10-10 事故**：管线组件缺省会按全局 config 自建仓储——装配时必须显式注入共享 db 的仓储，否则写操作漏到生产库
- 配置只走 memory_config.resolve_config；root_dir 一键派生子路径
- PipelineManager（自动调度）仍在 Agent/，是已知的有意保留项

## 经验日志（按时间追加，最新在最后）

- 2026-10-11｜蜂群系统建立，档案初始化。
