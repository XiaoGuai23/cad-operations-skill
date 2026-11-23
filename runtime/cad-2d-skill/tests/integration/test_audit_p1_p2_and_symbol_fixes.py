"""Adversarial regression tests for all 7 P1/P2 audit items + 4 v2.1.1 symbol/pin fixes."""

from __future__ import annotations

import json
from math import isclose
from pathlib import Path
import time

import ezdxf
import pytest

from cad_skill_core.benchmark.cad1k_harness import load_cad1k_task_bundle, run_cad1k_benchmark
from cad_skill_core.cad_drivers import validate_3dsolid_script_allowlist, verify_3dsolid_dxf_entities
from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.mcp.industry_adapters import generate_electrical_sld_bundle
from cad_skill_core.mcp.registry import call_mcp_tool
from cad_skill_core.mcp.sandbox import MCPSandboxError, MCPWorkspaceSandbox
from cad_skill_core.mcp.studio_ui import render_studio_html
from cad_skill_core.mcp.task_queue import AsyncCADJobManager
from cad_skill_core.modeling3d import Model3DSpec, generate_autocad_3d_script
from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.nl_revise import execute_nl_region_revision


def test_p1_1_external_cad1k_revision_does_not_delete_wrong_entity(tmp_path):
    bundle_dir = tmp_path / "ext_rev_add_circle"
    in_dir = bundle_dir / "input_files"
    in_dir.mkdir(parents=True)

    dxf_in = in_dir / "baseline.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (80.0, 0.0), dxfattribs={"layer": "0"})
    msp.add_text("KEEP-ME", dxfattribs={"height": 3.5}).set_placement((10.0, 10.0))
    doc.saveas(dxf_in)

    (bundle_dir / "task_desc.json").write_text(
        json.dumps(
            {
                "task_id": "EXT-ADD-CIRCLE-01",
                "title": "Add circle and preserve line",
                "mode": "revision",
                "prompt": "添加圆并保留直线",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out_root = tmp_path / "bench_out"
    summary = run_cad1k_benchmark(out_root, external_bundle_dirs=[bundle_dir])
    assert summary["task_count"] == 1
    assert summary["passed_count"] == 0
    task_res = summary["tasks"][0]
    assert task_res["pipeline_status"] in {"unsupported", "failed"}
    assert task_res["total_score"] == 0.0

    # Baseline DXF still has both LINE and TEXT untouched, and no bogus output DXF was published
    reopened = ezdxf.readfile(dxf_in)
    assert [e.dxftype() for e in reopened.modelspace()] == ["LINE", "TEXT"]
    assert not (out_root / "EXT-ADD-CIRCLE-01" / "output" / "drawing.dxf").exists()


@pytest.mark.parametrize("dia_sym", ["Ø", "ø", "∅", "⌀", "Φ", "φ"])
def test_p1_2_flange_all_diameter_symbols_produce_four_holes(tmp_path, dia_sym):
    brief = tmp_path / f"flange_{ord(dia_sym)}.md"
    brief.write_text(
        f"# 法兰盘\n外径：{dia_sym}140 mm\n内孔：{dia_sym}48 mm\n厚度：20 mm\n节圆直径：{dia_sym}102 mm\n安装孔：4×{dia_sym}14\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief])
    assert spec.validation.missing == []
    assert spec.validation.conflicts == []
    assert spec.geometry.parameters["hole_count"] == 4


def test_p1_2_flange_blocks_when_hole_description_cannot_be_parsed(tmp_path):
    brief = tmp_path / "flange_bad_holes.md"
    brief.write_text(
        "# 法兰盘\n外径：140 mm\n内孔：48 mm\n厚度：20 mm\n节圆直径：102 mm\n安装孔：均布若干通孔\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief])
    assert spec.validation.missing, "must block silent 0-hole flange when holes are described but unparsed"

    sb = MCPWorkspaceSandbox(tmp_path / "ws_block")
    res = call_mcp_tool(
        sb,
        "cad_generate_mechanical_2d",
        {"markdown_brief": brief.read_text(encoding="utf-8")},
    )
    assert res["isError"] is True
    assert res["structuredContent"]["status"] == "blocked_by_spec_validation"


def test_p1_3_tolerance_binds_only_to_matching_feature_not_equal_value_dimension(tmp_path):
    brief = tmp_path / "same_val_tol.md"
    brief.write_text(
        "# 连接法兰\n材料：45钢\n外径：100 mm\n内孔：20±0.05 mm\n板厚：20 mm\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief])
    assert spec.validation.missing == []

    bore_dims = [d for d in spec.dimensions if d.param_key == "bore_diameter"]
    thick_dims = [d for d in spec.dimensions if d.param_key == "thickness"]
    assert bore_dims and thick_dims
    for bd in bore_dims:
        assert bd.format_label() == "⌀20±0.05"
    for td in thick_dims:
        assert td.tolerance_upper is None
        assert td.tolerance_lower is None
        assert td.format_label() == "20"


def test_p1_4_electrical_stem_and_cad1k_task_id_block_path_traversal(tmp_path):
    sld_spec = {
        "title": "测试站",
        "sheet": {"width_mm": 440, "height_mm": 317},
        "voltage_levels": [{"name": "10kV", "bus": "single", "feeders": ["出线1"], "pt": 1}],
    }
    with pytest.raises(MCPSandboxError):
        generate_electrical_sld_bundle(sld_spec, tmp_path / "sld_out", stem="../escaped_sld")
    assert not (tmp_path / "escaped_sld.dxf").exists()

    bad_bundle = tmp_path / "bad_cad1k"
    bad_bundle.mkdir()
    (bad_bundle / "task_desc.json").write_text(
        json.dumps({"task_id": "../../escaped_task", "prompt": "# 法兰\n外径：100\n"}),
        encoding="utf-8",
    )
    with pytest.raises(MCPSandboxError):
        load_cad1k_task_bundle(bad_bundle)


def test_p1_5_all_3d_bodies_emit_real_3dsolid_commands_and_empty_dwg_is_rejected(tmp_path):
    bodies = [
        {
            "kind": "slotted_bracket_3d",
            "width": 140.0,
            "height": 80.0,
            "thickness": 12.0,
            "corner_radius": 8.0,
            "slot_length": 44.0,
            "slot_width": 14.0,
            "hole_diameter": 10.0,
            "hole_inset": 15.0,
            "source_id": "s1",
        },
        {
            "kind": "extrude_polygon",
            "outer_loop": [[0.0, 0.0], [60.0, 0.0], [60.0, 40.0], [0.0, 40.0]],
            "inner_loops": [[[15.0, 10.0], [35.0, 10.0], [35.0, 25.0], [15.0, 25.0]]],
            "height": 15.0,
            "source_id": "s1",
        },
        {
            "kind": "spur_gear_3d",
            "module_mm": 3.0,
            "teeth": 24,
            "face_width": 28.0,
            "bore_diameter": 22.0,
            "source_id": "s1",
        },
        {
            "kind": "bearing_cap_3d",
            "flange_diameter": 130.0,
            "spigot_diameter": 90.0,
            "bore_diameter": 42.0,
            "total_height": 32.0,
            "flange_thickness": 12.0,
            "bolt_circle_diameter": 110.0,
            "hole_diameter": 9.0,
            "hole_count": 6,
            "source_id": "s1",
        },
        {
            "kind": "ribbed_bracket_3d",
            "base_length": 130.0,
            "base_width": 84.0,
            "total_height": 92.0,
            "base_thickness": 14.0,
            "wall_thickness": 14.0,
            "rib_thickness": 10.0,
            "boss_hole_diameter": 28.0,
            "mount_hole_diameter": 12.0,
            "source_id": "s1",
        },
    ]
    for b in bodies:
        spec3d = Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": b["kind"], "drawing_number": "D3-01"},
                "body": b,
                "provenance": [{"id": "s1", "file": "test", "locator": "L1", "confidence": 1.0}],
            }
        )
        scr = generate_autocad_3d_script(spec3d, tmp_path / f"{b['kind']}.dwg")
        sanitized = validate_3dsolid_script_allowlist(scr)
        assert any(cmd in sanitized for cmd in ("_BOX", "_CYLINDER", "_.EXTRUDE"))
        assert "_.DXFOUT\nverify3d.dxf" in sanitized

    # Comment-only script with _SAVEAS must be rejected immediately
    with pytest.raises(ValueError, match="does not create or import any 3D solid"):
        validate_3dsolid_script_allowlist('; comment only\nFILEDIA 0\n_SAVEAS 2018 "out.dwg"\n_QUIT _Y\n')

    # Empty DXF with 0 3DSOLIDs must fail verification
    empty_dxf = tmp_path / "empty_verify.dxf"
    ezdxf.new("R2018").saveas(empty_dxf)
    with pytest.raises(ValueError, match="0 3DSOLID"):
        verify_3dsolid_dxf_entities(empty_dxf)


def test_p2_1_rubric_scorer_rejects_item_120_substring_and_unevaluated_bbox_span(tmp_path):
    dxf_path = tmp_path / "only_item_120.dxf"
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 30), (0, 30)], close=True)
    msp.add_text("ITEM-120", dxfattribs={"height": 3.5}).set_placement((5, 5))
    doc.saveas(dxf_path)

    report = score_dxf_drawing(
        dxf_path,
        custom_rubric={
            "criteria": [
                {"id": "DIM-12", "description": "Must have 12 mm dimension", "weight": 40, "expected_dimension_values": [12]},
                {"id": "SPAN-10K", "description": "Width 10000", "weight": 30, "type": "bbox_span", "width": 10000},
                {"id": "UNKNOWN-RULE", "description": "Future check", "weight": 30, "type": "future_unimplemented_check"},
            ]
        },
    )
    by_id = {c.criterion_id: c for c in report.custom_criteria}
    assert by_id["DIM-12"].passed is False and by_id["DIM-12"].earned == 0.0
    assert by_id["SPAN-10K"].passed is False and by_id["SPAN-10K"].earned == 0.0
    assert by_id["UNKNOWN-RULE"].passed is False and by_id["UNKNOWN-RULE"].earned == 0.0
    assert by_id["UNKNOWN-RULE"].status == "unevaluated"


def test_p2_2_async_job_worker_chinese_utf8_pipe(tmp_path):
    sb = MCPWorkspaceSandbox(tmp_path / "ws_utf8")
    jm = AsyncCADJobManager(sb, max_workers=1)
    try:
        rec = jm.submit_job(
            tool_name="cad_compile_spec",
            arguments={"markdown_text": "# 连接法兰盘\n材料：304不锈钢\n外径：140 mm\n内孔：48±0.02 mm\n板厚：20 mm\n节圆直径：102 mm\n安装孔：4×Ø14\n"},
            domain="mechanical_2d",
        )
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            cur = jm.get_job(rec["job_id"])
            if cur["status"] in {"completed", "failed"}:
                break
            time.sleep(0.2)
        assert cur["status"] == "completed", f"Chinese async job failed: {cur}"
        assert cur["result"]["validation"]["missing"] == []
    finally:
        jm.shutdown()


def test_v2_1_1_roughness_shelf_flush_tip_view_label_above_gdt_and_leader(tmp_path):
    task_dir = tmp_path / "gear_v211"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief = task_dir / "input" / "gear.md"
    brief.write_text(
        "设计一个45钢直齿圆柱齿轮，模数3齿数28齿宽30轴孔24，内孔粗糙度Ra1.6，其余Ra6.3，同轴度0.02",
        encoding="utf-8",
    )
    compile_spec_from_files([brief], output_path=task_dir / "work" / "drawing-spec.json")
    res = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert res.status in {"passed", "partial", "complete"}
    audit = json.loads(res.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0

    dxf_path = task_dir / "output" / "drawing.dxf"
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # 1. Verify Left View label ("左视图") is strictly ABOVE the GD&T frame ("[◎|⌀0.02|A]")!
    left_lbl = next(e for e in msp if e.dxftype() == "TEXT" and str(e.dxf.text).strip() == "左视图")
    gdt_tol_txt = next(e for e in msp if e.dxftype() == "TEXT" and "0.02" in str(e.dxf.text))
    left_lbl_y = float((left_lbl.get_placement()[1] or left_lbl.dxf.insert).y)
    gdt_tol_y = float((gdt_tol_txt.get_placement()[1] or gdt_tol_txt.dxf.insert).y)
    assert left_lbl_y > gdt_tol_y + 4.0, f"左视图 ({left_lbl_y}) must sit ABOVE GD&T frame ({gdt_tol_y})!"

    # 2. Verify Ra 1.6 has 4-vertex horizontal-shelf polyline and Ra 1.6 text sits to the right of the 60-deg stroke!
    ra16_txt = next(e for e in msp if e.dxftype() == "TEXT" and str(e.dxf.text).strip() == "Ra 1.6")
    ra16_x = float((ra16_txt.get_placement()[1] or ra16_txt.dxf.insert).x)
    ra16_y = float((ra16_txt.get_placement()[1] or ra16_txt.dxf.insert).y)

    shelf_polys = [
        e for e in msp
        if e.dxftype() == "LWPOLYLINE"
        and e.dxf.layer == "DIM"
        and not e.closed
        and len(list(e.get_points())) == 4
    ]
    assert len(shelf_polys) >= 2  # general Ra 6.3 + front Ra 1.6!
    # Find the Ra 1.6 polyline closest to ra16_txt
    ra16_poly = min(shelf_polys, key=lambda pl: abs(list(pl.get_points())[2][0] - ra16_x))
    pts = [(float(p[0]), float(p[1])) for p in ra16_poly.get_points()]
    # pts = [p_left, p_tip, p_right_top, p_shelf_end]
    assert isclose(pts[2][1], pts[3][1], abs_tol=1e-4)  # horizontal GB/T 131-2006 shelf!
    assert ra16_x > pts[2][0] + 0.2  # strictly right of 60-deg stroke -> ZERO line crossing text!
    assert ra16_y < pts[2][1]        # tucked cleanly underneath horizontal shelf!

    # Verify p_tip touches an OUTLINE horizontal top edge (0.00 mm gap!)
    outline_polys = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "OUTLINE" and e.closed]
    outline_top_ys = [max(float(p[1]) for p in pl.get_points()) for pl in outline_polys]
    assert any(isclose(pts[1][1], oy, abs_tol=1e-2) for oy in outline_top_ys)

    # 3. Verify GD&T 3-point orthogonal leader polyline + SOLID arrowhead exist!
    leader_polys = [
        e for e in msp
        if e.dxftype() == "LWPOLYLINE"
        and e.dxf.layer == "DIM"
        and not e.closed
        and len(list(e.get_points())) == 3
    ]
    assert len(leader_polys) >= 1
    solids = [e for e in msp if e.dxftype() == "SOLID" and e.dxf.layer == "DIM"]
    assert len(solids) >= 2  # Datum [A] filled triangle + GD&T leader solid arrowhead!

    # 4. Verify secondary edit on Ra 1.6 ("粗糙度改成 Ra 3.2") & Web Studio pin secondary edit hooks
    nl_res = execute_nl_region_revision(
        dxf_path,
        tmp_path / "ra_edit_out",
        "粗糙度改成 Ra 3.2",
        candidate_handles=[str(ra16_txt.dxf.handle)],
    )
    assert nl_res["status"] == "applied"
    edited_doc = ezdxf.readfile(nl_res["output_path"])
    assert any(e.dxftype() == "TEXT" and str(e.dxf.text) == "Ra 3.2" for e in edited_doc.modelspace())

    studio_html = render_studio_html()
    assert "reopenSavedPin2D" in studio_html
    assert "deleteEditingPin2D" in studio_html
    assert "pinChipsBar2D" in studio_html
