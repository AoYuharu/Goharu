# Anthropic 原生工具调用实现文档

## 概述

本项目现已支持 **Anthropic 原生工具调用格式**（`tool_use` / `tool_result`），同时保持对传统文本 JSON 格式的向后兼容。

### 主要特性

1. **双模式兼容**：根据 `provider` 和配置自动选择格式
   - `anthropic_compatible` + `use_native_tools: true` → 原生格式
   - `local_hf` 或 `use_native_tools: false` → 文本 JSON 格式

2. **多工具并发调用**：一次响应可包含多个 `tool_use` 块，并发执行

3. **混合重试策略**：
   - 首次尝试使用 `tool_choice` 参数引导模型
   - 失败后使用重试机制（最多 3 次）
   - 结构化 JSON 验证，失败自动打回重写

4. **结构化输出**：API 层面进行验证，避免格式错误

## 架构设计

### 1. ToolCall 类扩展 (`Memory/ToolCall.py`)

新增方法：

```python
# 从 Anthropic tool_use 块创建 ToolCall
@classmethod
def from_anthropic_tool_use(cls, tool_use_block)

# 转换为 Anthropic tool_use 块
def to_anthropic_tool_use(self, tool_use_id=None)

# 创建 Anthropic tool_result 块
@staticmethod
def create_anthropic_tool_result(tool_use_id, content, is_error=False)
```

### 2. LLMCore 扩展 (`Agent/LLMCore.py`)

#### 支持原生工具调用参数

```python
def _generate_anthropic_compatible(self, messages, **gen_kwargs):
    # 支持 tools 参数
    tools = gen_kwargs.pop("tools", None)
    if tools:
        request_kwargs["tools"] = tools

    # 支持 tool_choice 参数
    tool_choice = gen_kwargs.pop("tool_choice", None)
    if tool_choice:
        request_kwargs["tool_choice"] = tool_choice

    # 返回完整响应对象（包含 tool_use 块）
    if tools:
        return last_response
```

#### 消息格式处理

```python
def _prepare_anthropic_messages(self, messages):
    # 支持原生格式的 content（块数组）
    if isinstance(content, list):
        # content 是块数组：[{"type": "text", ...}, {"type": "tool_use", ...}]
        filtered_content = []
        for block in content:
            if block.get("type") in {"text", "tool_use", "tool_result"}:
                filtered_content.append(block)
        remote_messages.append({"role": role, "content": filtered_content})
```

### 3. ActorAgent 双模式实现 (`Agent/ActorAgent.py`)

#### 模式判断

```python
def _should_use_native_tools(self):
    """判断是否应该使用 Anthropic 原生工具调用"""
    return (
        self.provider == "anthropic_compatible"
        and self.use_native_tools
        and hasattr(self.tool_runtime, "last_tool_definitions")
        and self.tool_runtime.last_tool_definitions
    )
```

#### 双模式入口

```python
async def act(self, max_retries=3, on_tool_call_start=None):
    use_native = self._should_use_native_tools()

    if use_native:
        return await self._act_with_native_tools(max_retries, on_tool_call_start)
    else:
        return await self._act_with_text_tools(max_retries, on_tool_call_start)
```

#### 原生模式实现

```python
async def _act_with_native_tools(self, max_retries=3, on_tool_call_start=None):
    # 1. 转换工具定义为 Anthropic 格式
    anthropic_tools = self._convert_tools_to_anthropic_format(tool_definitions)

    # 2. 混合策略：首次使用 tool_choice 引导
    if max_retries == 3 and len(anthropic_tools) == 1:
        gen_kwargs["tool_choice"] = {"type": "tool", "name": anthropic_tools[0]["name"]}

    # 3. 调用 LLM
    response = self.query(messages, **gen_kwargs)

    # 4. 提取所有 tool_use 块
    tool_use_blocks = [block for block in response.content if block.type == "tool_use"]

    # 5. 并发执行所有工具调用
    results = await asyncio.gather(*[execute_single_tool(block) for block in tool_use_blocks])

    # 6. 错误处理和重试
    if errors and max_retries > 0:
        # 构建 tool_result（错误）并重试
        self.working.append({"role": "assistant", "content": [...]})
        self.working.append({"role": "user", "content": [tool_results + error_message]})
        return await self._act_with_native_tools(max_retries - 1, on_tool_call_start)

    # 7. 记录成功的工具调用
    self.working.append({"role": "assistant", "content": [tool_use_blocks]})
    self.working.append({"role": "user", "content": [tool_results]})
```

## 配置说明

### config.yaml

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: MiniMax-M2.7
    api_key_env: ANTHROPIC_API_KEY
    base_url: https://api.minimaxi.com/anthropic

    # 是否使用 Anthropic 原生工具调用格式
    # true: 使用原生格式（推荐，支持多工具并发、结构化验证）
    # false: 使用文本 JSON 格式（向后兼容）
    use_native_tools: true
```

### 模式选择逻辑

| provider | use_native_tools | 实际使用格式 |
|----------|------------------|-------------|
| `anthropic_compatible` | `true` | Anthropic 原生格式 |
| `anthropic_compatible` | `false` | 文本 JSON 格式 |
| `local_hf` | 任意 | 文本 JSON 格式 |

## 消息格式对比

### 文本 JSON 格式（传统）

```json
// Assistant 消息
{
  "role": "assistant",
  "content": "{\"tool\": \"Read\", \"arguments\": {\"file_path\": \"/test.txt\"}}"
}

// Tool 结果消息
{
  "role": "tool",
  "name": "Read",
  "content": "文件内容..."
}
```

### Anthropic 原生格式（新）

```json
// Assistant 消息（包含 tool_use 块）
{
  "role": "assistant",
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_01A2B3C4D5E6F7G8H9I0J1K2",
      "name": "Read",
      "input": {"file_path": "/test.txt"}
    }
  ]
}

// User 消息（包含 tool_result 块）
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_01A2B3C4D5E6F7G8H9I0J1K2",
      "content": "文件内容...",
      "is_error": false
    }
  ]
}
```

## 多工具并发调用示例

### 原生格式

```json
// Assistant 响应（一次返回多个 tool_use）
{
  "role": "assistant",
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_01",
      "name": "Read",
      "input": {"file_path": "/file1.txt"}
    },
    {
      "type": "tool_use",
      "id": "toolu_02",
      "name": "Grep",
      "input": {"pattern": "error", "path": "/logs"}
    }
  ]
}

// User 响应（返回所有 tool_result）
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_01",
      "content": "文件1内容...",
      "is_error": false
    },
    {
      "type": "tool_result",
      "tool_use_id": "toolu_02",
      "content": "搜索结果...",
      "is_error": false
    }
  ]
}
```

### 文本 JSON 格式

```json
// Assistant 响应（JSON 数组）
{
  "role": "assistant",
  "content": "[{\"tool\": \"Read\", \"arguments\": {\"file_path\": \"/file1.txt\"}}, {\"tool\": \"Grep\", \"arguments\": {\"pattern\": \"error\", \"path\": \"/logs\"}}]"
}

// Tool 结果（分别记录）
{
  "role": "tool",
  "name": "Read",
  "content": "文件1内容..."
}
{
  "role": "tool",
  "name": "Grep",
  "content": "搜索结果..."
}
```

## 错误处理和重试

### 混合重试策略

1. **首次尝试**（max_retries=3）：
   - 如果只有一个工具，使用 `tool_choice` 强制调用
   - 减少格式错误的可能性

2. **重试阶段**（max_retries=2,1）：
   - 将错误信息作为 `tool_result` 反馈给模型
   - 让模型重新生成正确的工具调用

3. **最终失败**（max_retries=0）：
   - 返回错误信息给用户

### 错误反馈示例

```json
// 工具调用失败
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_01",
      "content": "工具 'Rea' 不存在。可用工具: Read, Write, Grep",
      "is_error": true
    },
    {
      "type": "text",
      "text": "部分工具调用失败: 工具 'Rea' 不存在。可用工具: Read, Write, Grep\n请重新生成正确的工具调用。"
    }
  ]
}
```

## 优势对比

| 特性 | 文本 JSON 格式 | Anthropic 原生格式 |
|------|---------------|-------------------|
| **格式验证** | 需要手动解析 | API 层面验证 |
| **多工具调用** | 需要解析 JSON 数组 | 原生支持 |
| **错误处理** | 文本错误消息 | 结构化 `is_error` 标记 |
| **工具引导** | 不支持 | 支持 `tool_choice` |
| **兼容性** | 所有 provider | 仅 `anthropic_compatible` |
| **稳定性** | 依赖模型输出质量 | API 保证格式正确 |

## 测试

运行测试：

```bash
python test_anthropic_native_tools.py
```

测试覆盖：
- ✓ ToolCall 与 Anthropic 格式转换
- ✓ 文本 JSON 格式兼容性
- ✓ Anthropic 原生格式解析
- ✓ 工具定义格式转换
- ✓ 错误处理

## 迁移指南

### 从文本 JSON 迁移到原生格式

1. **更新配置**：
   ```yaml
   model:
     large-language-model:
       provider: anthropic_compatible
       use_native_tools: true  # 启用原生格式
   ```

2. **无需修改代码**：
   - `ActorAgent` 自动检测并选择正确的模式
   - 现有的工具定义无需修改
   - 工具执行逻辑保持不变

3. **验证**：
   - 运行测试确保功能正常
   - 观察日志确认使用了原生格式

### 回退到文本 JSON 格式

如果遇到问题，可以随时回退：

```yaml
model:
  large-language-model:
    use_native_tools: false  # 禁用原生格式
```

## 参考资料

- [Anthropic API 文档 - Tool Use](https://docs.anthropic.com/claude/docs/tool-use)
- [Claude Code 实现参考](D:\MyProject\Programming\claude-code\src\services\api\claude.ts)
- 项目内部实现：
  - `Memory/ToolCall.py` - 格式转换
  - `Agent/LLMCore.py` - API 调用
  - `Agent/ActorAgent.py` - 双模式实现
