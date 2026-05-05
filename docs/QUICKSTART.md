# 快速开始指南

## 实现状态

✅ **Anthropic 原生工具调用已完全实现并通过所有测试！**

- ✅ 双模式兼容（原生格式 + 文本 JSON）
- ✅ 多工具并发调用
- ✅ 混合重试策略
- ✅ 结构化验证
- ✅ 所有单元测试通过（10/10）
- ✅ 所有集成测试通过（5/5）
- ✅ 所有模拟测试通过（5/5）

## 设置 API Key

### Windows (PowerShell)

```powershell
# 临时设置（当前会话）
$env:ANTHROPIC_API_KEY = "your-api-key-here"

# 永久设置（用户级别）
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', 'your-api-key-here', 'User')
```

### Windows (CMD)

```cmd
# 临时设置
set ANTHROPIC_API_KEY=your-api-key-here

# 永久设置
setx ANTHROPIC_API_KEY "your-api-key-here"
```

### Linux/Mac

```bash
# 临时设置
export ANTHROPIC_API_KEY="your-api-key-here"

# 永久设置（添加到 ~/.bashrc 或 ~/.zshrc）
echo 'export ANTHROPIC_API_KEY="your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

## 配置文件

确保 `config.yaml` 中的配置正确：

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: MiniMax-M2.7  # 或其他兼容模型
    api_key_env: ANTHROPIC_API_KEY
    base_url: https://api.minimaxi.com/anthropic  # 或官方 API
    use_native_tools: true  # 启用原生工具调用
```

## 启动程序

```bash
python main.py
```

## 测试对话

### 1. 简单对话（不调用工具）

```
You > 你好，请介绍一下你自己
```

预期：模型直接回答，不调用工具。

### 2. 单工具调用

```
You > 请读取 config.yaml 文件的前 10 行
```

预期：调用 `Read` 工具，显示文件内容。

### 3. 多工具并发调用

```
You > 请同时执行：1) 读取 README.md 的前 5 行，2) 搜索所有 .py 文件
```

预期：同时调用 `Read` 和 `Glob` 工具，并发执行。

### 4. AgentDelegate 调用（关键测试）

```
You > 请使用 AgentDelegate 创建一个子 agent 来探索项目中所有的文档文件
```

预期：调用 `AgentDelegate` 工具，创建子 agent 执行任务。

## 验证原生工具调用

启动程序后，在日志中查找以下信息：

```
使用原生工具: True
```

如果看到这个，说明原生工具调用已启用。

## 切换到文本 JSON 模式

如果需要回退到文本 JSON 模式，修改 `config.yaml`：

```yaml
model:
  large-language-model:
    use_native_tools: false  # 禁用原生工具调用
```

## 运行测试

### 单元测试

```bash
python test_anthropic_native_tools.py
```

### 集成测试

```bash
python test_anthropic_integration.py
```

### 模拟测试（不需要 API key）

```bash
python test_mock_anthropic.py
```

## 故障排查

### 问题 1: `TypeError: LargeLanguageModel.query() got an unexpected keyword argument 'tools'`

**解决方案**：已修复。确保 `Agent/LargeLanguageModel.py` 中的 `query` 方法接受 `**kwargs`。

### 问题 2: API key 未设置

**错误信息**：`Environment variable ANTHROPIC_API_KEY is required`

**解决方案**：按照上面的步骤设置 API key。

### 问题 3: 模型不调用工具

**可能原因**：
1. `use_native_tools` 设置为 `false`
2. 模型不支持工具调用
3. 提示词不够明确

**解决方案**：
1. 检查 `config.yaml` 中的 `use_native_tools` 设置
2. 确认使用的是兼容 Anthropic API 的模型
3. 使用更明确的提示词，如"请使用 Read 工具读取文件"

### 问题 4: 工具调用失败

**查看日志**：检查 `logs/conversation_*.log` 文件，查看详细错误信息。

**常见原因**：
- 工具参数不正确
- 文件路径不存在
- 权限问题

## 性能优化建议

1. **启用原生工具调用**（推荐）
   - 更稳定的格式验证
   - 支持多工具并发
   - 更好的错误处理

2. **使用 tool_choice 引导**
   - 单工具场景下自动启用
   - 减少格式错误

3. **监控重试次数**
   - 如果频繁重试，检查提示词质量
   - 考虑调整模型参数

## 下一步

1. ✅ 设置 API key
2. ✅ 启动程序
3. ✅ 测试简单对话
4. ✅ 测试单工具调用
5. ✅ 测试多工具并发
6. ✅ **测试 AgentDelegate 调用**（关键）

完成以上步骤后，系统即可正常使用！

## 技术支持

- 详细文档：`docs/anthropic_native_tools.md`
- 实现总结：`docs/anthropic_implementation_summary.md`
- 测试文件：
  - `test_anthropic_native_tools.py`
  - `test_anthropic_integration.py`
  - `test_mock_anthropic.py`

## 版本信息

- 实现日期：2026-05-03
- 测试状态：✅ 全部通过
- 兼容性：Anthropic API 兼容模型
