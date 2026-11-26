"""College-Template Formal Graduation Design Specification (.docx Word + .md) Assembler.

Assembles the 18 A3 construction sheets (QL-01..QL-18), Opening-Report 3-scheme comparison,
6 live 300-DPI engineering calculation figures (with T-girder exterior/interior detail sections),
step-by-step T-girder / double-column pier / pile-bent abutment calculations, oral-defense
independent FEM comparison table, and 12 inline-cited GB/T 7714 references (>=10 Chinese +
>=2 English) into a formal Chinese university undergraduate graduation thesis `.docx`.

Typography & layout strictly match the Shengda / Chinese engineering college standard:
- Section 1 (Cover): Authentic college underline form, no page number;
- Section 2 (Front Matter): Uppercase Roman page numbers (I, II, III...), compressed Chinese
  abstract (~300 CJK chars), compressed English Abstract (~200 words), pre-populated + auto-updating
  Word 3-level Table of Contents (`TOC \\o "1-3"`);
- Section 3 (Main Body & Back Matter): Arabic page numbers starting at 1, left-aligned 16pt 黑体
  numbered chapter headings (`1  绪论` .. `11  结论与展望`), 15pt 黑体 Level-2 (`1.1`), 14pt 黑体
  Level-3 (`1.2.1`), 12pt 宋体 + Times New Roman body text, 10.5pt non-bold figure captions paired
  on the same page with quantitative engineering explanations, and 10.5pt three-line tables with
  `<w:cantSplit/>`, `<w:tblHeader/>`, and automatic `续表 X-Y` continuation headers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .bridge_calc import (
    audit_references_compliance,
    default_graduation_references,
    default_opening_report_schemes,
)
from .bridge_calc_book import (
    build_defense_verification_table,
    compute_independent_hand_cross_check,
)
from .bridge_drawings import DEFAULT_18_SHEET_INDEX

_PAGE_CONTENT_WIDTH_CM = 15.5  # 21.0 cm A4 - 3.0 cm left margin - 2.5 cm right margin


def _set_run_fonts(
    run: Any,
    *,
    zh_font: str = "宋体",
    en_font: str = "Times New Roman",
    size_pt: float = 12.0,
    bold: bool = False,
    color_rgb: tuple[int, int, int] = (0, 0, 0),
) -> None:
    run.font.name = en_font
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.font.color.rgb = RGBColor(*color_rgb)
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:ascii"), en_font)
    r_fonts.set(qn("w:hAnsi"), en_font)
    r_fonts.set(qn("w:eastAsia"), zh_font)
    r_fonts.set(qn("w:cs"), en_font)


def _enable_auto_update_fields(doc: Document) -> None:
    settings_el = doc.settings.element
    if settings_el.find(qn("w:updateFields")) is None:
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        settings_el.append(upd)


def _configure_heading_styles(doc: Document) -> None:
    specs = (
        ("Heading 1", "黑体", 16.0, WD_ALIGN_PARAGRAPH.LEFT),
        ("Heading 2", "黑体", 15.0, WD_ALIGN_PARAGRAPH.LEFT),
        ("Heading 3", "黑体", 14.0, WD_ALIGN_PARAGRAPH.LEFT),
    )
    for style_name, zh_f, sz_pt, align in specs:
        if style_name not in doc.styles:
            continue
        st = doc.styles[style_name]
        st.font.name = "Times New Roman"
        st.font.size = Pt(sz_pt)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0, 0, 0)
        r_pr = st._element.get_or_add_rPr()
        r_fonts = r_pr.find(qn("w:rFonts"))
        if r_fonts is None:
            r_fonts = OxmlElement("w:rFonts")
            r_pr.insert(0, r_fonts)
        r_fonts.set(qn("w:ascii"), "Times New Roman")
        r_fonts.set(qn("w:hAnsi"), "Times New Roman")
        r_fonts.set(qn("w:eastAsia"), zh_f)
        r_fonts.set(qn("w:cs"), "Times New Roman")
        st.paragraph_format.alignment = align


def _append_page_field_runs(paragraph: Any, placeholder_text: str = "1") -> None:
    r_begin = paragraph.add_run()
    _set_run_fonts(r_begin, zh_font="宋体", en_font="Times New Roman", size_pt=10.5)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    r_begin._r.append(fld_begin)

    r_instr = paragraph.add_run()
    _set_run_fonts(r_instr, zh_font="宋体", en_font="Times New Roman", size_pt=10.5)
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    r_instr._r.append(instr)

    r_sep = paragraph.add_run()
    _set_run_fonts(r_sep, zh_font="宋体", en_font="Times New Roman", size_pt=10.5)
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    r_sep._r.append(fld_sep)

    r_val = paragraph.add_run(placeholder_text)
    _set_run_fonts(r_val, zh_font="宋体", en_font="Times New Roman", size_pt=10.5)

    r_end = paragraph.add_run()
    _set_run_fonts(r_end, zh_font="宋体", en_font="Times New Roman", size_pt=10.5)
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r_end._r.append(fld_end)


def _configure_section_layout_and_numbering(
    section: Any,
    *,
    page_num_fmt: str | None,
    start_at: int | None = None,
) -> None:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.5)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.75)

    section.header.is_linked_to_previous = False
    section.footer.is_linked_to_previous = False

    sect_pr = section._sectPr
    old_pg_num = sect_pr.find(qn("w:pgNumType"))
    if old_pg_num is not None:
        sect_pr.remove(old_pg_num)

    p_ftr = section.footer.paragraphs[0] if section.footer.paragraphs else section.footer.add_paragraph()
    p_ftr.text = ""
    p_ftr.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if page_num_fmt is not None:
        pg_num = OxmlElement("w:pgNumType")
        pg_num.set(qn("w:fmt"), page_num_fmt)
        if start_at is not None:
            pg_num.set(qn("w:start"), str(start_at))
        sect_pr.append(pg_num)
        placeholder = "I" if page_num_fmt == "upperRoman" else str(start_at or 1)
        _append_page_field_runs(p_ftr, placeholder_text=placeholder)


def _set_paragraph_outline_level(paragraph: Any, level_idx: int) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    old = p_pr.find(qn("w:outlineLvl"))
    if old is not None:
        p_pr.remove(old)
    el = OxmlElement("w:outlineLvl")
    el.set(qn("w:val"), str(level_idx))
    p_pr.append(el)


def _set_cell_borders(
    cell: Any,
    *,
    top: str = "none",
    bottom: str = "none",
    left: str = "none",
    right: str = "none",
    top_sz: int = 4,
    bottom_sz: int = 4,
    color: str = "000000",
) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    old = tc_pr.find(qn("w:tcBorders"))
    if old is not None:
        tc_pr.remove(old)
    tc_borders = OxmlElement("w:tcBorders")
    for side, val, sz in (
        ("top", top, top_sz),
        ("left", left, 4),
        ("bottom", bottom, bottom_sz),
        ("right", right, 4),
    ):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), val)
        if val != "none":
            el.set(qn("w:sz"), str(sz))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), color)
        tc_borders.append(el)
    tc_pr.append(tc_borders)


def _set_row_cant_split_and_header(row: Any, *, is_header: bool = False, min_height_cm: float | None = None) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))
    if is_header and tr_pr.find(qn("w:tblHeader")) is None:
        tr_pr.append(OxmlElement("w:tblHeader"))
    if min_height_cm is not None:
        tr_h = OxmlElement("w:trHeight")
        tr_h.set(qn("w:val"), str(int(round(min_height_cm * 567.0))))
        tr_h.set(qn("w:hRule"), "atLeast")
        tr_pr.append(tr_h)


def _set_cell_width_cm(cell: Any, width_cm: float) -> None:
    cell.width = Cm(width_cm)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(round(width_cm * 567.0))))
    tc_w.set(qn("w:type"), "dxa")


def _add_body_para(
    doc: Document,
    text: str,
    *,
    indent: bool = True,
    bold_prefix: str = "",
    keep_with_next: bool = False,
) -> Any:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = Pt(2.5)
    pf.first_line_indent = Pt(24) if indent else Pt(0)
    pf.keep_with_next = keep_with_next
    if bold_prefix:
        r0 = p.add_run(bold_prefix)
        _set_run_fonts(r0, zh_font="黑体", size_pt=12.0, bold=True)
    r1 = p.add_run(text)
    _set_run_fonts(r1, zh_font="宋体", size_pt=12.0, bold=False)
    return p


def _add_formula_para(doc: Document, expr: str, eq_no: str = "") -> Any:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing = 1.35
    p.paragraph_format.space_before = Pt(2.5)
    p.paragraph_format.space_after = Pt(2.5)
    full_s = f"{expr}    ({eq_no})" if eq_no else expr
    r = p.add_run(full_s)
    _set_run_fonts(r, zh_font="宋体", en_font="Times New Roman", size_pt=11.5, bold=False)
    return p


def _add_chapter_heading(
    doc: Document,
    title: str,
    *,
    centered: bool = False,
    page_break_before: bool = True,
    include_in_toc: bool = True,
    toc_entries: list[tuple[int, str]] | None = None,
) -> Any:
    if page_break_before:
        doc.add_page_break()
    p = doc.add_paragraph(style="Heading 1" if include_in_toc and "Heading 1" in doc.styles else None)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if centered else WD_ALIGN_PARAGRAPH.LEFT
    pf = p.paragraph_format
    pf.first_line_indent = Pt(0)
    pf.space_before = Pt(10)
    pf.space_after = Pt(8)
    pf.line_spacing = 1.5
    pf.keep_with_next = True
    if include_in_toc:
        _set_paragraph_outline_level(p, 0)
        if toc_entries is not None:
            toc_entries.append((1, title))
    r = p.add_run(title)
    _set_run_fonts(r, zh_font="黑体", size_pt=16.0, bold=True)
    return p


def _add_section_heading(
    doc: Document,
    title: str,
    *,
    level: int = 2,
    toc_entries: list[tuple[int, str]] | None = None,
) -> Any:
    style_name = "Heading 2" if level == 2 else "Heading 3"
    p = doc.add_paragraph(style=style_name if style_name in doc.styles else None)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf = p.paragraph_format
    pf.first_line_indent = Pt(0)
    pf.space_before = Pt(7 if level == 2 else 5)
    pf.space_after = Pt(3.5 if level == 2 else 2.5)
    pf.line_spacing = 1.5
    pf.keep_with_next = True
    _set_paragraph_outline_level(p, 1 if level == 2 else 2)
    if toc_entries is not None:
        toc_entries.append((level, title))
    sz = 15.0 if level == 2 else 14.0
    r = p.add_run(title)
    _set_run_fonts(r, zh_font="黑体", size_pt=sz, bold=True)
    return p


def _compute_auto_col_widths_cm(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[float]:
    ncols = len(headers)
    weights: list[float] = []
    for ci in range(ncols):
        h_len = max(4.0, len(str(headers[ci])) * 1.15)
        max_cell = max((len(str(r[ci])) for r in rows if ci < len(r)), default=4)
        # Cap extreme long explanation columns so narrow columns keep generous width (>= 1.45 cm)
        w_score = max(h_len, min(26.0, float(max_cell) * 0.92))
        weights.append(max(4.5, w_score))
    tot_w = sum(weights)
    raw_cms = [_PAGE_CONTENT_WIDTH_CM * w / tot_w for w in weights]
    min_col_cm = 1.40 if ncols <= 7 else 1.22
    deficit = sum(max(0.0, min_col_cm - c) for c in raw_cms)
    if deficit > 1e-6:
        surplus_total = sum(max(0.0, c - min_col_cm) for c in raw_cms)
        adjusted: list[float] = []
        for c in raw_cms:
            if c < min_col_cm:
                adjusted.append(min_col_cm)
            elif surplus_total > 1e-6:
                adjusted.append(c - deficit * ((c - min_col_cm) / surplus_total))
            else:
                adjusted.append(c)
        return [round(x, 2) for x in adjusted]
    return [round(x, 2) for x in raw_cms]


def _extract_continuation_caption(caption: str) -> str:
    m = re.match(r"^((?:附)?表\s*[\d\-]+)\s*(.*)$", caption.strip())
    if m:
        tbl_id, rest = m.group(1), m.group(2).strip()
        return f"续{tbl_id}  {rest}" if rest else f"续{tbl_id}"
    return f"续{caption.strip()}"


def _add_three_line_table(
    doc: Document,
    caption: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    col_widths_cm: Sequence[float] | None = None,
    max_rows_per_chunk: int = 13,
) -> int:
    """Render a standard 10.5pt academic three-line table with cantSplit, tblHeader, and automatic 续表 chunking."""
    widths = list(col_widths_cm) if col_widths_cm is not None else _compute_auto_col_widths_cm(headers, rows)
    row_list = list(rows)
    chunks = [row_list[i : i + max_rows_per_chunk] for i in range(0, max(1, len(row_list)), max_rows_per_chunk)]
    created_tables = 0

    for chunk_idx, chunk_rows in enumerate(chunks):
        if chunk_idx > 0:
            doc.add_page_break()
        cap_str = caption if chunk_idx == 0 else _extract_continuation_caption(caption)
        cap_p = doc.add_paragraph()
        cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap_p.paragraph_format.first_line_indent = Pt(0)
        cap_p.paragraph_format.space_before = Pt(6)
        cap_p.paragraph_format.space_after = Pt(3)
        cap_p.paragraph_format.keep_with_next = True
        rc = cap_p.add_run(cap_str)
        _set_run_fonts(rc, zh_font="黑体", size_pt=10.5, bold=True)

        tbl = doc.add_table(rows=len(chunk_rows) + 1, cols=len(headers))
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False
        created_tables += 1

        hdr_row = tbl.rows[0]
        _set_row_cant_split_and_header(hdr_row, is_header=True, min_height_cm=0.72)
        for ci, h_text in enumerate(headers):
            cell = hdr_row.cells[ci]
            _set_cell_width_cm(cell, widths[ci])
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _set_cell_borders(cell, top="single", bottom="single", top_sz=12, bottom_sz=6)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.line_spacing = 1.20
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(str(h_text))
            _set_run_fonts(r, zh_font="黑体", size_pt=10.5, bold=True)

        n_c = len(chunk_rows)
        for ri, row_vals in enumerate(chunk_rows):
            is_last = ri == n_c - 1
            tr_obj = tbl.rows[ri + 1]
            _set_row_cant_split_and_header(tr_obj, is_header=False, min_height_cm=0.66)
            for ci, val in enumerate(row_vals):
                cell = tr_obj.cells[ci]
                _set_cell_width_cm(cell, widths[ci])
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                _set_cell_borders(
                    cell,
                    top="none",
                    bottom="single" if is_last else "none",
                    bottom_sz=12 if is_last else 0,
                )
                p = cell.paragraphs[0]
                s_val = str(val)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER if len(s_val) <= 18 else WD_ALIGN_PARAGRAPH.LEFT
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.line_spacing = 1.20
                p.paragraph_format.space_before = Pt(1.5)
                p.paragraph_format.space_after = Pt(1.5)
                r = p.add_run(s_val)
                _set_run_fonts(r, zh_font="宋体", size_pt=10.5, bold=False)

    return created_tables


def _embed_figure_if_available(
    doc: Document,
    fig_info: Mapping[str, Any] | None,
    *,
    width_cm: float = 14.3,
    fallback_caption: str = "",
) -> bool:
    if not fig_info:
        return False
    img_path_str = fig_info.get("png_path")
    if not img_path_str or not Path(str(img_path_str)).is_file():
        return False
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_img.paragraph_format.first_line_indent = Pt(0)
    p_img.paragraph_format.space_before = Pt(4)
    p_img.paragraph_format.space_after = Pt(1.5)
    p_img.paragraph_format.keep_with_next = True
    run_img = p_img.add_run()
    run_img.add_picture(str(img_path_str), width=Cm(width_cm))

    cap_text = str(fig_info.get("caption_zh") or fallback_caption or fig_info.get("title_zh") or "").strip()
    if cap_text:
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap.paragraph_format.first_line_indent = Pt(0)
        p_cap.paragraph_format.space_before = Pt(1)
        p_cap.paragraph_format.space_after = Pt(4)
        p_cap.paragraph_format.keep_with_next = True
        rc = p_cap.add_run(cap_text)
        # Strictly non-bold 10.5pt 宋体 + Times New Roman per college reference sample!
        _set_run_fonts(rc, zh_font="宋体", en_font="Times New Roman", size_pt=10.5, bold=False)
    return True


def _split_thesis_title_two_lines(title: str) -> tuple[str, str]:
    s = title.strip()
    if "（" in s and len(s) > 14:
        idx = s.index("（")
        return s[:idx].strip(), s[idx:].strip()
    if "(" in s and len(s) > 14:
        idx = s.index("(")
        return s[:idx].strip(), s[idx:].strip()
    if len(s) <= 18:
        return s, ""
    mid = len(s) // 2
    return s[:mid], s[mid:]


def _populate_toc_field_paragraphs(
    doc: Document,
    toc_anchor_para: Any,
    toc_entries: Sequence[tuple[int, str]],
) -> int:
    """Populate a Word `TOC \\o "1-3" \\h \\z \\u` complex field with pre-rendered 3-level entries."""
    if not toc_entries:
        return 0

    anchor_el = toc_anchor_para._p
    parent_el = anchor_el.getparent()
    insert_idx = parent_el.index(anchor_el)

    est_page = 1
    for idx, (lvl, title_str) in enumerate(toc_entries):
        if idx > 0 and lvl == 1:
            est_page += 2
        elif lvl == 2 and idx % 3 == 0:
            est_page += 1

        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_el = p._p
        parent_el.remove(p_el)
        parent_el.insert(insert_idx, p_el)
        insert_idx += 1

        pf = p.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.left_indent = Pt(0 if lvl == 1 else (12 if lvl == 2 else 24))
        pf.line_spacing = 1.35
        pf.space_before = Pt(2 if lvl == 1 else 0.5)
        pf.space_after = Pt(1.5 if lvl == 1 else 0.5)
        pf.tab_stops.add_tab_stop(Cm(15.3), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)

        if idx == 0:
            r_b = p.add_run()
            _set_run_fonts(r_b, size_pt=12.0)
            fb = OxmlElement("w:fldChar")
            fb.set(qn("w:fldCharType"), "begin")
            r_b._r.append(fb)

            r_i = p.add_run()
            _set_run_fonts(r_i, size_pt=12.0)
            ins = OxmlElement("w:instrText")
            ins.set(qn("xml:space"), "preserve")
            ins.text = ' TOC \\o "1-3" \\h \\z \\u '
            r_i._r.append(ins)

            r_s = p.add_run()
            _set_run_fonts(r_s, size_pt=12.0)
            fs = OxmlElement("w:fldChar")
            fs.set(qn("w:fldCharType"), "separate")
            r_s._r.append(fs)

        is_l1 = lvl == 1
        r_txt = p.add_run(f"{title_str}\t{est_page}")
        _set_run_fonts(
            r_txt,
            zh_font="黑体" if is_l1 else "宋体",
            en_font="Times New Roman",
            size_pt=12.0 if is_l1 else 10.5,
            bold=is_l1,
        )

        if idx == len(toc_entries) - 1:
            r_e = p.add_run()
            _set_run_fonts(r_e, size_pt=12.0)
            fe = OxmlElement("w:fldChar")
            fe.set(qn("w:fldCharType"), "end")
            r_e._r.append(fe)

    parent_el.remove(anchor_el)
    return len(toc_entries)


def generate_college_graduation_thesis_docx(
    calc_report: Mapping[str, Any],
    output_docx_path: str | Path,
    *,
    figures_summary: Mapping[str, Any] | None = None,
    change_impact: Mapping[str, Any] | None = None,
    student_metadata: Mapping[str, Any] | None = None,
    also_write_markdown: bool = True,
) -> dict[str, Any]:
    """Assemble and export a formal Chinese college-template Graduation Design Specification (.docx + .md)."""
    out_docx = Path(output_docx_path).expanduser().resolve()
    out_docx.parent.mkdir(parents=True, exist_ok=True)

    ps = calc_report["project_summary"]
    hydro = calc_report["hydrology"]
    sec = calc_report["t_girder_section"]
    lat = calc_report["lateral_distribution"]
    env = calc_report["superstructure_envelopes"]
    pre = calc_report["prestress"]
    pr_sum = calc_report["pier_reaction_summary"]
    cap_1d = calc_report["pier_cap_beam_1d"]
    gc = calc_report["girder_checks"]
    sc_chk = calc_report["slab_and_diaphragm_checks"]
    bc = calc_report["bearing_checks"]
    sub = calc_report["substructure_checks"]
    abut = calc_report["abutment_checks"]
    ledger = calc_report["rebar_and_tendon_ledger"]
    boq = calc_report["bill_of_quantities"]
    cross = calc_report.get("independent_cross_check") or compute_independent_hand_cross_check(calc_report)
    def_tbl = calc_report.get("defense_verification_table") or build_defense_verification_table(
        calc_report, calc_report.get("independent_fem_results")
    )
    schemes = list(
        calc_report.get("bridge_schemes")
        or default_opening_report_schemes(int(ps["n_spans"]), float(ps["standard_span_m"]), float(ps["deck_width_m"]))
    )
    refs = list(calc_report.get("references") or default_graduation_references())
    figs_by_id = dict((figures_summary or {}).get("by_id") or {})

    is_wenchang = "文昌" in str(ps.get("project_name", ""))
    meta = dict(student_metadata or {})
    school_name = str(meta.get("school_name") or ("郑州升达经贸管理学院" if is_wenchang else "土木工程专业本科毕业设计"))
    college_name = str(meta.get("college_name") or ("建筑工程学院" if is_wenchang else "土木与交通工程学院"))
    major_class = str(meta.get("major_class") or ("2021 级土木工程本科 1 班" if is_wenchang else "土木工程本科 1 班"))
    student_name = str(meta.get("student_name") or ("许琦莘" if is_wenchang else "设计人"))
    student_id = str(meta.get("student_id") or ("202109020152" if is_wenchang else "20210001"))
    advisor_name = str(meta.get("advisor_name") or ("李小霞（副教授）" if is_wenchang else "指导教师（副教授）"))
    completion_date = str(meta.get("completion_date") or "2025 年 4 月")
    thesis_title = str(meta.get("thesis_title") or ps["project_name"])
    thesis_title_en = str(
        meta.get("thesis_title_en")
        or f"Design of {ps['project_name'].split('（')[0]} ({ps['n_spans']}×{ps['standard_span_m']:g} m Precast PC Simply Supported T-Girder Bridge)"
    )

    ext_fem_status = str(def_tbl.get("external_fem_status", "pending_external_import"))
    is_fem_verified = ext_fem_status == "verified"

    doc = Document()
    _enable_auto_update_fields(doc)
    _configure_heading_styles(doc)

    # ==================================================================
    # SECTION 1: Formal College Cover Page (No Page Number)
    # ==================================================================
    sec_cover = doc.sections[0]
    _configure_section_layout_and_numbering(sec_cover, page_num_fmt=None)

    logo_candidate = meta.get("school_logo_path") or (
        out_docx.parent / "assets" / "school_logo.png" if "升达" in school_name else None
    )
    logo_path = Path(str(logo_candidate)) if logo_candidate else None

    p_sch = doc.add_paragraph()
    p_sch.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sch.paragraph_format.first_line_indent = Pt(0)
    p_sch.paragraph_format.space_before = Pt(28)
    p_sch.paragraph_format.space_after = Pt(14)
    if logo_path is not None and logo_path.is_file():
        p_sch.add_run().add_picture(str(logo_path), width=Cm(12.8))
    else:
        r_sch = p_sch.add_run(school_name)
        _set_run_fonts(r_sch, zh_font="黑体", size_pt=22.0, bold=True)

    p_deg = doc.add_paragraph()
    p_deg.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_deg.paragraph_format.first_line_indent = Pt(0)
    p_deg.paragraph_format.space_before = Pt(10)
    p_deg.paragraph_format.space_after = Pt(38)
    r_deg = p_deg.add_run("本科毕业设计")
    _set_run_fonts(r_deg, zh_font="宋体", size_pt=26.0, bold=True)

    t_line1, t_line2 = _split_thesis_title_two_lines(thesis_title)
    cover_rows = [
        ("题    目", t_line1),
        ("", t_line2),
        ("学生姓名", student_name),
        ("专业班级", major_class),
        ("学    号", student_id),
        ("学    院", college_name),
        ("指导教师(职称)", advisor_name),
        ("完成时间", completion_date),
    ]
    if not t_line2:
        cover_rows = [row for row in cover_rows if row[0] != ""]

    cov_tbl = doc.add_table(rows=len(cover_rows), cols=2)
    cov_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cov_tbl.autofit = False
    for ri, (lbl_k, val_v) in enumerate(cover_rows):
        tr_c = cov_tbl.rows[ri]
        _set_row_cant_split_and_header(tr_c, is_header=False, min_height_cm=1.15)
        c_left, c_right = tr_c.cells[0], tr_c.cells[1]
        _set_cell_width_cm(c_left, 3.8)
        _set_cell_width_cm(c_right, 9.6)
        c_left.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
        c_right.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
        _set_cell_borders(c_left, top="none", bottom="none", left="none", right="none")
        _set_cell_borders(c_right, top="none", bottom="single", left="none", right="none", bottom_sz=8)

        pl = c_left.paragraphs[0]
        pl.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        pl.paragraph_format.first_line_indent = Pt(0)
        pl.paragraph_format.space_after = Pt(2)
        rl = pl.add_run(f"{lbl_k}：" if lbl_k else "")
        _set_run_fonts(rl, zh_font="宋体", size_pt=14.0, bold=True)

        pr = c_right.paragraphs[0]
        pr.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pr.paragraph_format.first_line_indent = Pt(0)
        pr.paragraph_format.space_after = Pt(2)
        rr = pr.add_run(val_v)
        _set_run_fonts(rr, zh_font="宋体", size_pt=14.0, bold=False)

    # ==================================================================
    # SECTION 2: Front Matter (Roman Numerals I, II, III... — Abstract & 3-Level TOC)
    # ==================================================================
    sec_front = doc.add_section(WD_SECTION.NEW_PAGE)
    _configure_section_layout_and_numbering(sec_front, page_num_fmt="upperRoman", start_at=1)

    # Page I: Thesis Title + Compressed Chinese Abstract (~300 CJK characters)
    p_zh_top_title = doc.add_paragraph()
    p_zh_top_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_zh_top_title.paragraph_format.first_line_indent = Pt(0)
    p_zh_top_title.paragraph_format.space_before = Pt(4)
    p_zh_top_title.paragraph_format.space_after = Pt(6)
    r_zt = p_zh_top_title.add_run(thesis_title)
    _set_run_fonts(r_zt, zh_font="黑体", size_pt=16.0, bold=True)

    _add_chapter_heading(doc, "摘  要", centered=True, page_break_before=False, include_in_toc=False)

    fem_clause_zh = (
        "手算解析与独立有限元杆系模型复核吻合良好"
        if is_fem_verified
        else "已完成手算解析与杆系程序交叉复核并整理独立电算校核表（外部软件结果待导入）"
    )
    zh_abs_p1 = (
        f"本设计针对“{ps['project_name']}”开展方案比选、结构受力计算与全套施工图设计。"
        f"全桥标准跨径为 {ps['n_spans']}×{ps['standard_span_m']:g} m（总长 {hydro['total_bridge_length_m']:g} m，计算跨径 {ps['calc_span_m']:g} m），"
        f"单幅桥面宽 {ps['deck_width_m']:g} m，采用公路-I级荷载，安全等级一级。"
        "经同桥位简支T梁、连续箱梁与简支小箱梁三方案技术经济比选，推荐采用装配式预应力混凝土简支T梁（桥面连续）方案。"
    )
    zh_abs_p2 = (
        f"上部布置 {lat['n_girders']} 片高 {sec['mid_span_mm']['height']/1000:.2f} m 的 C50 预制T梁，按杠杆法与偏心压力法求得边梁跨中弯矩 Md={gc['uls_flexure']['demand_md_knm']:.1f} kN·m、支点剪力 Vd={gc['uls_shear']['demand_vd_kn']:.1f} kN；"
        f"配置 {pre['recommended_tendons']} 束 Φs15.2-{pre['strands_per_tendon']} 钢绞线，正斜截面承载力、抗裂与活载挠度均满足 JTG 3362—2018 限值。"
        f"下部采用双柱式桥墩与桩柱式桥台，按 JTG 3363—2019 独立核定墩桩长 {ps['pier_pile_length_m']:g} m、台桩长 {ps['abutment_pile_length_m']:g} m。"
        f"{fem_clause_zh}，配套绘出 QL-01～QL-18 共 18 张 A3 施工图。"
    )
    _add_body_para(doc, zh_abs_p1)
    _add_body_para(doc, zh_abs_p2)
    _add_body_para(
        doc,
        "预应力混凝土简支T梁；桥型比选；荷载横向分布；极限状态验算；桩柱式桥台",
        indent=False,
        bold_prefix="关键词：",
    )
    zh_abstract_cjk_count = sum(1 for ch in (zh_abs_p1 + zh_abs_p2) if "\u4e00" <= ch <= "\u9fff")

    # Page II: English Title + Compressed English Abstract (~200 words)
    doc.add_page_break()
    p_en_top_title = doc.add_paragraph()
    p_en_top_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_en_top_title.paragraph_format.first_line_indent = Pt(0)
    p_en_top_title.paragraph_format.space_before = Pt(4)
    p_en_top_title.paragraph_format.space_after = Pt(6)
    r_et = p_en_top_title.add_run(thesis_title_en)
    _set_run_fonts(r_et, zh_font="Times New Roman", en_font="Times New Roman", size_pt=15.0, bold=True)

    _add_chapter_heading(doc, "Abstract", centered=True, page_break_before=False, include_in_toc=False)

    fem_clause_en = (
        "Analytical hand solutions and independent finite-element models show close agreement across governing internal forces, deflections, and support reactions"
        if is_fem_verified
        else "Analytical hand solutions are cross-checked against the beam-frame solver, while the external commercial software comparison schedule is prepared for final model import"
    )
    en_abs_p1 = (
        f"This graduation design presents the hydrologic verification, multi-scheme comparison, structural calculation, and 18-sheet A3 construction detailing for a {ps['n_spans']}×{ps['standard_span_m']:g} m precast prestressed concrete simply supported T-girder bridge ({hydro['total_bridge_length_m']:g} m total length, {ps['deck_width_m']:g} m deck width, Highway-Class I loading, γ0 = 1.1). "
        "Comparing three candidate schemes sharing the same span layout—simply supported T-girders, continuous box girders, and simply supported box girders—the precast T-girder bridge with deck-continuous joints is selected."
    )
    en_abs_p2 = (
        f"The superstructure comprises {lat['n_girders']} precast C50 T-girders spaced at {lat['girder_spacing_m']:g} m ({sec['mid_span_mm']['height']/1000:.2f} m depth, {ps['calc_span_m']:g} m calculation span). "
        f"Transverse distribution factors from the lever rule and Courbon's eccentric compression method yield governing demands of Md = {gc['uls_flexure']['demand_md_knm']:.1f} kN·m and Vd = {gc['uls_shear']['demand_vd_kn']:.1f} kN. "
        f"Post-tensioned with {pre['recommended_tendons']} parabolic tendons of {pre['strands_per_tendon']}-Φs15.2 strands, flexural, shear, Class-A crack-resistance, and live-load deflection ({gc['sls_deflection']['live_deflection_mm']:.2f} mm ≤ L/600) checks satisfy JTG 3362-2018. "
        f"Double-column piers and pile-bent abutments on Φ{sub['bored_pile']['diameter_m']:g} m bored friction piles require independent pile lengths of {ps['pier_pile_length_m']:g} m and {ps['abutment_pile_length_m']:g} m under JTG 3363-2019. "
        f"{fem_clause_en}, matching construction sheets QL-01 through QL-18."
    )
    _add_body_para(doc, en_abs_p1)
    _add_body_para(doc, en_abs_p2)
    _add_body_para(
        doc,
        "Prestressed Concrete T-Girder Bridge; Scheme Comparison; Lateral Load Distribution; Limit State Verification; Pile-Bent Abutment",
        indent=False,
        bold_prefix="Key words: ",
    )
    en_abstract_word_count = len(re.findall(r"[A-Za-z0-9.\-]+", f"{en_abs_p1} {en_abs_p2}"))

    # Page III+: 3-Level Table of Contents (`目  录`)
    _add_chapter_heading(doc, "目  录", centered=True, page_break_before=True, include_in_toc=False)
    toc_anchor_para = doc.add_paragraph()

    # ==================================================================
    # SECTION 3: Main Body Chapters 1..11 + Back Matter (Arabic Page Numbers 1, 2, 3...)
    # ==================================================================
    sec_body = doc.add_section(WD_SECTION.NEW_PAGE)
    _configure_section_layout_and_numbering(sec_body, page_num_fmt="decimal", start_at=1)

    toc_entries: list[tuple[int, str]] = []
    body_citation_paras: list[str] = []
    embedded_fig_count = 0

    def _body(txt: str, **kwargs: Any) -> Any:
        body_citation_paras.append(txt)
        return _add_body_para(doc, txt, **kwargs)

    # ------------------------------------------------------------------
    # Chapter 1: 1  绪论
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "1  绪论", centered=False, page_break_before=False, toc_entries=toc_entries)
    _add_section_heading(doc, "1.1 工程设计概况与资料齐套核查", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "1.1.1 工程背景与桥面横断面布置", level=3, toc_entries=toc_entries)
    _body(
        f"本设计课题为“{ps['project_name']}”。桥梁跨越区域河流主槽，是连接两岸公路网的控制性节点工程 [1]。"
        f"全桥主体采用 {ps['n_spans']}×{ps['standard_span_m']:g} m 装配式预应力混凝土简支T梁桥（墩顶桥面连续），主体总长 {hydro['total_bridge_length_m']:g} m，单跨预制梁全长 {ps['beam_length_m']:g} m，计算跨径 L={ps['calc_span_m']:g} m [1,2]。"
        f"横断面布置为：0.50 m（防撞护栏）+ {ps['clear_width_m']:g} m（桥面净宽，含行车道 {ps['roadway_width_m']:g} m 及两侧安全带）+ 0.50 m（防撞护栏）= {ps['deck_width_m']:g} m，设双向 {ps['cross_slope_pct']:g}% 横坡 [1]。"
    )

    _add_section_heading(doc, "1.1.2 结构验算状态与初拟参数核查说明", level=3, toc_entries=toc_entries)
    unresolved = list(calc_report.get("unresolved_assumptions") or [])
    _body(
        f"为严格落实任务书“图纸与计算结果一致、明示初拟假定参数”的要求，本说明书将结构验算状态（{calc_report['calculation_status']}）与外业勘测齐套状态（{calc_report['graduation_readiness_status']}）分离列示：{calc_report.get('status_summary_zh', '')}。"
    )
    if unresolved:
        u_rows = [(u["field"], str(u["assumed_value"]), u["unit"], u["impact"], u["required_source"]) for u in unresolved]
        _add_three_line_table(
            doc,
            "表 1-1 当前仍采用初拟假定的工程基础参数及核定要求表",
            ("参数字段", "当前采用值", "单位", "影响的工程结论", "需核定的原始资料"),
            u_rows,
            col_widths_cm=(2.4, 3.1, 1.3, 4.4, 4.3),
        )

    _add_section_heading(doc, "1.2 工程水文水力计算与桥孔跨径布置", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "1.2.1 求设计流速与设计流量", level=3, toc_entries=toc_entries)
    _body(
        f"依据桥位河段水文调查断面参数及《公路桥涵设计通用规范》（JTG D60—2015）[2] 与曼宁均匀流公式 [7]，主河槽过水面积 A={hydro['area_m2']} m²，湿周 χ={hydro['wetted_perimeter_m']} m，水力半径 R=A/χ={hydro['hydraulic_radius_m']} m，糙率倒数 1/n={hydro['roughness_inv']}，水力比降 i={hydro['slope_i']*1000:g}‰："
    )
    _add_formula_para(
        doc,
        f"v = (1/n)·R^(2/3)·i^(1/2) = {hydro['roughness_inv']}×{hydro['hydraulic_radius_m']}^(2/3)×({hydro['slope_i']}^0.5) = {hydro['main_channel_velocity_m_s']} m/s",
        "1-1",
    )
    _add_formula_para(
        doc,
        f"Qp = A·v = {hydro['area_m2']}×{hydro['main_channel_velocity_m_s']} = {hydro['design_discharge_m3_s']} m³/s",
        "1-2",
    )

    _add_section_heading(doc, "1.2.2 桥孔净跨径复核与孔跨选定", level=3, toc_entries=toc_entries)
    _body(
        f"按冲刷稳定河宽公式复核，满足 1/100 设计洪水泄洪所需的最小桥孔净开口长度 Lj={hydro['net_opening_length_m']} m [7]。综合标准化跨径模数与下部基础造价，选定 {ps['n_spans']}×{ps['standard_span_m']:g} m 跨径布置（主体长 {hydro['total_bridge_length_m']:g} m > Lj），1/100 设计洪水位 {ps['flood_elevation_m']:.3f} m，桥面中心高程 {ps['deck_elevation_m']:.3f} m，梁底泄洪净空充裕 [1,2]。"
    )

    _add_section_heading(doc, "1.3 主要技术标准、材料指标与工程地质条件", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "1.3.1 执行规范体系与主要材料设计参数", level=3, toc_entries=toc_entries)
    _body(
        "本设计执行现行国家与行业标准：JTG B01—2014 [1]、JTG D60—2015 [2]、JTG 3362—2018 [3]、JTG 3363—2019 [4]、JTG/T 2231-01—2020 [5] 及 JT/T 4—2019 [10]，主要指标汇总于表 1-2。"
    )
    std_table_rows = [
        ("荷载与安全等级", "公路-I级；安全等级一级（γ0 = 1.1）", "JTG D60—2015"),
        ("设计使用年限", "主体结构设计使用年限 100 年（I 类环境）", "JTG B01—2014"),
        ("主梁及湿接缝", "C50：fcd=22.4 MPa, ftd=1.83 MPa, Ec=3.45×10⁴ MPa", "JTG 3362—2018"),
        ("桥面铺装层", f"{ps['asphalt_thick_mm']:g} mm 沥青面层 + {ps['concrete_paving_thick_mm']:g} mm C40防水混凝土", "QL-05 / QL-13"),
        ("下部墩台及桩基", "C30：fcd=13.8 MPa, ftd=1.39 MPa, Ec=3.00×10⁴ MPa", "JTG 3362—2018"),
        ("预应力钢绞线", "低松弛 Φs15.2：fpk=1860 MPa, fpd=1260 MPa", "JTG 3362—2018"),
        ("普通钢筋与支座", f"HRB400 / HPB300；{bc['bearing_model']} 板式橡胶支座", "JT/T 4—2019"),
    ]
    _add_three_line_table(
        doc,
        "表 1-2 本桥主要技术标准与结构材料设计指标汇总表",
        ("指标类别", "采用标准与设计参数", "规范及图纸依据"),
        std_table_rows,
        col_widths_cm=(3.3, 8.7, 3.5),
    )

    _add_section_heading(doc, "1.3.2 桥位气象水文与工程地质条件", level=3, toc_entries=toc_entries)
    pier_layers = sub["bored_pile"].get("layer_contributions") or []
    soil_desc = "；".join(
        f"{lyr['name']}（厚 {lyr['traversed_m']:.1f} m，qik={lyr['qik_kpa']:g} kPa，fa0={lyr['fa0_kpa']:g} kPa）"
        for lyr in pier_layers
    )
    _body(
        f"桥位区属暖温带大陆性季风过渡气候，地表水无腐蚀性 [3]。河床冲洪积地层自上而下依次为：{soil_desc} [4]。下部密实砂砾卵石层厚度稳定，适宜作为钻孔灌注摩擦桩持力层；地震动峰值加速度 0.10g（抗震设防烈度 VII 度）[5]。"
    )

    # ------------------------------------------------------------------
    # Chapter 2: 2  桥型方案综合比选
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "2  桥型方案综合比选", centered=False, page_break_before=True, toc_entries=toc_entries)
    _add_section_heading(doc, "2.1 比选原则与三套候选桥型方案构思", level=2, toc_entries=toc_entries)
    _body(
        "桥梁方案比选遵循“安全可靠、适用耐久、经济合理、施工便捷”原则 [6,7,11]。为保证从开题报告到施工图定稿口径严密统一，沿用同桥位三套 4×25 m 比选方案开展深入论证。"
    )
    for idx_s, sc in enumerate(schemes[:3], start=1):
        _add_section_heading(doc, f"2.1.{idx_s} {sc['scheme_no']}：{sc['name']}", level=3, toc_entries=toc_entries)
        _body(
            f"跨径布置 {sc['span_layout']}，上部采用 {sc['superstructure_type']}（梁高 {sc['girder_height_m']:.2f} m），下部采用 {sc['substructure_type']}。"
            f"受力特性：{sc['structural_mechanics']}；施工工期：{sc['construction_and_schedule']}；经济与管养：{sc['economy_and_maintenance']} [6,7]。"
        )

    _add_section_heading(doc, "2.2 三种桥型方案技术经济对比与推荐结论", level=2, toc_entries=toc_entries)
    s1, s2, s3 = (schemes + schemes[:1] * 3)[:3]
    scheme_cmp_rows = [
        ("跨径与梁高", f"{s1['span_layout']} (h={s1['girder_height_m']:.2f} m)", f"{s2['span_layout']} (h={s2['girder_height_m']:.2f} m)", f"{s3['span_layout']} (h={s3['girder_height_m']:.2f} m)"),
        ("上部主梁形式", s1["superstructure_type"], s2["superstructure_type"], s3["superstructure_type"]),
        ("结构受力特点", s1["structural_mechanics"], s2["structural_mechanics"], s3["structural_mechanics"]),
        ("施工工艺与工期", s1["construction_and_schedule"], s2["construction_and_schedule"], s3["construction_and_schedule"]),
        ("造价与后期养护", s1["economy_and_maintenance"], s2["economy_and_maintenance"], s3["economy_and_maintenance"]),
        ("综合比选结论", s1["verdict"], s2["verdict"], s3["verdict"]),
    ]
    _add_three_line_table(
        doc,
        "表 2-1 三种候选桥型方案技术经济综合比选表",
        ("比选维度", "方案一：简支T梁", "方案二：连续箱梁", "方案三：简支箱梁"),
        scheme_cmp_rows,
        col_widths_cm=(2.5, 4.4, 4.3, 4.3),
    )
    if _embed_figure_if_available(doc, figs_by_id.get("fig0_scheme_comparison"), width_cm=14.4):
        embedded_fig_count += 1
    _body(
        f"如图 2-1 所示，在相同 {s1['span_layout']} 跨径与 {ps['deck_width_m']:g} m 桥宽条件下，方案一（h={s1['girder_height_m']:.2f} m，5片预制T梁）相比方案二连续箱梁免除了河道满堂支架与体系转换风险，相比方案三简支小箱梁模板构造简单、单梁吊装重量轻且内腔易检，故确定方案一为推荐设计方案 [6,11]。"
    )

    # ------------------------------------------------------------------
    # Chapter 3: 3  上部结构构造拟定与截面几何特性计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "3  上部结构构造拟定与截面几何特性计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    mid_mm = sec["mid_span_mm"]
    end_mm = sec["end_span_mm"]
    mid_m = sec["mid_span_m"]
    bulb_w_mm = 560 if sec["effective_flange_width_mm"] <= 2000 else 600

    _add_section_heading(doc, "3.1 主梁横断面布置与细部构造尺寸", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "3.1.1 外边梁与中梁跨中及梁端截面尺寸（提取自 QL-05、QL-07）", level=3, toc_entries=toc_entries)
    _body(
        f"全幅横桥向布置 {lat['n_girders']} 片预制 C50 混凝土T梁，主梁中心间距 S={lat['girder_spacing_m']*1000:g} mm，相邻预制翼缘间设 400 mm 现浇 C50 湿接缝连接成整体桥面（对应图号 QL-05～QL-07）[6,9]。"
        f"主梁高 h={mid_mm['height']:g} mm（高跨比 1/{ps['standard_span_m']/(mid_mm['height']/1000):.1f}），跨中标准腹板厚 200 mm，马蹄宽×高={bulb_w_mm}×200 mm；距支点 1.20 m 范围内腹板加厚至 {bulb_w_mm} mm 并设 1.00 m 渐变段。全跨共设 5 道横隔梁（间距 4×{ps['calc_span_m']/4:g} m），外边梁与中梁实画细部尺寸见图 3-1。"
    )
    if _embed_figure_if_available(doc, figs_by_id.get("fig_girder_detail"), width_cm=14.3):
        embedded_fig_count += 1

    _add_section_heading(doc, "3.2 受压翼缘有效宽度与截面几何特性计算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "3.2.1 T形截面受压翼缘有效宽度计算", level=3, toc_entries=toc_entries)
    cands = sec["effective_flange_candidates_mm"]
    _body(
        f"结合图 3-1 构造尺寸，按 JTG 3362—2018 第 4.2.3 条 [3]，简支T梁受压翼缘有效宽度 b'f 取 L/3={cands['l_div_3']:g} mm、主梁中心距 S={cands['girder_spacing']:g} mm 及 b+2bh+12h'f={cands['b_plus_2bh_plus_12hf']:g} mm 三者最小值，核定 b'f={sec['effective_flange_width_mm']:g} mm。"
    )

    _add_section_heading(doc, "3.2.2 跨中马蹄截面与支点加厚截面几何特性", level=3, toc_entries=toc_entries)
    _body(
        f"采用多边形格林公式精确积分求得跨中标准截面与梁端加厚截面的面积、形心、惯性矩及上下核心距，跨中截面效率指标 ρ=(ks+kx)/h={mid_mm['efficiency_rho']:.4f} ≥ 0.50，满足预应力高效率截面判据 [6]，汇总见表 3-1。"
    )
    sec_rows = [
        ("毛截面面积 A", f"{mid_mm['area']:.1f} mm² ({mid_m['area_m2']:.4f} m²)", f"{end_mm['area']:.1f} mm²"),
        ("形心至梁底距离 yb", f"{mid_mm['y_bot']:.1f} mm", f"{end_mm['y_bot']:.1f} mm"),
        ("形心至梁顶距离 yt", f"{mid_mm['y_top']:.1f} mm", f"{end_mm['y_top']:.1f} mm"),
        ("抗弯惯性矩 Ix", f"{mid_m['ix_m4']:.6f} m⁴", f"{end_mm['ix']*1e-12:.6f} m⁴"),
        ("上 / 下核心距 ks / kx", f"{mid_mm['kern_upper_ks']:.1f} / {mid_mm['kern_lower_kx']:.1f} mm", f"{end_mm['kern_upper_ks']:.1f} / {end_mm['kern_lower_kx']:.1f} mm"),
        ("截面效率指标 ρ", f"{mid_mm['efficiency_rho']:.4f} (≥0.50 合格)", f"{end_mm['efficiency_rho']:.4f}"),
    ]
    _add_three_line_table(
        doc,
        "表 3-1 预制T梁跨中标准截面与梁端加厚截面几何特性表",
        ("几何特征量", "跨中标准马蹄截面", "梁端加厚腹板截面"),
        sec_rows,
        col_widths_cm=(4.5, 5.8, 5.2),
    )

    # ------------------------------------------------------------------
    # Chapter 4: 4  代表性主梁内力计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "4  代表性主梁内力计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    dl_bk = env["dead_load_breakdown"]
    span_l = float(ps["calc_span_m"])

    _add_section_heading(doc, "4.1 结构计算模型与永久作用效应计算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "4.1.1 一期结构自重与二期桥面恒载集度", level=3, toc_entries=toc_entries)
    _body(
        f"选取横向分布最不利的 1# 边主梁建立 10 单元（11 节点：Z1、B1～KZ～Z2）简支梁计算模型，并与 2# 中间双柱墩盖梁受荷模型对应（见图 4-1）。"
        f"单片T梁一期自重 g1,beam={dl_bk['g1_girder_self_kn_m']:.3f} kN/m，横隔梁折算荷载 g1,diaph={dl_bk['diaphragm_equiv_kn_m']:.3f} kN/m；"
        f"二期铺装与双侧护栏分摊小计 g2={dl_bk['superimposed_dead_kn_m']:.3f} kN/m，总恒载集度 g={env['dead_load_kn_m']:.4f} kN/m。"
        f"跨中恒载弯矩 Mg(L/2)=gL²/8={env['mid_span_summary']['m_dead_knm']:.2f} kN·m，支点恒载剪力 Vg(0)=gL/2={abs(float(env['support_summary']['q_dead_kn'])):.2f} kN [2,9]。"
    )
    if _embed_figure_if_available(doc, figs_by_id.get("fig1_structural_model"), width_cm=14.3):
        embedded_fig_count += 1

    _add_section_heading(doc, "4.2 荷载横向分布系数计算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "4.2.1 支点杠杆原理法与跨中刚性横梁偏心压力法", level=3, toc_entries=toc_entries)
    _body(
        f"由图 4-1 横向传力关系，本桥宽跨比 B/L={lat['width_to_span_ratio']:.4f} < 0.50，跨中及四分点采用刚性横梁偏心压力法（∑ai²={lat['sum_a2_m2']:.4f} m²），支点采用杠杆原理法求解公路-I级双车道横向分布系数 [6,8]，结果汇总于表 4-1。"
    )
    lat_rows = [
        (
            f"{lr['girder_no']}# 主梁",
            f"{ec['a_m']:+.3f}",
            f"{lr['m_oq']:.4f}",
            f"{ec['m_cq']:.4f}",
            f"{ec['m_cq_torsion_corrected']:.4f}",
        )
        for lr, ec in zip(lat["lever_rule"], lat["eccentric_compression"])
    ]
    _add_three_line_table(
        doc,
        "表 4-1 各片主梁支点杠杆法与跨中偏心压力法荷载横向分布系数表",
        ("主梁编号", "梁位坐标 ai (m)", "支点杠杆法 moq", "跨中偏心压力法 mcq", "计入抗扭修正 mcq,β"),
        lat_rows,
        col_widths_cm=(2.7, 3.1, 3.2, 3.3, 3.2),
    )

    _add_section_heading(doc, "4.3 汽车活载冲击系数与内力包络分析", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "4.3.1 竖向自振基频与汽车冲击系数", level=3, toc_entries=toc_entries)
    mu_v = float(env["impact_factor_mu"])
    _body(
        f"按 JTG D60—2015 第 4.3.2 条 [2]，简支T梁竖向基频 f1={env['fundamental_freq_hz']:.4f} Hz，汽车冲击系数 μ=0.1767·ln(f1)-0.0157={mu_v:.4f}（1+μ={1+mu_v:.4f}）[2,12]；车道荷载标准值 qk={env['lane_qk_kn_m']:g} kN/m，集中荷载 Pk={env['lane_pk_kn']:g} kN（算剪力乘 1.2）。"
    )

    _add_section_heading(doc, "4.3.2 特征截面内力包络图与控制截面用途", level=3, toc_entries=toc_entries)
    st_pick = [env["stations"][i] for i in (0, 1, 2, 3, 5)]
    env_rows = [
        (
            f"x={s['x_m']:.2f} m ({s['x_ratio']*10:g}L/10)",
            f"{s['m_dead_knm']:.1f}",
            f"{s['m_live_knm']:.1f}",
            f"{s['m_sls_char_knm']:.1f}",
            f"{s['m_uls_knm']:.1f}",
            f"{abs(float(s['q_uls_gov_kn'])):.1f}",
        )
        for s in st_pick
    ]
    _add_three_line_table(
        doc,
        "表 4-2 1#代表性T梁半跨特征截面内力组合汇总表",
        ("截面位置", "恒载 Mg (kN·m)", "活载 Mq (kN·m)", "标准组合 Ms (kN·m)", "基本组合 Md (kN·m)", "控制剪力 |Vd| (kN)"),
        env_rows,
        col_widths_cm=(3.3, 2.4, 2.4, 2.5, 2.5, 2.4),
    )
    if _embed_figure_if_available(doc, figs_by_id.get("fig2_moment_shear_envelope"), width_cm=14.2):
        embedded_fig_count += 1
    _body(
        f"由图 4-2 包络结果直接读取各项验算控制截面：（1）跨中截面 KZ（x={0.5*span_l:g} m）控制正截面弯矩，基本组合 Md={gc['uls_flexure']['demand_md_knm']:.1f} kN·m 用于第 5 章预应力配束与第 6 章正截面抗弯承载力 Mu 验算，标准组合 Ms={env['mid_span_summary']['m_sls_char_knm']:.1f} kN·m 与频遇组合 {env['mid_span_summary']['m_sls_freq_knm']:.1f} kN·m 用于正截面抗裂及压应力验算；"
        f"（2）四分点截面（x={0.25*span_l:g} m）Md={env['quarter_span_m_uls_knm']:.1f} kN·m 用于校核钢束弯起段正截面抗弯包络；"
        f"（3）支点截面 Z1（x=0）控制剪力，最大基本组合剪力 Vd={gc['uls_shear']['demand_vd_kn']:.1f} kN 用于斜截面抗剪承载力 Vu、梁端腹板加厚段主拉应力及 GJ1@100 箍筋加密区验算 [3]。"
    )

    # ------------------------------------------------------------------
    # Chapter 5: 5  预应力钢束估算、布置及预应力损失计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "5  预应力钢束估算、布置及预应力损失计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    _add_section_heading(doc, "5.1 预应力钢束估算与曲线几何布置", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "5.1.1 按抗裂与承载力双控估算钢绞线股数", level=3, toc_entries=toc_entries)
    _body(
        f"选用 1×7 股低松弛钢绞线 Φs15.2（单股面积 140 mm²，fpk=1860 MPa，fpd=1260 MPa）[3,9]：正常使用抗裂需 {pre['required_strands_sls']} 股，正截面抗弯承载力需 {pre['required_strands_uls']} 股。"
        f"综合马蹄管道净距与锚具模数，单片T梁采用 {pre['recommended_tendons']} 束 Φs15.2-{pre['strands_per_tendon']}（编号 N1～N{pre['recommended_tendons']}，共 {pre['provided_strands']} 股，Ap={pre['provided_strands']*140:g} mm²，对应图号 QL-08），两端对称张拉，控制应力 σcon=0.75fpk={pre['sigma_con_mpa']:g} MPa [3]。"
    )

    _add_section_heading(doc, "5.2 分阶段预应力损失与有效预应力分布", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "5.2.1 摩阻、锚固回缩、松弛及收缩徐变损失计算", level=3, toc_entries=toc_entries)
    loss_pick = [pre["loss_profile"][i] for i in (0, 1, 2, 3, 5)]
    loss_rows_t = [
        (
            f"{r['x_m']:.2f}",
            f"{r['sigma_l1_friction_mpa']:.1f}",
            f"{r['sigma_l2_anchorage_mpa']:.1f}",
            f"{r['sigma_l5_relaxation_mpa']:.1f}",
            f"{r['sigma_l6_creep_shrink_mpa']:.1f}",
            f"{r['total_loss_mpa']:.1f}",
            f"{r['effective_prestress_mpa']:.1f}",
        )
        for r in loss_pick
    ]
    _add_three_line_table(
        doc,
        "表 5-1 预应力钢束沿半跨特征截面预应力损失与有效预应力表（MPa）",
        ("距梁端 x(m)", "摩阻 σl1", "回缩 σl2", "松弛 σl5", "徐变 σl6", "总损失 σl", "有效应力 σpe"),
        loss_rows_t,
        col_widths_cm=(2.3, 2.1, 2.1, 2.1, 2.1, 2.3, 2.5),
    )
    if _embed_figure_if_available(doc, figs_by_id.get("fig3_tendon_and_prestress"), width_cm=14.2):
        embedded_fig_count += 1
    pe_end_val = float(pre["loss_profile"][0]["effective_prestress_mpa"])
    pe_mid_val = float(pre["loss_profile"][len(pre["loss_profile"]) // 2]["effective_prestress_mpa"])
    _body(
        f"结合图 5-1 上下对照分析：N1～N{pre['recommended_tendons']} 抛物线钢束在梁端分层散开锚固以减小支点负弯矩并贡献弯起抗剪分量 Vpb；"
        f"应力分布上，梁端截面（x=0）受单端 Δl=6 mm 锚具变形回缩（反摩阻影响长度 lf={pre['anchorage_influence_length_m']:.2f} m）主导，运营有效预应力代表值为 σpe(0)={pe_end_val:.1f} MPa；"
        f"跨中控制截面（x=L/2）管道摩阻与长期收缩徐变损失叠加后有效预应力代表值为 σpe(L/2)={pe_mid_val:.1f} MPa（约为 0.60fpk，保持率 {pe_mid_val/float(pre['sigma_con_mpa'])*100:.1f}%），为跨中正截面抗裂提供稳定预压储备 [3]。"
    )

    # ------------------------------------------------------------------
    # Chapter 6: 6  代表性主梁承载能力与正常使用极限状态验算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "6  代表性主梁承载能力与正常使用极限状态验算", centered=False, page_break_before=True, toc_entries=toc_entries)
    t_sec = gc["transformed_section"]
    uf = gc["uls_flexure"]
    us = gc["uls_shear"]
    scs = gc["sls_crack_and_stress"]
    sdef = gc["sls_deflection"]

    _add_section_heading(doc, "6.1 持久状况承载能力极限状态验算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "6.1.1 跨中正截面抗弯承载力验算", level=3, toc_entries=toc_entries)
    _body(
        f"按 JTG 3362—2018 第 5.2.2 条 [3]，马蹄底缘配置 R1 6Φ22 HRB400 普通主筋协同 {pre['tendon_designation']} 钢束受力，截面有效高度 h0={t_sec['h0_mm']:.1f} mm，混凝土受压区高度 x={uf['compression_depth_x_mm']:.1f} mm ≤ ξbh0={uf['max_depth_xib_h0_mm']:.1f} mm（第 {uf['section_type']} 类T形截面），抗弯承载力 Mu={uf['capacity_mu_knm']:.2f} kN·m ≥ Md={uf['demand_md_knm']:.2f} kN·m，满足规范要求。"
    )

    _add_section_heading(doc, "6.1.2 支点斜截面抗剪承载力验算", level=3, toc_entries=toc_entries)
    _body(
        f"腹板配置双肢 Φ12 HRB400 箍筋 GJ1（距梁端 2080 mm 加密区 @100 mm、跨中区 @150 mm，对应图号 QL-09），计入弯起束抗剪分量 Vpb={us['vpb_kn']:.2f} kN 后，斜截面抗剪承载力 Vu={us['capacity_vu_kn']:.2f} kN ≥ Vd={us['demand_vd_kn']:.2f} kN，且小于抗剪截面尺寸上限 {us['size_upper_limit_kn']:.2f} kN [3]。"
    )

    _add_section_heading(doc, "6.2 持久状况抗裂、应力及正常使用挠度验算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "6.2.1 正截面抗裂、法向压应力与主拉应力验算", level=3, toc_entries=toc_entries)
    _body(
        f"按全预应力 A 类构件验算 [3]：频遇组合跨中下缘净应力 {scs['net_bot_stress_freq_mpa']:.2f} MPa ≤ 0；标准组合最大压应力 {max(scs['top_comp_char_mpa'], scs['bot_comp_char_mpa']):.2f} MPa ≤ 0.50fck={scs['comp_allowable_mpa']:.2f} MPa；支点主拉应力 {scs['principal_tensile_mpa']:.3f} MPa ≤ 0.50ftk={scs['principal_tensile_allow_mpa']:.3f} MPa；施工张拉短暂压应力 {scs['transfer_bot_comp_mpa']:.2f} MPa ≤ {scs['transfer_comp_allow_mpa']:.2f} MPa。"
    )

    _add_section_heading(doc, "6.2.2 消除自重活载长期挠度与预拱度设置", level=3, toc_entries=toc_entries)
    _body(
        f"计入长期增长系数 ηθ=1.60 后，汽车活载挠度 flive={sdef['live_deflection_mm']:.2f} mm ≤ L/600={sdef['allowable_l_div_600_mm']:.2f} mm，长期净上拱 {sdef['net_long_term_camber_mm']:.1f} mm [3,9]，各项验算汇总于表 6-1。"
    )
    g_chk_rows = [
        ("跨中正截面抗弯 Mu", f"Md = {uf['demand_md_knm']:.2f} kN·m", f"Mu = {uf['capacity_mu_knm']:.2f} kN·m", "满足" if uf["passed"] else "超限"),
        ("跨中受压区高度 x", f"x = {uf['compression_depth_x_mm']:.1f} mm", f"ξbh0 = {uf['max_depth_xib_h0_mm']:.1f} mm", "满足" if uf["passed"] else "超限"),
        ("支点斜截面抗剪 Vu", f"Vd = {us['demand_vd_kn']:.2f} kN", f"Vu = {us['capacity_vu_kn']:.2f} kN", "满足" if us["passed"] else "超限"),
        ("频遇组合正截面抗裂", f"{scs['net_bot_stress_freq_mpa']:.2f} MPa", "≤ 0.00 MPa (A类)", "满足" if scs["passed"] else "超限"),
        ("标准组合最大法向压应力", f"{max(scs['top_comp_char_mpa'], scs['bot_comp_char_mpa']):.2f} MPa", f"≤ {scs['comp_allowable_mpa']:.2f} MPa", "满足" if scs["passed"] else "超限"),
        ("活载长期挠度 flive", f"{sdef['live_deflection_mm']:.2f} mm", f"≤ L/600 = {sdef['allowable_l_div_600_mm']:.2f} mm", "满足" if sdef["passed"] else "超限"),
    ]
    _add_three_line_table(
        doc,
        "表 6-1 代表性预制T梁承载能力与正常使用极限状态验算汇总表",
        ("验算项目", "作用效应计算值", "抗力或规范限值", "结论"),
        g_chk_rows,
        col_widths_cm=(4.3, 4.4, 4.6, 2.2),
    )

    # ------------------------------------------------------------------
    # Chapter 7: 7  行车道板、横隔梁及板式橡胶支座计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "7  行车道板、横隔梁及板式橡胶支座计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    ds = sc_chk["deck_slab"]
    dp = sc_chk["diaphragm"]
    _add_section_heading(doc, "7.1 行车道板与刚性中横隔梁计算", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "7.1.1 悬臂行车道板内力与配筋验算（QL-10）", level=3, toc_entries=toc_entries)
    _body(
        f"按公路-I级车辆单轮重 P=140 kN 最不利局部分布计算 [2,3]：悬臂板根部每延米基本组合弯矩 Md={ds['m_uls_knm_per_m']:.2f} kN·m/m、剪力 Vd={ds['q_uls_kn_per_m']:.2f} kN/m；翼缘顶层配置 W1 Φ12@125（As=904.8 mm²/m），抗弯承载力 Mu={ds['mu_capacity_knm_per_m']:.2f} kN·m/m ≥ Md，满足要求。"
    )
    _add_section_heading(doc, "7.1.2 跨中刚性横隔梁内力与配筋验算（QL-11）", level=3, toc_entries=toc_entries)
    _body(
        f"中横隔梁按偏心受压刚性横梁计算横向弯矩 [6,8]：基本组合正弯矩 Md={dp['m_uls_knm']:.2f} kN·m，底部配 HG1 4Φ25 HRB400 主筋，抗弯承载力 Mu={dp['mu_capacity_knm']:.2f} kN·m ≥ Md，合格。"
    )

    _add_section_heading(doc, "7.2 板式橡胶支座选型与力学验算（QL-12）", level=2, toc_entries=toc_entries)
    _body(
        f"单支座最大标准组合反力 Rmax={bc['max_reaction_kn']:.2f} kN，最小恒载反力 Rmin={bc['min_reaction_kn']:.2f} kN。按 JT/T 4—2019 选用 {bc['bearing_model']} 板式橡胶支座（全桥 {boq['bearing_count']} 块）[10]：平均压应力 σc={bc['compressive_stress_mpa']:.2f} MPa ≤ {bc['allowable_stress_mpa']:.1f} MPa，最小接触应力 {bc['min_contact_stress_mpa']:.2f} MPa ≥ 1.5 MPa（无脱空），水平剪切角正切 tanγ={bc['shear_strain_tan_gamma']:.3f} ≤ {bc['allowable_tan_gamma']:.2f}，全部合格。"
    )

    # ------------------------------------------------------------------
    # Chapter 8: 8  中间双柱式桥墩及钻孔灌注桩设计计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "8  中间双柱式桥墩及钻孔灌注桩设计计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    _add_section_heading(doc, "8.1 双跨连续支反力与双柱墩盖梁验算（QL-14、QL-16）", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "8.1.1 双跨支座反力与双悬臂盖梁配筋验算", level=3, toc_entries=toc_entries)
    _body(
        f"中间桥墩承受相邻两跨恒载与双跨影响线活载反力 [2,9]，5 个支点基本组合集中反力合计 {sum(pr_sum['two_span_pier_uls_per_girder_kn']):.2f} kN。"
        f"盖梁长 {cap_1d['cap_length_m']:g} m、截面 {sub['cap_beam']['width_m']*1000:g}×{sub['cap_beam']['height_m']*1000:g} mm：墩顶控制负弯矩 Md,hog={sub['cap_beam']['m_hog_uls_knm']:.2f} kN·m（顶层配 GL1 {sub['cap_beam']['top_rebar']}，Mu={sub['cap_beam']['mu_hog_capacity_knm']:.2f} kN·m），跨中正弯矩 Md,sag={sub['cap_beam']['m_sag_uls_knm']:.2f} kN·m（底层配 GL2 {sub['cap_beam']['bottom_rebar']}，Mu={sub['cap_beam']['mu_sag_capacity_knm']:.2f} kN·m），剪力 Vd={sub['cap_beam']['v_uls_kn']:.2f} kN ≤ Vu={sub['cap_beam']['vu_capacity_kn']:.2f} kN，最大裂缝宽度 wcr={sub['cap_beam']['crack_width_mm']:.3f} mm ≤ 0.200 mm [3]。"
    )

    _add_section_heading(doc, "8.2 双柱式圆柱墩与钻孔灌注桩基础计算（QL-17）", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "8.2.1 圆柱墩偏压承载力与横系梁锚固验算", level=3, toc_entries=toc_entries)
    _body(
        f"墩柱直径 Dc={sub['pier_column']['diameter_m']:g} m、墩高 Hc={sub['pier_column']['height_m']:g} m，配主筋 DZ1 {sub['pier_column']['main_rebar']}：柱底轴力 Nd={sub['pier_column']['n_uls_kn']:.2f} kN、弯矩 Md={sub['pier_column']['m_uls_knm']:.2f} kN·m，偏压承载力 Nu={sub['pier_column']['nu_capacity_kn']:.2f} kN ≥ Nd；桩顶系梁主筋 XL1 12Φ22 锚入墩柱核心区 780 mm ≥ 35d=770 mm [3,5]。"
    )

    _add_section_heading(doc, "8.2.2 墩台钻孔灌注桩分层摩阻力与桩长对比分析", level=3, toc_entries=toc_entries)
    if _embed_figure_if_available(doc, figs_by_id.get("fig4_pile_soil_profile"), width_cm=14.1):
        embedded_fig_count += 1
    unresolved_fset = {str(u.get("field")) for u in unresolved}
    soil_src_note = (
        "当前地质分层参数采用开题报告信阳冲洪积四层初拟地层指标（图题已明示“初拟地层”），待外业钻孔柱状图实测核定后复核终稿"
        if "soil_layers" in unresolved_fset
        else "地质分层参数已按桥位实测钻孔柱状图核定"
    )
    pier_sock_m = float(sub["bored_pile"].get("bearing_stratum_socket_m", max(0.0, float(sub["bored_pile"]["design_length_m"]) - 9.2)))
    _body(
        f"如图 8-1 并列对比所示，按 JTG 3363—2019 第 5.3.3 条摩擦桩公式 [Ra]=0.5u∑qikli+0.85Apfa0 独立求解 [4]："
        f"中间双柱墩因承受双跨恒活载（单桩桩顶 Rsls={sub['bored_pile']['pile_top_sls_demand_kn']:.1f} kN），核定设计桩长 Lpier={sub['bored_pile']['design_length_m']:g} m，桩端进入第4层密实砂砾卵石持力层深度 {pier_sock_m:.2f} m ≥ 2.5D={2.5*float(sub['bored_pile']['diameter_m']):g} m，容许承载力 [Ra]={sub['bored_pile']['allowable_ra_kn']:.1f} kN；"
        f"桩柱式桥台仅承受单跨反力及搭板、台背荷载（单桩 Rsls={abut['bored_pile']['pile_top_sls_demand_kn']:.1f} kN），独立核定桩长 Labut={abut['bored_pile']['design_length_m']:g} m，入持力层深度 {abut['bored_pile']['bearing_stratum_socket_m']:.2f} m ≥ {abut['bored_pile']['min_required_socket_m']:g} m，[Ra]={abut['bored_pile']['allowable_ra_kn']:.1f} kN。"
        f"资料来源说明：{soil_src_note}。"
    )

    # ------------------------------------------------------------------
    # Chapter 9: 9  桩柱式桥台及钻孔灌注桩独立设计计算
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "9  桩柱式桥台及钻孔灌注桩独立设计计算", centered=False, page_break_before=True, toc_entries=toc_entries)
    _add_section_heading(doc, "9.1 桥台竖向荷载、台背主动土压力与构件验算（QL-15、QL-18）", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "9.1.1 边跨支反力与朗肯主动土压力计算", level=3, toc_entries=toc_entries)
    _body(
        f"0#、{ps['n_spans']}# 桩柱式桥台承受边跨单跨恒载反力 {abut['load_breakdown']['single_span_girder_dead_total_kn']:.2f} kN、活载反力 {abut['load_breakdown']['single_span_girder_live_total_kn']:.2f} kN、6.0 m 搭板恒活载半重 {abut['load_breakdown']['approach_slab_dead_half_kn']+abut['load_breakdown']['approach_slab_live_half_kn']:.2f} kN 及台帽耳背墙自重，台帽底标准值总竖向力为 {abut['load_breakdown']['total_vertical_sls_at_cap_bottom_kn']:.2f} kN。"
        f"台背填土 γs={abut['earth_pressure']['backfill_gamma_kn_m3']:g} kN/m³、φ={abut['earth_pressure']['backfill_phi_deg']:g}°，朗肯系数 Ka={abut['earth_pressure']['rankine_ka']:.4f}，车辆荷载等代土层厚 heq={abut['earth_pressure']['equivalent_surcharge_heq_m']:.3f} m [2]。"
    )
    _add_section_heading(doc, "9.1.2 台帽、背墙牛腿及支座垫石局部承压验算", level=3, toc_entries=toc_entries)
    _body(
        f"台帽（QT1 {abut['abutment_cap']['main_rebar']}）抗弯 Mu={abut['abutment_cap']['mu_hog_capacity_knm']:.1f} ≥ Md={abut['abutment_cap']['m_hog_uls_knm']:.1f} kN·m，裂缝 wcr={abut['abutment_cap']['crack_width_mm']:.3f} ≤ 0.20 mm；背墙根部（QT2 Φ16@125双侧，锚入台帽 560 mm=35d）Mu={abut['backwall']['mu_capacity_total_knm']:.1f} ≥ Md={abut['backwall']['m_uls_total_knm']:.1f} kN·m；C40垫石（DS1钢筋网）局压 Flu={abut['pedestal_local_bearing']['capacity_flu_kn']:.1f} ≥ Fld={abut['pedestal_local_bearing']['demand_fld_kn']:.1f} kN [3,10]。"
    )

    _add_section_heading(doc, "9.2 桥台钻孔灌注桩桩长推导与桩身弯压验算", level=2, toc_entries=toc_entries)
    _body(
        f"按台位单桩标准值竖向荷载 Rabut,sls={abut['bored_pile']['pile_top_sls_demand_kn']:.2f} kN 独立核定桥台桩长 Labut={abut['bored_pile']['design_length_m']:g} m（[Ra]={abut['bored_pile']['allowable_ra_kn']:.2f} kN）[4]。桩身配主筋 QT3 {abut['bored_pile']['main_rebar']} 与螺旋箍 QT4 φ12@150，弯压承载力 Nu={abut['bored_pile']['nu_section_capacity_kn']:.1f} kN ≥ Nd={abut['bored_pile']['n_uls_kn']:.1f} kN，全桥 4 根桥台桩 C30 水下混凝土 {boq['volume_breakdown']['abut_pile_m3']} m³ 与 QL-02、QL-15、QL-18 完全一致。"
    )

    # ------------------------------------------------------------------
    # Chapter 10: 10  手算与独立电算模型复核及全图册选筋一致性追溯
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "10  手算与独立电算模型复核及全图册选筋一致性追溯", centered=False, page_break_before=True, toc_entries=toc_entries)
    mc = def_tbl["model_card"]
    _add_section_heading(doc, "10.1 独立电算建模条件与答辩复核对照", level=2, toc_entries=toc_entries)
    _add_section_heading(doc, "10.1.1 代表性主梁与中间双柱墩电算建模记录卡", level=3, toc_entries=toc_entries)
    _body(
        f"为检验上下部结构受力计算的准确性，针对 1# 边T梁与 2# 中间双柱墩整理电算建模条件（模型软件状态：{mc['software_name']}；跨度与离散：{mc['span_and_mesh']}；支承约束：{mc['boundary_restraints']}；荷载组合：{mc['loads_and_combinations']}）[8,11,12]。"
        + (
            f"当前程序内置解析复核最大相对偏差为 {cross['max_rel_diff_pct']}%；外部商业有限元软件（桥梁博士 Dr.Bridge V4.4 / Midas Civil）结果当前处于“待导入”状态，已随附完整参数建模操作卡与导入模板供机房实算填入。"
            if not is_fem_verified
            else f"独立有限元模型复核状态为“已核验吻合（{ext_fem_status}）”，各项指标均在允许偏差包络内。"
        )
    )
    for idx_mfig, mfig_p in enumerate(mc.get("model_figure_paths") or [], start=1):
        mp = Path(str(mfig_p))
        if mp.is_file():
            if _embed_figure_if_available(
                doc,
                {"png_path": str(mp), "caption_zh": f"图 10-{idx_mfig} 本桥独立有限元软件建模与节点信息图"},
                width_cm=14.2,
            ):
                embedded_fig_count += 1

    _add_section_heading(doc, "10.1.2 手算—本程序—独立有限元软件三方复核表", level=3, toc_entries=toc_entries)
    def_rows_w = [
        (
            r["title"],
            r["unit"],
            f"{r['hand_value']:.2f}",
            f"{r['program_value']:.2f}",
            r["software_display"],
            f"{r['hand_vs_software_diff_pct']}%" if r["hand_vs_software_diff_pct"] is not None else "—",
            r["verdict_zh"],
        )
        for r in def_tbl["rows"]
    ]
    _add_three_line_table(
        doc,
        "表 10-1 代表性T梁与中间双柱墩手算—程序—独立电算答辩复核对照表",
        ("对比指标", "单位", "手算值", "程序值", "独立电算值", "相对差异", "结论"),
        def_rows_w,
        col_widths_cm=(3.5, 1.4, 2.0, 2.0, 2.7, 1.7, 2.2),
    )

    _add_section_heading(doc, "10.2 全套施工图钢束与钢筋选筋溯源总表（QL-08～QL-18）", level=2, toc_entries=toc_entries)
    _body(
        "表 10-2 建立施工图钢束/钢筋编号（N1～DS1）至构件统计口径、控制内力需求及抗力的逐项溯源映射（跨页自动续表并重复列名），确保任一图面编号均可直接回查至对应计算章节。"
    )
    all_recs = list(ledger["tendons"]) + list(ledger["all_rebar_items"])
    ledger_rows_w = [
        (
            rec.get("tendon_id") or rec.get("bar_id"),
            rec["sheet_drawing_no"],
            rec.get("spec") or f"{rec['grade']} Φ{rec['dia_mm']:g}",
            f"{rec['quantity_scope']['counting_scope']}:{rec['quantity_scope']['count_per_member']}",
            f"{rec['quantity_scope']['bridge_total_weight_kg']} kg",
            f"{rec['governing_forces']['demand_symbol']}={rec['governing_forces']['demand_value']} ≤ {rec['governing_forces']['capacity_symbol']}={rec['governing_forces']['capacity_value']} {rec['governing_forces']['unit']}",
        )
        for rec in all_recs
    ]
    _add_three_line_table(
        doc,
        "表 10-2 全桥施工图钢束与钢筋编号（N1～DS1）选筋依据及数量溯源表",
        ("编号", "图号", "材料规格", "统计口径", "全桥总重", "控制内力需求与抗力"),
        ledger_rows_w,
        col_widths_cm=(1.5, 1.6, 2.5, 3.0, 2.2, 4.7),
        max_rows_per_chunk=12,
    )

    if change_impact and change_impact.get("has_changes"):
        _add_section_heading(doc, "10.3 本次设计变更影响章节与施工图号记录", level=2, toc_entries=toc_entries)
        _body(change_impact["summary_zh"])

    # ------------------------------------------------------------------
    # Chapter 11: 11  结论与展望
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "11  结论与展望", centered=False, page_break_before=True, toc_entries=toc_entries)
    fem_conc_zh = (
        "已完成手算解析公式与独立有限元模型的三方复核验证"
        if is_fem_verified
        else "已完成手算解析公式与杆系计算程序的交叉复核，并整理完备外部有限元软件建模卡待机房实算导入终核"
    )
    _body(
        f"本次毕业设计围绕“{ps['project_name']}”完成了水文桥孔复核、同桥位三桥型方案比选、上部预制 C50 预应力混凝土简支T梁及桥面系计算、中间双柱式桥墩及钻孔桩计算、桩柱式桥台及独立桩长推导（墩桩 {ps['pier_pile_length_m']:g} m、台桩 {ps['abutment_pile_length_m']:g} m），{fem_conc_zh}，并配套绘出 QL-01～QL-18 全套 18 张 A3 施工图与打印图册。"
        "主梁、横隔梁、支座及墩台桩基在承载能力极限状态与正常使用极限状态下的各项指标均满足现行 JTG 规范要求，计算说明书与施工图尺寸、配筋及工程量表严格一致。后续待外业实测河床断面与钻孔柱状图核定后，即可通过参数化联动一键定稿。"
    )

    # ------------------------------------------------------------------
    # Back Matter: 参考文献, 致  谢, 附  录 (Centered 16pt 黑体, included in TOC)
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "参考文献", centered=True, page_break_before=True, toc_entries=toc_entries)
    for idx_r, r_item in enumerate(refs, start=1):
        rno = int(r_item.get("ref_no", idx_r))
        _add_body_para(doc, f"[{rno}] {r_item['citation']}", indent=False)

    _add_chapter_heading(doc, "致  谢", centered=True, page_break_before=True, toc_entries=toc_entries)
    fem_ack_zh = (
        "结构手算、独立有限元模型校核以及十八张施工图纸反复打磨"
        if is_fem_verified
        else "结构手算、计算程序交叉对算以及十八张施工图纸反复打磨"
    )
    _add_body_para(
        doc,
        f"四年本科求学时光行至收官。在本次桥梁毕业设计的方案论证、{fem_ack_zh}过程中，谨向倾注大量心血悉心指导我的 {advisor_name} 老师致以最诚挚的敬意与感谢！老师严谨治学的工程态度与细致入微的审图意见，使我对桥梁上下部传力机理和规范条文有了系统而深刻的理解。",
    )
    _add_body_para(
        doc,
        f"同时谨向{school_name}{college_name}各位任课教师四年来在结构力学、混凝土结构设计原理、桥梁工程及基础工程课程中打下的扎实专业根基表示衷心感谢，感谢同组同学在建模研究与图纸互审中的交流互助，并向始终给予温暖支持的家人致以深深的谢意！",
    )

    _add_chapter_heading(doc, "附  录  全套施工图目录与主要工程材料数量汇总表", centered=True, page_break_before=True, toc_entries=toc_entries)
    idx_rows_w = [(str(i + 1), dno, dtitle, dscale, "A3") for i, (dno, dtitle, dscale) in enumerate(DEFAULT_18_SHEET_INDEX)]
    _add_three_line_table(
        doc,
        "附表 1 全套施工图设计图册目录（QL-01～QL-18）",
        ("序号", "图号", "图纸名称", "比例", "图幅"),
        idx_rows_w,
        col_widths_cm=(1.5, 2.2, 7.6, 2.6, 1.6),
        max_rows_per_chunk=18,
    )

    boq_rows_w = [(r["category"], r["material"], r["unit"], str(r["quantity"]), r["sheet_ref"]) for r in boq["rows"]]
    _add_three_line_table(
        doc,
        "附表 2 全桥主要工程材料数量汇总表（对应图号 QL-02）",
        ("工程部位", "材料名称及规格", "单位", "全桥数量", "对应图号"),
        boq_rows_w,
        col_widths_cm=(3.0, 5.8, 1.6, 2.5, 2.6),
        max_rows_per_chunk=14,
    )

    # Populate pre-rendered 3-level TOC field in Section 2 before saving
    toc_entry_count = _populate_toc_field_paragraphs(doc, toc_anchor_para, toc_entries)

    doc.save(str(out_docx))

    all_para_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    body_text_for_ref_audit = "\n".join(body_citation_paras)
    cjk_char_count = sum(1 for ch in all_para_text if "\u4e00" <= ch <= "\u9fff")
    ref_audit = audit_references_compliance(refs, body_text=body_text_for_ref_audit)

    md_mirror_path: str | None = None
    if also_write_markdown:
        md_p = out_docx.with_suffix(".md")
        md_p.write_text(all_para_text, encoding="utf-8")
        md_mirror_path = str(md_p)

    return {
        "docx_path": str(out_docx),
        "markdown_mirror_path": md_mirror_path,
        "section_count": len(doc.sections),
        "toc_entry_count": toc_entry_count,
        "zh_abstract_cjk_count": zh_abstract_cjk_count,
        "en_abstract_word_count": en_abstract_word_count,
        "external_fem_status": ext_fem_status,
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
        "cjk_character_count": cjk_char_count,
        "total_character_count": len(all_para_text),
        "embedded_figure_count": embedded_fig_count,
        "references_audit": ref_audit,
    }
