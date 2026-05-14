# 论文目录结构更新说明

## 更新日期
2026-05-08

## 更新内容

### 旧结构（已废弃）
```
runtime_memory/papers/{paper_id}/
├── summary.md
├── raw/
│   └── metadata.json
├── knowledge/
│   ├── innovation.md
│   └── methodology.md
├── reproduction/
│   └── dimension_flow.md
└── references/
    └── citations.json
```

### 新结构（当前版本）
```
runtime_memory/papers/{paper_id}/
├── summary.md                      # 论文总览
├── abstract/
│   └── content.md                  # 摘要内容
├── introduction/
│   └── content.md                  # 引言内容 + 分析
├── relatedwork/
│   └── content.md                  # 相关工作 + 分析
├── methods/
│   └── content.md                  # 方法论 + 核心组件 + 创新点
├── experiments/
│   └── content.md                  # 实验设置 + 结果 + 消融实验
├── conclusion/
│   └── content.md                  # 结论 + 局限性 + 未来工作
├── citations/
│   └── references.json             # 引用文献列表
├── innovations/
│   └── innovations.md              # 核心创新点详细说明
└── repository/
    └── info.md                     # GitHub链接 + 维度流 + 流程图
```

## 设计理念

### 1. 按论文章节组织
- 遵循学术论文的标准结构（Abstract → Introduction → Related Work → Methods → Experiments → Conclusion）
- 每个章节独立存储，便于单独查看和引用
- 保留原文内容 + 智能体分析结果

### 2. 专门目录
- **citations/**: 存储引用文献的结构化数据
- **innovations/**: 提取的核心创新点，便于快速了解论文贡献
- **repository/**: 代码仓库信息、维度流分析、流程图

### 3. 可扩展性
- 未来可以在每个章节目录下添加图片文件（如 `abstract/figure_1.png`）
- 可以添加更多元数据文件（如 `methods/formulas.json`）
- 支持多语言版本（如 `abstract/content_zh.md`）

## 文件内容说明

### abstract/content.md
- 包含论文摘要的原始文本
- 目前从 PDF 解析结果中提取

### introduction/content.md
- **原文内容**: PDF 提取的引言章节
- **分析**: 包括研究问题、研究动机、主要贡献

### relatedwork/content.md
- **原文内容**: PDF 提取的相关工作章节
- **分析总结**: 文献分析智能体的总结

### methods/content.md
- **原文内容**: PDF 提取的方法论章节
- **架构分析**: 整体架构描述
- **核心组件**: 每个组件的公式、作用
- **关键创新点**: 方法论层面的创新

### experiments/content.md
- **原文内容**: PDF 提取的实验章节
- **实验设置**: 数据集、Baselines
- **实验结果**: 结构化的结果数据
- **消融实验**: 各组件的影响分析

### conclusion/content.md
- **原文内容**: PDF 提取的结论章节
- **总结**: 主要成果
- **局限性**: 论文的不足之处
- **未来工作**: 后续研究方向

### citations/references.json
```json
{
  "citations": [
    {
      "citation_id": "bahdanau2014",
      "title": "论文标题",
      "authors": ["作者1", "作者2"],
      "year": 2014,
      "reason": "引用原因",
      "relation": "baseline | related_work | theoretical"
    }
  ],
  "paper_id": "paper_001",
  "title": "论文标题"
}
```

### innovations/innovations.md
- 核心创新点的详细说明
- 每个创新点包括：标题、描述、影响

### repository/info.md
- GitHub 链接
- 维度流分析（假数据追踪）
- Mermaid 流程图

## 代码修改

### 修改文件
- `Agent/PaperAnalysisOrchestrator.py` - `_integrate_knowledge()` 方法

### 主要变更
1. 创建新的目录结构（9个子目录）
2. 从 `pdf_data['sections']` 和 `content_data` 中提取各章节内容
3. 组合原文 + 分析结果，生成结构化的 Markdown 文件
4. 更新 `summary.md` 以反映新的目录结构
5. 更新报告生成逻辑，列出所有章节文件

## 测试结果

### 测试命令
```bash
python test_paper_analysis_direct.py
```

### 测试结果
- ✅ 总耗时: 275.98 秒
- ✅ 完成阶段: 7/7 (100%)
- ✅ 生成文件: 10个主要文件
- ✅ 目录结构: 符合预期

### 生成的文件列表
```
runtime_memory/papers/paper_001/
├── summary.md
├── abstract/content.md
├── introduction/content.md
├── relatedwork/content.md
├── methods/content.md
├── experiments/content.md
├── conclusion/content.md
├── citations/references.json
├── innovations/innovations.md
└── repository/info.md
```

## 下一步优化

### 1. 图片提取
- 从 PDF 中提取图片
- 保存到对应章节目录（如 `methods/figure_1.png`）
- 在 `content.md` 中引用图片

### 2. 公式提取
- 提取 LaTeX 公式
- 保存为独立文件或嵌入 Markdown

### 3. 表格提取
- 提取表格数据
- 保存为 CSV 或 JSON 格式

### 4. 代码克隆
- 当 GitHub 链接存在时，自动克隆代码
- 保存到 `repository/code/` 目录

### 5. 跨论文关联
- 建立论文之间的引用关系图
- 生成知识图谱

## 兼容性说明

### 向后兼容
- 旧的 `insights/` 和 `modules/` 目录仍然保留
- `AddInsight` 和 `AddModule` 工具仍然正常工作
- 知识管理工具链不受影响

### 迁移建议
- 旧论文数据可以保留，不需要重新分析
- 新分析的论文将使用新结构
- 可以编写迁移脚本将旧数据转换为新结构（可选）

## 总结

✅ **成功实现按章节组织的论文目录结构**
✅ **保留原文内容 + 智能体分析结果**
✅ **结构清晰，易于导航和引用**
✅ **可扩展性强，支持未来功能扩展**
✅ **测试通过，生成内容质量良好**
