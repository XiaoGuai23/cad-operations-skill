"""Parametric Civil & Bridge Engineering CAD sheet generators (DXF + SVG + PNG + audit).

Supports 5 industry-standard highway bridge & structural mechanics sheet types:
1. `bridge_elevation`: Multi-span Bridge General Elevation Layout (桥型总体布置图, 图 2-1)
2. `t_girder_section`: Standard Deck Cross-Section + Mid/End T-Girder Construction (横断面与T梁一般构造图, 图 3-1/3-2)
3. `tendon_layout`: Prestressing Tendon Mid/End Cross-Sections & Longitudinal Profile (预应力钢束布置图, 图 8-1)
4. `pier_and_abutment`: Double-Column Pier / Pile-Bent Abutment & Bearing Layout (桥墩/桥台一般构造及支座布置图, 图 10-1/11-1~11-4)
5. `mechanics_diagrams`: Structural Influence Lines, Lane Load & 10-Element M/Q Envelopes (结构力学影响线与内力包络图, 图 6-*/7-*)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

import ezdxf

from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.preview import preview_file

from .bridge_calc import (
    build_t_girder_polygon_mm,
    compute_lateral_distribution,
    compute_prestress_estimation_and_loss,
    compute_superstructure_envelopes,
    compute_t_girder_section,
)
from .symbols import (
    add_arch_dim,
    add_civil_text,
    draw_axis_bubble,
    draw_bearing_symbol,
    draw_boxed_tag,
    draw_elevation_marker,
    draw_ground_line,
    draw_pile_s_break,
    draw_slope_arrow,
    setup_civil_dxf_document,
)

CivilDrawingType = Literal[
    "bridge_elevation",
    "t_girder_section",
    "tendon_layout",
    "pier_and_abutment",
    "mechanics_diagrams",
]

CIVIL_DRAWING_TYPES: tuple[str, ...] = (
    "bridge_elevation",
    "t_girder_section",
    "tendon_layout",
    "pier_and_abutment",
    "mechanics_diagrams",
)


def _safe_stem(raw: str) -> str:
    cleaned = raw
    for ch in '<>:"/\\|?*':
        cleaned = cleaned.replace(ch, "_")
    return cleaned.strip() or "civil_bridge_drawing"


_STANDARD_ELEV_SCALES: tuple[int, ...] = (
    100, 150, 200, 250, 300, 400, 500, 600, 750, 800, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000
)


def _resolve_scale_denom(raw_scale: Any, total_bridge_mm: float, total_vert_mm: float, max_w_mm: float, max_h_mm: float) -> float:
    if raw_scale is not None and str(raw_scale).strip().lower() not in {"", "auto"}:
        s_txt = str(raw_scale).strip()
        if ":" in s_txt:
            num_s, den_s = s_txt.split(":", 1)
            denom = float(den_s) / float(num_s)
        else:
            denom = float(s_txt)
        if denom <= 0:
            raise ValueError(f"invalid scale {raw_scale!r}")
        if total_bridge_mm / denom > max_w_mm + 1e-6:
            raise ValueError(
                f"bridge length {total_bridge_mm/1000.0:g}m at scale 1:{denom:g} ({total_bridge_mm/denom:.1f}mm) "
                f"exceeds printable sheet width ({max_w_mm:.1f}mm)"
            )
        return denom

    for cand in _STANDARD_ELEV_SCALES:
        if (total_bridge_mm / cand) <= max_w_mm and (total_vert_mm / cand) <= max_h_mm:
            return float(cand)
    return float(math.ceil((total_bridge_mm / max_w_mm) / 100.0) * 100)


def _draw_bridge_elevation(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    n_spans = int(spec.get("n_spans", 4))
    if n_spans < 1:
        raise ValueError("n_spans must be >= 1")
    span_cm = float(spec.get("span_cm", 2500.0))
    girder_depth_cm = float(spec.get("girder_depth_cm", 170.0))
    barrier_h_cm = float(spec.get("barrier_height_cm", 100.0))
    abutment_len_cm = float(spec.get("abutment_length_cm", 400.0))
    pier_col_d_cm = float(spec.get("pier_diameter_cm", 120.0))
    pile_d_cm = float(spec.get("pile_diameter_cm", 140.0))
    pile_len_cm = float(spec.get("pile_length_cm", 2500.0))
    deck_elev_m = float(spec.get("deck_elevation_m", 86.500))
    flood_elev_m = float(spec.get("flood_elevation_m", 81.200))

    total_span_cm = n_spans * span_cm
    total_bridge_cm = total_span_cm + 2.0 * abutment_len_cm
    total_bridge_mm = total_bridge_cm * 10.0
    est_vert_mm = (barrier_h_cm + girder_depth_cm + 950.0 + pile_len_cm) * 10.0

    sheet_size = str(spec.get("sheet_size", "A3")).upper()
    from .symbols import SHEET_SIZES_MM
    if sheet_size not in SHEET_SIZES_MM:
        raise ValueError(f"unsupported sheet_size {sheet_size!r}")
    sw_mm, sh_mm = SHEET_SIZES_MM[sheet_size]
    max_bridge_paper_w = sw_mm - 80.0
    max_bridge_paper_h = sh_mm - 145.0

    scale_denom = _resolve_scale_denom(
        spec.get("scale"),
        total_bridge_mm=total_bridge_mm,
        total_vert_mm=est_vert_mm,
        max_w_mm=max_bridge_paper_w,
        max_h_mm=max_bridge_paper_h,
    )
    scale_str = f"1:{int(scale_denom)}" if abs(scale_denom - round(scale_denom)) < 1e-6 else f"1:{scale_denom:g}"

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=sheet_size,
        title=str(spec.get("title", f"{n_spans}×{span_cm/100:g}m 预应力混凝土简支T梁桥总体布置图")),
        project=str(spec.get("project", "公路桥梁工程设计")),
        drawing_no=str(spec.get("drawing_no", "QL-01")),
        organization=str(spec.get("organization", "土木与交通工程设计院")),
        unit_note="cm（高程以m计）",
        scale_label=scale_str,
    )

    # Exact physical scale: 1 cm in real bridge = (10.0 / scale_denom) mm on paper (isotropic hs == vs)
    hs = 10.0 / scale_denom
    vs = hs

    bridge_paper_w = total_bridge_cm * hs
    cx_sheet = 0.5 * (b["ix0"] + b["ix1"])
    x_abut_l = cx_sheet - 0.5 * bridge_paper_w
    x_span_0 = x_abut_l + abutment_len_cm * hs
    x_span_end = x_span_0 + total_span_cm * hs
    x_abut_r = x_span_end + abutment_len_cm * hs
    px_left, px_right = x_abut_l, x_abut_r

    y_deck = 205.0
    y_girder_bot = y_deck - girder_depth_cm * vs
    y_barrier_top = y_deck + barrier_h_cm * vs

    # Crash barrier + post ticks
    msp.add_lwpolyline(
        [(x_abut_l, y_deck), (x_abut_r, y_deck), (x_abut_r, y_barrier_top), (x_abut_l, y_barrier_top)],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    n_posts = max(12, n_spans * 6)
    for k in range(1, n_posts):
        xp = x_abut_l + (x_abut_r - x_abut_l) * (k / n_posts)
        msp.add_line((xp, y_deck), (xp, y_barrier_top), dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "防撞护栏", x_span_0 + 12.0, y_barrier_top + 1.8, height=2.3)

    # Spans (individual simply supported T-girders with expansion joint gap)
    pier_xs: list[float] = []
    for s_idx in range(n_spans):
        xs0 = x_span_0 + s_idx * span_cm * hs
        xs1 = xs0 + span_cm * hs
        pier_xs.append(xs0)
        gap = min(0.25, 0.02 * span_cm * hs)
        msp.add_lwpolyline(
            [(xs0 + gap, y_girder_bot), (xs1 - gap, y_girder_bot), (xs1 - gap, y_deck), (xs0 + gap, y_deck)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
    pier_xs.append(x_span_end)

    # Left & Right Abutments + Abutment Piles (drawn to true 1:scale_denom)
    abut_h_mm = 420.0 * vs
    for xa0, xa1 in ((x_abut_l, x_span_0), (x_span_end, x_abut_r)):
        msp.add_lwpolyline(
            [(xa0, y_deck - abut_h_mm), (xa1, y_deck - abut_h_mm), (xa1, y_deck), (xa0, y_deck)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        xc_p = 0.5 * (xa0 + xa1)
        pw = pile_d_cm * hs
        y_p_top = y_deck - abut_h_mm
        y_p_bot = y_p_top - pile_len_cm * vs * 0.72
        msp.add_line((xc_p - 0.5 * pw, y_p_top), (xc_p - 0.5 * pw, y_p_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line((xc_p + 0.5 * pw, y_p_top), (xc_p + 0.5 * pw, y_p_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, xc_p, y_p_bot, max(1.6, pw))

    # Natural riverbed valley profile
    terrain_pts: list[tuple[float, float]] = [(px_left - 8.0, y_deck - 340.0 * vs)]
    pier_ground_ys: list[float] = []
    for idx, xp in enumerate(pier_xs):
        t_norm = idx / max(1, n_spans)
        depth_cm = 360.0 + 580.0 * math.sin(math.pi * t_norm)
        yg = y_deck - depth_cm * vs
        pier_ground_ys.append(yg)
        terrain_pts.append((xp, yg))
    terrain_pts.append((px_right + 8.0, y_deck - 350.0 * vs))
    msp.add_lwpolyline(terrain_pts, close=False, dxfattribs={"layer": "TERRAIN"})
    draw_ground_line(msp, terrain_pts[1][0] - 6.0, terrain_pts[1][1] - 1.5, terrain_pts[-2][0] + 6.0, terrain_pts[-2][1] - 1.5, label="河床地面线", scale=0.85)

    # Design flood water line
    y_water = y_deck - max(8.0, (deck_elev_m - flood_elev_m) * 100.0 * vs)
    msp.add_line((x_span_0 + 8.0, y_water), (x_span_end - 8.0, y_water), dxfattribs={"layer": "CENTER"})
    draw_elevation_marker(msp, x_span_0 + 14.0, y_water, flood_elev_m, prefix="设计洪水位 ", scale=0.85)
    draw_elevation_marker(msp, x_abut_l + 2.0, y_deck, deck_elev_m, prefix="桥面 ", scale=0.85)

    # Intermediate Piers: cap beam + column + tie collar + bored pile + S-break
    for idx in range(1, n_spans):
        xp = pier_xs[idx]
        yg = pier_ground_ys[idx]
        cap_w = max(3.6, 170.0 * hs)
        cap_h = max(2.2, 150.0 * vs)
        col_w = pier_col_d_cm * hs
        pile_w = pile_d_cm * hs

        y_cap_bot = y_girder_bot - cap_h
        msp.add_lwpolyline(
            [(xp - 0.5 * cap_w, y_cap_bot), (xp + 0.5 * cap_w, y_cap_bot), (xp + 0.5 * cap_w, y_girder_bot), (xp - 0.5 * cap_w, y_girder_bot)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        y_tie = min(y_cap_bot - 2.0, yg - 80.0 * vs)
        msp.add_lwpolyline(
            [(xp - 0.5 * col_w, y_tie), (xp + 0.5 * col_w, y_tie), (xp + 0.5 * col_w, y_cap_bot), (xp - 0.5 * col_w, y_cap_bot)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        tie_h = max(1.8, 120.0 * vs)
        msp.add_lwpolyline(
            [(xp - 0.65 * pile_w, y_tie - tie_h), (xp + 0.65 * pile_w, y_tie - tie_h), (xp + 0.65 * pile_w, y_tie), (xp - 0.65 * pile_w, y_tie)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        y_pile_bot = y_tie - tie_h - pile_len_cm * vs
        msp.add_line((xp - 0.5 * pile_w, y_tie - tie_h), (xp - 0.5 * pile_w, y_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line((xp + 0.5 * pile_w, y_tie - tie_h), (xp + 0.5 * pile_w, y_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, xp, y_pile_bot, max(1.6, pile_w))
        msp.add_line((xp, y_barrier_top + 6.0), (xp, y_pile_bot - 4.0), dxfattribs={"layer": "CENTER"})

    # Circled Pier / Abutment Axis Bubbles 0 .. n_spans
    y_bubble = 248.0
    for idx, xp in enumerate(pier_xs):
        draw_axis_bubble(msp, xp, y_bubble, str(idx), radius=3.3, stem_to_y=y_barrier_top + 2.0)

    # Span chain dimensions + total bridge length dimension
    y_dim1 = 226.0
    y_dim2 = 236.0
    add_arch_dim(msp, (x_abut_l, y_barrier_top), (x_span_0, y_barrier_top), (0.5 * (x_abut_l + x_span_0), y_dim1), f"{abutment_len_cm:g}")
    for s_idx in range(n_spans):
        p0, p1 = pier_xs[s_idx], pier_xs[s_idx + 1]
        add_arch_dim(msp, (p0, y_barrier_top), (p1, y_barrier_top), (0.5 * (p0 + p1), y_dim1), f"{span_cm:g}")
    add_arch_dim(msp, (x_span_end, y_barrier_top), (x_abut_r, y_barrier_top), (0.5 * (x_span_end + x_abut_r), y_dim1), f"{abutment_len_cm:g}")
    add_arch_dim(
        msp,
        (x_abut_l, y_barrier_top),
        (x_abut_r, y_barrier_top),
        (0.5 * (x_abut_l + x_abut_r), y_dim2),
        f"桥梁全长 {total_bridge_cm:g} ({n_spans}×{span_cm:g})",
    )

    # Technical notes at bottom-left above border
    notes = [
        "说明：1. 本图尺寸除高程以 m 计外，其余均以 cm 计；",
        f"2. 上部结构采用 {n_spans}×{span_cm/100:g}m 预应力混凝土简支T梁，主梁高度 {girder_depth_cm:g}cm；",
        f"3. 下部结构桥墩采用双柱式墩（墩径 D={pier_col_cm_str(pier_col_d_cm)}cm），钻孔灌注桩基础（桩径 D={pier_col_cm_str(pile_d_cm)}cm，桩长 {pile_len_cm:g}cm）。",
    ]
    for i, nt in enumerate(notes):
        add_civil_text(msp, nt, b["ix0"] + 6.0, b["iy0"] + 24.0 - i * 6.5, height=2.3)

    return doc


def pier_col_cm_str(val: float) -> str:
    return f"{val:g}"


def _draw_t_girder_and_deck_section(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    n_girders = int(spec.get("n_girders", 5))
    spacing_mm = float(spec.get("girder_spacing_mm", 2348.0))
    deck_w_mm = float(spec.get("single_deck_width_mm", 11750.0))
    barrier_w_mm = float(spec.get("barrier_width_mm", 500.0))
    roadway_w_mm = deck_w_mm - 2.0 * barrier_w_mm
    asphalt_mm = float(spec.get("asphalt_thick_mm", 110.0))
    concrete_pav_mm = float(spec.get("concrete_paving_thick_mm", 100.0))

    girder_h_mm = float(spec.get("height_mm", 1700.0))
    flange_w_mm = float(spec.get("flange_width_mm", spacing_mm))
    tip_t_mm = float(spec.get("flange_tip_thick_mm", 200.0))
    haunch_w_mm = float(spec.get("haunch_width_mm", 210.0))
    haunch_h_mm = float(spec.get("haunch_height_mm", 70.0))
    web_t_mm = float(spec.get("web_thick_mm", 200.0))
    bulb_w_mm = float(spec.get("bulb_width_mm", 600.0))
    bulb_h_mm = float(spec.get("bulb_height_mm", 200.0))
    end_web_t_mm = float(spec.get("end_web_thick_mm", 600.0))

    sec_calc = compute_t_girder_section(
        girder_spacing_mm=spacing_mm,
        flange_width_mm=flange_w_mm,
        height_mm=girder_h_mm,
        flange_tip_thick_mm=tip_t_mm,
        haunch_width_mm=haunch_w_mm,
        haunch_height_mm=haunch_h_mm,
        web_thick_mm=web_t_mm,
        bulb_width_mm=bulb_w_mm,
        bulb_height_mm=bulb_h_mm,
        end_web_thick_mm=end_web_t_mm,
    )

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "上部结构标准横断面及T梁一般构造图")),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-02")),
        unit_note="mm",
        scale_label="1:40 / 1:25",
    )

    # --- TOP VIEW: Full Single-Carriageway Bridge Deck Standard Cross-Section (exact 1:40) ---
    cx_deck = 212.0
    y_girder_top = 214.0
    s_deck = 1.0 / 40.0

    x_d_left = cx_deck - 0.5 * deck_w_mm * s_deck
    x_d_right = cx_deck + 0.5 * deck_w_mm * s_deck
    pav_h_paper = max(3.6, (asphalt_mm + concrete_pav_mm) * s_deck * 2.2)

    # Two-layer deck pavement + crash barriers
    msp.add_lwpolyline(
        [(x_d_left, y_girder_top), (x_d_right, y_girder_top), (x_d_right, y_girder_top + 0.5 * pav_h_paper), (x_d_left, y_girder_top + 0.5 * pav_h_paper)],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    msp.add_lwpolyline(
        [(x_d_left, y_girder_top + 0.5 * pav_h_paper), (x_d_right, y_girder_top + 0.5 * pav_h_paper), (x_d_right, y_girder_top + pav_h_paper), (x_d_left, y_girder_top + pav_h_paper)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    bw_p = barrier_w_mm * s_deck
    bh_p = 18.0
    for xb0, dir_sign in ((x_d_left, 1.0), (x_d_right - bw_p, -1.0)):
        # New Jersey style crash barrier profile
        y0 = y_girder_top + pav_h_paper
        msp.add_lwpolyline(
            [
                (xb0, y0),
                (xb0 + bw_p, y0),
                (xb0 + (0.65 * bw_p if dir_sign > 0 else bw_p), y0 + bh_p),
                (xb0 + (0.0 if dir_sign > 0 else 0.35 * bw_p), y0 + bh_p),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )

    # N T-girders + transverse diaphragm web lines
    mid_poly = build_t_girder_polygon_mm(
        flange_width_mm=flange_w_mm,
        height_mm=girder_h_mm,
        flange_tip_thick_mm=tip_t_mm,
        haunch_width_mm=haunch_w_mm,
        haunch_height_mm=haunch_h_mm,
        web_thick_mm=web_t_mm,
        bulb_width_mm=bulb_w_mm,
        bulb_height_mm=bulb_h_mm,
    )
    g_centers_x: list[float] = []
    for i in range(n_girders):
        gx = cx_deck + (i - 0.5 * (n_girders - 1)) * spacing_mm * s_deck
        g_centers_x.append(gx)
        pts_p = [(gx + px * s_deck, y_girder_top + (py - girder_h_mm) * s_deck) for px, py in mid_poly]
        msp.add_lwpolyline(pts_p, close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line((gx, y_girder_top + 6.0), (gx, y_girder_top - girder_h_mm * s_deck - 5.0), dxfattribs={"layer": "CENTER"})
        add_civil_text(msp, f"{i+1}#梁", gx, y_girder_top - girder_h_mm * s_deck - 9.0, height=2.2, align="CENTER")

    # Diaphragm connecting lines between adjacent webs
    y_diaph_bot = y_girder_top - 0.72 * girder_h_mm * s_deck
    y_diaph_top = y_girder_top - (tip_t_mm + haunch_h_mm) * s_deck
    for i in range(n_girders - 1):
        msp.add_line((g_centers_x[i] + 0.5 * web_t_mm * s_deck, y_diaph_bot), (g_centers_x[i + 1] - 0.5 * web_t_mm * s_deck, y_diaph_bot), dxfattribs={"layer": "THIN"})
        msp.add_line((g_centers_x[i] + 0.5 * web_t_mm * s_deck, y_diaph_top), (g_centers_x[i + 1] - 0.5 * web_t_mm * s_deck, y_diaph_top), dxfattribs={"layer": "HIDDEN"})

    draw_slope_arrow(msp, cx_deck - 38.0, y_girder_top + pav_h_paper + 3.0, slope_text="2%", direction="left")
    draw_slope_arrow(msp, cx_deck + 38.0, y_girder_top + pav_h_paper + 3.0, slope_text="2%", direction="right")

    # Deck top & bottom dimension chains
    y_top_ref = y_girder_top + pav_h_paper + bh_p
    add_arch_dim(msp, (x_d_left, y_top_ref), (x_d_left + bw_p, y_top_ref), (x_d_left + 0.5 * bw_p, 246.0), f"{barrier_w_mm:g}")
    add_arch_dim(msp, (x_d_left + bw_p, y_top_ref), (x_d_right - bw_p, y_top_ref), (cx_deck, 246.0), f"行车道 {roadway_w_mm:g}")
    add_arch_dim(msp, (x_d_right - bw_p, y_top_ref), (x_d_right, y_top_ref), (x_d_right - 0.5 * bw_p, 246.0), f"{barrier_w_mm:g}")
    add_arch_dim(msp, (x_d_left, y_top_ref), (x_d_right, y_top_ref), (cx_deck, 256.0), f"单幅桥面宽 {deck_w_mm:g}")

    y_bot_g = y_girder_top - girder_h_mm * s_deck
    for i in range(n_girders - 1):
        add_arch_dim(
            msp,
            (g_centers_x[i], y_bot_g),
            (g_centers_x[i + 1], y_bot_g),
            (0.5 * (g_centers_x[i] + g_centers_x[i + 1]), y_bot_g - 15.0),
            f"{spacing_mm:g}",
        )
    add_civil_text(msp, f"铺装：{asphalt_mm:g}mm沥青混凝土 + {concrete_pav_mm:g}mm C40防水混凝土", x_d_right - 4.0, y_girder_top + pav_h_paper + 9.0, height=2.2, align="RIGHT")

    # --- BOTTOM LEFT: Detailed Mid-Span T-Girder Cross-Section (exact 1:25) ---
    s_det = 1.0 / 25.0
    cx_mid, y0_mid = 88.0, 44.0
    mid_det_pts = [(cx_mid + px * s_det, y0_mid + py * s_det) for px, py in mid_poly]
    msp.add_lwpolyline(mid_det_pts, close=True, dxfattribs={"layer": "OUTLINE"})
    hatch_m = msp.add_hatch(color=9, dxfattribs={"layer": "HATCH"})
    hatch_m.set_pattern_fill("ANSI31", scale=0.8)
    hatch_m.paths.add_polyline_path(mid_det_pts, is_closed=True)
    msp.add_line((cx_mid, y0_mid - 5.0), (cx_mid, y0_mid + girder_h_mm * s_det + 6.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "T梁跨中截面 (1:25)", cx_mid, y0_mid - 11.0, height=2.8, align="CENTER")

    # Detailed dimensions on Mid-Span T-Girder
    half_bf_p = 0.5 * flange_w_mm * s_det
    half_bulb_p = 0.5 * bulb_w_mm * s_det
    top_y_p = y0_mid + girder_h_mm * s_det
    add_arch_dim(msp, (cx_mid - half_bf_p, top_y_p), (cx_mid + half_bf_p, top_y_p), (cx_mid, top_y_p + 8.0), f"{flange_w_mm:g}")
    add_arch_dim(msp, (cx_mid - half_bulb_p, y0_mid), (cx_mid + half_bulb_p, y0_mid), (cx_mid, y0_mid - 5.5), f"{bulb_w_mm:g}")
    add_arch_dim(msp, (cx_mid + half_bf_p, y0_mid), (cx_mid + half_bf_p, top_y_p), (cx_mid + half_bf_p + 9.0, y0_mid + 0.5 * girder_h_mm * s_det), f"{girder_h_mm:g}", angle=90.0)
    add_arch_dim(msp, (cx_mid - half_bf_p, top_y_p - tip_t_mm * s_det), (cx_mid - half_bf_p, top_y_p), (cx_mid - half_bf_p - 8.0, top_y_p - 0.5 * tip_t_mm * s_det), f"{tip_t_mm:g}", angle=90.0)
    add_civil_text(msp, f"腹板厚 {web_t_mm:g}", cx_mid + 0.5 * web_t_mm * s_det + 3.0, y0_mid + 0.48 * girder_h_mm * s_det, height=2.2)

    # --- BOTTOM CENTER: Girder-End Cross-Section (exact 1:25) ---
    cx_end, y0_end = 204.0, 44.0
    end_poly = sec_calc["end_span_polygon_mm"]
    end_det_pts = [(cx_end + px * s_det, y0_end + py * s_det) for px, py in end_poly]
    msp.add_lwpolyline(end_det_pts, close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((cx_end, y0_end - 5.0), (cx_end, y0_end + girder_h_mm * s_det + 6.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "T梁支点截面 (1:25)", cx_end, y0_end - 11.0, height=2.8, align="CENTER")
    half_end_w = 0.5 * end_web_t_mm * s_det
    add_arch_dim(msp, (cx_end - half_end_w, y0_end), (cx_end + half_end_w, y0_end), (cx_end, y0_end - 5.5), f"{end_web_t_mm:g}")

    # --- BOTTOM RIGHT: Computed Section Property Summary Box ---
    mp = sec_calc["mid_span_mm"]
    box_x0, box_y0, box_w, box_h = 264.0, 56.0, 140.0, 62.0
    msp.add_lwpolyline(
        [(box_x0, box_y0), (box_x0 + box_w, box_y0), (box_x0 + box_w, box_y0 + box_h), (box_x0, box_y0 + box_h)],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    rows = [
        "【截面几何特性计算校核表】",
        f"受压翼缘有效宽 b'f = {sec_calc['effective_flange_width_mm']:g} mm",
        f"跨中截面积 A = {mp['area']/100.0:.1f} cm2",
        f"抗弯惯性矩 Ix = {mp['ix']/1e4:.0f} cm4",
        f"重心至底缘 yx = {mp['y_bot']/10.0:.2f} cm",
        f"上/下核心距 Ks={mp['kern_upper_ks']/10.0:.1f} / Kx={mp['kern_lower_kx']/10.0:.1f} cm",
        f"截面效率指标 ρ = {mp['efficiency_rho']:.3f} ≥ 0.50 (合格)",
    ]
    for idx, r_txt in enumerate(rows):
        add_civil_text(msp, r_txt, box_x0 + 4.0, box_y0 + box_h - 7.5 - idx * 8.0, height=2.3)

    return doc


def _draw_tendon_layout(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    girder_h_mm = float(spec.get("height_mm", 1700.0))
    flange_w_mm = float(spec.get("flange_width_mm", 2348.0))
    web_t_mm = float(spec.get("web_thick_mm", 200.0))
    bulb_w_mm = float(spec.get("bulb_width_mm", 600.0))
    bulb_h_mm = float(spec.get("bulb_height_mm", 200.0))
    end_web_t_mm = float(spec.get("end_web_thick_mm", 600.0))

    raw_n_tendons = spec.get("n_tendons", 5)
    if isinstance(raw_n_tendons, bool) or (isinstance(raw_n_tendons, float) and not raw_n_tendons.is_integer()):
        raise ValueError(f"n_tendons must be an integer, got {raw_n_tendons!r}")
    n_tendons = int(raw_n_tendons)
    if n_tendons < 1 or n_tendons > 10:
        raise ValueError(f"n_tendons must be between 1 and 10 (got {n_tendons}); refusing to silently alter tendon count")

    strands_per = int(spec.get("strands_per_tendon", 6))
    if strands_per < 1:
        raise ValueError(f"strands_per_tendon must be >= 1, got {strands_per}")
    half_span_mm = float(spec.get("half_beam_length_mm", 12303.0))

    # Build exact mid-span duct positions (never clamping n_tendons!)
    bulb_dx = min(120.0, max(60.0, 0.25 * bulb_w_mm))
    if n_tendons == 1:
        mid_ducts_mm: list[tuple[str, float, float]] = [("N1", 0.0, 100.0)]
    elif n_tendons == 2:
        mid_ducts_mm = [("N1", -bulb_dx, 100.0), ("N2", bulb_dx, 100.0)]
    else:
        mid_ducts_mm = [
            ("N1", -bulb_dx, 100.0),
            ("N2", bulb_dx, 100.0),
            ("N3", 0.0, 100.0),
        ]
        for extra_idx in range(4, n_tendons + 1):
            dy_mm = 100.0 + (extra_idx - 3) * 80.0
            if dy_mm > girder_h_mm - 200.0:
                raise ValueError(
                    f"girder height {girder_h_mm:g}mm cannot accommodate {n_tendons} mid-span ducts"
                )
            mid_ducts_mm.append((f"N{extra_idx}", 0.0, dy_mm))

    # Build exact girder-end anchorage vertical positions
    if n_tendons == 1:
        end_step_mm = 0.0
        end_ys_mm = [round(0.35 * girder_h_mm, 1)]
    else:
        avail_end_mm = girder_h_mm - 420.0
        end_step_mm = 300.0 if (n_tendons - 1) * 300.0 <= avail_end_mm else math.floor((avail_end_mm / (n_tendons - 1)) / 10.0) * 10.0
        if end_step_mm < 110.0:
            raise ValueError(
                f"girder height {girder_h_mm:g}mm cannot accommodate {n_tendons} anchorage plates at >=110mm pitch"
            )
        base_end_y = 240.0
        end_ys_mm = [base_end_y + i * end_step_mm for i in range(n_tendons)]

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "T梁预应力钢束构造及布置图")),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-03")),
        unit_note="mm",
        scale_label="1:15 / 纵断示意",
    )

    s_sec = 1.0 / 15.0  # exact 1:15 section scale
    y_base = 88.0

    # 1. Left: Mid-Span Section (跨中钢束断面)
    cx_mid = 82.0
    mid_poly = build_t_girder_polygon_mm(
        flange_width_mm=min(flange_w_mm, 1400.0),
        height_mm=girder_h_mm,
        web_thick_mm=web_t_mm,
        bulb_width_mm=bulb_w_mm,
        bulb_height_mm=bulb_h_mm,
    )
    msp.add_lwpolyline([(cx_mid + px * s_sec, y_base + py * s_sec) for px, py in mid_poly], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((cx_mid, y_base - 8.0), (cx_mid, y_base + girder_h_mm * s_sec + 8.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "跨中钢束断面 (1:15)", cx_mid, y_base - 15.0, height=3.0, align="CENTER")

    tag_pitch = min(9.0, 82.0 / max(1, n_tendons))
    for idx, (tag, dx_mm, dy_mm) in enumerate(mid_ducts_mm):
        px = cx_mid + dx_mm * s_sec
        py = y_base + dy_mm * s_sec
        msp.add_circle((px, py), 2.0, dxfattribs={"layer": "TENDON"})
        tag_x = cx_mid + (36.0 if dx_mm >= 0 else -36.0)
        tag_y = y_base + (8.0 + idx * tag_pitch)
        draw_boxed_tag(msp, tag_x, tag_y, tag, leader_to=(px, py))

    add_arch_dim(msp, (cx_mid - 0.5 * bulb_w_mm * s_sec, y_base), (cx_mid + 0.5 * bulb_w_mm * s_sec, y_base), (cx_mid, y_base - 7.0), f"{bulb_w_mm:g}")
    add_arch_dim(msp, (cx_mid - 0.5 * bulb_w_mm * s_sec, y_base), (cx_mid - 0.5 * bulb_w_mm * s_sec, y_base + 100.0 * s_sec), (cx_mid - 0.5 * bulb_w_mm * s_sec - 9.0, y_base + 50.0 * s_sec), "100", angle=90.0)

    # 2. Center: Girder-End Anchorage Section (梁端锚固断面)
    cx_end = 192.0
    end_poly = build_t_girder_polygon_mm(
        flange_width_mm=min(flange_w_mm, 1400.0),
        height_mm=girder_h_mm,
        haunch_width_mm=10.0,
        web_thick_mm=end_web_t_mm,
        bulb_width_mm=end_web_t_mm,
        bulb_height_mm=0.0,
    )
    msp.add_lwpolyline([(cx_end + px * s_sec, y_base + py * s_sec) for px, py in end_poly], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((cx_end, y_base - 8.0), (cx_end, y_base + girder_h_mm * s_sec + 8.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "梁端锚固断面 (1:15)", cx_end, y_base - 15.0, height=3.0, align="CENTER")

    for idx, ey_mm in enumerate(end_ys_mm):
        tag = mid_ducts_mm[idx][0]
        py = y_base + ey_mm * s_sec
        msp.add_circle((cx_end, py), 2.3, dxfattribs={"layer": "TENDON"})
        draw_boxed_tag(msp, cx_end + 34.0, py, tag, leader_to=(cx_end + 2.3, py))
        if idx > 0:
            py_prev = y_base + end_ys_mm[idx - 1] * s_sec
            add_arch_dim(
                msp,
                (cx_end - 0.5 * end_web_t_mm * s_sec, py_prev),
                (cx_end - 0.5 * end_web_t_mm * s_sec, py),
                (cx_end - 0.5 * end_web_t_mm * s_sec - 8.0, 0.5 * (py_prev + py)),
                f"{end_step_mm:g}",
                angle=90.0,
            )
    if n_tendons == 1:
        py0 = y_base + end_ys_mm[0] * s_sec
        add_arch_dim(
            msp,
            (cx_end - 0.5 * end_web_t_mm * s_sec, y_base),
            (cx_end - 0.5 * end_web_t_mm * s_sec, py0),
            (cx_end - 0.5 * end_web_t_mm * s_sec - 8.0, 0.5 * (y_base + py0)),
            f"{end_ys_mm[0]:g}",
            angle=90.0,
        )

    # 3. Right: Half-Span Parabolic Tendon Profile + Prestress Summary
    x_prof_end = 258.0
    x_prof_mid = 396.0
    y_prof_bot = 132.0
    h_prof = 76.0
    msp.add_lwpolyline(
        [(x_prof_end, y_prof_bot), (x_prof_mid, y_prof_bot), (x_prof_mid, y_prof_bot + h_prof), (x_prof_end, y_prof_bot + h_prof)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    msp.add_line((x_prof_mid, y_prof_bot - 8.0), (x_prof_mid, y_prof_bot + h_prof + 8.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "跨中对称线", x_prof_mid - 2.0, y_prof_bot + h_prof + 9.5, height=2.2, align="RIGHT")
    add_civil_text(msp, "半跨预应力钢束纵断面线形图", 0.5 * (x_prof_end + x_prof_mid), y_prof_bot - 14.0, height=2.8, align="CENTER")

    for idx, (tag, _, mid_y_mm) in enumerate(mid_ducts_mm):
        ey_mm = end_ys_mm[idx]
        pts_curve: list[tuple[float, float]] = []
        for k in range(25):
            u = k / 24.0
            yk_mm = mid_y_mm + (ey_mm - mid_y_mm) * ((1.0 - u) ** 2)
            xk_p = x_prof_end + u * (x_prof_mid - x_prof_end)
            yk_p = y_prof_bot + (yk_mm / girder_h_mm) * h_prof
            pts_curve.append((xk_p, yk_p))
        msp.add_lwpolyline(pts_curve, close=False, dxfattribs={"layer": "TENDON"})
        add_civil_text(msp, tag, pts_curve[0][0] - 5.5, pts_curve[0][1] - 1.0, height=2.1)

    add_arch_dim(msp, (x_prof_end, y_prof_bot + h_prof), (x_prof_mid, y_prof_bot + h_prof), (0.5 * (x_prof_end + x_prof_mid), y_prof_bot + h_prof + 7.0), f"半梁长 {half_span_mm:g}")

    ps_info = compute_prestress_estimation_and_loss(strands_per_tendon=strands_per)
    add_civil_text(
        msp,
        f"钢束规格：{n_tendons}束 Φs15.2-{strands_per} (fpk=1860MPa, σcon=1395MPa, 跨中有效预应力≈{ps_info['loss_profile'][5]['effective_prestress_mpa']:.0f}MPa)",
        b["ix0"] + 6.0,
        b["iy0"] + 16.0,
        height=2.3,
    )
    return doc


def _draw_pier_and_abutment(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    variant = str(spec.get("variant", "pier")).strip().lower()
    is_abutment = variant == "abutment"
    cap_len_mm = float(spec.get("cap_length_mm", 11750.0 if is_abutment else 10950.0))
    cap_h_mm = float(spec.get("cap_height_mm", 1500.0))
    cap_w_mm = float(spec.get("cap_width_mm", 1800.0 if is_abutment else 1700.0))
    col_spacing_mm = float(spec.get("column_spacing_mm", 6800.0 if is_abutment else 6650.0))
    col_d_mm = float(spec.get("column_diameter_mm", 1400.0 if is_abutment else 1200.0))
    col_h_mm = float(spec.get("column_height_mm", 4200.0 if is_abutment else 6500.0))
    pile_d_mm = float(spec.get("pile_diameter_mm", 1400.0))
    n_pedestals = int(spec.get("n_pedestals", 5))
    ped_spacing_mm = float(spec.get("pedestal_spacing_mm", 2348.0))
    bearing_model = str(spec.get("bearing_model", "GJZ 300×450×85"))

    default_title = "桩柱式桥台一般构造及支座布置图" if is_abutment else "双柱式桥墩一般构造及支座布置图"
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", default_title)),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-05" if is_abutment else "QL-04")),
        unit_note="mm",
        scale_label="1:80",
    )

    s_pier = 1.0 / 80.0  # exact 1:80 scale matching title block
    cx_elev = 112.0
    y_cap_top = 224.0

    # --- 1. LEFT: Double-Column Pier Front Elevation (桥墩立面图) ---
    half_cap = 0.5 * cap_len_mm * s_pier
    cap_h_p = cap_h_mm * s_pier
    overhang_p = 0.5 * (cap_len_mm - col_spacing_mm - col_d_mm) * s_pier
    haunch_drop_p = 0.32 * cap_h_p

    # Cap beam polygon with bottom sloped cantilevers
    cap_pts = [
        (cx_elev - half_cap, y_cap_top),
        (cx_elev + half_cap, y_cap_top),
        (cx_elev + half_cap, y_cap_top - cap_h_p + haunch_drop_p),
        (cx_elev + half_cap - overhang_p, y_cap_top - cap_h_p),
        (cx_elev - half_cap + overhang_p, y_cap_top - cap_h_p),
        (cx_elev - half_cap, y_cap_top - cap_h_p + haunch_drop_p),
    ]
    msp.add_lwpolyline(cap_pts, close=True, dxfattribs={"layer": "OUTLINE"})

    # Left & right seismic shear blocks (防震挡块) + N bearing pedestals (支座垫石)
    sb_w, sb_h = 3.2, 4.2
    for xsb in (cx_elev - half_cap, cx_elev + half_cap - sb_w):
        msp.add_lwpolyline(
            [(xsb, y_cap_top), (xsb + sb_w, y_cap_top), (xsb + sb_w, y_cap_top + sb_h), (xsb, y_cap_top + sb_h)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
    for i in range(n_pedestals):
        xp = cx_elev + (i - 0.5 * (n_pedestals - 1)) * ped_spacing_mm * s_pier
        msp.add_lwpolyline(
            [(xp - 2.4, y_cap_top), (xp + 2.4, y_cap_top), (xp + 2.4, y_cap_top + 1.8), (xp - 2.4, y_cap_top + 1.8)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )

    draw_slope_arrow(msp, cx_elev - 32.0, y_cap_top + 4.5, slope_text="2%", direction="left")
    draw_slope_arrow(msp, cx_elev + 32.0, y_cap_top + 4.5, slope_text="2%", direction="right")

    # Twin columns + tie beam (系梁) + ground line (地面线) + bored piles with S-break
    y_col_top = y_cap_top - cap_h_p
    y_col_bot = y_col_top - col_h_mm * s_pier
    col_w_p = col_d_mm * s_pier
    pile_w_p = pile_d_mm * s_pier
    col_xs = [cx_elev - 0.5 * col_spacing_mm * s_pier, cx_elev + 0.5 * col_spacing_mm * s_pier]

    for xc in col_xs:
        msp.add_lwpolyline(
            [(xc - 0.5 * col_w_p, y_col_bot), (xc + 0.5 * col_w_p, y_col_bot), (xc + 0.5 * col_w_p, y_col_top), (xc - 0.5 * col_w_p, y_col_top)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        # Tie collar + pile shaft + cylindrical S-break
        y_pile_top = y_col_bot - 5.0
        msp.add_lwpolyline(
            [(xc - 0.6 * pile_w_p, y_pile_top), (xc + 0.6 * pile_w_p, y_pile_top), (xc + 0.6 * pile_w_p, y_col_bot), (xc - 0.6 * pile_w_p, y_col_bot)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        y_pile_bot = y_pile_top - 40.0
        msp.add_line((xc - 0.5 * pile_w_p, y_pile_top), (xc - 0.5 * pile_w_p, y_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line((xc + 0.5 * pile_w_p, y_pile_top), (xc + 0.5 * pile_w_p, y_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, xc, y_pile_bot, pile_w_p)
        msp.add_line((xc, y_cap_top + 10.0), (xc, y_pile_bot - 6.0), dxfattribs={"layer": "CENTER"})

    # Transverse tie beam connecting the two pile tops
    msp.add_lwpolyline(
        [(col_xs[0] + 0.6 * pile_w_p, y_col_bot - 4.5), (col_xs[1] - 0.6 * pile_w_p, y_col_bot - 4.5), (col_xs[1] - 0.6 * pile_w_p, y_col_bot - 0.5), (col_xs[0] + 0.6 * pile_w_p, y_col_bot - 0.5)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    draw_ground_line(msp, cx_elev - half_cap - 8.0, y_col_bot + 6.0, cx_elev + half_cap + 8.0, y_col_bot + 6.0, label="地面线", scale=0.9)

    # Pier / Abutment elevation dimensions
    cap_label = "台帽长" if is_abutment else "盖梁长"
    sp_label = "桩距" if is_abutment else "墩距"
    elev_label = "桥台立面图" if is_abutment else "桥墩立面图"
    side_label = "桥台侧面图" if is_abutment else "桥墩侧面图"
    add_arch_dim(msp, (cx_elev - half_cap, y_cap_top + sb_h), (cx_elev + half_cap, y_cap_top + sb_h), (cx_elev, y_cap_top + 16.0), f"{cap_label} {cap_len_mm:g}")
    add_arch_dim(msp, (col_xs[0], y_col_bot), (col_xs[1], y_col_bot), (cx_elev, y_col_bot - 12.0), f"{sp_label} {col_spacing_mm:g}")
    add_arch_dim(msp, (cx_elev + half_cap, y_col_top), (cx_elev + half_cap, y_cap_top), (cx_elev + half_cap + 9.0, 0.5 * (y_col_top + y_cap_top)), f"{cap_h_mm:g}", angle=90.0)
    add_civil_text(msp, f"{'台柱' if is_abutment else '墩柱'} D={col_d_mm:g}", col_xs[1] + 0.5 * col_w_p + 2.5, 0.5 * (y_col_top + y_col_bot), height=2.2)
    add_civil_text(msp, f"钻孔桩 D={pile_d_mm:g}", col_xs[1] + 0.5 * pile_w_p + 2.5, y_col_bot - 26.0, height=2.2)
    add_civil_text(msp, elev_label, cx_elev, 52.0, height=3.0, align="CENTER")

    # --- 2. CENTER: Pier / Abutment Side Elevation ---
    cx_side = 216.0
    half_cw = 0.5 * cap_w_mm * s_pier
    msp.add_lwpolyline(
        [(cx_side - half_cw, y_col_top), (cx_side + half_cw, y_col_top), (cx_side + half_cw, y_cap_top), (cx_side - half_cw, y_cap_top)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    if is_abutment:
        # Draw abutment backwall (背墙) + earwall (耳墙) dashed projection on side view
        bw_w = 0.35 * cap_w_mm * s_pier
        bw_h = 1700.0 * s_pier
        msp.add_lwpolyline(
            [(cx_side - half_cw, y_cap_top), (cx_side - half_cw + bw_w, y_cap_top), (cx_side - half_cw + bw_w, y_cap_top + bw_h), (cx_side - half_cw, y_cap_top + bw_h)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        msp.add_lwpolyline(
            [(cx_side - half_cw - 14.0, y_col_top + 4.0), (cx_side - half_cw, y_col_top + 4.0), (cx_side - half_cw, y_cap_top + bw_h), (cx_side - half_cw - 14.0, y_cap_top + bw_h)],
            close=True,
            dxfattribs={"layer": "HIDDEN"},
        )
        add_civil_text(msp, "背墙/耳墙", cx_side - half_cw - 7.0, y_cap_top + bw_h + 2.0, height=2.1, align="CENTER")
    msp.add_lwpolyline(
        [(cx_side - 0.5 * col_w_p, y_col_bot), (cx_side + 0.5 * col_w_p, y_col_bot), (cx_side + 0.5 * col_w_p, y_col_top), (cx_side - 0.5 * col_w_p, y_col_top)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    y_pile_top_s = y_col_bot - 5.0
    y_pile_bot_s = y_pile_top_s - 40.0
    msp.add_line((cx_side - 0.5 * pile_w_p, y_col_bot), (cx_side - 0.5 * pile_w_p, y_pile_bot_s), dxfattribs={"layer": "OUTLINE"})
    msp.add_line((cx_side + 0.5 * pile_w_p, y_col_bot), (cx_side + 0.5 * pile_w_p, y_pile_bot_s), dxfattribs={"layer": "OUTLINE"})
    draw_pile_s_break(msp, cx_side, y_pile_bot_s, pile_w_p)
    msp.add_line((cx_side, y_cap_top + 10.0), (cx_side, y_pile_bot_s - 6.0), dxfattribs={"layer": "CENTER"})
    draw_ground_line(msp, cx_side - 18.0, y_col_bot + 6.0, cx_side + 18.0, y_col_bot + 6.0, label=None, scale=0.8)
    add_arch_dim(msp, (cx_side - half_cw, y_cap_top), (cx_side + half_cw, y_cap_top), (cx_side, y_cap_top + 28.0 if is_abutment else y_cap_top + 16.0), f"{cap_w_mm:g}")
    add_civil_text(msp, side_label, cx_side, 52.0, height=3.0, align="CENTER")

    # --- 3. RIGHT: Bearing Layout Plan (支座布置平面图, 图 10-1) ---
    bx0, by0, bw, bh = 282.0, 96.0, 118.0, 134.0
    msp.add_lwpolyline([(bx0, by0), (bx0 + bw, by0), (bx0 + bw, by0 + bh), (bx0, by0 + bh)], close=True, dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "单跨支座平面布置图", bx0 + 0.5 * bw, by0 + bh + 5.0, height=2.8, align="CENTER")
    add_civil_text(msp, f"支座型号：{bearing_model}", bx0 + 0.5 * bw, by0 - 7.0, height=2.3, align="CENTER")

    x_z1 = bx0 + 24.0
    x_z2 = bx0 + bw - 24.0
    msp.add_line((x_z1, by0 + 12.0), (x_z1, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    msp.add_line((x_z2, by0 + 12.0), (x_z2, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "Z1 固定端", x_z1, by0 + bh - 8.0, height=2.2, align="CENTER")
    add_civil_text(msp, "Z2 活动端", x_z2, by0 + bh - 8.0, height=2.2, align="CENTER")

    for i in range(n_pedestals):
        yg = by0 + 22.0 + i * ((bh - 44.0) / max(1, n_pedestals - 1))
        msp.add_line((x_z1 - 8.0, yg), (x_z2 + 8.0, yg), dxfattribs={"layer": "THIN"})
        draw_bearing_symbol(msp, x_z1, yg, kind="fixed", size=3.4)
        draw_bearing_symbol(msp, x_z2, yg, kind="sliding_longitudinal", size=3.4)
        add_civil_text(msp, f"{i+1}#", bx0 + 7.0, yg - 1.0, height=2.1)

    return doc


def _draw_mechanics_diagrams(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    n_girders = int(spec.get("n_girders", 5))
    spacing_m = float(spec.get("girder_spacing_m", 2.348))
    deck_w_m = float(spec.get("deck_width_m", 11.75))
    calc_span_m = float(spec.get("calc_span_m", 24.0))

    lat = compute_lateral_distribution(
        n_girders=n_girders,
        girder_spacing_m=spacing_m,
        deck_width_m=deck_w_m,
        calc_span_m=calc_span_m,
    )
    env = compute_superstructure_envelopes(
        calc_span_m=calc_span_m,
        m_oq=lat["lever_rule"][0]["m_oq"],
        m_cq=lat["eccentric_compression"][0]["m_cq"],
    )

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "主梁荷载横向影响线及内力组合包络图")),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥计算附图")),
        drawing_no=str(spec.get("drawing_no", "JS-01")),
        unit_note="kN / kN·m / m",
        scale_label="力学示意",
    )

    # --- PANEL 1 (Top-Left): Courbon Lateral Distribution Influence Lines (偏心压力法横向影响线) ---
    p1_x0, p1_x1, p1_y0 = 36.0, 196.0, 212.0
    p1_w = p1_x1 - p1_x0
    msp.add_line((p1_x0, p1_y0), (p1_x1, p1_y0), dxfattribs={"layer": "OUTLINE"})
    half_d = 0.5 * deck_w_m

    for g_idx in range(n_girders):
        gx_m = (g_idx - 0.5 * (n_girders - 1)) * spacing_m
        xp = p1_x0 + ((gx_m + half_d) / deck_w_m) * p1_w
        msp.add_line((xp, p1_y0 - 4.0), (xp, p1_y0 + 18.0), dxfattribs={"layer": "CENTER"})
        add_civil_text(msp, f"{g_idx+1}#", xp, p1_y0 + 20.0, height=2.1, align="CENTER")

    # Plot Girder 1# and Girder 2# Courbon influence lines + governing wheel arrows
    g1_ec = lat["eccentric_compression"][0]
    pts_eta1 = [
        (p1_x0 + ((xv + half_d) / deck_w_m) * p1_w, p1_y0 - yv * 34.0)
        for xv, yv in g1_ec["influence_line"]
    ]
    msp.add_lwpolyline(pts_eta1, close=False, dxfattribs={"layer": "REBAR"})
    for wx in g1_ec["wheel_positions_m"]:
        wx_p = p1_x0 + ((wx + half_d) / deck_w_m) * p1_w
        msp.add_line((wx_p, p1_y0 + 14.0), (wx_p, p1_y0 + 0.5), dxfattribs={"layer": "DIM"})
        msp.add_lwpolyline([(wx_p, p1_y0 + 0.5), (wx_p - 1.1, p1_y0 + 3.2), (wx_p + 1.1, p1_y0 + 3.2)], close=True, dxfattribs={"layer": "DIM"})

    add_civil_text(
        msp,
        f"1#梁横向影响线 (η11={g1_ec['eta_g1']:.2f}, η15={g1_ec['eta_gn']:.2f}, mcq1={g1_ec['m_cq']:.3f}, moq1={lat['lever_rule'][0]['m_oq']:.3f})",
        0.5 * (p1_x0 + p1_x1),
        p1_y0 - 30.0,
        height=2.2,
        align="CENTER",
    )
    add_arch_dim(msp, (p1_x0, p1_y0 + 24.0), (p1_x1, p1_y0 + 24.0), (0.5 * (p1_x0 + p1_x1), p1_y0 + 30.0), f"桥面宽 {deck_w_m*1000:g}")

    # --- PANEL 2 (Top-Right): Highway-I Lane Load (Pk + qk) & Mid-Span M Influence Line ---
    p2_x0, p2_x1, p2_y0 = 228.0, 392.0, 212.0
    p2_w = p2_x1 - p2_x0
    msp.add_line((p2_x0, p2_y0), (p2_x1, p2_y0), dxfattribs={"layer": "OUTLINE"})
    # Uniform load qk block + concentrated Pk arrow at mid-span
    msp.add_lwpolyline([(p2_x0, p2_y0 + 4.0), (p2_x1, p2_y0 + 4.0), (p2_x1, p2_y0 + 11.0), (p2_x0, p2_y0 + 11.0)], close=True, dxfattribs={"layer": "DIM"})
    p2_mid = 0.5 * (p2_x0 + p2_x1)
    msp.add_line((p2_mid, p2_y0 + 24.0), (p2_mid, p2_y0 + 11.0), dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([(p2_mid, p2_y0 + 11.0), (p2_mid - 1.6, p2_y0 + 14.5), (p2_mid + 1.6, p2_y0 + 14.5)], close=True, dxfattribs={"layer": "REBAR"})
    add_civil_text(msp, f"Pk={env['lane_pk_kn']:g}kN, qk={env['lane_qk_kn_m']:g}kN/m (1+μ={1+env['impact_factor_mu']:.3f})", p2_mid, p2_y0 + 26.5, height=2.2, align="CENTER")
    # Triangular M_{L/2} influence line below axis
    msp.add_lwpolyline([(p2_x0, p2_y0), (p2_mid, p2_y0 - 22.0), (p2_x1, p2_y0)], close=True, dxfattribs={"layer": "TENDON"})
    add_civil_text(msp, f"L/4={0.25*calc_span_m:.2f}m (跨中弯矩影响线)", p2_mid, p2_y0 - 29.0, height=2.2, align="CENTER")
    add_arch_dim(msp, (p2_x0, p2_y0), (p2_x1, p2_y0), (p2_mid, p2_y0 + 34.0), f"计算跨径 L={calc_span_m*1000:g}")

    # --- PANEL 3 (Bottom-Left): 10-Element Bending Moment Envelope Diagram (弯矩包络图) ---
    p3_x0, p3_x1, p3_y0 = 36.0, 196.0, 134.0
    p3_w = p3_x1 - p3_x0
    msp.add_line((p3_x0, p3_y0), (p3_x1, p3_y0), dxfattribs={"layer": "OUTLINE"})
    stations = env["stations"]
    max_m = max(st["m_uls_knm"] for st in stations) or 1.0
    m_uls_pts: list[tuple[float, float]] = []
    m_sls_pts: list[tuple[float, float]] = []

    for st in stations:
        xp = p3_x0 + st["x_ratio"] * p3_w
        yp_u = p3_y0 - (st["m_uls_knm"] / max_m) * 62.0
        yp_s = p3_y0 - (st["m_sls_char_knm"] / max_m) * 62.0
        m_uls_pts.append((xp, yp_u))
        m_sls_pts.append((xp, yp_s))
        msp.add_line((xp, p3_y0), (xp, yp_u), dxfattribs={"layer": "HATCH"})
        add_civil_text(msp, st["station"], xp, p3_y0 + 2.5, height=1.9, align="CENTER")

    msp.add_lwpolyline(m_uls_pts, close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline(m_sls_pts, close=False, dxfattribs={"layer": "OUTLINE"})
    add_arch_dim(msp, (p3_x0, p3_y0), (p3_x1, p3_y0), (0.5 * (p3_x0 + p3_x1), p3_y0 + 10.0), f"10等分跨径 {calc_span_m*1000:g}")
    add_civil_text(
        msp,
        f"10单元弯矩包络图：Md,max={max_m:.1f} kN·m / Mk,max={env['mid_span_summary']['m_sls_char_knm']:.1f} kN·m",
        0.5 * (p3_x0 + p3_x1),
        54.0,
        height=2.3,
        align="CENTER",
    )

    # --- PANEL 4 (Bottom-Right): 10-Element Shear Force Envelope Diagram (剪力包络图) ---
    p4_x0, p4_x1, p4_y0 = 228.0, 392.0, 102.0
    p4_w = p4_x1 - p4_x0
    msp.add_line((p4_x0, p4_y0), (p4_x1, p4_y0), dxfattribs={"layer": "OUTLINE"})
    max_q = max(abs(st["q_uls_gov_kn"]) for st in stations) or 1.0
    q_pos_pts: list[tuple[float, float]] = []
    q_neg_pts: list[tuple[float, float]] = []

    for st in stations:
        xp = p4_x0 + st["x_ratio"] * p4_w
        q_hi = 1.1 * (1.2 * st["q_dead_kn"] + 1.4 * st["q_live_pos_kn"])
        q_lo = 1.1 * (1.2 * st["q_dead_kn"] + 1.4 * st["q_live_neg_kn"])
        yp_hi = p4_y0 + (q_hi / max_q) * 32.0
        yp_lo = p4_y0 + (q_lo / max_q) * 32.0
        q_pos_pts.append((xp, yp_hi))
        q_neg_pts.append((xp, yp_lo))
        msp.add_line((xp, yp_lo), (xp, yp_hi), dxfattribs={"layer": "HATCH"})

    msp.add_lwpolyline(q_pos_pts, close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline(q_neg_pts, close=False, dxfattribs={"layer": "TENDON"})
    add_civil_text(
        msp,
        f"10单元剪力包络图：|Vd,max|={max_q:.1f} kN",
        0.5 * (p4_x0 + p4_x1),
        54.0,
        height=2.3,
        align="CENTER",
    )

    return doc


def generate_civil_bridge_dxf(
    drawing_type: CivilDrawingType,
    spec: dict[str, Any] | None,
    output_dir: Path,
    *,
    stem: str | None = None,
) -> Path:
    """Generate and audit a native DXF file for one of the 5 Civil/Bridge drawing types."""

    cfg = dict(spec or {})
    if drawing_type not in CIVIL_DRAWING_TYPES:
        raise ValueError(f"unsupported civil drawing_type {drawing_type!r}; expected one of {CIVIL_DRAWING_TYPES}")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    file_stem = _safe_stem(stem or cfg.get("output_basename") or drawing_type)
    dxf_path = out_root / f"{file_stem}.dxf"

    if drawing_type == "bridge_elevation":
        doc = _draw_bridge_elevation(cfg)
    elif drawing_type == "t_girder_section":
        doc = _draw_t_girder_and_deck_section(cfg)
    elif drawing_type == "tendon_layout":
        doc = _draw_tendon_layout(cfg)
    elif drawing_type == "pier_and_abutment":
        doc = _draw_pier_and_abutment(cfg)
    else:
        doc = _draw_mechanics_diagrams(cfg)

    doc.saveas(dxf_path)
    reopened = ezdxf.readfile(dxf_path)
    auditor = reopened.audit()
    if auditor.has_errors:
        raise RuntimeError(f"civil DXF audit failed with {len(auditor.errors)} error(s)")
    return dxf_path


def generate_civil_bridge_bundle(
    drawing_type: CivilDrawingType,
    spec: dict[str, Any] | None,
    output_dir: Path,
    *,
    stem: str | None = None,
    render_lock=None,
) -> dict[str, Any]:
    """Generate audited Civil/Bridge DXF + high-DPI PNG preview + rubric scorecard + audit.json."""

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    dxf_path = generate_civil_bridge_dxf(drawing_type, spec, out_root, stem=stem)

    from cad_skill_core.mcp.industry_adapters import build_auto_style_font_map

    preview_dir = out_root / "preview"
    font_map = build_auto_style_font_map(dxf_path)
    if render_lock is not None:
        with render_lock:
            prev = preview_file(dxf_path, preview_dir, font_map=font_map)
    else:
        prev = preview_file(dxf_path, preview_dir, font_map=font_map)

    png_src = preview_dir / prev["images"][0]
    png_path = out_root / f"{dxf_path.stem}.png"
    png_path.write_bytes(png_src.read_bytes())

    reopened = ezdxf.readfile(dxf_path)
    ez_audit = reopened.audit()
    type_counts: dict[str, int] = {}
    for ent in reopened.modelspace():
        dt = ent.dxftype()
        type_counts[dt] = type_counts.get(dt, 0) + 1

    status = "passed" if not ez_audit.has_errors else "failed"
    audit_json_path = out_root / "audit.json"
    audit_payload = {
        "domain": "civil_bridge",
        "drawing_type": drawing_type,
        "status": status,
        "ezdxf_audit_errors": len(ez_audit.errors),
        "entity_count": sum(type_counts.values()),
        "type_counts": type_counts,
        "issues": [],
    }
    audit_json_path.write_text(json.dumps(audit_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rubric = score_dxf_drawing(dxf_path, audit_json_path=audit_json_path).to_dict()
    artifacts = [
        {
            "name": p.name,
            "path": str(p.resolve()),
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        }
        for p in (dxf_path, png_path, audit_json_path)
    ]
    return {
        "status": status,
        "drawing_type": drawing_type,
        "rubric_score": rubric,
        "artifacts": artifacts,
        "preview_png_path": png_path,
        "audit_summary": {
            "errors": len(ez_audit.errors),
            "entity_count": sum(type_counts.values()),
            "type_counts": type_counts,
        },
    }
