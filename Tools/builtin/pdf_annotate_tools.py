"""
PDF 批注工具 - 文本高亮/划线、便签批注、图形批注、批注管理

基于 PyMuPDF (fitz)。坐标系：页面左上角为原点 (0,0)，y 轴向下，
单位为 point（1/72 英寸），页面尺寸可用 parse_pdf 或本组工具返回的 page 信息估算。

保存策略：
- 默认原地增量保存（saveIncr，追加写、保留原字节历史；不支持时降级为临时文件 + 替换）
- 传入 output_path 时另存副本，不改动原文件

并发安全：
- Actor 可能在同一 LLM 步并行下发多个工具调用（registry 经线程池并发执行）。
  同一文件的 open→修改→saveIncr 若并发交错，后写者的增量段会基于旧文件长度
  覆盖先写者的段，导致批注丢失。因此所有 handler 按文件路径持有锁，
  整个"打开→修改→保存"周期串行化（读取类工具同样持锁，保证看到最新落盘结果）。

工具一览：
- pdf_highlight          按文本搜索做高亮/下划线/删除线/波浪线，可附带弹注
- pdf_annotate           便签（popup 图标）或自由文本（freetext 直接显示在页面上）
- pdf_draw               矩形/椭圆/直线/箭头
- pdf_list_annotations   列出已有批注（校验用）
- pdf_delete_annotations 按页/按类型删除批注
"""

import json
import logging
import os
import re
import threading
from pathlib import Path

import fitz

from Tools.registry import registry
from Tools.pdf_utils import validate_pdf_magic_bytes

logger = logging.getLogger(__name__)

# =============================================================================
# 常量与公共辅助
# =============================================================================

COLOR_NAMES = {
    "yellow": (1.0, 1.0, 0.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "orange": (1.0, 0.65, 0.0),
    "purple": (0.63, 0.13, 0.94),
    "pink": (1.0, 0.41, 0.71),
    "cyan": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "gray": (0.5, 0.5, 0.5),
}

MARKUP_MODES = {
    "highlight": "add_highlight_annot",
    "underline": "add_underline_annot",
    "strikeout": "add_strikeout_annot",
    "squiggly": "add_squiggly_annot",
}

POPUP_ICONS = [
    "Comment", "Key", "Note", "Help", "NewParagraph", "Paragraph", "Insert",
    "Check", "Circle", "Cross", "RightArrow", "RightPointer", "Star",
    "UpArrow", "UpLeftArrow",
]

# 箭头端点常量（getattr 兜底，防止 PyMuPDF 版本改名）
_LE_NONE = getattr(fitz, "PDF_ANNOT_LE_NONE", 0)
_LE_CLOSED_ARROW = getattr(fitz, "PDF_ANNOT_LE_CLOSED_ARROW", 5)

_MAX_LIST_ANNOTATIONS = 500

# 按文件路径的串行化锁：防止同一步并行工具调用对同一 PDF 并发增量保存互相覆盖
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


def _error_response(reason: str, message: str) -> str:
    return json.dumps(
        {"status": "error", "reason": reason, "error": message},
        ensure_ascii=False,
    )


def _parse_color(color):
    """颜色解析：支持颜色名、#RGB/#RRGGBB、'r,g,b'（0-1 浮点或 0-255 整数）。

    返回 (r, g, b) 0-1 元组；color 为空返回 None；无法解析抛 ValueError。
    """
    if color is None or str(color).strip() == "":
        return None
    s = str(color).strip().lower()
    if s in COLOR_NAMES:
        return COLOR_NAMES[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            try:
                return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                pass
    parts = [p for p in s.replace(" ", "").split(",") if p]
    if len(parts) == 3:
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            vals = None
        if vals and all(0 <= v <= 255 for v in vals):
            if any(v > 1 for v in vals):
                vals = [v / 255.0 for v in vals]
            return tuple(vals)
    raise ValueError(
        f"Invalid color: '{color}'. Use a name ({', '.join(sorted(COLOR_NAMES))}), "
        "'#RRGGBB' hex, or 'r,g,b' floats (0-1) / ints (0-255)."
    )


def _open_pdf(pdf_path):
    """打开 PDF 并做基础校验。成功返回 (doc, Path)，失败返回 (None, err_json)。"""
    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        return None, _error_response("unavailable", f"PDF file not found: {pdf_path}")
    if path.stat().st_size == 0:
        return None, _error_response("empty", "PDF file is empty")
    if not validate_pdf_magic_bytes(str(path)):
        return None, _error_response(
            "corrupted",
            "File does not appear to be a valid PDF (missing %PDF- header)",
        )
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        return None, _error_response("corrupted", f"Failed to open PDF: {e}")
    if doc.is_encrypted:
        doc.close()
        return None, _error_response(
            "password_protected", "PDF is encrypted/password-protected"
        )
    return (doc, path), None


def _get_page(doc, page_number):
    """1-based 页码 → page 对象。失败返回 (None, err_json)。"""
    if not isinstance(page_number, int) or page_number < 1 or page_number > doc.page_count:
        return None, _error_response(
            "out_of_bounds",
            f"Invalid page {page_number}: PDF has {doc.page_count} pages "
            "(pages are 1-based)",
        )
    return doc[page_number - 1], None


def _save_pdf(doc, src_path: Path, output_path):
    """保存 PDF。成功返回 (written_path_str, None)，失败返回 (None, err_json)。"""
    try:
        if output_path:
            out = Path(output_path).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            if out == src_path:
                return _save_in_place_audited(doc, src_path)
            existed = out.exists()
            old_bytes = out.read_bytes() if existed else None
            doc.save(str(out), garbage=1, deflate=True)
            logger.info("pdf saved to copy: %s", out)
            _emit_pdf_change(out, "modify" if existed else "create", old_bytes)
            return str(out), None
        return _save_in_place_audited(doc, src_path)
    except PermissionError:
        return None, _error_response(
            "locked",
            "Cannot write PDF - the file may be open in a PDF viewer. "
            "Close the viewer and retry.",
        )
    except Exception as e:
        logger.exception("pdf save failed: %s", src_path)
        return None, _error_response("unknown", f"Failed to save PDF: {e}")


def _emit_pdf_change(path: Path, op: str, old_bytes):
    """审计缝：PDF 写盘后上报（读盘失败静默——审计不阻断主流程）。"""
    from Tools.file_change_hook import emit_file_change

    try:
        emit_file_change(str(path), op, old_bytes, path.read_bytes(), "hook:pdf")
    except OSError:
        pass


def _save_in_place_audited(doc, src_path: Path):
    old_bytes = src_path.read_bytes() if src_path.exists() else None
    written, err = _save_in_place(doc, src_path)
    if err is None:
        _emit_pdf_change(src_path, "modify", old_bytes)
    return written, err


def _save_in_place(doc, src_path: Path):
    """原地保存：优先增量保存（保留原字节可回溯），失败降级为临时文件 + 替换。"""
    try:
        if doc.can_save_incrementally():
            doc.saveIncr()
            logger.info("pdf saved in place (incremental): %s", src_path)
            return str(src_path), None
        raise RuntimeError("incremental save not supported for this document")
    except PermissionError:
        return None, _error_response(
            "locked",
            "Cannot write PDF - the file may be open in a PDF viewer. "
            "Close the viewer and retry.",
        )
    except Exception as e:
        logger.warning(
            "incremental save failed (%s), falling back to full rewrite", e
        )
        tmp = src_path.with_suffix(src_path.suffix + ".tmp")
        try:
            doc.save(str(tmp), garbage=1, deflate=True)
            os.replace(tmp, src_path)
            logger.info("pdf saved in place (full rewrite): %s", src_path)
            return str(src_path), None
        except PermissionError:
            tmp.unlink(missing_ok=True)
            return None, _error_response(
                "locked",
                "Cannot write PDF - the file may be open in a PDF viewer. "
                "Close the viewer and retry.",
            )
        except Exception as e2:
            tmp.unlink(missing_ok=True)
            logger.exception("pdf full-rewrite save failed: %s", src_path)
            return None, _error_response("unknown", f"Failed to save PDF: {e2}")


def _norm_rect(x0: float, y0: float, x1: float, y1: float) -> fitz.Rect:
    """两点构造归一化矩形（fitz.Rect(p1, p2) 对反向坐标不归一化，会得到空矩形）。"""
    return fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _group_quads_into_hits(quads):
    """把 search_for 返回的扁平 quad 列表按命中分组。

    单个命中换行时会产生多个 quad（每行一个）。规则：
    - 与上一 quad 在同一行（纵坐标区间重叠）→ 必为新命中（单次出现不可能同行两段）
    - 新起一行且起点不右于上一行起点（允许 2pt 容差）→ 视为同一命中换行
    其余 → 新命中。
    """
    hits = []
    current = []
    for q in quads:
        r = q.rect
        if not current:
            current = [q]
            continue
        prev = current[-1].rect
        same_line = r.y0 < prev.y1 - 1
        wrapped = not same_line and r.x0 <= prev.x0 + 2
        if not same_line and wrapped:
            current.append(q)
        else:
            hits.append(current)
            current = [q]
    if current:
        hits.append(current)
    return hits


def _build_needle_pattern(needle: str) -> re.Pattern:
    """把搜索词编译成正则，容忍 PDF 断行软连字符。

    学术 PDF 排版断行时会把单词拆成 'base-\\nlines'，原生 search_for 找不到
    'baselines'。词流以单空格拼接后：
    - needle 的空格 → \\s+（容忍换行）
    - needle 的连字符 → -\\s?（'multi- head' 与 'multi-head' 均可）
    - 其余相邻字符之间允许可选的 '- '（'baselines' 可命中 'base- lines'）
    """
    parts = []
    n = len(needle)
    for i, ch in enumerate(needle):
        if ch == " ":
            parts.append(r"\s+")
        elif ch == "-":
            parts.append(r"-\s?")
        else:
            parts.append(re.escape(ch))
            if i + 1 < n and needle[i + 1] != " ":
                parts.append(r"(?:-\s)?")
    return re.compile("".join(parts), re.IGNORECASE)


def _rect_to_quad(r: fitz.Rect) -> fitz.Quad:
    """Rect → Quad（Quad 构造器接受 4 个角点，不接受 Rect）。"""
    return fitz.Quad(
        fitz.Point(r.x0, r.y0), fitz.Point(r.x1, r.y0),
        fitz.Point(r.x0, r.y1), fitz.Point(r.x1, r.y1),
    )


def _word_stream_search(page, needle: str):
    """词流回退搜索：仅在原生 search_for 未命中时触发（软连字符断行场景）。

    用 get_text("words") 把页面拼成单空格分隔的字符流做正则匹配，
    命中区间的词按行（block,line）合并为 quad。词粒度（非字符级），
    返回结构与原生分组一致：List[List[Quad]]。
    """
    needle_norm = " ".join(needle.split())
    if not needle_norm:
        return []
    words = page.get_text("words")
    if not words:
        return []

    offsets = []  # 每个词在字符流中的 [start, end)
    pos = 0
    for w in words:
        offsets.append((pos, pos + len(w[4])))
        pos += len(w[4]) + 1
    stream = " ".join(w[4] for w in words)

    pattern = _build_needle_pattern(needle_norm)
    hits = []
    for m in pattern.finditer(stream):
        s, e = m.start(), m.end()
        covered = [i for i, (ws, we) in enumerate(offsets) if we > s and ws < e]
        if not covered:
            continue
        quads = []
        cur_key = None
        cur_rect: fitz.Rect | None = None
        for i in covered:
            w = words[i]
            key = (w[5], w[6])  # (block_no, line_no)
            r = fitz.Rect(w[:4])
            if cur_rect is None or key != cur_key:
                if cur_rect is not None:
                    quads.append(_rect_to_quad(cur_rect))
                cur_key = key
                cur_rect = r
            else:
                cur_rect = cur_rect | r
        if cur_rect is not None:
            quads.append(_rect_to_quad(cur_rect))
        hits.append(quads)
    return hits


def _search_hits(page, needle: str):
    """统一搜索入口：原生 search_for（字符级精确）优先，未命中走词流回退。

    返回 List[List[quad]]，每个命中一组 quad（跨行命中已合并）。
    """
    quads = page.search_for(needle, quads=True)
    if quads:
        return _group_quads_into_hits(quads)
    hits = _word_stream_search(page, needle)
    if hits:
        logger.info(
            "native search missed %r, word-stream fallback found %d hit(s)",
            needle, len(hits),
        )
    return hits


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _freetext_rect(page_rect: fitz.Rect, x, y, note: str, fontsize: float):
    """按文本量估算 freetext 批注框尺寸并夹取到页面内。"""
    # 估算单字符宽度：CJK ≈ fontsize，ASCII ≈ 0.5 * fontsize
    max_width = 260.0
    line_widths = []
    cur_w = 0.0
    n_lines = 1
    for ch in note:
        w = fontsize if _has_cjk(ch) else fontsize * 0.52
        if ch == "\n" or cur_w + w > max_width:
            line_widths.append(cur_w)
            n_lines += 1
            cur_w = 0.0 if ch == "\n" else w
        else:
            cur_w += w
    line_widths.append(cur_w)
    width = min(max_width, max(60.0, max(line_widths))) + 16
    height = n_lines * fontsize * 1.35 + 14
    rect = fitz.Rect(x, y, x + width, y + height)
    rect = rect & page_rect
    if rect.is_empty:
        rect = fitz.Rect(
            page_rect.x0 + 10, page_rect.y0 + 10,
            min(page_rect.x1 - 10, page_rect.x0 + 10 + width),
            min(page_rect.y1 - 10, page_rect.y0 + 10 + height),
        )
    return rect


# =============================================================================
# 工具 1: pdf_highlight
# =============================================================================

def pdf_highlight(
    pdf_path: str,
    text: str,
    page: int | None = None,
    mode: str = "highlight",
    scope: str = "all",
    color: str | None = None,
    note: str | None = None,
    author: str | None = None,
    output_path: str | None = None,
) -> str:
    """按文本搜索在 PDF 中做高亮/下划线/删除线/波浪线标记

    Args:
        pdf_path: PDF 文件路径
        text: 要标记的文本（大小写不敏感，跨行命中会自动合并为一条批注）
        page: 限定页码（1-based）；不传则搜索全部页面
        mode: highlight(高亮) / underline(下划线) / strikeout(删除线) / squiggly(波浪线)
        scope: all(标记所有出现处) / first(只标记第一处)
        color: 颜色名(yellow/red/green/blue/orange/purple/pink/cyan/magenta/black/white/gray)
               或 '#RRGGBB' 或 'r,g,b'；不传用查看器默认色（高亮为黄色）
        note: 可选，附在标记上的弹出批注内容
        author: 可选，批注作者名
        output_path: 可选，另存到该路径而不改原文件

    Returns:
        JSON：success 时含 matches(命中数)、pages、annot_count、output；
        未找到文本返回 status=error, reason=not_found
    """
    logger.info(
        "pdf_highlight: path=%s text=%r page=%s mode=%s scope=%s color=%s",
        pdf_path, text, page, mode, scope, color,
    )
    if not text or not text.strip():
        return _error_response("invalid", "text must be a non-empty string")
    if mode not in MARKUP_MODES:
        return _error_response(
            "invalid",
            f"Invalid mode: '{mode}'. Expected one of {sorted(MARKUP_MODES)}",
        )
    if scope not in ("all", "first"):
        return _error_response("invalid", "scope must be 'all' or 'first'")
    try:
        stroke = _parse_color(color)
    except ValueError as e:
        return _error_response("invalid", str(e))

    opened, err = _open_pdf(pdf_path)
    if err:
        return err
    assert opened is not None
    doc, src_path = opened
    lock = _file_lock(src_path)
    lock.acquire()
    try:
        if page is not None:
            pg, err = _get_page(doc, page)
            if err:
                return err
            assert pg is not None
            pages = [(page, pg)]
        else:
            pages = [(i + 1, doc[i]) for i in range(doc.page_count)]

        add_fn_name = MARKUP_MODES[mode]
        annot_count = 0
        match_pages = []
        total_hits = 0
        for page_no, pg in pages:
            hits = _search_hits(pg, text)
            if not hits:
                continue
            if scope == "first":
                hits = hits[:1]
            for hit in hits:
                annot = getattr(pg, add_fn_name)(hit)
                if stroke is not None:
                    annot.set_colors(stroke=stroke)
                info_kwargs = {}
                if note:
                    info_kwargs["content"] = note
                if author:
                    info_kwargs["title"] = author
                if info_kwargs:
                    annot.set_info(**info_kwargs)
                annot.update()
                annot_count += 1
            total_hits += len(hits)
            match_pages.append(page_no)
            if scope == "first" and hits:
                break

        if annot_count == 0:
            return _error_response(
                "not_found", f"Text not found in PDF: {text!r}"
            )

        written, err = _save_pdf(doc, src_path, output_path)
        if err:
            return err
        logger.info(
            "pdf_highlight done: %d annots on %d page(s) -> %s",
            annot_count, len(match_pages), written,
        )
        return json.dumps(
            {
                "status": "success",
                "mode": mode,
                "matches": total_hits,
                "pages": match_pages,
                "annot_count": annot_count,
                "output": written,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("pdf_highlight failed")
        return _error_response("unknown", f"Failed to highlight: {type(e).__name__}: {e}")
    finally:
        doc.close()
        lock.release()


# =============================================================================
# 工具 2: pdf_annotate
# =============================================================================

def pdf_annotate(
    pdf_path: str,
    note: str,
    page: int = 1,
    text: str | None = None,
    x: float | None = None,
    y: float | None = None,
    style: str = "popup",
    icon: str = "Comment",
    color: str | None = None,
    author: str | None = None,
    fontsize: float = 11,
    output_path: str | None = None,
) -> str:
    """在 PDF 页面上添加文字批注

    两种样式：
    - popup（默认）：一个批注图标，点击弹出批注内容。锚点优先取 text 搜索到的
      第一处文本右侧，否则取 (x, y)，都不给则放在页面左上角 (36, 36)。
    - freetext：批注文字直接显示在页面上（带边框的文本框），位置由 (x, y) 指定
      （默认 36,36），框尺寸按文本量自动估算。支持中文。

    Args:
        pdf_path: PDF 文件路径
        note: 批注内容
        page: 页码（1-based）
        text: 可选，锚定到该文本的第一处出现位置
        x, y: 可选，批注位置（页面左上角为原点，单位 point）
        style: popup / freetext
        icon: popup 图标：Comment/Note/Key/Help/Insert/Check/Cross/Star/RightArrow 等
        color: 颜色（popup=图标色，freetext=文字色）
        author: 可选，批注作者名
        fontsize: freetext 字号
        output_path: 可选，另存副本

    Returns:
        JSON：success 时含 style, page, rect, output
    """
    logger.info(
        "pdf_annotate: path=%s page=%s style=%s text=%r xy=(%s,%s) note=%r",
        pdf_path, page, style, text, x, y, note[:60],
    )
    if not note or not note.strip():
        return _error_response("invalid", "note must be a non-empty string")
    if style not in ("popup", "freetext"):
        return _error_response("invalid", "style must be 'popup' or 'freetext'")
    if icon not in POPUP_ICONS:
        return _error_response(
            "invalid", f"Invalid icon: '{icon}'. Expected one of {POPUP_ICONS}"
        )
    try:
        rgb = _parse_color(color)
    except ValueError as e:
        return _error_response("invalid", str(e))

    opened, err = _open_pdf(pdf_path)
    if err:
        return err
    assert opened is not None
    doc, src_path = opened
    lock = _file_lock(src_path)
    lock.acquire()
    try:
        pg, err = _get_page(doc, page)
        if err:
            return err
        assert pg is not None

        # 确定锚点
        anchor = None
        if text and text.strip():
            hits = _search_hits(pg, text)
            if not hits:
                return _error_response(
                    "not_found",
                    f"Anchor text not found on page {page}: {text!r}",
                )
            r = hits[0][0].rect
            anchor = fitz.Point(
                min(r.x1 + 3, pg.rect.x1 - 12), max(r.y0 - 2, pg.rect.y0 + 4)
            )
        elif x is not None and y is not None:
            anchor = fitz.Point(float(x), float(y))
        else:
            anchor = fitz.Point(36, 36)

        if style == "popup":
            annot = pg.add_text_annot(anchor, note, icon=icon)
            if rgb is not None:
                annot.set_colors(stroke=rgb)
            if author:
                annot.set_info(title=author)
            annot.update()
            rect = list(round(v, 1) for v in annot.rect)
        else:  # freetext
            fontname = "china-s" if _has_cjk(note) else "helv"
            rect_obj = _freetext_rect(pg.rect, anchor.x, anchor.y, note, fontsize)
            annot = pg.add_freetext_annot(
                rect_obj,
                note,
                fontsize=fontsize,
                fontname=fontname,
                text_color=rgb if rgb is not None else (0, 0, 0),
            )
            if author:
                annot.set_info(title=author)
            annot.update()
            rect = list(round(v, 1) for v in annot.rect)

        written, err = _save_pdf(doc, src_path, output_path)
        if err:
            return err
        logger.info("pdf_annotate done: style=%s page=%s -> %s", style, page, written)
        return json.dumps(
            {
                "status": "success",
                "style": style,
                "page": page,
                "rect": rect,
                "output": written,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("pdf_annotate failed")
        return _error_response("unknown", f"Failed to annotate: {type(e).__name__}: {e}")
    finally:
        doc.close()
        lock.release()


# =============================================================================
# 工具 3: pdf_draw
# =============================================================================

def pdf_draw(
    pdf_path: str,
    page: int,
    shape: str = "rect",
    x0: float = 0,
    y0: float = 0,
    x1: float = 0,
    y1: float = 0,
    color: str = "red",
    fill: str | None = None,
    width: float = 1.5,
    opacity: float | None = None,
    author: str | None = None,
    output_path: str | None = None,
) -> str:
    """在 PDF 页面上画图形批注：矩形 / 椭圆 / 直线 / 箭头

    Args:
        pdf_path: PDF 文件路径
        page: 页码（1-based）
        shape: rect(矩形) / oval(椭圆) / line(直线) / arrow(箭头, 从 (x0,y0) 指向 (x1,y1))
        x0, y0, x1, y1: 坐标（页面左上角原点，单位 point）。rect/oval 为对角两点，
                        自动归一化，允许颠倒；坐标越界自动夹取到页面内。
        color: 线条颜色（默认 red）
        fill: 填充色（仅 rect/oval 有效，默认不填充）
        width: 线宽（point）
        opacity: 不透明度 0-1（默认完全不透明）
        author: 可选，批注作者名
        output_path: 可选，另存副本

    Returns:
        JSON：success 时含 shape, page, rect, output
    """
    logger.info(
        "pdf_draw: path=%s page=%s shape=%s (%s,%s)-(%s,%s) color=%s fill=%s",
        pdf_path, page, shape, x0, y0, x1, y1, color, fill,
    )
    if shape not in ("rect", "oval", "line", "arrow"):
        return _error_response(
            "invalid", f"Invalid shape: '{shape}'. Expected rect/oval/line/arrow"
        )
    try:
        stroke = _parse_color(color)
        fill_rgb = _parse_color(fill)
    except ValueError as e:
        return _error_response("invalid", str(e))
    if opacity is not None and not (0 <= float(opacity) <= 1):
        return _error_response("invalid", "opacity must be between 0 and 1")

    opened, err = _open_pdf(pdf_path)
    if err:
        return err
    assert opened is not None
    doc, src_path = opened
    lock = _file_lock(src_path)
    lock.acquire()
    try:
        pg, err = _get_page(doc, page)
        if err:
            return err
        assert pg is not None

        p1 = fitz.Point(float(x0), float(y0))
        p2 = fitz.Point(float(x1), float(y1))

        if shape in ("line", "arrow"):
            # 水平/垂直线的矩形宽度或高度为 0，先膨胀 1pt 再做相交判断
            nb = _norm_rect(p1.x, p1.y, p2.x, p2.y)
            line_rect = fitz.Rect(nb.x0 - 1, nb.y0 - 1, nb.x1 + 1, nb.y1 + 1)
            if (line_rect & pg.rect).is_empty:
                return _error_response(
                    "out_of_bounds",
                    f"Line ({x0},{y0})-({x1},{y1}) does not intersect "
                    f"page bounds {list(pg.rect)}",
                )
            annot = pg.add_line_annot(p1, p2)
            if shape == "arrow":
                annot.set_line_ends(_LE_NONE, _LE_CLOSED_ARROW)
        else:
            rect = _norm_rect(p1.x, p1.y, p2.x, p2.y) & pg.rect
            if rect.is_empty:
                return _error_response(
                    "out_of_bounds",
                    f"Rectangle ({x0},{y0})-({x1},{y1}) does not intersect "
                    f"page bounds {list(pg.rect)}",
                )
            if shape == "rect":
                annot = pg.add_rect_annot(rect)
            else:
                annot = pg.add_circle_annot(rect)

        annot.set_colors(stroke=stroke, fill=fill_rgb)
        annot.set_border(width=float(width))  # type: ignore[arg-type]  # PyMuPDF stub 误标为 int，运行时支持 float
        if opacity is not None:
            annot.set_opacity(float(opacity))
        if author:
            annot.set_info(title=author)
        annot.update()

        written, err = _save_pdf(doc, src_path, output_path)
        if err:
            return err
        logger.info("pdf_draw done: shape=%s page=%s -> %s", shape, page, written)
        return json.dumps(
            {
                "status": "success",
                "shape": shape,
                "page": page,
                "rect": list(round(v, 1) for v in annot.rect),
                "output": written,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("pdf_draw failed")
        return _error_response("unknown", f"Failed to draw: {type(e).__name__}: {e}")
    finally:
        doc.close()
        lock.release()


# =============================================================================
# 工具 4: pdf_list_annotations
# =============================================================================

def pdf_list_annotations(pdf_path: str, page: int | None = None) -> str:
    """列出 PDF 中已有的批注（高亮/便签/图形等），用于校验批注结果

    Args:
        pdf_path: PDF 文件路径
        page: 可选，只看某一页（1-based）

    Returns:
        JSON：count(返回条数)、total(批注总数)、annotations[{page, type, rect,
        author, content, stroke, fill}]；超过 500 条截断并置 truncated=true
    """
    opened, err = _open_pdf(pdf_path)
    if err:
        return err
    assert opened is not None
    doc, src_path = opened
    lock = _file_lock(src_path)
    lock.acquire()
    try:
        if page is not None:
            pg, err = _get_page(doc, page)
            if err:
                return err
            assert pg is not None
            pages = [(page, pg)]
        else:
            pages = [(i + 1, doc[i]) for i in range(doc.page_count)]

        annotations = []
        total = 0
        for page_no, pg in pages:
            for annot in pg.annots() or []:
                total += 1
                if len(annotations) >= _MAX_LIST_ANNOTATIONS:
                    continue
                info = annot.info or {}
                colors = annot.colors or {}
                stroke = colors.get("stroke")
                fill = colors.get("fill")
                annotations.append(
                    {
                        "page": page_no,
                        "type": annot.type[1] if annot.type else "Unknown",
                        "rect": list(round(v, 1) for v in annot.rect),
                        "author": info.get("title") or None,
                        "content": (info.get("content") or "")[:300] or None,
                        "stroke": (
                            list(round(c, 2) for c in stroke) if stroke else None
                        ),
                        "fill": list(round(c, 2) for c in fill) if fill else None,
                    }
                )
        return json.dumps(
            {
                "status": "success",
                "count": len(annotations),
                "total": total,
                "truncated": total > len(annotations),
                "annotations": annotations,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("pdf_list_annotations failed")
        return _error_response(
            "unknown", f"Failed to list annotations: {type(e).__name__}: {e}"
        )
    finally:
        doc.close()
        lock.release()


# =============================================================================
# 工具 5: pdf_delete_annotations
# =============================================================================

def pdf_delete_annotations(
    pdf_path: str,
    page: int | None = None,
    annot_type: str | None = None,
    output_path: str | None = None,
) -> str:
    """删除 PDF 中的批注（修正错误批注用）

    Args:
        pdf_path: PDF 文件路径
        page: 可选，只删某一页（1-based）；不传则处理全部页面
        annot_type: 可选，只删指定类型，如 Highlight/Underline/StrikeOut/Squiggly/
                    Text(便签)/FreeText/Square/Circle/Line（大小写不敏感）；
                    不传则删除范围内全部批注
        output_path: 可选，另存副本

    Returns:
        JSON：deleted(删除条数)、output
    """
    logger.info(
        "pdf_delete_annotations: path=%s page=%s type=%s",
        pdf_path, page, annot_type,
    )
    opened, err = _open_pdf(pdf_path)
    if err:
        return err
    assert opened is not None
    doc, src_path = opened
    lock = _file_lock(src_path)
    lock.acquire()
    try:
        if page is not None:
            pg, err = _get_page(doc, page)
            if err:
                return err
            assert pg is not None
            pages = [pg]
        else:
            pages = [doc[i] for i in range(doc.page_count)]

        type_filter = annot_type.strip().lower() if annot_type else None
        deleted = 0
        for pg in pages:
            # 先收集再删除（遍历时删除会使迭代器失效）
            targets = []
            for annot in pg.annots() or []:
                name = annot.type[1] if annot.type else ""
                if type_filter and name.lower() != type_filter:
                    continue
                targets.append(annot)
            for annot in targets:
                pg.delete_annot(annot)
                deleted += 1

        if deleted == 0:
            return json.dumps(
                {"status": "success", "deleted": 0, "output": str(src_path)},
                ensure_ascii=False,
            )

        written, err = _save_pdf(doc, src_path, output_path)
        if err:
            return err
        logger.info("pdf_delete_annotations done: deleted=%d -> %s", deleted, written)
        return json.dumps(
            {"status": "success", "deleted": deleted, "output": written},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("pdf_delete_annotations failed")
        return _error_response(
            "unknown", f"Failed to delete annotations: {type(e).__name__}: {e}"
        )
    finally:
        doc.close()
        lock.release()


# =============================================================================
# 工具注册
# =============================================================================

_COLOR_DESC = (
    "Color: a name (yellow/red/green/blue/orange/purple/pink/cyan/magenta/"
    "black/white/gray), '#RRGGBB' hex, or 'r,g,b' floats (0-1) / ints (0-255)."
)
_COORD_DESC = "Coordinates: page origin is top-left, y grows downward, unit is point (1/72 inch)."

registry.register(
    name="pdf_highlight",
    description=(
        "Highlight, underline, strike out, or squiggle-underline text in a PDF "
        "by searching for the text (case-insensitive). Multi-line matches are "
        "merged into one annotation. Optionally attach a popup comment (note) "
        "to the markup. By default the PDF is modified in place (incremental "
        "save preserves the original bytes); pass output_path to write a copy "
        "instead. Use pdf_list_annotations to verify the result."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute.",
            },
            "text": {
                "type": "string",
                "description": "Text to mark up (case-insensitive search).",
            },
            "page": {
                "type": "integer",
                "description": "Restrict search to one page (1-based). Default: all pages.",
            },
            "mode": {
                "type": "string",
                "enum": ["highlight", "underline", "strikeout", "squiggly"],
                "description": "Markup type. Default: highlight.",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "first"],
                "description": "Mark all occurrences or only the first one. Default: all.",
            },
            "color": {
                "type": "string",
                "description": _COLOR_DESC + " Default: viewer default (yellow for highlight).",
            },
            "note": {
                "type": "string",
                "description": "Optional popup comment attached to the markup.",
            },
            "author": {
                "type": "string",
                "description": "Optional annotation author name.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: save to this path instead of modifying in place.",
            },
        },
        "required": ["pdf_path", "text"],
    },
    handler=pdf_highlight,
    group="pdf",
)

registry.register(
    name="pdf_annotate",
    description=(
        "Add a text annotation to a PDF page. style='popup' (default) places an "
        "icon that pops up the note when clicked - anchored to the first match "
        "of 'text', or to (x, y), or to the page's top-left corner. "
        "style='freetext' renders the note directly on the page as a bordered "
        "text box at (x, y) with auto-estimated size (Chinese text supported). "
        + _COORD_DESC
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute.",
            },
            "note": {
                "type": "string",
                "description": "Annotation content (Chinese supported).",
            },
            "page": {
                "type": "integer",
                "description": "Page number (1-based). Default: 1.",
            },
            "text": {
                "type": "string",
                "description": "Anchor the annotation to the first match of this text.",
            },
            "x": {"type": "number", "description": "X position in points from left."},
            "y": {"type": "number", "description": "Y position in points from top."},
            "style": {
                "type": "string",
                "enum": ["popup", "freetext"],
                "description": "popup icon or visible free text. Default: popup.",
            },
            "icon": {
                "type": "string",
                "enum": POPUP_ICONS,
                "description": "Icon for popup style. Default: Comment.",
            },
            "color": {
                "type": "string",
                "description": _COLOR_DESC + " popup: icon color; freetext: text color.",
            },
            "author": {
                "type": "string",
                "description": "Optional annotation author name.",
            },
            "fontsize": {
                "type": "number",
                "description": "Font size for freetext style. Default: 11.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: save to this path instead of modifying in place.",
            },
        },
        "required": ["pdf_path", "note"],
    },
    handler=pdf_annotate,
    group="pdf",
)

registry.register(
    name="pdf_draw",
    description=(
        "Draw a shape annotation on a PDF page: rect / oval / line / arrow "
        "(arrow points from (x0,y0) to (x1,y1)). Supports stroke color, fill "
        "color (rect/oval), line width, and opacity. " + _COORD_DESC + " "
        "Coordinates outside the page are clamped to page bounds."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute.",
            },
            "page": {
                "type": "integer",
                "description": "Page number (1-based).",
            },
            "shape": {
                "type": "string",
                "enum": ["rect", "oval", "line", "arrow"],
                "description": "Shape type. Default: rect.",
            },
            "x0": {"type": "number", "description": "First corner / start X."},
            "y0": {"type": "number", "description": "First corner / start Y."},
            "x1": {"type": "number", "description": "Second corner / end X."},
            "y1": {"type": "number", "description": "Second corner / end Y."},
            "color": {
                "type": "string",
                "description": _COLOR_DESC + " Stroke color. Default: red.",
            },
            "fill": {
                "type": "string",
                "description": _COLOR_DESC + " Fill color (rect/oval only). Default: none.",
            },
            "width": {
                "type": "number",
                "description": "Line width in points. Default: 1.5.",
            },
            "opacity": {
                "type": "number",
                "description": "Opacity 0-1. Default: fully opaque.",
            },
            "author": {
                "type": "string",
                "description": "Optional annotation author name.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: save to this path instead of modifying in place.",
            },
        },
        "required": ["pdf_path", "page", "x0", "y0", "x1", "y1"],
    },
    handler=pdf_draw,
    group="pdf",
)

registry.register(
    name="pdf_list_annotations",
    description=(
        "List existing annotations in a PDF (highlights, notes, shapes, etc.) "
        "with page, type, rect, author, content and colors. Use it to verify "
        "annotation results."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute.",
            },
            "page": {
                "type": "integer",
                "description": "Only list one page (1-based). Default: all pages.",
            },
        },
        "required": ["pdf_path"],
    },
    handler=pdf_list_annotations,
    group="pdf",
)

registry.register(
    name="pdf_delete_annotations",
    description=(
        "Delete annotations from a PDF, filtered by page and/or annotation type "
        "(e.g. Highlight, Underline, StrikeOut, Squiggly, Text, FreeText, "
        "Square, Circle, Line). Use it to undo wrong annotations."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute.",
            },
            "page": {
                "type": "integer",
                "description": "Only delete on one page (1-based). Default: all pages.",
            },
            "annot_type": {
                "type": "string",
                "description": (
                    "Only delete this annotation type (case-insensitive), e.g. "
                    "'Highlight'. Default: delete all annotation types."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Optional: save to this path instead of modifying in place.",
            },
        },
        "required": ["pdf_path"],
    },
    handler=pdf_delete_annotations,
    group="pdf",
)
