"""Integration tests for v2.5+ Civil/Bridge Optimization Plan, Review Counterexamples & 18-Sheet Suite."""

from __future__ import annotations

import math
from pathlib import Path
import re

import ezdxf

from cad_skill_core.benchmark.regression_flywheel import (
    record_civil_correction_regression,
    replay_regression_vault,
)
from cad_skill_core.civil import (
    generate_civil_bridge_bundle,
    generate_civil_bridge_dxf,
    generate_full_bridge_drawing_set,
    lookup_bar_or_tendon,
    run_full_bridge_calculation,
    setup_civil_dxf_document,
    verify_civil_dxf,
)
from cad_skill_core.standards.preference_memory import (
    learn_preferences_from_sample_dxf,
    load_preference_store,
)


def test_wenchang_cross_section_7000_8000_9000_and_real_2pct_slope(tmp_path):
    calc = run_full_bridge_calculation({"preset": "wenchang"})
    assert calc["status"] == "passed"
    assert calc["consistency_audit"]["status"] == "passed"
    assert calc["girder_checks"]["status"] == "passed"
    assert calc["substructure_checks"]["status"] == "passed"

    spec = {
        "preset": "wenchang",
        "_calc_report": calc,
        "drawing_no": "QL-05",
    }
    bundle = generate_civil_bridge_bundle("t_girder_section", spec, tmp_path / "wenchang_cross", stem="QL-05_cross")
    assert bundle["status"] == "passed"
    assert bundle["geometry_verification"]["status"] == "passed"
    assert bundle["geometry_verification"]["error_count"] == 0
    assert bundle["rubric_score"]["grade"] == "S"

    doc = ezdxf.readfile(tmp_path / "wenchang_cross" / "QL-05_cross.dxf")
    dim_texts = [str(e.dxf.get("text", "")) for e in doc.modelspace() if e.dxftype() == "DIMENSION"]
    joined = " | ".join(dim_texts)
    for token in ("7000", "8000", "9000", "1950", "600", "400", "80", "100"):
        assert token in joined, f"missing expected dimension token {token} in {joined}"


def test_reactive_calc_span_paving_dead_load_and_strict_unresolved_basis(tmp_path):
    # 1. Default Wenchang flags all unconfirmed survey & positioning basis items
    r_base = run_full_bridge_calculation({"preset": "wenchang"})
    unresolved_fields = {u["field"] for u in r_base["unresolved_assumptions"]}
    assert {
        "flood_elevation_m",
        "deck_elevation_m",
        "hydrology",
        "soil_layers",
        "pile_length_m",
        "calc_span_m",
    } <= unresolved_fields

    # 2. Supplying ONLY flood/deck elevations and pile_length_m MUST NOT clear soil_layers, hydrology, or calc_span_m!
    r_partial = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "flood_elevation_m": 80.600,
            "deck_elevation_m": 86.200,
            "pile_length_m": 22.0,
        }
    )
    partial_fields = {u["field"] for u in r_partial["unresolved_assumptions"]}
    assert {"soil_layers", "calc_span_m", "hydrology"} <= partial_fields

    # 3. Supplying complete survey + borehole soil_layers + bearing_center_offset_m clears unresolved_assumptions,
    #    while pending_graduation_deliverables still tracks thesis & E1/E2 seismic checks!
    r_confirmed = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "flood_elevation_m": 80.600,
            "deck_elevation_m": 86.200,
            "pile_length_m": 22.0,
            "bearing_center_offset_m": 0.40,
            "hydrology": {
                "area_m2": 325.0,
                "wetted_perimeter_m": 64.5,
                "roughness_inv": 25.0,
                "slope_permille": 0.8,
                "channel_width_m": 96.0,
                "standard_span_m": 25.0,
            },
            "soil_layers": [
                {"name": "①粉质黏土", "thickness_m": 5.0, "qik_kpa": 55.0, "fa0_kpa": 140.0},
                {"name": "②密实砂砾石", "thickness_m": 20.0, "qik_kpa": 130.0, "fa0_kpa": 420.0},
            ],
        }
    )
    assert r_confirmed["unresolved_assumptions"] == []
    assert math.isclose(r_confirmed["project_summary"]["calc_span_m"], 24.96 - 0.80, abs_tol=1e-3)
    pending_ids = {
        d["id"] for d in r_confirmed["pending_graduation_deliverables"] if d["status"] == "pending"
    }
    assert {"design_specification_thesis", "seismic_e1_e2_verification"} <= pending_ids

    # 4. Changing asphalt_thick_mm from 100mm -> 150mm updates BOTH BOQ (80 -> 120 m3) AND girder dead moment!
    r_asp150 = run_full_bridge_calculation({"preset": "wenchang", "asphalt_thick_mm": 150.0})
    assert math.isclose(r_base["bill_of_quantities"]["asphalt_paving_m3"], 80.0, abs_tol=0.01)
    assert math.isclose(r_asp150["bill_of_quantities"]["asphalt_paving_m3"], 120.0, abs_tol=0.01)
    m_dead_100 = r_base["superstructure_envelopes"]["mid_span_summary"]["m_dead_knm"]
    m_dead_150 = r_asp150["superstructure_envelopes"]["mid_span_summary"]["m_dead_knm"]
    assert m_dead_150 > m_dead_100 + 130.0

    # 5. Changing sigma_con_mpa or calc_span_m updates both calculation report and tendon_layout DXF
    r_mod = run_full_bridge_calculation({"preset": "wenchang", "calc_span_m": 23.6, "sigma_con_mpa": 1335.0})
    pe_base = r_base["prestress"]["loss_profile"][5]["effective_prestress_mpa"]
    pe_mod = r_mod["prestress"]["loss_profile"][5]["effective_prestress_mpa"]
    assert not math.isclose(pe_base, pe_mod, abs_tol=5.0)

    tendon_dxf = generate_civil_bridge_dxf(
        "tendon_layout",
        {"preset": "wenchang", "_calc_report": r_mod, "drawing_no": "QL-08"},
        tmp_path / "tendon_mod",
        stem="QL-08_mod",
    )
    ver = verify_civil_dxf(tendon_dxf, calc_report=r_mod)
    assert ver["status"] == "passed"
    doc = ezdxf.readfile(tendon_dxf)
    all_txt = " ".join(str(e.dxf.get("text", "")) for e in doc.modelspace() if e.dxftype() == "TEXT")
    assert "σcon=1335MPa" in all_txt
    assert f"{pe_mod:.2f}MPa" in all_txt


def test_intermediate_pier_two_span_reactions_and_failure_propagation_to_suite_and_ql12(tmp_path):
    # 1. Intermediate pier on 4x25m carries BOTH left & right adjacent spans (~2x single-span dead load)
    r_4span = run_full_bridge_calculation({"preset": "wenchang"})
    r_1span = run_full_bridge_calculation({"preset": "wenchang", "n_spans": 1})
    pr4 = r_4span["pier_reaction_summary"]
    pr1 = r_1span["pier_reaction_summary"]
    assert pr4["n_adjacent_spans"] == 2
    assert pr1["n_adjacent_spans"] == 1
    assert pr4["right_span_total_unfactored_dead_kn"] == pr4["left_span_total_unfactored_dead_kn"]
    assert pr4["left_span_total_unfactored_dead_kn"] > 1900.0
    assert math.isclose(
        pr4["pier_total_unfactored_dead_kn"],
        2.0 * pr4["left_span_total_unfactored_dead_kn"],
        abs_tol=0.05,
    )
    assert r_4span["substructure_checks"]["pier_column"]["n_uls_kn"] > 1.50 * r_1span["substructure_checks"]["pier_column"]["n_uls_kn"]

    # 2. Counterexample: pile_length_m = 1.0 -> substructure fails AND full 18-sheet manifest status MUST be "failed",
    #    while still saving bridge_calculation_report.json and pinpointing failed component + related drawings!
    fail_suite = generate_full_bridge_drawing_set(
        {"preset": "wenchang", "pile_length_m": 1.0},
        tmp_path / "pile_1m_counterexample",
    )
    assert fail_suite["status"] == "failed"
    assert fail_suite["calculation_report"]["status"] == "failed"
    assert Path(fail_suite["calculation_report_path"]).is_file()
    pile_fc = next(fc for fc in fail_suite["failed_checks"] if fc["check_key"] == "substructure_checks.bored_pile")
    assert pile_fc["demand"] > pile_fc["allowable"]
    assert "QL-17" in pile_fc["related_drawings"]
    assert "QL-17" in fail_suite["failed_drawings"]
    sh_17 = next(s for s in fail_suite["sheets"] if s["drawing_no"] == "QL-17")
    assert sh_17["verify_status"] == "failed"

    # 3. Counterexample: overloaded small bearing -> QL-12 MUST display "> 10.0MPa" & "超限不合格", NEVER "≤ 10.0MPa"!
    calc_bad_brg = run_full_bridge_calculation(
        {"preset": "wenchang", "bearing": {"bearing_width_mm": 250.0, "bearing_length_mm": 250.0}}
    )
    assert calc_bad_brg["status"] == "failed"
    assert calc_bad_brg["bearing_checks"]["passed"] is False
    assert calc_bad_brg["bearing_checks"]["compressive_stress_mpa"] > 10.0

    ql12_dxf = generate_civil_bridge_dxf(
        "bearing_layout",
        {"preset": "wenchang", "_calc_report": calc_bad_brg, "drawing_no": "QL-12"},
        tmp_path / "bad_bearing",
        stem="QL-12_overload",
    )
    doc_12 = ezdxf.readfile(ql12_dxf)
    txt_12 = " ".join(str(e.dxf.get("text", "")) for e in doc_12.modelspace() if e.dxftype() == "TEXT")
    assert "超限不合格" in txt_12
    assert "> 10.0MPa" in txt_12
    assert "≤ 10.0MPa" not in txt_12

    ver_12 = verify_civil_dxf(ql12_dxf, calc_report=calc_bad_brg)
    assert ver_12["status"] == "failed"
    assert any(i["code"] == "STRUCTURAL_DESIGN_CHECK_FAILED" for i in ver_12["issues"])

    # Also verify standalone false-inequality detector catches "12.82MPa ≤ 10.0MPa" even without calc_report!
    for ent in doc_12.modelspace():
        if ent.dxftype() == "TEXT" and "支座验算结论" in str(ent.dxf.get("text", "")):
            ent.dxf.text = "支座承压验算：σc=12.82MPa ≤ 10.0MPa，合格"
    fake_pass_12 = tmp_path / "bad_bearing" / "QL-12_fake_ineq.dxf"
    doc_12.saveas(fake_pass_12)
    ver_fake_12 = verify_civil_dxf(fake_pass_12)
    assert ver_fake_12["status"] == "failed"
    assert any(i["code"] == "FALSE_INEQUALITY_ON_DRAWING" for i in ver_fake_12["issues"])


def test_reopen_dxf_verifier_catches_1_to_400_scale_fake_slope_and_stale_prestress(tmp_path):
    elev_dxf = generate_civil_bridge_dxf(
        "bridge_elevation",
        {
            "drawing_no": "QL-03",
            "scale": "1:400",
            "deck_elevation_m": 85.000,
            "flood_elevation_m": 84.000,
        },
        tmp_path / "elev_400",
        stem="elev_1m",
    )
    ver_ok = verify_civil_dxf(elev_dxf)
    assert ver_ok["status"] == "passed"

    doc_el = ezdxf.readfile(elev_dxf)
    triangles = [
        e for e in doc_el.modelspace()
        if e.dxftype() == "LWPOLYLINE" and len(list(e.get_points("xy"))) == 3
    ]
    assert len(triangles) == 2
    y_tips = sorted(min(p[1] for p in t.get_points("xy")) for t in triangles)
    assert math.isclose(y_tips[1] - y_tips[0], 2.50, abs_tol=0.02)

    low_tri = min(triangles, key=lambda t: min(p[1] for p in t.get_points("xy")))
    pts_shifted = [(p[0], p[1] - 5.5) for p in low_tri.get_points("xy")]
    low_tri.set_points(pts_shifted, format="xy")
    bad_elev_dxf = tmp_path / "elev_400" / "bad_elev.dxf"
    doc_el.saveas(bad_elev_dxf)
    ver_bad_el = verify_civil_dxf(bad_elev_dxf)
    assert ver_bad_el["status"] == "failed"
    el_issue = next(i for i in ver_bad_el["issues"] if i["code"] == "TRUE_SCALE_ELEVATION_MISMATCH")
    assert el_issue["drawing_no"] == "QL-03"
    assert math.isclose(abs(el_issue["delta"]), 5.5, abs_tol=0.05)

    cross_dxf = generate_civil_bridge_dxf(
        "t_girder_section",
        {"preset": "wenchang", "drawing_no": "QL-05"},
        tmp_path / "cross_check",
        stem="cross_ok",
    )
    doc_cr = ezdxf.readfile(cross_dxf)
    for ent in doc_cr.modelspace():
        if ent.dxftype() == "LWPOLYLINE":
            pts = list(ent.get_points("xy"))
            if len(pts) == 6 and max(p[1] for p in pts) > 195.0:
                avg_y = sum(p[1] for p in pts[:3]) / 3.0
                flat_pts = [(pts[0][0], avg_y), (pts[1][0], avg_y), (pts[2][0], avg_y), (pts[3][0], avg_y + 2.5), (pts[4][0], avg_y + 2.5), (pts[5][0], avg_y + 2.5)]
                ent.set_points(flat_pts, format="xy")
    bad_slope_dxf = tmp_path / "cross_check" / "bad_flat_slope.dxf"
    doc_cr.saveas(bad_slope_dxf)
    ver_bad_slope = verify_civil_dxf(bad_slope_dxf)
    assert ver_bad_slope["status"] == "failed"
    slope_issue = next(i for i in ver_bad_slope["issues"] if i["code"] == "DECK_CROSS_SLOPE_MISMATCH")
    assert slope_issue["drawing_no"] == "QL-05"
    assert math.isclose(slope_issue["measured"], 0.0, abs_tol=0.05)

    calc_wc = run_full_bridge_calculation({"preset": "wenchang"})
    td_dxf = generate_civil_bridge_dxf(
        "tendon_layout",
        {"preset": "wenchang", "_calc_report": calc_wc, "drawing_no": "QL-08"},
        tmp_path / "td_check",
        stem="td_ok",
    )
    doc_td = ezdxf.readfile(td_dxf)
    for ent in doc_td.modelspace():
        if ent.dxftype() == "TEXT" and "跨中有效预应力=" in str(ent.dxf.get("text", "")):
            ent.dxf.text = re.sub(r"跨中有效预应力=\d+(?:\.\d+)?MPa", "跨中有效预应力=999.00MPa", ent.dxf.text)
    bad_td_dxf = tmp_path / "td_check" / "bad_td.dxf"
    doc_td.saveas(bad_td_dxf)
    ver_bad_td = verify_civil_dxf(bad_td_dxf, calc_report=calc_wc)
    assert ver_bad_td["status"] == "failed"
    td_issue = next(i for i in ver_bad_td["issues"] if i["code"] == "TENDON_EFFECTIVE_PRESTRESS_MISMATCH")
    assert td_issue["drawing_no"] == "QL-08"


def test_delivery_audit_on_ql09_ql13_ql17_ql18_and_visible_table_entity_tamper_detection(tmp_path):
    suite = generate_full_bridge_drawing_set({"preset": "wenchang"}, tmp_path / "wenchang_18_sheets")
    assert suite["status"] == "passed"
    cov = suite["coverage_audit"]
    assert cov["status"] == "passed"
    assert cov["total_a3_sheets"] == 18
    assert cov["missing_categories"] == []
    assert cov["failed_sheets"] == []
    assert cov["untraced_bar_or_tendon_ids"] == []
    assert all(sh["rubric_score"] >= 92.0 for sh in suite["sheets"])

    calc = suite["calculation_report"]
    for sample_id in (
        "N1",
        "N4",
        "R1",
        "GJ1",
        "W1",
        "HG1",
        "HL1",
        "LX1",
        "SF1",
        "DB1",
        "DB2",
        "GL1",
        "DZ1",
        "XL1",
        "XL2",
        "ZJ1",
        "QT1",
        "QT2",
        "QT3",
        "DS1",
    ):
        traced = lookup_bar_or_tendon(calc, sample_id)
        assert traced["calc_basis"]
        assert traced.get("total_weight_bridge_kg", traced.get("bridge_total_weight_kg", 0.0)) > 0

    sheets_by_no = {sh["drawing_no"]: sh for sh in suite["sheets"]}

    # 1. QL-09: Verify 6 R1 circles in bottom bulb + 6 R2 + 8 R3 = 20 circles + half-span stirrup zone verification!
    ql09_path = Path(sheets_by_no["QL-09"]["dxf_path"])
    doc_09 = ezdxf.readfile(ql09_path)
    rebar_circles_09 = [
        e for e in doc_09.modelspace()
        if e.dxftype() == "CIRCLE" and e.dxf.get("layer") == "REBAR" and 0.35 <= e.dxf.radius <= 3.0
    ]
    assert len(rebar_circles_09) == 20  # 6 (R1) + 6 (R2) + 8 (R3)
    dims_09 = " | ".join(str(e.dxf.get("text", "")) for e in doc_09.modelspace() if e.dxftype() == "DIMENSION")
    txts_09 = " ".join(str(e.dxf.get("text", "")) for e in doc_09.modelspace() if e.dxftype() == "TEXT")
    assert "20×100=2000" in dims_09
    assert "69×150=10350" in dims_09
    assert "支点中心线" in txts_09 and "跨中对称线(L/2)" in txts_09 and "180根" in txts_09

    # Tamper visible table R1 count TEXT from "6" -> "999" without touching XDATA!
    doc_09_tbl = ezdxf.readfile(ql09_path)
    r1_y = next(
        float((e.get_placement()[1] or e.dxf.insert).y)
        for e in doc_09_tbl.modelspace()
        if e.dxftype() == "TEXT" and str(e.dxf.get("text", "")).strip() == "R1" and float((e.get_placement()[1] or e.dxf.insert).x) > 210.0
    )
    mutated_999 = False
    for ent in doc_09_tbl.modelspace():
        if ent.dxftype() == "TEXT" and str(ent.dxf.get("text", "")).strip() == "6":
            ins = ent.get_placement()[1] or ent.dxf.insert
            if float(ins.x) > 210.0 and abs(float(ins.y) - r1_y) <= 1.5:
                ent.dxf.text = "999"
                mutated_999 = True
                break
    assert mutated_999 is True
    bad_09_tbl_path = tmp_path / "QL09_table_999.dxf"
    doc_09_tbl.saveas(bad_09_tbl_path)
    ver_09_tbl = verify_civil_dxf(bad_09_tbl_path, calc_report=calc)
    assert ver_09_tbl["status"] == "failed"
    codes_09_tbl = {i["code"] for i in ver_09_tbl["issues"]}
    assert "REBAR_COUNT_LEDGER_MISMATCH" in codes_09_tbl
    assert "VISIBLE_REBAR_TABLE_ARITHMETIC_MISMATCH" in codes_09_tbl
    assert "REBAR_ENTITY_COUNT_MISMATCH" in codes_09_tbl

    # Delete 1 bottom bulb circle (6 -> 5 circles) in QL-09 -> verifier MUST catch REBAR_ENTITY_COUNT_MISMATCH!
    doc_09_dot = ezdxf.readfile(ql09_path)
    bulb_dot = min(
        (
            e for e in doc_09_dot.modelspace()
            if e.dxftype() == "CIRCLE" and e.dxf.get("layer") == "REBAR" and 0.35 <= e.dxf.radius <= 3.0
        ),
        key=lambda e: float(e.dxf.center.y),
    )
    doc_09_dot.modelspace().delete_entity(bulb_dot)
    bad_09_dot_path = tmp_path / "QL09_5dots.dxf"
    doc_09_dot.saveas(bad_09_dot_path)
    ver_09_dot = verify_civil_dxf(bad_09_dot_path, calc_report=calc)
    assert ver_09_dot["status"] == "failed"
    dot_issue = next(i for i in ver_09_dot["issues"] if i["code"] == "REBAR_ENTITY_COUNT_MISMATCH" and "R1" in i["message"])
    assert dot_issue["expected"] == 6 and dot_issue["measured"] == 5

    # 2. QL-13: Must contain all 6 required sub-details + 500mm debonded strip endpoints (250+250) + enlarged corbel DB2 detail!
    ql13_ver = verify_civil_dxf(sheets_by_no["QL-13"]["dxf_path"], calc_report=calc)
    assert ql13_ver["status"] == "passed"
    assert {
        "crash_barrier_detail",
        "expansion_joint_detail",
        "scupper_drain_detail",
        "deck_continuity_detail",
        "approach_slab_detail",
        "approach_corbel_detail",
    } <= set(ql13_ver["registered_views"])
    doc_13 = ezdxf.readfile(sheets_by_no["QL-13"]["dxf_path"])
    txts_13 = " ".join(str(e.dxf.get("text", "")) for e in doc_13.modelspace() if e.dxftype() == "TEXT")
    dims_13 = " | ".join(str(e.dxf.get("text", "")) for e in doc_13.modelspace() if e.dxftype() == "DIMENSION")
    assert "排水方向" in txts_13 and "防水层收口" in txts_13 and "C50钢纤维砼槽口" in txts_13
    assert "250" in dims_13 and "无粘结层 500" in dims_13 and "牛腿宽 350" in dims_13 and "锚入 280" in dims_13

    # 3. QL-17 & QL-18: Verify grouped substructure unit schedules, JTG §9.3.2 anchorage basis, and aligned A-A cut + backwall!
    j_anch = calc["substructure_checks"]["joint_anchorage"]
    assert j_anch["passed"] is True
    assert j_anch["xl1_tie_beam_to_pier"]["design_la_mm"] == 780.0 >= j_anch["xl1_tie_beam_to_pier"]["req_35d_mm"]
    assert j_anch["qt2_wall_to_abutment_cap"]["design_la_mm"] == 560.0 >= j_anch["qt2_wall_to_abutment_cap"]["req_35d_mm"]

    ql17_ver = verify_civil_dxf(sheets_by_no["QL-17"]["dxf_path"], calc_report=calc)
    assert ql17_ver["status"] == "passed"
    assert {
        "pier_pile_elev",
        "pier_col_section",
        "bored_pile_section",
        "tie_beam_joint_detail",
        "tie_beam_section",
    } <= set(ql17_ver["registered_views"])
    doc_17 = ezdxf.readfile(sheets_by_no["QL-17"]["dxf_path"])
    dims_17 = " | ".join(str(e.dxf.get("text", "")) for e in doc_17.modelspace() if e.dxftype() == "DIMENSION")
    txts_17 = " ".join(str(e.dxf.get("text", "")) for e in doc_17.modelspace() if e.dxftype() == "TEXT")
    pile_mm = int(round(calc["substructure_checks"]["bored_pile"]["design_length_m"] * 1000.0))
    for req_tok in ("5200", "1200", "5500", "1400", "780", str(pile_mm)):
        assert req_tok in dims_17
    for req_unit_tok in ("构件部位", "单根墩柱", "单道系梁", "单根桩基", "每墩件数", "2柱", "1道", "2桩", "单座双柱墩钢筋合计"):
        assert req_unit_tok in txts_17

    # Tamper "2柱" -> "1柱" on QL-17 without updating 每墩重(kg) -> verifier MUST catch VISIBLE_REBAR_UNIT_MULTIPLIER_MISMATCH!
    doc_17_bad = ezdxf.readfile(sheets_by_no["QL-17"]["dxf_path"])
    for ent in doc_17_bad.modelspace():
        if ent.dxftype() == "TEXT" and str(ent.dxf.get("text", "")).strip() == "2柱":
            ent.dxf.text = "1柱"
            break
    bad_17_path = tmp_path / "QL17_bad_mult.dxf"
    doc_17_bad.saveas(bad_17_path)
    ver_17_bad = verify_civil_dxf(bad_17_path, calc_report=calc)
    assert ver_17_bad["status"] == "failed"
    assert any(i["code"] == "VISIBLE_REBAR_UNIT_MULTIPLIER_MISMATCH" for i in ver_17_bad["issues"])

    ql18_ver = verify_civil_dxf(sheets_by_no["QL-18"]["dxf_path"], calc_report=calc)
    assert ql18_ver["status"] == "passed"
    assert {
        "abutment_plan",
        "abutment_elev",
        "abutment_side_rebar",
        "abutment_cap_section",
        "abutment_joint_detail",
        "abutment_pile_section",
    } <= set(ql18_ver["registered_views"])
    doc_18 = ezdxf.readfile(sheets_by_no["QL-18"]["dxf_path"])
    dims_18 = " | ".join(str(e.dxf.get("text", "")) for e in doc_18.modelspace() if e.dxftype() == "DIMENSION")
    txts_18 = " ".join(str(e.dxf.get("text", "")) for e in doc_18.modelspace() if e.dxftype() == "TEXT")
    for req_tok_18 in ("9000", "5400", "2000", "1800", "550", "1750", "560", "1400"):
        assert req_tok_18 in dims_18
    assert "道路中心线" in txts_18 and "耳墙" in txts_18 and "弯钩朝内" in txts_18
    assert "2#垫石" in txts_18 and "背墙" in txts_18 and "单座桥台钢筋合计" in txts_18

    # Verify manifest sheet titles are all true Chinese titles (never raw python drawing_type identifiers!)
    for sh in suite["sheets"]:
        assert sh["title"] not in {"rebar_detail", "t_girder_general", "tendon_layout", "bearing_layout", "deck_ancillary"}
        assert any("\u4e00" <= ch <= "\u9fff" for ch in sh["title"])

    # Verify QL-01 visible 18-sheet index table & zero leaked snake_case field names, QL-02 volume breakdown, QL-08 tendon table, and QL-14/15 consistency!
    doc_01 = ezdxf.readfile(sheets_by_no["QL-01"]["dxf_path"])
    txts_01 = " ".join(str(e.dxf.get("text", "")) for e in doc_01.modelspace() if e.dxftype() == "TEXT")
    for idx_k in range(1, 19):
        assert f"QL-{idx_k:02d}" in txts_01
    assert "flood_elevation_m" not in txts_01 and "soil_layers" not in txts_01

    vbk = calc["bill_of_quantities"]["volume_breakdown"]
    assert vbk["tie_beam_m3"] > 0 and vbk["abut_wall_m3"] > 0
    assert vbk["abut_pile_length_m"] < vbk["pier_pile_length_m"]

    ql08_ver = verify_civil_dxf(sheets_by_no["QL-08"]["dxf_path"], calc_report=calc)
    assert ql08_ver["status"] == "passed"
    doc_08 = ezdxf.readfile(sheets_by_no["QL-08"]["dxf_path"])
    txts_08 = " ".join(str(e.dxf.get("text", "")) for e in doc_08.modelspace() if e.dxftype() == "TEXT")
    assert "预应力钢束数量及张拉参数表" in txts_08 and "单梁钢束合计" in txts_08

    ql14_ver = verify_civil_dxf(sheets_by_no["QL-14"]["dxf_path"], calc_report=calc)
    ql15_ver = verify_civil_dxf(sheets_by_no["QL-15"]["dxf_path"], calc_report=calc)
    ql16_ver = verify_civil_dxf(sheets_by_no["QL-16"]["dxf_path"], calc_report=calc)
    assert ql14_ver["status"] == "passed"
    assert ql15_ver["status"] == "passed"
    assert ql16_ver["status"] == "passed"

    # Tamper QL-16 single-line note to simulate truncated unclosed parenthesis -> verifier MUST catch TRUNCATED_UNCLOSED_PARENTHESIS!
    doc_16_trunc = ezdxf.readfile(sheets_by_no["QL-16"]["dxf_path"])
    mutated_trunc = False
    for ent in doc_16_trunc.modelspace():
        if ent.dxftype() == "TEXT" and "主控验算：" in str(ent.dxf.get("text", "")):
            ent.dxf.text = "主控验算：盖梁裂缝宽度验算 wcr: 0.176 <= 0.2 mm ("
            mutated_trunc = True
            break
    assert mutated_trunc is True
    bad_16_path = tmp_path / "QL16_truncated_paren.dxf"
    doc_16_trunc.saveas(bad_16_path)
    ver_16_bad = verify_civil_dxf(bad_16_path, calc_report=calc)
    assert ver_16_bad["status"] == "failed"
    assert any(i["code"] == "TRUNCATED_UNCLOSED_PARENTHESIS" for i in ver_16_bad["issues"])

    # Verify A3 1:1 B&W print PDF + PNG bundle export on QL-18
    b18 = generate_civil_bridge_bundle(
        "rebar_detail",
        {"preset": "wenchang", "component": "abutment_rebar", "drawing_no": "QL-18", "_calc_report": calc},
        tmp_path / "ql18_print_bundle",
        stem="QL-18_print",
    )
    assert b18["status"] == "passed"
    assert Path(b18["print_pdf_path"]).is_file() and Path(b18["print_pdf_path"]).stat().st_size > 5000
    assert Path(b18["preview_png_path"]).is_file() and Path(b18["preview_png_path"]).stat().st_size > 15000


def test_civil_preference_learning_scope_and_correction_regression_flywheel(tmp_path):
    mem_file = tmp_path / ".cad-memory" / "project-preferences.json"

    sample_pref = tmp_path / "fav_bridge_sample.dxf"
    doc_fav, _, _ = setup_civil_dxf_document(
        title="T梁标准横断面样图",
        style_overrides={"font": "gbenor.shx", "layer_colors": {"OUTLINE": 2, "DIM": 4}},
    )
    doc_fav.saveas(sample_pref)
    learned = learn_preferences_from_sample_dxf(sample_pref, mem_file, confirm=True)
    assert any(e.key == "civil_text_font" and e.value == "gbenor.shx" and e.status == "confirmed" for e in learned)
    assert all(e.part_family == "civil_bridge" for e in learned)
    assert all(e.evidence[0].source_drawing == "fav_bridge_sample.dxf" for e in learned)

    stray_sample = tmp_path / "stray_other_style.dxf"
    doc_stray, _, _ = setup_civil_dxf_document(
        title="外单位桥墩样图",
        style_overrides={"font": "txt.shx", "layer_colors": {"OUTLINE": 6, "DIM": 1}},
    )
    doc_stray.saveas(stray_sample)
    learn_preferences_from_sample_dxf(stray_sample, mem_file, confirm=False)

    store = load_preference_store(mem_file)
    confirmed_fonts = [e.value for e in store.entries if e.key == "civil_text_font" and e.status == "confirmed"]
    assert confirmed_fonts == ["gbenor.shx"]

    new_dxf = generate_civil_bridge_dxf(
        "t_girder_section",
        {
            "drawing_no": "QL-NEW",
            "single_deck_width_mm": 10200.0,
            "girder_spacing_mm": 2250.0,
            "n_girders": 5,
            "preference_memory_path": str(mem_file),
        },
        tmp_path / "new_bridge",
        stem="new_cross",
    )
    ver_new = verify_civil_dxf(new_dxf)
    assert ver_new["status"] == "passed"
    reopened_new = ezdxf.readfile(new_dxf)
    assert reopened_new.styles.get("CIVIL_TEXT").dxf.font == "gbenor.shx"
    assert reopened_new.layers.get("OUTLINE").dxf.color == 2
    dim_txts = " | ".join(str(e.dxf.get("text", "")) for e in reopened_new.modelspace() if e.dxftype() == "DIMENSION")
    assert "10200" in dim_txts and "2250" in dim_txts

    doc_faulty = ezdxf.readfile(new_dxf)
    for ent in doc_faulty.modelspace():
        if ent.dxftype() == "LWPOLYLINE":
            pts = list(ent.get_points("xy"))
            if len(pts) == 6 and max(p[1] for p in pts) > 195.0:
                y0 = pts[1][1]
                ent.set_points([(p[0], y0 if idx < 3 else y0 + 2.0) for idx, p in enumerate(pts)], format="xy")
    faulty_path = tmp_path / "faulty_flat_deck.dxf"
    doc_faulty.saveas(faulty_path)

    vault_dir = tmp_path / "reg_vault"
    record_civil_correction_regression(
        vault_dir,
        case_id="wenchang-2pct-cross-slope",
        title="桥面标2%横坡但几何画成0%平线纠错回归",
        faulty_dxf_path=faulty_path,
        drawing_type="t_girder_section",
        corrected_spec={"preset": "wenchang", "drawing_no": "QL-05"},
        calc_params={"preset": "wenchang"},
        expected_bad_code="DECK_CROSS_SLOPE_MISMATCH",
        bug_note="确保桥面多段线实测坡度严格等于2%",
    )
    rep = replay_regression_vault(vault_dir)
    assert rep["total"] == 1
    assert rep["all_passed"] is True
