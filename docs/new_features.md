# 新增功能：循环检测、可见输出、上下文查看、用户中断

## 功能概览

1. **死循环检测**：自动检测 Reflection 连续拒绝，防止无限循环
2. **Reflection 可见输出**：始终显示 Reflection 的思考过程
3. **上下文查看命令**：`/context` 查看即将发送给 API 的上下文结构
4. **用户中断**：`/interrupt` 中途打断 Agent 执行

## 1. 死循环检测

### 问题背景

之前发现系统会陷入死循环：
- Reflection 一直说"需要继续调用工具"
- Actor 没有真正调用工具
- 循环往复，永不结束

### 解决方案

**检测机制**：
```python
consecutive_rejections = 0  # 连续拒绝计数
last_tool_call_step = -1    # 最后一次工具调用的步骤

# 每次工具调用时重置
if action.get("type") == "tool":
    last_tool_call_step = step
    consecutive_rejections = 0

# 每次 Reflection 拒绝时累加
if "需要继续调用工具" in reflection:
    consecutive_rejections += 1

    # 达到阈值时中断
    if consecutive_rejections >= 10:
        # 显示诊断信息
        # 返回失败结果
```

**触发条件**：
- Reflection 连续 10 次说"需要继续调用工具"
- 期间没有任何工具调用

**触发后行为**：
1. 显示诊断信息：
   - 最后一次工具调用在哪一步
   - 当前在哪一步
   - 可能的原因分析
2. 返回失败结果，包含最后的 Reflection 内容
3. 给出建议：
   - 检查 Actor prompt
   - 检查模型是否理解工具调用格式
   - 尝试更明确的用户指令

**示例输出**：
```
⚠ Reflection 连续 10 次拒绝，可能陷入死循环

问题分析：
  - 最后一次工具调用在 step 1
  - 当前 step 11
  - Actor 可能没有响应 Reflection 的要求

建议：
  1. 检查 Actor prompt 是否足够强制
  2. 检查模型是否理解工具调用格式
  3. 尝试更明确的用户指令
```

### 配置

死循环阈值硬编码为 10 次，可以在代码中修改：
```python
if consecutive_rejections >= 10:  # 修改这里
```

## 2. Reflection 可见输出

### 改进前

Reflection 的思考过程默认不可见，需要在 `config.yaml` 中设置：
```yaml
ui:
  show_reflections: true
```

### 改进后

**始终显示 Reflection**，无需配置。

**Rich 模式**（彩色输出）：
```
╭─────────────────────────────────────────────╮
│ Reflection (step 1)                         │
├─────────────────────────────────────────────┤
│ 从对话历史来看，存在以下问题：              │
│                                             │
│ 1. 工具调用结果缺失                         │
│ 2. 编造风险                                 │
│                                             │
│ **需要继续调用工具**                        │
╰─────────────────────────────────────────────╯
```

**普通模式**：
```
[Reflection (step 1)]
从对话历史来看，存在以下问题：
...
```

### 好处

- 用户可以实时看到 Reflection 的判断
- 更容易理解为什么 Agent 继续执行或停止
- 便于调试和诊断问题

## 3. 上下文查看命令 `/context`

### 功能

查看即将发送给 API 的完整上下文结构。

### 使用方法

在任何时候输入 `/context`：
```
You > /context
```

### 输出示例

**Rich 模式**：
```json
[
  {
    "role": "system",
    "content_length": 1234,
    "content_preview": "你是一个本地智能体。\n\n## 核心原则\n\n**禁止编造结果**：..."
  },
  {
    "role": "user",
    "content_length": 45,
    "content_preview": "写一个输出当前日期的脚本"
  },
  ...
]

Total messages: 8, Total characters: 5432
```

**普通模式**：
```
=== Context Structure ===
[1] system: 1234 chars
    你是一个本地智能体。...
[2] user: 45 chars
    写一个输出当前日期的脚本...
...

Total: 8 messages
```

### 用途

- 调试：查看模型实际收到的上下文
- 优化：检查上下文长度，避免超出限制
- 理解：了解 prompt 的组成结构

## 4. 用户中断 `/interrupt`

### 功能

在 Agent 执行过程中，用户可以随时中断。

### 使用方法

**方式 1：在等待输入时**
```
You > /interrupt
Interrupt requested. Will stop at next step.
```

**方式 2：在 Agent 执行时**
- 由于 Agent 执行时无法输入，需要在下一轮输入前使用
- 或者使用 Ctrl+C 强制终止（会退出整个程序）

### 中断时机

中断会在**下一个 ReAct 步骤开始前**生效：
```python
for step in range(max_depth):
    # 检查中断
    if check_interrupt():
        return {"interrupted": True, ...}

    action = await actor.act()  # 如果中断，不会执行这里
    ...
```

### 中断后行为

1. 停止当前执行
2. 显示中断消息
3. 返回到用户输入提示符
4. 可以继续输入新的问题

**输出示例**：
```
⚠ Execution interrupted by user

╭─────────────────────────────────────────────╮
│ Interrupted                                 │
├─────────────────────────────────────────────┤
│ 执行已被用户中断。                          │
╰─────────────────────────────────────────────╯

You >
```

### 限制

- 无法在 LLM 推理过程中中断（需要等待当前推理完成）
- 无法在工具执行过程中中断（需要等待工具返回）
- 只能在步骤之间中断

## 5. 更新的命令列表

```
Commands:
  /help      Show this help
  /multi     Enter multiline input mode
  /context   Show the current context that will be sent to the API
  /interrupt Interrupt the current agent execution
  /exit      Exit the app
  /quit      Exit the app
  exit       Exit the app
```

## 使用场景

### 场景 1：调试死循环

```
You > 写一个脚本
[Agent 开始执行]
→ run_cmd ...
Reflection (step 1): 需要继续调用工具
Reflection (step 2): 需要继续调用工具
...
Reflection (step 10): 需要继续调用工具

⚠ Reflection 连续 10 次拒绝，可能陷入死循环
[显示诊断信息]

You > [可以重新尝试或调整指令]
```

### 场景 2：查看上下文

```
You > /context
[显示完整上下文结构]

You > [根据上下文调整问题]
```

### 场景 3：中断长时间执行

```
You > 执行一个复杂任务
[Agent 执行中...]
→ tool1 ...
Reflection: 需要继续
→ tool2 ...
Reflection: 需要继续
[发现方向不对]

You > /interrupt
Interrupt requested. Will stop at next step.

⚠ Execution interrupted by user

You > [重新提问]
```

## 技术实现

### 全局中断标志

```python
interrupt_requested = False  # 全局变量

def request_interrupt():
    global interrupt_requested
    interrupt_requested = True

def check_interrupt():
    global interrupt_requested
    if interrupt_requested:
        interrupt_requested = False
        return True
    return False
```

### 死循环检测变量

```python
consecutive_rejections = 0  # 连续拒绝计数
last_tool_call_step = -1    # 最后工具调用步骤
```

### Reflection 显示

移除了 `show_reflections` 配置检查，始终显示：
```python
# 旧代码
if show_reflections:
    console.print(f"[dim]reflection: {reflection}[/dim]")

# 新代码
console.print(Panel(
    reflection,
    title=f"[bold magenta]Reflection (step {step + 1})[/bold magenta]",
    border_style="magenta",
    padding=(1, 2)
))
```

## 配置选项

无需额外配置，所有功能开箱即用。

可以移除 `config.yaml` 中的：
```yaml
ui:
  show_reflections: false  # 已废弃，始终显示
```

## 已知限制

1. **中断延迟**：无法立即中断，需要等待当前步骤完成
2. **死循环阈值固定**：硬编码为 10 次，不可配置
3. **上下文预览长度**：固定 200 字符，不可配置

## 未来改进

- [ ] 支持配置死循环检测阈值
- [ ] 支持真正的异步中断（信号处理）
- [ ] 支持 `/context` 导出到文件
- [ ] 支持 `/context` 显示完整内容（不截断）
- [ ] 支持 Reflection 历史回溯（查看之前的 Reflection）
