# 工具调用多重防护系统

## 概述

为了提高模型工具调用的鲁棒性，实现了一套五层防护系统，能够自动修复常见的工具调用错误。

## 防护架构

```
模型输出 → ToolCall 解析器 → 防护系统 → 工具执行
                              ↓
                    防线1: 工具名修复
                              ↓
                    防线2: JSON 修复
                              ↓
                    防线3: 类型转换
                              ↓
                    防线4: 参数清理
                              ↓
                    防线5: 失败重试
```

## 各防线详解

### 防线1：工具名修复

**功能**：修复工具名的大小写、分隔符、拼写错误

**修复策略**：
1. 精确匹配（直接通过）
2. 大小写不敏感匹配：`RUN_CMD` → `run_cmd`
3. 分隔符归一化：`run-cmd` / `runCmd` → `run_cmd`
4. 模糊匹配（相似度 ≥ 0.8）：`run_cm` → `run_cmd`

**示例**：
```python
guard.fix_tool_name("RUN_CMD")      # → ("run_cmd", "大小写修复")
guard.fix_tool_name("run-cmd")      # → ("run_cmd", "分隔符修复")
guard.fix_tool_name("getknowledge") # → ("getKnowledge", "大小写修复")
```

### 防线2：参数 JSON 修复

**功能**：修复常见的 JSON 语法错误

**修复策略**：
1. 直接解析（格式正确）
2. 移除尾随逗号：`{"a": 1,}` → `{"a": 1}`
3. 补全未闭合括号：`{"a": {"b": 1}` → `{"a": {"b": 1}}`
4. 补全未闭合引号
5. 提取嵌入的 JSON 对象

**示例**：
```python
guard.fix_json('{"cmd": "ls",}')           # → ({"cmd": "ls"}, "修复尾随逗号")
guard.fix_json('{"cmd": "ls"')             # → ({"cmd": "ls"}, "修复未闭合括号")
guard.fix_json('{"a": {"b": 1}')           # → ({"a": {"b": 1}}, "修复未闭合括号")
```

### 防线3：参数类型强制转换

**功能**：根据工具 schema 自动转换参数类型

**转换规则**：
- `string → integer`：`"42"` → `42`
- `string → boolean`：`"true"` / `"false"` → `True` / `False`
- `string → number`：`"3.14"` → `3.14`
- `number → string`：`123` → `"123"`
- `boolean → integer`：`True` → `1`
- 单值 → 数组：`"item"` → `["item"]`

**示例**：
```python
# 工具 schema 定义 timeout 为 integer
guard.coerce_arguments("run_cmd", {"cmd": "ls", "timeout": "30"})
# → ({"cmd": "ls", "timeout": 30}, ["参数 timeout: string → integer"])
```

### 防线4：请求前参数清理

**功能**：移除无效参数，避免传递脏数据

**清理规则**：
- 移除 `null` 值
- 移除空字符串（可选）
- 递归清理嵌套对象
- 清理数组中的无效元素

**示例**：
```python
guard.sanitize_arguments({"a": 1, "b": None, "c": ""})
# → ({"a": 1}, ["移除 null 参数: b", "移除空字符串参数: c"])
```

### 防线5：无效工具调用重试

**功能**：工具调用失败时自动重试（最多 3 次）

**重试场景**：
1. 防护系统无法修复工具名（工具不存在）
2. 工具执行返回错误（参数验证失败）

**重试机制**：
- 将错误信息反馈给模型
- 要求模型重新生成工具调用
- 最多重试 3 次，失败后返回错误

**实现位置**：`ActorAgent.act(max_retries=3)`

## 使用方式

### 自动集成

防护系统已自动集成到 `ActorAgent` 中，无需手动调用：

```python
# 在 ActorAgent.build_messages() 中自动初始化
if self.guard is None and tool_definitions:
    self.guard = ToolCallGuard(tool_definitions)

# 在 ActorAgent.act() 中自动应用防护
guard_result = self.guard.guard(raw_tool_name, raw_args)
```

### 手动使用

也可以独立使用防护器：

```python
from Tools.guard import ToolCallGuard

# 初始化防护器
tools = [
    {"name": "run_cmd", "inputSchema": {"properties": {"cmd": {"type": "string"}}}}
]
guard = ToolCallGuard(tools)

# 执行完整防护
result = guard.guard("RUN_CMD", '{"cmd": "ls", "timeout": "30"}')

if result["success"]:
    tool_name = result["tool_name"]
    arguments = result["arguments"]
    print(f"修复后: {tool_name}({arguments})")
    print(f"防护日志: {result['logs']}")
else:
    print(f"防护失败: {result['error']}")
```

## 日志输出

防护系统会记录每一层的处理日志，便于调试：

```
[防线1-工具名] 大小写修复: RUN_CMD → run_cmd
[防线2-JSON] 参数已是字典，跳过解析
[防线3-类型转换] 1 个转换: 参数 timeout: string → integer
[防线4-清理] 无需清理
```

在 `config.yaml` 中启用 `ui.verbose: true` 可以在 CLI 中看到防护日志。

## 测试

### 单元测试

```bash
python test_guard.py
```

测试覆盖：
- 工具名修复（大小写、分隔符、模糊匹配）
- JSON 修复（尾随逗号、未闭合括号）
- 类型转换（字符串 ↔ 数字 ↔ 布尔）
- 参数清理（null、空字符串）
- Windows 路径处理

### 集成测试

```bash
python test_integration.py
```

测试场景：
- MiniMax 格式 + 大小写错误 + 类型错误
- 标准 JSON + 类型错误
- 工具名完全错误（预期失败）

## 性能影响

- **防线 1-4**：纯 Python 逻辑，耗时 < 1ms
- **防线 5**：重试会额外调用 LLM，每次重试增加 1-3 秒
- **总体影响**：对正常工具调用几乎无影响，仅在错误时触发重试

## 配置选项

在 `config.yaml` 中可以配置：

```yaml
ui:
  verbose: true  # 显示防护日志
```

## 已知限制

1. **模糊匹配阈值**：相似度 < 0.8 的工具名无法自动修复
2. **JSON 修复能力**：无法修复严重损坏的 JSON（如缺少大量引号）
3. **类型转换**：无法处理复杂的嵌套类型转换
4. **重试次数**：固定 3 次，不可配置（可扩展）

## 未来改进

- [ ] 支持自定义模糊匹配阈值
- [ ] 支持配置重试次数
- [ ] 支持更复杂的 JSON 修复（使用 LLM）
- [ ] 支持工具调用历史学习（记录常见错误模式）
- [ ] 支持参数默认值填充
