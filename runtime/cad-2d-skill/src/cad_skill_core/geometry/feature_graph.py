"""Composable 2D Feature Graph (`cad-feature-graph/v1`) for Non-Standard Mechanical Parts.

Breaks free of fixed part-family templates while preserving GB/T orthographic
alignment (`长对正、高平齐、宽相等`), hidden-line/centerline projection, and
collision-free feature-bound dimensions:

`BaseProfile (polygon | rect | stadium)`
  -> `CornerModifier (fillet | chamfer)`
  -> `Cutouts (circle_hole | hole_grid | bolt_circle | rect_cutout | slot_cutout)`
  -> `Automatic 3-View Orthographic Projection (front, top, left) + Dimensions`
"""

from __future__ import annotations

from collections.abc import Callable
import json
from math import acos, atan2, cos, degrees, hypot, isfinite, pi, radians, sin, tan
import os
import re
from typing import Annotated, Any, Literal, Union
import urllib.request

from pydantic import BaseModel, ConfigDict, Field

from .entities import (
    ArcEntity,
    BulgeVertex,
    CircularEntity,
    ContourEntity,
    DrawingEntity,
    LineEntity,
    PolylineEntity,
)
from .parametric import (
    ParametricPartResult,
    RoundedRectangleParameters,
    rounded_rectangle,
    verify_orthographic_consistency,
)


class _StrictGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ======================================================================
# 1. Base Outer Profiles
# ======================================================================


class RectProfile(_StrictGraphModel):
    kind: Literal["rect"] = "rect"
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    origin: tuple[float, float] = (0.0, 0.0)


class PolygonProfile(_StrictGraphModel):
    kind: Literal["polygon"] = "polygon"
    vertices: list[tuple[float, float]] = Field(min_length=3)


class StadiumProfile(_StrictGraphModel):
    kind: Literal["stadium"] = "stadium"
    length: float = Field(gt=0.0)
    width: float = Field(gt=0.0)
    center: tuple[float, float] = (0.0, 0.0)


BaseProfileSpec = Annotated[
    Union[RectProfile, PolygonProfile, StadiumProfile],
    Field(discriminator="kind"),
]


# ======================================================================
# 2. Composable Subtractive & Corner Features
# ======================================================================


class CornerModifierFeature(_StrictGraphModel):
    op: Literal["corner_mod"] = "corner_mod"
    mode: Literal["fillet", "chamfer"] = "fillet"
    size: float = Field(gt=0.0)
    size2: float | None = Field(default=None, gt=0.0)
    vertex_indices: list[int] | None = None


class CircleHoleFeature(_StrictGraphModel):
    op: Literal["circle_hole"] = "circle_hole"
    center: tuple[float, float]
    diameter: float = Field(gt=0.0)


class HoleGridFeature(_StrictGraphModel):
    op: Literal["hole_grid"] = "hole_grid"
    nx: int = Field(ge=1, le=20)
    ny: int = Field(ge=1, le=20)
    diameter: float = Field(gt=0.0)
    x0: float
    y0: float
    pitch_x: float = Field(ge=0.0)
    pitch_y: float = Field(ge=0.0)


class BoltCircleFeature(_StrictGraphModel):
    op: Literal["bolt_circle"] = "bolt_circle"
    center: tuple[float, float] = (0.0, 0.0)
    pcd: float = Field(gt=0.0)
    count: int = Field(ge=2, le=64)
    hole_diameter: float = Field(gt=0.0)
    start_angle_deg: float = 0.0


class RectCutoutFeature(_StrictGraphModel):
    op: Literal["rect_cutout"] = "rect_cutout"
    center: tuple[float, float]
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    corner_radius: float = Field(default=0.0, ge=0.0)


class SlotCutoutFeature(_StrictGraphModel):
    op: Literal["slot_cutout"] = "slot_cutout"
    center: tuple[float, float]
    length: float = Field(gt=0.0)
    width: float = Field(gt=0.0)
    angle_deg: float = 0.0


FeatureNode = Annotated[
    Union[
        CornerModifierFeature,
        CircleHoleFeature,
        HoleGridFeature,
        BoltCircleFeature,
        RectCutoutFeature,
        SlotCutoutFeature,
    ],
    Field(discriminator="op"),
]


class FeatureGraphSpec(_StrictGraphModel):
    schema_version: Literal["cad-feature-graph/v1"] = "cad-feature-graph/v1"
    name: str | None = None
    base_profile: BaseProfileSpec
    thickness_mm: float = Field(default=12.0, gt=0.0)
    views_mode: Literal["auto", "single", "two_view", "three_view"] = "auto"
    features: list[FeatureNode] = Field(default_factory=list)


# ======================================================================
# 3. Exact Polygon / Corner / Slot Compilation Helpers
# ======================================================================


def _clean_polygon_vertices(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for x, y in pts:
        if not (isfinite(x) and isfinite(y)):
            raise ValueError("polygon vertex coordinates must be finite")
        if cleaned and hypot(x - cleaned[-1][0], y - cleaned[-1][1]) <= 1e-6:
            continue
        cleaned.append((float(x), float(y)))
    if len(cleaned) >= 2 and hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) <= 1e-6:
        cleaned.pop()
    if len(cleaned) < 3:
        raise ValueError("polygon profile requires at least 3 distinct vertices")
    area2 = sum(
        cleaned[i][0] * cleaned[(i + 1) % len(cleaned)][1]
        - cleaned[(i + 1) % len(cleaned)][0] * cleaned[i][1]
        for i in range(len(cleaned))
    )
    if abs(area2) <= 1e-4:
        raise ValueError("polygon profile has zero area (collinear vertices)")
    return cleaned


def _build_outer_contour(
    base: BaseProfileSpec,
    corner_mods: list[CornerModifierFeature],
    source_id: str,
) -> tuple[list[DrawingEntity], list[float], list[float]]:
    """Build outer profile entities and return `(entities, crease_xs, crease_ys)`."""
    if isinstance(base, StadiumProfile):
        if base.length <= base.width:
            raise ValueError("stadium length must exceed width")
        r = base.width / 2.0
        half_flat = (base.length - base.width) / 2.0
        cx, cy = base.center
        ents: list[DrawingEntity] = [
            LineEntity(kind="line", layer="OUTLINE", start=(cx - half_flat, cy - r), end=(cx + half_flat, cy - r), source_id=source_id),
            LineEntity(kind="line", layer="OUTLINE", start=(cx - half_flat, cy + r), end=(cx + half_flat, cy + r), source_id=source_id),
            ArcEntity(kind="arc", layer="OUTLINE", center=(cx + half_flat, cy), radius=r, start_angle=-90.0, end_angle=90.0, source_id=source_id),
            ArcEntity(kind="arc", layer="OUTLINE", center=(cx - half_flat, cy), radius=r, start_angle=90.0, end_angle=270.0, source_id=source_id),
        ]
        return ents, [], []

    if isinstance(base, RectProfile):
        ox, oy = base.origin
        raw_verts = [
            (ox, oy),
            (ox + base.width, oy),
            (ox + base.width, oy + base.height),
            (ox, oy + base.height),
        ]
    else:
        raw_verts = _clean_polygon_vertices(base.vertices)

    n = len(raw_verts)
    mod_by_idx: dict[int, CornerModifierFeature] = {}
    for cm in corner_mods:
        targets = cm.vertex_indices if cm.vertex_indices is not None else list(range(n))
        for vi in targets:
            if 0 <= vi < n:
                mod_by_idx[vi] = cm

    # Interior step vertices create visible projection creases in top/left views
    xs_all = [v[0] for v in raw_verts]
    ys_all = [v[1] for v in raw_verts]
    min_vx, max_vx = min(xs_all), max(xs_all)
    min_vy, max_vy = min(ys_all), max(ys_all)
    crease_xs = sorted({round(x, 4) for x in xs_all if min_vx + 1e-3 < x < max_vx - 1e-3})
    crease_ys = sorted({round(y, 4) for y in ys_all if min_vy + 1e-3 < y < max_vy - 1e-3})

    if not mod_by_idx:
        return (
            [PolylineEntity(kind="polyline", layer="OUTLINE", vertices=raw_verts, closed=True, source_id=source_id)],
            crease_xs,
            crease_ys,
        )

    # Emit exact line + tangent arc / chamfer line segments around closed polygon
    trim_info: dict[int, tuple[tuple[float, float], tuple[float, float], DrawingEntity | None]] = {}
    for idx in range(n):
        cm = mod_by_idx.get(idx)
        curr_p = raw_verts[idx]
        if cm is None:
            trim_info[idx] = (curr_p, curr_p, None)
            continue
        prev_p = raw_verts[(idx - 1) % n]
        next_p = raw_verts[(idx + 1) % n]
        len_in = hypot(prev_p[0] - curr_p[0], prev_p[1] - curr_p[1])
        len_out = hypot(next_p[0] - curr_p[0], next_p[1] - curr_p[1])
        u_in = ((prev_p[0] - curr_p[0]) / len_in, (prev_p[1] - curr_p[1]) / len_in)
        u_out = ((next_p[0] - curr_p[0]) / len_out, (next_p[1] - curr_p[1]) / len_out)
        dot_u = max(-1.0, min(1.0, u_in[0] * u_out[0] + u_in[1] * u_out[1]))
        theta = acos(dot_u)
        if theta <= 1e-3 or abs(pi - theta) <= 1e-3:
            trim_info[idx] = (curr_p, curr_p, None)
            continue

        if cm.mode == "chamfer":
            d1 = cm.size
            d2 = cm.size2 if cm.size2 is not None else cm.size
            if d1 >= len_in * 0.48 or d2 >= len_out * 0.48:
                raise ValueError(f"chamfer size ({d1}x{d2}) exceeds half edge length at vertex {idx}")
            t_in = (curr_p[0] + u_in[0] * d1, curr_p[1] + u_in[1] * d1)
            t_out = (curr_p[0] + u_out[0] * d2, curr_p[1] + u_out[1] * d2)
            corner_ent: DrawingEntity = LineEntity(kind="line", layer="OUTLINE", start=t_in, end=t_out, source_id=source_id)
            trim_info[idx] = (t_in, t_out, corner_ent)
        else:
            rad = cm.size
            t_dist = rad / tan(theta / 2.0)
            if t_dist >= min(len_in, len_out) * 0.48:
                raise ValueError(f"fillet radius R{rad} exceeds half edge length at vertex {idx}")
            t_in = (curr_p[0] + u_in[0] * t_dist, curr_p[1] + u_in[1] * t_dist)
            t_out = (curr_p[0] + u_out[0] * t_dist, curr_p[1] + u_out[1] * t_dist)
            bx, by = u_in[0] + u_out[0], u_in[1] + u_out[1]
            blen = hypot(bx, by)
            c_dist = rad / sin(theta / 2.0)
            arc_cx = curr_p[0] + (bx / blen) * c_dist
            arc_cy = curr_p[1] + (by / blen) * c_dist
            a_in = degrees(atan2(t_in[1] - arc_cy, t_in[0] - arc_cx)) % 360.0
            a_out = degrees(atan2(t_out[1] - arc_cy, t_out[0] - arc_cx)) % 360.0
            ccw_sweep = (a_out - a_in) % 360.0
            sa, ea = (a_in, a_out) if ccw_sweep <= 180.0 + 1e-5 else (a_out, a_in)
            corner_ent = ArcEntity(
                kind="arc",
                layer="OUTLINE",
                center=(arc_cx, arc_cy),
                radius=rad,
                start_angle=sa,
                end_angle=ea,
                source_id=source_id,
            )
            trim_info[idx] = (t_in, t_out, corner_ent)

    built_ents: list[DrawingEntity] = []
    for idx in range(n):
        _, start_pt, corner_e = trim_info[idx]
        end_pt, _, _ = trim_info[(idx + 1) % n]
        if corner_e is not None:
            built_ents.append(corner_e)
        if hypot(end_pt[0] - start_pt[0], end_pt[1] - start_pt[1]) > 1e-5:
            built_ents.append(LineEntity(kind="line", layer="OUTLINE", start=start_pt, end=end_pt, source_id=source_id))
    return built_ents, crease_xs, crease_ys


def _outline_bounds(ents: list[DrawingEntity]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in ents:
        if e.layer != "OUTLINE":
            continue
        if isinstance(e, LineEntity):
            xs.extend([e.start[0], e.end[0]])
            ys.extend([e.start[1], e.end[1]])
        elif isinstance(e, PolylineEntity):
            xs.extend(p[0] for p in e.vertices)
            ys.extend(p[1] for p in e.vertices)
        elif isinstance(e, CircularEntity):
            xs.extend([e.center[0] - e.radius, e.center[0] + e.radius])
            ys.extend([e.center[1] - e.radius, e.center[1] + e.radius])
        elif isinstance(e, ArcEntity):
            sa = radians(e.start_angle)
            ea = radians(e.end_angle)
            pts_a = [
                (e.center[0] + e.radius * cos(sa), e.center[1] + e.radius * sin(sa)),
                (e.center[0] + e.radius * cos(ea), e.center[1] + e.radius * sin(ea)),
            ]
            for card in (0.0, 90.0, 180.0, 270.0):
                sweep = (e.end_angle - e.start_angle) % 360.0
                rel = (card - e.start_angle) % 360.0
                if rel <= sweep + 1e-6:
                    cr = radians(card)
                    pts_a.append((e.center[0] + e.radius * cos(cr), e.center[1] + e.radius * sin(cr)))
            xs.extend(p[0] for p in pts_a)
            ys.extend(p[1] for p in pts_a)
        elif isinstance(e, ContourEntity):
            xs.extend(v.point[0] for v in e.vertices)
            ys.extend(v.point[1] for v in e.vertices)
    if not xs or not ys:
        raise ValueError("feature graph produced empty OUTLINE geometry")
    return min(xs), min(ys), max(xs), max(ys)


def _emit_slot_entities(
    slot: SlotCutoutFeature,
    source_id: str,
) -> tuple[list[DrawingEntity], tuple[float, float, float, float]]:
    if slot.length <= slot.width:
        raise ValueError(f"slot length ({slot.length}) must exceed slot width ({slot.width})")
    r = slot.width / 2.0
    half_flat = (slot.length - slot.width) / 2.0
    cx, cy = slot.center
    ang_rad = radians(slot.angle_deg)
    ca, sa = cos(ang_rad), sin(ang_rad)
    nx, ny = -sa, ca

    c1 = (cx - half_flat * ca, cy - half_flat * sa)
    c2 = (cx + half_flat * ca, cy + half_flat * sa)
    l1_a = (c1[0] - r * nx, c1[1] - r * ny)
    l1_b = (c2[0] - r * nx, c2[1] - r * ny)
    l2_a = (c1[0] + r * nx, c1[1] + r * ny)
    l2_b = (c2[0] + r * nx, c2[1] + r * ny)

    ents: list[DrawingEntity] = [
        LineEntity(kind="line", layer="OUTLINE", start=l1_a, end=l1_b, source_id=source_id),
        LineEntity(kind="line", layer="OUTLINE", start=l2_a, end=l2_b, source_id=source_id),
        ArcEntity(
            kind="arc",
            layer="OUTLINE",
            center=c2,
            radius=r,
            start_angle=(slot.angle_deg - 90.0) % 360.0,
            end_angle=(slot.angle_deg + 90.0) % 360.0,
            source_id=source_id,
        ),
        ArcEntity(
            kind="arc",
            layer="OUTLINE",
            center=c1,
            radius=r,
            start_angle=(slot.angle_deg + 90.0) % 360.0,
            end_angle=(slot.angle_deg + 270.0) % 360.0,
            source_id=source_id,
        ),
        LineEntity(
            kind="line",
            layer="CENTER",
            start=(cx - (half_flat + r + 2.5) * ca, cy - (half_flat + r + 2.5) * sa),
            end=(cx + (half_flat + r + 2.5) * ca, cy + (half_flat + r + 2.5) * sa),
            source_id=source_id,
        ),
    ]
    s_min_x, s_min_y, s_max_x, s_max_y = _outline_bounds(ents)
    return ents, (s_min_x, s_min_y, s_max_x, s_max_y)


# ======================================================================
# 4. Orthographic 3-View Feature Graph Builder
# ======================================================================


def build_feature_graph_geometry(
    graph: FeatureGraphSpec | dict[str, Any],
    *,
    source_id: str = "feature-graph",
) -> ParametricPartResult:
    """Compile a `FeatureGraphSpec` into verified GB/T orthographic views + dimensions."""
    spec = graph if isinstance(graph, FeatureGraphSpec) else FeatureGraphSpec.model_validate(graph)
    T = float(spec.thickness_mm)

    corner_mods = [f for f in spec.features if isinstance(f, CornerModifierFeature)]
    outer_ents, crease_xs, crease_ys = _build_outer_contour(spec.base_profile, corner_mods, source_id)
    min_x, min_y, max_x, max_y = _outline_bounds(outer_ents)
    W_span = round(max_x - min_x, 4)
    H_span = round(max_y - min_y, 4)

    entities: list[DrawingEntity] = []
    front_idx: list[int] = []
    top_idx: list[int] = []
    left_idx: list[int] = []

    def add_f(e: DrawingEntity) -> int:
        i = len(entities)
        entities.append(e)
        front_idx.append(i)
        return i

    def add_t(e: DrawingEntity) -> int:
        i = len(entities)
        entities.append(e)
        top_idx.append(i)
        return i

    def add_l(e: DrawingEntity) -> int:
        i = len(entities)
        entities.append(e)
        left_idx.append(i)
        return i

    first_outer_idx = -1
    for oe in outer_ents:
        idx_o = add_f(oe)
        if first_outer_idx < 0:
            first_outer_idx = idx_o

    # Add invisible/auxiliary full-span bounding anchor if outer contour was split into segments
    # so horizontal/vertical overall dimensions bind to exact full-span [min_x, max_x] & [min_y, max_y]!
    if len(outer_ents) == 1 and isinstance(outer_ents[0], PolylineEntity):
        span_anchor_idx = first_outer_idx
    else:
        span_anchor_idx = first_outer_idx

    # Track internal through-features to project HIDDEN + CENTER lines onto top & left views
    # Each item: (x_min, x_max, x_center, y_min, y_max, y_center)
    proj_cutouts: list[tuple[float, float, float | None, float, float, float | None]] = []
    RepresentativeHole = tuple[int, float, str | None, str]  # (entity_idx, dia, prefix, param_key)
    rep_holes: list[RepresentativeHole] = []

    for feat in spec.features:
        if isinstance(feat, CornerModifierFeature):
            continue

        if isinstance(feat, CircleHoleFeature):
            cx, cy = feat.center
            r = feat.diameter / 2.0
            if not (min_x + r < cx < max_x - r and min_y + r < cy < max_y - r):
                raise ValueError(f"circle hole at ({cx}, {cy}) Ø{feat.diameter} escapes outer profile bounds")
            h_idx = add_f(CircularEntity(kind="circular", layer="OUTLINE", center=(cx, cy), radius=r, source_id=source_id))
            ext = max(r * 1.35, r + 2.0)
            add_f(LineEntity(kind="line", layer="CENTER", start=(cx - ext, cy), end=(cx + ext, cy), source_id=source_id))
            add_f(LineEntity(kind="line", layer="CENTER", start=(cx, cy - ext), end=(cx, cy + ext), source_id=source_id))
            proj_cutouts.append((cx - r, cx + r, cx, cy - r, cy + r, cy))
            rep_holes.append((h_idx, feat.diameter, "⌀", "hole_diameter"))

        elif isinstance(feat, HoleGridFeature):
            r = feat.diameter / 2.0
            total_holes = feat.nx * feat.ny
            first_grid_h = -1
            for ix in range(feat.nx):
                for iy in range(feat.ny):
                    hx = feat.x0 + ix * feat.pitch_x
                    hy = feat.y0 + iy * feat.pitch_y
                    if not (min_x + r < hx < max_x - r and min_y + r < hy < max_y - r):
                        raise ValueError(f"grid hole at ({hx}, {hy}) Ø{feat.diameter} escapes outer profile bounds")
                    hi = add_f(CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=r, source_id=source_id))
                    if first_grid_h < 0:
                        first_grid_h = hi
                    ext = max(r * 1.3, r + 1.8)
                    add_f(LineEntity(kind="line", layer="CENTER", start=(hx - ext, hy), end=(hx + ext, hy), source_id=source_id))
                    add_f(LineEntity(kind="line", layer="CENTER", start=(hx, hy - ext), end=(hx, hy + ext), source_id=source_id))
                    proj_cutouts.append((hx - r, hx + r, hx, hy - r, hy + r, hy))
            if first_grid_h >= 0:
                pref = f"{total_holes}×⌀" if total_holes > 1 else "⌀"
                rep_holes.append((first_grid_h, feat.diameter, pref, "hole_diameter"))

        elif isinstance(feat, BoltCircleFeature):
            cx, cy = feat.center
            r_h = feat.hole_diameter / 2.0
            r_pcd = feat.pcd / 2.0
            add_f(CircularEntity(kind="circular", layer="CENTER", center=(cx, cy), radius=r_pcd, source_id=source_id))
            first_bc_h = -1
            for k in range(feat.count):
                ang = radians(feat.start_angle_deg) + 2.0 * pi * k / feat.count
                hx, hy = cx + r_pcd * cos(ang), cy + r_pcd * sin(ang)
                if not (min_x + r_h < hx < max_x - r_h and min_y + r_h < hy < max_y - r_h):
                    raise ValueError(f"bolt-circle hole at ({hx:.1f}, {hy:.1f}) escapes outer profile bounds")
                hi = add_f(CircularEntity(kind="circular", layer="OUTLINE", center=(hx, hy), radius=r_h, source_id=source_id))
                if first_bc_h < 0:
                    first_bc_h = hi
                proj_cutouts.append((hx - r_h, hx + r_h, hx, hy - r_h, hy + r_h, hy))
            if first_bc_h >= 0:
                rep_holes.append((first_bc_h, feat.hole_diameter, f"{feat.count}×⌀", "hole_diameter"))

        elif isinstance(feat, RectCutoutFeature):
            cx, cy = feat.center
            x0, y0 = cx - feat.width / 2.0, cy - feat.height / 2.0
            x1, y1 = cx + feat.width / 2.0, cy + feat.height / 2.0
            if not (min_x < x0 and x1 < max_x and min_y < y0 and y1 < max_y):
                raise ValueError("rectangular cutout escapes outer profile bounds")
            if feat.corner_radius > 0.0:
                add_f(
                    rounded_rectangle(
                        RoundedRectangleParameters(
                            width=feat.width,
                            height=feat.height,
                            radius=feat.corner_radius,
                            origin=(x0, y0),
                        ),
                        layer="OUTLINE",
                        source_id=source_id,
                    )
                )
            else:
                add_f(
                    PolylineEntity(
                        kind="polyline",
                        layer="OUTLINE",
                        vertices=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                        closed=True,
                        source_id=source_id,
                    )
                )
            proj_cutouts.append((x0, x1, cx, y0, y1, cy))

        elif isinstance(feat, SlotCutoutFeature):
            slot_ents, (sx0, sy0, sx1, sy1) = _emit_slot_entities(feat, source_id)
            if not (min_x < sx0 and sx1 < max_x and min_y < sy0 and sy1 < max_y):
                raise ValueError("slot cutout escapes outer profile bounds")
            for se in slot_ents:
                add_f(se)
            proj_cutouts.append((sx0, sx1, feat.center[0], sy0, sy1, feat.center[1]))

    views_mode = spec.views_mode
    if views_mode == "single":
        chosen_views = ["front"]
    elif views_mode == "two_view":
        chosen_views = ["front", "left"]
    else:
        chosen_views = ["front", "top", "left"]

    top_box_idx = -1
    left_box_idx = -1

    # 2. Top View (俯视图: X=[min_x, max_x] 长对正, Y=[0, T])
    if "top" in chosen_views:
        top_box_idx = add_t(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(min_x, 0.0), (max_x, 0.0), (max_x, T), (min_x, T)],
                closed=True,
                source_id=source_id,
            )
        )
        for cx_step in crease_xs:
            add_t(LineEntity(kind="line", layer="OUTLINE", start=(cx_step, 0.0), end=(cx_step, T), source_id=source_id))
        seen_tx: set[float] = set()
        seen_tc: set[float] = set()
        for x0_c, x1_c, xc_c, _, _, _ in proj_cutouts:
            for x_edge in (round(x0_c, 3), round(x1_c, 3)):
                if x_edge not in seen_tx and min_x + 1e-3 < x_edge < max_x - 1e-3:
                    seen_tx.add(x_edge)
                    add_t(LineEntity(kind="line", layer="HIDDEN", start=(x_edge, 0.0), end=(x_edge, T), source_id=source_id))
            if xc_c is not None and round(xc_c, 3) not in seen_tc:
                seen_tc.add(round(xc_c, 3))
                add_t(LineEntity(kind="line", layer="CENTER", start=(xc_c, -1.8), end=(xc_c, T + 1.8), source_id=source_id))

    # 3. Left View (左视图: X=[0, T] 宽相等, Y=[min_y, max_y] 高平齐)
    if "left" in chosen_views:
        left_box_idx = add_l(
            PolylineEntity(
                kind="polyline",
                layer="OUTLINE",
                vertices=[(0.0, min_y), (T, min_y), (T, max_y), (0.0, max_y)],
                closed=True,
                source_id=source_id,
            )
        )
        for cy_step in crease_ys:
            add_l(LineEntity(kind="line", layer="OUTLINE", start=(0.0, cy_step), end=(T, cy_step), source_id=source_id))
        seen_ly: set[float] = set()
        seen_lc: set[float] = set()
        for _, _, _, y0_c, y1_c, yc_c in proj_cutouts:
            for y_edge in (round(y0_c, 3), round(y1_c, 3)):
                if y_edge not in seen_ly and min_y + 1e-3 < y_edge < max_y - 1e-3:
                    seen_ly.add(y_edge)
                    add_l(LineEntity(kind="line", layer="HIDDEN", start=(0.0, y_edge), end=(T, y_edge), source_id=source_id))
            if yc_c is not None and round(yc_c, 3) not in seen_lc:
                seen_lc.add(round(yc_c, 3))
                add_l(LineEntity(kind="line", layer="CENTER", start=(-1.8, yc_c), end=(T + 1.8, yc_c), source_id=source_id))

    view_map: dict[str, list[int]] = {"front": front_idx}
    if "top" in chosen_views:
        view_map["top"] = top_idx
    if "left" in chosen_views:
        view_map["left"] = left_idx

    ortho_errs = verify_orthographic_consistency(entities, view_map)
    if ortho_errs:
        raise ValueError(f"feature graph orthographic projection error: {'; '.join(ortho_errs)}")

    # Synthesize clean feature-bound dimensions
    horiz_target = top_box_idx if top_box_idx >= 0 else (span_anchor_idx if len(outer_ents) == 1 else None)
    vert_target = left_box_idx if left_box_idx >= 0 else (span_anchor_idx if len(outer_ents) == 1 else None)
    dims: list[dict[str, Any]] = [
        {
            "kind": "linear",
            "value": W_span,
            "source_id": source_id,
            "owner_view": "top" if top_box_idx >= 0 else "front",
            "target_entity_index": horiz_target,
            "orientation": "horizontal",
            "param_key": "width",
        },
        {
            "kind": "linear",
            "value": H_span,
            "source_id": source_id,
            "owner_view": "left" if left_box_idx >= 0 else "front",
            "target_entity_index": vert_target,
            "orientation": "vertical",
            "param_key": "height",
        },
    ]
    if left_box_idx >= 0:
        dims.append(
            {
                "kind": "linear",
                "value": T,
                "source_id": source_id,
                "owner_view": "left",
                "target_entity_index": left_box_idx,
                "orientation": "horizontal",
                "param_key": "thickness",
            }
        )
    elif top_box_idx >= 0:
        dims.append(
            {
                "kind": "linear",
                "value": T,
                "source_id": source_id,
                "owner_view": "top",
                "target_entity_index": top_box_idx,
                "orientation": "vertical",
                "param_key": "thickness",
            }
        )

    for h_ent_idx, h_dia, h_pref, h_pkey in rep_holes[:2]:
        dims.append(
            {
                "kind": "diameter",
                "value": h_dia,
                "source_id": source_id,
                "owner_view": "front",
                "target_entity_index": h_ent_idx,
                "orientation": "diameter",
                "prefix": h_pref,
                "param_key": h_pkey,
            }
        )

    return ParametricPartResult(
        views=chosen_views,
        entities=entities,
        view_entity_indices=view_map,
        dimensions=dims,
        parameters={
            "part_family": "custom-profile",
            "width_mm": W_span,
            "height_mm": H_span,
            "thickness_mm": T,
            "feature_count": len(spec.features),
        },
    )


# ======================================================================
# 5. Dual-Mode NL -> `FeatureGraphSpec` Compiler (LLM + Deterministic)
# ======================================================================

_VERTEX_PAIR_RE = re.compile(r"[\(（]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[\)）]")


def compile_nl_to_feature_graph(
    text: str,
    *,
    llm_callable: Callable[[str, dict[str, Any]], dict[str, Any] | str] | None = None,
) -> FeatureGraphSpec | None:
    """Compile non-standard multi-feature plate/profile descriptions into `FeatureGraphSpec`."""
    clean = (text or "").strip()
    if not clean:
        return None

    if llm_callable is not None:
        raw = llm_callable(clean, {"schema": FeatureGraphSpec.model_json_schema()})
        data = json.loads(raw) if isinstance(raw, str) else raw
        return FeatureGraphSpec.model_validate(data)

    api_key = os.environ.get("CAD_SKILL_LLM_API_KEY", "").strip()
    base_url = os.environ.get("CAD_SKILL_LLM_BASE_URL", "").strip()
    model_name = os.environ.get("CAD_SKILL_LLM_MODEL", "gpt-4o-mini").strip()
    if api_key and base_url.startswith("https://"):
        try:
            sys_msg = (
                "Output ONLY valid JSON matching schema_version='cad-feature-graph/v1' (FeatureGraphSpec) "
                "for non-standard 2D plate/bracket geometry, or {{\"skip\": true}} if it is a standard flange/gear/shaft."
            )
            req_body = json.dumps(
                {
                    "model": model_name,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": clean},
                    ],
                    "temperature": 0.0,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/chat/completions",
                data=req_body,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            parsed = json.loads(body["choices"][0]["message"]["content"])
            if not parsed.get("skip") and "base_profile" in parsed:
                return FeatureGraphSpec.model_validate(parsed)
        except Exception:
            pass

    # Deterministic offline recognition of non-standard custom profiles:
    # Triggers when user specifies >=3 polygon vertices OR custom plate keywords (异形板/连接板/多孔底板/T型滑块/方槽)
    has_custom_kw = bool(
        re.search(r"多边形|顶点|异形|连接板|多孔.*(?:底板|安装板)|T[型形][^\n，。]{0,8}?(?:板|滑块|垫块|块|座)|方槽|矩形槽|阵列.*孔", clean, re.IGNORECASE)
    )
    if not has_custom_kw:
        return None

    # 1. Determine base profile
    base: BaseProfileSpec | None = None
    m_poly_line = re.search(r"(?:多边形|顶点|轮廓顶点)[^\n。；]*", clean)
    if m_poly_line:
        v_pairs = [(float(x), float(y)) for x, y in _VERTEX_PAIR_RE.findall(m_poly_line.group(0))]
        if len(v_pairs) >= 3:
            base = PolygonProfile(vertices=v_pairs)

    if base is None and re.search(r"T[型形][^\n，。]{0,8}?(?:板|滑块|垫块|块|座)", clean, re.IGNORECASE):
        m_tw = re.search(r"(?:顶宽|头部宽|总宽|上宽)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        m_th = re.search(r"(?:总高|高度|高)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        m_sw = re.search(r"(?:腹板宽|立柱宽|下宽|柄宽)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        m_fh = re.search(r"(?:翼缘高|头部高|上高|横梁高)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        top_w = float(m_tw.group(1)) if m_tw else 120.0
        tot_h = float(m_th.group(1)) if m_th else 90.0
        stem_w = float(m_sw.group(1)) if m_sw else round(top_w * 0.4, 1)
        flange_h = float(m_fh.group(1)) if m_fh else round(tot_h * 0.35, 1)
        x_in = (top_w - stem_w) / 2.0
        y_step = tot_h - flange_h
        base = PolygonProfile(
            vertices=[
                (x_in, 0.0),
                (x_in + stem_w, 0.0),
                (x_in + stem_w, y_step),
                (top_w, y_step),
                (top_w, tot_h),
                (0.0, tot_h),
                (0.0, y_step),
                (x_in, y_step),
            ]
        )

    if base is None:
        m_w = re.search(r"(?:总长|板长|底板长|总宽|(?<!槽)(?<!腹板)(?<!立柱)长|(?<!槽)(?<!腹板)宽度)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        m_h = re.search(r"(?:板宽|总高|高度|(?<!翼缘)(?<!头部)高)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
        m_3dim = re.search(r"(?P<w>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<h>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<t>\d+(?:\.\d+)?)", clean)
        if m_w and m_h:
            base = RectProfile(width=float(m_w.group(1)), height=float(m_h.group(1)), origin=(0.0, 0.0))
        elif m_3dim:
            w_v, h_v = float(m_3dim.group("w")), float(m_3dim.group("h"))
            base = RectProfile(width=w_v, height=h_v, origin=(0.0, 0.0))

    if base is None:
        return None

    # Thickness
    m_thick = re.search(r"(?:板厚|厚度|厚)\s*[:：=]?\s*(\d+(?:\.\d+)?)", clean)
    if m_thick:
        thick_v = float(m_thick.group(1))
    else:
        m_3t = re.search(r"\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?\s*[×xX*]\s*(?P<t>\d+(?:\.\d+)?)", clean)
        thick_v = float(m_3t.group("t")) if m_3t else 12.0

    # Compute base bounds so relative features ("中心", "四角距边") know exact [bx0, by0, bx1, by1]
    preview_outer, _, _ = _build_outer_contour(base, [], "probe")
    bx0, by0, bx1, by1 = _outline_bounds(preview_outer)
    bw, bh = bx1 - bx0, by1 - by0
    bcx, bcy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)

    feats: list[FeatureNode] = []

    # Corner fillet / chamfer
    m_fil = re.search(r"(?:四角|倒圆角|圆角)\s*[:：=]?\s*[Rr]?\s*(\d+(?:\.\d+)?)", clean)
    m_cham = re.search(r"(?:四角)?(?:倒角|斜角)\s*[:：=]?\s*[Cc]?\s*(\d+(?:\.\d+)?)", clean)
    if m_fil:
        feats.append(CornerModifierFeature(mode="fillet", size=float(m_fil.group(1))))
    elif m_cham and not m_fil:
        feats.append(CornerModifierFeature(mode="chamfer", size=float(m_cham.group(1))))

    # Hole grid ("距边 20 布置 2×2 阵列 Ø10" or "四角距边 18 钻 4×Φ10")
    m_grid = re.search(
        r"距边\s*(?P<ins>\d+(?:\.\d+)?)[^。；\n]{0,20}?(?:(?P<nx>\d+)\s*[×xX*]\s*(?P<ny>\d+)\s*阵列[^0-9]*(?P<gd>\d+(?:\.\d+)?)|4\s*[×xX*]\s*[⌀∅ØøΦφ]?\s*(?P<d4>\d+(?:\.\d+)?))",
        clean,
    )
    if m_grid:
        inset = float(m_grid.group("ins"))
        nx = int(m_grid.group("nx") or 2)
        ny = int(m_grid.group("ny") or 2)
        gdia = float(m_grid.group("gd") or m_grid.group("d4"))
        px = (bw - 2.0 * inset) / max(1, nx - 1) if nx > 1 else 0.0
        py = (bh - 2.0 * inset) / max(1, ny - 1) if ny > 1 else 0.0
        feats.append(HoleGridFeature(nx=nx, ny=ny, diameter=gdia, x0=bx0 + inset, y0=by0 + inset, pitch_x=px, pitch_y=py))

    # Explicit coordinate holes — exclude the polygon header segment so vertices aren't misread as hole centers
    body_text = clean[m_poly_line.end() :] if m_poly_line else clean
    for cl in re.split(r"[，,；;。\n]+", body_text):
        m_pt = _VERTEX_PAIR_RE.search(cl)
        if m_pt and re.search(r"孔", cl) and not re.search(r"槽", cl):
            px_h, py_h = float(m_pt.group(1)), float(m_pt.group(2))
            cl_rest = cl[: m_pt.start()] + cl[m_pt.end() :]
            m_hd = re.search(r"[⌀∅ØøΦφ]?\s*(\d+(?:\.\d+)?)", cl_rest)
            if m_hd:
                feats.append(CircleHoleFeature(center=(px_h, py_h), diameter=float(m_hd.group(1))))
        elif m_pt and re.search(r"腰形槽|长圆槽|长槽", cl):
            px_s, py_s = float(m_pt.group(1)), float(m_pt.group(2))
            cl_rest = cl[: m_pt.start()] + cl[m_pt.end() :]
            m_sl = re.search(r"(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)", cl_rest)
            if m_sl:
                feats.append(SlotCutoutFeature(center=(px_s, py_s), length=float(m_sl.group(1)), width=float(m_sl.group(2))))

    # Centered rectangular cutout ("中心开 40×24 矩形方槽/方孔")
    m_rcut = re.search(r"(?:中心|中间)[^0-9，。；]{0,10}(?P<rw>\d+(?:\.\d+)?)\s*[×xX*]\s*(?P<rh>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*的?\s*(?:矩形槽|方槽|方孔|窗口)", clean)
    if m_rcut:
        feats.append(RectCutoutFeature(center=(bcx, bcy), width=float(m_rcut.group("rw")), height=float(m_rcut.group("rh"))))

    # Centered circle hole ("中心开 Ø28 通孔") when no explicit coordinate was given
    m_chole = re.search(r"(?:中心|中间)(?:开|钻|打)?\s*[⌀∅ØøΦφ]\s*(?P<cd>\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*(?:通孔|圆孔|孔)", clean)
    if m_chole:
        feats.append(CircleHoleFeature(center=(bcx, bcy), diameter=float(m_chole.group("cd"))))

    return FeatureGraphSpec(base_profile=base, thickness_mm=thick_v, features=feats)
