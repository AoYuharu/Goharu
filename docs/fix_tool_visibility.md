# 修复：工具可见性和描述问题

## 问题描述

用户报告了几个问题：
1. **看不到工具调用的返回结果** - 只在 `verbose: true` 时显示
2. **工具调用与 Actor 输出混在一起** - 不够清晰
3. **Agent 总是只用 run_cmd** - 不使用文件操作工具（Read、Edit、Grep）
4. **工具描述是中文** - 可能导致模型理解困难

## 根本原因分析

### 1. 工具返回结果不可见
**代码位置**: `main.py` 的 `render_step()` 函数

**问题**：
```python
if config.get("ui.verbose", False):
    console.print(f"  [dim]{result_preview}[/dim]")
```

工具返回结果只在 `verbose: true` 时显示，默认配置是 `verbose: false`，导致用户看不到工具执行结果。

### 2. 工具描述是中文
**代码位置**: `Tools/builtin/file_tools.py`

**问题**：
```python
description="查询文本文件内容，返回匹配文件路径、1-based 行号和单行内容。"
```

文件工具的描述全部是中文，而 `run_cmd` 的描述是英文。模型可能更容易理解英文描述，导致优先使用 `run_cmd`。

### 3. 工具注入格式
工具定义通过 `PromptAssembler._build_tool_directory_section()` 注入到系统提示中：

```python
content=f"{TOOL_DIRECTORY_PROMPT}\n\n{self._json_text(tool_definitions)}"
```

格式为：
```
以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。

[
  {"name": "Edit", "description": "...", "inputSchema": {...}},
  {"name": "Grep", "description": "...", "inputSchema": {...}},
  {"name": "Read", "description": "...", "inputSchema": {...}},
  {"name": "run_cmd", "description": "...", "inputSchema": {...}}
]
```

## 修复方案

### 1. 始终显示工具返回结果

**文件**: `main.py`

**修改前**：
```python
if config.get("ui.verbose", False):
    console.print(f"  [dim]{result_preview}[/dim]")
```

**修改后**：
```python
# 显示工具调用
console.print(f"\n[bold yellow]→ Tool Call[/bold yellow] [cyan]{tool_name}[/cyan] {arguments_summary}")

# 始终显示工具返回结果（不再依赖 verbose）
console.print(Panel(
    result_preview,
    title="[bold green]Tool Result[/bold green]",
    border_style="green",
    padding=(1, 2)
))
```

**效果**：
- Actor 输出：蓝色 Panel，标题 "Actor (step N)"
- 工具调用：黄色箭头 + 工具名
- 工具结果：绿色 Panel，标题 "Tool Result"
- 三者清晰分离

### 2. 将工具描述改为英文

**文件**: `Tools/builtin/file_tools.py`

**Grep 工具**：
```python
description=(
    "Search text in files and return matching file paths, 1-based line numbers, and line content. "
    "Required: pattern (text to search). Optional: path (file or directory, default '.'), "
    "case_sensitive (default true), max_results (default 100). "
    "Grep is read-only and does not grant Edit permission."
)
```

**Read 工具**：
```python
description=(
    "Read file content by 1-based line range [start_line, end_line]. "
    "Required: path. Optional: start_line (default 1), end_line (default: read to end), actor_id (default 'agent'). "
    "Returns file path, total lines, and line-by-line content. "
    "After successful read, the range is recorded. Edit can only modify ranges that have been Read first."
)
```

**Edit 工具**：
```python
description=(
    "Edit file by 1-based line numbers. "
    "Required: path, operation (insert/delete/replace), start_line. "
    "Operations: insert (insert content before start_line), delete (delete lines [start_line, end_line]), "
    "replace (replace lines [start_line, end_line] with content). "
    "IMPORTANT: You must Read the target range before Edit. Grep does not grant Edit permission. "
    "Use Read first, then Edit the same range."
)
```

### 3. 在 Actor 提示词中强调文件工具

**文件**: `prompts/actor/base.md`

**当前内容**：
```markdown
## 工具使用指南

- **文件读取**：调用 `Read`
- **文本搜索**：调用 `Grep`
- **文件修改**：先 `Read` 对应范围，再调用 `Edit`
- **创建文件**：调用 `run_cmd` 使用 Windows 命令
```

**建议增强**（可选）：
```markdown
## 工具选择优先级

**文件操作优先使用专用工具**：
- ✅ 读取文件内容：使用 `Read` 工具（不要用 `run_cmd` + `type`）
- ✅ 搜索文件内容：使用 `Grep` 工具（不要用 `run_cmd` + `findstr`）
- ✅ 修改文件内容：使用 `Edit` 工具（不要用 `run_cmd` + `echo`）
- ⚠️ 创建新文件：可以用 `run_cmd` + `echo` 或先创建空文件再用 `Edit`
- ⚠️ 执行脚本：使用 `run_cmd`
```

## 显示效果对比

### 修改前
```
[Actor (step 1)]
{"tool": "run_cmd", "arguments": {"cmd": "type file.txt"}}

→ run_cmd {"cmd": "type file.txt"}
```
- 看不到工具返回结果
- Actor 输出和工具调用混在一起

### 修改后
```
╭─────────────────────────────────────────────╮
│ Actor (step 1)                              │  ← 蓝色
├─────────────────────────────────────────────┤
│ {"tool": "Read", "arguments": {"path": ... │
╰─────────────────────────────────────────────╯

→ Tool Call Read {"path": "file.txt"}         ← 黄色

╭─────────────────────────────────────────────╮
│ Tool Result                                 │  ← 绿色
├─────────────────────────────────────────────┤
│ {"file": "file.txt", "total_lines": 10,    │
│  "content": [...]}                          │
╰─────────────────────────────────────────────╯
```
- 清晰分离三个部分
- 始终显示工具返回结果
- 使用专用文件工具

## 为什么模型优先使用 run_cmd？

### 可能的原因

1. **描述语言不一致**
   - `run_cmd`: 英文描述，详细示例
   - 文件工具: 中文描述（修复前）
   - 模型可能更容易理解英文

2. **描述详细程度**
   - `run_cmd`: 有具体示例（`dir`, `type`, `echo`）
   - 文件工具: 只有抽象描述，没有示例

3. **工具顺序**
   - 工具按注册顺序排列
   - `run_cmd` 可能排在前面，模型先看到

4. **提示词强调**
   - Actor 提示词中提到 `run_cmd` 的地方较多
   - 文件工具提到较少

### 验证方法

运行程序并观察模型选择：
```bash
python main.py
You > 读取 config.yaml 文件的前 10 行
```

**预期**：
- 模型应该使用 `Read` 工具：`{"tool": "Read", "arguments": {"path": "config.yaml", "end_line": 10}}`
- 而不是 `run_cmd`：`{"tool": "run_cmd", "arguments": {"cmd": "type config.yaml"}}`

## 测试验证

### 测试 1：工具返回结果可见
```bash
python main.py
You > 创建一个文件 test.txt
```

**预期**：
- 看到 Actor 输出（蓝色 Panel）
- 看到工具调用（黄色箭头）
- 看到工具返回结果（绿色 Panel）

### 测试 2：使用文件工具
```bash
You > 读取 config.yaml 的前 5 行
```

**预期**：
- 模型使用 `Read` 工具，不是 `run_cmd`
- 返回结果包含 `{"file": "config.yaml", "content": [...]}`

### 测试 3：搜索文件内容
```bash
You > 在当前目录搜索包含 "model" 的文件
```

**预期**：
- 模型使用 `Grep` 工具，不是 `run_cmd` + `findstr`
- 返回结果包含 `{"matches": [...], "count": N}`

### 测试 4：修改文件
```bash
You > 读取 test.txt，然后在第 1 行前插入 "Hello"
```

**预期**：
- 先调用 `Read` 读取文件
- 再调用 `Edit` 插入内容
- 不使用 `run_cmd` + `echo`

## 文件修改清单

- ✅ `main.py`：`render_step()` 始终显示工具返回结果，使用 Panel 分离显示
- ✅ `Tools/builtin/file_tools.py`：将 Grep、Read、Edit 的描述改为英文

## 后续改进建议

### 1. 添加工具使用统计
记录每个工具的使用频率：
```python
tool_usage_stats = {
    "run_cmd": 0,
    "Read": 0,
    "Edit": 0,
    "Grep": 0,
}
```

### 2. 在提示词中添加工具选择示例
```markdown
## 工具选择示例

**任务**: 读取文件内容
- ❌ 错误：`{"tool": "run_cmd", "arguments": {"cmd": "type file.txt"}}`
- ✅ 正确：`{"tool": "Read", "arguments": {"path": "file.txt"}}`

**任务**: 搜索文件
- ❌ 错误：`{"tool": "run_cmd", "arguments": {"cmd": "findstr pattern file.txt"}}`
- ✅ 正确：`{"tool": "Grep", "arguments": {"pattern": "pattern", "path": "file.txt"}}`
```

### 3. 工具描述添加使用场景
```python
description=(
    "Read file content by 1-based line range. "
    "Use this when you need to: view file content, check file structure, or prepare for editing. "
    "Do NOT use run_cmd with 'type' command - use this tool instead."
)
```

### 4. 调整工具注册顺序
将文件工具注册在 `run_cmd` 之前，让模型先看到：
```python
# file_tools.py 先注册
from Tools.builtin import file_tools
# core_tools.py 后注册
from Tools.builtin import core_tools
```
