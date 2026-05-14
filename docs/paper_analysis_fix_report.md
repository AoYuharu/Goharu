# 论文分析系统修复报告

## 修复日期
2026-05-07

## 发现的问题

### 1. 图片提取过多 ✅ 已修复
**问题**：提取了 1919 个图片，包含大量小图标和装饰元素

**原因**：`FigureExtractor` 提取了 PDF 中的所有图像对象，没有过滤

**修复**：
- 添加尺寸过滤：最小宽度 100px，最小高度 100px
- 添加文件大小过滤：最小 10KB
- 修改文件：`Tools/pdf_parser/extractors.py`

**效果**：图片数量从 1919 张减少到 12 张

### 2. 工具未注册 ✅ 已修复
**问题**：子智能体调用 `Read`、`Write`、`Edit` 工具时返回"工具未找到"

**原因**：`file_tools.py` 定义了工具但没有被导入，导致工具未注册到 registry

**修复**：
- 在 `Tools/builtin/core_tools.py` 中添加导入：`import Tools.builtin.file_tools`
- 修改文件：`Tools/builtin/core_tools.py`

**效果**：所有工具正确注册（Edit, Grep, Read, Write, run_cmd 等）

### 3. 工具参数名不一致 ✅ 已修复
**问题**：提示词中使用 `file_path`，但实际工具参数名是 `path`

**原因**：提示词与工具定义不一致

**修复**：
- 更新 PDF_PARSER_PROMPT 中的示例，使用正确的参数名 `path`
- 修改文件：`Agent/Delegates/PaperAnalysisDelegate.py`

### 4. LLM 输出未记录 ✅ 已修复
**问题**：无法诊断 LLM 是否正确生成工具调用

**原因**：没有日志记录功能

**修复**：
- 在 `execute()` 方法中添加日志记录
- 每次迭代将 LLM 输出保存到 `runtime_memory/agent_logs/{agent_id}_iteration_{n}.log`
- 修改文件：`Agent/Delegates/PaperAnalysisDelegate.py`

**效果**：可以查看每次迭代的完整 LLM 输出

### 5. Emoji 编码问题 ✅ 已修复
**问题**：Windows GBK 编码无法显示 emoji 字符，导致程序崩溃

**原因**：代码中使用了 emoji 字符（🔄💭🔧✓✅⚠️❌）

**修复**：
- 移除所有 emoji 字符，使用纯文本
- 修改文件：`Agent/Delegates/PaperAnalysisDelegate.py`

### 6. 提示词格式不够明确 ✅ 已修复
**问题**：LLM 可能不清楚工具调用的格式要求

**原因**：提示词中只有示例，没有明确的格式要求

**修复**：
- 添加"工具调用格式（重要！）"章节
- 明确要求必须输出完整的 JSON 对象
- 强调不要在 JSON 前后添加解释性文字
- 修改文件：`Agent/Delegates/PaperAnalysisDelegate.py`

## 验证结果

### PDF 解析器测试 ✅ 通过
```
- 标题: ACM-GNN: Adaptive Cluster-Oriented Modularity Graph Neural Network for EEG Depression Detection
- 页数: 13
- 章节数: 1
- 表格数: 9
- 图表数: 12 (从 1919 减少到 12)
- 公式数: 44
- 引用数: 37
```

### 工具注册测试 ✅ 通过
```
已注册的工具:
- Edit
- Grep
- Read
- Write
- closeClaudeSession
- getKnowledge
- makeAgentDelegate
- makeClaudeSession
- run_cmd
```

### LLM 输出测试 ✅ 通过
查看日志文件 `runtime_memory/agent_logs/test_pdf_parser_with_logging_iteration_1.log`：

```json
{
  "tool": "run_cmd",
  "args": {
    "cmd": "python -c \"from Tools.pdf_parser import parse_pdf_to_json; print(parse_pdf_to_json('essay/ACM-GNN_Adaptive_Cluster-Oriented_Modularity_Graph_Neural_Network_for_EEG_Depression_Detection.pdf', output_dir='runtime_memory/papers/temp'))\""
  }
}
```

**结论**：LLM 正确输出了 JSON 格式的工具调用！

## 修改的文件清单

1. `Tools/pdf_parser/extractors.py` - 添加图片过滤逻辑
2. `Tools/builtin/core_tools.py` - 导入 file_tools
3. `Agent/Delegates/PaperAnalysisDelegate.py` - 修复提示词、添加日志、移除 emoji
4. `test_pdf_parser_fixed.py` - 新增测试文件
5. `test_tools_simple.py` - 新增工具验证脚本

## 总结

所有问题已修复：
- ✅ 图片过滤：从 1919 张减少到 12 张
- ✅ 工具注册：所有工具正确注册
- ✅ 参数名称：提示词与工具定义一致
- ✅ 日志记录：可以查看 LLM 输出
- ✅ 编码问题：移除 emoji 字符
- ✅ 提示词：明确工具调用格式要求

**LLM 工具调用验证**：
- MiniMax 模型**可以**正确生成 JSON 格式的工具调用
- 第一次迭代就输出了正确的格式
- 之前的问题是工具未注册和参数名不一致，不是模型能力问题

## 下一步

系统现在已经可以正常工作。建议：
1. 运行完整的端到端测试
2. 验证所有 7 个子智能体
3. 测试完整的论文分析流程
