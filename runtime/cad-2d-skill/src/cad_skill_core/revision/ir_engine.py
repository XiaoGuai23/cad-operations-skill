"""Structured Revision IR (`cad-revision-ir/v2`) + LLM/Deterministic Compiler + Geometric Feature Primitives.

Architecture:
1. Upper Semantic Layer: Accepts typed `StructuredRevisionIR` JSON directly from an Agent/MCP client,
   compiles via an optional LLM structured-output provider (`CAD_SKILL_LLM_API_KEY`), or compiles
   offline via deterministic multi-clause NL rules.
2. Lower Deterministic Kernel: Executes every IR operation inside `DocumentCheckpoint` with `$INSUNITS`
   physical unit scaling, strict `compare_untouched` out-of-scope verification, and `ezdxf` audit.

Supported Geometric Feature Primitives (beyond delete/move/resize/text):
- `add_circle_hole`: Add a circular hole with optional GB/T centerlines.
- `add_bolt_circle`: Add an N-hole circular bolt pattern (PCD) concentric with a center or reference circle.
- `chamfer_corner`: Trim & bevel a corner (`C` / `d1 × d2`) on either two intersecting `LINE`s or an `LWPOLYLINE`.
- `fillet_corner`: Trim & blend a tangent `ARC` / polyline bulge of radius `R` on two `LINE`s or an `LWPOLYLINE`.
- `mirror_entities`: Reflect selected geometry across a vertical/horizontal/explicit symmetry axis.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from math import acos, atan2, cos, degrees, hypot, isclose, isfinite, pi, radians, sin, tan
import os
from pathlib import Path
import re
import tempfile
from typing import Annotated, Any, Literal, Union
import urllib.request

import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44
from pydantic import BaseModel, ConfigDict, Field

from .common import RevisionError, compare_untouched, sha256_file, snapshot_modelspace
from .post_verify import (
    BoltCircleExistsCheck,
    CircleDiameterEqualsCheck,
    CircleExistsAtCheck,
    CornerChamferExistsCheck,
    CornerFilletExistsCheck,
    HandlesAbsentCheck,
    HandlesTranslatedCheck,
    LinearSpanEqualsCheck,
    MirroredHandlesExistCheck,
    PostConditionSpec,
    TextContainsCheck,
    verify_revision_post_conditions,
)
from .transaction import DocumentCheckpoint

_INSUNITS_MM_SCALE: dict[int, tuple[float, str]] = {
    1: (25.4, "in"),
    2: (304.8, "ft"),
    4: (1.0, "mm"),
    5: (10.0, "cm"),
    6: (1000.0, "m"),
}

_PROMPT_UNIT_MM: dict[str, float] = {
    "mm": 1.0,
    "毫米": 1.0,
    "cm": 10.0,
    "厘米": 10.0,
    "m": 1000.0,
    "米": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "英寸": 25.4,
    "ft": 304.8,
    "feet": 304.8,
    "英尺": 304.8,
}

_UNIT_PAT = r"毫米|厘米|英寸|英尺|inches|inch|mm|cm|in|ft|m|米"


def scale_value_to_doc_units(value: float, raw_unit: str | None, doc_units: int) -> float:
    if not isfinite(value):
        raise RevisionError(f"non-finite numeric parameter: {value}")
    if not raw_unit or not raw_unit.strip():
        return float(value)
    u_clean = raw_unit.strip().lower()
    if u_clean not in _PROMPT_UNIT_MM:
        raise RevisionError(f"unsupported unit token: {raw_unit!r}")
    if doc_units == 0:
        raise RevisionError("图纸未定义物理单位（$INSUNITS=0），无法安全换算显式物理单位。")
    if doc_units not in _INSUNITS_MM_SCALE:
        raise RevisionError(f"unsupported DXF $INSUNITS code: {doc_units}")
    val_mm = float(value) * _PROMPT_UNIT_MM[u_clean]
    doc_mm, _ = _INSUNITS_MM_SCALE[doc_units]
    return val_mm / doc_mm


# ======================================================================
# 1. Strict Typed Revision IR Schema (`cad-revision-ir/v2`)
# ======================================================================


class _StrictIRBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddCircleHoleOp(_StrictIRBase):
    op: Literal["add_circle_hole"] = "add_circle_hole"
    diameter: float = Field(gt=0.0)
    center: tuple[float, float] | None = None
    reference_handle: str | None = None
    offset: tuple[float, float] = (0.0, 0.0)
    diameter_unit: str | None = None
    center_unit: str | None = None
    offset_unit: str | None = None
    unit: str | None = None
    layer: str = "OUTLINE"
    with_centerlines: bool = True


class AddBoltCircleOp(_StrictIRBase):
    op: Literal["add_bolt_circle"] = "add_bolt_circle"
    count: int = Field(ge=2, le=64)
    hole_diameter: float = Field(gt=0.0)
    bolt_circle_diameter: float = Field(gt=0.0)
    center: tuple[float, float] | None = None
    reference_handle: str | None = None
    start_angle_deg: float = 0.0
    hole_unit: str | None = None
    pcd_unit: str | None = None
    center_unit: str | None = None
    unit: str | None = None
    layer: str = "OUTLINE"
    with_pitch_circle: bool = True


class ChamferCornerOp(_StrictIRBase):
    op: Literal["chamfer_corner"] = "chamfer_corner"
    distance1: float = Field(gt=0.0)
    distance2: float | None = Field(default=None, gt=0.0)
    handles: list[str] = Field(default_factory=list)
    corner_hint: tuple[float, float] | None = None
    all_corners_in_region: bool = False
    distance1_unit: str | None = None
    distance2_unit: str | None = None
    unit: str | None = None


class FilletCornerOp(_StrictIRBase):
    op: Literal["fillet_corner"] = "fillet_corner"
    radius: float = Field(gt=0.0)
    handles: list[str] = Field(default_factory=list)
    corner_hint: tuple[float, float] | None = None
    all_corners_in_region: bool = False
    unit: str | None = None


class MirrorEntitiesOp(_StrictIRBase):
    op: Literal["mirror_entities"] = "mirror_entities"
    handles: list[str] = Field(default_factory=list)
    axis: Literal["vertical", "horizontal", "line"] = "vertical"
    axis_coord: float | None = None
    axis_point1: tuple[float, float] | None = None
    axis_point2: tuple[float, float] | None = None
    keep_original: bool = True
    unit: str | None = None


class DeleteEntitiesOp(_StrictIRBase):
    op: Literal["delete_entities"] = "delete_entities"
    handles: list[str] = Field(default_factory=list)
    entity_filter: Literal["all", "circle", "line", "text", "dimension", "hidden", "center"] = "all"


class MoveEntitiesOp(_StrictIRBase):
    op: Literal["move_entities"] = "move_entities"
    handles: list[str] = Field(default_factory=list)
    dx: float = 0.0
    dy: float = 0.0
    dx_unit: str | None = None
    dy_unit: str | None = None
    unit: str | None = None


class ResizeHoleOp(_StrictIRBase):
    op: Literal["resize_hole"] = "resize_hole"
    handles: list[str] = Field(default_factory=list)
    new_diameter: float = Field(gt=0.0)
    match_old_diameter: float | None = Field(default=None, gt=0.0)
    new_diameter_unit: str | None = None
    old_diameter_unit: str | None = None
    unit: str | None = None
    sync_dimensions: bool = True


class ResizeLinearOp(_StrictIRBase):
    op: Literal["resize_linear"] = "resize_linear"
    handles: list[str] = Field(default_factory=list)
    new_length: float = Field(gt=0.0)
    axis: Literal["auto", "horizontal", "vertical"] = "auto"
    new_length_unit: str | None = None
    unit: str | None = None


class UpdateTextOp(_StrictIRBase):
    op: Literal["update_text"] = "update_text"
    handles: list[str] = Field(default_factory=list)
    new_text: str
    old_text: str | None = None


RevisionIROperation = Annotated[
    Union[
        AddCircleHoleOp,
        AddBoltCircleOp,
        ChamferCornerOp,
        FilletCornerOp,
        MirrorEntitiesOp,
        DeleteEntitiesOp,
        MoveEntitiesOp,
        ResizeHoleOp,
        ResizeLinearOp,
        UpdateTextOp,
    ],
    Field(discriminator="op"),
]


class StructuredRevisionIR(_StrictIRBase):
    schema_version: Literal["cad-revision-ir/v2", "cad-revision-ir/v2.3"] = "cad-revision-ir/v2"
    baseline_sha256: str | None = None
    operations: list[RevisionIROperation] = Field(min_length=1)
    post_conditions: list[PostConditionSpec] = Field(default_factory=list)
    rationale_zh: str = ""


# ======================================================================
# 2. Exact 2D Computational Geometry Helpers
# ======================================================================


def _ensure_layer(doc: ezdxf.document.Drawing, layer_name: str) -> None:
    if layer_name not in doc.layers:
        if layer_name.upper() == "CENTER":
            if "CENTER" not in doc.linetypes:
                try:
                    doc.linetypes.add("CENTER", pattern=[12.5, 10.0, -2.5, 0.0])
                except Exception:
                    pass
            doc.layers.add(layer_name, color=1, linetype="CENTER" if "CENTER" in doc.linetypes else "CONTINUOUS")
        else:
            doc.layers.add(layer_name, color=7)


def _entity_center(ent: Any) -> tuple[float, float]:
    dt = ent.dxftype()
    if dt in {"CIRCLE", "ARC"}:
        c = ent.dxf.center
        return float(c.x), float(c.y)
    ext = bbox.extents([ent])
    if ext.has_data:
        return 0.5 * float(ext.extmin.x + ext.extmax.x), 0.5 * float(ext.extmin.y + ext.extmax.y)
    raise RevisionError(f"无法确定参考图元 #{ent.dxf.handle} 的几何中心")


def _line_intersection(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> tuple[float, float] | None:
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-8:
        return None
    det1 = x1 * y2 - y1 * x2
    det2 = x3 * y4 - y3 * x4
    px = (det1 * (x3 - x4) - (x1 - x2) * det2) / denom
    py = (det1 * (y3 - y4) - (y1 - y2) * det2) / denom
    return px, py


def _apply_corner_to_two_lines(
    msp: Any,
    ln1: Any,
    ln2: Any,
    *,
    mode: Literal["chamfer", "fillet"],
    d1: float,
    d2: float,
    radius: float,
) -> tuple[list[str], list[str]]:
    """Trim two intersecting LINEs and insert exact chamfer LINE or tangent fillet ARC."""
    a1 = (float(ln1.dxf.start.x), float(ln1.dxf.start.y))
    b1 = (float(ln1.dxf.end.x), float(ln1.dxf.end.y))
    a2 = (float(ln2.dxf.start.x), float(ln2.dxf.start.y))
    b2 = (float(ln2.dxf.end.x), float(ln2.dxf.end.y))

    inter = _line_intersection(a1, b1, a2, b2)
    if inter is None:
        raise RevisionError(f"直线 #{ln1.dxf.handle} 与 #{ln2.dxf.handle} 平行或共线，无法执行倒角/圆角。")
    px, py = inter

    # Identify near (corner) vs far (surviving) endpoint on each line
    if hypot(a1[0] - px, a1[1] - py) <= hypot(b1[0] - px, b1[1] - py):
        near1_attr, far1 = "start", b1
    else:
        near1_attr, far1 = "end", a1

    if hypot(a2[0] - px, a2[1] - py) <= hypot(b2[0] - px, b2[1] - py):
        near2_attr, far2 = "start", b2
    else:
        near2_attr, far2 = "end", a2

    len1 = hypot(far1[0] - px, far1[1] - py)
    len2 = hypot(far2[0] - px, far2[1] - py)
    if len1 <= 1e-5 or len2 <= 1e-5:
        raise RevisionError("交点与线段远端重合，边长不足以倒角/倒圆角。")

    u1 = ((far1[0] - px) / len1, (far1[1] - px) / len1)
    u2 = ((far2[0] - px) / len2, (far2[1] - px) / len2)
    dot_u = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
    theta = acos(dot_u)
    if theta <= 1e-3 or abs(pi - theta) <= 1e-3:
        raise RevisionError("相邻两边夹角退化（接近 0° 或 180°），无法倒角/倒圆角。")

    layer = str(ln1.dxf.get("layer", "OUTLINE"))
    z1 = float(getattr(ln1.dxf, near1_attr).z)
    z2 = float(getattr(ln2.dxf, near2_attr).z)

    if mode == "chamfer":
        if d1 >= len1 - 1e-4 or d2 >= len2 - 1e-4:
            raise RevisionError(f"倒角尺寸 ({d1:g}×{d2:g}) 超出相邻边可用长度 ({len1:.2f}, {len2:.2f})。")
        t1 = (px + d1 * u1[0], py + d1 * u1[1])
        t2 = (px + d2 * u2[0], py + d2 * u2[1])
        setattr(ln1.dxf, near1_attr, (t1[0], t1[1], z1))
        setattr(ln2.dxf, near2_attr, (t2[0], t2[1], z2))
        cham_ln = msp.add_line(t1, t2, dxfattribs={"layer": layer})
        return [str(ln1.dxf.handle).upper(), str(ln2.dxf.handle).upper()], [str(cham_ln.dxf.handle).upper()]

    # mode == "fillet"
    half_ang = 0.5 * theta
    trim_t = radius / tan(half_ang)
    if trim_t >= len1 - 1e-4 or trim_t >= len2 - 1e-4:
        raise RevisionError(f"圆角半径 R{radius:g} 所需切线长 {trim_t:.2f} 超出相邻边可用长度 ({len1:.2f}, {len2:.2f})。")
    t1 = (px + trim_t * u1[0], py + trim_t * u1[1])
    t2 = (px + trim_t * u2[0], py + trim_t * u2[1])
    bx, by = u1[0] + u2[0], u1[1] + u2[1]
    blen = hypot(bx, by)
    center_dist = radius / sin(half_ang)
    ox = px + center_dist * (bx / blen)
    oy = py + center_dist * (by / blen)

    ang1 = degrees(atan2(t1[1] - oy, t1[0] - ox)) % 360.0
    ang2 = degrees(atan2(t2[1] - oy, t2[0] - ox)) % 360.0
    if (ang2 - ang1) % 360.0 <= 180.0:
        sa, ea = ang1, ang2
    else:
        sa, ea = ang2, ang1

    setattr(ln1.dxf, near1_attr, (t1[0], t1[1], z1))
    setattr(ln2.dxf, near2_attr, (t2[0], t2[1], z2))
    arc = msp.add_arc((ox, oy), radius, sa, ea, dxfattribs={"layer": layer})
    return [str(ln1.dxf.handle).upper(), str(ln2.dxf.handle).upper()], [str(arc.dxf.handle).upper()]


def _apply_corner_to_lwpolyline(
    poly: Any,
    *,
    mode: Literal["chamfer", "fillet"],
    d1: float,
    d2: float,
    radius: float,
    corner_hint: tuple[float, float] | None,
    region_box: Sequence[float] | None,
    all_in_region: bool,
) -> None:
    raw_pts = [(float(x), float(y), float(s), float(e), float(b)) for x, y, s, e, b in poly.get_points("xyseb")]
    # Drop duplicate closing vertex if explicitly repeated on closed polyline
    if poly.closed and len(raw_pts) >= 4 and hypot(raw_pts[-1][0] - raw_pts[0][0], raw_pts[-1][1] - raw_pts[0][1]) <= 1e-5:
        raw_pts = raw_pts[:-1]
    n = len(raw_pts)
    if n < 3:
        raise RevisionError(f"多段线 #{poly.dxf.handle} 顶点数不足 3 个，无法倒角/倒圆角。")

    valid_indices = list(range(n)) if poly.closed else list(range(1, n - 1))
    if not valid_indices:
        raise RevisionError(f"开放多段线 #{poly.dxf.handle} 无内部拐角顶点。")

    target_indices: list[int] = []
    if region_box is not None and len(region_box) == 4:
        rx0, ry0, rx1, ry1 = (
            min(region_box[0], region_box[2]),
            min(region_box[1], region_box[3]),
            max(region_box[0], region_box[2]),
            max(region_box[1], region_box[3]),
        )
        in_box = [idx for idx in valid_indices if rx0 <= raw_pts[idx][0] <= rx1 and ry0 <= raw_pts[idx][1] <= ry1]
        if all_in_region and in_box:
            target_indices = in_box
        elif in_box and corner_hint is None:
            if len(in_box) == 1:
                target_indices = in_box
            else:
                cx, cy = 0.5 * (rx0 + rx1), 0.5 * (ry0 + ry1)
                target_indices = [min(in_box, key=lambda idx: hypot(raw_pts[idx][0] - cx, raw_pts[idx][1] - cy))]

    if not target_indices:
        if all_in_region and region_box is None:
            target_indices = valid_indices
        elif corner_hint is not None:
            hx, hy = corner_hint
            target_indices = [min(valid_indices, key=lambda idx: hypot(raw_pts[idx][0] - hx, raw_pts[idx][1] - hy))]
        elif region_box is not None and len(region_box) == 4:
            cx, cy = 0.5 * (region_box[0] + region_box[2]), 0.5 * (region_box[1] + region_box[3])
            target_indices = [min(valid_indices, key=lambda idx: hypot(raw_pts[idx][0] - cx, raw_pts[idx][1] - cy))]
        else:
            target_indices = [valid_indices[0]]

    target_set = set(target_indices)
    new_pts: list[tuple[float, float, float, float, float]] = []
    beveled_any = False

    for i in range(n):
        x, y, sw, ew, bulge = raw_pts[i]
        if i not in target_set:
            new_pts.append((x, y, sw, ew, bulge))
            continue

        prev_i = (i - 1) % n
        next_i = (i + 1) % n
        prev_pt = raw_pts[prev_i]
        next_pt = raw_pts[next_i]
        if abs(prev_pt[4]) > 1e-6 or abs(bulge) > 1e-6:
            if len(target_set) == 1:
                raise RevisionError("目标拐角相邻段已含圆弧凸度，无法重复倒角/倒圆角。")
            new_pts.append((x, y, sw, ew, bulge))
            continue

        v_prev = (prev_pt[0] - x, prev_pt[1] - y)
        v_next = (next_pt[0] - x, next_pt[1] - y)
        l_prev = hypot(*v_prev)
        l_next = hypot(*v_next)
        if l_prev <= 1e-5 or l_next <= 1e-5:
            new_pts.append((x, y, sw, ew, bulge))
            continue
        u_prev = (v_prev[0] / l_prev, v_prev[1] / l_prev)
        u_next = (v_next[0] / l_next, v_next[1] / l_next)
        dot_u = max(-1.0, min(1.0, u_prev[0] * u_next[0] + u_prev[1] * u_next[1]))
        theta = acos(dot_u)
        if theta <= 1e-3 or abs(pi - theta) <= 1e-3:
            new_pts.append((x, y, sw, ew, bulge))
            continue

        limit_factor = 0.48 if len(target_set) > 1 else 0.96
        if mode == "chamfer":
            if d1 > l_prev * limit_factor or d2 > l_next * limit_factor:
                raise RevisionError(f"倒角尺寸 ({d1:g}×{d2:g}) 超出多段线相邻边长度限制 ({l_prev:.2f}, {l_next:.2f})。")
            t1 = (x + d1 * u_prev[0], y + d1 * u_prev[1])
            t2 = (x + d2 * u_next[0], y + d2 * u_next[1])
            new_pts.append((t1[0], t1[1], sw, ew, 0.0))
            new_pts.append((t2[0], t2[1], sw, ew, 0.0))
            beveled_any = True
        else:
            half_ang = 0.5 * theta
            trim_t = radius / tan(half_ang)
            if trim_t > l_prev * limit_factor or trim_t > l_next * limit_factor:
                raise RevisionError(f"圆角半径 R{radius:g} 所需切线长 {trim_t:.2f} 超出多段线边长 ({l_prev:.2f}, {l_next:.2f})。")
            t1 = (x + trim_t * u_prev[0], y + trim_t * u_prev[1])
            t2 = (x + trim_t * u_next[0], y + trim_t * u_next[1])
            # Signed bulge from T1 -> T2: turn direction from incoming (-u_prev) to outgoing (u_next)
            in_dir = (-u_prev[0], -u_prev[1])
            cross_turn = in_dir[0] * u_next[1] - in_dir[1] * u_next[0]
            sweep = pi - theta
            arc_bulge = tan(0.25 * sweep) * (1.0 if cross_turn >= 0.0 else -1.0)
            new_pts.append((t1[0], t1[1], sw, ew, arc_bulge))
            new_pts.append((t2[0], t2[1], sw, ew, 0.0))
            beveled_any = True

    if not beveled_any:
        raise RevisionError(f"多段线 #{poly.dxf.handle} 未找到满足几何条件的直角/折角顶点。")
    poly.set_points(new_pts, format="xyseb")


def _reflect_point(
    pt: tuple[float, float],
    p0: tuple[float, float],
    ux: float,
    uy: float,
) -> tuple[float, float]:
    vx, vy = pt[0] - p0[0], pt[1] - p0[1]
    dot = vx * ux + vy * uy
    proj_x, proj_y = p0[0] + dot * ux, p0[1] + dot * uy
    return 2.0 * proj_x - pt[0], 2.0 * proj_y - pt[1]


def _mirror_single_entity(
    msp: Any,
    ent: Any,
    p0: tuple[float, float],
    ux: float,
    uy: float,
) -> Any | None:
    dt = ent.dxftype()
    lyr = str(ent.dxf.get("layer", "OUTLINE"))
    if dt == "LINE":
        s = _reflect_point((float(ent.dxf.start.x), float(ent.dxf.start.y)), p0, ux, uy)
        e = _reflect_point((float(ent.dxf.end.x), float(ent.dxf.end.y)), p0, ux, uy)
        return msp.add_line(s, e, dxfattribs={"layer": lyr})
    if dt == "CIRCLE":
        c = _reflect_point((float(ent.dxf.center.x), float(ent.dxf.center.y)), p0, ux, uy)
        return msp.add_circle(c, float(ent.dxf.radius), dxfattribs={"layer": lyr})
    if dt == "ARC":
        cx, cy = float(ent.dxf.center.x), float(ent.dxf.center.y)
        r = float(ent.dxf.radius)
        sa = radians(float(ent.dxf.start_angle))
        ea = radians(float(ent.dxf.end_angle))
        ps = _reflect_point((cx + r * cos(sa), cy + r * sin(sa)), p0, ux, uy)
        pe = _reflect_point((cx + r * cos(ea), cy + r * sin(ea)), p0, ux, uy)
        c_new = _reflect_point((cx, cy), p0, ux, uy)
        # Reflection reverses orientation (CCW becomes CW -> swap start/end angles!)
        new_sa = degrees(atan2(pe[1] - c_new[1], pe[0] - c_new[0])) % 360.0
        new_ea = degrees(atan2(ps[1] - c_new[1], ps[0] - c_new[0])) % 360.0
        return msp.add_arc(c_new, r, new_sa, new_ea, dxfattribs={"layer": lyr})
    if dt == "LWPOLYLINE":
        pts = [(float(x), float(y), float(s), float(e), float(b)) for x, y, s, e, b in ent.get_points("xyseb")]
        ref_pts = [(*_reflect_point((x, y), p0, ux, uy), s, e, -b) for x, y, s, e, b in pts]
        new_pl = msp.add_lwpolyline(ref_pts, format="xyseb", close=bool(ent.closed), dxfattribs={"layer": lyr})
        return new_pl
    if dt == "TEXT":
        ins = ent.get_placement()[1] or ent.dxf.insert
        new_ins = _reflect_point((float(ins.x), float(ins.y)), p0, ux, uy)
        t = msp.add_text(
            str(ent.dxf.get("text", "")),
            dxfattribs={
                "layer": lyr,
                "height": float(ent.dxf.get("height", 3.5)),
                "insert": new_ins,
            },
        )
        return t
    return None


# ======================================================================
# 3. Deterministic Execution of `StructuredRevisionIR`
# ======================================================================


def execute_revision_ir(
    dxf_path: Path | str,
    output_dir: Path | str,
    ir: StructuredRevisionIR | dict[str, Any],
    *,
    region_box: Sequence[float] | None = None,
    candidate_handles: Sequence[str] | None = None,
    expected_sha256: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and atomically execute a `StructuredRevisionIR` against a DXF drawing."""
    src_path = Path(dxf_path).expanduser().resolve(strict=True)
    current_sha = sha256_file(src_path)

    parsed_ir = ir if isinstance(ir, StructuredRevisionIR) else StructuredRevisionIR.model_validate(ir)
    check_sha = expected_sha256 or parsed_ir.baseline_sha256
    if check_sha and check_sha.lower() != current_sha.lower():
        raise RevisionError("图纸哈希已变化（baseline_sha256 不匹配），请刷新最新图纸后重试。")

    doc = ezdxf.readfile(src_path)
    doc_units = int(getattr(doc, "units", 4) or 0)
    msp = doc.modelspace()

    before_snap = snapshot_modelspace(doc)
    checkpoint = DocumentCheckpoint(doc)

    modified_handles: list[str] = []
    added_handles: list[str] = []
    deleted_handles: list[str] = []
    summaries: list[str] = []
    auto_checks: list[PostConditionSpec] = []
    net_move_doc: dict[str, tuple[float, float]] = {}
    final_circle_diam_doc: dict[str, float] = {}
    final_linear_span_doc: dict[str, tuple[float, Literal["auto", "horizontal", "vertical"]]] = {}

    cand_upper = [str(h).upper() for h in (candidate_handles or []) if h]

    try:
        for step in parsed_ir.operations:
            by_handle = {str(e.dxf.handle).upper(): e for e in msp if e.is_alive}

            if isinstance(step, AddCircleHoleOp):
                d_unit = step.diameter_unit if step.diameter_unit is not None else step.unit
                o_unit = step.offset_unit if step.offset_unit is not None else step.unit
                c_unit = step.center_unit if step.center_unit is not None else step.unit
                dia = scale_value_to_doc_units(step.diameter, d_unit, doc_units)
                ox = scale_value_to_doc_units(step.offset[0], o_unit, doc_units)
                oy = scale_value_to_doc_units(step.offset[1], o_unit, doc_units)
                if step.center is not None:
                    cx = scale_value_to_doc_units(step.center[0], c_unit, doc_units) + ox
                    cy = scale_value_to_doc_units(step.center[1], c_unit, doc_units) + oy
                elif step.reference_handle and step.reference_handle.upper() in by_handle:
                    rcx, rcy = _entity_center(by_handle[step.reference_handle.upper()])
                    cx, cy = rcx + ox, rcy + oy
                elif region_box is not None and len(region_box) == 4:
                    cx = 0.5 * (region_box[0] + region_box[2]) + ox
                    cy = 0.5 * (region_box[1] + region_box[3]) + oy
                elif cand_upper and cand_upper[0] in by_handle:
                    rcx, rcy = _entity_center(by_handle[cand_upper[0]])
                    cx, cy = rcx + ox, rcy + oy
                else:
                    raise RevisionError("新增圆孔需要指定圆心坐标、参考图元或框选位置。")

                _ensure_layer(doc, step.layer)
                cir = msp.add_circle((cx, cy), dia / 2.0, dxfattribs={"layer": step.layer})
                added_handles.append(str(cir.dxf.handle).upper())
                if step.with_centerlines:
                    _ensure_layer(doc, "CENTER")
                    ext_r = max(dia * 0.70, dia / 2.0 + 2.0)
                    cl_h = msp.add_line((cx - ext_r, cy), (cx + ext_r, cy), dxfattribs={"layer": "CENTER"})
                    cl_v = msp.add_line((cx, cy - ext_r), (cx, cy + ext_r), dxfattribs={"layer": "CENTER"})
                    added_handles.extend([str(cl_h.dxf.handle).upper(), str(cl_v.dxf.handle).upper()])
                auto_checks.append(
                    CircleExistsAtCheck(
                        center=(cx, cy),
                        diameter=dia,
                        layer=step.layer,
                        label=f"新增圆孔 ({cx:.2f}, {cy:.2f}) ⌀{dia:g}",
                    )
                )
                summaries.append(f"已在 ({cx:.1f}, {cy:.1f}) 新增 ⌀{dia:g} 圆孔")

            elif isinstance(step, AddBoltCircleOp):
                h_unit = step.hole_unit if step.hole_unit is not None else step.unit
                p_unit = step.pcd_unit if step.pcd_unit is not None else step.unit
                c_unit = step.center_unit if step.center_unit is not None else step.unit
                hole_d = scale_value_to_doc_units(step.hole_diameter, h_unit, doc_units)
                pcd = scale_value_to_doc_units(step.bolt_circle_diameter, p_unit, doc_units)
                if pcd <= hole_d * 1.2:
                    raise RevisionError(f"节圆直径 ({pcd:g}) 必须大于单孔直径 ({hole_d:g})。")
                if step.center is not None:
                    cx = scale_value_to_doc_units(step.center[0], c_unit, doc_units)
                    cy = scale_value_to_doc_units(step.center[1], c_unit, doc_units)
                elif step.reference_handle and step.reference_handle.upper() in by_handle:
                    cx, cy = _entity_center(by_handle[step.reference_handle.upper()])
                else:
                    ref_circles = [
                        by_handle[h]
                        for h in cand_upper
                        if h in by_handle and by_handle[h].dxftype() == "CIRCLE"
                    ]
                    if ref_circles:
                        biggest = max(ref_circles, key=lambda e: float(e.dxf.radius))
                        cx, cy = float(biggest.dxf.center.x), float(biggest.dxf.center.y)
                    elif region_box is not None and len(region_box) == 4:
                        cx = 0.5 * (region_box[0] + region_box[2])
                        cy = 0.5 * (region_box[1] + region_box[3])
                    else:
                        cx, cy = 0.0, 0.0

                _ensure_layer(doc, step.layer)
                r_pcd = pcd / 2.0
                for k in range(step.count):
                    ang = radians(step.start_angle_deg) + 2.0 * pi * k / step.count
                    hx, hy = cx + r_pcd * cos(ang), cy + r_pcd * sin(ang)
                    hc = msp.add_circle((hx, hy), hole_d / 2.0, dxfattribs={"layer": step.layer})
                    added_handles.append(str(hc.dxf.handle).upper())
                if step.with_pitch_circle:
                    _ensure_layer(doc, "CENTER")
                    pc = msp.add_circle((cx, cy), r_pcd, dxfattribs={"layer": "CENTER"})
                    added_handles.append(str(pc.dxf.handle).upper())
                auto_checks.append(
                    BoltCircleExistsCheck(
                        center=(cx, cy),
                        count=step.count,
                        hole_diameter=hole_d,
                        bolt_circle_diameter=pcd,
                        start_angle_deg=step.start_angle_deg,
                        label=f"{step.count}×⌀{hole_d:g} 均布孔 (节圆 ⌀{pcd:g})",
                    )
                )
                summaries.append(f"已围绕 ({cx:.1f}, {cy:.1f}) 新增 {step.count}×⌀{hole_d:g} 均布螺栓孔（节圆 ⌀{pcd:g}）")

            elif isinstance(step, (ChamferCornerOp, FilletCornerOp)):
                is_chamfer = isinstance(step, ChamferCornerOp)
                if is_chamfer:
                    u1 = step.distance1_unit if step.distance1_unit is not None else step.unit
                    u2 = (
                        step.distance2_unit
                        if step.distance2_unit is not None
                        else (u1 if step.distance2 is None else step.unit)
                    )
                    d1 = scale_value_to_doc_units(step.distance1, u1, doc_units)
                    d2 = scale_value_to_doc_units(step.distance2 if step.distance2 is not None else step.distance1, u2, doc_units)
                    rad = 0.0
                else:
                    d1 = d2 = 0.0
                    rad = scale_value_to_doc_units(step.radius, step.unit, doc_units)

                pool_handles = [h.upper() for h in step.handles if h] or cand_upper
                pool_ents = [
                    by_handle[h]
                    for h in pool_handles
                    if h in by_handle and str(by_handle[h].dxf.get("layer", "")).upper() not in {"FRAME", "CENTER", "DIM", "TEXT"}
                ]
                line_ents = [e for e in pool_ents if e.dxftype() == "LINE"]
                poly_ents = [e for e in pool_ents if e.dxftype() == "LWPOLYLINE"]

                if len(line_ents) >= 2:
                    # Pick the two lines whose intersection is closest to corner_hint / region_box center
                    best_pair: tuple[Any, Any] | None = None
                    ref_pt = step.corner_hint or (
                        (0.5 * (region_box[0] + region_box[2]), 0.5 * (region_box[1] + region_box[3]))
                        if region_box and len(region_box) == 4
                        else None
                    )
                    best_dist = 1e18
                    for i in range(len(line_ents)):
                        for j in range(i + 1, len(line_ents)):
                            la, lb = line_ents[i], line_ents[j]
                            ip = _line_intersection(
                                (float(la.dxf.start.x), float(la.dxf.start.y)),
                                (float(la.dxf.end.x), float(la.dxf.end.y)),
                                (float(lb.dxf.start.x), float(lb.dxf.start.y)),
                                (float(lb.dxf.end.x), float(lb.dxf.end.y)),
                            )
                            if ip is None:
                                continue
                            d_score = hypot(ip[0] - ref_pt[0], ip[1] - ref_pt[1]) if ref_pt else 0.0
                            if d_score < best_dist:
                                best_dist = d_score
                                best_pair = (la, lb)
                    if best_pair is None:
                        raise RevisionError("选中的两条直线平行，无法倒角/倒圆角。")
                    mod_h, add_h = _apply_corner_to_two_lines(
                        msp,
                        best_pair[0],
                        best_pair[1],
                        mode="chamfer" if is_chamfer else "fillet",
                        d1=d1,
                        d2=d2,
                        radius=rad,
                    )
                    modified_handles.extend(mod_h)
                    added_handles.extend(add_h)
                elif poly_ents:
                    target_poly = poly_ents[0]
                    _apply_corner_to_lwpolyline(
                        target_poly,
                        mode="chamfer" if is_chamfer else "fillet",
                        d1=d1,
                        d2=d2,
                        radius=rad,
                        corner_hint=step.corner_hint,
                        region_box=region_box,
                        all_in_region=step.all_corners_in_region,
                    )
                    modified_handles.append(str(target_poly.dxf.handle).upper())
                else:
                    raise RevisionError("倒角/倒圆角需要选中两条相交轮廓线或一个多段线拐角。")

                if is_chamfer:
                    auto_checks.append(
                        CornerChamferExistsCheck(distance1=d1, distance2=d2, min_count=1, label=f"倒角 C{d1:g}")
                    )
                else:
                    auto_checks.append(
                        CornerFilletExistsCheck(radius=rad, min_count=1, label=f"倒圆角 R{rad:g}")
                    )
                summaries.append(
                    f"已完成拐角倒角 C{d1:g}" + (f"×{d2:g}" if abs(d1 - d2) > 1e-6 else "")
                    if is_chamfer
                    else f"已完成拐角倒圆角 R{rad:g}"
                )

            elif isinstance(step, MirrorEntitiesOp):
                src_handles = [h.upper() for h in step.handles if h] or cand_upper
                src_ents = [
                    by_handle[h]
                    for h in src_handles
                    if h in by_handle and str(by_handle[h].dxf.get("layer", "")).upper() != "FRAME"
                ]
                if not src_ents:
                    raise RevisionError("镜像操作未找到有效的源图元。")

                if step.axis == "line" and step.axis_point1 is not None and step.axis_point2 is not None:
                    p1 = (
                        scale_value_to_doc_units(step.axis_point1[0], step.unit, doc_units),
                        scale_value_to_doc_units(step.axis_point1[1], step.unit, doc_units),
                    )
                    p2 = (
                        scale_value_to_doc_units(step.axis_point2[0], step.unit, doc_units),
                        scale_value_to_doc_units(step.axis_point2[1], step.unit, doc_units),
                    )
                    alen = hypot(p2[0] - p1[0], p2[1] - p1[1])
                    if alen <= 1e-6:
                        raise RevisionError("镜像轴两点不能重合。")
                    p0, ux, uy = p1, (p2[0] - p1[0]) / alen, (p2[1] - p1[1]) / alen
                elif step.axis == "horizontal":
                    ay = (
                        scale_value_to_doc_units(step.axis_coord, step.unit, doc_units)
                        if step.axis_coord is not None
                        else 0.0
                    )
                    p0, ux, uy = (0.0, ay), 1.0, 0.0
                else:
                    ax = (
                        scale_value_to_doc_units(step.axis_coord, step.unit, doc_units)
                        if step.axis_coord is not None
                        else 0.0
                    )
                    p0, ux, uy = (ax, 0.0), 0.0, 1.0

                step_added: list[str] = []
                for ent in src_ents:
                    cloned = _mirror_single_entity(msp, ent, p0, ux, uy)
                    if cloned is not None:
                        ch = str(cloned.dxf.handle).upper()
                        added_handles.append(ch)
                        step_added.append(ch)
                        if not step.keep_original:
                            dh = str(ent.dxf.handle).upper()
                            deleted_handles.append(dh)
                            msp.delete_entity(ent)
                if not step_added:
                    raise RevisionError("选中图元类型暂不支持几何镜像。")
                auto_checks.append(
                    MirroredHandlesExistCheck(added_handles=step_added, min_count=len(step_added), label="镜像图元存在性")
                )
                summaries.append(f"已沿{'水平' if step.axis == 'horizontal' else '垂直' if step.axis == 'vertical' else '指定'}对称轴镜像 {len(step_added)} 个图元")

            elif isinstance(step, DeleteEntitiesOp):
                pool_h = [h.upper() for h in step.handles if h] or cand_upper
                del_targets: list[str] = []
                for h in pool_h:
                    ent = by_handle.get(h)
                    if ent is None or str(ent.dxf.get("layer", "")).upper() == "FRAME":
                        continue
                    dt = ent.dxftype()
                    lyr = str(ent.dxf.get("layer", "")).upper()
                    if step.entity_filter == "circle" and dt not in {"CIRCLE", "ARC"}:
                        continue
                    if step.entity_filter == "line" and dt != "LINE":
                        continue
                    if step.entity_filter == "text" and dt not in {"TEXT", "MTEXT"}:
                        continue
                    if step.entity_filter == "dimension" and dt != "DIMENSION":
                        continue
                    if step.entity_filter == "hidden" and lyr != "HIDDEN":
                        continue
                    if step.entity_filter == "center" and lyr != "CENTER":
                        continue
                    del_targets.append(h)
                if not del_targets:
                    raise RevisionError("删除操作未命中匹配条件的可删除图元。")
                for h in del_targets:
                    msp.delete_entity(by_handle[h])
                    deleted_handles.append(h)
                summaries.append(f"已精准删除 {len(del_targets)} 个目标图元")

            elif isinstance(step, MoveEntitiesOp):
                pool_h = [h.upper() for h in step.handles if h] or cand_upper
                mv_targets = [
                    h for h in pool_h
                    if h in by_handle and str(by_handle[h].dxf.get("layer", "")).upper() != "FRAME"
                ]
                if not mv_targets:
                    raise RevisionError("平移操作未命中可移动图元。")
                ux_tok = step.dx_unit if step.dx_unit is not None else step.unit
                uy_tok = step.dy_unit if step.dy_unit is not None else step.unit
                dx_doc = scale_value_to_doc_units(step.dx, ux_tok, doc_units)
                dy_doc = scale_value_to_doc_units(step.dy, uy_tok, doc_units)
                mat = Matrix44.translate(dx_doc, dy_doc, 0.0)
                for h in mv_targets:
                    by_handle[h].transform(mat)
                    modified_handles.append(h)
                    prev_dx, prev_dy = net_move_doc.get(h, (0.0, 0.0))
                    net_move_doc[h] = (prev_dx + dx_doc, prev_dy + dy_doc)
                summaries.append(f"已将 {len(mv_targets)} 个图元平移 ({dx_doc:+g}, {dy_doc:+g})")

            elif isinstance(step, ResizeHoleOp):
                from .nl_revise import _match_associated_circle_dimension, _replace_exact_nominal_token

                pool_h = [h.upper() for h in step.handles if h] or cand_upper
                cir_cands = [
                    h for h in pool_h
                    if h in by_handle
                    and by_handle[h].dxftype() in {"CIRCLE", "ARC"}
                    and str(by_handle[h].dxf.get("layer", "")).upper() != "CENTER"
                ]
                if step.match_old_diameter is not None:
                    ou = step.old_diameter_unit if step.old_diameter_unit is not None else step.unit
                    old_target_d = scale_value_to_doc_units(step.match_old_diameter, ou, doc_units)
                    cir_cands = [
                        h for h in cir_cands
                        if isclose(2.0 * float(by_handle[h].dxf.radius), old_target_d, abs_tol=max(1e-2, old_target_d * 0.02))
                    ]
                if not cir_cands:
                    raise RevisionError("改孔径操作未找到匹配的圆/圆弧图元。")

                nu = step.new_diameter_unit if step.new_diameter_unit is not None else step.unit
                new_d_doc = scale_value_to_doc_units(step.new_diameter, nu, doc_units)
                new_r_doc = new_d_doc / 2.0
                old_r_doc = float(by_handle[cir_cands[0]].dxf.radius)
                old_d_doc = 2.0 * old_r_doc
                target_geom = [
                    (float(by_handle[h].dxf.center.x), float(by_handle[h].dxf.center.y), float(by_handle[h].dxf.radius))
                    for h in cir_cands
                ]
                for h in cir_cands:
                    by_handle[h].dxf.radius = new_r_doc
                    modified_handles.append(h)
                    final_circle_diam_doc[h] = new_d_doc

                synced_dim_cnt = 0
                if step.sync_dimensions:
                    for ent in msp:
                        if ent.dxftype() != "DIMENSION":
                            continue
                        ok_a, is_rad_d, matched_c = _match_associated_circle_dimension(
                            ent, target_geom, old_d_doc, old_r_doc
                        )
                        if ok_a and matched_c is not None:
                            dh = str(ent.dxf.handle).upper()
                            cur_txt = str(ent.dxf.get("text", ""))
                            old_v = old_r_doc if is_rad_d else old_d_doc
                            new_v = new_r_doc if is_rad_d else new_d_doc
                            if cur_txt and cur_txt != "<>":
                                ent.dxf.text = _replace_exact_nominal_token(cur_txt, old_v, new_v)
                            try:
                                ent.render()
                            except Exception:
                                pass
                            modified_handles.append(dh)
                            synced_dim_cnt += 1
                summaries.append(
                    f"已将 {len(cir_cands)} 处圆孔直径改为 ⌀{new_d_doc:g}"
                    + (f"（同步 {synced_dim_cnt} 处关联标注）" if synced_dim_cnt else "")
                )

            elif isinstance(step, ResizeLinearOp):
                pool_h = [h.upper() for h in step.handles if h] or cand_upper
                lu = step.new_length_unit if step.new_length_unit is not None else step.unit
                new_len_doc = scale_value_to_doc_units(step.new_length, lu, doc_units)
                poly_h = [
                    h for h in pool_h
                    if h in by_handle
                    and by_handle[h].dxftype() == "LWPOLYLINE"
                    and str(by_handle[h].dxf.get("layer", "")).upper() not in {"FRAME", "CENTER", "DIM"}
                ]
                line_h = [
                    h for h in pool_h
                    if h in by_handle
                    and by_handle[h].dxftype() == "LINE"
                    and str(by_handle[h].dxf.get("layer", "")).upper() not in {"FRAME", "CENTER", "DIM"}
                ]
                if poly_h:
                    p_ent = by_handle[poly_h[0]]
                    pts = list(p_ent.get_points("xyseb"))
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    ax_mode = step.axis if step.axis != "auto" else "horizontal"
                    if ax_mode == "vertical":
                        if max_y - min_y <= 1e-6:
                            raise RevisionError("轮廓高度跨度为 0，无法调整高度。")
                        ratio = new_len_doc / (max_y - min_y)
                        new_pts = [(p[0], min_y + (float(p[1]) - min_y) * ratio, p[2], p[3], p[4]) for p in pts]
                    else:
                        if max_x - min_x <= 1e-6:
                            raise RevisionError("轮廓宽度跨度为 0，无法调整宽度。")
                        ratio = new_len_doc / (max_x - min_x)
                        new_pts = [(min_x + (float(p[0]) - min_x) * ratio, p[1], p[2], p[3], p[4]) for p in pts]
                    p_ent.set_points(new_pts, format="xyseb")
                    modified_handles.append(poly_h[0])
                    final_linear_span_doc[poly_h[0]] = (new_len_doc, ax_mode)
                    summaries.append(f"已将轮廓 #{poly_h[0]} 跨度调整为 {new_len_doc:g}")
                elif line_h:
                    l_ent = by_handle[line_h[0]]
                    sx, sy, sz = float(l_ent.dxf.start.x), float(l_ent.dxf.start.y), float(l_ent.dxf.start.z)
                    ex, ey, ez = float(l_ent.dxf.end.x), float(l_ent.dxf.end.y), float(l_ent.dxf.end.z)
                    cur_l = hypot(ex - sx, ey - sy)
                    if cur_l <= 1e-7:
                        raise RevisionError("退化零长直线无法按方向调整长度。")
                    ux, uy = (ex - sx) / cur_l, (ey - sy) / cur_l
                    l_ent.dxf.end = (sx + ux * new_len_doc, sy + uy * new_len_doc, ez)
                    modified_handles.append(line_h[0])
                    final_linear_span_doc[line_h[0]] = (new_len_doc, "auto")
                    summaries.append(f"已将直线 #{line_h[0]} 长度调整为 {new_len_doc:g}")
                else:
                    raise RevisionError("调整长度操作未命中直线或多段线图元。")

            elif isinstance(step, UpdateTextOp):
                pool_h = [h.upper() for h in step.handles if h] or cand_upper
                txt_h = [
                    h for h in pool_h
                    if h in by_handle and by_handle[h].dxftype() in {"TEXT", "MTEXT", "DIMENSION"}
                ]
                if step.old_text:
                    txt_h = [
                        h for h in txt_h
                        if step.old_text in (
                            str(by_handle[h].dxf.get("text", ""))
                            if by_handle[h].dxftype() in {"TEXT", "DIMENSION"}
                            else by_handle[h].plain_text()
                        )
                    ]
                if not txt_h:
                    raise RevisionError("文字修改操作未命中匹配的文字图元。")
                for h in txt_h:
                    ent = by_handle[h]
                    if ent.dxftype() in {"TEXT", "DIMENSION"}:
                        cur = str(ent.dxf.get("text", ""))
                        ent.dxf.text = cur.replace(step.old_text, step.new_text) if step.old_text else step.new_text
                    else:
                        ent.text = ent.text.replace(step.old_text, step.new_text) if step.old_text else step.new_text
                    modified_handles.append(h)
                auto_checks.append(
                    TextContainsCheck(handles=txt_h, expected_substring=step.new_text, label=f"文字包含「{step.new_text}」")
                )
                summaries.append(f"已更新 {len(txt_h)} 处文字为「{step.new_text}」")

        del_set = set(deleted_handles)
        if del_set:
            auto_checks.append(HandlesAbsentCheck(handles=sorted(del_set), label="已删除句柄缺席核验"))
        for h, (ndx, ndy) in net_move_doc.items():
            if h not in del_set and h in before_snap:
                auto_checks.append(
                    HandlesTranslatedCheck(handles=[h], dx=ndx, dy=ndy, label=f"图元 #{h} 位移核验")
                )
        for h, fin_d in final_circle_diam_doc.items():
            if h not in del_set:
                auto_checks.append(
                    CircleDiameterEqualsCheck(handles=[h], diameter=fin_d, label=f"圆孔 #{h} 直径=⌀{fin_d:g}")
                )
        for h, (fin_l, fin_ax) in final_linear_span_doc.items():
            if h not in del_set:
                auto_checks.append(
                    LinearSpanEqualsCheck(handles=[h], length=fin_l, axis=fin_ax, label=f"图元 #{h} 跨度={fin_l:g}")
                )

        after_snap = snapshot_modelspace(doc)
        allowed_set = set(modified_handles) | set(added_handles) | set(deleted_handles)
        unexpected = compare_untouched(before_snap, after_snap, allowed_set, 1e-4)
        if unexpected:
            checkpoint.restore()
            raise RevisionError(f"安全护栏拦截到未授权的选区外图元变动，已回滚: {unexpected}")

        dxf_audit = doc.audit()
        if dxf_audit.has_errors:
            checkpoint.restore()
            raise RevisionError(f"IR 改图后 DXF 审计未通过 ({len(dxf_audit.errors)} 处错误)，已回滚。")

        all_post_checks: list[PostConditionSpec] = [*auto_checks, *parsed_ir.post_conditions]
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_dxf = out_dir / "revised.dxf"
        if not dry_run:
            tmp_out = out_dxf.with_name(f".{out_dxf.name}.tmp")
            doc.saveas(tmp_out)
            ver_report = verify_revision_post_conditions(src_path, tmp_out, all_post_checks)
            if not ver_report["passed"]:
                tmp_out.unlink(missing_ok=True)
                checkpoint.restore()
                failed_msgs = [it["detail"] for it in ver_report["items"] if not it["passed"]]
                raise RevisionError(f"事后重读 DXF 物理核验未通过，已拦截输出: {'；'.join(failed_msgs)}")
            tmp_out.replace(out_dxf)
            new_sha = sha256_file(out_dxf)
        else:
            with tempfile.TemporaryDirectory() as tmp_d:
                sim_dxf = Path(tmp_d) / "simulated.dxf"
                doc.saveas(sim_dxf)
                ver_report = verify_revision_post_conditions(src_path, sim_dxf, all_post_checks)
            checkpoint.restore()
            if not ver_report["passed"]:
                failed_msgs = [it["detail"] for it in ver_report["items"] if not it["passed"]]
                raise RevisionError(f"Dry-Run 事后重读 DXF 核验未通过: {'；'.join(failed_msgs)}")
            new_sha = current_sha

        dedup_mod = list(dict.fromkeys(modified_handles))
        dedup_add = list(dict.fromkeys(added_handles))
        dedup_del = list(dict.fromkeys(deleted_handles))
        return {
            "mode": "revision",
            "action": parsed_ir.operations[0].op if len(parsed_ir.operations) == 1 else "multi_op_ir",
            "status": "simulated" if dry_run else "applied",
            "dry_run": dry_run,
            "ir": parsed_ir.model_dump(mode="json"),
            "verification": ver_report,
            "baseline_sha256": current_sha,
            "new_sha256": new_sha,
            "modified_handles": dedup_mod,
            "added_handles": dedup_add,
            "deleted_handles": dedup_del,
            "output_path": None if dry_run else str(out_dxf),
            "summary_zh": "；".join(summaries),
        }
    except RevisionError:
        checkpoint.restore()
        raise
    except Exception as exc:
        checkpoint.restore()
        raise RevisionError(f"结构化 IR 执行失败并已原子回滚: {exc}") from exc


# ======================================================================
# 4. Dual-Mode NL -> `StructuredRevisionIR` Compiler (LLM + Offline Rules)
# ======================================================================


def compile_nl_to_unified_ir(
    prompt_text: str,
    *,
    candidate_handles: Sequence[str] | None = None,
    region_box: Sequence[float] | None = None,
    llm_callable: Callable[[str, dict[str, Any]], dict[str, Any] | str] | None = None,
) -> StructuredRevisionIR | None:
    """Compile ANY single- or multi-clause revision instruction into `StructuredRevisionIR` (all 10 ops)."""
    return compile_nl_to_feature_ir(
        prompt_text,
        candidate_handles=candidate_handles,
        region_box=region_box,
        llm_callable=llm_callable,
        include_core_ops_on_single_clause=True,
    )


def compile_nl_to_feature_ir(
    prompt_text: str,
    *,
    candidate_handles: Sequence[str] | None = None,
    region_box: Sequence[float] | None = None,
    llm_callable: Callable[[str, dict[str, Any]], dict[str, Any] | str] | None = None,
    include_core_ops_on_single_clause: bool = False,
) -> StructuredRevisionIR | None:
    """Compile feature-creation & multi-clause NL instructions into `StructuredRevisionIR`.

    1. If `llm_callable` or `CAD_SKILL_LLM_API_KEY` is configured, attempts structured JSON IR
       generation first and validates strictly against `StructuredRevisionIR`.
    2. Otherwise (or if offline), compiles deterministically via multi-clause geometric intent rules.
    Returns `None` if a single-clause prompt does not request a v2 feature primitive (allowing `nl_revise`
    to dispatch legacy single-clause delete/move/resize/text as usual).
    """
    clean = (prompt_text or "").strip()
    if not clean:
        return None

    cands = [str(h).upper() for h in (candidate_handles or []) if h]

    # Layer A: Optional injected or env-configured LLM Structured IR compiler
    if llm_callable is not None:
        raw_resp = llm_callable(
            clean,
            {
                "candidate_handles": cands,
                "region_box": list(region_box) if region_box else None,
                "schema": StructuredRevisionIR.model_json_schema(),
            },
        )
        payload = json.loads(raw_resp) if isinstance(raw_resp, str) else raw_resp
        return StructuredRevisionIR.model_validate(payload)

    api_key = os.environ.get("CAD_SKILL_LLM_API_KEY", "").strip()
    base_url = os.environ.get("CAD_SKILL_LLM_BASE_URL", "").strip()
    model_name = os.environ.get("CAD_SKILL_LLM_MODEL", "gpt-4o-mini").strip()
    if api_key and base_url.startswith("https://"):
        try:
            sys_msg = (
                "Output ONLY valid JSON matching schema_version='cad-revision-ir/v2.3' with an 'operations' list. "
                "Allowed ops: add_circle_hole, add_bolt_circle, chamfer_corner, fillet_corner, mirror_entities, "
                "delete_entities, move_entities, resize_hole, resize_linear, update_text. "
                "Decompose EVERY requested action in the user prompt into separate items in 'operations'."
            )
            req_body = json.dumps(
                {
                    "model": model_name,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"prompt": clean, "candidate_handles": cands, "region_box": list(region_box or [])},
                                ensure_ascii=False,
                            ),
                        },
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
            content_str = body["choices"][0]["message"]["content"]
            parsed_json = json.loads(content_str)
            if parsed_json.get("operations"):
                return StructuredRevisionIR.model_validate(parsed_json)
        except Exception:
            pass

    # Layer B: Deterministic Offline Unified IR Compiler (exhaustive clause-by-clause verification)
    clauses = _split_feature_clauses(clean)
    if not clauses:
        return None

    allow_core = include_core_ops_on_single_clause or (len(clauses) > 1)
    ops: list[RevisionIROperation] = []
    unparsed_clauses: list[str] = []
    rationales: list[str] = []

    for cl in clauses:
        op, reason = _compile_single_feature_clause(cl, cands, allow_core_ops=allow_core)
        if op is not None:
            ops.append(op)
            rationales.append(reason)
        else:
            unparsed_clauses.append(cl)

    if not ops:
        return None
    if unparsed_clauses:
        raise RevisionError(
            f"复合指令中包含未能完整解析或冲突的子句：「{'；'.join(unparsed_clauses)}」"
        )
    return StructuredRevisionIR(
        schema_version="cad-revision-ir/v2",
        operations=ops,
        rationale_zh="；".join(dict.fromkeys(rationales)),
    )


_PARAM_CONTINUATION_RE = re.compile(
    r"^\s*(?:节圆(?:直径)?|分布圆(?:直径)?|PCD|pcd|圆心|中心|位置|[xXyY]\s*=|轴线\s*[-0-9]|(?:不)?保留原图(?:元)?|删除原图(?:元)?|替换原图(?:元)?)",
    re.IGNORECASE,
)
_ANY_ACTION_VERB_RE = re.compile(
    r"(?:删|去掉|移除|清除|改成|改为|设为|设置|调整|放大|缩小|平移|移动|往[左右上下]|向[左右上下]|替换|"
    r"新增|添加|增加|新开|打一个|加一个|加一圈|倒角|倒圆|圆角|导圆|倒斜角|斜角|镜像|对称复制|"
    r"旋转|翻转|缩放|拉伸|修剪|延伸|打断|分解|"
    r"\b(?:delete|remove|change|set|resize|move|shift|add|chamfer|fillet|mirror|rotate|scale|trim|extend|explode)\b)",
    re.IGNORECASE,
)
_COORD_TUPLE_RE = re.compile(
    rf"[\(（]\s*(?P<x>-?\d+(?:\.\d+)?)\s*(?P<ux>{_UNIT_PAT})?\s*[,，]\s*(?P<y>-?\d+(?:\.\d+)?)\s*(?P<uy>{_UNIT_PAT})?\s*[\)）]\s*(?P<u_after>{_UNIT_PAT})?",
    re.IGNORECASE,
)


def _split_feature_clauses(text: str) -> list[str]:
    """Split prompt into independent action clauses outside `(...)`, merging parameter continuations."""
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in "（(":
            depth += 1
            buf.append(ch)
        elif ch in "）)":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in "，,；;。!！、\n":
            seg = "".join(buf).strip()
            if seg:
                chunks.append(seg)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        chunks.append(tail)

    # Also split inline conjunctions ("并删除...", "同时把...", "然后再...") outside parentheses
    fine_chunks: list[str] = []
    conj_re = re.compile(r"\s*(?:并且?|同时|然后再?|以及|接着|顺便)\s*(?=(?:把|将|在|沿|向|往|删|去掉|移除|改|新增|添加|增加|开|钻|倒|圆角|镜像|旋转|翻转|缩放|修剪|延伸))")
    for seg in chunks:
        parts = [p.strip() for p in conj_re.split(seg) if p.strip()]
        fine_chunks.extend(parts)

    # Merge pure parameter continuations (e.g. "新增 6×Ø10 均布螺栓孔" + "节圆 80", or "向右镜像" + "x=60")
    merged: list[str] = []
    for seg in fine_chunks:
        if (
            merged
            and _PARAM_CONTINUATION_RE.match(seg)
            and (
                re.match(r"^\s*(?:(?:不)?保留原图|删除原图|替换原图)", seg)
                or not re.search(r"(?:删|去掉|移除|改成|改为|新增|添加|倒角|圆角|镜像)", seg)
            )
        ):
            merged[-1] = f"{merged[-1]}，{seg}"
        else:
            merged.append(seg)
    return merged


def _parse_center_from_clause(clause: str) -> tuple[tuple[float, float] | None, str | None, tuple[int, int] | None]:
    m_ctr = _COORD_TUPLE_RE.search(clause)
    if not m_ctr:
        return None, None, None
    ux = m_ctr.group("ux")
    uy = m_ctr.group("uy")
    ua = m_ctr.group("u_after")
    if ux and uy and ux.lower() != uy.lower():
        raise RevisionError(f"圆心坐标 X/Y 单位不一致: ({ux} vs {uy})")
    c_unit = ux or uy or ua
    return (float(m_ctr.group("x")), float(m_ctr.group("y"))), c_unit, m_ctr.span()


def _has_unconsumed_action_tail(clause: str, consumed_spans: list[tuple[int, int]]) -> bool:
    """Return True if residual text outside consumed regex spans still contains another action verb or stray clause."""
    chars = list(clause)
    for s, e in consumed_spans:
        for idx in range(max(0, s), min(len(chars), e)):
            chars[idx] = " "
    leftover = "".join(chars)
    # Strip benign location/target nouns
    leftover = re.sub(
        r"(?:在|于|把|将|对|向|往|沿着?|围绕|中心|圆心|原点|此处|这里|这处|这个|该|矩形|多段线|轮廓|外框|法兰|钢板|板|拐角|直角|折角|边|处|上|内|外|的|个|条|处)+",
        " ",
        leftover,
    ).strip()
    if not leftover:
        return False
    return bool(_ANY_ACTION_VERB_RE.search(leftover))


def _assert_clause_fully_parsed(clause: str, consumed_spans: list[tuple[int, int]]) -> None:
    if _has_unconsumed_action_tail(clause, consumed_spans):
        raise RevisionError(f"指令包含未完整解析或冲突的附加动作，已拦截执行：「{clause}」")


def _compile_single_feature_clause(
    cl: str,
    cands: list[str],
    *,
    allow_core_ops: bool = False,
) -> tuple[RevisionIROperation | None, str]:
    # 1. Bolt Circle Pattern ("新增 6×Ø10 mm 均布螺栓孔，节圆 80" / "加一圈 4xM8 孔 PCD 4 in")
    m_bcd = re.search(
        rf"(?:新增|添加|增加|加(?:一圈)?|打|开|钻)\s*(?P<cnt>\d+)\s*(?:[xX×\-]|个)\s*(?:[⌀∅ØøΦφMm])?\s*(?P<hd>\d+(?:\.\d+)?)\s*(?P<u1>{_UNIT_PAT})?(?:[^0-9]*?)(?:节圆(?:直径)?|分布圆(?:直径)?|PCD|pcd|圆周)\s*(?:直径|为|:|：)?\s*(?:[⌀∅ØøΦφ])?\s*(?P<pcd>\d+(?:\.\d+)?)\s*(?P<u2>{_UNIT_PAT})?",
        cl,
        re.IGNORECASE,
    )
    if m_bcd:
        ctr, ctr_u, ctr_span = _parse_center_from_clause(cl)
        spans = [m_bcd.span()] + ([ctr_span] if ctr_span else [])
        _assert_clause_fully_parsed(cl, spans)
        return (
            AddBoltCircleOp(
                count=int(m_bcd.group("cnt")),
                hole_diameter=float(m_bcd.group("hd")),
                bolt_circle_diameter=float(m_bcd.group("pcd")),
                center=ctr,
                hole_unit=m_bcd.group("u1"),
                pcd_unit=m_bcd.group("u2"),
                center_unit=ctr_u,
            ),
            "离线确定性编译：新增圆周均布螺栓孔阵列",
        )

    # 2. Add Single Circle Hole ("在 (50, 30) 新增 Ø12 圆孔" / "在 (30, 40) 开 Ø12 mm 孔")
    m_add_kw = re.search(r"(?:新增|添加|增加|新开|开一个|打一个|加一个|钻一个|(?<!不)开|(?<!不)钻)", cl)
    if m_add_kw and re.search(r"(?:孔|圆)", cl) and not re.search(r"(?:改成|改为|设为|调整为|变成)", cl):
        ctr, ctr_u, ctr_span = _parse_center_from_clause(cl)
        cl_no_ctr = (cl[: ctr_span[0]] + (" " * (ctr_span[1] - ctr_span[0])) + cl[ctr_span[1] :]) if ctr_span else cl
        m_hd = re.search(
            rf"(?:直径|孔径|[⌀∅ØøΦφ])\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?(?:\s*的?\s*(?:圆孔|通孔|定位孔|孔|圆))?",
            cl_no_ctr,
            re.IGNORECASE,
        ) or re.search(
            rf"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?\s*的?\s*(?:圆孔|通孔|定位孔|孔)",
            cl_no_ctr,
            re.IGNORECASE,
        )
        if m_hd:
            spans = [m_add_kw.span(), m_hd.span()] + ([ctr_span] if ctr_span else [])
            _assert_clause_fully_parsed(cl, spans)
            return (
                AddCircleHoleOp(
                    diameter=float(m_hd.group("val")),
                    center=ctr,
                    diameter_unit=m_hd.group("unit"),
                    center_unit=ctr_u,
                ),
                "离线确定性编译：新增定位圆孔",
            )

    # 3. Fillet Corner ("倒圆角 R6" / "圆角改为 R8" / "全部四个角倒圆角 R5")
    m_fil_kw = re.search(r"(?:倒圆角|导圆角|圆角|fillet)", cl, re.IGNORECASE)
    if m_fil_kw:
        m_all = re.search(r"(?:全部|所有|四个角|四角|all)", cl, re.IGNORECASE)
        cl_for_r = cl
        if m_all:
            cl_for_r = cl[: m_all.start()] + (" " * (m_all.end() - m_all.start())) + cl[m_all.end() :]
        m_fil = re.search(
            rf"(?:[Rr]|半径|圆角(?:半径)?\s*(?:改为|改成|设为|为)?)\s*(?P<r>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?",
            cl_for_r,
            re.IGNORECASE,
        ) or re.search(rf"(?P<r>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?", cl_for_r, re.IGNORECASE)
        if m_fil:
            spans = [m_fil_kw.span(), m_fil.span()] + ([m_all.span()] if m_all else [])
            _assert_clause_fully_parsed(cl, spans)
            return (
                FilletCornerOp(
                    radius=float(m_fil.group("r")),
                    handles=cands,
                    all_corners_in_region=bool(m_all),
                    unit=m_fil.group("unit"),
                ),
                "离线确定性编译：拐角相切圆弧过渡（Fillet）",
            )

    # 4. Chamfer Corner ("倒角 C5" / "倒角 6×45°" / "倒斜角 3 mm × 5 mm")
    m_cham_kw = re.search(r"(?:倒角|倒斜角|斜角|chamfer)", cl, re.IGNORECASE)
    if m_cham_kw and not re.search(r"圆角", cl):
        m_all = re.search(r"(?:全部|所有|四个角|四角|all)", cl, re.IGNORECASE)
        m_asym = re.search(
            rf"(?P<d1>\d+(?:\.\d+)?)\s*(?P<u1>{_UNIT_PAT})?\s*[xX×]\s*(?P<d2>\d+(?:\.\d+)?)(?![0-9.])(?!\s*[°度])\s*(?P<u2>{_UNIT_PAT})?",
            cl,
            re.IGNORECASE,
        )
        m_sym = re.search(
            rf"(?:[Cc]\s*)?(?P<d>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?\s*(?:[xX×]\s*45\s*[°度]?)?",
            cl,
            re.IGNORECASE,
        )
        if m_asym:
            spans = [m_cham_kw.span(), m_asym.span()] + ([m_all.span()] if m_all else [])
            _assert_clause_fully_parsed(cl, spans)
            u1_raw = m_asym.group("u1")
            u2_raw = m_asym.group("u2")
            return (
                ChamferCornerOp(
                    distance1=float(m_asym.group("d1")),
                    distance2=float(m_asym.group("d2")),
                    handles=cands,
                    all_corners_in_region=bool(m_all),
                    distance1_unit=u1_raw or u2_raw,
                    distance2_unit=u2_raw,
                ),
                "离线确定性编译：不等距拐角倒角（Chamfer）",
            )
        if m_sym:
            spans = [m_cham_kw.span(), m_sym.span()] + ([m_all.span()] if m_all else [])
            _assert_clause_fully_parsed(cl, spans)
            return (
                ChamferCornerOp(
                    distance1=float(m_sym.group("d")),
                    handles=cands,
                    all_corners_in_region=bool(m_all),
                    distance1_unit=m_sym.group("unit"),
                ),
                "离线确定性编译：45°拐角倒角（Chamfer）",
            )

    # 5. Mirror Entities ("沿垂直轴 x=50 镜像" / "上下对称镜像")
    m_mir_kw = re.search(r"(?:镜像|对称复制|mirror)", cl, re.IGNORECASE)
    if m_mir_kw:
        is_horiz = bool(re.search(r"(?:上下|水平|X\s*轴|y\s*=)", cl, re.IGNORECASE))
        m_coord = re.search(rf"(?:[xXyY]\s*=\s*|轴线?\s*)(?P<c>-?\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?", cl)
        coord_val = float(m_coord.group("c")) if m_coord else 0.0
        m_keep = re.search(r"(?:不保留原图(?:元)?|删除原图(?:元)?|替换原图(?:元)?|保留原图(?:元)?)", cl)
        keep_orig = not bool(re.search(r"(?:不保留原图|删除原图|替换原图)", cl))
        spans = [m_mir_kw.span()] + ([m_coord.span()] if m_coord else []) + ([m_keep.span()] if m_keep else [])
        _assert_clause_fully_parsed(cl, spans)
        return (
            MirrorEntitiesOp(
                handles=cands,
                axis="horizontal" if is_horiz else "vertical",
                axis_coord=coord_val,
                keep_original=keep_orig,
                unit=m_coord.group("unit") if m_coord else None,
            ),
            "离线确定性编译：对称轴几何镜像（Mirror）",
        )

    if not allow_core_ops:
        return None, ""

    # 6. Delete Entities ("删除圆孔" / "去掉虚线" / "删掉中心线")
    m_del = re.search(r"(?:删除|删掉|删去|去掉|移除|清除|\bdelete\b|\bremove\b)", cl, re.IGNORECASE)
    if m_del:
        ef: Literal["all", "circle", "line", "text", "dimension", "hidden", "center"] = "all"
        if re.search(r"虚线|hidden", cl, re.IGNORECASE):
            ef = "hidden"
        elif re.search(r"中心线|轴线|center", cl, re.IGNORECASE):
            ef = "center"
        elif re.search(r"孔|圆|circle|hole", cl, re.IGNORECASE):
            ef = "circle"
        elif re.search(r"直线|线段|line", cl, re.IGNORECASE):
            ef = "line"
        elif re.search(r"标注|尺寸|dim", cl, re.IGNORECASE):
            ef = "dimension"
        elif re.search(r"文字|文本|text", cl, re.IGNORECASE):
            ef = "text"
        _assert_clause_fully_parsed(cl, [m_del.span()])
        return DeleteEntitiesOp(handles=cands, entity_filter=ef), "离线确定性编译：删除目标图元"

    # 7. Move / Translate ("向右平移 20 mm" / "往上移 1 in")
    m_mv = re.search(
        rf"(?:往|向)?\s*(?P<dir>左|右|上|下|left|right|up|down)\s*(?:平移|移动|移|偏移?)?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?",
        cl,
        re.IGNORECASE,
    )
    if m_mv:
        _assert_clause_fully_parsed(cl, [m_mv.span()])
        d_tok = m_mv.group("dir").lower()
        mag = float(m_mv.group("val"))
        ut = m_mv.group("unit")
        dx_v = -mag if d_tok in {"左", "left"} else (mag if d_tok in {"右", "right"} else 0.0)
        dy_v = mag if d_tok in {"上", "up"} else (-mag if d_tok in {"下", "down"} else 0.0)
        return (
            MoveEntitiesOp(handles=cands, dx=dx_v, dy=dy_v, dx_unit=ut, dy_unit=ut),
            "离线确定性编译：平移图元",
        )

    # 8. Resize Linear Span ("长度改为 120 mm" / "高度改成 80")
    m_lin_kw = re.search(r"(?:线长|长度|边长|总长|总宽|总高|底板长|底板宽|宽度|高度|跨度|\blength\b|\bwidth\b|\bheight\b)", cl, re.IGNORECASE)
    m_chg_num = re.search(
        rf"(?:改成|改为|设为|调整为|变成|=|:|：)\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?",
        cl,
        re.IGNORECASE,
    )
    if m_lin_kw and m_chg_num:
        _assert_clause_fully_parsed(cl, [m_lin_kw.span(), m_chg_num.span()])
        ax_m: Literal["auto", "horizontal", "vertical"] = (
            "vertical" if re.search(r"高|竖|height", cl, re.IGNORECASE) else "horizontal" if re.search(r"宽|width", cl, re.IGNORECASE) else "auto"
        )
        return (
            ResizeLinearOp(
                handles=cands,
                new_length=float(m_chg_num.group("val")),
                axis=ax_m,
                new_length_unit=m_chg_num.group("unit"),
            ),
            "离线确定性编译：调整线性跨度/长度",
        )

    # 9. Resize Hole Diameter ("把 Ø12 的孔改为 Ø18 mm" / "孔径改为 20 mm")
    m_cir_kw = re.search(r"(?:孔径|直径|半径|圆孔|螺栓孔|安装孔|通孔|孔|圆|\bdiameter\b|\bhole\b)", cl, re.IGNORECASE)
    if m_cir_kw:
        m_old_new = re.search(
            rf"(?:把|将)?\s*[⌀∅ØøΦφ]?\s*(?P<old>\d+(?:\.\d+)?)\s*(?P<ou>{_UNIT_PAT})?\s*的?\s*(?:圆孔|孔|圆|直径|孔径)?\s*(?:直径|孔径)?\s*(?:改成|改为|设为|调整为|变成)\s*[⌀∅ØøΦφ]?\s*(?P<new>\d+(?:\.\d+)?)\s*(?P<nu>{_UNIT_PAT})?",
            cl,
            re.IGNORECASE,
        )
        if m_old_new:
            _assert_clause_fully_parsed(cl, [m_cir_kw.span(), m_old_new.span()])
            return (
                ResizeHoleOp(
                    handles=cands,
                    match_old_diameter=float(m_old_new.group("old")),
                    old_diameter_unit=m_old_new.group("ou"),
                    new_diameter=float(m_old_new.group("new")),
                    new_diameter_unit=m_old_new.group("nu"),
                ),
                "离线确定性编译：按旧孔径匹配并修改孔径",
            )
        m_simple_d = re.search(
            rf"(?:改成|改为|设为|调整为|变成)\s*(?P<rad>[Rr]|半径)?\s*[⌀∅ØøΦφ]?\s*(?P<new>\d+(?:\.\d+)?)\s*(?P<nu>{_UNIT_PAT})?",
            cl,
            re.IGNORECASE,
        )
        if m_simple_d:
            _assert_clause_fully_parsed(cl, [m_cir_kw.span(), m_simple_d.span()])
            val_f = float(m_simple_d.group("new"))
            if m_simple_d.group("rad"):
                val_f *= 2.0
            return (
                ResizeHoleOp(handles=cands, new_diameter=val_f, new_diameter_unit=m_simple_d.group("nu")),
                "离线确定性编译：修改圆孔直径并同步标注",
            )

    # 10. Update Text ("材料改为 40Cr" / "把 Q235 改成 304不锈钢")
    m_txt_pair = re.search(r"把\s*(?P<old>[^，,；;]+?)\s*(?:改成|改为|替换成|换成)\s*(?P<new>.+)$", cl)
    if m_txt_pair and not re.search(r"孔|圆|长|宽|高|倒角|圆角", m_txt_pair.group("old")):
        return (
            UpdateTextOp(handles=cands, old_text=m_txt_pair.group("old").strip(), new_text=m_txt_pair.group("new").strip()),
            "离线确定性编译：替换图面文字",
        )
    m_txt_set = re.search(r"(?:材料|材质|文字|标题|图名)\s*(?:改成|改为|设为|换成)\s*[:：]?\s*(?P<new>.+)$", cl)
    if m_txt_set:
        return (
            UpdateTextOp(handles=cands, new_text=m_txt_set.group("new").strip()),
            "离线确定性编译：更新文字内容",
        )

    return None, ""
