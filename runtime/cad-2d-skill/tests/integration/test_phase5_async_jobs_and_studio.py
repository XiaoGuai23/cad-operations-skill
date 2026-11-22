from __future__ import annotations

import time

from fastapi.testclient import TestClient

from cad_skill_core.mcp import create_mcp_http_app


def test_studio_ui_drivers_and_concurrent_subprocess_async_jobs(tmp_path):
    app = create_mcp_http_app(tmp_path / "p5_ws", max_workers=2)
    client = TestClient(app)
    try:
        # 1. Root & /studio serve the Chinese Auto-Drafting Web Studio
        root_html = client.get("/")
        assert root_html.status_code == 200
        assert "CAD Operations Skill" in root_html.text
        assert "viewer3d" in root_html.text

        # 2. /api/v1/drivers returns multi-vendor probe matrix
        drv_resp = client.get("/api/v1/drivers")
        assert drv_resp.status_code == 200
        assert set(drv_resp.json()["drivers"].keys()) == {
            "autocad",
            "zwcad",
            "gstarcad",
            "caxa",
            "oda",
        }

        # 3. Submit TWO concurrent async jobs running in isolated worker subprocesses
        job_2d = client.post(
            "/api/v1/jobs",
            json={
                "domain": "mechanical_2d",
                "markdown_brief": (
                    "# 法兰盖\n"
                    "图号：ASYNC-2D-01\n"
                    "材料：Q235B\n"
                    "外径：120 mm\n"
                    "内孔：40 mm\n"
                    "厚度：16 mm\n"
                ),
            },
        )
        assert job_2d.status_code == 202
        job_2d_id = job_2d.json()["job_id"]

        job_3d = client.post(
            "/api/v1/jobs",
            json={
                "domain": "model_3d",
                "spec": {
                    "schema_version": "1.0",
                    "project": {"name": "异步三维轴套", "drawing_number": "ASYNC-3D-01"},
                    "material": {"text": "45钢", "source_id": "s1"},
                    "body": {
                        "kind": "cylinder",
                        "outer_diameter": 60.0,
                        "bore_diameter": 24.0,
                        "height": 45.0,
                        "source_id": "s1",
                    },
                    "provenance": [{"id": "s1", "file": "web", "locator": "L1", "confidence": 1.0}],
                },
                "with_2d_sheet": False,
            },
        )
        assert job_3d.status_code == 202
        job_3d_id = job_3d.json()["job_id"]

        # Poll both jobs until terminal state (isolated subprocesses typically finish in ~3-12s)
        deadline = time.monotonic() + 90.0
        final_2d = None
        final_3d = None
        while time.monotonic() < deadline:
            r2 = client.get(f"/api/v1/jobs/{job_2d_id}").json()
            r3 = client.get(f"/api/v1/jobs/{job_3d_id}").json()
            if r2["status"] in {"completed", "failed", "cancelled"}:
                final_2d = r2
            if r3["status"] in {"completed", "failed", "cancelled"}:
                final_3d = r3
            if final_2d is not None and final_3d is not None:
                break
            time.sleep(0.4)

        assert final_2d is not None and final_2d["status"] == "completed", final_2d
        assert final_3d is not None and final_3d["status"] == "completed", final_3d
        assert final_3d["result"]["audit_3d"]["watertight"] is True

        # 4. List jobs & download artifacts produced by subprocess workers
        listed = client.get("/api/v1/jobs").json()["jobs"]
        listed_ids = {j["job_id"] for j in listed}
        assert {job_2d_id, job_3d_id} <= listed_ids

        dxf_art = next(a for a in final_2d["result"]["artifacts"] if a["name"] == "drawing.dxf")
        assert client.get(dxf_art["download_url"]).status_code == 200

        glb_art = next(a for a in final_3d["result"]["artifacts"] if a["name"] == "model.glb")
        glb_resp = client.get(glb_art["download_url"])
        assert glb_resp.status_code == 200
        assert glb_resp.content[:4] == b"glTF"

        # 5. Calling cancel on an already completed job is idempotent
        cancel_done = client.post(f"/api/v1/jobs/{job_3d_id}/cancel")
        assert cancel_done.status_code == 200
        assert cancel_done.json()["status"] == "completed"
    finally:
        app.state.job_manager.shutdown()
