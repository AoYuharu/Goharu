"""
PDF 工具 - 提供 PDF 解析功能

双路径：
- pages 参数 → 逐页提取文本层（纯文本模型无法查看图片，不渲染 JPEG）
- 无 pages → 完整学术解析（元数据、章节、图表、公式、引用）

校验层：魔法字节、文件大小、空文件检查
统一错误分类：empty / too_large / password_protected / corrupted / unavailable / scanned

注意：仅提取 PDF 文本层，不做 OCR。扫描版 PDF 会返回 scanned 错误。

超时策略：
  本工具不自行管理超时。由 runtime 层的 tools.background_timeout
  统一控制：超过阈值后自动后台化（asyncio.shield + track_task）。
"""

import json
from pathlib import Path
from Tools.registry import registry
from Tools.pdf_utils import (
    extract_pdf_page_texts,
    get_pdf_page_count,
    parse_pdf_page_range,
    validate_pdf_magic_bytes,
    PDF_AT_MENTION_INLINE_THRESHOLD,
    PDF_MAX_EXTRACT_SIZE,
    PDF_MAX_PAGES_PER_READ,
)


def _error_response(reason: str, message: str) -> str:
    """构建统一的错误响应 JSON"""
    return json.dumps(
        {"status": "error", "reason": reason, "error": message},
        ensure_ascii=False,
    )


def parse_pdf(
    pdf_path: str,
    pages: str | None = None,
    output_dir: str = "runtime_memory/papers/temp",
) -> str:
    """解析 PDF 文件

    双路径模式：
    - 提供 pages 参数：逐页提取文本层（大 PDF 分页阅读）
    - 不提供 pages：执行完整学术解析（元数据、章节、图表、公式、引用）

    Args:
        pdf_path: PDF 文件路径（相对或绝对路径）
        pages: 页码范围，支持 "5" / "1-5" / "3-" 格式。最多 {PDF_MAX_PAGES_PER_READ} 页
        output_dir: 输出目录，用于保存提取的图表

    Returns:
        JSON 字符串。success 时包含：
          - type: "full"（完整解析）或 "parts"（逐页文本）
          - 完整解析: result_file, metadata, sections, figures_count, tables_count
          - 逐页文本: pages[{page, text}], page_count, total_pages
        error 时包含 status="error", reason, error
    """
    try:
        # ============================================================
        # 校验层（始终执行）
        # ============================================================
        pdf_path_obj = Path(pdf_path).resolve()
        if not pdf_path_obj.exists():
            return _error_response("unavailable", f"PDF file not found: {pdf_path}")

        file_size = pdf_path_obj.stat().st_size

        # 空文件检查
        if file_size == 0:
            return _error_response("empty", "PDF file is empty")

        # 魔法字节校验（防止 .txt 错误重命名为 .pdf）
        if not validate_pdf_magic_bytes(str(pdf_path_obj)):
            return _error_response(
                "corrupted",
                "File does not appear to be a valid PDF (missing %PDF- header)",
            )

        # 最大文件大小检查
        if file_size > PDF_MAX_EXTRACT_SIZE:
            return _error_response(
                "too_large",
                f"PDF file size ({file_size} bytes) exceeds maximum "
                f"({PDF_MAX_EXTRACT_SIZE} bytes)",
            )

        # ============================================================
        # 路径 A: 提供 pages 参数 → 逐页提取文本层
        # ============================================================
        if pages is not None and pages.strip():
            page_range = parse_pdf_page_range(pages)
            if page_range is None:
                return _error_response(
                    "corrupted",
                    f"Invalid page range: '{pages}'. "
                    "Expected format: '5', '1-5', or '3-'",
                )

            first_page = page_range["first_page"]
            last_page = page_range["last_page"]

            # 检查页数限制
            if last_page is not None:
                num_pages = last_page - first_page + 1
            else:
                num_pages = PDF_MAX_PAGES_PER_READ
            if num_pages > PDF_MAX_PAGES_PER_READ:
                return _error_response(
                    "too_large",
                    f"Requested {num_pages} pages, maximum is "
                    f"{PDF_MAX_PAGES_PER_READ} per request",
                )

            result = extract_pdf_page_texts(str(pdf_path_obj), first_page, last_page)
            if not result["success"]:
                return _error_response(
                    result["error"]["reason"],
                    result["error"]["message"],
                )

            data = result["data"]
            if not data["has_text"]:
                return _error_response(
                    "scanned",
                    "These pages contain no text layer — the PDF is likely a "
                    "scanned image document. OCR is not supported by this tool.",
                )
            return json.dumps(
                {
                    "status": "success",
                    "type": "parts",
                    "pages": data["pages"],
                    "page_count": data["page_count"],
                    "total_pages": data["total_pages"],
                },
                ensure_ascii=False,
            )

        # ============================================================
        # 路径 B: 无 pages 参数 → 完整学术解析
        # ============================================================
        page_count = get_pdf_page_count(str(pdf_path_obj))

        # 大 PDF 引导 Agent 使用 pages 参数
        if page_count is not None and page_count > PDF_AT_MENTION_INLINE_THRESHOLD:
            return _error_response(
                "too_large",
                f"PDF has {page_count} pages. "
                f"Large PDFs (>{PDF_AT_MENTION_INLINE_THRESHOLD} pages) "
                "should be read with the 'pages' parameter to specify a "
                "page range. For example, use pages='1-10' to read the "
                "first 10 pages.",
            )

        # 学术解析
        from Tools.pdf_parser import PDFParser

        parser = PDFParser()
        document = parser.parse(str(pdf_path_obj), output_dir=output_dir)
        full_dict = document.to_dict()

        # 扫描版检测：平均每页文本 <20 字符说明没有文本层，明确报错而不是返回空内容
        if len(full_dict.get("full_text", "").strip()) < 20 * max(1, page_count or 1):
            return _error_response(
                "scanned",
                "PDF text layer is empty or nearly empty — this is likely a "
                "scanned image document. OCR is not supported by this tool.",
            )

        # 保存完整结果到文件（按 PDF 文件名区分，避免并发覆盖）
        temp_dir = Path("runtime_memory/temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        result_file = temp_dir / f"pdf_parse_{pdf_path_obj.stem}.json"
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(full_dict, f, ensure_ascii=False, indent=2)

        metadata = full_dict.get("metadata", {})
        sections_summary = {
            name: len(sec.get("content", ""))
            for name, sec in full_dict.get("sections", {}).items()
        }

        return json.dumps(
            {
                "status": "success",
                "type": "full",
                "result_file": str(result_file),
                "metadata": metadata,
                "sections": sections_summary,
                "figures_count": len(full_dict.get("figures", [])),
                "tables_count": len(full_dict.get("tables", [])),
                "full_text_length": len(full_dict.get("full_text", "")),
                "page_count": page_count,
                "file_size": file_size,
            },
            ensure_ascii=False,
        )

    except FileNotFoundError:
        return _error_response("unavailable", f"PDF file not found: {pdf_path}")
    except Exception as e:
        error_msg = str(e)
        if "password" in error_msg.lower():
            reason = "password_protected"
        elif "corrupt" in error_msg.lower() or "invalid" in error_msg.lower():
            reason = "corrupted"
        else:
            reason = "unknown"
        return _error_response(reason, f"Failed to parse PDF: {error_msg}")


# =============================================================================
# 工具注册
# =============================================================================
registry.register(
    name="parse_pdf",
    description=(
        "Parse an academic PDF and extract structured content (title, authors, "
        "sections, figures, tables, formulas, references). For large PDFs "
        f"(more than {PDF_AT_MENTION_INLINE_THRESHOLD} pages), use the 'pages' "
        f"parameter to read specific page ranges as text. Page ranges support "
        f"formats: '5' (single page), '1-5' (range), or '3-' (from page 3 to "
        f"end). Maximum {PDF_MAX_PAGES_PER_READ} pages per request. "
        "Only the PDF text layer is read — scanned image PDFs (no OCR) will "
        "return a 'scanned' error."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": (
                    "PDF file path, relative or absolute (e.g. 'essay/paper.pdf')."
                ),
            },
            "pages": {
                "type": "string",
                "description": (
                    f"Page range for PDF files (e.g. '1-5', '3', '10-20'). "
                    f"Only applicable to PDF files. Maximum {PDF_MAX_PAGES_PER_READ} "
                    "pages per request."
                ),
            },
            "output_dir": {
                "type": "string",
                "description": (
                    "Directory to save extracted images. "
                    "Default: runtime_memory/papers/temp."
                ),
            },
        },
        "required": ["pdf_path"],
    },
    handler=parse_pdf,
    group="pdf",
)
