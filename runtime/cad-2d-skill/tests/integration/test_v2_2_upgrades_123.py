"""End-to-end integration tests for v2.2 Upgrades 1, 2, and 3:
1. Vector Entity Metadata, Hover/Click Studio Hooks, Red/Green Vector Diff & Undo/Redo;
2. Structured Revision IR (`cad-revision-ir/v2`) + LLM/Offline Compiler + 5 Geometric Primitives
   (`add_circle_hole`, `add_bolt_circle`, `chamfer_corner`, `fillet_corner`, `mirror_entities`);
3. Standalone Legacy DXF Health/Tampering Auditor & Deterministic Cleaner (`legacy_audit.py`).
"""

from __future__ import annotations

import base64
import json
from math import hypot, isclose, pi, sqrt, tan
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from cad_skill_core.inspection import audit_legacy_dxf, clean_legacy_dxf
from cad_skill_core.mcp import create_mcp_http_app
from cad_skill_core.mcp.cad_scene import compute_dxf_vector_diff, extract_dxf_vector_scene
from cad_skill_core.mcp.studio_ui import render_studio_html
from cad_skill_core.revision.common import compare_untouched, snapshot_modelspace
from cad_skill_core.revision.ir_engine import (
    StructuredRevisionIR,
    compile_nl_to_feature_ir,
    execute_revision_ir,
)
from cad_skill_core.revision.nl_revise import execute_nl_region_revision


def test_upgrade1_vector_scene_entities_meta_red_green_diff_and_studio_hooks(tmp_path: Path):
    before_dxf = tmp_path / "v0.dxf"
    after_dxf = tmp_path / "v1.dxf"

    doc0 = ezdxf.new("R2018")
    doc0.units = 4
    msp0 = doc0.modelspace()
    plate = msp0.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True, dxfattribs={"layer": "OUTLINE"})
    hole = msp0.add_circle((30, 30), 6.0, dxfattribs={"layer": "OUTLINE"})
    drop_ln = msp0.add_line((70, 10), (90, 10), dxfattribs={"layer": "HIDDEN"})
    doc0.saveas(before_dxf)

    scene0 = extract_dxf_vector_scene(before_dxf)
    assert scene0["entity_count"] == 3
    h_hole = str(hole.dxf.handle).upper()
    h_plate = str(plate.dxf.handle).upper()
    h_drop = str(drop_ln.dxf.handle).upper()
    assert "⌀12.00" in scene0["entities_meta"][h_hole]["desc"]
    assert scene0["sha256"]

    # Create v1: modify hole radius 6 -> 9, delete drop_ln, add new hole at (75, 30)
    doc1 = ezdxf.readfile(before_dxf)
    msp1 = doc1.modelspace()
    for ent in list(msp1):
        eh = str(ent.dxf.handle).upper()
        if eh == h_hole:
            ent.dxf.radius = 9.0
        elif eh == h_drop:
            msp1.delete_entity(ent)
    added_cir = msp1.add_circle((75, 30), 5.0, dxfattribs={"layer": "OUTLINE"})
    h_added = str(added_cir.dxf.handle).upper()
    doc1.saveas(after_dxf)

    vdiff = compute_dxf_vector_diff(before_dxf, after_dxf)
    assert vdiff["modified_handles"] == [h_hole]
    assert vdiff["deleted_handles"] == [h_drop]
    assert vdiff["added_handles"] == [h_added]
    assert vdiff["summary"] == {
        "added": 1,
        "deleted": 1,
        "modified": 1,
        "untouched": 1,
        "total_changed": 3,
    }
    ghost_handles = {gp["h"] for gp in vdiff["ghost_before_primitives"]}
    assert ghost_handles == {h_hole, h_drop}
    assert h_plate not in ghost_handles

    # Studio 2.2 interactive vector hooks
    html = render_studio_html()
    for hook in (
        "hitTestPoint2D",
        "btnVectorDiff2D",
        "btnUndo2D",
        "btnRedo2D",
        "btnLegacyAudit2D",
        "btnLegacyClean2D",
        "pushVersionNode2D",
        "applyVersionNode2D",
    ):
        assert hook in html, f"missing Studio 2.2 hook: {hook}"


def test_upgrade2_add_circle_hole_and_bolt_circle_via_nl_and_ir(tmp_path: Path):
    base_dxf = tmp_path / "plate.dxf"
    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    plate = msp.add_lwpolyline([(0, 0), (160, 0), (160, 120), (0, 120)], close=True, dxfattribs={"layer": "OUTLINE"})
    flange_disk = msp.add_circle((80, 60), 55.0, dxfattribs={"layer": "OUTLINE"})
    doc.saveas(base_dxf)

    before_snap = snapshot_modelspace(ezdxf.readfile(base_dxf))

    # 1. Add single Ø12 hole in an empty region of the plate via NL
    res_hole = execute_nl_region_revision(
        base_dxf,
        tmp_path / "add_hole_out",
        "新增直径 12 mm 的圆孔",
        region_box=(20.0, 20.0, 40.0, 40.0),
    )
    assert res_hole["action"] == "add_circle_hole"
    assert len(res_hole["added_handles"]) == 3  # 1 CIRCLE + 2 CENTER lines
    doc_after_hole = ezdxf.readfile(res_hole["output_path"])
    new_circles = [
        e for e in doc_after_hole.modelspace().query("CIRCLE") if str(e.dxf.handle).upper() != str(flange_disk.dxf.handle).upper()
    ]
    assert len(new_circles) == 1
    assert isclose(float(new_circles[0].dxf.center.x), 30.0, abs_tol=1e-5)
    assert isclose(float(new_circles[0].dxf.center.y), 30.0, abs_tol=1e-5)
    assert isclose(float(new_circles[0].dxf.radius), 6.0, abs_tol=1e-5)
    assert compare_untouched(before_snap, snapshot_modelspace(doc_after_hole), set(res_hole["added_handles"]), 1e-6) == []

    # 2. Add 6×Ø10 bolt-circle pattern on PCD 80 concentric with flange_disk
    res_bcd = execute_nl_region_revision(
        base_dxf,
        tmp_path / "add_bcd_out",
        "新增 6×Ø10 均布螺栓孔，节圆 80",
        candidate_handles=[str(flange_disk.dxf.handle)],
    )
    assert res_bcd["action"] == "add_bolt_circle"
    doc_bcd = ezdxf.readfile(res_bcd["output_path"])
    outline_holes = [
        e
        for e in doc_bcd.modelspace().query('CIRCLE[layer=="OUTLINE"]')
        if isclose(float(e.dxf.radius), 5.0, abs_tol=1e-5)
    ]
    assert len(outline_holes) == 6
    for hc in outline_holes:
        dist_from_center = hypot(float(hc.dxf.center.x) - 80.0, float(hc.dxf.center.y) - 60.0)
        assert isclose(dist_from_center, 40.0, abs_tol=1e-4)
    # Pitch circle on CENTER layer
    center_circles = list(doc_bcd.modelspace().query('CIRCLE[layer=="CENTER"]'))
    assert len(center_circles) == 1 and isclose(float(center_circles[0].dxf.radius), 40.0, abs_tol=1e-4)
    assert str(plate.dxf.handle).upper() not in set(res_bcd["modified_handles"])


def test_upgrade2_chamfer_and_fillet_on_two_lines_and_lwpolyline(tmp_path: Path):
    # A. Two intersecting orthogonal LINEs meeting at (0, 0)
    lines_dxf = tmp_path / "two_lines.dxf"
    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    l_vert = msp.add_line((0.0, 50.0), (0.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    l_horiz = msp.add_line((0.0, 0.0), (60.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    keep_circle = msp.add_circle((40.0, 35.0), 8.0, dxfattribs={"layer": "OUTLINE"})
    doc.saveas(lines_dxf)

    # A1. Chamfer C5
    res_cham = execute_nl_region_revision(
        lines_dxf,
        tmp_path / "chamfer_lines",
        "倒角 C5",
        candidate_handles=[str(l_vert.dxf.handle), str(l_horiz.dxf.handle)],
    )
    assert res_cham["action"] == "chamfer_corner"
    assert len(res_cham["modified_handles"]) == 2
    assert len(res_cham["added_handles"]) == 1
    cham_doc = ezdxf.readfile(res_cham["output_path"])
    cham_by_h = {str(e.dxf.handle).upper(): e for e in cham_doc.modelspace()}
    bevel_ln = cham_by_h[res_cham["added_handles"][0]]
    bevel_len = hypot(
        float(bevel_ln.dxf.end.x) - float(bevel_ln.dxf.start.x),
        float(bevel_ln.dxf.end.y) - float(bevel_ln.dxf.start.y),
    )
    assert isclose(bevel_len, 5.0 * sqrt(2.0), abs_tol=1e-4)
    assert isclose(float(cham_by_h[str(keep_circle.dxf.handle).upper()].dxf.radius), 8.0, abs_tol=1e-6)

    # A2. Fillet R8
    res_fil = execute_nl_region_revision(
        lines_dxf,
        tmp_path / "fillet_lines",
        "倒圆角 R8",
        candidate_handles=[str(l_vert.dxf.handle), str(l_horiz.dxf.handle)],
    )
    assert res_fil["action"] == "fillet_corner"
    fil_doc = ezdxf.readfile(res_fil["output_path"])
    arcs = list(fil_doc.modelspace().query("ARC"))
    assert len(arcs) == 1
    assert isclose(float(arcs[0].dxf.radius), 8.0, abs_tol=1e-5)
    assert isclose(float(arcs[0].dxf.center.x), 8.0, abs_tol=1e-5)
    assert isclose(float(arcs[0].dxf.center.y), 8.0, abs_tol=1e-5)
    assert isclose(float(arcs[0].dxf.start_angle), 180.0, abs_tol=1e-4)
    assert isclose(float(arcs[0].dxf.end_angle), 270.0, abs_tol=1e-4)

    # B. Closed LWPOLYLINE rectangle: single corner chamfer & 4-corner R5 fillet
    poly_dxf = tmp_path / "rect_poly.dxf"
    pdoc = ezdxf.new("R2018")
    pdoc.units = 4
    rect = pdoc.modelspace().add_lwpolyline(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    pdoc.saveas(poly_dxf)

    res_p_cham = execute_nl_region_revision(
        poly_dxf,
        tmp_path / "poly_chamfer",
        "倒角 6×45°",
        region_box=(-5.0, -5.0, 12.0, 12.0),
        candidate_handles=[str(rect.dxf.handle)],
    )
    assert res_p_cham["action"] == "chamfer_corner"
    p_cham_doc = ezdxf.readfile(res_p_cham["output_path"])
    upd_poly = list(p_cham_doc.modelspace().query("LWPOLYLINE"))[0]
    pts_cham = [(round(float(x), 3), round(float(y), 3)) for x, y in upd_poly.get_points("xy")]
    assert len(pts_cham) == 5
    assert (0.0, 6.0) in pts_cham and (6.0, 0.0) in pts_cham

    res_p_fil4 = execute_nl_region_revision(
        poly_dxf,
        tmp_path / "poly_fillet_all",
        "全部四个角倒圆角 R5",
        candidate_handles=[str(rect.dxf.handle)],
    )
    assert res_p_fil4["action"] == "fillet_corner"
    p_fil_doc = ezdxf.readfile(res_p_fil4["output_path"])
    fil_poly = list(p_fil_doc.modelspace().query("LWPOLYLINE"))[0]
    bulges = [abs(float(b)) for _, _, _, _, b in fil_poly.get_points("xyseb") if abs(float(b)) > 1e-6]
    assert len(bulges) == 4
    for b_val in bulges:
        assert isclose(b_val, tan(pi / 8.0), abs_tol=1e-5)


def test_upgrade2_mirror_entities_and_llm_callable_strict_schema_guard(tmp_path: Path):
    dxf_in = tmp_path / "half_part.dxf"
    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    c_left = msp.add_circle((20.0, 30.0), 7.0, dxfattribs={"layer": "OUTLINE"})
    arc_left = msp.add_arc((20.0, 30.0), 12.0, 90.0, 270.0, dxfattribs={"layer": "OUTLINE"})
    doc.saveas(dxf_in)

    res_mir = execute_nl_region_revision(
        dxf_in,
        tmp_path / "mirrored_out",
        "沿垂直轴 x=50 镜像",
        candidate_handles=[str(c_left.dxf.handle), str(arc_left.dxf.handle)],
    )
    assert res_mir["action"] == "mirror_entities"
    assert len(res_mir["added_handles"]) == 2
    mdoc = ezdxf.readfile(res_mir["output_path"])
    centers = sorted((round(float(c.dxf.center.x), 2), round(float(c.dxf.center.y), 2)) for c in mdoc.modelspace().query("CIRCLE"))
    assert centers == [(20.0, 30.0), (80.0, 30.0)]

    # Verify LLM Structured IR callable bridge accepts valid IR and rejects hallucinated fields
    valid_ir = compile_nl_to_feature_ir(
        "在中心打一个 14 的孔",
        llm_callable=lambda prompt, ctx: {
            "schema_version": "cad-revision-ir/v2",
            "operations": [{"op": "add_circle_hole", "diameter": 14.0, "center": [50.0, 30.0]}],
            "rationale_zh": "LLM JSON IR",
        },
    )
    assert isinstance(valid_ir, StructuredRevisionIR)
    ir_exec = execute_revision_ir(dxf_in, tmp_path / "llm_ir_out", valid_ir)
    assert ir_exec["status"] == "applied"

    with pytest.raises(ValidationError):
        compile_nl_to_feature_ir(
            "bad hallucination",
            llm_callable=lambda prompt, ctx: {
                "schema_version": "cad-revision-ir/v2",
                "operations": [{"op": "add_circle_hole", "diameter": 14.0, "hallucinated_param": 999}],
            },
        )


def test_upgrade3_legacy_auditor_detects_tampering_gaps_duplicates_collisions_and_auto_cleans(tmp_path: Path):
    dirty_dxf = tmp_path / "wild_legacy.dxf"
    doc = ezdxf.new("R2018")
    doc.units = 0  # Unitless ($INSUNITS = 0)
    doc.layers.add("OUTLINE")
    doc.layers.add("DIM")
    doc.layers.add("TEXT")
    msp = doc.modelspace()

    # 1. Unclosed contour with a 0.35 mm micro-gap at (100, 50) vs (100, 49.65)
    msp.add_line((0.0, 0.0), (100.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    msp.add_line((100.0, 0.0), (100.0, 49.65), dxfattribs={"layer": "OUTLINE"})
    msp.add_line((100.0, 50.0), (0.0, 50.0), dxfattribs={"layer": "OUTLINE"})
    msp.add_line((0.0, 50.0), (0.0, 0.0), dxfattribs={"layer": "OUTLINE"})

    # 2. Duplicate overlapping LINE & zero-length degenerate LINE
    msp.add_line((100.0, 0.0), (0.0, 0.0), dxfattribs={"layer": "OUTLINE"})  # reversed duplicate!
    msp.add_line((25.0, 25.0), (25.0, 25.0), dxfattribs={"layer": "OUTLINE"})  # zero-length!

    # 3. Tampered associative DIMENSION: actual 100.0 mm overridden to "125"
    tampered_dim = msp.add_linear_dim(
        base=(50.0, -15.0),
        p1=(0.0, 0.0),
        p2=(100.0, 0.0),
        text="125",
        dxfattribs={"layer": "DIM"},
    )
    tampered_dim.render()

    # 4. Exploded fake dimension: standalone LINE of length 40.0 + numeric TEXT "55" on DIM layer
    msp.add_line((10.0, 75.0), (50.0, 75.0), dxfattribs={"layer": "DIM"})
    msp.add_text("55", dxfattribs={"layer": "DIM", "height": 3.5, "insert": (28.0, 76.0)})

    # 5. Text crossed straight through by an OUTLINE line
    msp.add_text("COLLIDE-NOTE", dxfattribs={"layer": "TEXT", "height": 5.0, "insert": (30.0, 20.0)})
    msp.add_line((25.0, 22.5), (75.0, 22.5), dxfattribs={"layer": "OUTLINE"})

    doc.saveas(dirty_dxf)

    report = audit_legacy_dxf(dirty_dxf)
    codes = {iss.code for iss in report.issues}
    assert "UNITLESS_DRAWING" in codes
    assert "TAMPERED_DIMENSION_TEXT" in codes
    assert "EXPLODED_DIMENSION_FRAGMENT" in codes
    assert "OPEN_CONTOUR_GAP" in codes
    assert "ZERO_LENGTH_DEGENERATE" in codes
    assert "DUPLICATE_OVERLAPPING_ENTITY" in codes
    assert "TEXT_GEOMETRY_COLLISION" in codes
    assert report.status == "critical"
    assert all(iss.location_box is not None for iss in report.issues if iss.code != "UNITLESS_DRAWING")

    # Run deterministic cleaner
    cleaned_dxf = tmp_path / "cleaned_legacy.dxf"
    clean_res = clean_legacy_dxf(dirty_dxf, cleaned_dxf, close_micro_gaps_tol=0.6, normalize_insunits=4)
    assert len(clean_res["removed_degenerate_handles"]) == 1
    assert len(clean_res["removed_duplicate_handles"]) == 1
    assert len(clean_res["snapped_gap_handles"]) == 1
    assert clean_res["after_summary"]["degenerate_count"] == 0
    assert clean_res["after_summary"]["duplicate_count"] == 0
    assert clean_res["after_summary"]["topology_gap_count"] == 0
    assert clean_res["after_health_score"] > clean_res["before_health_score"]


def test_http_api_v2_2_upload_legacy_audit_revise_ir_and_vector_diff(tmp_path: Path):
    app = create_mcp_http_app(tmp_path / "ws_v22")
    client = TestClient(app)

    dxf_local = tmp_path / "sample_upload.dxf"
    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (80, 0), (80, 50), (0, 50)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((10, 10), (10, 10), dxfattribs={"layer": "OUTLINE"})  # zero-length degenerate
    doc.saveas(dxf_local)

    up_resp = client.post(
        "/api/v1/uploads",
        json={
            "filename": "sample_upload.dxf",
            "content_base64": base64.b64encode(dxf_local.read_bytes()).decode("ascii"),
        },
    )
    assert up_resp.status_code == 200
    up_data = up_resp.json()
    assert "legacy_audit" in up_data
    assert up_data["legacy_audit"]["summary"]["degenerate_count"] == 1
    uploaded_path = up_data["uploaded_path"]

    # Clean via HTTP /api/v1/tasks/legacy-audit
    clean_resp = client.post(
        "/api/v1/tasks/legacy-audit",
        json={"dxf_path": uploaded_path, "auto_clean": True},
    )
    assert clean_resp.status_code == 200
    clean_data = clean_resp.json()
    assert clean_data["after_audit"]["summary"]["degenerate_count"] == 0
    cleaned_path = clean_data["output_path"]

    # Apply Structured Revision IR via HTTP /api/v1/tasks/revise-ir
    ir_resp = client.post(
        "/api/v1/tasks/revise-ir",
        json={
            "dxf_path": cleaned_path,
            "ir": {
                "schema_version": "cad-revision-ir/v2",
                "operations": [
                    {"op": "add_circle_hole", "diameter": 14.0, "center": [40.0, 25.0]},
                ],
            },
        },
    )
    assert ir_resp.status_code == 200
    ir_data = ir_resp.json()
    assert ir_data["vector_diff"]["summary"]["added"] == 3

    # Standalone /api/v1/tasks/vector-diff endpoint
    vd_resp = client.post(
        "/api/v1/tasks/vector-diff",
        json={"before_dxf_path": cleaned_path, "after_dxf_path": ir_data["output_path"]},
    )
    assert vd_resp.status_code == 200
    assert vd_resp.json()["summary"]["added"] == 3
