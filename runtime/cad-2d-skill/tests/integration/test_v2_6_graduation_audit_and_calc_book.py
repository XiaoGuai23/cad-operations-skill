"""Integration tests for v2.6.0 Civil Bridge Optimization, Calculation Book & Graduation Audit."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.civil import (
    compute_independent_hand_cross_check,
    generate_bridge_calculation_book_markdown,
    generate_civil_bridge_dxf,
    generate_full_bridge_drawing_set,
    ingest_bridge_survey_data,
    lookup_bar_or_tendon,
    run_full_bridge_calculation,
    verify_civil_dxf,
)
from cad_skill_core.mcp.http_app import create_mcp_http_app


def test_independent_abutment_calculation_and_survey_data_ingestion(tmp_path):
    # 1. Default Wenchang derives abutment pile length independently from abutment loads + soil layers (never pier - 2.0!)
    calc = run_full_bridge_calculation({"preset": "wenchang"})
    abut = calc["abutment_checks"]
    assert abut["status"] == "passed"
    assert abut["earth_pressure"]["rankine_ka"] > 0.25
    assert 0.50 <= abut["earth_pressure"]["equivalent_surcharge_heq_m"] <= 0.80
    assert abut["abutment_cap"]["passed"] is True
    assert abut["backwall"]["passed"] is True
    assert abut["pedestal_local_bearing"]["passed"] is True
    assert abut["bored_pile"]["passed"] is True
    assert abut["bored_pile"]["bearing_stratum_socket_m"] >= abut["bored_pile"]["min_required_socket_m"]

    pier_len = calc["substructure_checks"]["bored_pile"]["design_length_m"]
    abut_len = abut["bored_pile"]["design_length_m"]
    assert pier_len == 21.0
    assert abut_len != pier_len - 2.0  # No longer hardcoded 21 - 2 = 19m!
    assert calc["bill_of_quantities"]["volume_breakdown"]["abut_pile_length_m"] == abut_len
    qt3 = lookup_bar_or_tendon(calc, "QT3")
    assert math.isclose(qt3["single_length_cm"], (abut_len + 1.0) * 100.0, abs_tol=0.5)

    # 2. Proof of independence: soft deep silt at 0#/4# abutments makes abutment piles LONGER than pier piles!
    calc_soft_abut = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "abutment_soil_layers": [
                {"name": "①台背素填土", "thickness_m": 3.0, "qik_kpa": 20.0, "fa0_kpa": 75.0},
                {"name": "②软塑粉质黏土", "thickness_m": 16.0, "qik_kpa": 36.0, "fa0_kpa": 110.0},
                {"name": "③密实砂砾石持力层", "thickness_m": 15.0, "qik_kpa": 125.0, "fa0_kpa": 400.0},
            ],
        }
    )
    assert calc_soft_abut["substructure_checks"]["bored_pile"]["design_length_m"] == 21.0
    assert calc_soft_abut["abutment_checks"]["bored_pile"]["design_length_m"] >= 23.0

    # 3. Counterexample: abutment_pile_length_m = 1.5 -> fails abutment_checks.bored_pile & flags 03 / 16 / 20 (legacy QL-03 / QL-15 / QL-18)!
    calc_short_abut = run_full_bridge_calculation({"preset": "wenchang", "abutment_pile_length_m": 1.5})
    assert calc_short_abut["calculation_status"] == "failed"
    ab_fc = next(fc for fc in calc_short_abut["failed_checks"] if fc["check_key"] == "abutment_checks.bored_pile")
    assert {"03", "16", "20"} <= set(ab_fc["related_drawings"])
    assert {"QL-03", "QL-15", "QL-18"} <= set(ab_fc["legacy_related_drawings"])

    # 4. Ingest real measured river cross-section + route profile + borehole survey data
    # Trapezoidal channel: z=84 at x=0 & x=100, flat bed z=78 on [10..90]; at flood_z=81.0:
    # water depth = 3.0m, water surface x in [5..95] (width=90m), bed=80m -> exact area = 0.5*(90+80)*3 = 255.0 m2!
    survey = {
        "river_cross_section": {
            "flood_elevation_m": 81.000,
            "roughness_n": 0.040,
            "slope_permille": 0.8,
            "stations": [
                {"x_m": 0.0, "z_m": 84.0},
                {"x_m": 10.0, "z_m": 78.0},
                {"x_m": 90.0, "z_m": 78.0},
                {"x_m": 100.0, "z_m": 84.0},
            ],
        },
        "route_profile": {"deck_elevation_m": 86.800},
        "pier_borehole": {
            "borehole_id": "ZK2",
            "confirmed_pile_length_m": 22.0,
            "layers": [
                {"name": "①冲积细砂", "top_elev_m": 78.0, "bot_elev_m": 73.0, "qik_kpa": 58.0, "fa0_kpa": 160.0},
                {"name": "②密实卵砾石", "top_elev_m": 73.0, "bot_elev_m": 50.0, "qik_kpa": 135.0, "fa0_kpa": 450.0},
            ],
        },
        "abutment_borehole": {
            "borehole_id": "ZK1",
            "confirmed_pile_length_m": 16.0,
            "layers": [
                {"name": "①粉质黏土", "top_elev_m": 83.5, "bot_elev_m": 76.5, "qik_kpa": 55.0, "fa0_kpa": 140.0},
                {"name": "②密实卵砾石", "top_elev_m": 76.5, "bot_elev_m": 55.0, "qik_kpa": 130.0, "fa0_kpa": 420.0},
            ],
        },
        "bearing_positioning": {"beam_length_m": 24.96, "bearing_center_offset_m": 0.48},
    }
    ing = ingest_bridge_survey_data(survey)
    assert math.isclose(ing["overrides"]["hydrology"]["area_m2"], 255.0, abs_tol=1e-3)
    assert math.isclose(ing["overrides"]["hydrology"]["channel_width_m"], 90.0, abs_tol=1e-3)

    calc_survey = run_full_bridge_calculation({"preset": "wenchang", "survey_data": survey})
    assert calc_survey["calculation_status"] == "passed"
    assert calc_survey["unresolved_assumptions"] == []
    assert calc_survey["assumption_dependent_conclusions"] == []
    assert calc_survey["project_summary"]["pier_pile_length_m"] == 22.0
    assert calc_survey["project_summary"]["abutment_pile_length_m"] == 16.0


def test_advisor_auditable_calc_book_hand_cross_check_and_full_rebar_provenance(tmp_path):
    calc = run_full_bridge_calculation({"preset": "wenchang"})

    # 1. Independent closed-form hand-calculation cross-check (all 6 core benchmark items pass!)
    cross = compute_independent_hand_cross_check(calc)
    assert cross["status"] == "passed"
    assert cross["all_passed"] is True
    item_ids = {it["item_id"] for it in cross["items"]}
    assert {
        "girder_mid_span_uls_moment",
        "girder_support_uls_shear",
        "intermediate_pier_two_span_reaction_sum",
        "pier_column_bottom_uls_axial",
        "pier_pile_top_sls_load",
        "abutment_pile_top_sls_load",
    } == item_ids

    # 2. Clicking ANY tendon or rebar ID across QL-08..QL-18 returns full 6-field provenance!
    all_ids = list(calc["rebar_and_tendon_ledger"]["index_by_id"].keys())
    assert len(all_ids) >= 27
    for item_id in all_ids:
        rec = lookup_bar_or_tendon(calc, item_id)
        qs = rec["quantity_scope"]
        sb = rec["selection_basis"]
        gf = rec["governing_forces"]
        lc = rec["load_case_and_combination"]
        prov = rec["parameter_provenance"]

        assert qs["counting_scope"]
        assert qs["count_per_member"] >= 1
        assert qs["total_count_bridge"] >= 1
        assert qs["bridge_total_weight_kg"] > 0.0
        assert sb["code_clause"]
        assert sb["material_spec"]
        assert sb["provided_area_mm2"] > 0.0
        assert sb["design_rule"]
        assert gf["control_section"]
        assert gf["demand_symbol"] and gf["capacity_symbol"]
        assert lc["limit_state"] and lc["combination_expression"]
        assert len(prov) >= 1 and all("status" in p and "source" in p for p in prov)

    # 3. Generate complete Markdown calculation book & verify thesis word-count note fix!
    book_md_path = tmp_path / "wenchang_calc_book.md"
    md_text = generate_bridge_calculation_book_markdown(calc, output_path=book_md_path)
    assert book_md_path.is_file()
    for required_heading in (
        "双状态总览",
        "第一章 工程概况、技术标准与桥型方案比选",
        "第二章 代表性一片预制T梁",
        "第三章 代表性中间双柱式桥墩",
        "第四章 桩柱式桥台",
        "第五章 手算解析公式与电算程序对算交叉校核表",
        "第六章 全套22张施工图预应力钢束与钢筋编号",
    ):
        assert required_heading in md_text
    assert "正文约一万字" in md_text
    assert "学校" in md_text and "指导教师" in md_text
    assert "不少于1.5万字" not in md_text
    assert "不少于1.5万字" not in json.dumps(calc, ensure_ascii=False)


def test_separated_calculation_vs_graduation_status_and_strict_seismic_audit():
    # 1. Default run: calculation_status == 'passed', graduation_readiness_status == 'pending_confirmation'
    c_def = run_full_bridge_calculation({"preset": "wenchang"})
    assert c_def["calculation_status"] == "passed"
    assert c_def["graduation_readiness_status"] == "pending_confirmation"
    assert len(c_def["assumption_dependent_conclusions"]) == 3

    # 2. Exact bug regression: seismic_checks: {"status": "pending"} MUST NOT become "completed"!
    c_seis_pending = run_full_bridge_calculation(
        {"preset": "wenchang", "seismic_checks": {"status": "pending"}}
    )
    seis_deliv = next(
        d for d in c_seis_pending["pending_graduation_deliverables"] if d["id"] == "seismic_e1_e2_verification"
    )
    assert seis_deliv["status"] == "pending"
    assert c_seis_pending["seismic_verification"]["verified"] is False

    # 3. Missing review_record/checked_by ALSO stays "pending"
    c_no_reviewer = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "seismic_checks": {
                "status": "passed",
                "e1": {"demand": 620.0, "capacity": 1450.0, "unit": "kN·m"},
                "e2": {"demand": 38.5, "capacity": 82.0, "unit": "mm"},
            },
        }
    )
    assert c_no_reviewer["seismic_verification"]["status"] == "pending"

    # 4. Failing E2 demand > capacity fails BOTH seismic_verification AND overall calculation_status!
    c_seis_fail = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "seismic_checks": {
                "status": "passed",
                "e1": {"demand": 620.0, "capacity": 1450.0, "unit": "kN·m"},
                "e2": {"demand": 125.0, "capacity": 82.0, "unit": "mm"},
                "checked_by": "王工复核",
            },
        }
    )
    assert c_seis_fail["seismic_verification"]["status"] == "failed"
    assert c_seis_fail["calculation_status"] == "failed"
    assert c_seis_fail["graduation_readiness_status"] == "failed"

    # 5. Full survey + thesis confirmed + valid reviewed E1/E2 -> graduation_readiness_status == 'ready'!
    c_ready = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "flood_elevation_m": 80.800,
            "deck_elevation_m": 86.500,
            "pile_length_m": 21.0,
            "abutment_pile_length_m": 15.0,
            "bearing_center_offset_m": 0.48,
            "hydrology": {
                "area_m2": 310.0,
                "wetted_perimeter_m": 62.0,
                "roughness_inv": 25.0,
                "slope_permille": 0.8,
                "channel_width_m": 96.0,
                "standard_span_m": 25.0,
            },
            "soil_layers": [
                {"name": "①粉质黏土", "thickness_m": 5.0, "qik_kpa": 55.0, "fa0_kpa": 140.0},
                {"name": "②密实砂砾石", "thickness_m": 20.0, "qik_kpa": 130.0, "fa0_kpa": 420.0},
            ],
            "deliverables_confirmed": {"thesis_document": True},
            "seismic_checks": {
                "status": "passed",
                "e1": {"demand": 620.0, "capacity": 1450.0, "unit": "kN·m"},
                "e2": {"demand": 38.5, "capacity": 82.0, "unit": "mm"},
                "checked_by": "桥梁教研室导师复核通过 (2026-11)",
            },
        }
    )
    assert c_ready["calculation_status"] == "passed"
    assert c_ready["graduation_readiness_status"] == "ready"
    assert c_ready["unresolved_assumptions"] == []
    assert c_ready["assumption_dependent_conclusions"] == []
    assert all(d["status"] == "completed" for d in c_ready["pending_graduation_deliverables"])


def test_ql09_true_stirrup_pitch_ql17_unit_scope_ql18_pedestal_cut_and_windows_cli(tmp_path):
    calc = run_full_bridge_calculation({"preset": "wenchang"})

    # 1. QL-09: Measure actual drawn vertical stirrup spacing on 1:80 paper (1.25mm = 100mm, 1.875mm = 150mm)
    ql09_dxf = generate_civil_bridge_dxf(
        "rebar_detail",
        {"preset": "wenchang", "component": "t_girder_web", "drawing_no": "QL-09", "_calc_report": calc},
        tmp_path / "ql09_dir",
        stem="QL-09_ok",
    )
    ver_09 = verify_civil_dxf(ql09_dxf, calc_report=calc)
    assert ver_09["status"] == "passed"

    doc_09 = ezdxf.readfile(ql09_dxf)
    stirrup_xs = sorted(
        float(e.dxf.start.x)
        for e in doc_09.modelspace()
        if e.dxftype() == "LINE"
        and e.dxf.get("layer") == "REBAR_STIRRUP"
        and float(e.dxf.start.x) > 220.0
        and abs(float(e.dxf.start.x) - float(e.dxf.end.x)) < 0.05
        and abs(float(e.dxf.end.y) - float(e.dxf.start.y)) > 12.0
    )
    assert len(stirrup_xs) == 90  # 21 (@100) + 69 (@150) == 90 half-span == 180/2!
    dense_paper_dxs = [stirrup_xs[i + 1] - stirrup_xs[i] for i in range(20)]
    std_paper_dxs = [stirrup_xs[i + 1] - stirrup_xs[i] for i in range(20, 89)]
    assert all(math.isclose(dx * 80.0, 100.0, abs_tol=0.2) for dx in dense_paper_dxs)
    assert all(math.isclose(dx * 80.0, 150.0, abs_tol=0.2) for dx in std_paper_dxs)

    # Delete every odd dense stirrup (turning @100 into @200) -> verifier MUST catch STIRRUP_DRAWN_PITCH_MISMATCH!
    doc_09_bad = ezdxf.readfile(ql09_dxf)
    dense_lines = sorted(
        (
            e for e in doc_09_bad.modelspace()
            if e.dxftype() == "LINE"
            and e.dxf.get("layer") == "REBAR_STIRRUP"
            and float(e.dxf.start.x) > 220.0
            and abs(float(e.dxf.start.x) - float(e.dxf.end.x)) < 0.05
            and abs(float(e.dxf.end.y) - float(e.dxf.start.y)) > 12.0
        ),
        key=lambda ent: float(ent.dxf.start.x),
    )[:21]
    for odd_ent in dense_lines[1::2]:
        doc_09_bad.modelspace().delete_entity(odd_ent)
    bad_09_path = tmp_path / "ql09_dir" / "QL-09_bad_200.dxf"
    doc_09_bad.saveas(bad_09_path)
    ver_09_bad = verify_civil_dxf(bad_09_path, calc_report=calc)
    assert ver_09_bad["status"] == "failed"
    codes_09 = {i["code"] for i in ver_09_bad["issues"]}
    assert "STIRRUP_DRAWN_PITCH_MISMATCH" in codes_09
    assert "STIRRUP_DRAWN_COUNT_MISMATCH" in codes_09

    # 2. QL-17: Explicit counting scope note & tamper rejection
    ql17_dxf = generate_civil_bridge_dxf(
        "rebar_detail",
        {"preset": "wenchang", "component": "pier_column_pile", "drawing_no": "QL-17", "_calc_report": calc},
        tmp_path / "ql17_dir",
        stem="QL-17_ok",
    )
    ver_17 = verify_civil_dxf(ql17_dxf, calc_report=calc)
    assert ver_17["status"] == "passed"
    doc_17 = ezdxf.readfile(ql17_dxf)
    txt_17 = " ".join(str(e.dxf.get("text", "")) for e in doc_17.modelspace() if e.dxftype() == "TEXT")
    assert "统计口径说明" in txt_17 and "单根墩柱" in txt_17 and "整座双柱墩" in txt_17

    for ent in doc_17.modelspace():
        if ent.dxftype() == "TEXT" and "单根墩柱" in str(ent.dxf.get("text", "")):
            ent.dxf.text = ent.dxf.text.replace("单根墩柱", "墩柱")
    bad_17_scope = tmp_path / "ql17_dir" / "QL-17_no_scope.dxf"
    doc_17.saveas(bad_17_scope)
    ver_17_bad = verify_civil_dxf(bad_17_scope, calc_report=calc)
    assert ver_17_bad["status"] == "failed"
    assert any(i["code"] == "MISSING_REBAR_UNIT_SCOPE_DECLARATION" for i in ver_17_bad["issues"])

    # 3. QL-18: Shift A-A cut in abutment_elev into gap between pedestals -> verifier MUST catch SECTION_CUT_PEDESTAL_MISALIGNED!
    ql18_dxf = generate_civil_bridge_dxf(
        "rebar_detail",
        {"preset": "wenchang", "component": "abutment_rebar", "drawing_no": "QL-18", "_calc_report": calc},
        tmp_path / "ql18_dir",
        stem="QL-18_ok",
    )
    ver_18 = verify_civil_dxf(ql18_dxf, calc_report=calc)
    assert ver_18["status"] == "passed"

    doc_18_bad = ezdxf.readfile(ql18_dxf)
    for ent in doc_18_bad.modelspace():
        if ent.dxftype() == "LINE" and ent.dxf.get("layer") in {"FRAME", "DIM"}:
            x0, x1 = float(ent.dxf.start.x), float(ent.dxf.end.x)
            y0, y1 = float(ent.dxf.start.y), float(ent.dxf.end.y)
            if 200.0 <= x0 <= 320.0 and abs(x0 - x1) < 0.05 and 2.0 <= abs(y1 - y0) <= 6.5 and 185.0 <= min(y0, y1) <= 270.0:
                ent.dxf.start = (x0 + 12.18, y0, 0.0)
                ent.dxf.end = (x1 + 12.18, y1, 0.0)
        elif ent.dxftype() == "TEXT" and str(ent.dxf.get("text", "")) == "A":
            ins = ent.get_placement()[1] or ent.dxf.insert
            if 200.0 <= float(ins.x) <= 325.0 and 185.0 <= float(ins.y) <= 270.0:
                ent.set_placement((float(ins.x) + 12.18, float(ins.y)), align=ent.get_placement()[0])
    bad_18_path = tmp_path / "ql18_dir" / "QL-18_shifted_cut.dxf"
    doc_18_bad.saveas(bad_18_path)
    ver_18_bad = verify_civil_dxf(bad_18_path, calc_report=calc)
    assert ver_18_bad["status"] == "failed"
    assert any(
        i["code"] == "SECTION_CUT_PEDESTAL_MISALIGNED" and i["view_id"] == "abutment_elev"
        for i in ver_18_bad["issues"]
    )

    # 4. Zero-PYTHONPATH / GBK-safe Windows one-command reproduction script + MCP lookup_bar/calc_book modes!
    repo_root = Path(__file__).resolve().parents[4]
    script_path = repo_root / "scripts" / "run_bridge_suite.py"
    assert script_path.is_file()

    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean_env.pop("PYTHONUTF8", None)
    clean_env["PYTHONIOENCODING"] = "gbk"  # Simulate Chinese Windows cp936/GBK console!

    out_suite_dir = tmp_path / "one_cmd_suite"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--out", str(out_suite_dir), "--json"],
        cwd=str(repo_root),
        env=clean_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    suite_json = json.loads(proc.stdout)
    assert suite_json["calculation_status"] == "passed"
    assert suite_json["graduation_readiness_status"] == "pending_confirmation"
    assert len(suite_json["sheets"]) == 22
    assert Path(suite_json["calculation_book_path"]).is_file()
    assert Path(suite_json["calculation_report_path"]).is_file()

    # Also verify MCP cad_civil_bridge_calc lookup_bar & calc_book modes
    app = create_mcp_http_app(tmp_path)
    client = TestClient(app)
    r_lookup = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "cad_civil_bridge_calc",
                "arguments": {"mode": "lookup_bar", "bar_id": "GJ1"},
            },
        },
    )
    assert r_lookup.status_code == 200
    sc_lookup = r_lookup.json()["result"]["structuredContent"]["result"]
    assert sc_lookup["bar_id"] == "GJ1"
    assert sc_lookup["governing_forces"]["demand_symbol"] == "Vd"
