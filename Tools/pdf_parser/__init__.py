"""
PDF Parser Module - 轻量级但功能完善的PDF解析工具

支持：
- PyMuPDF (fitz): 快速提取元数据、图片、结构
- pdfplumber: 准确提取文本和表格
- 公式识别: 简单提取公式区域

注意：仅提取 PDF 文本层，不做 OCR（扫描版 PDF 会得到空文本）。
"""

from .parser import PDFParser, parse_pdf_to_json
from .extractors import (
    MetadataExtractor,
    TextExtractor,
    TableExtractor,
    FigureExtractor,
    EquationExtractor,
    ReferenceExtractor
)
from .models import PDFDocument, PDFSection, PDFMetadata

__all__ = [
    'PDFParser',
    'parse_pdf_to_json',
    'MetadataExtractor',
    'TextExtractor',
    'TableExtractor',
    'FigureExtractor',
    'EquationExtractor',
    'ReferenceExtractor',
    'PDFDocument',
    'PDFSection',
    'PDFMetadata',
]
