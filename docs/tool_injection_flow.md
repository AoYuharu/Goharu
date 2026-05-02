# 工具注入上下文的完整流程

## 概述

工具定义通过以下流程注入到模型的上下文中：
1. 工具注册 → 2. 工具定义生成 → 3. 注入到系统提示 → 4. 渲染为消息 → 5. 发送给模型

## 完整流程

### 1. 工具注册阶段

**文件**: `Tools/builtin/file_tools.py`, `Tools/builtin/core_tools.py`

```python
registry.register(
    name="Read",
    description="Read file content by 1-based line range...",
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            ...
        },
        "required": ["path"],
    },
    handler=Read,
    group="file",
)
```

**存储位置**: `Tools/registry.py` 的 `ToolRegistry._entries` 字典

### 2. 工具定义生成

**文件**: `Tools/runtime.py` → `InProcessToolRuntime.list_tools()`

```python
async def list_tools(self):
    self.last_tool_definitions = [
        _normalize_tool_definition(tool)
        for tool in registry.list_definitions(group=self.group)
    ]
    return list(self.last_tool_definitions)
```

**生成格式** (`_normalize_tool_definition`):
```python
{
    "name": "Read",
    "description": "Read file content by 1-based line range...",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            ...
        },
        "required": ["path"]
    }
}
```

**存储位置**: `runtime.last_tool_definitions` 列表

### 3. 注入到系统提示

**文件**: `Agent/ActorAgent.py` → `build_messages()`

```python
def build_messages(self, extra_system_prompt=None):
    tool_definitions = getattr(self.tool_runtime, "last_tool_definitions", None)

    document = self.prompt_assembler.build_actor_document(
        history=self.working.get_context(),
        soul_markdown=...,
        user_profile_markdown=...,
        memory_markdown=...,
        extra_system_prompt=extra_system_prompt,
        tool_definitions=tool_definitions,  # ← 注入工具定义
    )
    return self.prompt_renderer.render_document(document)
```

**文件**: `Prompting/PromptAssembler.py` → `_build_tool_directory_section()`

```python
TOOL_DIRECTORY_PROMPT = "以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。"

def _build_tool_directory_section(self, tool_definitions):
    if not tool_definitions:
        return None
    return PromptSection(
        kind="system",
        title="tool_directory",
        content=f"{TOOL_DIRECTORY_PROMPT}\n\n{self._json_text(tool_definitions)}",
        metadata={"section_name": "tool_directory"},
    )
```

**生成的内容**:
```
以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。

[
  {
    "name": "Edit",
    "description": "Edit file by 1-based line numbers...",
    "inputSchema": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "File path to modify"},
        "operation": {"type": "string", "description": "Operation: insert, delete, or replace"},
        ...
      },
      "required": ["path", "operation", "start_line"]
    }
  },
  {
    "name": "Grep",
    "description": "Search text in files...",
    "inputSchema": {...}
  },
  {
    "name": "Read",
    "description": "Read file content...",
    "inputSchema": {...}
  },
  {
    "name": "getKnowledge",
    "description": "Get knowledge from local files...",
    "inputSchema": {...}
  },
  {
    "name": "run_cmd",
    "description": "Run shell command on Windows...",
    "inputSchema": {...}
  }
]
```

### 4. 渲染为消息

**文件**: `Prompting/PromptRenderer.py` → `render_section()`

```python
def render_section(self, section):
    content = self._stringify_content(section.content).strip()

    if section.kind in {"system", "user", "assistant"}:
        return {
            "role": section.kind,
            "content": content,
        }
```

**渲染结果**:
```python
{
    "role": "system",
    "content": "以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。\n\n[{\"name\": \"Edit\", ...}, ...]"
}
```

### 5. 发送给模型

**文件**: `Agent/LLMCore.py` → `_prepare_anthropic_messages()`

```python
def _prepare_anthropic_messages(self, messages):
    system_parts = []
    remote_messages = []

    for raw_message in messages:
        message = self._normalize_provider_message(raw_message)
        role = message.get("role")
        content = self._stringify_content(message.get("content", "")).strip()

        if role == "system":
            system_parts.append(content)  # ← 收集所有 system 消息
            continue

        if role in {"user", "assistant"}:
            remote_messages.append({"role": role, "content": content})

    return "\n\n".join(system_parts).strip(), remote_messages
```

**最终发送给 API**:
```python
{
    "model": "MiniMax-M2.7",
    "max_tokens": 1024,
    "temperature": 0.7,
    "system": "你是一个本地智能体。\n\n## 运行环境\n...\n\n以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。\n\n[{\"name\": \"Edit\", ...}, ...]",
    "messages": [
        {"role": "user", "content": "读取 config.yaml 的前 10 行"},
        ...
    ]
}
```

## 完整的系统提示结构

发送给模型的 `system` 字段包含以下部分（按顺序）：

1. **Actor 基础提示** (`prompts/actor/base.md`)
   - 运行环境（Windows）
   - 核心原则（禁止编造、工具优先、执行不描述、自己解决问题）
   - 工具调用格式（严格 JSON）
   - 工具使用指南
   - 回答模式

2. **SOUL.md** (如果存在)
   - 角色设定和行为边界

3. **USER.md** (如果存在)
   - 用户画像

4. **MEMORY.md** (如果存在)
   - 长期记忆索引

5. **工具目录** ← 这里注入工具定义
   ```
   以下是当前运行时实际可用的工具目录。只有这些工具可以调用；如需调用工具，参数必须严格匹配对应 schema。

   [工具定义 JSON 数组]
   ```

## 工具定义的 JSON Schema 格式

每个工具定义遵循以下格式：

```json
{
  "name": "工具名称",
  "description": "工具描述，说明功能、参数、使用场景",
  "inputSchema": {
    "type": "object",
    "properties": {
      "参数名": {
        "type": "string|integer|boolean|...",
        "description": "参数描述"
      }
    },
    "required": ["必填参数列表"]
  }
}
```

这个格式类似于 **Anthropic Claude 的 Tool Use API** 格式，但实际上是通过 `system` 消息发送的，而不是使用原生的 `tools` 参数。

## 为什么不使用原生 tools 参数？

### 当前方式（system 消息）
```python
{
    "system": "...\n\n工具目录：\n[{\"name\": \"Read\", ...}]",
    "messages": [...]
}
```

### 原生方式（tools 参数）
```python
{
    "system": "...",
    "tools": [
        {"name": "Read", "description": "...", "input_schema": {...}}
    ],
    "messages": [...]
}
```

### 原因分析

1. **兼容性**
   - 当前使用 `anthropic_compatible` 提供商（MiniMax）
   - MiniMax 可能不完全支持 Anthropic 的原生 `tools` 参数
   - 通过 `system` 消息注入更通用

2. **灵活性**
   - 可以自定义工具目录的提示语
   - 可以控制工具定义的格式
   - 不依赖提供商的 tool use 实现

3. **历史原因**
   - 代码可能最初为本地模型设计
   - 本地模型不支持原生 `tools` 参数
   - 需要通过提示词教会模型如何调用工具

## 模型如何理解工具？

模型通过以下方式理解工具：

1. **系统提示中的工具目录**
   - 看到所有可用工具的名称、描述、参数
   - 理解每个工具的功能和使用方法

2. **工具调用格式要求**
   - 提示词中明确要求输出 JSON 格式
   - 格式：`{"tool": "工具名", "arguments": {...}}`

3. **工具使用指南**
   - 提示词中说明何时使用哪个工具
   - 例如："文件读取：调用 `Read`"

4. **历史对话中的示例**
   - 如果之前调用过工具，模型可以看到调用和返回的示例
   - 学习如何正确使用工具

## 优化建议

### 1. 使用原生 tools 参数（如果提供商支持）

修改 `LLMCore._generate_anthropic_compatible()`:
```python
request_kwargs = {
    "model": ...,
    "max_tokens": ...,
    "messages": remote_messages,
    "tools": [  # ← 使用原生 tools 参数
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["inputSchema"]
        }
        for tool in tool_definitions
    ]
}
if system:
    request_kwargs["system"] = system
```

**优点**：
- 模型可能更好地理解工具
- 提供商可能有特殊优化
- 更符合 API 标准

**缺点**：
- 需要提供商支持
- 可能需要处理不同的响应格式

### 2. 简化工具描述

当前描述较长，可以简化：
```python
description="Read file content by line range. Required: path. Optional: start_line, end_line."
```

### 3. 添加工具使用示例

在工具目录后添加示例：
```
## 工具使用示例

读取文件：
{"tool": "Read", "arguments": {"path": "config.yaml", "end_line": 10}}

搜索文件：
{"tool": "Grep", "arguments": {"pattern": "model", "path": "."}}
```

### 4. 调整工具顺序

将常用工具放在前面：
```python
# 按使用频率排序
priority_order = ["Read", "Edit", "Grep", "run_cmd", "getKnowledge"]
```

## 调试工具注入

### 查看实际注入的内容

使用 `/context` 命令：
```bash
python main.py
You > /context
```

会显示即将发送给 API 的完整上下文结构，包括工具定义。

### 检查工具定义

```python
from Tools.registry import registry
from Tools.builtin import core_tools, file_tools
import json

# 查看所有注册的工具
for entry in registry.list_entries():
    definition = entry.to_definition()
    print(json.dumps(definition, ensure_ascii=False, indent=2))
```

### 验证工具注入

```python
from Agent.ActorAgent import ActorAgent
from Memory.MemoryManager import MemoryManager
from Tools.runtime import create_tool_runtime
import asyncio

async def check_injection():
    runtime = create_tool_runtime('in_process')
    await runtime.initialize()
    await runtime.list_tools()

    memory = MemoryManager()
    actor = ActorAgent(runtime, memory)

    messages = actor.build_messages()

    # 找到工具目录
    for msg in messages:
        if msg.get('role') == 'system' and '工具目录' in msg.get('content', ''):
            print(msg['content'])
            break

asyncio.run(check_injection())
```
