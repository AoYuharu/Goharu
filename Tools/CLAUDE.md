# Tools/ 维护者文档

本目录是**即拆即用的工具调用子系统**。外部接入只看 `api.py` + `README.md`；
本文件记录内部结构与改动时必须遵守的不变量。

## 架构

```
宿主 (Agent/ActorAgent)
  └─ runtime.call_tool(name, args)          Tools/runtime.py  InProcessToolRuntime
       ├─ 剥离 run_background → 后台化       （Tools/background_tasks.py，本模块内建）
       ├─ registry.dispatch(name, args)     Tools/registry.py  ToolRegistry（全局单例）
       │    ├─ JSON Schema 校验
       │    └─ sync→线程池 / async→await
       └─ 后处理 _post_process              格式化(result_formatters) + verify_nudge
```

## 不变量（改动前必读）

1. **配置只走 `config_proxy.get_config()`**：本目录（含 builtin/、mcp/）禁止
   `from configurationLoader import config`——这是与 Goharu 全局单例的唯一断点。
   新增配置键必须给默认值，并同步到 README 的配置表。
2. **registry 是进程级全局单例**：import builtin 模块即注册。外部 harness 与
   Goharu 共用同一进程时工具会互相可见，属预期行为。
   **条件工具组**（HaruNet 的 `harunet_tools` / `floor_tools`）**不进**
   `tools.builtin_modules` 静态表——未接入组网的智能体连工具描述都不该看见；
   由 Gateway 组合根在 `harunet.enabled` 时 `import` 注册（Tools 本身不认识
   任何功能开关）。新增"仅某功能启用时才存在"的工具照此模式。
3. **handler 线程安全**：同步 handler 在线程池并发执行，宿主可能在同一 LLM 步
   并行下发多个调用。写文件/共享资源必须自管锁（先例：pdf_annotate_tools 的
   per-path `_file_lock`）。
4. **返回字符串**：handler 返回 str（约定 JSON）；runtime 后处理追加格式化文本
   与 nudge。不要让 handler 抛异常穿透——返回 error JSON。
   **所有工具结果必须经 `result_formatters.format_tool_result` 转成自然语言**：
   已登记模板优先；未登记的 JSON 走通用自然语言模板 `_fmt_generic`
   （嵌套缩进、列表分点、布尔转中文），禁止回退到美化 JSON 直接喂模型。
   例外：本身就是标签/纯文本的工具（browser_inspect、Sleep、run_cmd 结果中的
   stdout/stderr）原样放行——它们的结构就是最易读的形态。
5. **file_tools 权限链**：Edit 前先 Read（模块内权限状态）；run_cmd 拒绝 shell
   写操作；security.py 黑名单先于一切执行。
6. **审计缝 `file_change_hook.py`**：写文件工具落盘后必须
   `emit_file_change(path, op, old_bytes, new_bytes, source)`（Write/Edit/
   _save_pdf/music_download 已接）。旧内容必须是**写前原始字节**
   （read_bytes，不走文本模式的换行符转换）。listener 由宿主装配期经
   `create_tools(on_file_change=)` 注入，默认 no-op——不得 import Checkpoint。
7. **ToolResultBudget 不在本目录**（在 Core/）——不要在 Tools 内做结果截断；
   background_tasks 注入记忆时惰性 import 它并判空降级（宿主未带 Core 时跳过）。

## 添加新内置工具

1. 在 `Tools/builtin/` 新建模块或加入现有模块，模块末尾 `registry.register(...)`。
2. 模块名加入 `config.yaml → tools.builtin_modules`（Goharu 侧）；README 的
   模块/依赖表同步更新。
3. 写独立测试文件 `test_<模块>.py`（tests/ 目录），直接调 handler + 必要时
   `asyncio.run(registry.dispatch(...))` 走一遍校验路径。
4. 全面日志：`logger = logging.getLogger(__name__)`，操作入口/结果各一条。

## 测试约定

- 单元/接口测试放 `tests/` 目录，直接 `python tests/test_xxx.py` 运行。
- `tests/test_tools_standalone.py` 是**外部 harness 视角**的接口契约测试：改公共
  API（api.py / registry / runtime 行为）后必须跑它。
- `tests/test_pdf_annotate_tools.py` 覆盖写类工具的并发锁与增量保存先例。
