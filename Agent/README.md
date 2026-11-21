# Goharu Agent Loop — 即拆即用的 ReAct 循环框架

把 ActorAgent（单步推理 + 工具执行）包装成完整的用户轮驱动：外部 harness
注入自己的 LLM / 工具 / 记忆即可跑通"模型决策 → 工具执行 → 多步循环 → 最终回答"。

## 依赖安装

零第三方依赖（纯标准库）；工具/记忆按各自 README 按需安装。

## 快速接入

```python
import asyncio
from Tools.api import create_tools, close_tools
from Memory.bridge import create_memory
from Agent.api import create_agent

async def main():
    tools = await create_tools(modules=["Tools.builtin.core_tools"])
    memory = create_memory(overrides={"memory.root_dir": "./my_memory"})  # 可选

    agent = await create_agent(
        tools=tools,
        llm=my_llm,          # 契约见下；None → Goharu LLMCore 单例
        memory=memory,       # None → 纯内存工作记忆（不持久化）
        on_event=lambda etype, payload: print(etype, payload),
        max_steps=8,
    )
    reply = await agent.run_turn("帮我看看当前目录有什么文件")
    print(reply)

    memory.close()
    await close_tools(tools)

asyncio.run(main())
```

完整可运行示例：`examples/agent_loop_demo.py`（假 LLM 离线跑通全流程）。

## LLM 访问层（`Agent/llm.py`，独立可拆）

**职责只有一件：可靠地把 messages 发给模型并拿回统一格式的响应。**
与 AgentLoop 的差别——LLM 层管"怎么调"（传输+韧性），AgentLoop 管"调完之后怎么决策"（工具循环）。两者**不需要合并**，连接点是注入契约：

```python
from Agent.llm import create_llm
from Agent.api import create_agent

llm = create_llm({                    # ← LLM 访问层：重试/退避/用量统计/provider 抽象
    "provider": "anthropic_compatible",   # 或 "openai_compatible" / "auto"（自动探测）
    "model": "claude-sonnet-4-6",
    "api_key_env": "ANTHROPIC_API_KEY",   # 从该环境变量读 key
    "max_retries": 10, "api_timeout": 120,
})
agent = await create_agent(tools=tools, llm=llm)      # 方式一：显式传入
agent = await create_agent(tools=tools, llm_config={...})  # 方式二：便利参数（等价）
```

`LLMClient` 接口：

| 接口 | 说明 |
|---|---|
| `query(messages, on_retry=None, tools=None, tool_choice=None)` | 带 tools → 返回 `NormalizedResponse`（`.content` 块 / `.stop_reason` / `.usage`）；不带 → 返回纯文本 str |
| `provider_name` / `supports_native_tools` | 当前 provider 信息 |
| `get_last_usage()` | 上次调用用量（含 cache_read/cache_creation 细分，算缓存命中率用） |
| `get_last_thinking()` | 上次调用的 thinking 内容 |
| `get_token_size(text)` | token 估算 |
| `reload(new_config)` | 热切换模型/配置，返回 `(ok, error)` |

**与 Goharu 的关系**：`create_llm(config=dict)` 创建独立实例（不读全局 config.yaml）；
Goharu 内部仍用 `LLMCore()` 单例，两者互不干扰（单例身份与用量共享已测试锁定）。
内置 provider：`anthropic_compatible`、`openai_compatible`、`auto`（依次探测）。
自定义 provider：实现 `Agent/providers/base.py` 的 `LLMProvider` 协议
（create_client/probe/build_request/normalize_response）并注册进 `providers._PROVIDERS`。

## 注入契约

### llm（可选；None 时走 Goharu LLMCore）

结构化接口对象，只需一个方法：

```python
class MyLLM:
    def query(self, messages, on_retry=None, tools=None, tool_choice=None, **kw):
        # messages: Anthropic Messages 格式（build_messages 的输出）
        # tools: Anthropic 原生工具定义 [{"name","description","input_schema"}]
        # 返回带以下属性的对象：
        #   .content      块列表：[{"type":"text","text":...} /
        #                {"type":"thinking","thinking":...} /
        #                {"type":"tool_use","id":...,"name":...,"input":{...}}]
        #   .stop_reason  "end_turn" / "tool_use" / "max_tokens"
        #   .usage        可选，token 用量 dict
        ...
```

### memory（可选；None 时内存态）

任何提供 `append(dict)` + `get_context() -> list` 的对象；
若另有 `retrieve_prompt_memory()`（如 MemoryBridge），每轮自动检索注入。
**消息写入由 ActorAgent 负责**（user/assistant/tool_result 往返全记录），
宿主无需手动 append 助手消息。

### on_event（可选）

`on_event(event_type: str, payload: dict)`。事件类型：

| event_type | payload | 时机 |
|---|---|---|
| `step` | `{step, max_steps}` | 每个 LLM 步开始 |
| `thinking` | `{text}` | 模型 thinking 块 |
| `text` | `{text}` | 伴随工具调用的文本块 |
| `tool_call` | `{tool, arguments}` | 工具开始执行 |
| `tool_result` | `{tool, preview}` | 单个工具完成（不等其他并发工具） |
| `retry` | `{attempt, max, backoff, phase}` | LLM 重试 |
| `answer` | `{answer}` | 最终回答 |
| `error` | `{error}` | 错误终止 |

### config（可选）

dict，注入 Tools 层配置代理（`tools.background_timeout`、`tools.verify_nudge.*`
等），键表见 `Tools/README.md`。

### subagent_llm（可选）

AgentDelegate 子代理（Explore/Plan/Verify）使用的 LLM，契约同 `llm` 参数。
不传则自动跟随主 `llm`/`llm_config`；可单独指定小模型跑子代理省钱。
子代理享有独立上下文窗口（探索/规划过程不占主对话）。

## 安全沙箱（run_cmd 内联五层链）

工具执行的安全链内建在 `run_cmd` 工具里，外部 harness 通过配置与回调使用：

```
模型工具调用 → ToolCallGuard 参数消毒 → 三态分类（safe/blocked/confirm）
  → confirm 态走 on_confirm 回调 → Windows Job Object 沙箱（POSIX 为空操作）
  → 进程追踪（可 kill_all）
```

- **黑白灰名单**：`tools.security.{enabled, dangerous_commands, require_confirmation, allow_confirmation}`
- **灰名单确认回调**：`create_agent(..., on_confirm=lambda cmd, desc: bool)`
  ——传入即自动开启 `allow_confirmation`；不传则灰名单按黑名单硬拦。
  回调在工具线程上同步调用，返回 True 放行 / False 拒绝。
- **shell 写限制**：`echo >` / `sed -i` 等一律拒绝，引导模型走 Write/Edit 工具。
- 安全组件可从 `Tools.api` 直接取用：`check_command_safety` /
  `CommandSecurityChecker` / `confirmation_manager` / `tool_process_tracker` /
  `translate_command`。

## 策略回退矩阵

| 策略 | 机制 | 配置/参数 | 默认 |
|---|---|---|---|
| LLM 重试 | 指数退避（1/2/4/8s 封顶），5xx/429/超时可重试 | `model.large-language-model.max_retries` | 10 次 |
| provider 回退 | auto 模式依次探测 OpenAI→Anthropic 兼容端点 | `model.large-language-model.provider` | auto |
| 工具失败重试 | 单次推理内非法/失败工具调用重试 | `create_agent(max_tool_retries=)` | 3 次 |
| 文本打捞 | 模型把工具调用写成 `[TOOL_CALL]`/`<minimax:tool_call>` 文本时打捞执行 | 内建，无开关 | 开 |
| 截断续写 | stop_reason=max_tokens 自动续写，最多 2 次 | 内建（MAX_AUTO_CONTINUE） | 2 次 |
| 后台化 | 工具超 `tools.background_timeout` 自动转后台任务（Tools/background_tasks.py 承载），轮边界 drain 回注 | `tools.background_timeout` | 120s |
| verify 闸门 | 连续 N 次文件修改未验证则拦截最终回答 | `tools.verify_nudge.*` | 开（3 次/最多拦 2 次） |
| LLM 错误 | 重试耗尽后返回 error 而非崩溃 | 内建 | — |

## run_turn 行为

`await agent.run_turn(user_message) -> str`：

- 内部循环调用 `ActorAgent.act()`：工具步自动执行并回写记忆，直到最终回答
- 达到 `max_steps` 返回封顶提示；LLM 异常返回 `"Error: ..."`（不抛异常）
- 中断（interrupted）返回提示文本

## 边界（不含，留给宿主）

消息批处理合并、ESC 中断、PipelineManager 自动记忆管线、micro-compact 调度。
Goharu 完整接线参考 `Gateway/gateway_entry.py` 的 `process_message`。

> 子代理（AgentDelegate）**已支持**：`create_agent(subagent_llm=...)` 注入即可，
> 默认跟随主 llm。
