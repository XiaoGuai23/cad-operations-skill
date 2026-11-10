"""Bounded rounded-rectangle dimensions drive, and are measured from, real arcs."""

from importlib import import_module
from math import sqrt

import ezdxf
import pytest

from cad_skill_core.geometry import entities


def api():
    assert hasattr(entities, "ContourEntity"), "closed contour API is missing"
    return import_module("cad_skill_core.geometry.parametric")


def test_radius_change_moves_line_endpoints_and_keeps_tangent_true_arcs(tmp_path):
    module = api()
    initial = module.rounded_rectangle(module.RoundedRectangleParameters(width=100.0, height=60.0, radius=10.0))
    changed = module.update_rounded_rectangle(initial, radius=20.0)
    compile_contour = import_module("cad_skill_core.geometry.contours").compile_contour
    old = compile_contour(initial)
    new = compile_contour(changed)
    assert old[0][:2] == pytest.approx((10.0, 0.0))
    assert old[1][:2] == pytest.approx((90.0, 0.0))
    assert new[0][:2] == pytest.approx((20.0, 0.0))
    assert new[1][:2] == pytest.approx((80.0, 0.0))
    assert changed.require_tangent is True
    document = ezdxf.new()
    document.modelspace().add_lwpolyline(new, format="xyb", close=True)
    document.saveas(tmp_path / "r20.dxf")
    polyline = ezdxf.readfile(tmp_path / "r20.dxf").modelspace()[0]
    assert polyline.closed
    parts = list(polyline.virtual_entities())
    assert [part.dxftype() for part in parts] == ["LINE", "ARC"] * 4
    assert [part.dxf.radius for part in parts if part.dxftype() == "ARC"] == pytest.approx([20.0] * 4)
    assert [p[0] for p in polyline.get_points("b") if p[0]] == pytest.approx([sqrt(2) - 1] * 4)


@pytest.mark.parametrize("clockwise", [False, True])
def test_reverse_measurement_supports_shifted_vertex_start_and_winding(clockwise):
    module = api()
    boundary = module.rounded_rectangle(module.RoundedRectangleParameters(width=90.0, height=50.0, radius=12.0, origin=(4.0, -7.0), clockwise=clockwise))
    boundary.vertices = boundary.vertices[3:] + boundary.vertices[:3]
    measured = module.derive_rounded_rectangle(boundary)
    assert measured.width == pytest.approx(90.0)
    assert measured.height == pytest.approx(50.0)
    assert measured.radius == pytest.approx(12.0)
    assert measured.origin == pytest.approx((4.0, -7.0))
    assert measured.clockwise is clockwise


@pytest.mark.parametrize("dimensions", [
    {"width": 0.0}, {"height": -1.0}, {"radius": 0.0}, {"radius": 30.0},
    {"radius": 31.0}, {"width": float("nan")}, {"origin": (float("inf"), 0.0)},
])
def test_conflicting_or_invalid_dimensions_fail_without_changing_input(dimensions):
    module = api()
    original = module.rounded_rectangle(module.RoundedRectangleParameters(width=100.0, height=60.0, radius=10.0))
    before = original.model_dump()
    with pytest.raises(ValueError):
        module.update_rounded_rectangle(original, **dimensions)
    assert original.model_dump() == before


def test_reverse_measurement_rejects_geometry_outside_the_supported_family():
    module = api()
    boundary = module.rounded_rectangle(module.RoundedRectangleParameters(width=100.0, height=60.0, radius=10.0))
    boundary.vertices[1].bulge = 0.2
    with pytest.raises(ValueError, match="rounded.rectangle|tangent"):
        module.derive_rounded_rectangle(boundary)


def test_dimension_update_preserves_metadata_and_rejects_unknown_parameters():
    module = api()
    boundary = module.rounded_rectangle(module.RoundedRectangleParameters(width=100.0, height=60.0, radius=10.0), layer="PROFILE", source_id="drawing:5")
    changed = module.update_rounded_rectangle(boundary, width=120.0, height=80.0, origin=(-2.0, 4.0))
    assert (changed.layer, changed.source_id) == ("PROFILE", "drawing:5")
    measured = module.derive_rounded_rectangle(changed)
    assert measured.width == pytest.approx(120.0)
    assert measured.height == pytest.approx(80.0)
    assert measured.origin == pytest.approx((-2.0, 4.0))
    with pytest.raises(ValueError, match="unsupported"):
        module.update_rounded_rectangle(boundary, diagonal=12.0)
