# Goharu Tools — 即拆即用的工具调用子系统

进程内工具运行时：注册表 + JSON Schema 校验 + 线程池分发 + 命令安全层 + MCP 桥接。
设计目标：任何 agent harness 不依赖 Goharu 其余部分即可接入。

## 依赖安装

- **最小运行**：零第三方依赖（纯标准库），核心工具（run_cmd/文件/搜索/Sleep/Glob 等）直接可用。
- 按需安装（pip 一律走国内源）：

```bash
pip install PyMuPDF -i https://pypi.tuna.tsinghua.edu.cn/simple   # pdf_tools / pdf_annotate_tools
pip install mcp httpx -i https://pypi.tuna.tsinghua.edu.cn/simple  # MCP 桥接（enable_mcp=True）
# document_tools 按需懒加载：python-docx（.docx）/ openpyxl（.xlsx）等
```

## 快速接入（外部 harness）

```python
import asyncio
from Tools.api import create_tools, close_tools

async def main():
    tools = await create_tools(
        # dict 即可：扁平点号键或嵌套 dict 都支持；不传则用默认值
        config={"tools": {"background_timeout": 120}},
        # 只加载你需要的工具模块（不传则读配置 tools.builtin_modules）
        modules=["Tools.builtin.core_tools", "Tools.builtin.file_tools"],
    )
    defs = await tools.list_tools()       # 喂给 LLM 的工具定义
    result = await tools.call_tool("run_cmd", {"command": "git status"})
    print(result.content)                 # 字符串结果（JSON 或格式化文本）
    await close_tools(tools)

asyncio.run(main())
```

> 唯一硬性要求：把 `Tools/` 所在目录（即 Goharu 仓库根）放到 `sys.path`。
> 不传入 config 时，若环境中存在 Goharu 的 `configurationLoader` 则自动回退到它。

## 公共 API

| 接口 | 签名 | 说明 |
|---|---|---|
| 创建运行时 | `await create_tools(config=None, modules=None, enable_mcp=False, on_task_change=None, on_confirm_needed=None, on_question=None, on_tool_output=None, on_subagent_event=None, memory=None) -> InProcessToolRuntime` | 注入配置、加载内置工具、可选启动 MCP（start 失败降级不拖垮运行时）；hook 参数把宿主的任务变更/确认/提问/流式输出/子代理事件回调接入对应模块单例；`memory=` 注入 memory_search/memory_add 的记忆对象 |
| 关闭 | `await close_tools(runtime)` | 断开 MCP、释放资源 |
| 工具定义 | `await runtime.list_tools() -> [{name, description, inputSchema}]` | 同步刷新 `runtime.last_tool_definitions` |
| 后台任务 | `runtime.background_tasks -> BackgroundTaskManager` | drain_pending / inject_into_memory / register_callback / reactivation_count |
| 调用工具 | `await runtime.call_tool(name, arguments) -> ToolResult` | `.content` 为字符串；特殊参数 `run_background=True` 转入后台（见下） |
| 注册自定义工具 | `registry.register(name, description, arguments_schema, handler, group=None, is_async=False)` | handler 返回字符串（约定 JSON） |
| 直接分发 | `await registry.dispatch(name, arguments) -> str` | 绕过 runtime 后处理（格式化/nudge） |
| 命令安全检查 | `check_command_safety(cmd) -> (bool, reason \| None)` | run_cmd 同款黑名单 |
| 配置注入 | `set_config(cfg)` / `get_config()` / `reset_config()` | 见"配置"一节 |

## 返回值约定

- 工具 handler 返回**字符串**；内置工具统一返回 JSON：`{"status": "success", ...}` 或 `{"status": "error", "reason": ..., "error": ...}`。
- `runtime.call_tool` 会在结果后追加**格式化文本**（`tools.result_format.enabled`）和**行为提醒**（verify_nudge，见配置表），最终仍是一个字符串。
- 未知工具 / 参数校验失败不会抛异常，而是返回 `{"error": ...}` JSON。

## 配置

`create_tools(config={...})` 传入 dict；扁平点号键与嵌套 dict 等价。

| 键 | 默认 | 说明 |
|---|---|---|
| `tools.builtin_modules` | `["Tools.builtin.core_tools"]` | 启动时加载的工具模块列表 |
| `tools.background_timeout` | `120`（秒） | 超时后转后台执行；<=0 表示永不后台化 |
| `tools.background_max_duration_seconds` | `150` | 自动转后台任务的硬超时，到点 cancel 并回注超时说明（仅自动转后台的任务，显式 run_background 不设限） |
| `tools.background_exempt_tools` | `["AskUser"]` | 永不后台化/不设超时的工具名（Goharu 配置追加了 `Sleep`——等待类工具必须前台完成，否则等待语义被破坏） |
| `tools.background_no_cancel_tools` | `["AgentDelegate"]` | 自动转后台后**不设硬超时取消**的工具名（子代理有自己的迭代预算自终止；150s 定时器会误杀合法长跑的 verify，2026-10-18 事故） |
| `tools.result_format.enabled` | `true` | 工具结果 JSON → 模型可读文本的格式化 |
| `tools.security.enabled` | `true` | run_cmd 危险命令拦截总开关 |
| `tools.security.dangerous_commands` | 内置列表 | 硬黑名单模式串 |
| `tools.security.allow_confirmation` | `false` | 灰名单命令是否走用户确认回调 |
| `tools.security.require_confirmation` | 内置列表 | 灰名单模式串 |
| `tools.verify_nudge.enabled` | `true` | 连续修改未验证时的提醒 |
| `tools.verify_nudge.mutation_threshold` | `3` | 触发提醒的**代码**修改次数（.md/.txt/临时文件不计） |
| `tools.verify_nudge.gate_final_answer` | `true` | 未验证时硬拦截最终回答（**仅限高风险目录** Agent/Tools/Gateway/TUI/prompts/config；普通代码只提醒不拦截） |
| `tools.verify_nudge.max_gate_fires` | `2` | 硬拦截最大次数（防死循环） |
| `tools.mcp.servers` | `{}` | MCP server 静态配置（见下） |
| `agent_delegate.<type>.max_concurrent` | `3` | 子代理并发上限（Goharu 专用工具） |

## 自定义工具

```python
from Tools.api import registry

def my_tool(text: str) -> str:
    import json
    return json.dumps({"status": "success", "echo": text}, ensure_ascii=False)

registry.register(
    name="my_tool",
    description="Echo the input text.",
    arguments_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to echo"}},
        "required": ["text"],
    },
    handler=my_tool,        # 同步函数自动进线程池；async 函数自动 await
    group="custom",
)
```

要点：
- handler 参数名必须与 schema properties 一致；registry 会按 schema 做类型校验。
- 同步 handler 经 `run_in_executor` 在线程池执行——**handler 必须线程安全**；写同一文件类资源请自管锁（先例：`Tools/builtin/pdf_annotate_tools.py` 的 per-path 锁）。
- 写文件请走 Read/Edit/Write 工具的权限约定；`run_cmd` 拒绝 shell 写操作（`echo >`、`sed -i` 等）。

## MCP 桥接（可选）

```python
tools = await create_tools(config={
    "tools": {"mcp": {"servers": {
        "my-server": {"command": "python", "args": ["my_mcp_server.py"]},
        # 或 HTTP: {"url": "http://localhost:8000/mcp"}
    }}}
}, enable_mcp=True)
```

- MCP 工具以 `mcp__<server>__<tool>` 注册进同一 registry，调用方式无差别。
- 运行期可通过内置工具 `mcp_manage` 热插拔（持久化在 `runtime_memory/mcp_servers.json`）。
- 需要可选依赖：`pip install mcp httpx`。

## 内置工具模块与依赖

| 模块 | 工具 | 额外依赖 | 独立可用 |
|---|---|---|---|
| `core_tools` | run_cmd, Write/Read/Edit/Grep（内含 file_tools、pdf_tools、knowledge_tools） | pdf 部分需 PyMuPDF | ✅ |
| `file_tools` | Write, Read, Edit, Grep | 无 | ✅ |
| `glob_tool` | Glob | 无 | ✅ |
| `sleep_tool` | Sleep | 无 | ✅ |
| `ask_user_tools` | AskUser | 需宿主注册 `question_manager.set_notifier`（无前端时返回 unavailable 降级） | ⚠️ 需宿主接线 |
| `system_info_tool` | get_system_info | 无 | ✅ |
| `pdf_tools` | parse_pdf | PyMuPDF | ✅ |
| `pdf_annotate_tools` | pdf_highlight / pdf_annotate / pdf_draw / pdf_list_annotations / pdf_delete_annotations | PyMuPDF | ✅ |
| `document_tools` | parse_document | 按需懒加载（python-docx 等） | ✅ |
| `skill_tool` | Skill | 读宿主的 skills 目录约定 | ⚠️ 需目录约定 |
| `agent_delegate` | AgentDelegate | 注入 llm 后可用（`create_agent(subagent_llm=)`）；否则回退 Goharu LLMCore | ✅ |
| `reload_tools` | ReloadTools | 无（热重载内置工具模块） | ✅ |
| `mcp_tools` | mcp_manage | mcp, httpx | ✅（启用 MCP 时） |
| `architect_tools` | ConsultArchitect / MessageHero / SetConfig | 宿主带 Architect 模块（惰性 import 降级） | ⚠️ 需 Architect |
| `error_hooks_tools` | ErrorHook | 宿主带 Architect 模块 + ErrorHookEngine | ⚠️ 需 Architect |

## 宿主边界（不属于本模块）

- **结果长度预算**：`ToolResultBudget` 在 `Core/`（`Core/ToolResultBudget.py`）；`background_tasks.inject_into_memory` 会惰性使用它，宿主未带 Core 时自动跳过。
- **后台任务管理**：`run_background=True` 或超时（`tools.background_timeout`）自动转后台，由本模块的 `Tools/background_tasks.py`（`BackgroundTaskManager` 单例）承载：任务在独立线程跑，`drain_pending()` 收结果、`inject_into_memory(memory, results)` 回注记忆、`kill_all()` 清理。宿主在轮边界自行 drain（参考 Gateway）。
- **LLM 对话循环**：本模块只提供工具定义与执行，不含 ReAct 循环（示例见 `examples/standalone_demo.py`）。
