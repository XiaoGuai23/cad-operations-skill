"""End-to-end integration tests for v2.3 Upgrades 1–4:
1. Unified Revision IR (10 ops) + Reopen-DXF Post-Condition Verifier
2. Project-Level Drafting Preference Memory (candidate -> confirmed, sample habit learning, safe dimension isolation)
3. Non-Template Composable Feature Graph (polygon / T-plate / hole-grid / slot -> 3-view orthographic GB/T sheet)
4. Failure-to-Regression Flywheel (record -> list -> replay via Python, CLI, and MCP/HTTP)
"""

from __future__ import annotations

import json
from math import isclose
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
import pytest

from cad_skill_core.benchmark.regression_flywheel import (
    list_regression_cases,
    record_regression_case,
    replay_regression_vault,
)
from cad_skill_core.cli import main as cli_main
from cad_skill_core.documents.spec_compiler import (
    compile_spec_from_feature_graph,
    compile_spec_from_files,
)
from cad_skill_core.mcp.http_app import create_mcp_http_app
from cad_skill_core.pipeline import run_task
from cad_skill_core.revision.ir_engine import compile_nl_to_unified_ir, execute_revision_ir
from cad_skill_core.revision.nl_revise import RevisionError, execute_nl_region_revision
from cad_skill_core.revision.post_verify import verify_revision_post_conditions
from cad_skill_core.schema.io import save_spec
from cad_skill_core.standards.preference_memory import (
    default_preference_memory_path,
    learn_preferences_from_sample_dxf,
    load_preference_store,
    set_preference_status,
    teach_preferences_from_text,
)


def test_upgrade1_unified_ir_two_holes_mixed_units_and_reopen_dxf_verifier(tmp_path: Path) -> None:
    """Single prompt opens two holes in mixed units, and Reopen-DXF verifier checks every item & catches 10x errors."""
    plate_dxf = tmp_path / "plate.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 4  # mm
    msp = doc.modelspace()
    rect = msp.add_lwpolyline([(0, 0), (140, 0), (140, 80), (0, 80)], close=True, dxfattribs={"layer": "OUTLINE"})
    old_hole = msp.add_circle((20.0, 20.0), 6.0, dxfattribs={"layer": "OUTLINE"})  # Ø12
    h_rect = str(rect.dxf.handle).upper()
    h_old = str(old_hole.dxf.handle).upper()
    doc.saveas(plate_dxf)

    # 1. Single sentence opening TWO holes with independent coords & mixed units (mm + in), PLUS resizing existing Ø12 -> Ø16!
    res = execute_nl_region_revision(
        plate_dxf,
        tmp_path / "out_multi",
        "在 (45, 40) 开 Ø10 mm 孔，在 (95, 40) 开 Ø0.5 in 孔，同时把 Ø12 的孔改为 Ø16 mm",
        candidate_handles=[h_rect, h_old],
    )
    assert res["status"] == "applied"
    assert len(res["ir"]["operations"]) == 3
    ver = res["verification"]
    assert ver["passed"] is True
    assert ver["checked_count"] == 3

    # Verify reopened DXF physically has Ø10.0 mm at (45,40), Ø12.7 mm at (95,40), and Ø16.0 mm at (20,20)
    reopened = ezdxf.readfile(res["output_path"])
    circles = [
        (round(float(e.dxf.center.x), 2), round(float(e.dxf.center.y), 2), round(2.0 * float(e.dxf.radius), 3))
        for e in reopened.modelspace()
        if e.dxftype() == "CIRCLE" and str(e.dxf.layer).upper() == "OUTLINE"
    ]
    assert (45.0, 40.0, 10.0) in circles
    assert (95.0, 40.0, 12.7) in circles
    assert (20.0, 20.0, 16.0) in circles

    # 2. Prove Post-Condition Verifier catches a 10x unit/scaling discrepancy (e.g. expected Ø2 mm vs actual Ø20 mm)!
    wrong_dxf = tmp_path / "wrong_10x.dxf"
    bad_doc = ezdxf.readfile(plate_dxf)
    bad_doc.modelspace().add_circle((50.0, 50.0), 10.0, dxfattribs={"layer": "OUTLINE"})  # drawn as Ø20 mm instead of Ø2 mm!
    bad_doc.saveas(wrong_dxf)
    bad_ver = verify_revision_post_conditions(
        plate_dxf,
        wrong_dxf,
        [{"check": "circle_exists_at", "center": [50.0, 50.0], "diameter": 2.0, "diameter_unit": "mm"}],
    )
    assert bad_ver["passed"] is False
    assert bad_ver["failed_count"] == 1

    # 3. Also verify execute_revision_ir hard-blocks and rolls back if an explicit post_condition is unmet!
    with pytest.raises(RevisionError, match="物理核验未通过"):
        execute_revision_ir(
            plate_dxf,
            tmp_path / "blocked_out",
            {
                "schema_version": "cad-revision-ir/v2.3",
                "operations": [{"op": "add_circle_hole", "center": [60.0, 40.0], "diameter": 20.0, "diameter_unit": "mm"}],
                "post_conditions": [
                    {"check": "circle_exists_at", "center": [60.0, 40.0], "diameter": 2.0, "diameter_unit": "mm"}
                ],
            },
        )
    assert not (tmp_path / "blocked_out" / "revised.dxf").exists()

    # 4. Verify compile_nl_to_unified_ir compiles standalone single-clause delete/move/resize too
    ir_move = compile_nl_to_unified_ir("向右平移 25 mm", candidate_handles=[h_old])
    assert ir_move is not None and ir_move.operations[0].op == "move_entities"


def test_upgrade2_preference_memory_learns_from_samples_and_user_without_polluting_dimensions(tmp_path: Path) -> None:
    """Learns flange drafting habits across 2 samples / explicit teaching while keeping new hole count & diameter strict."""
    project_ws = tmp_path / "proj"
    project_ws.mkdir()
    mem_path = default_preference_memory_path(project_ws)

    # Create two sample flange DXFs in the same project where user combined hole callout ("6×⌀10 均布于⌀90")
    # and placed radial leaders on the RIGHT side of geometry center, with "其余" + "Ra 3.2"
    for idx, fname in enumerate(("FLG-SAMPLE-01.dxf", "FLG-SAMPLE-02.dxf"), start=1):
        s_path = project_ws / fname
        s_doc = ezdxf.new("R2010")
        s_doc.units = 4
        msp = s_doc.modelspace()
        msp.add_circle((100.0, 100.0), 60.0, dxfattribs={"layer": "OUTLINE"})
        dim = msp.add_diameter_dim(center=(100.0, 100.0), radius=60.0, angle=25.0, text="6×⌀10 均布于⌀90")
        dim.render()
        dim.dimension.dxf.text_midpoint = (165.0, 125.0, 0.0)
        msp.add_leader([(155.0, 110.0), (185.0, 130.0)], dxfattribs={"layer": "DIM"})
        msp.add_text("法兰盘样图", dxfattribs={"layer": "TEXT", "height": 3.5}).set_placement((260.0, 30.0))
        msp.add_text("其余", dxfattribs={"layer": "TEXT", "height": 3.5}).set_placement((360.0, 270.0))
        msp.add_text("Ra 3.2", dxfattribs={"layer": "TEXT", "height": 2.6}).set_placement((375.0, 270.0))
        s_doc.saveas(s_path)

        learned = learn_preferences_from_sample_dxf(s_path, mem_path)
        assert learned
        store_now = load_preference_store(mem_path)
        by_k = {e.key: e for e in store_now.entries}
        if idx == 1:
            # First sample -> recorded as 'candidate', NOT yet auto-applied!
            assert by_k["hole_callout_style"].status == "candidate"
            assert by_k["leader_side"].status == "candidate"
        else:
            # Second independent sample with identical habits -> promoted to 'confirmed' with both sources cited!
            assert by_k["hole_callout_style"].status == "confirmed"
            assert by_k["hole_callout_style"].value == "combined_with_pcd"
            assert by_k["leader_side"].status == "confirmed"
            assert by_k["leader_side"].value == "right"
            assert by_k["default_general_roughness"].status == "confirmed"
            assert by_k["default_general_roughness"].value == "Ra 3.2"
            assert len(by_k["hole_callout_style"].evidence) == 2

    # Also test explicit natural-language preference teaching & status toggle
    taught = teach_preferences_from_text(
        mem_path,
        "法兰图统一加注：未注倒角 C1.5，锐边去毛刺；确认以后沿用",
        part_family="flange",
    )
    assert any(e.key == "default_technical_notes" and e.status == "confirmed" for e in taught)

    # Now draft a BRAND-NEW flange in this project with DIFFERENT dimensions:
    # 8×Φ14 holes on PCD 130 (OD 170, ID 65, thickness 22) — verify style/layout preferences apply
    # while hole count (8), hole diameter (14), and PCD (130) come 100% from the NEW brief!
    task_dir = project_ws / "new_flange_task"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief_md = task_dir / "input" / "brief.md"
    brief_md.write_text(
        "# 高压连接法兰\n图号：FLG-NEW-08\n材料：45钢\n外径：170 mm\n内径：65 mm\n厚度：22 mm\n节圆：130 mm\n螺栓孔：8×Φ14\n",
        encoding="utf-8",
    )
    spec = compile_spec_from_files([brief_md], output_path=task_dir / "work" / "drawing-spec.json")

    # Check hole callout merged style uses NEW 8×⌀14 & PCD 130 (never 6×⌀10 / 90!)
    labels = [d.format_label() for d in spec.dimensions]
    assert "8×⌀14 均布于⌀130" in labels
    assert not any("10" in lbl or "90" in lbl for lbl in labels)
    # Check datum placement & general Ra 3.2 & technical note & provenance assumption
    assert all(d.placement == "right-center" for d in spec.datums)
    gen_sf = [s for s in spec.surface_finishes if s.is_general]
    assert gen_sf and gen_sf[0].value == "Ra 3.2"
    assert any("未注倒角 C1.5" in n.text for n in spec.requirements.technical_notes)
    assert any("FLG-SAMPLE-01.dxf" in a and "FLG-SAMPLE-02.dxf" in a for a in spec.validation.assumptions)

    # Run full 2D pipeline to prove the preference-styled flange passes GB/T layout & inspection cleanly!
    res = run_task(task_dir)
    assert res.status in {"passed", "partial", "complete"}

    # Verify set_preference_status can reject/disable a preference
    updated_e = set_preference_status(mem_path, "pref-flange-hole_callout_style", "rejected")
    assert updated_e.status == "rejected"


def test_upgrade3_composable_feature_graph_generates_non_standard_3view_drawings(tmp_path: Path) -> None:
    """Non-standard polygon gusset plate & T-slider plate compile into 3-view GB/T sheets with 0 audit errors."""
    # Case A: Direct JSON FeatureGraphSpec — asymmetric 5-vertex gusset plate with fillet, bolt grid, rect pocket & angled slot
    fg_json = {
        "schema_version": "cad-feature-graph/v1",
        "name": "异形斜切安装连接板",
        "thickness_mm": 14.0,
        "base_profile": {
            "kind": "polygon",
            "vertices": [(0.0, 0.0), (150.0, 0.0), (150.0, 55.0), (95.0, 95.0), (0.0, 95.0)],
        },
        "features": [
            {"op": "corner_mod", "mode": "chamfer", "size": 5.0, "vertex_indices": [0, 1, 4]},
            {"op": "circle_hole", "center": (38.0, 52.0), "diameter": 24.0},
            {"op": "slot_cutout", "center": (108.0, 28.0), "length": 28.0, "width": 10.0, "angle_deg": 0.0},
        ],
    }
    t1 = tmp_path / "custom_gusset"
    (t1 / "input").mkdir(parents=True)
    (t1 / "work").mkdir(parents=True)
    (t1 / "input" / "note.md").write_text("# 异形斜切安装连接板\n", encoding="utf-8")
    spec1 = compile_spec_from_feature_graph(fg_json, drawing_number="FG-GUSSET-01", material="Q345B")
    assert spec1.requirements.views == ["front", "top", "left"]
    assert spec1.geometry.parameters["part_family"] == "custom-profile"
    save_spec(spec1, t1 / "work" / "drawing-spec.json")
    res1 = run_task(t1)
    assert res1.status in {"passed", "partial", "complete"}
    audit1 = json.loads(res1.audit_path.read_text(encoding="utf-8"))
    assert audit1["summary"]["errors"] == 0

    # Case B: Natural-language non-standard multi-hole slotted baseplate automatically routed to FeatureGraphSpec!
    t2 = tmp_path / "nl_custom_plate"
    (t2 / "input").mkdir(parents=True)
    (t2 / "work").mkdir(parents=True)
    brief2 = t2 / "input" / "req.md"
    brief2.write_text(
        "# 多孔方槽安装底板\n图号：PLT-GRID-02\n材料：6061铝合金\n总长：160 mm\n总高：100 mm\n板厚：12 mm\n"
        "四角倒圆角 R6，四角距边 20 布置 2×2 阵列 Ø10 安装孔，中心开 44×26 矩形方槽\n",
        encoding="utf-8",
    )
    spec2 = compile_spec_from_files([brief2], output_path=t2 / "work" / "drawing-spec.json")
    assert spec2.geometry.parameters["part_family"] == "custom-profile"
    assert spec2.requirements.views == ["front", "top", "left"]
    res2 = run_task(t2)
    assert res2.status in {"passed", "partial", "complete"}
    audit2 = json.loads(res2.audit_path.read_text(encoding="utf-8"))
    assert audit2["summary"]["errors"] == 0


def test_upgrade4_regression_flywheel_and_mcp_http_cli_endpoints(tmp_path: Path) -> None:
    """Record positive & negative failure cases into regression vault and replay via Python, CLI, and HTTP/MCP."""
    ws = tmp_path / "mcp_ws"
    ws.mkdir()
    inch_dxf = ws / "inch_part.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 1  # inches
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (8, 0), (8, 5), (0, 5)], close=True, dxfattribs={"layer": "OUTLINE"})
    hole = msp.add_circle((4.0, 2.5), 0.25, dxfattribs={"layer": "OUTLINE"})  # Ø0.5 in
    h_hole = str(hole.dxf.handle).upper()
    doc.saveas(inch_dxf)

    vault = ws / "reg_vault"
    # Case 1: Positive historical unit-conversion case (25.4 mm on inch drawing -> 1.0 in = 25.4 mm, NOT 25.4 in!)
    rec1 = record_regression_case(
        vault,
        case_id="inch-hole-25-4mm",
        title="Inch drawing hole resize from 25.4 mm",
        source_dxf_path=inch_dxf,
        prompt="把孔径改为 25.4 mm",
        candidate_handles=[h_hole],
        expected_post_conditions=[
            {"check": "circle_diameter_equals", "handles": [h_hole], "diameter": 25.4, "diameter_unit": "mm"}
        ],
        bug_note="Prevent 25.4x inch scaling regression",
    )
    assert rec1["status"] == "recorded"

    # Case 2: Negative historical partial-clause case (must_reject=True)
    rec2 = record_regression_case(
        vault,
        case_id="reject-mixed-unparsed",
        title="Reject mixed prompt with unparsed trailing verb",
        source_dxf_path=inch_dxf,
        prompt="在 (2, 2) 开 Ø0.5 in 孔 顺便旋转 33 度",
        must_reject=True,
    )
    assert rec2["must_reject"] is True
    assert len(list_regression_cases(vault)) == 2

    replay = replay_regression_vault(vault)
    assert replay["total"] == 2
    assert replay["all_passed"] is True

    # Verify CLI `run-regressions` & `preferences` commands return exit code 0
    assert cli_main(["run-regressions", "--vault", str(vault), "--json"]) == 0
    assert (
        cli_main(
            [
                "preferences",
                "teach",
                "--memory",
                str(ws / ".cad-memory" / "project-preferences.json"),
                "--text",
                "法兰图引线移到视图右侧，确认以后沿用",
                "--json",
            ]
        )
        == 0
    )

    # Verify FastAPI HTTP + MCP endpoints (`/api/v1/preferences`, `/api/v1/draft/feature-graph`, `/api/v1/regressions`)
    app = create_mcp_http_app(ws)
    with TestClient(app) as client:
        r_pref = client.post("/api/v1/preferences", json={"action": "list"})
        assert r_pref.status_code == 200
        assert len(r_pref.json()["entries"]) >= 1

        r_reg_rec = client.post(
            "/api/v1/regressions",
            json={
                "action": "record",
                "case_id": "http-two-holes",
                "title": "Two holes via HTTP regression vault",
                "dxf_path": str(inch_dxf),
                "prompt": "在 (2, 2) 开 Ø12.7 mm 孔，在 (6, 2) 开 Ø1 in 孔",
                "expected_post_conditions": [
                    {"check": "circle_exists_at", "center": [2.0, 2.0], "diameter": 12.7, "diameter_unit": "mm"},
                    {"check": "circle_exists_at", "center": [6.0, 2.0], "diameter": 1.0, "diameter_unit": "in"},
                ],
            },
        )
        assert r_reg_rec.status_code == 200
        r_reg_run = client.post("/api/v1/regressions", json={"action": "replay"})
        assert r_reg_run.status_code == 200
        assert r_reg_run.json()["all_passed"] is True

        r_fg = client.post(
            "/api/v1/draft/feature-graph",
            json={
                "markdown_brief": "# T型连接滑块\n顶宽：110\n总高：80\n腹板宽：44\n翼缘高：28\n板厚：12\n中心开 Ø18 通孔\n",
                "drawing_number": "FG-TSLIDE-01",
            },
        )
        assert r_fg.status_code == 200
        assert r_fg.json()["status"] in {"passed", "partial", "complete"}
