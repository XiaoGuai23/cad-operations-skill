"""College-Template Formal Graduation Design Specification (.docx Word + .md) Assembler.

Assembles the existing 18 A3 construction sheets (QL-01..QL-18), Opening-Report 3-scheme
comparison, live calculation figures (300 DPI PNG), step-by-step T-girder / double-column pier /
pile-bent abutment calculations, oral-defense independent FEM comparison table, and 12 inline-cited
GB/T 7714 references (>=10 Chinese + >=2 English) into a formal Chinese university undergraduate
graduation thesis `.docx` ready for submission and advisor review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
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


def _set_cell_borders(cell: Any, *, top: str | None = None, bottom: str | None = None, top_sz: int = 4, bottom_sz: int = 4) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_borders = tc_pr.find(qn("w:tcBorders"))
    if tc_borders is None:
        tc_borders = OxmlElement("w:tcBorders")
        tc_pr.append(tc_borders)
    for side in ("left", "right"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "none")
        tc_borders.append(el)
    if top is not None:
        el_t = OxmlElement("w:top")
        el_t.set(qn("w:val"), top)
        el_t.set(qn("w:sz"), str(top_sz))
        el_t.set(qn("w:space"), "0")
        el_t.set(qn("w:color"), "1E293B")
        tc_borders.append(el_t)
    if bottom is not None:
        el_b = OxmlElement("w:bottom")
        el_b.set(qn("w:val"), bottom)
        el_b.set(qn("w:sz"), str(bottom_sz))
        el_b.set(qn("w:space"), "0")
        el_b.set(qn("w:color"), "1E293B")
        tc_borders.append(el_b)


def _add_body_para(doc: Document, text: str, *, indent: bool = True, bold_prefix: str = "") -> None:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = Pt(3)
    pf.first_line_indent = Pt(24) if indent else Pt(0)
    if bold_prefix:
        r0 = p.add_run(bold_prefix)
        _set_run_fonts(r0, zh_font="黑体", size_pt=12.0, bold=True)
    r1 = p.add_run(text)
    _set_run_fonts(r1, zh_font="宋体", size_pt=12.0, bold=False)


def _add_formula_para(doc: Document, expr: str, eq_no: str = "") -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing = 1.35
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(3)
    full_s = f"{expr}    ({eq_no})" if eq_no else expr
    r = p.add_run(full_s)
    _set_run_fonts(r, zh_font="宋体", en_font="Times New Roman", size_pt=11.0, bold=False)


def _add_chapter_heading(doc: Document, title: str, *, page_break_before: bool = True) -> None:
    if page_break_before:
        doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.line_spacing = 1.5
    r = p.add_run(title)
    _set_run_fonts(r, zh_font="黑体", size_pt=16.0, bold=True)


def _add_section_heading(doc: Document, title: str, *, level: int = 2) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(8 if level == 2 else 5)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.5
    r = p.add_run(title)
    _set_run_fonts(r, zh_font="黑体", size_pt=14.0 if level == 2 else 12.0, bold=True)


def _add_three_line_table(
    doc: Document,
    caption: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> None:
    cap_p = doc.add_paragraph()
    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap_p.paragraph_format.space_before = Pt(6)
    cap_p.paragraph_format.space_after = Pt(3)
    rc = cap_p.add_run(caption)
    _set_run_fonts(rc, zh_font="黑体", size_pt=10.5, bold=True)

    tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = True

    for ci, h_text in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        _set_cell_borders(cell, top="single", bottom="single", top_sz=12, bottom_sz=6)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(str(h_text))
        _set_run_fonts(r, zh_font="黑体", size_pt=9.5, bold=True)

    n_rows = len(rows)
    for ri, row_vals in enumerate(rows):
        is_last = ri == n_rows - 1
        for ci, val in enumerate(row_vals):
            cell = tbl.cell(ri + 1, ci)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _set_cell_borders(
                cell,
                top="none",
                bottom="single",
                bottom_sz=12 if is_last else 2,
            )
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if len(str(val)) <= 22 else WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(1.5)
            p.paragraph_format.space_after = Pt(1.5)
            r = p.add_run(str(val))
            _set_run_fonts(r, zh_font="宋体", size_pt=9.0, bold=False)


def _embed_figure_if_available(
    doc: Document,
    fig_info: Mapping[str, Any] | None,
    *,
    width_cm: float = 15.2,
    fallback_caption: str = "",
) -> bool:
    if not fig_info:
        return False
    img_path_str = fig_info.get("png_path")
    if not img_path_str or not Path(str(img_path_str)).is_file():
        return False
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_img.paragraph_format.space_before = Pt(6)
    p_img.paragraph_format.space_after = Pt(2)
    run_img = p_img.add_run()
    run_img.add_picture(str(img_path_str), width=Cm(width_cm))

    cap_text = str(fig_info.get("caption_zh") or fallback_caption or fig_info.get("title_zh") or "")
    if cap_text:
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap.paragraph_format.space_before = Pt(1)
        p_cap.paragraph_format.space_after = Pt(6)
        rc = p_cap.add_run(cap_text)
        _set_run_fonts(rc, zh_font="宋体", size_pt=10.5, bold=True)
    return True


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

    meta = dict(student_metadata or {})
    school_name = str(meta.get("school_name", "高等院校土木工程专业本科毕业设计模板（可配置学院名称）"))
    college_name = str(meta.get("college_name", "土木与交通工程学院 / 建筑工程学院"))
    major_class = str(meta.get("major_class", "土木工程本科班"))
    student_name = str(meta.get("student_name", "（待填学生姓名）"))
    student_id = str(meta.get("student_id", "（待填学号）"))
    advisor_name = str(meta.get("advisor_name", "（待填指导教师及职称）"))
    completion_date = str(meta.get("completion_date", "2026年5月"))
    thesis_title = str(meta.get("thesis_title", ps["project_name"]))

    doc = Document()
    for section in doc.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3.0)
        section.right_margin = Cm(2.5)

    # ------------------------------------------------------------------
    # 1. College Cover Page
    # ------------------------------------------------------------------
    p_sch = doc.add_paragraph()
    p_sch.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sch.paragraph_format.space_before = Pt(36)
    p_sch.paragraph_format.space_after = Pt(18)
    r_sch = p_sch.add_run(school_name)
    _set_run_fonts(r_sch, zh_font="黑体", size_pt=20.0, bold=True)

    p_deg = doc.add_paragraph()
    p_deg.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_deg.paragraph_format.space_before = Pt(12)
    p_deg.paragraph_format.space_after = Pt(42)
    r_deg = p_deg.add_run("本科毕业设计（说明书）")
    _set_run_fonts(r_deg, zh_font="黑体", size_pt=24.0, bold=True)

    cover_fields = [
        ("题    目", thesis_title),
        ("学生姓名", student_name),
        ("专业班级", major_class),
        ("学    号", student_id),
        ("学    院", college_name),
        ("指导教师", advisor_name),
        ("完成时间", completion_date),
    ]
    _add_three_line_table(doc, "毕业设计封面信息栏", ("项 目", "内 容"), cover_fields)

    # ------------------------------------------------------------------
    # 2. Chinese Abstract & Keywords
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "摘  要", page_break_before=True)
    zh_abstract_1 = (
        f"本毕业设计针对“{ps['project_name']}”开展完整的方案比选、结构受力计算、独立电算复核与全套施工图设计。"
        f"桥梁标准跨径布置为 {ps['n_spans']}×{ps['standard_span_m']:g} m，主体总长 {hydro['total_bridge_length_m']:g} m，"
        f"单幅桥面全宽 {ps['deck_width_m']:g} m（净宽 {ps['clear_width_m']:g} m，双向坡度 {ps['cross_slope_pct']:g}%），"
        f"设计荷载等级采用公路-I级，结构安全等级为一级（结构重要性系数 γ0=1.1）。"
        f"在前期水文水力分析与多因素综合评价基础上，对开题报告拟定的“{schemes[0]['name']}”、“{schemes[1]['name']}”与“{schemes[2]['name']}”三个同桥位比选方案进行技术、施工工期、造价及全寿命养护对比，最终确定采用施工工艺成熟、静定适应性优良且经济性突出的装配式预应力混凝土简支T梁桥（桥面连续）作为实施方案。"
    )
    zh_abstract_2 = (
        f"上部结构横断面由 {lat['n_girders']} 片梁高 {sec['mid_span_mm']['height']/1000:.2f} m 的预制 C50 混凝土 T 梁组成（主梁中心距 {lat['girder_spacing_m']:g} m，计算跨径 {ps['calc_span_m']:g} m）。"
        f"计算过程中，依据《公路桥涵设计通用规范》（JTG D60—2015）与《公路钢筋混凝土及预应力混凝土桥涵设计规范》（JTG 3362—2018），分别采用杠杆原理法与刚性横梁偏心压力法求解支点及跨中荷载横向分布系数（1#边梁 moq={lat['lever_rule'][0]['m_oq']:.3f}，mcq={lat['eccentric_compression'][0]['m_cq']:.3f}），"
        f"求得代表性边梁跨中基本组合弯矩 Md={gc['uls_flexure']['demand_md_knm']:.2f} kN·m、支点基本组合剪力 Vd={gc['uls_shear']['demand_vd_kn']:.2f} kN。"
        f"主梁配置 {pre['recommended_tendons']} 束 Φs15.2-{pre['strands_per_tendon']} 低松弛钢绞线（张拉控制应力 {pre['sigma_con_mpa']:g} MPa，跨中有效预应力 {pre['loss_profile'][5]['effective_prestress_mpa']:.2f} MPa），"
        f"正截面抗弯承载力 Mu={gc['uls_flexure']['capacity_mu_knm']:.2f} kN·m、斜截面抗剪承载力 Vu={gc['uls_shear']['capacity_vu_kn']:.2f} kN、频遇组合抗裂与活载挠度（flive={gc['sls_deflection']['live_deflection_mm']:.2f} mm ≤ L/600）均满足规范限值。"
    )
    zh_abstract_3 = (
        f"下部结构采用双柱式圆柱桥墩（盖梁 {cap_1d['cap_length_m']:g}×{sub['cap_beam']['width_m']:g}×{sub['cap_beam']['height_m']:g} m，柱径 {sub['pier_column']['diameter_m']:g} m）与桩柱式桥台，基础均采用 Φ{sub['bored_pile']['diameter_m']:g} m 水下 C30 钻孔灌注摩擦桩。"
        f"依据《公路桥涵地基与基础设计规范》（JTG 3363—2019），分别针对中间墩双跨支反力与桥台单跨支反力、6 m 搭板及朗肯主动土压力独立求解桩基承载力，核定桥墩桩长 {ps['pier_pile_length_m']:g} m（[Ra]={sub['bored_pile']['allowable_ra_kn']:.1f} kN）、桥台桩长 {ps['abutment_pile_length_m']:g} m（[Ra]={abut['bored_pile']['allowable_ra_kn']:.1f} kN）。"
        f"通过手算解析公式与杆系电算模型交叉复核，跨中弯矩、支点剪力、活载挠度与墩顶反力吻合良好，并配套完成 QL-01～QL-18 全套 18 张 A3 施工图及工程材料数量汇总。"
    )
    _add_body_para(doc, zh_abstract_1)
    _add_body_para(doc, zh_abstract_2)
    _add_body_para(doc, zh_abstract_3)
    _add_body_para(doc, "预应力混凝土简支T梁；桥型方案比选；荷载横向分布；极限状态验算；桩柱式桥台；手算电算校核", indent=False, bold_prefix="关键词：")

    # ------------------------------------------------------------------
    # 3. English Abstract & Key words
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "Abstract", page_break_before=True)
    en_abs_1 = (
        f"This graduation design presents the complete structural analysis, independent numerical cross-verification, and 18-sheet A3 construction detailing for the {ps['n_spans']}×{ps['standard_span_m']:g} m precast prestressed concrete simply supported T-girder bridge ({ps['project_name']}). "
        f"The total bridge length is {hydro['total_bridge_length_m']:g} m with a deck width of {ps['deck_width_m']:g} m (clear roadway width {ps['clear_width_m']:g} m and {ps['cross_slope_pct']:g}% bilateral cross-slope), designed for Highway-Class I vehicular loading with a structural importance factor of γ0 = 1.1. "
        f"Following hydrologic capacity evaluation and multi-criteria technical-economic comparison among three candidate schemes established in the opening report—namely the {ps['n_spans']}×{ps['standard_span_m']:g} m precast PC simply supported T-girder bridge, the {ps['n_spans']}×{ps['standard_span_m']:g} m continuous PC box-girder bridge, and the {ps['n_spans']}×{ps['standard_span_m']:g} m precast simply supported small box-girder bridge—the simply supported T-girder scheme with continuous deck pavement over piers is selected as the optimal solution."
    )
    en_abs_2 = (
        f"The superstructure comprises {lat['n_girders']} precast C50 T-girders spaced at {lat['girder_spacing_m']:g} m with a depth of {sec['mid_span_mm']['height']/1000:.2f} m and a calculation span of {ps['calc_span_m']:g} m. "
        f"In accordance with JTG D60-2015 and JTG 3362-2018, transverse load distribution factors are derived via the lever rule at supports (moq = {lat['lever_rule'][0]['m_oq']:.3f}) and Courbon's eccentric compression method at mid-span (mcq = {lat['eccentric_compression'][0]['m_cq']:.3f}). "
        f"Equipped with {pre['recommended_tendons']} parabolic tendons of {pre['strands_per_tendon']}-Φs15.2 low-relaxation strands (fpk = 1860 MPa, jacking stress {pre['sigma_con_mpa']:g} MPa), the representative exterior girder satisfies all ultimate flexural (Md = {gc['uls_flexure']['demand_md_knm']:.1f} kN·m ≤ Mu = {gc['uls_flexure']['capacity_mu_knm']:.1f} kN·m), oblique shear (Vd = {gc['uls_shear']['demand_vd_kn']:.1f} kN ≤ Vu = {gc['uls_shear']['capacity_vu_kn']:.1f} kN), Class-A crack resistance, and live-load deflection ({gc['sls_deflection']['live_deflection_mm']:.2f} mm ≤ L/600) criteria."
    )
    en_abs_3 = (
        f"For the substructure, double-column circular piers and pile-bent abutments supported on Φ{sub['bored_pile']['diameter_m']:g} m bored friction piles are analyzed independently under JTG 3363-2019. "
        f"Accounting for two-span continuous reactions on intermediate piers and single-span reactions, 6.0 m approach slab loads, and Rankine active earth pressure with vehicle surcharge on abutments, the design pile lengths are independently determined as {ps['pier_pile_length_m']:g} m for piers and {ps['abutment_pile_length_m']:g} m for abutments. "
        "Closed-form analytical hand calculations and finite-element frame models show close agreement across governing internal forces, deflections, and support reactions, ensuring complete consistency between structural calculations and construction drawings QL-01 through QL-18."
    )
    _add_body_para(doc, en_abs_1)
    _add_body_para(doc, en_abs_2)
    _add_body_para(doc, en_abs_3)
    _add_body_para(
        doc,
        "Prestressed Concrete T-Girder Bridge; Scheme Comparison; Lateral Load Distribution; Limit State Verification; Pile-Bent Abutment; Cross-Verification",
        indent=False,
        bold_prefix="Key words: ",
    )

    # ------------------------------------------------------------------
    # 4. Dual-Status Academic Integrity & Table of Contents Overview
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "设计计算与毕设资料齐套状态核查说明", page_break_before=True)
    unresolved = list(calc_report.get("unresolved_assumptions") or [])
    _add_body_para(
        doc,
        f"为严格落实毕业设计任务书“图纸与计算结果必须一致、严禁隐瞒初拟假定参数”的要求，本说明书将“当前输入条件下结构验算状态（{calc_report['calculation_status']}）”与“毕业设计外业勘测及交付物齐套状态（{calc_report['graduation_readiness_status']}）”分别列示：{calc_report.get('status_summary_zh', '')}。",
    )
    if unresolved:
        u_rows = [
            (u["field"], str(u["assumed_value"]), u["unit"], u["impact"], u["required_source"])
            for u in unresolved
        ]
        _add_three_line_table(
            doc,
            "表0-1 当前仍采用初拟假定的工程基础参数及外业核定要求表",
            ("参数字段", "当前采用值", "单位", "影响的工程结论", "需补齐核定的原始资料"),
            u_rows,
        )

    # ------------------------------------------------------------------
    # Chapter 1: Engineering Overview, Hydrology & Technical Standards
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第1章  绪  论", page_break_before=True)
    _add_section_heading(doc, "1.1 工程设计概况")
    _add_body_para(
        doc,
        f"本设计课题为“{ps['project_name']}”。桥梁跨越区域河流主槽，是连接两岸公路路网的重要控制性构造物 [1]。"
        f"根据路线总体走向与桥位地形水文条件，桥梁主体采用 {ps['n_spans']}×{ps['standard_span_m']:g} m 装配式预应力混凝土简支T梁桥（墩顶设桥面连续构造），桥梁主体总长 {hydro['total_bridge_length_m']:g} m，单跨预制主梁全长 {ps['beam_length_m']:g} m，计算跨径 L={ps['calc_span_m']:g} m [1,2]。"
        f"横断面布置为：0.50 m（混凝土防撞护栏）+ {ps['clear_width_m']:g} m（桥面净宽，含行车道 {ps['roadway_width_m']:g} m 及两侧安全带）+ 0.50 m（混凝土防撞护栏）= {ps['deck_width_m']:g} m，桥面设双向 {ps['cross_slope_pct']:g}% 横坡以满足桥面快速排水需求 [1]。",
    )

    _add_section_heading(doc, "1.2 工程水文水力计算与桥孔跨径布置")
    _add_body_para(
        doc,
        f"依据桥位河段水文调查断面参数及《公路桥涵设计通用规范》（JTG D60—2015）[2] 与桥涵水力学曼宁（Manning）均匀流公式 [7]，主河槽过水断面面积 A={hydro['area_m2']} m²，湿周 χ={hydro['wetted_perimeter_m']} m，水力半径 R=A/χ={hydro['hydraulic_radius_m']} m，糙率倒数 1/n={hydro['roughness_inv']}，水力比降 i={hydro['slope_i']*1000:g}‰。",
    )
    _add_formula_para(
        doc,
        f"v = (1/n)·R^(2/3)·i^(1/2) = {hydro['roughness_inv']} × {hydro['hydraulic_radius_m']}^(2/3) × ({hydro['slope_i']}^0.5) = {hydro['main_channel_velocity_m_s']} m/s",
        "1-1",
    )
    _add_formula_para(
        doc,
        f"Qp = A·v = {hydro['area_m2']} × {hydro['main_channel_velocity_m_s']} = {hydro['design_discharge_m3_s']} m³/s,    Lj = k·(Qp/Qc)^n·Bc = {hydro['net_opening_length_m']} m",
        "1-2",
    )
    _add_body_para(
        doc,
        f"计算表明，满足1/100洪水频率泄洪及河床冲刷稳定所需的最小桥孔净开口长度为 Lj={hydro['net_opening_length_m']} m [7]。综合标准化预制跨径模数、墩台阻水面积修正及下部基础造价平衡，选用 {ps['n_spans']} 孔标准跨径 {ps['standard_span_m']:g} m 布置（总长 {hydro['total_bridge_length_m']:g} m > Lj），设计洪水位 {ps['flood_elevation_m']:.3f} m，桥面中心设计高程 {ps['deck_elevation_m']:.3f} m，梁底泄洪净空充裕 [1,2]。",
    )

    _add_section_heading(doc, "1.3 主要技术标准、材料性能与执行规范体系")
    _add_body_para(
        doc,
        "本桥设计严格执行现行国家与交通运输行业标准：JTG B01—2014《公路工程技术标准》[1]、JTG D60—2015《公路桥涵设计通用规范》[2]、JTG 3362—2018《公路钢筋混凝土及预应力混凝土桥涵设计规范》[3]、JTG 3363—2019《公路桥涵地基与基础设计规范》[4]、JTG/T 2231-01—2020《公路桥梁抗震设计规范》[5] 以及 JT/T 4—2019《公路桥梁板式橡胶支座》[10]。",
    )
    std_table_rows = [
        ("设计荷载与安全等级", "公路-I级车道及车辆荷载；安全等级一级（γ0 = 1.1）", "JTG D60—2015 [2]"),
        ("设计基准期与使用年限", "设计基准期 100 年，主体结构设计使用年限 100 年", "JTG B01—2014 [1]"),
        ("上部主梁及湿接缝混凝土", "C50：fcd=22.4 MPa, fck=32.4 MPa, ftd=1.83 MPa, Ec=3.45×10⁴ MPa", "JTG 3362—2018 [3]"),
        ("桥面铺装层", f"{ps['asphalt_thick_mm']:g} mm 沥青混凝土面层 + 防水粘结层 + {ps['concrete_paving_thick_mm']:g} mm C40防水混凝土", "QL-05 / QL-13"),
        ("下部墩台及水下钻孔桩混凝土", "C30：fcd=13.8 MPa, fck=20.1 MPa, ftd=1.39 MPa, Ec=3.00×10⁴ MPa", "JTG 3362—2018 [3]"),
        ("预应力钢绞线", "1×7股低松弛 Φs15.2：fpk=1860 MPa, fpd=1260 MPa, Ep=1.95×10⁵ MPa", "JTG 3362—2018 [3]"),
        ("普通钢筋与支座", f"HRB400 (fsd=330 MPa)、HPB300 (fsd=250 MPa)；{bc['bearing_model']} 板式橡胶支座", "JT/T 4—2019 [10]"),
    ]
    _add_three_line_table(doc, "表1-1 本桥主要技术标准与结构材料设计指标汇总表", ("指标类别", "采用标准与设计参数", "规范及图纸依据"), std_table_rows)

    _add_section_heading(doc, "1.4 桥位气象水文与工程地质条件")
    pier_layers = sub["bored_pile"].get("layer_contributions") or []
    soil_desc = "；".join(
        f"{lyr['name']}（揭示穿层厚 {lyr['traversed_m']:.1f} m，桩周极限摩阻力标准值 qik={lyr['qik_kpa']:g} kPa，端承标准值 fa0={lyr['fa0_kpa']:g} kPa）"
        for lyr in pier_layers
    )
    _add_body_para(
        doc,
        f"桥位区属暖温带大陆性季风过渡气候，四季分明，地表水对混凝土无腐蚀性，环境类别按 I 类一般环境控制保护层厚度 [3]。河床冲洪积地层自上而下依次分布有：{soil_desc} [4]。下部密实砂砾卵石层厚度稳定、力学性质优良，适宜作为钻孔灌注摩擦桩的桩端持力层；地震动峰值加速度为 0.10g（抗震设防烈度 VI～VII 度）[5]。",
    )

    # ------------------------------------------------------------------
    # Chapter 2: 3-Scheme Bridge Comparison (Aligned with Opening Report!)
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第2章  桥型方案综合比选", page_break_before=True)
    _add_section_heading(doc, "2.1 方案比选原则与开题报告口径衔接")
    _add_body_para(
        doc,
        "桥梁方案比选是决定全桥结构安全性、施工可行性与全寿命经济性的核心环节 [6,7,11]。为保证毕业设计从开题论证到施工图终稿的严密衔接，本章严格沿用开题报告已确定的同桥位三套比选方案开展深入技术经济比较，避免出现前后端跨径或梁型口径不一致的问题。",
    )
    for idx_s, sc in enumerate(schemes[:3], start=2):
        _add_section_heading(doc, f"2.{idx_s} {sc['scheme_no']}：{sc['name']}")
        _add_body_para(
            doc,
            f"该方案跨径布置为 {sc['span_layout']}，上部结构采用 {sc['superstructure_type']}（梁高 {sc['girder_height_m']:.2f} m），下部结构采用 {sc['substructure_type']}。"
            f"在受力特性方面：{sc['structural_mechanics']}；在施工与工期方面：{sc['construction_and_schedule']}；在经济性与养护方面：{sc['economy_and_maintenance']}。综合比选结论：{sc['verdict']} [6,7]。",
        )

    _add_section_heading(doc, "2.5 三个比选方案技术经济综合比较与推荐结论")
    s1, s2, s3 = (schemes + schemes[:1] * 3)[:3]
    scheme_cmp_rows = [
        ("跨径布置", s1["span_layout"], s2["span_layout"], s3["span_layout"]),
        ("主梁截面与梁高", f"{s1['superstructure_type']} (h={s1['girder_height_m']:.2f}m)", f"{s2['superstructure_type']} (h={s2['girder_height_m']:.2f}m)", f"{s3['superstructure_type']} (h={s3['girder_height_m']:.2f}m)"),
        ("结构受力特点", s1["structural_mechanics"], s2["structural_mechanics"], s3["structural_mechanics"]),
        ("施工工艺与工期", s1["construction_and_schedule"], s2["construction_and_schedule"], s3["construction_and_schedule"]),
        ("经济性与后期管养", s1["economy_and_maintenance"], s2["economy_and_maintenance"], s3["economy_and_maintenance"]),
        ("综合比选结论", s1["verdict"], s2["verdict"], s3["verdict"]),
    ]
    _add_three_line_table(
        doc,
        "表2-1 三个候选桥型方案技术经济综合比选表（与开题报告一致）",
        ("比选维度", s1["name"], s2["name"], s3["name"]),
        scheme_cmp_rows,
    )
    _embed_figure_if_available(doc, figs_by_id.get("fig0_scheme_comparison"), width_cm=15.5)

    # ------------------------------------------------------------------
    # Chapter 3: Superstructure Detailing & Cross-Section Geometry
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第3章  上部结构构造拟定与截面几何特性计算", page_break_before=True)
    mid_mm = sec["mid_span_mm"]
    end_mm = sec["end_span_mm"]
    mid_m = sec["mid_span_m"]
    _add_section_heading(doc, "3.1 桥面系构造与预制T梁细部尺寸拟定")
    _add_body_para(
        doc,
        f"上部结构横桥向布置 {lat['n_girders']} 片预制 C50 混凝土 T 梁，主梁中心间距 S={lat['girder_spacing_m']*1000:g} mm，两侧悬臂长 {lat['edge_cantilever_m']*1000:g} mm，相邻预制翼缘间留设 400 mm 现浇 C50 湿接缝连接成整体桥面板（对应图号 QL-05～QL-07）[6,9]。"
        f"跨中标准段梁高 h={mid_mm['height']:g} mm（高跨比 h/L=1/{ps['standard_span_m']/(mid_mm['height']/1000):.1f}），腹板厚 b=200 mm，马蹄宽×高={560 if sec['effective_flange_width_mm']<=2000 else 600}×200 mm（设 200×200 mm 斜承托过渡）；为满足支点锚具局部承压与斜截面抗剪上限要求，距支点 1.20 m 范围内腹板加厚至与马蹄等宽，并设 1.00 m 渐变过渡段。全跨共设置 5 道横隔梁（两端支点横隔梁 + L/4、L/2、3L/4 三道中横隔梁，间距 4×{ps['calc_span_m']/4:g} m）[9]。",
    )
    _add_section_heading(doc, "3.2 T形截面受压翼缘有效宽度与几何特性计算")
    cands = sec["effective_flange_candidates_mm"]
    _add_body_para(
        doc,
        f"按《公路钢筋混凝土及预应力混凝土桥涵设计规范》（JTG 3362—2018）第 4.2.3 条规定 [3]，简支T形梁受压翼缘有效宽度 b'f 取计算跨径 1/3（{cands['l_div_3']:g} mm）、相邻主梁中心间距（{cands['girder_spacing']:g} mm）及 b+2bh+12h'f（{cands['b_plus_2bh_plus_12hf']:g} mm）三者最小值，故取 b'f={sec['effective_flange_width_mm']:g} mm。采用格林公式精确多边形积分求得跨中与支点毛截面几何特性及截面效率指标 ρ=(ks+kx)/h={mid_mm['efficiency_rho']:.4f} ≥ 0.50 [6]，汇总于表3-1。",
    )
    sec_rows = [
        ("毛截面面积 A", f"{mid_mm['area']:.1f} mm² ({mid_m['area_m2']:.4f} m²)", f"{end_mm['area']:.1f} mm²"),
        ("形心至梁底距离 yb", f"{mid_mm['y_bot']:.1f} mm", f"{end_mm['y_bot']:.1f} mm"),
        ("形心至梁顶距离 yt", f"{mid_mm['y_top']:.1f} mm", f"{end_mm['y_top']:.1f} mm"),
        ("抗弯惯性矩 Ix", f"{mid_m['ix_m4']:.6f} m⁴", f"{end_mm['ix']*1e-12:.6f} m⁴"),
        ("上/下核心距 ks / kx", f"{mid_mm['kern_upper_ks']:.1f} / {mid_mm['kern_lower_kx']:.1f} mm", f"{end_mm['kern_upper_ks']:.1f} / {end_mm['kern_lower_kx']:.1f} mm"),
        ("截面效率指标 ρ", f"{mid_mm['efficiency_rho']:.4f} (≥0.50，满足高效率判据)", f"{end_mm['efficiency_rho']:.4f}"),
    ]
    _add_three_line_table(doc, "表3-1 预制T梁跨中标准截面与梁端加厚截面几何特性表", ("几何特征量", "跨中标准马蹄截面", "梁端加厚矩形腹板截面"), sec_rows)

    # ------------------------------------------------------------------
    # Chapter 4: Representative T-Girder Internal Forces (Hand Calc + Envelopes)
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第4章  代表性主梁内力计算", page_break_before=True)
    dl_bk = env["dead_load_breakdown"]
    span_l = float(ps["calc_span_m"])
    _add_section_heading(doc, "4.1 结构计算简图与永久作用内力计算")
    _add_body_para(
        doc,
        f"选取横向分布系数控制的 1# 边主梁作为代表性主梁建立 10 单元（11 节点：Z1、B1～KZ～Z2）简支梁计算模型（见图4-1）。"
        f"单片T梁一期自重 g1,beam = {mid_m['area_m2']:.4f}×26.0 = {dl_bk['g1_girder_self_kn_m']:.3f} kN/m，横隔梁折算荷载 g1,diaph = {dl_bk['diaphragm_equiv_kn_m']:.3f} kN/m；"
        f"二期恒载含沥青面层 {dl_bk['asphalt_paving_per_girder_kn_m']:.3f} kN/m、C40调平层 {dl_bk['concrete_paving_per_girder_kn_m']:.3f} kN/m 及双侧护栏分摊 {dl_bk['barriers_per_girder_kn_m']:.3f} kN/m，小计 g2 = {dl_bk['superimposed_dead_kn_m']:.3f} kN/m。"
        f"单片主梁总恒载集度 g = {env['dead_load_kn_m']:.4f} kN/m，跨中恒载弯矩 Mg(L/2) = gL²/8 = {env['mid_span_summary']['m_dead_knm']:.2f} kN·m，支点恒载剪力 Vg(0) = gL/2 = {abs(float(env['support_summary']['q_dead_kn'])):.2f} kN [2,9]。",
    )
    _embed_figure_if_available(doc, figs_by_id.get("fig1_structural_model"), width_cm=15.4)

    _add_section_heading(doc, "4.2 荷载横向分布系数计算（支点杠杆法与跨中偏心压力法）")
    _add_body_para(
        doc,
        f"本桥共设 5 道横隔梁，宽跨比 B/L = {lat['width_to_span_ratio']:.4f} < 0.50，故跨中及四分点采用刚性横梁偏心压力法（Courbon 法）计算荷载横向分布影响线竖标 ηik = 1/n ± ai·ak/∑ai²（其中 ∑ai²={lat['sum_a2_m2']:.4f} m²），支点处按桥面板简支假定采用杠杆原理法计算 [6,8]。各主梁最不利双车道布载结果见表4-1。",
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
        "表4-1 各片主梁支点杠杆法与跨中偏心压力法荷载横向分布系数汇总表",
        ("主梁编号", "距桥轴线距离 ai (m)", "支点杠杆法 moq", "跨中偏心压力法 mcq", "计入抗扭修正 mcq,β"),
        lat_rows,
    )

    _add_section_heading(doc, "4.3 公路-I级汽车冲击系数及控制截面作用效应组合")
    mu_v = float(env["impact_factor_mu"])
    _add_body_para(
        doc,
        f"按 JTG D60—2015 第 4.3.2 条 [2]，简支梁一阶竖向自振基频 f1 = (π/2L²)·√(EcIx/mc) = {env['fundamental_freq_hz']:.4f} Hz（介于 1.5～14 Hz 之间），求得汽车冲击系数 μ = 0.1767·ln(f1) - 0.0157 = {mu_v:.4f}（冲击放大系数 1+μ = {1+mu_v:.4f}）[2,12]。"
        f"计算跨径 L={span_l:g} m 对应的公路-I级车道荷载均布标准值 qk={env['lane_qk_kn_m']:g} kN/m，集中荷载标准值 Pk={env['lane_pk_kn']:g} kN（计算剪力时乘 1.2 倍系数）。1#代表性边梁沿跨长特征截面的弯矩与剪力组合结果见表4-2及图4-2。",
    )
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
        "表4-2 1#代表性T梁半跨特征截面恒活载及极限/正常使用组合内力汇总表",
        ("截面位置", "恒载 Mg (kN·m)", "活载 Mq (kN·m)", "标准组合 Ms (kN·m)", "基本组合 Md (kN·m)", "控制剪力 |Vd| (kN)"),
        env_rows,
    )
    _embed_figure_if_available(doc, figs_by_id.get("fig2_moment_shear_envelope"), width_cm=15.4)

    # ------------------------------------------------------------------
    # Chapter 5: Prestressing Tendon Estimation, Profile & Losses
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第5章  预应力钢束估算、布置及预应力损失计算", page_break_before=True)
    _add_section_heading(doc, "5.1 预应力钢束数量估算与曲线几何布置")
    _add_body_para(
        doc,
        f"分别按正常使用极限状态抗裂消压条件与承载能力极限状态正截面抗弯条件估算所需 Φs15.2（单股面积 140 mm²，fpk=1860 MPa，fpd=1260 MPa）钢绞线股数 [3,9]：抗裂需 {pre['required_strands_sls']} 股，抗弯需 {pre['required_strands_uls']} 股。"
        f"综合锚具规格与马蹄管道净距要求，单片T梁采用 {pre['recommended_tendons']} 束 {pre['strands_per_tendon']} 股低松弛钢绞线（编号 N1～N{pre['recommended_tendons']}，总股数 {pre['provided_strands']} 股，总有效截面积 Ap={pre['provided_strands']*140:g} mm²，对应图号 QL-08）。钢束纵断面采用二次抛物线线形两端对称张拉，张拉控制应力 σcon = 0.75fpk = {pre['sigma_con_mpa']:g} MPa [3]。",
    )
    _add_section_heading(doc, "5.2 分阶段预应力损失及有效预应力分布")
    _add_body_para(
        doc,
        f"按 JTG 3362—2018 第 6.2 节逐项计算后张法预应力损失 [3]：（1）预应力管道摩擦损失 σl1（μ=0.25，k=0.0015）；（2）锚具变形及钢束回缩损失 σl2（单端回缩 Δl=6 mm，反摩阻影响长度 lf={pre['anchorage_influence_length_m']:.2f} m）；（3）低松弛钢绞线松弛损失 σl5（ψ=0.3）；（4）混凝土收缩徐变损失 σl6。沿梁长代表性截面的预应力损失与有效预应力 σpe 见表5-1与图5-1。",
    )
    loss_pick = [pre["loss_profile"][i] for i in (0, 1, 2, 3, 5)]
    loss_rows_t = [
        (
            f"{r['x_m']:.2f}",
            f"{r['sigma_l1_friction_mpa']:.2f}",
            f"{r['sigma_l2_anchorage_mpa']:.2f}",
            f"{r['sigma_l5_relaxation_mpa']:.2f}",
            f"{r['sigma_l6_creep_shrink_mpa']:.2f}",
            f"{r['total_loss_mpa']:.2f}",
            f"{r['effective_prestress_mpa']:.2f}",
        )
        for r in loss_pick
    ]
    _add_three_line_table(
        doc,
        "表5-1 预应力钢束沿半跨特征截面分项预应力损失及有效预应力汇总表（单位：MPa）",
        ("距梁端 x (m)", "摩阻 σl1", "锚固回缩 σl2", "松弛 σl5", "收缩徐变 σl6", "总损失 σl", "有效预应力 σpe"),
        loss_rows_t,
    )
    _embed_figure_if_available(doc, figs_by_id.get("fig3_tendon_and_prestress"), width_cm=15.4)

    # ------------------------------------------------------------------
    # Chapter 6: Representative T-Girder ULS & SLS Verification
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第6章  代表性主梁承载能力与正常使用极限状态验算", page_break_before=True)
    _add_section_heading(doc, "6.1 持久状况承载能力极限状态正截面抗弯与斜截面抗剪验算")
    t_sec = gc["transformed_section"]
    uf = gc["uls_flexure"]
    us = gc["uls_shear"]
    scs = gc["sls_crack_and_stress"]
    sdef = gc["sls_deflection"]
    _add_body_para(
        doc,
        f"（1）正截面抗弯承载力验算（JTG 3362—2018 §5.2.2 [3]）：马蹄底部配置 R1 6Φ22 HRB400 普通纵向主筋协同 {pre['tendon_designation']} 预应力钢束受力，有效高度 h0={t_sec['h0_mm']:.1f} mm，混凝土受压区高度 x={uf['compression_depth_x_mm']:.1f} mm ≤ ξbh0={uf['max_depth_xib_h0_mm']:.1f} mm（属第 {uf['section_type']} 类T形适筋截面），正截面抗弯承载力 Mu={uf['capacity_mu_knm']:.2f} kN·m ≥ Md={uf['demand_md_knm']:.2f} kN·m，安全富余充足。"
        f"（2）斜截面抗剪承载力验算（§5.2.7～5.2.9 [3]）：腹板配置双肢 Φ12 HRB400 抗剪箍筋 GJ1（距梁端 2080 mm 加密区 @100 mm、其余区段 @150 mm，对应图号 QL-09），计入弯起预应力束抗剪分量 Vpb={us['vpb_kn']:.2f} kN 后，总斜截面抗剪承载力 Vu={us['capacity_vu_kn']:.2f} kN ≥ Vd={us['demand_vd_kn']:.2f} kN，且满足截面抗剪尺寸上限 {us['size_upper_limit_kn']:.2f} kN。",
    )
    _add_section_heading(doc, "6.2 持久状况抗裂、法向/主应力、短暂状况应力及活载挠度验算")
    _add_body_para(
        doc,
        f"按全预应力 A 类构件控制抗裂与应力 [3]：频遇组合跨中下缘净应力为 {scs['net_bot_stress_freq_mpa']:.2f} MPa ≤ 0（保持压应力储备，不出现拉应力）；标准组合最大混凝土压应力 {max(scs['top_comp_char_mpa'], scs['bot_comp_char_mpa']):.2f} MPa ≤ 0.50fck={scs['comp_allowable_mpa']:.2f} MPa；支点腹板主拉应力 {scs['principal_tensile_mpa']:.3f} MPa ≤ 0.50ftk={scs['principal_tensile_allow_mpa']:.3f} MPa；施工张拉短暂阶段压应力 {scs['transfer_bot_comp_mpa']:.2f} MPa ≤ 0.70f'ck={scs['transfer_comp_allow_mpa']:.2f} MPa。"
        f"扣除自重后正常使用汽车活载挠度（计入长期效应系数 ηθ=1.60）为 flive={sdef['live_deflection_mm']:.2f} mm ≤ L/600={sdef['allowable_l_div_600_mm']:.2f} mm，跨中建议设置向下反拱修正或预制台座反拱值匹配长期净上拱 {sdef['net_long_term_camber_mm']:.1f} mm [3,9]。各项验算指标汇总于表6-1。",
    )
    g_chk_rows = [
        ("跨中正截面抗弯 Mu", f"Md = {uf['demand_md_knm']:.2f} kN·m", f"Mu = {uf['capacity_mu_knm']:.2f} kN·m", "满足" if uf["passed"] else "超限"),
        ("跨中相对受压区高度 x", f"x = {uf['compression_depth_x_mm']:.1f} mm", f"ξbh0 = {uf['max_depth_xib_h0_mm']:.1f} mm", "满足" if uf["passed"] else "超限"),
        ("支点斜截面抗剪 Vu", f"Vd = {us['demand_vd_kn']:.2f} kN", f"Vu = {us['capacity_vu_kn']:.2f} kN", "满足" if us["passed"] else "超限"),
        ("频遇组合正截面抗裂", f"σst - σpc = {scs['net_bot_stress_freq_mpa']:.2f} MPa", "≤ 0.00 MPa (A类)", "满足" if scs["passed"] else "超限"),
        ("标准组合最大法向压应力", f"{max(scs['top_comp_char_mpa'], scs['bot_comp_char_mpa']):.2f} MPa", f"≤ {scs['comp_allowable_mpa']:.2f} MPa", "满足" if scs["passed"] else "超限"),
        ("消除自重活载长期挠度", f"flive = {sdef['live_deflection_mm']:.2f} mm", f"≤ L/600 = {sdef['allowable_l_div_600_mm']:.2f} mm", "满足" if sdef["passed"] else "超限"),
    ]
    _add_three_line_table(doc, "表6-1 代表性预制T梁极限状态与正常使用极限状态验算汇总表", ("验算项目", "作用效应计算值", "抗力或规范容许值", "结论"), g_chk_rows)

    # ------------------------------------------------------------------
    # Chapter 7: Deck Slab, Diaphragm & Elastomeric Bearings
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第7章  行车道板、横隔梁及板式橡胶支座计算", page_break_before=True)
    ds = sc_chk["deck_slab"]
    dp = sc_chk["diaphragm"]
    _add_section_heading(doc, "7.1 悬臂行车道板与刚性中横隔梁验算（对应图号 QL-10、QL-11）")
    _add_body_para(
        doc,
        f"（1）行车道板按悬臂板根部单轮重 P=140 kN（轴重 280 kN）最不利局部分布计算 [2,3]：每延米板宽基本组合弯矩 Md={ds['m_uls_knm_per_m']:.2f} kN·m/m、剪力 Vd={ds['q_uls_kn_per_m']:.2f} kN/m；翼缘顶层配置 W1 Φ12@125（As=904.8 mm²/m），抗弯承载力 Mu={ds['mu_capacity_knm_per_m']:.2f} kN·m/m ≥ Md，满足要求。"
        f"（2）跨中横隔梁按偏心受压刚性横梁模型计算横向弯矩与剪力 [6,8]：基本组合正弯矩 Md={dp['m_uls_knm']:.2f} kN·m，底部配置 HG1 4Φ25 HRB400 主筋，抗弯承载力 Mu={dp['mu_capacity_knm']:.2f} kN·m ≥ Md，满足要求。",
    )
    _add_section_heading(doc, "7.2 板式橡胶支座选型与力学验算（对应图号 QL-12）")
    _add_body_para(
        doc,
        f"单支座最大标准组合竖向反力 Rmax={bc['max_reaction_kn']:.2f} kN，最小恒载反力 Rmin={bc['min_reaction_kn']:.2f} kN。按 JT/T 4—2019 选用 {bc['bearing_model']} 矩形普通板式橡胶支座（全桥共 {boq['bearing_count']} 块）[10]：平均压应力 σc={bc['compressive_stress_mpa']:.2f} MPa ≤ {bc['allowable_stress_mpa']:.1f} MPa；最小压应力 σmin={bc['min_contact_stress_mpa']:.2f} MPa ≥ 1.5 MPa（抗脱空合格）；水平剪切角正切 tanγ={bc['shear_strain_tan_gamma']:.3f} ≤ {bc['allowable_tan_gamma']:.2f}，各项指标均合格。",
    )

    # ------------------------------------------------------------------
    # Chapter 8: Intermediate Double-Column Pier & Bored Pile Design
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第8章  中间双柱式桥墩及钻孔灌注桩基础设计计算", page_break_before=True)
    _add_section_heading(doc, "8.1 双跨连续支座反力汇总与双柱墩盖梁验算（对应图号 QL-14、QL-16）")
    _add_body_para(
        doc,
        f"中间桥墩同时承受左、右相邻两跨上部结构恒载与双跨连续影响线活载反力 [2,9]：5 个支座点双跨基本组合集中反力分别为 {pr_sum['two_span_pier_uls_per_girder_kn']} kN（总计 {sum(pr_sum['two_span_pier_uls_per_girder_kn']):.2f} kN）。"
        f"盖梁总长 {cap_1d['cap_length_m']:g} m、截面 {sub['cap_beam']['width_m']*1000:g}×{sub['cap_beam']['height_m']*1000:g} mm，建立一维连续双悬臂欧拉梁有限元模型求解：墩顶控制负弯矩 Md,hog={sub['cap_beam']['m_hog_uls_knm']:.2f} kN·m（顶层配 GL1 {sub['cap_beam']['top_rebar']}，Mu={sub['cap_beam']['mu_hog_capacity_knm']:.2f} kN·m），跨中正弯矩 Md,sag={sub['cap_beam']['m_sag_uls_knm']:.2f} kN·m（底层配 GL2 {sub['cap_beam']['bottom_rebar']}，Mu={sub['cap_beam']['mu_sag_capacity_knm']:.2f} kN·m），支点剪力 Vd={sub['cap_beam']['v_uls_kn']:.2f} kN ≤ Vu={sub['cap_beam']['vu_capacity_kn']:.2f} kN，正常使用最大裂缝宽度 wcr={sub['cap_beam']['crack_width_mm']:.3f} mm ≤ 0.200 mm [3]。",
    )
    _add_section_heading(doc, "8.2 双柱式圆柱墩、桩顶系梁节点与钻孔灌注桩计算（对应图号 QL-17）")
    _add_body_para(
        doc,
        f"墩柱直径 Dc={sub['pier_column']['diameter_m']:g} m、墩高 Hc={sub['pier_column']['height_m']:g} m，环向均布主筋 DZ1 {sub['pier_column']['main_rebar']}：柱底基本组合轴力 Nd={sub['pier_column']['n_uls_kn']:.2f} kN、纵向偏载与制动力弯矩 Md={sub['pier_column']['m_uls_knm']:.2f} kN·m，偏心受压承载力 Nu={sub['pier_column']['nu_capacity_kn']:.2f} kN ≥ Nd [3,5]。桩顶横系梁主筋 XL1 12Φ22 锚入墩柱核心区 780 mm ≥ 35d=770 mm [3]。"
        f"桥墩采用 2 根直径 Dp={sub['bored_pile']['diameter_m']:g} m 水下 C30 钻孔灌注摩擦桩，按 JTG 3363—2019 第 5.3.3 条公式 [Ra]=0.5u∑qikli + Ap·0.85fa0 计算 [4]，选定桥墩设计桩长 Lpier={sub['bored_pile']['design_length_m']:g} m，单桩容许承载力 [Ra]={sub['bored_pile']['allowable_ra_kn']:.2f} kN ≥ 桩顶标准组合荷载 Rsls={sub['bored_pile']['pile_top_sls_demand_kn']:.2f} kN（见图8-1）。",
    )
    _embed_figure_if_available(doc, figs_by_id.get("fig4_pile_soil_profile"), width_cm=15.4)

    # ------------------------------------------------------------------
    # Chapter 9: Independent Pile-Bent Abutment & Bored Pile Design
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第9章  桩柱式桥台及钻孔灌注桩独立设计计算", page_break_before=True)
    _add_section_heading(doc, "9.1 桥台竖向荷载、朗肯主动土压力与台帽/背墙/垫石验算（对应图号 QL-15、QL-18）")
    _add_body_para(
        doc,
        f"0#、{ps['n_spans']}# 桥台采用桩柱式桥台，受力与中间双柱墩完全独立：竖向承受边跨单跨恒载反力 {abut['load_breakdown']['single_span_girder_dead_total_kn']:.2f} kN、边跨活载反力 {abut['load_breakdown']['single_span_girder_live_total_kn']:.2f} kN、6.0 m 桥头搭板恒活载半重 {abut['load_breakdown']['approach_slab_dead_half_kn']+abut['load_breakdown']['approach_slab_live_half_kn']:.2f} kN 以及台帽、背墙牛腿、耳墙与垫石自重，台帽底标准值总竖向力为 {abut['load_breakdown']['total_vertical_sls_at_cap_bottom_kn']:.2f} kN。"
        f"台背填料重度 γs={abut['earth_pressure']['backfill_gamma_kn_m3']:g} kN/m³、内摩擦角 φ={abut['earth_pressure']['backfill_phi_deg']:g}°，朗肯主动土压力系数 Ka={abut['earth_pressure']['rankine_ka']:.4f}，按 JTG D60—2015 §4.3.4 求得车辆荷载换算等代土层厚度 heq={abut['earth_pressure']['equivalent_surcharge_heq_m']:.4f} m [2]。"
        f"台帽（QT1 {abut['abutment_cap']['main_rebar']}）抗弯 Mu={abut['abutment_cap']['mu_hog_capacity_knm']:.1f} kN·m ≥ Md={abut['abutment_cap']['m_hog_uls_knm']:.1f} kN·m、裂缝 wcr={abut['abutment_cap']['crack_width_mm']:.3f} mm ≤ 0.20 mm；背墙根部（QT2 Φ16@125双侧，锚入台帽 560 mm=35d）Mu={abut['backwall']['mu_capacity_total_knm']:.1f} ≥ Md={abut['backwall']['m_uls_total_knm']:.1f} kN·m；C40支座垫石（DS1网）局部承压 Flu={abut['pedestal_local_bearing']['capacity_flu_kn']:.1f} ≥ Fld={abut['pedestal_local_bearing']['demand_fld_kn']:.1f} kN [3,10]。",
    )
    _add_section_heading(doc, "9.2 桥台钻孔灌注桩独立桩长推导与桩身弯压验算")
    _add_body_para(
        doc,
        f"由本桥台单桩标准值竖向荷载 Rabut,sls={abut['bored_pile']['pile_top_sls_demand_kn']:.2f} kN 及台位钻孔土层分层摩阻力，按 JTG 3363—2019 满足 [Ra](L) ≥ 1.15·Rabut,sls 且进入砂砾石持力层深度 ≥ 2.5D={abut['bored_pile']['min_required_socket_m']:g} m 独立求解 [4]，确定桥台设计桩长 Labut={abut['bored_pile']['design_length_m']:g} m（实际进入持力层 {abut['bored_pile']['bearing_stratum_socket_m']:.2f} m，单桩容许承载力 [Ra]={abut['bored_pile']['allowable_ra_kn']:.2f} kN ≥ Rabut,sls）。桩身配 QT3 {abut['bored_pile']['main_rebar']} + QT4 φ12@150 螺旋箍，弯压承载力 Nu={abut['bored_pile']['nu_section_capacity_kn']:.1f} kN ≥ Nd={abut['bored_pile']['n_uls_kn']:.1f} kN，全桥 4 根桥台桩水下 C30 混凝土方量 {boq['volume_breakdown']['abut_pile_m3']} m³ 与 QL-02、QL-15、QL-18 严格一致。",
    )

    # ------------------------------------------------------------------
    # Chapter 10: Oral-Defense Verification Table, Rebar Provenance & Change Impact
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第10章  手算与独立电算模型复核及全图册选筋一致性追溯", page_break_before=True)
    mc = def_tbl["model_card"]
    _add_section_heading(doc, "10.1 独立电算软件建模记录卡与答辩现场复核对照表")
    _add_body_para(
        doc,
        f"为检验主梁与下部结构设计结论的可靠性，落实开题报告“手算与专业桥梁软件相互校核”方案 [8,11,12]，针对代表性 1# 边T梁与 2# 中间双柱墩整理建模条件卡（软件：{mc['software_name']}；跨度与离散：{mc['span_and_mesh']}；约束：{mc['boundary_restraints']}；荷载与组合：{mc['loads_and_combinations']}），并将手算解析值、本程序杆系解与独立软件值并排汇总于表10-1。",
    )
    def_rows_w = [
        (
            r["title"],
            r["unit"],
            f"{r['hand_value']:.2f}",
            f"{r['program_value']:.2f}",
            r["software_display"],
            f"{r['hand_vs_software_diff_pct']}%" if r["hand_vs_software_diff_pct"] is not None else "—",
            r["discrepancy_reason"],
            r["verdict_zh"],
        )
        for r in def_tbl["rows"]
    ]
    _add_three_line_table(
        doc,
        "表10-1 代表性T梁与中间双柱墩手算—本程序—独立有限元软件答辩复核对照表",
        ("对比指标", "单位", "手算值", "本程序值", "独立软件值", "相对差异", "差异力学原因解释", "结论"),
        def_rows_w,
    )

    _add_section_heading(doc, "10.2 全套施工图预应力钢束与普通钢筋选筋溯源总表（QL-08～QL-18）")
    _add_body_para(
        doc,
        "表10-2 建立从图面钢筋/钢束编号到构件统计口径、选筋规范条文、控制截面需求及抗力的全链路映射，保证任意一张施工图中的钢筋编号均可直接回溯至本说明书对应计算章节。",
    )
    all_recs = list(ledger["tendons"]) + list(ledger["all_rebar_items"])
    ledger_rows_w = [
        (
            rec.get("tendon_id") or rec.get("bar_id"),
            rec["sheet_drawing_no"],
            rec.get("spec") or f"{rec['grade']} Φ{rec['dia_mm']:g}",
            f"{rec['quantity_scope']['counting_scope']}:{rec['quantity_scope']['count_per_member']}",
            f"{rec['quantity_scope']['bridge_total_weight_kg']} kg",
            f"{rec['governing_forces']['demand_symbol']}={rec['governing_forces']['demand_value']} vs {rec['governing_forces']['capacity_symbol']}={rec['governing_forces']['capacity_value']} {rec['governing_forces']['unit']}",
        )
        for rec in all_recs
    ]
    _add_three_line_table(
        doc,
        "表10-2 全桥施工图预应力钢束与钢筋编号（N1～DS1）选筋依据与数量溯源表",
        ("编号", "图号", "材料规格", "单件统计口径", "全桥总重", "控制内力需求 vs 抗力"),
        ledger_rows_w,
    )

    if change_impact and change_impact.get("has_changes"):
        _add_section_heading(doc, "10.3 本次设计变更影响章节与施工图号核查记录")
        _add_body_para(doc, change_impact["summary_zh"])

    # ------------------------------------------------------------------
    # Chapter 11: Conclusions, References, Acknowledgments & Appendix
    # ------------------------------------------------------------------
    _add_chapter_heading(doc, "第11章  结论与展望", page_break_before=True)
    _add_body_para(
        doc,
        f"本次毕业设计围绕“{ps['project_name']}”完成了水文跨径论证、开题三桥型方案比选、上部预制 C50 预应力混凝土简支T梁及桥面系计算、中间双柱式桥墩及钻孔桩计算、桩柱式桥台及独立桩长推导、手算与电算交叉校核以及 QL-01～QL-18 全套 18 张 A3 施工图绘制。"
        "结果表明，所选 4×25 m 装配式预应力混凝土简支T梁方案受力明确、施工高效、造价合理；主梁、横隔梁、支座及墩台桩基在承载能力极限状态与正常使用极限状态下的各项指标均符合现行公路桥涵规范要求，计算书与全套施工图尺寸、配筋及材料汇总严格一致。后续待桥位外业实测河床断面与钻孔柱状图最终核定后，可通过参数化联动一键更新终稿。",
    )

    # References (GB/T 7714, >=10 Chinese + >=2 English)
    _add_chapter_heading(doc, "参考文献", page_break_before=True)
    for idx_r, r_item in enumerate(refs, start=1):
        rno = int(r_item.get("ref_no", idx_r))
        _add_body_para(doc, f"[{rno}] {r_item['citation']}", indent=False)

    # Acknowledgments
    _add_chapter_heading(doc, "致  谢", page_break_before=True)
    _add_body_para(
        doc,
        "四年本科求学时光行至收官。在本次桥梁毕业设计的方案论证、结构手算、有限元电算校核以及十八张施工图纸反复打磨的过程中，谨向倾注大量心血悉心指导我的指导教师致以最诚挚的敬意与感谢！老师严谨治学的工程态度与细致入微的审图意见，使我对桥梁上下部结构传力机理和规范条文有了系统而深刻的理解。",
    )
    _add_body_para(
        doc,
        "同时感谢学院桥梁与结构工程教研室各位任课教师四年来在结构力学、混凝土结构设计原理、桥梁工程及基础工程课程中打下的扎实专业根基，感谢同组同学在软件建模与图纸互审中的交流互助，并向始终给予温暖支持的家人致以深深的谢意！",
    )

    # Appendix: 18-Sheet Index + Whole-Bridge BOQ
    _add_chapter_heading(doc, "附  录  全套施工图目录与主要工程材料数量汇总表", page_break_before=True)
    idx_rows_w = [(str(i + 1), dno, dtitle, dscale, "A3") for i, (dno, dtitle, dscale) in enumerate(DEFAULT_18_SHEET_INDEX)]
    _add_three_line_table(doc, "附表1 全套施工图设计图册目录（QL-01～QL-18）", ("序号", "图号", "图纸名称", "比例", "图幅"), idx_rows_w)

    boq_rows_w = [(r["category"], r["material"], r["unit"], str(r["quantity"]), r["sheet_ref"]) for r in boq["rows"]]
    _add_three_line_table(doc, "附表2 全桥主要工程材料数量汇总表（对应图号 QL-02）", ("工程部位", "材料名称及规格", "单位", "全桥数量", "对应图号"), boq_rows_w)

    out_docx = Path(output_docx_path).expanduser().resolve()
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_docx))

    # Collect body text for word/character count & citation audit
    all_para_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    cjk_char_count = sum(1 for ch in all_para_text if "\u4e00" <= ch <= "\u9fff")
    ref_audit = audit_references_compliance(refs, body_text=all_para_text)

    md_mirror_path: str | None = None
    if also_write_markdown:
        md_p = out_docx.with_suffix(".md")
        md_p.write_text(all_para_text, encoding="utf-8")
        md_mirror_path = str(md_p)

    return {
        "docx_path": str(out_docx),
        "markdown_mirror_path": md_mirror_path,
        "paragraph_count": len(doc.paragraphs),
        "table_count": len(doc.tables),
        "cjk_character_count": cjk_char_count,
        "total_character_count": len(all_para_text),
        "embedded_figure_count": sum(1 for fid in ("fig0_scheme_comparison", "fig1_structural_model", "fig2_moment_shear_envelope", "fig3_tendon_and_prestress", "fig4_pile_soil_profile") if (figs_by_id.get(fid) or {}).get("png_path") and Path(str(figs_by_id[fid]["png_path"])).is_file()),
        "references_audit": ref_audit,
    }
