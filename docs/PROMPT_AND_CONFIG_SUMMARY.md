# 提示词结构和配置总结

## ✅ 已完成的工作

### 1. 提示词结构分析

已创建详细文档：`docs/PROMPT_STRUCTURE.md`

### 2. 子agent最大迭代次数调整

**修改文件**: `config.yaml`

**修改内容**:
```yaml
agent_delegate:
  max_iterations: 20  # 从 8 调整到 20
```

---

## 📋 提示词结构概览

### 主 Agent 提示词（文件）

| Agent | 文件位置 | 行数 | 作用 |
|-------|---------|------|------|
| **Actor** | `prompts/actor/base.md` | ~275行 | 主执行者，调用工具完成任务 |
| **Reflection** | `prompts/reflection/base.md` | ~91行 | 反思模块，决定是否结束 |
| **Reviewer** | `prompts/reviewer/base.md` | ~4行 | 用户画像复盘 |
| **Summarizer** | `prompts/summarizer/base.md` | ~3行 | 长期记忆摘要 |

### 子 Agent 提示词（代码中）

| Agent | 位置 | 行数 | 作用 |
|-------|------|------|------|
| **Explore** | `Agent/SubAgent.py:19-64` | ~46行 | 探索代码库，只读 |
| **Plan** | `Agent/SubAgent.py:67-117` | ~51行 | 设计实现方案，只读 |

---

## 🎯 各提示词核心要点

### Actor Agent (主执行者)

**核心原则**:
- ❌ 禁止编造结果
- ✅ 工具优先
- ✅ 执行，不要描述
- ✅ 自己解决问题

**工具调用格式**:
```json
// 单个工具
{"tool": "Read", "arguments": {"path": "file.py"}}

// 多个工具（并发）
[
  {"tool": "Read", "arguments": {"path": "a.py"}},
  {"tool": "Read", "arguments": {"path": "b.py"}}
]
```

**任务复杂度评估**:
- 简单任务 → 直接处理
- 中等任务 → 1-2个子agent
- 复杂任务 → 多个子agent并发

**文件操作规则**:
- 创建新文件 → `Write` 工具
- 修改文件 → `Read` + `Edit` 组合
- ❌ 禁止用 `run_cmd` 进行文件操作

---

### Reflection Agent (反思模块)

**核心职责**:
- 最终决策者
- 检查是否完成
- 检测编造
- 决定是否结束

**判断标准**:
- ✅ 可以结束 → 输出"可以给出最终回答"
- ❌ 需要继续 → 输出"需要继续调用工具"

**编造检测**:
- 检查工具调用记录
- 验证所有声明
- 零容忍编造

---

### Explore Agent (探索代码)

**角色**: 文件搜索专家

**权限**: 只读（严格禁止文件修改）

**可用工具**:
- Glob（文件匹配）
- Grep（内容搜索）
- Read（读取文件）
- run_cmd（只读命令）

**工作流程**: ReAct循环
1. 思考 → 决定下一步
2. 行动 → 调用工具
3. 观察 → 查看结果
4. 重复 → 直到完成

**输出要求**:
- 清晰的最终报告
- 发现的文件/代码
- 功能和作用
- 关键实现细节

---

### Plan Agent (设计方案)

**角色**: 软件架构师

**权限**: 只读（严格禁止文件修改）

**工作流程**:
1. 理解需求
2. 探索代码库
3. 设计方案
4. 细化计划

**输出要求**:
1. 现状分析
2. 设计方案
3. 实现步骤
4. 关键文件列表

---

## ⚙️ 配置参数

### 迭代次数

```yaml
# 主 Agent
mcp:
  maxDepth: 8  # 主agent最大迭代次数

# 子 Agent
agent_delegate:
  max_iterations: 20  # 子agent最大迭代次数（已调整）
  max_history_turns: 3  # 保留历史轮数
```

### 并发限制

```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # Explore最多3个并发
  plan:
    max_concurrent: 2  # Plan最多2个并发
  timeout: 300  # 超时时间（秒）
```

### Reflection 模式

```yaml
mcp:
  reflection_mode: answer_review  # adaptive / always / never / answer_review
  max_review_cycles: 3  # 最多审核循环次数
```

---

## 🔄 执行流程

```
用户输入
    ↓
Actor Agent (prompts/actor/base.md)
    ├─ 简单任务 → 直接执行
    └─ 复杂任务 → 创建子agent
            ↓
        ┌─────────────────┐
        │ Explore Agent   │ (并发)
        │ (SubAgent.py)   │
        └─────────────────┘
            或
        ┌─────────────────┐
        │ Plan Agent      │ (并发)
        │ (SubAgent.py)   │
        └─────────────────┘
    ↓
Reflection Agent (prompts/reflection/base.md)
    ├─ 检查完成度
    ├─ 检测编造
    └─ 决定是否结束
    ↓
最终答案
    ↓
Reviewer Agent (prompts/reviewer/base.md)
    └─ 更新用户画像
```

---

## 📊 并行执行位置

### 工具调用并行

**位置**: `Agent/ActorAgent.py:392`

```python
# 并发执行所有工具调用
results = await asyncio.gather(*[execute_single_tool(tc) for tc in tool_calls])
```

**效果**: 多个工具同时执行

### AgentDelegate 并行

**位置**: `Tools/builtin/agent_delegate.py:258`

```python
# 使用 run_in_executor 异步执行
result = await asyncio.wait_for(
    loop.run_in_executor(
        _manager.executor,
        _execute_subagent_task, ...
    ),
    timeout=timeout
)
```

**效果**: 多个子agent同时执行（已修复）

---

## 🎨 设计理念

### 1. 分工明确
- Actor: 协调和执行
- Reflection: 质量控制
- Explore: 探索代码
- Plan: 设计方案

### 2. 权限控制
- Actor: 读写权限
- Explore/Plan: 只读权限
- Reflection: 审核权限

### 3. 防止编造
- Reflection 严格检查
- 要求工具调用记录
- 零容忍编造

### 4. 并发优化
- 多工具并发
- 多子agent并发
- 提高效率

---

## 📝 修改指南

### 调整主agent行为
编辑 `prompts/actor/base.md`

### 调整反思逻辑
编辑 `prompts/reflection/base.md`

### 调整子agent行为
编辑 `Agent/SubAgent.py` 中的提示词常量

### 调整迭代次数
修改 `config.yaml`:
```yaml
mcp:
  maxDepth: 8  # 主agent

agent_delegate:
  max_iterations: 20  # 子agent（已调整）
```

### 调整并发限制
修改 `config.yaml`:
```yaml
agent_delegate:
  explore:
    max_concurrent: 3  # 可调整
  plan:
    max_concurrent: 2  # 可调整
```

---

## ✅ 本次修改总结

1. **创建文档**: `docs/PROMPT_STRUCTURE.md` - 详细的提示词结构说明
2. **调整配置**: `config.yaml` - 子agent最大迭代次数从 8 → 20
3. **并行修复**: `Tools/builtin/agent_delegate.py` - 使用异步等待（已完成）

**效果**:
- ✅ 子agent可以执行更多轮次（20轮）
- ✅ 多个子agent可以并发执行
- ✅ 提示词结构清晰可维护

---

**所有工作已完成！** 🎉
