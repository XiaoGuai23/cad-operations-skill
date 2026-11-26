"""Integration tests for v2.7+ Bridge Optimization & College Reference Alignment:
1. Unified Opening-Report 3-Scheme Comparison & >=10 Chinese + >=1 Foreign Inline-Cited Bibliography;
2. Independent FEM Software Import (Dr.Bridge / Midas Civil) + Built-in 2D Timoshenko Frame FEM & Defense Table;
3. 6 Word/PPT-Ready 300-DPI Calculation Figures (incl. T-Girder Exterior/Interior Sections extracted from QL-05/QL-07);
4. Formal College-Template Word Graduation Thesis (.docx):
   - Authentic underline cover form with real identity fields;
   - 3 sections: no cover page number, Roman (I, II, III) front matter, Arabic (1, 2, 3...) body;
   - Non-empty 3-level Word TOC (`TOC \\o "1-3"`) + left-aligned 16pt chapter headings, 15pt/14pt subheadings;
   - Compressed ~300-char Chinese abstract & ~200-word English Abstract with strict FEM status consistency;
   - 10.5pt non-bold concise figure captions + 10.5pt `<w:cantSplit/>` & `<w:tblHeader/>` tables + `续表`;
5. Design Change Impact Tracker verified against both advisor acceptance questions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
import ezdxf
from fastapi.testclient import TestClient
from PIL import Image

from cad_skill_core.civil import (
    audit_references_compliance,
    generate_bridge_calculation_book_markdown,
    generate_bridge_calculation_figures,
    generate_college_graduation_thesis_docx,
    generate_drbridge_modeling_guide,
    generate_full_bridge_drawing_set,
    ingest_independent_fem_results,
    lookup_bar_or_tendon,
    run_full_bridge_calculation,
    solve_independent_frame_fem,
    verify_civil_dxf,
    verify_internal_suite_consistency,
)
from cad_skill_core.mcp.http_app import create_mcp_http_app


def _all_dxf_text(dxf_path: str | Path) -> str:
    doc = ezdxf.readfile(dxf_path)
    parts: list[str] = []
    for ent in doc.modelspace():
        dt = ent.dxftype()
        if dt in {"TEXT", "MTEXT"}:
            parts.append(str(ent.dxf.get("text", "")))
        elif dt == "DIMENSION":
            parts.append(str(ent.dxf.get("text", "")))
    return " | ".join(parts)


def test_opening_report_three_schemes_and_dual_compliance_references(tmp_path):
    calc = run_full_bridge_calculation({"preset": "wenchang"})
    schemes = calc["bridge_schemes"]
    assert len(schemes) == 3
    names = [s["name"] for s in schemes]
    assert "4×25 m 装配式预应力混凝土简支T梁桥" in names[0]
    assert "4×25 m 预应力混凝土连续箱梁桥" in names[1]
    assert "4×25 m 装配式预应力混凝土简支箱梁桥" in names[2]

    md_book = generate_bridge_calculation_book_markdown(calc, output_path=tmp_path / "book.md")
    assert "4×25 m 预应力混凝土连续箱梁桥" in md_book
    assert "4×25 m 装配式预应力混凝土简支箱梁桥" in md_book
    assert "3×33m" not in md_book and "3×33 m" not in md_book
    assert "25+40+25" not in md_book

    body_only = md_book.split("## 参考文献")[0]
    ref_audit = audit_references_compliance(calc["references"], body_text=body_only)
    assert ref_audit["status"] == "passed"
    assert ref_audit["chinese_count"] >= 10
    assert ref_audit["foreign_count"] >= 1
    assert ref_audit["task_book_compliant"] is True
    assert ref_audit["opening_report_compliant"] is True
    assert ref_audit["uncited_ref_nos"] == []


def test_independent_fem_import_builtin_solver_and_defense_table(tmp_path):
    # 1. Default run without external FEM data never fakes external software values
    calc_no_fem = run_full_bridge_calculation({"preset": "wenchang"})
    def_tbl_0 = calc_no_fem["defense_verification_table"]
    assert def_tbl_0["external_fem_status"] == "pending_external_import"
    assert all(r["software_value"] is None and "待导入" in r["software_display"] for r in def_tbl_0["rows"])
    metric_keys = [r["metric_key"] for r in def_tbl_0["rows"]]
    assert metric_keys == [
        "girder_mid_moment_uls_knm",
        "girder_support_shear_uls_kn",
        "girder_live_deflection_mm",
        "pier_top_reaction_uls_kn",
    ]

    # 2. Built-in independent 2D Variable-Section Timoshenko Frame FEM + Dr.Bridge Guide
    fem_builtin = solve_independent_frame_fem(calc_no_fem)
    assert "Timoshenko" in fem_builtin["software_name"]
    calc_builtin = run_full_bridge_calculation({"preset": "wenchang", "fem_solver": "builtin_frame_fem"})
    def_builtin = calc_builtin["defense_verification_table"]
    assert def_builtin["external_fem_status"] == "verified"
    assert def_builtin["all_within_tolerance"] is True
    assert all(r["hand_vs_software_diff_pct"] <= 6.0 for r in def_builtin["rows"])

    guide = generate_drbridge_modeling_guide(calc_no_fem, tmp_path / "fem_guide")
    assert Path(guide["guide_markdown_path"]).is_file()
    assert Path(guide["template_json_path"]).is_file()
    assert "桥梁博士" in guide["guide_markdown"]

    # 3. Import realistic Dr.Bridge CSV/Markdown table & verify side-by-side defense table
    ex = def_tbl_0["import_template_example"]["metrics"]
    fem_md_file = tmp_path / "drbridge_export.md"
    fem_md_file.write_text(
        "\n".join(
            [
                "| 项目 | 软件计算值 | 单位 |",
                "| --- | --- | --- |",
                f"| 主梁跨中弯矩 | {ex['girder_mid_moment_uls_knm']} | kN·m |",
                f"| 主梁支点剪力 | {ex['girder_support_shear_uls_kn']} | kN |",
                f"| 主梁活载挠度 | {ex['girder_live_deflection_mm']} | mm |",
                f"| 中墩墩顶反力 | {ex['pier_top_reaction_uls_kn']} | kN |",
            ]
        ),
        encoding="utf-8",
    )
    ingested = ingest_independent_fem_results(fem_md_file)
    assert ingested is not None
    assert len(ingested["metrics"]) == 4

    calc_with_fem = run_full_bridge_calculation({"preset": "wenchang", "independent_fem_results": fem_md_file})
    def_tbl_ok = calc_with_fem["defense_verification_table"]
    assert def_tbl_ok["external_fem_status"] == "verified"
    assert def_tbl_ok["all_within_tolerance"] is True
    assert def_tbl_ok["compared_metric_count"] == 4
    for row in def_tbl_ok["rows"]:
        assert row["software_value"] is not None
        assert row["hand_vs_software_diff_pct"] <= 5.0
        assert len(row["discrepancy_reason"]) >= 12

    # 4. Counterexample: wrong unit / missing factor in external FEM triggers exceeds_tolerance!
    calc_bad_fem = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "independent_fem_results": {
                "software_name": "Midas Civil 2024",
                "tolerance_pct": 8.0,
                "metrics": {
                    "girder_mid_moment_uls_knm": 2100.0,
                    "girder_support_shear_uls_kn": ex["girder_support_shear_uls_kn"],
                },
            },
        }
    )
    assert calc_bad_fem["defense_verification_table"]["external_fem_status"] == "exceeds_tolerance"
    assert calc_bad_fem["defense_verification_table"]["all_within_tolerance"] is False


def test_word_and_ppt_calculation_figures_generation(tmp_path):
    dummy_scheme_img = tmp_path / "opening_scheme1_elev.png"
    Image.new("RGB", (320, 120), color=(240, 248, 255)).save(dummy_scheme_img)

    calc = run_full_bridge_calculation({"preset": "wenchang"})
    calc["bridge_schemes"][0]["elevation_image_path"] = str(dummy_scheme_img)

    figs = generate_bridge_calculation_figures(calc, tmp_path / "figures", dpi=300)
    assert figs["figure_count"] == 6
    expected_ids = {
        "fig0_scheme_comparison",
        "fig_girder_detail",
        "fig1_structural_model",
        "fig2_moment_shear_envelope",
        "fig3_tendon_and_prestress",
        "fig4_pile_soil_profile",
    }
    assert set(figs["by_id"].keys()) == expected_ids
    assert len(figs["by_id"]["fig0_scheme_comparison"]["reused_opening_report_images"]) == 1
    assert "初拟地层" in figs["by_id"]["fig4_pile_soil_profile"]["caption_zh"]
    assert figs["by_id"]["fig0_scheme_comparison"]["caption_zh"] == "图 2-1 三种桥型方案立面及横断面对比（单位：m）"

    for fid in expected_ids:
        f_meta = figs["by_id"][fid]
        assert len(f_meta["caption_zh"]) <= 35, f"caption too long and risks wrapping: {f_meta['caption_zh']}"
        png_p = Path(f_meta["png_path"])
        svg_p = Path(f_meta["svg_path"])
        assert png_p.is_file() and png_p.stat().st_size > 25_000
        assert svg_p.is_file() and svg_p.stat().st_size > 10_000
        with Image.open(png_p) as im:
            w, h = im.size
            assert w >= 2800 and h >= 1750

    svg_env = Path(figs["by_id"]["fig2_moment_shear_envelope"]["svg_path"]).read_text(encoding="utf-8")
    assert "kN·m" in svg_env and "kN" in svg_env
    svg_pile = Path(figs["by_id"]["fig4_pile_soil_profile"]["svg_path"]).read_text(encoding="utf-8")
    assert f"{calc['project_summary']['pier_pile_length_m']:g} m" in svg_pile
    assert f"{calc['project_summary']['abutment_pile_length_m']:g} m" in svg_pile


def test_college_template_formal_graduation_thesis_docx_typography_and_layout(tmp_path):
    calc = run_full_bridge_calculation({"preset": "wenchang"})
    figs = generate_bridge_calculation_figures(calc, tmp_path / "figs", dpi=200)
    docx_out = tmp_path / "wenchang_graduation_thesis.docx"

    res = generate_college_graduation_thesis_docx(
        calc,
        docx_out,
        figures_summary=figs,
    )
    assert Path(res["docx_path"]).is_file()
    assert res["embedded_figure_count"] == 6
    assert res["section_count"] == 3
    assert res["toc_entry_count"] >= 35
    # Task book target: ~300 CJK chars for Chinese abstract & ~200 words for English Abstract
    assert 240 <= res["zh_abstract_cjk_count"] <= 360
    assert 155 <= res["en_abstract_word_count"] <= 245
    assert res["references_audit"]["status"] == "passed"
    assert res["references_audit"]["uncited_ref_nos"] == []

    reopened = Document(str(docx_out))
    assert len(reopened.sections) == 3

    # Section 0 (Cover): no page number; Section 1 (Front): upperRoman start=1; Section 2 (Body): decimal start=1
    assert reopened.sections[0]._sectPr.find(qn("w:pgNumType")) is None
    pg_front = reopened.sections[1]._sectPr.find(qn("w:pgNumType"))
    assert pg_front is not None and pg_front.get(qn("w:fmt")) == "upperRoman" and pg_front.get(qn("w:start")) == "1"
    pg_body = reopened.sections[2]._sectPr.find(qn("w:pgNumType"))
    assert pg_body is not None and pg_body.get(qn("w:fmt")) == "decimal" and pg_body.get(qn("w:start")) == "1"

    # Cover table filled with real student metadata (never placeholder '待填' or '2026年5月')
    cov_text = " | ".join(cell.text.strip() for row in reopened.tables[0].rows for cell in row.cells)
    assert "许琦莘" in cov_text and "202109020152" in cov_text and "李小霞" in cov_text
    assert "待填" not in cov_text and "可配置学院名称" not in cov_text

    # Check numbered chapter Heading 1 is LEFT-aligned 16pt, Heading 2 is 15pt, Heading 3 is 14pt, figure captions non-bold 10.5pt
    h1_ch1 = next(p for p in reopened.paragraphs if "\t" not in p.text and p.text.strip() == "1  绪论")
    assert h1_ch1.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert math.isclose(h1_ch1.runs[0].font.size.pt, 16.0, abs_tol=0.1)

    h2_s = next(p for p in reopened.paragraphs if "\t" not in p.text and p.text.strip().startswith("1.2 工程水文"))
    assert h2_s.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert math.isclose(h2_s.runs[0].font.size.pt, 15.0, abs_tol=0.1)

    h3_s = next(p for p in reopened.paragraphs if "\t" not in p.text and p.text.strip().startswith("1.2.1 求设计流速"))
    assert h3_s.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert math.isclose(h3_s.runs[0].font.size.pt, 14.0, abs_tol=0.1)

    fig_cap_p = next(p for p in reopened.paragraphs if p.text.strip().startswith("图 2-1 三种桥型方案"))
    assert fig_cap_p.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert fig_cap_p.runs[0].bold is False
    assert math.isclose(fig_cap_p.runs[0].font.size.pt, 10.5, abs_tol=0.1)

    # Check every body table has 10.5pt cells, <w:cantSplit/> on every row, <w:tblHeader/> on row 0, plus 续表 10-2
    for tbl in reopened.tables[1:]:
        assert tbl.rows[0]._tr.find(qn("w:trPr")).find(qn("w:tblHeader")) is not None
        for row in tbl.rows:
            assert row._tr.find(qn("w:trPr")).find(qn("w:cantSplit")) is not None
        sample_run = tbl.rows[1].cells[0].paragraphs[0].runs[0]
        assert math.isclose(sample_run.font.size.pt, 10.5, abs_tol=0.1)

    full_text = "\n".join(p.text for p in reopened.paragraphs)
    for token in (
        "郑州升达经贸管理学院",
        "本科毕业设计",
        "摘  要",
        "Abstract",
        "目  录",
        "1  绪论",
        "1.2.1 求设计流速与设计流量",
        "2  桥型方案综合比选",
        "图 3-1 预制T梁外边梁与中梁构造尺寸图（单位：mm）",
        "10  手算与独立电算模型复核及全图册选筋一致性追溯",
        "续表 10-2",
        "11  结论与展望",
        "参考文献",
        "致  谢",
        "附  录",
    ):
        assert token in full_text, f"missing expected section/token {token!r} in thesis .docx"

    # Honest FEM status check: pending_external_import must NOT claim FEM verification is finished in Acknowledgments
    assert "有限元电算校核以及" not in full_text
    assert "待导入" in full_text


def test_design_change_impact_two_advisor_acceptance_questions(tmp_path):
    suite_dir = tmp_path / "reactive_suite"

    base_suite = generate_full_bridge_drawing_set({"preset": "wenchang"}, suite_dir)
    assert base_suite["calculation_status"] == "passed"
    assert base_suite["change_impact"]["mode"] == "initial_baseline"
    assert Path(base_suite["drbridge_modeling_guide_path"]).is_file()
    base_calc = base_suite["calculation_report"]
    assert base_calc["project_summary"]["abutment_pile_length_m"] == 15.0
    n_tendons_base = base_calc["project_summary"]["n_tendons"]
    base_abut_m3 = base_calc["bill_of_quantities"]["volume_breakdown"]["abut_pile_m3"]
    base_strand_kg = base_calc["bill_of_quantities"]["prestress_strand_kg"]

    # Question 1: 桥台桩长改动后 (15m -> 18m)，计算书、QL-15、QL-18、QL-02 的桩长和工程量是否一起更新？
    mod_abut_suite = generate_full_bridge_drawing_set(
        {"preset": "wenchang", "abutment_pile_length_m": 18.0},
        suite_dir,
    )
    assert mod_abut_suite["calculation_status"] == "passed"
    chg_abut = mod_abut_suite["change_impact"]
    assert chg_abut["mode"] == "diff_against_baseline"
    assert chg_abut["has_changes"] is True
    assert chg_abut["internal_consistency"]["status"] == "passed"
    assert {"QL-02", "QL-03", "QL-15", "QL-18"} <= set(chg_abut["affected_drawings"])
    assert {"QL-02", "QL-03", "QL-15", "QL-18"} <= set(chg_abut["dxf_sha256_changed_drawings"])
    assert any("第四章" in c for c in chg_abut["affected_calc_book_chapters"])

    mod_abut_calc = mod_abut_suite["calculation_report"]
    new_abut_m3 = mod_abut_calc["bill_of_quantities"]["volume_breakdown"]["abut_pile_m3"]
    assert new_abut_m3 > base_abut_m3 + 15.0
    qt3_18 = lookup_bar_or_tendon(mod_abut_calc, "QT3")
    assert math.isclose(qt3_18["single_length_cm"], 1900.0, abs_tol=0.5)

    txt_ql15 = _all_dxf_text(suite_dir / "QL-15" / "QL-15_pier_and_abutment.dxf")
    txt_ql18 = _all_dxf_text(suite_dir / "QL-18" / "QL-18_rebar_detail.dxf")
    txt_ql02 = _all_dxf_text(suite_dir / "QL-02" / "QL-02_notes_and_boq.dxf")
    book_18 = Path(mod_abut_suite["calculation_book_path"]).read_text(encoding="utf-8")

    assert "设计桩长 18m" in txt_ql15
    assert "桥台桩长 18m" in txt_ql18 and "1900" in txt_ql18
    assert f"{new_abut_m3} m3 (桩长 18m)" in txt_ql02
    assert "L_{\\text{abut}} = 18\\text{ m}" in book_18 and f"{new_abut_m3}\\text{{ m}}^3" in book_18

    # Question 2: 钢束数量改动后 (n_tendons -> n_tendons_base + 1)，主梁验算、QL-08 和材料汇总是否一致？
    new_n_tendons = n_tendons_base + 1
    mod_tendon_suite = generate_full_bridge_drawing_set(
        {"preset": "wenchang", "abutment_pile_length_m": 18.0, "n_tendons": new_n_tendons},
        suite_dir,
    )
    assert mod_tendon_suite["calculation_status"] == "passed"
    chg_ten = mod_tendon_suite["change_impact"]
    assert chg_ten["has_changes"] is True
    assert chg_ten["internal_consistency"]["status"] == "passed"
    assert {"QL-02", "QL-08", "QL-09"} <= set(chg_ten["affected_drawings"])
    assert {"QL-02", "QL-08", "QL-09"} <= set(chg_ten["dxf_sha256_changed_drawings"])

    mod_ten_calc = mod_tendon_suite["calculation_report"]
    assert mod_ten_calc["prestress"]["recommended_tendons"] == new_n_tendons
    assert mod_ten_calc["girder_checks"]["uls_flexure"]["capacity_mu_knm"] > base_calc["girder_checks"]["uls_flexure"]["capacity_mu_knm"] + 800.0
    new_strand_kg = mod_ten_calc["bill_of_quantities"]["prestress_strand_kg"]
    assert new_strand_kg > base_strand_kg + 500.0

    ql08_path = suite_dir / "QL-08" / "QL-08_tendon_layout.dxf"
    assert verify_civil_dxf(ql08_path, calc_report=mod_ten_calc)["status"] == "passed"
    txt_ql08 = _all_dxf_text(ql08_path)
    txt_ql02_ten = _all_dxf_text(suite_dir / "QL-02" / "QL-02_notes_and_boq.dxf")
    assert f"N{new_n_tendons}" in txt_ql08
    assert f"{new_strand_kg}" in txt_ql02_ten
    assert verify_internal_suite_consistency(mod_ten_calc)["status"] == "passed"

    calc_under_tendon = run_full_bridge_calculation({"preset": "wenchang", "n_tendons": 2})
    assert calc_under_tendon["calculation_status"] == "failed"
    assert any("QL-08" in fc["related_drawings"] for fc in calc_under_tendon["failed_checks"])

    app = create_mcp_http_app(tmp_path)
    client = TestClient(app)
    tools_resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}})
    assert len(tools_resp.json()["result"]["tools"]) == 28

    r_chg = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "cad_civil_bridge_calc",
                "arguments": {
                    "mode": "change_impact",
                    "params": {
                        "before_params": {"preset": "wenchang"},
                        "after_params": {"preset": "wenchang", "abutment_pile_length_m": 18.0},
                    },
                },
            },
        },
    )
    assert r_chg.status_code == 200
    chg_mcp = r_chg.json()["result"]["structuredContent"]["result"]
    assert {"QL-02", "QL-03", "QL-15", "QL-18"} <= set(chg_mcp["affected_drawings"])
