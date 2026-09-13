"""
Office 文档解析工具 - Word / Excel / PowerPoint（含 WPS 老格式）

- .docx: python-docx → 段落（含标题层级）+ 表格
- .xlsx: openpyxl → 各 sheet 的单元格文本（管道分隔行）
- .pptx: python-pptx → 每页幻灯片文本框 + 备注
- .doc/.ppt/.xls: win32com 调本机 WPS/Office 转换为新格式后解析

与 parse_pdf 同一错误分类风格：unavailable / empty / too_large / unsupported / converted_failed
仅提取文本内容，不读取文档内嵌图片。
"""

import json
from pathlib import Path
from typing import Optional

from Tools.registry import registry

DOC_MAX_SIZE = 100 * 1024 * 1024  # 100 MB
XLSX_MAX_ROWS_PER_SHEET = 500

_EXT_TYPES = {
    ".docx": "word", ".doc": "word",
    ".xlsx": "spreadsheet", ".xls": "spreadsheet",
    ".pptx": "slides", ".ppt": "slides",
}
_LEGACY_EXT = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}

# (扩展名 → [(COM ProgID 候选), 保存格式常量])；Office 优先，其次 WPS
_COM_TARGETS = {
    ".doc": (["Word.Application", "KWPS.Application"], 16),   # wdFormatXMLDocument
    ".ppt": (["PowerPoint.Application", "KWPP.Application"], 24),  # ppSaveAsOpenXMLPresentation
    ".xls": (["Excel.Application", "KET.Application"], 51),   # xlOpenXMLWorkbook
}


def _error_response(reason: str, message: str) -> str:
    return json.dumps(
        {"status": "error", "reason": reason, "error": message},
        ensure_ascii=False,
    )


def _parse_docx(path: str) -> dict:
    import docx

    document = docx.Document(path)
    blocks = []
    # document.element.body 按文档顺序遍历段落和表格
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, document)
            text = para.text.strip()
            if text:
                blocks.append({
                    "kind": "paragraph",
                    "style": para.style.name if para.style else "Normal",
                    "text": text,
                })
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            blocks.append({"kind": "table", "rows": rows})

    return {"type": "word", "blocks": blocks,
            "paragraph_count": sum(1 for b in blocks if b["kind"] == "paragraph"),
            "table_count": sum(1 for b in blocks if b["kind"] == "table")}


def _parse_xlsx(path: str) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = []
    for sheet in wb.worksheets:
        rows = []
        truncated = False
        for i, row in enumerate(sheet.iter_rows(values_only=True)):
            if i >= XLSX_MAX_ROWS_PER_SHEET:
                truncated = True
                break
            cells = ["" if c is None else str(c) for c in row]
            if any(cells):  # 跳过全空行
                rows.append(" | ".join(cells))
        sheets.append({
            "name": sheet.title,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        })
    wb.close()
    return {"type": "spreadsheet", "sheets": sheets, "sheet_count": len(sheets)}


def _parse_pptx(path: str) -> dict:
    from pptx import Presentation

    prs = Presentation(path)
    slides = []
    for i, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    texts.append(text)
            elif shape.has_table:
                rows = [[cell.text.strip() for cell in row.cells]
                        for row in shape.table.rows]
                texts.extend(" | ".join(r) for r in rows)
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        slides.append({"slide": i, "texts": texts, "notes": notes})
    return {"type": "slides", "slides": slides, "slide_count": len(slides)}


def _convert_legacy(path: Path) -> tuple[Optional[Path], Optional[str]]:
    """用本机 WPS/Office COM 把老格式 (.doc/.ppt/.xls) 转为新格式。

    Returns:
        (转换后的文件路径, None) 或 (None, 错误消息)
    """
    target_ext = _LEGACY_EXT[path.suffix.lower()]
    progs, save_format = _COM_TARGETS[path.suffix.lower()]
    out_path = (Path("runtime_memory/temp") / (path.stem + target_ext)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None, "pywin32 is not installed; cannot convert legacy Office formats"

    pythoncom.CoInitialize()
    app = None
    last_error = None
    try:
        for progid in progs:
            try:
                app = win32com.client.DispatchEx(progid)
                break
            except Exception as e:
                last_error = e
        if app is None:
            return None, (
                f"Neither Microsoft Office nor WPS found (tried {progs}). "
                f"Legacy {path.suffix} files require a local Office/WPS install. "
                f"Last error: {last_error}"
            )
        try:
            app.Visible = False
        except Exception:
            pass
        doc = None
        try:
            # Word: Documents.Open / PowerPoint: Presentations.Open / Excel: Workbooks.Open
            collection_name = {
                ".doc": "Documents", ".ppt": "Presentations", ".xls": "Workbooks",
            }[path.suffix.lower()]
            collection = getattr(app, collection_name)
            try:
                doc = collection.Open(str(path), ReadOnly=True)
            except TypeError:
                doc = collection.Open(str(path))
            doc.SaveAs(str(out_path), save_format)
            doc.Close(False)
        finally:
            try:
                app.Quit()
            except Exception:
                pass
        return out_path, None
    except Exception as e:
        return None, f"COM conversion failed: {e}"
    finally:
        pythoncom.CoUninitialize()


def parse_document(document_path: str) -> str:
    """解析 Office/WPS 文档（Word/Excel/PowerPoint）

    Args:
        document_path: 文档路径（.docx/.xlsx/.pptx/.doc/.xls/.ppt）

    Returns:
        JSON 字符串。success 时 type 为 word/slides/spreadsheet，
        内容分别为 blocks/slides/sheets。error 时包含 reason。
    """
    try:
        path = Path(document_path).resolve()
        if not path.exists():
            return _error_response("unavailable", f"File not found: {document_path}")

        file_size = path.stat().st_size
        if file_size == 0:
            return _error_response("empty", "File is empty")
        if file_size > DOC_MAX_SIZE:
            return _error_response(
                "too_large",
                f"File size ({file_size} bytes) exceeds maximum ({DOC_MAX_SIZE} bytes)",
            )

        ext = path.suffix.lower()
        if ext not in _EXT_TYPES:
            return _error_response(
                "unsupported",
                f"Unsupported extension '{ext}'. "
                f"Supported: {', '.join(sorted(_EXT_TYPES))}",
            )

        # 老格式先转换为新格式
        parse_path = path
        converted_from = None
        if ext in _LEGACY_EXT:
            converted, err = _convert_legacy(path)
            if err:
                return _error_response("conversion_failed", err)
            parse_path = converted
            converted_from = ext

        doc_type = _EXT_TYPES[ext]
        if doc_type == "word":
            data = _parse_docx(str(parse_path))
        elif doc_type == "spreadsheet":
            data = _parse_xlsx(str(parse_path))
        else:
            data = _parse_pptx(str(parse_path))

        # 空内容检测
        has_content = (
            any(b.get("text") or b.get("rows") for b in data.get("blocks", []))
            or any(s.get("rows") for s in data.get("sheets", []))
            or any(s.get("texts") or s.get("notes") for s in data.get("slides", []))
        )
        if not has_content:
            return _error_response(
                "empty",
                "No text content extracted. The document may consist of images only.",
            )

        data.update({
            "status": "success",
            "file": str(path),
            "file_size": file_size,
        })
        if converted_from:
            data["converted_from"] = converted_from
        return json.dumps(data, ensure_ascii=False)

    except Exception as e:
        return _error_response("unknown", f"Failed to parse document: {e}")


registry.register(
    name="parse_document",
    description=(
        "Parse Office/WPS documents: Word (.docx/.doc), Excel (.xlsx/.xls), "
        "PowerPoint (.pptx/.ppt). Returns structured text content: Word gives "
        "paragraphs (with heading styles) and tables; Excel gives sheet rows "
        "(pipe-separated cells); PowerPoint gives per-slide texts and speaker "
        "notes. Legacy .doc/.ppt/.xls are converted via the locally installed "
        "WPS/Office. Text only — embedded images are not readable. Use "
        "parse_pdf for PDF files."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "document_path": {
                "type": "string",
                "description": "Document file path, relative or absolute.",
            },
        },
        "required": ["document_path"],
    },
    handler=parse_document,
    group="document",
)
