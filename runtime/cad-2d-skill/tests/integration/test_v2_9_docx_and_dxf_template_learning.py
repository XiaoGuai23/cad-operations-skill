"""End-to-end integration tests for v2.9 Automatic `.docx` Thesis & `.dxf` Album Template Learning:

1. Learn a distinct university's `.docx` graduation-thesis template (`learn_thesis_template_from_docx`):
   - Extracts custom margins, running header, cover banner & field labels (strictly stripping sample student PII!),
     centered `"第一章"` Chinese-numeral Heading 1 (`18pt 微软雅黑`), `14pt/12pt` subheadings, `11.5pt 仿宋` body,
     `9.5pt 楷体` bold figure captions, and `9.5pt 仿宋` (`16/8/16` border weight) three-line tables;
   - Applies the learned `ThesisTemplateProfile` in `generate_college_graduation_thesis_docx` and verifies
     both body and 3-level hyperlinked TOC adopt the learned typography & `"第一章  绪论"` numbering.
2. Learn a design-institute `.dxf` construction album style (`learn_dxf_album_profile` / `learn_template_styles`):
   - Extracts Chinese CAD layer mappings (`钢筋`->`REBAR`, `钢束`->`TENDON`, `混凝土`->`CONCRETE`, `标注`->`DIM`),
     lineweights, custom fonts, `DIMSTYLE` (`dimtxt=2.8`, `dimasz=2.2`), inner frame margins (`20mm` left),
     and drawing-number prefix (`JS-01..JS-21`);
   - Applies the learned `AlbumTemplateProfile` across all 22 sheets + Calc Book + Word Appendix Table 1,
     passing `verify_civil_dxf` on every generated sheet.
3. Verify CLI (`cad-skill learn-template`), `scripts/run_bridge_suite.py --learn-docx --learn-dxf`,
   and MCP `cad_learn_standard` (keeping exact 28-tool contract intact).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
import ezdxf
from fastapi.testclient import TestClient
from PIL import Image

from cad_skill_core.civil import (
    generate_bridge_calculation_figures,
    generate_college_graduation_thesis_docx,
    generate_full_bridge_drawing_set,
    learn_dxf_album_profile,
    learn_template_styles,
    learn_thesis_template_from_docx,
    run_full_bridge_calculation,
    verify_civil_dxf,
)
from cad_skill_core.mcp.http_app import create_mcp_http_app


def _set_sample_run_font(run, zh_font: str, size_pt: float, *, bold: bool = False) -> None:
    run.font.name = "Arial"
    run.font.size = Pt(size_pt)
    run.bold = bold
    r_pr = run._r.get_or_add_rPr()
    rf = OxmlElement("w:rFonts")
    rf.set(qn("w:ascii"), "Arial")
    rf.set(qn("w:hAnsi"), "Arial")
    rf.set(qn("w:eastAsia"), zh_font)
    r_pr.insert(0, rf)


def _build_other_university_sample_docx(path: Path) -> Path:
    """Create a synthetic reference `.docx` from another university (with sample PII that MUST be stripped!)."""
    doc = Document()

    # Section 0: Cover with custom banner & custom left-column labels + sample PII ("张三丰" / "20190888" / "赵敏教授")
    s0 = doc.sections[0]
    s0.left_margin = Cm(3.2)
    s0.right_margin = Cm(2.2)
    s0.top_margin = Cm(2.8)
    s0.bottom_margin = Cm(2.4)

    p_sch = doc.add_paragraph()
    p_sch.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_sample_run_font(p_sch.add_run("长安交通工程学院"), "华文中宋", 24.0, bold=True)

    p_deg = doc.add_paragraph()
    p_deg.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_sample_run_font(p_deg.add_run("毕业设计（论文）"), "华文中宋", 28.0, bold=True)

    cov = doc.add_table(rows=7, cols=2)
    sample_cover_rows = [
        ("设计题目：", "某高速公路 3×30m 连续箱梁桥设计"),
        ("姓    名：", "张三丰"),
        ("专业及班级：", "道路桥梁与渡河工程 1901 班"),
        ("学生学号：", "2019088801"),
        ("所在院系：", "公路与桥梁工程学院"),
        ("导师姓名：", "赵敏（教授）"),
        ("提交日期：", "2023年6月"),
    ]
    for i, (k, v) in enumerate(sample_cover_rows):
        cov.rows[i].cells[0].text = k
        cov.rows[i].cells[1].text = v

    # Section 1: Front matter (Roman) + Section 2: Body (Arabic + running header)
    s1 = doc.add_section(WD_SECTION.NEW_PAGE)
    pg1 = OxmlElement("w:pgNumType")
    pg1.set(qn("w:fmt"), "upperRoman")
    s1._sectPr.append(pg1)

    s2 = doc.add_section(WD_SECTION.NEW_PAGE)
    s2.left_margin = Cm(3.2)
    s2.right_margin = Cm(2.2)
    s2.top_margin = Cm(2.8)
    s2.bottom_margin = Cm(2.4)
    s2.header.is_linked_to_previous = False
    s2.header.paragraphs[0].text = "本科毕业设计计算说明书"
    pg2 = OxmlElement("w:pgNumType")
    pg2.set(qn("w:fmt"), "decimal")
    s2._sectPr.append(pg2)

    # Centered "第一章" Heading 1 in 18pt 微软雅黑
    for ch_txt in ("第一章  绪论", "第二章  方案比选"):
        p_h1 = doc.add_paragraph()
        p_h1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_sample_run_font(p_h1.add_run(ch_txt), "微软雅黑", 18.0, bold=True)

    for h2_txt in ("1.1 工程概况", "1.2 技术标准"):
        p_h2 = doc.add_paragraph()
        p_h2.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _set_sample_run_font(p_h2.add_run(h2_txt), "黑体", 14.0, bold=True)

    p_h3 = doc.add_paragraph()
    p_h3.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_sample_run_font(p_h3.add_run("1.2.1 荷载等级与桥面宽度"), "黑体", 12.0, bold=True)

    for _ in range(3):
        p_b = doc.add_paragraph()
        p_b.paragraph_format.line_spacing = 1.35
        p_b.paragraph_format.first_line_indent = Pt(23.0)
        _set_sample_run_font(
            p_b.add_run("本桥梁工程按照公路一级荷载标准进行上下部结构整体内力计算、预应力配束与桩基承载力复核。"),
            "仿宋",
            11.5,
            bold=False,
        )

    p_fig = doc.add_paragraph()
    p_fig.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_sample_run_font(p_fig.add_run("图 1-1 桥位总体布置示意图"), "楷体", 9.5, bold=True)

    p_tcap = doc.add_paragraph()
    p_tcap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_sample_run_font(p_tcap.add_run("表 1-1 主要技术指标汇总表"), "微软雅黑", 10.0, bold=True)

    tbl = doc.add_table(rows=3, cols=2)
    for ci, h_s in enumerate(("项目", "指标")):
        c = tbl.rows[0].cells[ci]
        tc_b = OxmlElement("w:tcBorders")
        for side, sz in (("top", "16"), ("bottom", "8")):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), sz)
            tc_b.append(el)
        c._tc.get_or_add_tcPr().append(tc_b)
        p_c = c.paragraphs[0]
        _set_sample_run_font(p_c.add_run(h_s), "微软雅黑", 9.5, bold=True)

    for ri in (1, 2):
        for ci, v_s in enumerate(("设计荷载", "公路-I级")):
            c = tbl.rows[ri].cells[ci]
            if ri == 2:
                tc_b = OxmlElement("w:tcBorders")
                el = OxmlElement("w:bottom")
                el.set(qn("w:val"), "single")
                el.set(qn("w:sz"), "16")
                tc_b.append(el)
                c._tc.get_or_add_tcPr().append(tc_b)
            p_c = c.paragraphs[0]
            _set_sample_run_font(p_c.add_run(v_s), "仿宋", 9.5, bold=False)

    doc.save(str(path))
    return path


def _build_design_institute_sample_dxfs(dxf_dir: Path) -> Path:
    """Create 2 sample A3 DXF sheets using Chinese layer names, custom colors/lineweights, and `JS-` prefix."""
    dxf_dir.mkdir(parents=True, exist_ok=True)
    for idx_s, title_s in ((1, "设计总说明"), (2, "预应力钢束构造图")):
        doc = ezdxf.new("R2010", setup=True)
        doc.layers.add("图框", color=7, lineweight=60)
        doc.layers.add("混凝土", color=6, lineweight=30)
        doc.layers.add("钢筋", color=2, lineweight=80)
        doc.layers.add("箍筋", color=40, lineweight=40)
        doc.layers.add("钢束", color=5, lineweight=70)
        doc.layers.add("标注", color=4, lineweight=18)

        st = doc.styles.add("CIVIL_TEXT", font="simfang.ttf")
        st.dxf.width = 0.80
        doc.styles.add("CIVIL_TITLE", font="simhei.ttf")
        doc.dimstyles.add(
            "CIVIL_INSTITUTE",
            dxfattribs={
                "dimblk": ezdxf.ARROWS.architectural_tick,
                "dimtxt": 2.8,
                "dimasz": 2.2,
                "dimgap": 0.9,
            },
        )
        msp = doc.modelspace()
        # Outer A3 paper (420x297) + inner border with 20mm left margin, 8mm other margin
        msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
        msp.add_lwpolyline([(20, 8), (412, 8), (412, 289), (20, 289)], close=True, dxfattribs={"layer": "图框"})
        msp.add_lwpolyline([(252, 8), (412, 8), (412, 40), (252, 40)], close=True, dxfattribs={"layer": "图框"})
        dno = f"JS-{idx_s:02d}"
        msp.add_text(f"图名：{title_s}", dxfattribs={"height": 3.5}).set_placement((260, 26))
        msp.add_text(dno, dxfattribs={"height": 4.0}).set_placement((380, 14))
        msp.add_text("执行标准：GB 1499.1-2024", dxfattribs={"height": 2.5}).set_placement((30, 250))
        doc.saveas(dxf_dir / f"{dno}_sample.dxf")
    return dxf_dir


def test_learn_docx_template_and_apply_to_new_graduation_thesis(tmp_path):
    ref_docx = _build_other_university_sample_docx(tmp_path / "other_school_ref.docx")
    tpl = learn_thesis_template_from_docx(ref_docx)

    # 1. Verify exact typography, margins, headings, tables, and cover labels learned
    assert math.isclose(tpl.left_margin_cm, 3.2, abs_tol=0.05)
    assert math.isclose(tpl.right_margin_cm, 2.2, abs_tol=0.05)
    assert math.isclose(tpl.top_margin_cm, 2.8, abs_tol=0.05)
    assert math.isclose(tpl.bottom_margin_cm, 2.4, abs_tol=0.05)
    assert tpl.running_header_text == "本科毕业设计计算说明书"
    assert tpl.degree_banner_text == "毕业设计（论文）"
    assert tpl.degree_banner_zh_font == "华文中宋"
    assert math.isclose(tpl.degree_banner_size_pt, 28.0, abs_tol=0.1)
    assert tpl.cover_field_labels["college_name"] == "所在院系"
    assert tpl.cover_field_labels["advisor_name"] == "导师姓名"
    assert tpl.cover_field_labels["completion_date"] == "提交日期"

    assert tpl.h1_numbering_style == "chapter_zh"
    assert tpl.h1_alignment == "center"
    assert tpl.h1_zh_font == "微软雅黑"
    assert math.isclose(tpl.h1_size_pt, 18.0, abs_tol=0.1)
    assert math.isclose(tpl.h2_size_pt, 14.0, abs_tol=0.1)
    assert math.isclose(tpl.h3_size_pt, 12.0, abs_tol=0.1)

    assert tpl.body_zh_font == "仿宋"
    assert math.isclose(tpl.body_size_pt, 11.5, abs_tol=0.1)
    assert math.isclose(tpl.body_line_spacing, 1.35, abs_tol=0.02)
    assert tpl.figure_caption_zh_font == "楷体"
    assert math.isclose(tpl.figure_caption_size_pt, 9.5, abs_tol=0.1)
    assert tpl.figure_caption_bold is True
    assert tpl.table_cell_zh_font == "仿宋"
    assert math.isclose(tpl.table_cell_size_pt, 9.5, abs_tol=0.1)
    assert tpl.top_border_sz == 16 and tpl.mid_border_sz == 8 and tpl.bottom_border_sz == 16

    # Strictly zero sample PII stored in learned profile!
    tpl_json_str = json.dumps(tpl.model_dump(mode="json"), ensure_ascii=False)
    for sample_pii in ("张三丰", "2019088801", "赵敏", "3×30m"):
        assert sample_pii not in tpl_json_str

    # 2. Generate Wenchang thesis using the learned template -> verify closed-loop application!
    calc = run_full_bridge_calculation({"preset": "wenchang"})
    dummy_png = tmp_path / "dummy_fig.png"
    Image.new("RGB", (400, 200), color=(255, 255, 255)).save(dummy_png)
    figs = {"by_id": {"fig0_scheme_comparison": {"png_path": str(dummy_png), "caption_zh": "图 2-1 三种桥型方案比选图"}}}

    out_docx = tmp_path / "wenchang_in_other_school_style.docx"
    res = generate_college_graduation_thesis_docx(
        calc,
        out_docx,
        figures_summary=figs,
        thesis_template=tpl,
        student_metadata={"school_name": "长安交通工程学院", "student_name": "李华"},
    )
    assert res["applied_thesis_template"] == tpl.profile_name

    reopened = Document(str(out_docx))
    assert math.isclose(reopened.sections[-1].left_margin.cm, 3.2, abs_tol=0.05)
    assert reopened.sections[-1].header.paragraphs[0].text == "本科毕业设计计算说明书"

    cov_dump = " | ".join(c.text.strip() for r in reopened.tables[0].rows for c in r.cells)
    assert "所在院系：" in cov_dump and "导师姓名：" in cov_dump and "李华" in cov_dump
    assert "张三丰" not in cov_dump

    # Numbered Chapter 1 rendered as centered 18pt "第一章  绪论" in BOTH body and TOC!
    ch1_body = next(p for p in reopened.paragraphs if "\t" not in p.text and p.text.strip() == "第一章  绪论")
    assert ch1_body.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert math.isclose(ch1_body.runs[0].font.size.pt, 18.0, abs_tol=0.1)
    assert ch1_body.runs[0]._r.find(qn("w:rPr")).find(qn("w:rFonts")).get(qn("w:eastAsia")) == "微软雅黑"

    toc_ch1 = next(p for p in reopened.paragraphs if "第一章  绪论" in p.text and "\t" in p.text)
    assert toc_ch1 is not None

    fig_cap = next(p for p in reopened.paragraphs if p.text.strip().startswith("图 2-1"))
    assert fig_cap.runs[0].bold is True
    assert math.isclose(fig_cap.runs[0].font.size.pt, 9.5, abs_tol=0.1)

    sample_tbl_cell = reopened.tables[1].rows[1].cells[0].paragraphs[0].runs[0]
    assert math.isclose(sample_tbl_cell.font.size.pt, 9.5, abs_tol=0.1)


def test_learn_dxf_album_and_unified_one_command_suite(tmp_path):
    ref_docx = _build_other_university_sample_docx(tmp_path / "ref_spec.docx")
    ref_dxf_dir = _build_design_institute_sample_dxfs(tmp_path / "ref_dxfs")

    album_prof = learn_dxf_album_profile(ref_dxf_dir)
    assert album_prof.sheet_size == "A3"
    assert math.isclose(album_prof.margin_left_mm, 20.0, abs_tol=0.5)
    assert math.isclose(album_prof.margin_other_mm, 8.0, abs_tol=0.5)
    assert album_prof.text_font == "simfang.ttf"
    assert album_prof.drawing_no_prefix == "JS-"
    assert album_prof.layer_colors["REBAR"] == 2
    assert album_prof.layer_colors["TENDON"] == 5
    assert album_prof.layer_colors["CONCRETE"] == 6
    assert math.isclose(album_prof.layer_lineweights_mm["REBAR"], 0.80, abs_tol=0.01)
    assert math.isclose(album_prof.dim_text_height_mm, 2.8, abs_tol=0.05)

    # Unified memory bundle + full 22-sheet bridge suite generation
    mem_dir = tmp_path / ".cad-memory"
    bundle = learn_template_styles(docx_path=ref_docx, dxf_path_or_dir=ref_dxf_dir, output_dir=mem_dir)
    assert Path(bundle["thesis_template_path"]).is_file()
    assert Path(bundle["album_template_path"]).is_file()
    assert Path(bundle["standard_yaml_path"]).is_file()

    out_suite = tmp_path / "js_styled_suite"
    suite = generate_full_bridge_drawing_set(
        {"preset": "wenchang"},
        out_suite,
        template_memory_dir=mem_dir,
        generate_figures=False,
    )
    assert suite["status"] == "passed"
    assert len(suite["sheets"]) == 22
    assert suite["sheets"][0]["drawing_no"] == "JS-01"
    assert suite["sheets"][7]["drawing_no"] == "JS-08"
    assert suite["sheets"][19]["drawing_no"] == "JS-19-1"
    assert suite["sheets"][-1]["drawing_no"] == "JS-21"

    # Reopen generated JS-08 DXF and verify learned layers, lineweights, font, dimstyle & margins!
    js08_path = Path(suite["sheets"][7]["dxf_path"])
    assert verify_civil_dxf(js08_path, calc_report=suite["calculation_report"])["status"] == "passed"
    doc08 = ezdxf.readfile(js08_path)
    assert doc08.layers.get("REBAR").color == 2
    assert doc08.layers.get("REBAR").dxf.lineweight == 80
    assert doc08.layers.get("TENDON").color == 5
    assert doc08.layers.get("CONCRETE").color == 6
    assert doc08.styles.get("CIVIL_TEXT").dxf.font == "simfang.ttf"
    assert math.isclose(doc08.dimstyles.get("CIVIL_ARCH").dxf.dimtxt, 2.8, abs_tol=0.05)

    # Verify Word Appendix Table 1 & Chapters also reflect JS-01..JS-21 + "第一章  绪论"!
    reopened_thesis = Document(str(suite["graduation_thesis_docx_path"]))
    app_dnos = [r.cells[1].text.strip() for r in reopened_thesis.tables[-2].rows[1:]]
    assert app_dnos[0] == "JS-01" and "JS-19-1" in app_dnos and app_dnos[-1] == "JS-21"
    assert any(p.text.strip() == "第一章  绪论" for p in reopened_thesis.paragraphs if "\t" not in p.text)

    # Also test CLI `learn-template` & MCP `cad_learn_standard` (preserving 28-tool contract)
    repo_root = Path(__file__).resolve().parents[4]
    cli_env = dict(os.environ)
    cli_env["PYTHONPATH"] = str(repo_root / "runtime" / "cad-2d-skill" / "src")
    cli_out = tmp_path / "cli_mem"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cad_skill_core.cli",
            "learn-template",
            "--docx",
            str(ref_docx),
            "--dxf",
            str(ref_dxf_dir),
            "--out",
            str(cli_out),
            "--json",
        ],
        cwd=str(repo_root),
        env=cli_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    cli_json = json.loads(proc.stdout)
    assert cli_json["status"] == "learned"
    assert cli_json["album_template"]["drawing_no_prefix"] == "JS-"

    app = create_mcp_http_app(tmp_path)
    client = TestClient(app)
    tools_list = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).json()
    assert len(tools_list["result"]["tools"]) == 28

    r_mcp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "cad_learn_standard",
                "arguments": {
                    "sample_docx_path": str(ref_docx),
                    "sample_dxf_dir": str(ref_dxf_dir),
                    "profile_name": "institute-js-profile",
                },
            },
        },
    )
    assert r_mcp.status_code == 200
    mcp_sc = r_mcp.json()["result"]["structuredContent"]
    assert mcp_sc["status"] == "learned"
    assert mcp_sc["thesis_template"]["h1_numbering_style"] == "chapter_zh"
    assert mcp_sc["album_template"]["drawing_no_prefix"] == "JS-"
