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
from math import acos, atan2, cos, degrees, hypot, isfinite, pi, radians, sin, tan
import os
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Union
import urllib.request

import ezdxf
from ezdxf import bbox
from pydantic import BaseModel, ConfigDict, Field

from .common import RevisionError, compare_untouched, sha256_file, snapshot_modelspace
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


RevisionIROperation = Annotated[
    Union[
        AddCircleHoleOp,
        AddBoltCircleOp,
        ChamferCornerOp,
        FilletCornerOp,
        MirrorEntitiesOp,
    ],
    Field(discriminator="op"),
]


class StructuredRevisionIR(_StrictIRBase):
    schema_version: Literal["cad-revision-ir/v2"] = "cad-revision-ir/v2"
    baseline_sha256: str | None = None
    operations: list[RevisionIROperation] = Field(min_length=1)
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

    cand_upper = [str(h).upper() for h in (candidate_handles or []) if h]

    try:
        for step in parsed_ir.operations:
            by_handle = {str(e.dxf.handle).upper(): e for e in msp if e.is_alive}

            if isinstance(step, AddCircleHoleOp):
                dia = scale_value_to_doc_units(step.diameter, step.unit, doc_units)
                ox = scale_value_to_doc_units(step.offset[0], step.unit, doc_units)
                oy = scale_value_to_doc_units(step.offset[1], step.unit, doc_units)
                if step.center is not None:
                    cx = scale_value_to_doc_units(step.center[0], step.unit, doc_units) + ox
                    cy = scale_value_to_doc_units(step.center[1], step.unit, doc_units) + oy
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
                summaries.append(f"已在 ({cx:.1f}, {cy:.1f}) 新增 ⌀{dia:g} 圆孔")

            elif isinstance(step, AddBoltCircleOp):
                hole_d = scale_value_to_doc_units(step.hole_diameter, step.unit, doc_units)
                pcd = scale_value_to_doc_units(step.bolt_circle_diameter, step.unit, doc_units)
                if pcd <= hole_d * 1.2:
                    raise RevisionError(f"节圆直径 ({pcd:g}) 必须大于单孔直径 ({hole_d:g})。")
                if step.center is not None:
                    cx = scale_value_to_doc_units(step.center[0], step.unit, doc_units)
                    cy = scale_value_to_doc_units(step.center[1], step.unit, doc_units)
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
                summaries.append(f"已围绕 ({cx:.1f}, {cy:.1f}) 新增 {step.count}×⌀{hole_d:g} 均布螺栓孔（节圆 ⌀{pcd:g}）")

            elif isinstance(step, (ChamferCornerOp, FilletCornerOp)):
                is_chamfer = isinstance(step, ChamferCornerOp)
                if is_chamfer:
                    d1 = scale_value_to_doc_units(step.distance1, step.unit, doc_units)
                    d2 = scale_value_to_doc_units(step.distance2 or step.distance1, step.unit, doc_units)
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

                mirrored_cnt = 0
                for ent in src_ents:
                    cloned = _mirror_single_entity(msp, ent, p0, ux, uy)
                    if cloned is not None:
                        added_handles.append(str(cloned.dxf.handle).upper())
                        mirrored_cnt += 1
                        if not step.keep_original:
                            deleted_handles.append(str(ent.dxf.handle).upper())
                            msp.delete_entity(ent)
                if mirrored_cnt == 0:
                    raise RevisionError("选中图元类型暂不支持几何镜像。")
                summaries.append(f"已沿{'水平' if step.axis == 'horizontal' else '垂直' if step.axis == 'vertical' else '指定'}对称轴镜像 {mirrored_cnt} 个图元")

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

        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_dxf = out_dir / "revised.dxf"
        if not dry_run:
            tmp_out = out_dxf.with_name(f".{out_dxf.name}.tmp")
            doc.saveas(tmp_out)
            tmp_out.replace(out_dxf)
            new_sha = sha256_file(out_dxf)
        else:
            checkpoint.restore()
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


def compile_nl_to_feature_ir(
    prompt_text: str,
    *,
    candidate_handles: Sequence[str] | None = None,
    region_box: Sequence[float] | None = None,
    llm_callable: Callable[[str, dict[str, Any]], dict[str, Any] | str] | None = None,
) -> StructuredRevisionIR | None:
    """Compile feature-creation & corner/mirror NL instructions into `StructuredRevisionIR`.

    1. If `llm_callable` or `CAD_SKILL_LLM_API_KEY` is configured, attempts structured JSON IR
       generation first and validates strictly against `StructuredRevisionIR`.
    2. Otherwise (or if offline), compiles deterministically via multi-clause geometric intent rules.
    Returns `None` if the prompt does not request a v2 feature primitive (allowing `nl_revise` to
    dispatch delete/move/resize/text as usual).
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
                "Output ONLY valid JSON matching schema_version='cad-revision-ir/v2' with an 'operations' list. "
                "Allowed ops: add_circle_hole, add_bolt_circle, chamfer_corner, fillet_corner, mirror_entities. "
                "If the prompt is not one of these 5 operations, output {{\"operations\": []}}."
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

    # Layer B: Deterministic Offline Feature-Primitive Compiler
    ops: list[RevisionIROperation] = []

    # 1. Bolt Circle Pattern ("新增 6×Ø10 均布螺栓孔，节圆 80" / "加一圈 4xM8 孔 PCD 100")
    m_bcd = re.search(
        rf"(?:新增|添加|增加|加(?:一圈)?|打)\s*(?P<cnt>\d+)\s*(?:[xX×\-]|个)\s*(?:[⌀∅ØøΦφMm])?\s*(?P<hd>\d+(?:\.\d+)?)\s*(?P<u1>{_UNIT_PAT})?.*?(?:节圆(?:直径)?|分布圆(?:直径)?|PCD|pcd|圆周)\s*(?:直径|为|:|：)?\s*(?:[⌀∅ØøΦφ])?\s*(?P<pcd>\d+(?:\.\d+)?)\s*(?P<u2>{_UNIT_PAT})?",
        clean,
        re.IGNORECASE,
    )
    if m_bcd:
        m_ctr = re.search(r"[\(（]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[\)）]", clean)
        ctr = (float(m_ctr.group(1)), float(m_ctr.group(2))) if m_ctr else None
        ops.append(
            AddBoltCircleOp(
                count=int(m_bcd.group("cnt")),
                hole_diameter=float(m_bcd.group("hd")),
                bolt_circle_diameter=float(m_bcd.group("pcd")),
                center=ctr,
                unit=m_bcd.group("u2") or m_bcd.group("u1"),
            )
        )
        return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：新增圆周均布螺栓孔阵列")

    # 2. Add Single Circle Hole ("在 (50, 30) 新增 Ø12 圆孔" / "添加一个直径 16 mm 的孔")
    if re.search(r"(?:新增|添加|增加|新开|打一个|加一个).*?(?:孔|圆)", clean):
        m_hd = re.search(
            rf"(?:直径|孔径|[⌀∅ØøΦφ])\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?",
            clean,
            re.IGNORECASE,
        ) or re.search(rf"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?\s*(?:的)?(?:圆孔|通孔|孔)", clean, re.IGNORECASE)
        if m_hd:
            m_ctr = re.search(r"[\(（]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[\)）]", clean)
            ctr = (float(m_ctr.group(1)), float(m_ctr.group(2))) if m_ctr else None
            ops.append(
                AddCircleHoleOp(
                    diameter=float(m_hd.group("val")),
                    center=ctr,
                    unit=m_hd.group("unit"),
                )
            )
            return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：新增定位圆孔")

    # 3. Fillet Corner ("倒圆角 R6" / "圆角改为 R8" / "导圆 R5")
    if re.search(r"(?:倒圆角|导圆角|圆角|fillet)", clean, re.IGNORECASE):
        m_fil = re.search(
            rf"(?:[Rr]|半径|圆角(?:半径)?\s*(?:改为|设为|为)?)\s*(?P<r>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?",
            clean,
            re.IGNORECASE,
        ) or re.search(rf"(?P<r>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?", clean, re.IGNORECASE)
        if m_fil:
            all_flag = bool(re.search(r"(?:全部|所有|四个角|四角|all)", clean, re.IGNORECASE))
            ops.append(
                FilletCornerOp(
                    radius=float(m_fil.group("r")),
                    handles=cands,
                    all_corners_in_region=all_flag,
                    unit=m_fil.group("unit"),
                )
            )
            return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：拐角相切圆弧过渡（Fillet）")

    # 4. Chamfer Corner ("倒角 C5" / "倒角 4×45°" / "倒斜角 3x5")
    if re.search(r"(?:倒角|倒斜角|斜角|chamfer)", clean, re.IGNORECASE) and not re.search(r"圆角", clean):
        m_asym = re.search(
            rf"(?P<d1>\d+(?:\.\d+)?)\s*[xX×]\s*(?P<d2>\d+(?:\.\d+)?)(?![0-9.])(?!\s*[°度])\s*(?P<unit>{_UNIT_PAT})?",
            clean,
            re.IGNORECASE,
        )
        m_sym = re.search(
            rf"(?:[Cc]\s*)?(?P<d>\d+(?:\.\d+)?)\s*(?:[xX×]\s*45\s*[°度]?)?\s*(?P<unit>{_UNIT_PAT})?",
            clean,
            re.IGNORECASE,
        )
        all_flag = bool(re.search(r"(?:全部|所有|四个角|四角|all)", clean, re.IGNORECASE))
        if m_asym:
            ops.append(
                ChamferCornerOp(
                    distance1=float(m_asym.group("d1")),
                    distance2=float(m_asym.group("d2")),
                    handles=cands,
                    all_corners_in_region=all_flag,
                    unit=m_asym.group("unit"),
                )
            )
            return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：不等距拐角倒角（Chamfer）")
        if m_sym:
            ops.append(
                ChamferCornerOp(
                    distance1=float(m_sym.group("d")),
                    handles=cands,
                    all_corners_in_region=all_flag,
                    unit=m_sym.group("unit"),
                )
            )
            return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：45°拐角倒角（Chamfer）")

    # 5. Mirror Entities ("沿 Y 轴镜像" / "上下对称镜像" / "向右镜像，x=60")
    if re.search(r"(?:镜像|对称复制|mirror)", clean, re.IGNORECASE):
        is_horiz = bool(re.search(r"(?:上下|水平|X\s*轴|y\s*=)", clean, re.IGNORECASE))
        m_coord = re.search(rf"(?:[xXyY]\s*=\s*|轴线?\s*)(?P<c>-?\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})?", clean)
        coord_val = float(m_coord.group("c")) if m_coord else 0.0
        keep_orig = not bool(re.search(r"(?:不保留原图|删除原图|替换原图)", clean))
        ops.append(
            MirrorEntitiesOp(
                handles=cands,
                axis="horizontal" if is_horiz else "vertical",
                axis_coord=coord_val,
                keep_original=keep_orig,
                unit=m_coord.group("unit") if m_coord else None,
            )
        )
        return StructuredRevisionIR(operations=ops, rationale_zh="离线确定性编译：对称轴几何镜像（Mirror）")

    return None
