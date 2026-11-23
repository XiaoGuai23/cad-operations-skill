"""End-to-end adversarial regression tests for all 8 v2.1.2 round-2 audit items."""

from __future__ import annotations

import json
from math import hypot, isclose
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.cad_drivers import validate_3dsolid_script_allowlist
from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.modeling3d import Model3DSpec, generate_autocad_3d_script
from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.common import RevisionError, compare_untouched, snapshot_modelspace
from cad_skill_core.revision.nl_revise import execute_nl_region_revision


def test_p1_1_negated_delete_clause_resizes_hole_without_deleting_and_pure_negation_blocks(tmp_path: Path):
    dxf_in = tmp_path / "hole_negation_in.dxf"
    out_dir = tmp_path / "hole_negation_out"

    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    hole = msp.add_circle((30.0, 20.0), 6.0, dxfattribs={"layer": "OUTLINE"})
    hole_handle = str(hole.dxf.handle).upper()
    doc.saveas(dxf_in)

    res = execute_nl_region_revision(
        dxf_in,
        out_dir,
        "不要删除这个孔，把它的直径改为 18",
        candidate_handles=[hole_handle],
    )
    assert res["status"] == "applied"
    assert res["action"] == "resize_circle"
    assert res["modified_handles"] == [hole_handle]

    reopened = ezdxf.readfile(res["output_path"])
    circles = list(reopened.modelspace().query("CIRCLE"))
    assert len(circles) == 1
    assert str(circles[0].dxf.handle).upper() == hole_handle
    assert isclose(float(circles[0].dxf.radius), 9.0, abs_tol=1e-6)

    # Purely negated instruction must block rather than deleting
    with pytest.raises(RevisionError):
        execute_nl_region_revision(
            dxf_in,
            tmp_path / "should_not_apply",
            "不要删除这个孔",
            candidate_handles=[hole_handle],
        )


def test_p1_2_resize_selected_hole_preserves_out_of_box_120_dim_other_hole_and_audits_handles(tmp_path: Path):
    """Reviewer E2E check: after modifying a selected Ø12 hole to Ø18, verify out-of-box entities, dimensions, and handle log."""
    dxf_in = tmp_path / "e2e_hole_isolation_in.dxf"
    out_dir_handles = tmp_path / "e2e_hole_isolation_handles"
    out_dir_bbox = tmp_path / "e2e_hole_isolation_bbox"

    doc = ezdxf.new("R2018")
    doc.units = 4
    doc.layers.add("OUTLINE", lineweight=50)
    doc.layers.add("DIM", lineweight=25)
    doc.layers.add("TEXT", lineweight=25)
    msp = doc.modelspace()

    # Selected Ø12 hole inside (0, 0, 40, 40) + its associated diameter dimension
    hole_sel = msp.add_circle((20.0, 20.0), 6.0, dxfattribs={"layer": "OUTLINE"})
    dim_sel = msp.add_diameter_dim(center=(20.0, 20.0), radius=6.0, angle=35.0, text="⌀12", dxfattribs={"layer": "DIM"})
    dim_sel.render()

    # Out-of-box unselected Ø12 hole + its own diameter dimension (must NOT change!)
    hole_other = msp.add_circle((90.0, 20.0), 6.0, dxfattribs={"layer": "OUTLINE"})
    dim_other_hole = msp.add_diameter_dim(
        center=(90.0, 20.0), radius=6.0, angle=35.0, text="⌀12", dxfattribs={"layer": "DIM"}
    )
    dim_other_hole.render()

    # Out-of-box 120 linear dimension (previously corrupted to "180" by substring replace!)
    dim_120 = msp.add_linear_dim(
        base=(60.0, -25.0), p1=(0.0, 0.0), p2=(120.0, 0.0), text="120", dxfattribs={"layer": "DIM"}
    )
    dim_120.render()

    # Out-of-box geometry and note containing "12"
    frame_line = msp.add_line((0.0, 60.0), (120.0, 60.0), dxfattribs={"layer": "OUTLINE"})
    note_txt = msp.add_text("ITEM-120 / HOLE-12", dxfattribs={"layer": "TEXT", "height": 3.5, "insert": (50.0, 70.0)})
    doc.saveas(dxf_in)

    h_hole_sel = str(hole_sel.dxf.handle).upper()
    h_dim_sel = str(dim_sel.dimension.dxf.handle).upper()
    h_hole_other = str(hole_other.dxf.handle).upper()
    h_dim_other = str(dim_other_hole.dimension.dxf.handle).upper()
    h_dim_120 = str(dim_120.dimension.dxf.handle).upper()
    h_line = str(frame_line.dxf.handle).upper()
    h_note = str(note_txt.dxf.handle).upper()

    before_doc = ezdxf.readfile(dxf_in)
    before_snap = snapshot_modelspace(before_doc)

    # 1. Selection by explicit hole handle
    res_h = execute_nl_region_revision(
        dxf_in,
        out_dir_handles,
        "将孔径改成 Ø18",
        candidate_handles=[h_hole_sel],
    )
    assert res_h["status"] == "applied"
    assert res_h["action"] == "resize_circle"
    assert set(res_h["modified_handles"]) == {h_hole_sel, h_dim_sel}

    after_h = ezdxf.readfile(res_h["output_path"])
    by_handle_h = {str(e.dxf.handle).upper(): e for e in after_h.modelspace()}

    assert isclose(float(by_handle_h[h_hole_sel].dxf.radius), 9.0, abs_tol=1e-6)
    assert by_handle_h[h_dim_sel].dxf.text == "⌀18"

    # Out-of-box dimension 120, unselected Ø12 hole, line, and text are 100% untouched
    assert by_handle_h[h_dim_120].dxf.text == "120"
    assert isclose(float(by_handle_h[h_hole_other].dxf.radius), 6.0, abs_tol=1e-6)
    assert by_handle_h[h_dim_other].dxf.text == "⌀12"
    assert by_handle_h[h_note].dxf.text == "ITEM-120 / HOLE-12"
    assert h_line in by_handle_h
    assert compare_untouched(before_snap, snapshot_modelspace(after_h), set(res_h["modified_handles"]), 1e-6) == []

    # 2. Selection by bounding box (0, 0, 40, 40)
    res_b = execute_nl_region_revision(
        dxf_in,
        out_dir_bbox,
        "将孔径改成 Ø18",
        region_box=(0.0, 0.0, 40.0, 40.0),
    )
    assert set(res_b["modified_handles"]) == {h_hole_sel, h_dim_sel}
    after_b = ezdxf.readfile(res_b["output_path"])
    by_handle_b = {str(e.dxf.handle).upper(): e for e in after_b.modelspace()}
    assert by_handle_b[h_dim_120].dxf.text == "120"
    assert compare_untouched(before_snap, snapshot_modelspace(after_b), set(res_b["modified_handles"]), 1e-6) == []


def test_p1_3_mixed_line_and_circle_selection_length_change_modifies_only_line(tmp_path: Path):
    dxf_in = tmp_path / "mixed_sel_in.dxf"
    out_dir = tmp_path / "mixed_sel_out"

    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    ln = msp.add_line((10.0, 10.0), (60.0, 10.0), dxfattribs={"layer": "OUTLINE"})
    cir = msp.add_circle((35.0, 35.0), 10.0, dxfattribs={"layer": "OUTLINE"})
    h_ln = str(ln.dxf.handle).upper()
    h_cir = str(cir.dxf.handle).upper()
    doc.saveas(dxf_in)

    before_snap = snapshot_modelspace(ezdxf.readfile(dxf_in))

    res = execute_nl_region_revision(
        dxf_in,
        out_dir,
        "长度改为 90",
        candidate_handles=[h_ln, h_cir],
    )
    assert res["status"] in {"applied", "passed"}
    assert res["action"] == "set_length"
    assert res["modified_handles"] == [h_ln]

    after_doc = ezdxf.readfile(res["output_path"])
    by_handle = {str(e.dxf.handle).upper(): e for e in after_doc.modelspace()}
    upd_ln = by_handle[h_ln]
    upd_cir = by_handle[h_cir]
    new_len = hypot(
        float(upd_ln.dxf.end.x) - float(upd_ln.dxf.start.x),
        float(upd_ln.dxf.end.y) - float(upd_ln.dxf.start.y),
    )
    assert isclose(new_len, 90.0, abs_tol=1e-6)
    assert isclose(float(upd_cir.dxf.radius), 10.0, abs_tol=1e-6)
    assert compare_untouched(before_snap, snapshot_modelspace(after_doc), {h_ln}, 1e-6) == []


def test_p1_4_inch_drawing_converts_explicit_mm_prompt_to_document_inches(tmp_path: Path):
    dxf_in = tmp_path / "inch_drawing_in.dxf"
    out_dir = tmp_path / "inch_drawing_out"

    doc = ezdxf.new("R2018")
    doc.units = 1  # Inches ($INSUNITS = 1)
    msp = doc.modelspace()
    hole = msp.add_circle((2.0, 2.0), 0.25, dxfattribs={"layer": "OUTLINE"})  # Ø0.5 in
    h_hole = str(hole.dxf.handle).upper()
    doc.saveas(dxf_in)

    res = execute_nl_region_revision(
        dxf_in,
        out_dir,
        "把孔径改为 25.4 mm",
        candidate_handles=[h_hole],
    )
    assert res["status"] == "applied"
    assert res["action"] == "resize_circle"

    after_doc = ezdxf.readfile(res["output_path"])
    upd_hole = list(after_doc.modelspace().query("CIRCLE"))[0]
    # 25.4 mm == 1.0 inch diameter -> 0.5 inch radius (NOT 25.4 inches / 12.7 radius!)
    assert isclose(float(upd_hole.dxf.radius), 0.5, abs_tol=1e-6)

    # Unitless DXF ($INSUNITS = 0) with explicit physical unit must raise RevisionError rather than guessing
    unitless_in = tmp_path / "unitless_in.dxf"
    udoc = ezdxf.new("R2018")
    udoc.units = 0
    uhole = udoc.modelspace().add_circle((0.0, 0.0), 5.0)
    udoc.saveas(unitless_in)
    with pytest.raises(RevisionError):
        execute_nl_region_revision(
            unitless_in,
            tmp_path / "unitless_out",
            "把孔径改为 25.4 mm",
            candidate_handles=[str(uhole.dxf.handle).upper()],
        )


def test_p1_5_multiple_gdt_clauses_preserve_exact_datums_b_and_c_and_render_both(tmp_path: Path):
    task_dir = tmp_path / "gdt_multi_datum"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief = task_dir / "input" / "flange_gdt.md"
    brief.write_text(
        "# 法兰盘\n"
        "材料：45钢\n"
        "外径：140 mm\n"
        "内孔：48 mm\n"
        "厚度：20 mm\n"
        "节圆直径：102 mm\n"
        "安装孔：4×Ø14\n"
        "端面对基准 B 的垂直度 0.05，另一端面对基准 C 的平行度 0.02\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief], output_path=task_dir / "work" / "drawing-spec.json")
    assert spec.validation.missing == []
    assert spec.validation.conflicts == []

    frames = [(f.symbol, f.tolerance, f.datum_refs, f.target_feature) for f in spec.gdt_frames]
    assert frames == [
        ("perpendicularity", "0.05", ["B"], "端面"),
        ("parallelism", "0.02", ["C"], "另一端面"),
    ]
    assert [d.letter for d in spec.datums] == ["B", "C"]

    result = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert result.status in {"passed", "partial", "complete"}

    audit_payload = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit_payload["summary"]["errors"] == 0

    dxf_doc = ezdxf.readfile(task_dir / "output" / "drawing.dxf")
    emitted_texts = [str(e.dxf.text).strip() for e in dxf_doc.modelspace().query("TEXT")]
    assert "0.05" in emitted_texts
    assert "0.02" in emitted_texts
    assert emitted_texts.count("B") == 2  # once in GDT frame, once in datum box [B]
    assert emitted_texts.count("C") == 2  # once in GDT frame, once in datum box [C]
    assert "A" not in emitted_texts

    # Missing numeric tolerance on GD&T clause must record validation.missing instead of silently dropping
    bad_brief = tmp_path / "gdt_missing_val.md"
    bad_brief.write_text(
        "# 法兰盘\n外径：140 mm\n内孔：48 mm\n厚度：20 mm\n节圆直径：102 mm\n安装孔：4×Ø14\n端面对基准 B 的垂直度\n",
        encoding="utf-8",
    )
    bad_spec = compile_spec_from_files([bad_brief])
    assert bad_spec.validation.missing


def test_p1_6_surface_roughness_preserves_both_general_ra_6_3_and_mating_ra_0_8(tmp_path: Path):
    task_dir = tmp_path / "roughness_split"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief = task_dir / "input" / "flange_ra.md"
    brief.write_text(
        "# 法兰盘\n"
        "材料：45钢\n"
        "外径：140 mm\n"
        "内孔：48 mm\n"
        "厚度：20 mm\n"
        "节圆直径：102 mm\n"
        "安装孔：4×Ø14\n"
        "其余表面 Ra 6.3，配合面 Ra 0.8\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief], output_path=task_dir / "work" / "drawing-spec.json")
    assert spec.validation.missing == []

    general_sfs = [sf for sf in spec.surface_finishes if sf.is_general]
    local_sfs = [sf for sf in spec.surface_finishes if not sf.is_general]
    assert len(general_sfs) == 1 and general_sfs[0].value == "Ra 6.3"
    assert len(local_sfs) == 1 and local_sfs[0].value == "Ra 0.8"
    assert local_sfs[0].target_surface == "配合面"

    result = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert result.status in {"passed", "partial", "complete"}
    audit_payload = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit_payload["summary"]["errors"] == 0

    dxf_doc = ezdxf.readfile(task_dir / "output" / "drawing.dxf")
    all_texts = [str(e.dxf.text).strip() for e in dxf_doc.modelspace().query("TEXT")]
    assert "Ra 6.3" in all_texts
    assert "Ra 0.8" in all_texts


def test_p1_7_native_dwg_spur_gear_and_ribbed_bracket_scripts_generate_full_teeth_and_rib(tmp_path: Path):
    gear_spec = Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "gear_m3_z24", "drawing_number": "G3-24"},
            "body": {
                "kind": "spur_gear_3d",
                "module_mm": 3.0,
                "teeth": 24,
                "face_width": 28.0,
                "bore_diameter": 22.0,
                "source_id": "s1",
            },
            "provenance": [{"id": "s1", "file": "test", "locator": "L1", "confidence": 1.0}],
        }
    )
    raw_scr = generate_autocad_3d_script(gear_spec, tmp_path / "gear_m3_z24.dwg")
    sanitized_scr = validate_3dsolid_script_allowlist(raw_scr)

    pline_lines = [ln.strip() for ln in sanitized_scr.splitlines() if ln.strip().upper().startswith("_.PLINE ")]
    assert len(pline_lines) == 1, "SpurGear3D script must emit closed _.PLINE outer tooth profile"
    tokens = pline_lines[0].split()[1:-1]  # strip '_.PLINE' and trailing '_C'
    assert len(tokens) == 24 * 8  # 8 profile points per tooth * 24 teeth

    radii = [hypot(*(float(v) for v in tok.split(","))) for tok in tokens]
    # r_f = 3*(24-2.5)/2 = 32.25, r_p = 3*24/2 = 36.0, r_a = 3*(24+2)/2 = 39.0
    assert isclose(min(radii), 32.25, abs_tol=1e-2)
    assert isclose(max(radii), 39.0, abs_tol=1e-2)
    # Every tooth has 3 tip vertices (left tip corner, tip center, right tip corner) -> 72 tip vertices (24 teeth)
    tip_vertices = [r for r in radii if isclose(r, 39.0, abs_tol=1e-2)]
    assert len(tip_vertices) == 24 * 3
    assert any(ln.strip().upper().startswith("_.EXTRUDE ") for ln in sanitized_scr.splitlines())

    # RibbedBracket3D script must also emit _WEDGE stiffening rib and horizontal boss hole cylinder
    bracket_spec = Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "rib_bracket", "drawing_number": "RB-01"},
            "body": {
                "kind": "ribbed_bracket_3d",
                "base_length": 130.0,
                "base_width": 84.0,
                "base_thickness": 14.0,
                "wall_thickness": 16.0,
                "total_height": 92.0,
                "rib_thickness": 12.0,
                "boss_hole_diameter": 28.0,
                "mount_hole_diameter": 12.0,
                "source_id": "s1",
            },
            "provenance": [{"id": "s1", "file": "test", "locator": "L1", "confidence": 1.0}],
        }
    )
    bracket_scr = validate_3dsolid_script_allowlist(generate_autocad_3d_script(bracket_spec, tmp_path / "bracket.dwg"))
    assert any(ln.strip().upper().startswith("_WEDGE ") for ln in bracket_scr.splitlines())
    assert any(" _A " in ln.upper() for ln in bracket_scr.splitlines())


def test_p2_1_rubric_scorer_enforces_strict_explicit_tolerance_without_3pct_relaxation(tmp_path: Path):
    # 1. Actual width 1020 mm vs required 1000 ± 0.1 mm MUST fail (previously passed due to 3% rel_tol = 30.6 mm!)
    dxf_bad = tmp_path / "width_1020.dxf"
    doc_bad = ezdxf.new("R2018")
    doc_bad.layers.add("OUTLINE", lineweight=50)
    doc_bad.layers.add("DIM", lineweight=25)
    msp_bad = doc_bad.modelspace()
    msp_bad.add_lwpolyline(
        [(0.0, 0.0), (1020.0, 0.0), (1020.0, 500.0), (0.0, 500.0)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    dim_bad = msp_bad.add_linear_dim(
        base=(510.0, -20.0), p1=(0.0, 0.0), p2=(1000.5, 0.0), text="1000.5", dxfattribs={"layer": "DIM"}
    )
    dim_bad.render()
    doc_bad.saveas(dxf_bad)

    strict_rubric = {
        "criteria": [
            {"id": "SPAN-STRICT", "description": "1000 ± 0.1 mm width", "type": "bbox_span", "width": 1000.0, "tolerance": 0.1, "weight": 50},
            {"id": "DIM-STRICT", "description": "1000 ± 0.1 mm dim", "expected_dimension_values": [1000.0], "tolerance": 0.1, "weight": 50},
        ]
    }
    bad_report = score_dxf_drawing(dxf_bad, custom_rubric=strict_rubric)
    by_id_bad = {c.criterion_id: c for c in bad_report.custom_criteria}
    assert by_id_bad["SPAN-STRICT"].passed is False
    assert by_id_bad["SPAN-STRICT"].earned == 0.0
    assert by_id_bad["DIM-STRICT"].passed is False
    assert by_id_bad["DIM-STRICT"].earned == 0.0

    # 2. Actual width 1000.08 mm and dim 1000.05 mm (within ±0.1 mm) MUST pass
    dxf_ok = tmp_path / "width_1000_08.dxf"
    doc_ok = ezdxf.new("R2018")
    doc_ok.layers.add("OUTLINE", lineweight=50)
    doc_ok.layers.add("DIM", lineweight=25)
    msp_ok = doc_ok.modelspace()
    msp_ok.add_lwpolyline(
        [(0.0, 0.0), (1000.08, 0.0), (1000.08, 500.0), (0.0, 500.0)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    dim_ok = msp_ok.add_linear_dim(
        base=(500.0, 20.0), p1=(0.0, 0.0), p2=(1000.05, 0.0), text="1000.05", dxfattribs={"layer": "DIM"}
    )
    dim_ok.render()
    doc_ok.saveas(dxf_ok)

    ok_report = score_dxf_drawing(dxf_ok, custom_rubric=strict_rubric)
    assert all(c.passed for c in ok_report.custom_criteria)
