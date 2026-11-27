"""Reopen-DXF Geometric, Visible-Table, Rebar-Entity & Calculation Cross-Verifier for Bridge Drawings.

Reopens generated or edited `.dxf` files via `ezdxf.readfile()` and measures:
1. Actual modelspace polyline slopes (`2%` deck/cap cross-slopes) and physical `1:scale` height/span ratios;
2. Native `DIMENSION` measurements against printed labels and `calc_report` component dimensions;
3. Visible mathematical inequalities (`A ≤ B`, `A ≥ B`) and structural pass/fail conclusions against `calc_report`;
4. Human-visible rebar schedule table `TEXT` rows (`编号`, `规格`, `单长`, `根数`, `总长`, `总重`), callout tags,
   and drawn `REBAR` cross-section `CIRCLE` counts (never blindly trusting XDATA metadata);
5. Sub-detail view completeness on `QL-13` (barrier, continuity, expansion joint, scupper, approach slab),
   `QL-17` (pier column + bored pile), and `QL-18` (abutment cap + backwall + pedestal + pile).

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
_INEQ_CLAIM_RE = re.compile(
    r"([-+]?\d+(?:\.\d+)?)\s*(?:MPa|kPa|kN·m|kN|mm|cm|m)?\s*(≤|<=|≥|>=)\s*([-+]?\d+(?:\.\d+)?)"
)
_SPEC_DIA_RE = re.compile(r"(?:%%c|[Φφ⌀])\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

_REQUIRED_QL13_VIEWS: tuple[tuple[str, str], ...] = (
    ("crash_barrier_detail", "防撞护栏构造详图"),
    ("expansion_joint_detail", "桥台型钢伸缩装置详图"),
    ("scupper_drain_detail", "桥面泄水管构造详图"),
    ("deck_continuity_detail", "墩顶桥面连续构造详图"),
    ("approach_slab_detail", "桥头搭板构造详图"),
    ("approach_corbel_detail", "桥头搭板台背牛腿支承放大图"),
)

_LEAKED_INTERNAL_TOKENS: tuple[str, ...] = (
    "flood_elevation_m",
    "deck_elevation_m",
    "soil_layers",
    "pile_length_m",
    "calc_span_m",
    "t_girder_general",
    "tendon_layout",
    "rebar_detail",
    "bearing_layout",
    "deck_ancillary",
)


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


def _find_enclosing_view(views: list[dict[str, Any]], px: float, py: float, max_dist: float = 32.0) -> dict[str, Any] | None:
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
    if best_v is not None and best_d <= max_dist:
        return best_v
    return views[0] if len(views) == 1 else None


def _extract_trailing_dim_number(label: str) -> float | None:
    s = (label or "").strip()
    if not s or "×" in s or "*" in s or "(" in s or "（" in s:
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


def _parse_visible_rebar_tables(texts: list[tuple[str, float, float]]) -> list[dict[str, Any]]:
    """Reconstruct human-visible rebar schedule tables directly from modelspace TEXT entities."""
    hdr_alias: dict[str, str] = {
        "构件部位": "构件部位",
        "编号": "编号",
        "规格(mm)": "规格(mm)",
        "单长(cm)": "单长(cm)",
        "根数": "根数",
        "单件根数": "根数",
        "总长(m)": "总长(m)",
        "单件长(m)": "总长(m)",
        "总重(kg)": "总重(kg)",
        "单件重(kg)": "总重(kg)",
        "每墩件数": "每座件数",
        "每台件数": "每座件数",
        "每墩重(kg)": "每座重(kg)",
        "每台重(kg)": "每座重(kg)",
    }
    tables: list[dict[str, Any]] = []
    id_headers = [(s, tx, ty) for s, tx, ty in texts if s == "编号"]
    for _, hx_id, hy_hdr in id_headers:
        hdr_cells = [(s, tx, ty) for s, tx, ty in texts if abs(ty - hy_hdr) <= 1.5 and tx >= hx_id - 30.0]
        col_map: dict[str, float] = {}
        for s, tx, _ in hdr_cells:
            if s in hdr_alias:
                col_map[hdr_alias[s]] = tx
        if "编号" not in col_map or "根数" not in col_map:
            continue

        has_unit_mult = "每座件数" in col_map and "每座重(kg)" in col_map
        x_min = min(col_map.values()) - 14.0
        x_max = max(col_map.values()) + 18.0
        sum_candidates = [
            (s, tx, ty)
            for s, tx, ty in texts
            if "钢筋合计" in s and x_min <= tx <= x_max and ty < hy_hdr - 2.0
        ]
        y_sum = max((ty for _, _, ty in sum_candidates), default=46.0)
        sum_txt = next((s for s, _, ty in sum_candidates if abs(ty - y_sum) < 0.5), "")
        m_sum = re.search(r"(\d+(?:\.\d+)?)\s*kg", sum_txt)
        reported_sum_kg = float(m_sum.group(1)) if m_sum else None

        body_cells = [
            (s, tx, ty)
            for s, tx, ty in texts
            if x_min <= tx <= x_max and (y_sum + 1.8) <= ty <= (hy_hdr - 1.8)
        ]
        # Exclude merged group labels positioned inside the '构件部位' column before row clustering
        part_col_x = col_map.get("构件部位")
        if part_col_x is not None:
            body_cells = [
                (s, tx, ty)
                for s, tx, ty in body_cells
                if abs(tx - part_col_x) > abs(tx - col_map["编号"])
            ]

        # Cluster body cells into rows by y coordinate
        body_cells.sort(key=lambda item: -item[2])
        row_clusters: list[list[tuple[str, float, float]]] = []
        for cell in body_cells:
            if not row_clusters or abs(cell[2] - row_clusters[-1][0][2]) > 1.8:
                row_clusters.append([cell])
            else:
                row_clusters[-1].append(cell)

        parsed_rows: dict[str, dict[str, Any]] = {}
        for r_cells in row_clusters:
            assigned: dict[str, str] = {}
            for s, tx, _ in r_cells:
                nearest_col = min(col_map.keys(), key=lambda ck: abs(col_map[ck] - tx))
                if abs(col_map[nearest_col] - tx) <= 16.0:
                    assigned[nearest_col] = s
            bid = assigned.get("编号", "").strip()
            if not bid:
                continue
            spec_s = assigned.get("规格(mm)", "")
            m_d = _SPEC_DIA_RE.search(spec_s)
            dia_val = float(m_d.group(1)) if m_d else None

            def _f(col_key: str) -> float | None:
                raw_v = assigned.get(col_key, "").strip()
                try:
                    return float(raw_v)
                except ValueError:
                    return None

            cnt_f = _f("根数")
            mult_raw = assigned.get("每座件数", "").strip()
            m_mult = re.search(r"(\d+)", mult_raw) if mult_raw else None
            parsed_rows[bid] = {
                "bar_id": bid,
                "spec": spec_s,
                "dia_mm": dia_val,
                "single_cm": _f("单长(cm)"),
                "count": int(round(cnt_f)) if cnt_f is not None else None,
                "count_raw": assigned.get("根数", ""),
                "total_m": _f("总长(m)"),
                "weight_kg": _f("总重(kg)"),
                "unit_mult": int(m_mult.group(1)) if m_mult else None,
                "unit_mult_raw": mult_raw,
                "sub_weight_kg": _f("每座重(kg)"),
            }

        tables.append(
            {
                "bbox": (x_min, y_sum - 2.0, x_max, hy_hdr + 9.0),
                "rows": parsed_rows,
                "reported_sum_kg": reported_sum_kg,
                "has_unit_mult": has_unit_mult,
            }
        )
    return tables


def verify_civil_dxf(
    dxf_path: str | Path,
    *,
    calc_report: Mapping[str, Any] | None = None,
    expected_spec: Mapping[str, Any] | None = None,
    slope_tol_pct: float = 0.08,
    paper_tol_mm: float = 0.06,
) -> dict[str, Any]:
    """Reopen `dxf_path`, measure real geometry, visible tables & rebar entities, and cross-check `calc_report`."""
    path = Path(dxf_path).expanduser().resolve(strict=True)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    views, meta = extract_dxf_views_and_meta(doc)
    views_by_id = {str(v["view_id"]): v for v in views if "view_id" in v}

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

    texts: list[tuple[str, float, float]] = []
    polylines: list[list[tuple[float, float]]] = []
    lines_geom: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    rebar_dots: list[tuple[float, float, float]] = []
    tendon_dots: list[tuple[float, float, float]] = []
    dims: list[Any] = []

    for ent in msp:
        dt = ent.dxftype()
        lyr = str(ent.dxf.get("layer", ""))
        if dt == "TEXT":
            ins = ent.get_placement()[1] or ent.dxf.insert
            texts.append((str(ent.dxf.get("text", "")).strip(), float(ins.x), float(ins.y)))
        elif dt == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in ent.get_points("xy")]
            if len(pts) >= 2:
                polylines.append(pts)
        elif dt == "LINE":
            s_pt, e_pt = ent.dxf.start, ent.dxf.end
            lines_geom.append(((float(s_pt.x), float(s_pt.y)), (float(e_pt.x), float(e_pt.y)), lyr))
        elif dt == "CIRCLE":
            rad = float(ent.dxf.get("radius", 0.0))
            c_pt = ent.dxf.center
            if lyr == "REBAR" and 0.35 <= rad <= 3.0:
                rebar_dots.append((float(c_pt.x), float(c_pt.y), rad))
            elif lyr == "TENDON" and 0.6 <= rad <= 4.0:
                tendon_dots.append((float(c_pt.x), float(c_pt.y), rad))
        elif dt == "DIMENSION":
            dims.append(ent)

    # ------------------------------------------------------------------
    # 0. Visible Inequality Truth Check + Truncation / Internal-Key Leak Check
    # ------------------------------------------------------------------
    new_item_re = re.compile(r"^(?:[一二三四五六七八九十]+、|\[[A-Za-z0-9]+\]|\d+[.、)）]|【)")
    sorted_col_texts = sorted(enumerate(texts), key=lambda item: (round(item[1][1] / 6.0), -item[1][2]))
    consumed_text_idxs: set[int] = set()
    logical_paragraphs: list[str] = []
    for pos, (orig_idx, (s_cur, tx_cur, ty_cur)) in enumerate(sorted_col_texts):
        if orig_idx in consumed_text_idxs:
            continue
        consumed_text_idxs.add(orig_idx)
        para = s_cur
        prev_x, prev_y = tx_cur, ty_cur
        for next_orig_idx, (s_nxt, tx_nxt, ty_nxt) in sorted_col_texts[pos + 1 :]:
            if next_orig_idx in consumed_text_idxs:
                continue
            if abs(tx_nxt - prev_x) <= 6.0 and 1.5 <= (prev_y - ty_nxt) <= 8.0 and not new_item_re.match(s_nxt):
                if para.count("(") > para.count(")") or para.count("（") > para.count("）"):
                    para = para + s_nxt
                    consumed_text_idxs.add(next_orig_idx)
                    prev_x, prev_y = tx_nxt, ty_nxt
                    continue
            if abs(tx_nxt - prev_x) <= 6.0 and (prev_y - ty_nxt) > 8.5:
                break
        logical_paragraphs.append(para)

    for para in logical_paragraphs:
        if para.count("(") != para.count(")") or para.count("（") != para.count("）"):
            checks_run += 1
            _fail(
                "TRUNCATED_UNCLOSED_PARENTHESIS",
                expected="closed_parentheses",
                measured=para,
                delta=None,
                message=f"图面文本存在未闭合括号截断：'{para}'",
            )

    for s, _, _ in texts:
        for bad_tok in _LEAKED_INTERNAL_TOKENS:
            if bad_tok in s:
                checks_run += 1
                _fail(
                    "LEAKED_INTERNAL_FIELD_NAME",
                    expected="zh_engineering_term",
                    measured=bad_tok,
                    delta=None,
                    message=f"图面文本残留未转换的程序内部字段名 '{bad_tok}'：'{s}'",
                )
        for m_in in _INEQ_CLAIM_RE.finditer(s):
            lhs = float(m_in.group(1))
            op = m_in.group(2)
            rhs = float(m_in.group(3))
            checks_run += 1
            if op in {"≤", "<="} and lhs > rhs + 1e-4:
                _fail(
                    "FALSE_INEQUALITY_ON_DRAWING",
                    expected=rhs,
                    measured=lhs,
                    delta=lhs - rhs,
                    message=f"图面出现自相矛盾的伪合格不等式 '{m_in.group(0)}'（计算值 {lhs:g} 实际大于限值 {rhs:g}）",
                )
            elif op in {"≥", ">="} and lhs < rhs - 1e-4:
                _fail(
                    "FALSE_INEQUALITY_ON_DRAWING",
                    expected=rhs,
                    measured=lhs,
                    delta=lhs - rhs,
                    message=f"图面出现自相矛盾的伪合格不等式 '{m_in.group(0)}'（计算值 {lhs:g} 实际小于限值 {rhs:g}）",
                )

    # ------------------------------------------------------------------
    # 1. Real Deck Cross-Slope Geometry Check (标了 2% 横坡，实测几何必为 2% 坡度)
    # ------------------------------------------------------------------
    slope_labels = [(float(m.group(1)), tx, ty) for s, tx, ty in texts if (m := _SLOPE_PCT_RE.match(s))]
    if slope_labels:
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
    elev_points: list[tuple[float, float, float, str]] = []
    for s, tx, ty in texts:
        m_el = _ELEV_TEXT_RE.search(s)
        if not m_el:
            continue
        tok = m_el.group(1)
        elev_m = 0.0 if tok == "±0.000" else float(tok)
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
    dims_by_view: dict[str, list[float]] = {}
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
        vid_s = str(v_box["view_id"])
        dims_by_view.setdefault(vid_s, []).append(val_num)
        ang_rad = math.radians(float(dim.dxf.get("angle", 0.0)))
        dx_p = float(p3.x - p2.x)
        dy_p = float(p3.y - p2.y)
        meas = dim.get_measurement()
        proj_paper_mm = (
            float(meas)
            if isinstance(meas, (int, float)) and meas > 0
            else abs(dx_p * math.cos(ang_rad) + dy_p * math.sin(ang_rad))
        )
        unit_mm = _UNIT_TO_MM.get(str(v_box.get("real_unit", "mm")), 1.0)
        scale_denom = float(v_box["scale_denom"])
        expected_paper_mm = val_num * (unit_mm / scale_denom)
        checks_run += 1
        tol_mm = max(paper_tol_mm, 0.008 * expected_paper_mm)
        if abs(proj_paper_mm - expected_paper_mm) > tol_mm:
            _fail(
                "DIMENSION_SCALE_MISMATCH",
                view_id=vid_s,
                expected=round(expected_paper_mm, 3),
                measured=round(proj_paper_mm, 3),
                delta=proj_paper_mm - expected_paper_mm,
                message=(
                    f"尺寸标注 '{raw_txt}' 在视图 {vid_s} (1:{scale_denom:g}) "
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
    # 5. Visible Rebar Schedule Table + Callouts + Drawn Rebar Entities Verification
    # ------------------------------------------------------------------
    visible_tables = _parse_visible_rebar_tables(texts)
    visible_bars: dict[str, dict[str, Any]] = {}
    table_bboxes: list[tuple[float, float, float, float]] = []
    for tbl in visible_tables:
        table_bboxes.append(tbl["bbox"])
        sum_calc = 0.0
        for bid, r_info in tbl["rows"].items():
            visible_bars[bid] = r_info
            cnt_v = r_info["count"]
            len_cm = r_info["single_cm"]
            tot_m = r_info["total_m"]
            wt_kg = r_info["weight_kg"]
            if cnt_v is None or len_cm is None or tot_m is None or wt_kg is None:
                checks_run += 1
                _fail(
                    "VISIBLE_REBAR_TABLE_CELL_INVALID",
                    expected="numeric_cells",
                    measured=str(r_info),
                    delta=None,
                    message=f"可见钢筋表编号 {bid} 存在缺失或非数值单元格",
                )
                continue
            exp_tot_m = (len_cm / 100.0) * cnt_v
            checks_run += 1
            if abs(tot_m - exp_tot_m) > max(0.18, 0.015 * exp_tot_m):
                _fail(
                    "VISIBLE_REBAR_TABLE_ARITHMETIC_MISMATCH",
                    expected=round(exp_tot_m, 2),
                    measured=round(tot_m, 2),
                    delta=tot_m - exp_tot_m,
                    message=(
                        f"可见钢筋表编号 {bid} 根数({cnt_v}) × 单长({len_cm:g}cm) = {exp_tot_m:.2f}m，"
                        f"与图面总长列 ({tot_m:.2f}m) 不符（疑似手工篡改根数或单长）"
                    ),
                )
            if tbl.get("has_unit_mult"):
                u_mult = r_info.get("unit_mult")
                sub_wt = r_info.get("sub_weight_kg")
                checks_run += 1
                if u_mult is None or sub_wt is None or abs(sub_wt - wt_kg * u_mult) > max(0.35, 0.015 * wt_kg * u_mult):
                    _fail(
                        "VISIBLE_REBAR_UNIT_MULTIPLIER_MISMATCH",
                        expected=round(wt_kg * (u_mult or 1), 1),
                        measured=sub_wt,
                        delta=(sub_wt - wt_kg * u_mult) if (sub_wt is not None and u_mult is not None) else None,
                        message=(
                            f"可见钢筋表编号 {bid} 单件重({wt_kg:.1f}kg) × 每座构件数({r_info.get('unit_mult_raw')}) "
                            f"与每座重列 ({sub_wt}kg) 不符"
                        ),
                    )
                if sub_wt is not None:
                    sum_calc += sub_wt
            else:
                sum_calc += wt_kg
        if tbl["reported_sum_kg"] is not None and tbl["rows"]:
            checks_run += 1
            if abs(sum_calc - tbl["reported_sum_kg"]) > max(1.0, 0.02 * sum_calc):
                _fail(
                    "VISIBLE_REBAR_SUM_MISMATCH",
                    expected=round(sum_calc, 1),
                    measured=round(tbl["reported_sum_kg"], 1),
                    delta=tbl["reported_sum_kg"] - sum_calc,
                    message=f"可见钢筋表各行总重之和 ({sum_calc:.1f}kg) 与表底合计 ({tbl['reported_sum_kg']:.1f}kg) 不符",
                )

    if visible_bars:
        # Check every bar in the visible table has a callout tag outside the table bbox
        def _outside_tables(px: float, py: float) -> bool:
            return not any((b0 - 2.0 <= px <= b2 + 2.0 and b1 - 2.0 <= py <= b3 + 2.0) for b0, b1, b2, b3 in table_bboxes)

        callout_tokens = {s for s, tx, ty in texts if _outside_tables(tx, ty)}
        for bid in visible_bars:
            checks_run += 1
            if bid not in callout_tokens:
                _fail(
                    "MISSING_REBAR_CALLOUT_TAG",
                    expected=bid,
                    measured="missing",
                    delta=None,
                    message=f"钢筋表列出了编号 {bid}，但结构图面上未找到对应的 [{bid}] 引出标注",
                )

    # Map drawn REBAR cross-section dots to real coordinates inside each ViewBox
    dots_by_view: dict[str, list[tuple[float, float]]] = {}
    for px, py, _ in rebar_dots:
        vb_d = _find_enclosing_view(views, px, py, max_dist=10.0)
        if vb_d is not None:
            vid = str(vb_d["view_id"])
            sc = float(vb_d["scale_denom"])
            rx = (px - float(vb_d["origin_x"])) * sc
            ry = (py - float(vb_d["origin_y"])) * sc
            dots_by_view.setdefault(vid, []).append((rx, ry))

    def _check_dot_count(view_id: str, bar_id: str, actual_cnt: int, desc: str) -> None:
        nonlocal checks_run
        if bar_id not in visible_bars or visible_bars[bar_id]["count"] is None:
            return
        exp_cnt = int(visible_bars[bar_id]["count"])
        checks_run += 1
        if actual_cnt != exp_cnt:
            _fail(
                "REBAR_ENTITY_COUNT_MISMATCH",
                view_id=view_id,
                expected=exp_cnt,
                measured=actual_cnt,
                delta=float(actual_cnt - exp_cnt),
                message=(
                    f"{desc} 钢筋编号 {bar_id} 可见材料表写 {exp_cnt} 根，"
                    f"但断面图实测仅绘制了 {actual_cnt} 根钢筋实体（差值 {actual_cnt - exp_cnt:+d} 根）"
                ),
            )

    if "rebar_t_girder_web" in views_by_id:
        pts_w = dots_by_view.get("rebar_t_girder_web", [])
        vb_w = views_by_id["rebar_t_girder_web"]
        vb_w_rect = vb_w.get("paper_bounds") or [0.0, 0.0, 0.0, 0.0]
        sc_w = float(vb_w["scale_denom"])
        oy_w = float(vb_w["origin_y"])
        poly_rys = [
            (py - oy_w) * sc_w
            for pts in polylines
            if len(pts) >= 8 and all(_rect_distance(vb_w_rect, p[0], p[1]) <= 4.0 for p in pts)
            for _, py in pts
        ]
        gh_est = max(poly_rys, default=1750.0)
        bot_cnt = sum(1 for _, ry in pts_w if ry < 220.0)
        top_cnt = sum(1 for _, ry in pts_w if ry > gh_est - 200.0)
        mid_cnt = sum(1 for _, ry in pts_w if 220.0 <= ry <= gh_est - 200.0)
        _check_dot_count("rebar_t_girder_web", "R1", bot_cnt, "主梁马蹄底部主筋")
        _check_dot_count("rebar_t_girder_web", "R2", top_cnt, "主梁顶板架立筋")
        _check_dot_count("rebar_t_girder_web", "R3", mid_cnt, "主梁腹板水平防裂筋")

    if "girder_web_elev" in views_by_id:
        vb_gwe = views_by_id["girder_web_elev"]
        gwe_b = vb_gwe.get("paper_bounds") or [0.0, 0.0, 0.0, 0.0]
        sc_gwe = float(vb_gwe["scale_denom"])
        ox_gwe = float(vb_gwe["origin_x"])
        stirrup_rxs = sorted(
            round((p0[0] - ox_gwe) * sc_gwe, 2)
            for p0, p1, lyr in lines_geom
            if lyr == "REBAR_STIRRUP"
            and abs(p0[0] - p1[0]) < 0.05
            and abs(p1[1] - p0[1]) >= 12.0
            and _rect_distance(gwe_b, p0[0], p0[1]) <= 3.0
        )
        sheet_joined = " ".join(s for s, _, _ in texts if s)
        if "@100" in sheet_joined and "@150" in sheet_joined:
            dense_rxs = [rx for rx in stirrup_rxs if rx <= 2100.0]
            std_rxs = [rx for rx in stirrup_rxs if rx > 2100.0]
            checks_run += 1
            if len(dense_rxs) < 2:
                _fail(
                    "STIRRUP_DRAWN_PITCH_MISMATCH",
                    view_id="girder_web_elev",
                    expected=100.0,
                    measured="missing",
                    delta=None,
                    message="主梁腹板立面图未绘制加密区 GJ1 竖向箍筋实体",
                )
            else:
                dense_dxs = [b_x - a_x for a_x, b_x in zip(dense_rxs[:-1], dense_rxs[1:])]
                avg_dense_mm = sum(dense_dxs) / len(dense_dxs)
                if any(abs(dx - 100.0) > 2.0 for dx in dense_dxs):
                    _fail(
                        "STIRRUP_DRAWN_PITCH_MISMATCH",
                        view_id="girder_web_elev",
                        expected=100.0,
                        measured=round(avg_dense_mm, 1),
                        delta=round(avg_dense_mm - 100.0, 1),
                        message=f"QL-09 加密区标注 @100，但图面实画箍筋间距为 {avg_dense_mm:.1f} mm（与 @100 标注不符）",
                    )
            if std_rxs:
                anchor_std = [dense_rxs[-1]] + std_rxs if dense_rxs else std_rxs
                std_dxs = [b_x - a_x for a_x, b_x in zip(anchor_std[:-1], anchor_std[1:])]
                avg_std_mm = sum(std_dxs) / len(std_dxs)
                checks_run += 1
                if any(abs(dx - 150.0) > 2.0 for dx in std_dxs):
                    _fail(
                        "STIRRUP_DRAWN_PITCH_MISMATCH",
                        view_id="girder_web_elev",
                        expected=150.0,
                        measured=round(avg_std_mm, 1),
                        delta=round(avg_std_mm - 150.0, 1),
                        message=f"QL-09 普通区标注 @150，但图面实画箍筋间距为 {avg_std_mm:.1f} mm（与 @150 标注不符）",
                    )
        if "GJ1" in visible_bars and visible_bars["GJ1"]["count"] is not None:
            exp_gj1_full = int(visible_bars["GJ1"]["count"])
            meas_gj1_full = 2 * len(stirrup_rxs)
            checks_run += 1
            if meas_gj1_full != exp_gj1_full:
                _fail(
                    "STIRRUP_DRAWN_COUNT_MISMATCH",
                    view_id="girder_web_elev",
                    expected=exp_gj1_full,
                    measured=meas_gj1_full,
                    delta=float(meas_gj1_full - exp_gj1_full),
                    message=(
                        f"QL-09 半跨立面实画箍筋 {len(stirrup_rxs)} 根（全梁对称折算 {meas_gj1_full} 根），"
                        f"与钢筋表 GJ1 根数 ({exp_gj1_full} 根) 不一致"
                    ),
                )

    if "rebar_t_girder_flange" in views_by_id or drawing_no == "QL-10":
        for req_vid in ("rebar_t_girder_flange", "wet_joint_rebar_detail"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_FLANGE_WET_JOINT_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"翼缘板及湿接缝配筋图缺少必要视图：{req_vid}",
                )
        if "rebar_t_girder_flange" in views_by_id:
            _check_dot_count("rebar_t_girder_flange", "W3", len(dots_by_view.get("rebar_t_girder_flange", [])), "翼缘板纵向分布筋")

    if "diaphragm_section" in views_by_id or drawing_no == "QL-11":
        for req_vid in ("rebar_diaphragm", "diaphragm_section", "end_diaphragm_section"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_DIAPHRAGM_REBAR_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"中/端横隔梁配筋图缺少必要视图：{req_vid}",
                )
        if "diaphragm_section" in views_by_id:
            pts_d = dots_by_view.get("diaphragm_section", [])
            _check_dot_count("diaphragm_section", "HG1", sum(1 for _, ry in pts_d if ry < 350.0), "横隔梁底筋")
            _check_dot_count("diaphragm_section", "HG2", sum(1 for _, ry in pts_d if ry > 900.0), "横隔梁顶筋")

    if "pier_cap_section" in views_by_id or drawing_no == "QL-16":
        for req_vid in ("rebar_pier_cap", "pier_cap_section", "pier_cap_end_detail"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_PIER_CAP_REBAR_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"桥墩盖梁配筋图缺少必要视图：{req_vid}",
                )
        if "pier_cap_section" in views_by_id:
            pts_c = dots_by_view.get("pier_cap_section", [])
            _check_dot_count("pier_cap_section", "GL1", sum(1 for _, ry in pts_c if ry > 700.0), "盖梁顶层负弯矩主筋")
            _check_dot_count("pier_cap_section", "GL2", sum(1 for _, ry in pts_c if ry <= 700.0), "盖梁底层正弯矩主筋")

    if "bearing_section" in views_by_id or drawing_no == "QL-12":
        for req_vid in ("bearing_section", "bearing_pedestal_detail"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_BEARING_PEDESTAL_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"支座及垫石构造图缺少必要视图：{req_vid}",
                )

    # 5b. Visible Prestressing Tendon Schedule Table + Duct CIRCLE Entity Verification (QL-08)
    if "tendon_mid_section" in views_by_id or "tendon_end_section" in views_by_id:
        tendon_by_view: dict[str, int] = {}
        for px, py, _ in tendon_dots:
            vb_t = _find_enclosing_view(views, px, py, max_dist=10.0)
            if vb_t is not None:
                vid_t = str(vb_t["view_id"])
                tendon_by_view[vid_t] = tendon_by_view.get(vid_t, 0) + 1
        vis_tendon_ids = sorted(
            {s for s, tx, ty in texts if tx >= 225.0 and ty >= 46.0 and re.fullmatch(r"N\d+", s)}
        )
        checks_run += 1
        if not vis_tendon_ids:
            _fail(
                "MISSING_VISIBLE_TENDON_SCHEDULE",
                expected="tendon_schedule_table",
                measured="missing",
                delta=None,
                message="预应力钢束图缺少可见的《预应力钢束数量及张拉参数表》",
            )
        else:
            exp_td_n = len(vis_tendon_ids)
            for td_vid, td_desc in (("tendon_mid_section", "跨中钢束断面"), ("tendon_end_section", "梁端锚固断面")):
                if td_vid in views_by_id:
                    meas_td_n = tendon_by_view.get(td_vid, 0)
                    checks_run += 1
                    if meas_td_n != exp_td_n:
                        _fail(
                            "TENDON_DUCT_COUNT_MISMATCH",
                            view_id=td_vid,
                            expected=exp_td_n,
                            measured=meas_td_n,
                            delta=float(meas_td_n - exp_td_n),
                            message=f"{td_desc} 实测绘制波纹管圆孔 ({meas_td_n}孔) 与可见钢束明细表 ({exp_td_n}束) 不符",
                        )

    if "pier_col_section" in views_by_id or drawing_no == "QL-17":
        for req_vid in ("pier_pile_elev", "pier_col_section", "bored_pile_section", "tie_beam_joint_detail", "tie_beam_section"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_SUBSTRUCTURE_REBAR_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"桥墩下部配筋图缺少必要的构件或系梁节点视图：{req_vid}",
                )
        if "pier_col_section" in views_by_id:
            _check_dot_count("pier_col_section", "DZ1", len(dots_by_view.get("pier_col_section", [])), "桥墩圆柱墩主筋")
        if "bored_pile_section" in views_by_id:
            _check_dot_count("bored_pile_section", "ZJ1", len(dots_by_view.get("bored_pile_section", [])), "钻孔灌注桩主筋")
        if "tie_beam_section" in views_by_id:
            _check_dot_count("tie_beam_section", "XL1", len(dots_by_view.get("tie_beam_section", [])), "桩顶横系梁主筋")
        # Enforce explicit counting scope declaration on QL-17 (single column / tie beam / pile vs whole pier!)
        ql17_joined = " ".join(s for s, _, _ in texts if s)
        has_mult_tbl_17 = any(t.get("has_unit_mult") for t in visible_tables)
        has_scope_tokens_17 = all(tok in ql17_joined for tok in ("单根墩柱", "单道系梁", "单根", "每墩"))
        checks_run += 1
        if not (has_mult_tbl_17 and has_scope_tokens_17):
            _fail(
                "MISSING_REBAR_UNIT_SCOPE_DECLARATION",
                expected="构件部位(单根墩柱/单道系梁/单根桩基) + 每墩件数/每墩重",
                measured=f"has_unit_mult={has_mult_tbl_17},has_scope_text={has_scope_tokens_17}",
                delta=None,
                message="QL-17 桥墩钢筋明细表未明确区分按单柱/单桩/单道系梁统计还是按整座双柱墩汇总",
            )

    if "abutment_side_rebar" in views_by_id or drawing_no == "QL-18":
        for req_vid in ("abutment_plan", "abutment_elev", "abutment_side_rebar", "abutment_cap_section", "abutment_joint_detail", "abutment_pile_section"):
            checks_run += 1
            if req_vid not in views_by_id:
                _fail(
                    "MISSING_ABUTMENT_REBAR_VIEW",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"桥台配筋图缺少必要的平面定位/立面/台帽/耳背墙节点/桩基视图：{req_vid}",
                )
        if "abutment_cap_section" in views_by_id:
            _check_dot_count("abutment_cap_section", "QT1", len(dots_by_view.get("abutment_cap_section", [])), "桥台台帽主筋")
        if "abutment_pile_section" in views_by_id:
            _check_dot_count("abutment_pile_section", "QT3", len(dots_by_view.get("abutment_pile_section", [])), "桥台钻孔桩主筋")
        # Verify A-A section cut in BOTH abutment_plan AND abutment_elev passes through a drawn bearing pedestal!
        if "abutment_cap_section" in views_by_id and "DS1" in visible_bars:
            for ped_vid, ped_h_min, ped_h_max, zh_v in (
                ("abutment_plan", 4.5, 8.5, "桥台平面图"),
                ("abutment_elev", 1.2, 3.2, "桥台正立面图"),
            ):
                if ped_vid not in views_by_id:
                    continue
                v_pv = views_by_id[ped_vid]
                pv_b = v_pv.get("paper_bounds") or [0.0, 0.0, 0.0, 0.0]
                ped_cxs = [
                    0.5 * (min(p[0] for p in pts) + max(p[0] for p in pts))
                    for pts in polylines
                    if len(pts) == 4
                    and all(_rect_distance(pv_b, p[0], p[1]) <= 2.0 for p in pts)
                    and 4.5 <= (max(p[0] for p in pts) - min(p[0] for p in pts)) <= 8.5
                    and ped_h_min <= (max(p[1] for p in pts) - min(p[1] for p in pts)) <= ped_h_max
                ]
                a_lbl_xs = [
                    tx for s, tx, ty in texts if s == "A" and _rect_distance(pv_b, tx, ty) <= 4.5
                ]
                cut_xs = [
                    p0[0]
                    for p0, p1, lyr in lines_geom
                    if lyr in {"FRAME", "DIM"}
                    and abs(p0[0] - p1[0]) < 0.05
                    and 2.0 <= abs(p0[1] - p1[1]) <= 6.5
                    and _rect_distance(pv_b, p0[0], p0[1]) <= 6.5
                    and any(abs(p0[0] - ax) <= 7.0 for ax in a_lbl_xs)
                ]
                checks_run += 1
                if not ped_cxs or not cut_xs:
                    _fail(
                        "SECTION_CUT_PEDESTAL_MISALIGNED",
                        view_id=ped_vid,
                        expected="pedestals+A-A_cut",
                        measured=f"pedestals={len(ped_cxs)},cuts={len(cut_xs)}",
                        delta=None,
                        message=f"{zh_v} 缺少所画支座垫石或 A-A 剖切线，无法核对 A-A 剖面垫石位置",
                    )
                else:
                    aa_cx = cut_xs[0]
                    min_ped_dist = min(abs(aa_cx - pcx) for pcx in ped_cxs)
                    if min_ped_dist > 1.8:
                        _fail(
                            "SECTION_CUT_PEDESTAL_MISALIGNED",
                            view_id=ped_vid,
                            expected=0.0,
                            measured=round(min_ped_dist, 2),
                            delta=min_ped_dist,
                            message=f"{zh_v} A-A 剖切线未穿过所画支座垫石（偏离最近垫石中心 {min_ped_dist:.2f} mm），但 A-A 断面画出了垫石与 DS1",
                        )

    # ------------------------------------------------------------------
    # 6. QL-13 / 14 Deck Ancillary Subdetail Completeness Verification
    # ------------------------------------------------------------------
    if drawing_no in {"QL-13", "14"} or "crash_barrier_detail" in views_by_id:
        for req_vid, zh_name in _REQUIRED_QL13_VIEWS:
            checks_run += 1
            v_info = views_by_id.get(req_vid)
            if v_info is None:
                _fail(
                    "MISSING_ANCILLARY_SUBDETAIL",
                    expected=req_vid,
                    measured="missing",
                    delta=None,
                    message=f"桥面附属构造图缺少开题报告要求的详图：{zh_name} ({req_vid})",
                )
                continue
            vb_rect = v_info.get("paper_bounds") or [0.0, 0.0, 0.0, 0.0]
            has_geom = any(
                _rect_distance(vb_rect, p[0], p[1]) <= 2.0 for pts in polylines for p in pts
            ) or any(_rect_distance(vb_rect, p0[0], p0[1]) <= 2.0 for p0, _, _ in lines_geom)
            has_dim = bool(dims_by_view.get(req_vid))
            if not has_geom or not has_dim:
                _fail(
                    "INCOMPLETE_ANCILLARY_SUBDETAIL",
                    view_id=req_vid,
                    expected="geometry+dimension",
                    measured=f"geom={has_geom},dim={has_dim}",
                    delta=None,
                    message=f"桥面附属详图 '{zh_name}' ({req_vid}) 缺少实体几何或控制尺寸标注",
                )

    # 6b. Title Block Scale vs Registered ViewBox Scale Consistency Check!
    if views:
        actual_scales = {int(round(float(v["scale_denom"]))) for v in views if "scale_denom" in v}
        for s_tb, tx_tb, ty_tb in texts:
            if s_tb.startswith("比例 ") and tx_tb >= 230.0 and ty_tb <= 46.0:
                tb_ratios = {int(m_r.group(1)) for m_r in re.finditer(r"1\s*:\s*(\d+)", s_tb)}
                if tb_ratios:
                    checks_run += 1
                    if tb_ratios != actual_scales:
                        _fail(
                            "TITLE_BLOCK_SCALE_CONFLICT",
                            expected=sorted(actual_scales),
                            measured=sorted(tb_ratios),
                            delta=None,
                            message=(
                                f"标题栏标注比例 {sorted(f'1:{r}' for r in tb_ratios)} 与图面实际子图比例 "
                                f"{sorted(f'1:{r}' for r in actual_scales)} 不一致（存在过期或遗漏比例）"
                            ),
                        )

    # ------------------------------------------------------------------
    # 7. Authoritative Calculation Report Cross-Checks & Failure Propagation
    # ------------------------------------------------------------------
    if calc_report is not None:
        joined_text = " | ".join(s for s, _, _ in texts if s)

        # 7a. Propagate failed structural checks from calc_report directly to affected sheets!
        for fc in calc_report.get("failed_checks", []):
            rel_dnos = set(fc.get("related_drawings") or []) | set(fc.get("legacy_related_drawings") or [])
            if drawing_no in rel_dnos:
                dem = fc.get("demand")
                allow = fc.get("allowable")
                delta_v = (float(dem) - float(allow)) if isinstance(dem, (int, float)) and isinstance(allow, (int, float)) else None
                checks_run += 1
                _fail(
                    "STRUCTURAL_DESIGN_CHECK_FAILED",
                    expected=allow if allow is not None else "passed",
                    measured=dem if dem is not None else "failed",
                    delta=delta_v,
                    message=f"关联结构验算未通过 [{fc.get('component')}]：{fc.get('message')}",
                )

        # 7b. Bearing sheet (QL-12) stress & pass/fail text vs bearing_checks
        if "bearing_checks" in calc_report and ("支座" in joined_text and "σc=" in joined_text):
            brg = calc_report["bearing_checks"]
            exp_sc = float(brg["compressive_stress_mpa"])
            m_sc = re.search(r"σc\s*=\s*(\d+(?:\.\d+)?)\s*MPa", joined_text)
            if m_sc:
                meas_sc = float(m_sc.group(1))
                checks_run += 1
                if abs(meas_sc - exp_sc) > 0.15:
                    _fail(
                        "BEARING_STRESS_TEXT_MISMATCH",
                        expected=exp_sc,
                        measured=meas_sc,
                        delta=meas_sc - exp_sc,
                        message=f"支座图压应力 σc={meas_sc:g}MPa 与计算书 {exp_sc:g}MPa 不符",
                    )
            if not brg.get("passed", True):
                checks_run += 1
                if "结论(合格)" in joined_text or ("合格" in joined_text and "不合格" not in joined_text):
                    _fail(
                        "BEARING_FALSE_PASS_ON_DRAWING",
                        expected="超限不合格",
                        measured="合格",
                        delta=None,
                        message="支座验算实际未通过，但图面仍标注为合格",
                    )

        # 7c. Tendon effective prestress & control stress check (QL-08)
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

        # 7d. Visible rebar schedule rows vs authoritative ledger (rejects tampering visible TEXT!)
        ledger_idx = ((calc_report.get("rebar_and_tendon_ledger") or {}).get("index_by_id")) or {}
        ref_ids = list((expected_spec or {}).get("referenced_ids") or [])
        if not ref_ids and visible_bars:
            ref_ids = [
                bid
                for bid, item in ledger_idx.items()
                if (item.get("sheet_drawing_no") == drawing_no or item.get("legacy_ql_no") == drawing_no) and "bar_id" in item
            ]
        if ref_ids:
            for req_bid in ref_ids:
                if req_bid in ledger_idx and "bar_id" in ledger_idx[req_bid]:
                    checks_run += 1
                    if req_bid not in visible_bars:
                        _fail(
                            "VISIBLE_REBAR_ROW_MISSING",
                            expected=req_bid,
                            measured="missing",
                            delta=None,
                            message=f"图面可见钢筋明细表缺少账本要求的钢筋编号 {req_bid}",
                        )
        for bid, vis_row in visible_bars.items():
            if bid in ledger_idx and "bar_id" in ledger_idx[bid]:
                auth = ledger_idx[bid]
                exp_cnt = int(auth["count"])
                meas_cnt = vis_row["count"]
                checks_run += 1
                if meas_cnt != exp_cnt:
                    _fail(
                        "REBAR_COUNT_LEDGER_MISMATCH",
                        expected=exp_cnt,
                        measured=meas_cnt if meas_cnt is not None else "invalid",
                        delta=float(meas_cnt - exp_cnt) if meas_cnt is not None else None,
                        message=f"钢筋编号 {bid} 图面可见明细表根数 ({meas_cnt}) 与计算配筋账本 ({exp_cnt}) 不一致",
                    )
                exp_dia = float(auth["dia_mm"])
                meas_dia = vis_row["dia_mm"]
                checks_run += 1
                if meas_dia is None or abs(meas_dia - exp_dia) > 0.2:
                    _fail(
                        "REBAR_DIA_LEDGER_MISMATCH",
                        expected=exp_dia,
                        measured=meas_dia if meas_dia is not None else "invalid",
                        delta=(meas_dia - exp_dia) if meas_dia is not None else None,
                        message=f"钢筋编号 {bid} 图面可见直径 ({meas_dia}) 与计算账本 ({exp_dia}mm) 不一致",
                    )
                exp_len_cm = float(auth["single_length_cm"])
                meas_len_cm = vis_row["single_cm"]
                checks_run += 1
                if meas_len_cm is None or abs(meas_len_cm - exp_len_cm) > 2.0:
                    _fail(
                        "REBAR_LENGTH_LEDGER_MISMATCH",
                        expected=exp_len_cm,
                        measured=meas_len_cm if meas_len_cm is not None else "invalid",
                        delta=(meas_len_cm - exp_len_cm) if meas_len_cm is not None else None,
                        message=f"钢筋编号 {bid} 图面可见单长 ({meas_len_cm}cm) 与计算账本 ({exp_len_cm}cm) 不一致",
                    )

        # 7e. Substructure rebar sheet (QL-17 / 19) component dimensions vs substructure_checks
        if ("pier_pile_elev" in views_by_id or drawing_no in {"QL-17", "19"}) and "substructure_checks" in calc_report:
            sub_c = calc_report["substructure_checks"]
            req_sub_dims = [
                ("墩柱直径", float(sub_c["pier_column"]["diameter_m"]) * 1000.0),
                ("墩柱高度", float(sub_c["pier_column"]["height_m"]) * 1000.0),
                ("钻孔桩直径", float(sub_c["bored_pile"]["diameter_m"]) * 1000.0),
                ("钻孔桩设计桩长", float(sub_c["bored_pile"]["design_length_m"]) * 1000.0),
            ]
            for d_name, req_mm in req_sub_dims:
                checks_run += 1
                if not any(abs(dv - req_mm) <= 2.0 for dv in dim_numeric_values):
                    _fail(
                        "SUBSTRUCTURE_DIM_CALC_MISMATCH",
                        expected=req_mm,
                        measured="missing_or_different",
                        delta=None,
                        message=f"桥墩桩基配筋图缺少或偏离计算书 {d_name} 尺寸 ({req_mm:g} mm)",
                    )

        # 7f. BOQ sheet (QL-02 / 02) visible quantities vs bill_of_quantities
        if drawing_no in {"QL-02", "02"} and "bill_of_quantities" in calc_report:
            txt_tokens = {s.strip() for s, _, _ in texts if s.strip()}
            for r_boq in calc_report["bill_of_quantities"].get("rows", []):
                q_str = f"{r_boq['quantity']}"
                checks_run += 1
                if q_str not in txt_tokens:
                    _fail(
                        "BOQ_VISIBLE_QUANTITY_MISMATCH",
                        expected=q_str,
                        measured="missing",
                        delta=None,
                        message=f"工程量汇总表未找到 '{r_boq['category']}' 的计算书数量 {q_str} {r_boq['unit']}",
                    )

        # 7g. Sheet Index completeness on QL-01 / 01 (co-reads calc_report['sheet_manifest']!)
        if drawing_no in {"QL-01", "01"} and "施工图目录" in joined_text:
            txt_tokens_01 = {s.strip() for s, _, _ in texts if s.strip()}
            manifest_entries = ((calc_report.get("sheet_manifest") or {}).get("entries")) or [
                {"drawing_no": f"QL-{i:02d}"} for i in range(1, 19)
            ]
            for e_man in manifest_entries:
                req_dno = str(e_man["drawing_no"])
                checks_run += 1
                if req_dno not in txt_tokens_01:
                    _fail(
                        "SHEET_INDEX_ENTRY_MISSING",
                        expected=req_dno,
                        measured="missing",
                        delta=None,
                        message=f"全套施工图目录表缺少清单要求的图号 {req_dno}",
                    )

        # 7h. Cross-sheet general construction dimensions on QL-14/15 (pier) & QL-15/16 (abutment)
        is_pier_gen = ("substructure_elev" in views_by_id and "pier_top_support_chain" in views_by_id) or drawing_no == "QL-14"
        is_abut_gen = ("substructure_elev" in views_by_id and not is_pier_gen) or (drawing_no in {"QL-15", "16"} and not is_pier_gen)
        if is_pier_gen and "substructure_checks" in calc_report:
            sub_14 = calc_report["substructure_checks"]
            cap_pos = calc_report.get("pier_cap_beam_1d", {}).get("column_positions_m") or [-2.6, 2.6]
            req_14_dims = [
                ("盖梁长", float(calc_report["pier_cap_beam_1d"]["cap_length_m"]) * 1000.0),
                ("盖梁宽", float(sub_14["cap_beam"].get("width_m", 1.60)) * 1000.0),
                ("墩距", round((float(cap_pos[-1]) - float(cap_pos[0])) * 1000.0, 1)),
                ("墩柱直径", float(sub_14["pier_column"]["diameter_m"]) * 1000.0),
                ("墩柱高度", float(sub_14["pier_column"]["height_m"]) * 1000.0),
                ("桩顶系梁高", 1200.0),
                ("钻孔桩直径", float(sub_14["bored_pile"]["diameter_m"]) * 1000.0),
            ]
            for d_name, req_mm in req_14_dims:
                checks_run += 1
                if not any(abs(dv - req_mm) <= 2.0 for dv in dim_numeric_values):
                    _fail(
                        "PIER_GENERAL_DIM_INCONSISTENT",
                        expected=req_mm,
                        measured="missing_or_different",
                        delta=None,
                        message=f"桥墩一般构造图缺少或与配筋图/计算书不一致的尺寸：{d_name} ({req_mm:g} mm)",
                    )

        if is_abut_gen and "project_summary" in calc_report and "substructure_elev" in views_by_id:
            deck_mm = float(calc_report["project_summary"]["deck_width_m"]) * 1000.0
            pile_d_15 = float(calc_report["substructure_checks"]["bored_pile"]["diameter_m"]) * 1000.0
            req_15_dims = [
                ("台帽长", deck_mm),
                ("台帽宽", 1800.0),
                ("背墙厚", 550.0),
                ("耳墙长", 2000.0),
                ("桩距", round(max(3600.0, deck_mm - 3600.0), 1)),
                ("桩径", pile_d_15),
            ]
            for d_name, req_mm in req_15_dims:
                checks_run += 1
                if not any(abs(dv - req_mm) <= 2.0 for dv in dim_numeric_values):
                    _fail(
                        "ABUTMENT_GENERAL_DIM_INCONSISTENT",
                        expected=req_mm,
                        measured="missing_or_different",
                        delta=None,
                        message=f"桥台一般构造图缺少或与桥台配筋图不一致的尺寸：{d_name} ({req_mm:g} mm)",
                    )

        # 7i. Shared Support Coordinate Chain (480 + 40 + 480 = 1000 mm) across Sheets 06/QL-06, 13/QL-12, 15/QL-14!
        sup_lay_v = calc_report.get("support_layout")
        if isinstance(sup_lay_v, Mapping) and (
            {"girder_pier_support_chain", "pier_bearing_chain", "pier_top_support_chain"} & set(views_by_id.keys())
        ):
            exp_d_end = float(sup_lay_v["beam_end_to_bearing_mm"])
            exp_gap = float(sup_lay_v["expansion_gap_mm"])
            exp_pitch = float(sup_lay_v["pier_bearing_row_pitch_mm"])
            for chain_lbl, exp_val in (
                ("梁端距支座中心距", exp_d_end),
                ("梁端伸缩缝宽", exp_gap),
                ("中墩双排支座纵向中心距", exp_pitch),
            ):
                checks_run += 1
                if not any(abs(dv - exp_val) <= 1.5 for dv in dim_numeric_values):
                    _fail(
                        "SUPPORT_CHAIN_DESYNC",
                        expected=exp_val,
                        measured="missing_or_different",
                        delta=None,
                        message=(
                            f"图面支承尺寸链缺少或偏离共用支承坐标 '{chain_lbl}={exp_val:g} mm' "
                            f"（要求图06/13/15共用 {sup_lay_v.get('pier_chain_expression')} mm）"
                        ),
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
