"""Integration tests for v2.5 Civil/Bridge Optimization Plan & Wenchang Bridge 18-Sheet Suite."""

from __future__ import annotations

import math
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

    # Reopen DXF independently and check explicit 7000 / 8000 / 9000 & 4x1950+2x600=9000 dims
    doc = ezdxf.readfile(tmp_path / "wenchang_cross" / "QL-05_cross.dxf")
    dim_texts = [str(e.dxf.get("text", "")) for e in doc.modelspace() if e.dxftype() == "DIMENSION"]
    joined = " | ".join(dim_texts)
    for token in ("7000", "8000", "9000", "1950", "600", "400", "80", "100"):
        assert token in joined, f"missing expected dimension token {token} in {joined}"


def test_reactive_calc_span_and_sigma_con_propagation_plus_unresolved_items(tmp_path):
    # Default Wenchang lists unresolved site items (flood elevation, deck elevation, pile length)
    r_base = run_full_bridge_calculation({"preset": "wenchang"})
    unresolved_fields = {u["field"] for u in r_base["unresolved_assumptions"]}
    assert {"flood_elevation_m", "deck_elevation_m", "pile_length_m"} <= unresolved_fields

    # Supplying explicit survey values clears unresolved_assumptions
    r_confirmed = run_full_bridge_calculation(
        {
            "preset": "wenchang",
            "flood_elevation_m": 80.600,
            "deck_elevation_m": 86.200,
            "pile_length_m": 18.0,
            "calc_span_m": 24.16,
        }
    )
    assert r_confirmed["unresolved_assumptions"] == []

    # Changing sigma_con_mpa or calc_span_m updates both calculation report and tendon_layout DXF
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


def test_reopen_dxf_verifier_catches_1_to_400_scale_fake_slope_and_stale_prestress(tmp_path):
    # 1. At 1:400, 1.0m elevation difference MUST measure strictly 2.500mm on paper
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

    # Verify raw paper dy is 2.5mm, then corrupt water triangle by -5.5mm (simulating old max(8.0, ...) bug)
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

    # 2. Flatten a 2% sloped cross-section deck to 0% -> verifier MUST catch DECK_CROSS_SLOPE_MISMATCH
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

    # 3. Alter effective prestress text on tendon sheet -> verifier MUST catch TENDON_EFFECTIVE_PRESTRESS_MISMATCH
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


def test_full_18_sheet_a3_suite_9_categories_and_bar_tendon_traceability(tmp_path):
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
    for sample_id in ("N1", "N4", "R1", "GJ1", "W1", "HG1", "HL1", "GL1", "DZ1", "ZJ1", "QT1"):
        traced = lookup_bar_or_tendon(calc, sample_id)
        assert traced["calc_basis"]
        assert traced.get("total_weight_bridge_kg", traced.get("bridge_total_weight_kg", 0.0)) > 0


def test_civil_preference_learning_scope_and_correction_regression_flywheel(tmp_path):
    mem_file = tmp_path / ".cad-memory" / "project-preferences.json"

    # 1. Create a preferred civil sample DXF (font=gbenor.shx, OUTLINE color=2) and confirm it
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

    # 2. Feed an unconfirmed sample DXF with a DIFFERENT style -> must NOT overwrite confirmed project habits!
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

    # 3. Generate a bridge cross-section with NEW dimensions (deck=10200, spacing=2250) -> reuses style, adopts new dims!
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

    # 4. Record a faulty flat-slope DXF correction into the regression vault and replay it automatically
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
