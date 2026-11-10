"""Analytical validation of simple closed line/circular-arc boundaries.

Bulges encode exact circles, never tessellated chord approximations. The
absolute coordinate tolerance is in drawing units. Optional G1 tangency
uses a fixed 1e-7 radian tolerance. This is a boundary validator, not a
general geometric constraint solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan, atan2, cos, hypot, isfinite, pi, radians, sin, sqrt, tan, tau, ulp

from .entities import ContourEntity, ContourLineSegment, Point2D


BulgePoint = tuple[float, float, float]


@dataclass(frozen=True)
class _Edge:
    start: Point2D
    end: Point2D
    bulge: float
    center: Point2D | None = None
    radius: float = 0.0
    angle: float = 0.0
    sweep: float = 0.0


def compile_contour(contour: ContourEntity) -> list[BulgePoint]:
    """Return validated ``(x, y, bulge)`` vertices for a closed LWPOLYLINE.

    Reject discontinuity, open chains, zero length, overlapping segments,
    crossings and nonadjacent touching (including arc tangencies). Two
    complementary semicircles are a valid circle. Segment endpoints within
    ``tolerance`` are identified as the same vertex. Nonzero arc curvature
    below that tolerance, or supporting circles without sufficient floating-
    point resolution, is rejected rather than silently converted to a line.
    """

    tolerance = contour.tolerance
    if not isfinite(tolerance) or tolerance <= 0:
        raise ValueError("contour tolerance must be finite and positive")
    if (contour.segments is None) == (contour.vertices is None):
        raise ValueError("contour requires exactly one of segments or vertices")
    if contour.vertices is not None:
        vertices = [(v.point[0], v.point[1], v.bulge) for v in contour.vertices]
    else:
        segments = contour.segments or []
        endpoints: list[tuple[Point2D, Point2D, float]] = []
        for segment in segments:
            if isinstance(segment, ContourLineSegment):
                _finite(*segment.start, *segment.end)
                endpoints.append((segment.start, segment.end, 0.0))
            else:
                _finite(*segment.center, segment.radius, segment.start_angle, segment.sweep)
                if segment.radius <= tolerance:
                    raise ValueError("contour arc radius must exceed tolerance")
                if not 0 < abs(segment.sweep) < 360:
                    raise ValueError("contour arc sweep must satisfy 0 < abs(sweep) < 360")
                angle = radians(segment.start_angle % 360)
                sweep = radians(segment.sweep)
                start = _point_on_circle(segment.center, segment.radius, angle)
                end = _point_on_circle(segment.center, segment.radius, angle + sweep)
                endpoints.append((start, end, tan(sweep / 4)))
        for index, (_, end, _) in enumerate(endpoints):
            if _distance(end, endpoints[(index + 1) % len(endpoints)][0]) > tolerance:
                reason = "closure" if index == len(endpoints) - 1 else "continuity"
                raise ValueError(f"contour {reason} failed at segment {index}")
        vertices = [(start[0], start[1], bulge) for start, _, bulge in endpoints]
    if len(vertices) < 2:
        raise ValueError("contour requires at least two segments")
    for vertex in vertices:
        _finite(*vertex)
    edges = [
        _edge(vertex, vertices[(index + 1) % len(vertices)], tolerance)
        for index, vertex in enumerate(vertices)
    ]
    if contour.segments is not None:
        for segment, edge in zip(contour.segments, edges, strict=True):
            if not isinstance(segment, ContourLineSegment):
                # A small endpoint snap can greatly change the circle of a
                # near-full arc. Preserve the declared carrier as well as
                # continuity; otherwise reject the ambiguous input.
                if (edge.center is None or _distance(segment.center, edge.center) > tolerance
                        or abs(segment.radius - edge.radius) > tolerance):
                    raise ValueError("contour endpoint snapping changes the declared arc carrier beyond tolerance")
    for i, first in enumerate(edges):
        for j in range(i + 1, len(edges)):
            second = edges[j]
            allowed: list[Point2D] = []
            if j == i + 1:
                allowed.append(first.end)
            if i == 0 and j == len(edges) - 1:
                allowed.append(first.start)
            for point in _intersections(first, second, tolerance):
                if not any(_distance(point, joint) <= tolerance * 4 for joint in allowed):
                    raise ValueError(f"contour self-intersection between segments {i} and {j}")
    if contour.require_tangent:
        for i, first in enumerate(edges):
            second = edges[(i + 1) % len(edges)]
            incoming, outgoing = _tangent(first, at_end=True), _tangent(second, at_end=False)
            if _dot(incoming, outgoing) <= 0 or abs(_cross(incoming, outgoing)) > 1e-7:
                raise ValueError(f"contour is not tangent at joint {(i + 1) % len(edges)}")
    return vertices


def _finite(*values: float) -> None:
    if not all(isfinite(value) for value in values):
        raise ValueError("contour coordinates and geometry must be finite")


def _point_on_circle(center: Point2D, radius: float, angle: float) -> Point2D:
    return center[0] + radius * cos(angle), center[1] + radius * sin(angle)


def _distance(a: Point2D, b: Point2D) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _sub(a: Point2D, b: Point2D) -> Point2D:
    return a[0] - b[0], a[1] - b[1]


def _dot(a: Point2D, b: Point2D) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Point2D, b: Point2D) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _unit(a: Point2D) -> Point2D:
    length = hypot(*a)
    return a[0] / length, a[1] / length


def _edge(first: BulgePoint, following: BulgePoint, tolerance: float) -> _Edge:
    start, end, bulge = first[:2], following[:2], first[2]
    length = _distance(start, end)
    _finite(length)
    if length <= tolerance:
        raise ValueError("contour zero-length segment is not allowed")
    if bulge == 0:
        return _Edge(start, end, bulge)
    sweep = 4 * atan(bulge)
    if not 0 < abs(sweep) < tau:
        raise ValueError("contour arc sweep cannot be represented reliably")
    # Bulge is sagitta / half-chord. An almost straight arc creates an enormous
    # supporting circle; circle intersections can then lose real crossings.
    # Preserve the requested geometry by rejecting unresolved curvature instead
    # of substituting its chord while still emitting an arc.
    sagitta = (length / 2) * abs(bulge)
    if sagitta <= tolerance:
        raise ValueError("contour shallow arc curvature is below tolerance; use an explicit line if intended")
    direction = _unit(_sub(end, start))
    offset = length * (1 / bulge - bulge) / 4
    center = ((start[0] / 2 + end[0] / 2) - direction[1] * offset,
              (start[1] / 2 + end[1] / 2) + direction[0] * offset)
    radius = hypot(length / 2, offset)
    _finite(*center, radius)
    # Keep a conservative roundoff margin for the several circle intersection
    # additions/subtractions. Tightening tolerance does not add float precision.
    if 16 * max(ulp(radius), ulp(center[0]), ulp(center[1])) > tolerance:
        raise ValueError("contour arc supporting circle lacks floating-point precision at the requested tolerance")
    return _Edge(start, end, bulge, center, radius,
                 atan2(start[1] - center[1], start[0] - center[0]), sweep)


def _on_arc(edge: _Edge, point: Point2D, tolerance: float) -> bool:
    assert edge.center is not None
    if abs(_distance(point, edge.center) - edge.radius) > tolerance * 4:
        return False
    angle = atan2(point[1] - edge.center[1], point[0] - edge.center[0])
    delta = ((angle - edge.angle) if edge.sweep > 0 else (edge.angle - angle)) % tau
    angle_tolerance = tolerance / edge.radius
    return delta <= abs(edge.sweep) + angle_tolerance or tau - delta <= angle_tolerance


def _intersections(a: _Edge, b: _Edge, tolerance: float) -> list[Point2D]:
    if a.center is None and b.center is None:
        return _line_line(a, b, tolerance)
    if a.center is None:
        return _line_arc(a, b, tolerance)
    if b.center is None:
        return _line_arc(b, a, tolerance)
    return _arc_arc(a, b, tolerance)


def _line_line(a: _Edge, b: _Edge, tolerance: float) -> list[Point2D]:
    da, db = _unit(_sub(a.end, a.start)), _unit(_sub(b.end, b.start))
    delta = _sub(b.start, a.start)
    determinant = _cross(da, db)
    la, lb = _distance(a.start, a.end), _distance(b.start, b.end)
    if abs(determinant) <= min(1e-14, tolerance / max(la, lb)):
        if abs(_cross(delta, da)) > tolerance:
            return []
        projection = sorted((_dot(delta, da), _dot(_sub(b.end, a.start), da)))
        low, high = max(0.0, projection[0]), min(la, projection[1])
        if high < low - tolerance:
            return []
        if high - low > tolerance:
            raise ValueError("contour overlapping line segments")
        return [(a.start[0] + low * da[0], a.start[1] + low * da[1])]
    ta, tb = _cross(delta, db) / determinant, _cross(delta, da) / determinant
    if -tolerance <= ta <= la + tolerance and -tolerance <= tb <= lb + tolerance:
        return [(a.start[0] + ta * da[0], a.start[1] + ta * da[1])]
    return []


def _line_arc(line: _Edge, arc: _Edge, tolerance: float) -> list[Point2D]:
    assert arc.center is not None
    direction = _unit(_sub(line.end, line.start))
    delta = _sub(arc.center, line.start)
    along, perpendicular = _dot(delta, direction), abs(_cross(delta, direction))
    if perpendicular > arc.radius + tolerance:
        return []
    half_chord = arc.radius * sqrt(max(0.0, 1 - (perpendicular / arc.radius) ** 2))
    length = _distance(line.start, line.end)
    result = []
    for distance in (along - half_chord, along + half_chord):
        if -tolerance <= distance <= length + tolerance:
            point = (line.start[0] + distance * direction[0], line.start[1] + distance * direction[1])
            if _on_arc(arc, point, tolerance):
                result.append(point)
    return result


def _arc_intervals(edge: _Edge) -> list[tuple[float, float]]:
    start = (edge.angle if edge.sweep > 0 else edge.angle + edge.sweep) % tau
    end = start + abs(edge.sweep)
    return [(start, min(tau, end))] + ([(0.0, end - tau)] if end > tau else [])


def _arc_arc(a: _Edge, b: _Edge, tolerance: float) -> list[Point2D]:
    assert a.center is not None and b.center is not None
    distance = _distance(a.center, b.center)
    if distance <= tolerance and abs(a.radius - b.radius) <= tolerance:
        angle_tolerance = tolerance / min(a.radius, b.radius)
        for first in _arc_intervals(a):
            for second in _arc_intervals(b):
                if min(first[1], second[1]) - max(first[0], second[0]) > angle_tolerance:
                    raise ValueError("contour overlapping circular arcs")
        return [p for p in (a.start, a.end, b.start, b.end)
                if _on_arc(a, p, tolerance) and _on_arc(b, p, tolerance)]
    if (distance <= tolerance or distance > a.radius + b.radius + tolerance
            or distance < abs(a.radius - b.radius) - tolerance):
        return []
    scale = max(distance, a.radius, b.radius)
    d, r1, r2 = distance / scale, a.radius / scale, b.radius / scale
    along = (d * d + r1 * r1 - r2 * r2) / (2 * d)
    height = sqrt(max(0.0, r1 * r1 - along * along)) * scale
    direction = _unit(_sub(b.center, a.center))
    middle = (a.center[0] + along * scale * direction[0], a.center[1] + along * scale * direction[1])
    candidates = [(middle[0] - sign * height * direction[1], middle[1] + sign * height * direction[0])
                  for sign in (-1, 1)]
    return [p for p in candidates if _on_arc(a, p, tolerance) and _on_arc(b, p, tolerance)]


def _tangent(edge: _Edge, *, at_end: bool) -> Point2D:
    if edge.center is None:
        return _unit(_sub(edge.end, edge.start))
    radial = _unit(_sub(edge.end if at_end else edge.start, edge.center))
    sign = 1 if edge.sweep > 0 else -1
    return -sign * radial[1], sign * radial[0]
