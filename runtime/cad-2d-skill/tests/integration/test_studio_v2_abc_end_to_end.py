from __future__ import annotations

import base64

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.mcp import create_mcp_http_app


def test_studio_v2_upload_revise_diff_and_conversational_3d_end_to_end(tmp_path):
    app = create_mcp_http_app(tmp_path / "studio_v2_ws")
    client = TestClient(app)
    try:
        # 1. Verify Studio 2.0 HTML shell exposes all 5 workflow tabs & interactive canvases
        html = client.get("/studio").text
        assert "CAD Operations Studio 2.0" in html
        assert "上传 DXF 局部改图与对比" in html
        assert "parseOBJWithCreaseEdges" in html

        # 2. Conversational 3D+2D generation + live /cad-scene vector extraction (Step A + Step C)
        gen3d = client.post(
            "/api/v1/tasks/generate",
            json={
                "domain": "model_3d",
                "markdown_brief": "做一个304不锈钢带颈法兰，外径145内孔46底盘厚18颈径74颈高22，在108的节圆上均布6个12的孔",
                "with_2d_sheet": True,
            },
        )
        assert gen3d.status_code == 200, gen3d.text
        g3_data = gen3d.json()
        assert g3_data["audit_3d"]["watertight"] is True
        assert g3_data["sheet_2d"]["audit_summary"]["errors"] == 0

        scene_resp = client.get(f"/api/v1/tasks/{g3_data['task_id']}/cad-scene")
        assert scene_resp.status_code == 200
        scene = scene_resp.json()
        assert scene["primitive_count"] > 20
        assert any(l["name"] == "OUTLINE" for l in scene["layers"])

        # 3. Upload existing DXF -> Inventory -> Dry-Run -> Commit Revision -> Before/After Diff (Step B)
        base_dxf = tmp_path / "editable.dxf"
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = 4
        doc.layers.add("OUTLINE", color=7)
        line_ent = doc.modelspace().add_line((0, 0), (50, 0), dxfattribs={"layer": "OUTLINE"})
        doc.modelspace().add_circle((25, 25), 10.0, dxfattribs={"layer": "OUTLINE"})
        doc.saveas(base_dxf)

        up = client.post(
            "/api/v1/uploads",
            json={
                "filename": "editable.dxf",
                "content_base64": base64.b64encode(base_dxf.read_bytes()).decode("ascii"),
            },
        )
        assert up.status_code == 200
        up_data = up.json()
        inv = up_data["inventory"]
        line_handle = str(line_ent.dxf.handle)
        assert any(e["handle"] == line_handle for e in inv["entities"])

        plan = {
            "schema": "cad-revision/v1",
            "baseline_sha256": inv["sha256"],
            "units": inv["units"],
            "tolerance": 1e-4,
            "operations": [
                {
                    "id": "op-len",
                    "type": "set_length",
                    "target": {"handle": line_handle},
                    "length": 85.0,
                    "fixed": "start",
                }
            ],
        }

        # Dry-run first
        dry = client.post(
            "/api/v1/tasks/revise",
            json={"dxf_path": up_data["uploaded_path"], "plan": plan, "dry_run": True},
        )
        assert dry.status_code == 200
        assert dry.json()["dry_run"] is True

        # Commit revision
        rev = client.post(
            "/api/v1/tasks/revise",
            json={"dxf_path": up_data["uploaded_path"], "plan": plan, "dry_run": False},
        )
        assert rev.status_code == 200
        rev_dxf = next(a for a in rev.json()["artifacts"] if a["name"].endswith(".dxf"))

        # Generate Before/After Diff views
        diff = client.post(
            "/api/v1/tasks/diff",
            json={
                "before_dxf_path": up_data["uploaded_path"],
                "after_dxf_path": rev_dxf["path"],
            },
        )
        assert diff.status_code == 200
        diff_arts = diff.json()["artifacts"]
        assert any(a["name"].endswith(".png") for a in diff_arts)
    finally:
        app.state.job_manager.shutdown()
