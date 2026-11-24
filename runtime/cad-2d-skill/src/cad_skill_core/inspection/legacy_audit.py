"""Standalone health, tampering, topology, and collision auditor & cleaner for arbitrary legacy DXF drawings.

Works on ANY external/wild DXF without requiring internal DrawingSpec or LayoutPlan files:
1. Detects tampered or manually overridden dimensions (`TAMPERED_DIMENSION_TEXT`);
2. Detects exploded fake dimensions (`EXPLODED_DIMENSION_FRAGMENT`);
3. Detects open contour micro-gaps & unclosed loops blocking CNC/laser CAM (`OPEN_CONTOUR_GAP`),
   strictly scaled to the DXF's `$INSUNITS` physical units;
4. Detects 3D-true zero-length degenerate entities (`ZERO_LENGTH_DEGENERATE`) while preserving
   vertical Z edges and extruded thickness entities;
5. Detects overlapping duplicate entities (`DUPLICATE_OVERLAPPING_ENTITY`) while preserving
   geometry at distinct Z elevations, thicknesses, and extrusion normals;
6. Detects text-contour collisions (`TEXT_GEOMETRY_COLLISION`);
7. Diagnoses missing `$INSUNITS`, Layer `0` dumping, and nested `INSERT` blocks (`LAYER_AND_UNIT_HYGIENE`);
8. Provides deterministic atomic cleanup (`clean_legacy_dxf`) with before/after verification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import cos, hypot, isfinite, radians, sin
from pathlib import Path
import re
from typing import Any

import ezdxf
from ezdxf import bbox

from cad_skill_core.revision.common import sha256_file

_NON_CONTOUR_LAYER_RE = re.compile(
    r"(?:^0_DEFPOINTS$|^DEFPOINTS$|DIM|TEXT|NOTE|ANNO|CENTER|AXIS|HATCH|FRAME|BORDER|TITLE|GRID|VIEWPORT)",
    re.IGNORECASE,
)
_DIM_LAYER_RE = re.compile(r"(?:DIM|ANNO|标注|尺寸)", re.IGNORECASE)
_MTEXT_CTRL_RE = re.compile(r"\\[A-Za-z][^;]*;|[{}]")
_LEADING_COUNT_RE = re.compile(r"^\s*\d+\s*[xX×\-]\s*")
_TOL_SUFFIX_RE = re.compile(r"(?:±|\+-|\+|-|\().*$")
_NOMINAL_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)")

# Physical millimetres per 1.0 drawing unit for standard DXF $INSUNITS codes
_DOC_UNIT_TO_MM: dict[int, float] = {
    1: 25.4,         # Inches
    2: 304.8,        # Feet
    3: 1_609_344.0,  # Miles
    4: 1.0,          # Millimeters
    5: 10.0,         # Centimeters
    6: 1000.0,       # Meters
    7: 1_000_000.0,  # Kilometers
    8: 2.54e-5,      # Microinches
    9: 0.0254,       # Mils
    10: 914.4,       # Yards
    13: 1e-3,        # Microns
    14: 100.0,       # Decimeters
}


def _mm_to_doc_units(mm_val: float, insunits: int) -> float:
    return float(mm_val) / _DOC_UNIT_TO_MM.get(int(insunits), 1.0)


def _doc_units_to_mm(doc_val: float, insunits: int) -> float:
    return float(doc_val) * _DOC_UNIT_TO_MM.get(int(insunits), 1.0)


def _quantize(val: float, quantum: float) -> int:
    return round(float(val) / max(quantum, 1e-12))


def _entity_thickness(ent: Any) -> float:
    if ent.dxf.is_supported("thickness") and ent.dxf.hasattr("thickness"):
        return float(ent.dxf.thickness)
    return 0.0


def _extrusion_key(ent: Any) -> tuple[float, float, float]:
    if ent.dxf.is_supported("extrusion") and ent.dxf.hasattr("extrusion"):
        ex = ent.dxf.extrusion
        return (round(float(ex.x), 4), round(float(ex.y), 4), round(float(ex.z), 4))
    return (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class LegacyAuditIssue:
    issue_id: str
    code: str
    severity: str  # "error" | "warning" | "info"
    category: str  # "tampering" | "topology" | "degeneracy" | "duplication" | "collision" | "hygiene"
    message_zh: str
    handles: tuple[str, ...]
    location_box: tuple[float, float, float, float] | None = None
    suggestion_zh: str = ""
    measured_value: float | None = None
    displayed_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["handles"] = list(self.handles)
        payload["location_box"] = list(self.location_box) if self.location_box is not None else None
        return payload


@dataclass(frozen=True)
class LegacyAuditReport:
    dxf_path: str
    sha256: str
    insunits: int
    entity_count: int
    health_score: float
    grade: str
    status: str  # "healthy" | "warning" | "critical"
    summary: dict[str, int]
    issues: tuple[LegacyAuditIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dxf_path": self.dxf_path,
            "sha256": self.sha256,
            "insunits": self.insunits,
            "entity_count": self.entity_count,
            "health_score": self.health_score,
            "grade": self.grade,
            "status": self.status,
            "summary": dict(self.summary),
            "issues": [item.to_dict() for item in self.issues],
        }


def _pad_box(x0: float, y0: float, x1: float, y1: float, pad: float = 3.0) -> tuple[float, float, float, float]:
    return (
        round(min(x0, x1) - pad, 5),
        round(min(y0, y1) - pad, 5),
        round(max(x0, x1) + pad, 5),
        round(max(y0, y1) + pad, 5),
    )


def _entity_box(ent: Any, pad: float = 2.5) -> tuple[float, float, float, float] | None:
    try:
        ext = bbox.extents([ent])
        if ext.has_data and isfinite(ext.extmin.x) and isfinite(ext.extmax.x):
            return _pad_box(float(ext.extmin.x), float(ext.extmin.y), float(ext.extmax.x), float(ext.extmax.y), pad=pad)
    except Exception:
        pass
    dt = ent.dxftype()
    if dt == "LINE":
        s, e = ent.dxf.start, ent.dxf.end
        return _pad_box(float(s.x), float(s.y), float(e.x), float(e.y), pad=pad)
    if dt in {"CIRCLE", "ARC"}:
        c = ent.dxf.center
        r = max(0.0, float(ent.dxf.radius))
        return _pad_box(float(c.x) - r, float(c.y) - r, float(c.x) + r, float(c.y) + r, pad=pad)
    if dt in {"TEXT", "MTEXT"} and ent.dxf.hasattr("insert"):
        ins = ent.dxf.insert
        return _pad_box(float(ins.x), float(ins.y), float(ins.x) + 4.0, float(ins.y) + 4.0, pad=pad)
    return None


def _parse_displayed_nominal(raw_text: str) -> tuple[float | None, bool]:
    """Return `(nominal_value, is_thread_callout)` from an explicit dimension override string."""
    cleaned = _MTEXT_CTRL_RE.sub("", raw_text or "").strip()
    if not cleaned or cleaned == "<>" or "<>" in cleaned:
        return None, False
    cleaned = cleaned.replace("%%c", "⌀").replace("%%C", "⌀").replace("%%d", "°").replace("%%p", "±")
    cleaned = _LEADING_COUNT_RE.sub("", cleaned).strip()
    is_thread = bool(re.match(r"^[Mm]\s*\d+", cleaned))
    cleaned_no_tol = _TOL_SUFFIX_RE.sub("", cleaned).strip()
    cleaned_no_prefix = re.sub(r"^(?:[⌀∅ØøΦφRrCcMm]|SR|S⌀)\s*", "", cleaned_no_tol).strip()
    m = _NOMINAL_NUM_RE.match(cleaned_no_prefix)
    if not m:
        return None, is_thread
    val = float(m.group(1))
    return (val if isfinite(val) else None), is_thread


def _measure_true_dimension_geometry(ent: Any) -> float | None:
    """Compute true geometric distance/diameter/radius of a DIMENSION entity."""
    dtype = int(ent.dxf.get("dimtype", 0)) & 7
    dimlfac = 1.0
    try:
        if hasattr(ent, "get_dim_style"):
            ds = ent.get_dim_style()
            if ds is not None and ds.dxf.hasattr("dimlfac"):
                lf = float(ds.dxf.dimlfac)
                if isfinite(lf) and abs(lf) > 1e-6:
                    dimlfac = abs(lf)
    except Exception:
        pass

    measured: float | None = None
    try:
        gm = ent.get_measurement()
        if isinstance(gm, (int, float)) and isfinite(gm) and float(gm) > 1e-6:
            measured = float(gm)
    except Exception:
        measured = None

    if measured is None:
        if dtype in {0, 1} and ent.dxf.hasattr("defpoint2") and ent.dxf.hasattr("defpoint3"):
            p2, p3 = ent.dxf.defpoint2, ent.dxf.defpoint3
            dx, dy = float(p3.x) - float(p2.x), float(p3.y) - float(p2.y)
            if dtype == 1:
                measured = hypot(dx, dy)
            else:
                ang = radians(float(ent.dxf.get("angle", 0.0)))
                proj = abs(dx * cos(ang) + dy * sin(ang))
                measured = proj if proj > 1e-5 else hypot(dx, dy)
        elif dtype in {3, 4} and ent.dxf.hasattr("defpoint") and ent.dxf.hasattr("defpoint4"):
            p0, p4 = ent.dxf.defpoint, ent.dxf.defpoint4
            measured = hypot(float(p4.x) - float(p0.x), float(p4.y) - float(p0.y))
        elif ent.dxf.hasattr("actual_measurement"):
            am = float(ent.dxf.actual_measurement)
            if isfinite(am) and am > 1e-6:
                measured = am

    if measured is None or measured <= 1e-6:
        return None
    return measured * dimlfac


def _segment_intersects_rect(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rx0: float,
    ry0: float,
    rx1: float,
    ry1: float,
) -> bool:
    """Liang-Barsky line segment vs axis-aligned rectangle intersection test."""
    if max(x1, x2) < rx0 or min(x1, x2) > rx1 or max(y1, y2) < ry0 or min(y1, y2) > ry1:
        return False
    dx = x2 - x1
    dy = y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - rx0, rx1 - x1, y1 - ry0, ry1 - y1)
    u1, u2 = 0.0, 1.0
    for pi_v, qi_v in zip(p, q):
        if abs(pi_v) < 1e-12:
            if qi_v < 0.0:
                return False
        else:
            t = qi_v / pi_v
            if pi_v < 0.0:
                if t > u2:
                    return False
                if t > u1:
                    u1 = t
            else:
                if t < u1:
                    return False
                if t < u2:
                    u2 = t
    return u1 <= u2


def _max_insert_nesting_depth(doc: ezdxf.document.Drawing, block_name: str, seen: set[str] | None = None) -> int:
    visited = set(seen or ())
    if block_name in visited or block_name not in doc.blocks:
        return 0
    visited.add(block_name)
    blk = doc.blocks.get(block_name)
    if blk is None:
        return 0
    sub_depths = [
        1 + _max_insert_nesting_depth(doc, str(child.dxf.name), visited)
        for child in blk
        if child.dxftype() == "INSERT"
    ]
    return max(sub_depths, default=1)


def audit_legacy_dxf(
    dxf_path: Path | str,
    *,
    gap_tolerance_mm: float = 2.0,
    dim_rel_tol: float = 0.02,
    dim_abs_tol: float = 0.15,
) -> LegacyAuditReport:
    """Run standalone engineering health, tampering, topology & collision audit on any DXF file."""
    resolved = Path(dxf_path).expanduser().resolve(strict=True)
    digest = sha256_file(resolved)
    doc = ezdxf.readfile(resolved)
    dxf_audit = doc.audit()
    msp = doc.modelspace()
    entities = [e for e in msp if e.is_alive]

    insunits = int(getattr(doc, "units", 0) or 0)
    gap_tol_doc = _mm_to_doc_units(gap_tolerance_mm, insunits)
    degen_tol_doc = _mm_to_doc_units(1e-4, insunits)
    coord_q = _mm_to_doc_units(1e-3, insunits)
    text_q = _mm_to_doc_units(1e-2, insunits)
    snap_q = _mm_to_doc_units(0.002, insunits)
    pad_doc = _mm_to_doc_units(3.0, insunits)
    dim_abs_doc = _mm_to_doc_units(dim_abs_tol, insunits)

    issues: list[LegacyAuditIssue] = []

    def _emit(
        code: str,
        severity: str,
        category: str,
        message_zh: str,
        handles: list[str] | tuple[str, ...],
        location_box: tuple[float, float, float, float] | None = None,
        suggestion_zh: str = "",
        measured_value: float | None = None,
        displayed_value: float | None = None,
    ) -> None:
        idx = len(issues) + 1
        issues.append(
            LegacyAuditIssue(
                issue_id=f"LEG-{idx:03d}",
                code=code,
                severity=severity,
                category=category,
                message_zh=message_zh,
                handles=tuple(str(h).upper() for h in handles if h),
                location_box=location_box,
                suggestion_zh=suggestion_zh,
                measured_value=round(measured_value, 5) if measured_value is not None else None,
                displayed_value=round(displayed_value, 5) if displayed_value is not None else None,
            )
        )

    if dxf_audit.has_errors:
        _emit(
            "DXF_STRUCTURAL_ERROR",
            "error",
            "hygiene",
            f"DXF 底层结构审计检出 {len(dxf_audit.errors)} 处句柄或表记录损坏",
            (),
            suggestion_zh="建议先执行结构修复或重新导出标准 R2018 DXF。",
        )

    # ------------------------------------------------------------------
    # 1. Layer, Unit & Block Hygiene
    # ------------------------------------------------------------------
    if insunits == 0:
        _emit(
            "UNITLESS_DRAWING",
            "warning",
            "hygiene",
            "图纸未设置物理单位（$INSUNITS = 0），跨软件导入或英制/公制混用极易导致比例失真",
            (),
            suggestion_zh="建议将 $INSUNITS 显式归一化为 4（毫米 mm）。",
        )

    layer0_count = sum(1 for e in entities if str(e.dxf.get("layer", "0")) == "0")
    if len(entities) >= 6 and layer0_count / len(entities) >= 0.80:
        _emit(
            "LAYER_ZERO_DUMPING",
            "warning",
            "hygiene",
            f"共 {layer0_count}/{len(entities)} 个图元（{layer0_count * 100 // len(entities)}%）全部堆叠在默认 0 图层，缺少轮廓/中心线/尺寸分层",
            tuple(str(e.dxf.handle) for e in entities[:6]),
            suggestion_zh="建议按 GB/T 图层规范拆分 OUTLINE、CENTER、DIM、TEXT、HATCH。",
        )

    inserts = [e for e in entities if e.dxftype() == "INSERT"]
    deep_inserts: list[tuple[Any, int]] = []
    for ins in inserts:
        bname = str(ins.dxf.get("name", ""))
        if bname.startswith("*"):
            continue
        depth = _max_insert_nesting_depth(doc, bname)
        if depth >= 2:
            deep_inserts.append((ins, depth))
    if deep_inserts:
        top_ins, max_d = max(deep_inserts, key=lambda item: item[1])
        _emit(
            "NESTED_BLOCK_REFERENCE",
            "warning",
            "hygiene",
            f"检测到 {len(deep_inserts)} 处多层嵌套块引用（最深 {max_d} 层，块名 {top_ins.dxf.name!r}），可能导致下游审图与 CAM 漏读内部轮廓",
            tuple(str(i.dxf.handle) for i, _ in deep_inserts[:6]),
            location_box=_entity_box(top_ins, pad=pad_doc),
            suggestion_zh="建议对多层嵌套图块执行受控展开或统一块基点与单位。",
        )

    # ------------------------------------------------------------------
    # 2. Tampered Dimensions & Exploded Fake Dimensions
    # ------------------------------------------------------------------
    for ent in entities:
        if ent.dxftype() != "DIMENSION":
            continue
        raw_txt = str(ent.dxf.get("text", "")).strip()
        disp_val, is_thread = _parse_displayed_nominal(raw_txt)
        if disp_val is None:
            continue
        true_val = _measure_true_dimension_geometry(ent)
        if true_val is None:
            continue
        if is_thread and 0.72 * disp_val <= true_val <= 1.05 * disp_val:
            continue
        allowed_diff = max(dim_abs_doc, abs(disp_val) * dim_rel_tol)
        if abs(disp_val - true_val) > allowed_diff:
            _emit(
                "TAMPERED_DIMENSION_TEXT",
                "error",
                "tampering",
                f"篡改尺寸告警：标注 #{ent.dxf.handle} 显示文字为「{raw_txt}」（标称 {disp_val:g}），但几何实测值为 {true_val:.3f}（偏差 {abs(disp_val - true_val):.3f}）",
                (str(ent.dxf.handle),),
                location_box=_entity_box(ent, pad=pad_doc * 1.3),
                suggestion_zh="请核对是否人为手动改写了尺寸文字而未同步修改几何图形（假尺寸高危隐患）！",
                measured_value=true_val,
                displayed_value=disp_val,
            )

    # Detect exploded dimensions: numeric TEXT near a LINE accompanied by small SOLID arrowheads or on DIM layer
    arrow_min_doc = _mm_to_doc_units(0.8, insunits)
    arrow_max_doc = _mm_to_doc_units(12.0, insunits)
    arrow_near_doc = _mm_to_doc_units(6.0, insunits)
    text_near_doc = _mm_to_doc_units(8.5, insunits)
    min_dim_line_doc = _mm_to_doc_units(3.0, insunits)

    small_solids = [e for e in entities if e.dxftype() == "SOLID" and (_entity_box(e, pad=0.0) is not None)]
    arrow_tips: list[tuple[float, float, str]] = []
    for sol in small_solids:
        pts = [getattr(sol.dxf, k) for k in ("vtx0", "vtx1", "vtx2", "vtx3") if sol.dxf.hasattr(k)]
        if len(pts) >= 3:
            xs = [float(p.x) for p in pts]
            ys = [float(p.y) for p in pts]
            span = hypot(max(xs) - min(xs), max(ys) - min(ys))
            if arrow_min_doc <= span <= arrow_max_doc:
                arrow_tips.append((sum(xs) / len(xs), sum(ys) / len(ys), str(sol.dxf.handle)))

    all_lines = [e for e in entities if e.dxftype() == "LINE"]
    for txt_ent in entities:
        if txt_ent.dxftype() not in {"TEXT", "MTEXT"}:
            continue
        raw_t = (
            str(txt_ent.dxf.get("text", "")).strip()
            if txt_ent.dxftype() == "TEXT"
            else txt_ent.plain_text().strip()
        )
        disp_v, _ = _parse_displayed_nominal(raw_t)
        if disp_v is None or len(raw_t) > 18:
            continue
        t_ins = (
            (txt_ent.get_placement()[1] or txt_ent.dxf.insert)
            if txt_ent.dxftype() == "TEXT"
            else txt_ent.dxf.insert
        )
        tx, ty, tz = float(t_ins.x), float(t_ins.y), float(t_ins.z)
        t_layer = str(txt_ent.dxf.get("layer", "0"))

        for ln in all_lines:
            s, e = ln.dxf.start, ln.dxf.end
            x1, y1, z1 = float(s.x), float(s.y), float(s.z)
            x2, y2, z2 = float(e.x), float(e.y), float(e.z)
            if abs(tz - 0.5 * (z1 + z2)) > text_near_doc:
                continue
            seg_len = hypot(x2 - x1, y2 - y1, z2 - z1)
            if seg_len <= min_dim_line_doc:
                continue
            mx, my = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
            if hypot(tx - mx, ty - my) > text_near_doc:
                continue
            ln_layer = str(ln.dxf.get("layer", "0"))
            near_arrows = [
                ah for ax, ay, ah in arrow_tips
                if min(hypot(ax - x1, ay - y1), hypot(ax - x2, ay - y2)) <= arrow_near_doc
            ]
            is_dim_like = bool(near_arrows) or (_DIM_LAYER_RE.search(ln_layer) is not None and _DIM_LAYER_RE.search(t_layer) is not None)
            if not is_dim_like:
                continue
            involved = [str(txt_ent.dxf.handle), str(ln.dxf.handle), *near_arrows[:2]]
            comb_box = _pad_box(min(x1, x2, tx), min(y1, y2, ty), max(x1, x2, tx), max(y1, y2, ty), pad=pad_doc)
            _emit(
                "EXPLODED_DIMENSION_FRAGMENT",
                "warning",
                "hygiene",
                f"检测到炸开的散落尺寸标注（文字「{raw_t}」与独立线段 #{ln.dxf.handle} 分离，失去关联性）",
                involved,
                location_box=comb_box,
                suggestion_zh="建议替换为原生关联 DIMENSION 标注，避免改图时尺寸与图形脱节。",
                measured_value=seg_len,
                displayed_value=disp_v,
            )
            if abs(disp_v - seg_len) > max(_mm_to_doc_units(0.5, insunits), abs(disp_v) * 0.03):
                _emit(
                    "TAMPERED_DIMENSION_TEXT",
                    "error",
                    "tampering",
                    f"炸开假尺寸告警：散落文字 #{txt_ent.dxf.handle} 标称「{raw_t}」（{disp_v:g}），但对应尺寸线 #{ln.dxf.handle} 实测长度为 {seg_len:.3f}",
                    involved,
                    location_box=comb_box,
                    suggestion_zh="散落标注数值与实测线段长度严重不符，请立即复核真实设计尺寸！",
                    measured_value=seg_len,
                    displayed_value=disp_v,
                )
            break

    # ------------------------------------------------------------------
    # 3. 3D-Aware Zero-Length Entities & Elevation-Preserving Duplicates
    # ------------------------------------------------------------------
    seen_lines: dict[tuple[Any, ...], str] = {}
    seen_circles: dict[tuple[Any, ...], str] = {}
    seen_texts: dict[tuple[Any, ...], str] = {}
    # (x1, y1, z1, x2, y2, z2, handle, layer)
    contour_segments: list[tuple[float, float, float, float, float, float, str, str]] = []

    for ent in entities:
        dt = ent.dxftype()
        eh = str(ent.dxf.handle).upper()
        lyr = str(ent.dxf.get("layer", "0"))
        thick = _entity_thickness(ent)
        ext_k = _extrusion_key(ent)

        if dt == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            x1, y1, z1 = float(s.x), float(s.y), float(s.z)
            x2, y2, z2 = float(e.x), float(e.y), float(e.z)
            seg_len_3d = hypot(x2 - x1, y2 - y1, z2 - z1)
            if seg_len_3d <= degen_tol_doc and abs(thick) <= degen_tol_doc:
                _emit(
                    "ZERO_LENGTH_DEGENERATE",
                    "warning",
                    "degeneracy",
                    f"零长退化直线 #{eh}（图层 {lyr}，坐标 ({x1:.3f}, {y1:.3f}, {z1:.3f})，三维长度 {seg_len_3d:.6f}）",
                    (eh,),
                    location_box=_pad_box(x1, y1, x2, y2, pad=pad_doc),
                    suggestion_zh="可使用自动清洗移除零长退化线段。",
                )
                continue
            p_a = (_quantize(x1, coord_q), _quantize(y1, coord_q), _quantize(z1, coord_q))
            p_b = (_quantize(x2, coord_q), _quantize(y2, coord_q), _quantize(z2, coord_q))
            canon_key = (lyr.upper(), min(p_a, p_b), max(p_a, p_b), _quantize(thick, coord_q), ext_k)
            if canon_key in seen_lines:
                first_h = seen_lines[canon_key]
                _emit(
                    "DUPLICATE_OVERLAPPING_ENTITY",
                    "warning",
                    "duplication",
                    f"重叠重线 #{eh} 与 #{first_h} 完全重合（图层 {lyr}，长度 {seg_len_3d:.3f}）",
                    (first_h, eh),
                    location_box=_pad_box(x1, y1, x2, y2, pad=pad_doc),
                    suggestion_zh="重线会导致激光切割二次烧蚀或审图计数异常，建议去重清洗。",
                )
            else:
                seen_lines[canon_key] = eh
                if not _NON_CONTOUR_LAYER_RE.search(lyr) and hypot(x2 - x1, y2 - y1) > degen_tol_doc:
                    contour_segments.append((x1, y1, z1, x2, y2, z2, eh, lyr))

        elif dt in {"CIRCLE", "ARC"}:
            c = ent.dxf.center
            cx, cy, cz = float(c.x), float(c.y), float(c.z)
            r = float(ent.dxf.radius)
            if r <= degen_tol_doc:
                _emit(
                    "ZERO_LENGTH_DEGENERATE",
                    "warning",
                    "degeneracy",
                    f"退化零半径{('圆' if dt == 'CIRCLE' else '圆弧')} #{eh}（圆心 ({cx:.3f}, {cy:.3f}, {cz:.3f})，半径 {r:.6f}）",
                    (eh,),
                    location_box=_pad_box(cx, cy, cx, cy, pad=pad_doc),
                    suggestion_zh="建议移除退化圆/圆弧。",
                )
                continue
            if dt == "CIRCLE":
                ckey = (
                    lyr.upper(),
                    _quantize(cx, coord_q),
                    _quantize(cy, coord_q),
                    _quantize(cz, coord_q),
                    _quantize(r, coord_q),
                    _quantize(thick, coord_q),
                    ext_k,
                )
                if ckey in seen_circles:
                    first_h = seen_circles[ckey]
                    _emit(
                        "DUPLICATE_OVERLAPPING_ENTITY",
                        "warning",
                        "duplication",
                        f"重复同心同高程等径圆 #{eh} 与 #{first_h} 完全叠放（圆心 ({cx:.2f}, {cy:.2f}, {cz:.2f})，⌀{2 * r:.2f}）",
                        (first_h, eh),
                        location_box=_pad_box(cx - r, cy - r, cx + r, cy + r, pad=pad_doc),
                        suggestion_zh="建议去重保留单个圆图元。",
                    )
                else:
                    seen_circles[ckey] = eh
            elif not _NON_CONTOUR_LAYER_RE.search(lyr):
                sa = radians(float(ent.dxf.start_angle))
                ea = radians(float(ent.dxf.end_angle))
                ax1, ay1 = cx + r * cos(sa), cy + r * sin(sa)
                ax2, ay2 = cx + r * cos(ea), cy + r * sin(ea)
                contour_segments.append((ax1, ay1, cz, ax2, ay2, cz, eh, lyr))

        elif dt == "LWPOLYLINE":
            elev = float(ent.dxf.get("elevation", 0.0))
            pts = [(float(x), float(y)) for x, y in ent.get_points("xy")]
            if len(pts) < 2 and abs(thick) <= degen_tol_doc:
                px, py = pts[0] if pts else (0.0, 0.0)
                _emit(
                    "ZERO_LENGTH_DEGENERATE",
                    "warning",
                    "degeneracy",
                    f"退化单顶点多段线 #{eh}（顶点数 {len(pts)}）",
                    (eh,),
                    location_box=_pad_box(px, py, px, py, pad=pad_doc),
                    suggestion_zh="建议删除退化多段线。",
                )
                continue
            path_len = sum(hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(max(0, len(pts) - 1)))
            if path_len <= degen_tol_doc and abs(thick) <= degen_tol_doc:
                px, py = pts[0] if pts else (0.0, 0.0)
                _emit(
                    "ZERO_LENGTH_DEGENERATE",
                    "warning",
                    "degeneracy",
                    f"零长退化多段线 #{eh}（总长 {path_len:.6f}）",
                    (eh,),
                    location_box=_pad_box(px, py, px, py, pad=pad_doc),
                    suggestion_zh="建议删除退化多段线。",
                )
                continue
            if not _NON_CONTOUR_LAYER_RE.search(lyr) and len(pts) >= 2:
                if not ent.closed and len(pts) >= 3:
                    close_gap = hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
                    if degen_tol_doc < close_gap <= gap_tol_doc:
                        gap_mm = _doc_units_to_mm(close_gap, insunits)
                        _emit(
                            "OPEN_CONTOUR_GAP",
                            "error",
                            "topology",
                            f"轮廓多段线 #{eh} 首尾未闭合，存在 {gap_mm:.3f} mm 微小断口",
                            (eh,),
                            location_box=_pad_box(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], pad=pad_doc),
                            suggestion_zh="下料/CNC 编程要求闭合外轮廓，建议闭合多段线首尾微缝。",
                            measured_value=close_gap,
                        )
                    elif close_gap > gap_tol_doc:
                        contour_segments.append((pts[0][0], pts[0][1], elev, pts[-1][0], pts[-1][1], elev, eh, lyr))
                elif not ent.closed and len(pts) == 2:
                    contour_segments.append((pts[0][0], pts[0][1], elev, pts[1][0], pts[1][1], elev, eh, lyr))

        elif dt in {"TEXT", "MTEXT"}:
            txt_s = str(ent.dxf.get("text", "")).strip() if dt == "TEXT" else ent.plain_text().strip()
            ins = (ent.get_placement()[1] or ent.dxf.insert) if dt == "TEXT" else ent.dxf.insert
            tx, ty, tz = float(ins.x), float(ins.y), float(ins.z)
            if not txt_s:
                _emit(
                    "ZERO_LENGTH_DEGENERATE",
                    "info",
                    "degeneracy",
                    f"空白文字图元 #{eh}（坐标 ({tx:.2f}, {ty:.2f})）",
                    (eh,),
                    location_box=_pad_box(tx, ty, tx + pad_doc, ty + pad_doc, pad=pad_doc),
                    suggestion_zh="建议清理无内容空白文字。",
                )
                continue
            h_val = float(ent.dxf.get("height" if dt == "TEXT" else "char_height", 3.5))
            rot_val = round(float(ent.dxf.get("rotation", 0.0)), 2)
            tkey = (
                lyr.upper(),
                _quantize(tx, text_q),
                _quantize(ty, text_q),
                _quantize(tz, text_q),
                _quantize(h_val, coord_q),
                rot_val,
                txt_s,
            )
            if tkey in seen_texts:
                first_h = seen_texts[tkey]
                _emit(
                    "DUPLICATE_OVERLAPPING_ENTITY",
                    "warning",
                    "duplication",
                    f"重复叠放文字 #{eh} 与 #{first_h}（「{txt_s}」在 ({tx:.2f}, {ty:.2f}, {tz:.2f}) 完全重叠）",
                    (first_h, eh),
                    location_box=_pad_box(tx, ty, tx + pad_doc * 2.0, ty + pad_doc, pad=pad_doc),
                    suggestion_zh="建议删除重复叠放文字。",
                )
            else:
                seen_texts[tkey] = eh

    # ------------------------------------------------------------------
    # 4. 3D- & Unit-Aware Open Contour Endpoint Graph Micro-Gap Analysis
    # ------------------------------------------------------------------
    node_buckets: dict[tuple[int, int, int], list[tuple[float, float, float, str, str]]] = {}
    for x1, y1, z1, x2, y2, z2, eh, lyr in contour_segments:
        for px, py, pz in ((x1, y1, z1), (x2, y2, z2)):
            key = (_quantize(px, snap_q), _quantize(py, snap_q), _quantize(pz, snap_q))
            node_buckets.setdefault(key, []).append((px, py, pz, eh, lyr))

    dangling_nodes: list[tuple[float, float, float, str, str]] = [
        items[0] for items in node_buckets.values() if len(items) == 1
    ]
    reported_gap_pairs: set[tuple[str, str]] = set()
    for i in range(len(dangling_nodes)):
        px, py, pz, ha, _ = dangling_nodes[i]
        best_j = -1
        best_d = gap_tol_doc + 1.0
        for j in range(i + 1, len(dangling_nodes)):
            qx, qy, qz, hb, _ = dangling_nodes[j]
            if ha == hb:
                continue
            d = hypot(qx - px, qy - py, qz - pz)
            if degen_tol_doc < d <= gap_tol_doc and d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            qx, qy, qz, hb, _ = dangling_nodes[best_j]
            pair_key = (min(ha, hb), max(ha, hb))
            if pair_key not in reported_gap_pairs:
                reported_gap_pairs.add(pair_key)
                gap_mm = _doc_units_to_mm(best_d, insunits)
                _emit(
                    "OPEN_CONTOUR_GAP",
                    "error",
                    "topology",
                    f"轮廓断线微缝：图元 #{ha} 与 #{hb} 端点未闭合，间距 {gap_mm:.3f} mm",
                    (ha, hb),
                    location_box=_pad_box(px, py, qx, qy, pad=pad_doc),
                    suggestion_zh="肉眼不易察觉的轮廓断口会导致填充溢出或激光切割中断，建议执行微缝自动吸附闭合。",
                    measured_value=best_d,
                )

    # ------------------------------------------------------------------
    # 5. Text-Geometry Collision (Same Elevation Plane)
    # ------------------------------------------------------------------
    geom_segs_for_collision: list[tuple[float, float, float, float, float, str]] = []
    min_box_doc = _mm_to_doc_units(1.2, insunits)
    for ent in entities:
        lyr = str(ent.dxf.get("layer", "0"))
        if _NON_CONTOUR_LAYER_RE.search(lyr) and lyr != "0":
            continue
        eh = str(ent.dxf.handle).upper()
        dt = ent.dxftype()
        if dt == "LINE":
            s, e = ent.dxf.start, ent.dxf.end
            if abs(float(s.z) - float(e.z)) <= gap_tol_doc:
                geom_segs_for_collision.append((float(s.x), float(s.y), float(e.x), float(e.y), 0.5 * (float(s.z) + float(e.z)), eh))
        elif dt == "LWPOLYLINE":
            elev = float(ent.dxf.get("elevation", 0.0))
            pts = [(float(x), float(y)) for x, y in ent.get_points("xy")]
            cnt = len(pts) if ent.closed else max(0, len(pts) - 1)
            for idx_p in range(cnt):
                p0, p1 = pts[idx_p], pts[(idx_p + 1) % len(pts)]
                geom_segs_for_collision.append((p0[0], p0[1], p1[0], p1[1], elev, eh))

    for txt_ent in entities:
        dt = txt_ent.dxftype()
        if dt not in {"TEXT", "MTEXT"}:
            continue
        rot = abs(float(txt_ent.dxf.get("rotation", 0.0))) % 180.0
        if rot > 8.0 and abs(rot - 180.0) > 8.0:
            continue
        txt_s = str(txt_ent.dxf.get("text", "")).strip() if dt == "TEXT" else txt_ent.plain_text().strip()
        if len(txt_s) < 2:
            continue
        ins_pt = (txt_ent.get_placement()[1] or txt_ent.dxf.insert) if dt == "TEXT" else txt_ent.dxf.insert
        tz = float(ins_pt.z)
        raw_box = _entity_box(txt_ent, pad=0.0)
        if raw_box is None:
            continue
        bx0, by0, bx1, by1 = raw_box
        bw, bh = bx1 - bx0, by1 - by0
        if bw <= min_box_doc or bh <= min_box_doc:
            continue
        ix0, iy0 = bx0 + bw * 0.18, by0 + bh * 0.20
        ix1, iy1 = bx1 - bw * 0.18, by1 - bh * 0.20
        th = str(txt_ent.dxf.handle).upper()
        for gx1, gy1, gx2, gy2, gz, gh in geom_segs_for_collision:
            if gh == th or abs(gz - tz) > gap_tol_doc:
                continue
            if _segment_intersects_rect(gx1, gy1, gx2, gy2, ix0, iy0, ix1, iy1):
                _emit(
                    "TEXT_GEOMETRY_COLLISION",
                    "warning",
                    "collision",
                    f"文字压线：文字 #{th}（「{txt_s[:18]}」）与轮廓线 #{gh} 发生穿过干涉",
                    (th, gh),
                    location_box=_pad_box(bx0, by0, bx1, by1, pad=pad_doc),
                    suggestion_zh="建议平移文字避让轮廓线或开启背景遮罩。",
                )
                break

    # ------------------------------------------------------------------
    # 6. Compute Overall Health Score & Grade
    # ------------------------------------------------------------------
    err_cnt = sum(1 for i in issues if i.severity == "error")
    warn_cnt = sum(1 for i in issues if i.severity == "warning")
    info_cnt = sum(1 for i in issues if i.severity == "info")

    penalty = err_cnt * 22.0 + warn_cnt * 7.5 + info_cnt * 1.5
    score = round(max(0.0, 100.0 - penalty), 1)
    if err_cnt > 0:
        status = "critical"
    elif warn_cnt > 0:
        status = "warning"
    else:
        status = "healthy"

    if score >= 92.0 and err_cnt == 0:
        grade = "S"
    elif score >= 82.0 and err_cnt == 0:
        grade = "A"
    elif score >= 70.0:
        grade = "B"
    elif score >= 55.0:
        grade = "C"
    else:
        grade = "D"

    summary = {
        "errors": err_cnt,
        "warnings": warn_cnt,
        "infos": info_cnt,
        "total_issues": len(issues),
        "tampering_count": sum(1 for i in issues if i.category == "tampering"),
        "topology_gap_count": sum(1 for i in issues if i.category == "topology"),
        "degenerate_count": sum(1 for i in issues if i.category == "degeneracy"),
        "duplicate_count": sum(1 for i in issues if i.category == "duplication"),
        "collision_count": sum(1 for i in issues if i.category == "collision"),
    }

    return LegacyAuditReport(
        dxf_path=str(resolved),
        sha256=digest,
        insunits=insunits,
        entity_count=len(entities),
        health_score=score,
        grade=grade,
        status=status,
        summary=summary,
        issues=tuple(issues),
    )


def clean_legacy_dxf(
    dxf_path: Path | str,
    output_path: Path | str,
    *,
    remove_zero_length: bool = True,
    remove_duplicates: bool = True,
    close_micro_gaps_tol: float = 0.8,
    normalize_insunits: int | None = 4,
) -> dict[str, Any]:
    """Deterministically clean degenerate entities, duplicates & micro-gaps on a copy of a legacy DXF.

    Note: `close_micro_gaps_tol` is specified in physical millimetres (mm) and is automatically
    converted to the drawing's `$INSUNITS` before snapping endpoints.
    """
    src = Path(dxf_path).expanduser().resolve(strict=True)
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    before_report = audit_legacy_dxf(src, gap_tolerance_mm=max(2.0, close_micro_gaps_tol))
    doc = ezdxf.readfile(src)
    msp = doc.modelspace()

    removed_degenerate: list[str] = []
    removed_duplicates: list[str] = []
    snapped_gap_handles: list[str] = []

    orig_units = int(getattr(doc, "units", 0) or 0)
    if normalize_insunits is not None and orig_units == 0:
        doc.units = int(normalize_insunits)
    effective_units = int(getattr(doc, "units", 0) or 0)

    degen_tol_doc = _mm_to_doc_units(1e-4, effective_units)
    gap_tol_doc = _mm_to_doc_units(close_micro_gaps_tol, effective_units)
    coord_q = _mm_to_doc_units(1e-3, effective_units)
    text_q = _mm_to_doc_units(1e-2, effective_units)
    snap_q = _mm_to_doc_units(0.002, effective_units)

    # Pass 1: Remove 3D-true zero-length / degenerate entities (preserves vertical Z edges & extruded thickness!)
    if remove_zero_length:
        for ent in list(msp):
            if not ent.is_alive:
                continue
            dt = ent.dxftype()
            eh = str(ent.dxf.handle).upper()
            thick = abs(_entity_thickness(ent))
            drop = False
            if dt == "LINE":
                s, e = ent.dxf.start, ent.dxf.end
                len_3d = hypot(float(e.x) - float(s.x), float(e.y) - float(s.y), float(e.z) - float(s.z))
                drop = len_3d <= degen_tol_doc and thick <= degen_tol_doc
            elif dt in {"CIRCLE", "ARC"}:
                drop = float(ent.dxf.radius) <= degen_tol_doc
            elif dt == "LWPOLYLINE":
                pts = [(float(x), float(y)) for x, y in ent.get_points("xy")]
                path_len = sum(
                    hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(max(0, len(pts) - 1))
                )
                drop = (len(pts) < 2 or path_len <= degen_tol_doc) and thick <= degen_tol_doc
            elif dt in {"TEXT", "MTEXT"}:
                txt_s = str(ent.dxf.get("text", "")).strip() if dt == "TEXT" else ent.plain_text().strip()
                drop = not txt_s
            if drop:
                msp.delete_entity(ent)
                removed_degenerate.append(eh)

    # Pass 2: Deduplicate exact 3D overlapping LINE / CIRCLE / LWPOLYLINE / TEXT (preserves distinct Z heights!)
    if remove_duplicates:
        seen_ln: set[tuple[Any, ...]] = set()
        seen_cir: set[tuple[Any, ...]] = set()
        seen_poly: set[tuple[Any, ...]] = set()
        seen_txt: set[tuple[Any, ...]] = set()
        for ent in list(msp):
            if not ent.is_alive:
                continue
            dt = ent.dxftype()
            eh = str(ent.dxf.handle).upper()
            lyr = str(ent.dxf.get("layer", "0")).upper()
            thick_q = _quantize(_entity_thickness(ent), coord_q)
            ext_k = _extrusion_key(ent)

            if dt == "LINE":
                s, e = ent.dxf.start, ent.dxf.end
                pa = (_quantize(float(s.x), coord_q), _quantize(float(s.y), coord_q), _quantize(float(s.z), coord_q))
                pb = (_quantize(float(e.x), coord_q), _quantize(float(e.y), coord_q), _quantize(float(e.z), coord_q))
                key = (lyr, min(pa, pb), max(pa, pb), thick_q, ext_k)
                if key in seen_ln:
                    msp.delete_entity(ent)
                    removed_duplicates.append(eh)
                else:
                    seen_ln.add(key)
            elif dt == "CIRCLE":
                c = ent.dxf.center
                key_c = (
                    lyr,
                    _quantize(float(c.x), coord_q),
                    _quantize(float(c.y), coord_q),
                    _quantize(float(c.z), coord_q),
                    _quantize(float(ent.dxf.radius), coord_q),
                    thick_q,
                    ext_k,
                )
                if key_c in seen_cir:
                    msp.delete_entity(ent)
                    removed_duplicates.append(eh)
                else:
                    seen_cir.add(key_c)
            elif dt == "LWPOLYLINE":
                elev_q = _quantize(float(ent.dxf.get("elevation", 0.0)), coord_q)
                pts_q = tuple(
                    (_quantize(float(x), coord_q), _quantize(float(y), coord_q), round(float(b), 4))
                    for x, y, _, _, b in ent.get_points("xyseb")
                )
                key_p = (lyr, elev_q, bool(ent.closed), pts_q, thick_q, ext_k)
                if key_p in seen_poly:
                    msp.delete_entity(ent)
                    removed_duplicates.append(eh)
                else:
                    seen_poly.add(key_p)
            elif dt in {"TEXT", "MTEXT"}:
                txt_s = str(ent.dxf.get("text", "")).strip() if dt == "TEXT" else ent.plain_text().strip()
                ins = (ent.get_placement()[1] or ent.dxf.insert) if dt == "TEXT" else ent.dxf.insert
                h_val = float(ent.dxf.get("height" if dt == "TEXT" else "char_height", 3.5))
                rot_val = round(float(ent.dxf.get("rotation", 0.0)), 2)
                key_t = (
                    lyr,
                    _quantize(float(ins.x), text_q),
                    _quantize(float(ins.y), text_q),
                    _quantize(float(ins.z), text_q),
                    _quantize(h_val, coord_q),
                    rot_val,
                    txt_s,
                )
                if key_t in seen_txt:
                    msp.delete_entity(ent)
                    removed_duplicates.append(eh)
                else:
                    seen_txt.add(key_t)

    # Pass 3: Close micro-gaps using $INSUNITS-scaled tolerance & 3D Z-elevation matching
    if close_micro_gaps_tol > 0.0:
        contour_lines: list[Any] = []
        for ent in msp:
            if not ent.is_alive:
                continue
            lyr = str(ent.dxf.get("layer", "0"))
            if _NON_CONTOUR_LAYER_RE.search(lyr):
                continue
            if ent.dxftype() == "LWPOLYLINE" and not ent.closed:
                pts = list(ent.get_points("xyseb"))
                if len(pts) >= 3:
                    d_close = hypot(float(pts[-1][0]) - float(pts[0][0]), float(pts[-1][1]) - float(pts[0][1]))
                    if degen_tol_doc < d_close <= gap_tol_doc:
                        pts[-1] = (pts[0][0], pts[0][1], pts[-1][2], pts[-1][3], pts[-1][4])
                        ent.set_points(pts[:-1], format="xyseb")
                        ent.closed = True
                        snapped_gap_handles.append(str(ent.dxf.handle).upper())
            elif ent.dxftype() == "LINE":
                s, e = ent.dxf.start, ent.dxf.end
                if hypot(float(e.x) - float(s.x), float(e.y) - float(s.y)) > degen_tol_doc:
                    contour_lines.append(ent)

        buckets: dict[tuple[int, int, int], list[tuple[Any, str, float, float, float]]] = {}
        for ln in contour_lines:
            s, e = ln.dxf.start, ln.dxf.end
            for attr, px, py, pz in (
                ("start", float(s.x), float(s.y), float(s.z)),
                ("end", float(e.x), float(e.y), float(e.z)),
            ):
                k = (_quantize(px, snap_q), _quantize(py, snap_q), _quantize(pz, snap_q))
                buckets.setdefault(k, []).append((ln, attr, px, py, pz))
        dangling = [items[0] for items in buckets.values() if len(items) == 1]
        used_idx: set[int] = set()
        for i in range(len(dangling)):
            if i in used_idx:
                continue
            ln_a, attr_a, ax, ay, az = dangling[i]
            for j in range(i + 1, len(dangling)):
                if j in used_idx:
                    continue
                ln_b, _, bx, by, bz = dangling[j]
                if ln_a.dxf.handle == ln_b.dxf.handle:
                    continue
                dist_3d = hypot(bx - ax, by - ay, bz - az)
                if degen_tol_doc < dist_3d <= gap_tol_doc:
                    setattr(ln_a.dxf, attr_a, (bx, by, bz))
                    snapped_gap_handles.append(str(ln_a.dxf.handle).upper())
                    used_idx.add(i)
                    used_idx.add(j)
                    break

    audit_res = doc.audit()
    if audit_res.has_errors:
        raise ValueError(f"cleaned DXF failed ezdxf audit with {len(audit_res.errors)} errors")

    tmp_out = out.with_name(f".{out.name}.tmp")
    doc.saveas(tmp_out)
    tmp_out.replace(out)

    after_report = audit_legacy_dxf(out, gap_tolerance_mm=max(2.0, close_micro_gaps_tol))
    return {
        "status": "cleaned",
        "source_path": str(src),
        "output_path": str(out),
        "before_sha256": before_report.sha256,
        "after_sha256": after_report.sha256,
        "removed_degenerate_handles": removed_degenerate,
        "removed_duplicate_handles": removed_duplicates,
        "snapped_gap_handles": snapped_gap_handles,
        "before_health_score": before_report.health_score,
        "after_health_score": after_report.health_score,
        "before_grade": before_report.grade,
        "after_grade": after_report.grade,
        "before_summary": before_report.summary,
        "after_summary": after_report.summary,
        "after_audit": after_report.to_dict(),
    }
