# /clear 命令：清空当前会话上下文

## 功能说明

`/clear` 命令用于清空当前会话的上下文，让你可以开始一个全新的对话，而不需要重启程序。

## 使用方法

在任何时候输入：
```
You > /clear
```

系统会显示：
```
✓ Current session context cleared
```

## 效果

### 清空的内容
- ✅ 当前会话的所有消息（今天的对话历史）
- ✅ Actor 的工具调用记录
- ✅ Reflection 的判断历史
- ✅ 用户的问题和 Assistant 的回答

### 保留的内容
- ✅ 长期记忆（MEMORY.md 和主题文件）
- ✅ 用户画像（USER.md）
- ✅ 之前日期的对话历史
- ✅ 工具定义和配置

## 使用场景

### 场景 1：切换话题
```
You > 帮我写一个 Python 脚本
[完成任务...]

You > /clear
✓ Current session context cleared

You > 现在帮我分析一下这段代码
[开始新的对话，不受之前上下文影响]
```

### 场景 2：上下文过长
```
You > [经过多轮对话，上下文变得很长]

You > /clear
✓ Current session context cleared

You > [重新开始，上下文清空，推理更快]
```

### 场景 3：重置状态
```
You > [模型陷入某种状态或循环]

You > /clear
✓ Current session context cleared

You > [重新开始，状态重置]
```

### 场景 4：测试不同方法
```
You > 用方法 A 解决这个问题
[尝试方法 A...]

You > /clear
✓ Current session context cleared

You > 用方法 B 解决这个问题
[尝试方法 B，不受方法 A 的影响]
```

## 与重启程序的区别

| 操作 | /clear | 重启程序 |
|------|--------|----------|
| 清空当前会话 | ✅ | ✅ |
| 保留长期记忆 | ✅ | ✅ |
| 保留用户画像 | ✅ | ✅ |
| 保留工具状态 | ✅ | ❌ |
| 速度 | 快速 | 慢（需要重新加载模型） |
| 日志 | 继续记录 | 新建日志文件 |

## 技术实现

### 1. WorkingMemory.clear_today()
```python
def clear_today(self):
    """清空今天的消息（当前会话）"""
    today = date.today().isoformat()
    self.delete_day(today)
```

删除今天的日志文件（`YYYY-MM-DD.json`）。

### 2. MemoryManager.clear_context()
```python
def clear_context(self):
    """清空当前会话上下文"""
    self.working.clear_today()
```

调用 WorkingMemory 的清空方法。

### 3. main.py 中的命令处理
```python
if command == "/clear":
    if memory_manager:
        memory_manager.clear_context()
        console.print("[green]✓ Current session context cleared[/green]")
    else:
        print_message("[yellow]Memory manager not available yet[/yellow]")
    continue
```

## 注意事项

### 1. 不可恢复
清空后的上下文**无法恢复**，请确认后再执行。

### 2. 只清空今天
只清空今天的对话，之前日期的对话历史不受影响。

### 3. 长期记忆保留
长期记忆（MEMORY.md）和用户画像（USER.md）不会被清空。

### 4. 工具状态保留
文件工具的读写状态（已读范围、锁状态）会保留，因为它们存储在内存中，不在 WorkingMemory 中。

## 完整命令列表

```
Commands:
  /help      Show this help
  /multi     Enter multiline input mode
  /context   Show the current context that will be sent to the API
  /clear     Clear the current session context          ← 新增
  /interrupt Interrupt the current agent execution
  /exit      Exit the app
  /quit      Exit the app
  exit       Exit the app
```

## 示例对话

```
You > 写一个 Python 脚本输出当前时间

╭─────────────────────────────────────────────╮
│ Actor (step 1)                              │
├─────────────────────────────────────────────┤
│ {"tool": "Edit", "arguments": {...}}        │
╰─────────────────────────────────────────────╯

→ Edit {"path": "time.py", ...}

╭─────────────────────────────────────────────╮
│ Reflection (1/5, step 1)                    │
├─────────────────────────────────────────────┤
│ 可以给出最终回答。                          │
╰─────────────────────────────────────────────╯

╭─────────────────────────────────────────────╮
│ Assistant                                   │
├─────────────────────────────────────────────┤
│ 已创建 time.py 文件，输出当前时间。         │
╰─────────────────────────────────────────────╯

You > /clear
✓ Current session context cleared

You > 写一个 Python 脚本输出 Hello World

[开始新的对话，不记得之前的 time.py]
```

## 文件修改

- ✅ `Memory/WorkingMemory.py`：添加 `clear_today()` 方法
- ✅ `Memory/MemoryManager.py`：添加 `clear_context()` 方法
- ✅ `main.py`：添加 `/clear` 命令处理和更新帮助文本

## 测试

```bash
# 启动程序
python main.py

# 进行一些对话
You > 你好

# 清空上下文
You > /clear
✓ Current session context cleared

# 验证上下文已清空
You > /context
[应该只显示系统提示，没有之前的对话]

# 继续对话
You > 写一个脚本
[应该不记得之前的对话]
```
