from __future__ import annotations

import json

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.cli import main as cli_main
from cad_skill_core.mcp import create_mcp_http_app


def test_cli_drivers_and_mcp_list_drivers_live_readonly_probe(tmp_path, capsys):
    # 1. CLI `cad-skill drivers --json` (read-only static signature & registry probe)
    rc = cli_main(["drivers", "--json"])
    assert rc == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["command"] == "drivers"
    assert set(cli_payload["drivers"].keys()) == {"autocad", "zwcad", "gstarcad", "caxa", "oda"}
    for drv_info in cli_payload["drivers"].values():
        assert drv_info["status"] in {"available", "not-installed", "untrusted", "inaccessible", "wrong-version"}

    # 2. MCP `cad_list_drivers` + `cad_verify_roundtrip` over HTTP
    dxf_a = tmp_path / "mcp_ws" / "uploads" / "before.dxf"
    dxf_b = tmp_path / "mcp_ws" / "uploads" / "after.dxf"
    dxf_a.parent.mkdir(parents=True, exist_ok=True)
    for p in (dxf_a, dxf_b):
        doc = ezdxf.new("R2018")
        doc.layers.add("OUTLINE", color=7)
        doc.modelspace().add_circle((0, 0), 25.0, dxfattribs={"layer": "OUTLINE"})
        doc.saveas(p)

    app = create_mcp_http_app(tmp_path / "mcp_ws")
    client = TestClient(app)

    drv_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cad_list_drivers", "arguments": {"allowed_autocad_versions": ["2023", "2024"]}},
        },
    ).json()["result"]
    assert drv_resp["isError"] is False
    assert "gstarcad" in drv_resp["structuredContent"]["drivers"]
    assert "zwcad" in drv_resp["structuredContent"]["drivers"]

    rt_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "cad_verify_roundtrip",
                "arguments": {
                    "before_dxf_path": str(dxf_a),
                    "after_dxf_path": str(dxf_b),
                },
            },
        },
    ).json()["result"]
    assert rt_resp["isError"] is False
    assert rt_resp["structuredContent"]["status"] == "passed"

    # CLI verify-roundtrip exit code 0 on pass and 1 on lost entity
    assert cli_main(["verify-roundtrip", str(dxf_a), str(dxf_b), "--json"]) == 0
    capsys.readouterr()

    doc_bad = ezdxf.new("R2018")
    doc_bad.saveas(dxf_b)
    assert cli_main(["verify-roundtrip", str(dxf_a), str(dxf_b), "--json"]) == 1
    capsys.readouterr()
