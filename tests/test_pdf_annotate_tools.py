"""
PDF 批注工具单元测试 - test_pdf_annotate_tools.py

策略（交互式代码测试约定）：
- 不启动 TUI，直接调用 Tools/builtin/pdf_annotate_tools.py 的 handler 函数
- 用桌面 T3Time.pdf 的副本做写入测试，绝不碰原文件
- 每个写操作后用 fitz 重新打开文件验证批注真实存在、类型/颜色/内容正确
- 覆盖错误路径：文件不存在、页码越界、文本未命中、非法颜色/形状/模式
- 另存副本模式：验证原文件字节不变
- 结束时清理临时目录

运行：python test_pdf_annotate_tools.py
"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

import fitz

from Tools.builtin import pdf_annotate_tools as pat
from Tools.registry import registry

SRC_PDF = (
    Path.home() / "Desktop" / "T3Time_Analysis" / "T3Time_backup_original.pdf"
)  # 用未标注的原始备份做测试源（桌面原文件可能已被标注过）
TEST_DIR = Path("runtime_memory/temp/pdf_annot_test")
WORK_PDF = TEST_DIR / "T3Time.pdf"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def call(fn, **kwargs):
    """调用 handler 并解析 JSON 结果"""
    return json.loads(fn(**kwargs))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def annots_by_type(pdf: Path):
    """重新打开 PDF，按类型统计批注：{type_name: count}，附全部批注对象信息"""
    doc = fitz.open(str(pdf))
    stats = {}
    items = []
    for i in range(doc.page_count):
        for annot in doc[i].annots() or []:
            tname = annot.type[1]
            stats[tname] = stats.get(tname, 0) + 1
            items.append(
                {
                    "page": i + 1,
                    "type": tname,
                    "rect": annot.rect,
                    "content": annot.info.get("content", ""),
                    "title": annot.info.get("title", ""),
                    "colors": annot.colors,
                }
            )
    doc.close()
    return stats, items


def main():
    print("=" * 60)
    print("PDF 批注工具测试")
    print("=" * 60)

    if not SRC_PDF.exists():
        print(f"测试 PDF 不存在: {SRC_PDF}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 0. 注册检查 + registry 校验路径
    # ------------------------------------------------------------------
    print("\n[0] 工具注册与 registry 校验")
    for name in (
        "pdf_highlight", "pdf_annotate", "pdf_draw",
        "pdf_list_annotations", "pdf_delete_annotations",
    ):
        check(f"registered: {name}", registry.get_entry(name) is not None)
        entry = registry.get_entry(name)
        check(f"group=pdf: {name}", entry is not None and entry.group == "pdf")

    # dispatch 缺少必填参数 → 校验错误
    res = json.loads(asyncio.run(registry.dispatch("pdf_highlight", {"pdf_path": "x.pdf"})))
    check("dispatch missing required arg -> error", "error" in res)

    # ------------------------------------------------------------------
    # 1. 准备工作副本
    # ------------------------------------------------------------------
    print("\n[1] 准备工作副本")
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC_PDF, WORK_PDF)
    orig_size = WORK_PDF.stat().st_size
    print(f"  工作副本: {WORK_PDF} ({orig_size} bytes)")

    # ------------------------------------------------------------------
    # 2. 高亮：first + all + 附带批注
    # ------------------------------------------------------------------
    print("\n[2] pdf_highlight")
    r = call(
        pat.pdf_highlight, pdf_path=str(WORK_PDF),
        text="Multivariate time series forecasting", page=1,
        scope="first", note="核心任务定义", author="tester",
    )
    check("highlight first scope success", r.get("status") == "success", str(r))
    check("highlight first = 1 annot", r.get("annot_count") == 1, str(r))

    stats, items = annots_by_type(WORK_PDF)
    check("fitz 验证 Highlight 存在", stats.get("Highlight") == 1, str(stats))
    hl = [a for a in items if a["type"] == "Highlight"]
    check("高亮附带 note", bool(hl) and hl[0]["content"] == "核心任务定义", str(hl))
    check("高亮 author", bool(hl) and hl[0]["title"] == "tester", str(hl))

    # scope=all 全文档高亮 "T3Time"（大小写不敏感也命中）
    r = call(pat.pdf_highlight, pdf_path=str(WORK_PDF), text="t3time", color="#00C853")
    check("highlight all scope success", r.get("status") == "success", str(r))
    n = r.get("annot_count", 0)
    check("highlight all 命中多处", n >= 3, str(r))
    stats, items = annots_by_type(WORK_PDF)
    check("fitz 验证 Highlight 总数", stats.get("Highlight") == n + 1, str(stats))
    custom = [
        a for a in items
        if a["type"] == "Highlight"
        and a["colors"].get("stroke")
        and abs(a["colors"]["stroke"][1] - 0xC8 / 255) < 0.01
        and a["colors"]["stroke"][0] < 0.01
    ]
    check("自定义颜色已写入 (#00C853)", len(custom) == n,
          f"expected {n}, got {len(custom)}")

    # 其余划线模式
    for mode in ("underline", "strikeout", "squiggly"):
        r = call(
            pat.pdf_highlight, pdf_path=str(WORK_PDF), text="Abstract",
            page=1, mode=mode, scope="first",
        )
        check(f"mode={mode} success", r.get("status") == "success", str(r))
    stats, _ = annots_by_type(WORK_PDF)
    check("Underline 存在", stats.get("Underline") == 1, str(stats))
    check("StrikeOut 存在", stats.get("StrikeOut") == 1, str(stats))
    check("Squiggly 存在", stats.get("Squiggly") == 1, str(stats))

    # 软连字符断行回退：page 2 文本层是 "base-\nlines"，原生 search_for
    # 找不到 "baselines"，应触发词流回退命中
    r = call(
        pat.pdf_highlight, pdf_path=str(WORK_PDF),
        text="state-of-the-art baselines and demonstrates strong generalization",
        page=2, scope="first", color="purple",
    )
    check("软连字符断行回退命中", r.get("status") == "success", str(r))
    check("回退命中数 = 1", r.get("annot_count") == 1, str(r))

    # 增量保存：原字节前缀应保持不变
    head_unchanged = WORK_PDF.read_bytes()[:4096] == SRC_PDF.read_bytes()[:4096]
    check("增量保存保留原字节前缀", head_unchanged)

    # ------------------------------------------------------------------
    # 3. 批注：popup（锚定文本 / 坐标）+ freetext（中文）
    # ------------------------------------------------------------------
    print("\n[3] pdf_annotate")
    r = call(
        pat.pdf_annotate, pdf_path=str(WORK_PDF), note="这是摘要的起点",
        page=1, text="Abstract", style="popup", icon="Comment", color="blue",
    )
    check("popup 锚定文本 success", r.get("status") == "success", str(r))

    r = call(
        pat.pdf_annotate, pdf_path=str(WORK_PDF), note="star here",
        page=2, x=300, y=400, style="popup", icon="Star",
    )
    check("popup 坐标 success", r.get("status") == "success", str(r))

    r = call(
        pat.pdf_annotate, pdf_path=str(WORK_PDF),
        note="中文自由文本批注：残差融合是关键创新点", page=1,
        x=60, y=700, style="freetext", color="red", fontsize=12,
    )
    check("freetext 中文 success", r.get("status") == "success", str(r))

    stats, items = annots_by_type(WORK_PDF)
    check("Text(便签) x2", stats.get("Text") == 2, str(stats))
    check("FreeText x1", stats.get("FreeText") == 1, str(stats))
    ft = [a for a in items if a["type"] == "FreeText"]
    check("freetext 内容正确", bool(ft) and "残差融合" in ft[0]["content"], str(ft))

    # ------------------------------------------------------------------
    # 4. 画图：rect/oval/line/arrow
    # ------------------------------------------------------------------
    print("\n[4] pdf_draw")
    r = call(
        pat.pdf_draw, pdf_path=str(WORK_PDF), page=2, shape="rect",
        x0=100, y0=100, x1=500, y1=180, color="blue", fill="yellow", opacity=0.4,
    )
    check("rect success", r.get("status") == "success", str(r))
    r = call(
        pat.pdf_draw, pdf_path=str(WORK_PDF), page=2, shape="oval",
        x0=100, y0=220, x1=300, y1=300, color="purple", width=2,
    )
    check("oval success", r.get("status") == "success", str(r))
    r = call(
        pat.pdf_draw, pdf_path=str(WORK_PDF), page=2, shape="line",
        x0=100, y0=350, x1=500, y1=350, color="green",
    )
    check("line success (水平线越界判断不误报)", r.get("status") == "success", str(r))
    r = call(
        pat.pdf_draw, pdf_path=str(WORK_PDF), page=2, shape="arrow",
        x0=500, y0=400, x1=200, y1=500, color="#FF5722", width=2,
    )
    check("arrow success", r.get("status") == "success", str(r))

    stats, items = annots_by_type(WORK_PDF)
    check("Square x1", stats.get("Square") == 1, str(stats))
    check("Circle x1", stats.get("Circle") == 1, str(stats))
    check("Line x2", stats.get("Line") == 2, str(stats))
    sq = [a for a in items if a["type"] == "Square"]
    check("rect 填充色写入", bool(sq) and bool(sq[0]["colors"].get("fill")), str(sq))

    # ------------------------------------------------------------------
    # 5. 列出批注
    # ------------------------------------------------------------------
    print("\n[5] pdf_list_annotations")
    r = call(pat.pdf_list_annotations, pdf_path=str(WORK_PDF))
    check("list success", r.get("status") == "success", str(r))
    total_expected = sum(stats.values())
    check("list total 与 fitz 一致", r.get("total") == total_expected,
          f"list={r.get('total')} fitz={total_expected}")
    check("list 含 type/rect/content 字段",
          all("type" in a and "rect" in a for a in r.get("annotations", [])))

    r = call(pat.pdf_list_annotations, pdf_path=str(WORK_PDF), page=2)
    _doc = fitz.open(str(WORK_PDF))
    n_p2 = len(list(_doc[1].annots() or []))
    _doc.close()
    check("list 单页过滤", r.get("total") == n_p2,
          f"list={r.get('total')} fitz={n_p2}")

    # ------------------------------------------------------------------
    # 6. 删除批注
    # ------------------------------------------------------------------
    print("\n[6] pdf_delete_annotations")
    before, _ = annots_by_type(WORK_PDF)
    r = call(pat.pdf_delete_annotations, pdf_path=str(WORK_PDF), annot_type="highlight")
    check("delete Highlight success", r.get("status") == "success", str(r))
    check("delete 数量 = 之前 Highlight 数",
          r.get("deleted") == before.get("Highlight"), str(r))
    after, _ = annots_by_type(WORK_PDF)
    check("fitz 验证 Highlight 已清空", after.get("Highlight") is None, str(after))
    check("其余批注不受影响",
          after.get("Text") == 2 and after.get("Square") == 1, str(after))

    r = call(pat.pdf_delete_annotations, pdf_path=str(WORK_PDF), page=99)
    check("delete 页码越界 -> out_of_bounds",
          r.get("reason") == "out_of_bounds", str(r))

    # ------------------------------------------------------------------
    # 7. 另存副本模式：原文件不变
    # ------------------------------------------------------------------
    print("\n[7] output_path 另存副本")
    copy_src = TEST_DIR / "copy_src.pdf"
    copy_out = TEST_DIR / "copy_out.pdf"
    shutil.copy2(SRC_PDF, copy_src)
    src_hash = sha256(copy_src)
    r = call(
        pat.pdf_highlight, pdf_path=str(copy_src), text="T3Time", page=1,
        scope="first", output_path=str(copy_out),
    )
    check("另存副本 success", r.get("status") == "success", str(r))
    check("原文件字节不变", sha256(copy_src) == src_hash)
    stats, _ = annots_by_type(copy_out)
    check("副本含 Highlight", stats.get("Highlight") == 1, str(stats))

    # ------------------------------------------------------------------
    # 8. 错误路径
    # ------------------------------------------------------------------
    print("\n[8] 错误路径")
    r = call(pat.pdf_highlight, pdf_path=str(TEST_DIR / "nope.pdf"), text="x")
    check("文件不存在 -> unavailable", r.get("reason") == "unavailable", str(r))

    fake = TEST_DIR / "fake.pdf"
    fake.write_text("not a pdf", encoding="utf-8")
    r = call(pat.pdf_highlight, pdf_path=str(fake), text="x")
    check("假 PDF -> corrupted", r.get("reason") == "corrupted", str(r))

    r = call(pat.pdf_highlight, pdf_path=str(WORK_PDF), text="zzz_not_exist_zzz")
    check("文本未命中 -> not_found", r.get("reason") == "not_found", str(r))

    r = call(pat.pdf_highlight, pdf_path=str(WORK_PDF), text="T3Time", page=999)
    check("页码越界 -> out_of_bounds", r.get("reason") == "out_of_bounds", str(r))

    r = call(pat.pdf_highlight, pdf_path=str(WORK_PDF), text="T3Time", color="notacolor")
    check("非法颜色 -> invalid", r.get("reason") == "invalid", str(r))

    r = call(pat.pdf_draw, pdf_path=str(WORK_PDF), page=1, shape="triangle",
             x0=0, y0=0, x1=1, y1=1)
    check("非法形状 -> invalid", r.get("reason") == "invalid", str(r))

    r = call(pat.pdf_draw, pdf_path=str(WORK_PDF), page=1, shape="rect",
             x0=9000, y0=9000, x1=9100, y1=9100)
    check("矩形完全在页外 -> out_of_bounds", r.get("reason") == "out_of_bounds", str(r))

    r = call(pat.pdf_annotate, pdf_path=str(WORK_PDF), note="n", page=1,
             text="zzz_not_exist_zzz")
    check("锚定文本未命中 -> not_found", r.get("reason") == "not_found", str(r))

    r = call(pat.pdf_highlight, pdf_path=str(WORK_PDF), text="  ")
    check("空文本 -> invalid", r.get("reason") == "invalid", str(r))

    # ------------------------------------------------------------------
    # 9. 并发回归：同文件并行写不丢批注（修复并行 saveIncr 互相覆盖）
    # ------------------------------------------------------------------
    print("\n[9] 并发写入回归")
    import threading

    conc_pdf = TEST_DIR / "concurrent.pdf"
    shutil.copy2(SRC_PDF, conc_pdf)
    results = {}

    def _do_annotate():
        results["a"] = call(
            pat.pdf_annotate, pdf_path=str(conc_pdf), note="并发便签",
            page=1, x=60, y=700, style="freetext",
        )

    def _do_highlight():
        results["h"] = call(
            pat.pdf_highlight, pdf_path=str(conc_pdf),
            text="Adaptive Multi-Head Alignment", page=1, scope="first",
            color="green",
        )

    t1 = threading.Thread(target=_do_annotate)
    t2 = threading.Thread(target=_do_highlight)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    check(
        "并发两个写操作都成功",
        results["a"].get("status") == "success"
        and results["h"].get("status") == "success",
        str(results),
    )
    stats, _ = annots_by_type(conc_pdf)
    check(
        "FreeText 与 Highlight 都在",
        stats.get("FreeText") == 1 and stats.get("Highlight") == 1,
        str(stats),
    )

    # ------------------------------------------------------------------
    # 清理 + 汇总
    # ------------------------------------------------------------------
    print("\n[清理]")
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    check("临时目录已清理", not TEST_DIR.exists())

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
