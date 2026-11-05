"""Known-geometry checks for absolute, local CAD constraint edits."""

from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.revision.common import RevisionError
from cad_skill_core.revision.constraints import apply_constraint, check_constraint


def sel(entity):
    return {"handle": entity.dxf.handle}


def vertices(entity):
    return [(float(x), float(y)) for x, y in entity.get_points("xy")]


def rectangle(modelspace, x0=0, y0=0, x1=10, y1=20):
    return modelspace.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True)


def test_align_uses_reference_boundary_and_preserves_unrelated_geometry(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m)
    reference = m.add_line((-5, 36), (15, 36))
    other = m.add_circle((100, 100), 4)
    op = {"type": "align", "targets": [sel(part)], "anchor": "max_y",
          "reference": sel(reference), "reference_anchor": "min_y", "offset": -2}

    result = apply_constraint(doc, op, 1e-6)

    assert vertices(part) == [(0, 14), (10, 14), (10, 34), (0, 34)]
    assert tuple(other.dxf.center) == (100, 100, 0)
    assert tuple(reference.dxf.start) == (-5, 36, 0)
    assert result["status"] == "passed"
    assert result["touched_handles"] == [part.dxf.handle]
    output = tmp_path / "aligned.dxf"
    doc.saveas(output)
    reopened = ezdxf.readfile(output)
    assert check_constraint(reopened, op, 1e-6)["status"] == "passed"
    reopened.entitydb[part.dxf.handle].translate(0, -1, 0)
    assert check_constraint(reopened, op, 1e-6)["status"] == "failed"


def test_alignment_is_absolute_and_second_application_does_not_move_again():
    doc = ezdxf.new()
    line = doc.modelspace().add_line((1, 2), (5, 7))
    op = {"type": "align", "targets": [sel(line)], "anchor": "center_x", "coordinate": 10}

    apply_constraint(doc, op, 1e-6)
    second = apply_constraint(doc, op, 1e-6)

    assert tuple(line.dxf.start) == (8, 2, 0)
    assert tuple(line.dxf.end) == (12, 7, 0)
    assert second["touched_handles"] == []
    assert check_constraint(doc, op, 1e-6)["status"] == "passed"


def test_contact_moves_boundary_to_finite_reference_edge():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m, 2, 3, 8, 9)
    pad = rectangle(m, 0, 20, 10, 30)
    op = {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
          "reference": sel(pad), "reference_anchor": "min_y"}

    result = apply_constraint(doc, op, 1e-6)

    assert vertices(part) == [(2, 14), (8, 14), (8, 20), (2, 20)]
    assert result["checks"]["finite_contact"] is True
    assert check_constraint(doc, op, 1e-6)["status"] == "passed"


def test_contact_rejects_parallel_edges_with_no_orthogonal_overlap_without_mutation():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m, 50, 0, 60, 10)
    pad = rectangle(m, 0, 20, 10, 30)
    before = vertices(part)
    op = {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
          "reference": sel(pad), "reference_anchor": "min_y"}

    with pytest.raises(RevisionError, match="contact|overlap"):
        apply_constraint(doc, op, 1e-6)

    assert vertices(part) == before


def test_contact_does_not_treat_a_triangle_bbox_as_a_contact_edge():
    doc = ezdxf.new()
    m = doc.modelspace()
    triangle = m.add_lwpolyline([(0, 0), (10, 0), (5, 5)], close=True)
    line = m.add_line((0, 20), (10, 20))
    op = {"type": "contact", "targets": [sel(triangle)], "anchor": "max_y",
          "reference": sel(line), "reference_anchor": "min_y"}

    with pytest.raises(RevisionError, match="boundary|contact|edge"):
        apply_constraint(doc, op, 1e-6)

    assert vertices(triangle) == [(0, 0), (10, 0), (5, 5)]


def test_resize_changes_top_and_keeps_bottom_and_intermediate_vertices(tmp_path):
    doc = ezdxf.new()
    part = doc.modelspace().add_lwpolyline(
        [(0, 0), (10, 0), (10, 8), (8, 8), (8, 20), (2, 20), (2, 8), (0, 8)], close=True
    )
    op = {"type": "resize_fixed_edge", "targets": [sel(part)], "axis": "y",
          "fixed": "min", "size": 25}

    result = apply_constraint(doc, op, 1e-6)

    assert vertices(part) == [(0, 0), (10, 0), (10, 8), (8, 8), (8, 25), (2, 25), (2, 8), (0, 8)]
    assert result["checks"]["fixed_edge_preserved"] is True
    output = tmp_path / "resize.dxf"
    doc.saveas(output)
    reopened = ezdxf.readfile(output)
    verification_op = result["verification_operation"]
    assert check_constraint(reopened, verification_op, 1e-6)["status"] == "passed"
    reopened.entitydb[part.dxf.handle].translate(0, 1, 0)
    assert check_constraint(reopened, verification_op, 1e-6)["status"] == "failed"


def test_resize_fixed_max_x_is_absolute_and_preserves_line_attributes():
    doc = ezdxf.new()
    m = doc.modelspace()
    lines = [m.add_line(a, b, dxfattribs={"color": 3, "lineweight": 50}) for a, b in [
        ((5, 1), (15, 1)), ((15, 1), (15, 6)), ((15, 6), (5, 6)), ((5, 6), (5, 1))
    ]]
    op = {"type": "resize_fixed_edge", "targets": [sel(e) for e in lines], "axis": "x",
          "fixed": "max", "size": 13}

    apply_constraint(doc, op, 1e-6)
    second = apply_constraint(doc, op, 1e-6)

    assert tuple(lines[0].dxf.start) == (2, 1, 0)
    assert tuple(lines[0].dxf.end) == (15, 1, 0)
    assert all(e.dxf.color == 3 and e.dxf.lineweight == 50 for e in lines)
    assert second["touched_handles"] == []


def test_resize_rejects_crossing_intermediate_vertices_before_mutation():
    doc = ezdxf.new()
    part = doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 8), (5, 10), (0, 8)], close=True)
    before = vertices(part)
    op = {"type": "resize_fixed_edge", "targets": [sel(part)], "axis": "y",
          "fixed": "min", "size": 7}

    with pytest.raises(RevisionError, match="cross|boundary|collapse"):
        apply_constraint(doc, op, 1e-6)

    assert vertices(part) == before


@pytest.mark.parametrize("fixed,expected_start,expected_end", [
    ("start", (0, 0, 0), (6, 8, 0)),
    ("end", (-3, -4, 0), (3, 4, 0)),
])
def test_set_length_keeps_requested_endpoint_and_direction(fixed, expected_start, expected_end):
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0, 0), (3, 4))
    op = {"type": "set_length", "target": sel(line), "fixed": fixed, "length": 10}

    result = apply_constraint(doc, op, 1e-6)

    assert tuple(line.dxf.start) == expected_start
    assert tuple(line.dxf.end) == expected_end
    assert result["checks"]["fixed_endpoint_preserved"] is True
    assert check_constraint(doc, result["verification_operation"], 1e-6)["status"] == "passed"
    assert apply_constraint(doc, op, 1e-6)["touched_handles"] == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "10", 0, -1])
def test_set_length_rejects_invalid_length_without_mutation(bad):
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0, 0), (3, 4))
    op = {"type": "set_length", "target": sel(line), "fixed": "start", "length": bad}

    with pytest.raises(RevisionError):
        apply_constraint(doc, op, 1e-6)

    assert tuple(line.dxf.start) == (0, 0, 0)
    assert tuple(line.dxf.end) == (3, 4, 0)


@pytest.mark.parametrize("bad_tol", [0, -1, float("nan"), float("inf"), True, "0.01"])
def test_tolerance_requires_positive_finite_number(bad_tol):
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0, 0), (3, 4))
    with pytest.raises(RevisionError):
        apply_constraint(doc, {"type": "align", "targets": [sel(line)], "anchor": "min_x", "coordinate": 4}, bad_tol)
    assert tuple(line.dxf.start) == (0, 0, 0)


def test_all_targets_are_validated_before_any_entity_changes():
    doc = ezdxf.new()
    m = doc.modelspace()
    line = m.add_line((0, 0), (10, 0))
    hatch = m.add_hatch()
    op = {"type": "align", "targets": [sel(line), sel(hatch)], "anchor": "min_y", "coordinate": 10}

    with pytest.raises(RevisionError, match="unsupported|HATCH"):
        apply_constraint(doc, op, 1e-6)

    assert tuple(line.dxf.start) == (0, 0, 0)


@pytest.mark.parametrize("kind", ["nonplanar", "bulge", "extrusion"])
def test_resize_rejects_nonplanar_or_curved_geometry_without_mutation(kind):
    doc = ezdxf.new()
    m = doc.modelspace()
    if kind == "nonplanar":
        part = m.add_line((0, 0, 0), (10, 10, 1))
    else:
        part = rectangle(m)
        if kind == "bulge":
            part.set_points([(0, 0, 1), (10, 0, 0), (10, 20, 0), (0, 20, 0)], format="xyb")
        else:
            part.dxf.extrusion = (0, 1, 0)
    op = {"type": "resize_fixed_edge", "targets": [sel(part)], "axis": "y", "fixed": "min", "size": 30}
    before = part.dxfattribs().copy()

    with pytest.raises(RevisionError):
        apply_constraint(doc, op, 1e-6)

    assert part.dxfattribs() == before


def test_reference_cannot_be_part_of_targets():
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0, 0), (10, 0))
    op = {"type": "align", "targets": [sel(line)], "anchor": "min_y", "reference": sel(line), "reference_anchor": "max_y", "offset": 10}

    with pytest.raises(RevisionError, match="reference|target"):
        apply_constraint(doc, op, 1e-6)
    assert tuple(line.dxf.start) == (0, 0, 0)


def test_alignment_arc_bounds_do_not_use_full_circle_extents():
    doc = ezdxf.new()
    arc = doc.modelspace().add_arc((0, 0), 5, 0, 90)
    op = {"type": "align", "targets": [sel(arc)], "anchor": "min_x", "coordinate": 20}

    apply_constraint(doc, op, 1e-6)

    assert arc.dxf.center.x == pytest.approx(20)
    assert arc.dxf.center.y == 0
    assert arc.dxf.radius == 5
    assert check_constraint(doc, op, 1e-6)["status"] == "passed"


def test_unknown_fields_and_mixed_reference_forms_are_rejected():
    doc = ezdxf.new()
    m = doc.modelspace()
    line = m.add_line((0, 0), (10, 0))
    ref = m.add_line((0, 5), (10, 5))
    for extra in ({"coordinates": 10}, {"coordinate": 10, "reference": sel(ref), "reference_anchor": "min_y"}):
        op = {"type": "align", "targets": [sel(line)], "anchor": "min_y", **extra}
        with pytest.raises(RevisionError):
            apply_constraint(doc, op, 1e-6)
    assert tuple(line.dxf.start) == (0, 0, 0)


def test_verification_binds_set_length_fixed_endpoint_after_save(tmp_path):
    doc = ezdxf.new()
    line = doc.modelspace().add_line((2, 3), (5, 7))
    result = apply_constraint(doc, {"type": "set_length", "target": sel(line), "fixed": "end", "length": 10}, 1e-6)
    output = tmp_path / "length.dxf"
    doc.saveas(output)
    reopened = ezdxf.readfile(output)
    assert check_constraint(reopened, result["verification"], 1e-6)["status"] == "passed"
    reopened.entitydb[line.dxf.handle].translate(1, 0, 0)
    assert check_constraint(reopened, result["verification"], 1e-6)["status"] == "failed"


def test_contact_rejects_reference_with_bbox_but_no_boundary_edge():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m)
    triangle = m.add_lwpolyline([(0, 25), (10, 25), (5, 20)], close=True)
    with pytest.raises(RevisionError, match="boundary|edge|contact"):
        apply_constraint(doc, {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
                               "reference": sel(triangle), "reference_anchor": "min_y"}, 1e-6)
    assert vertices(part) == [(0, 0), (10, 0), (10, 20), (0, 20)]


def test_contact_requires_overlap_of_actual_intervals_not_their_envelope():
    doc = ezdxf.new()
    m = doc.modelspace()
    left = rectangle(m, 0, 0, 2, 2)
    right = rectangle(m, 8, 0, 10, 2)
    ref = m.add_line((4, 20), (6, 20))
    with pytest.raises(RevisionError, match="contact|overlap"):
        apply_constraint(doc, {"type": "contact", "targets": [sel(left), sel(right)], "anchor": "max_y",
                               "reference": sel(ref), "reference_anchor": "min_y"}, 1e-6)
    assert vertices(left)[0] == (0, 0)
    assert vertices(right)[0] == (8, 0)


def test_contact_rejects_entities_in_different_elevations():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = m.add_line((0, 0, 1), (10, 0, 1))
    ref = m.add_line((0, 5, 2), (10, 5, 2))
    with pytest.raises(RevisionError, match="plane|planar|elevation"):
        apply_constraint(doc, {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
                               "reference": sel(ref), "reference_anchor": "min_y"}, 1e-6)
    assert tuple(part.dxf.start) == (0, 0, 1)


def test_set_length_rejects_zero_length_source():
    doc = ezdxf.new()
    line = doc.modelspace().add_line((2, 3), (2, 3))
    with pytest.raises(RevisionError, match="zero|degenerate|length"):
        apply_constraint(doc, {"type": "set_length", "target": sel(line), "fixed": "start", "length": 4}, 1e-6)


@pytest.mark.parametrize("value", [True, "5", float("nan"), float("inf")])
def test_align_rejects_nonfinite_or_non_numeric_coordinate(value):
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0, 0), (10, 0))
    with pytest.raises(RevisionError):
        apply_constraint(doc, {"type": "align", "targets": [sel(line)], "anchor": "min_y", "coordinate": value}, 1e-6)
    assert tuple(line.dxf.start) == (0, 0, 0)


def test_alignment_overflow_rejected_before_mutation():
    doc = ezdxf.new()
    line = doc.modelspace().add_line((-1e308, 0), (-9e307, 0))
    with pytest.raises(RevisionError):
        apply_constraint(doc, {"type": "align", "targets": [sel(line)], "anchor": "min_x", "coordinate": 1e308}, 1e-6)
    assert line.dxf.start.x == -1e308


def test_rejected_operation_is_atomic_even_when_first_target_is_supported():
    doc = ezdxf.new()
    m = doc.modelspace()
    first = rectangle(m)
    second = rectangle(m, 20, 0, 30, 20)
    second.set_points([(20, 0, 1), (30, 0, 0), (30, 20, 0), (20, 20, 0)], format="xyb")
    with pytest.raises(RevisionError):
        apply_constraint(doc, {"type": "resize_fixed_edge", "targets": [sel(first), sel(second)],
                               "axis": "y", "fixed": "min", "size": 25}, 1e-6)
    assert vertices(first) == [(0, 0), (10, 0), (10, 20), (0, 20)]


def test_circle_alignment_uses_exact_bounds_and_retains_radius():
    doc = ezdxf.new()
    circle = doc.modelspace().add_circle((2, 3), 5)
    op = {"type": "align", "targets": [sel(circle)], "anchor": "max_x", "coordinate": 20}
    apply_constraint(doc, op, 1e-6)
    assert tuple(circle.dxf.center) == (15, 3, 0)
    assert circle.dxf.radius == 5
    assert check_constraint(doc, op, 1e-6)["status"] == "passed"


def test_contact_verification_reports_failed_when_boundary_overlap_is_lost():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m, 2, 3, 8, 9)
    pad = rectangle(m, 0, 20, 10, 30)
    op = {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
          "reference": sel(pad), "reference_anchor": "min_y"}
    result = apply_constraint(doc, op, 1e-6)
    part.translate(50, 0, 0)
    assert check_constraint(doc, result["verification_operation"], 1e-6)["status"] == "failed"


def test_contact_verification_reports_failed_when_boundary_is_no_longer_straight():
    doc = ezdxf.new()
    m = doc.modelspace()
    part = rectangle(m, 0, 0, 10, 10)
    ref = m.add_line((0, 20), (10, 20))
    op = {"type": "contact", "targets": [sel(part)], "anchor": "max_y",
          "reference": sel(ref), "reference_anchor": "min_y"}
    result = apply_constraint(doc, op, 1e-6)
    part.set_points([(0, 10), (10, 10), (5, 20)], format="xy")
    assert check_constraint(doc, result["verification_operation"], 1e-6)["status"] == "failed"
