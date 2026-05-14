# 论文分析智能体系统

## 概述

本系统实现了一个基于多 Agent 协作的论文分析智能体，专门用于分析神经网络领域（LLM、VLM）的学术论文。

## 架构设计

### 系统架构

```
论文分析智能体系统
│
├── 主智能体 (ActorAgent)
│   └── 协调 7 个子智能体按 SOP 流程执行
│
└── 7 个子智能体 (Agent Delegates)
    ├── 1️⃣ PDF Parser Agent          - PDF 解析
    ├── 2️⃣ Content Analysis Agent     - 内容分析
    ├── 3️⃣ Tech Extraction Agent      - 技术提取
    ├── 4️⃣ Fake Data Reproduction Agent - 假数据复现
    ├── 5️⃣ Literature Analysis Agent  - 相关文献分析
    ├── 6️⃣ Relation Analysis Agent    - 关联分析
    └── 7️⃣ Knowledge Integration Agent - 知识库整合
```

### 执行流程

```
阶段 1: PDF 解析 (串行)
   ↓
阶段 2: 内容分析 (串行)
   ↓
阶段 3: 并行执行
   ├── 技术提取
   ├── 假数据复现
   └── 相关文献分析
   ↓
阶段 4: 关联分析 (串行)
   ↓
阶段 5: 知识库整合 (串行)
   ↓
生成最终报告
```

## 文件结构

```
Agent/
├── Delegates/
│   ├── __init__.py
│   └── PaperAnalysisDelegate.py      # 7 个子智能体的实现和提示词
├── prompts/
│   └── paper_analysis_sop.md         # 主智能体的 SOP 提示词
└── PaperAnalysisOrchestrator.py      # 主智能体协调器（可选，用于直接调用）

Tools/
└── builtin/
    ├── core_tools.py                  # 注册 makeAgentDelegate 工具
    └── agent_delegate_tool.py         # makeAgentDelegate 工具实现

MCP/
└── MCP.py                             # 添加 makeAgentDelegate 到 MCP 接口
```

## 核心组件

### 1. PaperAnalysisDelegate

**位置**: `Agent/Delegates/PaperAnalysisDelegate.py`

**功能**: 实现 7 个子智能体的基类和配置

**子智能体类型**:
- `pdf_parser`: PDF 解析
- `content_analysis`: 内容分析
- `tech_extraction`: 技术提取
- `fake_data_reproduction`: 假数据复现
- `literature_analysis`: 相关文献分析
- `relation_analysis`: 关联分析
- `knowledge_integration`: 知识库整合

**每个子智能体包含**:
- 系统提示词（定义任务和输出格式）
- 允许的工具列表（Read, Write, Edit, Grep, run_cmd, getKnowledge）
- ReAct 循环执行逻辑

### 2. makeAgentDelegate 工具

**位置**: `Tools/builtin/agent_delegate_tool.py`

**功能**: 创建并执行子智能体

**参数**:
- `agent_type`: 子智能体类型
- `task`: 任务描述
- `agent_id`: 唯一标识符

**返回**: JSON 格式的执行结果

### 3. 主智能体 SOP 提示词

**位置**: `Agent/prompts/paper_analysis_sop.md`

**功能**: 指导主智能体按照 5 个阶段协调子智能体

**关键内容**:
- 详细的 SOP 流程说明
- 每个阶段的工具调用示例
- 串行 vs 并行执行规则
- 错误处理和进度报告

## 使用方法

### 方式 1: 通过主智能体（推荐）

用户直接向主智能体发送请求：

```
用户: 分析这篇论文 papers/attention_transformer.pdf
```

主智能体会自动：
1. 按照 SOP 流程调用 7 个子智能体
2. 报告每个阶段的进度
3. 生成最终分析报告

### 方式 2: 直接调用 PaperAnalysisOrchestrator（可选）

```python
from Agent.PaperAnalysisOrchestrator import PaperAnalysisOrchestrator
from Tools.registry import registry

orchestrator = PaperAnalysisOrchestrator(
    tools_registry=registry,
    output_callback=print
)

result = orchestrator.analyze_paper("papers/attention_transformer.pdf")
print(result["report"])
```

## 子智能体详细说明

### 1️⃣ PDF Parser Agent

**任务**: 从 PDF 中提取所有原始材料

**输出**:
```json
{
  "metadata": {"title": "...", "authors": [...], "year": 2024},
  "sections": {"abstract": "...", "introduction": "...", ...},
  "figures": [...],
  "tables": [...],
  "equations": [...],
  "references": [...]
}
```

### 2️⃣ Content Analysis Agent

**任务**: 深度分析所有章节

**输出**:
```json
{
  "abstract_analysis": {...},
  "introduction_analysis": {...},
  "method_analysis": {...},
  "experiments_analysis": {...},
  "conclusion_analysis": {...}
}
```

### 3️⃣ Tech Extraction Agent

**任务**: 提取技术模块和创新点

**输出**:
```json
{
  "modules": [
    {
      "name": "Multi-Head Attention",
      "category": "Core Mechanisms",
      "principle": "...",
      "complexity": "O(n²)",
      "use_cases": [...]
    }
  ],
  "innovations": [...]
}
```

### 4️⃣ Fake Data Reproduction Agent

**任务**: 生成假数据并追踪维度变化

**输出**:
```json
{
  "status": "success | skipped",
  "fake_input": {"shape": [1, 128, 512]},
  "dimension_flow": [...],
  "flow_diagram_mermaid": "..."
}
```

### 5️⃣ Literature Analysis Agent

**任务**: 分析引用文献和 Related Work

**输出**:
```json
{
  "related_work_summary": "...",
  "key_citations": [...],
  "citation_categories": {...},
  "missing_papers": [...]
}
```

### 6️⃣ Relation Analysis Agent

**任务**: 构建引用网络、检索相似论文、判断 SOTA

**输出**:
```json
{
  "citation_network": {...},
  "similar_papers": [...],
  "sota_status": {
    "is_sota": true,
    "tasks": [...]
  }
}
```

### 7️⃣ Knowledge Integration Agent

**任务**: 更新知识库索引

**输出**:
```json
{
  "updated_files": [...],
  "summary": {
    "paper_id": "paper_001",
    "new_modules": 2,
    "new_insights": 1,
    "is_sota": true
  }
}
```

## 知识库结构

分析结果会存储到以下位置：

```
runtime_memory/
├── papers/                          # 论文库
│   └── paper_001_attention_transformer/
│       ├── raw/                     # 原始材料
│       │   ├── paper.pdf
│       │   ├── sections/*.txt
│       │   ├── figures/*.png
│       │   └── metadata.json
│       ├── knowledge/               # 知识提炼
│       │   ├── innovation.md
│       │   ├── methodology.md
│       │   ├── experiments.md
│       │   └── limitations.md
│       ├── reproduction/            # 复现信息
│       │   ├── dimension_flow.md
│       │   └── baseline.md
│       ├── references/              # 引用关系
│       │   ├── citations.json
│       │   └── citation_format.txt
│       └── summary.md               # 论文总览
│
├── modules/                         # 前沿模块库
│   ├── MODULE_INDEX.md             # 模块索引（注入上下文）
│   └── details/                    # 详细文档（按需检索）
│
├── insights/                        # 思路库
│   ├── INSIGHT_INDEX.md            # 思路索引（注入上下文）
│   └── details/                    # 详细文档（按需检索）
│
└── context/                         # 上下文注入层
    ├── MEMORY.md                   # 全局记忆索引（注入）
    ├── CURRENT_FOCUS.md            # 当前关注点（注入）
    └── SOTA_SNAPSHOT.md            # SOTA 快照（注入）
```

## 配置

在 `config.yaml` 中添加以下配置：

```yaml
agent_delegate:
  max_iterations: 8              # 子智能体最大迭代次数
  max_history_turns: 3           # 保留的历史轮数
```

## 测试

### 测试单个子智能体

```python
from Agent.Delegates.PaperAnalysisDelegate import PaperAnalysisDelegate
from Tools.registry import registry

delegate = PaperAnalysisDelegate(
    agent_type="pdf_parser",
    task="从 papers/test.pdf 中提取所有原始材料",
    agent_id="test_001",
    tools_registry=registry
)

result = delegate.execute()
print(result)
```

### 测试完整流程

```
用户: 分析这篇论文 papers/attention_transformer.pdf
```

主智能体会自动执行完整的 5 阶段流程。

## 优势

1. **模块化设计**: 7 个子智能体各司其职，易于维护和扩展
2. **并行优化**: 阶段 3 的 3 个子智能体可并行执行，提高效率
3. **标准化流程**: SOP 确保每篇论文都经过相同的分析流程
4. **知识积累**: 分析结果持久化到知识库，支持跨论文检索
5. **可扩展性**: 可以轻松添加新的子智能体（如"代码分析 Agent"）

## 未来扩展

1. **PDF 解析增强**: 集成 Nougat/Mathpix 提高公式识别准确率
2. **向量检索**: 激活 RAG 模块，支持语义搜索历史论文
3. **知识图谱**: 构建论文引用网络的可视化图谱
4. **多论文对比**: 支持同时分析多篇论文并生成对比报告
5. **自动化测试**: 添加单元测试和集成测试

## 注意事项

1. **依赖检查**: 确保所有依赖已安装（PyMuPDF, pdfplumber 等）
2. **路径配置**: 确保 `runtime_memory/` 目录存在
3. **Token 限制**: 长论文可能超过上下文窗口，需要分段处理
4. **错误处理**: 子智能体失败时，主智能体会报告错误并尝试恢复

## 贡献者

- 设计与实现: Claude Code
- 架构指导: Happy Engineering

---

Generated with [Claude Code](https://claude.ai/code)
via [Happy](https://happy.engineering)
