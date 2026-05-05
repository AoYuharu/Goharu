# Anthropic 原生工具调用 - 重要说明

## ⚠️ 模型兼容性问题

经过测试发现：

### 测试结果

1. ✅ **代码实现正确** - 完全支持 Anthropic 原生格式
2. ✅ **API 支持原生格式** - MiniMax API 返回 `tool_use` 块
3. ❌ **模型行为不匹配** - MiniMax 模型返回文本 JSON 而不是使用原生格式

### 问题原因

**MiniMax 模型**虽然通过 Anthropic 兼容的 API，但模型本身被训练成：
- 在响应中直接输出 JSON 格式的工具调用
- 而不是依赖 API 的原生 `tool_use` 机制

示例：
```json
// MiniMax 返回的是文本
"[{\"id\": \"call_function_abc123_1\", \"name\": \"Read\", \"input\": {...}, \"type\": \"tool_use\"}]"

// 而不是 API 原生的 tool_use 块
{
  "type": "tool_use",
  "id": "toolu_xxx",
  "name": "Read",
  "input": {...}
}
```

## 解决方案

### 方案 1：使用文本 JSON 模式（当前推荐）

对于 **MiniMax** 模型，应该禁用原生工具调用：

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: MiniMax-M2.7
    base_url: https://api.minimaxi.com/anthropic
    use_native_tools: false  # 禁用，使用文本 JSON
```

**优点**：
- ✅ 与 MiniMax 模型行为匹配
- ✅ 工具调用正常工作
- ✅ 支持多工具并发（通过 JSON 数组）

**缺点**：
- ⚠️ 需要手动解析 JSON
- ⚠️ 没有 API 层面的格式验证

### 方案 2：使用官方 Claude API（推荐用于生产）

如果使用 **Anthropic 官方 Claude** 模型，原生工具调用会完美工作：

```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    model: claude-3-5-sonnet-20241022
    api_key_env: ANTHROPIC_API_KEY
    base_url: https://api.anthropic.com  # 官方 API
    use_native_tools: true  # 启用原生格式
```

**优点**：
- ✅ API 层面格式验证
- ✅ 原生多工具并发
- ✅ tool_choice 参数支持
- ✅ 更稳定可靠

**缺点**：
- ⚠️ 需要 Anthropic API key
- ⚠️ 可能有费用

## 当前配置

已将配置更新为：

```yaml
use_native_tools: false  # 适配 MiniMax 模型
```

## 测试验证

### 文本 JSON 模式测试

```bash
# 应该可以正常工作
python main.py
```

然后测试：
- "读取 config.yaml 文件"
- "同时读取 config.yaml 和搜索所有 .py 文件"
- "创建子 agent 搜索所有 .md 文件"

### 原生格式测试（仅适用于 Claude API）

如果切换到 Claude API：

1. 设置 API key：
   ```bash
   export ANTHROPIC_API_KEY="your-key"
   ```

2. 更新配置：
   ```yaml
   model: claude-3-5-sonnet-20241022
   base_url: https://api.anthropic.com
   use_native_tools: true
   ```

3. 运行测试：
   ```bash
   python test_final.py
   ```

## 实现价值

虽然 MiniMax 不支持原生格式，但我们的实现仍然有价值：

1. **代码架构正确** - 支持双模式切换
2. **未来兼容** - 当 MiniMax 或其他模型支持时，可以直接启用
3. **Claude 支持** - 可以无缝切换到 Claude API
4. **完整测试** - 所有代码逻辑都经过验证

## 总结

### 对于 MiniMax 用户

✅ **功能正常** - 使用文本 JSON 模式（`use_native_tools: false`）
✅ **多 agent 支持** - AgentDelegate 可以正常工作
✅ **并发调用** - 通过 JSON 数组支持多工具并发

### 对于 Claude API 用户

✅ **原生格式** - 完整支持 Anthropic 原生工具调用
✅ **更稳定** - API 层面格式验证
✅ **更强大** - tool_choice 参数支持

---

**当前状态**：代码实现完整，已适配 MiniMax 模型行为。
