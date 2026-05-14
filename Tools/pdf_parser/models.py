"""
数据模型 - 标准化的PDF解析结果
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from pathlib import Path


@dataclass
class PDFMetadata:
    """PDF元数据"""
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None  # 会议/期刊
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    page_count: int = 0
    file_size: int = 0  # bytes
    creation_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFSection:
    """PDF章节"""
    name: str  # abstract, introduction, method, etc.
    title: str  # 章节标题
    content: str  # 章节内容
    page_start: int
    page_end: int
    level: int = 1  # 标题级别

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFFigure:
    """图表"""
    id: str  # figure_1, figure_2
    caption: str
    page: int
    bbox: List[float] = field(default_factory=list)  # [x0, y0, x1, y1]
    image_path: Optional[str] = None  # 保存的图片路径

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFTable:
    """表格"""
    id: str  # table_1, table_2
    caption: str
    page: int
    data: List[List[str]] = field(default_factory=list)  # 表格数据
    bbox: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFEquation:
    """公式"""
    id: str  # eq_1, eq_2
    content: str  # 公式文本
    page: int
    bbox: List[float] = field(default_factory=list)
    latex: Optional[str] = None  # LaTeX格式（如果可用）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFReference:
    """引用文献"""
    id: str  # ref_1, ref_2
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    raw_text: str = ""  # 原始引用文本

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PDFDocument:
    """完整的PDF文档"""
    file_path: str
    metadata: PDFMetadata
    sections: Dict[str, PDFSection] = field(default_factory=dict)
    figures: List[PDFFigure] = field(default_factory=list)
    tables: List[PDFTable] = field(default_factory=list)
    equations: List[PDFEquation] = field(default_factory=list)
    references: List[PDFReference] = field(default_factory=list)
    full_text: str = ""  # 完整文本

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "file_path": self.file_path,
            "metadata": self.metadata.to_dict(),
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            "figures": [f.to_dict() for f in self.figures],
            "tables": [t.to_dict() for t in self.tables],
            "equations": [e.to_dict() for e in self.equations],
            "references": [r.to_dict() for r in self.references],
            "full_text": self.full_text
        }
