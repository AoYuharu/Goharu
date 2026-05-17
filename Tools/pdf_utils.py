"""
PDF 工具模块 - 与 Claude Code Read 工具对齐的 PDF 处理逻辑

提供：
- PDF 魔法字节校验（防止非 PDF 文件误处理）
- 页面计数（PyMuPDF，替代 pdfinfo）
- 页面提取为 JPEG（PyMuPDF，替代 pdftoppm）
- 页码范围解析（支持 "1-5" / "3" / "3-" 格式）
- 文件扩展名检查

常量阈值与 Claude Code 保持一致。
"""

import base64
import os
from pathlib import Path
from typing import Optional

# =============================================================================
# 常量（与 Claude Code apiLimits.ts 对齐）
# =============================================================================

# 最大原始 PDF 文件大小（20 MB），超过此值不读取为 base64
# 因为 base64 编码后 +33%，20 MB 解码后大约 26.7 MB，在 32 MB API 请求限制内
PDF_TARGET_RAW_SIZE = 20 * 1024 * 1024  # 20 MB

# 文件大小阈值（3 MB），超过此值的大 PDF 应提取为页面图片而非发送 base64
PDF_EXTRACT_SIZE_THRESHOLD = 3 * 1024 * 1024  # 3 MB

# 页面提取路径的最大 PDF 文件大小（100 MB）
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # 100 MB

# 单次读取的最大页数
PDF_MAX_PAGES_PER_READ = 20

# @ mention 内联阈值：页数超过此值时引导 Agent 使用 pages 参数
PDF_AT_MENTION_INLINE_THRESHOLD = 10

# =============================================================================
# 文件扩展名检查
# =============================================================================


def is_pdf_extension(path: str) -> bool:
    """检查文件路径的扩展名是否为 .pdf"""
    ext = os.path.splitext(path)[1]
    return ext.lower() == ".pdf"


# =============================================================================
# 魔法字节校验
# =============================================================================


def validate_pdf_magic_bytes(filepath: str) -> bool:
    """检查文件前 5 字节是否为 %PDF-

    防止 HTML/文本文件被错误重命名为 .pdf 后进入处理流程。

    Args:
        filepath: PDF 文件路径

    Returns:
        True 如果文件以 %PDF- 开头
    """
    try:
        with open(filepath, "rb") as f:
            header = f.read(5)
        return header == b"%PDF-"
    except (IOError, OSError):
        return False


# =============================================================================
# PDF 读取（base64 编码）
# =============================================================================


def read_pdf(filepath: str) -> dict:
    """读取 PDF 文件为 base64 编码字符串

    与 Claude Code readPDF() 对齐：
    - 检查文件非空
    - 检查文件不超过 PDF_TARGET_RAW_SIZE
    - 校验 %PDF- 魔法字节
    - 返回 base64 编码的 PDF 内容

    Args:
        filepath: PDF 文件路径

    Returns:
        dict: {"success": True, "data": {"base64": str, "size": int}}
        或    {"success": False, "error": {"reason": str, "message": str}}
    """
    try:
        filepath_obj = Path(filepath)
        if not filepath_obj.exists():
            return {
                "success": False,
                "error": {"reason": "unavailable", "message": f"File not found: {filepath}"},
            }

        file_size = filepath_obj.stat().st_size
        if file_size == 0:
            return {
                "success": False,
                "error": {"reason": "empty", "message": "PDF file is empty"},
            }

        if file_size > PDF_TARGET_RAW_SIZE:
            return {
                "success": False,
                "error": {
                    "reason": "too_large",
                    "message": f"PDF file size ({file_size} bytes) exceeds maximum ({PDF_TARGET_RAW_SIZE} bytes)",
                },
            }

        if not validate_pdf_magic_bytes(filepath):
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": "File does not appear to be a valid PDF (missing %PDF- header)",
                },
            }

        with open(filepath, "rb") as f:
            pdf_bytes = f.read()

        b64_content = base64.b64encode(pdf_bytes).decode("ascii")
        return {
            "success": True,
            "data": {"base64": b64_content, "size": file_size},
        }
    except Exception as e:
        return {
            "success": False,
            "error": {"reason": "unknown", "message": str(e)},
        }


# =============================================================================
# 页面计数（PyMuPDF）
# =============================================================================


def get_pdf_page_count(filepath: str) -> Optional[int]:
    """使用 PyMuPDF 获取 PDF 页面数

    与 Claude Code getPDFPageCount() 对齐，但使用 PyMuPDF 替代 pdfinfo。

    Args:
        filepath: PDF 文件路径

    Returns:
        页面数，如果无法读取则返回 None
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    try:
        doc = fitz.open(filepath)
        # 处理加密/密码保护的 PDF
        if doc.needs_pass:
            doc.close()
            return None
        page_count = len(doc)
        doc.close()
        return page_count
    except Exception:
        return None


# =============================================================================
# 页码范围解析
# =============================================================================


def parse_pdf_page_range(pages_str: str) -> Optional[dict]:
    """解析页码范围字符串

    与 Claude Code parsePDFPageRange() 对齐，支持格式：
    - "5" → {"first_page": 5, "last_page": 5}
    - "1-5" → {"first_page": 1, "last_page": 5}
    - "3-" → {"first_page": 3, "last_page": None}（到末尾）

    Args:
        pages_str: 页码范围字符串

    Returns:
        解析后的页码范围 dict，无效输入返回 None
    """
    if not pages_str or not pages_str.strip():
        return None

    pages_str = pages_str.strip()

    # 格式 1: "N"（单页）
    if pages_str.isdigit():
        page_num = int(pages_str)
        if page_num < 1:
            return None
        return {"first_page": page_num, "last_page": page_num}

    # 格式 2: "M-N"（页码范围）
    if "-" in pages_str:
        parts = pages_str.split("-", 1)
        first_part = parts[0].strip()
        last_part = parts[1].strip() if len(parts) > 1 else ""

        # "M-" 格式
        if first_part.isdigit() and last_part == "":
            first_page = int(first_part)
            if first_page < 1:
                return None
            return {"first_page": first_page, "last_page": None}

        # "M-N" 格式
        if first_part.isdigit() and last_part.isdigit():
            first_page = int(first_part)
            last_page = int(last_part)
            if first_page < 1 or last_page < first_page:
                return None
            return {"first_page": first_page, "last_page": last_page}

    return None


# =============================================================================
# 页面提取（PyMuPDF 渲染为 JPEG）
# =============================================================================


def extract_pdf_pages(
    filepath: str,
    first_page: int,
    last_page: Optional[int],
    output_dir: str,
    dpi: int = 100,
) -> dict:
    """使用 PyMuPDF 渲染指定页面为 JPEG 图片

    与 Claude Code extractPDFPages() 对齐，但使用 PyMuPDF 替代 pdftoppm。

    Args:
        filepath: PDF 文件路径
        first_page: 起始页码（1-based）
        last_page: 结束页码（1-based，None 表示到文件末尾但不超过 max_pages）
        output_dir: 输出目录
        dpi: 渲染 DPI（默认 100，与 pdftoppm 默认值对齐）

    Returns:
        dict: {"success": True, "data": {"images": [...], "output_dir": str, "page_count": int}}
        或    {"success": False, "error": {"reason": str, "message": str}}
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "success": False,
            "error": {
                "reason": "unavailable",
                "message": "PyMuPDF (fitz) is not installed",
            },
        }

    try:
        # --- 文件校验 ---
        filepath_obj = Path(filepath)
        if not filepath_obj.exists():
            return {
                "success": False,
                "error": {"reason": "unavailable", "message": f"File not found: {filepath}"},
            }

        file_size = filepath_obj.stat().st_size
        if file_size == 0:
            return {
                "success": False,
                "error": {"reason": "empty", "message": "PDF file is empty"},
            }

        if file_size > PDF_MAX_EXTRACT_SIZE:
            return {
                "success": False,
                "error": {
                    "reason": "too_large",
                    "message": (
                        f"PDF file size ({file_size} bytes) exceeds maximum "
                        f"({PDF_MAX_EXTRACT_SIZE} bytes) for page extraction"
                    ),
                },
            }

        if not validate_pdf_magic_bytes(filepath):
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": "File does not appear to be a valid PDF (missing %PDF- header)",
                },
            }

        # --- 打开 PDF ---
        doc = fitz.open(filepath)
        if doc.needs_pass:
            doc.close()
            return {
                "success": False,
                "error": {
                    "reason": "password_protected",
                    "message": "PDF is password-protected and cannot be opened",
                },
            }

        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            return {
                "success": False,
                "error": {"reason": "empty", "message": "PDF has no pages"},
            }

        # --- 确定页码范围 ---
        actual_first = max(1, first_page)
        if last_page is not None:
            actual_last = min(last_page, total_pages)
        else:
            # 开放范围：从 first_page 到末尾，但不超过 MAX_PAGES_PER_READ
            actual_last = min(first_page + PDF_MAX_PAGES_PER_READ - 1, total_pages)

        if actual_first > total_pages:
            doc.close()
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": f"First page ({actual_first}) exceeds total pages ({total_pages})",
                },
            }

        if actual_first > actual_last:
            doc.close()
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": "Invalid page range",
                },
            }

        # --- 创建输出目录 ---
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # --- 渲染页面 ---
        images = []
        for page_num in range(actual_first - 1, actual_last):  # 0-based index
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi)
            page_label = page_num + 1  # 1-based
            image_filename = f"page-{page_label:02d}.jpg"
            image_path = out_path / image_filename
            pix.save(str(image_path))

            images.append(
                {
                    "page": page_label,
                    "path": str(image_path),
                    "width": pix.width,
                    "height": pix.height,
                }
            )

        doc.close()

        return {
            "success": True,
            "data": {
                "images": images,
                "output_dir": str(out_path),
                "page_count": len(images),
                "total_pages": total_pages,
            },
        }

    except Exception as e:
        error_msg = str(e)
        # 分类已知错误类型
        if "password" in error_msg.lower():
            reason = "password_protected"
        elif "corrupt" in error_msg.lower() or "invalid" in error_msg.lower():
            reason = "corrupted"
        else:
            reason = "unknown"
        return {
            "success": False,
            "error": {"reason": reason, "message": error_msg},
        }
