# Prompting/ 维护者文档

上下文管理器（ContextManager）：PromptAssembler（组装）+ PromptRenderer（渲染）
+ PromptLoader（提示词文件加载）+ TurnReminders（每轮便利贴，reminders.py）
+ 压缩阶梯（MicroCompactor / 窗口截断 / ContextCompactor）。外部接入看
`api.py` + `README.md`。

## 每轮便利贴（TurnReminders，claude-code system-reminder 机制精简版）

`reminders.py` 提供四种便利贴，由 `_build_turn_context` 聚合并贴当轮用户消息：

| 便利贴 | 内容 | 触发 |
|---|---|---|
| datetime | 当前日期时间（消日期幻觉） | 每轮 |
| date_change | "日期已变为 X" | 跨天（首调只 bootstrap） |
| memory_stats | L1/L2 规模 + persona 新鲜度 + memory_search 引导 | retrieval_pack 带 memory_stats 时 |
| skills | 技能清单 | full（首轮）→ sparse（无变化一行）→ delta（增删只列增量） |

节流状态由调用方持有的 dict 承载（`reminder_state`，与 memory_contexts 同生命周期；
ActorAgent 持有 `_reminder_state`）。不传 state → 无状态退化（date_change 不触发、
技能每轮全量，facade 单次调用的安全默认）。测试：`tests/test_reminders.py`。

## 压缩阶梯层级归属

| 层 | 机制 | 代码位置 | ContextManager 方法 |
|---|---|---|---|
| L0 | 规则微折叠 | Prompting/MicroCompactor.py | `micro_compact` |
| L1 | 窗口截断 | Prompting/loop_messages.py `truncate_history` | `truncate_history` |
| L2 | LLM 摘要压缩 | Prompting/ContextCompactor.py（llm 可注入，None 时惰性回退 Agent.LargeLanguageModel） | `should_llm_compact` / `llm_compact` |
| L3 | 记忆蒸馏 | Memory/ 子系统 | 不在本模块（retrieve 回注） |
| 辅助 | 工具结果预算 | Core/ToolResultBudget.py | `apply_result_budget` |
| 辅助 | token 估算 | Core/TokenEstimator.py | `estimate_tokens` |

不变量：**截断/消毒/消息组装的唯一实现是 Prompting/loop_messages.py 的模块级
纯函数**（`truncate_history` / `sanitize_messages_for_api` /
`build_tool_result_message`），ActorAgent 静态方法与 ContextManager 都必须
委托到它们，禁止平行实现。

边界不变量：本目录只依赖 Core/ 与注入对象，禁止 import Agent./Tools. 的顶层
依赖——git_status / skill_tool / platform_utils 等 Goharu 特有注入一律函数内
惰性 import 且 ImportError 时优雅降级（跳过对应 section，不报错穿透）。

## 缓存断点铁律的代码落点

改任何组装逻辑前必读（根 CLAUDE.md 有完整背景）：

| 铁律 | 落点 |
|---|---|
| 断点 ≤4（实际用 2） | `PromptAssembler.build_actor_document`：system 收敛循环 + 历史轮 `enable_cache` |
| 静态前缀逐字节稳定 | system 区只来自提示词文件 + git_status（30s 缓存）+ persona.md 全文（`persona_text` 参数，L3 低频变化、失效重建用户已接受）；per-turn 内容禁入 |
| per-turn 内容贴当轮用户消息 + 按 id 记账 | `_build_memory_context`（仅 L1 memories + L2 scenes；persona 摘要在 `include_persona=False` 的 facade 模式才走 per-turn）+ `memory_contexts` 账本（调用方持有） |
| 一次性 LLM 调用 1 个断点 | `PromptLoader.load_system_sections` 末位文件 |

## 不变量

1. **历史消息永不修改**：build_actor_document 只追加 prepend（记忆块），不重写
   已渲染内容；新内容只能出现在当轮。
2. **PromptLoader 依赖配置对象的 `.get` 与 `.path`**：`api.py` 的 `_PipelineConfig`
   适配器是唯一外部注入点；不要新增对 configurationLoader 的直接引用。
   门面 `create_prompt_loader(config=)` 供宿主把加载器注入 ActorAgent 等组装器；
   宿主可传"叠加配置对象"（只改 `prompt.system.<key>` 列表，其余键委托）来
   按功能启停追加提示词段——**Prompting 不认识任何具体功能，段文件取舍属于
   组合根**（Goharu 的叠加层在 Gateway/prompt_sections.py）。
3. **Goharu 特有注入可关**：`include_git_status` / `include_skills`（默认开，
   保持 Goharu 行为；facade 默认关）。
4. 渲染器纯函数：PromptRenderer 不许引入状态/IO。

## 接口契约测试

`tests/test_prompting_standalone.py`（tests/ 目录）：断点位置、**跨 turn 前缀逐字节稳定**、
retrieval_pack 落位、钉账退化路径、文件/字符串两种注入、micro_compact。
改组装逻辑后必须跑，且必须跑 `tests/test_cache_breakpoints.py`。
