# 工具调用格式统一

## 修改动机

**问题**：代码中支持多种工具调用格式（标准 JSON、invoke 标签、arrow 语法），导致：
1. 代码复杂，维护困难
2. 模型可能输出不同格式，不一致
3. 没有明确的格式规范

**目标**：
1. 统一为标准 JSON 格式
2. 在提示词中强制要求 JSON 格式
3. 简化解析代码，只保留必要的容错机制
4. 添加"自己解决问题"的原则

## 修改内容

### 1. 强化提示词 - 格式要求

**文件**: `prompts/actor/base.md`

**新增内容**：
```markdown
## 工具调用格式

**严格要求**：当你需要使用工具时，必须【只输出纯 JSON】，不要有任何其他内容。

**格式要求**：
1. 只输出一个 JSON 对象
2. 不要输出 markdown 代码块（不要用 ```）
3. 不要输出任何解释、前后缀或额外文字
4. 不要使用 XML 标签（如 `<invoke>`、`<tool_call>` 等）
5. 不要使用其他语法（如 `tool => "..."`）

**唯一正确格式**：
{"tool": "工具名", "arguments": {...}}

**示例**：
- ✅ 正确：`{"tool": "run_cmd", "arguments": {"cmd": "dir"}}`
- ❌ 错误：`<invoke name="run_cmd">...</invoke>`
- ❌ 错误：`tool => "run_cmd"`
- ❌ 错误：```json\n{"tool": "run_cmd", ...}\n```
- ❌ 错误：`我将调用工具：{"tool": "run_cmd", ...}`
```

### 2. 添加"自己解决问题"原则

**文件**: `prompts/actor/base.md`

**新增内容**：
```markdown
**自己解决问题，不要让用户动手**：
- ❌ 错误：告诉用户"你需要先安装依赖"、"请手动创建这个文件"、"你可以运行这个命令"
- ✅ 正确：直接调用工具安装依赖、创建文件、运行命令
- ❌ 错误：遇到错误就放弃，让用户自己处理
- ✅ 正确：分析错误原因，尝试其他方法，直到解决问题
- **记住：如果有解决方案，你就应该自己执行，而不是指导用户去做！**
```

### 3. 简化 ToolCall 解析逻辑

**文件**: `Memory/ToolCall.py`

**删除的方法**：
- `_try_from_arrow_syntax()` - 删除 arrow 语法支持（`tool => "..."`）

**简化的方法**：
- `_try_from_minimax_syntax()` - 只保留 MiniMax 标签支持，删除 invoke 标签支持

**修改后的 `try_from_text()`**：
```python
@classmethod
def try_from_text(cls, text):
    """
    从文本中提取工具调用

    支持的格式：
    1. 标准 JSON（推荐）: {"tool": "...", "arguments": {...}}
    2. MiniMax 标签（容错）: <minimax:tool_call>{"tool": "...", ...}</minimax:tool_call>
    """
    # 优先尝试 MiniMax 特殊格式（某些模型会自动添加）
    minimax_call = cls._try_from_minimax_syntax(stripped)
    if minimax_call is not None:
        return minimax_call

    # 标准 JSON 格式解析
    # ... (提取 JSON 对象并解析)
```

## 支持的格式

### 主格式（推荐）
```json
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
```

### 容错格式（不推荐，但会解析）

1. **MiniMax 标签**（模型自动添加，无法控制）：
```xml
<minimax:tool_call>
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
</minimax:tool_call>
```

2. **Markdown 代码块**（模型可能误用）：
````markdown
```json
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
```
````

### 不再支持的格式（已删除）

1. ~~**invoke 标签**~~：
```xml
<invoke name="run_cmd", "arguments": {"cmd": "dir"}}
```

2. ~~**Arrow 语法**~~：
```
tool => "run_cmd"
arguments => {"cmd": "dir"}
```

## 为什么保留 MiniMax 标签？

**原因**：MiniMax 模型在 `thinking` 字段中会自动添加 `<minimax:tool_call>` 标签，这是模型行为，不是我们能控制的。

**策略**：
- 在提示词中要求标准 JSON
- 但在解析时容错 MiniMax 标签
- 如果模型遵守提示词，就不会用到这个容错机制

## 为什么保留 Markdown 代码块解析？

**原因**：模型可能误解提示词，在 JSON 外包裹 ````json ... ````

**策略**：
- 在提示词中明确禁止 markdown 代码块
- 但在解析时容错，自动去除代码块标记
- 这是防御性编程

## 测试验证

### 测试 1：标准 JSON
```python
from Memory.ToolCall import ToolCall

text = '{"tool": "run_cmd", "arguments": {"cmd": "dir"}}'
call = ToolCall.try_from_text(text)
assert call.tool_name == "run_cmd"
assert call.arguments == {"cmd": "dir"}
```

### 测试 2：MiniMax 标签（容错）
```python
text = '<minimax:tool_call>{"tool": "run_cmd", "arguments": {"cmd": "dir"}}</minimax:tool_call>'
call = ToolCall.try_from_text(text)
assert call.tool_name == "run_cmd"
```

### 测试 3：Markdown 代码块（容错）
```python
text = '```json\n{"tool": "run_cmd", "arguments": {"cmd": "dir"}}\n```'
call = ToolCall.try_from_text(text)
assert call.tool_name == "run_cmd"
```

### 测试 4：不再支持的格式
```python
# Arrow 语法 - 应该返回 None
text = 'tool => "run_cmd"\narguments => {"cmd": "dir"}'
call = ToolCall.try_from_text(text)
assert call is None

# invoke 标签 - 应该返回 None
text = '<invoke name="run_cmd", "arguments": {"cmd": "dir"}}'
call = ToolCall.try_from_text(text)
assert call is None
```

## 实际运行测试

```bash
python main.py
You > 创建一个文件 test.txt，内容为 "Hello"
```

**预期**：
- 模型输出：`{"tool": "run_cmd", "arguments": {"cmd": "echo Hello > test.txt"}}`
- 不应该输出：`<invoke ...>` 或 `tool => "..."`

## 代码行数对比

### 修改前
- `ToolCall.py`: ~229 行
- `_try_from_arrow_syntax()`: 18 行
- `_try_from_minimax_syntax()`: 48 行（支持 3 种格式）

### 修改后
- `ToolCall.py`: ~210 行（减少 19 行）
- `_try_from_arrow_syntax()`: 已删除
- `_try_from_minimax_syntax()`: 18 行（只支持 1 种格式）

**简化效果**：
- 删除 1 个方法
- 简化 1 个方法（减少 30 行）
- 总共减少约 50 行代码

## 文件修改清单

- ✅ `prompts/actor/base.md`：强化 JSON 格式要求，添加"自己解决问题"原则
- ✅ `Memory/ToolCall.py`：删除 arrow 语法，简化 minimax 语法，更新文档注释

## 后续建议

### 1. 监控模型输出
观察模型是否遵守提示词，是否还会输出非标准格式。如果模型总是输出标准 JSON，可以考虑进一步简化容错代码。

### 2. 添加格式警告
如果检测到模型使用了容错格式，记录警告：
```python
if minimax_call is not None:
    logger.warning("Model used MiniMax tag format instead of standard JSON")
    return minimax_call
```

### 3. 统计格式使用
记录每种格式的使用频率，决定是否保留容错机制：
```python
format_stats = {
    "standard_json": 0,
    "minimax_tag": 0,
    "markdown_block": 0,
}
```

### 4. 考虑完全移除容错
如果模型 100% 遵守提示词，可以删除所有容错代码，只保留标准 JSON 解析：
```python
@classmethod
def try_from_text(cls, text):
    try:
        payload = json.loads(text.strip())
        return cls._from_payload(payload)
    except:
        return None
```
