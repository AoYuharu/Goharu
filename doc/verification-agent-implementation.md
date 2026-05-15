# Verification Agent 工程实现复现文档

## 概述

Verification Agent 是 Claude Code 中的内置对抗性验证子 agent，专为长复杂任务设计。其核心理念是 **"not to confirm the implementation works — it's to try to break it"**。它作为一个独立的、被剥夺写权限的、后台运行的 fork subagent，在非平凡任务完成后负责对抗性验证，防止主模型草草了事或幻觉声称完成。

---

## 1. 触发时机

### 1.1 Feature Gate（前置条件）

三组条件全部满足才会激活（`builtInAgents.ts:64-68` + `prompts.ts:479-485`）：

```
① FEATURE_VERIFICATION_AGENT = 1 (build-time flag)
② GrowthBook: tengu_hive_evidence = true (运行时 flag，默认 false)
③ 非穷鬼模式 (!isPoorModeActive())
④ 有 Agent 工具可用 (hasAgentTool)
```

### 1.2 主模型何时被告知调用 Verification Agent

当 feature gate 开启时，以下文本被注入主模型的系统提示词（`prompts.ts:485`）：

> "The contract: when non-trivial implementation happens on your turn, independent adversarial verification must happen before you report completion — regardless of who did the implementing (you directly, a fork you spawned, or a subagent). You are the one reporting to the user; you own the gate. Non-trivial means: 3+ file edits, backend/API changes, or infrastructure changes. Spawn the Agent tool with subagent_type='verification'..."

关键约束：
- **非平凡任务**的定义：3+ 文件编辑、后端/API 变更、基础设施变更
- 主模型自己的检查和 fork 的自我检查**不能替代** Verification Agent
- 只有 verifier 才能给出 PASS/FAIL/PARTIAL 判定

### 1.3 主模型如何调用

模型通过 AgentTool 调用：
```
Agent({
  subagent_type: "verification",
  description: "Verification of task X",
  prompt: "原始用户请求 + 修改文件列表 + 实现方法 + 计划路径"
})
```

---

## 2. Agent 配置

### 2.1 完整配置表（`verificationAgent.ts:134-152`）

| 属性 | 值 | 说明 |
|------|-----|------|
| `agentType` | `'verification'` | 类型标识 |
| `source` | `'built-in'` | 内置 agent |
| `baseDir` | `'built-in'` | 查找路径 |
| `color` | `'red'` | UI 显示颜色 |
| `background` | `true` | **始终异步运行** |
| `model` | `'inherit'` | 使用与主 agent 相同的模型 |
| `maxTurns` | `undefined` | **无回合限制** |
| `timeout` | 无 | **无时间限制** |
| `disallowedTools` | `Agent, ExitPlanModeV2, Edit, Write, NotebookEdit` | 禁止的工具 |
| `getSystemPrompt` | 返回 VERIFICATION_SYSTEM_PROMPT | 独立系统提示词 |

### 2.2 系统提示词注入机制（`runAgent.ts:892-918`）

Verification Agent 的系统提示词**不使用主 agent 的完整系统提示词**。它的 system prompt 由两部分组成：

```
Verification Agent 最终 System Prompt =
    VERIFICATION_SYSTEM_PROMPT（约 120 行专业提示词）
  + enhanceSystemPromptWithEnvDetails() 追加的环境信息
    （OS、Shell、工作目录、绝对路径说明、emoji 规则等）
```

`getSystemPrompt` 只返回静态字符串，`enhanceSystemPromptWithEnvDetails()` 在 `runAgent.ts` 中调用，统一追加密钥环境细节。

---

## 3. 工具权限

### 3.1 三层过滤链

Verification Agent 的工具集通过三层过滤确定（`agentToolUtils.ts:70-225`）：

```
第 1 层：父 agent 所有可用工具（wildcard，全部通过）
    ↓
第 2 层：ALL_AGENT_DISALLOWED_TOOLS（全局禁止）- 过滤掉：
    TaskOutput, ExitPlanModeV2, EnterPlanMode, AskUserQuestion, TaskStop
    ↓
第 3 层：Agent 自身的 disallowedTools - 过滤掉：
    Agent, ExitPlanModeV2, Edit, Write, NotebookEdit
    ↓
第 4 层：ASYNC_AGENT_ALLOWED_TOOLS（异步白名单）- 仅保留：
    FileRead, WebSearch, TodoWrite, Grep, WebFetch, Glob,
    Bash(tool)*, Skill, SyntheticOutput, ToolSearch,
    EnterWorktree, ExitWorktree
```

### 3.2 Verification Agent 最终工具清单

| 工具 | 可用 | 说明 |
|------|------|------|
| FileRead (Read) | ✅ | 读取项目文件和验证输出 |
| Grep | ✅ | 搜索代码 |
| Glob | ✅ | 查找文件 |
| Bash (bash/powershell) | ✅ | 运行命令、测试、启动服务器、curl |
| WebSearch (WebSearch) | ✅ | 搜索 web |
| WebFetch (WebFetch) | ✅ | 获取网页 |
| TodoWrite (TodoWrite) | ✅ | 记录待办事项 |
| Skill (Skill) | ✅ | 调用技能 |
| ToolSearch (ToolSearch) | ✅ | 搜索可用工具 |
| EnterWorktree / ExitWorktree | ✅ | 工作树操作 |
| Write (Write) | ❌ | **禁止写入项目文件** |
| Edit (Edit) | ❌ | **禁止编辑项目文件** |
| NotebookEdit | ❌ | **禁止修改 notebook** |
| Agent (Agent) | ❌ | **禁止嵌套 agent** |
| Task (Task) | ❌ | **禁止创建任务** |
| ExitPlanMode | ❌ | 无关 |
| EnterPlanMode | ❌ | 无关 |
| AskUserQuestion | ❌ | 禁止中断用户 |
| TaskOutput | ❌ | 无关 |
| TaskStop | ❌ | 禁止停止其他任务 |

关键设计：**有 Bash 权限，可以在 /tmp 写临时测试脚本，但不能触碰项目文件的任何内容**。

---

## 4. 运行生命周期

### 4.1 启动流程（`AgentTool.tsx:827-1045`）

```
主模型调用 Agent({ subagent_type: 'verification', ... })
    ↓
AgentTool.call() 解析参数
    ↓
查找 agentType === 'verification' 的 AgentDefinition
    ↓
shouldRunAsync = true （因为 background === true）
    ↓
registerAsyncAgent() → LocalAgentTaskState(isBackgrounded: true)
    ↓
void runAsyncAgentLifecycle()  // fire-and-forget
    ↓
返回 { status: 'async_launched', agentId, outputFile }
    ↓
主模型继续下一轮 ReAct（非阻塞）
```

### 4.2 异步执行（`agentToolUtils.ts:509-687`）

```
runAsyncAgentLifecycle()
    ↓
构建 Agent 系统提示词
    ↓
准备 agent 消息上下文（主模型传入的 prompt）
    ↓
调用 query() 执行 agent 的 ReAct 循环（无 maxTurns 限制）
    ↓
Agent 完成 → 提取最终文本内容（应包含 VERDICT 行）
    ↓
finalizeAgentTool() 整理结果
    ↓
enqueueAgentNotification() 创建 <task-notification> XML
    ↓
enqueuePendingNotification() 入队
    ↓
主模型下一轮 drain 队列，以 attachment 形式收到验证报告
```

### 4.3 结果回传格式（`LocalAgentTask.tsx:342-349`）

```xml
<task-notification>
  <task-id>agent-abc123</task-id>
  <tool-use-id>toolu_xyz</tool-use-id>
  <output-file>/path/to/output.jsonl</output-file>
  <status>completed</status>
  <summary>Agent "Verification" completed</summary>
  <result>
    ### Check: build passes
    **Command run:** bun run typecheck
    **Output observed:** 0 errors
    **Result: PASS**
    ...
    VERDICT: PASS
  </result>
</task-notification>
```

### 4.4 主模型的后续行为（prompts.ts:485）

主模型收到验证报告后的指令：
- **PASS**：抽查报告中的 2-3 条命令，确认 Command run 块和输出能复现。如 PASS 缺少命令块或输出不一致 → resume verifier
- **FAIL**：修复问题 → resume verifier 带修复信息 → 重复直到 PASS
- **PARTIAL**：报告已验证部分和无法验证的原因

### 4.5 One-Shot 状态

Verification Agent **不是** one-shot agent（只有 Explore 和 Plan 是）。这意味着 `agentId` 和 `usage` 块会包含在 tool_result trailer 中，使主模型能够通过 `SendMessage` 工具**继续**验证 agent（如 "resume the verifier with its findings plus your fix"）。

---

## 5. 贫户模式（Poor Mode）影响

`src/commands/poor/poorMode.ts`：当穷鬼模式激活时，`isPoorModeActive()` 返回 true → prompts.ts 中的 "The contract" 指令被抑制 → 主模型不会被要求调用 Verification Agent（节省大量 token）。

---

## 6. 提示词注入位置汇总

| 注入位置 | 内容 | 接收者 |
|---------|------|--------|
| `verificationAgent.ts:10-129` | VERIFICATION_SYSTEM_PROMPT | Verification Agent 的 system prompt |
| `verificationAgent.ts:150-151` | criticalSystemReminder_EXPERIMENTAL | 每轮注入 Verification Agent 的 conversation |
| `runAgent.ts:892-918` | 环境细节 (OS, Shell, CWD 等) | 追加到 Verification Agent 的 system prompt |
| `runAgent.ts:139-151` | DEFAULT_AGENT_PROMPT | 仅当 getSystemPrompt 抛出异常时的兜底 |
| `prompts.ts:485` | "The contract" 文本 | 主模型的 system prompt session-specific guidance 部分 |
| `verificationAgent.ts:131-132` | whenToUse 文本 | 注入到 agent listing 提示词中，供模型参考何时使用 |
| `builtInAgents.ts:64-68` | Feature gate 逻辑 | 决定是否在可用 agent 列表中注册 |

---

## 7. 关键文件索引

| 文件 | 作用 |
|------|------|
| `packages/builtin-tools/src/tools/AgentTool/built-in/verificationAgent.ts` | Agent 定义 + 系统提示词 |
| `packages/builtin-tools/src/tools/AgentTool/builtInAgents.ts` | 注册内置 agent，feature gate |
| `packages/builtin-tools/src/tools/AgentTool/AgentTool.tsx` | Agent 调用入口，sync/async 决策 |
| `packages/builtin-tools/src/tools/AgentTool/agentToolUtils.ts` | runAsyncAgentLifecycle(), 工具过滤 |
| `packages/builtin-tools/src/tools/AgentTool/runAgent.ts` | getAgentSystemPrompt(), maxTurns 参数 |
| `packages/builtin-tools/src/tools/AgentTool/loadAgentsDir.ts` | AgentDefinition 类型定义 |
| `packages/builtin-tools/src/tools/AgentTool/constants.ts` | AGENT_TOOL_NAME, VERIFICATION_AGENT_TYPE |
| `src/constants/prompts.ts` | "The contract" 主模型指令 |
| `src/constants/tools.ts` | ALL_AGENT_DISALLOWED_TOOLS, ASYNC_AGENT_ALLOWED_TOOLS |
| `src/tasks/LocalAgentTask/LocalAgentTask.tsx` | enqueueAgentNotification(), 异步通知 |
| `src/commands/poor/poorMode.ts` | 穷鬼模式对验证 agent 的影响 |
