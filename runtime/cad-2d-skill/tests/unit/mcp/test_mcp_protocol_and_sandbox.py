from __future__ import annotations

import json
import pytest

from cad_skill_core.mcp import MCPDispatcher, MCPSandboxError, MCPWorkspaceSandbox


def test_mcp_initialize_negotiates_supported_versions_and_ignores_notifications(tmp_path):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))

    init_resp = dispatcher.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
    )
    assert init_resp["result"]["protocolVersion"] == "2025-03-26"
    assert init_resp["result"]["serverInfo"]["name"] == "cad-operations-skill-mcp"

    fallback_resp = dispatcher.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        }
    )
    assert fallback_resp["result"]["protocolVersion"] == "2024-11-05"

    # Notifications without 'id' must return None (no frame emitted)
    assert (
        dispatcher.handle_payload(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        is None
    )
    assert dispatcher.handle_payload({"jsonrpc": "2.0", "id": 3, "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {},
    }


def test_mcp_lists_all_fifteen_2d_3d_and_driver_tools_with_strict_json_schemas(tmp_path):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))
    resp = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == 23
    assert names == [
        "cad_doctor",
        "cad_compile_spec",
        "cad_validate_spec",
        "cad_learn_standard",
        "cad_generate_mechanical_2d",
        "cad_generate_electrical_sld",
        "cad_generate_pavement",
        "cad_inventory_dxf",
        "cad_revise_dxf",
        "cad_preview_and_diff",
        "cad_generate_3d",
        "cad_project_3d_to_2d",
        "cad_list_drivers",
        "cad_verify_roundtrip",
        "cad_compile_3d_spec",
        "cad_open_viewer",
        "cad_app_load_scene",
        "cad_score_drawing",
        "cad_run_cad1k_eval",
        "cad_revise_by_nl",
        "cad_audit_legacy_drawing",
        "cad_apply_revision_ir",
        "cad_vector_diff",
    ]
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False


def test_mcp_resources_and_prompts_round_trip(tmp_path):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))

    res_list = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 11, "method": "resources/list"})
    uris = {r["uri"] for r in res_list["result"]["resources"]}
    assert uris == {
        "ui://cad-operations/viewer.html",
        "cad://standards/gb-mechanical",
        "cad://standards/presets",
        "cad://schemas/drawing-spec",
        "cad://schemas/model-3d-spec",
        "cad://revision/operations",
        "cad://schemas/revision-ir",
    }

    for uri in uris:
        read_resp = dispatcher.handle_payload(
            {"jsonrpc": "2.0", "id": 12, "method": "resources/read", "params": {"uri": uri}}
        )
        contents = read_resp["result"]["contents"]
        assert len(contents) == 1 and len(contents[0]["text"]) > 50

    prompts_resp = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 13, "method": "prompts/list"})
    assert {p["name"] for p in prompts_resp["result"]["prompts"]} == {
        "draft-from-brief",
        "safe-dxf-revision",
        "visual-self-check-and-revise",
    }

    draft_prompt = dispatcher.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "prompts/get",
            "params": {"name": "draft-from-brief", "arguments": {"brief": "外径120法兰"}},
        }
    )
    assert "cad_compile_spec" in draft_prompt["result"]["messages"][0]["content"]["text"]


@pytest.mark.parametrize(
    ("raw_json", "expected_code"),
    [
        ("{bad json", -32700),
        ("[]", -32600),
        ('{"jsonrpc":"1.0","id":1,"method":"ping"}', -32600),
        ('{"jsonrpc":"2.0","id":1,"method":"unknown/method"}', -32601),
        ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"nonexistent"}}', -32602),
        ('{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"cad://unknown"}}', -32602),
    ],
)
def test_mcp_jsonrpc_error_codes(tmp_path, raw_json, expected_code):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))
    raw_resp = dispatcher.handle_raw_json(raw_json)
    assert raw_resp is not None
    parsed = json.loads(raw_resp)
    assert parsed["error"]["code"] == expected_code


def test_sandbox_blocks_directory_traversal_and_outside_host_paths(tmp_path):
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("confidential", encoding="utf-8")

    sandbox = MCPWorkspaceSandbox(tmp_path / "isolated_ws")

    with pytest.raises(MCPSandboxError, match="escapes|invalid"):
        sandbox.resolve_readable_file(outside_secret)

    with pytest.raises(MCPSandboxError, match="escapes|invalid"):
        sandbox.resolve_readable_file("../secret.txt")

    with pytest.raises(MCPSandboxError, match="invalid"):
        sandbox.resolve_task_dir("../../etc")

    task_id, _ = sandbox.allocate_task_dir("sec")
    with pytest.raises(MCPSandboxError, match="invalid"):
        sandbox.resolve_task_artifact(task_id, "../secret.txt")


def test_mcp_tool_rejects_extra_undeclared_arguments_cleanly(tmp_path):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))
    resp = dispatcher.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "cad_doctor", "arguments": {"hallucinated_field": 123}},
        }
    )
    result = resp["result"]
    assert result["isError"] is True
    assert "hallucinated_field" in result["structuredContent"]["error"]


def test_mcp_batch_requests_and_resource_templates(tmp_path):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))
    batch_resp = dispatcher.handle_payload(
        [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
            {"jsonrpc": "2.0", "id": "b", "method": "resources/templates/list"},
        ]
    )
    assert isinstance(batch_resp, list) and len(batch_resp) == 2
    assert batch_resp[0]["id"] == "a"
    templates = batch_resp[1]["result"]["resourceTemplates"]
    assert templates[0]["uriTemplate"] == "cad://tasks/{task_id}/audit"


@pytest.mark.parametrize(
    ("prompt_name", "args", "expect_error"),
    [
        ("draft-from-brief", {}, True),
        ("safe-dxf-revision", {"dxf_path": "a.dxf"}, True),
        ("safe-dxf-revision", {"dxf_path": "a.dxf", "change_request": "加长至80mm"}, False),
    ],
)
def test_mcp_prompts_argument_validation(tmp_path, prompt_name, args, expect_error):
    dispatcher = MCPDispatcher(MCPWorkspaceSandbox(tmp_path / "ws"))
    resp = dispatcher.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "prompts/get",
            "params": {"name": prompt_name, "arguments": args},
        }
    )
    if expect_error:
        assert resp["error"]["code"] == -32602
    else:
        assert "cad_revise_dxf" in resp["result"]["messages"][0]["content"]["text"]


def test_mcp_validate_spec_and_dry_run_revision_and_single_preview(tmp_path):
    import ezdxf
    from fastapi.testclient import TestClient
    from cad_skill_core.mcp import create_mcp_http_app

    dxf_file = tmp_path / "single.dxf"
    doc = ezdxf.new("R2010", setup=True)
    line = doc.modelspace().add_line((0.0, 0.0), (30.0, 0.0))
    doc.saveas(dxf_file)

    app = create_mcp_http_app(tmp_path / "ws", allowed_read_roots=[tmp_path])
    client = TestClient(app)

    # Web compile endpoint
    comp = client.post("/api/v1/tasks/compile", json={"markdown_text": "# 圆角板\n宽度：80 mm\n高度：50 mm\n圆角半径：R6\n"})
    assert comp.status_code == 200
    spec_obj = comp.json()["spec"]

    # Validate spec tool (valid & invalid)
    val_ok = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "cad_validate_spec", "arguments": {"spec": spec_obj}}},
    ).json()["result"]
    assert val_ok["isError"] is False
    assert val_ok["structuredContent"]["status"] == "valid"

    val_bad = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "cad_validate_spec", "arguments": {"spec": {"bad": 1}}}},
    ).json()["result"]
    assert val_bad["isError"] is True

    # Single preview mode
    prev = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "cad_preview_and_diff", "arguments": {"dxf_path": str(dxf_file), "include_image_base64": True}}},
    ).json()["result"]
    assert prev["isError"] is False
    assert prev["structuredContent"]["mode"] == "preview"
    assert any(c["type"] == "image" for c in prev["content"])

    # Web dry_run revise endpoint
    inv = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "cad_inventory_dxf", "arguments": {"dxf_path": str(dxf_file)}}},
    ).json()["result"]["structuredContent"]
    dry_rev = client.post(
        "/api/v1/tasks/revise",
        json={
            "dxf_path": str(dxf_file),
            "dry_run": True,
            "plan": {
                "schema": "cad-revision/v1",
                "baseline_sha256": inv["sha256"],
                "units": inv["units"],
                "tolerance": 1e-6,
                "operations": [{"id": "dry-1", "type": "set_length", "target": {"handle": line.dxf.handle}, "length": 45.0, "fixed": "start"}],
            },
        },
    )
    assert dry_rev.status_code == 200
    assert dry_rev.json()["dry_run"] is True
    assert dry_rev.json()["artifacts"] == []

