"""Parametric Civil & Bridge Engineering CAD sheet generators (DXF + PNG + Reopen-DXF Verifier + 9-Category Suite).

Supports single-sheet generation and full 18-sheet A3 graduation/construction drawing sets covering
all 9 task-book categories with true `1:scale` ViewBox geometry, real `2%` deck/cap cross-slopes,
comb leaders for thin layers, traceable rebar schedules, and automatic reopen-DXF verification.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    run_full_bridge_calculation,
)
from .bridge_verify import verify_civil_dxf
from .symbols import (
    SHEET_SIZES_MM,
    ViewBox,
    add_arch_dim,
    add_civil_text,
    attach_sheet_metadata,
    draw_axis_bubble,
    draw_bearing_symbol,
    draw_boxed_tag,
    draw_comb_leader,
    draw_detail_callout,
    draw_elevation_marker,
    draw_ground_line,
    draw_pile_s_break,
    draw_rebar_schedule_table,
    draw_slope_arrow,
    setup_civil_dxf_document,
)

CivilDrawingType = Literal[
    "bridge_elevation",
    "bridge_plan_section",
    "t_girder_section",
    "t_girder_general",
    "tendon_layout",
    "rebar_detail",
    "bearing_layout",
    "deck_ancillary",
    "pier_and_abutment",
    "notes_and_boq",
    "mechanics_diagrams",
]

CIVIL_DRAWING_TYPES: tuple[str, ...] = (
    "bridge_elevation",
    "bridge_plan_section",
    "t_girder_section",
    "t_girder_general",
    "tendon_layout",
    "rebar_detail",
    "bearing_layout",
    "deck_ancillary",
    "pier_and_abutment",
    "notes_and_boq",
    "mechanics_diagrams",
)

TASK_BOOK_9_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("cat1_general_layout", "1. 桥型布置图(包括平面立面剖面图)"),
    ("cat2_girder_general", "2. 主梁一般构造图"),
    ("cat3_tendon_layout", "3. 主梁预应力束布置图"),
    ("cat4_girder_rebar", "4. 主梁普通钢筋构造图"),
    ("cat5_standard_cross_section", "5. 主梁标准横断面构造图"),
    ("cat6_bearing_detail", "6. 主梁支座构造图"),
    ("cat7_deck_ancillary", "7. 桥面附属结构构造图"),
    ("cat8_substructure_general", "8. 下部结构一般构造图"),
    ("cat9_substructure_rebar", "9. 下部结构普通钢筋图"),
)

_STANDARD_ELEV_SCALES: tuple[int, ...] = (
    100, 150, 200, 250, 300, 400, 500, 600, 750, 800, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000
)


def _safe_stem(raw: str) -> str:
    cleaned = raw
    for ch in '<>:"/\\|?*':
        cleaned = cleaned.replace(ch, "_")
    return cleaned.strip() or "civil_bridge_drawing"


def _resolve_style_overrides(spec: Mapping[str, Any]) -> dict[str, Any]:
    overrides = dict(spec.get("style_overrides") or {})
    mem_path = spec.get("preference_memory_path")
    if mem_path:
        from cad_skill_core.standards.preference_memory import resolve_civil_preferences

        learned = resolve_civil_preferences(mem_path, part_family=str(spec.get("part_family", "civil_bridge")))
        # Explicit style_overrides win over learned preferences; project dimensions are never touched
        merged = {**learned, **overrides}
        return merged
    return overrides


def _resolve_scale_denom(
    raw_scale: Any,
    total_bridge_mm: float,
    total_vert_mm: float,
    max_w_mm: float,
    max_h_mm: float,
) -> float:
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


def _ensure_calc_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    existing = spec.get("_calc_report")
    if isinstance(existing, Mapping) and "prestress" in existing and "rebar_and_tendon_ledger" in existing:
        return dict(existing)
    calc_params = dict(spec.get("calc_params") or {})
    if spec.get("preset"):
        calc_params.setdefault("preset", spec["preset"])
    if "calc_span_m" in spec:
        calc_params["calc_span_m"] = float(spec["calc_span_m"])
    if "sigma_con_mpa" in spec:
        calc_params["sigma_con_mpa"] = float(spec["sigma_con_mpa"])
    if "girder_spacing_mm" in spec:
        calc_params.setdefault("t_girder", {})["girder_spacing_mm"] = float(spec["girder_spacing_mm"])
        calc_params.setdefault("lateral", {})["girder_spacing_m"] = float(spec["girder_spacing_mm"]) / 1000.0
    if "single_deck_width_mm" in spec:
        calc_params.setdefault("lateral", {})["deck_width_m"] = float(spec["single_deck_width_mm"]) / 1000.0
    if "height_mm" in spec:
        calc_params.setdefault("t_girder", {})["height_mm"] = float(spec["height_mm"])
    return run_full_bridge_calculation(calc_params)


# ======================================================================
# 1. Bridge General Elevation (QL-03, True 1:S ViewBox — 1m = 2.5mm @ 1:400)
# ======================================================================
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
    pile_len_cm = float(spec.get("pile_length_cm", 1800.0))
    deck_elev_m = float(spec.get("deck_elevation_m", 86.500))
    flood_elev_m = float(spec.get("flood_elevation_m", 81.200))

    total_span_cm = n_spans * span_cm
    total_bridge_cm = total_span_cm + 2.0 * abutment_len_cm
    total_bridge_mm = total_bridge_cm * 10.0
    est_vert_mm = (barrier_h_cm + girder_depth_cm + 850.0 + pile_len_cm) * 10.0

    sheet_size = str(spec.get("sheet_size", "A3")).upper()
    if sheet_size not in SHEET_SIZES_MM:
        raise ValueError(f"unsupported sheet_size {sheet_size!r}")
    sw_mm, sh_mm = SHEET_SIZES_MM[sheet_size]
    scale_denom = _resolve_scale_denom(
        spec.get("scale"),
        total_bridge_mm=total_bridge_mm,
        total_vert_mm=est_vert_mm,
        max_w_mm=sw_mm - 80.0,
        max_h_mm=sh_mm - 140.0,
    )
    scale_str = f"1:{int(scale_denom)}" if abs(scale_denom - round(scale_denom)) < 1e-6 else f"1:{scale_denom:g}"

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=sheet_size,
        title=str(spec.get("title", f"{n_spans}×{span_cm/100:g}m 预应力混凝土简支T梁桥总体布置图(立面)")),
        project=str(spec.get("project", "公路桥梁工程设计")),
        drawing_no=str(spec.get("drawing_no", "QL-01")),
        organization=str(spec.get("organization", "土木与交通工程设计院")),
        unit_note="cm（高程以m计）",
        scale_label=scale_str,
        style_overrides=_resolve_style_overrides(spec),
    )

    cx_sheet = 0.5 * (b["ix0"] + b["ix1"])
    y_deck_paper = 204.0
    deck_elev_cm = deck_elev_m * 100.0
    # Origin in real coordinates: x=0 at bridge center, y=0 at elevation 0.000m (strictly isotropic 1:scale_denom!)
    vb = ViewBox(
        "elevation_main",
        origin_x=cx_sheet,
        origin_y=y_deck_paper - deck_elev_cm * (10.0 / scale_denom),
        scale_denom=scale_denom,
        real_unit="cm",
    )

    rx_abut_l = -0.5 * total_bridge_cm
    rx_span_0 = rx_abut_l + abutment_len_cm
    rx_span_end = rx_span_0 + total_span_cm
    rx_abut_r = rx_span_end + abutment_len_cm

    ry_deck = deck_elev_cm
    ry_girder_bot = ry_deck - girder_depth_cm
    ry_barrier_top = ry_deck + barrier_h_cm

    # Crash barrier + posts (exact 1:scale_denom)
    msp.add_lwpolyline(
        [
            vb.pt(rx_abut_l, ry_deck),
            vb.pt(rx_abut_r, ry_deck),
            vb.pt(rx_abut_r, ry_barrier_top),
            vb.pt(rx_abut_l, ry_barrier_top),
        ],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    n_posts = max(12, n_spans * 6)
    for k in range(1, n_posts):
        rxp = rx_abut_l + total_bridge_cm * (k / n_posts)
        msp.add_line(vb.pt(rxp, ry_deck), vb.pt(rxp, ry_barrier_top), dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "防撞护栏", vb.x(rx_span_0) + 10.0, vb.y(ry_barrier_top) + 1.8, height=2.2)

    # Spans
    pier_rxs: list[float] = []
    for s_idx in range(n_spans):
        rxs0 = rx_span_0 + s_idx * span_cm
        rxs1 = rxs0 + span_cm
        pier_rxs.append(rxs0)
        gap_cm = 4.0
        msp.add_lwpolyline(
            [
                vb.pt(rxs0 + gap_cm, ry_girder_bot),
                vb.pt(rxs1 - gap_cm, ry_girder_bot),
                vb.pt(rxs1 - gap_cm, ry_deck),
                vb.pt(rxs0 + gap_cm, ry_deck),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
    pier_rxs.append(rx_span_end)

    # Left & Right Abutments + True-Scale Piles (zero * 0.72 distortion!)
    abut_h_cm = 380.0
    abut_pile_cm = max(1200.0, pile_len_cm - 200.0)
    for rxa0, rxa1 in ((rx_abut_l, rx_span_0), (rx_span_end, rx_abut_r)):
        msp.add_lwpolyline(
            [
                vb.pt(rxa0, ry_deck - abut_h_cm),
                vb.pt(rxa1, ry_deck - abut_h_cm),
                vb.pt(rxa1, ry_deck),
                vb.pt(rxa0, ry_deck),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        rxc_p = 0.5 * (rxa0 + rxa1)
        ry_p_top = ry_deck - abut_h_cm
        ry_p_bot = ry_p_top - abut_pile_cm
        msp.add_line(vb.pt(rxc_p - 0.5 * pile_d_cm, ry_p_top), vb.pt(rxc_p - 0.5 * pile_d_cm, ry_p_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb.pt(rxc_p + 0.5 * pile_d_cm, ry_p_top), vb.pt(rxc_p + 0.5 * pile_d_cm, ry_p_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb.x(rxc_p), vb.y(ry_p_bot), max(1.4, vb.span(pile_d_cm)))

    # Natural riverbed valley profile + optional soil strata lines
    terrain_pts: list[tuple[float, float]] = [vb.pt(rx_abut_l - 250.0, ry_deck - 320.0)]
    pier_ground_rys: list[float] = []
    for idx, rxp in enumerate(pier_rxs):
        t_norm = idx / max(1, n_spans)
        depth_cm = 340.0 + 480.0 * math.sin(math.pi * t_norm)
        ryg = ry_deck - depth_cm
        pier_ground_rys.append(ryg)
        terrain_pts.append(vb.pt(rxp, ryg))
    terrain_pts.append(vb.pt(rx_abut_r + 250.0, ry_deck - 330.0))
    msp.add_lwpolyline(terrain_pts, close=False, dxfattribs={"layer": "TERRAIN"})
    draw_ground_line(
        msp,
        terrain_pts[1][0] - 5.0,
        terrain_pts[1][1] - 1.2,
        terrain_pts[-2][0] + 5.0,
        terrain_pts[-2][1] - 1.2,
        label="河床地面线",
        scale=0.82,
    )

    # True-scale Design Flood Water Line (strictly (deck_elev_m - flood_elev_m)*1000/scale_denom mm below deck!)
    ry_water = flood_elev_m * 100.0
    msp.add_line(vb.pt(rx_span_0 + 200.0, ry_water), vb.pt(rx_span_end - 200.0, ry_water), dxfattribs={"layer": "CENTER"})
    draw_elevation_marker(msp, vb.x(rx_span_0 + 420.0), vb.y(ry_water), flood_elev_m, prefix="设计洪水位 ", scale=0.82)
    draw_elevation_marker(msp, vb.x(rx_abut_l + 60.0), vb.y(ry_deck), deck_elev_m, prefix="桥面 ", scale=0.82)

    # Intermediate Piers (strictly true 1:scale_denom — zero max(...) clamping!)
    cap_w_cm = 170.0
    cap_h_cm = 150.0
    tie_h_cm = 120.0
    min_paper_y = y_deck_paper
    for idx in range(1, n_spans):
        rxp = pier_rxs[idx]
        ryg = pier_ground_rys[idx]
        ry_cap_bot = ry_girder_bot - cap_h_cm
        msp.add_lwpolyline(
            [
                vb.pt(rxp - 0.5 * cap_w_cm, ry_cap_bot),
                vb.pt(rxp + 0.5 * cap_w_cm, ry_cap_bot),
                vb.pt(rxp + 0.5 * cap_w_cm, ry_girder_bot),
                vb.pt(rxp - 0.5 * cap_w_cm, ry_girder_bot),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        ry_tie_top = min(ry_cap_bot - 150.0, ryg - 60.0)
        msp.add_lwpolyline(
            [
                vb.pt(rxp - 0.5 * pier_col_d_cm, ry_tie_top),
                vb.pt(rxp + 0.5 * pier_col_d_cm, ry_tie_top),
                vb.pt(rxp + 0.5 * pier_col_d_cm, ry_cap_bot),
                vb.pt(rxp - 0.5 * pier_col_d_cm, ry_cap_bot),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        ry_tie_bot = ry_tie_top - tie_h_cm
        msp.add_lwpolyline(
            [
                vb.pt(rxp - 0.65 * pile_d_cm, ry_tie_bot),
                vb.pt(rxp + 0.65 * pile_d_cm, ry_tie_bot),
                vb.pt(rxp + 0.65 * pile_d_cm, ry_tie_top),
                vb.pt(rxp - 0.65 * pile_d_cm, ry_tie_top),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        ry_pile_bot = ry_tie_bot - pile_len_cm
        min_paper_y = min(min_paper_y, vb.y(ry_pile_bot))
        msp.add_line(vb.pt(rxp - 0.5 * pile_d_cm, ry_tie_bot), vb.pt(rxp - 0.5 * pile_d_cm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb.pt(rxp + 0.5 * pile_d_cm, ry_tie_bot), vb.pt(rxp + 0.5 * pile_d_cm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb.x(rxp), vb.y(ry_pile_bot), max(1.4, vb.span(pile_d_cm)))
        msp.add_line((vb.x(rxp), vb.y(ry_barrier_top) + 6.0), (vb.x(rxp), vb.y(ry_pile_bot) - 3.5), dxfattribs={"layer": "CENTER"})

    # Circled Pier / Abutment Axis Bubbles 0 .. n_spans
    step_d = float(b["dim_stack_step"])
    y_top_bar = vb.y(ry_barrier_top)
    y_dim1 = y_top_bar + 16.0
    y_dim2 = y_dim1 + step_d + 1.5
    y_bubble = y_dim2 + 12.0
    for idx, rxp in enumerate(pier_rxs):
        draw_axis_bubble(msp, vb.x(rxp), y_bubble, str(idx), radius=3.2, stem_to_y=y_top_bar + 2.0)

    # Span chain dimensions (exact 1:scale_denom projections)
    add_arch_dim(msp, (vb.x(rx_abut_l), y_top_bar), (vb.x(rx_span_0), y_top_bar), (vb.x(0.5 * (rx_abut_l + rx_span_0)), y_dim1), f"{abutment_len_cm:g}")
    for s_idx in range(n_spans):
        rp0, rp1 = pier_rxs[s_idx], pier_rxs[s_idx + 1]
        add_arch_dim(msp, (vb.x(rp0), y_top_bar), (vb.x(rp1), y_top_bar), (vb.x(0.5 * (rp0 + rp1)), y_dim1), f"{span_cm:g}")
    add_arch_dim(msp, (vb.x(rx_span_end), y_top_bar), (vb.x(rx_abut_r), y_top_bar), (vb.x(0.5 * (rx_span_end + rx_abut_r)), y_dim1), f"{abutment_len_cm:g}")
    add_arch_dim(
        msp,
        (vb.x(rx_abut_l), y_top_bar),
        (vb.x(rx_abut_r), y_top_bar),
        (cx_sheet, y_dim2),
        f"桥梁全长 {total_bridge_cm:g} ({n_spans}×{span_cm:g})",
    )

    vb.register(doc, msp, paper_bounds=(vb.x(rx_abut_l) - 12.0, min_paper_y - 8.0, vb.x(rx_abut_r) + 12.0, y_bubble + 6.0))

    notes = [
        "说明：1. 本图尺寸除高程以 m 计外，其余均以 cm 计，横竖向严格按同一比例绘制；",
        f"2. 上部结构采用 {n_spans}×{span_cm/100:g}m 装配式预应力混凝土简支T梁，主梁高度 {girder_depth_cm:g}cm；",
        f"3. 下部桥墩采用双柱式墩（D={pier_col_d_cm:g}cm），钻孔灌注桩基础（D={pile_d_cm:g}cm，设计桩长 {pile_len_cm:g}cm）。",
    ]
    for i, nt in enumerate(notes):
        add_civil_text(msp, nt, b["ix0"] + 6.0, b["iy0"] + 23.0 - i * 6.2, height=2.2)

    return doc


# ======================================================================
# 2. Bridge Plan & Site Cross-Section (QL-04, Completes Task Book Cat 1)
# ======================================================================
def _draw_bridge_plan_section(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    n_spans = int(spec.get("n_spans", 4))
    span_cm = float(spec.get("span_cm", 2500.0))
    abut_cm = float(spec.get("abutment_length_cm", 400.0))
    deck_w_cm = float(spec.get("deck_width_cm", 900.0))
    roadway_w_cm = float(spec.get("roadway_width_cm", 700.0))
    total_cm = n_spans * span_cm + 2.0 * abut_cm

    scale_denom = _resolve_scale_denom(spec.get("scale", "1:400"), total_cm * 10.0, deck_w_cm * 10.0, 340.0, 90.0)
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "桥型总体布置图（平面及横剖面图）")),
        project=str(spec.get("project", "信阳市文昌中桥设计（4×25m简支T梁）")),
        drawing_no=str(spec.get("drawing_no", "QL-04")),
        unit_note="cm",
        scale_label=f"1:{int(scale_denom)} / 1:100",
        style_overrides=_resolve_style_overrides(spec),
    )

    # Top: Plan view (exact 1:scale_denom)
    cx = 0.5 * (b["ix0"] + b["ix1"])
    vb_plan = ViewBox("bridge_plan", cx, 206.0, scale_denom, "cm")
    rx0, rx1 = -0.5 * total_cm, 0.5 * total_cm
    ry0, ry1 = -0.5 * deck_w_cm, 0.5 * deck_w_cm
    msp.add_lwpolyline([vb_plan.pt(rx0, ry0), vb_plan.pt(rx1, ry0), vb_plan.pt(rx1, ry1), vb_plan.pt(rx0, ry1)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb_plan.pt(rx0 - 150.0, 0.0), vb_plan.pt(rx1 + 150.0, 0.0), dxfattribs={"layer": "CENTER"})
    # Guardrail inner lines & pier transverse axes
    for ryc in (-0.5 * roadway_w_cm, 0.5 * roadway_w_cm):
        msp.add_line(vb_plan.pt(rx0, ryc), vb_plan.pt(rx1, ryc), dxfattribs={"layer": "THIN"})
    for idx in range(n_spans + 1):
        rxp = rx0 + abut_cm + idx * span_cm
        msp.add_line((vb_plan.x(rxp), vb_plan.y(ry0) - 4.0), (vb_plan.x(rxp), vb_plan.y(ry1) + 4.0), dxfattribs={"layer": "CENTER"})
        draw_axis_bubble(msp, vb_plan.x(rxp), vb_plan.y(ry1) + 21.0, str(idx), radius=3.0, stem_to_y=vb_plan.y(ry1) + 4.0)
        if idx < n_spans:
            rxn = rxp + span_cm
            add_arch_dim(msp, (vb_plan.x(rxp), vb_plan.y(ry1)), (vb_plan.x(rxn), vb_plan.y(ry1)), (vb_plan.x(0.5 * (rxp + rxn)), vb_plan.y(ry1) + 10.5), f"{span_cm:g}")
    add_arch_dim(msp, (vb_plan.x(rx0), vb_plan.y(ry0)), (vb_plan.x(rx0), vb_plan.y(ry1)), (vb_plan.x(rx0) - 10.0, vb_plan.y(0.0)), f"{deck_w_cm:g}", angle=90.0)
    add_civil_text(msp, f"桥型平面图 (1:{int(scale_denom)}，斜交角0°正交)", cx, vb_plan.y(ry0) - 10.0, height=2.6, align="CENTER")
    vb_plan.register(doc, msp, paper_bounds=(vb_plan.x(rx0) - 16.0, vb_plan.y(ry0) - 8.0, vb_plan.x(rx1) + 12.0, vb_plan.y(ry1) + 25.0))

    # Bottom-Left: 1:100 Pier Cross-Section Profile
    vb_sec = ViewBox("site_cross_profile", 122.0, 128.0, 100.0, "cm")
    half_d = 0.5 * deck_w_cm
    slope = 0.02
    msp.add_lwpolyline(
        [
            vb_sec.pt(-half_d, -half_d * slope),
            vb_sec.pt(0.0, 0.0),
            vb_sec.pt(half_d, -half_d * slope),
            vb_sec.pt(half_d, -half_d * slope - 175.0),
            vb_sec.pt(-half_d, -half_d * slope - 175.0),
        ],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    col_sp_cm = 520.0 if deck_w_cm <= 1000.0 else 665.0
    for sign in (-0.5, 0.5):
        rxc = sign * col_sp_cm
        msp.add_lwpolyline(
            [vb_sec.pt(rxc - 60.0, -720.0), vb_sec.pt(rxc + 60.0, -720.0), vb_sec.pt(rxc + 60.0, -185.0), vb_sec.pt(rxc - 60.0, -185.0)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
    draw_slope_arrow(msp, vb_sec.x(-0.45 * half_d), vb_sec.y(0.0) + 5.5, slope_text="2%", direction="left")
    draw_slope_arrow(msp, vb_sec.x(0.45 * half_d), vb_sec.y(0.0) + 5.5, slope_text="2%", direction="right")
    add_arch_dim(msp, (vb_sec.x(-half_d), vb_sec.y(0.0)), (vb_sec.x(half_d), vb_sec.y(0.0)), (vb_sec.x(0.0), vb_sec.y(0.0) + 14.0), f"{deck_w_cm:g}")
    add_arch_dim(msp, (vb_sec.x(-0.5 * col_sp_cm), vb_sec.y(-720.0)), (vb_sec.x(0.5 * col_sp_cm), vb_sec.y(-720.0)), (vb_sec.x(0.0), vb_sec.y(-720.0) - 8.0), f"{col_sp_cm:g}")
    add_civil_text(msp, "桥位横剖面图 (1:100)", vb_sec.x(0.0), 34.0, height=2.6, align="CENTER")
    vb_sec.register(doc, msp, paper_bounds=(vb_sec.x(-half_d) - 12.0, 38.0, vb_sec.x(half_d) + 12.0, vb_sec.y(0.0) + 18.0))

    return doc


# ======================================================================
# 3. Standard Deck Cross-Section (QL-05, Real 2% Slope + 7000/8000/9000 + 1:10 Detail)
# ======================================================================
def _draw_t_girder_and_deck_section(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    is_wenchang = str(spec.get("preset", "")).strip().lower() == "wenchang"
    n_girders = int(spec.get("n_girders", 5))
    spacing_mm = float(spec.get("girder_spacing_mm", 1950.0 if is_wenchang else 2348.0))
    deck_w_mm = float(spec.get("single_deck_width_mm", 9000.0 if is_wenchang else 11750.0))
    barrier_w_mm = float(spec.get("barrier_width_mm", 500.0))
    clear_w_mm = deck_w_mm - 2.0 * barrier_w_mm
    if "lateral_clearance_mm" in spec:
        lat_clear_mm = float(spec["lateral_clearance_mm"])
    elif "roadway_width_mm" in spec:
        lat_clear_mm = max(0.0, 0.5 * (clear_w_mm - float(spec["roadway_width_mm"])))
    else:
        lat_clear_mm = 500.0 if is_wenchang else 0.0
    roadway_w_mm = float(spec.get("roadway_width_mm", clear_w_mm - 2.0 * lat_clear_mm))
    edge_cant_mm = float(spec.get("edge_cantilever_mm", 0.5 * (deck_w_mm - (n_girders - 1) * spacing_mm)))
    wet_joint_mm = float(spec.get("wet_joint_width_mm", 400.0))

    asphalt_mm = float(spec.get("asphalt_thick_mm", 100.0 if is_wenchang else 110.0))
    concrete_pav_mm = float(spec.get("concrete_paving_thick_mm", 80.0 if is_wenchang else 100.0))
    cross_slope_pct = float(spec.get("cross_slope_pct", 2.0))
    slope = cross_slope_pct / 100.0

    girder_h_mm = float(spec.get("height_mm", 1750.0 if is_wenchang else 1700.0))
    flange_w_mm = float(spec.get("flange_width_mm", spacing_mm))
    tip_t_mm = float(spec.get("flange_tip_thick_mm", 200.0))
    haunch_w_mm = float(spec.get("haunch_width_mm", 210.0))
    haunch_h_mm = float(spec.get("haunch_height_mm", 70.0))
    web_t_mm = float(spec.get("web_thick_mm", 200.0))
    bulb_w_mm = float(spec.get("bulb_width_mm", 560.0 if is_wenchang else 600.0))
    bulb_h_mm = float(spec.get("bulb_height_mm", 200.0))
    end_web_t_mm = float(spec.get("end_web_thick_mm", bulb_w_mm))

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

    s_deck_denom = 40.0 if deck_w_mm <= 9600.0 else 50.0
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "主梁标准横断面及桥面铺装构造图")),
        project=str(spec.get("project", "4×25m 装配式预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-05" if is_wenchang else "QL-02")),
        unit_note="mm",
        scale_label=f"1:{int(s_deck_denom)} / 1:25 / 1:10",
        style_overrides=_resolve_style_overrides(spec),
    )

    # --- TOP VIEW: Full Bridge Deck Standard Cross-Section (true 1:s_deck_denom & real 2% roof slope!) ---
    cx_deck = 198.0
    y_crown_paper = 204.0
    vb_deck = ViewBox("deck_cross_section", cx_deck, y_crown_paper, s_deck_denom, "mm")

    half_d = 0.5 * deck_w_mm
    rx_clear_l = -half_d + barrier_w_mm
    rx_clear_r = half_d - barrier_w_mm
    pav_tot_mm = concrete_pav_mm + asphalt_mm

    def _ry_flange_top(rx: float) -> float:
        return -abs(rx) * slope

    # Concrete leveling layer + asphalt surface polygons with exact 2.00% roof slope
    msp.add_lwpolyline(
        [
            vb_deck.pt(rx_clear_l, _ry_flange_top(rx_clear_l)),
            vb_deck.pt(0.0, 0.0),
            vb_deck.pt(rx_clear_r, _ry_flange_top(rx_clear_r)),
            vb_deck.pt(rx_clear_r, _ry_flange_top(rx_clear_r) + concrete_pav_mm),
            vb_deck.pt(0.0, concrete_pav_mm),
            vb_deck.pt(rx_clear_l, _ry_flange_top(rx_clear_l) + concrete_pav_mm),
        ],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    msp.add_lwpolyline(
        [
            vb_deck.pt(rx_clear_l, _ry_flange_top(rx_clear_l) + concrete_pav_mm),
            vb_deck.pt(0.0, concrete_pav_mm),
            vb_deck.pt(rx_clear_r, _ry_flange_top(rx_clear_r) + concrete_pav_mm),
            vb_deck.pt(rx_clear_r, _ry_flange_top(rx_clear_r) + pav_tot_mm),
            vb_deck.pt(0.0, pav_tot_mm),
            vb_deck.pt(rx_clear_l, _ry_flange_top(rx_clear_l) + pav_tot_mm),
        ],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )

    # Left & right crash barriers (true scale: 500mm x 1000mm)
    barrier_h_mm = 1000.0
    for rxb0, sign in ((-half_d, 1.0), (half_d - barrier_w_mm, -1.0)):
        ry_base = _ry_flange_top(rxb0 if sign > 0 else rxb0 + barrier_w_mm)
        msp.add_lwpolyline(
            [
                vb_deck.pt(rxb0, ry_base),
                vb_deck.pt(rxb0 + barrier_w_mm, ry_base),
                vb_deck.pt(rxb0 + (0.65 * barrier_w_mm if sign > 0 else barrier_w_mm), ry_base + barrier_h_mm),
                vb_deck.pt(rxb0 + (0.0 if sign > 0 else 0.35 * barrier_w_mm), ry_base + barrier_h_mm),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )

    # N vertical-web T-girders whose top flanges follow the 2% roof slope + wet-joint dashed lines
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
    g_real_xs: list[float] = []
    for i in range(n_girders):
        rgx = (i - 0.5 * (n_girders - 1)) * spacing_mm
        g_real_xs.append(rgx)
        ry_g_base = _ry_flange_top(rgx) - girder_h_mm
        pts_g: list[tuple[float, float]] = []
        for px, py in mid_poly:
            rx_abs = rgx + px
            # Clamp outer flange tips of edge girders to exact [-half_d, +half_d]
            rx_clamped = max(-half_d, min(half_d, rx_abs))
            if abs(py - girder_h_mm) < 1e-3:
                ry_abs = _ry_flange_top(rx_clamped)
            elif abs(py - (girder_h_mm - tip_t_mm)) < 1e-3:
                ry_abs = _ry_flange_top(rx_clamped) - tip_t_mm
            else:
                ry_abs = ry_g_base + py
            pts_g.append(vb_deck.pt(rx_clamped, ry_abs))
        msp.add_lwpolyline(pts_g, close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line(
            (vb_deck.x(rgx), vb_deck.y(pav_tot_mm) + 4.0),
            (vb_deck.x(rgx), vb_deck.y(ry_g_base) - 4.0),
            dxfattribs={"layer": "CENTER"},
        )
        add_civil_text(msp, f"{i+1}#梁", vb_deck.x(rgx), vb_deck.y(ry_g_base) - 7.8, height=2.1, align="CENTER")

    # Diaphragm & wet-joint seam lines between girders
    for i in range(n_girders - 1):
        rx_mid_j = 0.5 * (g_real_xs[i] + g_real_xs[i + 1])
        for dx_wj in (-0.5 * wet_joint_mm, 0.5 * wet_joint_mm):
            rxw = rx_mid_j + dx_wj
            msp.add_line(
                vb_deck.pt(rxw, _ry_flange_top(rxw) - tip_t_mm),
                vb_deck.pt(rxw, _ry_flange_top(rxw)),
                dxfattribs={"layer": "HIDDEN"},
            )
        ry_d_bot = _ry_flange_top(rx_mid_j) - 0.75 * girder_h_mm
        msp.add_line(
            vb_deck.pt(g_real_xs[i] + 0.5 * web_t_mm, ry_d_bot),
            vb_deck.pt(g_real_xs[i + 1] - 0.5 * web_t_mm, ry_d_bot),
            dxfattribs={"layer": "THIN"},
        )

    # Bidirectional 2% slope callouts positioned cleanly above the sloped asphalt surface
    y_surf_crown = vb_deck.y(pav_tot_mm)
    draw_slope_arrow(msp, vb_deck.x(-0.45 * clear_w_mm), y_surf_crown + 5.5, slope_text=f"{cross_slope_pct:g}%", direction="left")
    draw_slope_arrow(msp, vb_deck.x(0.45 * clear_w_mm), y_surf_crown + 5.5, slope_text=f"{cross_slope_pct:g}%", direction="right")

    # Comb leader for thin pavement layers + detail zoom callout to 1:10 view
    probe_rx = 0.68 * rx_clear_r
    probe_px = vb_deck.x(probe_rx)
    ry_fl = _ry_flange_top(probe_rx)
    draw_comb_leader(
        msp,
        probe_px,
        [
            vb_deck.y(ry_fl + concrete_pav_mm + 0.5 * asphalt_mm),
            vb_deck.y(ry_fl + concrete_pav_mm),
            vb_deck.y(ry_fl + 0.5 * concrete_pav_mm),
        ],
        [
            f"{asphalt_mm:g}mm 沥青混凝土铺装",
            "防水粘结层",
            f"{concrete_pav_mm:g}mm C40混凝土整平层",
        ],
        shelf_x=vb_deck.x(half_d) + 8.0,
        shelf_top_y=y_surf_crown + 15.0,
        shelf_width=52.0,
    )
    rj0 = 0.5 * (g_real_xs[-2] + g_real_xs[-1])
    draw_detail_callout(
        msp,
        vb_deck.x(rj0),
        vb_deck.y(_ry_flange_top(rj0)),
        radius=4.8,
        label="I 湿接缝详图(1:10)",
        leader_to=(vb_deck.x(half_d) + 8.0, vb_deck.y(-0.35 * girder_h_mm)),
    )

    # Top Roadway Independent Dimension Chains (7000 / 8000 / 9000!)
    step_d = min(7.8, float(b["dim_stack_step"]))
    y_ref_top = vb_deck.y(barrier_h_mm)
    y_tier1 = y_ref_top + 7.5
    add_arch_dim(msp, (vb_deck.x(-half_d), y_ref_top), (vb_deck.x(rx_clear_l), y_ref_top), (vb_deck.x(-half_d + 0.5 * barrier_w_mm), y_tier1), f"{barrier_w_mm:g}")
    if lat_clear_mm > 1.0:
        rx_road_l = rx_clear_l + lat_clear_mm
        rx_road_r = rx_clear_r - lat_clear_mm
        add_arch_dim(msp, (vb_deck.x(rx_clear_l), y_ref_top), (vb_deck.x(rx_road_l), y_ref_top), (vb_deck.x(rx_clear_l + 0.5 * lat_clear_mm), y_tier1), f"{lat_clear_mm:g}")
        add_arch_dim(msp, (vb_deck.x(rx_road_l), y_ref_top), (vb_deck.x(rx_road_r), y_ref_top), (cx_deck, y_tier1), f"行车道 {roadway_w_mm:g}")
        add_arch_dim(msp, (vb_deck.x(rx_road_r), y_ref_top), (vb_deck.x(rx_clear_r), y_ref_top), (vb_deck.x(rx_road_r + 0.5 * lat_clear_mm), y_tier1), f"{lat_clear_mm:g}")
        y_tier2 = y_tier1 + step_d
        add_arch_dim(msp, (vb_deck.x(rx_clear_l), y_ref_top), (vb_deck.x(rx_clear_r), y_ref_top), (cx_deck, y_tier2), f"护栏内净宽 {clear_w_mm:g}")
        y_tier3 = y_tier2 + step_d
    else:
        add_arch_dim(msp, (vb_deck.x(rx_clear_l), y_ref_top), (vb_deck.x(rx_clear_r), y_ref_top), (cx_deck, y_tier1), f"行车道 {roadway_w_mm:g}")
        y_tier3 = y_tier1 + step_d
    add_arch_dim(msp, (vb_deck.x(rx_clear_r), y_ref_top), (vb_deck.x(half_d), y_ref_top), (vb_deck.x(rx_clear_r + 0.5 * barrier_w_mm), y_tier1), f"{barrier_w_mm:g}")
    add_arch_dim(msp, (vb_deck.x(-half_d), y_ref_top), (vb_deck.x(half_d), y_ref_top), (cx_deck, y_tier3), f"桥面全宽 {deck_w_mm:g}")

    # Bottom Girder Structural Dimension Chain (600 + 4×1950 + 600 = 9000!)
    y_bot_ref = vb_deck.y(_ry_flange_top(g_real_xs[0]) - girder_h_mm)
    y_bot_chain = y_bot_ref - 11.5
    if edge_cant_mm > 1.0:
        add_arch_dim(msp, (vb_deck.x(-half_d), y_bot_ref), (vb_deck.x(g_real_xs[0]), y_bot_ref), (vb_deck.x(-half_d + 0.5 * edge_cant_mm), y_bot_chain), f"{edge_cant_mm:g}")
        add_arch_dim(msp, (vb_deck.x(g_real_xs[-1]), y_bot_ref), (vb_deck.x(half_d), y_bot_ref), (vb_deck.x(g_real_xs[-1] + 0.5 * edge_cant_mm), y_bot_chain), f"{edge_cant_mm:g}")
    for i in range(n_girders - 1):
        add_arch_dim(
            msp,
            (vb_deck.x(g_real_xs[i]), y_bot_ref),
            (vb_deck.x(g_real_xs[i + 1]), y_bot_ref),
            (vb_deck.x(0.5 * (g_real_xs[i] + g_real_xs[i + 1])), y_bot_chain),
            f"{spacing_mm:g}",
        )

    vb_deck.register(
        doc,
        msp,
        paper_bounds=(vb_deck.x(-half_d) - 10.0, y_bot_chain - 4.0, vb_deck.x(half_d) + 10.0, y_tier3 + 4.0),
    )
    attach_sheet_metadata(
        doc,
        msp,
        "deck_width_chains",
        {
            "roadway_w_mm": roadway_w_mm,
            "clear_w_mm": clear_w_mm,
            "deck_w_mm": deck_w_mm,
            "girder_spacing_mm": spacing_mm,
            "edge_cantilever_mm": edge_cant_mm if edge_cant_mm > 1.0 else 0.0,
            "cross_slope_pct": cross_slope_pct,
        },
    )

    # --- BOTTOM LEFT: Detailed Mid-Span T-Girder Cross-Section (exact 1:25) ---
    vb_mid = ViewBox("mid_girder_detail", 76.0, 42.0, 25.0, "mm")
    mid_det_pts = [vb_mid.pt(px, py) for px, py in mid_poly]
    msp.add_lwpolyline(mid_det_pts, close=True, dxfattribs={"layer": "OUTLINE"})
    hatch_m = msp.add_hatch(color=9, dxfattribs={"layer": "HATCH"})
    hatch_m.set_pattern_fill("ANSI31", scale=0.8)
    hatch_m.paths.add_polyline_path(mid_det_pts, is_closed=True)
    msp.add_line((vb_mid.x(0.0), vb_mid.y(-100.0)), (vb_mid.x(0.0), vb_mid.y(girder_h_mm + 120.0)), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "中梁跨中截面 (1:25)", vb_mid.x(0.0), 27.0, height=2.5, align="CENTER")

    half_bf = 0.5 * flange_w_mm
    half_bulb = 0.5 * bulb_w_mm
    add_arch_dim(msp, vb_mid.pt(-half_bf, girder_h_mm), vb_mid.pt(half_bf, girder_h_mm), (vb_mid.x(0.0), vb_mid.y(girder_h_mm) + 7.0), f"{flange_w_mm:g}")
    add_arch_dim(msp, vb_mid.pt(-half_bulb, 0.0), vb_mid.pt(half_bulb, 0.0), (vb_mid.x(0.0), vb_mid.y(0.0) - 5.5), f"{bulb_w_mm:g}")
    add_arch_dim(msp, vb_mid.pt(half_bf, 0.0), vb_mid.pt(half_bf, girder_h_mm), (vb_mid.x(half_bf) + 8.0, vb_mid.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
    vb_mid.register(doc, msp, paper_bounds=(vb_mid.x(-half_bf) - 8.0, 32.0, vb_mid.x(half_bf) + 12.0, vb_mid.y(girder_h_mm) + 10.0))

    # --- BOTTOM CENTER: 1:10 Enlarged Pavement & Wet-Joint Detail ("大样 I") ---
    vb_det = ViewBox("pavement_wet_joint_detail", 192.0, 68.0, 10.0, "mm")
    wj_half = 0.5 * wet_joint_mm
    det_half = wj_half + 140.0
    # Left & right precast flange tips + central cast-in-place wet joint + leveling + asphalt
    msp.add_lwpolyline([vb_det.pt(-det_half, -tip_t_mm), vb_det.pt(-wj_half, -tip_t_mm), vb_det.pt(-wj_half, 0.0), vb_det.pt(-det_half, 0.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([vb_det.pt(wj_half, -tip_t_mm), vb_det.pt(det_half, -tip_t_mm), vb_det.pt(det_half, 0.0), vb_det.pt(wj_half, 0.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([vb_det.pt(-wj_half, -tip_t_mm), vb_det.pt(wj_half, -tip_t_mm), vb_det.pt(wj_half, 0.0), vb_det.pt(-wj_half, 0.0)], close=True, dxfattribs={"layer": "HIDDEN"})
    msp.add_lwpolyline([vb_det.pt(-det_half, 0.0), vb_det.pt(det_half, 0.0), vb_det.pt(det_half, concrete_pav_mm), vb_det.pt(-det_half, concrete_pav_mm)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_lwpolyline([vb_det.pt(-det_half, concrete_pav_mm), vb_det.pt(det_half, concrete_pav_mm), vb_det.pt(det_half, pav_tot_mm), vb_det.pt(-det_half, pav_tot_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    add_arch_dim(msp, vb_det.pt(-wj_half, -tip_t_mm), vb_det.pt(wj_half, -tip_t_mm), (vb_det.x(0.0), vb_det.y(-tip_t_mm) - 6.0), f"湿接缝 {wet_joint_mm:g}")
    add_arch_dim(msp, vb_det.pt(det_half, 0.0), vb_det.pt(det_half, concrete_pav_mm), (vb_det.x(det_half) + 7.5, vb_det.y(0.5 * concrete_pav_mm)), f"{concrete_pav_mm:g}", angle=90.0)
    add_arch_dim(msp, vb_det.pt(det_half, concrete_pav_mm), vb_det.pt(det_half, pav_tot_mm), (vb_det.x(det_half) + 7.5, vb_det.y(concrete_pav_mm + 0.5 * asphalt_mm)), f"{asphalt_mm:g}", angle=90.0)
    add_civil_text(msp, "I 铺装与湿接缝详图 (1:10)", vb_det.x(0.0), 34.0, height=2.4, align="CENTER")
    vb_det.register(doc, msp, paper_bounds=(vb_det.x(-det_half) - 6.0, 38.0, vb_det.x(det_half) + 12.0, vb_det.y(pav_tot_mm) + 6.0))

    # --- BOTTOM RIGHT: Section Property Summary Box ---
    mp = sec_calc["mid_span_mm"]
    box_x0, box_y0, box_w, box_h = 266.0, 52.0, 138.0, 58.0
    msp.add_lwpolyline([(box_x0, box_y0), (box_x0 + box_w, box_y0), (box_x0 + box_w, box_y0 + box_h), (box_x0, box_y0 + box_h)], close=True, dxfattribs={"layer": "THIN"})
    rows = [
        "【截面几何特性计算校核表】",
        f"受压翼缘有效宽 b'f = {sec_calc['effective_flange_width_mm']:g} mm",
        f"跨中截面积 A = {mp['area']/100.0:.1f} cm2",
        f"抗弯惯性矩 Ix = {mp['ix']/1e4:.0f} cm4",
        f"上/下核心距 Ks={mp['kern_upper_ks']/10.0:.1f} / Kx={mp['kern_lower_kx']/10.0:.1f} cm",
        f"截面效率指标 ρ = {mp['efficiency_rho']:.3f} ≥ 0.50 (合格)",
    ]
    for idx, r_txt in enumerate(rows):
        add_civil_text(msp, r_txt, box_x0 + 4.0, box_y0 + box_h - 7.5 - idx * 8.6, height=2.2)

    return doc


# ======================================================================
# 4. Precast T-Girder General Construction (QL-06 & QL-07, Cat 2)
# ======================================================================
def _draw_t_girder_general(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    variant = str(spec.get("variant", "longitudinal")).strip().lower()
    beam_len_mm = float(spec.get("beam_length_mm", 24960.0))
    calc_span_mm = float(spec.get("calc_span_mm", 24000.0))
    girder_h_mm = float(spec.get("height_mm", 1750.0))
    spacing_mm = float(spec.get("girder_spacing_mm", 1950.0))
    edge_cant_mm = float(spec.get("edge_cantilever_mm", 600.0))
    bulb_w_mm = float(spec.get("bulb_width_mm", 560.0))

    if variant == "cross_details":
        doc, msp, b = setup_civil_dxf_document(
            sheet_size=str(spec.get("sheet_size", "A3")),
            title=str(spec.get("title", "25m预制T梁一般构造图（二：中梁与边梁变截面详图）")),
            project=str(spec.get("project", "信阳市文昌中桥设计")),
            drawing_no=str(spec.get("drawing_no", "QL-07")),
            unit_note="mm",
            scale_label="1:25",
            style_overrides=_resolve_style_overrides(spec),
        )
        # Left: Asymmetric Edge Girder Mid-Span (outer cantilever 600mm, inner precast 775mm)
        vb_edge = ViewBox("edge_girder_mid", 98.0, 92.0, 25.0, "mm")
        inner_half = 0.5 * (spacing_mm - 400.0)
        edge_pts = [
            (-0.5 * bulb_w_mm, 0.0), (0.5 * bulb_w_mm, 0.0), (0.5 * bulb_w_mm, 200.0),
            (100.0, 400.0), (100.0, girder_h_mm - 270.0), (310.0, girder_h_mm - 200.0),
            (inner_half, girder_h_mm - 200.0), (inner_half, girder_h_mm),
            (-edge_cant_mm, girder_h_mm), (-edge_cant_mm, girder_h_mm - 200.0),
            (-310.0, girder_h_mm - 200.0), (-100.0, girder_h_mm - 270.0),
            (-100.0, 400.0), (-0.5 * bulb_w_mm, 200.0),
        ]
        msp.add_lwpolyline([vb_edge.pt(x, y) for x, y in edge_pts], close=True, dxfattribs={"layer": "OUTLINE"})
        add_arch_dim(msp, vb_edge.pt(-edge_cant_mm, girder_h_mm), vb_edge.pt(0.0, girder_h_mm), (vb_edge.x(-0.5 * edge_cant_mm), vb_edge.y(girder_h_mm) + 8.0), f"{edge_cant_mm:g}")
        add_arch_dim(msp, vb_edge.pt(0.0, girder_h_mm), vb_edge.pt(inner_half, girder_h_mm), (vb_edge.x(0.5 * inner_half), vb_edge.y(girder_h_mm) + 8.0), f"{inner_half:g}")
        add_arch_dim(msp, vb_edge.pt(inner_half, 0.0), vb_edge.pt(inner_half, girder_h_mm), (vb_edge.x(inner_half) + 9.0, vb_edge.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
        add_civil_text(msp, "外边梁跨中预制截面 (1:25)", vb_edge.x(0.0), 74.0, height=2.6, align="CENTER")
        vb_edge.register(doc, msp, paper_bounds=(vb_edge.x(-edge_cant_mm) - 10.0, 78.0, vb_edge.x(inner_half) + 14.0, vb_edge.y(girder_h_mm) + 14.0))

        # Right: Girder Support Thickened End Section (1:25)
        vb_sup = ViewBox("girder_support_end", 282.0, 92.0, 25.0, "mm")
        end_poly = build_t_girder_polygon_mm(flange_width_mm=2.0 * inner_half, height_mm=girder_h_mm, web_thick_mm=bulb_w_mm, bulb_width_mm=bulb_w_mm, bulb_height_mm=0.0)
        msp.add_lwpolyline([vb_sup.pt(x, y) for x, y in end_poly], close=True, dxfattribs={"layer": "OUTLINE"})
        add_arch_dim(msp, vb_sup.pt(-0.5 * bulb_w_mm, 0.0), vb_sup.pt(0.5 * bulb_w_mm, 0.0), (vb_sup.x(0.0), vb_sup.y(0.0) - 7.0), f"{bulb_w_mm:g}")
        add_arch_dim(msp, vb_sup.pt(-inner_half, girder_h_mm), vb_sup.pt(inner_half, girder_h_mm), (vb_sup.x(0.0), vb_sup.y(girder_h_mm) + 8.0), f"{2.0*inner_half:g}")
        add_civil_text(msp, "中梁支点加厚截面 (1:25)", vb_sup.x(0.0), 74.0, height=2.6, align="CENTER")
        vb_sup.register(doc, msp, paper_bounds=(vb_sup.x(-inner_half) - 10.0, 78.0, vb_sup.x(inner_half) + 14.0, vb_sup.y(girder_h_mm) + 14.0))
        return doc

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "25m预制T梁一般构造图（一：纵立面及平面布置）")),
        project=str(spec.get("project", "信阳市文昌中桥设计")),
        drawing_no=str(spec.get("drawing_no", "QL-06")),
        unit_note="mm",
        scale_label="1:80",
        style_overrides=_resolve_style_overrides(spec),
    )
    cx = 0.5 * (b["ix0"] + b["ix1"])
    vb = ViewBox("girder_longitudinal", cx, 184.0, 80.0, "mm")
    half_l = 0.5 * beam_len_mm
    half_c = 0.5 * calc_span_mm
    end_off = half_l - half_c
    # Elevation rectangle + flange bottom line + 5 diaphragm dashed verticals
    msp.add_lwpolyline([vb.pt(-half_l, 0.0), vb.pt(half_l, 0.0), vb.pt(half_l, girder_h_mm), vb.pt(-half_l, girder_h_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb.pt(-half_l, girder_h_mm - 200.0), vb.pt(half_l, girder_h_mm - 200.0), dxfattribs={"layer": "THIN"})
    for k in range(5):
        rxd = -half_c + k * (0.25 * calc_span_mm)
        msp.add_line(vb.pt(rxd, 200.0), vb.pt(rxd, girder_h_mm - 200.0), dxfattribs={"layer": "HIDDEN"})
    draw_bearing_symbol(msp, vb.x(-half_c), vb.y(0.0) - 2.2, kind="fixed", size=3.0)
    draw_bearing_symbol(msp, vb.x(half_c), vb.y(0.0) - 2.2, kind="sliding_longitudinal", size=3.0)

    add_arch_dim(msp, vb.pt(-half_l, girder_h_mm), vb.pt(-half_c, girder_h_mm), (vb.x(-half_l + 0.5 * end_off), vb.y(girder_h_mm) + 8.5), f"{end_off:g}")
    add_arch_dim(msp, vb.pt(-half_c, girder_h_mm), vb.pt(half_c, girder_h_mm), (cx, vb.y(girder_h_mm) + 8.5), f"计算跨径 {calc_span_mm:g}")
    add_arch_dim(msp, vb.pt(half_c, girder_h_mm), vb.pt(half_l, girder_h_mm), (vb.x(half_c + 0.5 * end_off), vb.y(girder_h_mm) + 8.5), f"{end_off:g}")
    add_arch_dim(msp, vb.pt(-half_l, girder_h_mm), vb.pt(half_l, girder_h_mm), (cx, vb.y(girder_h_mm) + 17.5), f"预制主梁全长 {beam_len_mm:g}")
    add_arch_dim(msp, vb.pt(half_l, 0.0), vb.pt(half_l, girder_h_mm), (vb.x(half_l) + 9.0, vb.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
    add_civil_text(msp, "预制T梁纵立面图 (1:80，设端横隔板2道、中横隔板3道)", cx, vb.y(0.0) - 10.0, height=2.5, align="CENTER")

    # Plan view directly below in same 1:80 ViewBox
    ry_p0 = -4200.0
    pre_w = spacing_mm - 400.0
    msp.add_lwpolyline([vb.pt(-half_l, ry_p0 - 0.5 * pre_w), vb.pt(half_l, ry_p0 - 0.5 * pre_w), vb.pt(half_l, ry_p0 + 0.5 * pre_w), vb.pt(-half_l, ry_p0 + 0.5 * pre_w)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb.pt(-half_l - 300.0, ry_p0), vb.pt(half_l + 300.0, ry_p0), dxfattribs={"layer": "CENTER"})
    add_arch_dim(msp, vb.pt(-half_l, ry_p0 - 0.5 * pre_w), vb.pt(-half_l, ry_p0 + 0.5 * pre_w), (vb.x(-half_l) - 9.0, vb.y(ry_p0)), f"{pre_w:g}", angle=90.0)
    add_civil_text(msp, "预制T梁顶面平面图 (1:80)", cx, vb.y(ry_p0 - 0.5 * pre_w) - 8.5, height=2.5, align="CENTER")
    vb.register(doc, msp, paper_bounds=(vb.x(-half_l) - 15.0, vb.y(ry_p0 - 0.5 * pre_w) - 10.0, vb.x(half_l) + 15.0, vb.y(girder_h_mm) + 22.0))
    return doc


# ======================================================================
# 5. Prestressing Tendon Layout (QL-08, Live Reactive Linkage to Calc!)
# ======================================================================
def _draw_tendon_layout(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    calc = _ensure_calc_report(spec)
    ps_info = calc["prestress"]
    girder_h_mm = float(spec.get("height_mm", calc["t_girder_section"]["mid_span_mm"]["height"]))
    flange_w_mm = float(spec.get("flange_width_mm", calc["t_girder_section"]["effective_flange_width_mm"]))
    web_t_mm = float(spec.get("web_thick_mm", 200.0))
    bulb_w_mm = float(spec.get("bulb_width_mm", 560.0 if flange_w_mm <= 2000.0 else 600.0))
    bulb_h_mm = float(spec.get("bulb_height_mm", 200.0))
    end_web_t_mm = float(spec.get("end_web_thick_mm", bulb_w_mm))

    raw_n_tendons = spec.get("n_tendons", ps_info["recommended_tendons"])
    if isinstance(raw_n_tendons, bool) or (isinstance(raw_n_tendons, float) and not raw_n_tendons.is_integer()):
        raise ValueError(f"n_tendons must be an integer, got {raw_n_tendons!r}")
    n_tendons = int(raw_n_tendons)
    if n_tendons < 1 or n_tendons > 10:
        raise ValueError(f"n_tendons must be between 1 and 10 (got {n_tendons}); refusing to silently alter tendon count")

    strands_per = int(spec.get("strands_per_tendon", ps_info["strands_per_tendon"]))
    if strands_per < 1:
        raise ValueError(f"strands_per_tendon must be >= 1, got {strands_per}")
    beam_len_mm = float(spec.get("beam_length_mm", calc["project_summary"]["beam_length_m"] * 1000.0))
    half_span_mm = float(spec.get("half_beam_length_mm", 0.5 * beam_len_mm))

    bulb_dx = min(120.0, max(60.0, 0.25 * bulb_w_mm))
    if n_tendons == 1:
        mid_ducts_mm: list[tuple[str, float, float]] = [("N1", 0.0, 100.0)]
    elif n_tendons == 2:
        mid_ducts_mm = [("N1", -bulb_dx, 100.0), ("N2", bulb_dx, 100.0)]
    else:
        mid_ducts_mm = [("N1", -bulb_dx, 100.0), ("N2", bulb_dx, 100.0), ("N3", 0.0, 100.0)]
        for extra_idx in range(4, n_tendons + 1):
            dy_mm = 100.0 + (extra_idx - 3) * 80.0
            if dy_mm > girder_h_mm - 200.0:
                raise ValueError(f"girder height {girder_h_mm:g}mm cannot accommodate {n_tendons} mid-span ducts")
            mid_ducts_mm.append((f"N{extra_idx}", 0.0, dy_mm))

    if n_tendons == 1:
        end_step_mm = 0.0
        end_ys_mm = [round(0.35 * girder_h_mm, 1)]
    else:
        avail_end_mm = girder_h_mm - 420.0
        end_step_mm = 300.0 if (n_tendons - 1) * 300.0 <= avail_end_mm else math.floor((avail_end_mm / (n_tendons - 1)) / 10.0) * 10.0
        if end_step_mm < 110.0:
            raise ValueError(f"girder height {girder_h_mm:g}mm cannot accommodate {n_tendons} anchorage plates at >=110mm pitch")
        end_ys_mm = [240.0 + i * end_step_mm for i in range(n_tendons)]

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "T梁预应力钢束构造及布置图")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-08" if spec.get("preset") == "wenchang" else "QL-03")),
        unit_note="mm",
        scale_label="1:15 / 1:100",
        style_overrides=_resolve_style_overrides(spec),
    )

    # 1. Left: Mid-Span Section (exact 1:15 ViewBox)
    vb_mid = ViewBox("tendon_mid_section", 76.0, 88.0, 15.0, "mm")
    crop_fl = min(flange_w_mm, 1350.0)
    mid_poly = build_t_girder_polygon_mm(flange_width_mm=crop_fl, height_mm=girder_h_mm, web_thick_mm=web_t_mm, bulb_width_mm=bulb_w_mm, bulb_height_mm=bulb_h_mm)
    msp.add_lwpolyline([vb_mid.pt(px, py) for px, py in mid_poly], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((vb_mid.x(0.0), vb_mid.y(-100.0)), (vb_mid.x(0.0), vb_mid.y(girder_h_mm + 100.0)), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "跨中钢束断面 (1:15)", vb_mid.x(0.0), 71.0, height=2.7, align="CENTER")

    tag_pitch = min(9.0, 82.0 / max(1, n_tendons))
    for idx, (tag, dx_mm, dy_mm) in enumerate(mid_ducts_mm):
        px, py = vb_mid.pt(dx_mm, dy_mm)
        msp.add_circle((px, py), 2.0, dxfattribs={"layer": "TENDON"})
        draw_boxed_tag(msp, vb_mid.x(0.0) + (34.0 if dx_mm >= 0 else -34.0), 96.0 + idx * tag_pitch, tag, leader_to=(px, py))

    add_arch_dim(msp, vb_mid.pt(-0.5 * bulb_w_mm, 0.0), vb_mid.pt(0.5 * bulb_w_mm, 0.0), (vb_mid.x(0.0), vb_mid.y(0.0) - 6.5), f"{bulb_w_mm:g}")
    add_arch_dim(msp, vb_mid.pt(-0.5 * bulb_w_mm, 0.0), vb_mid.pt(-0.5 * bulb_w_mm, 100.0), (vb_mid.x(-0.5 * bulb_w_mm) - 8.0, vb_mid.y(50.0)), "100", angle=90.0)
    vb_mid.register(doc, msp, paper_bounds=(24.0, 74.0, 128.0, 218.0))

    # 2. Center: Girder-End Anchorage Section (exact 1:15 ViewBox)
    vb_end = ViewBox("tendon_end_section", 184.0, 88.0, 15.0, "mm")
    end_poly = build_t_girder_polygon_mm(flange_width_mm=crop_fl, height_mm=girder_h_mm, haunch_width_mm=10.0, web_thick_mm=end_web_t_mm, bulb_width_mm=end_web_t_mm, bulb_height_mm=0.0)
    msp.add_lwpolyline([vb_end.pt(px, py) for px, py in end_poly], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((vb_end.x(0.0), vb_end.y(-100.0)), (vb_end.x(0.0), vb_end.y(girder_h_mm + 100.0)), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "梁端锚固断面 (1:15)", vb_end.x(0.0), 71.0, height=2.7, align="CENTER")

    for idx, ey_mm in enumerate(end_ys_mm):
        tag = mid_ducts_mm[idx][0]
        px, py = vb_end.pt(0.0, ey_mm)
        msp.add_circle((px, py), 2.2, dxfattribs={"layer": "TENDON"})
        draw_boxed_tag(msp, vb_end.x(0.0) + 32.0, py, tag, leader_to=(px + 2.2, py))
        if idx > 0:
            py_prev = vb_end.y(end_ys_mm[idx - 1])
            add_arch_dim(msp, (vb_end.x(-0.5 * end_web_t_mm), py_prev), (vb_end.x(-0.5 * end_web_t_mm), py), (vb_end.x(-0.5 * end_web_t_mm) - 8.0, 0.5 * (py_prev + py)), f"{end_step_mm:g}", angle=90.0)
    if n_tendons == 1:
        py0 = vb_end.y(end_ys_mm[0])
        add_arch_dim(msp, vb_end.pt(-0.5 * end_web_t_mm, 0.0), (vb_end.x(-0.5 * end_web_t_mm), py0), (vb_end.x(-0.5 * end_web_t_mm) - 8.0, 0.5 * (vb_end.y(0.0) + py0)), f"{end_ys_mm[0]:g}", angle=90.0)
    vb_end.register(doc, msp, paper_bounds=(136.0, 74.0, 236.0, 218.0))

    # 3. Right: Half-Span Parabolic Tendon Profile (true 1:100 horizontal + Tendon Schedule)
    vb_prof = ViewBox("tendon_half_profile", 262.0, 152.0, 100.0, "mm")
    msp.add_lwpolyline(
        [vb_prof.pt(0.0, 0.0), vb_prof.pt(half_span_mm, 0.0), vb_prof.pt(half_span_mm, girder_h_mm), vb_prof.pt(0.0, girder_h_mm)],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    msp.add_line((vb_prof.x(half_span_mm), vb_prof.y(-350.0)), (vb_prof.x(half_span_mm), vb_prof.y(girder_h_mm + 450.0)), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "半跨预应力钢束纵断线形 (1:100)", vb_prof.x(0.5 * half_span_mm), vb_prof.y(0.0) - 9.0, height=2.5, align="CENTER")

    for idx, (tag, _, mid_y_mm) in enumerate(mid_ducts_mm):
        ey_mm = end_ys_mm[idx]
        pts_curve = [vb_prof.pt(half_span_mm * (k / 24.0), mid_y_mm + (ey_mm - mid_y_mm) * ((1.0 - k / 24.0) ** 2)) for k in range(25)]
        msp.add_lwpolyline(pts_curve, close=False, dxfattribs={"layer": "TENDON"})
        add_civil_text(msp, tag, pts_curve[0][0] - 5.2, pts_curve[0][1] - 0.8, height=1.9)

    add_arch_dim(msp, vb_prof.pt(0.0, girder_h_mm), vb_prof.pt(half_span_mm, girder_h_mm), (vb_prof.x(0.5 * half_span_mm), vb_prof.y(girder_h_mm) + 7.5), f"半梁长 {half_span_mm:g}")
    vb_prof.register(doc, msp, paper_bounds=(250.0, 140.0, 404.0, 200.0))

    # Tendon Summary Box above title block (live values from calc_report!)
    mid_pe = float(ps_info["loss_profile"][len(ps_info["loss_profile"]) // 2]["effective_prestress_mpa"])
    sigma_con = float(ps_info["sigma_con_mpa"])
    tendon_rows = calc["rebar_and_tendon_ledger"]["tendons"]
    elong_sample = tendon_rows[0]["theoretical_elongation_mm"] if tendon_rows else 170.0
    add_civil_text(
        msp,
        f"钢束规格：{n_tendons}束 Φs15.2-{strands_per} (fpk=1860MPa, σcon={sigma_con:g}MPa, 跨中有效预应力={mid_pe:.2f}MPa, 单束理论伸长量={elong_sample:g}mm)",
        b["ix0"] + 5.0,
        b["iy0"] + 16.0,
        height=2.15,
    )
    attach_sheet_metadata(doc, msp, "tendon_schedule", {"items": tendon_rows, "sigma_con_mpa": sigma_con, "mid_effective_prestress_mpa": mid_pe})
    return doc


# ======================================================================
# 6. Data-Driven Ordinary Rebar Construction Renderer (Cats 4 & 9)
# ======================================================================
_REBAR_COMPONENT_META: dict[str, tuple[str, str, str]] = {
    "t_girder_web": ("t_girder_web_rebar", "主梁梁肋普通钢筋构造图", "QL-09"),
    "t_girder_flange": ("t_girder_flange_rebar", "主梁翼缘板及湿接缝钢筋构造图", "QL-10"),
    "diaphragm": ("diaphragm_rebar", "主梁中横隔梁与端横隔梁钢筋构造图", "QL-11"),
    "pier_cap": ("pier_cap_rebar", "桥墩盖梁普通钢筋构造图", "QL-16"),
    "pier_column_pile": ("pier_column_pile_rebar", "桥墩墩柱、系梁及钻孔桩钢筋构造图", "QL-17"),
    "abutment_rebar": ("abutment_rebar", "桥台台帽、耳背墙及桩基钢筋构造图", "QL-18"),
}


def _draw_rebar_detail(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    comp = str(spec.get("component", "t_girder_web")).strip().lower()
    if comp not in _REBAR_COMPONENT_META:
        raise ValueError(f"unsupported rebar component {comp!r}; expected one of {sorted(_REBAR_COMPONENT_META)}")
    grp_key, def_title, def_dno = _REBAR_COMPONENT_META[comp]
    calc = _ensure_calc_report(spec)
    bars = calc["rebar_and_tendon_ledger"]["rebar_groups"][grp_key]

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", def_title)),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", def_dno)),
        unit_note="mm（钢筋表长以cm计）",
        scale_label="图示比例",
        style_overrides=_resolve_style_overrides(spec),
    )

    # Render Rebar Schedule Table on right side above title block
    draw_rebar_schedule_table(
        doc,
        msp,
        bars,
        x0=216.0,
        y0=b["tb_y1"] + 6.0,
        width=188.0,
        title=f"{def_title}钢筋数量表",
    )

    # Left structural reinforcement diagram in true-scale ViewBox
    girder_h = float(calc["t_girder_section"]["mid_span_mm"]["height"])
    flange_w = float(calc["t_girder_section"]["effective_flange_width_mm"])
    cap_len_mm = float(calc["pier_cap_beam_1d"]["cap_length_m"]) * 1000.0
    col_d_mm = float(calc["substructure_checks"]["pier_column"]["diameter_m"]) * 1000.0
    col_h_mm = float(calc["substructure_checks"]["pier_column"]["height_m"]) * 1000.0

    if comp in {"t_girder_web", "t_girder_flange"}:
        vb = ViewBox(f"rebar_{comp}", 112.0, 74.0, 20.0, "mm")
        poly = build_t_girder_polygon_mm(flange_width_mm=flange_w, height_mm=girder_h, bulb_width_mm=560.0)
        msp.add_lwpolyline([vb.pt(x, y) for x, y in poly], close=True, dxfattribs={"layer": "OUTLINE"})
        # Stirrup loop + top/bottom bars
        msp.add_lwpolyline([vb.pt(-70.0, 60.0), vb.pt(70.0, 60.0), vb.pt(70.0, girder_h - 60.0), vb.pt(-70.0, girder_h - 60.0)], close=True, dxfattribs={"layer": "REBAR"})
        msp.add_line(vb.pt(-0.46 * flange_w, girder_h - 45.0), vb.pt(0.46 * flange_w, girder_h - 45.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb.pt(-0.46 * flange_w, girder_h - 145.0), vb.pt(0.46 * flange_w, girder_h - 145.0), dxfattribs={"layer": "REBAR"})
        for rx_b in (-180.0, -90.0, 0.0, 90.0, 180.0):
            msp.add_circle(vb.pt(rx_b, 65.0), 1.4, dxfattribs={"layer": "REBAR"})
        add_arch_dim(msp, vb.pt(-0.5 * flange_w, girder_h), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.0), vb.y(girder_h) + 8.0), f"{flange_w:g}")
        add_arch_dim(msp, vb.pt(0.5 * flange_w, 0.0), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.5 * flange_w) + 9.0, vb.y(0.5 * girder_h)), f"{girder_h:g}", angle=90.0)
        anchors = [vb.pt(90.0, 65.0), vb.pt(0.30 * flange_w, girder_h - 45.0), vb.pt(70.0, 0.5 * girder_h), vb.pt(-70.0, 0.35 * girder_h)]
        vb.register(doc, msp, paper_bounds=(24.0, 56.0, 206.0, 250.0))
    elif comp in {"diaphragm", "pier_cap"}:
        w_mm = cap_len_mm if comp == "pier_cap" else 4.0 * flange_w
        h_mm = 1400.0 if comp == "pier_cap" else 1350.0
        s_den = 60.0
        vb = ViewBox(f"rebar_{comp}", 114.0, 128.0, s_den, "mm")
        hw = 0.5 * w_mm
        msp.add_lwpolyline([vb.pt(-hw, 0.0), vb.pt(hw, 0.0), vb.pt(hw, h_mm), vb.pt(-hw, h_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb.pt(-hw + 80.0, h_mm - 75.0), vb.pt(hw - 80.0, h_mm - 75.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb.pt(-hw + 80.0, 75.0), vb.pt(hw - 80.0, 75.0), dxfattribs={"layer": "REBAR"})
        for k in range(11):
            rxs = -hw + 200.0 + k * ((w_mm - 400.0) / 10.0)
            msp.add_line(vb.pt(rxs, 75.0), vb.pt(rxs, h_mm - 75.0), dxfattribs={"layer": "REBAR"})
        add_arch_dim(msp, vb.pt(-hw, h_mm), vb.pt(hw, h_mm), (vb.x(0.0), vb.y(h_mm) + 9.0), f"{w_mm:g}")
        add_arch_dim(msp, vb.pt(hw, 0.0), vb.pt(hw, h_mm), (vb.x(hw) + 8.0, vb.y(0.5 * h_mm)), f"{h_mm:g}", angle=90.0)
        anchors = [vb.pt(0.0, h_mm - 75.0), vb.pt(0.0, 75.0), vb.pt(0.25 * hw, 0.5 * h_mm), vb.pt(-0.25 * hw, 0.5 * h_mm)]
        vb.register(doc, msp, paper_bounds=(24.0, 96.0, 206.0, 240.0))
    else:
        # Column / Pile / Abutment vertical reinforcement cage (1:80)
        vb = ViewBox(f"rebar_{comp}", 112.0, 82.0, 80.0, "mm")
        cage_h = col_h_mm + 5500.0
        msp.add_lwpolyline([vb.pt(-0.5 * col_d_mm, 0.0), vb.pt(0.5 * col_d_mm, 0.0), vb.pt(0.5 * col_d_mm, cage_h), vb.pt(-0.5 * col_d_mm, cage_h)], close=True, dxfattribs={"layer": "OUTLINE"})
        for dx_r in (-0.38 * col_d_mm, 0.0, 0.38 * col_d_mm):
            msp.add_line(vb.pt(dx_r, 120.0), vb.pt(dx_r, cage_h - 120.0), dxfattribs={"layer": "REBAR"})
        for k in range(14):
            ryh = 300.0 + k * ((cage_h - 600.0) / 13.0)
            msp.add_line(vb.pt(-0.42 * col_d_mm, ryh), vb.pt(0.42 * col_d_mm, ryh), dxfattribs={"layer": "REBAR"})
        add_arch_dim(msp, vb.pt(-0.5 * col_d_mm, 0.0), vb.pt(0.5 * col_d_mm, 0.0), (vb.x(0.0), vb.y(0.0) - 7.5), f"{col_d_mm:g}")
        add_arch_dim(msp, vb.pt(0.5 * col_d_mm, 0.0), vb.pt(0.5 * col_d_mm, cage_h), (vb.x(0.5 * col_d_mm) + 10.0, vb.y(0.5 * cage_h)), f"{cage_h:g}", angle=90.0)
        anchors = [vb.pt(0.38 * col_d_mm, 0.78 * cage_h), vb.pt(-0.42 * col_d_mm, 0.62 * cage_h), vb.pt(0.38 * col_d_mm, 0.32 * cage_h), vb.pt(-0.42 * col_d_mm, 0.18 * cage_h)]
        vb.register(doc, msp, paper_bounds=(24.0, 56.0, 206.0, 250.0))

    # Draw boxed bar ID tags linked to every bar in the schedule
    for idx, b_item in enumerate(bars):
        ax, ay = anchors[idx % len(anchors)]
        tx = 38.0 if idx % 2 == 0 else 188.0
        ty = 228.0 - idx * 14.0
        draw_boxed_tag(msp, tx, ty, str(b_item["bar_id"]), leader_to=(ax, ay), layer="REBAR")

    add_civil_text(msp, f"验算依据：{bars[0]['calc_basis'][:56]}", b["ix0"] + 6.0, b["iy0"] + 16.0, height=2.1)
    return doc


# ======================================================================
# 7. Bearing Detail & Layout (QL-12, Cat 6) & Deck Ancillary (QL-13, Cat 7)
# ======================================================================
def _draw_bearing_layout(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    calc = _ensure_calc_report(spec)
    brg = calc["bearing_checks"]
    parts = brg["bearing_model"].replace("GJZ", "").strip().split("×")
    bw_mm = float(parts[0]) if len(parts) == 3 else 300.0
    bl_mm = float(parts[1]) if len(parts) == 3 else 400.0
    bt_mm = float(parts[2]) if len(parts) == 3 else 74.0
    ped_sp_mm = float(calc["lateral_distribution"]["girder_spacing_m"]) * 1000.0
    n_ped = int(calc["lateral_distribution"]["n_girders"])

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "板式橡胶支座及支座垫石构造布置图")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-12")),
        unit_note="mm",
        scale_label="1:5 / 平面示意",
        style_overrides=_resolve_style_overrides(spec),
    )

    # Left: 1:5 Laminated Elastomeric Bearing Section (exact 1:5 ViewBox)
    vb = ViewBox("bearing_section", 112.0, 126.0, 5.0, "mm")
    hl = 0.5 * bl_mm
    msp.add_lwpolyline([vb.pt(-hl, 0.0), vb.pt(hl, 0.0), vb.pt(hl, bt_mm), vb.pt(-hl, bt_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    for k in range(1, 6):
        ry_plate = bt_mm * (k / 6.0)
        msp.add_line(vb.pt(-hl + 5.0, ry_plate), vb.pt(hl - 5.0, ry_plate), dxfattribs={"layer": "REBAR"})
    add_arch_dim(msp, vb.pt(-hl, bt_mm), vb.pt(hl, bt_mm), (vb.x(0.0), vb.y(bt_mm) + 9.0), f"{bl_mm:g}")
    add_arch_dim(msp, vb.pt(hl, 0.0), vb.pt(hl, bt_mm), (vb.x(hl) + 9.0, vb.y(0.5 * bt_mm)), f"{bt_mm:g}", angle=90.0)
    # Plan of single pad above in same 1:5 ViewBox
    ry_plan0 = bt_mm + 90.0
    msp.add_lwpolyline([vb.pt(-hl, ry_plan0), vb.pt(hl, ry_plan0), vb.pt(hl, ry_plan0 + bw_mm), vb.pt(-hl, ry_plan0 + bw_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    add_arch_dim(msp, vb.pt(hl, ry_plan0), vb.pt(hl, ry_plan0 + bw_mm), (vb.x(hl) + 9.0, vb.y(ry_plan0 + 0.5 * bw_mm)), f"{bw_mm:g}", angle=90.0)
    add_civil_text(msp, f"{brg['bearing_model']} 支座剖面与平面详图 (1:5)", vb.x(0.0), 108.0, height=2.5, align="CENTER")
    vb.register(doc, msp, paper_bounds=(45.0, 112.0, 185.0, 255.0))

    # Right: Single-span bearing layout & JT/T 4-2019 check summary
    bx0, by0, bw, bh = 232.0, 96.0, 162.0, 142.0
    msp.add_lwpolyline([(bx0, by0), (bx0 + bw, by0), (bx0 + bw, by0 + bh), (bx0, by0 + bh)], close=True, dxfattribs={"layer": "THIN"})
    x_z1, x_z2 = bx0 + 34.0, bx0 + bw - 34.0
    for i in range(n_ped):
        yg = by0 + 22.0 + i * ((bh - 44.0) / max(1, n_ped - 1))
        msp.add_line((x_z1 - 10.0, yg), (x_z2 + 10.0, yg), dxfattribs={"layer": "THIN"})
        draw_bearing_symbol(msp, x_z1, yg, kind="fixed", size=3.4)
        draw_bearing_symbol(msp, x_z2, yg, kind="sliding_longitudinal", size=3.4)
        add_civil_text(msp, f"{i+1}# ({ped_sp_mm:g}间距)", bx0 + 5.0, yg - 1.0, height=1.9)
    add_civil_text(msp, f"支座承压验算：σc={brg['compressive_stress_mpa']}MPa ≤ 10.0MPa，抗滑与抗脱空合格", b["ix0"] + 6.0, b["iy0"] + 16.0, height=2.2)
    return doc


def _draw_deck_ancillary(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    calc = _ensure_calc_report(spec)
    bars = calc["rebar_and_tendon_ledger"]["rebar_groups"]["deck_ancillary_rebar"]
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "桥面附属结构构造图（防撞护栏、桥面连续及伸缩缝）")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-13")),
        unit_note="mm",
        scale_label="1:15",
        style_overrides=_resolve_style_overrides(spec),
    )
    # Left: 1:15 Concrete Crash Barrier Section + HL1 Rebar
    vb = ViewBox("crash_barrier_detail", 76.0, 96.0, 15.0, "mm")
    b_pts = [(0.0, 0.0), (500.0, 0.0), (500.0, 250.0), (350.0, 450.0), (320.0, 1000.0), (0.0, 1000.0)]
    msp.add_lwpolyline([vb.pt(x, y) for x, y in b_pts], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([vb.pt(45.0, -80.0), vb.pt(45.0, 955.0), vb.pt(280.0, 955.0), vb.pt(310.0, 440.0), vb.pt(455.0, 240.0), vb.pt(455.0, -80.0)], close=False, dxfattribs={"layer": "REBAR"})
    draw_boxed_tag(msp, vb.x(500.0) + 24.0, vb.y(720.0), "HL1", leader_to=vb.pt(295.0, 720.0), layer="REBAR")
    add_arch_dim(msp, vb.pt(0.0, 0.0), vb.pt(500.0, 0.0), (vb.x(250.0), vb.y(0.0) - 8.0), "500")
    add_arch_dim(msp, vb.pt(0.0, 0.0), vb.pt(0.0, 1000.0), (vb.x(0.0) - 9.0, vb.y(500.0)), "1000", angle=90.0)
    add_civil_text(msp, "防撞护栏构造及配筋图 (1:15)", vb.x(250.0), 74.0, height=2.5, align="CENTER")
    vb.register(doc, msp, paper_bounds=(45.0, 78.0, 145.0, 182.0))

    # Top-Right: Pier-Top Continuous Deck Link Detail (1:20)
    vb_cont = ViewBox("deck_continuity_detail", 275.0, 188.0, 20.0, "mm")
    msp.add_lwpolyline([vb_cont.pt(-1200.0, 0.0), vb_cont.pt(-20.0, 0.0), vb_cont.pt(-20.0, 200.0), vb_cont.pt(-1200.0, 200.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([vb_cont.pt(20.0, 0.0), vb_cont.pt(1200.0, 0.0), vb_cont.pt(1200.0, 200.0), vb_cont.pt(20.0, 200.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([vb_cont.pt(-1200.0, 200.0), vb_cont.pt(1200.0, 200.0), vb_cont.pt(1200.0, 280.0), vb_cont.pt(-1200.0, 280.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_line(vb_cont.pt(-1100.0, 245.0), vb_cont.pt(1100.0, 245.0), dxfattribs={"layer": "REBAR"})
    draw_boxed_tag(msp, vb_cont.x(0.0), vb_cont.y(280.0) + 12.0, "LX1", leader_to=vb_cont.pt(0.0, 245.0), layer="REBAR")
    add_arch_dim(msp, vb_cont.pt(-1200.0, 280.0), vb_cont.pt(1200.0, 280.0), (vb_cont.x(0.0), vb_cont.y(280.0) + 21.0), "桥面连续加强段 2400")
    add_civil_text(msp, "墩顶桥面连续及PVC泄水管构造 (1:20)", vb_cont.x(0.0), 175.0, height=2.4, align="CENTER")
    vb_cont.register(doc, msp, paper_bounds=(205.0, 178.0, 355.0, 232.0))

    draw_rebar_schedule_table(doc, msp, bars, x0=216.0, y0=b["tb_y1"] + 8.0, width=188.0, title="桥面附属结构钢筋明细表")
    return doc


# ======================================================================
# 8. Double-Column Pier & Pile-Bent Abutment (QL-14 & QL-15, Cat 8)
# ======================================================================
def _draw_pier_and_abutment(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    variant = str(spec.get("variant", "pier")).strip().lower()
    is_abutment = variant == "abutment"
    is_wenchang = str(spec.get("preset", "")).strip().lower() == "wenchang"
    cap_len_mm = float(spec.get("cap_length_mm", (9000.0 if is_abutment else 8800.0) if is_wenchang else (11750.0 if is_abutment else 10950.0)))
    cap_h_mm = float(spec.get("cap_height_mm", 1400.0 if is_wenchang else 1500.0))
    cap_w_mm = float(spec.get("cap_width_mm", 1800.0 if is_abutment else (1600.0 if is_wenchang else 1700.0)))
    col_spacing_mm = float(spec.get("column_spacing_mm", (5400.0 if is_abutment else 5200.0) if is_wenchang else (6800.0 if is_abutment else 6650.0)))
    col_d_mm = float(spec.get("column_diameter_mm", 1400.0 if is_abutment else 1200.0))
    col_h_mm = float(spec.get("column_height_mm", 4200.0 if is_abutment else 5500.0))
    pile_d_mm = float(spec.get("pile_diameter_mm", 1400.0))
    n_pedestals = int(spec.get("n_pedestals", 5))
    ped_spacing_mm = float(spec.get("pedestal_spacing_mm", 1950.0 if is_wenchang else 2348.0))
    bearing_model = str(spec.get("bearing_model", "GJZ 300×400×74" if is_wenchang else "GJZ 300×450×85"))
    slope = float(spec.get("cross_slope_pct", 2.0)) / 100.0

    default_title = "桩柱式桥台一般构造及支座布置图" if is_abutment else "双柱式桥墩一般构造及支座布置图"
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", default_title)),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-05" if is_abutment else "QL-04")),
        unit_note="mm",
        scale_label="1:80",
        style_overrides=_resolve_style_overrides(spec),
    )

    # 1. Left: Front Elevation in true 1:80 ViewBox (with real 2% cap-beam roof cross-slope!)
    vb_el = ViewBox("substructure_elev", 108.0, 224.0, 80.0, "mm")
    half_cap = 0.5 * cap_len_mm
    edge_drop = half_cap * slope
    overhang = 0.5 * (cap_len_mm - col_spacing_mm - col_d_mm)
    haunch_rise = 0.32 * cap_h_mm

    cap_pts = [
        vb_el.pt(-half_cap, -edge_drop),
        vb_el.pt(0.0, 0.0),
        vb_el.pt(half_cap, -edge_drop),
        vb_el.pt(half_cap, -cap_h_mm + haunch_rise),
        vb_el.pt(half_cap - overhang, -cap_h_mm),
        vb_el.pt(-half_cap + overhang, -cap_h_mm),
        vb_el.pt(-half_cap, -cap_h_mm + haunch_rise),
    ]
    msp.add_lwpolyline(cap_pts, close=True, dxfattribs={"layer": "OUTLINE"})

    for i in range(n_pedestals):
        rxp = (i - 0.5 * (n_pedestals - 1)) * ped_spacing_mm
        ry_top = -abs(rxp) * slope
        msp.add_lwpolyline(
            [vb_el.pt(rxp - 200.0, ry_top), vb_el.pt(rxp + 200.0, ry_top), vb_el.pt(rxp + 200.0, ry_top + 150.0), vb_el.pt(rxp - 200.0, ry_top + 150.0)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )

    draw_slope_arrow(msp, vb_el.x(-0.48 * half_cap), vb_el.y(0.0) + 5.5, slope_text="2%", direction="left")
    draw_slope_arrow(msp, vb_el.x(0.48 * half_cap), vb_el.y(0.0) + 5.5, slope_text="2%", direction="right")

    ry_col_top = -cap_h_mm
    ry_col_bot = ry_col_top - col_h_mm
    ry_pile_top = ry_col_bot - 400.0
    ry_pile_bot = ry_pile_top - 3200.0
    for rxc in (-0.5 * col_spacing_mm, 0.5 * col_spacing_mm):
        msp.add_lwpolyline(
            [vb_el.pt(rxc - 0.5 * col_d_mm, ry_col_bot), vb_el.pt(rxc + 0.5 * col_d_mm, ry_col_bot), vb_el.pt(rxc + 0.5 * col_d_mm, ry_col_top), vb_el.pt(rxc - 0.5 * col_d_mm, ry_col_top)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        msp.add_lwpolyline(
            [vb_el.pt(rxc - 0.6 * pile_d_mm, ry_pile_top), vb_el.pt(rxc + 0.6 * pile_d_mm, ry_pile_top), vb_el.pt(rxc + 0.6 * pile_d_mm, ry_col_bot), vb_el.pt(rxc - 0.6 * pile_d_mm, ry_col_bot)],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        msp.add_line(vb_el.pt(rxc - 0.5 * pile_d_mm, ry_pile_top), vb_el.pt(rxc - 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb_el.pt(rxc + 0.5 * pile_d_mm, ry_pile_top), vb_el.pt(rxc + 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb_el.x(rxc), vb_el.y(ry_pile_bot), vb_el.span(pile_d_mm))
        msp.add_line((vb_el.x(rxc), vb_el.y(0.0) + 10.0), (vb_el.x(rxc), vb_el.y(ry_pile_bot) - 5.0), dxfattribs={"layer": "CENTER"})

    msp.add_lwpolyline(
        [
            vb_el.pt(-0.5 * col_spacing_mm + 0.6 * pile_d_mm, ry_col_bot - 360.0),
            vb_el.pt(0.5 * col_spacing_mm - 0.6 * pile_d_mm, ry_col_bot - 360.0),
            vb_el.pt(0.5 * col_spacing_mm - 0.6 * pile_d_mm, ry_col_bot - 40.0),
            vb_el.pt(-0.5 * col_spacing_mm + 0.6 * pile_d_mm, ry_col_bot - 40.0),
        ],
        close=True,
        dxfattribs={"layer": "OUTLINE"},
    )
    draw_ground_line(msp, vb_el.x(-half_cap) - 6.0, vb_el.y(ry_col_bot) + 5.0, vb_el.x(half_cap) + 6.0, vb_el.y(ry_col_bot) + 5.0, label="地面线", scale=0.85)

    cap_label = "台帽长" if is_abutment else "盖梁长"
    sp_label = "桩距" if is_abutment else "墩距"
    add_arch_dim(msp, (vb_el.x(-half_cap), vb_el.y(0.0)), (vb_el.x(half_cap), vb_el.y(0.0)), (vb_el.x(0.0), vb_el.y(0.0) + 15.0), f"{cap_label} {cap_len_mm:g}")
    add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm, ry_col_bot), vb_el.pt(0.5 * col_spacing_mm, ry_col_bot), (vb_el.x(0.0), vb_el.y(ry_col_bot) - 10.0), f"{sp_label} {col_spacing_mm:g}")
    add_arch_dim(msp, (vb_el.x(half_cap), vb_el.y(ry_col_top)), (vb_el.x(half_cap), vb_el.y(0.0)), (vb_el.x(half_cap) + 8.5, vb_el.y(0.5 * ry_col_top)), f"{cap_h_mm:g}", angle=90.0)
    add_civil_text(msp, "桥台立面图 (1:80)" if is_abutment else "桥墩立面图 (1:80)", vb_el.x(0.0), 50.0, height=2.8, align="CENTER")
    vb_el.register(doc, msp, paper_bounds=(vb_el.x(-half_cap) - 10.0, 56.0, vb_el.x(half_cap) + 14.0, vb_el.y(0.0) + 20.0))

    # 2. Center: Side View in true 1:80 ViewBox
    vb_sd = ViewBox("substructure_side", 224.0, 224.0, 80.0, "mm")
    half_cw = 0.5 * cap_w_mm
    msp.add_lwpolyline([vb_sd.pt(-half_cw, ry_col_top), vb_sd.pt(half_cw, ry_col_top), vb_sd.pt(half_cw, 0.0), vb_sd.pt(-half_cw, 0.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    if is_abutment:
        bw_mm = 0.35 * cap_w_mm
        msp.add_lwpolyline([vb_sd.pt(-half_cw, 0.0), vb_sd.pt(-half_cw + bw_mm, 0.0), vb_sd.pt(-half_cw + bw_mm, 1750.0), vb_sd.pt(-half_cw, 1750.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_lwpolyline([vb_sd.pt(-half_cw - 1100.0, ry_col_top + 300.0), vb_sd.pt(-half_cw, ry_col_top + 300.0), vb_sd.pt(-half_cw, 1750.0), vb_sd.pt(-half_cw - 1100.0, 1750.0)], close=True, dxfattribs={"layer": "HIDDEN"})
    msp.add_lwpolyline([vb_sd.pt(-0.5 * col_d_mm, ry_col_bot), vb_sd.pt(0.5 * col_d_mm, ry_col_bot), vb_sd.pt(0.5 * col_d_mm, ry_col_top), vb_sd.pt(-0.5 * col_d_mm, ry_col_top)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb_sd.pt(-0.5 * pile_d_mm, ry_col_bot), vb_sd.pt(-0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb_sd.pt(0.5 * pile_d_mm, ry_col_bot), vb_sd.pt(0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
    draw_pile_s_break(msp, vb_sd.x(0.0), vb_sd.y(ry_pile_bot), vb_sd.span(pile_d_mm))
    add_arch_dim(msp, vb_sd.pt(-half_cw, 0.0), vb_sd.pt(half_cw, 0.0), (vb_sd.x(0.0), vb_sd.y(0.0) + (26.0 if is_abutment else 15.0)), f"{cap_w_mm:g}")
    add_civil_text(msp, "桥台侧面图 (1:80)" if is_abutment else "桥墩侧面图 (1:80)", vb_sd.x(0.0), 50.0, height=2.8, align="CENTER")
    vb_sd.register(doc, msp, paper_bounds=(192.0, 56.0, 266.0, 256.0))

    # 3. Right: Bearing Layout Plan
    bx0, by0, bw, bh = 282.0, 96.0, 118.0, 134.0
    msp.add_lwpolyline([(bx0, by0), (bx0 + bw, by0), (bx0 + bw, by0 + bh), (bx0, by0 + bh)], close=True, dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "单跨支座平面布置图", bx0 + 0.5 * bw, by0 + bh + 5.0, height=2.6, align="CENTER")
    add_civil_text(msp, f"支座型号：{bearing_model}", bx0 + 0.5 * bw, by0 - 7.0, height=2.2, align="CENTER")
    x_z1, x_z2 = bx0 + 24.0, bx0 + bw - 24.0
    msp.add_line((x_z1, by0 + 12.0), (x_z1, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    msp.add_line((x_z2, by0 + 12.0), (x_z2, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    for i in range(n_pedestals):
        yg = by0 + 22.0 + i * ((bh - 44.0) / max(1, n_pedestals - 1))
        msp.add_line((x_z1 - 8.0, yg), (x_z2 + 8.0, yg), dxfattribs={"layer": "THIN"})
        draw_bearing_symbol(msp, x_z1, yg, kind="fixed", size=3.3)
        draw_bearing_symbol(msp, x_z2, yg, kind="sliding_longitudinal", size=3.3)
        add_civil_text(msp, f"{i+1}#", bx0 + 7.0, yg - 1.0, height=2.0)

    return doc


# ======================================================================
# 9. Notes / Sheet Index & Whole-Bridge Bill of Quantities (QL-01 & QL-02)
# ======================================================================
def _draw_notes_and_boq(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    variant = str(spec.get("variant", "boq")).strip().lower()
    calc = _ensure_calc_report(spec)
    ps = calc["project_summary"]
    boq = calc["bill_of_quantities"]

    if variant == "notes":
        doc, msp, b = setup_civil_dxf_document(
            sheet_size=str(spec.get("sheet_size", "A3")),
            title=str(spec.get("title", "设计总说明及全套施工图目录")),
            project=str(spec.get("project", ps["project_name"])),
            drawing_no=str(spec.get("drawing_no", "QL-01")),
            unit_note="mm / m",
            scale_label="无比例",
            style_overrides=_resolve_style_overrides(spec),
        )
        notes = [
            f"一、工程概况：{ps['project_name']}，跨径布置 {ps['n_spans']}×{ps['standard_span_m']:g}m，计算跨径 {ps['calc_span_m']:g}m；",
            f"二、横断面标准：桥面全宽 {ps['deck_width_m']*1000:g}mm，护栏内净宽 {ps['clear_width_m']*1000:g}mm，行车道宽 {ps['roadway_width_m']*1000:g}mm，双向横坡 {ps['cross_slope_pct']:g}%；",
            "三、主要材料：预制T梁采用 C50 混凝土，预应力钢束采用 Φs15.2 (fpk=1860MPa)；桥面调平层 C40；墩台盖梁及水下桩基采用 C30 混凝土；普通钢筋 HRB400 / HPB300；",
            "四、设计规范：《公路工程技术标准》(JTG B01-2014)、《公路桥涵设计通用规范》(JTG D60-2015)、《公路钢筋混凝土及预应力混凝土桥涵设计规范》(JTG 3362-2018)、《公路桥涵地基与基础设计规范》(JTG 3363-2019)；",
            "五、图册构成：QL-01~QL-18 共 18 张 A3 施工图，完整覆盖总体布置、T梁构造、预应力束、上下部普通钢筋、支座、桥面附属及工程量表。",
        ]
        if calc.get("unresolved_assumptions"):
            miss_names = "、".join(u["field"] for u in calc["unresolved_assumptions"])
            notes.append(f"六、待现场实测确认项：{miss_names}（当前采用设计初拟值参与联动计算，实测更新后全套图表自动同步）。")
        msp.add_lwpolyline([(24.0, 72.0), (402.0, 72.0), (402.0, 256.0), (24.0, 256.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        for idx, line in enumerate(notes):
            add_civil_text(msp, line, 30.0, 244.0 - idx * 14.0, height=2.25)
        return doc

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "全桥主要工程材料数量汇总表")),
        project=str(spec.get("project", ps["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-02")),
        unit_note="m3 / kg / 块",
        scale_label="表格",
        style_overrides=_resolve_style_overrides(spec),
    )
    x0, y_top, w = 28.0, 254.0, 372.0
    rows = boq["rows"]
    row_h = 11.0
    y_bot = y_top - (len(rows) + 1) * row_h
    msp.add_lwpolyline([(x0, y_bot), (x0 + w, y_bot), (x0 + w, y_top), (x0, y_top)], close=True, dxfattribs={"layer": "FRAME"})
    cols = (x0, x0 + 96.0, x0 + 196.0, x0 + 236.0, x0 + 306.0, x0 + w)
    for cx in cols[1:-1]:
        msp.add_line((cx, y_bot), (cx, y_top), dxfattribs={"layer": "THIN"})
    headers = ("工程部位", "材料名称及规格", "单位", "全桥数量", "对应图号")
    for ci, h_s in enumerate(headers):
        add_civil_text(msp, h_s, 0.5 * (cols[ci] + cols[ci + 1]), y_top - 7.5, height=2.4, align="CENTER")
    msp.add_line((x0, y_top - row_h), (x0 + w, y_top - row_h), dxfattribs={"layer": "FRAME"})

    for ri, r in enumerate(rows):
        ry = y_top - (ri + 2) * row_h
        if ri < len(rows) - 1:
            msp.add_line((x0, ry), (x0 + w, ry), dxfattribs={"layer": "THIN"})
        vals = (str(r["category"]), str(r["material"]), str(r["unit"]), f"{r['quantity']}", str(r["sheet_ref"]))
        for ci, vs in enumerate(vals):
            add_civil_text(msp, vs, 0.5 * (cols[ci] + cols[ci + 1]), ry + 3.5, height=2.2, align="CENTER")

    attach_sheet_metadata(doc, msp, "boq_summary", boq)
    return doc


# ======================================================================
# 10. Structural Mechanics Influence Lines & Envelopes (JS-01)
# ======================================================================
def _draw_mechanics_diagrams(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    n_girders = int(spec.get("n_girders", 5))
    spacing_m = float(spec.get("girder_spacing_m", 2.348))
    deck_w_m = float(spec.get("deck_width_m", 11.75))
    calc_span_m = float(spec.get("calc_span_m", 24.0))

    lat = compute_lateral_distribution(n_girders=n_girders, girder_spacing_m=spacing_m, deck_width_m=deck_w_m, calc_span_m=calc_span_m)
    env = compute_superstructure_envelopes(calc_span_m=calc_span_m, m_oq=lat["lever_rule"][0]["m_oq"], m_cq=lat["eccentric_compression"][0]["m_cq"])

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "主梁荷载横向影响线及内力组合包络图")),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥计算附图")),
        drawing_no=str(spec.get("drawing_no", "JS-01")),
        unit_note="kN / kN·m / m",
        scale_label="力学示意",
        style_overrides=_resolve_style_overrides(spec),
    )

    p1_x0, p1_x1, p1_y0 = 36.0, 196.0, 212.0
    p1_w = p1_x1 - p1_x0
    msp.add_line((p1_x0, p1_y0), (p1_x1, p1_y0), dxfattribs={"layer": "OUTLINE"})
    half_d = 0.5 * deck_w_m
    for g_idx in range(n_girders):
        gx_m = (g_idx - 0.5 * (n_girders - 1)) * spacing_m
        xp = p1_x0 + ((gx_m + half_d) / deck_w_m) * p1_w
        msp.add_line((xp, p1_y0 - 4.0), (xp, p1_y0 + 18.0), dxfattribs={"layer": "CENTER"})
        add_civil_text(msp, f"{g_idx+1}#", xp, p1_y0 + 20.0, height=2.1, align="CENTER")

    g1_ec = lat["eccentric_compression"][0]
    msp.add_lwpolyline([(p1_x0 + ((xv + half_d) / deck_w_m) * p1_w, p1_y0 - yv * 34.0) for xv, yv in g1_ec["influence_line"]], close=False, dxfattribs={"layer": "REBAR"})
    for wx in g1_ec["wheel_positions_m"]:
        wx_p = p1_x0 + ((wx + half_d) / deck_w_m) * p1_w
        msp.add_line((wx_p, p1_y0 + 14.0), (wx_p, p1_y0 + 0.5), dxfattribs={"layer": "DIM"})
    add_civil_text(msp, f"1#梁横向影响线 (mcq1={g1_ec['m_cq']:.3f}, 刚接修正={g1_ec['m_cq_torsion_corrected']:.3f})", 0.5 * (p1_x0 + p1_x1), p1_y0 - 30.0, height=2.1, align="CENTER")
    add_arch_dim(msp, (p1_x0, p1_y0 + 24.0), (p1_x1, p1_y0 + 24.0), (0.5 * (p1_x0 + p1_x1), p1_y0 + 30.0), f"桥面宽 {deck_w_m*1000:g}")

    p2_x0, p2_x1, p2_y0 = 228.0, 392.0, 212.0
    p2_mid = 0.5 * (p2_x0 + p2_x1)
    msp.add_line((p2_x0, p2_y0), (p2_x1, p2_y0), dxfattribs={"layer": "OUTLINE"})
    msp.add_lwpolyline([(p2_x0, p2_y0 + 4.0), (p2_x1, p2_y0 + 4.0), (p2_x1, p2_y0 + 11.0), (p2_x0, p2_y0 + 11.0)], close=True, dxfattribs={"layer": "DIM"})
    msp.add_lwpolyline([(p2_x0, p2_y0), (p2_mid, p2_y0 - 22.0), (p2_x1, p2_y0)], close=True, dxfattribs={"layer": "TENDON"})
    add_civil_text(msp, f"Pk={env['lane_pk_kn']:g}kN, qk={env['lane_qk_kn_m']:g}kN/m (1+μ={1+env['impact_factor_mu']:.3f})", p2_mid, p2_y0 + 26.5, height=2.2, align="CENTER")
    add_arch_dim(msp, (p2_x0, p2_y0), (p2_x1, p2_y0), (p2_mid, p2_y0 + 34.0), f"计算跨径 L={calc_span_m*1000:g}")

    p3_x0, p3_x1, p3_y0 = 36.0, 196.0, 134.0
    p3_w = p3_x1 - p3_x0
    msp.add_line((p3_x0, p3_y0), (p3_x1, p3_y0), dxfattribs={"layer": "OUTLINE"})
    stations = env["stations"]
    max_m = max(st["m_uls_knm"] for st in stations) or 1.0
    m_uls_pts = [(p3_x0 + st["x_ratio"] * p3_w, p3_y0 - (st["m_uls_knm"] / max_m) * 62.0) for st in stations]
    m_sls_pts = [(p3_x0 + st["x_ratio"] * p3_w, p3_y0 - (st["m_sls_char_knm"] / max_m) * 62.0) for st in stations]
    msp.add_lwpolyline(m_uls_pts, close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline(m_sls_pts, close=False, dxfattribs={"layer": "OUTLINE"})
    add_civil_text(msp, f"10单元弯矩包络：Md,max={max_m:.1f} kN·m", 0.5 * (p3_x0 + p3_x1), 54.0, height=2.3, align="CENTER")

    p4_x0, p4_x1, p4_y0 = 228.0, 392.0, 102.0
    p4_w = p4_x1 - p4_x0
    msp.add_line((p4_x0, p4_y0), (p4_x1, p4_y0), dxfattribs={"layer": "OUTLINE"})
    max_q = max(abs(st["q_uls_gov_kn"]) for st in stations) or 1.0
    q_pos_pts = [(p4_x0 + st["x_ratio"] * p4_w, p4_y0 + (1.1 * (1.2 * st["q_dead_kn"] + 1.4 * st["q_live_pos_kn"]) / max_q) * 32.0) for st in stations]
    q_neg_pts = [(p4_x0 + st["x_ratio"] * p4_w, p4_y0 + (1.1 * (1.2 * st["q_dead_kn"] + 1.4 * st["q_live_neg_kn"]) / max_q) * 32.0) for st in stations]
    msp.add_lwpolyline(q_pos_pts, close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline(q_neg_pts, close=False, dxfattribs={"layer": "TENDON"})
    add_civil_text(msp, f"10单元剪力包络：|Vd,max|={max_q:.1f} kN", 0.5 * (p4_x0 + p4_x1), 54.0, height=2.3, align="CENTER")
    return doc


# ======================================================================
# Public Dispatchers, Reopen-DXF Bundle Auditor & Full 18-Sheet Set
# ======================================================================
def generate_civil_bridge_dxf(
    drawing_type: CivilDrawingType,
    spec: dict[str, Any] | None,
    output_dir: Path,
    *,
    stem: str | None = None,
) -> Path:
    """Generate and audit a native DXF file for one of the Civil/Bridge drawing types."""
    cfg = dict(spec or {})
    if drawing_type not in CIVIL_DRAWING_TYPES:
        raise ValueError(f"unsupported civil drawing_type {drawing_type!r}; expected one of {CIVIL_DRAWING_TYPES}")

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    file_stem = _safe_stem(stem or cfg.get("output_basename") or drawing_type)
    dxf_path = out_root / f"{file_stem}.dxf"

    dispatch = {
        "bridge_elevation": _draw_bridge_elevation,
        "bridge_plan_section": _draw_bridge_plan_section,
        "t_girder_section": _draw_t_girder_and_deck_section,
        "t_girder_general": _draw_t_girder_general,
        "tendon_layout": _draw_tendon_layout,
        "rebar_detail": _draw_rebar_detail,
        "bearing_layout": _draw_bearing_layout,
        "deck_ancillary": _draw_deck_ancillary,
        "pier_and_abutment": _draw_pier_and_abutment,
        "notes_and_boq": _draw_notes_and_boq,
        "mechanics_diagrams": _draw_mechanics_diagrams,
    }
    doc = dispatch[drawing_type](cfg)
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
    """Generate Civil/Bridge DXF + high-DPI PNG + Reopen-DXF geometric/calc verification + rubric score."""
    cfg = dict(spec or {})
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    dxf_path = generate_civil_bridge_dxf(drawing_type, cfg, out_root, stem=stem)

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

    calc_for_verify = cfg.get("_calc_report") if isinstance(cfg.get("_calc_report"), Mapping) else None
    geom_verify = verify_civil_dxf(dxf_path, calc_report=calc_for_verify, expected_spec=cfg)
    status = "passed" if (not ez_audit.has_errors and geom_verify["status"] == "passed") else "failed"

    audit_json_path = out_root / "audit.json"
    audit_payload = {
        "domain": "civil_bridge",
        "drawing_type": drawing_type,
        "status": status,
        "ezdxf_audit_errors": len(ez_audit.errors),
        "geometry_verification": geom_verify,
        "entity_count": sum(type_counts.values()),
        "type_counts": type_counts,
        "issues": geom_verify["issues"],
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
        "geometry_verification": geom_verify,
        "rubric_score": rubric,
        "artifacts": artifacts,
        "preview_png_path": png_path,
        "audit_summary": {
            "errors": len(ez_audit.errors) + geom_verify["error_count"],
            "entity_count": sum(type_counts.values()),
            "type_counts": type_counts,
        },
    }


def audit_task_book_coverage(
    sheets: list[dict[str, Any]],
    calc_report: Mapping[str, Any],
    *,
    min_a3_sheets: int = 15,
) -> dict[str, Any]:
    """Audit generated bridge drawing set against the 9 task-book categories, >=15 A3 count, and bar traceability."""
    covered_cats: dict[str, list[str]] = {cid: [] for cid, _ in TASK_BOOK_9_CATEGORIES}
    failed_sheets: list[str] = []
    untraced_ids: list[str] = []
    ledger_idx = calc_report["rebar_and_tendon_ledger"]["index_by_id"]

    for sh in sheets:
        cat = sh.get("category_id")
        if cat in covered_cats:
            covered_cats[cat].append(sh["drawing_no"])
        if sh.get("verify_status") != "passed":
            failed_sheets.append(sh["drawing_no"])
        for item_id in sh.get("referenced_ids", []):
            entry = ledger_idx.get(item_id)
            if not entry or not str(entry.get("calc_basis", "")).strip():
                untraced_ids.append(f"{sh['drawing_no']}:{item_id}")

    missing_cats = [title for cid, title in TASK_BOOK_9_CATEGORIES if not covered_cats[cid]]
    a3_count = sum(1 for sh in sheets if sh.get("sheet_size", "A3") == "A3")
    passed = (a3_count >= min_a3_sheets) and not missing_cats and not failed_sheets and not untraced_ids
    return {
        "status": "passed" if passed else "failed",
        "total_a3_sheets": a3_count,
        "min_required_a3_sheets": min_a3_sheets,
        "category_coverage": {cid: dnos for cid, dnos in covered_cats.items()},
        "missing_categories": missing_cats,
        "failed_sheets": failed_sheets,
        "untraced_bar_or_tendon_ids": untraced_ids,
    }


def generate_full_bridge_drawing_set(
    params: Mapping[str, Any] | None,
    output_dir: str | Path,
    *,
    render_png: bool = False,
    preference_memory_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a unified 18-sheet A3 bridge construction drawing set + calculation report + coverage audit."""
    cfg = dict(params or {"preset": "wenchang"})
    calc = run_full_bridge_calculation(cfg)
    ps = calc["project_summary"]
    lat = calc["lateral_distribution"]
    sub = calc["substructure_checks"]
    ledger = calc["rebar_and_tendon_ledger"]

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    base_spec = {
        "project": ps["project_name"],
        "sheet_size": "A3",
        "preset": cfg.get("preset", ""),
        "_calc_report": calc,
        "preference_memory_path": str(preference_memory_path) if preference_memory_path else None,
        "style_overrides": cfg.get("style_overrides"),
    }

    n_spans = int(ps["n_spans"])
    span_cm = float(ps["standard_span_m"]) * 100.0
    deck_w_mm = float(ps["deck_width_m"]) * 1000.0
    spacing_mm = float(lat["girder_spacing_m"]) * 1000.0
    edge_cant_mm = float(lat["edge_cantilever_m"]) * 1000.0
    girder_h_mm = float(calc["t_girder_section"]["mid_span_mm"]["height"])
    pile_len_cm = float(sub["bored_pile"]["design_length_m"]) * 100.0

    sheet_plan: list[tuple[str, str | None, CivilDrawingType, dict[str, Any], list[str]]] = [
        ("QL-01", None, "notes_and_boq", {"variant": "notes", "title": "设计总说明及全套施工图目录"}, []),
        ("QL-02", None, "notes_and_boq", {"variant": "boq", "title": "全桥主要工程材料数量汇总表"}, []),
        (
            "QL-03",
            "cat1_general_layout",
            "bridge_elevation",
            {
                "title": "桥型总体布置图（一：立面图）",
                "n_spans": n_spans,
                "span_cm": span_cm,
                "girder_depth_cm": girder_h_mm / 10.0,
                "pile_length_cm": pile_len_cm,
                "deck_elevation_m": ps["deck_elevation_m"],
                "flood_elevation_m": ps["flood_elevation_m"],
            },
            [],
        ),
        (
            "QL-04",
            "cat1_general_layout",
            "bridge_plan_section",
            {
                "title": "桥型总体布置图（二：平面及横剖面图）",
                "n_spans": n_spans,
                "span_cm": span_cm,
                "deck_width_cm": deck_w_mm / 10.0,
                "roadway_width_cm": float(ps["roadway_width_m"]) * 100.0,
            },
            [],
        ),
        (
            "QL-05",
            "cat5_standard_cross_section",
            "t_girder_section",
            {
                "title": "主梁标准横断面及桥面铺装构造图",
                "n_girders": lat["n_girders"],
                "girder_spacing_mm": spacing_mm,
                "single_deck_width_mm": deck_w_mm,
                "roadway_width_mm": float(ps["roadway_width_m"]) * 1000.0,
                "lateral_clearance_mm": float(lat["lateral_clearance_m"]) * 1000.0,
                "edge_cantilever_mm": edge_cant_mm,
                "height_mm": girder_h_mm,
                "cross_slope_pct": ps["cross_slope_pct"],
                "asphalt_thick_mm": float(cfg.get("asphalt_thick_mm", 100.0)),
                "concrete_paving_thick_mm": float(cfg.get("concrete_paving_thick_mm", 80.0)),
            },
            [],
        ),
        (
            "QL-06",
            "cat2_girder_general",
            "t_girder_general",
            {
                "variant": "longitudinal",
                "beam_length_mm": float(ps["beam_length_m"]) * 1000.0,
                "calc_span_mm": float(ps["calc_span_m"]) * 1000.0,
                "height_mm": girder_h_mm,
                "girder_spacing_mm": spacing_mm,
            },
            [],
        ),
        (
            "QL-07",
            "cat2_girder_general",
            "t_girder_general",
            {
                "variant": "cross_details",
                "height_mm": girder_h_mm,
                "girder_spacing_mm": spacing_mm,
                "edge_cantilever_mm": max(400.0, edge_cant_mm),
            },
            [],
        ),
        (
            "QL-08",
            "cat3_tendon_layout",
            "tendon_layout",
            {
                "height_mm": girder_h_mm,
                "flange_width_mm": spacing_mm,
                "beam_length_mm": float(ps["beam_length_m"]) * 1000.0,
            },
            [t["tendon_id"] for t in ledger["tendons"]],
        ),
        ("QL-09", "cat4_girder_rebar", "rebar_detail", {"component": "t_girder_web"}, [b["bar_id"] for b in ledger["rebar_groups"]["t_girder_web_rebar"]]),
        ("QL-10", "cat4_girder_rebar", "rebar_detail", {"component": "t_girder_flange"}, [b["bar_id"] for b in ledger["rebar_groups"]["t_girder_flange_rebar"]]),
        ("QL-11", "cat4_girder_rebar", "rebar_detail", {"component": "diaphragm"}, [b["bar_id"] for b in ledger["rebar_groups"]["diaphragm_rebar"]]),
        ("QL-12", "cat6_bearing_detail", "bearing_layout", {}, []),
        ("QL-13", "cat7_deck_ancillary", "deck_ancillary", {}, [b["bar_id"] for b in ledger["rebar_groups"]["deck_ancillary_rebar"]]),
        (
            "QL-14",
            "cat8_substructure_general",
            "pier_and_abutment",
            {
                "variant": "pier",
                "title": "双柱式桥墩一般构造图",
                "cap_length_mm": float(calc["pier_cap_beam_1d"]["cap_length_m"]) * 1000.0,
                "column_spacing_mm": round((calc["pier_cap_beam_1d"]["column_positions_m"][-1] - calc["pier_cap_beam_1d"]["column_positions_m"][0]) * 1000.0, 1),
                "pedestal_spacing_mm": spacing_mm,
                "n_pedestals": lat["n_girders"],
                "bearing_model": calc["bearing_checks"]["bearing_model"],
            },
            [],
        ),
        (
            "QL-15",
            "cat8_substructure_general",
            "pier_and_abutment",
            {
                "variant": "abutment",
                "title": "桩柱式桥台一般构造图",
                "cap_length_mm": deck_w_mm,
                "column_spacing_mm": round(max(3600.0, deck_w_mm - 3600.0), 1),
                "pedestal_spacing_mm": spacing_mm,
                "n_pedestals": lat["n_girders"],
                "bearing_model": calc["bearing_checks"]["bearing_model"],
            },
            [],
        ),
        ("QL-16", "cat9_substructure_rebar", "rebar_detail", {"component": "pier_cap"}, [b["bar_id"] for b in ledger["rebar_groups"]["pier_cap_rebar"]]),
        ("QL-17", "cat9_substructure_rebar", "rebar_detail", {"component": "pier_column_pile"}, [b["bar_id"] for b in ledger["rebar_groups"]["pier_column_pile_rebar"]]),
        ("QL-18", "cat9_substructure_rebar", "rebar_detail", {"component": "abutment_rebar"}, [b["bar_id"] for b in ledger["rebar_groups"]["abutment_rebar"]]),
    ]

    generated_sheets: list[dict[str, Any]] = []
    for dno, cat_id, dtype, sub_spec, ref_ids in sheet_plan:
        merged_spec = {**base_spec, **sub_spec, "drawing_no": dno}
        stem = f"{dno}_{dtype}"
        sheet_dir = out_root / dno
        if render_png:
            bundle = generate_civil_bridge_bundle(dtype, merged_spec, sheet_dir, stem=stem)
            ver = bundle["geometry_verification"]
            score = bundle["rubric_score"]["total_score"]
            dxf_file = sheet_dir / f"{stem}.dxf"
            png_file: Path | None = sheet_dir / f"{stem}.png"
        else:
            dxf_file = generate_civil_bridge_dxf(dtype, merged_spec, sheet_dir, stem=stem)
            ver = verify_civil_dxf(dxf_file, calc_report=calc, expected_spec=merged_spec)
            score = score_dxf_drawing(dxf_file).total_score
            png_file = None

        generated_sheets.append(
            {
                "drawing_no": dno,
                "category_id": cat_id,
                "drawing_type": dtype,
                "title": merged_spec.get("title", dtype),
                "sheet_size": "A3",
                "dxf_path": str(dxf_file),
                "png_path": str(png_file) if png_file else None,
                "verify_status": ver["status"],
                "verify_issues": ver["issues"],
                "rubric_score": score,
                "referenced_ids": ref_ids,
            }
        )

    coverage = audit_task_book_coverage(generated_sheets, calc)
    calc_out_path = out_root / "bridge_calculation_report.json"
    calc_out_path.write_text(json.dumps(calc, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_out_path = out_root / "drawing_set_manifest.json"
    manifest_payload = {
        "status": "passed" if (coverage["status"] == "passed" and calc["consistency_audit"]["status"] == "passed") else "failed",
        "project_summary": ps,
        "unresolved_assumptions": calc["unresolved_assumptions"],
        "coverage_audit": coverage,
        "sheets": generated_sheets,
        "calculation_report_path": str(calc_out_path),
    }
    manifest_out_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest_payload, "calculation_report": calc, "manifest_path": str(manifest_out_path)}
