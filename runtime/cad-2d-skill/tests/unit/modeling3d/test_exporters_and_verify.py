from __future__ import annotations

import ezdxf
import numpy as np
from PIL import Image
import pytest

from cad_skill_core.modeling3d import (
    Model3DSpec,
    SolidMesh3D,
    build_solid_mesh,
    export_3d_dxf,
    export_binary_stl,
    export_glb,
    export_obj,
    generate_autocad_3d_script,
    probe_3d_capabilities,
    render_isometric_png,
    verify_3d_artifacts,
)


def _sample_flange_spec() -> Model3DSpec:
    return Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "带颈对焊法兰", "drawing_number": "FLG3D-01"},
            "material": {"text": "304不锈钢", "source_id": "src"},
            "body": {
                "kind": "flange_3d",
                "outer_diameter": 150.0,
                "bore_diameter": 48.0,
                "thickness": 20.0,
                "hub_diameter": 76.0,
                "hub_height": 22.0,
                "bolt_circle_diameter": 112.0,
                "hole_diameter": 14.0,
                "hole_count": 4,
                "source_id": "src",
            },
            "provenance": [{"id": "src", "file": "brief.md", "locator": "line 1", "confidence": 1.0}],
        }
    )


def test_exporters_and_round_trip_verifier_pass_on_valid_solid(tmp_path):
    spec = _sample_flange_spec()
    mesh = build_solid_mesh(spec, circle_segments=64)

    stl = export_binary_stl(mesh, tmp_path / "part.stl")
    obj = export_obj(mesh, tmp_path / "part.obj")
    glb = export_glb(mesh, tmp_path / "part.glb", name="flange")
    dxf3d = export_3d_dxf(mesh, tmp_path / "part3d.dxf")
    iso_png = render_isometric_png(mesh, tmp_path / "iso.png", title="FLG3D-01")

    for p in (stl, obj, glb, dxf3d, iso_png):
        assert p.is_file() and p.stat().st_size > 0

    with Image.open(iso_png) as im:
        assert im.size[0] > 200 and im.size[1] > 150

    reopened_dxf = ezdxf.readfile(dxf3d)
    assert not reopened_dxf.audit().has_errors
    assert len(list(reopened_dxf.modelspace().query("MESH"))) == 1

    report = verify_3d_artifacts(
        spec,
        stl_path=stl,
        glb_path=glb,
        dxf3d_path=dxf3d,
        expected_volume_mm3=mesh.expected_volume_mm3,
        expected_bbox_mm=mesh.expected_bbox_mm,
    )
    assert report.status == "passed", report.issues
    assert report.watertight is True
    assert report.open_boundary_edges == 0
    assert report.actual_spans_mm == pytest.approx((150.0, 150.0, 42.0), abs=0.5)


def test_verifier_catches_non_watertight_mesh_with_missing_triangle(tmp_path):
    spec = _sample_flange_spec()
    mesh = build_solid_mesh(spec, circle_segments=32)
    # Drop one triangle to create 3 open boundary half-edges!
    broken_mesh = SolidMesh3D(
        vertices=mesh.vertices,
        faces=mesh.faces[:-1].copy(),
        expected_volume_mm3=mesh.expected_volume_mm3,
        expected_bbox_mm=mesh.expected_bbox_mm,
    )
    broken_stl = export_binary_stl(broken_mesh, tmp_path / "broken.stl")

    report = verify_3d_artifacts(spec, stl_path=broken_stl)
    assert report.status == "failed"
    assert report.watertight is False
    assert report.open_boundary_edges >= 3
    assert any(i.code == "MESH_NOT_WATERTIGHT" for i in report.issues)


def test_verifier_catches_inverted_normals_negative_volume(tmp_path):
    spec = _sample_flange_spec()
    mesh = build_solid_mesh(spec, circle_segments=32)
    inverted = SolidMesh3D(
        vertices=mesh.vertices,
        faces=mesh.faces[:, [0, 2, 1]].copy(),
        expected_volume_mm3=mesh.expected_volume_mm3,
        expected_bbox_mm=mesh.expected_bbox_mm,
    )
    inv_stl = export_binary_stl(inverted, tmp_path / "inverted.stl")
    report = verify_3d_artifacts(spec, stl_path=inv_stl)
    assert report.status == "failed"
    assert any(i.code == "MESH_NONPOSITIVE_VOLUME" for i in report.issues)


def test_autocad_3d_script_and_capability_probe_are_safe_and_honest(tmp_path):
    caps = probe_3d_capabilities()
    assert caps["builtin_manifold_mesh"]["available"] is True
    assert "solidworks_com" in caps and "freecad_headless" in caps

    spec = _sample_flange_spec()
    scr = generate_autocad_3d_script(spec, tmp_path / "flange3d.dwg")
    assert "_CYLINDER" in scr
    assert "_UNION" in scr
    assert "_SUBTRACT" in scr
    assert "_SAVEAS" in scr

    with pytest.raises(ValueError, match="unsafe path"):
        generate_autocad_3d_script(spec, tmp_path / 'bad"quote.dwg')
