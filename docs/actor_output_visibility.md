# Actor 输出可见化与 Reflection 限制

## 更新内容

### 1. Actor 原始输出可见化

**功能**：现在可以看到 Actor 模型的完整原始输出，包括它的思考过程。

**配置**：
```yaml
ui:
  show_actor_output: true  # 默认开启
```

**显示效果**：

**Rich 模式**：
```
╭─────────────────────────────────────────────╮
│ Actor (step 1)                              │
├─────────────────────────────────────────────┤
│ 我需要创建一个 Python 脚本来输出当前日期。  │
│ 让我先调用工具...                           │
│                                             │
│ {"tool": "run_cmd", "arguments": {...}}     │
╰─────────────────────────────────────────────╯

→ run_cmd {"command": "..."}
```

**普通模式**：
```
[Actor (step 1)]
我需要创建一个 Python 脚本来输出当前日期。
让我先调用工具...

{"tool": "run_cmd", "arguments": {...}}

[step 1] tool: run_cmd
args   : {"command": "..."}
```

### 2. Reflection 最大步数限制

**限制**：Reflection 最多执行 5 次，防止无限循环。

**实现**：
```python
max_reflection_steps = 5  # Reflection 最大步数
reflection_count = 0      # Reflection 计数器

def should_reflect(step_num, action_type):
    # 如果已经达到 Reflection 最大次数，不再 reflect
    if reflection_count >= max_reflection_steps:
        return False
    # ... 其他逻辑
```

**显示效果**：
```
╭─────────────────────────────────────────────╮
│ Reflection (1/5, step 1)                    │
├─────────────────────────────────────────────┤
│ 从对话历史来看，存在以下问题：              │
│ ...                                         │
╰─────────────────────────────────────────────╯

╭─────────────────────────────────────────────╮
│ Reflection (2/5, step 3)                    │
├─────────────────────────────────────────────┤
│ ...                                         │
╰─────────────────────────────────────────────╯

...

╭─────────────────────────────────────────────╮
│ Reflection (5/5, step 8)                    │
├─────────────────────────────────────────────┤
│ 已达到最大 Reflection 次数，结束执行。      │
╰─────────────────────────────────────────────╯
```

**达到上限后的行为**：
- 不再触发新的 Reflection
- 直接进入最终回答阶段
- 避免因 Reflection 循环导致的资源浪费

## 完整的输出流程

现在用户可以看到完整的执行过程：

```
You > 写一个输出当前日期的脚本

╭─────────────────────────────────────────────╮
│ Actor (step 1)                              │  ← 新增：Actor 的思考
├─────────────────────────────────────────────┤
│ {"tool": "run_cmd", "arguments": {...}}     │
╰─────────────────────────────────────────────╯

→ run_cmd {"command": "echo ..."}              ← 工具调用

╭─────────────────────────────────────────────╮
│ Reflection (1/5, step 1)                    │  ← Reflection 的判断
├─────────────────────────────────────────────┤
│ 需要继续调用工具验证文件是否创建成功。      │
╰─────────────────────────────────────────────╯

╭─────────────────────────────────────────────╮
│ Actor (step 2)                              │  ← Actor 继续执行
├─────────────────────────────────────────────┤
│ {"tool": "run_cmd", "arguments": {...}}     │
╰─────────────────────────────────────────────╯

→ run_cmd {"command": "python date.py"}

╭─────────────────────────────────────────────╮
│ Reflection (2/5, step 2)                    │
├─────────────────────────────────────────────┤
│ 可以给出最终回答。                          │
╰─────────────────────────────────────────────╯

╭─────────────────────────────────────────────╮
│ Assistant                                   │  ← 最终回答
├─────────────────────────────────────────────┤
│ 已成功创建并测试脚本，输出：2026-05-01      │
╰─────────────────────────────────────────────╯
```

## 配置选项

### config.yaml

```yaml
ui:
  verbose: false              # 详细模式（显示工具结果预览）
  show_reflections: false     # 已废弃（Reflection 始终显示）
  show_memory_events: false   # 显示内存事件
  show_actor_output: true     # 显示 Actor 原始输出（新增）

mcp:
  maxDepth: 8                 # 最大执行步数
  reflection_mode: adaptive   # Reflection 模式
```

### Reflection 模式

- **adaptive**（默认）：自适应触发
  - Actor 给出答案时
  - 达到最大深度前
  - 每 3 步触发一次
  - **限制：最多 5 次**

- **always**：每步都触发
  - **限制：最多 5 次**

- **never**：从不触发
  - 不推荐使用

## 代码修改

### main.py

1. **render_step 函数**（第 240 行）：
   ```python
   def render_step(step_number, action):
       raw_reply = action.get("raw_reply", "")

       # 显示 Actor 的原始输出
       if raw_reply and config.get("ui.show_actor_output", True):
           console.print(Panel(
               raw_reply,
               title=f"[bold blue]Actor (step {step_number})[/bold blue]",
               border_style="blue",
               padding=(1, 2)
           ))
   ```

2. **run_agent 函数**（第 299 行）：
   ```python
   max_reflection_steps = 5  # Reflection 最大步数
   reflection_count = 0      # Reflection 计数器

   def should_reflect(step_num, action_type):
       if reflection_count >= max_reflection_steps:
           return False
       # ... 其他逻辑
   ```

3. **Reflection 显示**（第 381 行）：
   ```python
   console.print(Panel(
       reflection,
       title=f"[bold magenta]Reflection ({reflection_count}/{max_reflection_steps}, step {step + 1})[/bold magenta]",
       border_style="magenta",
       padding=(1, 2)
   ))
   ```

## 好处

### 1. 完整的可观测性
- 用户可以看到 Actor 的完整思考过程
- 理解模型为什么做出某个决策
- 便于调试和优化 prompt

### 2. 防止无限循环
- Reflection 最多 5 次，避免资源浪费
- 即使 Actor 和 Reflection 陷入循环，也会在 5 次后自动结束
- 配合死循环检测（10 次连续拒绝），双重保护

### 3. 更好的用户体验
- 实时看到模型的工作进度
- 了解每一步的决策依据
- 增强对系统的信任

## 使用建议

### 调试模式
```yaml
ui:
  verbose: true           # 显示工具结果详情
  show_actor_output: true # 显示 Actor 思考
```

### 生产模式
```yaml
ui:
  verbose: false          # 隐藏工具结果详情
  show_actor_output: true # 仍然显示 Actor 思考（推荐）
```

### 最小输出模式
```yaml
ui:
  verbose: false
  show_actor_output: false  # 只显示工具调用和 Reflection
```

## 未来改进

- [ ] 支持配置 Reflection 最大次数（目前硬编码为 5）
- [ ] 支持按步骤类型过滤 Actor 输出（只显示工具调用，不显示纯文本）
- [ ] 支持导出完整的执行日志（包括所有 Actor 和 Reflection 输出）
- [ ] 支持实时流式显示 Actor 输出（当前是等待完整输出后显示）
