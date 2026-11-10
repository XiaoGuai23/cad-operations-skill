"""Closed boundaries must preserve exact arcs and reject non-simple geometry."""

from importlib import import_module
from math import sqrt
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.geometry import entities
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.schema.io import load_spec
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import load_standard


def contour(**kwargs):
    assert hasattr(entities, "ContourEntity"), "closed true-arc contour API is missing"
    return entities.ContourEntity(kind="contour", layer="OUTLINE", source_id="src", **kwargs)


def compile_boundary(**kwargs):
    boundary = contour(**kwargs)
    return import_module("cad_skill_core.geometry.contours").compile_contour(boundary)


def vertex(x, y, bulge=0.0):
    return {"point": [float(x), float(y)], "bulge": float(bulge)}


def test_save_readback_is_one_closed_polyline_with_true_semicircle(tmp_path):
    boundary = contour(segments=[
        {"kind": "line", "start": [-10.0, 0.0], "end": [10.0, 0.0]},
        {"kind": "arc", "center": [0.0, 0.0], "radius": 10.0,
         "start_angle": 0.0, "sweep": 180.0},
    ])
    data = load_spec(Path("tests/fixtures/specs/simple-flange.json")).model_dump(mode="json")
    boundary.source_id = data["provenance"][0]["id"]
    data["geometry"]["entities"] = [boundary.model_dump(mode="json")]
    spec = DrawingSpec.model_validate(data)
    result = write_dxf(spec, load_standard(Path("knowledge/standards/gb-mechanical.yaml")), {}, tmp_path / "contour.dxf")
    modelspace = ezdxf.readfile(result.path).modelspace()
    assert result.entity_counts == {"LWPOLYLINE": 1}
    assert len(modelspace) == 1
    polyline = modelspace[0]
    assert polyline.closed
    parts = list(polyline.virtual_entities())
    assert [part.dxftype() for part in parts] == ["LINE", "ARC"]
    assert parts[1].dxf.radius == pytest.approx(10.0)
    assert parts[1].dxf.center.isclose((0.0, 0.0, 0.0))
    assert parts[1].dxf.start_angle == pytest.approx(0.0)
    assert parts[1].dxf.end_angle % 360 == pytest.approx(180.0)


def test_clockwise_arc_retains_signed_bulge_and_circle_radius(tmp_path):
    values = compile_boundary(segments=[
        {"kind": "arc", "center": [0.0, 0.0], "radius": 5.0,
         "start_angle": 180.0, "sweep": -180.0},
        {"kind": "line", "start": [5.0, 0.0], "end": [-5.0, 0.0]},
    ])
    doc = ezdxf.new()
    polyline = doc.modelspace().add_lwpolyline(values, format="xyb", close=True)
    doc.saveas(tmp_path / "clockwise.dxf")
    reopened = ezdxf.readfile(tmp_path / "clockwise.dxf").modelspace()[0]
    assert reopened.get_points("b")[0][0] == pytest.approx(-1.0)
    arc = next(part for part in reopened.virtual_entities() if part.dxftype() == "ARC")
    assert arc.dxf.radius == pytest.approx(5.0)
    assert arc.start_point.isclose((5.0, 0.0, 0.0))
    assert arc.end_point.isclose((-5.0, 0.0, 0.0))


def test_two_semicircles_form_a_tangent_circle():
    values = compile_boundary(vertices=[vertex(-3, 0, 1), vertex(3, 0, 1)], require_tangent=True)
    assert len(values) == 2
    assert values[0][2] == 1.0


@pytest.mark.parametrize("kwargs, message", [
    ({}, "exactly one"),
    ({"vertices": [], "segments": []}, "exactly one"),
    ({"vertices": [vertex(0, 0)]}, "at least two"),
    ({"vertices": [vertex(0, 0), vertex(1, 0), vertex(0, 0)]}, "zero-length"),
    ({"vertices": [vertex(0, 0), vertex(1, 0), vertex(0, 1)], "tolerance": 0.0}, "tolerance"),
    ({"vertices": [vertex(0, 0), vertex(float("nan"), 0), vertex(0, 1)]}, "finite"),
    ({"vertices": [vertex(0, 0, float("inf")), vertex(1, 0), vertex(0, 1)]}, "finite"),
    ({"segments": [
        {"kind": "line", "start": [0.0, 0.0], "end": [1.0, 0.0]},
        {"kind": "line", "start": [2.0, 0.0], "end": [0.0, 0.0]},
    ]}, "continuity"),
    ({"segments": [
        {"kind": "line", "start": [0.0, 0.0], "end": [1.0, 0.0]},
        {"kind": "line", "start": [1.0, 0.0], "end": [1.0, 1.0]},
    ]}, "closure"),
    ({"segments": [
        {"kind": "arc", "center": [0.0, 0.0], "radius": 0.0, "start_angle": 0.0, "sweep": 180.0},
        {"kind": "line", "start": [0.0, 0.0], "end": [1.0, 0.0]},
    ]}, "radius"),
    ({"segments": [
        {"kind": "arc", "center": [0.0, 0.0], "radius": 1.0, "start_angle": 0.0, "sweep": 360.0},
        {"kind": "line", "start": [0.0, 0.0], "end": [1.0, 0.0]},
    ]}, "sweep"),
])
def test_invalid_geometry_is_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        compile_boundary(**kwargs)


@pytest.mark.parametrize("vertices", [
    [vertex(0, 0), vertex(2, 2), vertex(0, 2), vertex(2, 0)],
    [vertex(0, 0), vertex(3, 0), vertex(1, 0), vertex(1, 2)],
    [vertex(2, 0, 1), vertex(-2, 0), vertex(0, 3), vertex(0, -3)],
    [vertex(0, 0, -1), vertex(4, 0, -1), vertex(4, 4, -1), vertex(0, 4, -1)],
    [vertex(-2, 0, 1), vertex(2, 0, -1)],
])
def test_crossings_and_overlapping_lines_or_arcs_are_rejected(vertices):
    with pytest.raises(ValueError, match="intersection|overlap"):
        compile_boundary(vertices=vertices)


def test_optional_tangency_rejects_a_corner_but_plain_contour_allows_it():
    vertices = [vertex(0, 0), vertex(2, 0), vertex(2, 2), vertex(0, 2)]
    assert len(compile_boundary(vertices=vertices)) == 4
    with pytest.raises(ValueError, match="tangent"):
        compile_boundary(vertices=vertices, require_tangent=True)


def test_nonadjacent_arc_tangency_is_rejected_without_tessellation():
    # Upper semicircle touches the horizontal top edge at (0, 2).
    with pytest.raises(ValueError, match="intersection"):
        compile_boundary(vertices=[vertex(2, 0, 1), vertex(-2, 0), vertex(-3, 2), vertex(3, 2)])


def test_major_arc_keeps_exact_bulge():
    values = compile_boundary(vertices=[vertex(1, 0, 1 + sqrt(2)), vertex(0, -1)])
    assert values[0][2] == pytest.approx(1 + sqrt(2))


@pytest.mark.parametrize("sweep", [-359.99, -270.0, -0.1, 0.1, 270.0, 359.99])
def test_arcs_crossing_angle_zero_and_near_full_sweeps_keep_their_carrier(sweep, tmp_path):
    from math import cos, radians, sin

    start = (10.0, 0.0)
    end = (10.0 * cos(radians(sweep)), 10.0 * sin(radians(sweep)))
    values = compile_boundary(segments=[
        {"kind": "arc", "center": [0.0, 0.0], "radius": 10.0,
         "start_angle": 0.0, "sweep": sweep},
        {"kind": "line", "start": end, "end": start},
    ])
    document = ezdxf.new()
    document.modelspace().add_lwpolyline(values, format="xyb", close=True)
    document.saveas(tmp_path / "sweep.dxf")
    arc = next(e for e in ezdxf.readfile(tmp_path / "sweep.dxf").modelspace()[0].virtual_entities() if e.dxftype() == "ARC")
    assert arc.dxf.radius == pytest.approx(10.0, abs=1e-8)
    assert arc.dxf.center.isclose((0.0, 0.0, 0.0), abs_tol=1e-8)
    assert (arc.dxf.end_angle - arc.dxf.start_angle) % 360 == pytest.approx(abs(sweep), abs=1e-8)


def test_closed_circle_with_mixed_arc_sizes_is_not_mistaken_for_overlap():
    from math import tan, radians

    values = compile_boundary(vertices=[vertex(1, 0, tan(radians(270) / 4)), vertex(0, -1, tan(radians(90) / 4))], require_tangent=True)
    assert len(values) == 2


def test_almost_parallel_long_crossing_edges_are_still_rejected():
    with pytest.raises(ValueError, match="intersection"):
        compile_boundary(vertices=[vertex(0, 0), vertex(1e10, 1e-5), vertex(0, 1e-5), vertex(1e10, 0)])


def test_long_narrow_triangle_above_tolerance_is_not_mistaken_for_overlap():
    assert len(compile_boundary(vertices=[vertex(0, 0), vertex(1e10, 0), vertex(5e9, 1e-5)])) == 3


def test_bulge_that_rounds_to_a_full_turn_is_rejected():
    with pytest.raises(ValueError, match="resolv|represent"):
        compile_boundary(vertices=[vertex(0, 0, 1e300), vertex(2, 0, 1e-300)])


def test_endpoint_snapping_must_not_distort_the_declared_arc_carrier():
    from math import cos, radians, sin

    sweep = 359.999
    end = (100.0 * cos(radians(sweep)), 100.0 * sin(radians(sweep)))
    # A 5e-8 endpoint adjustment would change radius by about 0.00286.
    # This exceeds the declared 1e-7 geometry tolerance despite closure passing.
    with pytest.raises(ValueError, match="carrier"):
        compile_boundary(segments=[
            {"kind": "arc", "center": [0.0, 0.0], "radius": 100.0,
             "start_angle": 0.0, "sweep": sweep},
            {"kind": "line", "start": (end[0], end[1] + 5e-8), "end": (100.0, 0.0)},
        ])


@pytest.mark.parametrize("bulge", [1e-9, -1e-9, 1e-12, -1e-12])
@pytest.mark.parametrize("tolerance", [1e-7, 1e-12])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_shallow_arc_bow_tie_is_rejected_when_circle_math_is_unreliable(bulge, tolerance, scale):
    # Huge supporting circles previously hid the crossing of these near-line
    # arcs. Tightening tolerance cannot restore missing float precision.
    with pytest.raises(ValueError, match="resolv|precision|shallow"):
        compile_boundary(vertices=[vertex(x * scale, y * scale, bulge) for x, y in
                                   [(0, 0), (10, 10), (0, 10), (10, 0)]],
                         tolerance=tolerance * scale)


def test_shallow_nonzero_bulges_are_rejected_instead_of_silently_becoming_lines():
    corners = [(0, 0), (10, 0), (10, 10), (0, 10)]
    with pytest.raises(ValueError, match="resolv|precision|shallow"):
        compile_boundary(vertices=[vertex(x, y, 1e-12) for x, y in corners])
    # The caller must explicitly choose straight edges when that is the intent.
    assert len(compile_boundary(vertices=[vertex(x, y) for x, y in corners])) == 4


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_resolvable_shallow_arcs_keep_the_original_nonzero_bulge(scale):
    values = compile_boundary(vertices=[vertex(x * scale, y * scale, 1e-4) for x, y in
                                        [(0, 0), (10, 0), (10, 10), (0, 10)]],
                              tolerance=1e-7 * scale)
    assert all(value[2] == 1e-4 for value in values)
