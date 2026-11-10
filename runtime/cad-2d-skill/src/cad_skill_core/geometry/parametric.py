"""Bounded dimension driving for axis-aligned, four-equal-radius rectangles.

These functions rebuild and measure exact geometry. They do not create
AutoCAD live dimensional/geometric constraints or a general sketch solver.
"""

from __future__ import annotations

from math import hypot, isfinite, pi, tan

from pydantic import BaseModel, ConfigDict, model_validator

from .contours import compile_contour
from .entities import BulgeVertex, ContourEntity, Point2D


class RoundedRectangleParameters(BaseModel):
    """Strictly positive dimensions; radius is strictly below half either side.

    The strict upper bound keeps four nonzero straight sides. Capsules and
    zero-radius rectangles are deliberately outside this phase's family.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    width: float
    height: float
    radius: float
    origin: Point2D = (0.0, 0.0)
    clockwise: bool = False

    @model_validator(mode="after")
    def feasible_dimensions(self) -> RoundedRectangleParameters:
        if not all(isfinite(value) for value in (self.width, self.height, self.radius, *self.origin)):
            raise ValueError("rounded rectangle dimensions must be finite")
        if self.width <= 0 or self.height <= 0 or not 0 < self.radius < min(self.width, self.height) / 2:
            raise ValueError("rounded rectangle requires width,height > 0 and 0 < radius < min(width,height)/2")
        return self


def rounded_rectangle(
    parameters: RoundedRectangleParameters, *, layer: str = "OUTLINE", source_id: str = "rounded-rectangle"
) -> ContourEntity:
    """Solve the supported rectangle family with exact quarter-circle bulges."""

    x, y = parameters.origin
    w, h, r = parameters.width, parameters.height, parameters.radius
    b = tan(pi / 8)
    values = [(x + r, y, 0.0), (x + w - r, y, b),
              (x + w, y + r, 0.0), (x + w, y + h - r, b),
              (x + w - r, y + h, 0.0), (x + r, y + h, b),
              (x, y + h - r, 0.0), (x, y + r, b)]
    if parameters.clockwise:
        values = [(values[i][0], values[i][1], -values[(i - 1) % len(values)][2])
                  for i in reversed(range(len(values)))]
    result = ContourEntity(kind="contour", layer=layer, source_id=source_id,
                          vertices=[BulgeVertex(point=(x, y), bulge=b) for x, y, b in values],
                          require_tangent=True)
    compile_contour(result)
    return result


def derive_rounded_rectangle(contour: ContourEntity) -> RoundedRectangleParameters:
    """Measure and verify an exact supported boundary, in either winding.

    Start vertex is arbitrary. Geometry that does not match an axis-aligned
    four-equal-radius rectangle is rejected instead of approximately fitted.
    """

    vertices = compile_contour(contour)
    message = "contour is not a supported axis-aligned rounded rectangle"
    if len(vertices) != 8:
        raise ValueError(message)
    arcs = [v for v in vertices if v[2] != 0]
    if len(arcs) != 4 or any(abs(abs(v[2]) - tan(pi / 8)) > 1e-9 for v in arcs):
        raise ValueError(message)
    min_x, min_y = min(v[0] for v in vertices), min(v[1] for v in vertices)
    width, height = max(v[0] for v in vertices) - min_x, max(v[1] for v in vertices) - min_y
    index = next(i for i, v in enumerate(vertices) if v[2] != 0)
    a, b = vertices[index], vertices[(index + 1) % 8]
    radius = hypot(b[0] - a[0], b[1] - a[1]) * (1 + a[2] * a[2]) / (4 * abs(a[2]))
    parameters = RoundedRectangleParameters(width=width, height=height, radius=radius,
                                            origin=(min_x, min_y), clockwise=a[2] < 0)
    expected = compile_contour(rounded_rectangle(parameters))
    for offset in range(8):
        if all(hypot(v[0] - expected[(i + offset) % 8][0], v[1] - expected[(i + offset) % 8][1]) <= contour.tolerance
               and abs(v[2] - expected[(i + offset) % 8][2]) <= 1e-9
               for i, v in enumerate(vertices)):
            return parameters
    raise ValueError(message)


def update_rounded_rectangle(contour: ContourEntity, **dimension_changes: object) -> ContourEntity:
    """Apply width/height/radius/origin edits atomically after checking feasibility."""

    unsupported = set(dimension_changes) - {"width", "height", "radius", "origin"}
    if unsupported:
        raise ValueError(f"unsupported rounded rectangle parameters: {', '.join(sorted(unsupported))}")
    previous = derive_rounded_rectangle(contour)
    parameters = RoundedRectangleParameters.model_validate({**previous.model_dump(), **dimension_changes})
    result = rounded_rectangle(parameters, layer=contour.layer, source_id=contour.source_id)
    result.tolerance = contour.tolerance
    compile_contour(result)
    return result
