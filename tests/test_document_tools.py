"""parse_document 工具测试：三格式 + 老格式 COM 转换 + 错误场景。"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio
import json
from pathlib import Path

import Tools.builtin.document_tools  # noqa: F401  触发注册
from Tools.registry import registry

TMP = Path("runtime_memory/temp")
TMP.mkdir(parents=True, exist_ok=True)


def make_samples():
    import docx

    d = docx.Document()
    d.add_heading("测试文档标题", level=1)
    d.add_paragraph("这是第一个段落，包含中文内容。")
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text = "表头A"
    t.rows[0].cells[1].text = "表头B"
    t.rows[1].cells[0].text = "数据1"
    t.rows[1].cells[1].text = "数据2"
    d.save(TMP / "test.docx")

    from pptx import Presentation

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "第一页标题"
    s.placeholders[1].text = "幻灯片正文内容"
    s.notes_slide.notes_text_frame.text = "演讲者备注：这里是备注"
    prs.save(TMP / "test.pptx")

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "销售数据"
    ws.append(["月份", "销量", "收入"])
    ws.append(["一月", 100, 5000])
    wb.save(TMP / "test.xlsx")


async def dispatch(path: str) -> dict:
    r = await registry.dispatch("parse_document", {"document_path": path})
    return json.loads(r if isinstance(r, str) else json.dumps(r))


async def main():
    make_samples()
    failed = 0

    def check(name, cond, extra=""):
        nonlocal failed
        if not cond:
            failed += 1
        print(f"{'PASS' if cond else 'FAIL'} {name}{' — ' + extra if extra else ''}")

    # 1. docx
    r = await dispatch(str(TMP / "test.docx"))
    check("docx success", r["status"] == "success" and r["type"] == "word")
    texts = [b["text"] for b in r["blocks"] if b["kind"] == "paragraph"]
    tables = [b for b in r["blocks"] if b["kind"] == "table"]
    check("docx heading", any("测试文档标题" in t for t in texts))
    check("docx table", tables and tables[0]["rows"][1] == ["数据1", "数据2"])

    # 2. pptx
    r = await dispatch(str(TMP / "test.pptx"))
    check("pptx success", r["status"] == "success" and r["type"] == "slides")
    check("pptx slide text", "第一页标题" in r["slides"][0]["texts"])
    check("pptx notes", "备注" in r["slides"][0]["notes"])

    # 3. xlsx
    r = await dispatch(str(TMP / "test.xlsx"))
    check("xlsx success", r["status"] == "success" and r["type"] == "spreadsheet")
    check("xlsx rows", r["sheets"][0]["rows"][1] == "一月 | 100 | 5000")

    # 4. 错误场景
    r = await dispatch(str(TMP / "test.pdf"))
    check("wrong ext", r["status"] == "error" and r["reason"] == "unsupported")
    r = await dispatch("nonexistent.docx")
    check("missing file", r["status"] == "error" and r["reason"] == "unavailable")

    # 5. 老格式 .doc 转换（需要本机 WPS/Office；不可用则明确报错也算通过）
    doc_path = TMP / "legacy_test.doc"
    try:
        import win32com.client  # noqa: F401

        # 用 COM 生成一个真正的 .doc（二进制格式）
        try:
            import pythoncom

            pythoncom.CoInitialize()
            app = None
            for progid in ("Word.Application", "KWPS.Application"):
                try:
                    app = win32com.client.DispatchEx(progid)
                    break
                except Exception:
                    continue
            if app:
                doc = app.Documents.Add()
                doc.Content.Text = "老格式文档内容测试"
                doc.SaveAs(str(doc_path.resolve()), 0)  # wdFormatDocument = .doc
                doc.Close(False)
                app.Quit()
                r = await dispatch(str(doc_path))
                check(
                    "legacy .doc conversion",
                    r["status"] == "success" and r.get("converted_from") == ".doc"
                    and any("老格式文档内容测试" in b.get("text", "") for b in r.get("blocks", [])),
                    r.get("error", ""),
                )
            else:
                print("SKIP legacy .doc — no Office/WPS installed")
        finally:
            pythoncom.CoUninitialize()
    except ImportError:
        print("SKIP legacy .doc — pywin32 not installed")

    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'}")
    return failed


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
