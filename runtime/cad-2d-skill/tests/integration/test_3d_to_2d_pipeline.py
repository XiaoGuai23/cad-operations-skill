from __future__ import annotations

import json

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.cli import main as cli_main
from cad_skill_core.mcp import create_mcp_http_app


def test_cli_and_mcp_3d_to_2d_coupled_pipeline_end_to_end(tmp_path):
    spec_3d_dict = {
        "schema_version": "1.0",
        "project": {"name": "带颈连接法兰", "drawing_number": "FLG-3D2D-01"},
        "material": {"text": "45钢", "source_id": "src"},
        "technical_notes": [{"text": "未注倒角 C1.5，去毛刺。", "source_id": "src"}],
        "body": {
            "kind": "flange_3d",
            "outer_diameter": 140.0,
            "bore_diameter": 46.0,
            "thickness": 18.0,
            "hub_diameter": 72.0,
            "hub_height": 20.0,
            "bolt_circle_diameter": 104.0,
            "hole_diameter": 12.0,
            "hole_count": 4,
            "source_id": "src",
        },
        "provenance": [{"id": "src", "file": "brief.md", "locator": "line 1", "confidence": 1.0}],
    }
    spec_path = tmp_path / "flange3d.json"
    spec_path.write_text(json.dumps(spec_3d_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    # 1. CLI validate-3d-spec + generate-3d --with-2d-sheet
    assert cli_main(["validate-3d-spec", str(spec_path), "--json"]) == 0
    cli_out = tmp_path / "cli_3d_out"
    assert cli_main(["generate-3d", str(spec_path), "--out", str(cli_out), "--with-2d-sheet", "--json"]) == 0

    for fname in (
        "model.stl",
        "model.obj",
        "model.glb",
        "model3d.dxf",
        "iso.png",
        "audit-3d.json",
        "audit-3d-report.md",
        "sheet2d.dxf",
        "sheet2d.png",
        "sheet2d-audit.json",
    ):
        assert (cli_out / fname).is_file() and (cli_out / fname).stat().st_size > 0

    audit3d = json.loads((cli_out / "audit-3d.json").read_text(encoding="utf-8"))
    assert audit3d["status"] == "passed"
    assert audit3d["watertight"] is True

    audit2d = json.loads((cli_out / "sheet2d-audit.json").read_text(encoding="utf-8"))
    assert audit2d["summary"]["errors"] == 0
    assert not ezdxf.readfile(cli_out / "sheet2d.dxf").audit().has_errors

    # 2. MCP /mcp tool call + Web API /api/v1/tasks/generate (domain='model_3d') + GLB download
    app = create_mcp_http_app(tmp_path / "mcp_3d_ws")
    client = TestClient(app)

    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "cad_generate_3d",
                "arguments": {
                    "spec": spec_3d_dict,
                    "with_2d_sheet": False,
                    "include_image_base64": True,
                },
            },
        },
    ).json()["result"]
    assert mcp_resp["isError"] is False
    assert any(c["type"] == "image" and c["mimeType"] == "image/png" for c in mcp_resp["content"])

    web_resp = client.post(
        "/api/v1/tasks/generate",
        json={"domain": "model_3d", "spec": spec_3d_dict},
    )
    assert web_resp.status_code == 200
    glb_art = next(a for a in web_resp.json()["artifacts"] if a["name"] == "model.glb")
    glb_dl = client.get(glb_art["download_url"])
    assert glb_dl.status_code == 200
    assert glb_dl.headers["content-type"].startswith("model/gltf-binary")
    assert glb_dl.content[:4] == b"glTF"


def test_project_all_3d_body_kinds_pass_2d_layout_and_orthographic_audit(tmp_path):
    from cad_skill_core.modeling3d import Model3DSpec, project_model3d_to_drawing_spec, run_3d_task

    bodies = [
        {
            "kind": "box",
            "width": 100.0,
            "depth": 60.0,
            "height": 30.0,
            "through_holes": [[-25.0, 0.0, 12.0], [25.0, 0.0, 12.0]],
            "source_id": "src",
        },
        {
            "kind": "cylinder",
            "outer_diameter": 60.0,
            "bore_diameter": 24.0,
            "height": 50.0,
            "source_id": "src",
        },
        {
            "kind": "extrude_polygon",
            "outer_loop": [[0.0, 0.0], [80.0, 0.0], [80.0, 40.0], [40.0, 40.0], [40.0, 60.0], [0.0, 60.0]],
            "inner_loops": [[[12.0, 10.0], [28.0, 10.0], [28.0, 26.0], [12.0, 26.0]]],
            "height": 20.0,
            "source_id": "src",
        },
        {
            "kind": "stepped_shaft_3d",
            "steps": [[30.0, 24.0], [45.0, 38.0], [25.0, 20.0]],
            "bore_diameter": 10.0,
            "source_id": "src",
        },
        {
            "kind": "slotted_bracket_3d",
            "width": 120.0,
            "height": 70.0,
            "thickness": 12.0,
            "corner_radius": 8.0,
            "slot_length": 44.0,
            "slot_width": 14.0,
            "hole_diameter": 8.0,
            "hole_inset": 14.0,
            "source_id": "src",
        },
    ]

    for idx, body in enumerate(bodies):
        spec_3d = Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": f"三维投影测试件{idx}", "drawing_number": f"PRJ-3D-{idx:02d}"},
                "material": {"text": "Q235B", "source_id": "src"},
                "body": body,
                "provenance": [{"id": "src", "file": "brief.md", "locator": "line 1", "confidence": 1.0}],
            }
        )
        spec_2d = project_model3d_to_drawing_spec(spec_3d)
        assert len(spec_2d.requirements.views) >= 2
        res = run_3d_task(spec_3d, tmp_path / f"kind_{idx}", with_2d_sheet=True, circle_segments=36)
        assert res["status"] == "passed", f"{body['kind']} failed: {res}"
        assert res["audit_3d"]["watertight"] is True
        assert res["sheet_2d"]["audit_summary"]["errors"] == 0

