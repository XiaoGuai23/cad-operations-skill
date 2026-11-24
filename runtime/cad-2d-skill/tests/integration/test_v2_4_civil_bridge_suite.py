"""Integration tests for v2.4.0 Civil & Highway Bridge Calculation, QA & CAD Suite."""

from __future__ import annotations

import json
import math
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
import pytest

from cad_skill_core.civil import (
    CIVIL_DRAWING_TYPES,
    audit_bridge_design_consistency,
    compute_hydrology_and_span,
    compute_lateral_distribution,
    compute_polygon_section_properties,
    compute_prestress_estimation_and_loss,
    compute_superstructure_envelopes,
    compute_t_girder_section,
    draw_axis_bubble,
    generate_civil_bridge_bundle,
    ingest_and_audit_calc_table,
    run_full_bridge_calculation,
    solve_continuous_cap_beam_1d,
)
from cad_skill_core.cli import main as cli_main
from cad_skill_core.mcp.http_app import create_mcp_http_app


def test_wuqu_bridge_hydrology_section_lateral_and_cap_beam_equilibrium():
    # 1. Hydrology benchmark (Wuqu River: A=898.525 m2, chi=127.86 m, 1/n=22.22, i=0.5 permille, Bc=103.08 m)
    hydro = compute_hydrology_and_span(
        area_m2=898.525,
        wetted_perimeter_m=127.86,
        roughness_inv=22.22,
        slope_permille=0.5,
        channel_width_m=103.08,
        stability_k=0.90,
        stability_exp_n=0.84,
        standard_span_m=25.0,
    )
    assert math.isclose(hydro["hydraulic_radius_m"], 7.0274, abs_tol=0.01)
    assert math.isclose(hydro["net_opening_length_m"], 92.772, abs_tol=0.02)
    assert hydro["recommended_spans"] == 4
    assert hydro["recommended_layout"] == "4x25m"
    assert hydro["total_bridge_length_m"] == 100.0

    # 2. Exact polygon Green's theorem check on a known 600x1700 rectangle
    rect = compute_polygon_section_properties([(0, 0), (600, 0), (600, 1700), (0, 1700)])
    assert math.isclose(rect["area"], 600.0 * 1700.0, rel_tol=1e-9)
    assert math.isclose(rect["ix"], 600.0 * (1700.0**3) / 12.0, rel_tol=1e-9)
    assert math.isclose(rect["efficiency_rho"], 1.0 / 3.0, abs_tol=1e-4)

    # 3. T-girder effective compression flange width b'_f & efficiency rho >= 0.5
    t_sec = compute_t_girder_section(
        calc_span_mm=24500.0,
        girder_spacing_mm=2348.0,
        flange_width_mm=2348.0,
        height_mm=1700.0,
    )
    assert t_sec["effective_flange_width_mm"] == 2348.0
    assert t_sec["effective_flange_candidates_mm"]["b_plus_2bh_plus_12hf"] == 3440.0
    assert t_sec["mid_span_mm"]["passes_efficiency"] is True
    assert t_sec["mid_span_mm"]["efficiency_rho"] >= 0.50

    # 4. Courbon Eccentric Compression ordinates (sum(a_i^2)=55.131 m2, eta_11=0.60, eta_15=-0.20, m_cq3=0.40)
    lat = compute_lateral_distribution(
        n_girders=5,
        girder_spacing_m=2.348,
        deck_width_m=11.75,
        calc_span_m=24.5,
    )
    assert math.isclose(lat["sum_a2_m2"], 55.131, abs_tol=0.01)
    assert lat["eccentric_compression_applicable"] is True
    ec = lat["eccentric_compression"]
    assert math.isclose(ec[0]["eta_g1"], 0.60, abs_tol=1e-3)
    assert math.isclose(ec[0]["eta_gn"], -0.20, abs_tol=1e-3)
    assert math.isclose(ec[1]["eta_g1"], 0.40, abs_tol=1e-3)
    assert math.isclose(ec[1]["eta_gn"], 0.00, abs_tol=1e-3)
    assert math.isclose(ec[2]["m_cq"], 0.40, abs_tol=1e-3)

    # 5. Superstructure 10-element envelopes & Prestress estimation (SLS=27.42, ULS=28.02 on thesis inputs)
    ps_thesis = compute_prestress_estimation_and_loss(
        m_sls_char_knm=4549.0,
        m_uls_knm=6399.525,
        girder_height_m=1.70,
        y_bot_m=1.131,
        kern_upper_ks_m=0.2685,
        centroid_cover_ap_m=0.15,
        strands_per_tendon=6,
    )
    assert math.isclose(ps_thesis["required_strands_sls"], 27.42, abs_tol=0.05)
    assert math.isclose(ps_thesis["required_strands_uls"], 28.08, abs_tol=0.02)
    assert ps_thesis["recommended_tendons"] == 5
    # Symmetric friction loss profile
    losses = ps_thesis["loss_profile"]
    assert math.isclose(losses[0]["effective_prestress_mpa"], losses[-1]["effective_prestress_mpa"], abs_tol=1e-2)

    env = compute_superstructure_envelopes(calc_span_m=24.0)
    assert len(env["stations"]) == 11
    assert env["stations"][5]["station"] == "KZ"
    assert env["stations"][5]["m_uls_knm"] > env["stations"][5]["m_sls_char_knm"] > 0

    # 6. Pure-Python 1D continuous overhanging cap-beam stiffness solver vertical force equilibrium
    g_loads = (1091.64, 977.89, 849.48, 977.89, 1091.64)
    cap = solve_continuous_cap_beam_1d(
        cap_length_m=10.95,
        cap_width_m=1.70,
        cap_height_m=1.50,
        column_spacing_m=6.65,
        girder_reactions_kn=g_loads,
    )
    total_react = sum(r["reaction_kn"] for r in cap["support_reactions"])
    expected_self_wt = 1.2 * 1.70 * 1.50 * 25.0 * 10.95
    assert math.isclose(total_react, sum(g_loads) + expected_self_wt, abs_tol=0.05)
    assert cap["max_hogging_moment_knm"] < -1000.0


def test_consistency_auditor_catches_all_three_real_thesis_errors():
    # Catch 1) 11.5m vs 11.75m / 24.5m deck width mismatch & 2) 110cm/100cm pavement 10x unit typo
    bad_spec = {
        "n_carriageways": 2,
        "single_deck_width_m": 11.5,
        "roadway_width_m": 10.75,
        "barrier_width_m": 0.50,
        "median_width_m": 1.0,
        "total_deck_width_m": 24.5,
        "n_girders": 5,
        "girder_spacing_mm": 2348.0,
        "pier_bearing_spacing_mm": 2300.0,
        "pavement_layers": [
            {"name": "沥青混凝土铺装", "thickness": 110, "unit": "cm"},
            {"name": "C40混凝土铺装", "thickness": 100, "unit": "cm"},
        ],
    }
    audit = audit_bridge_design_consistency(bad_spec)
    assert audit["status"] == "failed"
    codes = {i["code"] for i in audit["issues"]}
    assert "DECK_SINGLE_WIDTH_MISMATCH" in codes
    assert "DECK_TOTAL_WIDTH_MISMATCH" in codes
    assert "PIER_BEARING_SPACING_MISMATCH" in codes
    assert "PAVEMENT_THICKNESS_UNIT_TYPO" in codes

    # Catch 3) Table 12-5 crack width 0.314mm > 0.200mm falsely marked '是'
    crack_md = """
    | 截面 | 顶缘裂缝(mm) | 容许值(mm) | 是否满足 | 底缘裂缝(mm) | 容许值(mm) | 是否满足 |
    | --- | --- | --- | --- | --- | --- | --- |
    | B1 | 0.117 | 0.200 | 是 | 0.000 | 0.200 | 是 |
    | J1 | 0.314 | 0.200 | 是 | 0.000 | 0.200 | 是 |
    | J2 | 0.000 | 0.200 | 是 | 0.032 | 0.200 | 是 |
    """
    tbl_res = ingest_and_audit_calc_table(table_text=crack_md)
    assert tbl_res["parsed_check_count"] == 6
    assert tbl_res["audit"]["status"] == "failed"
    assert tbl_res["audit"]["error_count"] == 1
    assert "0.314" in tbl_res["audit"]["issues"][0]["message"]
    assert "误判" in tbl_res["audit"]["issues"][0]["message"]


def test_all_five_civil_bridge_drawings_score_s_grade_and_enforce_gb_axes(tmp_path):
    for dtype in CIVIL_DRAWING_TYPES:
        out_dir = tmp_path / dtype
        bundle = generate_civil_bridge_bundle(dtype, {}, out_dir)
        assert bundle["status"] == "passed"
        assert bundle["audit_summary"]["errors"] == 0
        assert bundle["rubric_score"]["total_score"] >= 95.0
        assert bundle["rubric_score"]["grade"] == "S"
        dxf_file = out_dir / f"{dtype}.dxf"
        png_file = out_dir / f"{dtype}.png"
        assert dxf_file.is_file() and dxf_file.stat().st_size > 2000
        assert png_file.is_file() and png_file.stat().st_size > 5000

    # Also test pile-bent abutment variant ('桥台一般构造图')
    abut_bundle = generate_civil_bridge_bundle(
        "pier_and_abutment",
        {"variant": "abutment"},
        tmp_path / "abutment_sheet",
        stem="bridge_abutment",
    )
    assert abut_bundle["status"] == "passed"
    assert abut_bundle["rubric_score"]["grade"] == "S"

    # GB/T 50001 forbidden axis letters I, O, Z must be rejected
    doc = ezdxf.new("R2010")
    for bad_axis in ("I", "O", "Z"):
        with pytest.raises(ValueError, match="forbidden"):
            draw_axis_bubble(doc.modelspace(), 10.0, 10.0, bad_axis)


def test_cli_mcp_and_http_civil_bridge_endpoints(tmp_path):
    # 1. CLI bridge-calc & generate-civil
    calc_json = tmp_path / "full_calc.json"
    rc1 = cli_main(["bridge-calc", "--mode", "full", "--out", str(calc_json), "--json"])
    assert rc1 == 0
    saved = json.loads(calc_json.read_text(encoding="utf-8"))
    assert saved["result"]["consistency_audit"]["status"] == "passed"

    cli_sheet_dir = tmp_path / "cli_elev"
    rc2 = cli_main(["generate-civil", "bridge_elevation", "--out", str(cli_sheet_dir), "--json"])
    assert rc2 == 0
    assert (cli_sheet_dir / "bridge_elevation.dxf").is_file()

    # 2. FastAPI HTTP & MCP endpoints
    app = create_mcp_http_app(tmp_path / "mcp_ws")
    client = TestClient(app)
    try:
        r_calc = client.post(
            "/api/v1/civil/calc",
            json={"mode": "lateral_distribution", "params": {"n_girders": 5, "girder_spacing_m": 2.348}},
        )
        assert r_calc.status_code == 200
        assert r_calc.json()["result"]["n_girders"] == 5

        r_gen = client.post(
            "/api/v1/civil/generate",
            json={"drawing_type": "t_girder_section", "spec": {"n_girders": 5}, "stem": "wuqu_girder"},
        )
        assert r_gen.status_code == 200
        body = r_gen.json()
        assert body["status"] == "passed"
        assert body["domain"] == "civil_bridge"
        dl_url = next(a["download_url"] for a in body["artifacts"] if a["name"] == "wuqu_girder.dxf")
        dl = client.get(dl_url)
        assert dl.status_code == 200
        assert len(dl.content) > 2000

        # Unified /api/v1/tasks/generate domain='civil_bridge'
        r_uni = client.post(
            "/api/v1/tasks/generate",
            json={"domain": "civil_bridge", "drawing_type": "mechanics_diagrams"},
        )
        assert r_uni.status_code == 200
        assert r_uni.json()["status"] == "passed"
    finally:
        app.state.job_manager.shutdown()
