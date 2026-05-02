# 修复：Actor 输出可见性和用户中断问题

## 问题描述

用户报告了以下问题：
1. **看不到 Actor 的输出**：只能看到 Reflection，看不到 Actor 的思考过程
2. **无法中断执行**：输入完毕后 `You >` 直接消失，`Ctrl+C` 也没用
3. **工具调用没有返回结果**：日志显示工具调用发出但没有返回

## 根本原因

### 1. Progress 的 transient 模式
```python
with Progress(..., transient=True) as progress:
    # 所有输出都会被清除
```

`transient=True` 会在 Progress 结束时清除所有输出，导致：
- Actor 的输出被清除
- 工具调用信息被清除
- 用户看不到任何执行过程

### 2. Progress 阻塞用户输入
在 `Progress` 的 `with` 块内部，用户无法输入，因此：
- `/interrupt` 命令无法使用
- `Ctrl+C` 被 Progress 捕获

### 3. answer 类型缺少 raw_reply
```python
return {
    "type": "answer",
    "answer": reply,
    "answer_preview": self._preview_text(reply),
    # 缺少 "raw_reply": reply
}
```

## 修复方案

### 1. 移除 Progress 包装器

**修改前**：
```python
if RICH_AVAILABLE and console:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Thinking...", total=None)
        for step in range(max_depth):
            action = await actor.act()
            render_step(step + 1, action)
            # ...
```

**修改后**：
```python
if RICH_AVAILABLE and console:
    # 不使用 Progress，直接显示输出
    for step in range(max_depth):
        # 检查中断
        if check_interrupt():
            console.print("[yellow]⚠ Execution interrupted by user[/yellow]")
            return {"interrupted": True, ...}

        action = await actor.act()
        render_step(step + 1, action)
        # ...
```

### 2. 添加 raw_reply 到 answer 类型

**Agent/ActorAgent.py**：
```python
self.working.append({"role": "assistant", "content": reply})
return {
    "type": "answer",
    "answer": reply,
    "answer_preview": self._preview_text(reply),
    "raw_reply": reply,  # 新增
}
```

### 3. 修复缩进错误

统一了所有缩进，确保代码结构正确。

## 测试步骤

### 测试 1：Actor 输出可见性

1. 启动程序：
```bash
python main.py
```

2. 输入任务：
```
You > 写一个python文件，输出当前时间
```

3. **预期结果**：
```
╭─────────────────────────────────────────────╮
│ Actor (step 1)                              │  ← 应该看到这个
├─────────────────────────────────────────────┤
│ {"tool": "Edit", "arguments": {...}}        │
╰─────────────────────────────────────────────╯

→ Edit {"path": "time.py", ...}               ← 应该看到工具调用

╭─────────────────────────────────────────────╮
│ Reflection (1/5, step 1)                    │  ← 应该看到 Reflection
├─────────────────────────────────────────────┤
│ ...                                         │
╰─────────────────────────────────────────────╯
```

### 测试 2：用户中断

1. 启动程序并输入任务
2. 在执行过程中，在另一个终端或等待下一轮输入时输入：
```
You > /interrupt
```

3. **预期结果**：
```
Interrupt requested. Will stop at next step.

⚠ Execution interrupted by user

╭─────────────────────────────────────────────╮
│ Interrupted                                 │
├─────────────────────────────────────────────┤
│ 执行已被用户中断。                          │
╰─────────────────────────────────────────────╯

You >
```

### 测试 3：工具调用执行

1. 输入任务：
```
You > 创建一个文件 test.txt，内容为 "Hello World"
```

2. **预期结果**：
- 应该看到 Actor 输出工具调用 JSON
- 应该看到工具执行（`→ run_cmd ...`）
- 应该看到 Reflection 判断
- 最终应该看到成功消息

3. 验证文件是否创建：
```bash
cat test.txt
```

应该输出：
```
Hello World
```

## 已知限制

### 1. 中断延迟
- 无法在 LLM 推理过程中立即中断
- 无法在工具执行过程中立即中断
- 只能在步骤之间中断

### 2. Ctrl+C 行为
- `Ctrl+C` 会强制终止整个程序（退出应用）
- 如果需要优雅中断，使用 `/interrupt` 命令

### 3. 无进度指示
- 移除了 Progress 后，没有 spinner 显示
- 用户需要通过 Actor 和 Reflection 的输出来判断进度

## 后续改进建议

### 1. 添加非阻塞的进度指示
使用独立线程显示进度，不阻塞输出：
```python
import threading

def show_spinner():
    while not stop_spinner:
        print(".", end="", flush=True)
        time.sleep(0.5)

spinner_thread = threading.Thread(target=show_spinner)
spinner_thread.start()
```

### 2. 支持异步中断
使用信号处理支持真正的异步中断：
```python
import signal

def signal_handler(sig, frame):
    request_interrupt()

signal.signal(signal.SIGINT, signal_handler)
```

### 3. 添加执行摘要
在每个步骤后显示简短摘要：
```
[Step 1] Actor → Edit tool → Success
[Step 2] Reflection → Continue
[Step 3] Actor → run_cmd tool → Success
[Step 4] Reflection → Done
```

## 文件修改清单

- ✅ `main.py` (第 340-456 行)：移除 Progress，修复缩进
- ✅ `Agent/ActorAgent.py` (第 176-181 行)：添加 raw_reply 到 answer 类型

## 验证

```bash
# 语法检查
python -m py_compile main.py
python -m py_compile Agent/ActorAgent.py

# 运行程序
python main.py
```
