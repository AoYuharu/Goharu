# Goharu Prompting — 即拆即用的上下文管理器（ContextManager）

把（系统提示词 + 对话历史 + 记忆检索包）组装成 Anthropic Messages 格式的
messages 列表，**内建 prompt cache 断点纪律**与**完整压缩阶梯**——新智能体
直接套用即可解决所有上下文相关问题，也可逐层微调。

## 依赖安装

零第三方依赖（纯标准库）。`estimate_tokens` 可选装 tokenizer 提升精度；
L2 压缩需注入 llm（契约：`query(messages) -> str 或响应对象`）。

## 快速接入

```python
from Prompting.api import create_context_manager

ctx = create_context_manager(
    system_prompt="你是一个助手。",
    max_messages=40,                  # L1 窗口：最多保留 40 条（0=不截断）
    compact_threshold_tokens=30000,   # L2 阈值：超过则建议 LLM 摘要压缩
    llm=my_llm,                       # L2 压缩用（可选）
)

memory_contexts = {}  # 跨 turn 复用同一 dict（记忆钉账账本）

# 每轮：
report = ctx.context_report(history)          # 各压缩层状态
history, _ = ctx.micro_compact(history)       # L0：折叠过期工具结果
history = ctx.truncate_history(history)       # L1：窗口截断（配对安全）
if report["llm_compact"]["recommended"]:
    summary = ctx.llm_compact(history)        # L2：LLM 摘要（返回文本）
    history = [{"role": "user", "content": summary, "id": "..."}]  # 由你替换

messages = ctx.build_messages(history, retrieval_pack=pack,
                              memory_contexts=memory_contexts)
```

> 兼容旧名：`create_context_pipeline` / `ContextPipeline` 仍可用。

## 压缩阶梯（四层）

| 层 | 机制 | 触发时机 | 配置/参数 | 产物 |
|---|---|---|---|---|
| L0 微折叠 | 规则：过期长工具结果折叠为占位符 | 每轮一次（你调 `micro_compact`） | age_threshold_hours=1, keep=5, min_chars=120 | 折叠后历史 |
| L1 窗口截断 | 保留最近 N 条，丢弃孤儿 tool_result 配对 | 每轮组装前（`truncate_history`） | max_messages（0=关） | 截断后历史 |
| L2 LLM 摘要 | 整段历史压缩为一条摘要 | token 超阈值（`should_llm_compact` 判定，你决定何时调 `llm_compact`） | compact_threshold_tokens=30000 | 摘要文本 |
| L3 记忆蒸馏 | L1 原子 / L2 场景 / L3 画像沉淀 | 后台管线（Memory 子系统） | 见 Memory/README.md | 可检索的长期记忆 |
| 辅助 | 工具结果预算：超限落盘+占位符引用 | 工具执行后（`apply_result_budget`） | cache_dir / max_single=8000 / max_batch=24000 | 占位符引用 |

## 公共 API（`ContextManager`）

| 接口 | 签名 | 说明 |
|---|---|---|
| 组装消息 | `build_messages(history, *, retrieval_pack=None, memory_contexts=None, extra_system_prompt=None) -> list[dict]` | 断点纪律内建，见下 |
| token 估算 | `estimate_tokens(text) -> int` / `estimate_messages_tokens(messages) -> int` | 无 tokenizer 时 len//3+1 兜底 |
| L0 微折叠 | `micro_compact(messages, ...) -> (messages, folded)` | 消息需带 ISO `timestamp` |
| L1 窗口截断 | `truncate_history(history, max_messages=None) -> history` | None 用创建配置 |
| L2 阈值判定 | `should_llm_compact(messages, max_tokens=None) -> bool` | |
| L2 摘要压缩 | `llm_compact(messages, system_prompt=None) -> str` | 需注入 llm |
| 结果预算 | `apply_result_budget(results, cache_dir=None, max_single_tokens=None, max_batch_tokens=None) -> bool` | 超限结果落盘+占位符，原地修改 |
| 状态报告 | `context_report(messages) -> dict` | 各层状态与建议 |

## 缓存断点契约（外部 harness 必守）

`build_messages` 内建以下纪律（Anthropic 按前缀缓存，任何前缀字节变化都会
使其后缓存全部失效）：

1. **断点 ≤4**（Anthropic 硬限制）：本管线只用 2 个——最后一个 system 块 +
   最后一个可缓存历史轮。
2. **静态前缀逐字节稳定**：system 区绝不放每轮变化的内容。
3. **per-turn 检索内容贴当轮用户消息**：`retrieval_pack` 合成"本轮上下文块"
   prepend 到最新 user 消息，并按消息 `id` 记入 `memory_contexts`——历史渲染
   时贴回各自原消息，**旧消息内容永不变化**。

调用方义务只有两个：① 消息带稳定 `id`（无 id 退化为只贴最新一条）；
② `memory_contexts` 跨 turn 复用同一 dict 对象。

## retrieval_pack 数据结构

```python
{
    "profile_summary": "用户画像文本",           # L3 persona（可选）
    "memories": [{"title", "text", "source_id", "score"}],   # L1 原子
    "scenes":   [{"title", "summary", "source_id", "keywords", "score"}],  # L2 场景
}
```

与 Memory 子系统 `retrieve()` 的返回完全一致，原样传入即可。

## config 文件模式（可选）

不传 `system_prompt` 时按 config 读提示词文件：

```python
ctx = create_context_manager(
    config={"prompt": {"system": {"actor": ["prompts/actor/base.md"],
                                 "shared": ["prompts/shared/common.md"]}}},
    base_dir="/path/to/project",   # 相对路径的解析基准
)
```

`prompt.system.*` 的值是文件路径列表；actor 的最后一个文件会附断点。

## Goharu 特有注入（独立模式默认关闭）

`include_git_status=True`（注入 git status system 块）、`include_skills=True`
（扫描 `<repo>/Skills/*/skill.md` 贴当轮消息）——两项是 Goharu 运行环境特性，
外部 harness 一般保持关闭。
