"""Reopen-DXF Geometric & Calculation Cross-Verifier for Civil/Bridge Drawings.

Reopens generated or edited `.dxf` files via `ezdxf.readfile()` and measures actual modelspace
coordinates, polyline slopes, physical `1:scale` height/span ratios, `DIMENSION` measurements,
and printed engineering values against the authoritative calculation report.

Every discrepancy reports `{drawing_no, code, view_id, expected, measured, delta, message}`.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from pathlib import Path
import re
from typing import Any

import ezdxf

from .symbols import META_XDATA_APPID, VIEW_XDATA_APPID

_UNIT_TO_MM: dict[str, float] = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
_SLOPE_PCT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")
_ELEV_TEXT_RE = re.compile(r"([+-]\d+\.\d{3}|±0\.000)\s*$")
_FIRST_NUM_RE = re.compile(r"(?<![A-Za-z0-9_Φφ⌀])(\d+(?:\.\d+)?)(?:\s*(?:mm|cm|m))?\s*$")


def _decode_xdata_records(doc: ezdxf.document.Drawing, appid: str) -> list[dict[str, Any]]:
    msp = doc.modelspace()
    out: list[dict[str, Any]] = []
    if appid not in doc.appids:
        return out
    for ent in msp:
        if not ent.has_xdata(appid):
            continue
        buf = ""
        for code, val in ent.get_xdata(appid):
            if code != 1000:
                continue
            s = str(val)
            if s.startswith("@@REC@@"):
                if buf:
                    try:
                        out.append(json.loads(buf))
                    except json.JSONDecodeError:
                        pass
                buf = s[len("@@REC@@") :]
            elif s.startswith("@@CONT@@"):
                buf += s[len("@@CONT@@") :]
            else:
                if buf:
                    try:
                        out.append(json.loads(buf))
                    except json.JSONDecodeError:
                        pass
                    buf = ""
                try:
                    out.append(json.loads(s))
                except json.JSONDecodeError:
                    pass
        if buf:
            try:
                out.append(json.loads(buf))
            except json.JSONDecodeError:
                pass
    return out


def extract_dxf_views_and_meta(doc: ezdxf.document.Drawing) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    views = _decode_xdata_records(doc, VIEW_XDATA_APPID)
    meta_records = _decode_xdata_records(doc, META_XDATA_APPID)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in meta_records:
        k = str(rec.get("key", "misc"))
        grouped.setdefault(k, []).append(dict(rec.get("data") or {}))
    return views, grouped


def _rect_distance(b: list[float], px: float, py: float) -> float:
    dx = max(b[0] - px, 0.0, px - b[2])
    dy = max(b[1] - py, 0.0, py - b[3])
    return math.hypot(dx, dy)


def _find_enclosing_view(views: list[dict[str, Any]], px: float, py: float) -> dict[str, Any] | None:
    if not views:
        return None
    best_v: dict[str, Any] | None = None
    best_d = 1e9
    for v in views:
        b = v.get("paper_bounds")
        if b and len(b) == 4:
            d = _rect_distance(b, px, py)
            if d < best_d:
                best_d = d
                best_v = v
    if best_v is not None and best_d <= 32.0:
        return best_v
    return views[0] if len(views) == 1 else None


def _extract_trailing_dim_number(label: str) -> float | None:
    s = (label or "").strip()
    if not s or "×" in s or "*" in s or "(" in s or "（" in s:
        # Handle "桥梁全长 10800 (4×2500)" -> extract 10800 before '('
        pre = re.split(r"[\(（]", s, maxsplit=1)[0].strip()
        if "×" in pre or "*" in pre:
            return None
        s = pre
    m = _FIRST_NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def verify_civil_dxf(
    dxf_path: str | Path,
    *,
    calc_report: Mapping[str, Any] | None = None,
    expected_spec: Mapping[str, Any] | None = None,
    slope_tol_pct: float = 0.08,
    paper_tol_mm: float = 0.06,
) -> dict[str, Any]:
    """Reopen `dxf_path`, measure real geometry & dimensions, and cross-check against `calc_report`."""
    path = Path(dxf_path).expanduser().resolve(strict=True)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    views, meta = extract_dxf_views_and_meta(doc)

    ident = (meta.get("sheet_identity") or [{}])[0]
    drawing_no = str(ident.get("drawing_no") or (expected_spec or {}).get("drawing_no") or path.stem)
    issues: list[dict[str, Any]] = []
    checks_run = 0

    def _fail(
        code: str,
        *,
        expected: float | str,
        measured: float | str,
        delta: float | None,
        message: str,
        view_id: str = "sheet",
    ) -> None:
        issues.append(
            {
                "drawing_no": drawing_no,
                "view_id": view_id,
                "code": code,
                "severity": "error",
                "expected": expected,
                "measured": measured,
                "delta": round(delta, 4) if delta is not None else None,
                "message": f"[{drawing_no}] {message}",
            }
        )

    # Collect texts and polylines once
    texts: list[tuple[str, float, float]] = []
    polylines: list[list[tuple[float, float]]] = []
    dims: list[Any] = []
    for ent in msp:
        dt = ent.dxftype()
        if dt == "TEXT":
            ins = ent.get_placement()[1] or ent.dxf.insert
            texts.append((str(ent.dxf.get("text", "")).strip(), float(ins.x), float(ins.y)))
        elif dt == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in ent.get_points("xy")]
            if len(pts) >= 2:
                polylines.append(pts)
        elif dt == "DIMENSION":
            dims.append(ent)

    # ------------------------------------------------------------------
    # 1. Real Deck Cross-Slope Geometry Check (标了 2% 横坡，实测几何必为 2% 坡度)
    # ------------------------------------------------------------------
    slope_labels = [(float(m.group(1)), tx, ty) for s, tx, ty in texts if (m := _SLOPE_PCT_RE.match(s))]
    if slope_labels:
        # Measure the topmost wide deck/cap surface segment directly beneath each slope callout
        for target_pct, sx, sy in slope_labels:
            topmost_y = -1e9
            measured_slope_pct: float | None = None
            for pts in polylines:
                n_pts = len(pts)
                for i in range(n_pts - 1):
                    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                    seg_dx = abs(x1 - x0)
                    if seg_dx < 15.0:
                        continue
                    x_lo, x_hi = min(x0, x1), max(x0, x1)
                    y_mid = 0.5 * (y0 + y1)
                    if (x_lo - 6.0) <= sx <= (x_hi + 6.0) and 0.5 <= (sy - y_mid) <= 28.0:
                        if y_mid > topmost_y:
                            topmost_y = y_mid
                            measured_slope_pct = (abs(y1 - y0) / seg_dx) * 100.0
            checks_run += 1
            if measured_slope_pct is None:
                _fail(
                    "DECK_SLOPE_SURFACE_MISSING",
                    expected=target_pct,
                    measured="none",
                    delta=None,
                    message=f"标注了 {target_pct:g}% 横坡，但下方未找到对应的桥面/盖梁顶缘几何线段",
                )
            elif abs(measured_slope_pct - target_pct) > slope_tol_pct:
                _fail(
                    "DECK_CROSS_SLOPE_MISMATCH",
                    expected=round(target_pct, 3),
                    measured=round(measured_slope_pct, 3),
                    delta=measured_slope_pct - target_pct,
                    message=(
                        f"桥面横坡标注为 {target_pct:.2f}%，但 DXF 顶面多段线实测坡度为 "
                        f"{measured_slope_pct:.2f}%（差值 {measured_slope_pct - target_pct:+.2f}%）"
                    ),
                )

    # ------------------------------------------------------------------
    # 2. True Physical 1:Scale Elevation Delta Check (1:400 下 1m 高差实测必为 2.5mm)
    # ------------------------------------------------------------------
    # Reconstruct elevation marker tip coordinates from triangular markers + text
    elev_points: list[tuple[float, float, float, str]] = []
    for s, tx, ty in texts:
        m_el = _ELEV_TEXT_RE.search(s)
        if not m_el:
            continue
        tok = m_el.group(1)
        elev_m = 0.0 if tok == "±0.000" else float(tok)
        # Find nearest small closed 3-vertex triangle (draw_elevation_marker) near (tx, ty)
        tip_y: float | None = None
        tip_x: float = tx
        best_tri_dist = 1e9
        for pts in polylines:
            if len(pts) == 3:
                ys = [p[1] for p in pts]
                xs = [p[0] for p in pts]
                cy_tri = min(ys)
                cx_tri = xs[ys.index(cy_tri)]
                if abs(cx_tri - tx) < 15.0 and 0.2 <= (ty - cy_tri) <= 22.0:
                    d_tri = math.hypot(cx_tri - tx, ty - cy_tri)
                    if d_tri < best_tri_dist:
                        best_tri_dist = d_tri
                        tip_y = cy_tri
                        tip_x = cx_tri
        if tip_y is not None:
            elev_points.append((elev_m, tip_x, tip_y, s))

    if len(elev_points) >= 2:
        base_el, bx, by, b_lbl = elev_points[0]
        v_box = _find_enclosing_view(views, bx, by)
        if v_box is not None:
            scale_denom = float(v_box["scale_denom"])
            for other_el, ox, oy, o_lbl in elev_points[1:]:
                v_other = _find_enclosing_view(views, ox, oy)
                if v_other is None or v_other["view_id"] != v_box["view_id"]:
                    continue
                dh_m = abs(base_el - other_el)
                expected_dy_mm = dh_m * (1000.0 / scale_denom)
                measured_dy_mm = abs(by - oy)
                checks_run += 1
                if abs(measured_dy_mm - expected_dy_mm) > paper_tol_mm:
                    _fail(
                        "TRUE_SCALE_ELEVATION_MISMATCH",
                        view_id=str(v_box["view_id"]),
                        expected=round(expected_dy_mm, 3),
                        measured=round(measured_dy_mm, 3),
                        delta=measured_dy_mm - expected_dy_mm,
                        message=(
                            f"视图 {v_box['view_id']} (1:{scale_denom:g}) 标高 '{b_lbl}' 与 '{o_lbl}' "
                            f"高差 {dh_m:.3f}m 理论纸面距离应为 {expected_dy_mm:.3f}mm，实测为 {measured_dy_mm:.3f}mm "
                            f"（差值 {measured_dy_mm - expected_dy_mm:+.3f}mm）"
                        ),
                    )

    # ------------------------------------------------------------------
    # 3. Linear DIMENSION True-Scale Measurement vs Label Verification
    # ------------------------------------------------------------------
    dim_numeric_values: list[float] = []
    for dim in dims:
        raw_txt = str(dim.dxf.get("text", "")).strip()
        val_num = _extract_trailing_dim_number(raw_txt)
        if val_num is not None:
            dim_numeric_values.append(val_num)
        if not views or val_num is None or val_num <= 0:
            continue
        p2 = dim.dxf.defpoint2
        p3 = dim.dxf.defpoint3
        mid_x = 0.5 * float(p2.x + p3.x)
        mid_y = 0.5 * float(p2.y + p3.y)
        v_box = _find_enclosing_view(views, mid_x, mid_y)
        if v_box is None:
            continue
        ang_rad = math.radians(float(dim.dxf.get("angle", 0.0)))
        dx_p = float(p3.x - p2.x)
        dy_p = float(p3.y - p2.y)
        meas = dim.get_measurement()
        proj_paper_mm = float(meas) if isinstance(meas, (int, float)) and meas > 0 else abs(dx_p * math.cos(ang_rad) + dy_p * math.sin(ang_rad))
        unit_mm = _UNIT_TO_MM.get(str(v_box.get("real_unit", "mm")), 1.0)
        scale_denom = float(v_box["scale_denom"])
        expected_paper_mm = val_num * (unit_mm / scale_denom)
        checks_run += 1
        tol_mm = max(paper_tol_mm, 0.008 * expected_paper_mm)
        if abs(proj_paper_mm - expected_paper_mm) > tol_mm:
            _fail(
                "DIMENSION_SCALE_MISMATCH",
                view_id=str(v_box["view_id"]),
                expected=round(expected_paper_mm, 3),
                measured=round(proj_paper_mm, 3),
                delta=proj_paper_mm - expected_paper_mm,
                message=(
                    f"尺寸标注 '{raw_txt}' 在视图 {v_box['view_id']} (1:{scale_denom:g}) "
                    f"理论纸面跨度应为 {expected_paper_mm:.3f}mm，实测特征点跨度为 {proj_paper_mm:.3f}mm "
                    f"（差值 {proj_paper_mm - expected_paper_mm:+.3f}mm）"
                ),
            )

    # ------------------------------------------------------------------
    # 4. Cross-Section Dual Width-Chain Check (7000 / 8000 / 9000 + Girder Chain)
    # ------------------------------------------------------------------
    deck_meta = (meta.get("deck_width_chains") or [None])[0]
    if deck_meta is not None:
        required_dims = [
            ("行车道宽", float(deck_meta["roadway_w_mm"])),
            ("护栏内净宽", float(deck_meta["clear_w_mm"])),
            ("桥面全宽", float(deck_meta["deck_w_mm"])),
            ("主梁间距", float(deck_meta["girder_spacing_mm"])),
        ]
        if float(deck_meta.get("edge_cantilever_mm", 0.0)) > 0:
            required_dims.append(("边梁悬臂宽", float(deck_meta["edge_cantilever_mm"])))
        for name_zh, req_val in required_dims:
            checks_run += 1
            if not any(abs(dv - req_val) < 1.0 for dv in dim_numeric_values):
                _fail(
                    "MISSING_WIDTH_CHAIN_DIM",
                    expected=req_val,
                    measured="missing",
                    delta=None,
                    message=f"横断面缺少关键宽度尺寸标注：{name_zh} ({req_val:g} mm)",
                )

    # ------------------------------------------------------------------
    # 5. Calculation Report Cross-Check (Tendon Prestress, Rebar Ledger, BOQ)
    # ------------------------------------------------------------------
    if calc_report is not None:
        joined_text = " | ".join(s for s, _, _ in texts if s)

        # 5a. Tendon effective prestress & control stress check
        if "prestress" in calc_report and ("有效预应力" in joined_text or "σpe" in joined_text):
            ps = calc_report["prestress"]
            loss_rows = ps.get("loss_profile") or []
            if loss_rows:
                exp_pe = float(loss_rows[len(loss_rows) // 2]["effective_prestress_mpa"])
                m_pe = re.search(r"(?:有效预应力|σpe)\s*[≈=:]?\s*(\d+(?:\.\d+)?)\s*MPa", joined_text)
                if m_pe:
                    meas_pe = float(m_pe.group(1))
                    checks_run += 1
                    if abs(meas_pe - exp_pe) > 1.2:
                        _fail(
                            "TENDON_EFFECTIVE_PRESTRESS_MISMATCH",
                            expected=round(exp_pe, 2),
                            measured=round(meas_pe, 2),
                            delta=meas_pe - exp_pe,
                            message=(
                                f"钢束图跨中有效预应力标注为 {meas_pe:g} MPa，与计算书结果 {exp_pe:.2f} MPa 不一致 "
                                f"（差值 {meas_pe - exp_pe:+.2f} MPa）"
                            ),
                        )
            exp_con = float(ps.get("sigma_con_mpa", 0.0) or 0.0)
            if exp_con > 0:
                m_con = re.search(r"σcon\s*=\s*(\d+(?:\.\d+)?)\s*MPa", joined_text)
                if m_con:
                    meas_con = float(m_con.group(1))
                    checks_run += 1
                    if abs(meas_con - exp_con) > 1.0:
                        _fail(
                            "TENDON_CONTROL_STRESS_MISMATCH",
                            expected=round(exp_con, 2),
                            measured=round(meas_con, 2),
                            delta=meas_con - exp_con,
                            message=(
                                f"钢束图张拉控制应力 σcon 标注为 {meas_con:g} MPa，与计算书 {exp_con:g} MPa 不符 "
                                f"（差值 {meas_con - exp_con:+.2f} MPa）"
                            ),
                        )

        # 5b. Rebar schedule rows vs authoritative ledger
        ledger_idx = ((calc_report.get("rebar_and_tendon_ledger") or {}).get("index_by_id")) or {}
        for sched in meta.get("rebar_schedule", []):
            for row in sched.get("items", []):
                bid = str(row.get("bar_id", ""))
                if bid in ledger_idx:
                    auth = ledger_idx[bid]
                    exp_cnt = int(auth["count"])
                    meas_cnt = int(row["count"])
                    checks_run += 1
                    if meas_cnt != exp_cnt:
                        _fail(
                            "REBAR_COUNT_LEDGER_MISMATCH",
                            expected=exp_cnt,
                            measured=meas_cnt,
                            delta=float(meas_cnt - exp_cnt),
                            message=f"钢筋编号 {bid} 图面明细表根数 ({meas_cnt}) 与计算配筋账本 ({exp_cnt}) 不一致",
                        )

    return {
        "drawing_no": drawing_no,
        "path": str(path),
        "status": "passed" if not issues else "failed",
        "checks_run": checks_run,
        "error_count": len(issues),
        "issues": issues,
        "registered_views": [v["view_id"] for v in views],
    }
