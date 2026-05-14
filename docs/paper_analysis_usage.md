# 论文分析系统使用指南

## 系统概述

本系统实现了一个完整的论文分析智能体，采用**主智能体 + 7个子智能体**的协作架构，能够自动解析PDF论文并进行深度分析。

## 架构设计

### 核心特点

✅ **低耦合设计**
- PDF解析器使用策略模式，各提取器独立可替换
- 子智能体通过工具接口调用，主智能体无需了解实现细节
- 数据模型标准化，便于序列化和传输

✅ **多层解析策略**
- PyMuPDF: 快速提取元数据和图片
- pdfplumber: 准确提取文本和表格
- Tesseract OCR: 处理扫描版PDF（可选）

✅ **完整的工作流程**
- 5个阶段：PDF解析 → 内容分析 → 并行提取 → 关联分析 → 知识库整合
- 支持串行和并行执行
- 自动进度报告和错误处理

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：
- `PyMuPDF>=1.23.0` - PDF元数据和图片提取
- `pdfplumber>=0.10.0` - 文本和表格提取
- `Pillow>=10.0.0` - 图像处理
- `pytesseract>=0.3.10` - OCR支持

### 2. 测试系统

```bash
# 测试集成
python test_paper_analysis_integration.py

# 测试PDF解析
python test_pdf_parser.py
```

### 3. 使用方式

#### 方式1: 通过主智能体（推荐）

在 `main.py` 中直接向主智能体发送请求：

```
用户: 分析这篇论文 papers/attention_transformer.pdf
```

主智能体会自动：
1. 按照SOP流程调用7个子智能体
2. 报告每个阶段的进度
3. 生成最终分析报告

#### 方式2: 直接使用PDF解析器

```python
from Tools.pdf_parser import parse_pdf, parse_pdf_to_json

# 解析PDF
document = parse_pdf(
    "papers/sample.pdf",
    output_dir="runtime_memory/papers/temp",
    use_ocr=False  # 是否使用OCR
)

# 访问解析结果
print(f"标题: {document.metadata.title}")
print(f"作者: {', '.join(document.metadata.authors)}")
print(f"章节: {', '.join(document.get_section_names())}")

# 或直接输出JSON
json_str = parse_pdf_to_json(
    "papers/sample.pdf",
    save_to="output.json"
)
```

#### 方式3: 直接调用协调器

```python
from Agent.PaperAnalysisOrchestrator import PaperAnalysisOrchestrator
from Tools.registry import registry

orchestrator = PaperAnalysisOrchestrator(
    tools_registry=registry,
    output_callback=print
)

result = orchestrator.analyze_paper("papers/sample.pdf")
print(result["report"])
```

## 系统组件

### PDF解析器 (`Tools/pdf_parser/`)

**核心类**:
- `PDFParser`: 主解析器，协调各个提取器
- `MetadataExtractor`: 提取元数据
- `TextExtractor`: 提取文本（支持OCR）
- `TableExtractor`: 提取表格
- `FigureExtractor`: 提取图片
- `EquationExtractor`: 提取公式
- `ReferenceExtractor`: 提取引用文献

**数据模型**:
- `PDFDocument`: 完整文档
- `PDFMetadata`: 元数据
- `PDFSection`: 章节
- `PDFFigure`, `PDFTable`, `PDFEquation`, `PDFReference`: 各类内容

### 子智能体 (`Agent/Delegates/`)

**7个专门的子智能体**:

1. **PDF Parser Agent** - PDF解析
   - 工具: `Read`, `run_cmd`, `Write`
   - 任务: 调用PDF解析脚本提取所有原始材料

2. **Content Analysis Agent** - 内容分析
   - 工具: `Read`
   - 任务: 深度分析摘要、引言、方法、实验、结论

3. **Tech Extraction Agent** - 技术提取
   - 工具: `Read`
   - 任务: 提取可复用的技术模块和创新点

4. **Fake Data Reproduction Agent** - 假数据复现
   - 工具: `Read`, `run_cmd`
   - 任务: 生成假数据并追踪维度变化

5. **Literature Analysis Agent** - 相关文献分析
   - 工具: `Read`, `Grep`
   - 任务: 分析引用文献和Related Work

6. **Relation Analysis Agent** - 关联分析
   - 工具: `Read`, `getKnowledge`
   - 任务: 构建引用网络、检索相似论文、判断SOTA

7. **Knowledge Integration Agent** - 知识库整合
   - 工具: `Read`, `Write`, `Edit`
   - 任务: 更新所有索引文件和知识库

### 主协调器 (`Agent/PaperAnalysisOrchestrator.py`)

负责按照SOP流程协调7个子智能体：
- 阶段1、2、4、5：串行执行
- 阶段3：并行执行（技术提取、假数据复现、相关文献）

### 工具接口 (`Tools/builtin/agent_delegate_tool.py`)

**makeAgentDelegate** 工具：
```json
{
  "tool": "makeAgentDelegate",
  "args": {
    "agent_type": "pdf_parser",
    "task": "从 papers/test.pdf 中提取所有原始材料",
    "agent_id": "pdf_parser_001"
  }
}
```

## 配置

在 `config.yaml` 中：

```yaml
agent_delegate:
  max_iterations: 20  # 子智能体最大迭代次数
  max_history_turns: 3  # 保留的历史轮数
```

## 输出结构

分析结果存储在 `runtime_memory/papers/{paper_id}/`:

```
runtime_memory/papers/paper_001_attention_transformer/
├── raw/                     # 原始材料
│   ├── paper.pdf
│   ├── sections/*.txt
│   ├── figures/*.png
│   └── metadata.json
├── knowledge/               # 知识提炼
│   ├── innovation.md
│   ├── methodology.md
│   ├── experiments.md
│   └── limitations.md
├── reproduction/            # 复现信息
│   ├── dimension_flow.md
│   └── baseline.md
├── references/              # 引用关系
│   ├── citations.json
│   └── citation_format.txt
└── summary.md               # 论文总览
```

## 示例输出

```
🎉 论文分析完成！

## 论文信息
- 标题: Attention Is All You Need
- 作者: Vaswani et al.
- 年份: 2017

## 分析结果
- 提取模块: 3 个
- 识别创新点: 2 个
- 相似论文: 5 篇
- SOTA 状态: 是

## 生成文件
- 论文总览: papers/paper_001/summary.md
- 详细分析: papers/paper_001/knowledge/
- 复现指南: papers/paper_001/reproduction/

查看论文总览: Read papers/paper_001/summary.md
```

## 扩展性

### 添加新的提取器

```python
from Tools.pdf_parser.extractors import BaseExtractor

class CustomExtractor(BaseExtractor):
    def extract(self, pdf_path: str, **kwargs):
        # 实现提取逻辑
        return result
```

### 添加新的子智能体

在 `PaperAnalysisDelegateConfig` 中添加：

```python
NEW_AGENT_PROMPT = """你的提示词..."""

@classmethod
def get_system_prompt(cls, agent_type: str):
    prompts = {
        # ... 现有的
        "new_agent": cls.NEW_AGENT_PROMPT,
    }
    return prompts[agent_type.lower()]
```

## 故障排除

### PDF解析失败
- 检查PDF文件是否损坏
- 尝试启用OCR: `use_ocr=True`
- 检查依赖是否正确安装

### 子智能体超时
- 增加 `agent_delegate.max_iterations`
- 检查任务描述是否清晰
- 查看日志了解具体错误

### 工具调用失败
- 确认工具已注册: `registry.get_entry("tool_name")`
- 检查工具参数是否正确
- 查看 `allowed_tools` 列表

## 性能优化

- **并行执行**: 阶段3的3个子智能体自动并行
- **提示词缓存**: 系统提示词和工具schema自动缓存
- **上下文管理**: 自动保留最近N轮历史，避免上下文过长

## 测试覆盖

✅ PDF解析器单元测试
✅ 数据模型测试
✅ 提取器测试
✅ 子智能体创建测试
✅ 工具注册测试
✅ 配置加载测试
✅ 集成测试

## 贡献

欢迎贡献代码！请确保：
1. 遵循现有的代码风格
2. 添加适当的测试
3. 更新文档

---

**设计理念**: 模块化、低耦合、可扩展、易测试

**技术栈**: Python 3.8+, PyMuPDF, pdfplumber, Anthropic API

**许可**: 与主项目相同
