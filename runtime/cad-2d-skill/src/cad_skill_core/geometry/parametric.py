"""Bounded parametric 2D mechanical part builders and projection checks.

These functions rebuild and measure exact 2D geometry with traceable source IDs,
view partitions, and feature-bound dimensions. They do not create AutoCAD live
dimensional/geometric constraints or a general sketch solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, isclose, isfinite, pi, sin, tan
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contours import compile_contour
from .entities import (
    ArcEntity,
    BulgeVertex,
    CircleEntity,
    CircularEntity,
    ContourArcSegment,
    ContourEntity,
    ContourLineSegment,
    DrawingEntity,
    HatchEntity,
    LineEntity,
    Point2D,
    PolylineEntity,
)


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
    values = [
        (x + r, y, 0.0),
        (x + w - r, y, b),
        (x + w, y + r, 0.0),
        (x + w, y + h - r, b),
        (x + w - r, y + h, 0.0),
        (x + r, y + h, b),
        (x, y + h - r, 0.0),
        (x, y + r, b),
    ]
    if parameters.clockwise:
        values = [
            (values[i][0], values[i][1], -values[(i - 1) % len(values)][2])
            for i in reversed(range(len(values)))
        ]
    result = ContourEntity(
        kind="contour",
        layer=layer,
        source_id=source_id,
        vertices=[BulgeVertex(point=(vx, vy), bulge=vb) for vx, vy, vb in values],
        require_tangent=True,
    )
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
    parameters = RoundedRectangleParameters(
        width=width,
        height=height,
        radius=radius,
        origin=(min_x, min_y),
        clockwise=a[2] < 0,
    )
    expected = compile_contour(rounded_rectangle(parameters))
    for offset in range(8):
        if all(
            hypot(v[0] - expected[(i + offset) % 8][0], v[1] - expected[(i + offset) % 8][1]) <= contour.tolerance
            and abs(v[2] - expected[(i + offset) % 8][2]) <= 1e-9
            for i, v in enumerate(vertices)
        ):
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


# ---------------------------------------------------------------------------
# Phase 2 Parametric Part Builders & Orthographic Consistency Checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParametricPartResult:
    """Generated 2D entities, view partition, dimensions, and metadata."""

    views: list[str]
    entities: list[DrawingEntity]
    view_entity_indices: dict[str, list[int]]
    dimensions: list[dict[str, Any]]
    parameters: dict[str, Any]


class FlangeParameters(BaseModel):
    """Validated parameters for a circular flange with optional bolt circle and section."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    outer_diameter: float
    bore_diameter: float = 0.0
    thickness: float = 20.0
    bolt_circle_diameter: float | None = None
    hole_diameter: float | None = None
    hole_count: int = 0

    @model_validator(mode="after")
    def validate_flange_geometry(self) -> FlangeParameters:
        nums = [self.outer_diameter, self.bore_diameter, self.thickness]
        if self.bolt_circle_diameter is not None:
            nums.append(self.bolt_circle_diameter)
        if self.hole_diameter is not None:
            nums.append(self.hole_diameter)
        if not all(isfinite(v) for v in nums):
            raise ValueError("flange dimensions must be finite")
        if self.outer_diameter <= 0 or self.thickness <= 0:
            raise ValueError("flange outer_diameter and thickness must be positive")
        if self.bore_diameter < 0 or self.bore_diameter >= self.outer_diameter:
            raise ValueError("flange bore_diameter must satisfy 0 <= bore_diameter < outer_diameter")
        if self.hole_count < 0:
            raise ValueError("flange hole_count cannot be negative")
        if self.hole_count > 0:
            if self.bolt_circle_diameter is None or self.hole_diameter is None:
                raise ValueError("bolt_circle_diameter and hole_diameter are required when hole_count > 0")
            if self.hole_diameter <= 0:
                raise ValueError("hole_diameter must be positive")
            min_bcd = self.bore_diameter + self.hole_diameter + 2.0
            max_bcd = self.outer_diameter - self.hole_diameter - 2.0
            if not (min_bcd <= self.bolt_circle_diameter <= max_bcd):
                raise ValueError("bolt holes intersect bore or outer rim")
            if self.hole_count >= 2:
                chord = self.bolt_circle_diameter * sin(pi / self.hole_count)
                if chord <= self.hole_diameter * 1.1:
                    raise ValueError("adjacent bolt holes overlap on bolt circle")
        return self


def build_flange_geometry(
    params: FlangeParameters,
    *,
    source_id: str = "flange",
    include_right_section: bool = True,
    three_views: bool = True,
) -> ParametricPartResult:
    """Build standard GB/T flat-laid three views for a flange:
    - front (主视图, upper-left): horizontal full-section elevation (width=OD, height=thickness)
    - top   (俯视图, lower-left): circular plan view with pitch circle & bolt holes (长对正)
    - left  (左视图, upper-right): horizontal side elevation (高平齐 & 宽相等)
    """

    r_out = params.outer_diameter / 2.0
    r_bore = params.bore_diameter / 2.0
    th = params.thickness
    ext = r_out * 1.06
    entities: list[DrawingEntity] = []
    dims: list[dict[str, Any]] = []

    if include_right_section and three_views:
        front_indices: list[int] = []
        top_indices: list[int] = []
        left_indices: list[int] = []

        def add_front(ent: DrawingEntity) -> int:
            idx = len(entities)
            entities.append(ent)
            front_indices.append(idx)
            return idx

        def add_top(ent: DrawingEntity) -> int:
            idx = len(entities)
            entities.append(ent)
            top_indices.append(idx)
            return idx

        def add_left(ent: DrawingEntity) -> int:
            idx = len(entities)
            entities.append(ent)
            left_indices.append(idx)
            return idx

        # 1. Front View (主视图 全剖: width = 2*r_out, height = th)
        sec_front_idx = add_front(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(-r_out, 0.0), (r_out, 0.0), (r_out, th), (-r_out, th)],
                closed=True,
                source_id=source_id,
            )
        )
        add_front(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, th + 2.0), source_id=source_id))

        has_sec_holes = bool(params.hole_count >= 2 and params.bolt_circle_diameter and params.hole_diameter)
        r_bcd = (params.bolt_circle_diameter or 0.0) / 2.0
        r_hole = (params.hole_diameter or 0.0) / 2.0

        if r_bore > 0:
            add_front(LineEntity(kind="line", layer="OUTLINE", start=(-r_bore, 0.0), end=(-r_bore, th), source_id=source_id))
            add_front(LineEntity(kind="line", layer="OUTLINE", start=(r_bore, 0.0), end=(r_bore, th), source_id=source_id))

        if has_sec_holes:
            for sign in (-1.0, 1.0):
                hc_x = sign * r_bcd
                add_front(LineEntity(kind="line", layer="OUTLINE", start=(hc_x - r_hole, 0.0), end=(hc_x - r_hole, th), source_id=source_id))
                add_front(LineEntity(kind="line", layer="OUTLINE", start=(hc_x + r_hole, 0.0), end=(hc_x + r_hole, th), source_id=source_id))
                add_front(LineEntity(kind="line", layer="CENTER", start=(hc_x, -1.8), end=(hc_x, th + 1.8), source_id=source_id))
            pitch_span_idx = add_front(
                LineEntity(kind="line", layer="CENTER", start=(-r_bcd, 0.0), end=(r_bcd, 0.0), source_id=source_id)
            )
            # Hatch the 4 solid metal segments (or 3 if r_bore == 0)
            add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_out, 0.0), (-r_bcd - r_hole, 0.0), (-r_bcd - r_hole, th), (-r_out, th)], pattern="ANSI31", source_id=source_id))
            add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bcd + r_hole, 0.0), (r_out, 0.0), (r_out, th), (r_bcd + r_hole, th)], pattern="ANSI31", source_id=source_id))
            if r_bore > 0:
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_bcd + r_hole, 0.0), (-r_bore, 0.0), (-r_bore, th), (-r_bcd + r_hole, th)], pattern="ANSI31", source_id=source_id))
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bore, 0.0), (r_bcd - r_hole, 0.0), (r_bcd - r_hole, th), (r_bore, th)], pattern="ANSI31", source_id=source_id))
            else:
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_bcd + r_hole, 0.0), (r_bcd - r_hole, 0.0), (r_bcd - r_hole, th), (-r_bcd + r_hole, th)], pattern="ANSI31", source_id=source_id))
            dims.append(
                {
                    "kind": "diameter",
                    "value": params.bolt_circle_diameter,
                    "source_id": source_id,
                    "owner_view": "front",
                    "target_entity_index": pitch_span_idx,
                    "orientation": "horizontal",
                    "prefix": "⌀",
                }
            )
        else:
            if r_bore > 0:
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_out, 0.0), (-r_bore, 0.0), (-r_bore, th), (-r_out, th)], pattern="ANSI31", source_id=source_id))
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bore, 0.0), (r_out, 0.0), (r_out, th), (r_bore, th)], pattern="ANSI31", source_id=source_id))
            else:
                add_front(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_out, 0.0), (r_out, 0.0), (r_out, th), (-r_out, th)], pattern="ANSI31", source_id=source_id))

        dims.append(
            {
                "kind": "linear",
                "value": th,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": sec_front_idx,
                "orientation": "vertical",
            }
        )

        # 2. Top View (俯视图: circular plan view below front, width = 2*r_out 长对正!)
        outer_top_idx = add_top(
            CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_out, source_id=source_id)
        )
        if r_bore > 0:
            bore_top_idx = add_top(
                CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_bore, source_id=source_id)
            )
            dims.append(
                {
                    "kind": "diameter",
                    "value": params.bore_diameter,
                    "source_id": source_id,
                    "owner_view": "top",
                    "target_entity_index": bore_top_idx,
                    "orientation": "diameter",
                }
            )
        add_top(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=source_id))
        add_top(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=source_id))

        hole_ys: list[float] = []
        first_hole_idx: int | None = None
        if params.hole_count > 0 and params.bolt_circle_diameter and params.hole_diameter:
            add_top(
                CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_bcd, source_id=source_id)
            )
            for k in range(params.hole_count):
                ang = 2.0 * pi * k / params.hole_count
                hx = round(r_bcd * cos(ang), 6)
                hy = round(r_bcd * sin(ang), 6)
                hole_ys.append(hy)
                h_idx = add_top(
                    CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=r_hole, source_id=source_id)
                )
                if first_hole_idx is None:
                    first_hole_idx = h_idx
            if first_hole_idx is not None:
                hole_prefix = f"{params.hole_count}×⌀" if params.hole_count > 1 else "⌀"
                dims.append(
                    {
                        "kind": "diameter",
                        "value": params.hole_diameter,
                        "source_id": source_id,
                        "owner_view": "top",
                        "target_entity_index": first_hole_idx,
                        "orientation": "diameter",
                        "prefix": hole_prefix,
                    }
                )
                if params.hole_count == 1:
                    dims.append(
                        {
                            "kind": "diameter",
                            "value": params.bolt_circle_diameter,
                            "source_id": source_id,
                            "owner_view": "front",
                            "target_entity_index": sec_front_idx,
                            "orientation": "horizontal",
                        }
                    )

        dims.append(
            {
                "kind": "diameter",
                "value": params.outer_diameter,
                "source_id": source_id,
                "owner_view": "top",
                "target_entity_index": outer_top_idx,
                "orientation": "horizontal",
                "prefix": "⌀",
            }
        )

        # 3. Left View (左视图: side elevation upper-right, width = 2*r_out 宽相等, height = th 高平齐!)
        left_box_idx = add_left(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(-r_out, 0.0), (r_out, 0.0), (r_out, th), (-r_out, th)],
                closed=True,
                source_id=source_id,
            )
        )
        add_left(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, th + 2.0), source_id=source_id))
        if r_bore > 0:
            add_left(LineEntity(kind="line", layer="HIDDEN", start=(-r_bore, 0.0), end=(-r_bore, th), source_id=source_id))
            add_left(LineEntity(kind="line", layer="HIDDEN", start=(r_bore, 0.0), end=(r_bore, th), source_id=source_id))
        if params.hole_count > 0 and params.hole_diameter:
            for hy_u in sorted({round(v, 4) for v in hole_ys if abs(v) > 1e-3}):
                add_left(LineEntity(kind="line", layer="HIDDEN", start=(hy_u - r_hole, 0.0), end=(hy_u - r_hole, th), source_id=source_id))
                add_left(LineEntity(kind="line", layer="HIDDEN", start=(hy_u + r_hole, 0.0), end=(hy_u + r_hole, th), source_id=source_id))
        dims.append(
            {
                "kind": "diameter",
                "value": params.outer_diameter,
                "source_id": source_id,
                "owner_view": "left",
                "target_entity_index": left_box_idx,
                "orientation": "horizontal",
                "prefix": "⌀",
            }
        )

        return ParametricPartResult(
            views=["front", "top", "left"],
            entities=entities,
            view_entity_indices={"front": front_indices, "top": top_indices, "left": left_indices},
            dimensions=dims,
            parameters={
                "part_family": "flange",
                "outer_diameter_mm": params.outer_diameter,
                "bore_diameter_mm": params.bore_diameter,
                "thickness_mm": params.thickness,
                "hole_count": params.hole_count,
            },
        )

    # Legacy 1-view or 2-view fallback when include_right_section=False or three_views=False
    front_indices = []

    def add_front_legacy(ent: DrawingEntity) -> int:
        idx = len(entities)
        entities.append(ent)
        front_indices.append(idx)
        return idx

    outer_idx = add_front_legacy(
        CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_out, source_id=source_id)
    )
    dims.append(
        {
            "kind": "diameter",
            "value": params.outer_diameter,
            "source_id": source_id,
            "owner_view": "front",
            "target_entity_index": outer_idx,
            "orientation": "horizontal",
            "prefix": "⌀",
        }
    )
    if r_bore > 0:
        bore_idx = add_front_legacy(
            CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_bore, source_id=source_id)
        )
        dims.append(
            {
                "kind": "diameter",
                "value": params.bore_diameter,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": bore_idx,
                "orientation": "diameter",
            }
        )
    add_front_legacy(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=source_id))
    add_front_legacy(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=source_id))
    if params.hole_count > 0 and params.bolt_circle_diameter and params.hole_diameter:
        r_bcd = params.bolt_circle_diameter / 2.0
        r_hole = params.hole_diameter / 2.0
        add_front_legacy(
            CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_bcd, source_id=source_id)
        )
        first_h: int | None = None
        for k in range(params.hole_count):
            ang = 2.0 * pi * k / params.hole_count
            h_i = add_front_legacy(
                CircularEntity(
                    kind="circular",
                    layer="OUTLINE",
                    center=(round(r_bcd * cos(ang), 6), round(r_bcd * sin(ang), 6)),
                    radius=r_hole,
                    source_id=source_id,
                )
            )
            if first_h is None:
                first_h = h_i
        if first_h is not None:
            dims.append(
                {
                    "kind": "diameter",
                    "value": params.hole_diameter,
                    "source_id": source_id,
                    "owner_view": "front",
                    "target_entity_index": first_h,
                    "orientation": "diameter",
                    "prefix": f"{params.hole_count}×⌀" if params.hole_count > 1 else "⌀",
                }
            )

    views = ["front"]
    view_map: dict[str, list[int]] = {"front": front_indices}
    if include_right_section:
        sec_indices: list[int] = []

        def add_sec(ent: DrawingEntity) -> int:
            idx = len(entities)
            entities.append(ent)
            sec_indices.append(idx)
            return idx

        sec_idx = add_sec(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(0.0, -r_out), (th, -r_out), (th, r_out), (0.0, r_out)],
                closed=True,
                source_id=source_id,
            )
        )
        if r_bore > 0:
            add_sec(LineEntity(kind="line", layer="OUTLINE", start=(0.0, r_bore), end=(th, r_bore), source_id=source_id))
            add_sec(LineEntity(kind="line", layer="OUTLINE", start=(0.0, -r_bore), end=(th, -r_bore), source_id=source_id))
            add_sec(HatchEntity(kind="hatch", layer="HATCH", boundary=[(0.0, r_bore), (th, r_bore), (th, r_out), (0.0, r_out)], pattern="ANSI31", source_id=source_id))
            add_sec(HatchEntity(kind="hatch", layer="HATCH", boundary=[(0.0, -r_out), (th, -r_out), (th, -r_bore), (0.0, -r_bore)], pattern="ANSI31", source_id=source_id))
        else:
            add_sec(HatchEntity(kind="hatch", layer="HATCH", boundary=[(0.0, -r_out), (th, -r_out), (th, r_out), (0.0, r_out)], pattern="ANSI31", source_id=source_id))
        add_sec(LineEntity(kind="line", layer="CENTER", start=(-2.0, 0.0), end=(th + 2.0, 0.0), source_id=source_id))
        views.append("right")
        view_map["right"] = sec_indices
        dims.append(
            {
                "kind": "linear",
                "value": th,
                "source_id": source_id,
                "owner_view": "right",
                "target_entity_index": sec_idx,
                "orientation": "horizontal",
            }
        )

    return ParametricPartResult(
        views=views,
        entities=entities,
        view_entity_indices=view_map,
        dimensions=dims,
        parameters={
            "part_family": "flange",
            "outer_diameter_mm": params.outer_diameter,
            "bore_diameter_mm": params.bore_diameter,
            "thickness_mm": params.thickness,
            "hole_count": params.hole_count,
        },
    )


class SteppedShaftParameters(BaseModel):
    """Validated parameters for a multi-stepped cylindrical shaft."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    steps: list[tuple[float, float]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_steps(self) -> SteppedShaftParameters:
        for idx, (length, diameter) in enumerate(self.steps, start=1):
            if not (isfinite(length) and isfinite(diameter)) or length <= 0 or diameter <= 0:
                raise ValueError(f"step {idx} length and diameter must be positive finite numbers")
        return self


def build_stepped_shaft_geometry(
    params: SteppedShaftParameters,
    *,
    source_id: str = "shaft",
    include_right_view: bool = True,
    three_views: bool = True,
) -> ParametricPartResult:
    """Build standard GB/T three views (front, top, left) for a stepped shaft."""

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    dims: list[dict[str, Any]] = []

    def add_front(ent: DrawingEntity) -> int:
        idx = len(entities)
        entities.append(ent)
        front_indices.append(idx)
        return idx

    x_cursor = 0.0
    step_boxes: list[int] = []
    for length, diameter in params.steps:
        r = diameter / 2.0
        box_idx = add_front(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(x_cursor, -r), (x_cursor + length, -r), (x_cursor + length, r), (x_cursor, r)],
                closed=True,
                source_id=source_id,
            )
        )
        step_boxes.append(box_idx)
        x_cursor += length

    total_length = x_cursor
    max_diameter = max(d for _, d in params.steps)
    axis_idx = add_front(
        LineEntity(kind="line", layer="CENTER", start=(0.0, 0.0), end=(total_length, 0.0), source_id=source_id)
    )
    if include_right_view and three_views and len(params.steps) >= 2:
        dims.append(
            {
                "kind": "linear",
                "value": params.steps[0][0],
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": step_boxes[0],
                "orientation": "horizontal",
            }
        )
    else:
        dims.append(
            {
                "kind": "linear",
                "value": total_length,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": axis_idx,
                "orientation": "horizontal",
            }
        )
    max_step_idx = max(range(len(params.steps)), key=lambda i: params.steps[i][1])
    dims.append(
        {
            "kind": "diameter",
            "value": max_diameter,
            "source_id": source_id,
            "owner_view": "front",
            "target_entity_index": step_boxes[max_step_idx],
            "orientation": "vertical",
            "prefix": "⌀",
        }
    )

    views = ["front"]
    view_map: dict[str, list[int]] = {"front": front_indices}

    if include_right_view and three_views:
        # Top view (俯视图: 长对正 width == total_length, height == max_diameter)
        top_indices: list[int] = []
        tx = 0.0
        top_step_boxes: list[int] = []
        for length, diameter in params.steps:
            r = diameter / 2.0
            tb_idx = len(entities)
            top_indices.append(tb_idx)
            entities.append(
                PolylineEntity(
                    kind="polyline",
                    layer="OUTLINE",
                    vertices=[(tx, -r), (tx + length, -r), (tx + length, r), (tx, r)],
                    closed=True,
                    source_id=source_id,
                )
            )
            top_step_boxes.append(tb_idx)
            tx += length
        top_axis_idx = len(entities)
        top_indices.append(top_axis_idx)
        entities.append(
            LineEntity(kind="line", layer="CENTER", start=(0.0, 0.0), end=(total_length, 0.0), source_id=source_id)
        )
        views.append("top")
        view_map["top"] = top_indices
        if len(params.steps) >= 2:
            dims.append(
                {
                    "kind": "linear",
                    "value": total_length,
                    "source_id": source_id,
                    "owner_view": "top",
                    "target_entity_index": top_axis_idx,
                    "orientation": "horizontal",
                }
            )
            dims.append(
                {
                    "kind": "diameter",
                    "value": params.steps[-1][1],
                    "source_id": source_id,
                    "owner_view": "top",
                    "target_entity_index": top_step_boxes[-1],
                    "orientation": "vertical",
                    "prefix": "⌀",
                }
            )

        # Left end-view (左视图: 高平齐 & 宽相等 concentric step circles)
        left_indices: list[int] = []
        unique_radii = sorted({round(d / 2.0, 6) for _, d in params.steps}, reverse=True)
        outer_end_idx: int | None = None
        for r in unique_radii:
            c_idx = len(entities)
            left_indices.append(c_idx)
            entities.append(
                CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r, source_id=source_id)
            )
            if outer_end_idx is None:
                outer_end_idx = c_idx
        max_r = max_diameter / 2.0
        for start, end in (((-max_r, 0.0), (max_r, 0.0)), ((0.0, -max_r), (0.0, max_r))):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="CENTER", start=start, end=end, source_id=source_id))
        views.append("left")
        view_map["left"] = left_indices
        if outer_end_idx is not None:
            dims.append(
                {
                    "kind": "diameter",
                    "value": max_diameter,
                    "source_id": source_id,
                    "owner_view": "left",
                    "target_entity_index": outer_end_idx,
                    "orientation": "horizontal",
                    "prefix": "⌀",
                }
            )
    elif include_right_view:
        right_indices: list[int] = []
        unique_radii = sorted({round(d / 2.0, 6) for _, d in params.steps}, reverse=True)
        for r in unique_radii:
            right_indices.append(len(entities))
            entities.append(
                CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r, source_id=source_id)
            )
        max_r = max_diameter / 2.0
        for start, end in (((-max_r, 0.0), (max_r, 0.0)), ((0.0, -max_r), (0.0, max_r))):
            right_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="CENTER", start=start, end=end, source_id=source_id))
        views.append("right")
        view_map["right"] = right_indices

    return ParametricPartResult(
        views=views,
        entities=entities,
        view_entity_indices=view_map,
        dimensions=dims,
        parameters={
            "part_family": "stepped-shaft",
            "total_length_mm": total_length,
            "max_diameter_mm": max_diameter,
        },
    )


class SlottedBracketParameters(BaseModel):
    """Validated parameters for a rounded rectangular bracket plate with central slot."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    width: float
    height: float
    thickness: float = 12.0
    corner_radius: float = 8.0
    slot_length: float = 40.0
    slot_width: float = 14.0
    hole_diameter: float | None = None
    hole_inset: float = 16.0

    @model_validator(mode="after")
    def validate_bracket(self) -> SlottedBracketParameters:
        vals = [
            self.width,
            self.height,
            self.thickness,
            self.corner_radius,
            self.slot_length,
            self.slot_width,
            self.hole_inset,
        ]
        if self.hole_diameter is not None:
            vals.append(self.hole_diameter)
        if not all(isfinite(v) for v in vals):
            raise ValueError("bracket parameters must be finite")
        if self.width <= 0 or self.height <= 0 or self.thickness <= 0:
            raise ValueError("bracket width, height, and thickness must be positive")
        if not (0 < self.corner_radius < min(self.width, self.height) / 2.0):
            raise ValueError("corner_radius must satisfy 0 < corner_radius < min(width, height)/2")
        if self.slot_width <= 0 or self.slot_length <= self.slot_width:
            raise ValueError("slot requires slot_length > slot_width > 0")
        if self.slot_length >= self.width - 8.0 or self.slot_width >= self.height - 8.0:
            raise ValueError("central slot leaves insufficient wall thickness inside bracket")
        if self.hole_diameter is not None:
            if self.hole_diameter <= 0:
                raise ValueError("hole_diameter must be positive")
            if self.hole_inset <= self.hole_diameter / 2.0 + 2.0 or self.hole_inset >= min(self.width, self.height) / 2.0:
                raise ValueError("hole_inset places mounting holes outside valid plate margin")
        return self


def build_slotted_bracket_geometry(
    params: SlottedBracketParameters,
    *,
    source_id: str = "bracket",
    include_top_view: bool = True,
    three_views: bool = True,
    flat_orientation: bool = False,
) -> ParametricPartResult:
    """Build standard GB/T three views (front, top, left) for a slotted bracket plate."""

    entities: list[DrawingEntity] = []
    dims: list[dict[str, Any]] = []
    sr = params.slot_width / 2.0
    half_straight = (params.slot_length - params.slot_width) / 2.0

    if flat_orientation and include_top_view and three_views:
        # Z-up flat-laid bracket:
        # - front (主视图): width x thickness front elevation with hidden slot & hole walls
        # - top   (俯视图): width x height rounded plate plan with central slot & 4 holes (长对正)
        # - left  (左视图): height x thickness side elevation (高平齐 & 宽相等)
        front_indices: list[int] = []
        top_indices: list[int] = []
        left_indices: list[int] = []

        f_box = len(entities)
        front_indices.append(f_box)
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(-params.width / 2.0, 0.0), (params.width / 2.0, 0.0), (params.width / 2.0, params.thickness), (-params.width / 2.0, params.thickness)],
                closed=True,
                source_id=source_id,
            )
        )
        front_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -1.8), end=(0.0, params.thickness + 1.8), source_id=source_id))
        for sx in (-params.slot_length / 2.0, params.slot_length / 2.0):
            front_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(sx, 0.0), end=(sx, params.thickness), source_id=source_id))
        if params.hole_diameter is not None:
            hx = params.width / 2.0 - params.hole_inset
            hr = params.hole_diameter / 2.0
            for sx in (-hx, hx):
                for dx_h in (-hr, hr):
                    front_indices.append(len(entities))
                    entities.append(LineEntity(kind="line", layer="HIDDEN", start=(sx + dx_h, 0.0), end=(sx + dx_h, params.thickness), source_id=source_id))

        # Top view (俯视图): rounded plate contour + obround slot + 4 corner holes
        plate_top_idx = len(entities)
        top_indices.append(plate_top_idx)
        entities.append(
            rounded_rectangle(
                RoundedRectangleParameters(
                    width=params.width,
                    height=params.height,
                    radius=params.corner_radius,
                    origin=(-params.width / 2.0, -params.height / 2.0),
                ),
                layer="OUTLINE",
                source_id=source_id,
            )
        )
        slot_contour_f = ContourEntity(
            kind="contour",
            layer="OUTLINE",
            require_tangent=True,
            segments=[
                ContourLineSegment(kind="line", start=(-half_straight, -sr), end=(half_straight, -sr)),
                ContourArcSegment(kind="arc", center=(half_straight, 0.0), radius=sr, start_angle=-90.0, sweep=180.0),
                ContourLineSegment(kind="line", start=(half_straight, sr), end=(-half_straight, sr)),
                ContourArcSegment(kind="arc", center=(-half_straight, 0.0), radius=sr, start_angle=90.0, sweep=180.0),
            ],
            source_id=source_id,
        )
        compile_contour(slot_contour_f)
        slot_top_idx = len(entities)
        top_indices.append(slot_top_idx)
        entities.append(slot_contour_f)
        top_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=(-params.width * 0.54, 0.0), end=(params.width * 0.54, 0.0), source_id=source_id))
        top_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -params.height * 0.54), end=(0.0, params.height * 0.54), source_id=source_id))

        tr_hole_idx: int | None = None
        if params.hole_diameter is not None:
            hx = params.width / 2.0 - params.hole_inset
            hy = params.height / 2.0 - params.hole_inset
            hr = params.hole_diameter / 2.0
            for sx in (-1.0, 1.0):
                for sy in (-1.0, 1.0):
                    h_idx = len(entities)
                    top_indices.append(h_idx)
                    entities.append(
                        CircularEntity(kind="circular", layer="OUTLINE", center=(sx * hx, sy * hy), radius=hr, source_id=source_id)
                    )
                    if sx > 0 and sy > 0:
                        tr_hole_idx = h_idx

        # Left view (左视图): height x thickness side elevation
        l_box = len(entities)
        left_indices.append(l_box)
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(-params.height / 2.0, 0.0), (params.height / 2.0, 0.0), (params.height / 2.0, params.thickness), (-params.height / 2.0, params.thickness)],
                closed=True,
                source_id=source_id,
            )
        )
        left_indices.append(len(entities))
        entities.append(LineEntity(kind="line", layer="CENTER", start=(0.0, -1.8), end=(0.0, params.thickness + 1.8), source_id=source_id))
        for sy in (-sr, sr):
            left_indices.append(len(entities))
            entities.append(LineEntity(kind="line", layer="HIDDEN", start=(sy, 0.0), end=(sy, params.thickness), source_id=source_id))

        dims = [
            {"kind": "linear", "value": params.slot_length, "source_id": source_id, "owner_view": "front", "target_entity_index": f_box, "orientation": "horizontal"}
            if False
            else {"kind": "linear", "value": params.thickness, "source_id": source_id, "owner_view": "front", "target_entity_index": f_box, "orientation": "vertical"},
            {"kind": "linear", "value": params.slot_length, "source_id": source_id, "owner_view": "front", "target_entity_index": f_box - 2 if False else slot_top_idx, "orientation": "horizontal"},
        ]
        # Cleanly assign non-overlapping dimensions across front, top, and left:
        dims = [
            {"kind": "linear", "value": params.thickness, "source_id": source_id, "owner_view": "front", "target_entity_index": f_box, "orientation": "vertical"},
            {"kind": "linear", "value": params.width, "source_id": source_id, "owner_view": "top", "target_entity_index": plate_top_idx, "orientation": "horizontal"},
            {"kind": "linear", "value": params.height, "source_id": source_id, "owner_view": "top", "target_entity_index": plate_top_idx, "orientation": "vertical"},
            {"kind": "linear", "value": params.height, "source_id": source_id, "owner_view": "left", "target_entity_index": l_box, "orientation": "horizontal"},
        ]
        if tr_hole_idx is not None and params.hole_diameter is not None:
            dims.append(
                {
                    "kind": "diameter",
                    "value": params.hole_diameter,
                    "source_id": source_id,
                    "owner_view": "top",
                    "target_entity_index": tr_hole_idx,
                    "orientation": "diameter",
                    "prefix": "4×⌀",
                }
            )
        return ParametricPartResult(
            views=["front", "top", "left"],
            entities=entities,
            view_entity_indices={"front": front_indices, "top": top_indices, "left": left_indices},
            dimensions=dims,
            parameters={
                "part_family": "slotted-bracket",
                "width_mm": params.width,
                "height_mm": params.height,
                "thickness_mm": params.thickness,
            },
        )

    front_indices: list[int] = []

    def add_front(ent: DrawingEntity) -> int:
        idx = len(entities)
        entities.append(ent)
        front_indices.append(idx)
        return idx

    outer_plate = rounded_rectangle(
        RoundedRectangleParameters(
            width=params.width,
            height=params.height,
            radius=params.corner_radius,
            origin=(-params.width / 2.0, -params.height / 2.0),
        ),
        layer="OUTLINE",
        source_id=source_id,
    )
    plate_idx = add_front(outer_plate)

    # Central tangent obround slot
    sr = params.slot_width / 2.0
    half_straight = (params.slot_length - params.slot_width) / 2.0
    slot_contour = ContourEntity(
        kind="contour",
        layer="OUTLINE",
        require_tangent=True,
        segments=[
            ContourLineSegment(kind="line", start=(-half_straight, -sr), end=(half_straight, -sr)),
            ContourArcSegment(kind="arc", center=(half_straight, 0.0), radius=sr, start_angle=-90.0, sweep=180.0),
            ContourLineSegment(kind="line", start=(half_straight, sr), end=(-half_straight, sr)),
            ContourArcSegment(kind="arc", center=(-half_straight, 0.0), radius=sr, start_angle=90.0, sweep=180.0),
        ],
        source_id=source_id,
    )
    compile_contour(slot_contour)
    slot_idx = add_front(slot_contour)
    add_front(LineEntity(kind="line", layer="CENTER", start=(-params.width * 0.54, 0.0), end=(params.width * 0.54, 0.0), source_id=source_id))
    add_front(LineEntity(kind="line", layer="CENTER", start=(0.0, -params.height * 0.54), end=(0.0, params.height * 0.54), source_id=source_id))

    top_right_hole_idx: int | None = None
    if params.hole_diameter is not None:
        hx = params.width / 2.0 - params.hole_inset
        hy = params.height / 2.0 - params.hole_inset
        hr = params.hole_diameter / 2.0
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                h_idx = add_front(
                    CircularEntity(
                        kind="circular",
                        layer="OUTLINE",
                        center=(sx * hx, sy * hy),
                        radius=hr,
                        source_id=source_id,
                    )
                )
                if sx > 0 and sy > 0:
                    top_right_hole_idx = h_idx

    dims.append(
        {
            "kind": "linear",
            "value": params.slot_length,
            "source_id": source_id,
            "owner_view": "front",
            "target_entity_index": slot_idx,
            "orientation": "horizontal",
        }
    )
    dims.append(
        {
            "kind": "linear",
            "value": params.height,
            "source_id": source_id,
            "owner_view": "front",
            "target_entity_index": plate_idx,
            "orientation": "vertical",
        }
    )
    if top_right_hole_idx is not None and params.hole_diameter is not None:
        dims.append(
            {
                "kind": "diameter",
                "value": params.hole_diameter,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": top_right_hole_idx,
                "orientation": "diameter",
                "prefix": "4×⌀",
            }
        )

    views = ["front"]
    view_map: dict[str, list[int]] = {"front": front_indices}

    if include_top_view:
        top_indices: list[int] = []
        top_idx = len(entities)
        entities.append(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[
                    (-params.width / 2.0, 0.0),
                    (params.width / 2.0, 0.0),
                    (params.width / 2.0, params.thickness),
                    (-params.width / 2.0, params.thickness),
                ],
                closed=True,
                source_id=source_id,
            )
        )
        top_indices.append(top_idx)
        for sx in (-params.slot_length / 2.0, params.slot_length / 2.0):
            top_indices.append(len(entities))
            entities.append(
                LineEntity(
                    kind="line",
                    layer="HIDDEN",
                    start=(sx, 0.0),
                    end=(sx, params.thickness),
                    source_id=source_id,
                )
            )
        views.append("top")
        view_map["top"] = top_indices
        dims.append(
            {
                "kind": "linear",
                "value": params.width,
                "source_id": source_id,
                "owner_view": "top",
                "target_entity_index": top_idx,
                "orientation": "horizontal",
            }
        )

        if three_views:
            # Left view (左视图: width == thickness 宽相等 with top, height == params.height 高平齐 with front!)
            left_indices: list[int] = []
            left_box_idx = len(entities)
            left_indices.append(left_box_idx)
            entities.append(
                PolylineEntity(
                    kind="polyline",
                    layer="OUTLINE",
                    vertices=[
                        (0.0, -params.height / 2.0),
                        (params.thickness, -params.height / 2.0),
                        (params.thickness, params.height / 2.0),
                        (0.0, params.height / 2.0),
                    ],
                    closed=True,
                    source_id=source_id,
                )
            )
            for sy in (-sr, sr):
                left_indices.append(len(entities))
                entities.append(
                    LineEntity(kind="line", layer="HIDDEN", start=(0.0, sy), end=(params.thickness, sy), source_id=source_id)
                )
            left_indices.append(len(entities))
            entities.append(
                LineEntity(kind="line", layer="CENTER", start=(-1.5, 0.0), end=(params.thickness + 1.5, 0.0), source_id=source_id)
            )
            views.append("left")
            view_map["left"] = left_indices
            dims.append(
                {
                    "kind": "linear",
                    "value": params.thickness,
                    "source_id": source_id,
                    "owner_view": "left",
                    "target_entity_index": left_box_idx,
                    "orientation": "horizontal",
                }
            )
    else:
        dims.insert(
            0,
            {
                "kind": "linear",
                "value": params.width,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": plate_idx,
                "orientation": "horizontal",
            },
        )

    return ParametricPartResult(
        views=views,
        entities=entities,
        view_entity_indices=view_map,
        dimensions=dims,
        parameters={
            "part_family": "slotted-bracket",
            "width_mm": params.width,
            "height_mm": params.height,
            "thickness_mm": params.thickness,
        },
    )


def _entity_outline_box(entity: DrawingEntity) -> tuple[float, float, float, float] | None:
    if entity.layer != "OUTLINE":
        return None
    if isinstance(entity, LineEntity):
        xs = (entity.start[0], entity.end[0])
        ys = (entity.start[1], entity.end[1])
        return min(xs), min(ys), max(xs), max(ys)
    if isinstance(entity, PolylineEntity):
        xs = [p[0] for p in entity.vertices]
        ys = [p[1] for p in entity.vertices]
        return min(xs), min(ys), max(xs), max(ys)
    if isinstance(entity, CircleEntity):
        cx, cy = entity.center
        return cx - entity.radius, cy - entity.radius, cx + entity.radius, cy + entity.radius
    if isinstance(entity, CircularEntity) and entity.is_full_circle:
        cx, cy = entity.center
        return cx - entity.radius, cy - entity.radius, cx + entity.radius, cy + entity.radius
    if isinstance(entity, (ArcEntity, CircularEntity)):
        circ = entity if isinstance(entity, CircularEntity) else CircularEntity(
            kind="circular",
            layer=entity.layer,
            source_id=entity.source_id,
            center=entity.center,
            radius=entity.radius,
            start_angle=entity.start_angle,
            sweep=(entity.end_angle - entity.start_angle) % 360.0 or 360.0,
        )
        cx, cy = circ.center
        r = circ.radius
        pts = [
            (cx + r * cos(circ.start_angle * pi / 180.0), cy + r * sin(circ.start_angle * pi / 180.0)),
            (cx + r * cos(circ.end_angle * pi / 180.0), cy + r * sin(circ.end_angle * pi / 180.0)),
        ]
        s_mod = circ.start_angle % 360.0
        for ang, pt in ((0.0, (cx + r, cy)), (90.0, (cx, cy + r)), (180.0, (cx - r, cy)), (270.0, (cx, cy - r))):
            if (ang - s_mod) % 360.0 <= circ.sweep + 1e-9:
                pts.append(pt)
        return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)
    if isinstance(entity, ContourEntity):
        verts = compile_contour(entity)
        n = len(verts)
        xs: list[float] = []
        ys: list[float] = []
        for idx, (x1, y1, bulge) in enumerate(verts):
            x2, y2, _ = verts[(idx + 1) % n]
            xs.extend((x1, x2))
            ys.extend((y1, y2))
            if abs(bulge) > 1e-12:
                chord = hypot(x2 - x1, y2 - y1)
                radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
                cot_half = (1.0 - bulge * bulge) / (2.0 * bulge)
                cx = 0.5 * (x1 + x2) - 0.5 * (y2 - y1) * cot_half
                cy = 0.5 * (y1 + y2) + 0.5 * (x2 - x1) * cot_half
                from math import atan2, degrees
                a1 = degrees(atan2(y1 - cy, x1 - cx))
                a2 = degrees(atan2(y2 - cy, x2 - cx))
                start = a1 if bulge > 0 else a2
                sweep = (a2 - a1) % 360.0 if bulge > 0 else (a1 - a2) % 360.0
                s_mod = start % 360.0
                for ang, pt in ((0.0, (cx + radius, cy)), (90.0, (cx, cy + radius)), (180.0, (cx - radius, cy)), (270.0, (cx, cy - radius))):
                    if (ang - s_mod) % 360.0 <= sweep + 1e-9:
                        xs.append(pt[0])
                        ys.append(pt[1])
        return min(xs), min(ys), max(xs), max(ys)
    return None


def verify_orthographic_consistency(
    entities: list[DrawingEntity],
    view_entity_indices: dict[str, list[int]],
    *,
    tolerance_mm: float = 1e-3,
) -> list[str]:
    """Verify GB/T orthographic projection alignment across OUTLINE views.

    Checks:
    - 长对正 (equal width): ``front`` width == ``top`` / ``bottom`` width
    - 高平齐 (equal height): ``front`` height == ``left`` / ``right`` height
    - 宽相等 (equal depth): ``top`` / ``bottom`` height == ``left`` / ``right`` width
    """

    if not entities or not view_entity_indices:
        return []

    view_dims: dict[str, tuple[float, float]] = {}
    for view_name, indices in view_entity_indices.items():
        boxes = [
            box
            for idx in indices
            if 0 <= idx < len(entities)
            if (box := _entity_outline_box(entities[idx])) is not None
        ]
        if not boxes:
            continue
        w = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        h = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        if w > tolerance_mm and h > tolerance_mm:
            view_dims[view_name] = (w, h)

    violations: list[str] = []
    front = view_dims.get("front")
    if front is not None:
        for v_other in ("top", "bottom"):
            if v_other in view_dims and not isclose(front[0], view_dims[v_other][0], abs_tol=tolerance_mm):
                violations.append(
                    f"orthographic width mismatch (长对正): front width {front[0]:g} mm != {v_other} width {view_dims[v_other][0]:g} mm"
                )
        for v_other in ("left", "right"):
            if v_other in view_dims and not isclose(front[1], view_dims[v_other][1], abs_tol=tolerance_mm):
                violations.append(
                    f"orthographic height mismatch (高平齐): front height {front[1]:g} mm != {v_other} height {view_dims[v_other][1]:g} mm"
                )
    for h_view in ("top", "bottom"):
        for side_view in ("left", "right"):
            if h_view in view_dims and side_view in view_dims:
                depth_h = view_dims[h_view][1]
                depth_w = view_dims[side_view][0]
                if not isclose(depth_h, depth_w, abs_tol=tolerance_mm):
                    violations.append(
                        f"orthographic depth mismatch (宽相等): {h_view} depth {depth_h:g} mm != {side_view} depth {depth_w:g} mm"
                    )
    return violations


# ---------------------------------------------------------------------------
# CAD-1K Inspired Classic Mechanical 3-View Part Builders
# (Spur Gear GB/T 4459.2, Bearing Cap, Ribbed L-Bracket)
# ---------------------------------------------------------------------------


class SpurGearParameters(BaseModel):
    """Standard involute cylindrical spur gear parameters (GB/T 4459.2)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    module_mm: float = 3.0
    teeth: int = 28
    face_width: float = 28.0
    bore_diameter: float = 24.0
    keyway_width: float | None = None
    keyway_depth: float | None = None

    @model_validator(mode="after")
    def validate_gear(self) -> SpurGearParameters:
        if not all(isfinite(v) for v in (self.module_mm, self.face_width, self.bore_diameter)):
            raise ValueError("gear dimensions must be finite")
        if self.module_mm <= 0 or self.teeth < 10 or self.face_width <= 0 or self.bore_diameter <= 0:
            raise ValueError("spur gear requires module_mm > 0, teeth >= 10, face_width > 0, bore_diameter > 0")
        root_dia = self.module_mm * (self.teeth - 2.5)
        if self.bore_diameter >= root_dia - 8.0:
            raise ValueError("bore_diameter leaves insufficient rim thickness below gear root diameter")
        return self


def build_spur_gear_geometry(
    params: SpurGearParameters,
    *,
    source_id: str = "spur-gear",
) -> ParametricPartResult:
    """Build standard GB/T 4459.2 flat-laid three views for a cylindrical spur gear.

    Per GB/T 4459.2:
    - Front full-section view leaves the tooth zone [r_root, r_tip] unhatched,
      draws tip & root lines as OUTLINE, and pitch lines as red CENTER dash-dot!
    - Top plan view shows tip circle (OUTLINE), pitch circle (CENTER), hub circle,
      shaft bore + standard flat keyway slot, and optional lightening holes.
    """
    m = params.module_mm
    z = params.teeth
    b = params.face_width
    pitch_d = m * z
    tip_d = m * (z + 2.0)
    root_d = m * (z - 2.5)
    r_p = pitch_d / 2.0
    r_a = tip_d / 2.0
    r_f = root_d / 2.0
    r_bore = params.bore_diameter / 2.0
    kw = params.keyway_width if params.keyway_width is not None else round(max(4.0, params.bore_diameter * 0.28), 1)
    kd = params.keyway_depth if params.keyway_depth is not None else round(max(1.8, kw * 0.42), 1)
    r_hub = min(r_f - 3.0, max(r_bore + kd + 5.0, r_bore * 1.6))
    ext = r_a * 1.06

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    top_indices: list[int] = []
    left_indices: list[int] = []

    def add_f(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); front_indices.append(idx); return idx

    def add_t(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); top_indices.append(idx); return idx

    def add_l(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); left_indices.append(idx); return idx

    # 1. Front View (主视图 全剖: width = tip_d, height = face_width, teeth unhatched per GB/T 4459.2!)
    f_box = add_f(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(-r_a, 0.0), (r_a, 0.0), (r_a, b), (-r_a, b)], closed=True, source_id=source_id))
    add_f(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, b + 2.0), source_id=source_id))
    for sign in (-1.0, 1.0):
        add_f(LineEntity(kind="line", layer="CENTER", start=(sign * r_p, -1.8), end=(sign * r_p, b + 1.8), source_id=source_id))
        add_f(LineEntity(kind="line", layer="OUTLINE", start=(sign * r_f, 0.0), end=(sign * r_f, b), source_id=source_id))
        add_f(LineEntity(kind="line", layer="OUTLINE", start=(sign * r_bore, 0.0), end=(sign * r_bore, b), source_id=source_id))
    # Keyway back wall in section (at +r_bore + kd)
    r_key_wall = r_bore + kd
    add_f(LineEntity(kind="line", layer="OUTLINE", start=(r_key_wall, 0.0), end=(r_key_wall, b), source_id=source_id))
    pitch_span_idx = add_f(LineEntity(kind="line", layer="CENTER", start=(-r_p, 0.0), end=(r_p, 0.0), source_id=source_id))
    # Hatch solid gear body between bore/keyway and root circle only (teeth stay unhatched!)
    add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_f, 0.0), (-r_bore, 0.0), (-r_bore, b), (-r_f, b)], pattern="ANSI31", source_id=source_id))
    add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_key_wall, 0.0), (r_f, 0.0), (r_f, b), (r_key_wall, b)], pattern="ANSI31", source_id=source_id))

    # 2. Top View (俯视图: tip circle, pitch circle, hub circle, bore + keyway notch, lightening holes)
    tip_top_idx = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_a, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_p, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="THIN", center=(0.0, 0.0), radius=r_f, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_hub, source_id=source_id))
    bore_top_idx = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_bore, source_id=source_id))
    # Keyway rectangular slot on right (+X) side of bore matching front section
    ky_half = kw / 2.0
    x_int = (max(r_bore * r_bore - ky_half * ky_half, 0.01)) ** 0.5
    add_t(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(x_int, ky_half), (r_key_wall, ky_half), (r_key_wall, -ky_half), (x_int, -ky_half)], closed=False, source_id=source_id))
    add_t(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=source_id))
    add_t(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=source_id))

    first_light_hole: int | None = None
    if r_f - r_hub >= 14.0:
        r_web = (r_f + r_hub) / 2.0
        r_lh = round(min(8.0, (r_f - r_hub) * 0.28), 1)
        for k in range(4):
            ang = pi / 4.0 + k * pi / 2.0
            lh_i = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(round(r_web * cos(ang), 5), round(r_web * sin(ang), 5)), radius=r_lh, source_id=source_id))
            if first_light_hole is None:
                first_light_hole = lh_i

    # 3. Left View (左视图: width = tip_d 宽相等, height = face_width 高平齐)
    l_box = add_l(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(-r_a, 0.0), (r_a, 0.0), (r_a, b), (-r_a, b)], closed=True, source_id=source_id))
    add_l(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, b + 2.0), source_id=source_id))
    for sign in (-1.0, 1.0):
        add_l(LineEntity(kind="line", layer="CENTER", start=(sign * r_p, -1.5), end=(sign * r_p, b + 1.5), source_id=source_id))
        add_l(LineEntity(kind="line", layer="HIDDEN", start=(sign * r_bore, 0.0), end=(sign * r_bore, b), source_id=source_id))

    dims: list[dict[str, Any]] = [
        {"kind": "diameter", "value": pitch_d, "source_id": source_id, "owner_view": "front", "target_entity_index": pitch_span_idx, "orientation": "horizontal", "prefix": "⌀"},
        {"kind": "linear", "value": b, "source_id": source_id, "owner_view": "front", "target_entity_index": f_box, "orientation": "vertical"},
        {"kind": "diameter", "value": tip_d, "source_id": source_id, "owner_view": "top", "target_entity_index": tip_top_idx, "orientation": "horizontal", "prefix": "⌀"},
        {"kind": "diameter", "value": params.bore_diameter, "source_id": source_id, "owner_view": "top", "target_entity_index": bore_top_idx, "orientation": "diameter"},
        {"kind": "diameter", "value": tip_d, "source_id": source_id, "owner_view": "left", "target_entity_index": l_box, "orientation": "horizontal", "prefix": "⌀"},
    ]
    return ParametricPartResult(
        views=["front", "top", "left"],
        entities=entities,
        view_entity_indices={"front": front_indices, "top": top_indices, "left": left_indices},
        dimensions=dims,
        parameters={
            "part_family": "spur-gear",
            "module_mm": m,
            "teeth": z,
            "pitch_diameter_mm": pitch_d,
            "tip_diameter_mm": tip_d,
            "face_width_mm": b,
        },
    )


class BearingCapParameters(BaseModel):
    """Gearbox / housing bearing through-cap (轴承透盖) parameters."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    flange_diameter: float = 130.0
    spigot_diameter: float = 90.0
    bore_diameter: float = 42.0
    total_height: float = 32.0
    flange_thickness: float = 12.0
    bolt_circle_diameter: float = 110.0
    hole_diameter: float = 9.0
    hole_count: int = 6

    @model_validator(mode="after")
    def validate_cap(self) -> BearingCapParameters:
        if not (0 < self.bore_diameter < self.spigot_diameter < self.flange_diameter):
            raise ValueError("bearing cap requires 0 < bore_diameter < spigot_diameter < flange_diameter")
        if not (0 < self.flange_thickness < self.total_height):
            raise ValueError("bearing cap requires 0 < flange_thickness < total_height")
        if self.hole_count > 0:
            if not (self.spigot_diameter + self.hole_diameter + 2.0 <= self.bolt_circle_diameter <= self.flange_diameter - self.hole_diameter - 2.0):
                raise ValueError("bearing cap bolt circle interferes with spigot or outer flange rim")
        return self


def build_bearing_cap_geometry(
    params: BearingCapParameters,
    *,
    source_id: str = "bearing-cap",
) -> ParametricPartResult:
    """Build standard GB/T flat-laid three views for a stepped bearing cap."""
    rf = params.flange_diameter / 2.0
    rs = params.spigot_diameter / 2.0
    rb = params.bore_diameter / 2.0
    r_rec = round((rs + rb) / 2.0, 2)
    tf = params.flange_thickness
    th = params.total_height
    z_floor = round(tf * 0.65, 2)
    r_bcd = params.bolt_circle_diameter / 2.0
    rh = params.hole_diameter / 2.0
    ext = rf * 1.06

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    top_indices: list[int] = []
    left_indices: list[int] = []

    def add_f(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); front_indices.append(idx); return idx

    def add_t(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); top_indices.append(idx); return idx

    def add_l(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); left_indices.append(idx); return idx

    outer_pts = [(-rf, 0.0), (rf, 0.0), (rf, tf), (rs, tf), (rs, th), (-rs, th), (-rs, tf), (-rf, tf)]
    f_sec_idx = add_f(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=outer_pts, closed=True, source_id=source_id))
    add_f(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, th + 2.0), source_id=source_id))
    # Stepped internal bearing recess + shaft bore
    add_f(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(-rb, 0.0), (-rb, z_floor), (-r_rec, z_floor), (-r_rec, th)], closed=False, source_id=source_id))
    add_f(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(rb, 0.0), (rb, z_floor), (r_rec, z_floor), (r_rec, th)], closed=False, source_id=source_id))

    pitch_span_idx = f_sec_idx
    if params.hole_count >= 2:
        for sign in (-1.0, 1.0):
            hc_x = sign * r_bcd
            add_f(LineEntity(kind="line", layer="OUTLINE", start=(hc_x - rh, 0.0), end=(hc_x - rh, tf), source_id=source_id))
            add_f(LineEntity(kind="line", layer="OUTLINE", start=(hc_x + rh, 0.0), end=(hc_x + rh, tf), source_id=source_id))
            add_f(LineEntity(kind="line", layer="CENTER", start=(hc_x, -1.5), end=(hc_x, tf + 1.5), source_id=source_id))
        pitch_span_idx = add_f(LineEntity(kind="line", layer="CENTER", start=(-r_bcd, 0.0), end=(r_bcd, 0.0), source_id=source_id))
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-rf, 0.0), (-r_bcd - rh, 0.0), (-r_bcd - rh, tf), (-rf, tf)], pattern="ANSI31", source_id=source_id))
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(r_bcd + rh, 0.0), (rf, 0.0), (rf, tf), (r_bcd + rh, tf)], pattern="ANSI31", source_id=source_id))
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-r_bcd + rh, 0.0), (-rb, 0.0), (-rb, z_floor), (-r_rec, z_floor), (-r_rec, th), (-rs, th), (-rs, tf), (-r_bcd + rh, tf)], pattern="ANSI31", source_id=source_id))
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(rb, 0.0), (r_bcd - rh, 0.0), (r_bcd - rh, tf), (rs, tf), (rs, th), (r_rec, th), (r_rec, z_floor), (rb, z_floor)], pattern="ANSI31", source_id=source_id))
    else:
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(-rf, 0.0), (-rb, 0.0), (-rb, z_floor), (-r_rec, z_floor), (-r_rec, th), (-rs, th), (-rs, tf), (-rf, tf)], pattern="ANSI31", source_id=source_id))
        add_f(HatchEntity(kind="hatch", layer="HATCH", boundary=[(rb, 0.0), (rf, 0.0), (rf, tf), (rs, tf), (rs, th), (r_rec, th), (r_rec, z_floor), (rb, z_floor)], pattern="ANSI31", source_id=source_id))

    # Top View (俯视图: circular plan view)
    t_out_idx = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=rf, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=rs, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=r_rec, source_id=source_id))
    t_bore_idx = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, 0.0), radius=rb, source_id=source_id))
    add_t(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=source_id))
    add_t(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=source_id))
    first_h: int | None = None
    if params.hole_count > 0:
        add_t(CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_bcd, source_id=source_id))
        for k in range(params.hole_count):
            ang = 2.0 * pi * k / params.hole_count
            hi = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(round(r_bcd * cos(ang), 6), round(r_bcd * sin(ang), 6)), radius=rh, source_id=source_id))
            if first_h is None:
                first_h = hi

    # Left View (左视图: stepped external side elevation, 高平齐 & 宽相等)
    l_out_idx = add_l(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=outer_pts, closed=True, source_id=source_id))
    add_l(LineEntity(kind="line", layer="OUTLINE", start=(-rs, tf), end=(rs, tf), source_id=source_id))
    add_l(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, th + 2.0), source_id=source_id))
    for x_hid, y_top in ((-rb, z_floor), (rb, z_floor), (-r_rec, th), (r_rec, th)):
        add_l(LineEntity(kind="line", layer="HIDDEN", start=(x_hid, z_floor if abs(x_hid) == r_rec else 0.0), end=(x_hid, y_top), source_id=source_id))
    spigot_line_idx = add_l(LineEntity(kind="line", layer="OUTLINE", start=(-rs, th), end=(rs, th), source_id=source_id))

    dims: list[dict[str, Any]] = [
        {"kind": "diameter", "value": params.bolt_circle_diameter if params.hole_count >= 2 else params.flange_diameter, "source_id": source_id, "owner_view": "front", "target_entity_index": pitch_span_idx, "orientation": "horizontal", "prefix": "⌀"},
        {"kind": "linear", "value": th, "source_id": source_id, "owner_view": "front", "target_entity_index": f_sec_idx, "orientation": "vertical"},
        {"kind": "diameter", "value": params.flange_diameter, "source_id": source_id, "owner_view": "top", "target_entity_index": t_out_idx, "orientation": "horizontal", "prefix": "⌀"},
        {"kind": "diameter", "value": params.bore_diameter, "source_id": source_id, "owner_view": "top", "target_entity_index": t_bore_idx, "orientation": "diameter"},
        {"kind": "diameter", "value": params.spigot_diameter, "source_id": source_id, "owner_view": "left", "target_entity_index": spigot_line_idx, "orientation": "horizontal", "prefix": "⌀"},
    ]
    if first_h is not None and params.hole_count > 0:
        dims.append(
            {"kind": "diameter", "value": params.hole_diameter, "source_id": source_id, "owner_view": "top", "target_entity_index": first_h, "orientation": "diameter", "prefix": f"{params.hole_count}×⌀" if params.hole_count > 1 else "⌀"}
        )

    return ParametricPartResult(
        views=["front", "top", "left"],
        entities=entities,
        view_entity_indices={"front": front_indices, "top": top_indices, "left": left_indices},
        dimensions=dims,
        parameters={
            "part_family": "bearing-cap",
            "flange_diameter_mm": params.flange_diameter,
            "spigot_diameter_mm": params.spigot_diameter,
            "bore_diameter_mm": params.bore_diameter,
            "total_height_mm": th,
        },
    )


class RibbedBracketParameters(BaseModel):
    """Right-angle L-bracket with central triangular stiffening web/rib."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_length: float = 130.0
    base_width: float = 84.0
    total_height: float = 92.0
    base_thickness: float = 14.0
    wall_thickness: float = 14.0
    rib_thickness: float = 10.0
    boss_hole_diameter: float = 28.0
    mount_hole_diameter: float = 12.0

    @model_validator(mode="after")
    def validate_ribbed_bracket(self) -> RibbedBracketParameters:
        if self.base_length <= 36.0 or self.base_width <= 30.0 or self.total_height <= 36.0:
            raise ValueError("ribbed bracket requires base_length > 36, base_width > 30, total_height > 36")
        if self.base_thickness >= self.total_height * 0.5 or self.wall_thickness >= self.base_length * 0.5:
            raise ValueError("ribbed bracket wall/base thickness exceeds envelope")
        if not (0 < self.rib_thickness < self.base_width - 8.0):
            raise ValueError("rib_thickness must satisfy 0 < rib_thickness < base_width - 8")
        return self


def build_ribbed_bracket_geometry(
    params: RibbedBracketParameters,
    *,
    source_id: str = "ribbed-bracket",
) -> ParametricPartResult:
    """Build standard GB/T three views (front, top, left) for a ribbed L-bracket."""
    L = params.base_length
    W = params.base_width
    H = params.total_height
    t1 = params.base_thickness
    t2 = params.wall_thickness
    tr = params.rib_thickness
    r_boss = params.boss_hole_diameter / 2.0
    r_mnt = params.mount_hole_diameter / 2.0
    z_hole = round(H - max(r_boss + 12.0, H * 0.30), 2)
    x_rib_end = round(L - max(22.0, r_mnt * 3.0), 2)
    z_rib_top = round(min(H - 8.0, z_hole - r_boss - 2.0) if z_hole - r_boss - 2.0 > t1 + 12.0 else H - 10.0, 2)
    x_mnt = round(L - max(18.0, r_mnt * 2.2), 2)
    y_mnt = round(W / 2.0 - max(15.0, r_mnt * 1.8), 2)

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    top_indices: list[int] = []
    left_indices: list[int] = []

    def add_f(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); front_indices.append(idx); return idx

    def add_t(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); top_indices.append(idx); return idx

    def add_l(ent: DrawingEntity) -> int:
        idx = len(entities); entities.append(ent); left_indices.append(idx); return idx

    # 1. Front View (主视图: L-profile + triangular stiffening rib + hidden holes, width = L, height = H)
    f_l_idx = add_f(
        PolylineEntity(
            kind="polyline",
            layer="OUTLINE",
            vertices=[(0.0, 0.0), (L, 0.0), (L, t1), (t2, t1), (t2, H), (0.0, H)],
            closed=True,
            source_id=source_id,
        )
    )
    add_f(LineEntity(kind="line", layer="OUTLINE", start=(t2, z_rib_top), end=(x_rib_end, t1), source_id=source_id))
    add_f(LineEntity(kind="line", layer="CENTER", start=(-2.0, z_hole), end=(t2 + 2.0, z_hole), source_id=source_id))
    for dy_b in (-r_boss, r_boss):
        add_f(LineEntity(kind="line", layer="HIDDEN", start=(0.0, z_hole + dy_b), end=(t2, z_hole + dy_b), source_id=source_id))
    add_f(LineEntity(kind="line", layer="CENTER", start=(x_mnt, -2.0), end=(x_mnt, t1 + 2.0), source_id=source_id))
    for dx_m in (-r_mnt, r_mnt):
        add_f(LineEntity(kind="line", layer="HIDDEN", start=(x_mnt + dx_m, 0.0), end=(x_mnt + dx_m, t1), source_id=source_id))

    # 2. Top View (俯视图: width = L 长对正, height = W)
    t_box_idx = add_t(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(0.0, -W / 2.0), (L, -W / 2.0), (L, W / 2.0), (0.0, W / 2.0)], closed=True, source_id=source_id))
    add_t(LineEntity(kind="line", layer="OUTLINE", start=(t2, -W / 2.0), end=(t2, W / 2.0), source_id=source_id))
    add_t(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(t2, -tr / 2.0), (x_rib_end, -tr / 2.0), (x_rib_end, tr / 2.0), (t2, tr / 2.0)], closed=False, source_id=source_id))
    add_t(LineEntity(kind="line", layer="CENTER", start=(-2.0, 0.0), end=(L + 2.0, 0.0), source_id=source_id))
    for sy_b in (-r_boss, r_boss):
        add_t(LineEntity(kind="line", layer="HIDDEN", start=(0.0, sy_b), end=(t2, sy_b), source_id=source_id))
    top_mnt_idx = add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(x_mnt, y_mnt), radius=r_mnt, source_id=source_id))
    add_t(CircularEntity(kind="circular", layer="OUTLINE", center=(x_mnt, -y_mnt), radius=r_mnt, source_id=source_id))

    # 3. Left View (左视图: width = W 宽相等, height = H 高平齐)
    l_box_idx = add_l(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(-W / 2.0, 0.0), (W / 2.0, 0.0), (W / 2.0, H), (-W / 2.0, H)], closed=True, source_id=source_id))
    add_l(LineEntity(kind="line", layer="OUTLINE", start=(-W / 2.0, t1), end=(W / 2.0, t1), source_id=source_id))
    add_l(PolylineEntity(kind="polyline", layer="OUTLINE", vertices=[(-tr / 2.0, t1), (-tr / 2.0, z_rib_top), (tr / 2.0, z_rib_top), (tr / 2.0, t1)], closed=False, source_id=source_id))
    add_l(LineEntity(kind="line", layer="CENTER", start=(0.0, -2.0), end=(0.0, H + 2.0), source_id=source_id))
    add_l(LineEntity(kind="line", layer="CENTER", start=(-r_boss * 1.25, z_hole), end=(r_boss * 1.25, z_hole), source_id=source_id))
    boss_circle_idx = add_l(CircularEntity(kind="circular", layer="OUTLINE", center=(0.0, z_hole), radius=r_boss, source_id=source_id))
    for sy_m in (-y_mnt, y_mnt):
        for dx_m in (-r_mnt, r_mnt):
            add_l(LineEntity(kind="line", layer="HIDDEN", start=(sy_m + dx_m, 0.0), end=(sy_m + dx_m, t1), source_id=source_id))

    dims: list[dict[str, Any]] = [
        {"kind": "linear", "value": L, "source_id": source_id, "owner_view": "front", "target_entity_index": f_l_idx, "orientation": "horizontal"},
        {"kind": "linear", "value": H, "source_id": source_id, "owner_view": "front", "target_entity_index": f_l_idx, "orientation": "vertical"},
        {"kind": "linear", "value": W, "source_id": source_id, "owner_view": "top", "target_entity_index": t_box_idx, "orientation": "vertical"},
        {"kind": "diameter", "value": params.mount_hole_diameter, "source_id": source_id, "owner_view": "top", "target_entity_index": top_mnt_idx, "orientation": "diameter", "prefix": "2×⌀"},
        {"kind": "linear", "value": W, "source_id": source_id, "owner_view": "left", "target_entity_index": l_box_idx, "orientation": "horizontal"},
        {"kind": "diameter", "value": params.boss_hole_diameter, "source_id": source_id, "owner_view": "left", "target_entity_index": boss_circle_idx, "orientation": "diameter"},
    ]
    return ParametricPartResult(
        views=["front", "top", "left"],
        entities=entities,
        view_entity_indices={"front": front_indices, "top": top_indices, "left": left_indices},
        dimensions=dims,
        parameters={
            "part_family": "ribbed-bracket",
            "base_length_mm": L,
            "base_width_mm": W,
            "total_height_mm": H,
        },
    )
