from __future__ import annotations

from collections import Counter
from pydantic import ValidationError
import pytest

from cad_skill_core.modeling3d import (
    Model3DSpec,
    build_solid_mesh,
)


def _assert_watertight_manifold(mesh) -> None:
    directed: Counter[tuple[int, int]] = Counter()
    for a, b, c in mesh.faces:
        ia, ib, ic = int(a), int(b), int(c)
        assert len({ia, ib, ic}) == 3
        directed[(ia, ib)] += 1
        directed[(ib, ic)] += 1
        directed[(ic, ia)] += 1
    for (u, v), count in directed.items():
        assert directed[(v, u)] == count, f"unmatched half-edge ({u}->{v})"
    vol = mesh.signed_volume_mm3()
    assert vol > 0.0
    assert vol == pytest.approx(mesh.expected_volume_mm3, rel=0.025)


@pytest.mark.parametrize(
    "body_payload",
    [
        {
            "kind": "box",
            "width": 100.0,
            "depth": 60.0,
            "height": 25.0,
            "through_holes": [[-25.0, 0.0, 12.0], [25.0, 0.0, 12.0]],
            "source_id": "src",
        },
        {
            "kind": "cylinder",
            "outer_diameter": 60.0,
            "bore_diameter": 24.0,
            "height": 45.0,
            "source_id": "src",
        },
        {
            "kind": "cylinder",
            "outer_diameter": 40.0,
            "bore_diameter": 0.0,
            "height": 30.0,
            "source_id": "src",
        },
        {
            "kind": "extrude_polygon",
            "outer_loop": [[-40.0, -30.0], [40.0, -30.0], [40.0, 30.0], [-40.0, 30.0]],
            "inner_loops": [[[-12.0, -8.0], [12.0, -8.0], [12.0, 8.0], [-12.0, 8.0]]],
            "height": 18.0,
            "source_id": "src",
        },
        {
            "kind": "flange_3d",
            "outer_diameter": 160.0,
            "bore_diameter": 50.0,
            "thickness": 20.0,
            "bolt_circle_diameter": 115.0,
            "hole_diameter": 14.0,
            "hole_count": 6,
            "source_id": "src",
        },
        {
            "kind": "flange_3d",
            "outer_diameter": 180.0,
            "bore_diameter": 54.0,
            "thickness": 22.0,
            "hub_diameter": 86.0,
            "hub_height": 28.0,
            "bolt_circle_diameter": 136.0,
            "hole_diameter": 16.0,
            "hole_count": 8,
            "source_id": "src",
        },
        {
            "kind": "stepped_shaft_3d",
            "steps": [[30.0, 24.0], [50.0, 38.0], [25.0, 20.0]],
            "bore_diameter": 0.0,
            "source_id": "src",
        },
        {
            "kind": "stepped_shaft_3d",
            "steps": [[35.0, 32.0], [55.0, 48.0]],
            "bore_diameter": 14.0,
            "source_id": "src",
        },
        {
            "kind": "slotted_bracket_3d",
            "width": 130.0,
            "height": 80.0,
            "thickness": 14.0,
            "corner_radius": 10.0,
            "slot_length": 50.0,
            "slot_width": 16.0,
            "hole_diameter": 10.0,
            "hole_inset": 16.0,
            "source_id": "src",
        },
    ],
)
def test_all_3d_bodies_compile_to_watertight_positive_volume_manifolds(body_payload):
    spec = Model3DSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "3D Test Solid", "drawing_number": "3D-001"},
            "material": {"text": "45钢", "source_id": "src"},
            "body": body_payload,
            "provenance": [{"id": "src", "file": "spec.md", "locator": "line 1", "confidence": 1.0}],
        }
    )
    mesh = build_solid_mesh(spec, circle_segments=64)
    _assert_watertight_manifold(mesh)
    assert mesh.surface_area_mm2() > 0


@pytest.mark.parametrize(
    ("bad_body", "err_match"),
    [
        ({"kind": "box", "width": 40.0, "depth": 40.0, "height": 10.0, "through_holes": [[18.0, 0.0, 10.0]], "source_id": "src"}, "wall"),
        ({"kind": "cylinder", "outer_diameter": 30.0, "bore_diameter": 30.0, "height": 10.0, "source_id": "src"}, "bore_diameter"),
        ({"kind": "flange_3d", "outer_diameter": 120.0, "bore_diameter": 40.0, "hub_diameter": 95.0, "hub_height": 15.0, "bolt_circle_diameter": 100.0, "hole_diameter": 14.0, "hole_count": 4, "source_id": "src"}, "interfere"),
        ({"kind": "stepped_shaft_3d", "steps": [[20.0, 16.0]], "bore_diameter": 15.5, "source_id": "src"}, "bore_diameter"),
        ({"kind": "slotted_bracket_3d", "width": 90.0, "height": 60.0, "slot_length": 60.0, "slot_width": 20.0, "hole_diameter": 12.0, "hole_inset": 15.0, "source_id": "src"}, "interfere"),
        ({"kind": "cylinder", "outer_diameter": 30.0, "height": 10.0, "source_id": "unknown-id"}, "unknown"),
    ],
)
def test_model3d_spec_rejects_infeasible_geometry_and_broken_provenance(bad_body, err_match):
    with pytest.raises(ValidationError, match=err_match):
        Model3DSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "Invalid 3D"},
                "body": bad_body,
                "provenance": [{"id": "src", "file": "spec.md", "locator": "line 1", "confidence": 1.0}],
            }
        )
