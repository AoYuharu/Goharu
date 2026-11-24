# Agent/ 维护者文档

ReAct 循环层。本目录大部分模块是 Goharu 运行时内部件（PipelineManager、
SubAgent 等；ContextCompactor 与 loop_messages 已归 Prompting/，压缩阶梯的
纯工具 TokenEstimator/ToolResultBudget 在 Core/）；BackgroundTaskManager 已归入
Tools/（background_tasks.py，工具后台化是工具运行时的职责）。对外门面是
`api.py`（`create_agent` / `StandaloneAgent`），接入契约见 `README.md`。

## 结构与边界

```
宿主 → Agent.api.StandaloneAgent.run_turn（turn 级循环：max_steps、事件映射）
        └─ ActorAgent.act（step 级：组装 prompt → LLM → 工具并发执行 → 回写记忆）
             ├─ Prompting/  上下文管线（缓存断点契约在 Prompting/CLAUDE.md）
             ├─ Tools/      工具运行时（call_tool / last_tool_definitions）
             └─ working     记忆结构化接口（append / get_context / [retrieve_prompt_memory]）

AgentDelegate → SubAgent.execute（子代理 turn 驱动：迭代上限/倒计时/JSONL/verdict）
        └─ 同一个 ActorAgent.act——循环代码只有一份，子代理差异经三缝注入：
           document_builder（提示词）、_SubToolRuntime（工具白名单+命令策略）、
           tool_result_trailing_texts（每轮倒计时/提醒）
```

## 不变量（改动前必读）

1. **ActorAgent 只认结构化接口**：tool_runtime 需 `last_tool_definitions` /
   `list_tools()` / `call_tool()`；working 需 `append` / `get_context`，
   `retrieve_prompt_memory` 可选（hasattr 检查 + `memory.retrieval.enabled`）。
   新增对具体类（MemoryManager 等）的直接引用是破坏解耦。
2. **llm 注入点**：`ActorAgent(tool_runtime, working, llm=None)`——llm=None 必须
   严格保持 Goharu 原行为（LLMCore 单例）；注入时 `self.core=None`，
   `query()` 转发。`self.core` 的触点为 `_should_use_native_tools`（注入→True）
   和 usage 提取（注入→`response.usage`），新增 core 触点必须同样判空。
3. **act() 返回契约**：dict 带 `type` ∈ answer / tool / tool_batch /
   tool_backgrounded / continue / interrupted / error；answer 带 `answer` 键，
   error 带 `error` 键。StandaloneAgent 与 Gateway 都依赖该枚举，增删类型
   要同步两侧。
4. **事件只走注入回调**（on_tool_call_start / on_thinking / on_text /
   on_tool_result / on_retry / on_built）：ActorAgent 不直接写 UI。
5. **verify 闸门**：`verify_nudge.gate_final_answer`（Tools 层）在 answer 前
   拦截未验证的修改——默认开启，配置 `tools.verify_nudge.*` 控制。
6. **确认回调线程模型**：`create_agent(on_confirm=)` 的回调在**工具线程**上
   同步执行（confirmation_manager 的 request 阻塞该线程等 resolve）——回调内
   禁止再等待 asyncio 事件循环（会死锁）。
7. **截断/消毒/消息组装唯一实现**：`Prompting/loop_messages.py` 是 ReAct 循环
   消息协议的唯一来源——`truncate_history`（配对安全截断）、
   `sanitize_messages_for_api`（配对完整性消毒）、`build_tool_result_message`
   （tool_result 块必须位于 user 消息最前，提示文本只能追加在后；
   DeepSeek Anthropic 兼容端点的硬约束，2026-10-17 子代理 400 事故）。
   ActorAgent / SubAgent / ContextManager 都委托它们，**禁止在任一循环里手写**。
   改动后跑 `tests/test_loop_messages.py`。
   （2026-10-27 起该模块归属 Prompting/——消息协议是上下文管理职责，
   Agent 层 import 使用，方向 Agent→Prompting 合法。）
8. **import 副作用**：模块级 `_LLM_EXECUTOR` 线程池 + atexit——引入新模块级
   资源要有同样的清理配对。
9. **LLMCore 单例身份**：无参 `LLMCore()` 必须是全局唯一实例（`__new__` 里
   赋值 `_instance`），Gateway 与 ActorAgent 靠它共享 `_last_usage` 等状态；
   独立实例只能由 `LLMCore(llm_config=dict)` 产生（Agent/llm.py 门面）。
   改动后有契约测试 `tests/test_llm_standalone.py` 的"全局单例仍是单例"把守。
10. **SubAgent 注入触点**：`self.llm` 可能是注入对象（无 `.core` 属性）——
    任何 `llm.core.*` 访问必须先判空（先例：`_should_use_native_tools`），
    `get_last_usage()` 用 hasattr 守卫。子代理 llm 由
    `Tools/subagent_config.py`（无副作用持有模块）传递，禁止在
    agent_delegate.py 里存注入值（import 即注册工具，有副作用）。
    SubAgent 自身不再直接调 LLM：`execute()` 把 `self.llm` 转交
    ActorAgent 引擎，判空职责在引擎侧。
11. **循环引擎唯一**（2026-10-18 thinking 压平 400 事故后收敛）：主/子循环
    共用 `ActorAgent.act()`。子代理差异只经三缝注入：`document_builder`
    （提示词，system+task+reminder，无记忆/persona）、`_SubToolRuntime`
    （allowed_tools 白名单 + run_cmd 命令策略，直连 registry.dispatch）、
    `tool_result_trailing_texts`（每轮倒计时 + CRITICAL_REMINDERS）；
    子代理构造引擎时 `verify_gate_enabled=False`。**禁止在 SubAgent 里
    手写 LLM 调用 / 截断续写 / assistant 消息重建 / 工具执行**——那是引擎
    职责。ActorAgent 的递归重入点（messages=None / 截断续写重建）必须走
    `_build_step_messages()`，否则 document_builder 路径的递归会落回默认
    组装器。改动后跑 `tests/test_subagent_loop_unified.py`（含 thinking
    结构化回传回归：截断续写必须以 `{"type":"thinking"}` 块回传）。

12. **prompt_loader 注入缝**（2026-11-24）：`create_agent(prompt_loader=)` →
    `ActorAgent(prompt_loader=)` → `PromptAssembler(prompt_loader=)`。None →
    Prompting 默认加载器（全局配置 prompt.system.*），Goharu 原行为不变；宿主
    组合根可注入"叠加了功能段"的加载器（`Prompting.api.create_prompt_loader` +
    Gateway/prompt_sections.py），让未启用的功能在 system 里零痕迹。
    Agent 层只认"加载器"这个通用对象，禁止在此 import 任何具体功能的开关。

## 接口契约测试

`tests/test_agent_loop_standalone.py`（tests/ 目录）：剧本化假 LLM 驱动真实 ActorAgent——
工具流 / 事件序列 / MemoryBridge 与内存态 / max_steps / 错误路径 / llm=None 回退。
改 ActorAgent 或 api.py 后必跑；改 prompt 组装后另跑 `tests/test_cache_breakpoints.py`。
`tests/test_subagent_loop_unified.py`：假 LLM + 假 registry 驱动真实 SubAgent.execute()
（内部走同一引擎）——thinking 结构化回传回归 / 白名单拒绝 / 倒计时注入 /
迭代上限 partial / verify VERDICT / 子代理断点与前缀稳定。改 SubAgent 或引擎注入缝后必跑。
