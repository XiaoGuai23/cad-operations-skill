"""Regression tests for v2.2.1 review items (P1/P2) and Unifont CI layout compatibility."""

from __future__ import annotations

from math import hypot, isclose
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.inspection.legacy_audit import audit_legacy_dxf, clean_legacy_dxf
from cad_skill_core.mcp.cad_scene import compute_dxf_vector_diff
from cad_skill_core.revision.ir_engine import compile_nl_to_feature_ir
from cad_skill_core.revision.nl_revise import RevisionError, execute_nl_region_revision


def test_p1_bolt_circle_independent_hole_and_pcd_unit_scaling(tmp_path: Path) -> None:
    """[P1] Hole diameter and bolt-circle diameter (PCD) must convert their units independently."""
    # Case A: mm drawing ($INSUNITS=4), prompt mixes "10 mm" holes with "4 in" PCD (= 101.6 mm)
    mm_dxf = tmp_path / "flange_mm.dxf"
    doc_mm = ezdxf.new("R2010")
    doc_mm.units = 4  # mm
    msp_mm = doc_mm.modelspace()
    msp_mm.add_circle((0.0, 0.0), 80.0, dxfattribs={"layer": "OUTLINE"})
    doc_mm.saveas(mm_dxf)

    res_mm = execute_nl_region_revision(
        mm_dxf,
        tmp_path / "out_mm",
        "新增 6×Ø10 mm 均布螺栓孔，节圆 4 in",
    )
    assert res_mm["status"] == "applied"
    assert len(res_mm["added_handles"]) == 7  # 6 bolt holes + 1 dashed PCD circle

    out_doc_mm = ezdxf.readfile(res_mm["output_path"])
    added_mm_holes = [
        e
        for e in out_doc_mm.modelspace()
        if str(e.dxf.handle).upper() in set(res_mm["added_handles"])
        and str(e.dxf.layer).upper() == "OUTLINE"
    ]
    assert len(added_mm_holes) == 6
    for hole in added_mm_holes:
        # Hole diameter 10 mm -> radius 5.0 mm
        assert isclose(float(hole.dxf.radius), 5.0, abs_tol=1e-4)
        # PCD 4 in -> 101.6 mm -> radial distance from (0,0) = 50.8 mm
        r_center = hypot(float(hole.dxf.center.x), float(hole.dxf.center.y))
        assert isclose(r_center, 50.8, abs_tol=1e-4)

    # Case B: inch drawing ($INSUNITS=1), hole has explicit "25.4 mm" (= 1.0 in) while PCD "6" is unitless (= 6.0 in)
    in_dxf = tmp_path / "flange_in.dxf"
    doc_in = ezdxf.new("R2010")
    doc_in.units = 1  # inches
    msp_in = doc_in.modelspace()
    msp_in.add_circle((0.0, 0.0), 5.0, dxfattribs={"layer": "OUTLINE"})
    doc_in.saveas(in_dxf)

    res_in = execute_nl_region_revision(
        in_dxf,
        tmp_path / "out_in",
        "新增 4×Ø25.4 mm 均布螺栓孔，节圆 6",
    )
    assert res_in["status"] == "applied"
    out_doc_in = ezdxf.readfile(res_in["output_path"])
    added_in_holes = [
        e
        for e in out_doc_in.modelspace()
        if str(e.dxf.handle).upper() in set(res_in["added_handles"])
        and str(e.dxf.layer).upper() == "OUTLINE"
    ]
    assert len(added_in_holes) == 4
    for hole in added_in_holes:
        # 25.4 mm -> 1.0 in diameter -> radius 0.5 in
        assert isclose(float(hole.dxf.radius), 0.5, abs_tol=1e-4)
        # Unitless PCD "6" stays in native drawing units -> 6.0 in diameter -> radius 3.0 in
        r_center = hypot(float(hole.dxf.center.x), float(hole.dxf.center.y))
        assert isclose(r_center, 3.0, abs_tol=1e-4)


def test_p1_legacy_audit_and_clean_scale_seam_tolerance_by_insunits(tmp_path: Path) -> None:
    """[P1] Gap/seam tolerances (in mm) must scale into $INSUNITS (e.g. metres / inches) instead of raw coordinates."""
    # Metre drawing ($INSUNITS=6):
    # - Deliberate 0.5 m (500 mm) doorway opening at top-right -> MUST NOT be flagged or snapped closed!
    # - True 0.0004 m (0.4 mm) micro-gap at bottom-right -> MUST be flagged and snapped closed!
    meter_dxf = tmp_path / "room_meters.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6  # metres
    msp = doc.modelspace()
    l_bottom = msp.add_line((0.0, 0.0), (3.9996, 0.0), dxfattribs={"layer": "OUTLINE"})  # 0.4 mm gap to (4.0, 0.0)
    l_right = msp.add_line((4.0, 0.0), (4.0, 2.5), dxfattribs={"layer": "OUTLINE"})      # stops at y=2.5 (0.5 m doorway to y=3.0)
    l_top = msp.add_line((4.0, 3.0), (0.0, 3.0), dxfattribs={"layer": "OUTLINE"})
    msp.add_line((0.0, 3.0), (0.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    h_bottom = str(l_bottom.dxf.handle).upper()
    h_right = str(l_right.dxf.handle).upper()
    h_top = str(l_top.dxf.handle).upper()
    doc.saveas(meter_dxf)

    report = audit_legacy_dxf(meter_dxf, gap_tolerance_mm=1.0)
    gap_issues = [i for i in report.issues if i.code == "OPEN_CONTOUR_GAP"]
    assert len(gap_issues) == 1
    assert set(gap_issues[0].handles) == {h_bottom, h_right}
    assert h_top not in gap_issues[0].handles

    cleaned_dxf = tmp_path / "room_meters_clean.dxf"
    clean_res = clean_legacy_dxf(meter_dxf, cleaned_dxf, close_micro_gaps_tol=0.8)
    assert len(clean_res["snapped_gap_handles"]) == 1

    clean_doc = ezdxf.readfile(cleaned_dxf)
    by_h = {str(e.dxf.handle).upper(): e for e in clean_doc.modelspace()}
    # 0.4 mm micro-gap was snapped closed
    assert hypot(
        float(by_h[h_bottom].dxf.end.x) - float(by_h[h_right].dxf.start.x),
        float(by_h[h_bottom].dxf.end.y) - float(by_h[h_right].dxf.start.y),
    ) <= 1e-7
    # 0.5 m doorway opening was preserved untouched!
    door_span = hypot(
        float(by_h[h_right].dxf.end.x) - float(by_h[h_top].dxf.start.x),
        float(by_h[h_right].dxf.end.y) - float(by_h[h_top].dxf.start.y),
    )
    assert isclose(door_span, 0.5, abs_tol=1e-6)


def test_p1_clean_legacy_dxf_preserves_3d_vertical_edges_and_distinct_z_elevations(tmp_path: Path) -> None:
    """[P1] Cleaning must preserve vertical Z lines and entities at distinct Z elevations."""
    dxf_3d = tmp_path / "frame_3d.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()

    # 1. Vertical 3D column line: 2D projection is (10, 20)->(10, 20) (len_2d=0), 3D length=40 -> MUST KEEP
    v_col = msp.add_line((10.0, 20.0, 0.0), (10.0, 20.0, 40.0), dxfattribs={"layer": "OUTLINE"})
    # 2. Lower & upper horizontal beams with identical XY but Z=0 vs Z=40 -> MUST KEEP BOTH
    beam_z0 = msp.add_line((0.0, 0.0, 0.0), (80.0, 0.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    beam_z40 = msp.add_line((0.0, 0.0, 40.0), (80.0, 0.0, 40.0), dxfattribs={"layer": "OUTLINE"})
    # 3. Exact same-Z duplicate at Z=40 -> MUST REMOVE ONLY THIS DUPLICATE
    beam_z40_dup = msp.add_line((80.0, 0.0, 40.0), (0.0, 0.0, 40.0), dxfattribs={"layer": "OUTLINE"})
    # 4. Lower & upper circles at Z=0 vs Z=40 -> MUST KEEP BOTH
    cir_z0 = msp.add_circle((40.0, 40.0, 0.0), 12.0, dxfattribs={"layer": "OUTLINE"})
    cir_z40 = msp.add_circle((40.0, 40.0, 40.0), 12.0, dxfattribs={"layer": "OUTLINE"})
    # 5. Lower & upper LWPOLYLINEs at elevation=0 vs elevation=40 -> MUST KEEP BOTH
    poly_z0 = msp.add_lwpolyline([(0, 0), (50, 0), (50, 30)], close=True, dxfattribs={"layer": "OUTLINE", "elevation": 0.0})
    poly_z40 = msp.add_lwpolyline([(0, 0), (50, 0), (50, 30)], close=True, dxfattribs={"layer": "OUTLINE", "elevation": 40.0})
    # 6. True 3D zero-length degenerate point-line at (5, 5, 10)->(5, 5, 10) -> MUST REMOVE
    degen_3d = msp.add_line((5.0, 5.0, 10.0), (5.0, 5.0, 10.0), dxfattribs={"layer": "OUTLINE"})

    keep_handles = {
        str(e.dxf.handle).upper()
        for e in (v_col, beam_z0, beam_z40, cir_z0, cir_z40, poly_z0, poly_z40)
    }
    h_dup = str(beam_z40_dup.dxf.handle).upper()
    h_degen = str(degen_3d.dxf.handle).upper()
    doc.saveas(dxf_3d)

    cleaned_path = tmp_path / "frame_3d_clean.dxf"
    res = clean_legacy_dxf(dxf_3d, cleaned_path)
    assert res["removed_degenerate_handles"] == [h_degen]
    assert res["removed_duplicate_handles"] == [h_dup]

    after_doc = ezdxf.readfile(cleaned_path)
    surviving = {str(e.dxf.handle).upper() for e in after_doc.modelspace()}
    assert keep_handles.issubset(surviving)
    assert h_dup not in surviving
    assert h_degen not in surviving


def test_p2_feature_ir_compiler_verifies_whole_prompt_and_executes_multi_clause(tmp_path: Path) -> None:
    """[P2] Compiler must parse all clauses or reject mixed/unparsed trailing clauses instead of dropping them."""
    plate_dxf = tmp_path / "plate.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    rect = msp.add_lwpolyline(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    h_rect = str(rect.dxf.handle).upper()
    doc.saveas(plate_dxf)

    # 1. Valid 2-clause feature prompt -> compiles and executes BOTH operations!
    res_multi = execute_nl_region_revision(
        plate_dxf,
        tmp_path / "out_multi",
        "在 (50, 30) 新增 Ø16 圆孔，同时全部四个角倒圆角 R6",
        candidate_handles=[h_rect],
    )
    assert res_multi["status"] == "applied"
    assert len(res_multi["ir"]["operations"]) == 2
    assert len(res_multi["added_handles"]) == 3  # 1 hole circle + 2 cross-hair centerlines
    assert h_rect in res_multi["modified_handles"]

    # 2. Mixed prompt with an unsupported/conflicting clause -> MUST raise RevisionError, never silently drop!
    with pytest.raises(RevisionError):
        execute_nl_region_revision(
            plate_dxf,
            tmp_path / "out_mixed",
            "在 (50, 30) 新增 Ø16 圆孔，并把右边直线旋转 37 度",
            candidate_handles=[h_rect],
        )
    assert not (tmp_path / "out_mixed" / "revised.dxf").exists()

    # 3. Single clause with unconsumed action tail -> raises RevisionError in BOTH direct compiler and nl_revise!
    with pytest.raises(RevisionError):
        compile_nl_to_feature_ir("新增 Ø12 圆孔 删除外框")
    with pytest.raises(RevisionError):
        execute_nl_region_revision(
            plate_dxf,
            tmp_path / "out_mixed2",
            "新增 Ø12 圆孔 删除外框",
            candidate_handles=[h_rect],
        )


def test_p2_vector_diff_detects_edits_inside_referenced_block_definitions(tmp_path: Path) -> None:
    """[P2] compute_dxf_vector_diff must flag INSERTs as modified when their referenced BLOCK geometry changes."""
    before_dxf = tmp_path / "before_blk.dxf"
    after_dxf = tmp_path / "after_blk.dxf"

    doc = ezdxf.new("R2010")
    doc.units = 4
    blk = doc.blocks.new(name="VALVE_SYM")
    blk.add_circle((0.0, 0.0), 5.0, dxfattribs={"layer": "0"})
    blk.add_line((-5.0, 0.0), (5.0, 0.0), dxfattribs={"layer": "0"})
    msp = doc.modelspace()
    ins = msp.add_blockref("VALVE_SYM", (30.0, 20.0), dxfattribs={"layer": "OUTLINE"})
    untouched_ln = msp.add_line((0.0, 0.0), (100.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    h_ins = str(ins.dxf.handle).upper()
    h_ln = str(untouched_ln.dxf.handle).upper()
    doc.saveas(before_dxf)

    # Modify ONLY the geometry inside doc.blocks["VALVE_SYM"] while keeping the INSERT's own DXF tags identical!
    doc2 = ezdxf.readfile(before_dxf)
    for child in doc2.blocks.get("VALVE_SYM"):
        if child.dxftype() == "CIRCLE":
            child.dxf.radius = 9.5
    doc2.saveas(after_dxf)

    diff = compute_dxf_vector_diff(before_dxf, after_dxf)
    assert h_ins in diff["modified_handles"]
    assert h_ln not in diff["modified_handles"]
    assert diff["summary"]["modified"] == 1
    assert diff["summary"]["untouched"] == 1
    # Ghost overlay must capture the pre-edit R=5.0 circle inside the block at (30, 20)
    ghost_circles = [p for p in diff["ghost_before_primitives"] if p["k"] == "circle"]
    assert len(ghost_circles) == 1
    assert isclose(float(ghost_circles[0]["d"][0]), 30.0, abs_tol=1e-4)
    assert isclose(float(ghost_circles[0]["d"][1]), 20.0, abs_tol=1e-4)
    assert isclose(float(ghost_circles[0]["d"][2]), 5.0, abs_tol=1e-4)
