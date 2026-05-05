# Rich Markup 错误修复报告

## 🐛 问题描述

**错误信息**:
```
rich.errors.MarkupError: closing tag '[/TOOL_CALL]' at position 169 doesn't match any open tag
```

## 🔍 问题分析

### 根本原因

**Rich 库误把模型输出当作 markup 标签解析**：

模型输出包含：
```
[/TOOL_CALL]
```

Rich 尝试将其解析为 markup 标签（类似 `[bold]text[/bold]`），但找不到对应的开始标签 `[TOOL_CALL]`，导致报错。

### 发生位置

从堆栈追踪看，错误发生在 `rich.console.py` 的渲染过程中：
```python
rich.console.py:1385 in render_lines
  ↓
rich.markup.py:167 in render
  ↓
MarkupError: closing tag '[/TOOL_CALL]' doesn't match
```

说明是在**显示输出时**出错，不是在解析工具调用时。

---

## 🛡️ 我们的防御机制

### 三重防御（针对工具调用解析）

我们的防御是针对**工具调用解析**的：

1. **防线1**: `ToolCallGuard` - 修复工具名和参数
2. **防线2**: Schema 验证 - 验证参数格式
3. **防线3**: 重试机制 - 失败后重试

**但是**，这些防御都在**解析层**，而 Rich 的 markup 解析发生在**显示层**，所以没有被拦截。

### 为什么没有拦住？

```
模型输出
  ↓
工具调用解析 ← 我们的三重防御在这里
  ↓
工具执行
  ↓
结果显示 ← Rich markup 解析在这里（我们没有防御）
  ↓
❌ 错误发生
```

---

## ✅ 修复方案

### 问题代码

**缺少 `escape()` 的地方**：

```python
# ❌ 错误：直接显示用户内容
console.print(Panel(
    raw_reply,  # 可能包含 [xxx] 标签
    title="...",
))

console.print(Panel(
    result_preview,  # 可能包含 [xxx] 标签
    title="...",
))

console.print(Panel(
    reflection,  # 可能包含 [xxx] 标签
    title="...",
))
```

### 修复代码

**添加 `escape()` 转义**：

```python
# ✅ 正确：转义用户内容
from rich.markup import escape

console.print(Panel(
    escape(raw_reply),  # 转义特殊字符
    title="...",
))

console.print(Panel(
    escape(result_preview),
    title="...",
))

console.print(Panel(
    escape(reflection),
    title="...",
))
```

### 修复的位置

**文件**: `main.py`

1. **第 315-322 行** - Actor 输出显示
   - 添加 `escape(raw_reply)`

2. **第 351-361 行** - 工具结果显示
   - 添加 `escape(tool_name)`
   - 添加 `escape(arguments_summary)`
   - 添加 `escape(result_preview)`

3. **第 549-555 行** - Reflection 显示（第一处）
   - 添加 `escape(reflection)`

4. **第 620-626 行** - Reflection 显示（第二处）
   - 添加 `escape(reflection)`

---

## 📊 Rich Markup 语法

### Rich 的 markup 标签

Rich 使用方括号作为标签：
```python
"[bold]粗体[/bold]"
"[red]红色[/red]"
"[cyan]青色[/cyan]"
```

### 冲突的情况

如果用户内容包含方括号：
```python
# 用户内容
"[/TOOL_CALL]"
"[bold]这不是标签"
"[123]数组索引"

# Rich 会尝试解析为标签
# 导致错误
```

### escape() 的作用

```python
from rich.markup import escape

# 转义前
"[/TOOL_CALL]"

# 转义后
"\\[/TOOL_CALL]"  # 反斜杠转义，Rich 不会解析为标签
```

---

## 🎯 完整的防御体系

### 解析层防御（已有）

```
模型输出
  ↓
┌─────────────────────────┐
│ 防线1: ToolCallGuard    │ ← 修复工具名和参数
│ 防线2: Schema 验证      │ ← 验证参数格式
│ 防线3: 重试机制         │ ← 失败后重试
└─────────────────────────┘
  ↓
工具执行
```

### 显示层防御（新增）

```
工具结果
  ↓
┌─────────────────────────┐
│ 防线4: Rich escape()    │ ← 转义特殊字符
└─────────────────────────┘
  ↓
安全显示
```

---

## 🔍 其他已有 escape 的地方

代码中已经有一些地方使用了 `escape()`：

1. **第 385-386 行** - 错误消息
2. **第 399-406 行** - 批量工具调用结果
3. **第 422-429 行** - 单个工具调用结果
4. **第 485-497 行** - on_tool_call_start 回调
5. **第 807-816 行** - 子agent输出回调
6. **第 861-862 行** - 最终答案显示

**但是**，有些地方遗漏了，导致了这次的错误。

---

## ✅ 修复效果

### 修复前

```python
console.print(Panel(raw_reply, ...))
# 如果 raw_reply 包含 "[/TOOL_CALL]"
# ❌ Rich 尝试解析为标签 → 错误
```

### 修复后

```python
console.print(Panel(escape(raw_reply), ...))
# escape() 将 "[/TOOL_CALL]" 转义为 "\\[/TOOL_CALL]"
# ✅ Rich 不会解析为标签 → 正常显示
```

---

## 📝 最佳实践

### 规则

**所有用户生成的内容都必须 escape**：

```python
# ✅ 正确
from rich.markup import escape

console.print(Panel(escape(user_content), ...))
console.print(f"[cyan]{escape(user_text)}[/cyan]")

# ❌ 错误
console.print(Panel(user_content, ...))  # 可能包含标签
console.print(f"[cyan]{user_text}[/cyan]")  # 可能包含标签
```

### 什么需要 escape？

- ✅ 模型输出（raw_reply, answer, reflection）
- ✅ 工具结果（result_preview）
- ✅ 用户输入（question）
- ✅ 错误消息（error_msg）
- ❌ 固定文本（"Tool Call", "Assistant"）
- ❌ 我们自己的标签（"[bold]", "[cyan]"）

---

## 🎉 总结

### 问题

- Rich 库误把模型输出中的 `[/TOOL_CALL]` 当作 markup 标签解析
- 我们的三重防御在解析层，没有覆盖显示层

### 修复

- 在所有显示用户内容的地方添加 `escape()`
- 共修复 4 处遗漏的地方

### 效果

- ✅ 防止 Rich markup 解析错误
- ✅ 用户内容安全显示
- ✅ 不影响正常的 markup 标签

### 教训

**显示层也需要防御**：
- 解析层防御 → 防止工具调用错误
- 显示层防御 → 防止渲染错误

**所有用户内容都要转义**！

---

**修复完成！现在所有用户生成的内容都会被正确转义，不会再出现 Rich markup 错误。** ✅
