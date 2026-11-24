"""Regression tests for all 8 P1/P2 review findings in v2.4.1."""

from __future__ import annotations

import math

import ezdxf
import pytest

from cad_skill_core.civil import (
    compute_hydrology_and_span,
    generate_civil_bridge_dxf,
    ingest_and_audit_calc_table,
    run_full_bridge_calculation,
)
from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.revision.post_verify import LinearSpanEqualsCheck, verify_revision_post_conditions
from cad_skill_core.standards.preference_memory import (
    apply_project_preferences_to_spec,
    load_preference_store,
    record_preference_observation,
    teach_preferences_from_text,
)


def test_p1_calc_table_parses_scientific_notation_and_rejects_missing_na_values():
    # 1. Scientific notation 1.0E+03 (1000.0) > limit 200 must FAIL
    sci_md = """
    | 截面 | 计算值 | 容许值 |
    | --- | --- | --- |
    | KZ | 1.0E+03 | 200 |
    """
    r_sci = ingest_and_audit_calc_table(table_text=sci_md)
    assert r_sci["parsed_check_count"] == 1
    assert math.isclose(r_sci["parsed_checks"][0]["value"], 1000.0)
    assert r_sci["audit"]["status"] == "failed"
    assert any(i["code"] == "LIMIT_STATE_EXCEEDED" for i in r_sci["audit"]["issues"])

    # 2. '#N/A' or missing calculation value must NOT pass with 0 checks
    na_md = """
    | 截面 | 计算值 | 容许值 | 是否满足 |
    | --- | --- | --- | --- |
    | KZ | #N/A | 200 | 是 |
    """
    r_na = ingest_and_audit_calc_table(table_text=na_md)
    assert r_na["parsed_check_count"] == 0
    assert r_na["unverified_count"] == 1
    assert r_na["audit"]["status"] == "failed"
    assert any(i["code"] == "UNVERIFIED_TABLE_VALUE" for i in r_na["audit"]["issues"])


def test_p1_full_bridge_calc_propagates_girder_count_to_cap_beam_and_consistency():
    res_4 = run_full_bridge_calculation({"lateral": {"n_girders": 4}})
    assert len(res_4["pier_cap_beam_1d"]["girder_positions_m"]) == 4
    assert res_4["consistency_audit"]["status"] == "passed"

    res_6 = run_full_bridge_calculation({"lateral": {"n_girders": 6}})
    assert len(res_6["pier_cap_beam_1d"]["girder_positions_m"]) == 6
    assert res_6["consistency_audit"]["status"] == "passed"

    # Explicit mismatched cap_beam reactions count must fail consistency_audit
    res_mismatch = run_full_bridge_calculation(
        {
            "lateral": {"n_girders": 4},
            "cap_beam": {"girder_reactions_kn": [900.0, 900.0, 900.0, 900.0, 900.0], "cap_length_m": 11.5},
        }
    )
    assert res_mismatch["consistency_audit"]["status"] == "failed"
    assert any(i["code"] == "PEDESTAL_COUNT_MISMATCH" for i in res_mismatch["consistency_audit"]["issues"])


def test_p1_tendon_layout_honors_exact_tendon_count_or_raises(tmp_path):
    # 6 tendons -> 6 mid-span circles + 6 end circles = 12 circles, '6束' text, N6 tag
    dxf_6 = generate_civil_bridge_dxf("tendon_layout", {"n_tendons": 6}, tmp_path / "t6")
    doc_6 = ezdxf.readfile(dxf_6)
    msp_6 = list(doc_6.modelspace())
    texts_6 = [str(e.dxf.text) for e in msp_6 if e.dxftype() == "TEXT"]
    tendon_circles_6 = [e for e in msp_6 if e.dxftype() == "CIRCLE" and e.dxf.layer == "TENDON"]
    assert any("6束" in t for t in texts_6)
    assert "N6" in texts_6
    assert len(tendon_circles_6) == 12

    # 1 tendon -> 1 mid-span circle + 1 end circle = 2 circles, '1束' text, no N2
    dxf_1 = generate_civil_bridge_dxf("tendon_layout", {"n_tendons": 1}, tmp_path / "t1")
    doc_1 = ezdxf.readfile(dxf_1)
    msp_1 = list(doc_1.modelspace())
    texts_1 = [str(e.dxf.text) for e in msp_1 if e.dxftype() == "TEXT"]
    tendon_circles_1 = [e for e in msp_1 if e.dxftype() == "CIRCLE" and e.dxf.layer == "TENDON"]
    assert any("1束" in t for t in texts_1)
    assert "N1" in texts_1
    assert "N2" not in texts_1
    assert len(tendon_circles_1) == 2

    # Out-of-range tendon counts must explicitly raise ValueError rather than silently clamping
    for invalid_n in (0, -2, 12):
        with pytest.raises(ValueError, match="n_tendons"):
            generate_civil_bridge_dxf("tendon_layout", {"n_tendons": invalid_n}, tmp_path / "bad")


def test_p1_bridge_elevation_geometry_matches_title_block_scale_exactly(tmp_path):
    # Default: 4*2500 + 2*400 = 10800 cm = 108,000 mm -> at 1:400 must be exact 270.000 mm on paper
    dxf_default = generate_civil_bridge_dxf("bridge_elevation", {}, tmp_path / "elev_400")
    doc = ezdxf.readfile(dxf_default)
    msp = list(doc.modelspace())
    texts = [str(e.dxf.text) for e in msp if e.dxftype() == "TEXT"]
    assert any("比例 1:400" in t for t in texts)

    # Barrier rectangle spans the full bridge [x_abut_l, x_abut_r]
    thin_rects = [
        e for e in msp
        if e.dxftype() == "LWPOLYLINE" and e.closed and e.dxf.layer == "THIN"
    ]
    # Outer sheet border is 420 mm wide; barrier rectangle is the bridge superstructure envelope
    widths = sorted(
        max(pt[0] for pt in e.get_points("xy")) - min(pt[0] for pt in e.get_points("xy"))
        for e in thin_rects
    )
    assert any(math.isclose(w, 108000.0 / 400.0, abs_tol=1e-4) for w in widths)

    # Explicit 1:500 scale -> exact 108,000 / 500 = 216.000 mm
    dxf_500 = generate_civil_bridge_dxf("bridge_elevation", {"scale": "1:500"}, tmp_path / "elev_500")
    doc_500 = ezdxf.readfile(dxf_500)
    msp_500 = list(doc_500.modelspace())
    texts_500 = [str(e.dxf.text) for e in msp_500 if e.dxftype() == "TEXT"]
    assert any("比例 1:500" in t for t in texts_500)
    widths_500 = [
        max(pt[0] for pt in e.get_points("xy")) - min(pt[0] for pt in e.get_points("xy"))
        for e in msp_500
        if e.dxftype() == "LWPOLYLINE" and e.closed and e.dxf.layer == "THIN"
    ]
    assert any(math.isclose(w, 108000.0 / 500.0, abs_tol=1e-4) for w in widths_500)


def test_p1_and_p2_preference_memory_negation_and_conflicting_sample_isolation(tmp_path):
    mem = tmp_path / "prefs.json"

    # P1a: Negated roughness instruction must NOT save Ra 6.3 as confirmed/candidate
    recorded_neg_ra = teach_preferences_from_text(mem, "法兰图以后不要把未注粗糙度设为 Ra 6.3")
    assert len(recorded_neg_ra) == 1
    assert recorded_neg_ra[0].status == "rejected"

    brief = tmp_path / "flange.md"
    brief.write_text("# 法兰\n外径 120mm，内径 60mm，厚度 20mm，6个 Φ12 通孔均布在 Φ90 节圆上。", encoding="utf-8")
    base_spec = compile_spec_from_files([brief], part_family="flange")
    updated_spec, applied, _ = apply_project_preferences_to_spec(base_spec, mem, family="flange")
    assert applied == []
    gen_ras = [sf.value for sf in updated_spec.surface_finishes if sf.is_general]
    assert "Ra 6.3" not in gen_ras

    # P1b: Negated leader instruction ("不要把引线放在右侧") must NOT confirm right side
    mem2 = tmp_path / "prefs_leader.json"
    neg_leader = teach_preferences_from_text(mem2, "不要把引线放在右侧")
    assert len(neg_leader) == 1
    assert neg_leader[0].value == "right"
    assert neg_leader[0].status == "rejected"

    # Contrastive sentence: reject right + confirm left, plus technical note containing '不得' inside note text
    both = teach_preferences_from_text(mem2, "不要把引线放在右侧，以后统一放在左侧；默认技术要求：表面不得有毛刺，锐边倒钝")
    statuses = {(e.key, str(e.value)): e.status for e in load_preference_store(mem2).entries}
    assert statuses[("leader_side", "right")] == "rejected"
    assert statuses[("leader_side", "left")] == "confirmed"
    assert statuses[("default_technical_notes", "['表面不得有毛刺，锐边倒钝']")] == "confirmed"

    # P2: Recording a conflicting passive sample must keep confirmed preference intact and store conflict as candidate
    mem3 = tmp_path / "prefs_conflict.json"
    record_preference_observation(
        mem3,
        part_family="*",
        key="leader_side",
        value="right",
        source_drawing="approved-flange-01.dxf",
        confirm=True,
    )
    record_preference_observation(
        mem3,
        part_family="*",
        key="leader_side",
        value="left",
        source_drawing="odd-sample-left.dxf",
        confirm=False,
        auto_confirm_threshold=2,
    )
    store3 = load_preference_store(mem3)
    right_e = next(e for e in store3.entries if e.key == "leader_side" and e.value == "right")
    left_e = next(e for e in store3.entries if e.key == "leader_side" and e.value == "left")
    assert right_e.status == "confirmed"
    assert right_e.evidence[0].source_drawing == "approved-flange-01.dxf"
    assert left_e.status == "candidate"

    spec_after, applied3, cands3 = apply_project_preferences_to_spec(base_spec, mem3, family="flange")
    assert spec_after.geometry.parameters.get("preferred_leader_side") == "right"
    assert any(a["value"] == "right" for a in applied3)
    assert any(c["value"] == "left" for c in cands3)


def test_p2_hydrology_reports_actual_adopted_design_discharge():
    h500 = compute_hydrology_and_span(
        area_m2=120.0,
        wetted_perimeter_m=40.0,
        design_discharge_m3_s=500.0,
        channel_width_m=80.0,
    )
    h1000 = compute_hydrology_and_span(
        area_m2=120.0,
        wetted_perimeter_m=40.0,
        design_discharge_m3_s=1000.0,
        channel_width_m=80.0,
    )
    assert h500["design_discharge_m3_s"] == 500.0
    assert h1000["design_discharge_m3_s"] == 1000.0
    assert h1000["net_opening_length_m"] > h500["net_opening_length_m"]


def test_p2_post_verify_linear_span_checks_requested_axis_on_line_entities(tmp_path):
    dxf_path = tmp_path / "vert_line.dxf"
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    line_ent = doc.modelspace().add_line((0.0, 0.0), (0.0, 10.0))
    h = line_ent.dxf.handle
    doc.saveas(dxf_path)

    # Vertical 10 mm line has horizontal span 0 mm -> horizontal=10 mm check MUST fail
    rep_horiz = verify_revision_post_conditions(
        dxf_path,
        dxf_path,
        [LinearSpanEqualsCheck(handles=[h], length=10.0, axis="horizontal")],
    )
    assert rep_horiz["passed"] is False
    assert "0.000" in rep_horiz["items"][0]["detail"]

    # Vertical=10 mm check MUST pass
    rep_vert = verify_revision_post_conditions(
        dxf_path,
        dxf_path,
        [LinearSpanEqualsCheck(handles=[h], length=10.0, axis="vertical")],
    )
    assert rep_vert["passed"] is True
