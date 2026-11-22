from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.mcp import create_mcp_http_app
from tests.helpers import RUNTIME_ROOT

REPO_ROOT = RUNTIME_ROOT.parents[1]


def test_mcp_stdio_subprocess_end_to_end_compile_generate_inventory_and_revise(tmp_path):
    ws = tmp_path / "stdio_ws"
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "cad_doctor", "arguments": {"skip_native": True}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "cad_compile_spec",
                "arguments": {
                    "markdown_text": (
                        "# 连接法兰盘\n"
                        "图号：MCP-FLG-01\n"
                        "材料：Q235B\n"
                        "外径：140 mm\n"
                        "内孔：50 mm\n"
                        "节圆直径：96 mm\n"
                        "安装孔：4×Φ14\n"
                        "厚度：20 mm\n"
                    )
                },
            },
        },
    ]

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(RUNTIME_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "cad_skill_core.cli",
            "mcp-stdio",
            "--workspace",
            str(ws),
        ],
        input="\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )

    # Every single stdout line MUST be valid JSON-RPC (zero stray print noise)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 3  # notification produces no stdout line!
    frames = [json.loads(ln) for ln in lines]
    assert [f["id"] for f in frames] == [1, 2, 3]
    compiled_payload = frames[2]["result"]["structuredContent"]
    assert compiled_payload["status"] == "compiled"
    compiled_spec = compiled_payload["spec"]

    # Second stdio session: generate 2D with Base64 PNG + inventory + dry-run revise
    gen_requests = [
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "cad_generate_mechanical_2d",
                "arguments": {"spec": compiled_spec, "include_image_base64": True},
            },
        }
    ]
    proc2 = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "cad_skill_core.cli",
            "mcp-stdio",
            "--workspace",
            str(ws),
        ],
        input=json.dumps(gen_requests[0], ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    gen_frame = json.loads(proc2.stdout.strip())
    gen_result = gen_frame["result"]
    assert gen_result["isError"] is False
    assert any(c["type"] == "image" and c["mimeType"] == "image/png" for c in gen_result["content"])
    img_block = next(c for c in gen_result["content"] if c["type"] == "image")
    assert base64.b64decode(img_block["data"]).startswith(b"\x89PNG")

    mech_task_id = gen_result["structuredContent"]["task_id"]
    inv_and_rev = [
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "cad_inventory_dxf", "arguments": {"task_id": mech_task_id}},
        },
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "resources/read",
            "params": {"uri": f"cad://tasks/{mech_task_id}/audit"},
        },
    ]
    proc3 = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "cad_skill_core.cli",
            "mcp-stdio",
            "--workspace",
            str(ws),
        ],
        input="\n".join(json.dumps(r, ensure_ascii=False) for r in inv_and_rev) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    f3 = [json.loads(ln) for ln in proc3.stdout.splitlines() if ln.strip()]
    inv_data = f3[0]["result"]["structuredContent"]
    assert len(inv_data["entities"]) > 10
    audit_res = json.loads(f3[1]["result"]["contents"][0]["text"])
    assert audit_res["summary"]["errors"] == 0


def test_mcp_http_and_web_api_generate_mechanical_sld_pavement_and_download_artifacts(tmp_path):
    app = create_mcp_http_app(tmp_path / "http_ws")
    client = TestClient(app)

    # 1. Health check
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["tool_count"] == 17

    # 1b. MCP App (ui://cad-operations/viewer.html, aux_pane entrypoint, app-only scene loader)
    tools_resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "tools-list-1", "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    tools_by_name = {t["name"]: t for t in tools_resp}
    assert tools_by_name["cad_generate_mechanical_2d"]["_meta"]["ui"]["resourceUri"] == "ui://cad-operations/viewer.html"
    assert tools_by_name["cad_open_viewer"]["_meta"]["com.google.antigravity/ui"]["entrypoints"][0]["type"] == "aux_pane"
    assert tools_by_name["cad_app_load_scene"]["_meta"]["ui"]["visibility"] == ["app"]

    ui_read = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "res-ui-1",
            "method": "resources/read",
            "params": {"uri": "ui://cad-operations/viewer.html"},
        },
    ).json()["result"]["contents"][0]
    assert ui_read["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/update-model-context" in ui_read["text"]
    assert "框选提问/改图" in ui_read["text"]

    # 2. Streamable HTTP /mcp JSON-RPC initialize + notification
    mcp_init = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "init-1", "method": "initialize", "params": {}},
    )
    assert mcp_init.status_code == 200
    assert mcp_init.json()["id"] == "init-1"

    mcp_notif = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert mcp_notif.status_code == 202

    # 3. Web API: Electrical SLD generation & DXF/PNG download
    sld_spec = json.loads((REPO_ROOT / "examples" / "electrical_sld_spec.json").read_text(encoding="utf-8"))
    sld_resp = client.post(
        "/api/v1/tasks/generate",
        json={"domain": "electrical_sld", "spec": sld_spec},
    )
    assert sld_resp.status_code == 200
    sld_body = sld_resp.json()
    assert sld_body["status"] == "passed"
    sld_task_id = sld_body["task_id"]

    app_scene = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "scene-1",
            "method": "tools/call",
            "params": {"name": "cad_app_load_scene", "arguments": {"task_id": sld_task_id}},
        },
    ).json()["result"]["structuredContent"]
    assert app_scene["status"] == "ok"
    assert app_scene["cad_scene"]["primitive_count"] > 10

    status_resp = client.get(f"/api/v1/tasks/{sld_task_id}")
    assert status_resp.status_code == 200
    dxf_art = next(a for a in status_resp.json()["artifacts"] if a["name"].endswith(".dxf"))
    dl_resp = client.get(dxf_art["download_url"])
    assert dl_resp.status_code == 200
    dxf_tmp = tmp_path / "downloaded_sld.dxf"
    dxf_tmp.write_bytes(dl_resp.content)
    assert not ezdxf.readfile(dxf_tmp).audit().has_errors

    # Path traversal via download URL must 404/reject
    assert client.get(f"/api/v1/tasks/{sld_task_id}/artifacts/..%2Ftask-meta.json").status_code in {400, 404}

    # 4. Web API: Pavement CAD generation
    pav_spec = json.loads((REPO_ROOT / "examples" / "pavement_structure_spec.json").read_text(encoding="utf-8-sig"))
    pav_resp = client.post(
        "/api/v1/tasks/generate",
        json={"domain": "pavement", "spec": pav_spec},
    )
    assert pav_resp.status_code == 200
    assert pav_resp.json()["status"] == "passed"
    assert pav_resp.json()["audit_summary"]["errors"] == 0

    # 5. Web API: Incomplete mechanical brief returns 422 with missing list
    bad_mech = client.post(
        "/api/v1/tasks/generate",
        json={"domain": "mechanical_2d", "markdown_brief": "# 缺失尺寸法兰\n内孔：40 mm\n"},
    )
    assert bad_mech.status_code == 422
    assert bad_mech.json()["validation"]["missing"]


def test_mcp_learn_standard_preview_and_revise_workflow_over_http_mcp(tmp_path):
    sample_dxf = tmp_path / "allowed_sample.dxf"
    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("OUTLINE", color=2, lineweight=50)
    msp = doc.modelspace()
    line = msp.add_line((0.0, 0.0), (50.0, 0.0), dxfattribs={"layer": "OUTLINE"})
    line_handle = line.dxf.handle
    doc.saveas(sample_dxf)

    app = create_mcp_http_app(tmp_path / "ws2", allowed_read_roots=[tmp_path])
    client = TestClient(app)

    # Learn standard
    learn = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cad_learn_standard", "arguments": {"sample_dxf_path": str(sample_dxf)}},
        },
    ).json()["result"]
    assert learn["isError"] is False
    assert learn["structuredContent"]["standard"]["layers"]["OUTLINE"]["color"] == 2

    # Inventory -> Revise -> Diff Review
    inv = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "cad_inventory_dxf", "arguments": {"dxf_path": str(sample_dxf)}},
        },
    ).json()["result"]["structuredContent"]
    source_sha = inv["sha256"]

    rev_plan = {
        "schema": "cad-revision/v1",
        "baseline_sha256": source_sha,
        "units": inv["units"],
        "tolerance": 1e-6,
        "operations": [
            {
                "id": "op-1",
                "type": "set_length",
                "target": {"handle": line_handle},
                "length": 80.0,
                "fixed": "start",
            }
        ],
    }
    rev = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "cad_revise_dxf",
                "arguments": {
                    "dxf_path": str(sample_dxf),
                    "plan": rev_plan,
                    "include_image_base64": True,
                },
            },
        },
    ).json()["result"]
    assert rev["isError"] is False
    revised_dxf = next(
        a["path"] for a in rev["structuredContent"]["artifacts"] if a["name"].endswith(".dxf")
    )

    diff = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "cad_preview_and_diff",
                "arguments": {
                    "dxf_path": str(sample_dxf),
                    "after_dxf_path": revised_dxf,
                    "include_image_base64": True,
                },
            },
        },
    ).json()["result"]
    assert diff["isError"] is False
    assert line_handle in diff["structuredContent"]["changed_handles"]
