from __future__ import annotations

import base64

import ezdxf
from fastapi.testclient import TestClient

from cad_skill_core.mcp import create_mcp_http_app
from cad_skill_core.mcp.cad_scene import extract_dxf_vector_scene


def test_extract_dxf_vector_scene_preserves_layers_handles_colors_and_bounds(tmp_path):
    dxf_path = tmp_path / "scene_test.dxf"
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("OUTLINE", color=7, linetype="CONTINUOUS")
    doc.layers.add("CENTER", color=1, linetype="CENTER")
    doc.layers.add("DIM", color=2, linetype="CONTINUOUS")

    msp = doc.modelspace()
    poly = msp.add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True, dxfattribs={"layer": "OUTLINE"})
    circ = msp.add_circle((60, 40), 18.0, dxfattribs={"layer": "OUTLINE"})
    axis = msp.add_line((-10, 40), (130, 40), dxfattribs={"layer": "CENTER"})
    dim = msp.add_linear_dim(base=(60, 95), p1=(0, 80), p2=(120, 80), dxfattribs={"layer": "DIM"})
    dim.render()
    doc.saveas(dxf_path)

    scene = extract_dxf_vector_scene(dxf_path)
    assert scene["dxf_name"] == "scene_test.dxf"
    assert scene["bounds_mm"][0] <= -10.0 and scene["bounds_mm"][2] >= 130.0
    layer_names = {l["name"] for l in scene["layers"]}
    assert {"OUTLINE", "CENTER", "DIM"} <= layer_names
    handles_in_scene = {p["h"] for p in scene["primitives"]}
    assert str(poly.dxf.handle) in handles_in_scene
    assert str(circ.dxf.handle) in handles_in_scene
    assert str(axis.dxf.handle) in handles_in_scene


def test_upload_endpoint_accepts_dxf_and_markdown_and_rejects_disallowed_extensions(tmp_path):
    app = create_mcp_http_app(tmp_path / "up_ws")
    client = TestClient(app)
    try:
        # 1. Upload DXF -> auto-returns inventory + vector scene
        src_dxf = tmp_path / "part.dxf"
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = 4
        doc.layers.add("OUTLINE", color=7)
        doc.modelspace().add_line((0, 0), (60, 0), dxfattribs={"layer": "OUTLINE"})
        doc.modelspace().add_circle((30, 20), 10.0, dxfattribs={"layer": "OUTLINE"})
        doc.saveas(src_dxf)

        b64_dxf = base64.b64encode(src_dxf.read_bytes()).decode("ascii")
        r_dxf = client.post(
            "/api/v1/uploads",
            json={"filename": "零件原图.dxf", "content_base64": b64_dxf},
        )
        assert r_dxf.status_code == 200, r_dxf.text
        body = r_dxf.json()
        assert body["status"] == "uploaded"
        assert len(body["inventory"]["entities"]) == 2
        assert body["scene"]["primitive_count"] >= 2

        # 2. Reject executable / disallowed extension
        r_bad = client.post(
            "/api/v1/uploads",
            json={"filename": "payload.exe", "content_base64": b64_dxf},
        )
        assert r_bad.status_code == 400
    finally:
        app.state.job_manager.shutdown()
