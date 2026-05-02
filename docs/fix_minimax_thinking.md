# 修复：MiniMax thinking 字段和 tool_call 标签支持

## 问题描述

用户遇到错误：
```
ValueError: Anthropic-compatible provider returned no text content:
{"id": "0643f028bb5646eeafffbef7cee0292c", "content": [{"type": "thinking", "thinking": "...<minimax:tool_call>..."}], ...}
```

**根本原因**：
1. MiniMax 模型返回的 `content` 数组中只有 `thinking` 类型的块，没有 `text` 类型
2. `thinking` 字段里包含了工具调用，使用了 `<minimax:tool_call>` 标签
3. 当前的 `_extract_response_text()` 只提取 `text` 类型，忽略了 `thinking`
4. 当前的 `ToolCall.try_from_text()` 不支持 `<minimax:tool_call>` 标签

## MiniMax 响应格式

### 标准响应（有 text）
```json
{
  "content": [
    {"type": "text", "text": "这是回答"}
  ]
}
```

### MiniMax thinking 响应（无 text）
```json
{
  "content": [
    {
      "type": "thinking",
      "thinking": "用户问文件保存在哪里...\n<minimax:tool_call>\n{\"tool\": \"run_cmd\", \"arguments\": {\"cmd\": \"...\"}}\n</minimax:tool_call>"
    }
  ]
}
```

## 修复方案

### 1. 支持从 thinking 字段提取内容

**文件**: `Agent/LLMCore.py`

**修改前**：
```python
@staticmethod
def _extract_response_text(response):
    text_parts = []
    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            continue
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))

    response_text = "\n".join(part for part in text_parts if part).strip()
    if response_text:
        return response_text

    # fallback...
    return ""
```

**修改后**：
```python
@staticmethod
def _extract_response_text(response):
    text_parts = []
    thinking_parts = []

    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                # MiniMax 的 thinking 字段可能包含工具调用
                thinking_parts.append(block.get("thinking", ""))
            continue
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
        elif getattr(block, "type", None) == "thinking":
            thinking_parts.append(getattr(block, "thinking", ""))

    response_text = "\n".join(part for part in text_parts if part).strip()
    if response_text:
        return response_text

    # 如果没有 text 内容，尝试从 thinking 中提取
    thinking_text = "\n".join(part for part in thinking_parts if part).strip()
    if thinking_text:
        return thinking_text

    # fallback...
    return ""
```

### 2. 支持 `<minimax:tool_call>` 标签

**文件**: `Memory/ToolCall.py`

在 `_try_from_minimax_syntax()` 方法开头添加：

```python
@classmethod
def _try_from_minimax_syntax(cls, text):
    # Format 1: <minimax:tool_call>{"tool": "...", "arguments": {...}}</minimax:tool_call>
    match = re.search(
        r'<minimax:tool_call>\s*(\{.*?\})\s*</minimax:tool_call>',
        text,
        re.DOTALL
    )
    if match:
        try:
            payload = json.loads(match.group(1))
            tool_call = cls._from_payload(payload)
            if tool_call is not None:
                return tool_call
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    # Format 2: <invoke name="tool", "arguments": {...}}
    # ... (原有代码)
```

## 支持的工具调用格式

### 1. 标准 JSON
```json
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
```

### 2. MiniMax tool_call 标签
```xml
<minimax:tool_call>
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
</minimax:tool_call>
```

### 3. invoke 标签（旧格式）
```xml
<invoke name="run_cmd", "arguments": {"cmd": "dir"}}
```

### 4. Arrow 语法
```
tool => "run_cmd"
arguments => {"cmd": "dir"}
```

## 执行流程

### 修复前
```
MiniMax 返回 thinking 字段
  ↓
_extract_response_text() 只找 text 类型
  ↓
找不到 text，返回空字符串
  ↓
ValueError: returned no text content
```

### 修复后
```
MiniMax 返回 thinking 字段
  ↓
_extract_response_text() 先找 text，找不到则找 thinking
  ↓
提取 thinking 内容（包含 <minimax:tool_call>）
  ↓
ToolCall.try_from_text() 解析 <minimax:tool_call> 标签
  ↓
成功提取工具调用
```

## 测试验证

### 测试 1：模拟 MiniMax thinking 响应
```python
# 创建测试文件 test_minimax.py
from Memory.ToolCall import ToolCall

thinking_text = """
用户问文件保存在哪里，我需要查看当前目录。
<minimax:tool_call>
{"tool": "run_cmd", "arguments": {"cmd": "dir"}}
</minimax:tool_call>
"""

tool_call = ToolCall.try_from_text(thinking_text)
print(f"Tool: {tool_call.tool_name}")
print(f"Args: {tool_call.arguments}")
```

**预期输出**：
```
Tool: run_cmd
Args: {'cmd': 'dir'}
```

### 测试 2：实际运行
```bash
python main.py
You > 写一个 Python 文件，输出当前时间
```

**预期**：
- 模型应该能正常提取 thinking 字段
- 工具调用应该被正确解析
- 不再出现 "returned no text content" 错误

## 兼容性

### 支持的提供商
- ✅ Anthropic Claude（标准 text 块）
- ✅ MiniMax（thinking 块 + `<minimax:tool_call>` 标签）
- ✅ 其他 Anthropic-compatible 提供商（标准 text 块）

### 响应优先级
1. 优先使用 `text` 类型的内容
2. 如果没有 `text`，使用 `thinking` 类型的内容
3. 如果都没有，尝试 `response.text` 或 `response.completion` 属性

## 文件修改清单

- ✅ `Agent/LLMCore.py`：`_extract_response_text()` 支持 thinking 字段
- ✅ `Memory/ToolCall.py`：`_try_from_minimax_syntax()` 支持 `<minimax:tool_call>` 标签

## 验证

```bash
# 语法检查
python -m py_compile Agent/LLMCore.py Memory/ToolCall.py

# 运行程序
python main.py
```

## 注意事项

### 1. thinking 内容可能很长
MiniMax 的 thinking 字段可能包含大量思考过程，不仅仅是工具调用。`ToolCall.try_from_text()` 会从中提取工具调用部分。

### 2. 多个工具调用
如果 thinking 中包含多个 `<minimax:tool_call>` 标签，当前实现只会提取第一个。如果需要支持多个，需要修改解析逻辑。

### 3. JSON 转义
thinking 字段中的 JSON 可能包含转义字符，当前实现会尝试处理，但复杂情况可能需要额外处理。

## 后续改进建议

### 1. 统一响应格式
创建响应适配器，统一不同提供商的响应格式：
```python
class ResponseAdapter:
    @staticmethod
    def extract_content(response, provider):
        if provider == "minimax":
            return extract_thinking(response)
        else:
            return extract_text(response)
```

### 2. 支持多工具调用
如果模型在一次响应中调用多个工具：
```python
def extract_all_tool_calls(text):
    calls = []
    for match in re.finditer(r'<minimax:tool_call>(.*?)</minimax:tool_call>', text):
        call = ToolCall.try_from_text(match.group(1))
        if call:
            calls.append(call)
    return calls
```

### 3. 日志记录
记录 thinking 内容，帮助调试：
```python
if thinking_text:
    logger.debug(f"Extracted from thinking: {thinking_text[:200]}...")
```
