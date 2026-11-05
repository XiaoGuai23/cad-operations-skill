"""Deterministic, absolute edits of explicitly selected planar DXF geometry.

Alignment supports LINE, straight zero-width LWPOLYLINE, CIRCLE and ARC.
Contact and fixed-edge resizing require real straight boundary segments;
set_length operates on LINE. All candidate geometry is checked before edits.
"""

from __future__ import annotations

from copy import deepcopy
import math

from .common import RevisionError, resolve_entity, resolve_entities, validate_tolerance


_ANCHORS = {"min_x", "max_x", "center_x", "min_y", "max_y", "center_y"}
_STRAIGHT = {"LINE", "LWPOLYLINE"}
_SUPPORTED = _STRAIGHT | {"CIRCLE", "ARC"}


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RevisionError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise RevisionError(f"{name} must be a finite number") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise RevisionError(f"{name} must be a finite {'positive ' if positive else ''}number")
    return value


def _vector(value, name):
    return [_number(v, name) for v in value]


def _state(entity, tolerance):
    kind = entity.dxftype()
    if kind not in _SUPPORTED:
        raise RevisionError(f"unsupported constraint entity: {kind}")
    extrusion = _vector(entity.dxf.get("extrusion", (0, 0, 1)), "extrusion")
    if any(abs(a - b) > 1e-12 for a, b in zip(extrusion, (0, 0, 1))):
        raise RevisionError(f"unsupported extrusion for planar {kind}")
    if _number(entity.dxf.get("thickness", 0), "thickness") != 0:
        raise RevisionError(f"unsupported nonzero thickness for planar {kind}")
    result = {"type": kind}
    if kind == "LINE":
        result.update(start=_vector(entity.dxf.start, "start"), end=_vector(entity.dxf.end, "end"))
        if abs(result["start"][2] - result["end"][2]) > tolerance:
            raise RevisionError("nonplanar LINE is unsupported")
    elif kind == "LWPOLYLINE":
        points = [_vector(p, "polyline vertex") for p in entity.get_points("xyseb")]
        if len(points) < 2:
            raise RevisionError("degenerate LWPOLYLINE needs at least two vertices")
        if any(p[4] != 0 for p in points):
            raise RevisionError("curved LWPOLYLINE bulges are unsupported")
        if _number(entity.dxf.get("const_width", 0), "polyline width") != 0 or any(p[2] or p[3] for p in points):
            raise RevisionError("nonzero LWPOLYLINE widths are unsupported")
        result.update(points=points, closed=bool(entity.closed),
                      elevation=_number(entity.dxf.get("elevation", 0), "elevation"))
    else:
        result.update(center=_vector(entity.dxf.center, "center"),
                      radius=_number(entity.dxf.radius, "radius", positive=True))
        if kind == "ARC":
            result.update(start_angle=_number(entity.dxf.start_angle, "start_angle"),
                          end_angle=_number(entity.dxf.end_angle, "end_angle"))
    return result


def _points(state):
    if state["type"] == "LINE":
        return [state["start"], state["end"]]
    if state["type"] == "LWPOLYLINE":
        return [[p[0], p[1], state["elevation"]] for p in state["points"]]
    center, radius = state["center"], state["radius"]
    if state["type"] == "CIRCLE":
        return [[center[0] - radius, center[1], center[2]],
                [center[0] + radius, center[1], center[2]],
                [center[0], center[1] - radius, center[2]],
                [center[0], center[1] + radius, center[2]]]
    start = state["start_angle"] % 360
    end = state["end_angle"] % 360
    sweep = (end - start) % 360
    angles = [start, end] + [a for a in (0, 90, 180, 270) if (a - start) % 360 <= sweep]
    result = []
    for angle in angles:
        # Exact quadrant coordinates avoid artificial ~1e-15 bounding offsets.
        if angle in (0, 90, 180, 270):
            x, y = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle]
        else:
            x, y = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        result.append([center[0] + radius * x, center[1] + radius * y, center[2]])
    return result


def _bounds(states):
    points = [p for state in states.values() for p in _points(state)]
    for point in points:
        _vector(point, "geometry coordinate")
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _anchor(states, anchor):
    box = _bounds(states)
    axis = 0 if anchor.endswith("x") else 1
    if anchor.startswith("min"):
        return box[axis]
    if anchor.startswith("max"):
        return box[axis + 2]
    return box[axis] / 2 + box[axis + 2] / 2


def _coplanar(states, tolerance):
    elevations = [p[2] for state in states.values() for p in _points(state)]
    if max(elevations) - min(elevations) > tolerance:
        raise RevisionError("constraint geometry must share a planar elevation")


def _segments(state):
    if state["type"] not in _STRAIGHT:
        raise RevisionError(f"unsupported {state['type']} for straight boundary contact or resize")
    points = _points(state)
    pairs = list(zip(points, points[1:]))
    if state["type"] == "LWPOLYLINE" and state["closed"]:
        pairs.append((points[-1], points[0]))
    return pairs


def _boundary_intervals(states, anchor, tolerance):
    if anchor.startswith("center"):
        raise RevisionError("contact requires a min/max boundary edge anchor")
    value = _anchor(states, anchor)
    axis = 0 if anchor.endswith("x") else 1
    other = 1 - axis
    intervals = []
    for state in states.values():
        for a, b in _segments(state):
            if abs(a[axis] - value) <= tolerance and abs(b[axis] - value) <= tolerance:
                lo, hi = sorted((a[other], b[other]))
                if hi - lo > tolerance:
                    intervals.append((lo, hi))
    return intervals


def _has_contact(targets, anchor, reference, reference_anchor, tolerance):
    left = _boundary_intervals(targets, anchor, tolerance)
    right = _boundary_intervals(reference, reference_anchor, tolerance)
    return any(min(b, d) - max(a, c) > tolerance for a, b in left for c, d in right)


def _prepare(doc, operation, tolerance, *, checking=False):
    tolerance = validate_tolerance(tolerance)
    if not isinstance(operation, dict):
        raise RevisionError("constraint operation must be an object")
    kind = operation.get("type")
    allowed = {
        "align": {"type", "targets", "anchor", "coordinate", "reference", "reference_anchor", "offset"},
        "contact": {"type", "targets", "anchor", "reference", "reference_anchor", "offset"},
        "resize_fixed_edge": {"type", "targets", "axis", "fixed", "size"},
        "set_length": {"type", "target", "fixed", "length"},
    }
    if not isinstance(kind, str) or kind not in allowed:
        raise RevisionError(f"unsupported constraint operation: {kind!r}")
    keys = allowed[kind] | ({"expected_geometry"} if checking else set())
    if set(operation) - keys:
        raise RevisionError(f"unknown {kind} fields: {sorted(set(operation) - keys, key=str)}")
    if kind == "set_length":
        required = {"target", "fixed", "length"}
    elif kind == "resize_fixed_edge":
        required = {"targets", "axis", "fixed", "size"}
    else:
        required = {"targets", "anchor"}
    if required - set(operation):
        raise RevisionError(f"missing {kind} fields: {sorted(required - set(operation))}")
    entities = ([resolve_entity(doc, operation["target"])] if kind == "set_length"
                else resolve_entities(doc, operation["targets"]))
    states = {e.dxf.handle: _state(e, tolerance) for e in entities}
    _coplanar(states, tolerance)
    _bounds(states)
    context = {"type": kind, "entities": entities, "states": states, "tolerance": tolerance}
    if kind in {"align", "contact"}:
        anchor = operation["anchor"]
        if not isinstance(anchor, str) or anchor not in _ANCHORS:
            raise RevisionError("invalid anchor: expected min/max/center_x or min/max/center_y")
        has_reference = "reference" in operation
        has_coordinate = "coordinate" in operation
        if has_reference == has_coordinate:
            raise RevisionError("specify exactly one coordinate or reference")
        offset = _number(operation.get("offset", 0), "offset")
        context.update(anchor=anchor, offset=offset)
        if has_reference:
            if "reference_anchor" not in operation:
                raise RevisionError("reference_anchor is required with reference")
            ref_anchor = operation["reference_anchor"]
            if not isinstance(ref_anchor, str) or ref_anchor not in _ANCHORS or ref_anchor[-1] != anchor[-1]:
                raise RevisionError("reference_anchor must use the same axis as anchor")
            ref = resolve_entity(doc, operation["reference"])
            if ref.dxf.handle in states:
                raise RevisionError("reference cannot be part of targets")
            reference = {ref.dxf.handle: _state(ref, tolerance)}
            _coplanar({**states, **reference}, tolerance)
            target_value = _anchor(reference, ref_anchor) + offset
            context.update(reference=reference, reference_anchor=ref_anchor)
        else:
            if "reference_anchor" in operation:
                raise RevisionError("reference_anchor requires reference")
            target_value = _number(operation["coordinate"], "coordinate") + offset
        context["target_value"] = _number(target_value, "target coordinate")
        if kind == "contact":
            if offset != 0:
                raise RevisionError("contact offset must be zero")
            contact = _has_contact(states, anchor, context["reference"], context["reference_anchor"], tolerance)
            if not contact and not checking:
                raise RevisionError("finite contact requires overlapping boundary edge intervals")
    elif kind == "resize_fixed_edge":
        if operation["axis"] not in ("x", "y") or operation["fixed"] not in ("min", "max"):
            raise RevisionError("resize requires axis x/y and fixed min/max")
        size = _number(operation["size"], "size", positive=True)
        axis = operation["axis"]
        for boundary in ("min", "max"):
            if not _boundary_intervals(states, f"{boundary}_{axis}", tolerance):
                raise RevisionError(f"no finite straight boundary edge at {boundary}_{axis}")
        low, high = _anchor(states, f"min_{axis}"), _anchor(states, f"max_{axis}")
        if high - low <= tolerance or size <= tolerance:
            raise RevisionError("resize would collapse a boundary")
        context.update(axis=0 if axis == "x" else 1, fixed=operation["fixed"], size=size, low=low, high=high)
    else:
        if len(entities) != 1 or entities[0].dxftype() != "LINE":
            raise RevisionError("set_length supports only LINE")
        if operation["fixed"] not in ("start", "end"):
            raise RevisionError("set_length fixed must be start or end")
        length = _number(operation["length"], "length", positive=True)
        state = next(iter(states.values()))
        dx, dy = state["end"][0] - state["start"][0], state["end"][1] - state["start"][1]
        current = _number(math.hypot(dx, dy), "source line length")
        if current <= tolerance:
            raise RevisionError("zero or degenerate source line length")
        context.update(fixed=operation["fixed"], length=length, current_length=current, direction=(dx / current, dy / current))
    return context


def _translate(state, axis, amount):
    if state["type"] == "LINE":
        state["start"][axis] += amount
        state["end"][axis] += amount
    elif state["type"] == "LWPOLYLINE":
        for point in state["points"]:
            point[axis] += amount
    else:
        state["center"][axis] += amount


def _propose(context):
    states = context["states"]
    after = deepcopy(states)
    tolerance, kind = context["tolerance"], context["type"]
    if kind in {"align", "contact"}:
        amount = _number(context["target_value"] - _anchor(states, context["anchor"]), "translation")
        if abs(amount) > tolerance:
            for state in after.values():
                _translate(state, 0 if context["anchor"].endswith("x") else 1, amount)
    elif kind == "resize_fixed_edge":
        axis, fixed = context["axis"], context["fixed"]
        moving = context["high"] if fixed == "min" else context["low"]
        position = _number(context["low"] + context["size"] if fixed == "min" else context["high"] - context["size"], "moving boundary")
        if abs(position - moving) > tolerance:
            for state in after.values():
                points = [state["start"], state["end"]] if state["type"] == "LINE" else state["points"]
                for point in points:
                    if abs(point[axis] - moving) <= tolerance:
                        point[axis] = position
                    elif ((fixed == "min" and point[axis] >= position - tolerance)
                          or (fixed == "max" and point[axis] <= position + tolerance)):
                        raise RevisionError("moving boundary would cross or collapse intermediate vertices")
    else:
        if abs(context["length"] - context["current_length"]) > tolerance:
            state = next(iter(after.values()))
            fixed = context["fixed"]
            moving = "end" if fixed == "start" else "start"
            sign = 1 if fixed == "start" else -1
            for axis in (0, 1):
                state[moving][axis] = state[fixed][axis] + sign * context["length"] * context["direction"][axis]
    _bounds(after)
    return after


def _write(entity, state):
    kind = state["type"]
    if kind == "LINE":
        entity.dxf.start, entity.dxf.end = state["start"], state["end"]
    elif kind == "LWPOLYLINE":
        entity.set_points(state["points"], format="xyseb")
    else:
        entity.dxf.center = state["center"]


def _matches(actual, expected, tolerance):
    """Compare evidence recursively while rejecting malformed numeric evidence."""
    if isinstance(actual, dict):
        if not isinstance(expected, dict) or set(actual) != set(expected):
            raise RevisionError("expected_geometry has invalid keys or entity geometry schema")
        return all(_matches(value, expected[key], tolerance) for key, value in actual.items())
    if isinstance(actual, list):
        if not isinstance(expected, list):
            raise RevisionError("expected_geometry has invalid coordinate structure")
        if len(actual) != len(expected):
            return False
        return all(_matches(a, b, tolerance) for a, b in zip(actual, expected))
    if isinstance(actual, bool):
        if not isinstance(expected, bool):
            raise RevisionError("expected_geometry requires boolean closed state")
        return actual == expected
    if isinstance(actual, str):
        if not isinstance(expected, str):
            raise RevisionError("expected_geometry requires a type string")
        return actual == expected
    return abs(actual - _number(expected, "expected_geometry coordinate")) <= tolerance


def _checks(context, states):
    tolerance, kind = context["tolerance"], context["type"]
    if kind in {"align", "contact"}:
        checks = {"anchor_aligned": abs(_anchor(states, context["anchor"]) - context["target_value"]) <= tolerance}
        if kind == "contact":
            checks["finite_contact"] = checks["anchor_aligned"] and _has_contact(
                states, context["anchor"], context["reference"], context["reference_anchor"], tolerance)
        return checks
    if kind == "resize_fixed_edge":
        box = _bounds(states)
        return {"size_matches": abs(box[context["axis"] + 2] - box[context["axis"]] - context["size"]) <= tolerance}
    state = next(iter(states.values()))
    length = math.hypot(state["end"][0] - state["start"][0], state["end"][1] - state["start"][1])
    return {"length_matches": abs(length - context["length"]) <= tolerance}


def apply_constraint(doc, operation, tolerance):
    """Apply one validated absolute constraint and return save/reopen evidence."""
    context = _prepare(doc, operation, tolerance)
    before = context["states"]
    after = _propose(context)
    checks = _checks(context, after)
    if context["type"] == "resize_fixed_edge":
        anchor = f"{context['fixed']}_{'x' if context['axis'] == 0 else 'y'}"
        checks["fixed_edge_preserved"] = abs(_anchor(before, anchor) - _anchor(after, anchor)) <= context["tolerance"]
    elif context["type"] == "set_length":
        old, new = next(iter(before.values())), next(iter(after.values()))
        checks["fixed_endpoint_preserved"] = old[context["fixed"]] == new[context["fixed"]]
    if not all(checks.values()):
        raise RevisionError("candidate geometry does not satisfy the requested constraint")
    touched = [handle for handle in before if before[handle] != after[handle]]
    try:
        for entity in context["entities"]:
            if entity.dxf.handle in touched:
                _write(entity, after[entity.dxf.handle])
    except Exception:
        for entity in context["entities"]:
            if entity.dxf.handle in touched:
                _write(entity, before[entity.dxf.handle])
        raise
    verification = deepcopy(operation)
    verification["expected_geometry"] = deepcopy(after)
    return {"status": "passed", "touched_handles": touched, "created_handles": [], "deleted_handles": [],
            "before": before, "after": after, "checks": checks,
            "verification_operation": verification, "verification": deepcopy(verification)}


def check_constraint(doc, operation, tolerance):
    """Check the relation and, when supplied, its independently saved geometry."""
    context = _prepare(doc, operation, tolerance, checking=True)
    checks = _checks(context, context["states"])
    if "expected_geometry" in operation:
        checks["expected_geometry_matches"] = _matches(
            context["states"], operation["expected_geometry"], context["tolerance"])
        if context["type"] == "resize_fixed_edge":
            checks["fixed_edge_preserved"] = checks["expected_geometry_matches"]
        elif context["type"] == "set_length":
            fixed = context["fixed"]
            checks["fixed_endpoint_preserved"] = all(
                _matches(state[fixed], operation["expected_geometry"][handle][fixed], context["tolerance"])
                for handle, state in context["states"].items())
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "touched_handles": [], "created_handles": [], "deleted_handles": [],
            "before": deepcopy(context["states"]), "after": deepcopy(context["states"])}
