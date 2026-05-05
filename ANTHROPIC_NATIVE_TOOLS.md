# Anthropic 原生工具调用 - 快速开始

## 🎉 功能已启用

你的项目现在支持 **Anthropic 原生工具调用格式**！

## 当前状态

✅ **已启用** - 在 `config.yaml` 中配置为：
```yaml
model:
  large-language-model:
    provider: anthropic_compatible
    use_native_tools: true  # 已启用
```

## 验证功能

运行测试验证功能正常：

```bash
# 单元测试
python test_anthropic_native_tools.py

# 集成测试
python test_anthropic_integration.py

# 端到端测试
python test_basic_e2e.py

# 快速验证
python test_final.py
```

## 使用方法

### 正常使用
直接运行主程序，系统会自动使用原生工具调用：

```bash
python main.py
```

然后正常对话即可：
- **简单对话**: "你好"
- **工具调用**: "读取 config.yaml 文件"
- **多工具**: "同时读取 config.yaml 和搜索所有 .py 文件"
- **AgentDelegate**: "创建子 agent 搜索所有 .md 文件"

### 切换模式

如果需要回退到文本 JSON 模式，修改 `config.yaml`：

```yaml
model:
  large-language-model:
    use_native_tools: false  # 禁用原生格式
```

## 优势

使用原生格式的好处：

1. **更稳定** - API 层面格式验证，减少解析错误
2. **更快速** - 支持多工具并发执行
3. **更精确** - 使用 `tool_choice` 参数引导模型
4. **更智能** - 结构化错误处理和自动重试

## 文档

详细文档请查看：

- **实现文档**: `docs/anthropic_native_tools.md`
- **实现总结**: `docs/anthropic_implementation_summary.md`
- **最终报告**: `docs/anthropic_final_report.md`

## 测试结果

所有测试已通过 ✅：

- ✅ 单元测试（5/5）
- ✅ 集成测试（5/5）
- ✅ 端到端测试（2/2）
- ✅ 实际运行测试（2/2）

## 问题排查

如果遇到问题：

1. **检查配置**: 确认 `config.yaml` 中 `use_native_tools: true`
2. **检查 API**: 确认 MiniMax API 兼容 Anthropic 格式
3. **查看日志**: 检查 `logs/` 目录下的日志文件
4. **运行测试**: 运行 `test_final.py` 验证功能

## 支持

如有问题，请查看：
- 测试文件中的示例代码
- 文档目录中的详细说明
- 或联系开发团队

---

**享受更强大的工具调用体验！** 🚀
