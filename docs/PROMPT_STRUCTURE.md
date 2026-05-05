# 提示词结构文档

## 📋 提示词概览

项目使用多个专门的提示词文件，分别控制不同 Agent 的行为。

## 📁 提示词文件位置

### 主 Agent 提示词（在 `prompts/` 目录）

```
prompts/
├── actor/
│   └── base.md          # Actor Agent（主执行者）
├── reflection/
│   └── base.md          # Reflection Agent（反思模块）
├── reviewer/
│   ├── base.md          # Reviewer Agent（用户画像复盘）
│   └── contract.md      # 审核合约
└── summarizer/
    └── base.md          # Summarizer Agent（长期记忆摘要）
```

### 子 Agent 提示词（在代码中硬编码）

```
Agent/SubAgent.py
├── EXPLORE_SYSTEM_PROMPT    # Explore agent 提示词（第19-64行）
└── PLAN_SYSTEM_PROMPT       # Plan agent 提示词（第67-117行）
```

## 🎯 各提示词的作用

### 1. Actor Agent (`prompts/actor/base.md`)

**角色**: 主执行者，负责理解用户需求并调用工具完成任务

**核心内容**:
- 运行环境说明（Windows）
- 核心原则（禁止编造、工具优先、执行不描述）
- 工具调用格式（JSON格式，支持并发）
- 任务复杂度评估（何时使用子agent）
- 多agent并发使用理念
- 文件操作规则（Write/Read/Edit）
- 测试策略

**关键特点**:
- 强调"执行，不要描述"
- 支持多工具并发调用
- 智能使用子agent（Explore/Plan）
- 严格的文件操作权限控制

**字数**: ~275 行

---

### 2. Reflection Agent (`prompts/reflection/base.md`)

**角色**: 反思模块，最终决策者，决定是否结束回合

**核心内容**:
- 核心职责（最终决策权）
- 判断标准（何时结束/何时继续）
- 编造检测（防止模型编造结果）
- 反思输出格式

**关键特点**:
- 拥有最终决策权
- 零容忍编造
- 必须检查工具调用记录
- 输出必须包含"可以给出最终回答"或"需要继续调用工具"

**字数**: ~91 行

---

### 3. Reviewer Agent (`prompts/reviewer/base.md`)

**角色**: 用户画像复盘模块

**核心内容**:
- 在回答完成后复盘
- 提炼用户画像增量
- 输出纯JSON

**关键特点**:
- 只在回答完成后执行
- 输出格式严格（纯JSON）

**字数**: ~4 行（非常简短）

---

### 4. Summarizer Agent (`prompts/summarizer/base.md`)

**角色**: 长期记忆摘要模块

**核心内容**:
- 摘要过期的对话记录
- 输出纯JSON

**关键特点**:
- 只输出结果，无额外解释

**字数**: ~3 行（非常简短）

---

### 5. Explore Agent (SubAgent - 代码中)

**角色**: 文件搜索专家，探索代码库

**核心内容**:
- 只读模式（严格禁止文件修改）
- ReAct循环（思考→行动→观察）
- 可用工具（Glob、Grep、Read、run_cmd）
- 最终输出要求（清晰的报告）

**关键特点**:
- 只读权限
- 持续探索（不要第一次就停止）
- 必须给出分析结论
- 目标：8轮内完成

**字数**: ~46 行

---

### 6. Plan Agent (SubAgent - 代码中)

**角色**: 软件架构师和规划专家

**核心内容**:
- 只读模式（严格禁止文件修改）
- 工作流程（理解需求→探索→设计→细化）
- 可用工具（Glob、Grep、Read、run_cmd）
- 必需输出（完整的实现计划）

**关键特点**:
- 只读权限
- 深入探索现有代码
- 输出完整实现计划
- 包含关键文件列表

**字数**: ~51 行

---

## 🔧 配置关系

### 提示词加载配置 (`config.yaml`)

```yaml
prompt:
  system:
    shared: []
    actor:
      - ./prompts/actor/base.md
    reflection:
      - ./prompts/reflection/base.md
    summarizer:
      - ./prompts/summarizer/base.md
    reviewer:
      - ./prompts/reviewer/base.md
      - ./prompts/reviewer/contract.md
```

### 迭代次数配置

**主 Agent**:
```yaml
mcp:
  maxDepth: 8  # 主agent最大迭代次数
```

**子 Agent**:
```yaml
agent_delegate:
  max_iterations: 8  # 子agent最大迭代次数（默认）
  max_history_turns: 3  # 保留的历史轮数
```

---

## 📊 提示词层次结构

```
用户输入
    ↓
┌─────────────────────────────────────┐
│ Actor Agent (prompts/actor/base.md) │
│ - 理解需求                           │
│ - 评估复杂度                         │
│ - 决定是否使用子agent                │
└─────────────────────────────────────┘
    ↓
    ├─ 简单任务 → 直接执行
    │
    └─ 复杂任务 → 创建子agent
                    ↓
        ┌───────────────────────────┐
        │ Explore Agent (代码中)     │
        │ - 探索代码库               │
        │ - 搜索文件                 │
        │ - 分析代码                 │
        └───────────────────────────┘
                    或
        ┌───────────────────────────┐
        │ Plan Agent (代码中)        │
        │ - 理解架构                 │
        │ - 设计方案                 │
        │ - 制定计划                 │
        └───────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│ Reflection Agent                    │
│ (prompts/reflection/base.md)        │
│ - 检查是否完成                       │
│ - 检测编造                           │
│ - 决定是否结束                       │
└─────────────────────────────────────┘
    ↓
最终答案
    ↓
┌─────────────────────────────────────┐
│ Reviewer Agent                      │
│ (prompts/reviewer/base.md)          │
│ - 复盘对话                           │
│ - 更新用户画像                       │
└─────────────────────────────────────┘
```

---

## 🎨 提示词设计理念

### 1. 分工明确
- 每个 Agent 有明确的职责
- 避免职责重叠
- 专注于自己的领域

### 2. 权限控制
- Actor: 完整权限（读写）
- Explore/Plan: 只读权限
- Reflection: 审核权限

### 3. 防止编造
- Reflection 严格检查工具调用记录
- 要求所有声明都有证据
- 零容忍编造

### 4. 并发优化
- Actor 支持多工具并发
- 鼓励使用多个子agent并发
- 提高执行效率

---

## 📝 修改建议

### 如果要调整行为

1. **修改主agent行为** → 编辑 `prompts/actor/base.md`
2. **修改反思逻辑** → 编辑 `prompts/reflection/base.md`
3. **修改子agent行为** → 编辑 `Agent/SubAgent.py` 中的提示词
4. **调整迭代次数** → 修改 `config.yaml`

### 常见调整

- **增加子agent迭代次数**: 修改 `config.yaml` 中的 `agent_delegate.max_iterations`
- **修改并发限制**: 修改 `config.yaml` 中的 `agent_delegate.explore.max_concurrent`
- **调整工具权限**: 修改 `SubAgent.py` 中的 `get_allowed_tools()`

---

## 🔍 提示词特点对比

| Agent | 长度 | 权限 | 迭代 | 主要任务 |
|-------|------|------|------|---------|
| Actor | 长 | 读写 | 8轮 | 执行任务 |
| Reflection | 中 | 审核 | - | 质量控制 |
| Explore | 中 | 只读 | 8轮 | 探索代码 |
| Plan | 中 | 只读 | 8轮 | 设计方案 |
| Reviewer | 短 | - | - | 用户画像 |
| Summarizer | 短 | - | - | 记忆摘要 |

---

**总结**: 提示词系统采用分层设计，主agent负责协调，子agent负责专项任务，Reflection负责质量控制，形成完整的执行-审核闭环。
