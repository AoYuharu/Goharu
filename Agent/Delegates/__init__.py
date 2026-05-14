"""
Agent Delegates - 论文分析子智能体模块

提供 7 个专门的子智能体用于论文分析：
1. PDFParserDelegate - PDF 解析
2. ContentAnalysisDelegate - 内容分析
3. TechExtractionDelegate - 技术提取
4. FakeDataReproductionDelegate - 假数据复现
5. LiteratureAnalysisDelegate - 相关文献分析
6. RelationAnalysisDelegate - 关联分析
7. KnowledgeIntegrationDelegate - 知识库整合
"""

from .PaperAnalysisDelegate import PaperAnalysisDelegate

__all__ = ["PaperAnalysisDelegate"]
