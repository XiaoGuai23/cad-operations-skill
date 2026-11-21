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
) -> ParametricPartResult:
    """Build front view and optional right-side section view (高平齐) for a flange."""

    r_out = params.outer_diameter / 2.0
    r_bore = params.bore_diameter / 2.0
    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    dims: list[dict[str, Any]] = []

    def add_front(ent: DrawingEntity) -> int:
        idx = len(entities)
        entities.append(ent)
        front_indices.append(idx)
        return idx

    outer_idx = add_front(
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
        bore_idx = add_front(
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

    # Orthogonal centerlines
    ext = r_out * 1.08
    add_front(LineEntity(kind="line", layer="CENTER", start=(-ext, 0.0), end=(ext, 0.0), source_id=source_id))
    add_front(LineEntity(kind="line", layer="CENTER", start=(0.0, -ext), end=(0.0, ext), source_id=source_id))

    if params.hole_count > 0 and params.bolt_circle_diameter and params.hole_diameter:
        r_bcd = params.bolt_circle_diameter / 2.0
        r_hole = params.hole_diameter / 2.0
        add_front(
            CircularEntity(kind="circular", layer="CENTER", center=(0.0, 0.0), radius=r_bcd, source_id=source_id)
        )
        for k in range(params.hole_count):
            ang = 2.0 * pi * k / params.hole_count
            add_front(
                CircularEntity(
                    kind="circular",
                    layer="OUTLINE",
                    center=(round(r_bcd * cos(ang), 6), round(r_bcd * sin(ang), 6)),
                    radius=r_hole,
                    source_id=source_id,
                )
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

        th = params.thickness
        # Right/section view has height == 2*r_out (高平齐 with front!) and width == thickness
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
            add_sec(
                HatchEntity(
                    kind="hatch",
                    layer="HATCH",
                    boundary=[(0.0, r_bore), (th, r_bore), (th, r_out), (0.0, r_out)],
                    pattern="ANSI31",
                    source_id=source_id,
                )
            )
            add_sec(
                HatchEntity(
                    kind="hatch",
                    layer="HATCH",
                    boundary=[(0.0, -r_out), (th, -r_out), (th, -r_bore), (0.0, -r_bore)],
                    pattern="ANSI31",
                    source_id=source_id,
                )
            )
        else:
            add_sec(
                HatchEntity(
                    kind="hatch",
                    layer="HATCH",
                    boundary=[(0.0, -r_out), (th, -r_out), (th, r_out), (0.0, r_out)],
                    pattern="ANSI31",
                    source_id=source_id,
                )
            )
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
) -> ParametricPartResult:
    """Build front elevation and optional right end-view (高平齐) for a stepped shaft."""

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

    if include_right_view:
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
) -> ParametricPartResult:
    """Build front plate contour (rounded rect + tangent slot + holes) and optional top view (长对正)."""

    entities: list[DrawingEntity] = []
    front_indices: list[int] = []
    dims: list[dict[str, Any]] = []

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
    add_front(slot_contour)

    if params.hole_diameter is not None:
        hx = params.width / 2.0 - params.hole_inset
        hy = params.height / 2.0 - params.hole_inset
        hr = params.hole_diameter / 2.0
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                add_front(
                    CircularEntity(
                        kind="circular",
                        layer="OUTLINE",
                        center=(sx * hx, sy * hy),
                        radius=hr,
                        source_id=source_id,
                    )
                )

    dims.append(
        {
            "kind": "linear",
            "value": params.width,
            "source_id": source_id,
            "owner_view": "front",
            "target_entity_index": plate_idx,
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
