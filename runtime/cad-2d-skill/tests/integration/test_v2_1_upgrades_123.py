from __future__ import annotations

import json
from math import isclose

import ezdxf
from fastapi.testclient import TestClient
import pytest

from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.mcp.http_app import create_mcp_http_app
from cad_skill_core.modeling3d import compile_model3d_from_text, run_3d_task
from cad_skill_core.pipeline import PipelineOptions, run_task


def test_upgrade_1_factory_roughness_datum_and_gdt_frames_render_collision_free(tmp_path):
    task_dir = tmp_path / "pro_gear"
    (task_dir / "input").mkdir(parents=True)
    (task_dir / "work").mkdir(parents=True)
    brief = task_dir / "input" / "brief.md"
    brief.write_text(
        "# 高精密直齿圆柱齿轮\n"
        "图号：GEAR-PRO-01\n"
        "材料：20CrMnTi\n"
        "模数：3 mm\n"
        "齿数：28\n"
        "齿宽：30 mm\n"
        "轴孔：24 mm\n"
        "技术要求：1. 配合内孔表面粗糙度 Ra0.8，其余 Ra6.3，轴孔同轴度 0.015。\n",
        encoding="utf-8",
    )

    spec = compile_spec_from_files([brief], output_path=task_dir / "work" / "drawing-spec.json")
    assert len(spec.surface_finishes) == 2
    assert any(sf.is_general and sf.value == "Ra 6.3" for sf in spec.surface_finishes)
    assert any((not sf.is_general) and sf.value == "Ra 0.8" for sf in spec.surface_finishes)
    assert len(spec.datums) == 1 and spec.datums[0].letter == "A"
    assert len(spec.gdt_frames) == 1
    assert spec.gdt_frames[0].symbol == "coaxiality"
    assert spec.gdt_frames[0].tolerance == "⌀0.015"

    res = run_task(task_dir, PipelineOptions(run_autocad=False))
    assert res.status in {"partial", "complete", "passed"}
    audit = json.loads(res.audit_path.read_text(encoding="utf-8"))
    assert audit["summary"]["errors"] == 0

    dxf_path = task_dir / "output" / "drawing.dxf"
    doc = ezdxf.readfile(dxf_path)
    texts = [str(e.dxf.text) for e in doc.modelspace() if e.dxftype() == "TEXT"]
    joined = " | ".join(texts)
    assert "其余" in joined
    assert "Ra 6.3" in joined
    assert "Ra 0.8" in joined
    assert "⌀0.015" in joined
    assert "A" in joined

    rubric = score_dxf_drawing(dxf_path, audit_json_path=res.audit_path)
    assert rubric.total_score >= 95.0
    assert rubric.grade == "S"


def test_upgrade_2_marquee_capsule_natural_language_revision_and_chaining(tmp_path):
    app = create_mcp_http_app(tmp_path / "ws_nl")
    client = TestClient(app)
    try:
        # 1. Generate a 2D flange blueprint via HTTP
        gen = client.post(
            "/api/v1/tasks/generate",
            json={
                "domain": "mechanical_2d",
                "markdown_brief": "画个45钢连接法兰盘，外径140内孔48厚20，在102节圆上均布4个14的螺栓孔",
            },
        )
        assert gen.status_code == 200
        gen_data = gen.json()
        task_id = gen_data["task_id"]

        # Fetch inventory to locate top-view bolt holes (radius = 7.0 mm) and title material text
        inv_resp = client.post("/api/v1/tasks/inventory", json={"task_id": task_id})
        assert inv_resp.status_code == 200
        entities = inv_resp.json()["entities"]
        bolt_holes = [
            e for e in entities
            if e["type"] == "CIRCLE" and e["layer"] == "OUTLINE" and isclose(float(e["geometry"]["radius"]), 7.0, abs_tol=1e-3)
        ]
        assert len(bolt_holes) == 4
        cx, cy = bolt_holes[0]["geometry"]["center"][:2]

        # 2. Pure question pin ("问题一？") does not mutate DXF and returns inspection summary
        q_resp = client.post(
            "/api/v1/tasks/revise-nl",
            json={
                "task_id": task_id,
                "prompt_text": "这个螺栓孔多大？",
                "region_box": [cx - 12, cy - 12, cx + 12, cy + 12],
                "candidate_handles": [bolt_holes[0]["handle"]],
            },
        )
        assert q_resp.status_code == 200
        assert q_resp.json()["mode"] == "question_pin"
        assert "⌀14" in q_resp.json()["summary_zh"]

        # 3. Marquee edit #1: "把框里的安装孔改成 Φ18" -> resizes all 4 bolt holes if selected, or single hole
        r1 = client.post(
            "/api/v1/tasks/revise-nl",
            json={
                "task_id": task_id,
                "prompt_text": "把框里的安装孔改成 Φ18",
                "candidate_handles": [h["handle"] for h in bolt_holes],
            },
        )
        assert r1.status_code == 200
        r1_data = r1.json()
        assert r1_data["mode"] == "revision"
        assert r1_data["action"] == "resize_circle"
        assert r1_data["rubric_score"]["total_score"] >= 90.0
        rev1_path = r1_data["output_path"]

        # Verify all 4 bolt holes now have radius == 9.0 mm (Φ18)
        doc1 = ezdxf.readfile(rev1_path)
        r9_circles = [
            e for e in doc1.modelspace()
            if e.dxftype() == "CIRCLE" and e.dxf.layer == "OUTLINE" and isclose(float(e.dxf.radius), 9.0, abs_tol=1e-3)
        ]
        assert len(r9_circles) == 4

        # 4. Chained marquee edit #2 on revised DXF: "删掉虚线" in top view
        hidden_handles = [
            e["handle"] for e in r1_data["inventory"]["entities"] if e["layer"] == "HIDDEN"
        ]
        assert len(hidden_handles) >= 2
        r2 = client.post(
            "/api/v1/tasks/revise-nl",
            json={
                "dxf_path": rev1_path,
                "prompt_text": "删掉框里的虚线",
                "candidate_handles": hidden_handles[:2],
            },
        )
        assert r2.status_code == 200
        r2_data = r2.json()
        assert r2_data["action"] == "delete_entities"
        assert len(r2_data["modified_handles"]) == 2

        # 5. Chained marquee edit #3: change material text ("把 45钢 改成 304不锈钢")
        mat_handles = [
            e["handle"] for e in r2_data["inventory"]["entities"]
            if e["type"] == "TEXT" and "45钢" in str(e["geometry"].get("text", ""))
        ]
        assert mat_handles
        r3 = client.post(
            "/api/v1/tasks/revise-nl",
            json={
                "dxf_path": r2_data["output_path"],
                "prompt_text": "把 45钢 改成 304不锈钢",
                "candidate_handles": mat_handles,
            },
        )
        assert r3.status_code == 200
        doc3 = ezdxf.readfile(r3.json()["output_path"])
        all_t3 = " | ".join(str(e.dxf.text) for e in doc3.modelspace() if e.dxftype() == "TEXT")
        assert "304不锈钢" in all_t3
    finally:
        app.state.job_manager.shutdown()


@pytest.mark.parametrize(
    ("brief_3d", "expected_kind"),
    [
        ("设计一个45钢直齿圆柱齿轮，模数3齿数24齿宽28轴孔22", "spur_gear_3d"),
        ("画一个HT200减速器轴承透盖，外径130止口直径90内孔42高度32厚12，在110节圆上均布6个9的孔", "bearing_cap_3d"),
        ("做一个Q235B带加强筋L型直角支座，底板长130底板宽84总高92厚14加强筋厚10轴承孔28安装孔径12", "ribbed_bracket_3d"),
    ],
)
def test_upgrade_3_three_new_3d_solids_watertight_step_export_and_2d_sheet(tmp_path, brief_3d, expected_kind):
    spec_3d = compile_model3d_from_text(brief_3d)
    assert spec_3d.validation.missing == []
    assert spec_3d.validation.conflicts == []
    assert spec_3d.body.kind == expected_kind

    out_dir = tmp_path / expected_kind
    bundle = run_3d_task(spec_3d, out_dir, with_2d_sheet=True)
    assert bundle["status"] == "passed", f"{expected_kind} failed 3D audit: {bundle['audit_3d']}"
    assert bundle["audit_3d"]["watertight"] is True
    assert bundle["audit_3d"]["open_boundary_edges"] == 0

    step_file = out_dir / "model.step"
    assert step_file.is_file()
    step_txt = step_file.read_text(encoding="ascii")
    assert step_txt.startswith("ISO-10303-21;")
    assert "FACETED_BREP(" in step_txt
    assert "CLOSED_SHELL(" in step_txt
    assert "SI_UNIT(.MILLI.,.METRE.)" in step_txt

    sheet_dxf = out_dir / "sheet2d.dxf"
    assert sheet_dxf.is_file()
    assert bundle["sheet_2d"]["audit_summary"]["errors"] == 0
