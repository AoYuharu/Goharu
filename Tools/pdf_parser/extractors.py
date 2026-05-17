"""
提取器基类和具体实现
"""

import re
import sys
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from PIL import Image
    import pytesseract
except ImportError:
    pytesseract = None

from .models import (
    PDFMetadata, PDFSection, PDFFigure, PDFTable,
    PDFEquation, PDFReference
)


class BaseExtractor(ABC):
    """提取器基类"""

    @abstractmethod
    def extract(self, pdf_path: str, **kwargs) -> Any:
        """提取内容"""
        pass


class MetadataExtractor(BaseExtractor):
    """元数据提取器 - 使用 PyMuPDF"""

    def extract(self, pdf_path: str, **kwargs) -> PDFMetadata:
        """提取PDF元数据"""
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is not installed. Run: pip install PyMuPDF")

        doc = fitz.open(pdf_path)
        metadata = doc.metadata or {}

        # 提取基本信息
        title = metadata.get('title', '') or Path(pdf_path).stem
        authors = self._parse_authors(metadata.get('author', ''))
        year = self._extract_year(metadata.get('creationDate', ''))

        # 文件信息
        page_count = len(doc)
        file_size = Path(pdf_path).stat().st_size

        doc.close()

        return PDFMetadata(
            title=title,
            authors=authors,
            year=year,
            page_count=page_count,
            file_size=file_size,
            creation_date=metadata.get('creationDate')
        )

    def _parse_authors(self, author_str: str) -> List[str]:
        """解析作者字符串"""
        if not author_str:
            return []
        # 简单分割，可以根据需要改进
        return [a.strip() for a in re.split(r'[,;]', author_str) if a.strip()]

    def _extract_year(self, date_str: str) -> Optional[int]:
        """从日期字符串提取年份"""
        if not date_str:
            return None
        match = re.search(r'(\d{4})', date_str)
        return int(match.group(1)) if match else None


class TextExtractor(BaseExtractor):
    """文本提取器 - 使用 pdfplumber + PyMuPDF + OCR"""

    def __init__(self, use_ocr: bool = True):
        self.use_ocr = use_ocr

    def extract(self, pdf_path: str, **kwargs) -> str:
        """提取完整文本"""
        text = ""

        # 优先使用 pdfplumber（文本提取更准确）
        if pdfplumber:
            text = self._extract_with_pdfplumber(pdf_path)

        # 如果 pdfplumber 不可用或提取失败，使用 PyMuPDF
        if not text and fitz:
            text = self._extract_with_pymupdf(pdf_path)

        # 如果文本很少且启用了OCR，尝试OCR
        if len(text.strip()) < 100 and self.use_ocr and pytesseract:
            ocr_text = self._extract_with_ocr(pdf_path)
            if len(ocr_text) > len(text):
                text = ocr_text

        return text

    def _extract_with_pdfplumber(self, pdf_path: str) -> str:
        """使用 pdfplumber 提取文本"""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text_parts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                return "\n\n".join(text_parts)
        except Exception as e:
            print(f"pdfplumber extraction failed: {e}", file=sys.stderr)
            return ""

    def _extract_with_pymupdf(self, pdf_path: str) -> str:
        """使用 PyMuPDF 提取文本"""
        try:
            doc = fitz.open(pdf_path)
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"PyMuPDF extraction failed: {e}", file=sys.stderr)
            return ""

    def _extract_with_ocr(self, pdf_path: str) -> str:
        """使用 OCR 提取文本（扫描版PDF）"""
        try:
            doc = fitz.open(pdf_path)
            text_parts = []

            for page_num in range(min(5, len(doc))):  # 只OCR前5页
                page = doc[page_num]
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img, lang='eng')
                text_parts.append(text)

            doc.close()
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"OCR extraction failed: {e}", file=sys.stderr)
            return ""

    def extract_sections(self, pdf_path: str, full_text: str) -> Dict[str, PDFSection]:
        """从完整文本中提取章节"""
        sections = {}

        # 更灵活的章节标题模式 - 允许标题前后有其他内容
        section_patterns = {
            'abstract': r'(?i)\babstract\b',
            'introduction': r'(?i)(?:\d+\.?\s*)?\bintroduction\b',
            'related_work': r'(?i)(?:\d+\.?\s*)?\brelated\s+work\b',
            'method': r'(?i)(?:\d+\.?\s*)?\b(method|methodology|approach)\b',
            'experiments': r'(?i)(?:\d+\.?\s*)?\b(experiment|evaluation|results)\b',
            'conclusion': r'(?i)(?:\d+\.?\s*)?\bconclusion\b',
            'references': r'(?i)\breferences\b',
        }

        # 使用更智能的方法：基于文本块分割
        # 首先找到所有章节标题的位置
        section_positions = []

        for section_name, pattern in section_patterns.items():
            for match in re.finditer(pattern, full_text):
                # 检查匹配是否在行首或前面只有数字/空格
                start_pos = match.start()
                # 向前查找最近的换行符
                line_start = full_text.rfind('\n', 0, start_pos) + 1
                prefix = full_text[line_start:start_pos].strip()

                # 如果前缀只包含数字、点、空格，则认为是章节标题
                if not prefix or re.match(r'^[\d\.\s]+$', prefix):
                    section_positions.append({
                        'name': section_name,
                        'start': start_pos,
                        'match_text': match.group(0)
                    })

        # 按位置排序
        section_positions.sort(key=lambda x: x['start'])

        # 提取每个章节的内容
        for i, section_info in enumerate(section_positions):
            section_name = section_info['name']
            start_pos = section_info['start']

            # 找到章节标题所在行的结束位置
            title_end = full_text.find('\n', start_pos)
            if title_end == -1:
                title_end = len(full_text)

            # 内容从标题下一行开始
            content_start = title_end + 1

            # 内容到下一个章节开始或文档结束
            if i + 1 < len(section_positions):
                content_end = section_positions[i + 1]['start']
            else:
                content_end = len(full_text)

            content = full_text[content_start:content_end].strip()

            # 只保存有实质内容的章节（至少50个字符）
            if len(content) >= 50:
                sections[section_name] = PDFSection(
                    name=section_name,
                    title=section_name.replace('_', ' ').title(),
                    content=content,
                    page_start=1,  # 简化处理，不计算具体页码
                    page_end=1
                )

        return sections


class TableExtractor(BaseExtractor):
    """表格提取器 - 使用 pdfplumber"""

    def extract(self, pdf_path: str, **kwargs) -> List[PDFTable]:
        """提取所有表格"""
        if pdfplumber is None:
            return []

        tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_tables = page.extract_tables()
                    for table_idx, table_data in enumerate(page_tables):
                        if table_data:
                            tables.append(PDFTable(
                                id=f"table_{len(tables) + 1}",
                                caption=f"Table {len(tables) + 1}",
                                page=page_num,
                                data=table_data
                            ))
        except Exception as e:
            print(f"Table extraction failed: {e}", file=sys.stderr)

        return tables


class FigureExtractor(BaseExtractor):
    """图表提取器 - 使用 PyMuPDF"""

    def extract(self, pdf_path: str, output_dir: Optional[str] = None, **kwargs) -> List[PDFFigure]:
        """提取所有图表（过滤小图标和装饰元素）"""
        if fitz is None:
            return []

        figures = []
        doc = fitz.open(pdf_path)

        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

        # 过滤参数
        min_width = kwargs.get('min_width', 100)  # 最小宽度（像素）
        min_height = kwargs.get('min_height', 100)  # 最小高度（像素）
        min_size = kwargs.get('min_size', 10000)  # 最小文件大小（字节）

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                images = page.get_images()

                for img_idx, img in enumerate(images):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    # 获取图片尺寸
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)
                    size = len(image_bytes)

                    # 过滤条件：
                    # 1. 尺寸太小的图片（小图标、装饰元素）
                    # 2. 文件大小太小的图片
                    if width < min_width or height < min_height or size < min_size:
                        continue

                    figure_id = f"figure_{len(figures) + 1}"
                    image_path = None

                    # 保存图片
                    if output_dir:
                        image_ext = base_image["ext"]
                        image_filename = f"{figure_id}.{image_ext}"
                        image_path = str(output_path / image_filename)

                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)

                    figures.append(PDFFigure(
                        id=figure_id,
                        caption=f"Figure {len(figures) + 1}",
                        page=page_num + 1,
                        bbox=[0, 0, width, height],  # 添加尺寸信息
                        image_path=image_path
                    ))
        except Exception as e:
            print(f"Figure extraction failed: {e}", file=sys.stderr)
        finally:
            doc.close()

        return figures


class EquationExtractor(BaseExtractor):
    """公式提取器 - 简单识别"""

    def extract(self, pdf_path: str, full_text: str, **kwargs) -> List[PDFEquation]:
        """提取公式（简单模式）"""
        equations = []

        # 识别常见的公式模式
        # 1. 独立行的数学符号
        # 2. 括号内的公式编号 (1), (2)
        lines = full_text.split('\n')

        for line_num, line in enumerate(lines):
            line_stripped = line.strip()

            # 检测数学符号密集的行
            math_symbols = sum(1 for c in line_stripped if c in '=+-×÷∑∫∂∇αβγθλμσ')
            if math_symbols >= 3 and len(line_stripped) > 5:
                equations.append(PDFEquation(
                    id=f"eq_{len(equations) + 1}",
                    content=line_stripped,
                    page=1  # 简化处理
                ))

        return equations


class ReferenceExtractor(BaseExtractor):
    """引用文献提取器"""

    def extract(self, pdf_path: str, full_text: str, **kwargs) -> List[PDFReference]:
        """提取引用文献"""
        references = []

        # 找到 References 章节
        ref_match = re.search(r'(?i)^references\s*$', full_text, re.MULTILINE)
        if not ref_match:
            return references

        ref_section = full_text[ref_match.end():]

        # 简单分割引用（每个引用通常以 [数字] 或数字. 开头）
        ref_pattern = r'(?:^\[\d+\]|^\d+\.)\s*(.+?)(?=(?:^\[\d+\]|^\d+\.)|$)'
        matches = re.finditer(ref_pattern, ref_section, re.MULTILINE | re.DOTALL)

        for match in matches:
            raw_text = match.group(1).strip()
            if len(raw_text) > 20:  # 过滤太短的
                references.append(PDFReference(
                    id=f"ref_{len(references) + 1}",
                    raw_text=raw_text[:500]  # 限制长度
                ))

        return references
