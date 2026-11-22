from __future__ import annotations

import ezdxf
from fastapi.testclient import TestClient
import pytest

from cad_skill_core.mcp import (
    DXFBombError,
    MCPWorkspaceSandbox,
    create_mcp_http_app,
    inspect_dxf_safety_limits,
)


def test_api_key_authentication_allows_public_shell_and_guards_mcp_and_api_routes(tmp_path):
    app = create_mcp_http_app(
        tmp_path / "sec_ws",
        api_key="cad-prod-secret-2026",
        cors_origins=["http://localhost:3000"],
    )
    client = TestClient(app)
    try:
        # 1. Public endpoints remain accessible without credentials
        h = client.get("/api/v1/health")
        assert h.status_code == 200
        assert h.json()["auth_required"] is True

        studio = client.get("/studio")
        assert studio.status_code == 200
        assert "智能自动出图工作台" in studio.text

        # 2. CORS preflight OPTIONS passes without token and emits Access-Control-Allow-Origin
        preflight = client.options(
            "/api/v1/tasks/generate",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3000"

        # 3. Protected routes reject unauthenticated or wrong token with 401
        assert client.get("/api/v1/jobs").status_code == 401
        assert (
            client.get("/api/v1/jobs", headers={"Authorization": "Bearer wrong-key"}).status_code
            == 401
        )
        assert (
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).status_code
            == 401
        )

        # 4. Valid Authorization: Bearer <token> and X-API-Key both succeed
        ok_bearer = client.get(
            "/api/v1/jobs",
            headers={"Authorization": "Bearer cad-prod-secret-2026"},
        )
        assert ok_bearer.status_code == 200

        ok_header = client.post(
            "/mcp",
            headers={"X-API-Key": "cad-prod-secret-2026"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert ok_header.status_code == 200
        assert len(ok_header.json()["result"]["tools"]) == 20
    finally:
        app.state.job_manager.shutdown()


def test_dxf_bomb_entity_count_and_astronomical_coordinate_guards(tmp_path):
    ws = tmp_path / "bomb_ws"
    uploads = ws / "uploads"
    uploads.mkdir(parents=True)

    # 1. Normal DXF passes
    safe_dxf = uploads / "safe.dxf"
    doc_ok = ezdxf.new("R2018")
    for idx in range(5):
        doc_ok.modelspace().add_line((0, idx), (50, idx))
    doc_ok.saveas(safe_dxf)

    stats = inspect_dxf_safety_limits(safe_dxf, max_entities=20)
    assert stats["entity_markers"] == 5

    # 2. Entity count overflow raises DXFBombError via sandbox.resolve_readable_file
    many_dxf = uploads / "too_many.dxf"
    doc_many = ezdxf.new("R2018")
    for idx in range(30):
        doc_many.modelspace().add_circle((idx * 2.0, 0.0), 1.0)
    doc_many.saveas(many_dxf)

    strict_sb = MCPWorkspaceSandbox(ws, max_dxf_entities=15)
    assert strict_sb.resolve_readable_file(safe_dxf) == safe_dxf.resolve()
    with pytest.raises(DXFBombError, match="entity count exceeds limit"):
        strict_sb.resolve_readable_file(many_dxf)

    # 3. Astronomical coordinate (> 1e8) raises DXFBombError
    huge_coord_dxf = uploads / "huge_coord.dxf"
    doc_huge = ezdxf.new("R2018")
    doc_huge.modelspace().add_line((0.0, 0.0), (5.0e9, 10.0))
    doc_huge.saveas(huge_coord_dxf)

    with pytest.raises(DXFBombError, match="magnitude"):
        strict_sb.resolve_readable_file(huge_coord_dxf)
