"""Reopen-DXF Post-Condition Verifier for Unified CAD Revisions.

Every revision operation emits explicit, typed `PostConditionSpec` assertions.
After the deterministic CAD kernel writes `revised.dxf` to disk, this verifier
reopens BOTH `baseline.dxf` and `revised.dxf` via `ezdxf.readfile()`, converts
all geometry into physical millimetres (`mm`) via `$INSUNITS`, and checks each
assertion item-by-item.

This catches:
- Partial execution ("two-hole prompt only created the first hole");
- 10x or 25.4x unit-conversion errors ("Ø2 mm drawn as Ø20 mm or Ø2 in");
- Silent no-op mutations where handles were reported modified without reaching target geometry.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import atan, cos, hypot, isclose, radians, sin
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import ezdxf
from ezdxf import bbox
from pydantic import BaseModel, ConfigDict, Field

from .common import RevisionError

_INSUNITS_TO_MM: dict[int, float] = {
    1: 25.4,    # inches
    2: 304.8,   # feet
    4: 1.0,     # mm
    5: 10.0,    # cm
    6: 1000.0,  # m
}

_UNIT_TOKEN_TO_MM: dict[str, float] = {
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


def _doc_scale_mm(doc_units: int) -> float:
    return _INSUNITS_TO_MM.get(int(doc_units), 1.0)


def _expected_to_mm(val: float, raw_unit: str | None, doc_units: int) -> float:
    if raw_unit and raw_unit.strip():
        u = raw_unit.strip().lower()
        if u not in _UNIT_TOKEN_TO_MM:
            raise RevisionError(f"unsupported post-condition unit: {raw_unit!r}")
        if int(doc_units) == 0:
            raise RevisionError("drawing has $INSUNITS=0; cannot verify explicit physical unit")
        return float(val) * _UNIT_TOKEN_TO_MM[u]
    return float(val) * _doc_scale_mm(doc_units)


class _StrictCheckBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CircleExistsAtCheck(_StrictCheckBase):
    check: Literal["circle_exists_at"] = "circle_exists_at"
    center: tuple[float, float]
    diameter: float = Field(gt=0.0)
    center_unit: str | None = None
    diameter_unit: str | None = None
    layer: str | None = None
    tolerance_mm: float = 0.05
    label: str = ""


class BoltCircleExistsCheck(_StrictCheckBase):
    check: Literal["bolt_circle_exists"] = "bolt_circle_exists"
    center: tuple[float, float]
    count: int = Field(ge=2, le=64)
    hole_diameter: float = Field(gt=0.0)
    bolt_circle_diameter: float = Field(gt=0.0)
    start_angle_deg: float = 0.0
    hole_unit: str | None = None
    pcd_unit: str | None = None
    center_unit: str | None = None
    tolerance_mm: float = 0.08
    label: str = ""


class HandlesAbsentCheck(_StrictCheckBase):
    check: Literal["handles_absent"] = "handles_absent"
    handles: list[str] = Field(min_length=1)
    label: str = ""


class HandlesTranslatedCheck(_StrictCheckBase):
    check: Literal["handles_translated"] = "handles_translated"
    handles: list[str] = Field(min_length=1)
    dx: float = 0.0
    dy: float = 0.0
    dx_unit: str | None = None
    dy_unit: str | None = None
    tolerance_mm: float = 0.08
    label: str = ""


class CircleDiameterEqualsCheck(_StrictCheckBase):
    check: Literal["circle_diameter_equals"] = "circle_diameter_equals"
    handles: list[str] = Field(min_length=1)
    diameter: float = Field(gt=0.0)
    diameter_unit: str | None = None
    tolerance_mm: float = 0.05
    label: str = ""


class LinearSpanEqualsCheck(_StrictCheckBase):
    check: Literal["linear_span_equals"] = "linear_span_equals"
    handles: list[str] = Field(min_length=1)
    length: float = Field(gt=0.0)
    axis: Literal["auto", "horizontal", "vertical"] = "auto"
    length_unit: str | None = None
    tolerance_mm: float = 0.08
    label: str = ""


class CornerFilletExistsCheck(_StrictCheckBase):
    check: Literal["corner_fillet_exists"] = "corner_fillet_exists"
    radius: float = Field(gt=0.0)
    unit: str | None = None
    min_count: int = Field(default=1, ge=1)
    tolerance_mm: float = 0.10
    label: str = ""


class CornerChamferExistsCheck(_StrictCheckBase):
    check: Literal["corner_chamfer_exists"] = "corner_chamfer_exists"
    distance1: float = Field(gt=0.0)
    distance2: float | None = Field(default=None, gt=0.0)
    distance1_unit: str | None = None
    distance2_unit: str | None = None
    min_count: int = Field(default=1, ge=1)
    tolerance_mm: float = 0.12
    label: str = ""


class MirroredHandlesExistCheck(_StrictCheckBase):
    check: Literal["mirrored_handles_exist"] = "mirrored_handles_exist"
    added_handles: list[str] = Field(min_length=1)
    min_count: int = Field(default=1, ge=1)
    label: str = ""


class TextContainsCheck(_StrictCheckBase):
    check: Literal["text_contains"] = "text_contains"
    handles: list[str] = Field(min_length=1)
    expected_substring: str
    label: str = ""


PostConditionSpec = Annotated[
    Union[
        CircleExistsAtCheck,
        BoltCircleExistsCheck,
        HandlesAbsentCheck,
        HandlesTranslatedCheck,
        CircleDiameterEqualsCheck,
        LinearSpanEqualsCheck,
        CornerFilletExistsCheck,
        CornerChamferExistsCheck,
        MirroredHandlesExistCheck,
        TextContainsCheck,
    ],
    Field(discriminator="check"),
]


def _entity_anchor_xy(ent: Any) -> tuple[float, float] | None:
    dt = ent.dxftype()
    if dt in {"CIRCLE", "ARC"}:
        c = ent.dxf.center
        return float(c.x), float(c.y)
    if dt == "LINE":
        s, e = ent.dxf.start, ent.dxf.end
        return 0.5 * (float(s.x) + float(e.x)), 0.5 * (float(s.y) + float(e.y))
    ext = bbox.extents([ent])
    if ext.has_data:
        return 0.5 * float(ext.extmin.x + ext.extmax.x), 0.5 * float(ext.extmin.y + ext.extmax.y)
    return None


def _polyline_fillet_radii_doc(ent: Any) -> list[float]:
    radii: list[float] = []
    pts = list(ent.get_points("xyseb"))
    n = len(pts)
    if n < 2:
        return radii
    seg_cnt = n if ent.closed else n - 1
    for idx in range(seg_cnt):
        x0, y0, _, _, bulge = pts[idx]
        x1, y1, _, _, _ = pts[(idx + 1) % n]
        if abs(float(bulge)) > 1e-6:
            chord = hypot(float(x1) - float(x0), float(y1) - float(y0))
            theta = 4.0 * atan(abs(float(bulge)))
            s_half = sin(theta / 2.0)
            if chord > 1e-7 and s_half > 1e-7:
                radii.append(chord / (2.0 * s_half))
    return radii


def _polyline_segment_lengths_doc(ent: Any) -> list[float]:
    pts = [(float(x), float(y)) for x, y in ent.get_points("xy")]
    n = len(pts)
    if n < 2:
        return []
    seg_cnt = n if ent.closed else n - 1
    return [hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in range(seg_cnt)]


def _extract_entity_text(ent: Any) -> str:
    dt = ent.dxftype()
    if dt == "TEXT":
        return str(ent.dxf.get("text", ""))
    if dt == "MTEXT":
        return ent.plain_text()
    if dt == "DIMENSION":
        raw = str(ent.dxf.get("text", ""))
        if raw and raw != "<>":
            return raw
        if ent.dxf.is_supported("actual_measurement") and ent.dxf.hasattr("actual_measurement"):
            return f"{float(ent.dxf.actual_measurement):g}"
        return raw
    return ""


def verify_revision_post_conditions(
    baseline_dxf: Path | str,
    revised_dxf: Path | str,
    checks: Sequence[PostConditionSpec | dict[str, Any]],
) -> dict[str, Any]:
    """Reopen `baseline_dxf` & `revised_dxf` from disk and verify every post-condition item."""
    base_doc = ezdxf.readfile(Path(baseline_dxf))
    rev_doc = ezdxf.readfile(Path(revised_dxf))
    doc_units = int(getattr(rev_doc, "units", 0) or 0)
    scale_mm = _doc_scale_mm(doc_units)

    base_by_h = {str(e.dxf.handle).upper(): e for e in base_doc.modelspace() if e.is_alive}
    rev_ents = [e for e in rev_doc.modelspace() if e.is_alive]
    rev_by_h = {str(e.dxf.handle).upper(): e for e in rev_ents}

    from pydantic import TypeAdapter

    adapter: TypeAdapter[PostConditionSpec] = TypeAdapter(PostConditionSpec)
    parsed_checks = [
        c if isinstance(c, _StrictCheckBase) else adapter.validate_python(c)
        for c in checks
    ]

    results: list[dict[str, Any]] = []
    all_passed = True

    for idx, item in enumerate(parsed_checks, start=1):
        passed = False
        detail = ""

        if isinstance(item, CircleExistsAtCheck):
            exp_cx = _expected_to_mm(item.center[0], item.center_unit, doc_units)
            exp_cy = _expected_to_mm(item.center[1], item.center_unit, doc_units)
            exp_d = _expected_to_mm(item.diameter, item.diameter_unit, doc_units)
            matched_h = None
            best_err = float("inf")
            for ent in rev_ents:
                if ent.dxftype() != "CIRCLE":
                    continue
                if item.layer and str(ent.dxf.get("layer", "")).upper() != item.layer.upper():
                    continue
                if str(ent.dxf.get("layer", "")).upper() == "CENTER":
                    continue
                cx_mm = float(ent.dxf.center.x) * scale_mm
                cy_mm = float(ent.dxf.center.y) * scale_mm
                d_mm = 2.0 * float(ent.dxf.radius) * scale_mm
                pos_err = hypot(cx_mm - exp_cx, cy_mm - exp_cy)
                dia_err = abs(d_mm - exp_d)
                best_err = min(best_err, max(pos_err, dia_err))
                if pos_err <= item.tolerance_mm and dia_err <= item.tolerance_mm:
                    matched_h = str(ent.dxf.handle).upper()
                    passed = True
                    detail = f"已核实圆孔 #{matched_h}: 圆心=({cx_mm:.3f}, {cy_mm:.3f}), 实测直径={d_mm:.3f} (期望 {exp_d:.3f})"
                    break
            if not passed:
                detail = f"未找到期望圆孔: 期望圆心=({exp_cx:.3f}, {exp_cy:.3f}), 期望直径={exp_d:.3f} (最小残差={best_err:.3f})"

        elif isinstance(item, BoltCircleExistsCheck):
            exp_cx = _expected_to_mm(item.center[0], item.center_unit, doc_units)
            exp_cy = _expected_to_mm(item.center[1], item.center_unit, doc_units)
            exp_hd = _expected_to_mm(item.hole_diameter, item.hole_unit, doc_units)
            exp_pcd = _expected_to_mm(item.bolt_circle_diameter, item.pcd_unit, doc_units)
            r_pcd = exp_pcd / 2.0
            circles_mm = [
                (
                    str(e.dxf.handle).upper(),
                    float(e.dxf.center.x) * scale_mm,
                    float(e.dxf.center.y) * scale_mm,
                    2.0 * float(e.dxf.radius) * scale_mm,
                )
                for e in rev_ents
                if e.dxftype() == "CIRCLE" and str(e.dxf.get("layer", "")).upper() != "CENTER"
            ]
            found_cnt = 0
            for k in range(item.count):
                ang = radians(item.start_angle_deg + 360.0 * k / item.count)
                tx = exp_cx + r_pcd * cos(ang)
                ty = exp_cy + r_pcd * sin(ang)
                if any(
                    hypot(cx - tx, cy - ty) <= item.tolerance_mm and abs(dm - exp_hd) <= item.tolerance_mm
                    for _, cx, cy, dm in circles_mm
                ):
                    found_cnt += 1
            passed = found_cnt == item.count
            detail = (
                f"已核实均布螺栓孔 {found_cnt}/{item.count} 处 (孔径={exp_hd:.3f}, 节圆={exp_pcd:.3f})"
            )

        elif isinstance(item, HandlesAbsentCheck):
            still_present = [h.upper() for h in item.handles if h.upper() in rev_by_h]
            passed = len(still_present) == 0
            detail = "目标句柄均已从 DXF 移除" if passed else f"以下句柄仍残留于 DXF: {still_present}"

        elif isinstance(item, HandlesTranslatedCheck):
            exp_dx = _expected_to_mm(item.dx, item.dx_unit, doc_units)
            exp_dy = _expected_to_mm(item.dy, item.dy_unit, doc_units)
            bad: list[str] = []
            for h in item.handles:
                hu = h.upper()
                b_ent = base_by_h.get(hu)
                a_ent = rev_by_h.get(hu)
                if b_ent is None or a_ent is None:
                    bad.append(f"#{hu}(missing)")
                    continue
                bp = _entity_anchor_xy(b_ent)
                ap = _entity_anchor_xy(a_ent)
                if bp is None or ap is None:
                    continue
                act_dx = (ap[0] - bp[0]) * scale_mm
                act_dy = (ap[1] - bp[1]) * scale_mm
                if hypot(act_dx - exp_dx, act_dy - exp_dy) > item.tolerance_mm:
                    bad.append(f"#{hu}(actual=({act_dx:.3f},{act_dy:.3f}))")
            passed = len(bad) == 0
            detail = f"已核实 {len(item.handles)} 个图元平移 ({exp_dx:+.3f}, {exp_dy:+.3f})" if passed else f"平移偏差超差: {bad}"

        elif isinstance(item, CircleDiameterEqualsCheck):
            exp_d = _expected_to_mm(item.diameter, item.diameter_unit, doc_units)
            mismatches: list[str] = []
            for h in item.handles:
                hu = h.upper()
                ent = rev_by_h.get(hu)
                if ent is None or ent.dxftype() not in {"CIRCLE", "ARC"}:
                    mismatches.append(f"#{hu}(not_found)")
                    continue
                act_d = 2.0 * float(ent.dxf.radius) * scale_mm
                if abs(act_d - exp_d) > item.tolerance_mm:
                    mismatches.append(f"#{hu}(实测={act_d:.3f} vs 期望={exp_d:.3f})")
            passed = len(mismatches) == 0
            detail = f"已核实 {len(item.handles)} 处圆孔实测直径={exp_d:.3f}" if passed else f"孔径核验不符: {mismatches}"

        elif isinstance(item, LinearSpanEqualsCheck):
            exp_len = _expected_to_mm(item.length, item.length_unit, doc_units)
            mismatches_l: list[str] = []
            for h in item.handles:
                hu = h.upper()
                ent = rev_by_h.get(hu)
                if ent is None:
                    mismatches_l.append(f"#{hu}(not_found)")
                    continue
                dt = ent.dxftype()
                if dt == "LINE":
                    s, e = ent.dxf.start, ent.dxf.end
                    act_l = hypot(float(e.x) - float(s.x), float(e.y) - float(s.y)) * scale_mm
                    ok_l = abs(act_l - exp_len) <= item.tolerance_mm
                else:
                    ext = bbox.extents([ent])
                    if not ext.has_data:
                        mismatches_l.append(f"#{hu}(empty_bbox)")
                        continue
                    w_mm = float(ext.extmax.x - ext.extmin.x) * scale_mm
                    h_mm = float(ext.extmax.y - ext.extmin.y) * scale_mm
                    if item.axis == "horizontal":
                        act_l = w_mm
                        ok_l = abs(w_mm - exp_len) <= item.tolerance_mm
                    elif item.axis == "vertical":
                        act_l = h_mm
                        ok_l = abs(h_mm - exp_len) <= item.tolerance_mm
                    else:
                        act_l = w_mm if abs(w_mm - exp_len) <= abs(h_mm - exp_len) else h_mm
                        ok_l = abs(act_l - exp_len) <= item.tolerance_mm
                if not ok_l:
                    mismatches_l.append(f"#{hu}(实测={act_l:.3f} vs 期望={exp_len:.3f})")
            passed = len(mismatches_l) == 0
            detail = f"已核实线性跨度={exp_len:.3f}" if passed else f"线性跨度核验不符: {mismatches_l}"

        elif isinstance(item, CornerFilletExistsCheck):
            exp_r = _expected_to_mm(item.radius, item.unit, doc_units)
            hit_r = 0
            for ent in rev_ents:
                if ent.dxftype() == "ARC":
                    if abs(float(ent.dxf.radius) * scale_mm - exp_r) <= item.tolerance_mm:
                        hit_r += 1
                elif ent.dxftype() == "LWPOLYLINE":
                    for r_doc in _polyline_fillet_radii_doc(ent):
                        if abs(r_doc * scale_mm - exp_r) <= item.tolerance_mm:
                            hit_r += 1
            passed = hit_r >= item.min_count
            detail = f"已核实 R={exp_r:.3f} 圆角特征 {hit_r} 处 (至少需 {item.min_count} 处)"

        elif isinstance(item, CornerChamferExistsCheck):
            d1_mm = _expected_to_mm(item.distance1, item.distance1_unit, doc_units)
            d2_mm = (
                _expected_to_mm(item.distance2, item.distance2_unit, doc_units)
                if item.distance2 is not None
                else d1_mm
            )
            exp_chord = hypot(d1_mm, d2_mm)
            hit_c = 0
            for ent in rev_ents:
                if ent.dxftype() == "LINE":
                    s, e = ent.dxf.start, ent.dxf.end
                    seg_mm = hypot(float(e.x) - float(s.x), float(e.y) - float(s.y)) * scale_mm
                    if abs(seg_mm - exp_chord) <= item.tolerance_mm:
                        hit_c += 1
                elif ent.dxftype() == "LWPOLYLINE":
                    for seg_doc in _polyline_segment_lengths_doc(ent):
                        if abs(seg_doc * scale_mm - exp_chord) <= item.tolerance_mm:
                            hit_c += 1
            passed = hit_c >= item.min_count
            detail = f"已核实倒角斜边长={exp_chord:.3f} 特征 {hit_c} 处 (至少需 {item.min_count} 处)"

        elif isinstance(item, MirroredHandlesExistCheck):
            present_cnt = sum(1 for h in item.added_handles if h.upper() in rev_by_h)
            passed = present_cnt >= item.min_count and present_cnt == len(item.added_handles)
            detail = f"已核实镜像生成图元 {present_cnt}/{len(item.added_handles)} 个"

        elif isinstance(item, TextContainsCheck):
            bad_t: list[str] = []
            for h in item.handles:
                hu = h.upper()
                ent = rev_by_h.get(hu)
                if ent is None:
                    bad_t.append(f"#{hu}(not_found)")
                    continue
                actual_txt = _extract_entity_text(ent)
                if item.expected_substring not in actual_txt and not isclose(
                    _try_float(actual_txt), _try_float(item.expected_substring), abs_tol=1e-3
                ):
                    bad_t.append(f"#{hu}(actual={actual_txt!r})")
            passed = len(bad_t) == 0
            detail = f"已核实文字包含「{item.expected_substring}」" if passed else f"文字核验不符: {bad_t}"

        if not passed:
            all_passed = False
        results.append(
            {
                "index": idx,
                "check": item.check,
                "label": item.label or item.check,
                "passed": passed,
                "detail": detail,
            }
        )

    return {
        "passed": all_passed,
        "checked_count": len(results),
        "failed_count": sum(1 for r in results if not r["passed"]),
        "items": results,
    }


def _try_float(s: str) -> float:
    try:
        return float(s.strip())
    except Exception:
        return float("nan")
