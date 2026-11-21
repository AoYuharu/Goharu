# Tools 代理经验档案

> 管辖：`Tools/`。章程：`Tools/CLAUDE.md`。契约测试：`tests/test_tools_standalone.py`。
> 主代理派发任务前读本文件并把相关经验带进简报；完成后把新经验追加到"经验日志"。

## 领域知识（长期沉淀）

- registry 是进程级全局单例；同步 handler 经线程池并发执行——写工具必须自管锁（pdf_annotate_tools 的 per-path 锁是先例）
- 安全链顺序：guard 消毒 → security 三态分类 → confirmation → CommandJob 沙箱 → process tracker
- 配置只走 config_proxy（扁平/嵌套 dict 皆可）；模块 import 即注册工具
- 后台任务由 background_tasks.py 承载（已从 Agent/ 迁入）

## 经验日志（按时间追加，最新在最后）

- 2026-10-11｜蜂群系统建立，档案初始化。
- 2026-10-13｜新增 AskUser 交互提问工具：question_manager 镜像 confirmation_manager（Event 阻塞+notifier）。与 confirmation 的关键差异：无前端时确认=拒绝（False 语义安全），提问必须区分 unavailable（降级为正文罗列）和 timeout（按默认方案继续）——manager 需暴露 has_notifier 判据，handler 拿到 None 后二次判定。
- 2026-10-13｜Windows GBK 控制台下 logging/print 中文显示为乱码但不影响逻辑；测试断言不要依赖日志文本。
- 2026-10-13｜ReloadTools 热重载落地：reload 测试必须 cache_from_source 删 pyc + invalidate_caches()，否则同 mtime/大小命中旧 pyc 假成功；import_module 对已加载模块是 no-op；reload 语法错误时旧 handler 完整保留（工具不消失）；runtime 刷新收口用统一 _finalize_success 而非两处各插一行。
- 2026-10-17｜**Sleep 不应自动转后台**：等待类工具（Sleep）超 25s 被自动提升为后台任务后，模型立刻继续执行，等待语义被破坏。已加入 Goharu 配置 `tools.background_exempt_tools: [AskUser, Sleep]`——白名单工具在 runtime 层同步等待、不设超时。另：后台任务 hard-timeout 杀死的结果必须在 TUI 显式提示（`task.background.update` 增补 `timed_out`/`error` 字段），静默消失会让用户以为杀死没生效。
- 2026-10-17｜**工具结果禁止裸 JSON**：`format_tool_result` 的兜底从「美化 JSON」改为通用自然语言模板 `_fmt_generic`（嵌套缩进、列表「-」分点、True/False→是否、主正文字段直出）。约定：除 inspect 这类标签语言有意义的结果外，任何工具结果都必须转成适合模型阅读的文本。改 formatter 后跑 `tests/test_result_formatters.py`。
