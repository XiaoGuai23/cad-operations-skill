"""Automatic Style & Layout Template Learner from Reference `.docx` and `.dxf` Files.

Extracts reusable, PII-clean formatting profiles from:
1. Reference `.docx` graduation theses / engineering specifications (`ThesisTemplateProfile`):
   - Page size, margins, header/footer distances, Roman/Arabic section numbering;
   - Cover banner text, font sizes, and exact cover table field labels (while strictly
     discarding any sample student's personal name, ID, class, or advisor values);
   - Level 1 / 2 / 3 heading East-Asia & Western fonts, point sizes, bold flags, alignment
     (`left` vs `center`), and numbering convention (`arabic` `1  绪论` vs `chapter_zh` `第一章  绪论`);
   - Body paragraph font, point size, line spacing, and first-line indent;
   - Figure caption font, point size, bold flag, and alignment;
   - Academic three-line table caption/header/cell typography, top/mid/bottom border weights,
     and `续表` continuation habit.
2. Reference `.dxf` sheet or multi-sheet `.dxf` album directory (`AlbumTemplateProfile`):
   - Sheet format (`A0`..`A4`), inner frame margins (`margin_left_mm`, `margin_other_mm`),
     and bottom-right title-block bounding box;
   - Layer color & lineweight mapping across both standard English (`FRAME`, `OUTLINE`, `CONCRETE`,
     `REBAR`, `REBAR_STIRRUP`, `TENDON`, `DIM`, `TEXT`, `HATCH`, ...) and Chinese design-institute
     layer names (`图框`, `粗实线`, `钢筋`, `箍筋`, `钢束`, `标注`, ...);
   - Text style fonts (`font`, `title_font`, `width_factor`) and `DIMSTYLE` (`archtick` vs
     `closed_filled`, `dimtxt`, `dimasz`, `dimgap`);
   - Drawing-number prefix convention (`01..21` vs `QL-01..`, `JS-01..`, `S-01..`) and sheet index.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any, Literal

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
import ezdxf
from ezdxf import bbox
from pydantic import BaseModel, ConfigDict, Field
import yaml

from cad_skill_core.revision.common import sha256_file
from .preference_memory import (
    PreferenceEntry,
    PreferenceEvidence,
    default_preference_memory_path,
    load_preference_store,
    save_preference_store,
)
from .sample_learner import learn_standard_from_dxf, save_standard_yaml


# ======================================================================
# 1. Pydantic Schemas (`cad-thesis-template/v1` & `cad-album-template/v1`)
# ======================================================================
DEFAULT_COVER_FIELD_LABELS: dict[str, str] = {
    "title": "题    目",
    "student_name": "学生姓名",
    "major_class": "专业班级",
    "student_id": "学    号",
    "college_name": "学    院",
    "advisor_name": "指导教师(职称)",
    "completion_date": "完成时间",
}


class ThesisTemplateProfile(BaseModel):
    """Reusable Word specification / graduation-thesis formatting profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cad-thesis-template/v1"] = "cad-thesis-template/v1"
    profile_name: str = "learned-thesis-template"
    source_docx: str = ""
    source_sha256: str = ""
    learned_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # Page & Section geometry (cm)
    page_width_cm: float = Field(default=21.0, gt=10.0, le=50.0)
    page_height_cm: float = Field(default=29.7, gt=10.0, le=60.0)
    top_margin_cm: float = Field(default=2.5, ge=1.0, le=6.0)
    bottom_margin_cm: float = Field(default=2.5, ge=1.0, le=6.0)
    left_margin_cm: float = Field(default=3.0, ge=1.0, le=6.0)
    right_margin_cm: float = Field(default=2.5, ge=1.0, le=6.0)
    header_distance_cm: float = Field(default=1.5, ge=0.5, le=4.0)
    footer_distance_cm: float = Field(default=1.75, ge=0.5, le=4.0)
    front_page_num_fmt: str = "upperRoman"
    body_page_num_fmt: str = "decimal"
    running_header_text: str = ""

    # Cover layout (strictly PII-free!)
    school_title_zh_font: str = "黑体"
    school_title_size_pt: float = Field(default=22.0, ge=12.0, le=42.0)
    degree_banner_text: str = "本科毕业设计"
    degree_banner_zh_font: str = "宋体"
    degree_banner_size_pt: float = Field(default=26.0, ge=14.0, le=48.0)
    cover_field_labels: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_COVER_FIELD_LABELS))

    # Headings (Level 1 / 2 / 3)
    h1_numbering_style: Literal["arabic", "chapter_zh"] = "arabic"
    h1_zh_font: str = "黑体"
    h1_en_font: str = "Times New Roman"
    h1_size_pt: float = Field(default=16.0, ge=10.5, le=28.0)
    h1_bold: bool = True
    h1_alignment: Literal["left", "center"] = "left"

    h2_zh_font: str = "黑体"
    h2_en_font: str = "Times New Roman"
    h2_size_pt: float = Field(default=15.0, ge=10.0, le=24.0)
    h2_bold: bool = True
    h2_alignment: Literal["left", "center"] = "left"

    h3_zh_font: str = "黑体"
    h3_en_font: str = "Times New Roman"
    h3_size_pt: float = Field(default=14.0, ge=9.0, le=22.0)
    h3_bold: bool = True
    h3_alignment: Literal["left", "center"] = "left"

    # Body paragraph
    body_zh_font: str = "宋体"
    body_en_font: str = "Times New Roman"
    body_size_pt: float = Field(default=12.0, ge=9.0, le=18.0)
    body_line_spacing: float = Field(default=1.5, ge=1.0, le=2.5)
    body_first_line_indent_pt: float = Field(default=24.0, ge=0.0, le=48.0)

    # Figure captions
    figure_caption_zh_font: str = "宋体"
    figure_caption_en_font: str = "Times New Roman"
    figure_caption_size_pt: float = Field(default=10.5, ge=8.0, le=16.0)
    figure_caption_bold: bool = False
    figure_caption_alignment: Literal["center", "left"] = "center"

    # Academic three-line tables
    table_caption_zh_font: str = "黑体"
    table_caption_size_pt: float = Field(default=10.5, ge=8.0, le=16.0)
    table_caption_bold: bool = True
    table_header_zh_font: str = "黑体"
    table_header_size_pt: float = Field(default=10.5, ge=7.5, le=15.0)
    table_header_bold: bool = True
    table_cell_zh_font: str = "宋体"
    table_cell_size_pt: float = Field(default=10.5, ge=7.5, le=15.0)
    top_border_sz: int = Field(default=12, ge=2, le=36)
    mid_border_sz: int = Field(default=6, ge=2, le=24)
    bottom_border_sz: int = Field(default=12, ge=2, le=36)
    use_continuation_table: bool = True

    extracted_evidence: dict[str, Any] = Field(default_factory=dict)


class AlbumTemplateProfile(BaseModel):
    """Reusable CAD sheet & multi-sheet construction album style profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cad-album-template/v1"] = "cad-album-template/v1"
    profile_name: str = "learned-album-template"
    source_dxf_count: int = 1
    source_files: list[str] = Field(default_factory=list)
    learned_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    sheet_size: Literal["A0", "A1", "A2", "A3", "A4"] = "A3"
    sheet_width_mm: float = 420.0
    sheet_height_mm: float = 297.0
    margin_left_mm: float = Field(default=15.0, ge=5.0, le=45.0)
    margin_other_mm: float = Field(default=10.0, ge=4.0, le=30.0)
    title_block_width_mm: float | None = None
    title_block_height_mm: float | None = None

    text_font: str = "Deng.ttf"
    title_font: str = "simhei.ttf"
    text_width_factor: float = Field(default=0.85, ge=0.4, le=1.5)

    layer_colors: dict[str, int] = Field(default_factory=dict)
    layer_lineweights_mm: dict[str, float] = Field(default_factory=dict)

    dim_arrow_style: Literal["archtick", "closed_filled"] = "archtick"
    dim_text_height_mm: float = Field(default=2.4, ge=1.2, le=15.0)
    dim_arrow_size_mm: float = Field(default=1.8, ge=0.6, le=15.0)
    dim_gap_mm: float = Field(default=0.7, ge=0.2, le=10.0)
    dim_stack_step_mm: float = Field(default=8.5, ge=4.0, le=25.0)
    schedule_dock: str = "bottom_right"

    drawing_no_style: Literal["numeric_2digit", "prefixed"] = "numeric_2digit"
    drawing_no_prefix: str = ""
    rebar_standard_note: str | None = None
    detected_sheet_index: list[dict[str, str]] = Field(default_factory=list)


# ======================================================================
# 2. `.docx` Thesis Template Extractor (`learn_thesis_template_from_docx`)
# ======================================================================
_COVER_SLOT_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("题目", "论文题目", "设计题目", "毕业设计题目", "课题名称")),
    ("advisor_name", ("指导教师", "导师姓名", "指导老师", "导师")),
    ("student_id", ("学号", "学生学号")),
    ("major_class", ("专业班级", "专业及班级", "专业", "班级")),
    ("college_name", ("学院", "院系", "所在学院", "所在院系", "系别", "二级学院")),
    ("completion_date", ("完成时间", "完成日期", "提交日期", "日期", "时间")),
    ("student_name", ("学生姓名", "作者姓名", "设计人", "姓名")),
)

_ZH_CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百零\d]+\s*章\b")
_ARABIC_H1_RE = re.compile(r"^\d{1,2}\s{1,4}[\u4e00-\u9fffA-Za-z]")
_H2_RE = re.compile(r"^\d{1,2}\.\d{1,2}(?!\.\d)\s*[\u4e00-\u9fffA-Za-z]")
_H3_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,2}\s*[\u4e00-\u9fffA-Za-z]")
_FIG_CAP_RE = re.compile(r"^图\s*\d+(?:[-.\s]\d+)?\s+\S+")
_TBL_CAP_RE = re.compile(r"^(?:附\s*)?表\s*\d*(?:[-.\s]\d+)?\s+\S+")
_CONT_TBL_RE = re.compile(r"^续\s*(?:附\s*)?表")


def _extract_rpr_font_info(
    r_pr: Any,
) -> tuple[str | None, str | None, float | None, bool | None]:
    if r_pr is None:
        return None, None, None, None
    zh_f: str | None = None
    en_f: str | None = None
    sz_pt: float | None = None
    bold_v: bool | None = None

    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is not None:
        zh_f = r_fonts.get(qn("w:eastAsia")) or None
        en_f = r_fonts.get(qn("w:ascii")) or r_fonts.get(qn("w:hAnsi")) or None

    sz_el = r_pr.find(qn("w:sz"))
    if sz_el is not None and sz_el.get(qn("w:val")):
        try:
            sz_pt = round(float(sz_el.get(qn("w:val"))) / 2.0, 2)
        except ValueError:
            pass

    b_el = r_pr.find(qn("w:b"))
    if b_el is not None:
        raw_b = b_el.get(qn("w:val"))
        val_b = str(raw_b if raw_b is not None else "true").strip().lower()
        bold_v = val_b not in {"0", "false", "off", "none"}

    return zh_f, en_f, sz_pt, bold_v


def _paragraph_effective_typography(
    para: Any,
    *,
    default_zh: str = "宋体",
    default_en: str = "Times New Roman",
    default_size_pt: float = 12.0,
    default_bold: bool = False,
) -> dict[str, Any]:
    """Resolve effective font, size, bold, and alignment across run XML and inherited paragraph style."""
    zh_f: str | None = None
    en_f: str | None = None
    sz_pt: float | None = None
    bold_v: bool | None = None

    # 1. Inspect non-empty runs first (direct formatting wins over style in Word)
    for run in para.runs:
        if not run.text or not run.text.strip():
            continue
        rz, re_f, rsz, rb = _extract_rpr_font_info(run._r.find(qn("w:rPr")))
        zh_f = zh_f or rz
        en_f = en_f or re_f or (run.font.name if run.font and run.font.name else None)
        if sz_pt is None:
            if rsz is not None:
                sz_pt = rsz
            elif run.font and run.font.size is not None:
                sz_pt = round(float(run.font.size.pt), 2)
        if bold_v is None:
            if rb is not None:
                bold_v = rb
            elif run.bold is not None:
                bold_v = bool(run.bold)
        if zh_f and sz_pt is not None and bold_v is not None:
            break

    # 2. Walk paragraph style chain for any unset properties
    st = getattr(para, "style", None)
    visited = 0
    while st is not None and visited < 5:
        visited += 1
        st_rpr = st._element.find(qn("w:rPr")) if hasattr(st, "_element") else None
        sz_s_zh, sz_s_en, sz_s_pt, sb = _extract_rpr_font_info(st_rpr)
        zh_f = zh_f or sz_s_zh
        en_f = en_f or sz_s_en or (st.font.name if getattr(st, "font", None) and st.font.name else None)
        if sz_pt is None:
            if sz_s_pt is not None:
                sz_pt = sz_s_pt
            elif getattr(st, "font", None) and st.font.size is not None:
                sz_pt = round(float(st.font.size.pt), 2)
        if bold_v is None:
            if sb is not None:
                bold_v = sb
            elif getattr(st, "font", None) and st.font.bold is not None:
                bold_v = bool(st.font.bold)
        st = getattr(st, "base_style", None)

    align_enum = para.alignment
    if align_enum is None and getattr(para, "style", None) is not None:
        align_enum = para.style.paragraph_format.alignment
    align_str: Literal["left", "center"] = (
        "center" if align_enum == WD_ALIGN_PARAGRAPH.CENTER else "left"
    )

    pf = para.paragraph_format
    ls_val: float | None = None
    if isinstance(pf.line_spacing, (int, float)) and 0.8 <= float(pf.line_spacing) <= 3.0:
        ls_val = round(float(pf.line_spacing), 2)

    indent_pt: float | None = None
    if pf.first_line_indent is not None:
        try:
            indent_pt = round(float(pf.first_line_indent.pt), 1)
        except Exception:
            pass

    return {
        "zh_font": zh_f or default_zh,
        "en_font": en_f or default_en,
        "size_pt": sz_pt if sz_pt is not None else default_size_pt,
        "bold": bold_v if bold_v is not None else default_bold,
        "alignment": align_str,
        "line_spacing": ls_val,
        "first_line_indent_pt": indent_pt,
    }


def _mode_or_default(values: list[Any], default: Any) -> Any:
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def _detect_heading_level(para: Any, text_s: str) -> int | None:
    if "\t" in text_s:
        return None  # Skip TOC rows (`1  绪论\t1`)
    st_name = str(getattr(getattr(para, "style", None), "name", "") or "").strip().lower()
    p_pr = para._p.find(qn("w:pPr"))
    outline_el = p_pr.find(qn("w:outlineLvl")) if p_pr is not None else None
    if outline_el is not None and outline_el.get(qn("w:val")) is not None:
        try:
            ol = int(outline_el.get(qn("w:val")))
            if ol in {0, 1, 2}:
                return ol + 1
        except ValueError:
            pass
    if st_name in {"heading 1", "标题 1", "章标题"}:
        return 1
    if st_name in {"heading 2", "标题 2", "节标题"}:
        return 2
    if st_name in {"heading 3", "标题 3", "条标题"}:
        return 3
    if _H3_RE.match(text_s):
        return 3
    if _H2_RE.match(text_s):
        return 2
    if _ZH_CHAPTER_RE.match(text_s) or _ARABIC_H1_RE.match(text_s):
        return 1
    return None


def learn_thesis_template_from_docx(
    docx_path: str | Path,
    *,
    profile_name: str | None = None,
) -> ThesisTemplateProfile:
    """Inspect a reference `.docx` specification/thesis and derive a PII-free `ThesisTemplateProfile`."""
    p = Path(docx_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"reference .docx does not exist: {p}")

    doc = Document(str(p))
    profile = ThesisTemplateProfile(
        profile_name=profile_name or f"learned-thesis-{p.stem}",
        source_docx=p.name,
        source_sha256=sha256_file(p),
    )

    # 1. Section geometry, margins, page numbering & generic running header
    if doc.sections:
        body_sec = doc.sections[-1]
        if body_sec.page_width is not None:
            profile.page_width_cm = round(float(body_sec.page_width.cm), 2)
        if body_sec.page_height is not None:
            profile.page_height_cm = round(float(body_sec.page_height.cm), 2)
        if body_sec.top_margin is not None:
            profile.top_margin_cm = round(float(body_sec.top_margin.cm), 2)
        if body_sec.bottom_margin is not None:
            profile.bottom_margin_cm = round(float(body_sec.bottom_margin.cm), 2)
        if body_sec.left_margin is not None:
            profile.left_margin_cm = round(float(body_sec.left_margin.cm), 2)
        if body_sec.right_margin is not None:
            profile.right_margin_cm = round(float(body_sec.right_margin.cm), 2)
        if body_sec.header_distance is not None:
            profile.header_distance_cm = round(float(body_sec.header_distance.cm), 2)
        if body_sec.footer_distance is not None:
            profile.footer_distance_cm = round(float(body_sec.footer_distance.cm), 2)

        for idx_s, sec in enumerate(doc.sections):
            pg_el = sec._sectPr.find(qn("w:pgNumType"))
            if pg_el is not None and pg_el.get(qn("w:fmt")):
                fmt = str(pg_el.get(qn("w:fmt")))
                if idx_s == 1 or "roman" in fmt.lower():
                    profile.front_page_num_fmt = fmt
                elif idx_s >= len(doc.sections) - 1:
                    profile.body_page_num_fmt = fmt

        hdr_txts = [hp.text.strip() for hp in body_sec.header.paragraphs if hp.text.strip()]
        if hdr_txts:
            cand_hdr = hdr_txts[0]
            # Keep only generic academic running headers (never sample bridge or student names)
            if any(k in cand_hdr for k in ("毕业设计", "毕业论文", "计算说明书", "设计说明书")) and len(cand_hdr) <= 28:
                profile.running_header_text = cand_hdr

    # 2. Cover banner & cover field labels (strictly PII-free: extract ONLY left-column field labels!)
    for para in doc.paragraphs[:12]:
        txt = para.text.strip()
        if not txt:
            continue
        typo = _paragraph_effective_typography(para)
        if any(k in txt for k in ("毕业设计", "毕业论文", "学位论文", "课程设计")) and len(txt) <= 20:
            profile.degree_banner_text = txt
            profile.degree_banner_zh_font = typo["zh_font"]
            if 14.0 <= typo["size_pt"] <= 48.0:
                profile.degree_banner_size_pt = typo["size_pt"]
            break
        if any(k in txt for k in ("大学", "学院")) and len(txt) <= 24 and typo["size_pt"] >= 16.0:
            profile.school_title_zh_font = typo["zh_font"]
            profile.school_title_size_pt = typo["size_pt"]

    learned_labels = dict(DEFAULT_COVER_FIELD_LABELS)
    if doc.tables:
        cov_tbl = doc.tables[0]
        if len(cov_tbl.columns) >= 2 and len(cov_tbl.rows) <= 14:
            for row in cov_tbl.rows:
                raw_lbl = row.cells[0].text.strip().rstrip(":：")
                norm_lbl = re.sub(r"[\s()（）:：]", "", raw_lbl)
                if not norm_lbl:
                    continue
                for slot_key, synonyms in _COVER_SLOT_SYNONYMS:
                    if any(norm_lbl == syn or syn in norm_lbl for syn in synonyms):
                        learned_labels[slot_key] = raw_lbl
                        break
    profile.cover_field_labels = learned_labels

    # 3. Headings (Level 1/2/3), Body, Figure Captions, and Table Captions
    h1_typos: list[dict[str, Any]] = []
    h2_typos: list[dict[str, Any]] = []
    h3_typos: list[dict[str, Any]] = []
    body_typos: list[dict[str, Any]] = []
    fig_cap_typos: list[dict[str, Any]] = []
    tbl_cap_typos: list[dict[str, Any]] = []
    zh_chapter_hits = 0
    arabic_chapter_hits = 0
    saw_cont_table = False

    back_matter_titles = {"摘要", "abstract", "目录", "参考文献", "致谢", "附录"}

    for para in doc.paragraphs:
        txt = para.text.strip()
        if not txt:
            continue
        if _CONT_TBL_RE.match(txt):
            saw_cont_table = True
            continue
        if _FIG_CAP_RE.match(txt):
            fig_cap_typos.append(_paragraph_effective_typography(para, default_size_pt=10.5, default_bold=False))
            continue
        if _TBL_CAP_RE.match(txt):
            tbl_cap_typos.append(_paragraph_effective_typography(para, default_zh="黑体", default_size_pt=10.5, default_bold=True))
            continue

        lvl = _detect_heading_level(para, txt)
        if lvl == 1:
            norm_no_space = re.sub(r"\s+", "", txt).lower()
            if any(norm_no_space.startswith(bt) for bt in back_matter_titles):
                continue
            if _ZH_CHAPTER_RE.match(txt):
                zh_chapter_hits += 1
            elif _ARABIC_H1_RE.match(txt):
                arabic_chapter_hits += 1
            h1_typos.append(_paragraph_effective_typography(para, default_zh="黑体", default_size_pt=16.0, default_bold=True))
        elif lvl == 2:
            h2_typos.append(_paragraph_effective_typography(para, default_zh="黑体", default_size_pt=15.0, default_bold=True))
        elif lvl == 3:
            h3_typos.append(_paragraph_effective_typography(para, default_zh="黑体", default_size_pt=14.0, default_bold=True))
        elif len(txt) >= 36 and "\t" not in txt:
            body_typos.append(_paragraph_effective_typography(para, default_zh="宋体", default_size_pt=12.0, default_bold=False))

    if zh_chapter_hits > arabic_chapter_hits:
        profile.h1_numbering_style = "chapter_zh"
    elif arabic_chapter_hits > 0:
        profile.h1_numbering_style = "arabic"

    if h1_typos:
        profile.h1_zh_font = _mode_or_default([t["zh_font"] for t in h1_typos], profile.h1_zh_font)
        profile.h1_en_font = _mode_or_default([t["en_font"] for t in h1_typos], profile.h1_en_font)
        profile.h1_size_pt = _mode_or_default([t["size_pt"] for t in h1_typos], profile.h1_size_pt)
        profile.h1_bold = _mode_or_default([t["bold"] for t in h1_typos], profile.h1_bold)
        profile.h1_alignment = _mode_or_default([t["alignment"] for t in h1_typos], profile.h1_alignment)
    if h2_typos:
        profile.h2_zh_font = _mode_or_default([t["zh_font"] for t in h2_typos], profile.h2_zh_font)
        profile.h2_en_font = _mode_or_default([t["en_font"] for t in h2_typos], profile.h2_en_font)
        profile.h2_size_pt = _mode_or_default([t["size_pt"] for t in h2_typos], profile.h2_size_pt)
        profile.h2_bold = _mode_or_default([t["bold"] for t in h2_typos], profile.h2_bold)
        profile.h2_alignment = _mode_or_default([t["alignment"] for t in h2_typos], profile.h2_alignment)
    if h3_typos:
        profile.h3_zh_font = _mode_or_default([t["zh_font"] for t in h3_typos], profile.h3_zh_font)
        profile.h3_en_font = _mode_or_default([t["en_font"] for t in h3_typos], profile.h3_en_font)
        profile.h3_size_pt = _mode_or_default([t["size_pt"] for t in h3_typos], profile.h3_size_pt)
        profile.h3_bold = _mode_or_default([t["bold"] for t in h3_typos], profile.h3_bold)
        profile.h3_alignment = _mode_or_default([t["alignment"] for t in h3_typos], profile.h3_alignment)

    if body_typos:
        profile.body_zh_font = _mode_or_default([t["zh_font"] for t in body_typos], profile.body_zh_font)
        profile.body_en_font = _mode_or_default([t["en_font"] for t in body_typos], profile.body_en_font)
        profile.body_size_pt = _mode_or_default([t["size_pt"] for t in body_typos], profile.body_size_pt)
        ls_cands = [t["line_spacing"] for t in body_typos if t["line_spacing"] is not None]
        if ls_cands:
            profile.body_line_spacing = _mode_or_default(ls_cands, profile.body_line_spacing)
        ind_cands = [t["first_line_indent_pt"] for t in body_typos if t["first_line_indent_pt"] is not None and t["first_line_indent_pt"] >= 0]
        if ind_cands:
            profile.body_first_line_indent_pt = _mode_or_default(ind_cands, profile.body_first_line_indent_pt)

    if fig_cap_typos:
        profile.figure_caption_zh_font = _mode_or_default([t["zh_font"] for t in fig_cap_typos], profile.figure_caption_zh_font)
        profile.figure_caption_en_font = _mode_or_default([t["en_font"] for t in fig_cap_typos], profile.figure_caption_en_font)
        profile.figure_caption_size_pt = _mode_or_default([t["size_pt"] for t in fig_cap_typos], profile.figure_caption_size_pt)
        profile.figure_caption_bold = _mode_or_default([t["bold"] for t in fig_cap_typos], profile.figure_caption_bold)
        profile.figure_caption_alignment = _mode_or_default([t["alignment"] for t in fig_cap_typos], profile.figure_caption_alignment)

    if tbl_cap_typos:
        profile.table_caption_zh_font = _mode_or_default([t["zh_font"] for t in tbl_cap_typos], profile.table_caption_zh_font)
        profile.table_caption_size_pt = _mode_or_default([t["size_pt"] for t in tbl_cap_typos], profile.table_caption_size_pt)
        profile.table_caption_bold = _mode_or_default([t["bold"] for t in tbl_cap_typos], profile.table_caption_bold)

    # 4. Inspect body three-line tables (skip table 0 if it is the 2-column cover form)
    body_tables = doc.tables[1:] if len(doc.tables) > 1 and len(doc.tables[0].columns) == 2 else list(doc.tables)
    hdr_cell_typos: list[dict[str, Any]] = []
    data_cell_typos: list[dict[str, Any]] = []
    top_szs: list[int] = []
    mid_szs: list[int] = []
    bot_szs: list[int] = []

    for tbl in body_tables[:8]:
        if len(tbl.rows) < 2:
            continue
        r0_p = tbl.rows[0].cells[0].paragraphs[0] if tbl.rows[0].cells[0].paragraphs else None
        if r0_p and r0_p.text.strip():
            hdr_cell_typos.append(_paragraph_effective_typography(r0_p, default_zh="黑体", default_size_pt=10.5, default_bold=True))
        r1_p = tbl.rows[1].cells[0].paragraphs[0] if tbl.rows[1].cells[0].paragraphs else None
        if r1_p and r1_p.text.strip():
            data_cell_typos.append(_paragraph_effective_typography(r1_p, default_zh="宋体", default_size_pt=10.5, default_bold=False))

        tc_pr_0 = tbl.rows[0].cells[0]._tc.find(qn("w:tcPr"))
        bdr_0 = tc_pr_0.find(qn("w:tcBorders")) if tc_pr_0 is not None else None
        if bdr_0 is not None:
            t_el = bdr_0.find(qn("w:top"))
            m_el = bdr_0.find(qn("w:bottom"))
            if t_el is not None and t_el.get(qn("w:sz")):
                top_szs.append(int(t_el.get(qn("w:sz"))))
            if m_el is not None and m_el.get(qn("w:sz")):
                mid_szs.append(int(m_el.get(qn("w:sz"))))

        tc_pr_last = tbl.rows[-1].cells[0]._tc.find(qn("w:tcPr"))
        bdr_last = tc_pr_last.find(qn("w:tcBorders")) if tc_pr_last is not None else None
        if bdr_last is not None:
            b_el = bdr_last.find(qn("w:bottom"))
            if b_el is not None and b_el.get(qn("w:sz")):
                bot_szs.append(int(b_el.get(qn("w:sz"))))

    if hdr_cell_typos:
        profile.table_header_zh_font = _mode_or_default([t["zh_font"] for t in hdr_cell_typos], profile.table_header_zh_font)
        profile.table_header_size_pt = _mode_or_default([t["size_pt"] for t in hdr_cell_typos], profile.table_header_size_pt)
        profile.table_header_bold = _mode_or_default([t["bold"] for t in hdr_cell_typos], profile.table_header_bold)
    if data_cell_typos:
        profile.table_cell_zh_font = _mode_or_default([t["zh_font"] for t in data_cell_typos], profile.table_cell_zh_font)
        profile.table_cell_size_pt = _mode_or_default([t["size_pt"] for t in data_cell_typos], profile.table_cell_size_pt)
    if top_szs:
        profile.top_border_sz = _mode_or_default(top_szs, profile.top_border_sz)
    if mid_szs:
        profile.mid_border_sz = _mode_or_default(mid_szs, profile.mid_border_sz)
    if bot_szs:
        profile.bottom_border_sz = _mode_or_default(bot_szs, profile.bottom_border_sz)
    if saw_cont_table:
        profile.use_continuation_table = True

    profile.extracted_evidence = {
        "section_count": len(doc.sections),
        "sampled_h1_count": len(h1_typos),
        "sampled_h2_count": len(h2_typos),
        "sampled_h3_count": len(h3_typos),
        "sampled_body_paragraphs": len(body_typos),
        "sampled_figure_captions": len(fig_cap_typos),
        "sampled_body_tables": len(body_tables),
        "zh_chapter_hits": zh_chapter_hits,
        "arabic_chapter_hits": arabic_chapter_hits,
    }
    return profile


# ======================================================================
# 3. `.dxf` Sheet & Construction Album Learner (`learn_dxf_album_profile`)
# ======================================================================
_CIVIL_LAYER_ALIASES: dict[str, tuple[str, ...]] = {
    "FRAME": ("FRAME", "BORDER", "TITLE", "TITLEBLOCK", "TK", "图框", "标题栏"),
    "OUTLINE": ("OUTLINE", "STRUCT", "VISIBLE", "CONTOUR", "粗实线", "外轮廓"),
    "CONCRETE": ("CONCRETE", "CONC", "GIRDER", "PIER", "混凝土", "结构轮廓", "构件轮廓"),
    "THIN": ("THIN", "FINE", "WIRE", "细实线", "细线"),
    "REBAR": ("REBAR", "MAIN_BAR", "BAR", "钢筋", "主筋", "普通钢筋"),
    "REBAR_STIRRUP": ("REBAR_STIRRUP", "STIRRUP", "HOOP", "箍筋", "构造筋", "分布筋"),
    "TENDON": ("TENDON", "PRESTRESS", "STRAND", "预应力", "钢束", "预应力束"),
    "CENTER": ("CENTER", "AXIS", "CENTERLINE", "中心线", "轴线"),
    "HIDDEN": ("HIDDEN", "DASH", "DASHED", "虚线", "不可见线"),
    "DIM": ("DIM", "DIMENSION", "DIMS", "尺寸", "标注"),
    "TEXT": ("TEXT", "NOTE", "NOTES", "ANNOTATION", "文字", "说明"),
    "HATCH": ("HATCH", "SECTION", "FILL", "剖面", "填充"),
    "TERRAIN": ("TERRAIN", "GROUND", "SOIL", "地面线", "地质", "河床"),
}

_SHEET_DIMS: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}


def _collect_dxf_files(path_or_dir: Path) -> list[Path]:
    if path_or_dir.is_file():
        return [path_or_dir]
    if path_or_dir.is_dir():
        files = sorted(path_or_dir.rglob("*.dxf"))
        if not files:
            raise FileNotFoundError(f"no .dxf files found inside directory: {path_or_dir}")
        return files
    raise FileNotFoundError(f"DXF path or directory does not exist: {path_or_dir}")


def learn_dxf_album_profile(
    dxf_path_or_dir: str | Path,
    *,
    profile_name: str | None = None,
) -> AlbumTemplateProfile:
    """Learn CAD sheet geometry, layer colors/lineweights, dimstyle, and numbering prefix from 1..N DXFs."""
    root_p = Path(dxf_path_or_dir).expanduser().resolve()
    dxf_files = _collect_dxf_files(root_p)

    layer_color_votes: dict[str, list[int]] = {k: [] for k in _CIVIL_LAYER_ALIASES}
    layer_lw_votes: dict[str, list[float]] = {k: [] for k in _CIVIL_LAYER_ALIASES}
    font_votes: list[str] = []
    title_font_votes: list[str] = []
    width_votes: list[float] = []
    arrow_votes: list[str] = []
    dimtxt_votes: list[float] = []
    dimasz_votes: list[float] = []
    dimgap_votes: list[float] = []
    sheet_votes: list[str] = []
    margin_left_votes: list[float] = []
    margin_other_votes: list[float] = []
    tb_boxes: list[tuple[float, float]] = []
    detected_dnos: list[tuple[str, str, str]] = []
    rebar_std_hits: list[str] = []

    for dxf_f in dxf_files:
        doc = ezdxf.readfile(dxf_f)
        sample_layers = {
            lyr.dxf.name.upper(): lyr
            for lyr in doc.layers
            if lyr.dxf.name.upper() not in {"0", "DEFPOINTS"}
        }
        for canon, aliases in _CIVIL_LAYER_ALIASES.items():
            for al in aliases:
                if al.upper() in sample_layers:
                    chosen = sample_layers[al.upper()]
                    c_val = abs(int(chosen.color))
                    if 1 <= c_val <= 255:
                        layer_color_votes[canon].append(c_val)
                    lw_raw = int(chosen.dxf.get("lineweight", -1))
                    if lw_raw > 0:
                        layer_lw_votes[canon].append(round(lw_raw / 100.0, 2))
                    break

        for st in doc.styles:
            sname = st.dxf.name.upper()
            f_str = str(st.dxf.get("font", "") or "").strip()
            if not f_str:
                continue
            if sname in {"CIVIL_TITLE", "TITLE", "HEADER"}:
                title_font_votes.append(f_str)
            elif sname in {"CIVIL_TEXT", "NORMAL", "CN", "TEXT", "HZ"}:
                font_votes.append(f_str)
                wf = float(st.dxf.get("width", 0.85) or 0.85)
                if 0.4 <= wf <= 1.5:
                    width_votes.append(round(wf, 2))

        for ds in doc.dimstyles:
            ds_name = ds.dxf.name.upper()
            if ds_name in {"STANDARD", "ISO-25", "ANNOTATIVE"} and len(doc.dimstyles) > 1:
                continue
            blk = str(ds.dxf.get("dimblk", "") or "").upper()
            if any(k in blk for k in ("ARCH", "TICK", "OBLIQUE")):
                arrow_votes.append("archtick")
            elif blk != "" or ds_name not in {"STANDARD"}:
                arrow_votes.append("archtick" if "CIVIL" in ds_name else "closed_filled")
            dt_v = float(ds.dxf.get("dimtxt", 0.0) or 0.0)
            da_v = float(ds.dxf.get("dimasz", 0.0) or 0.0)
            dg_v = float(ds.dxf.get("dimgap", 0.0) or 0.0)
            if 1.2 <= dt_v <= 15.0:
                dimtxt_votes.append(round(dt_v, 2))
            if 0.6 <= da_v <= 15.0:
                dimasz_votes.append(round(da_v, 2))
            if 0.2 <= dg_v <= 10.0:
                dimgap_votes.append(round(dg_v, 2))

        msp = doc.modelspace()
        ext = bbox.extents(msp)
        if ext.has_data:
            w_ext = float(ext.extmax.x - ext.extmin.x)
            h_ext = float(ext.extmax.y - ext.extmin.y)
            span_l, span_s = max(w_ext, h_ext), min(w_ext, h_ext)
            for s_key, (sw, sh) in _SHEET_DIMS.items():
                if abs(span_l - sw) + abs(span_s - sh) <= 35.0:
                    sheet_votes.append(s_key)
                    break

            # Detect inner FRAME border margins & bottom-right title block
            frame_polys: list[tuple[float, float, float, float]] = []
            for ent in msp:
                if ent.dxftype() == "LWPOLYLINE" and ent.closed and str(ent.dxf.get("layer", "")).upper() in {"FRAME", "BORDER", "TK", "图框"}:
                    eb = bbox.extents([ent])
                    if eb.has_data:
                        frame_polys.append((float(eb.extmin.x), float(eb.extmin.y), float(eb.extmax.x), float(eb.extmax.y)))
            for fx0, fy0, fx1, fy1 in frame_polys:
                fw, fh = fx1 - fx0, fy1 - fy0
                if fw >= 0.80 * w_ext and fh >= 0.80 * h_ext:
                    ml = round(fx0 - float(ext.extmin.x), 1)
                    mo = round(fy0 - float(ext.extmin.y), 1)
                    if 5.0 <= ml <= 45.0:
                        margin_left_votes.append(ml)
                    if 4.0 <= mo <= 30.0:
                        margin_other_votes.append(mo)
                elif 80.0 <= fw <= 0.65 * w_ext and 16.0 <= fh <= 0.40 * h_ext and fx1 >= float(ext.extmax.x) - 35.0 and fy0 <= float(ext.extmin.y) + 35.0:
                    tb_boxes.append((round(fw, 1), round(fh, 1)))

        # Scan text entities for rebar standard edition & sheet number / title
        sheet_dno = ""
        sheet_title = ""
        sheet_scale = ""
        for ent in msp:
            if ent.dxftype() not in {"TEXT", "MTEXT"}:
                continue
            ts = str(ent.dxf.get("text", "") or "").strip()
            if not ts:
                continue
            m_gb = re.search(r"GB(?:/T)?\s*1499\.[12][—\-]\d{4}", ts)
            if m_gb:
                rebar_std_hits.append(m_gb.group(0))
            if ts.startswith("图名：") or ts.startswith("图名:"):
                sheet_title = ts.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif ts.startswith("比例 "):
                sheet_scale = ts.replace("比例", "", 1).strip()
            elif re.match(r"^(?:[A-Za-z]{1,4}-\d{1,3}(?:-\d+)?|\d{2}(?:-\d+)?)$", ts):
                sheet_dno = ts

        if not sheet_dno:
            m_stem = re.match(r"^([A-Za-z]{1,4}-\d{1,3}(?:-\d+)?|\d{2}(?:-\d+)?)(?:_|$)", dxf_f.stem)
            if m_stem:
                sheet_dno = m_stem.group(1)
        if sheet_dno:
            detected_dnos.append((sheet_dno, sheet_title or dxf_f.stem, sheet_scale or "图示比例"))

    chosen_sheet: Literal["A0", "A1", "A2", "A3", "A4"] = _mode_or_default(sheet_votes, "A3")
    sw_mm, sh_mm = _SHEET_DIMS[chosen_sheet]
    learned_colors = {k: _mode_or_default(v, 0) for k, v in layer_color_votes.items() if v}
    learned_lws = {k: _mode_or_default(v, 0.0) for k, v in layer_lw_votes.items() if v}
    tb_w, tb_h = _mode_or_default(tb_boxes, (None, None))

    # Infer numbering prefix (`QL-`, `JS-`, `S-`, or pure numeric `01`)
    prefixes: list[str] = []
    for dno_s, _, _ in detected_dnos:
        m_pf = re.match(r"^([A-Za-z]{1,4}-)\d+", dno_s)
        if m_pf:
            prefixes.append(m_pf.group(1).upper())
        elif re.match(r"^\d{2}(?:-\d+)?$", dno_s):
            prefixes.append("")
    chosen_prefix = _mode_or_default(prefixes, "")

    return AlbumTemplateProfile(
        profile_name=profile_name or f"learned-album-{root_p.stem}",
        source_dxf_count=len(dxf_files),
        source_files=[f.name for f in dxf_files[:40]],
        sheet_size=chosen_sheet,
        sheet_width_mm=sw_mm,
        sheet_height_mm=sh_mm,
        margin_left_mm=_mode_or_default(margin_left_votes, 15.0 if chosen_sheet in {"A3", "A4"} else 25.0),
        margin_other_mm=_mode_or_default(margin_other_votes, 10.0),
        title_block_width_mm=tb_w,
        title_block_height_mm=tb_h,
        text_font=_mode_or_default(font_votes, "Deng.ttf"),
        title_font=_mode_or_default(title_font_votes, "simhei.ttf"),
        text_width_factor=_mode_or_default(width_votes, 0.85),
        layer_colors=learned_colors,
        layer_lineweights_mm=learned_lws,
        dim_arrow_style=_mode_or_default(arrow_votes, "archtick"),
        dim_text_height_mm=_mode_or_default(dimtxt_votes, 2.4),
        dim_arrow_size_mm=_mode_or_default(dimasz_votes, 1.8),
        dim_gap_mm=_mode_or_default(dimgap_votes, 0.7),
        drawing_no_style="prefixed" if chosen_prefix else "numeric_2digit",
        drawing_no_prefix=chosen_prefix,
        rebar_standard_note=_mode_or_default(rebar_std_hits, None),
        detected_sheet_index=[
            {"drawing_no": d, "title": t, "scale_label": sc} for d, t, sc in detected_dnos
        ],
    )


# ======================================================================
# 4. Unified `.cad-memory` Persistence & Loaders
# ======================================================================
def save_thesis_template(profile: ThesisTemplateProfile, path: str | Path) -> Path:
    dst = Path(path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json")
    if dst.suffix.lower() in {".yaml", ".yml"}:
        dst.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def save_album_template(profile: AlbumTemplateProfile, path: str | Path) -> Path:
    dst = Path(path).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json")
    if dst.suffix.lower() in {".yaml", ".yml"}:
        dst.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def load_thesis_template(
    source: ThesisTemplateProfile | Mapping[str, Any] | str | Path | None,
) -> ThesisTemplateProfile | None:
    if source is None:
        return None
    if isinstance(source, ThesisTemplateProfile):
        return source
    if isinstance(source, Mapping):
        if "thesis_template" in source and isinstance(source["thesis_template"], Mapping):
            return ThesisTemplateProfile.model_validate(source["thesis_template"])
        return ThesisTemplateProfile.model_validate(dict(source))
    p = Path(source).expanduser().resolve()
    if p.is_dir():
        for cand in (p / "thesis-template.json", p / "thesis-template.yaml", p / "template-bundle.json"):
            if cand.is_file():
                p = cand
                break
        else:
            return None
    if not p.is_file():
        return None
    if p.suffix.lower() == ".docx":
        return learn_thesis_template_from_docx(p)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.suffix.lower() in {".yaml", ".yml"} else json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(raw, Mapping) and "thesis_template" in raw and raw["thesis_template"] is not None:
        return ThesisTemplateProfile.model_validate(raw["thesis_template"])
    return ThesisTemplateProfile.model_validate(raw)


def load_album_template(
    source: AlbumTemplateProfile | Mapping[str, Any] | str | Path | None,
) -> AlbumTemplateProfile | None:
    if source is None:
        return None
    if isinstance(source, AlbumTemplateProfile):
        return source
    if isinstance(source, Mapping):
        if "album_template" in source and isinstance(source["album_template"], Mapping):
            return AlbumTemplateProfile.model_validate(source["album_template"])
        return AlbumTemplateProfile.model_validate(dict(source))
    p = Path(source).expanduser().resolve()
    if p.is_dir():
        for cand in (p / "album-template.json", p / "album-template.yaml", p / "template-bundle.json"):
            if cand.is_file():
                raw_c = yaml.safe_load(cand.read_text(encoding="utf-8")) if cand.suffix.lower() in {".yaml", ".yml"} else json.loads(cand.read_text(encoding="utf-8-sig"))
                if isinstance(raw_c, Mapping) and "album_template" in raw_c and raw_c["album_template"] is not None:
                    return AlbumTemplateProfile.model_validate(raw_c["album_template"])
                if isinstance(raw_c, Mapping) and raw_c.get("schema_version") == "cad-album-template/v1":
                    return AlbumTemplateProfile.model_validate(raw_c)
        # If directory contains raw `.dxf` files instead of `.cad-memory`, learn directly from it!
        if any(p.glob("*.dxf")) or any(p.rglob("*.dxf")):
            return learn_dxf_album_profile(p)
        return None
    if not p.is_file():
        return None
    if p.suffix.lower() == ".dxf":
        return learn_dxf_album_profile(p)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.suffix.lower() in {".yaml", ".yml"} else json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(raw, Mapping) and "album_template" in raw and raw["album_template"] is not None:
        return AlbumTemplateProfile.model_validate(raw["album_template"])
    return AlbumTemplateProfile.model_validate(raw)


def album_template_to_style_overrides(album: AlbumTemplateProfile | None) -> dict[str, Any]:
    """Convert a learned `AlbumTemplateProfile` into `setup_civil_dxf_document` style_overrides."""
    if album is None:
        return {}
    return {
        "font": album.text_font,
        "title_font": album.title_font,
        "text_width_factor": album.text_width_factor,
        "layer_colors": dict(album.layer_colors),
        "layer_lineweights_mm": dict(album.layer_lineweights_mm),
        "margin_left_mm": album.margin_left_mm,
        "margin_other_mm": album.margin_other_mm,
        "dim_arrow_style": album.dim_arrow_style,
        "dim_text_height_mm": album.dim_text_height_mm,
        "dim_arrow_size_mm": album.dim_arrow_size_mm,
        "dim_gap_mm": album.dim_gap_mm,
        "dim_stack_step_mm": album.dim_stack_step_mm,
        "schedule_dock": album.schedule_dock,
    }


def learn_template_styles(
    *,
    docx_path: str | Path | None = None,
    dxf_path_or_dir: str | Path | None = None,
    output_dir: str | Path = Path(".cad-memory"),
    profile_name: str | None = None,
    confirm_preferences: bool = True,
) -> dict[str, Any]:
    """Unified one-call learner for `.docx` thesis templates and/or `.dxf` sheet/album styles."""
    if docx_path is None and dxf_path_or_dir is None:
        raise ValueError("provide at least one of docx_path or dxf_path_or_dir")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    pref_mem_path = out_root / "project-preferences.json"

    thesis_prof: ThesisTemplateProfile | None = None
    thesis_json_path: Path | None = None
    if docx_path is not None:
        thesis_prof = learn_thesis_template_from_docx(docx_path, profile_name=profile_name)
        thesis_json_path = save_thesis_template(thesis_prof, out_root / "thesis-template.json")

    album_prof: AlbumTemplateProfile | None = None
    album_json_path: Path | None = None
    standard_yaml_path: Path | None = None
    if dxf_path_or_dir is not None:
        album_prof = learn_dxf_album_profile(dxf_path_or_dir, profile_name=profile_name)
        album_json_path = save_album_template(album_prof, out_root / "album-template.json")

        # Also emit mechanical/general DrawingStandard YAML & sync civil preference memory
        first_dxf = _collect_dxf_files(Path(dxf_path_or_dir).expanduser().resolve())[0]
        std_learned = learn_standard_from_dxf(
            first_dxf,
            profile_name=profile_name,
            preference_memory_path=pref_mem_path,
            confirm_preferences=confirm_preferences,
        )
        standard_yaml_path = save_standard_yaml(std_learned.standard, out_root / "learned-standard.yaml")

        # Record civil font & layer_colors into project-preferences.json
        store = load_preference_store(pref_mem_path)
        by_id = {e.pref_id: e for e in store.entries}
        ev = PreferenceEvidence(
            source_drawing=first_dxf.name,
            source_sha256=sha256_file(first_dxf),
            note="auto-learned from reference DXF album template",
        )
        status_v: Literal["confirmed", "candidate"] = "confirmed" if confirm_preferences else "candidate"
        for pid, pkey, pval in (
            ("civil_bridge:civil_text_font", "civil_text_font", album_prof.text_font),
            ("civil_bridge:civil_layer_colors", "civil_layer_colors", album_prof.layer_colors),
        ):
            if not pval:
                continue
            if pid in by_id:
                by_id[pid].value = pval
                by_id[pid].status = status_v
                by_id[pid].occurrences += 1
                by_id[pid].evidence.append(ev)
            else:
                store.entries.append(
                    PreferenceEntry(
                        pref_id=pid,
                        part_family="civil_bridge",
                        key=pkey,  # type: ignore[arg-type]
                        value=pval,
                        status=status_v,
                        occurrences=1,
                        evidence=[ev],
                    )
                )
        save_preference_store(store, pref_mem_path)

    bundle_path = out_root / "template-bundle.json"
    bundle_payload = {
        "schema_version": "cad-template-bundle/v1",
        "output_dir": str(out_root),
        "thesis_template_path": str(thesis_json_path) if thesis_json_path else None,
        "album_template_path": str(album_json_path) if album_json_path else None,
        "standard_yaml_path": str(standard_yaml_path) if standard_yaml_path else None,
        "preference_memory_path": str(pref_mem_path) if pref_mem_path.is_file() else None,
        "thesis_template": thesis_prof.model_dump(mode="json") if thesis_prof else None,
        "album_template": album_prof.model_dump(mode="json") if album_prof else None,
    }
    bundle_path.write_text(json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "learned",
        "bundle_path": str(bundle_path),
        **bundle_payload,
    }
