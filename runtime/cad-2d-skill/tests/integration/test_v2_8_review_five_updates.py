"""Integration regression suite for the 5 advisor/code-review optimization items (3 P1 + 2 P2):

1. P1 — 修正电算验收判定 (`partially_verified`, null template rejection, ULS vs SLS load-combination / section / unit audit);
2. P1 — 22 张图册和说明书共用单一图册清单与项目资料入口 (PDF 大纲书签 + DXF 图表目录 + Word 三级书签超链接目录 & 附录表 1 联动更新 + 默认封面零样本信息残留);
3. P1 — N4 三维束心越界 (`x=2980 mm` 处越出 200 mm 腹板 10 mm，支持桥梁博士 `new.txt` / JSON 导入) 与支座尺寸链 `480+40+480=1000 mm` 出图前联动校核;
4. P2 — 按几何、尺寸、文字与材料表做 DXF 语义内容哈希比对（原样重跑必须为 0 张图纸变化）;
5. P2 — 真实平面刚架刚度矩阵 `[K]{u}={F}` 标注内置简化复核范围 + 优先接入桥梁博士下部分册真实结果。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
import ezdxf
from pypdf import PdfReader

from cad_skill_core.civil import (
    audit_tendon_3d_section_clearance,
    compute_dxf_semantic_digest,
    generate_bridge_calculation_book_markdown,
    generate_civil_bridge_dxf,
    generate_college_graduation_thesis_docx,
    generate_drbridge_modeling_guide,
    generate_full_bridge_drawing_set,
    lookup_bar_or_tendon,
    parse_drbridge_tendon_and_section_model,
    run_full_bridge_calculation,
    solve_independent_frame_fem,
    verify_civil_dxf,
)


def _dxf_text_dump(dxf_path: str | Path) -> str:
    doc = ezdxf.readfile(dxf_path)
    out: list[str] = []
    for ent in doc.modelspace():
        dt = ent.dxftype()
        if dt in {"TEXT", "MTEXT", "DIMENSION"}:
            out.append(str(ent.dxf.get("text", "")))
    return " | ".join(out)


def test_p1_strict_fem_verification_partial_template_and_sls_rejection(tmp_path):
    base_calc = run_full_bridge_calculation({"preset": "wenchang"})
    base_tbl = base_calc["defense_verification_table"]
    prog_by_key = {r["metric_key"]: float(r["program_value"]) for r in base_tbl["rows"]}

    # 1. Importing ONLY 1 metric MUST report "partially_verified" (部分核对), NEVER "verified"!
    calc_partial = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "independent_fem_results": {
                "software_name": "桥梁博士 Dr.Bridge V4.4",
                "checked_by": "王工复核",
                "metrics": {
                    "girder_mid_moment_uls_knm": {
                        "value": round(prog_by_key["girder_mid_moment_uls_knm"] * 0.99, 1),
                        "member": "1#外边梁",
                        "section": "跨中 1/2L",
                        "combination": "承载能力极限状态基本组合",
                        "unit": "kN·m",
                        "source": "桥梁博士V4.4上部计算书表4-2",
                    }
                },
            },
        }
    )
    tbl_partial = calc_partial["defense_verification_table"]
    assert tbl_partial["external_fem_status"] == "partially_verified"
    assert tbl_partial["compared_required_count"] == 1
    assert len(tbl_partial["missing_required_metrics"]) == 3
    assert "部分核对" in tbl_partial["status_summary_zh"]
    md_partial = generate_bridge_calculation_book_markdown(calc_partial)
    assert "部分核对" in md_partial

    # 2. Re-importing generated template JSON or import_template_example MUST NEVER pass as "verified"!
    guide = generate_drbridge_modeling_guide(base_calc, tmp_path / "drb_guide")
    tpl_json_path = Path(guide["template_json_path"])
    tpl_payload = json.loads(tpl_json_path.read_text(encoding="utf-8"))
    assert tpl_payload["is_template"] is True
    assert all((v.get("value") if isinstance(v, dict) else v) is None for v in tpl_payload["metrics"].values())
    assert all((v.get("value") if isinstance(v, dict) else v) is None for v in tpl_payload["substructure_metrics"].values())

    calc_reimport_tpl = run_full_bridge_calculation(
        {"preset": "wenchang", "independent_fem_results": tpl_json_path}
    )
    assert calc_reimport_tpl["defense_verification_table"]["external_fem_status"] == "pending_external_import"
    assert calc_reimport_tpl["defense_verification_table"]["compared_metric_count"] == 0

    calc_reimport_ex = run_full_bridge_calculation(
        {"preset": "wenchang", "independent_fem_results": base_tbl["import_template_example"]}
    )
    assert calc_reimport_ex["defense_verification_table"]["external_fem_status"] == "pending_external_import"

    # Even if numbers are filled while leaving `is_template=True`, validator explicitly rejects them!
    filled_but_still_template = dict(tpl_payload)
    filled_but_still_template["metrics"] = {k: round(v, 1) for k, v in prog_by_key.items()}
    calc_tpl_flag = run_full_bridge_calculation(
        {"preset": "wenchang", "independent_fem_results": filled_but_still_template}
    )
    assert calc_tpl_flag["defense_verification_table"]["external_fem_status"] == "invalid_import"
    assert any(r["reason_code"] == "template_payload_rejected" for r in calc_tpl_flag["defense_verification_table"]["rejected_metrics"])

    # 3. Reject SLS "运营标准值" shear entering ULS "基本组合" slot, wrong unit, and wrong section!
    bad_sls_md = tmp_path / "bad_sls_shear.md"
    bad_sls_md.write_text(
        "\n".join(
            [
                "| 项目 | 控制截面 | 荷载组合 | 软件计算值 | 单位 | 来源 |",
                "| --- | --- | --- | --- | --- | --- |",
                f"| 主梁支点剪力 | 支点截面 | 运营标准值 | {prog_by_key['girder_support_shear_uls_kn']:.1f} | kN | 桥梁博士输出 |",
                f"| 主梁跨中弯矩 | 跨中截面 | 基本组合 | {prog_by_key['girder_mid_moment_uls_knm']:.1f} | MPa | 桥梁博士输出 |",
            ]
        ),
        encoding="utf-8",
    )
    calc_sls_reject = run_full_bridge_calculation(
        {"preset": "wenchang", "independent_fem_results": bad_sls_md}
    )
    tbl_rej = calc_sls_reject["defense_verification_table"]
    assert tbl_rej["external_fem_status"] == "invalid_import"
    rej_codes = {r["reason_code"] for r in tbl_rej["rejected_metrics"]}
    assert "load_combination_mismatch" in rej_codes
    assert "unit_mismatch" in rej_codes


def test_p1_unified_22_sheet_manifest_pdf_docx_co_reading_and_neutral_cover(tmp_path):
    suite_dir = tmp_path / "wenchang_22_suite"
    suite = generate_full_bridge_drawing_set({"preset": "wenchang"}, suite_dir, render_png=True)
    assert suite["status"] == "passed"
    assert len(suite["sheets"]) == 22

    expected_dnos = [
        "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11",
        "12", "13", "14", "15", "16", "17", "18", "19", "19-1", "20", "21",
    ]
    assert [sh["drawing_no"] for sh in suite["sheets"]] == expected_dnos
    assert all(sh["verify_status"] == "passed" and sh["rubric_score"] >= 92.0 for sh in suite["sheets"])

    # Verify merged 22-page A3 PDF album + 22 PDF outline bookmarks
    album_pdf = Path(suite["merged_pdf_album_path"])
    assert album_pdf.is_file() and album_pdf.name == "01_to_21_A3_print_album.pdf"
    pdf_reader = PdfReader(str(album_pdf))
    assert len(pdf_reader.pages) == 22
    assert len(pdf_reader.outline) == 22

    # Verify key 22-sheet construction details & standards alignment
    calc = suite["calculation_report"]
    txt_01 = _dxf_text_dump(suite_dir / "01" / "01_notes_and_boq.dxf")
    txt_02 = _dxf_text_dump(suite_dir / "02" / "02_notes_and_boq.dxf")
    txt_09 = _dxf_text_dump(suite_dir / "09" / "09_tendon_layout.dxf")
    txt_17 = _dxf_text_dump(suite_dir / "17" / "17_rebar_detail.dxf")
    txt_18 = _dxf_text_dump(suite_dir / "18" / "18_rebar_detail.dxf")
    txt_19_1 = _dxf_text_dump(suite_dir / "19-1" / "19-1_rebar_detail.dxf")
    txt_21 = _dxf_text_dump(suite_dir / "21" / "21_rebar_detail.dxf")

    assert "GB 1499.1-2024" in txt_01 and "GB 1499.2-2024" in txt_01
    assert "19-1" in txt_01 and "预应力钢束束心坐标及定位钢筋图" in txt_01
    assert "1.87" in txt_02  # C40 bearing pedestal concrete 1.87 m3
    assert "DW1" in txt_09 and "束心坐标" in txt_09
    assert "1240" in txt_17  # GL3 6-leg group perimeter 1240 cm
    assert "DK1" in txt_18 and "抗震挡块" in txt_18
    assert "XL1" in txt_19_1 and "桩顶系梁" in txt_19_1
    assert "EB1" in txt_21 and "EB2" in txt_21 and "EB3" in txt_21
    eb_sum_bridge = sum(
        lookup_bar_or_tendon(calc, bid)["bridge_total_weight_kg"] for bid in ("EB1", "EB2", "EB3")
    )
    assert math.isclose(eb_sum_bridge, 624.4, abs_tol=1.0)

    # Generate Word thesis from exact same calc report -> verify 22-row Appendix, bookmarks, hyperlinks, & ZERO sample PII!
    docx_path = tmp_path / "wenchang_22_thesis.docx"
    docx_res = generate_college_graduation_thesis_docx(calc, docx_path)
    assert docx_res["sheet_count"] == 22
    doc = Document(str(docx_path))
    all_docx_text = "\n".join(p.text for p in doc.paragraphs) + "\n" + "\n".join(
        cell.text for tbl in doc.tables for row in tbl.rows for cell in row.cells
    )
    for forbidden_pii in ("许琦莘", "李小霞", "202109020152"):
        assert forbidden_pii not in all_docx_text
        assert forbidden_pii not in json.dumps(calc, ensure_ascii=False)
        assert forbidden_pii not in txt_01

    appendix_tbl = doc.tables[-2]
    assert len(appendix_tbl.rows) == 23  # 1 header + 22 sheets
    app_dnos = [row.cells[1].text.strip() for row in appendix_tbl.rows[1:]]
    assert app_dnos == expected_dnos

    # Verify XML bookmarks (<w:bookmarkStart>) and TOC internal hyperlinks (<w:hyperlink w:anchor=...>)
    xml_body = doc.element.body.xml
    assert "w:bookmarkStart" in xml_body
    assert "w:hyperlink" in xml_body
    hyperlinks = doc.element.body.findall(".//" + qn("w:hyperlink"))
    bookmarks = {bm.get(qn("w:name")) for bm in doc.element.body.findall(".//" + qn("w:bookmarkStart"))}
    assert len(hyperlinks) >= 35
    assert all(hl.get(qn("w:anchor")) in bookmarks for hl in hyperlinks)
    assert "bm_sheet_19_1" in bookmarks

    # Dynamic manifest co-update test: rename Sheet 09 + append Sheet 22 -> DXF 01, PDF, Calc Book & Word all sync!
    custom_dir = tmp_path / "wenchang_23_custom"
    custom_suite = generate_full_bridge_drawing_set(
        {
            "preset": "wenchang",
            "sheet_title_overrides": {"09": "主梁三维钢束坐标与定位钢筋构造详图"},
            "extra_sheets": [
                {
                    "drawing_no": "22",
                    "role_key": "construction_notes_extra",
                    "category_id": "notes_and_boq",
                    "drawing_type": "notes_and_boq",
                    "title": "预制T梁张拉压浆与架设施工流程图",
                    "scale_label": "示意",
                    "sub_spec": {"variant": "notes"},
                }
            ],
        },
        custom_dir,
    )
    assert len(custom_suite["sheets"]) == 23
    txt_01_custom = _dxf_text_dump(custom_dir / "01" / "01_notes_and_boq.dxf")
    assert "主梁三维钢束坐标与定位钢筋构造详图" in txt_01_custom
    assert "预制T梁张拉压浆与架设施工流程图" in txt_01_custom
    md_custom = Path(custom_suite["calculation_book_path"]).read_text(encoding="utf-8")
    assert "全套23张施工图" in md_custom

    docx_23 = tmp_path / "wenchang_23.docx"
    res_23 = generate_college_graduation_thesis_docx(custom_suite["calculation_report"], docx_23)
    assert res_23["sheet_count"] == 23
    doc_23 = Document(str(docx_23))
    assert len(doc_23.tables[-2].rows) == 24
    docx_23_txt = "\n".join(p.text for p in doc_23.paragraphs) + "\n" + "\n".join(
        c.text for r in doc_23.tables[-2].rows for c in r.cells
    )
    assert "主梁三维钢束坐标与定位钢筋构造详图" in docx_23_txt
    assert "预制T梁张拉压浆与架设施工流程图" in docx_23_txt


def test_p1_tendon_n4_2980mm_overrun_drbridge_new_txt_and_shared_support_chain(tmp_path):
    # 1. Feed Dr.Bridge `new.txt` with N4-1 lateral offset z=110 mm at x=2980 mm from beam end
    #    (where 200 mm standard web has half-width 100 mm -> 10.0 mm centroid overrun!)
    drbridge_new_txt = tmp_path / "new.txt"
    drbridge_new_txt.write_text(
        "\n".join(
            [
                "# 桥梁博士 Dr.Bridge 预应力钢束束心三维空间坐标导出 (new.txt)",
                "[N1-1] 7-Φs15.2 波纹管=70",
                "0, 520, 0",
                "2980, 310, 0",
                "12480, 140, 0",
                "[N4-1] 7-Φs15.2 波纹管=70",
                "0, 1280, -140",
                "1980, 920, -125",
                "2980, 680, -110",
                "6200, 320, 0",
                "12480, 220, 0",
            ]
        ),
        encoding="utf-8",
    )
    parsed = parse_drbridge_tendon_and_section_model(drbridge_new_txt)
    assert parsed is not None
    assert "N4" in parsed["tendons_by_id"]
    assert any(t["tendon_id"] == "N4" for t in parsed["tendons"])

    audit_direct = audit_tendon_3d_section_clearance(drbridge_new_txt)
    assert audit_direct["status"] == "failed"
    n4_issue = next(
        v for v in audit_direct["issues"]
        if v["tendon_id"] == "N4" and v["code"] == "TENDON_CENTROID_OUT_OF_SECTION"
    )
    assert math.isclose(n4_issue["x_from_beam_end_mm"], 2980.0, abs_tol=1.0)
    assert math.isclose(n4_issue["centroid_overrun_mm"], 10.0, abs_tol=0.2)

    # Propagates to full calculation & blocks Sheets 08 and 09 before drawing delivery!
    calc_n4_fail = run_full_bridge_calculation(
        {"preset": "wenchang", "drbridge_tendon_model": str(drbridge_new_txt)}
    )
    assert calc_n4_fail["calculation_status"] == "failed"
    td_fc = next(
        fc for fc in calc_n4_fail["failed_checks"] if "tendon_clearance_audit" in fc["check_key"]
    )
    assert {"08", "09"} <= set(td_fc["related_drawings"])

    sh08_bad = generate_civil_bridge_dxf(
        "tendon_layout",
        {"preset": "wenchang", "variant": "layout", "drawing_no": "08", "_calc_report": calc_n4_fail},
        tmp_path / "bad_n4_08",
        stem="08_bad",
    )
    ver_08_bad = verify_civil_dxf(sh08_bad, calc_report=calc_n4_fail)
    assert ver_08_bad["status"] == "failed"
    assert any(i["code"] == "STRUCTURAL_DESIGN_CHECK_FAILED" for i in ver_08_bad["issues"])

    # 2. Valid default Wenchang 3D tendon geometry -> Sheets 08, 09, and 02 share exact same duct length & non-zero lateral end_z_mm!
    calc_ok = run_full_bridge_calculation({"preset": "wenchang"})
    assert calc_ok["tendon_clearance_audit"]["status"] == "passed"
    t3d = calc_ok["tendon_3d_geometry"]
    end_zs = [abs(float(t["end_z_mm"])) for t in t3d["tendons"]]
    assert any(z >= 40.0 for z in end_zs), "Girder-end tendons N3/N4 should have true lateral z offsets in thickened end block!"
    assert math.isclose(
        calc_ok["bill_of_quantities"]["corrugated_duct_m"],
        round(float(t3d["duct_net_length_per_girder_m"]) * 20.0, 1),
        abs_tol=0.2,
    )

    # 3. Shared support chain `480 + 40 + 480 = 1000 mm` across Sheets 06, 13, 15 + reopen tamper detection!
    sup = calc_ok["support_layout"]
    assert sup["chain_closed"] is True
    assert sup["pier_top_chain_mm"] == [480.0, 40.0, 480.0]
    assert math.isclose(sup["pier_bearing_c2c_mm"], 1000.0, abs_tol=0.1)
    assert math.isclose(sup["total_support_stack_mm"], 244.0, abs_tol=0.1)

    sh06 = generate_civil_bridge_dxf(
        "t_girder_general",
        {"preset": "wenchang", "variant": "longitudinal", "drawing_no": "06", "_calc_report": calc_ok},
        tmp_path / "sup_chain",
        stem="06_ok",
    )
    sh13 = generate_civil_bridge_dxf(
        "bearing_layout",
        {"preset": "wenchang", "drawing_no": "13", "_calc_report": calc_ok},
        tmp_path / "sup_chain",
        stem="13_ok",
    )
    sh15 = generate_civil_bridge_dxf(
        "pier_and_abutment",
        {"preset": "wenchang", "variant": "pier", "drawing_no": "15", "_calc_report": calc_ok},
        tmp_path / "sup_chain",
        stem="15_ok",
    )
    for p in (sh06, sh13, sh15):
        assert verify_civil_dxf(p, calc_report=calc_ok)["status"] == "passed"
        dump = _dxf_text_dump(p)
        assert "480" in dump and "40" in dump and "1000" in dump

    # Tamper `1000` -> `920` on Sheet 15 -> verifier MUST flag SUPPORT_CHAIN_DESYNC!
    doc_15 = ezdxf.readfile(sh15)
    mutated = False
    for ent in doc_15.modelspace():
        if ent.dxftype() == "DIMENSION" and "1000" in str(ent.dxf.get("text", "")):
            ent.dxf.text = str(ent.dxf.get("text", "")).replace("1000", "920")
            mutated = True
    assert mutated is True
    bad_15 = tmp_path / "sup_chain" / "15_desync.dxf"
    doc_15.saveas(bad_15)
    ver_15_bad = verify_civil_dxf(bad_15, calc_report=calc_ok)
    assert ver_15_bad["status"] == "failed"
    assert any(i["code"] == "SUPPORT_CHAIN_DESYNC" for i in ver_15_bad["issues"])


def test_p2_semantic_dxf_content_digest_zero_false_positives_on_identical_rerun(tmp_path):
    suite_dir = tmp_path / "semantic_diff_suite"

    # Run 1: baseline
    run1 = generate_full_bridge_drawing_set({"preset": "wenchang"}, suite_dir)
    assert run1["change_impact"]["mode"] == "initial_baseline"

    # Even if DXF header metadata ($TDUPDATE / $USRTIMER) changes on disk, semantic digest stays identical
    sh01_path = Path(run1["sheets"][0]["dxf_path"])
    sem_before = compute_dxf_semantic_digest(sh01_path)["content_sha256"]
    doc01 = ezdxf.readfile(sh01_path)
    doc01.header["$USRTIMER"] = 0
    resaved_01 = tmp_path / "resaved_01.dxf"
    doc01.saveas(resaved_01)
    assert compute_dxf_semantic_digest(resaved_01)["content_sha256"] == sem_before

    # Run 2: EXACT SAME parameters -> MUST report 0 changed sheets!
    run2 = generate_full_bridge_drawing_set({"preset": "wenchang"}, suite_dir)
    chg2 = run2["change_impact"]
    assert chg2["mode"] == "diff_against_baseline"
    assert chg2["has_changes"] is False
    assert chg2["dxf_content_changed_drawings"] == []
    assert chg2["dxf_sha256_changed_drawings"] == []
    assert chg2["parameter_changes"] == []

    # Run 3: Modify ONLY `abutment_pile_length_m` (15 -> 18) -> ONLY abutment/elevation/BOQ sheets change;
    #        superstructure T-girder sheets 05..12 MUST remain 0-diff!
    run3 = generate_full_bridge_drawing_set(
        {"preset": "wenchang", "abutment_pile_length_m": 18.0},
        suite_dir,
    )
    chg3 = run3["change_impact"]
    assert chg3["has_changes"] is True
    changed_set = set(chg3["dxf_content_changed_drawings"])
    assert {"02", "03", "16", "20"} <= changed_set
    for untouched_girder_sheet in ("05", "06", "07", "08", "09", "10", "11", "12"):
        assert untouched_girder_sheet not in changed_set


def test_p2_planar_frame_substructure_matrix_scope_and_drbridge_substructure_priority():
    calc_base = run_full_bridge_calculation({"preset": "wenchang"})
    fem_builtin = solve_independent_frame_fem(calc_base)

    # 1. Built-in solver uses real 2D planar frame stiffness matrix [K]{u}={F}, NEVER heuristic reduction factor,
    #    and honestly declares its simplified boundary scope!
    pier_meta = fem_builtin["pier_frame_model"]
    assert pier_meta["solver_method"] == "2D_planar_timoshenko_frame_stiffness_matrix"
    assert pier_meta["uses_heuristic_reduction_factor"] is False
    assert pier_meta["dof_count"] >= 24 and pier_meta["dof_count"] % 3 == 0
    assert fem_builtin["verification_scope"] == "builtin_simplified_frame_fem"
    assert "桩顶系梁" in fem_builtin["scope_limitations_zh"] and "m 法" in fem_builtin["scope_limitations_zh"]

    calc_builtin = run_full_bridge_calculation({"preset": "wenchang", "fem_solver": "builtin_frame_fem"})
    tbl_builtin = calc_builtin["defense_verification_table"]
    assert tbl_builtin["verification_scope"] == "builtin_simplified_frame_fem"
    assert "内置简化刚架复核" in tbl_builtin["method_label"]
    assert len(tbl_builtin["substructure_rows"]) >= 4

    # 2. When real Dr.Bridge substructure results (`桥梁博士下部分册`) are provided, they strictly override built-in values!
    calc_drb_sub = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "fem_solver": "builtin_frame_fem",
            "substructure_fem_results": {
                "software_name": "桥梁博士 Dr.Bridge V4.4（下部分册实算）",
                "checked_by": "桥梁机房实算复核",
                "metrics": {
                    "cap_hogging_moment_uls_knm": 2588.5,
                    "cap_sagging_moment_uls_knm": 1652.0,
                    "pier_column_axial_uls_kn": 3905.0,
                    "pier_column_moment_uls_knm": 512.0,
                    "tie_beam_moment_uls_knm": 324.6,
                    "tie_beam_crack_width_mm": 0.142,
                },
            },
        }
    )
    sub_rows = {r["metric_key"]: r for r in calc_drb_sub["defense_verification_table"]["substructure_rows"]}
    assert math.isclose(sub_rows["cap_hogging_moment_uls_knm"]["software_value"], 2588.5, abs_tol=0.05)
    assert math.isclose(sub_rows["tie_beam_moment_uls_knm"]["software_value"], 324.6, abs_tol=0.05)
    assert math.isclose(sub_rows["tie_beam_crack_width_mm"]["software_value"], 0.142, abs_tol=1e-4)
    assert sub_rows["cap_hogging_moment_uls_knm"]["provenance_tag"] == "桥梁博士/外部FEM实算导入"

    md_sub = generate_bridge_calculation_book_markdown(calc_drb_sub)
    assert "2588.5" in md_sub and "324.6" in md_sub and "桥梁博士/外部FEM实算导入" in md_sub
