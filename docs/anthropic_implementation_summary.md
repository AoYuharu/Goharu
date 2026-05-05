# Anthropic 原生工具调用实现总结

## 实现完成 ✓

已成功为项目添加 **Anthropic 原生工具调用格式**支持，同时保持对传统文本 JSON 格式的完全向后兼容。

## 核心改动

### 1. ToolCall 类扩展 (`Memory/ToolCall.py`)

新增三个方法支持 Anthropic 原生格式：

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

### 2. LLMCore 增强 (`Agent/LLMCore.py`)

#### 支持原生工具调用参数

- 添加 `tools` 参数支持（工具定义列表）
- 添加 `tool_choice` 参数支持（强制工具调用）
- 当使用工具时返回完整响应对象（包含 tool_use 块）

#### 消息格式处理增强

- 支持 `content` 为块数组（`[{"type": "text", ...}, {"type": "tool_use", ...}]`）
- 支持 `content` 为字符串（向后兼容）
- 自动过滤和处理 `tool_use` 和 `tool_result` 块

### 3. ActorAgent 双模式实现 (`Agent/ActorAgent.py`)

#### 新增配置检测

```python
def __init__(self, tool_runtime, working):
    # 检测是否使用 Anthropic 原生工具调用
    llm_config = config.get("model.large-language-model", {}) or {}
    self.provider = llm_config.get("provider", "local_hf")
    self.use_native_tools = llm_config.get("use_native_tools", True)
```

#### 模式自动选择

```python
async def act(self, max_retries=3, on_tool_call_start=None):
    use_native = self._should_use_native_tools()

    if use_native:
        return await self._act_with_native_tools(max_retries, on_tool_call_start)
    else:
        return await self._act_with_text_tools(max_retries, on_tool_call_start)
```

#### 原生模式实现 (`_act_with_native_tools`)

1. **工具定义转换**：MCP 格式 → Anthropic 格式
2. **混合重试策略**：
   - 首次尝试使用 `tool_choice` 引导（单工具场景）
   - 失败后使用错误反馈重试（最多 3 次）
3. **多工具并发**：使用 `asyncio.gather` 并发执行
4. **结构化错误处理**：使用 `tool_result` 的 `is_error` 标记

### 4. 配置文件更新 (`config.yaml`)

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    # 是否使用 Anthropic 原生工具调用格式
    use_native_tools: true  # 默认启用
```

## 功能特性

### ✓ 双模式兼容

| 条件 | 使用格式 |
|------|---------|
| `provider: anthropic_compatible` + `use_native_tools: true` | Anthropic 原生格式 |
| `provider: anthropic_compatible` + `use_native_tools: false` | 文本 JSON 格式 |
| `provider: local_hf` | 文本 JSON 格式 |

### ✓ 多工具并发调用

一次响应可包含多个 `tool_use` 块，系统自动并发执行：

```json
{
  "role": "assistant",
  "content": [
    {"type": "tool_use", "id": "toolu_01", "name": "Read", "input": {...}},
    {"type": "tool_use", "id": "toolu_02", "name": "Grep", "input": {...}}
  ]
}
```

### ✓ 混合重试策略

1. **首次尝试**（max_retries=3）：使用 `tool_choice` 强制调用（单工具场景）
2. **重试阶段**（max_retries=2,1）：将错误作为 `tool_result` 反馈
3. **最终失败**（max_retries=0）：返回错误信息

### ✓ 结构化验证

- API 层面进行 JSON 格式验证
- 使用 `is_error` 标记区分成功/失败
- 自动过滤无效块

### ✓ 向后兼容

- 保留所有文本 JSON 格式解析逻辑
- 旧代码无需修改即可运行
- 可随时切换回文本模式

## 测试验证

### 单元测试 (`test_anthropic_native_tools.py`)

✓ 测试 1: ToolCall 与 Anthropic 格式转换
✓ 测试 2: 文本 JSON 格式兼容性
✓ 测试 3: Anthropic 原生格式解析
✓ 测试 4: 工具定义格式转换
✓ 测试 5: 错误处理

### 集成测试 (`test_anthropic_integration.py`)

✓ 测试 1: 模式自动选择
✓ 测试 2: 工具定义转换
✓ 测试 3: 消息格式处理
✓ 测试 4: 向后兼容性
✓ 测试 5: 错误场景处理

**所有测试 100% 通过！**

## 使用方法

### 启用原生工具调用

在 `config.yaml` 中：

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    use_native_tools: true  # 启用
```

### 禁用原生工具调用（回退）

```yaml
model:
  large-language-model:
    use_native_tools: false  # 禁用，使用文本 JSON
```

### 无需修改代码

- `ActorAgent` 自动检测配置并选择正确模式
- 工具定义无需修改
- 工具执行逻辑保持不变

## 优势对比

| 特性 | 文本 JSON 格式 | Anthropic 原生格式 |
|------|---------------|-------------------|
| **格式验证** | 手动解析，易出错 | API 层面验证 ✓ |
| **多工具调用** | 需解析 JSON 数组 | 原生支持 ✓ |
| **错误处理** | 文本错误消息 | 结构化 `is_error` ✓ |
| **工具引导** | 不支持 | 支持 `tool_choice` ✓ |
| **稳定性** | 依赖模型输出质量 | API 保证格式 ✓ |
| **兼容性** | 所有 provider ✓ | 仅 `anthropic_compatible` |

## 文件清单

### 核心实现

- `Memory/ToolCall.py` - 格式转换方法
- `Agent/LLMCore.py` - API 调用增强
- `Agent/ActorAgent.py` - 双模式实现
- `config.yaml` - 配置选项

### 测试文件

- `test_anthropic_native_tools.py` - 单元测试
- `test_anthropic_integration.py` - 集成测试

### 文档

- `docs/anthropic_native_tools.md` - 详细实现文档
- `docs/anthropic_implementation_summary.md` - 本文档

## 参考资料

- [Anthropic API 文档 - Tool Use](https://docs.anthropic.com/claude/docs/tool-use)
- [Claude Code 实现参考](D:\MyProject\Programming\claude-code\src\services\api\claude.ts)

## 下一步建议

1. **实际测试**：使用真实的 Anthropic API 进行端到端测试
2. **性能监控**：对比两种模式的性能差异
3. **日志增强**：添加详细的工具调用日志
4. **错误分析**：收集和分析工具调用失败的原因

## 总结

✓ 实现完整的 Anthropic 原生工具调用支持
✓ 保持 100% 向后兼容
✓ 支持多工具并发调用
✓ 实现混合重试策略
✓ 通过所有单元测试和集成测试
✓ 提供完整的文档和示例

**系统已准备好使用 Anthropic 原生工具调用！**
