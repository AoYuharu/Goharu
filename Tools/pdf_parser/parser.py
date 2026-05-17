"""
PDF Parser - 主解析器类

低耦合设计：
- 使用策略模式：不同的提取器可独立替换
- 依赖注入：提取器通过构造函数注入
- 单一职责：每个提取器只负责一种内容
"""

import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from .models import PDFDocument
from .extractors import (
    MetadataExtractor,
    TextExtractor,
    TableExtractor,
    FigureExtractor,
    EquationExtractor,
    ReferenceExtractor
)


class PDFParser:
    """
    PDF解析器主类

    使用组合模式，将不同的提取任务委托给专门的提取器
    """

    def __init__(
        self,
        use_ocr: bool = True,
        extract_figures: bool = True,
        extract_tables: bool = True,
        extract_equations: bool = True,
        extract_references: bool = True
    ):
        """
        初始化PDF解析器

        Args:
            use_ocr: 是否使用OCR处理扫描版PDF
            extract_figures: 是否提取图表
            extract_tables: 是否提取表格
            extract_equations: 是否提取公式
            extract_references: 是否提取引用文献
        """
        self.use_ocr = use_ocr
        self.extract_figures = extract_figures
        self.extract_tables = extract_tables
        self.extract_equations = extract_equations
        self.extract_references = extract_references

        # 初始化提取器（依赖注入）
        self.metadata_extractor = MetadataExtractor()
        self.text_extractor = TextExtractor(use_ocr=use_ocr)
        self.table_extractor = TableExtractor() if extract_tables else None
        self.figure_extractor = FigureExtractor() if extract_figures else None
        self.equation_extractor = EquationExtractor() if extract_equations else None
        self.reference_extractor = ReferenceExtractor() if extract_references else None

    def parse(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None
    ) -> PDFDocument:
        """
        解析PDF文件

        Args:
            pdf_path: PDF文件路径
            output_dir: 输出目录（用于保存图片等）

        Returns:
            PDFDocument对象
        """
        pdf_path = str(Path(pdf_path).resolve())

        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        # 1. 提取元数据
        print(f"[1/6] Extracting metadata...", file=sys.stderr)
        metadata = self.metadata_extractor.extract(pdf_path)

        # 2. 提取文本
        print(f"[2/6] Extracting text...", file=sys.stderr)
        full_text = self.text_extractor.extract(pdf_path)

        # 3. 提取章节
        print(f"[3/6] Extracting sections...", file=sys.stderr)
        sections = self.text_extractor.extract_sections(pdf_path, full_text)

        # 4. 提取表格
        print(f"[4/6] Extracting tables...", file=sys.stderr)
        tables = []
        if self.table_extractor:
            tables = self.table_extractor.extract(pdf_path)

        # 5. 提取图表
        print(f"[5/6] Extracting figures...", file=sys.stderr)
        figures = []
        if self.figure_extractor:
            figures = self.figure_extractor.extract(pdf_path, output_dir=output_dir)

        # 6. 提取公式
        print(f"[6/6] Extracting equations and references...", file=sys.stderr)
        equations = []
        if self.equation_extractor:
            equations = self.equation_extractor.extract(pdf_path, full_text=full_text)

        # 7. 提取引用文献
        references = []
        if self.reference_extractor:
            references = self.reference_extractor.extract(pdf_path, full_text=full_text)

        # 构建文档对象
        document = PDFDocument(
            file_path=pdf_path,
            metadata=metadata,
            sections=sections,
            figures=figures,
            tables=tables,
            equations=equations,
            references=references,
            full_text=full_text
        )

        return document

    def parse_to_json(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None,
        save_to: Optional[str] = None
    ) -> str:
        """
        解析PDF并返回JSON格式

        Args:
            pdf_path: PDF文件路径
            output_dir: 输出目录
            save_to: 保存JSON文件的路径（可选）

        Returns:
            JSON字符串
        """
        document = self.parse(pdf_path, output_dir)
        json_data = document.to_dict()
        json_str = json.dumps(json_data, ensure_ascii=False, indent=2)

        if save_to:
            Path(save_to).write_text(json_str, encoding='utf-8')
            print(f"\nJSON saved to: {save_to}", file=sys.stderr)

        return json_str


def parse_pdf_to_json(
    pdf_path: str,
    output_dir: Optional[str] = None,
    save_to: Optional[str] = None,
    use_ocr: bool = False,
    **kwargs
) -> str:
    """
    便捷函数：解析PDF并返回JSON

    Args:
        pdf_path: PDF文件路径
        output_dir: 输出目录
        save_to: 保存JSON的路径
        use_ocr: 是否使用OCR
        **kwargs: 其他参数

    Returns:
        JSON字符串
    """
    parser = PDFParser(use_ocr=use_ocr, **kwargs)
    return parser.parse_to_json(pdf_path, output_dir, save_to)
