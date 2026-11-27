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
    draw_section_cut_symbol,
    draw_slope_arrow,
    draw_tendon_schedule_table,
    export_civil_a3_print_pdf_png,
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

DEFAULT_18_SHEET_INDEX: tuple[tuple[str, str, str], ...] = (
    ("QL-01", "设计总说明及全套施工图目录", "无比例"),
    ("QL-02", "全桥主要工程材料数量汇总表", "表格"),
    ("QL-03", "桥型总体布置图（一：立面图）", "1:400"),
    ("QL-04", "桥型总体布置图（二：平面及横剖面图）", "1:400/1:100"),
    ("QL-05", "主梁标准横断面及桥面铺装构造图", "1:40/1:25/1:10"),
    ("QL-06", "25m预制T梁一般构造图（一：纵立面及平面）", "1:80"),
    ("QL-07", "25m预制T梁一般构造图（二：边中梁变截面详图）", "1:25"),
    ("QL-08", "T梁预应力钢束构造及张拉布置图", "1:15/1:90"),
    ("QL-09", "主梁梁肋普通钢筋构造图", "1:20/1:80"),
    ("QL-10", "主梁翼缘板及湿接缝钢筋构造图", "1:20/1:10"),
    ("QL-11", "主梁中横隔梁与端横隔梁钢筋构造图", "1:65/1:25"),
    ("QL-12", "板式橡胶支座及支座垫石构造布置图", "1:5/1:10"),
    ("QL-13", "桥面附属结构构造图（护栏/伸缩缝/泄水管/搭板）", "1:10/1:15/1:40/1:60"),
    ("QL-14", "双柱式桥墩一般构造图", "1:80"),
    ("QL-15", "桩柱式桥台一般构造图", "1:80"),
    ("QL-16", "桥墩盖梁普通钢筋构造图", "1:65/1:30/1:35"),
    ("QL-17", "桥墩墩柱、系梁及钻孔桩钢筋构造图", "图示比例"),
    ("QL-18", "桥台台帽、耳背墙及桩基钢筋构造图", "图示比例"),
)

_UNRESOLVED_FIELD_LABELS_ZH: dict[str, str] = {
    "flood_elevation_m": "设计洪水位",
    "deck_elevation_m": "桥面设计高程",
    "hydrology": "实测河床水文断面",
    "soil_layers": "钻孔地质分层及摩阻力",
    "pile_length_m": "持力层与设计桩长",
    "calc_span_m": "支座定位与实际计算跨径",
}


def _wrap_zh_text(text: str, width_chars: int = 38) -> list[str]:
    s = str(text).strip()
    if len(s) <= width_chars:
        return [s]
    out: list[str] = []
    cur = ""
    for ch in s:
        cur += ch
        if len(cur) >= width_chars and ch in "，。；、）) ":
            out.append(cur.strip())
            cur = "    "
        elif len(cur) >= width_chars + 4:
            out.append(cur.strip())
            cur = "    "
    if cur.strip():
        out.append(cur.rstrip())
    return out

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
    merged: dict[str, Any] = {}
    album_src = spec.get("album_template") or spec.get("template_memory_dir")
    mem_path = spec.get("preference_memory_path")
    if album_src is None and mem_path:
        mem_parent = Path(str(mem_path)).expanduser().resolve().parent
        if (mem_parent / "album-template.json").is_file() or (mem_parent / "template-bundle.json").is_file():
            album_src = mem_parent
    if album_src is not None:
        from cad_skill_core.standards.template_learner import album_template_to_style_overrides, load_album_template

        merged.update(album_template_to_style_overrides(load_album_template(album_src)))
    if mem_path:
        from cad_skill_core.standards.preference_memory import resolve_civil_preferences

        learned = resolve_civil_preferences(mem_path, part_family=str(spec.get("part_family", "civil_bridge")))
        merged.update(learned)
    # Explicit style_overrides win over learned templates/preferences; project dimensions are never touched
    merged.update(overrides)
    return merged


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
    abut_pile_len_cm = float(spec.get("abutment_pile_length_cm", pile_len_cm))
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

    # Left & Right Abutments (backwall + cap + approach slab silhouette) + True-Scale Piles
    abut_h_cm = 315.0
    abut_pile_cm = abut_pile_len_cm
    for rxa0, rxa1, is_left in ((rx_abut_l, rx_span_0, True), (rx_span_end, rx_abut_r, False)):
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
        rx_bw = rxa1 - 55.0 if is_left else rxa0 + 55.0
        msp.add_line(vb.pt(rx_bw, ry_deck - 175.0), vb.pt(rx_bw, ry_deck), dxfattribs={"layer": "THIN"})
        msp.add_line(vb.pt(rxa0, ry_deck - 175.0), vb.pt(rxa1, ry_deck - 175.0), dxfattribs={"layer": "THIN"})
        rxc_p = (rxa1 - 90.0) if is_left else (rxa0 + 90.0)
        ry_p_top = ry_deck - abut_h_cm
        ry_p_bot = ry_p_top - abut_pile_cm
        msp.add_line(vb.pt(rxc_p - 0.5 * pile_d_cm, ry_p_top), vb.pt(rxc_p - 0.5 * pile_d_cm, ry_p_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb.pt(rxc_p + 0.5 * pile_d_cm, ry_p_top), vb.pt(rxc_p + 0.5 * pile_d_cm, ry_p_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb.x(rxc_p), vb.y(ry_p_bot), max(1.4, vb.span(pile_d_cm)))

    # Natural riverbed valley profile + non-overlapping ground & flood elevation markers!
    terrain_pts: list[tuple[float, float]] = [vb.pt(rx_abut_l - 280.0, ry_deck - 315.0)]
    pier_ground_rys: list[float] = []
    for idx, rxp in enumerate(pier_rxs):
        t_norm = idx / max(1, n_spans)
        depth_cm = 330.0 + 500.0 * math.sin(math.pi * t_norm)
        ryg = ry_deck - depth_cm
        pier_ground_rys.append(ryg)
        terrain_pts.append(vb.pt(rxp, ryg))
    terrain_pts.append(vb.pt(rx_abut_r + 280.0, ry_deck - 315.0))
    msp.add_lwpolyline(terrain_pts, close=False, dxfattribs={"layer": "TERRAIN"})
    # Place ground symbol cleanly on right bank embankment shoulder (zero overlap with water level!)
    gx_r0 = vb.x(rx_abut_r + 20.0)
    gy_r0 = vb.y(ry_deck - 315.0)
    draw_ground_line(msp, gx_r0, gy_r0, gx_r0 + 18.0, gy_r0, label="河床地面线", scale=0.80)

    # True-scale Design Flood Water Line (strictly (deck_elev_m - flood_elev_m)*1000/scale_denom mm below deck!)
    ry_water = flood_elev_m * 100.0
    msp.add_line(vb.pt(rx_span_0 + 220.0, ry_water), vb.pt(rx_span_end - 220.0, ry_water), dxfattribs={"layer": "CENTER"})
    # Place flood elevation marker over mid-river channel (cleanly separated from piers & ground label!)
    rx_flood_lbl = 0.22 * span_cm if n_spans % 2 == 0 else 0.0
    draw_elevation_marker(msp, vb.x(rx_flood_lbl), vb.y(ry_water), flood_elev_m, prefix="设计洪水位 ", scale=0.80)
    draw_elevation_marker(msp, vb.x(rx_abut_l + 40.0), vb.y(ry_deck), deck_elev_m, prefix="桥面 ", scale=0.80)

    # Intermediate Piers (strictly true 1:scale_denom — linked to real cap & column heights!)
    cap_w_cm = float(spec.get("cap_width_cm", 160.0))
    cap_h_cm = float(spec.get("cap_height_cm", 140.0))
    col_h_cm = float(spec.get("column_height_cm", 550.0))
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
        ry_tie_top = min(ry_cap_bot - col_h_cm, ryg - 40.0)
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
        f"3. 下部桥墩采用双柱式墩（D={pier_col_d_cm:g}cm），钻孔灌注桩（D={pile_d_cm:g}cm，桥墩桩长 {pile_len_cm:g}cm，桥台桩长 {abut_pile_cm:g}cm）。",
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
        (
            f"截面效率指标 ρ = {mp['efficiency_rho']:.3f} ≥ 0.50 (合格)"
            if mp["passes_efficiency"]
            else f"截面效率指标 ρ = {mp['efficiency_rho']:.3f} < 0.50 (超限不合格!)"
        ),
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
            title=str(spec.get("title", "25m预制T梁一般构造图（二：边中梁变截面及横隔板详图）")),
            project=str(spec.get("project", "信阳市文昌中桥设计")),
            drawing_no=str(spec.get("drawing_no", "QL-07")),
            unit_note="mm",
            scale_label="1:25",
            style_overrides=_resolve_style_overrides(spec),
        )
        # 1. Left: Asymmetric Edge Girder Mid-Span (1:25) with complete flange/haunch/web/bulb dimensions
        vb_edge = ViewBox("edge_girder_mid", 80.0, 88.0, 25.0, "mm")
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
        msp.add_line((vb_edge.x(0.0), vb_edge.y(-100.0)), (vb_edge.x(0.0), vb_edge.y(girder_h_mm + 120.0)), dxfattribs={"layer": "CENTER"})
        add_arch_dim(msp, vb_edge.pt(-edge_cant_mm, girder_h_mm), vb_edge.pt(0.0, girder_h_mm), (vb_edge.x(-0.5 * edge_cant_mm), vb_edge.y(girder_h_mm) + 8.0), f"{edge_cant_mm:g}")
        add_arch_dim(msp, vb_edge.pt(0.0, girder_h_mm), vb_edge.pt(inner_half, girder_h_mm), (vb_edge.x(0.5 * inner_half), vb_edge.y(girder_h_mm) + 8.0), f"{inner_half:g}")
        add_arch_dim(msp, vb_edge.pt(-0.5 * bulb_w_mm, 0.0), vb_edge.pt(0.5 * bulb_w_mm, 0.0), (vb_edge.x(0.0), vb_edge.y(0.0) - 6.5), f"{bulb_w_mm:g}")
        add_arch_dim(msp, vb_edge.pt(-100.0, 900.0), vb_edge.pt(100.0, 900.0), (vb_edge.x(0.0), vb_edge.y(900.0) + 5.5), "200")
        add_arch_dim(msp, vb_edge.pt(inner_half, 0.0), vb_edge.pt(inner_half, girder_h_mm), (vb_edge.x(inner_half) + 8.5, vb_edge.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
        add_arch_dim(msp, vb_edge.pt(-edge_cant_mm, girder_h_mm - 200.0), vb_edge.pt(-edge_cant_mm, girder_h_mm), (vb_edge.x(-edge_cant_mm) - 6.5, vb_edge.y(girder_h_mm - 100.0)), "200", angle=90.0)
        add_arch_dim(msp, vb_edge.pt(-0.5 * bulb_w_mm, 0.0), vb_edge.pt(-0.5 * bulb_w_mm, 200.0), (vb_edge.x(-0.5 * bulb_w_mm) - 6.5, vb_edge.y(100.0)), "200", angle=90.0)
        add_civil_text(msp, "一、外边梁跨中预制截面 (1:25)", vb_edge.x(0.0), 68.0, height=2.4, align="CENTER")
        vb_edge.register(doc, msp, paper_bounds=(vb_edge.x(-edge_cant_mm) - 10.0, 66.0, vb_edge.x(inner_half) + 13.0, vb_edge.y(girder_h_mm) + 14.0))

        # 2. Center: Girder Support Thickened End Section (1:25)
        vb_sup = ViewBox("girder_support_end", 206.0, 88.0, 25.0, "mm")
        end_poly = build_t_girder_polygon_mm(flange_width_mm=2.0 * inner_half, height_mm=girder_h_mm, web_thick_mm=bulb_w_mm, bulb_width_mm=bulb_w_mm, bulb_height_mm=0.0)
        msp.add_lwpolyline([vb_sup.pt(x, y) for x, y in end_poly], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line((vb_sup.x(0.0), vb_sup.y(-100.0)), (vb_sup.x(0.0), vb_sup.y(girder_h_mm + 120.0)), dxfattribs={"layer": "CENTER"})
        add_arch_dim(msp, vb_sup.pt(-0.5 * bulb_w_mm, 0.0), vb_sup.pt(0.5 * bulb_w_mm, 0.0), (vb_sup.x(0.0), vb_sup.y(0.0) - 6.5), f"加厚肋 {bulb_w_mm:g}")
        add_arch_dim(msp, vb_sup.pt(-inner_half, girder_h_mm), vb_sup.pt(inner_half, girder_h_mm), (vb_sup.x(0.0), vb_sup.y(girder_h_mm) + 8.0), f"{2.0*inner_half:g}")
        add_arch_dim(msp, vb_sup.pt(inner_half, 0.0), vb_sup.pt(inner_half, girder_h_mm), (vb_sup.x(inner_half) + 8.5, vb_sup.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
        add_civil_text(msp, "二、中梁支点加厚截面 (1:25)", vb_sup.x(0.0), 68.0, height=2.4, align="CENTER")
        vb_sup.register(doc, msp, paper_bounds=(vb_sup.x(-inner_half) - 8.0, 66.0, vb_sup.x(inner_half) + 13.0, vb_sup.y(girder_h_mm) + 14.0))

        # 3. Right (above Title Block): Precast Diaphragm Outrigger Wing & Wet-Joint Splice Elevation (1:25)
        vb_dw = ViewBox("diaphragm_wing_detail", 336.0, 126.0, 25.0, "mm")
        dia_h = 1350.0
        # T-girder web core + bilateral precast diaphragm wings [-inner_half .. +inner_half] x [girder_h - dia_h .. girder_h]
        msp.add_lwpolyline(
            [
                vb_dw.pt(-inner_half, girder_h_mm),
                vb_dw.pt(inner_half, girder_h_mm),
                vb_dw.pt(inner_half, girder_h_mm - dia_h),
                vb_dw.pt(100.0, girder_h_mm - dia_h),
                vb_dw.pt(100.0, 0.0),
                vb_dw.pt(-100.0, 0.0),
                vb_dw.pt(-100.0, girder_h_mm - dia_h),
                vb_dw.pt(-inner_half, girder_h_mm - dia_h),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        msp.add_line(vb_dw.pt(-inner_half, girder_h_mm - 200.0), vb_dw.pt(inner_half, girder_h_mm - 200.0), dxfattribs={"layer": "THIN"})
        # Adjacent wet-joint splice zone [inner_half .. inner_half + 400]
        msp.add_lwpolyline(
            [
                vb_dw.pt(inner_half, girder_h_mm - dia_h),
                vb_dw.pt(inner_half + 400.0, girder_h_mm - dia_h),
                vb_dw.pt(inner_half + 400.0, girder_h_mm),
                vb_dw.pt(inner_half, girder_h_mm),
            ],
            close=True,
            dxfattribs={"layer": "HIDDEN"},
        )
        add_arch_dim(msp, vb_dw.pt(inner_half, girder_h_mm), vb_dw.pt(inner_half + 400.0, girder_h_mm), (vb_dw.x(inner_half + 200.0), vb_dw.y(girder_h_mm) + 7.5), "湿接缝 400")
        add_arch_dim(msp, vb_dw.pt(-inner_half, girder_h_mm - dia_h), vb_dw.pt(-inner_half, girder_h_mm), (vb_dw.x(-inner_half) - 7.5, vb_dw.y(girder_h_mm - 0.5 * dia_h)), f"{dia_h:g}", angle=90.0)
        add_civil_text(msp, "三、中横隔板预制及湿接缝构造 (1:25)", vb_dw.x(100.0), 112.0, height=2.3, align="CENTER")
        vb_dw.register(doc, msp, paper_bounds=(286.0, 110.0, 404.0, 214.0))
        return doc

    calc = _ensure_calc_report(spec)
    sup_lay = calc.get("support_layout") or {}
    if "beam_length_mm" not in spec and sup_lay.get("beam_length_mm"):
        beam_len_mm = float(sup_lay["beam_length_mm"])
    if "calc_span_mm" not in spec and sup_lay.get("calc_span_mm"):
        calc_span_mm = float(sup_lay["calc_span_mm"])
    gap_mm = float(sup_lay.get("expansion_gap_mm", max(20.0, round(float(calc["project_summary"]["standard_span_m"]) * 1000.0 - beam_len_mm, 1))))

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "25m预制T梁一般构造图（一：纵立面及平面布置）")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-06")),
        unit_note="mm",
        scale_label="1:25 / 1:80",
        style_overrides=_resolve_style_overrides(spec),
    )
    cx = 0.5 * (b["ix0"] + b["ix1"])
    vb = ViewBox("girder_longitudinal", cx, 196.0, 80.0, "mm")
    half_l = 0.5 * beam_len_mm
    half_c = 0.5 * calc_span_mm
    end_off = round(half_l - half_c, 1)
    pier_pitch_mm = round(2.0 * end_off + gap_mm, 1)
    dia_sp = 0.25 * calc_span_mm
    # Elevation rectangle + flange bottom line + bulb top line + end web thickening transition + 5 diaphragms
    msp.add_lwpolyline([vb.pt(-half_l, 0.0), vb.pt(half_l, 0.0), vb.pt(half_l, girder_h_mm), vb.pt(-half_l, girder_h_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb.pt(-half_l, girder_h_mm - 200.0), vb.pt(half_l, girder_h_mm - 200.0), dxfattribs={"layer": "THIN"})
    msp.add_line(vb.pt(-half_c + 2200.0, 200.0), vb.pt(half_c - 2200.0, 200.0), dxfattribs={"layer": "THIN"})
    # End thickened web transition lines (1200 straight + 1000 taper from support)
    for sx_end in (-1.0, 1.0):
        rx_s1 = sx_end * (half_c - 1200.0)
        rx_s2 = sx_end * (half_c - 2200.0)
        msp.add_line(vb.pt(rx_s1, 0.0), vb.pt(rx_s1, girder_h_mm - 200.0), dxfattribs={"layer": "HIDDEN"})
        msp.add_line(vb.pt(rx_s1, 0.0), vb.pt(rx_s2, 200.0), dxfattribs={"layer": "HIDDEN"})
    for k in range(5):
        rxd = -half_c + k * dia_sp
        msp.add_lwpolyline(
            [vb.pt(rxd - 100.0, girder_h_mm - 1350.0), vb.pt(rxd + 100.0, girder_h_mm - 1350.0), vb.pt(rxd + 100.0, girder_h_mm - 200.0), vb.pt(rxd - 100.0, girder_h_mm - 200.0)],
            close=True,
            dxfattribs={"layer": "THIN"},
        )
        if 0 < k < 4:
            add_arch_dim(
                msp,
                vb.pt(-half_c + (k - 1) * dia_sp, 0.0),
                vb.pt(rxd, 0.0),
                (vb.x(-half_c + (k - 0.5) * dia_sp), vb.y(0.0) - 6.5),
                f"{dia_sp:g}",
            )
    add_arch_dim(msp, vb.pt(-half_c + 3.0 * dia_sp, 0.0), vb.pt(half_c, 0.0), (vb.x(half_c - 0.5 * dia_sp), vb.y(0.0) - 6.5), f"{dia_sp:g}")
    add_arch_dim(msp, vb.pt(-half_c, 0.0), vb.pt(-half_c + dia_sp, 0.0), (vb.x(-half_c + 0.5 * dia_sp), vb.y(0.0) - 6.5), f"{dia_sp:g}")
    draw_bearing_symbol(msp, vb.x(-half_c), vb.y(0.0) - 2.0, kind="fixed", size=2.8)
    draw_bearing_symbol(msp, vb.x(half_c), vb.y(0.0) - 2.0, kind="sliding_longitudinal", size=2.8)

    add_arch_dim(msp, vb.pt(-half_l, girder_h_mm), vb.pt(-half_c, girder_h_mm), (vb.x(-half_l + 0.5 * end_off), vb.y(girder_h_mm) + 8.0), f"{end_off:g}")
    add_arch_dim(msp, vb.pt(-half_c, girder_h_mm), vb.pt(half_c, girder_h_mm), (cx, vb.y(girder_h_mm) + 8.0), f"计算跨径 {calc_span_mm:g}")
    add_arch_dim(msp, vb.pt(half_c, girder_h_mm), vb.pt(half_l, girder_h_mm), (vb.x(half_c + 0.5 * end_off), vb.y(girder_h_mm) + 8.0), f"{end_off:g}")
    add_arch_dim(msp, vb.pt(-half_l, girder_h_mm), vb.pt(half_l, girder_h_mm), (cx, vb.y(girder_h_mm) + 16.5), f"预制主梁全长 {beam_len_mm:g}")
    add_arch_dim(msp, vb.pt(half_l, 0.0), vb.pt(half_l, girder_h_mm), (vb.x(half_l) + 8.5, vb.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
    add_civil_text(msp, "预制T梁纵立面图 (1:80，端横隔板2道、中横隔板3道，间距4×6000)", cx, vb.y(0.0) - 14.5, height=2.4, align="CENTER")

    # Plan view directly below in same 1:80 ViewBox, showing precast flange + 5 diaphragm stubs + 400mm wet joint!
    ry_p0 = -4800.0
    pre_w = spacing_mm - 400.0
    msp.add_lwpolyline([vb.pt(-half_l, ry_p0 - 0.5 * pre_w), vb.pt(half_l, ry_p0 - 0.5 * pre_w), vb.pt(half_l, ry_p0 + 0.5 * pre_w), vb.pt(-half_l, ry_p0 + 0.5 * pre_w)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_line(vb.pt(-half_l, ry_p0 + 0.5 * spacing_mm), vb.pt(half_l, ry_p0 + 0.5 * spacing_mm), dxfattribs={"layer": "HIDDEN"})
    msp.add_line(vb.pt(-half_l - 300.0, ry_p0), vb.pt(half_l + 300.0, ry_p0), dxfattribs={"layer": "CENTER"})
    for k in range(5):
        rxd = -half_c + k * dia_sp
        msp.add_line(vb.pt(rxd, ry_p0 - 0.5 * spacing_mm), vb.pt(rxd, ry_p0 + 0.5 * spacing_mm), dxfattribs={"layer": "THIN"})
    add_arch_dim(msp, vb.pt(-half_l, ry_p0 - 0.5 * pre_w), vb.pt(-half_l, ry_p0 + 0.5 * pre_w), (vb.x(-half_l) - 8.5, vb.y(ry_p0)), f"预制宽 {pre_w:g}", angle=90.0)
    add_arch_dim(msp, vb.pt(half_l, ry_p0 + 0.5 * pre_w), vb.pt(half_l, ry_p0 + 0.5 * spacing_mm), (vb.x(half_l) + 8.5, vb.y(ry_p0 + 0.5 * (pre_w + spacing_mm))), "200", angle=90.0)
    add_civil_text(msp, "预制T梁顶面及横隔板平面图 (1:80，虚线为半侧湿接缝中心)", cx, vb.y(ry_p0 - 0.5 * pre_w) - 9.5, height=2.4, align="CENTER")
    vb.register(doc, msp, paper_bounds=(vb.x(-half_l) - 15.0, vb.y(ry_p0 - 0.5 * pre_w) - 11.0, vb.x(half_l) + 15.0, vb.y(girder_h_mm) + 21.0))

    # Lower-Left Inset (1:25): Shared Pier-Top Longitudinal Support Chain (480 + 40 + 480 = 1000 mm)
    vb_sc = ViewBox("girder_pier_support_chain", 112.0, 62.0, 25.0, "mm")
    g_half = 0.5 * gap_mm
    b_x = g_half + end_off  # e.g. 20 + 480 = 500 mm
    for sx_g in (-1.0, 1.0):
        x_in_g = sx_g * g_half
        x_out_g = sx_g * (b_x + 360.0)
        msp.add_lwpolyline(
            [vb_sc.pt(x_in_g, 0.0), vb_sc.pt(x_out_g, 0.0), vb_sc.pt(x_out_g, 420.0), vb_sc.pt(x_in_g, 420.0)],
            close=False,
            dxfattribs={"layer": "OUTLINE"},
        )
        msp.add_line(vb_sc.pt(x_in_g, 0.0), vb_sc.pt(x_in_g, 420.0), dxfattribs={"layer": "OUTLINE"})
        draw_bearing_symbol(msp, vb_sc.x(sx_g * b_x), vb_sc.y(0.0) - 1.8, kind="fixed" if sx_g < 0 else "sliding_longitudinal", size=2.6)
        msp.add_line((vb_sc.x(sx_g * b_x), vb_sc.y(-220.0)), (vb_sc.x(sx_g * b_x), vb_sc.y(460.0)), dxfattribs={"layer": "CENTER"})
    msp.add_line((vb_sc.x(0.0), vb_sc.y(-260.0)), (vb_sc.x(0.0), vb_sc.y(500.0)), dxfattribs={"layer": "CENTER"})
    add_arch_dim(msp, vb_sc.pt(-b_x, 420.0), vb_sc.pt(-g_half, 420.0), (vb_sc.x(-0.5 * (b_x + g_half)), vb_sc.y(420.0) + 6.0), f"{end_off:g}")
    add_arch_dim(msp, vb_sc.pt(-g_half, 420.0), vb_sc.pt(g_half, 420.0), (vb_sc.x(0.0), vb_sc.y(420.0) + 6.0), f"{gap_mm:g}")
    add_arch_dim(msp, vb_sc.pt(g_half, 420.0), vb_sc.pt(b_x, 420.0), (vb_sc.x(0.5 * (b_x + g_half)), vb_sc.y(420.0) + 6.0), f"{end_off:g}")
    add_arch_dim(msp, vb_sc.pt(-b_x, -180.0), vb_sc.pt(b_x, -180.0), (vb_sc.x(0.0), vb_sc.y(-180.0) - 5.5), f"墩顶支座纵距 {pier_pitch_mm:g}")
    add_civil_text(
        msp,
        f"中墩梁端支承定位尺寸链 (1:25)：{end_off:g}+{gap_mm:g}+{end_off:g}={pier_pitch_mm:g}mm",
        vb_sc.x(0.0),
        36.5,
        height=2.0,
        align="CENTER",
    )
    vb_sc.register(doc, msp, paper_bounds=(44.0, 35.0, 184.0, 96.0))
    attach_sheet_metadata(doc, msp, "support_layout", dict(sup_lay))
    return doc


# ======================================================================
# 5. Prestressing Tendon Layout (QL-08, Live Reactive Linkage to Calc!)
# ======================================================================
def _draw_tendon_layout(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    from .bridge_tendon_and_support import build_bridge_tendon_3d_geometry

    variant = str(spec.get("variant", "layout")).strip().lower()
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
    calc_span_mm = float(calc["project_summary"]["calc_span_m"]) * 1000.0
    half_span_mm = float(spec.get("half_beam_length_mm", 0.5 * beam_len_mm))

    # Read shared 3D tendon geometry from calc_report (recomputing only if spec overrode n_tendons/height_mm)
    t3d = calc.get("tendon_3d_geometry")
    if (
        not t3d
        or len(t3d.get("tendons", [])) != n_tendons
        or not math.isclose(float(t3d.get("beam_length_mm", beam_len_mm)), beam_len_mm, abs_tol=1.0)
    ):
        t3d = build_bridge_tendon_3d_geometry(
            n_tendons=n_tendons,
            strands_per_tendon=strands_per,
            beam_length_mm=beam_len_mm,
            calc_span_mm=calc_span_mm,
            girder_height_mm=girder_h_mm,
            bulb_width_mm=bulb_w_mm,
        )
        for tp_tmp in t3d["tendons"]:
            tp_tmp.pop("_eval_fn", None)

    t_profiles = list(t3d["tendons"])[:n_tendons]
    mid_ducts_mm: list[tuple[str, float, float]] = [
        (str(tp["tendon_id"]), float(tp["mid_z_mm"]), float(tp["mid_y_mm"])) for tp in t_profiles
    ]
    end_ducts_mm: list[tuple[str, float, float]] = [
        (str(tp["tendon_id"]), float(tp["end_z_mm"]), float(tp["end_y_mm"])) for tp in t_profiles
    ]
    end_ys_mm = [float(tp["end_y_mm"]) for tp in t_profiles]
    end_step_mm = round(end_ys_mm[1] - end_ys_mm[0], 1) if len(end_ys_mm) >= 2 else 0.0
    duct_per_girder_m = float(t3d["duct_net_length_per_girder_m"])

    # ---- Sheet 09: Tendon Centroid 3D Coordinates & Positioning Rebar Detail (`variant="coordinates"`) ----
    if variant == "coordinates":
        doc, msp, b = setup_civil_dxf_document(
            sheet_size=str(spec.get("sheet_size", "A3")),
            title=str(spec.get("title", "预制T梁预应力钢束束心坐标及定位钢筋图")),
            project=str(spec.get("project", calc["project_summary"]["project_name"])),
            drawing_no=str(spec.get("drawing_no", "09")),
            unit_note="mm",
            scale_label="1:10 / 1:70",
            style_overrides=_resolve_style_overrides(spec),
        )
        # 1. Top: 1:70 Half-Span Tendon Elevation & Positioning Grid (`DW1` @800/@500)
        vb_ce = ViewBox("tendon_coord_elev", 34.0, 206.0, 70.0, "mm")
        brg_off_09 = max(200.0, round(0.5 * (beam_len_mm - calc_span_mm), 1))
        msp.add_lwpolyline(
            [vb_ce.pt(0.0, 0.0), vb_ce.pt(half_span_mm, 0.0), vb_ce.pt(half_span_mm, girder_h_mm), vb_ce.pt(0.0, girder_h_mm)],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line((vb_ce.x(brg_off_09), vb_ce.y(-180.0)), (vb_ce.x(brg_off_09), vb_ce.y(girder_h_mm + 180.0)), dxfattribs={"layer": "CENTER"})
        for tp in t_profiles:
            pts_c09 = [vb_ce.pt(float(pt["x_mm"]), float(pt["y_mm"])) for pt in tp["half_span_curve_mm"]]
            msp.add_lwpolyline(pts_c09, close=False, dxfattribs={"layer": "TENDON"})
            add_civil_text(msp, str(tp["tendon_id"]), pts_c09[0][0] - 4.5, pts_c09[0][1] - 0.8, height=1.75)
        for kx_st in (0.0, brg_off_09, 2680.0, 2980.0, 6480.0, half_span_mm):
            if kx_st <= half_span_mm:
                msp.add_line(vb_ce.pt(kx_st, 0.0), vb_ce.pt(kx_st, girder_h_mm), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_ce.pt(0.0, girder_h_mm), vb_ce.pt(half_span_mm, girder_h_mm), (vb_ce.x(0.5 * half_span_mm), vb_ce.y(girder_h_mm) + 7.5), f"半梁长 {half_span_mm:g}")
        add_arch_dim(msp, vb_ce.pt(half_span_mm, 0.0), vb_ce.pt(half_span_mm, girder_h_mm), (vb_ce.x(half_span_mm) + 7.5, vb_ce.y(0.5 * girder_h_mm)), f"{girder_h_mm:g}", angle=90.0)
        add_civil_text(msp, "一、半跨预应力钢束定位网格纵断面 (1:70，直线段≤800mm、曲线段≤500mm设DW1定位网)", vb_ce.x(0.5 * half_span_mm), 192.0, height=2.15, align="CENTER")
        vb_ce.register(doc, msp, paper_bounds=(26.0, 190.0, 232.0, 252.0))

        # 2. Top-Right: Unified Confirmed Tendon Centroid (x, y, z), Bend Radius R, End Angle θ & Duct Net Length Table!
        tx0, ty_top, tw = 238.0, 256.0, 166.0
        n_trows = len(t_profiles)
        t_hdr_h, t_row_h = 11.0, min(11.5, 92.0 / max(1, n_trows))
        ty_bot = ty_top - t_hdr_h - n_trows * t_row_h
        msp.add_lwpolyline([(tx0, ty_bot), (tx0 + tw, ty_bot), (tx0 + tw, ty_top), (tx0, ty_top)], close=True, dxfattribs={"layer": "FRAME"})
        msp.add_line((tx0, ty_top - 5.2), (tx0 + tw, ty_top - 5.2), dxfattribs={"layer": "FRAME"})
        msp.add_line((tx0, ty_top - t_hdr_h), (tx0 + tw, ty_top - t_hdr_h), dxfattribs={"layer": "FRAME"})
        add_civil_text(msp, "二、钢束束心三维坐标(y/z)、倾角及波纹管净长表", tx0 + 0.5 * tw, ty_top - 3.8, height=2.05, align="CENTER")
        t_cols = (tx0, tx0 + 14.0, tx0 + 38.0, tx0 + 62.0, tx0 + 88.0, tx0 + 112.0, tx0 + 132.0, tx0 + 148.0, tx0 + tw)
        for cx_t in t_cols[1:-1]:
            msp.add_line((cx_t, ty_bot), (cx_t, ty_top - 5.2), dxfattribs={"layer": "THIN"})
        t_hdrs = ("束号", "梁端x=0", f"支点{brg_off_09:g}", "x=2980", "跨中L/2", "R(mm)", "端角θ", "净长(m)")
        for ci, hs in enumerate(t_hdrs):
            add_civil_text(msp, hs, 0.5 * (t_cols[ci] + t_cols[ci + 1]), ty_top - t_hdr_h + 1.6, height=1.62, align="CENTER")

        for ri, tp in enumerate(t_profiles):
            ry_t = ty_top - t_hdr_h - (ri + 1) * t_row_h
            if ri < n_trows - 1:
                msp.add_line((tx0, ry_t), (tx0 + tw, ry_t), dxfattribs={"layer": "THIN"})
            st_by_x = {round(float(s["x_mm"]), 1): s for s in tp["station_table"]}
            s0 = st_by_x.get(0.0, {"y_mm": tp["end_y_mm"], "z_mm": tp["end_z_mm"]})
            sb = st_by_x.get(round(brg_off_09, 1), s0)
            s2980 = st_by_x.get(2980.0, sb)
            smid = st_by_x.get(round(half_span_mm, 1), {"y_mm": tp["mid_y_mm"], "z_mm": tp["mid_z_mm"]})
            row_cells = (
                str(tp["tendon_id"]),
                f"{s0['y_mm']:g}/{s0['z_mm']:g}",
                f"{sb['y_mm']:g}/{sb['z_mm']:g}",
                f"{s2980['y_mm']:g}/{s2980['z_mm']:g}",
                f"{smid['y_mm']:g}/{smid['z_mm']:g}",
                f"{tp['vertical_bend_radius_mm']:g}",
                f"{tp['end_angle_vert_deg']:.2f}°",
                f"{tp['duct_net_length_m']:.2f}",
            )
            for ci, cv in enumerate(row_cells):
                add_civil_text(msp, cv, 0.5 * (t_cols[ci] + t_cols[ci + 1]), ry_t + 0.30 * t_row_h, height=1.60, align="CENTER")

        # 3. Bottom-Left: 1:10 DW1 Corrugated Duct Positioning Rebar Mesh Detail (`Φ10` 井字定位架)
        vb_dw = ViewBox("tendon_positioning_detail", 112.0, 92.0, 10.0, "mm")
        d_duct = float(t3d.get("duct_diameter_mm", 70.0))
        msp.add_lwpolyline([vb_dw.pt(-100.0, -110.0), vb_dw.pt(100.0, -110.0), vb_dw.pt(100.0, 110.0), vb_dw.pt(-100.0, 110.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_dw.pt(-68.0, -95.0), vb_dw.pt(68.0, -95.0), vb_dw.pt(68.0, 95.0), vb_dw.pt(-68.0, 95.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        msp.add_circle(vb_dw.pt(0.0, 0.0), vb_dw.span(0.5 * d_duct), dxfattribs={"layer": "TENDON"})
        for s_dw in (-0.5 * d_duct - 6.0, 0.5 * d_duct + 6.0):
            msp.add_line(vb_dw.pt(-68.0, s_dw), vb_dw.pt(68.0, s_dw), dxfattribs={"layer": "REBAR"})
            msp.add_line(vb_dw.pt(s_dw, -68.0), vb_dw.pt(s_dw, 68.0), dxfattribs={"layer": "REBAR"})
        draw_boxed_tag(msp, vb_dw.x(145.0), vb_dw.y(65.0), "DW1", leader_to=vb_dw.pt(0.5 * d_duct + 6.0, 45.0), layer="REBAR")
        add_arch_dim(msp, vb_dw.pt(-100.0, -110.0), vb_dw.pt(100.0, -110.0), (vb_dw.x(0.0), vb_dw.y(-110.0) - 5.5), "腹板宽 200")
        add_arch_dim(msp, vb_dw.pt(-0.5 * d_duct, 0.0), vb_dw.pt(0.5 * d_duct, 0.0), (vb_dw.x(0.0), vb_dw.y(110.0) + 5.5), f"波纹管 Φ{d_duct:g}")
        add_civil_text(msp, "三、DW1 φ10井字定位钢筋网片详图 (1:10，与图08共用同一套束形曲线数据)", vb_dw.x(0.0), 64.0, height=2.1, align="CENTER")
        vb_dw.register(doc, msp, paper_bounds=(48.0, 62.0, 188.0, 148.0))

        dw_bars = list((calc["rebar_and_tendon_ledger"]["rebar_groups"]).get("tendon_positioning_rebar") or [])
        if dw_bars:
            draw_rebar_schedule_table(
                doc,
                msp,
                dw_bars,
                x0=b["tb_x0"],
                y0=b["tb_y1"],
                width=b["ix1"] - b["tb_x0"],
                title="波纹管定位钢筋数量明细表",
                dock_to_title_block=True,
            )
        add_civil_text(
            msp,
            f"核对说明：本图束心坐标(y/z)、端倾角θ及单梁波纹管净长({duct_per_girder_m:.2f}m)与钢束构造图及工程量表同源生成，全里程三维净距校验：{calc.get('tendon_clearance_audit', {}).get('status', 'passed')}。",
            b["ix0"] + 5.0,
            b["iy0"] + 16.0,
            height=1.88,
        )
        attach_sheet_metadata(doc, msp, "tendon_coordinates", {"tendons": t_profiles, "duct_net_length_per_girder_m": duct_per_girder_m})
        return doc

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "T梁预应力钢束构造及张拉布置图")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-08" if spec.get("preset") == "wenchang" else "QL-03")),
        unit_note="mm",
        scale_label="1:15 / 1:90",
        style_overrides=_resolve_style_overrides(spec),
    )

    # 1. Left: A-A Mid-Span Section (exact 1:15 ViewBox)
    vb_mid = ViewBox("tendon_mid_section", 70.0, 86.0, 15.0, "mm")
    crop_fl = min(flange_w_mm, 1250.0)
    mid_poly = build_t_girder_polygon_mm(flange_width_mm=crop_fl, height_mm=girder_h_mm, web_thick_mm=web_t_mm, bulb_width_mm=bulb_w_mm, bulb_height_mm=bulb_h_mm)
    msp.add_lwpolyline([vb_mid.pt(px, py) for px, py in mid_poly], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_line((vb_mid.x(0.0), vb_mid.y(-100.0)), (vb_mid.x(0.0), vb_mid.y(girder_h_mm + 100.0)), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "A-A 跨中钢束断面 (1:15)", vb_mid.x(0.0), 68.0, height=2.4, align="CENTER")

    tag_pitch = min(9.5, 84.0 / max(1, n_tendons))
    for idx, (tag, dx_mm, dy_mm) in enumerate(mid_ducts_mm):
        px, py = vb_mid.pt(dx_mm, dy_mm)
        msp.add_circle((px, py), 1.85, dxfattribs={"layer": "TENDON"})
        draw_boxed_tag(msp, vb_mid.x(0.0) + (32.0 if dx_mm >= 0 else -32.0), 96.0 + idx * tag_pitch, tag, leader_to=(px, py))

    add_arch_dim(msp, vb_mid.pt(-0.5 * bulb_w_mm, 0.0), vb_mid.pt(0.5 * bulb_w_mm, 0.0), (vb_mid.x(0.0), vb_mid.y(0.0) - 6.5), f"{bulb_w_mm:g}")
    add_arch_dim(msp, vb_mid.pt(-0.5 * bulb_w_mm, 0.0), vb_mid.pt(-0.5 * bulb_w_mm, 100.0), (vb_mid.x(-0.5 * bulb_w_mm) - 7.5, vb_mid.y(50.0)), "100", angle=90.0)
    vb_mid.register(doc, msp, paper_bounds=(22.0, 66.0, 120.0, 218.0))

    # 2. Center: B-B Girder-End Anchorage Section (exact 1:15 ViewBox — draws TRUE lateral `end_z_mm` offsets!)
    vb_end = ViewBox("tendon_end_section", 174.0, 86.0, 15.0, "mm")
    end_poly = build_t_girder_polygon_mm(flange_width_mm=crop_fl, height_mm=girder_h_mm, haunch_width_mm=10.0, web_thick_mm=end_web_t_mm, bulb_width_mm=end_web_t_mm, bulb_height_mm=0.0)
    msp.add_lwpolyline([vb_end.pt(px, py) for px, py in end_poly], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_line((vb_end.x(0.0), vb_end.y(-100.0)), (vb_end.x(0.0), vb_end.y(girder_h_mm + 100.0)), dxfattribs={"layer": "CENTER"})
    add_arch_dim(msp, vb_end.pt(-0.5 * end_web_t_mm, 0.0), vb_end.pt(0.5 * end_web_t_mm, 0.0), (vb_end.x(0.0), vb_end.y(0.0) - 6.5), f"{end_web_t_mm:g}")
    add_civil_text(msp, "B-B 梁端锚固断面 (1:15)", vb_end.x(0.0), 68.0, height=2.4, align="CENTER")

    for idx, (tag, ez_mm, ey_mm) in enumerate(end_ducts_mm):
        px, py = vb_end.pt(ez_mm, ey_mm)
        msp.add_circle((px, py), 2.0, dxfattribs={"layer": "TENDON"})
        draw_boxed_tag(msp, vb_end.x(0.0) + 30.0, py, tag, leader_to=(px + 2.0, py))
        if idx == 0:
            add_arch_dim(msp, vb_end.pt(-0.5 * end_web_t_mm, 0.0), (vb_end.x(-0.5 * end_web_t_mm), py), (vb_end.x(-0.5 * end_web_t_mm) - 7.5, 0.5 * (vb_end.y(0.0) + py)), f"{ey_mm:g}", angle=90.0)
        else:
            dy_e = round(ey_mm - end_ducts_mm[idx - 1][2], 1)
            py_prev = vb_end.y(end_ducts_mm[idx - 1][2])
            if abs(dy_e) > 1.0:
                add_arch_dim(msp, (vb_end.x(-0.5 * end_web_t_mm), py_prev), (vb_end.x(-0.5 * end_web_t_mm), py), (vb_end.x(-0.5 * end_web_t_mm) - 7.5, 0.5 * (py_prev + py)), f"{dy_e:g}", angle=90.0)
    vb_end.register(doc, msp, paper_bounds=(126.0, 66.0, 224.0, 218.0))

    # 3. Upper-Right: Half-Span Confirmed 3D Tendon Profile (1:90)
    vb_prof = ViewBox("tendon_half_profile", 244.0, 174.0, 90.0, "mm")
    brg_off_mm = max(200.0, round(0.5 * (beam_len_mm - calc_span_mm), 1))
    msp.add_lwpolyline(
        [vb_prof.pt(0.0, 0.0), vb_prof.pt(half_span_mm, 0.0), vb_prof.pt(half_span_mm, girder_h_mm), vb_prof.pt(0.0, girder_h_mm)],
        close=False,
        dxfattribs={"layer": "CONCRETE"},
    )
    msp.add_line(vb_prof.pt(0.0, 0.0), vb_prof.pt(0.0, girder_h_mm), dxfattribs={"layer": "CONCRETE"})
    x_sup_t = vb_prof.x(brg_off_mm)
    msp.add_line((x_sup_t, vb_prof.y(0.0) - 5.0), (x_sup_t, vb_prof.y(girder_h_mm) + 4.5), dxfattribs={"layer": "CENTER"})
    msp.add_lwpolyline([(x_sup_t, vb_prof.y(0.0)), (x_sup_t - 1.9, vb_prof.y(0.0) - 2.9), (x_sup_t + 1.9, vb_prof.y(0.0) - 2.9)], close=True, dxfattribs={"layer": "CONCRETE"})
    x_sym_t = vb_prof.x(half_span_mm)
    msp.add_line((x_sym_t, vb_prof.y(0.0) - 7.0), (x_sym_t, vb_prof.y(girder_h_mm) + 7.5), dxfattribs={"layer": "CENTER"})
    for sy_tk in (vb_prof.y(girder_h_mm) + 5.0, vb_prof.y(0.0) - 4.8):
        msp.add_line((x_sym_t - 2.0, sy_tk + 0.6), (x_sym_t + 2.0, sy_tk + 0.6), dxfattribs={"layer": "DIM"})
        msp.add_line((x_sym_t - 2.0, sy_tk - 0.6), (x_sym_t + 2.0, sy_tk - 0.6), dxfattribs={"layer": "DIM"})
    add_civil_text(msp, f"支点(x={brg_off_mm:g})", x_sup_t + 1.0, vb_prof.y(girder_h_mm) + 2.0, height=1.8, align="LEFT")
    add_civil_text(msp, "跨中对称线(L/2)", x_sym_t, vb_prof.y(girder_h_mm) + 8.5, height=1.85, align="CENTER")

    draw_section_cut_symbol(msp, vb_prof.x(180.0), vb_prof.y(0.0) - 1.5, vb_prof.x(180.0), vb_prof.y(girder_h_mm) + 1.5, "B", look_dx=1.0, look_dy=0.0, tick_len=2.6, arrow_len=2.6)
    draw_section_cut_symbol(msp, vb_prof.x(half_span_mm - 950.0), vb_prof.y(0.0) - 1.5, vb_prof.x(half_span_mm - 950.0), vb_prof.y(girder_h_mm) + 1.5, "A", look_dx=-1.0, look_dy=0.0, tick_len=2.6, arrow_len=2.6)

    for tp in t_profiles:
        pts_curve = [vb_prof.pt(float(pt["x_mm"]), float(pt["y_mm"])) for pt in tp["half_span_curve_mm"]]
        msp.add_lwpolyline(pts_curve, close=False, dxfattribs={"layer": "TENDON"})
        add_civil_text(msp, str(tp["tendon_id"]), pts_curve[0][0] - 5.0, pts_curve[0][1] - 0.8, height=1.82)

    th_min = min(float(tp["end_angle_vert_deg"]) for tp in t_profiles)
    th_max = max(float(tp["end_angle_vert_deg"]) for tp in t_profiles)
    add_arch_dim(msp, vb_prof.pt(0.0, girder_h_mm), vb_prof.pt(half_span_mm, girder_h_mm), (vb_prof.x(0.5 * half_span_mm), vb_prof.y(girder_h_mm) + 15.0), f"半梁长 {half_span_mm:g}")
    add_civil_text(msp, f"直线+圆弧相切束形，梁端倾角 {th_min:.2f}°~{th_max:.2f}°，单梁波纹管净长 {duct_per_girder_m:.2f}m", vb_prof.x(0.5 * half_span_mm), vb_prof.y(0.0) - 9.5, height=1.78, align="CENTER")
    add_civil_text(msp, "半跨预应力钢束纵断线形图 (1:90)", vb_prof.x(0.5 * half_span_mm), vb_prof.y(0.0) - 16.5, height=2.25, align="CENTER")
    vb_prof.register(doc, msp, paper_bounds=(234.0, 154.0, 404.0, 224.0))

    # 4. Bottom-Right: Visible Prestressing Tendon & Jacking Schedule Table fused atop Title Block!
    mid_pe = float(ps_info["loss_profile"][len(ps_info["loss_profile"]) // 2]["effective_prestress_mpa"])
    sigma_con = float(ps_info["sigma_con_mpa"])
    tendon_rows = list(calc["rebar_and_tendon_ledger"]["tendons"])[:n_tendons]
    draw_tendon_schedule_table(
        doc,
        msp,
        tendon_rows,
        x0=b["tb_x0"],
        y0=b["tb_y1"],
        width=b["ix1"] - b["tb_x0"],
        end_ys_mm=end_ys_mm,
        mid_ys_mm=[m[2] for m in mid_ducts_mm],
        sigma_con_mpa=sigma_con,
        mid_sigma_pe_mpa=mid_pe,
        dock_to_title_block=True,
    )

    elong_sample = tendon_rows[0]["theoretical_elongation_mm"] if tendon_rows else 170.0
    add_civil_text(
        msp,
        f"钢束规格：{n_tendons}束 Φs15.2-{strands_per} (fpk=1860MPa, σcon={sigma_con:g}MPa, 跨中有效预应力={mid_pe:.2f}MPa, 首束理论伸长量={elong_sample:g}mm, 单梁波纹管净长={duct_per_girder_m:.2f}m)",
        b["ix0"] + 5.0,
        b["iy0"] + 16.0,
        height=1.85,
    )
    attach_sheet_metadata(
        doc,
        msp,
        "tendon_schedule",
        {
            "items": tendon_rows,
            "sigma_con_mpa": sigma_con,
            "mid_effective_prestress_mpa": mid_pe,
            "duct_net_length_per_girder_m": duct_per_girder_m,
        },
    )
    return doc


# ======================================================================
# 6. Data-Driven Ordinary Rebar Construction Renderer (Cats 4 & 9)
# ======================================================================
_REBAR_COMPONENT_META: dict[str, tuple[str, str, str]] = {
    "t_girder_web": ("t_girder_web_rebar", "主梁梁肋普通钢筋构造图", "QL-09"),
    "t_girder_flange": ("t_girder_flange_rebar", "主梁翼缘板及湿接缝钢筋构造图", "QL-10"),
    "diaphragm": ("diaphragm_rebar", "主梁中横隔梁与端横隔梁钢筋构造图", "QL-11"),
    "pier_cap": ("pier_cap_rebar", "桥墩盖梁普通钢筋构造图", "QL-16"),
    "pier_cap_secondary": ("pier_cap_secondary_rebar", "桥墩盖梁普通钢筋构造图（二：弯起筋、挡块及大样）", "18"),
    "pier_column_pile": ("pier_column_pile_rebar", "桥墩墩柱、系梁及钻孔桩钢筋构造图", "QL-17"),
    "tie_beam_joint": ("pier_column_pile_rebar", "桩顶系梁钢筋构造及节点详图", "19-1"),
    "abutment_rebar": ("abutment_rebar", "桥台台帽、耳背墙及桩基钢筋构造图", "QL-18"),
    "abutment_ear_backwall": ("abutment_ear_backwall_rebar", "桥台耳墙及背墙钢筋展开构造图", "21"),
}


def _draw_row_bar_circles(
    msp: Any,
    vb: ViewBox,
    count: int,
    x_lo_mm: float,
    x_hi_mm: float,
    y_mm: float,
    *,
    dot_r_mm: float = 1.05,
) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    if count == 1:
        pt = vb.pt(0.5 * (x_lo_mm + x_hi_mm), y_mm)
        msp.add_circle(pt, dot_r_mm, dxfattribs={"layer": "REBAR"})
        return [pt]
    pts: list[tuple[float, float]] = []
    for i in range(count):
        rx = x_lo_mm + (x_hi_mm - x_lo_mm) * (i / (count - 1))
        pt = vb.pt(rx, y_mm)
        msp.add_circle(pt, dot_r_mm, dxfattribs={"layer": "REBAR"})
        pts.append(pt)
    return pts


def _draw_perimeter_bar_circles(
    msp: Any,
    vb: ViewBox,
    count: int,
    cage_radius_mm: float,
    *,
    dot_r_mm: float = 0.95,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(max(0, count)):
        ang = 2.0 * math.pi * (i / count)
        pt = vb.pt(cage_radius_mm * math.cos(ang), cage_radius_mm * math.sin(ang))
        msp.add_circle(pt, dot_r_mm, dxfattribs={"layer": "REBAR"})
        pts.append(pt)
    return pts


def _draw_hoop_polygon(msp: Any, vb: ViewBox, radius_mm: float, *, segments: int = 36) -> None:
    pts = [
        vb.pt(radius_mm * math.cos(2.0 * math.pi * k / segments), radius_mm * math.sin(2.0 * math.pi * k / segments))
        for k in range(segments)
    ]
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "REBAR_STIRRUP"})


def _draw_rebar_detail(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    comp = str(spec.get("component", "t_girder_web")).strip().lower()
    if comp not in _REBAR_COMPONENT_META:
        raise ValueError(f"unsupported rebar component {comp!r}; expected one of {sorted(_REBAR_COMPONENT_META)}")
    grp_key, def_title, def_dno = _REBAR_COMPONENT_META[comp]
    calc = _ensure_calc_report(spec)
    r_groups = calc["rebar_and_tendon_ledger"]["rebar_groups"]
    if comp == "tie_beam_joint":
        bars = [b for b in r_groups["pier_column_pile_rebar"] if b["bar_id"] in {"XL1", "XL2"}]
    else:
        bars = list(r_groups.get(grp_key) or [])
    bars_by_id = {str(item["bar_id"]): item for item in bars}

    rebar_scale_lbl = {
        "pier_cap_secondary": "1:20 / 1:25 / 1:50",
        "tie_beam_joint": "1:20 / 1:40",
        "abutment_ear_backwall": "1:25 / 1:65",
    }.get(comp, "图示比例")

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", def_title)),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", def_dno)),
        unit_note="mm（钢筋表长以cm计）",
        scale_label=rebar_scale_lbl,
        style_overrides=_resolve_style_overrides(spec),
    )

    # Merge Rebar Schedule Table directly atop the bottom-right Title Block (shared 180mm width & border)
    n_piers_br = max(1, int(calc["project_summary"]["n_spans"]) - 1)
    n_ped_br = int(calc["lateral_distribution"]["n_girders"])
    if comp == "pier_column_pile":
        draw_rebar_schedule_table(
            doc,
            msp,
            bars,
            x0=b["tb_x0"],
            y0=b["tb_y1"],
            width=b["ix1"] - b["tb_x0"],
            title="双柱式桥墩钢筋数量明细表（按单座桥墩计）",
            dock_to_title_block=True,
            unit_count_header="每墩件数",
            unit_weight_header="每墩重(kg)",
            summary_prefix="单座双柱墩钢筋合计(2柱+1系梁+2桩)",
            bridge_units_count=n_piers_br,
            bridge_units_noun="座墩",
        )
    elif comp == "abutment_rebar":
        draw_rebar_schedule_table(
            doc,
            msp,
            bars,
            x0=b["tb_x0"],
            y0=b["tb_y1"],
            width=b["ix1"] - b["tb_x0"],
            title="桩柱式桥台钢筋数量明细表（按单座桥台计）",
            dock_to_title_block=True,
            unit_count_header="每台件数",
            unit_weight_header="每台重(kg)",
            summary_prefix=f"单座桥台钢筋合计(1台帽+1耳背墙+2桩+{n_ped_br}垫石)",
            bridge_units_count=2,
            bridge_units_noun="座台",
        )
    else:
        draw_rebar_schedule_table(
            doc,
            msp,
            bars,
            x0=b["tb_x0"],
            y0=b["tb_y1"],
            width=b["ix1"] - b["tb_x0"],
            title="钢筋数量明细表",
            dock_to_title_block=True,
        )

    girder_h = float(calc["t_girder_section"]["mid_span_mm"]["height"])
    flange_w = float(calc["t_girder_section"]["effective_flange_width_mm"])
    bulb_w = 560.0 if flange_w <= 2000.0 else 600.0
    beam_len_mm = float(calc["project_summary"]["beam_length_m"]) * 1000.0
    cap_len_mm = float(calc["pier_cap_beam_1d"]["cap_length_m"]) * 1000.0
    cap_w_mm = float(calc["substructure_checks"]["cap_beam"].get("width_m", 1.60)) * 1000.0
    cap_h_mm = float(calc["substructure_checks"]["cap_beam"].get("height_m", 1.40)) * 1000.0
    col_d_mm = float(calc["substructure_checks"]["pier_column"]["diameter_m"]) * 1000.0
    col_h_mm = float(calc["substructure_checks"]["pier_column"]["height_m"]) * 1000.0
    pile_d_mm = float(calc["substructure_checks"]["bored_pile"]["diameter_m"]) * 1000.0
    pile_len_mm = float(calc["substructure_checks"]["bored_pile"]["design_length_m"]) * 1000.0

    anchor_by_id: dict[str, tuple[float, float]] = {}

    if comp == "t_girder_web":
        vb = ViewBox("rebar_t_girder_web", 112.0, 76.0, 20.0, "mm")
        poly = build_t_girder_polygon_mm(flange_width_mm=flange_w, height_mm=girder_h, bulb_width_mm=bulb_w)
        msp.add_lwpolyline([vb.pt(x, y) for x, y in poly], close=True, dxfattribs={"layer": "CONCRETE"})
        # GJ1 closed web stirrup loop (0.35mm stirrup layer)
        msp.add_lwpolyline(
            [vb.pt(-68.0, 52.0), vb.pt(68.0, 52.0), vb.pt(68.0, girder_h - 52.0), vb.pt(-68.0, girder_h - 52.0)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        # R1 bottom bulb main bars: exact count from schedule (e.g. 6Φ22)
        r1_cnt = int(bars_by_id["R1"]["count"])
        if r1_cnt <= 6:
            r1_pts = _draw_row_bar_circles(msp, vb, r1_cnt, -0.36 * bulb_w, 0.36 * bulb_w, 65.0, dot_r_mm=1.15)
        else:
            n_row1 = min(6, (r1_cnt + 1) // 2)
            r1_pts = _draw_row_bar_circles(msp, vb, n_row1, -0.36 * bulb_w, 0.36 * bulb_w, 58.0, dot_r_mm=1.08)
            r1_pts += _draw_row_bar_circles(msp, vb, r1_cnt - n_row1, -0.28 * bulb_w, 0.28 * bulb_w, 118.0, dot_r_mm=1.08)

        # R2 top flange erection bars: exact count from schedule (e.g. 6Φ16)
        r2_cnt = int(bars_by_id["R2"]["count"])
        r2_left = r2_cnt // 2
        r2_pts = _draw_row_bar_circles(msp, vb, r2_left, -0.42 * flange_w, -0.10 * flange_w, girder_h - 55.0, dot_r_mm=0.98)
        r2_pts += _draw_row_bar_circles(msp, vb, r2_cnt - r2_left, 0.10 * flange_w, 0.42 * flange_w, girder_h - 55.0, dot_r_mm=0.98)

        # R3 web horizontal crack-control bars: exact count in pairs along web faces (e.g. 8Φ12)
        r3_cnt = int(bars_by_id["R3"]["count"])
        n_pairs = r3_cnt // 2
        r3_pts: list[tuple[float, float]] = []
        for p_i in range(n_pairs):
            ry_w = 340.0 + (girder_h - 680.0) * (p_i / max(1, n_pairs - 1))
            for rx_w in (-60.0, 60.0):
                pt_w = vb.pt(rx_w, ry_w)
                msp.add_circle(pt_w, 0.88, dxfattribs={"layer": "REBAR"})
                r3_pts.append(pt_w)
        if r3_cnt % 2 == 1:
            pt_mid = vb.pt(0.0, 0.5 * girder_h)
            msp.add_circle(pt_mid, 0.88, dxfattribs={"layer": "REBAR"})
            r3_pts.append(pt_mid)

        add_arch_dim(msp, vb.pt(-0.5 * flange_w, girder_h), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.0), vb.y(girder_h) + 8.0), f"{flange_w:g}")
        add_arch_dim(msp, vb.pt(-0.5 * bulb_w, 0.0), vb.pt(0.5 * bulb_w, 0.0), (vb.x(0.0), vb.y(0.0) - 6.5), f"{bulb_w:g}")
        add_arch_dim(msp, vb.pt(0.5 * flange_w, 0.0), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.5 * flange_w) + 9.0, vb.y(0.5 * girder_h)), f"{girder_h:g}", angle=90.0)
        add_civil_text(msp, f"R1 {r1_cnt}Φ{bars_by_id['R1']['dia_mm']:g}", vb.x(0.0), vb.y(0.0) - 13.0, height=1.95, align="CENTER")
        add_civil_text(msp, f"R2 {r2_cnt}Φ{bars_by_id['R2']['dia_mm']:g} | R3 {r3_cnt}Φ{bars_by_id['R3']['dia_mm']:g}", vb.x(0.0), vb.y(girder_h) + 13.5, height=1.95, align="CENTER")
        add_civil_text(msp, "A-A 跨中梁肋普通钢筋断面 (1:20)", vb.x(0.0), 54.0, height=2.4, align="CENTER")
        vb.register(doc, msp, paper_bounds=(28.0, 52.0, 198.0, 188.0))

        # Half-span stirrup spacing elevation above schedule table (1:80) with complete zone & boundary verification!
        half_l_mm = 0.5 * beam_len_mm
        calc_span_mm = float(calc["project_summary"]["calc_span_m"]) * 1000.0
        brg_offset_mm = max(200.0, round(0.5 * (beam_len_mm - calc_span_mm), 1))
        n_mid_half = max(1, int(round((half_l_mm - 2130.0) / 150.0)))
        dense_start_mm = 80.0
        dense_len_mm = 2000.0
        dense_end_mm = dense_start_mm + dense_len_mm
        mid_zone_mm = n_mid_half * 150.0
        mid_end_mm = min(half_l_mm - 20.0, dense_end_mm + mid_zone_mm)
        mid_span_zone_mm = round(mid_end_mm - dense_end_mm, 1)
        gj1_dia = float(bars_by_id["GJ1"]["dia_mm"])
        gj1_tot = int(bars_by_id["GJ1"]["count"])

        vb_el = ViewBox("girder_web_elev", 232.0, 166.0, 80.0, "mm")
        # Concrete girder web half-elevation (0 = beam end, half_l_mm = mid-span symmetry plane)
        msp.add_lwpolyline(
            [vb_el.pt(0.0, 0.0), vb_el.pt(half_l_mm, 0.0), vb_el.pt(half_l_mm, girder_h), vb_el.pt(0.0, girder_h)],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line(vb_el.pt(0.0, 0.0), vb_el.pt(0.0, girder_h), dxfattribs={"layer": "CONCRETE"})
        # R1 bottom & R2 top longitudinal main bars (0.60mm REBAR) + R3 horizontal crack-control bars
        msp.add_line(vb_el.pt(60.0, 75.0), vb_el.pt(half_l_mm, 75.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb_el.pt(60.0, girder_h - 65.0), vb_el.pt(half_l_mm, girder_h - 65.0), dxfattribs={"layer": "REBAR"})
        for ry_r3 in (0.35 * girder_h, 0.65 * girder_h):
            msp.add_line(vb_el.pt(80.0, ry_r3), vb_el.pt(half_l_mm, ry_r3), dxfattribs={"layer": "THIN"})

        # GJ1 vertical stirrups: exact 100mm true-scale pitch in [80..2080] (21 bars) and exact 150mm pitch in (2080..mid_end_mm]
        for k_d in range(21):
            rx_d = dense_start_mm + k_d * 100.0
            msp.add_line(vb_el.pt(rx_d, 75.0), vb_el.pt(rx_d, girder_h - 65.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        for k_m in range(1, n_mid_half + 1):
            rx_m = dense_end_mm + k_m * 150.0
            msp.add_line(vb_el.pt(rx_m, 75.0), vb_el.pt(rx_m, girder_h - 65.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        # Dashed zone boundary at x = 2080mm separating dense vs standard stirrup zones
        msp.add_line((vb_el.x(dense_end_mm), vb_el.y(0.0) - 4.0), (vb_el.x(dense_end_mm), vb_el.y(girder_h) + 4.0), dxfattribs={"layer": "HIDDEN"})

        # Left boundary: Beam end + Support centerline (支点中心线, x = brg_offset_mm) + bearing triangle
        x_sup = vb_el.x(brg_offset_mm)
        y_bot = vb_el.y(0.0)
        y_top = vb_el.y(girder_h)
        msp.add_line((x_sup, y_bot - 6.0), (x_sup, y_top + 5.5), dxfattribs={"layer": "CENTER"})
        msp.add_lwpolyline([(x_sup, y_bot), (x_sup - 2.1, y_bot - 3.2), (x_sup + 2.1, y_bot - 3.2)], close=True, dxfattribs={"layer": "CONCRETE"})
        add_civil_text(msp, "梁端", vb_el.x(0.0) - 1.2, y_top + 2.2, height=1.85, align="RIGHT")
        add_civil_text(msp, f"支点中心线(x={brg_offset_mm:g})", x_sup + 1.2, y_top + 2.2, height=1.85, align="LEFT")

        # Right boundary: Mid-span symmetry line (跨中对称线 L/2) + GB/T double symmetry ticks
        x_sym = vb_el.x(half_l_mm)
        msp.add_line((x_sym, y_bot - 8.0), (x_sym, y_top + 8.0), dxfattribs={"layer": "CENTER"})
        for sy_tick in (y_top + 5.5, y_bot - 5.5):
            msp.add_line((x_sym - 2.2, sy_tick + 0.7), (x_sym + 2.2, sy_tick + 0.7), dxfattribs={"layer": "DIM"})
            msp.add_line((x_sym - 2.2, sy_tick - 0.7), (x_sym + 2.2, sy_tick - 0.7), dxfattribs={"layer": "DIM"})
        add_civil_text(msp, "跨中对称线(L/2)", x_sym, y_top + 9.2, height=1.9, align="CENTER")

        # A-A section cut symbol near mid-span linking directly to left 1:20 cross-section
        x_aa = vb_el.x(half_l_mm - 950.0)
        draw_section_cut_symbol(msp, x_aa, y_bot - 1.5, x_aa, y_top + 1.5, "A", look_dx=-1.0, look_dy=0.0, tick_len=3.0, arrow_len=3.0)

        # True-scale native dimensions for stirrup zones & half-beam geometry
        add_arch_dim(
            msp,
            vb_el.pt(dense_start_mm, 0.0),
            vb_el.pt(dense_end_mm, 0.0),
            (vb_el.x(0.5 * (dense_start_mm + dense_end_mm)), y_bot - 8.5),
            f"20×100={dense_len_mm:g}",
        )
        add_arch_dim(
            msp,
            vb_el.pt(dense_end_mm, 0.0),
            vb_el.pt(mid_end_mm, 0.0),
            (vb_el.x(0.5 * (dense_end_mm + mid_end_mm)), y_bot - 8.5),
            f"{n_mid_half}×150={mid_span_zone_mm:g}",
        )
        add_arch_dim(
            msp,
            vb_el.pt(0.0, girder_h),
            vb_el.pt(half_l_mm, girder_h),
            (vb_el.x(0.5 * half_l_mm), y_top + 16.5),
            f"半梁长 {half_l_mm:g}",
        )

        # Direct stirrup zone specification & start/end position callouts
        add_civil_text(
            msp,
            f"加密区:GJ1 Φ{gj1_dia:g}@100(21根,x=80~2080)",
            vb_el.x(dense_start_mm),
            y_bot - 16.0,
            height=1.88,
            align="LEFT",
        )
        add_civil_text(
            msp,
            f"普通区:GJ1 Φ{gj1_dia:g}@150({n_mid_half}根,x=2080~{mid_end_mm:g})",
            vb_el.x(dense_end_mm + 2400.0),
            y_bot - 16.0,
            height=1.88,
            align="LEFT",
        )
        add_civil_text(
            msp,
            f"箍筋复核：半梁 21+{n_mid_half}={21 + n_mid_half}根，全梁对称共 {gj1_tot}根（首筋距梁端80，末筋距L/2为{half_l_mm - mid_end_mm:g}）",
            vb_el.x(0.5 * half_l_mm),
            133.0,
            height=1.88,
            align="CENTER",
        )
        add_civil_text(msp, "半跨梁肋纵筋及箍筋分区布置立面图 (1:80)", vb_el.x(0.5 * half_l_mm), 124.5, height=2.3, align="CENTER")
        vb_el.register(doc, msp, paper_bounds=(218.0, 122.0, 404.0, 232.0))

        anchor_by_id = {
            "R1": r1_pts[-1] if r1_pts else vb.pt(120.0, 65.0),
            "R2": r2_pts[-1] if r2_pts else vb.pt(0.30 * flange_w, girder_h - 55.0),
            "GJ1": vb.pt(68.0, 0.52 * girder_h),
            "R3": r3_pts[0] if r3_pts else vb.pt(-60.0, 500.0),
        }

    elif comp == "t_girder_flange":
        # Left: 1:20 Precast T-Girder Flange Transverse Rebar Section (exact W3 count!)
        vb = ViewBox("rebar_t_girder_flange", 108.0, 74.0, 20.0, "mm")
        poly = build_t_girder_polygon_mm(flange_width_mm=flange_w, height_mm=girder_h, bulb_width_mm=bulb_w)
        msp.add_lwpolyline([vb.pt(x, y) for x, y in poly], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb.pt(-0.47 * flange_w, girder_h - 42.0), vb.pt(0.47 * flange_w, girder_h - 42.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb.pt(-0.47 * flange_w, girder_h - 156.0), vb.pt(0.47 * flange_w, girder_h - 156.0), dxfattribs={"layer": "REBAR"})
        w3_cnt = int(bars_by_id["W3"]["count"])
        w3_top = w3_cnt // 2
        w3_pts = _draw_row_bar_circles(msp, vb, w3_top, -0.44 * flange_w, 0.44 * flange_w, girder_h - 56.0, dot_r_mm=0.9)
        w3_pts += _draw_row_bar_circles(msp, vb, w3_cnt - w3_top, -0.44 * flange_w, 0.44 * flange_w, girder_h - 142.0, dot_r_mm=0.9)
        draw_detail_callout(
            msp,
            vb.x(0.42 * flange_w),
            vb.y(girder_h - 100.0),
            5.0,
            "I 湿接缝(1:10)",
            leader_to=(vb.x(0.5 * flange_w) + 6.0, vb.y(girder_h) + 12.0),
        )
        add_arch_dim(msp, vb.pt(-0.5 * flange_w, girder_h), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.0), vb.y(girder_h) + 8.0), f"{flange_w:g}")
        add_arch_dim(msp, vb.pt(0.5 * flange_w, 0.0), vb.pt(0.5 * flange_w, girder_h), (vb.x(0.5 * flange_w) + 9.0, vb.y(0.5 * girder_h)), f"{girder_h:g}", angle=90.0)
        add_civil_text(msp, "一、翼缘板横向配筋断面 (1:20)", vb.x(0.0), 54.0, height=2.35, align="CENTER")
        vb.register(doc, msp, paper_bounds=(26.0, 52.0, 194.0, 192.0))

        # Upper-Right: 1:10 Detail I — 400mm Cast-in-Place Wet-Joint Loop-Bar Splice Detail!
        vb_wj = ViewBox("wet_joint_rebar_detail", 316.0, 184.0, 10.0, "mm")
        # Left & right precast flange tips [-420..-200] & [200..420] x [0..200] + roughened shear keys
        msp.add_lwpolyline([vb_wj.pt(-420.0, 0.0), vb_wj.pt(-200.0, 0.0), vb_wj.pt(-180.0, 100.0), vb_wj.pt(-200.0, 200.0), vb_wj.pt(-420.0, 200.0)], close=False, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_wj.pt(420.0, 0.0), vb_wj.pt(200.0, 0.0), vb_wj.pt(180.0, 100.0), vb_wj.pt(200.0, 200.0), vb_wj.pt(420.0, 200.0)], close=False, dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb_wj.pt(-200.0, 200.0), vb_wj.pt(200.0, 200.0), dxfattribs={"layer": "HIDDEN"})
        msp.add_line(vb_wj.pt(-200.0, 0.0), vb_wj.pt(200.0, 0.0), dxfattribs={"layer": "HIDDEN"})
        # Top negative-moment bar W1 continuous / welded across wet joint + Bottom U-hairpin loop overlap W2 (搭接 340mm)
        msp.add_line(vb_wj.pt(-400.0, 162.0), vb_wj.pt(400.0, 162.0), dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb_wj.pt(-400.0, 142.0), vb_wj.pt(170.0, 142.0), vb_wj.pt(170.0, 44.0), vb_wj.pt(-400.0, 44.0)], close=False, dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb_wj.pt(400.0, 128.0), vb_wj.pt(-170.0, 128.0), vb_wj.pt(-170.0, 58.0), vb_wj.pt(400.0, 58.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
        # Longitudinal lacer bars inside wet-joint loop (drawn on REBAR_STIRRUP to isolate W3 section dot count!)
        for rx_lj in (-135.0, 0.0, 135.0):
            msp.add_circle(vb_wj.pt(rx_lj, 148.0), 0.78, dxfattribs={"layer": "REBAR_STIRRUP"})
            msp.add_circle(vb_wj.pt(rx_lj, 58.0), 0.78, dxfattribs={"layer": "REBAR_STIRRUP"})

        add_arch_dim(msp, vb_wj.pt(-200.0, 200.0), vb_wj.pt(200.0, 200.0), (vb_wj.x(0.0), vb_wj.y(200.0) + 12.5), "现浇C50湿接缝 400")
        add_arch_dim(msp, vb_wj.pt(-170.0, 200.0), vb_wj.pt(170.0, 200.0), (vb_wj.x(0.0), vb_wj.y(200.0) + 5.8), "环形筋搭接 340")
        add_arch_dim(msp, vb_wj.pt(420.0, 0.0), vb_wj.pt(420.0, 200.0), (vb_wj.x(420.0) + 6.5, vb_wj.y(100.0)), "200", angle=90.0)
        add_civil_text(msp, "预制端面人工凿毛，W2环形筋搭接并穿纵向筋焊接", vb_wj.x(0.0), vb_wj.y(0.0) - 8.5, height=1.82, align="CENTER")
        add_civil_text(msp, "二、I 湿接缝横向环形钢筋连接详图 (1:10)", vb_wj.x(0.0), vb_wj.y(0.0) - 16.0, height=2.25, align="CENTER")
        vb_wj.register(doc, msp, paper_bounds=(234.0, 162.0, 404.0, 232.0))

        anchor_by_id = {
            "W1": vb.pt(0.28 * flange_w, girder_h - 42.0),
            "W2": vb.pt(0.28 * flange_w, girder_h - 156.0),
            "W3": w3_pts[-1] if w3_pts else vb.pt(-0.28 * flange_w, girder_h - 56.0),
        }

    elif comp == "diaphragm":
        n_g = int(calc["lateral_distribution"]["n_girders"])
        sp_mm = float(calc["lateral_distribution"]["girder_spacing_m"]) * 1000.0
        w_mm = (n_g - 1) * sp_mm
        h_mm = 1350.0
        vb = ViewBox("rebar_diaphragm", 114.0, 174.0, 65.0, "mm")
        hw = 0.5 * w_mm
        msp.add_lwpolyline([vb.pt(-hw, 0.0), vb.pt(hw, 0.0), vb.pt(hw, h_mm), vb.pt(-hw, h_mm)], close=True, dxfattribs={"layer": "CONCRETE"})
        # Show the 5 intersecting T-girder webs + 4 cast-in-place wet-joint diaphragm splice zones!
        for gi in range(n_g):
            rxg = -hw + gi * sp_mm
            msp.add_line((vb.x(rxg), vb.y(-260.0)), (vb.x(rxg), vb.y(h_mm + 260.0)), dxfattribs={"layer": "CENTER"})
            msp.add_lwpolyline(
                [vb.pt(rxg - 100.0, -220.0), vb.pt(rxg + 100.0, -220.0), vb.pt(rxg + 100.0, h_mm), vb.pt(rxg - 100.0, h_mm)],
                close=False,
                dxfattribs={"layer": "THIN"},
            )
            add_civil_text(msp, f"{gi+1}#梁", vb.x(rxg), vb.y(-220.0) - 3.8, height=1.72, align="CENTER")
            if gi < n_g - 1:
                rx_wj = rxg + 0.5 * sp_mm
                msp.add_line(vb.pt(rx_wj - 200.0, 0.0), vb.pt(rx_wj - 200.0, h_mm), dxfattribs={"layer": "HIDDEN"})
                msp.add_line(vb.pt(rx_wj + 200.0, 0.0), vb.pt(rx_wj + 200.0, h_mm), dxfattribs={"layer": "HIDDEN"})

        msp.add_line(vb.pt(-hw + 80.0, h_mm - 75.0), vb.pt(hw - 80.0, h_mm - 75.0), dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb.pt(-hw + 80.0, h_mm - 120.0), vb.pt(-0.45 * hw, 75.0), vb.pt(0.45 * hw, 75.0), vb.pt(hw - 80.0, h_mm - 120.0)], close=False, dxfattribs={"layer": "REBAR"})
        for k in range(13):
            rxs = -hw + 200.0 + k * ((w_mm - 400.0) / 12.0)
            msp.add_line(vb.pt(rxs, 75.0), vb.pt(rxs, h_mm - 75.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        x_aa_d = vb.x(-0.22 * hw)
        draw_section_cut_symbol(msp, x_aa_d, vb.y(0.0) - 1.5, x_aa_d, vb.y(h_mm) + 1.5, "A", look_dx=1.0, look_dy=0.0, tick_len=2.6, arrow_len=2.6)
        add_arch_dim(msp, vb.pt(-hw, h_mm), vb.pt(hw, h_mm), (vb.x(0.0), vb.y(h_mm) + 9.5), f"中横隔梁 {w_mm:g} ({n_g-1}×{sp_mm:g})")
        add_arch_dim(msp, vb.pt(hw, 0.0), vb.pt(hw, h_mm), (vb.x(hw) + 8.0, vb.y(0.5 * h_mm)), f"{h_mm:g}", angle=90.0)
        add_civil_text(msp, "一、中横隔梁与主梁肋连接配筋立面图 (1:65)", vb.x(0.0), 152.0, height=2.3, align="CENTER")
        vb.register(doc, msp, paper_bounds=(24.0, 150.0, 212.0, 232.0))

        # Lower-Left: 1:25 A-A Mid-Diaphragm Cross-Section (exact HG1=4 & HG2=4 counts!)
        vb_sec = ViewBox("diaphragm_section", 114.0, 68.0, 25.0, "mm")
        msp.add_lwpolyline([vb_sec.pt(-100.0, 0.0), vb_sec.pt(100.0, 0.0), vb_sec.pt(100.0, h_mm), vb_sec.pt(-100.0, h_mm)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_sec.pt(-72.0, 50.0), vb_sec.pt(72.0, 50.0), vb_sec.pt(72.0, h_mm - 50.0), vb_sec.pt(-72.0, h_mm - 50.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        hg1_pts = _draw_row_bar_circles(msp, vb_sec, int(bars_by_id["HG1"]["count"]), -60.0, 60.0, 75.0, dot_r_mm=0.95)
        hg2_pts = _draw_row_bar_circles(msp, vb_sec, int(bars_by_id["HG2"]["count"]), -60.0, 60.0, h_mm - 75.0, dot_r_mm=0.9)
        add_arch_dim(msp, vb_sec.pt(-100.0, 0.0), vb_sec.pt(100.0, 0.0), (vb_sec.x(0.0), vb_sec.y(0.0) - 6.5), "200")
        add_civil_text(msp, "二、A-A 中横隔梁断面 (1:25)", vb_sec.x(0.0), 50.0, height=2.25, align="CENTER")
        vb_sec.register(doc, msp, paper_bounds=(74.0, 48.0, 154.0, 136.0))

        # Upper-Right: 1:25 End Diaphragm Cross-Section (端横隔梁 240×1450 + 翼缘顶板接口, completing sheet title!)
        vb_end_d = ViewBox("end_diaphragm_section", 318.0, 154.0, 25.0, "mm")
        end_dh = 1450.0
        msp.add_lwpolyline(
            [
                vb_end_d.pt(-120.0, 0.0),
                vb_end_d.pt(120.0, 0.0),
                vb_end_d.pt(120.0, end_dh - 200.0),
                vb_end_d.pt(380.0, end_dh - 200.0),
                vb_end_d.pt(380.0, end_dh),
                vb_end_d.pt(-380.0, end_dh),
                vb_end_d.pt(-380.0, end_dh - 200.0),
                vb_end_d.pt(-120.0, end_dh - 200.0),
            ],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_lwpolyline(
            [vb_end_d.pt(-85.0, 55.0), vb_end_d.pt(85.0, 55.0), vb_end_d.pt(85.0, end_dh - 55.0), vb_end_d.pt(-85.0, end_dh - 55.0)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        # Schematic longitudinal dots in end diaphragm drawn on REBAR_STIRRUP so HG1/HG2 count in A-A stays exact!
        for rx_ed in (-65.0, -22.0, 22.0, 65.0):
            msp.add_circle(vb_end_d.pt(rx_ed, 80.0), 0.90, dxfattribs={"layer": "REBAR_STIRRUP"})
            msp.add_circle(vb_end_d.pt(rx_ed, end_dh - 80.0), 0.90, dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_end_d.pt(-120.0, 0.0), vb_end_d.pt(120.0, 0.0), (vb_end_d.x(0.0), vb_end_d.y(0.0) - 6.0), "端隔梁厚 240")
        add_arch_dim(msp, vb_end_d.pt(380.0, 0.0), vb_end_d.pt(380.0, end_dh), (vb_end_d.x(380.0) + 7.5, vb_end_d.y(0.5 * end_dh)), f"{end_dh:g}", angle=90.0)
        add_civil_text(msp, "三、端横隔梁配筋断面及桥面连接 (1:25)", vb_end_d.x(0.0), 136.0, height=2.2, align="CENTER")
        vb_end_d.register(doc, msp, paper_bounds=(262.0, 134.0, 386.0, 236.0))

        anchor_by_id = {
            "HG1": hg1_pts[-1] if hg1_pts else vb.pt(0.0, 75.0),
            "HG2": hg2_pts[-1] if hg2_pts else vb.pt(0.0, h_mm - 75.0),
            "HG3": vb.pt(0.25 * hw, 0.5 * h_mm),
        }

    elif comp == "pier_cap":
        col_positions_cap = calc["pier_cap_beam_1d"].get("column_positions_m") or [-2.6, 2.6]
        col_sp_cap_mm = round(max(3200.0, float(col_positions_cap[-1] - col_positions_cap[0]) * 1000.0), 1)
        vb = ViewBox("rebar_pier_cap", 114.0, 178.0, 65.0, "mm")
        hw = 0.5 * cap_len_mm
        overhang_cap = max(200.0, 0.5 * (cap_len_mm - col_sp_cap_mm - col_d_mm))
        haunch_cap = 0.30 * cap_h_mm
        cap_elev_pts = [
            vb.pt(-hw, haunch_cap),
            vb.pt(-hw + overhang_cap, 0.0),
            vb.pt(hw - overhang_cap, 0.0),
            vb.pt(hw, haunch_cap),
            vb.pt(hw, cap_h_mm),
            vb.pt(-hw, cap_h_mm),
        ]
        msp.add_lwpolyline(cap_elev_pts, close=True, dxfattribs={"layer": "CONCRETE"})
        # Dashed twin pier column support positions + centerlines
        for sx_c in (-0.5, 0.5):
            rxc_s = sx_c * col_sp_cap_mm
            msp.add_line((vb.x(rxc_s), vb.y(-550.0)), (vb.x(rxc_s), vb.y(cap_h_mm + 260.0)), dxfattribs={"layer": "CENTER"})
            msp.add_line(vb.pt(rxc_s - 0.5 * col_d_mm, -500.0), vb.pt(rxc_s - 0.5 * col_d_mm, 0.0), dxfattribs={"layer": "HIDDEN"})
            msp.add_line(vb.pt(rxc_s + 0.5 * col_d_mm, -500.0), vb.pt(rxc_s + 0.5 * col_d_mm, 0.0), dxfattribs={"layer": "HIDDEN"})

        msp.add_line(vb.pt(-hw + 80.0, cap_h_mm - 75.0), vb.pt(hw - 80.0, cap_h_mm - 75.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb.pt(-hw + overhang_cap + 60.0, 75.0), vb.pt(hw - overhang_cap - 60.0, 75.0), dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb.pt(-0.84 * hw, cap_h_mm - 95.0), vb.pt(-0.42 * hw, 95.0), vb.pt(0.42 * hw, 95.0), vb.pt(0.84 * hw, cap_h_mm - 95.0)], close=False, dxfattribs={"layer": "REBAR"})
        for k in range(17):
            rxs = -hw + 220.0 + k * ((cap_len_mm - 440.0) / 16.0)
            ry_bot_st = 75.0 + max(0.0, (abs(rxs) - (hw - overhang_cap)) / max(1.0, overhang_cap)) * haunch_cap
            msp.add_line(vb.pt(rxs, min(cap_h_mm - 180.0, ry_bot_st)), vb.pt(rxs, cap_h_mm - 75.0), dxfattribs={"layer": "REBAR_STIRRUP"})

        x_aa_cap = vb.x(0.5 * col_sp_cap_mm - 350.0)
        draw_section_cut_symbol(msp, x_aa_cap, vb.y(0.0) - 1.5, x_aa_cap, vb.y(cap_h_mm) + 1.5, "A", look_dx=1.0, look_dy=0.0, tick_len=2.6, arrow_len=2.6)
        add_arch_dim(msp, vb.pt(-hw, cap_h_mm), vb.pt(hw, cap_h_mm), (vb.x(0.0), vb.y(cap_h_mm) + 9.0), f"盖梁长 {cap_len_mm:g}")
        add_arch_dim(msp, vb.pt(-0.5 * col_sp_cap_mm, 0.0), vb.pt(0.5 * col_sp_cap_mm, 0.0), (vb.x(0.0), vb.y(0.0) - 7.5), f"墩距 {col_sp_cap_mm:g}")
        add_arch_dim(msp, vb.pt(hw, 0.0), vb.pt(hw, cap_h_mm), (vb.x(hw) + 8.0, vb.y(0.5 * cap_h_mm)), f"{cap_h_mm:g}", angle=90.0)
        add_civil_text(msp, "一、桥墩盖梁配筋立面图 (1:65)", vb.x(0.0), 152.0, height=2.35, align="CENTER")
        vb.register(doc, msp, paper_bounds=(20.0, 150.0, 208.0, 232.0))

        # Lower-Left: 1:30 A-A Cap-Beam Cross-Section with 6-leg Stirrups & Exact GL1 / GL2 Circle Counts!
        vb_sec = ViewBox("pier_cap_section", 114.0, 68.0, 30.0, "mm")
        hcw = 0.5 * cap_w_mm
        msp.add_lwpolyline([vb_sec.pt(-hcw, 0.0), vb_sec.pt(hcw, 0.0), vb_sec.pt(hcw, cap_h_mm), vb_sec.pt(-hcw, cap_h_mm)], close=True, dxfattribs={"layer": "CONCRETE"})
        # Multi-leg stirrups: outer closed loop + 2 inner closed loops = 6 vertical legs!
        msp.add_lwpolyline([vb_sec.pt(-hcw + 55.0, 55.0), vb_sec.pt(hcw - 55.0, 55.0), vb_sec.pt(hcw - 55.0, cap_h_mm - 55.0), vb_sec.pt(-hcw + 55.0, cap_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        n_legs_cap = int(calc["substructure_checks"]["cap_beam"].get("stirrup_legs", 6))
        if n_legs_cap >= 6:
            msp.add_lwpolyline([vb_sec.pt(-0.52 * hcw, 55.0), vb_sec.pt(-0.16 * hcw, 55.0), vb_sec.pt(-0.16 * hcw, cap_h_mm - 55.0), vb_sec.pt(-0.52 * hcw, cap_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
            msp.add_lwpolyline([vb_sec.pt(0.16 * hcw, 55.0), vb_sec.pt(0.52 * hcw, 55.0), vb_sec.pt(0.52 * hcw, cap_h_mm - 55.0), vb_sec.pt(0.16 * hcw, cap_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        elif n_legs_cap >= 4:
            msp.add_lwpolyline([vb_sec.pt(-0.35 * hcw, 55.0), vb_sec.pt(0.35 * hcw, 55.0), vb_sec.pt(0.35 * hcw, cap_h_mm - 55.0), vb_sec.pt(-0.35 * hcw, cap_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})

        gl1_cnt = int(bars_by_id["GL1"]["count"])
        gl1_r1 = min(12, (gl1_cnt + 1) // 2) if gl1_cnt > 12 else gl1_cnt
        gl1_pts = _draw_row_bar_circles(msp, vb_sec, gl1_r1, -hcw + 85.0, hcw - 85.0, cap_h_mm - 75.0, dot_r_mm=0.9)
        if gl1_cnt > gl1_r1:
            gl1_pts += _draw_row_bar_circles(msp, vb_sec, gl1_cnt - gl1_r1, -hcw + 105.0, hcw - 105.0, cap_h_mm - 140.0, dot_r_mm=0.9)
        gl2_cnt = int(bars_by_id["GL2"]["count"])
        gl2_r1 = min(12, (gl2_cnt + 1) // 2) if gl2_cnt > 12 else gl2_cnt
        gl2_pts = _draw_row_bar_circles(msp, vb_sec, gl2_r1, -hcw + 85.0, hcw - 85.0, 75.0, dot_r_mm=0.9)
        if gl2_cnt > gl2_r1:
            gl2_pts += _draw_row_bar_circles(msp, vb_sec, gl2_cnt - gl2_r1, -hcw + 105.0, hcw - 105.0, 140.0, dot_r_mm=0.9)
        add_civil_text(msp, f"GL3 {n_legs_cap}肢Φ12@100", vb_sec.x(0.0), vb_sec.y(cap_h_mm) + 3.5, height=1.82, align="CENTER")
        add_arch_dim(msp, vb_sec.pt(-hcw, 0.0), vb_sec.pt(hcw, 0.0), (vb_sec.x(0.0), vb_sec.y(0.0) - 6.5), f"盖梁宽 {cap_w_mm:g}")
        add_civil_text(msp, "二、A-A 盖梁支点配筋横断面 (1:30)", vb_sec.x(0.0), 50.0, height=2.25, align="CENTER")
        vb_sec.register(doc, msp, paper_bounds=(72.0, 48.0, 158.0, 134.0))

        # Upper-Right: 1:35 Cap-Beam Cantilever End & Seismic Shear Block Detail (盖梁悬臂端及防震挡块配筋详图)
        vb_blk = ViewBox("pier_cap_end_detail", 322.0, 162.0, 35.0, "mm")
        msp.add_lwpolyline(
            [
                vb_blk.pt(-1400.0, haunch_cap),
                vb_blk.pt(-1400.0, cap_h_mm),
                vb_blk.pt(-1050.0, cap_h_mm),
                vb_blk.pt(-1050.0, cap_h_mm + 550.0),
                vb_blk.pt(-1400.0, cap_h_mm + 550.0),
            ],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_lwpolyline(
            [
                vb_blk.pt(-1400.0, haunch_cap),
                vb_blk.pt(-200.0, 0.0),
                vb_blk.pt(1200.0, 0.0),
                vb_blk.pt(1200.0, cap_h_mm),
                vb_blk.pt(-1050.0, cap_h_mm),
            ],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        # Edge pedestal + seismic block loop rebar + GL1 end anchorage hook
        msp.add_lwpolyline([vb_blk.pt(-850.0, cap_h_mm), vb_blk.pt(-350.0, cap_h_mm), vb_blk.pt(-350.0, cap_h_mm + 150.0), vb_blk.pt(-850.0, cap_h_mm + 150.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_blk.pt(1150.0, cap_h_mm - 80.0), vb_blk.pt(-1320.0, cap_h_mm - 80.0), vb_blk.pt(-1320.0, haunch_cap + 120.0)], close=False, dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb_blk.pt(-1330.0, cap_h_mm - 420.0), vb_blk.pt(-1330.0, cap_h_mm + 490.0), vb_blk.pt(-1110.0, cap_h_mm + 490.0), vb_blk.pt(-1110.0, cap_h_mm - 420.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_blk.pt(-1400.0, cap_h_mm + 550.0), vb_blk.pt(-1050.0, cap_h_mm + 550.0), (vb_blk.x(-1225.0), vb_blk.y(cap_h_mm + 550.0) + 5.5), "挡块 350")
        add_arch_dim(msp, vb_blk.pt(-1400.0, cap_h_mm), vb_blk.pt(-1400.0, cap_h_mm + 550.0), (vb_blk.x(-1400.0) - 6.0, vb_blk.y(cap_h_mm + 275.0)), "550", angle=90.0)
        add_civil_text(msp, "GL1梁端弯折锚固+抗震防落梁挡块箍筋", vb_blk.x(-100.0), 146.0, height=1.82, align="CENTER")
        add_civil_text(msp, "三、盖梁悬臂端及防震挡块详图 (1:35)", vb_blk.x(-100.0), 138.0, height=2.2, align="CENTER")
        vb_blk.register(doc, msp, paper_bounds=(246.0, 136.0, 396.0, 246.0))

        anchor_by_id = {
            "GL1": gl1_pts[-1] if gl1_pts else vb.pt(0.0, cap_h_mm - 75.0),
            "GL2": gl2_pts[-1] if gl2_pts else vb.pt(0.0, 75.0),
            "GL3": vb.pt(0.28 * hw, cap_h_mm - 75.0),
            "GL4": vb.pt(-0.62 * hw, 0.48 * cap_h_mm),
        }

    elif comp == "pier_column_pile":
        # QL-17: Twin-Column + Horizontal Pile-Top Tie Beam + Twin Bored Piles Elevation + A-A / B-B / C-C + I Joint!
        col_positions = calc["pier_cap_beam_1d"].get("column_positions_m") or [-2.6, 2.6]
        col_sp_mm = round(max(3200.0, float(col_positions[-1] - col_positions[0]) * 1000.0), 1)
        tie_w_mm = 1000.0
        tie_h_mm = 1200.0
        tot_h_mm = col_h_mm + tie_h_mm + pile_len_mm
        tot_w_mm = col_sp_mm + pile_d_mm + 400.0
        s_ep = next(
            (s for s in (100.0, 125.0, 150.0, 180.0, 200.0, 250.0, 300.0, 400.0) if tot_h_mm / s <= 170.0 and tot_w_mm / s <= 64.0),
            400.0,
        )
        vb_ep = ViewBox("pier_pile_elev", 78.0, 62.0, s_ep, "mm")
        ry_c0 = pile_len_mm + tie_h_mm
        ry_c1 = ry_c0 + col_h_mm
        collar_hw = 0.5 * pile_d_mm + 150.0
        half_sp = 0.5 * col_sp_mm

        # 1) Twin Bored Piles + Twin Pier Columns + Center Axes
        for rxc in (-half_sp, half_sp):
            msp.add_line((vb_ep.x(rxc), vb_ep.y(0.0) - 3.5), (vb_ep.x(rxc), vb_ep.y(ry_c1) + 4.5), dxfattribs={"layer": "CENTER"})
            # Bored friction pile [0 .. pile_len_mm]
            msp.add_lwpolyline(
                [vb_ep.pt(rxc - 0.5 * pile_d_mm, 0.0), vb_ep.pt(rxc + 0.5 * pile_d_mm, 0.0), vb_ep.pt(rxc + 0.5 * pile_d_mm, pile_len_mm), vb_ep.pt(rxc - 0.5 * pile_d_mm, pile_len_mm)],
                close=True,
                dxfattribs={"layer": "CONCRETE"},
            )
            for rx_p in (-0.36 * pile_d_mm, 0.36 * pile_d_mm):
                msp.add_line(vb_ep.pt(rxc + rx_p, 180.0), vb_ep.pt(rxc + rx_p, pile_len_mm + 980.0), dxfattribs={"layer": "REBAR"})
            n_hoop_vis = max(3, min(11, int(round(pile_len_mm / 1600.0))))
            for k_h in range(n_hoop_vis):
                ry_p = 250.0 + max(300.0, pile_len_mm - 500.0) * (k_h / max(1, n_hoop_vis - 1))
                if ry_p < pile_len_mm:
                    msp.add_line(vb_ep.pt(rxc - 0.42 * pile_d_mm, ry_p), vb_ep.pt(rxc + 0.42 * pile_d_mm, ry_p), dxfattribs={"layer": "REBAR_STIRRUP"})

            # Pier column [ry_c0 .. ry_c1]
            msp.add_lwpolyline(
                [vb_ep.pt(rxc - 0.5 * col_d_mm, ry_c0), vb_ep.pt(rxc + 0.5 * col_d_mm, ry_c0), vb_ep.pt(rxc + 0.5 * col_d_mm, ry_c1), vb_ep.pt(rxc - 0.5 * col_d_mm, ry_c1)],
                close=True,
                dxfattribs={"layer": "CONCRETE"},
            )
            for rx_c in (-0.35 * col_d_mm, 0.35 * col_d_mm):
                msp.add_line(vb_ep.pt(rxc + rx_c, pile_len_mm + 220.0), vb_ep.pt(rxc + rx_c, ry_c1 + 450.0), dxfattribs={"layer": "REBAR"})
            for k_c in range(6):
                ry_ch = ry_c0 + 300.0 + max(300.0, col_h_mm - 600.0) * (k_c / 5.0)
                if ry_ch < ry_c1:
                    msp.add_line(vb_ep.pt(rxc - 0.41 * col_d_mm, ry_ch), vb_ep.pt(rxc + 0.41 * col_d_mm, ry_ch), dxfattribs={"layer": "REBAR_STIRRUP"})

        # 2) Continuous Horizontal Pile-Top Tie Beam connecting BOTH columns & piles [pile_len_mm .. ry_c0]
        msp.add_lwpolyline(
            [
                vb_ep.pt(-half_sp - collar_hw, pile_len_mm),
                vb_ep.pt(half_sp + collar_hw, pile_len_mm),
                vb_ep.pt(half_sp + collar_hw, ry_c0),
                vb_ep.pt(-half_sp - collar_hw, ry_c0),
            ],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        # XL1 top & bottom tie-beam main bars spanning across and anchoring into both column/pile cores!
        msp.add_line(vb_ep.pt(-half_sp - 480.0, ry_c0 - 95.0), vb_ep.pt(half_sp + 480.0, ry_c0 - 95.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb_ep.pt(-half_sp - 480.0, pile_len_mm + 95.0), vb_ep.pt(half_sp + 480.0, pile_len_mm + 95.0), dxfattribs={"layer": "REBAR"})
        for k_tb in range(7):
            rx_tb = (-half_sp + 0.65 * pile_d_mm) + (col_sp_mm - 1.30 * pile_d_mm) * (k_tb / 6.0)
            msp.add_line(vb_ep.pt(rx_tb, pile_len_mm + 95.0), vb_ep.pt(rx_tb, ry_c0 - 95.0), dxfattribs={"layer": "REBAR_STIRRUP"})

        # Section-cut markers A-A (column), B-B (pile), C-C (tie beam), and Detail I callout ring
        y_aa = vb_ep.y(ry_c0 + 0.55 * col_h_mm)
        draw_section_cut_symbol(msp, vb_ep.x(half_sp - 0.65 * col_d_mm), y_aa, vb_ep.x(half_sp + 0.65 * col_d_mm), y_aa, "A", look_dx=0.0, look_dy=-1.0, tick_len=2.8, arrow_len=2.8)
        y_bb = vb_ep.y(0.54 * pile_len_mm)
        draw_section_cut_symbol(msp, vb_ep.x(half_sp - 0.65 * pile_d_mm), y_bb, vb_ep.x(half_sp + 0.65 * pile_d_mm), y_bb, "B", look_dx=0.0, look_dy=-1.0, tick_len=2.8, arrow_len=2.8)
        x_cc = vb_ep.x(0.0)
        draw_section_cut_symbol(msp, x_cc, vb_ep.y(pile_len_mm) - 1.5, x_cc, vb_ep.y(ry_c0) + 1.5, "C", look_dx=1.0, look_dy=0.0, tick_len=2.8, arrow_len=2.8)
        draw_detail_callout(
            msp,
            vb_ep.x(half_sp),
            vb_ep.y(pile_len_mm + 0.5 * tie_h_mm),
            5.0,
            "I 节点(1:30)",
            leader_to=(vb_ep.x(half_sp + collar_hw) + 2.5, vb_ep.y(ry_c0) + 5.5),
        )

        # True-scale dimensions on twin-column + tie-beam + pile elevation
        add_arch_dim(msp, vb_ep.pt(-half_sp, ry_c1), vb_ep.pt(half_sp, ry_c1), (vb_ep.x(0.0), vb_ep.y(ry_c1) + 13.0), f"墩距 {col_sp_mm:g}")
        add_arch_dim(msp, vb_ep.pt(-half_sp - 0.5 * col_d_mm, ry_c1), vb_ep.pt(-half_sp + 0.5 * col_d_mm, ry_c1), (vb_ep.x(-half_sp), vb_ep.y(ry_c1) + 6.0), f"{col_d_mm:g}")
        add_arch_dim(msp, vb_ep.pt(-half_sp - 0.5 * pile_d_mm, 0.0), vb_ep.pt(-half_sp + 0.5 * pile_d_mm, 0.0), (vb_ep.x(-half_sp), vb_ep.y(0.0) - 6.0), f"{pile_d_mm:g}")
        x_dim_l = vb_ep.x(-half_sp - collar_hw) - 12.2
        add_arch_dim(msp, vb_ep.pt(-half_sp - 0.5 * pile_d_mm, 0.0), vb_ep.pt(-half_sp - 0.5 * pile_d_mm, pile_len_mm), (x_dim_l, vb_ep.y(0.5 * pile_len_mm)), f"桩长 {pile_len_mm:g}", angle=90.0)
        add_arch_dim(msp, vb_ep.pt(-half_sp - collar_hw, pile_len_mm), vb_ep.pt(-half_sp - collar_hw, ry_c0), (x_dim_l, vb_ep.y(pile_len_mm + 0.5 * tie_h_mm)), f"{tie_h_mm:g}", angle=90.0)
        add_arch_dim(msp, vb_ep.pt(-half_sp - 0.5 * col_d_mm, ry_c0), vb_ep.pt(-half_sp - 0.5 * col_d_mm, ry_c1), (x_dim_l, vb_ep.y(ry_c0 + 0.5 * col_h_mm)), f"墩高 {col_h_mm:g}", angle=90.0)
        add_civil_text(msp, f"双柱墩、桩顶系梁及钻孔桩正立面 (1:{int(s_ep)})", vb_ep.x(0.0), 47.5, height=2.2, align="CENTER")
        vb_ep.register(doc, msp, paper_bounds=(22.0, 46.0, 134.0, 256.0))

        # Center-Top: 1:25 A-A Pier Column Section (exact DZ1 count = 20!)
        vb_csec = ViewBox("pier_col_section", 180.0, 212.0, 25.0, "mm")
        msp.add_circle(vb_csec.pt(0.0, 0.0), vb_csec.span(0.5 * col_d_mm), dxfattribs={"layer": "CONCRETE"})
        _draw_hoop_polygon(msp, vb_csec, 0.5 * col_d_mm - 55.0)
        dz1_cnt = int(bars_by_id["DZ1"]["count"])
        dz1_pts = _draw_perimeter_bar_circles(msp, vb_csec, dz1_cnt, 0.5 * col_d_mm - 78.0, dot_r_mm=0.92)
        add_civil_text(msp, f"DZ1 {dz1_cnt}Φ{bars_by_id['DZ1']['dia_mm']:g}", vb_csec.x(0.0), vb_csec.y(0.0), height=1.88, align="MIDDLE_CENTER")
        add_arch_dim(msp, vb_csec.pt(-0.5 * col_d_mm, 0.0), vb_csec.pt(0.5 * col_d_mm, 0.0), (vb_csec.x(0.0), vb_csec.y(0.5 * col_d_mm) + 6.5), f"墩径 {col_d_mm:g}")
        add_civil_text(msp, "A-A 墩柱配筋断面 (1:25)", vb_csec.x(0.0), 178.0, height=2.2, align="CENTER")
        vb_csec.register(doc, msp, paper_bounds=(142.0, 174.0, 220.0, 252.0))

        # Center-Bottom: 1:25 B-B Bored Pile Section (exact ZJ1 count = 22!)
        vb_psec = ViewBox("bored_pile_section", 180.0, 108.0, 25.0, "mm")
        msp.add_circle(vb_psec.pt(0.0, 0.0), vb_psec.span(0.5 * pile_d_mm), dxfattribs={"layer": "CONCRETE"})
        _draw_hoop_polygon(msp, vb_psec, 0.5 * pile_d_mm - 65.0)
        zj1_cnt = int(bars_by_id["ZJ1"]["count"])
        zj1_pts = _draw_perimeter_bar_circles(msp, vb_psec, zj1_cnt, 0.5 * pile_d_mm - 88.0, dot_r_mm=0.92)
        add_civil_text(msp, f"ZJ1 {zj1_cnt}Φ{bars_by_id['ZJ1']['dia_mm']:g}", vb_psec.x(0.0), vb_psec.y(0.0), height=1.88, align="MIDDLE_CENTER")
        add_arch_dim(msp, vb_psec.pt(-0.5 * pile_d_mm, 0.0), vb_psec.pt(0.5 * pile_d_mm, 0.0), (vb_psec.x(0.0), vb_psec.y(0.5 * pile_d_mm) + 6.5), f"桩径 {pile_d_mm:g}")
        add_civil_text(msp, "B-B 钻孔桩钢筋笼断面 (1:25)", vb_psec.x(0.0), 70.0, height=2.2, align="CENTER")
        vb_psec.register(doc, msp, paper_bounds=(142.0, 66.0, 220.0, 152.0))

        # Upper-Right Left: 1:30 Detail I — Tie-Beam to Pier Column / Pile-Top Joint Anchorage Detail!
        vb_tj = ViewBox("tie_beam_joint_detail", 282.0, 180.0, 30.0, "mm")
        # Concrete joint contour: left incoming tie beam [-1450..-850] + collar [-850..+850] x [0..1200] + upper col + lower pile
        msp.add_lwpolyline(
            [vb_tj.pt(-1450.0, 0.0), vb_tj.pt(850.0, 0.0), vb_tj.pt(850.0, tie_h_mm), vb_tj.pt(-1450.0, tie_h_mm)],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line(vb_tj.pt(-1450.0, -90.0), vb_tj.pt(-1450.0, tie_h_mm + 90.0), dxfattribs={"layer": "THIN"})
        msp.add_lwpolyline(
            [vb_tj.pt(-0.5 * col_d_mm, tie_h_mm), vb_tj.pt(-0.5 * col_d_mm, tie_h_mm + 850.0), vb_tj.pt(0.5 * col_d_mm, tie_h_mm + 850.0), vb_tj.pt(0.5 * col_d_mm, tie_h_mm)],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_lwpolyline(
            [vb_tj.pt(-0.5 * pile_d_mm, 0.0), vb_tj.pt(-0.5 * pile_d_mm, -850.0), vb_tj.pt(0.5 * pile_d_mm, -850.0), vb_tj.pt(0.5 * pile_d_mm, 0.0)],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line((vb_tj.x(0.0), vb_tj.y(-920.0)), (vb_tj.x(0.0), vb_tj.y(tie_h_mm + 920.0)), dxfattribs={"layer": "CENTER"})
        # DZ1 column bars anchored down through tie-beam joint into pile top
        for rx_dc in (-0.38 * col_d_mm, 0.38 * col_d_mm):
            msp.add_line(vb_tj.pt(rx_dc, -320.0), vb_tj.pt(rx_dc, tie_h_mm + 820.0), dxfattribs={"layer": "REBAR"})
        # ZJ1 pile bars anchored up into tie-beam joint (1050mm >= 40d)
        for rx_zp in (-0.42 * pile_d_mm, 0.42 * pile_d_mm):
            msp.add_line(vb_tj.pt(rx_zp, -820.0), vb_tj.pt(rx_zp, 1050.0), dxfattribs={"layer": "REBAR"})
        # XL1 tie-beam top & bottom longitudinal bars entering column/pile core (anchorage 780mm >= 35d=770 + 90° inward hook!)
        anch_xl_mm = float(
            ((calc["substructure_checks"].get("joint_anchorage") or {}).get("xl1_tie_beam_to_pier") or {}).get("design_la_mm", 780.0)
        )
        rx_face = -0.5 * col_d_mm
        rx_end = rx_face + anch_xl_mm  # -600 + 780 = +180mm past column centerline!
        msp.add_lwpolyline(
            [vb_tj.pt(-1450.0, tie_h_mm - 90.0), vb_tj.pt(rx_end, tie_h_mm - 90.0), vb_tj.pt(rx_end, tie_h_mm - 440.0)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        msp.add_lwpolyline(
            [vb_tj.pt(-1450.0, 90.0), vb_tj.pt(rx_end, 90.0), vb_tj.pt(rx_end, 440.0)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        # XL2 tie-beam stirrups (@100) + joint core horizontal hoops
        for rx_st in (-1320.0, -1160.0, -1000.0, -840.0):
            msp.add_line(vb_tj.pt(rx_st, 90.0), vb_tj.pt(rx_st, tie_h_mm - 90.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        for ry_jh in (280.0, 600.0, 920.0):
            msp.add_line(vb_tj.pt(-0.42 * pile_d_mm, ry_jh), vb_tj.pt(0.42 * pile_d_mm, ry_jh), dxfattribs={"layer": "REBAR_STIRRUP"})

        add_arch_dim(msp, vb_tj.pt(rx_face, tie_h_mm), vb_tj.pt(rx_end, tie_h_mm), (vb_tj.x(0.5 * (rx_face + rx_end)), vb_tj.y(tie_h_mm + 850.0) + 5.0), f"锚固 {anch_xl_mm:g}")
        add_arch_dim(msp, vb_tj.pt(850.0, 0.0), vb_tj.pt(850.0, tie_h_mm), (vb_tj.x(850.0) + 7.5, vb_tj.y(0.5 * tie_h_mm)), f"{tie_h_mm:g}", angle=90.0)
        add_civil_text(msp, "XL1锚入核心la=780≥35d=770(JTG3362§9.3.2)", vb_tj.x(-260.0), 141.5, height=1.75, align="CENTER")
        add_civil_text(msp, "I 系梁与墩柱/桩顶连接详图 (1:30)", vb_tj.x(-260.0), 134.5, height=2.1, align="CENTER")
        vb_tj.register(doc, msp, paper_bounds=(230.0, 132.0, 324.0, 256.0))

        # Upper-Right Right: 1:25 C-C Tie-Beam Cross-Section (exact XL1 count = 12!) + 1:50 XL2 4-Leg Stirrup Breakdown!
        vb_tsec = ViewBox("tie_beam_section", 370.0, 173.0, 25.0, "mm")
        htw = 0.5 * tie_w_mm
        msp.add_lwpolyline([vb_tsec.pt(-htw, 0.0), vb_tsec.pt(htw, 0.0), vb_tsec.pt(htw, tie_h_mm), vb_tsec.pt(-htw, tie_h_mm)], close=True, dxfattribs={"layer": "CONCRETE"})
        # 4-leg stirrup XL2 (outer 890x1090 + inner 340x1090 closed stirrup loops on REBAR_STIRRUP)
        msp.add_lwpolyline([vb_tsec.pt(-htw + 55.0, 55.0), vb_tsec.pt(htw - 55.0, 55.0), vb_tsec.pt(htw - 55.0, tie_h_mm - 55.0), vb_tsec.pt(-htw + 55.0, tie_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        msp.add_lwpolyline([vb_tsec.pt(-170.0, 55.0), vb_tsec.pt(170.0, 55.0), vb_tsec.pt(170.0, tie_h_mm - 55.0), vb_tsec.pt(-170.0, tie_h_mm - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        xl1_cnt = int(bars_by_id["XL1"]["count"]) if "XL1" in bars_by_id else 12
        xl2_sets = int(bars_by_id["XL2"]["count"]) if "XL2" in bars_by_id else 53
        xl1_top = xl1_cnt // 2
        xl1_pts = _draw_row_bar_circles(msp, vb_tsec, xl1_top, -htw + 85.0, htw - 85.0, tie_h_mm - 80.0, dot_r_mm=0.92)
        xl1_pts += _draw_row_bar_circles(msp, vb_tsec, xl1_cnt - xl1_top, -htw + 85.0, htw - 85.0, 80.0, dot_r_mm=0.92)
        add_civil_text(msp, f"XL1 {xl1_cnt}Φ22 | XL2 四肢Φ12@100({xl2_sets}组)", vb_tsec.x(0.0), vb_tsec.y(tie_h_mm) + 4.2, height=1.75, align="CENTER")
        add_arch_dim(msp, vb_tsec.pt(-htw, 0.0), vb_tsec.pt(htw, 0.0), (vb_tsec.x(0.0), vb_tsec.y(0.0) - 5.8), f"{tie_w_mm:g}")
        add_arch_dim(msp, vb_tsec.pt(htw, 0.0), vb_tsec.pt(htw, tie_h_mm), (vb_tsec.x(htw) + 6.5, vb_tsec.y(0.5 * tie_h_mm)), f"{tie_h_mm:g}", angle=90.0)
        add_civil_text(msp, "C-C 系梁断面 (1:25)", vb_tsec.x(0.0), 139.5, height=2.05, align="CENTER")
        vb_tsec.register(doc, msp, paper_bounds=(336.0, 137.0, 406.0, 250.0))

        # Lower-Right (above schedule): 1:50 XL2 4-Leg Composite Stirrup Exploded Breakdown (外箍890x1090 + 内箍340x1090 = 722.0cm/组)
        vb_xl2 = ViewBox("tie_beam_stirrup_detail", 345.5, 108.5, 50.0, "mm")
        outer_w_mm = tie_w_mm - 110.0  # 890
        outer_h_mm = tie_h_mm - 110.0  # 1090
        inner_w_mm = 340.0
        msp.add_lwpolyline(
            [vb_xl2.pt(0.0, 0.0), vb_xl2.pt(outer_w_mm, 0.0), vb_xl2.pt(outer_w_mm, outer_h_mm), vb_xl2.pt(0.0, outer_h_mm)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        msp.add_line(vb_xl2.pt(outer_w_mm, outer_h_mm), vb_xl2.pt(outer_w_mm - 140.0, outer_h_mm - 140.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_civil_text(msp, "+", vb_xl2.x(outer_w_mm) + 2.8, vb_xl2.y(0.5 * outer_h_mm) - 0.8, height=2.0, align="CENTER")
        rx_in0 = outer_w_mm + 280.0
        msp.add_lwpolyline(
            [vb_xl2.pt(rx_in0, 0.0), vb_xl2.pt(rx_in0 + inner_w_mm, 0.0), vb_xl2.pt(rx_in0 + inner_w_mm, outer_h_mm), vb_xl2.pt(rx_in0, outer_h_mm)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        msp.add_line(vb_xl2.pt(rx_in0 + inner_w_mm, outer_h_mm), vb_xl2.pt(rx_in0 + inner_w_mm - 110.0, outer_h_mm - 140.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_xl2.pt(0.0, 0.0), vb_xl2.pt(outer_w_mm, 0.0), (vb_xl2.x(0.5 * outer_w_mm), vb_xl2.y(0.0) - 4.5), f"{outer_w_mm:g}")
        add_arch_dim(msp, vb_xl2.pt(rx_in0, 0.0), vb_xl2.pt(rx_in0 + inner_w_mm, 0.0), (vb_xl2.x(rx_in0 + 0.5 * inner_w_mm), vb_xl2.y(0.0) - 4.5), f"{inner_w_mm:g}")
        add_arch_dim(msp, vb_xl2.pt(0.0, 0.0), vb_xl2.pt(0.0, outer_h_mm), (vb_xl2.x(0.0) - 4.2, vb_xl2.y(0.5 * outer_h_mm)), f"{outer_h_mm:g}", angle=90.0)
        tx_xl2 = vb_xl2.x(rx_in0 + inner_w_mm) + 2.2
        add_civil_text(msp, "XL2四肢复合箍大样 (1:50)", tx_xl2, 128.0, height=1.68, align="LEFT")
        add_civil_text(msp, f"按组计: {xl2_sets}组(@100)", tx_xl2, 123.2, height=1.62, align="LEFT")
        add_civil_text(msp, "外箍L1=416cm+内箍L2=306cm", tx_xl2, 118.4, height=1.58, align="LEFT")
        add_civil_text(msp, "每组单长=722.0cm(含135°钩)", tx_xl2, 113.6, height=1.58, align="LEFT")
        vb_xl2.register(doc, msp, paper_bounds=(336.0, 101.0, 408.0, 134.0))

        anchor_by_id = {
            "DZ1": dz1_pts[2] if len(dz1_pts) > 2 else vb_csec.pt(0.0, 480.0),
            "DZ2": vb_ep.pt(-half_sp - 0.41 * col_d_mm, ry_c0 + 0.55 * col_h_mm),
            "XL1": xl1_pts[0] if xl1_pts else vb_tsec.pt(-350.0, tie_h_mm - 80.0),
            "XL2": vb_tsec.pt(170.0, 0.72 * tie_h_mm),
            "ZJ1": zj1_pts[2] if len(zj1_pts) > 2 else vb_psec.pt(0.0, 560.0),
            "ZJ2": vb_ep.pt(-half_sp - 0.42 * pile_d_mm, 0.40 * pile_len_mm),
        }

    elif comp == "pier_cap_secondary":
        # Sheet 18: Pier Cap Bent-Up Bars (GL4), GL3 6-Leg Stirrup Breakdown (1240cm/组) & Seismic Stoppers (DK1/DK2)
        vb_cb2 = ViewBox("pier_cap_bent_elev", 122.0, 192.0, 50.0, "mm")
        h_cap_l = 0.5 * cap_len_mm
        msp.add_lwpolyline([vb_cb2.pt(-h_cap_l, 0.0), vb_cb2.pt(h_cap_l, 0.0), vb_cb2.pt(h_cap_l, cap_h_mm), vb_cb2.pt(-h_cap_l, cap_h_mm)], close=True, dxfattribs={"layer": "CONCRETE"})
        # Seismic stopper blocks on both ends [±(h_cap_l-350) .. ±h_cap_l, cap_h_mm .. cap_h_mm+450]
        for sx_dk in (-1.0, 1.0):
            msp.add_lwpolyline(
                [vb_cb2.pt(sx_dk * (h_cap_l - 350.0), cap_h_mm), vb_cb2.pt(sx_dk * h_cap_l, cap_h_mm), vb_cb2.pt(sx_dk * h_cap_l, cap_h_mm + 450.0), vb_cb2.pt(sx_dk * (h_cap_l - 300.0), cap_h_mm + 450.0)],
                close=True,
                dxfattribs={"layer": "CONCRETE"},
            )
        # GL4 bent-up shear bar trajectory
        msp.add_lwpolyline(
            [vb_cb2.pt(-h_cap_l + 250.0, cap_h_mm - 120.0), vb_cb2.pt(-2800.0, cap_h_mm - 120.0), vb_cb2.pt(-1700.0, 120.0), vb_cb2.pt(1700.0, 120.0), vb_cb2.pt(2800.0, cap_h_mm - 120.0), vb_cb2.pt(h_cap_l - 250.0, cap_h_mm - 120.0)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        add_arch_dim(msp, vb_cb2.pt(-h_cap_l, 0.0), vb_cb2.pt(h_cap_l, 0.0), (vb_cb2.x(0.0), vb_cb2.y(0.0) - 6.0), f"盖梁全长 {cap_len_mm:g}")
        add_arch_dim(msp, vb_cb2.pt(h_cap_l, 0.0), vb_cb2.pt(h_cap_l, cap_h_mm), (vb_cb2.x(h_cap_l) + 7.0, vb_cb2.y(0.5 * cap_h_mm)), f"{cap_h_mm:g}", angle=90.0)
        add_civil_text(msp, "一、盖梁GL4弯起斜筋及抗震挡块立面图 (1:50)", vb_cb2.x(0.0), 178.0, height=2.15, align="CENTER")
        vb_cb2.register(doc, msp, paper_bounds=(26.0, 176.0, 224.0, 246.0))

        # Lower-Left: 1:25 GL3 6-Leg Stirrup Group Breakdown (外箍1480×1280 + 2内箍480×1280 = 1240cm/组)
        vb_gl3 = ViewBox("pier_cap_stirrup_detail", 76.0, 78.0, 25.0, "mm")
        ow, oh, iw = cap_w_mm - 120.0, cap_h_mm - 120.0, 480.0
        msp.add_lwpolyline([vb_gl3.pt(-0.5 * ow, 0.0), vb_gl3.pt(0.5 * ow, 0.0), vb_gl3.pt(0.5 * ow, oh), vb_gl3.pt(-0.5 * ow, oh)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        for sx_in in (-260.0, 260.0):
            msp.add_lwpolyline([vb_gl3.pt(sx_in - 0.5 * iw, 20.0), vb_gl3.pt(sx_in + 0.5 * iw, 20.0), vb_gl3.pt(sx_in + 0.5 * iw, oh - 20.0), vb_gl3.pt(sx_in - 0.5 * iw, oh - 20.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_gl3.pt(-0.5 * ow, 0.0), vb_gl3.pt(0.5 * ow, 0.0), (vb_gl3.x(0.0), vb_gl3.y(0.0) - 5.5), f"外箍宽 {ow:g}")
        add_civil_text(msp, "二、GL3六肢复合箍大样 (1:25，每组1240cm)", vb_gl3.x(0.0), 62.0, height=2.0, align="CENTER")
        vb_gl3.register(doc, msp, paper_bounds=(32.0, 60.0, 122.0, 146.0))

        # Lower-Center: 1:20 DK1/DK2 Seismic Stopper Block Detail
        vb_dk = ViewBox("pier_cap_stopper_detail", 176.0, 86.0, 20.0, "mm")
        msp.add_lwpolyline([vb_dk.pt(-220.0, -350.0), vb_dk.pt(350.0, -350.0), vb_dk.pt(350.0, 450.0), vb_dk.pt(50.0, 450.0), vb_dk.pt(0.0, 0.0), vb_dk.pt(-220.0, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        dk1_pt = vb_dk.pt(180.0, 280.0)
        dk2_pt = vb_dk.pt(180.0, 140.0)
        msp.add_lwpolyline([vb_dk.pt(60.0, -280.0), vb_dk.pt(60.0, 390.0), vb_dk.pt(290.0, 390.0), vb_dk.pt(290.0, -280.0)], close=False, dxfattribs={"layer": "REBAR"})
        for ry_dk in (100.0, 220.0, 340.0):
            msp.add_line(vb_dk.pt(55.0, ry_dk), vb_dk.pt(300.0, ry_dk), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_dk.pt(0.0, 0.0), vb_dk.pt(350.0, 0.0), (vb_dk.x(175.0), vb_dk.y(450.0) + 5.5), "挡块 350")
        add_arch_dim(msp, vb_dk.pt(350.0, 0.0), vb_dk.pt(350.0, 450.0), (vb_dk.x(350.0) + 6.5, vb_dk.y(225.0)), "450", angle=90.0)
        add_civil_text(msp, "三、抗震挡块配筋详图 (1:20)", vb_dk.x(80.0), 62.0, height=2.0, align="CENTER")
        vb_dk.register(doc, msp, paper_bounds=(134.0, 60.0, 222.0, 146.0))
        anchor_by_id = {"DK1": dk1_pt, "DK2": dk2_pt}

    elif comp == "tie_beam_joint":
        # Sheet 19-1: Pile-Top Tie-Beam Full Reinforcement & Node Enlargement (1:40 / 1:20)
        col_sp_19 = round((calc["pier_cap_beam_1d"]["column_positions_m"][-1] - calc["pier_cap_beam_1d"]["column_positions_m"][0]) * 1000.0, 1)
        tie_w_19, tie_h_19 = 1000.0, 1200.0
        h_sp19 = 0.5 * col_sp_19
        collar_19 = 0.5 * pile_d_mm + 150.0
        vb_tbf = ViewBox("tie_beam_full_elev", 124.0, 196.0, 40.0, "mm")
        msp.add_lwpolyline(
            [vb_tbf.pt(-h_sp19 - collar_19, 0.0), vb_tbf.pt(h_sp19 + collar_19, 0.0), vb_tbf.pt(h_sp19 + collar_19, tie_h_19), vb_tbf.pt(-h_sp19 - collar_19, tie_h_19)],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        for rxc_19 in (-h_sp19, h_sp19):
            msp.add_lwpolyline([vb_tbf.pt(rxc_19 - 0.5 * col_d_mm, tie_h_19), vb_tbf.pt(rxc_19 - 0.5 * col_d_mm, tie_h_19 + 650.0), vb_tbf.pt(rxc_19 + 0.5 * col_d_mm, tie_h_19 + 650.0), vb_tbf.pt(rxc_19 + 0.5 * col_d_mm, tie_h_19)], close=False, dxfattribs={"layer": "CONCRETE"})
            msp.add_lwpolyline([vb_tbf.pt(rxc_19 - 0.5 * pile_d_mm, 0.0), vb_tbf.pt(rxc_19 - 0.5 * pile_d_mm, -650.0), vb_tbf.pt(rxc_19 + 0.5 * pile_d_mm, -650.0), vb_tbf.pt(rxc_19 + 0.5 * pile_d_mm, 0.0)], close=False, dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb_tbf.pt(-h_sp19 - 180.0, tie_h_19 - 90.0), vb_tbf.pt(h_sp19 + 180.0, tie_h_19 - 90.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb_tbf.pt(-h_sp19 - 180.0, 90.0), vb_tbf.pt(h_sp19 + 180.0, 90.0), dxfattribs={"layer": "REBAR"})
        for k_s19 in range(11):
            rx_s19 = (-h_sp19 + 800.0) + (col_sp_19 - 1600.0) * (k_s19 / 10.0)
            msp.add_line(vb_tbf.pt(rx_s19, 90.0), vb_tbf.pt(rx_s19, tie_h_19 - 90.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_tbf.pt(-h_sp19, tie_h_19 + 650.0), vb_tbf.pt(h_sp19, tie_h_19 + 650.0), (vb_tbf.x(0.0), vb_tbf.y(tie_h_19 + 650.0) + 5.5), f"墩桩中心距 {col_sp_19:g}")
        add_arch_dim(msp, vb_tbf.pt(h_sp19 + collar_19, 0.0), vb_tbf.pt(h_sp19 + collar_19, tie_h_19), (vb_tbf.x(h_sp19 + collar_19) + 6.5, vb_tbf.y(0.5 * tie_h_19)), f"系梁高 {tie_h_19:g}", angle=90.0)
        add_civil_text(msp, "一、桩顶横系梁全长配筋立面图 (1:40)", vb_tbf.x(0.0), 174.0, height=2.15, align="CENTER")
        vb_tbf.register(doc, msp, paper_bounds=(26.0, 172.0, 226.0, 254.0))

        # Lower-Left: 1:20 Core Joint Anchorage Enlargement (la = 780 >= 35d = 770)
        vb_tjc = ViewBox("tie_beam_core_joint", 78.0, 82.0, 20.0, "mm")
        msp.add_lwpolyline([vb_tjc.pt(-1100.0, 0.0), vb_tjc.pt(700.0, 0.0), vb_tjc.pt(700.0, tie_h_19), vb_tjc.pt(-1100.0, tie_h_19)], close=False, dxfattribs={"layer": "CONCRETE"})
        rx_f19 = -0.5 * col_d_mm
        rx_e19 = rx_f19 + 780.0
        msp.add_lwpolyline([vb_tjc.pt(-1100.0, tie_h_19 - 90.0), vb_tjc.pt(rx_e19, tie_h_19 - 90.0), vb_tjc.pt(rx_e19, tie_h_19 - 420.0)], close=False, dxfattribs={"layer": "REBAR"})
        msp.add_lwpolyline([vb_tjc.pt(-1100.0, 90.0), vb_tjc.pt(rx_e19, 90.0), vb_tjc.pt(rx_e19, 420.0)], close=False, dxfattribs={"layer": "REBAR"})
        add_arch_dim(msp, vb_tjc.pt(rx_f19, tie_h_19), vb_tjc.pt(rx_e19, tie_h_19), (vb_tjc.x(0.5 * (rx_f19 + rx_e19)), vb_tjc.y(tie_h_19) + 5.5), "锚固 la=780")
        add_civil_text(msp, "二、系梁与墩柱/桩顶核心节点放大 (1:20)", vb_tjc.x(-150.0), 62.0, height=2.0, align="CENTER")
        vb_tjc.register(doc, msp, paper_bounds=(20.0, 60.0, 126.0, 154.0))

        # Lower-Center: 1:20 C-C Tie-Beam Cross Section
        vb_tcc = ViewBox("tie_beam_cc_section", 176.0, 82.0, 20.0, "mm")
        htw19 = 0.5 * tie_w_19
        msp.add_lwpolyline([vb_tcc.pt(-htw19, 0.0), vb_tcc.pt(htw19, 0.0), vb_tcc.pt(htw19, tie_h_19), vb_tcc.pt(-htw19, tie_h_19)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_tcc.pt(-htw19 + 55.0, 55.0), vb_tcc.pt(htw19 - 55.0, 55.0), vb_tcc.pt(htw19 - 55.0, tie_h_19 - 55.0), vb_tcc.pt(-htw19 + 55.0, tie_h_19 - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        msp.add_lwpolyline([vb_tcc.pt(-170.0, 55.0), vb_tcc.pt(170.0, 55.0), vb_tcc.pt(170.0, tie_h_19 - 55.0), vb_tcc.pt(-170.0, tie_h_19 - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        xl1_c19 = int(bars_by_id["XL1"]["count"])
        xl1_p19 = _draw_row_bar_circles(msp, vb_tcc, xl1_c19 // 2, -htw19 + 85.0, htw19 - 85.0, tie_h_19 - 80.0, dot_r_mm=0.95)
        xl1_p19 += _draw_row_bar_circles(msp, vb_tcc, xl1_c19 - xl1_c19 // 2, -htw19 + 85.0, htw19 - 85.0, 80.0, dot_r_mm=0.95)
        add_arch_dim(msp, vb_tcc.pt(-htw19, 0.0), vb_tcc.pt(htw19, 0.0), (vb_tcc.x(0.0), vb_tcc.y(0.0) - 5.5), f"系梁宽 {tie_w_19:g}")
        add_civil_text(msp, "三、C-C 系梁四肢箍断面 (1:20)", vb_tcc.x(0.0), 62.0, height=2.0, align="CENTER")
        vb_tcc.register(doc, msp, paper_bounds=(136.0, 60.0, 222.0, 154.0))
        anchor_by_id = {
            "XL1": xl1_p19[0] if xl1_p19 else vb_tcc.pt(-350.0, tie_h_19 - 80.0),
            "XL2": vb_tcc.pt(170.0, 0.65 * tie_h_19),
        }

    elif comp == "abutment_ear_backwall":
        # Sheet 21: Abutment Earwall & Backwall Expanded Reinforcement (EB1, EB2, EB3 + QT2 start/end pitch)
        ab_w21 = round(float(calc["project_summary"]["deck_width_m"]) * 1000.0, 1)
        ew_l21, bw_h21, ew_t21 = 2000.0, 1750.0, 450.0
        tot_exp_mm = ab_w21 + 2.0 * ew_l21  # 9000 + 4000 = 13000 mm
        vb_ebw = ViewBox("ear_backwall_expanded", 124.0, 196.0, 65.0, "mm")
        hx_exp = 0.5 * tot_exp_mm
        hx_bw = 0.5 * ab_w21
        msp.add_lwpolyline([vb_ebw.pt(-hx_exp, 0.0), vb_ebw.pt(hx_exp, 0.0), vb_ebw.pt(hx_exp, bw_h21), vb_ebw.pt(-hx_exp, bw_h21)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb_ebw.pt(-hx_bw, 0.0), vb_ebw.pt(-hx_bw, bw_h21), dxfattribs={"layer": "HIDDEN"})
        msp.add_line(vb_ebw.pt(hx_bw, 0.0), vb_ebw.pt(hx_bw, bw_h21), dxfattribs={"layer": "HIDDEN"})
        # EB1 horizontal main bars in both earwalls + QT2 vertical bars in backwall (with explicit 62.5mm start/end edge offset!)
        for ry_eb1 in (250.0, 650.0, 1050.0, 1450.0):
            msp.add_line(vb_ebw.pt(-hx_exp + 60.0, ry_eb1), vb_ebw.pt(-hx_bw + 650.0, ry_eb1), dxfattribs={"layer": "REBAR"})
            msp.add_line(vb_ebw.pt(hx_bw - 650.0, ry_eb1), vb_ebw.pt(hx_exp - 60.0, ry_eb1), dxfattribs={"layer": "REBAR"})
        for k_v21 in range(19):
            rx_v21 = (-hx_bw + 62.5) + (ab_w21 - 125.0) * (k_v21 / 18.0)
            msp.add_line(vb_ebw.pt(rx_v21, 50.0), vb_ebw.pt(rx_v21, bw_h21 - 60.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_ebw.pt(-hx_exp, bw_h21), vb_ebw.pt(-hx_bw, bw_h21), (vb_ebw.x(-0.5 * (hx_exp + hx_bw)), vb_ebw.y(bw_h21) + 6.0), f"左耳墙 {ew_l21:g}")
        add_arch_dim(msp, vb_ebw.pt(-hx_bw, bw_h21), vb_ebw.pt(hx_bw, bw_h21), (vb_ebw.x(0.0), vb_ebw.y(bw_h21) + 6.0), f"背墙展开 {ab_w21:g}(首末筋距边62.5,QT2@125示意抽画)")
        add_arch_dim(msp, vb_ebw.pt(hx_bw, bw_h21), vb_ebw.pt(hx_exp, bw_h21), (vb_ebw.x(0.5 * (hx_exp + hx_bw)), vb_ebw.y(bw_h21) + 6.0), f"右耳墙 {ew_l21:g}")
        add_civil_text(msp, "一、桥台耳墙及背墙钢筋展开立面图 (1:65)", vb_ebw.x(0.0), 182.0, height=2.15, align="CENTER")
        vb_ebw.register(doc, msp, paper_bounds=(20.0, 180.0, 228.0, 244.0))

        # Lower-Left: 1:25 Earwall Cantilever Section & Corner Diagonal Rebar (EB1, EB2, EB3)
        vb_ewc = ViewBox("earwall_cantilever_sec", 112.0, 84.0, 25.0, "mm")
        msp.add_lwpolyline([vb_ewc.pt(0.0, 0.0), vb_ewc.pt(ew_l21 + 550.0, 0.0), vb_ewc.pt(ew_l21 + 550.0, ew_t21), vb_ewc.pt(550.0, ew_t21), vb_ewc.pt(550.0, 950.0), vb_ewc.pt(0.0, 950.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        eb1_p = vb_ewc.pt(1400.0, ew_t21 - 55.0)
        eb2_p = vb_ewc.pt(1050.0, 80.0)
        eb3_p = vb_ewc.pt(480.0, 480.0)
        msp.add_lwpolyline([vb_ewc.pt(70.0, 860.0), vb_ewc.pt(70.0, 55.0), vb_ewc.pt(ew_l21 + 480.0, 55.0), vb_ewc.pt(ew_l21 + 480.0, ew_t21 - 55.0), vb_ewc.pt(180.0, ew_t21 - 55.0)], close=False, dxfattribs={"layer": "REBAR"})
        msp.add_line(vb_ewc.pt(260.0, 720.0), vb_ewc.pt(820.0, 160.0), dxfattribs={"layer": "REBAR"})
        for k_eb2 in range(8):
            msp.add_circle(vb_ewc.pt(700.0 + k_eb2 * 220.0, 80.0), 0.85, dxfattribs={"layer": "REBAR_STIRRUP"})
        add_arch_dim(msp, vb_ewc.pt(550.0, 0.0), vb_ewc.pt(ew_l21 + 550.0, 0.0), (vb_ewc.x(550.0 + 0.5 * ew_l21), vb_ewc.y(0.0) - 5.5), f"耳墙悬臂 {ew_l21:g}")
        add_arch_dim(msp, vb_ewc.pt(ew_l21 + 550.0, 0.0), vb_ewc.pt(ew_l21 + 550.0, ew_t21), (vb_ewc.x(ew_l21 + 550.0) + 6.5, vb_ewc.y(0.5 * ew_t21)), f"耳墙厚 {ew_t21:g}", angle=90.0)
        add_civil_text(msp, "二、耳背墙隅角及悬臂水平剖面 (1:25，单台EB1~EB3重312.2kg、两台624.4kg已计入台账)", vb_ewc.x(1100.0), 62.0, height=1.95, align="CENTER")
        vb_ewc.register(doc, msp, paper_bounds=(54.0, 60.0, 224.0, 142.0))
        anchor_by_id = {"EB1": eb1_p, "EB2": eb2_p, "EB3": eb3_p}

    else:
        # comp == "abutment_rebar" (QL-18 / 20): Reorganized 5-View Sheet!
        # Upper-Left: Abutment Plan (定位耳墙方向/双桩位/道路中心线/A-A剖切)
        # Upper-Right: Abutment Front Elevation (桩折断/B-B剖切) + Side View (I节点引出)
        # Lower-Left -> Center -> Mid-Right: A-A Cap Section -> I Wall-Cap Anchorage Detail -> B-B Pile Section!
        ab_cap_len = round(float(calc["project_summary"]["deck_width_m"]) * 1000.0, 1)
        ab_pile_sp = round(max(3600.0, ab_cap_len - 3600.0), 1)
        abut_cap_w = 1800.0
        abut_cap_h = 1400.0
        bw_thk = 550.0
        bw_h = 1750.0
        ew_len = 2000.0
        ew_thk = 450.0
        n_ped_ab = int(calc["lateral_distribution"]["n_girders"])
        ped_sp_ab = float(calc["lateral_distribution"]["girder_spacing_m"]) * 1000.0
        hcw = 0.5 * abut_cap_w
        hcl = 0.5 * ab_cap_len
        h_psp = 0.5 * ab_pile_sp

        # 1) Upper-Left: 1:80 Abutment Plan View (桥台平面定位图)
        vb_plan = ViewBox("abutment_plan", 108.0, 212.0, 80.0, "mm")
        # Cap outline (9000 x 1800) + Backwall inner line (y = +hcw - bw_thk)
        msp.add_lwpolyline(
            [vb_plan.pt(-hcl, -hcw), vb_plan.pt(hcl, -hcw), vb_plan.pt(hcl, hcw), vb_plan.pt(-hcl, hcw)],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line(vb_plan.pt(-hcl, hcw - bw_thk), vb_plan.pt(hcl, hcw - bw_thk), dxfattribs={"layer": "THIN"})
        # Both Earwalls extending rearward toward embankment (+hcw .. +hcw + ew_len)
        for sx_ew in (-1.0, 1.0):
            x_outer = sx_ew * hcl
            x_inner = sx_ew * (hcl - ew_thk)
            msp.add_lwpolyline(
                [vb_plan.pt(x_outer, hcw), vb_plan.pt(x_inner, hcw), vb_plan.pt(x_inner, hcw + ew_len), vb_plan.pt(x_outer, hcw + ew_len)],
                close=True,
                dxfattribs={"layer": "CONCRETE"},
            )
        # Hidden bored pile circles + pile center crosshairs at x = ±h_psp
        for rx_pl in (-h_psp, h_psp):
            msp.add_circle(vb_plan.pt(rx_pl, 0.0), vb_plan.span(0.5 * pile_d_mm), dxfattribs={"layer": "HIDDEN"})
            msp.add_line(vb_plan.pt(rx_pl - 900.0, 0.0), vb_plan.pt(rx_pl + 900.0, 0.0), dxfattribs={"layer": "CENTER"})
            msp.add_line(vb_plan.pt(rx_pl, -900.0), vb_plan.pt(rx_pl, 900.0), dxfattribs={"layer": "CENTER"})
        # 5 Bearing pedestals (520x520) along span side (y_plan = -600 .. -80, cleanly clear of backwall y=+350..+900)
        ped_centers_ab: list[float] = []
        for i_p in range(n_ped_ab):
            rx_pd = (i_p - 0.5 * (n_ped_ab - 1)) * ped_sp_ab
            ped_centers_ab.append(rx_pd)
            msp.add_lwpolyline(
                [vb_plan.pt(rx_pd - 260.0, -600.0), vb_plan.pt(rx_pd + 260.0, -600.0), vb_plan.pt(rx_pd + 260.0, -80.0), vb_plan.pt(rx_pd - 260.0, -80.0)],
                close=True,
                dxfattribs={"layer": "THIN"},
            )
        # Target pedestal for A-A cut: 2# pedestal (rx = -1950mm, clear of road centerline & clear of pile circle!)
        aa_target_rx = ped_centers_ab[1] if len(ped_centers_ab) >= 2 else 0.0
        add_civil_text(msp, "2#垫石", vb_plan.x(aa_target_rx + 280.0), vb_plan.y(-540.0), height=1.68, align="LEFT")

        # Road centerline + directional labels + A-A vertical section cut passing dead-center through 2# pedestal & backwall!
        msp.add_line((vb_plan.x(0.0), vb_plan.y(-hcw) - 10.0), (vb_plan.x(0.0), vb_plan.y(hcw + ew_len) + 8.0), dxfattribs={"layer": "CENTER"})
        add_civil_text(msp, "道路中心线", vb_plan.x(0.0) + 1.5, vb_plan.y(hcw + ew_len) + 5.0, height=1.9, align="LEFT")
        add_civil_text(msp, "耳墙(向台后伸)", vb_plan.x(-hcl + ew_thk) + 1.5, vb_plan.y(hcw + 0.55 * ew_len), height=1.82, align="LEFT")
        add_civil_text(msp, "桩中心", vb_plan.x(h_psp + 0.5 * pile_d_mm) + 1.2, vb_plan.y(0.0) + 1.0, height=1.8, align="LEFT")
        ax_sp = vb_plan.x(-3000.0)
        ay_sp0, ay_sp1 = vb_plan.y(-hcw) - 2.0, vb_plan.y(-hcw) - 5.5
        msp.add_line((ax_sp, ay_sp0), (ax_sp, ay_sp1), dxfattribs={"layer": "DIM"})
        msp.add_lwpolyline([(ax_sp, ay_sp1), (ax_sp - 0.8, ay_sp1 + 1.5), (ax_sp + 0.8, ay_sp1 + 1.5)], close=True, dxfattribs={"layer": "DIM"})
        add_civil_text(msp, "跨径侧", ax_sp + 1.5, ay_sp1 + 0.4, height=1.82, align="LEFT")

        # look_dx=+1.0 (looking right): left of A-A section = +y_plan (台背背墙), right of A-A section = -y_plan (2#支座垫石)
        x_aa_plan = vb_plan.x(aa_target_rx)
        draw_section_cut_symbol(msp, x_aa_plan, vb_plan.y(-hcw) - 2.2, x_aa_plan, vb_plan.y(hcw) + 2.2, "A", look_dx=1.0, look_dy=0.0, tick_len=3.0, arrow_len=3.0)

        add_arch_dim(msp, vb_plan.pt(-hcl, -hcw), vb_plan.pt(hcl, -hcw), (vb_plan.x(0.0), vb_plan.y(-hcw) - 8.5), f"台帽长 {ab_cap_len:g}")
        add_arch_dim(msp, vb_plan.pt(-h_psp, 0.0), vb_plan.pt(h_psp, 0.0), (vb_plan.x(0.0), vb_plan.y(hcw) + 12.0), f"桩距 {ab_pile_sp:g}")
        add_arch_dim(msp, vb_plan.pt(-hcl, hcw), vb_plan.pt(-hcl, hcw + ew_len), (vb_plan.x(-hcl) - 8.0, vb_plan.y(hcw + 0.5 * ew_len)), f"耳墙 {ew_len:g}", angle=90.0)
        add_arch_dim(msp, vb_plan.pt(hcl, -hcw), vb_plan.pt(hcl, hcw), (vb_plan.x(hcl) + 8.0, vb_plan.y(0.0)), f"{abut_cap_w:g}", angle=90.0)
        add_civil_text(msp, "桥台平面定位图 (1:80)", vb_plan.x(0.0), 178.0, height=2.25, align="CENTER")
        vb_plan.register(doc, msp, paper_bounds=(32.0, 176.0, 184.0, 266.0))

        # 2) Upper-Right: 1:80 Abutment Front Elevation (含5块垫石 + A-A/B-B剖切 + 桩折断) + 1:60 Side View (I节点引出)
        vb_elev = ViewBox("abutment_elev", 268.0, 212.0, 80.0, "mm")
        pile_stub_mm = 2600.0
        msp.add_lwpolyline(
            [vb_elev.pt(-hcl, 0.0), vb_elev.pt(hcl, 0.0), vb_elev.pt(hcl, abut_cap_h), vb_elev.pt(-hcl, abut_cap_h)],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_lwpolyline(
            [vb_elev.pt(-hcl, abut_cap_h), vb_elev.pt(hcl, abut_cap_h), vb_elev.pt(hcl, abut_cap_h + bw_h), vb_elev.pt(-hcl, abut_cap_h + bw_h)],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        # 5 Bearing pedestals sitting in front of backwall on cap top [abut_cap_h .. abut_cap_h + 150]
        for rx_pd in ped_centers_ab:
            msp.add_lwpolyline(
                [vb_elev.pt(rx_pd - 260.0, abut_cap_h), vb_elev.pt(rx_pd + 260.0, abut_cap_h), vb_elev.pt(rx_pd + 260.0, abut_cap_h + 150.0), vb_elev.pt(rx_pd - 260.0, abut_cap_h + 150.0)],
                close=True,
                dxfattribs={"layer": "THIN"},
            )
        # Cap QT1 horizontal main bars + Backwall QT2 vertical wall bars (@125)
        anch_qt2_mm = float(
            ((calc["substructure_checks"].get("joint_anchorage") or {}).get("qt2_wall_to_abutment_cap") or {}).get("design_la_mm", 560.0)
        )
        msp.add_line(vb_elev.pt(-hcl + 80.0, abut_cap_h - 85.0), vb_elev.pt(hcl - 80.0, abut_cap_h - 85.0), dxfattribs={"layer": "REBAR"})
        msp.add_line(vb_elev.pt(-hcl + 80.0, 85.0), vb_elev.pt(hcl - 80.0, 85.0), dxfattribs={"layer": "REBAR"})
        for k_bw in range(15):
            rx_bw = -hcl + 220.0 + k_bw * ((ab_cap_len - 440.0) / 14.0)
            msp.add_line(vb_elev.pt(rx_bw, abut_cap_h - anch_qt2_mm), vb_elev.pt(rx_bw, abut_cap_h + bw_h - 80.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        # Twin bored piles shortened with S-break + QT3 cage + B-B section cut on right pile + A-A section cut on 2# pedestal!
        for rx_pl in (-h_psp, h_psp):
            msp.add_line((vb_elev.x(rx_pl), vb_elev.y(-pile_stub_mm) - 4.0), (vb_elev.x(rx_pl), vb_elev.y(abut_cap_h + bw_h) + 4.0), dxfattribs={"layer": "CENTER"})
            msp.add_line(vb_elev.pt(rx_pl - 0.5 * pile_d_mm, 0.0), vb_elev.pt(rx_pl - 0.5 * pile_d_mm, -pile_stub_mm), dxfattribs={"layer": "CONCRETE"})
            msp.add_line(vb_elev.pt(rx_pl + 0.5 * pile_d_mm, 0.0), vb_elev.pt(rx_pl + 0.5 * pile_d_mm, -pile_stub_mm), dxfattribs={"layer": "CONCRETE"})
            draw_pile_s_break(msp, vb_elev.x(rx_pl), vb_elev.y(-pile_stub_mm), vb_elev.span(pile_d_mm))
            for rx_qp in (-0.36 * pile_d_mm, 0.36 * pile_d_mm):
                msp.add_line(vb_elev.pt(rx_pl + rx_qp, -pile_stub_mm + 200.0), vb_elev.pt(rx_pl + rx_qp, 850.0), dxfattribs={"layer": "REBAR"})
            for ry_ph in (-600.0, -1350.0, -2100.0):
                msp.add_line(vb_elev.pt(rx_pl - 0.42 * pile_d_mm, ry_ph), vb_elev.pt(rx_pl + 0.42 * pile_d_mm, ry_ph), dxfattribs={"layer": "REBAR_STIRRUP"})

        x_aa_elev = vb_elev.x(aa_target_rx)
        draw_section_cut_symbol(msp, x_aa_elev, vb_elev.y(0.0) - 2.0, x_aa_elev, vb_elev.y(abut_cap_h + bw_h) + 2.0, "A", look_dx=1.0, look_dy=0.0, tick_len=2.8, arrow_len=2.8)
        y_bb_ab = vb_elev.y(-1450.0)
        draw_section_cut_symbol(msp, vb_elev.x(h_psp - 0.65 * pile_d_mm), y_bb_ab, vb_elev.x(h_psp + 0.65 * pile_d_mm), y_bb_ab, "B", look_dx=0.0, look_dy=-1.0, tick_len=2.8, arrow_len=2.8)
        abut_pile_len_m_18 = float(((calc.get("abutment_checks") or {}).get("bored_pile") or {}).get("design_length_m", 15.0))
        add_arch_dim(msp, vb_elev.pt(-h_psp - 0.5 * pile_d_mm, -pile_stub_mm), vb_elev.pt(-h_psp + 0.5 * pile_d_mm, -pile_stub_mm), (vb_elev.x(-h_psp), vb_elev.y(-pile_stub_mm) - 10.2), f"桩径 {pile_d_mm:g}")
        add_civil_text(msp, f"桥台设计桩长 L={abut_pile_len_m_18:g}m", vb_elev.x(h_psp), vb_elev.y(-pile_stub_mm) - 10.2, height=1.72, align="CENTER")
        add_arch_dim(msp, vb_elev.pt(-hcl, 0.0), vb_elev.pt(-hcl, abut_cap_h), (vb_elev.x(-hcl) - 7.5, vb_elev.y(0.5 * abut_cap_h)), f"{abut_cap_h:g}", angle=90.0)
        add_civil_text(msp, "桥台正立面配筋图 (1:80)", vb_elev.x(0.0), 162.5, height=2.2, align="CENTER")
        vb_elev.register(doc, msp, paper_bounds=(196.0, 160.0, 332.0, 268.0))

        # 1:60 Side View with Detail I callout ring
        vb_ab = ViewBox("abutment_side_rebar", 374.0, 212.0, 60.0, "mm")
        msp.add_lwpolyline([vb_ab.pt(-hcw, 0.0), vb_ab.pt(hcw, 0.0), vb_ab.pt(hcw, abut_cap_h), vb_ab.pt(-hcw, abut_cap_h)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline(
            [
                vb_ab.pt(-hcw, abut_cap_h),
                vb_ab.pt(-hcw + bw_thk, abut_cap_h),
                vb_ab.pt(-hcw + bw_thk, abut_cap_h + bw_h),
                vb_ab.pt(-hcw, abut_cap_h + bw_h),
                vb_ab.pt(-hcw, abut_cap_h + bw_h - 300.0),
                vb_ab.pt(-hcw - 300.0, abut_cap_h + bw_h - 300.0),
                vb_ab.pt(-hcw - 300.0, abut_cap_h + bw_h - 650.0),
                vb_ab.pt(-hcw, abut_cap_h + bw_h - 850.0),
            ],
            close=True,
            dxfattribs={"layer": "CONCRETE"},
        )
        # Earwall rear projection dashed outline + bearing pedestal
        msp.add_lwpolyline(
            [vb_ab.pt(-hcw - 900.0, abut_cap_h + 250.0), vb_ab.pt(-hcw, abut_cap_h), vb_ab.pt(-hcw, abut_cap_h + bw_h), vb_ab.pt(-hcw - 900.0, abut_cap_h + bw_h)],
            close=True,
            dxfattribs={"layer": "HIDDEN"},
        )
        msp.add_lwpolyline([vb_ab.pt(80.0, abut_cap_h), vb_ab.pt(600.0, abut_cap_h), vb_ab.pt(600.0, abut_cap_h + 150.0), vb_ab.pt(80.0, abut_cap_h + 150.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline(
            [vb_ab.pt(-hcw + 65.0, abut_cap_h - anch_qt2_mm), vb_ab.pt(-hcw + 65.0, abut_cap_h + bw_h - 65.0), vb_ab.pt(-hcw + bw_thk - 65.0, abut_cap_h + bw_h - 65.0), vb_ab.pt(-hcw + bw_thk - 65.0, abut_cap_h - anch_qt2_mm)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        side_pile_mm = 2200.0
        msp.add_line(vb_ab.pt(-0.5 * pile_d_mm, 0.0), vb_ab.pt(-0.5 * pile_d_mm, -side_pile_mm), dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb_ab.pt(0.5 * pile_d_mm, 0.0), vb_ab.pt(0.5 * pile_d_mm, -side_pile_mm), dxfattribs={"layer": "CONCRETE"})
        draw_pile_s_break(msp, vb_ab.x(0.0), vb_ab.y(-side_pile_mm), vb_ab.span(pile_d_mm))
        draw_detail_callout(
            msp,
            vb_ab.x(-hcw + 0.5 * bw_thk),
            vb_ab.y(abut_cap_h),
            4.8,
            "I 详图(1:20)",
            leader_to=(vb_ab.x(hcw) - 5.0, vb_ab.y(abut_cap_h + bw_h) + 4.5),
        )
        add_arch_dim(msp, vb_ab.pt(hcw, abut_cap_h), vb_ab.pt(hcw, abut_cap_h + bw_h), (vb_ab.x(hcw) + 6.5, vb_ab.y(abut_cap_h + 0.5 * bw_h)), f"{bw_h:g}", angle=90.0)
        add_civil_text(msp, "侧面图 (1:60)", vb_ab.x(0.0), 166.0, height=2.2, align="CENTER")
        vb_ab.register(doc, msp, paper_bounds=(340.0, 164.0, 408.0, 266.0))

        # 3a) Lower-Left: 1:40 A-A Section through 2# Pedestal Center (cuts Cap 1800x1400 + Backwall 550x1750 + 2# Pedestal 520x150!)
        vb_ac = ViewBox("abutment_cap_section", 58.0, 76.0, 40.0, "mm")
        # Cap body [-hcw .. +hcw] x [0 .. 1400] + closed stirrup
        msp.add_lwpolyline([vb_ac.pt(-hcw, 0.0), vb_ac.pt(hcw, 0.0), vb_ac.pt(hcw, abut_cap_h), vb_ac.pt(-hcw, abut_cap_h)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_ac.pt(-hcw + 55.0, 55.0), vb_ac.pt(hcw - 55.0, 55.0), vb_ac.pt(hcw - 55.0, abut_cap_h - 55.0), vb_ac.pt(-hcw + 55.0, abut_cap_h - 55.0)], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
        # Full-width Backwall + Corbel cut by A-A on left/rear [-hcw .. -hcw + bw_thk] x [1400 .. 3150]
        msp.add_lwpolyline(
            [
                vb_ac.pt(-hcw, abut_cap_h),
                vb_ac.pt(-hcw, abut_cap_h + bw_h - 850.0),
                vb_ac.pt(-hcw - 300.0, abut_cap_h + bw_h - 650.0),
                vb_ac.pt(-hcw - 300.0, abut_cap_h + bw_h - 300.0),
                vb_ac.pt(-hcw, abut_cap_h + bw_h - 300.0),
                vb_ac.pt(-hcw, abut_cap_h + bw_h),
                vb_ac.pt(-hcw + bw_thk, abut_cap_h + bw_h),
                vb_ac.pt(-hcw + bw_thk, abut_cap_h),
            ],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        # QT2 vertical backwall bars cut in A-A, anchored 560mm into cap with inward hooks!
        msp.add_lwpolyline(
            [vb_ac.pt(-hcw + 60.0, abut_cap_h + bw_h - 65.0), vb_ac.pt(-hcw + 60.0, abut_cap_h - anch_qt2_mm), vb_ac.pt(-hcw + 220.0, abut_cap_h - anch_qt2_mm)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        msp.add_lwpolyline(
            [vb_ac.pt(-hcw + bw_thk - 60.0, abut_cap_h + bw_h - 65.0), vb_ac.pt(-hcw + bw_thk - 60.0, abut_cap_h - anch_qt2_mm), vb_ac.pt(-hcw + bw_thk - 220.0, abut_cap_h - anch_qt2_mm)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        # 2# Bearing Pedestal (520 x 150) cut on right/span side [80 .. 600] x [1400 .. 1550] + DS1 2-layer mesh
        msp.add_lwpolyline([vb_ac.pt(80.0, abut_cap_h), vb_ac.pt(600.0, abut_cap_h), vb_ac.pt(600.0, abut_cap_h + 150.0), vb_ac.pt(80.0, abut_cap_h + 150.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_line(vb_ac.pt(115.0, abut_cap_h + 55.0), vb_ac.pt(565.0, abut_cap_h + 55.0), dxfattribs={"layer": "REBAR_STIRRUP"})
        msp.add_line(vb_ac.pt(115.0, abut_cap_h + 105.0), vb_ac.pt(565.0, abut_cap_h + 105.0), dxfattribs={"layer": "REBAR_STIRRUP"})

        qt1_cnt = int(bars_by_id["QT1"]["count"])
        qt1_top = qt1_cnt // 2
        qt1_pts = _draw_row_bar_circles(msp, vb_ac, qt1_top, -hcw + 85.0, hcw - 85.0, abut_cap_h - 80.0, dot_r_mm=0.82)
        qt1_pts += _draw_row_bar_circles(msp, vb_ac, qt1_cnt - qt1_top, -hcw + 85.0, hcw - 85.0, 80.0, dot_r_mm=0.82)
        add_civil_text(msp, f"QT1 {qt1_cnt}Φ{bars_by_id['QT1']['dia_mm']:g}(上下各{qt1_top})", vb_ac.x(180.0), vb_ac.y(0.46 * abut_cap_h), height=1.70, align="CENTER")
        add_civil_text(msp, "背墙", vb_ac.x(-hcw + 0.5 * bw_thk), vb_ac.y(abut_cap_h + 0.55 * bw_h), height=1.68, align="CENTER")
        add_civil_text(msp, "2#垫石+DS1网", vb_ac.x(340.0), vb_ac.y(abut_cap_h + 150.0) + 4.5, height=1.68, align="CENTER")
        add_arch_dim(msp, vb_ac.pt(-hcw, 0.0), vb_ac.pt(hcw, 0.0), (vb_ac.x(0.0), vb_ac.y(0.0) - 5.5), f"台帽宽 {abut_cap_w:g}")
        add_arch_dim(msp, vb_ac.pt(-hcw, abut_cap_h + bw_h), vb_ac.pt(-hcw + bw_thk, abut_cap_h + bw_h), (vb_ac.x(-hcw + 0.5 * bw_thk), vb_ac.y(abut_cap_h + bw_h) + 4.8), f"{bw_thk:g}")
        add_arch_dim(msp, vb_ac.pt(-hcw, abut_cap_h), vb_ac.pt(-hcw, abut_cap_h + bw_h), (vb_ac.x(-hcw - 300.0) - 4.2, vb_ac.y(abut_cap_h + 0.5 * bw_h)), f"{bw_h:g}", angle=90.0)
        add_civil_text(msp, "A-A 台帽、背墙及2#垫石剖面 (1:40)", vb_ac.x(0.0), 62.5, height=2.05, align="CENTER")
        vb_ac.register(doc, msp, paper_bounds=(20.0, 60.0, 94.0, 166.0))

        # 3b) Lower-Center: 1:20 Detail I — Earwall/Backwall to Cap Connection & Anchorage Detail!
        vb_jnt = ViewBox("abutment_joint_detail", 125.0, 106.0, 20.0, "mm")
        # Concrete outline: cap upper portion [x=0..640, y=-700..0] + backwall [x=0..550, y=0..820] + corbel [-280..0]
        msp.add_lwpolyline(
            [
                vb_jnt.pt(0.0, -700.0),
                vb_jnt.pt(0.0, 260.0),
                vb_jnt.pt(-280.0, 440.0),
                vb_jnt.pt(-280.0, 740.0),
                vb_jnt.pt(0.0, 740.0),
                vb_jnt.pt(0.0, 820.0),
                vb_jnt.pt(bw_thk, 820.0),
                vb_jnt.pt(bw_thk, 0.0),
                vb_jnt.pt(660.0, 0.0),
            ],
            close=False,
            dxfattribs={"layer": "CONCRETE"},
        )
        msp.add_line(vb_jnt.pt(0.0, 0.0), vb_jnt.pt(bw_thk, 0.0), dxfattribs={"layer": "HIDDEN"})  # horizontal construction joint
        msp.add_line(vb_jnt.pt(-30.0, -700.0), vb_jnt.pt(660.0, -700.0), dxfattribs={"layer": "THIN"})
        msp.add_line(vb_jnt.pt(660.0, -700.0), vb_jnt.pt(660.0, 30.0), dxfattribs={"layer": "THIN"})
        # Cap top longitudinal bars (QT1) + cap stirrup top leg
        msp.add_line(vb_jnt.pt(55.0, -75.0), vb_jnt.pt(640.0, -75.0), dxfattribs={"layer": "REBAR"})
        # QT2 Φ16@125 along BOTH rear (台背侧 x=55) and front (跨径侧 x=bw_thk-55) wall faces,
        # anchored 560mm into cap with 90° hooks turned INWARD!
        msp.add_lwpolyline(
            [vb_jnt.pt(55.0, 800.0), vb_jnt.pt(55.0, -anch_qt2_mm), vb_jnt.pt(235.0, -anch_qt2_mm)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        msp.add_lwpolyline(
            [vb_jnt.pt(bw_thk - 55.0, 800.0), vb_jnt.pt(bw_thk - 55.0, -anch_qt2_mm), vb_jnt.pt(bw_thk - 235.0, -anch_qt2_mm)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
        # Corbel reinforcement loop + horizontal wall distribution dots (on REBAR_STIRRUP so section counts stay exact!)
        msp.add_lwpolyline(
            [vb_jnt.pt(120.0, 320.0), vb_jnt.pt(-230.0, 470.0), vb_jnt.pt(-230.0, 690.0), vb_jnt.pt(220.0, 690.0)],
            close=False,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        for ry_dist in (150.0, 350.0, 550.0, 750.0):
            msp.add_circle(vb_jnt.pt(85.0, ry_dist), 0.62, dxfattribs={"layer": "REBAR_STIRRUP"})
            msp.add_circle(vb_jnt.pt(bw_thk - 85.0, ry_dist), 0.62, dxfattribs={"layer": "REBAR_STIRRUP"})

        add_arch_dim(msp, vb_jnt.pt(0.0, 820.0), vb_jnt.pt(bw_thk, 820.0), (vb_jnt.x(0.5 * bw_thk), vb_jnt.y(820.0) + 5.0), f"墙厚 {bw_thk:g}")
        add_arch_dim(msp, vb_jnt.pt(bw_thk, -anch_qt2_mm), vb_jnt.pt(bw_thk, 0.0), (vb_jnt.x(660.0) + 5.5, vb_jnt.y(-0.5 * anch_qt2_mm)), f"锚固 {anch_qt2_mm:g}", angle=90.0)
        add_civil_text(msp, "QT2 Φ16@125(双侧)", vb_jnt.x(0.5 * bw_thk), vb_jnt.y(220.0), height=1.78, align="CENTER")
        add_civil_text(msp, "弯钩朝内,la=560=35d(C30)", vb_jnt.x(0.5 * bw_thk), vb_jnt.y(-anch_qt2_mm - 110.0), height=1.68, align="CENTER")
        add_civil_text(msp, "I 耳背墙与台帽连接详图 (1:20)", vb_jnt.x(220.0), 62.5, height=2.05, align="CENTER")
        vb_jnt.register(doc, msp, paper_bounds=(98.0, 60.0, 168.0, 158.0))

        # 3c) Lower-Mid-Right: 1:30 B-B Abutment Bored Pile Cross-Section (exact QT3 count = 20 + QT4 spiral hoop!)
        vb_ap = ViewBox("abutment_pile_section", 199.0, 104.0, 30.0, "mm")
        msp.add_circle(vb_ap.pt(0.0, 0.0), vb_ap.span(0.5 * pile_d_mm), dxfattribs={"layer": "CONCRETE"})
        _draw_hoop_polygon(msp, vb_ap, 0.5 * pile_d_mm - 65.0)
        qt3_cnt = int(bars_by_id["QT3"]["count"])
        qt4_cnt = int(bars_by_id["QT4"]["count"]) if "QT4" in bars_by_id else 127
        qt3_pts = _draw_perimeter_bar_circles(msp, vb_ap, qt3_cnt, 0.5 * pile_d_mm - 88.0, dot_r_mm=0.90)
        add_civil_text(msp, f"QT3 {qt3_cnt}Φ{bars_by_id['QT3']['dia_mm']:g}(环布)", vb_ap.x(0.0), vb_ap.y(110.0), height=1.78, align="CENTER")
        add_civil_text(msp, f"QT4 φ12@150({qt4_cnt}圈/桩)", vb_ap.x(0.0), vb_ap.y(-140.0), height=1.68, align="CENTER")
        add_arch_dim(msp, vb_ap.pt(-0.5 * pile_d_mm, 0.0), vb_ap.pt(0.5 * pile_d_mm, 0.0), (vb_ap.x(0.0), vb_ap.y(0.5 * pile_d_mm) + 6.5), f"桩径 {pile_d_mm:g}")
        add_civil_text(msp, "B-B 桩身配筋断面 (1:30)", vb_ap.x(0.0), 62.5, height=2.05, align="CENTER")
        vb_ap.register(doc, msp, paper_bounds=(172.0, 60.0, 226.0, 150.0))

        anchor_by_id = {
            "QT1": qt1_pts[-1] if qt1_pts else vb_ac.pt(600.0, 80.0),
            "QT2": vb_jnt.pt(55.0, 560.0),
            "QT3": qt3_pts[3] if len(qt3_pts) > 3 else vb_ap.pt(0.0, 560.0),
            "QT4": vb_elev.pt(-h_psp - 0.42 * pile_d_mm, -1350.0),
            "DS1": vb_ac.pt(420.0, abut_cap_h + 105.0),
        }

    # Draw boxed bar ID tags right alongside each bar's actual zone (zero crossing leaders!)
    custom_tag_pos: dict[str, tuple[float, float]] = {}
    if comp == "abutment_rebar":
        custom_tag_pos = {
            "QT1": (88.0, 74.0),
            "DS1": (86.0, 130.0),
            "QT2": (106.0, 145.0),
            "QT3": (218.0, 140.0),
            "QT4": (208.0, 194.0),
        }
    elif comp == "pier_column_pile":
        custom_tag_pos = {
            "DZ1": (214.0, 238.0),
            "DZ2": (34.0, 210.0),
            "XL1": (344.0, 231.0),
            "XL2": (396.0, 231.0),
            "ZJ1": (214.0, 136.0),
            "ZJ2": (34.0, 120.0),
        }
    elif comp == "pier_cap":
        custom_tag_pos = {
            "GL1": (182.0, 118.0),
            "GL2": (182.0, 72.0),
            "GL3": (194.0, 222.0),
            "GL4": (34.0, 168.0),
        }

    for idx, b_item in enumerate(bars):
        bid = str(b_item["bar_id"])
        ax, ay = anchor_by_id.get(bid, (100.0, 140.0))
        if bid in custom_tag_pos:
            tx, ty = custom_tag_pos[bid]
        elif comp == "t_girder_web":
            tx = 40.0 if ax < 112.0 else 186.0
            ty = max(66.0, min(228.0, ay + 6.0))
        else:
            tx = 34.0 if ax < 112.0 else 192.0
            ty = max(66.0, min(236.0, ay + (10.0 if idx % 2 == 0 else -10.0)))
        draw_boxed_tag(msp, tx, ty, bid, leader_to=(ax, ay), layer="REBAR")

    def _safe_basis_str(raw_basis: str, max_chars: int = 92) -> str:
        s_b = str(raw_basis).strip()
        if len(s_b) <= max_chars:
            return s_b
        for sep in ("；", ";", "，", ","):
            pos = s_b.rfind(sep, 0, max_chars)
            if pos >= 24:
                return s_b[:pos]
        return s_b[:max_chars].rstrip("([（ ")

    if comp in {"pier_column_pile", "abutment_rebar"}:
        if comp == "pier_column_pile":
            scope_note = "统计口径说明：单件根数/单件重按单根墩柱、单道系梁、单根钻孔桩计；每墩重按整座双柱墩(2柱+1系梁+2桩)汇总。"
        else:
            ab_len_note = float(((calc.get("abutment_checks") or {}).get("bored_pile") or {}).get("design_length_m", 15.0))
            scope_note = f"统计口径说明：单件根数按单座台帽、单座耳背墙、单根桩、单个垫石计；每台重按整座桥台汇总（独立求得桥台桩长 {ab_len_note:g}m）。"
        add_civil_text(msp, scope_note, b["ix0"] + 6.0, b["iy0"] + 34.5, height=1.72)
        n_note_rows = min(5, len(bars))
        for idx, b_item in enumerate(bars[:n_note_rows]):
            add_civil_text(
                msp,
                f"[{b_item['bar_id']}] {_safe_basis_str(str(b_item['calc_basis']), 78)}",
                b["ix0"] + 6.0,
                b["iy0"] + 29.0 - idx * 5.0,
                height=1.70,
            )
    else:
        add_civil_text(msp, f"主控验算：{_safe_basis_str(str(bars[0]['calc_basis']), 96)}", b["ix0"] + 6.0, b["iy0"] + 16.0, height=1.90)
    return doc


# ======================================================================
# 7. Bearing Detail & Layout (QL-12, Cat 6) & Deck Ancillary (QL-13, Cat 7)
# ======================================================================
def _draw_bearing_layout(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    calc = _ensure_calc_report(spec)
    brg = calc["bearing_checks"]
    sup_lay = calc.get("support_layout") or {}
    parts = brg["bearing_model"].replace("GJZ", "").strip().split("×")
    bw_mm = float(parts[0]) if len(parts) == 3 else 300.0
    bl_mm = float(parts[1]) if len(parts) == 3 else 400.0
    bt_mm = float(parts[2]) if len(parts) == 3 else 74.0
    ped_sp_mm = float(calc["lateral_distribution"]["girder_spacing_m"]) * 1000.0
    n_ped = int(calc["lateral_distribution"]["n_girders"])
    ped_geom = sup_lay.get("pedestal_geometry") or {}
    ped_w = float(ped_geom.get("transverse_width_mm", 520.0))
    ped_l = float(ped_geom.get("longitudinal_length_mm", 600.0))
    ped_h = float(ped_geom.get("height_mm", 150.0))
    ped_tot_m3 = float(ped_geom.get("total_volume_m3", 1.87))
    wedge_mm = float((sup_lay.get("vertical_stack_mm") or {}).get("wedge_steel_plate_mm", 20.0))
    vert_tot_mm = round(wedge_mm + bt_mm + ped_h, 1)
    d_end_mm = float(sup_lay.get("beam_end_to_bearing_mm", 480.0))
    gap_mm = float(sup_lay.get("expansion_gap_mm", 40.0))
    row_pitch_mm = float(sup_lay.get("pier_bearing_row_pitch_mm", 2.0 * d_end_mm + gap_mm))

    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "板式橡胶支座及支座垫石构造布置图")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-12")),
        unit_note="mm",
        scale_label="1:5 / 1:10 / 1:25",
        style_overrides=_resolve_style_overrides(spec),
    )

    # 1. Upper-Left: 1:5 Laminated Elastomeric Bearing Section & Plan (exact 1:5 ViewBox)
    vb = ViewBox("bearing_section", 112.0, 166.0, 5.0, "mm")
    hl = 0.5 * bl_mm
    msp.add_lwpolyline([vb.pt(-hl, 0.0), vb.pt(hl, 0.0), vb.pt(hl, bt_mm), vb.pt(-hl, bt_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    for k in range(1, 6):
        ry_plate = bt_mm * (k / 6.0)
        msp.add_line(vb.pt(-hl + 5.0, ry_plate), vb.pt(hl - 5.0, ry_plate), dxfattribs={"layer": "REBAR"})
    add_arch_dim(msp, vb.pt(-hl, bt_mm), vb.pt(hl, bt_mm), (vb.x(0.0), vb.y(bt_mm) + 7.5), f"{bl_mm:g}")
    add_arch_dim(msp, vb.pt(hl, 0.0), vb.pt(hl, bt_mm), (vb.x(hl) + 8.5, vb.y(0.5 * bt_mm)), f"{bt_mm:g}", angle=90.0)
    ry_plan0 = bt_mm + 65.0
    msp.add_lwpolyline([vb.pt(-hl, ry_plan0), vb.pt(hl, ry_plan0), vb.pt(hl, ry_plan0 + bw_mm), vb.pt(-hl, ry_plan0 + bw_mm)], close=True, dxfattribs={"layer": "OUTLINE"})
    add_arch_dim(msp, vb.pt(hl, ry_plan0), vb.pt(hl, ry_plan0 + bw_mm), (vb.x(hl) + 8.5, vb.y(ry_plan0 + 0.5 * bw_mm)), f"{bw_mm:g}", angle=90.0)
    add_civil_text(msp, f"一、{brg['bearing_model']} 支座剖面与平面详图 (1:5)", vb.x(0.0), 156.0, height=2.3, align="CENTER")
    vb.register(doc, msp, paper_bounds=(44.0, 154.0, 192.0, 266.0))

    # 2. Lower-Left: 1:10 Bearing Installation & C40 Pedestal Detail (520×600×150, 20+74+150=244mm)
    vb_ped = ViewBox("bearing_pedestal_detail", 112.0, 76.0, 10.0, "mm")
    msp.add_lwpolyline([vb_ped.pt(-420.0, -130.0), vb_ped.pt(420.0, -130.0), vb_ped.pt(420.0, 0.0), vb_ped.pt(-420.0, 0.0)], close=False, dxfattribs={"layer": "CONCRETE"})
    msp.add_line(vb_ped.pt(-420.0, 0.0), vb_ped.pt(420.0, 0.0), dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_ped.pt(-0.5 * ped_l, 0.0), vb_ped.pt(0.5 * ped_l, 0.0), vb_ped.pt(0.5 * ped_l, ped_h), vb_ped.pt(-0.5 * ped_l, ped_h)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_line(vb_ped.pt(-0.5 * ped_l + 40.0, 55.0), vb_ped.pt(0.5 * ped_l - 40.0, 55.0), dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_line(vb_ped.pt(-0.5 * ped_l + 40.0, 105.0), vb_ped.pt(0.5 * ped_l - 40.0, 105.0), dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_lwpolyline([vb_ped.pt(-hl, ped_h), vb_ped.pt(hl, ped_h), vb_ped.pt(hl, ped_h + bt_mm), vb_ped.pt(-hl, ped_h + bt_mm)], close=True, dxfattribs={"layer": "REBAR"})
    y_g_bot = ped_h + bt_mm + wedge_mm
    msp.add_lwpolyline([vb_ped.pt(-hl - 20.0, ped_h + bt_mm), vb_ped.pt(hl + 20.0, ped_h + bt_mm), vb_ped.pt(hl + 20.0, y_g_bot), vb_ped.pt(-hl - 20.0, y_g_bot)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_lwpolyline([vb_ped.pt(-280.0, y_g_bot + 160.0), vb_ped.pt(-280.0, y_g_bot), vb_ped.pt(280.0, y_g_bot), vb_ped.pt(280.0, y_g_bot + 160.0)], close=False, dxfattribs={"layer": "CONCRETE"})
    add_arch_dim(msp, vb_ped.pt(-0.5 * ped_l, 0.0), vb_ped.pt(0.5 * ped_l, 0.0), (vb_ped.x(0.0), vb_ped.y(0.0) - 6.0), f"C40垫石 {ped_w:g}×{ped_l:g}(全桥40块={ped_tot_m3:.2f}m3)")
    add_arch_dim(msp, vb_ped.pt(0.5 * ped_l, 0.0), vb_ped.pt(0.5 * ped_l, ped_h), (vb_ped.x(420.0) + 5.5, vb_ped.y(0.5 * ped_h)), f"{ped_h:g}", angle=90.0)
    add_arch_dim(msp, vb_ped.pt(0.5 * ped_l, 0.0), vb_ped.pt(0.5 * ped_l, y_g_bot), (vb_ped.x(420.0) + 12.5, vb_ped.y(0.5 * y_g_bot)), f"总高 {vert_tot_mm:g}({wedge_mm:g}+{bt_mm:g}+{ped_h:g})", angle=90.0)
    add_civil_text(msp, "梁底楔形调平钢板 + DS1防劈裂钢筋网", vb_ped.x(0.0), vb_ped.y(y_g_bot + 160.0) + 3.5, height=1.82, align="CENTER")
    add_civil_text(msp, "二、支座安装及支座垫石构造剖面 (1:10)", vb_ped.x(0.0), 48.0, height=2.3, align="CENTER")
    vb_ped.register(doc, msp, paper_bounds=(44.0, 46.0, 198.0, 138.0))

    # 3. Upper-Right: Single-span bearing layout & legend
    bx0, by0, bw, bh = 232.0, 146.0, 164.0, 102.0
    msp.add_lwpolyline([(bx0, by0), (bx0 + bw, by0), (bx0 + bw, by0 + bh), (bx0, by0 + bh)], close=True, dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "三、单跨支座平面布置示意（全桥共40块）", bx0 + 0.5 * bw, by0 + bh + 4.5, height=2.2, align="CENTER")
    x_z1, x_z2 = bx0 + 46.0, bx0 + bw - 28.0
    msp.add_line((x_z1, by0 + 10.0), (x_z1, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    msp.add_line((x_z2, by0 + 10.0), (x_z2, by0 + bh - 12.0), dxfattribs={"layer": "CENTER"})
    add_civil_text(msp, "固定端(桥墩)", x_z1, by0 + bh - 9.0, height=1.7, align="CENTER")
    add_civil_text(msp, "纵向活动端", x_z2, by0 + bh - 9.0, height=1.7, align="CENTER")
    for i in range(n_ped):
        yg = by0 + 16.0 + i * ((bh - 34.0) / max(1, n_ped - 1))
        msp.add_line((x_z1 - 8.0, yg), (x_z2 + 8.0, yg), dxfattribs={"layer": "THIN"})
        draw_bearing_symbol(msp, x_z1, yg, kind="fixed", size=3.0)
        draw_bearing_symbol(msp, x_z2, yg, kind="sliding_longitudinal", size=3.0)
        add_civil_text(msp, f"{i+1}# ({ped_sp_mm:g}间距)", bx0 + 5.0, yg - 1.0, height=1.75)

    # 4. Lower-Right (above Title Block): 1:25 Shared Pier-Top Longitudinal Support Chain (480 + 40 + 480 = 1000 mm)
    vb_chn = ViewBox("pier_bearing_chain", 314.0, 96.0, 25.0, "mm")
    gh_13 = 0.5 * gap_mm
    bx_13 = 0.5 * row_pitch_mm
    msp.add_lwpolyline([vb_chn.pt(-800.0, -244.0), vb_chn.pt(800.0, -244.0), vb_chn.pt(800.0, -94.0), vb_chn.pt(-800.0, -94.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    for sx_13 in (-1.0, 1.0):
        msp.add_lwpolyline([vb_chn.pt(sx_13 * gh_13, 0.0), vb_chn.pt(sx_13 * (bx_13 + 300.0), 0.0), vb_chn.pt(sx_13 * (bx_13 + 300.0), 320.0), vb_chn.pt(sx_13 * gh_13, 320.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        draw_bearing_symbol(msp, vb_chn.x(sx_13 * bx_13), vb_chn.y(0.0) - 1.6, kind="fixed" if sx_13 < 0 else "sliding_longitudinal", size=2.4)
        msp.add_line((vb_chn.x(sx_13 * bx_13), vb_chn.y(-270.0)), (vb_chn.x(sx_13 * bx_13), vb_chn.y(360.0)), dxfattribs={"layer": "CENTER"})
    add_arch_dim(msp, vb_chn.pt(-bx_13, 320.0), vb_chn.pt(-gh_13, 320.0), (vb_chn.x(-0.5 * (bx_13 + gh_13)), vb_chn.y(320.0) + 5.5), f"{d_end_mm:g}")
    add_arch_dim(msp, vb_chn.pt(-gh_13, 320.0), vb_chn.pt(gh_13, 320.0), (vb_chn.x(0.0), vb_chn.y(320.0) + 5.5), f"{gap_mm:g}")
    add_arch_dim(msp, vb_chn.pt(gh_13, 320.0), vb_chn.pt(bx_13, 320.0), (vb_chn.x(0.5 * (bx_13 + gh_13)), vb_chn.y(320.0) + 5.5), f"{d_end_mm:g}")
    add_arch_dim(msp, vb_chn.pt(-bx_13, -244.0), vb_chn.pt(bx_13, -244.0), (vb_chn.x(0.0), vb_chn.y(-244.0) - 5.5), f"双排支座纵距 {row_pitch_mm:g}")
    add_civil_text(msp, f"四、中墩双排支座顺桥向定位链 (1:25)：{d_end_mm:g}+{gap_mm:g}+{d_end_mm:g}={row_pitch_mm:g}mm", vb_chn.x(0.0), 74.0, height=1.88, align="CENTER")
    vb_chn.register(doc, msp, paper_bounds=(230.0, 72.0, 398.0, 134.0))

    sigma_c = float(brg["compressive_stress_mpa"])
    allow_c = float(brg["allowable_stress_mpa"])
    tan_g = float(brg["shear_strain_tan_gamma"])
    allow_g = float(brg["allowable_tan_gamma"])
    sigma_min = float(brg["min_contact_stress_mpa"])
    allow_min = float(brg.get("min_allowable_stress_mpa", 1.5))
    op_c = "≤" if sigma_c <= allow_c + 1e-6 else ">"
    op_g = "≤" if tan_g <= allow_g + 1e-6 else ">"
    op_m = "≥" if sigma_min >= allow_min - 1e-6 else "<"
    uplift_txt = "抗脱空合格" if brg["anti_uplift_passed"] else "最小反力抗脱空不满足"
    verdict_txt = "合格" if brg["passed"] else "超限不合格"
    add_civil_text(
        msp,
        f"支座验算结论({verdict_txt})：σc={sigma_c:.2f}MPa {op_c} {allow_c:.1f}MPa，tanγ={tan_g:.3f} {op_g} {allow_g:.2f}，σmin={sigma_min:.2f}MPa {op_m} {allow_min:.1f}MPa，{uplift_txt}",
        b["ix0"] + 6.0,
        b["iy0"] + 16.0,
        height=2.05,
    )
    attach_sheet_metadata(doc, msp, "bearing_check_summary", {**dict(brg), "support_layout": dict(sup_lay)})
    return doc


def _draw_deck_ancillary(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    calc = _ensure_calc_report(spec)
    bars = calc["rebar_and_tendon_ledger"]["rebar_groups"]["deck_ancillary_rebar"]
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", "桥面附属结构构造图（护栏、桥面连续、D80伸缩缝、泄水管及桥头搭板）")),
        project=str(spec.get("project", calc["project_summary"]["project_name"])),
        drawing_no=str(spec.get("drawing_no", "QL-13")),
        unit_note="mm",
        scale_label="1:10 / 1:15 / 1:40 / 1:60",
        style_overrides=_resolve_style_overrides(spec),
    )

    # 1. Top-Left: 1:15 Concrete Crash Barrier Section + Explicit Total Height (1000) vs Above-Paving Clear Height (820)!
    vb_bar = ViewBox("crash_barrier_detail", 36.0, 180.0, 15.0, "mm")
    b_pts = [(0.0, 0.0), (500.0, 0.0), (500.0, 250.0), (350.0, 450.0), (320.0, 1000.0), (0.0, 1000.0)]
    msp.add_lwpolyline([vb_bar.pt(x, y) for x, y in b_pts], close=True, dxfattribs={"layer": "CONCRETE"})
    # T-girder cantilever flange [-180..0] + adjacent 80 C40 leveling + 100 asphalt [500..740] (finished roadway at y = +180mm!)
    msp.add_lwpolyline([vb_bar.pt(0.0, -180.0), vb_bar.pt(740.0, -180.0), vb_bar.pt(740.0, 0.0), vb_bar.pt(0.0, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_bar.pt(500.0, 0.0), vb_bar.pt(740.0, 0.0), vb_bar.pt(740.0, 180.0), vb_bar.pt(500.0, 180.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_line(vb_bar.pt(500.0, 80.0), vb_bar.pt(740.0, 80.0), dxfattribs={"layer": "HIDDEN"})
    # HL1 vertical hairpin anchored 140mm into flange with horizontal hook
    msp.add_lwpolyline(
        [vb_bar.pt(140.0, -135.0), vb_bar.pt(45.0, -135.0), vb_bar.pt(45.0, 955.0), vb_bar.pt(280.0, 955.0), vb_bar.pt(310.0, 440.0), vb_bar.pt(455.0, 240.0), vb_bar.pt(455.0, -135.0), vb_bar.pt(360.0, -135.0)],
        close=False,
        dxfattribs={"layer": "REBAR"},
    )
    draw_boxed_tag(msp, vb_bar.x(480.0), vb_bar.y(860.0), "HL1", leader_to=vb_bar.pt(295.0, 740.0), layer="REBAR")
    add_civil_text(msp, "路面完成面", vb_bar.x(508.0), vb_bar.y(205.0), height=1.55, align="LEFT")
    add_civil_text(msp, "100沥青+80调平", vb_bar.x(508.0), vb_bar.y(102.0), height=1.45, align="LEFT")
    add_civil_text(msp, "HL1锚入翼缘140", vb_bar.x(508.0), vb_bar.y(-110.0), height=1.55, align="LEFT")
    add_arch_dim(msp, vb_bar.pt(0.0, 0.0), vb_bar.pt(500.0, 0.0), (vb_bar.x(250.0), vb_bar.y(-180.0) - 5.2), "500")
    # Explicitly distinguish total precast/cast height (1000 from flange top) vs buried paving (180) + exposed clear height above roadway (820)!
    add_arch_dim(msp, vb_bar.pt(0.0, 0.0), vb_bar.pt(0.0, 1000.0), (vb_bar.x(0.0) - 8.2, vb_bar.y(500.0)), "构件总高 1000", angle=90.0)
    add_arch_dim(msp, vb_bar.pt(740.0, 0.0), vb_bar.pt(740.0, 180.0), (vb_bar.x(740.0) + 7.5, vb_bar.y(90.0)), "铺装 180", angle=90.0)
    add_arch_dim(msp, vb_bar.pt(320.0, 180.0), vb_bar.pt(320.0, 1000.0), (vb_bar.x(740.0) + 7.5, vb_bar.y(590.0)), "外露净高 820", angle=90.0)
    add_civil_text(msp, "一、防撞护栏及翼缘锚固接口 (1:15)", vb_bar.x(340.0), 156.0, height=2.10, align="CENTER")
    vb_bar.register(doc, msp, paper_bounds=(22.0, 154.0, 112.0, 256.0))

    # 2. Top-Center: 1:10 Abutment D80 Modular Steel Expansion Joint + Blockout + SF1 Anchor Interface!
    vb_joint = ViewBox("expansion_joint_detail", 178.0, 208.0, 10.0, "mm")
    # Left backwall [-430..-40] & right T-girder flange [+40..+460] with 500×220 blockout recess [-250..+250, -220..0]
    msp.add_lwpolyline(
        [vb_joint.pt(-430.0, -300.0), vb_joint.pt(-40.0, -300.0), vb_joint.pt(-40.0, -220.0), vb_joint.pt(-250.0, -220.0), vb_joint.pt(-250.0, 0.0), vb_joint.pt(-430.0, 0.0)],
        close=True,
        dxfattribs={"layer": "CONCRETE"},
    )
    msp.add_lwpolyline(
        [vb_joint.pt(40.0, -300.0), vb_joint.pt(460.0, -300.0), vb_joint.pt(460.0, -180.0), vb_joint.pt(250.0, -180.0), vb_joint.pt(250.0, -220.0), vb_joint.pt(40.0, -220.0)],
        close=True,
        dxfattribs={"layer": "CONCRETE"},
    )
    # Adjacent right-side deck pavement flush with top y=0: 80mm C40 [-180..-100] + waterproof [-100] + 100mm asphalt [-100..0]
    msp.add_lwpolyline([vb_joint.pt(250.0, -180.0), vb_joint.pt(460.0, -180.0), vb_joint.pt(460.0, 0.0), vb_joint.pt(250.0, 0.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_line(vb_joint.pt(250.0, -100.0), vb_joint.pt(460.0, -100.0), dxfattribs={"layer": "HIDDEN"})
    # Cast-in-place C50 steel-fiber concrete anchorage blockouts [-250..-40] & [40..250] with diagonal hatch ticks
    msp.add_lwpolyline([vb_joint.pt(-250.0, -220.0), vb_joint.pt(-40.0, -220.0), vb_joint.pt(-40.0, 0.0), vb_joint.pt(-250.0, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_joint.pt(40.0, -220.0), vb_joint.pt(250.0, -220.0), vb_joint.pt(250.0, 0.0), vb_joint.pt(40.0, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    for hx_blk in (-210.0, -140.0, 90.0, 160.0):
        msp.add_line(vb_joint.pt(hx_blk - 20.0, -205.0), vb_joint.pt(hx_blk + 20.0, -165.0), dxfattribs={"layer": "THIN"})
    # F-shaped steel edge profiles [-75..-40] & [40..75] + neoprene V-seal + foam filler in 80mm gap
    msp.add_lwpolyline([vb_joint.pt(-75.0, 0.0), vb_joint.pt(-40.0, 0.0), vb_joint.pt(-40.0, -85.0), vb_joint.pt(-65.0, -85.0)], close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([vb_joint.pt(75.0, 0.0), vb_joint.pt(40.0, 0.0), vb_joint.pt(40.0, -85.0), vb_joint.pt(65.0, -85.0)], close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([vb_joint.pt(-40.0, -25.0), vb_joint.pt(0.0, -68.0), vb_joint.pt(40.0, -25.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_line(vb_joint.pt(-40.0, -150.0), vb_joint.pt(40.0, -150.0), dxfattribs={"layer": "HIDDEN"})
    # Pre-embedded U-bars rising from backwall & T-girder flange (REBAR_STIRRUP) + SF1 welded anchor loops (REBAR)!
    msp.add_lwpolyline([vb_joint.pt(-210.0, -280.0), vb_joint.pt(-210.0, -75.0), vb_joint.pt(-145.0, -75.0), vb_joint.pt(-145.0, -280.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_lwpolyline([vb_joint.pt(145.0, -280.0), vb_joint.pt(145.0, -75.0), vb_joint.pt(210.0, -75.0), vb_joint.pt(210.0, -280.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_lwpolyline([vb_joint.pt(-55.0, -45.0), vb_joint.pt(-175.0, -175.0), vb_joint.pt(-195.0, -80.0)], close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([vb_joint.pt(55.0, -45.0), vb_joint.pt(175.0, -175.0), vb_joint.pt(195.0, -80.0)], close=False, dxfattribs={"layer": "REBAR"})

    draw_boxed_tag(msp, vb_joint.x(-150.0), vb_joint.y(0.0) + 14.5, "SF1", leader_to=vb_joint.pt(-125.0, -120.0), layer="REBAR")
    add_civil_text(msp, "F型钢+氯丁橡胶止水带", vb_joint.x(60.0), vb_joint.y(0.0) + 12.5, height=1.75, align="LEFT")
    add_civil_text(msp, "100沥青+防水层+80调平", vb_joint.x(255.0), vb_joint.y(0.0) + 3.0, height=1.70, align="LEFT")
    add_civil_text(msp, "C50钢纤维砼槽口(SF1与预埋筋焊接)", vb_joint.x(0.0), vb_joint.y(-300.0) - 11.5, height=1.75, align="CENTER")
    add_arch_dim(msp, vb_joint.pt(-40.0, 0.0), vb_joint.pt(40.0, 0.0), (vb_joint.x(0.0), vb_joint.y(0.0) + 6.0), "缝宽 80")
    add_arch_dim(msp, vb_joint.pt(-250.0, -220.0), vb_joint.pt(250.0, -220.0), (vb_joint.x(0.0), vb_joint.y(-300.0) - 5.2), "预留槽口 500")
    add_arch_dim(msp, vb_joint.pt(-250.0, -220.0), vb_joint.pt(-250.0, 0.0), (vb_joint.x(-430.0) - 5.5, vb_joint.y(-110.0)), "220", angle=90.0)
    add_civil_text(msp, "二、桥台D80型钢伸缩装置及槽口锚固 (1:10)", vb_joint.x(10.0), 156.0, height=2.15, align="CENTER")
    vb_joint.register(doc, msp, paper_bounds=(122.0, 154.0, 248.0, 256.0))

    # 3. Top-Right: 1:10 DN100 Scupper Deck Drain + Paving/Waterproofing Interface + Drainage Direction!
    vb_scup = ViewBox("scupper_drain_detail", 334.0, 202.0, 10.0, "mm")
    # Three-layer deck section around pre-formed hole [-80..+80]:
    # y in [-180..0]: T-girder flange (CONCRETE); y in [0..80]: 80mm C40 leveling; y=80: waterproofing; y in [80..180]: 100mm asphalt
    for sx_sc in (-1.0, 1.0):
        x_in = sx_sc * 80.0
        x_gr = sx_sc * 125.0
        x_out = sx_sc * 300.0
        msp.add_lwpolyline([vb_scup.pt(x_in, -180.0), vb_scup.pt(x_out, -180.0), vb_scup.pt(x_out, 0.0), vb_scup.pt(x_in, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
        msp.add_lwpolyline([vb_scup.pt(x_in, 0.0), vb_scup.pt(x_out, 0.0), vb_scup.pt(x_out, 80.0), vb_scup.pt(x_in, 80.0)], close=True, dxfattribs={"layer": "THIN"})
        msp.add_lwpolyline([vb_scup.pt(x_gr, 80.0), vb_scup.pt(x_out, 80.0), vb_scup.pt(x_out, 180.0), vb_scup.pt(x_gr, 180.0)], close=True, dxfattribs={"layer": "THIN"})
        # Waterproofing membrane (防水粘结层) at y=80 turning neatly into scupper clamping flange + lateral weep hole!
        msp.add_lwpolyline([vb_scup.pt(x_out, 80.0), vb_scup.pt(sx_sc * 62.0, 80.0), vb_scup.pt(sx_sc * 62.0, 102.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
        # Grouted sleeve annulus [±50..±80, y=-180..80]
        msp.add_line(vb_scup.pt(sx_sc * 50.0, -90.0), vb_scup.pt(x_in, -60.0), dxfattribs={"layer": "THIN"})
        msp.add_line(vb_scup.pt(sx_sc * 50.0, 10.0), vb_scup.pt(x_in, 40.0), dxfattribs={"layer": "THIN"})

    # Recessed cast-iron inlet grate pan (y=170, recessed 10mm below asphalt y=180 for positive surface drainage!)
    msp.add_lwpolyline(
        [vb_scup.pt(-125.0, 170.0), vb_scup.pt(125.0, 170.0), vb_scup.pt(95.0, 132.0), vb_scup.pt(-95.0, 132.0)],
        close=True,
        dxfattribs={"layer": "REBAR"},
    )
    for gx_bar in (-75.0, -35.0, 0.0, 35.0, 75.0):
        msp.add_line(vb_scup.pt(gx_bar, 152.0), vb_scup.pt(gx_bar, 170.0), dxfattribs={"layer": "REBAR_STIRRUP"})
    # Clamping seepage collar at y=80 + DN100 vertical drain pipe projecting 150mm below flange soffit (-180..-330)
    msp.add_line(vb_scup.pt(-95.0, 80.0), vb_scup.pt(95.0, 80.0), dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline(
        [vb_scup.pt(-50.0, 132.0), vb_scup.pt(-50.0, -315.0), vb_scup.pt(50.0, -330.0), vb_scup.pt(50.0, 132.0)],
        close=True,
        dxfattribs={"layer": "REBAR"},
    )
    # Explicit Drainage Direction Arrows (进水方向 + 管内竖向排水方向)!
    px_c = vb_scup.x(0.0)
    msp.add_line((px_c, vb_scup.y(242.0)), (px_c, vb_scup.y(182.0)), dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_lwpolyline([(px_c, vb_scup.y(182.0)), (px_c - 1.1, vb_scup.y(204.0)), (px_c + 1.1, vb_scup.y(204.0))], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_line((px_c, vb_scup.y(40.0)), (px_c, vb_scup.y(-320.0)), dxfattribs={"layer": "CENTER"})
    # Place lower drainage direction arrow & text cleanly to the right of DN100 dimension!
    ax_dr = vb_scup.x(105.0)
    msp.add_line((ax_dr, vb_scup.y(-230.0)), (ax_dr, vb_scup.y(-345.0)), dxfattribs={"layer": "REBAR_STIRRUP"})
    msp.add_lwpolyline([(ax_dr, vb_scup.y(-345.0)), (ax_dr - 1.1, vb_scup.y(-322.0)), (ax_dr + 1.1, vb_scup.y(-322.0))], close=True, dxfattribs={"layer": "REBAR_STIRRUP"})
    add_civil_text(msp, "桥面进水(格栅低于铺装10)", px_c, vb_scup.y(250.0), height=1.78, align="CENTER")
    add_civil_text(msp, "排水方向", ax_dr + 2.0, vb_scup.y(-335.0), height=1.85, align="LEFT")

    # Layer & Waterproofing Interface Comb Callouts on right side
    add_civil_text(msp, "100沥青砼", vb_scup.x(305.0), vb_scup.y(125.0), height=1.70, align="LEFT")
    add_civil_text(msp, "防水层收口+渗水孔", vb_scup.x(305.0), vb_scup.y(72.0), height=1.70, align="LEFT")
    add_civil_text(msp, "80调平层+细石砼封堵", vb_scup.x(305.0), vb_scup.y(22.0), height=1.70, align="LEFT")

    add_arch_dim(msp, vb_scup.pt(-50.0, -330.0), vb_scup.pt(50.0, -330.0), (vb_scup.x(0.0), vb_scup.y(-330.0) - 5.8), "DN 100")
    add_arch_dim(msp, vb_scup.pt(-300.0, -330.0), vb_scup.pt(-300.0, -180.0), (vb_scup.x(-300.0) - 5.5, vb_scup.y(-255.0)), "150", angle=90.0)
    add_arch_dim(msp, vb_scup.pt(-300.0, 0.0), vb_scup.pt(-300.0, 180.0), (vb_scup.x(-300.0) - 5.5, vb_scup.y(90.0)), "180", angle=90.0)
    add_civil_text(msp, "三、桥面DN100泄水管及防水层接口 (1:10)", vb_scup.x(0.0), 154.0, height=2.15, align="CENTER")
    vb_scup.register(doc, msp, paper_bounds=(268.0, 152.0, 408.0, 256.0))

    # 4. Bottom-Left: 1:40 Pier-Top Continuous Deck Link Detail (2400/40 = 60mm paper width!)
    vb_cont = ViewBox("deck_continuity_detail", 58.0, 98.0, 40.0, "mm")
    msp.add_lwpolyline([vb_cont.pt(-1200.0, 0.0), vb_cont.pt(-20.0, 0.0), vb_cont.pt(-20.0, 200.0), vb_cont.pt(-1200.0, 200.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_cont.pt(20.0, 0.0), vb_cont.pt(1200.0, 0.0), vb_cont.pt(1200.0, 200.0), vb_cont.pt(20.0, 200.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_cont.pt(-1200.0, 200.0), vb_cont.pt(1200.0, 200.0), vb_cont.pt(1200.0, 280.0), vb_cont.pt(-1200.0, 280.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_lwpolyline([vb_cont.pt(-1200.0, 280.0), vb_cont.pt(1200.0, 280.0), vb_cont.pt(1200.0, 380.0), vb_cont.pt(-1200.0, 380.0)], close=True, dxfattribs={"layer": "THIN"})
    # Beam-end joint centerline (x = 0, gap = 40mm) + 500mm debonded strip [-250 .. +250] with explicit endpoint ticks!
    msp.add_line((vb_cont.x(0.0), vb_cont.y(-90.0)), (vb_cont.x(0.0), vb_cont.y(410.0)), dxfattribs={"layer": "CENTER"})
    msp.add_lwpolyline([vb_cont.pt(-250.0, 200.0), vb_cont.pt(250.0, 200.0), vb_cont.pt(250.0, 212.0), vb_cont.pt(-250.0, 212.0)], close=True, dxfattribs={"layer": "OUTLINE"})
    for rx_db in (-250.0, 250.0):
        msp.add_line(vb_cont.pt(rx_db, -20.0), vb_cont.pt(rx_db, 218.0), dxfattribs={"layer": "HIDDEN"})
    msp.add_line(vb_cont.pt(-1100.0, 245.0), vb_cont.pt(1100.0, 245.0), dxfattribs={"layer": "REBAR"})
    draw_boxed_tag(msp, vb_cont.x(-780.0), vb_cont.y(380.0) + 8.5, "LX1", leader_to=vb_cont.pt(-600.0, 245.0), layer="REBAR")
    add_civil_text(msp, "500宽无粘结层(跨缝对称)", vb_cont.x(60.0), vb_cont.y(380.0) + 6.2, height=1.60, align="LEFT")
    add_civil_text(msp, "梁端接缝(缝宽40)", vb_cont.x(0.0), vb_cont.y(0.0) - 18.0, height=1.65, align="CENTER")
    add_arch_dim(msp, vb_cont.pt(-250.0, 0.0), vb_cont.pt(0.0, 0.0), (vb_cont.x(-125.0), vb_cont.y(0.0) - 5.5), "250")
    add_arch_dim(msp, vb_cont.pt(0.0, 0.0), vb_cont.pt(250.0, 0.0), (vb_cont.x(125.0), vb_cont.y(0.0) - 5.5), "250")
    add_arch_dim(msp, vb_cont.pt(-250.0, 0.0), vb_cont.pt(250.0, 0.0), (vb_cont.x(0.0), vb_cont.y(0.0) - 11.8), "无粘结层 500")
    add_arch_dim(msp, vb_cont.pt(-1200.0, 380.0), vb_cont.pt(1200.0, 380.0), (vb_cont.x(0.0), vb_cont.y(380.0) + 15.5), "桥面连续段 2400")
    add_civil_text(msp, "四、墩顶桥面连续构造 (1:40)", vb_cont.x(0.0), 68.5, height=2.10, align="CENTER")
    vb_cont.register(doc, msp, paper_bounds=(22.0, 66.0, 96.0, 140.0))

    # 5. Bottom-Center Upper: 1:60 6.0m RC Approach Slab Longitudinal View (6000/60 = 100mm > 60mm of 2400 slab!)
    vb_slab = ViewBox("approach_slab_detail", 112.0, 108.0, 60.0, "mm")
    msp.add_lwpolyline([vb_slab.pt(-420.0, -520.0), vb_slab.pt(-220.0, -520.0), vb_slab.pt(-220.0, 300.0), vb_slab.pt(-420.0, 300.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_slab.pt(-220.0, -350.0), vb_slab.pt(130.0, -200.0), vb_slab.pt(130.0, 0.0), vb_slab.pt(-220.0, 0.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_slab.pt(0.0, 0.0), vb_slab.pt(6000.0, 0.0), vb_slab.pt(6000.0, 300.0), vb_slab.pt(0.0, 300.0)], close=True, dxfattribs={"layer": "CONCRETE"})
    msp.add_lwpolyline([vb_slab.pt(130.0, -150.0), vb_slab.pt(6000.0, -150.0), vb_slab.pt(6000.0, 0.0), vb_slab.pt(130.0, 0.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_line(vb_slab.pt(80.0, 55.0), vb_slab.pt(5920.0, 55.0), dxfattribs={"layer": "REBAR"})
    msp.add_line(vb_slab.pt(80.0, 245.0), vb_slab.pt(5920.0, 245.0), dxfattribs={"layer": "REBAR"})
    msp.add_line(vb_slab.pt(65.0, -260.0), vb_slab.pt(65.0, 245.0), dxfattribs={"layer": "REBAR"})
    draw_boxed_tag(msp, vb_slab.x(2800.0), vb_slab.y(300.0) + 9.0, "DB1", leader_to=vb_slab.pt(2800.0, 245.0), layer="REBAR")
    draw_detail_callout(
        msp,
        vb_slab.x(20.0),
        vb_slab.y(0.0),
        4.2,
        "I 牛腿(1:15)",
        leader_to=(vb_slab.x(650.0), vb_slab.y(300.0) + 8.0),
    )
    add_civil_text(msp, "150碎石垫层", vb_slab.x(3100.0), vb_slab.y(-150.0) - 5.0, height=1.68, align="CENTER")
    add_arch_dim(msp, vb_slab.pt(0.0, 300.0), vb_slab.pt(6000.0, 300.0), (vb_slab.x(3000.0), vb_slab.y(300.0) + 17.0), "桥头搭板 6000")
    add_arch_dim(msp, vb_slab.pt(6000.0, 0.0), vb_slab.pt(6000.0, 300.0), (vb_slab.x(6000.0) + 7.5, vb_slab.y(150.0)), "300", angle=90.0)
    add_civil_text(msp, "五、桥头搭板纵断构造 (1:60)", vb_slab.x(3000.0), 89.5, height=2.10, align="CENTER")
    vb_slab.register(doc, msp, paper_bounds=(102.0, 88.0, 224.0, 144.0))

    # 6. Bottom-Center Lower: 1:15 Detail I — Enlarged Abutment Corbel Bearing & DB2 Anti-Slide Dowel Detail!
    vb_cb = ViewBox("approach_corbel_detail", 156.0, 48.0, 15.0, "mm")
    # Backwall stem [-380 .. -180] x [-400 .. +300] + Corbel [-180 .. +170] (350mm total corbel projection!)
    msp.add_lwpolyline(
        [
            vb_cb.pt(-380.0, -400.0),
            vb_cb.pt(-380.0, 300.0),
            vb_cb.pt(-180.0, 300.0),
            vb_cb.pt(-180.0, -20.0),
            vb_cb.pt(170.0, -20.0),
            vb_cb.pt(170.0, -180.0),
            vb_cb.pt(-180.0, -360.0),
            vb_cb.pt(-180.0, -400.0),
        ],
        close=False,
        dxfattribs={"layer": "CONCRETE"},
    )
    # 20mm Asphalt felt bearing pad [0 .. 170] x [-20 .. 0] + 20mm joint filler [-20 .. 0] x [0 .. 300]
    msp.add_lwpolyline([vb_cb.pt(0.0, -20.0), vb_cb.pt(170.0, -20.0), vb_cb.pt(170.0, 0.0), vb_cb.pt(0.0, 0.0)], close=True, dxfattribs={"layer": "THIN"})
    msp.add_line(vb_cb.pt(-20.0, 0.0), vb_cb.pt(-20.0, 300.0), dxfattribs={"layer": "THIN"})
    # Approach slab left end [0 .. 660] x [0 .. 300] + right break line
    msp.add_lwpolyline(
        [vb_cb.pt(660.0, 0.0), vb_cb.pt(0.0, 0.0), vb_cb.pt(0.0, 300.0), vb_cb.pt(660.0, 300.0)],
        close=False,
        dxfattribs={"layer": "CONCRETE"},
    )
    msp.add_line(vb_cb.pt(660.0, -35.0), vb_cb.pt(660.0, 335.0), dxfattribs={"layer": "THIN"})
    # DB1 top & bottom slab bars with end hooks + Corbel inclined stirrup
    msp.add_lwpolyline([vb_cb.pt(640.0, 245.0), vb_cb.pt(45.0, 245.0), vb_cb.pt(45.0, 175.0)], close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([vb_cb.pt(640.0, 55.0), vb_cb.pt(45.0, 55.0), vb_cb.pt(45.0, 125.0)], close=False, dxfattribs={"layer": "REBAR"})
    msp.add_lwpolyline([vb_cb.pt(-310.0, -40.0), vb_cb.pt(135.0, -40.0), vb_cb.pt(135.0, -155.0), vb_cb.pt(-240.0, -320.0)], close=False, dxfattribs={"layer": "REBAR_STIRRUP"})
    # Prominent DB2 Φ22 vertical dowel anchor bar at x = +85mm (anchored 280mm into corbel, extending +240mm into slab!)
    msp.add_lwpolyline(
        [vb_cb.pt(155.0, -280.0), vb_cb.pt(85.0, -280.0), vb_cb.pt(85.0, 240.0)],
        close=False,
        dxfattribs={"layer": "REBAR"},
    )
    # DB2 leader tip lands squarely on the DB2 vertical dowel bar!
    draw_boxed_tag(msp, vb_cb.x(430.0), vb_cb.y(-165.0), "DB2", leader_to=vb_cb.pt(85.0, -110.0), layer="REBAR")
    add_civil_text(msp, "20油毛毡垫层+沥青麻絮", vb_cb.x(200.0), vb_cb.y(-80.0), height=1.65, align="LEFT")
    add_arch_dim(msp, vb_cb.pt(-180.0, -20.0), vb_cb.pt(170.0, -20.0), (vb_cb.x(-5.0), vb_cb.y(300.0) + 5.5), "牛腿宽 350")
    add_arch_dim(msp, vb_cb.pt(-380.0, -280.0), vb_cb.pt(-380.0, 0.0), (vb_cb.x(-380.0) - 5.5, vb_cb.y(-140.0)), "锚入 280", angle=90.0)
    add_civil_text(msp, "六、I 台背牛腿支承及DB2锚栓放大图 (1:15)", vb_cb.x(140.0), 15.5, height=2.05, align="CENTER")
    vb_cb.register(doc, msp, paper_bounds=(120.0, 14.0, 224.0, 84.0))

    draw_rebar_schedule_table(
        doc,
        msp,
        bars,
        x0=b["tb_x0"],
        y0=b["tb_y1"],
        width=b["ix1"] - b["tb_x0"],
        title="桥面附属结构钢筋明细表",
        dock_to_title_block=True,
    )
    return doc


# ======================================================================
# 8. Double-Column Pier & Pile-Bent Abutment (QL-14 & QL-15, Cat 8)
# ======================================================================
def _draw_pier_and_abutment(spec: dict[str, Any]) -> ezdxf.document.Drawing:
    variant = str(spec.get("variant", "pier")).strip().lower()
    is_abutment = variant == "abutment"
    is_wenchang = str(spec.get("preset", "")).strip().lower() == "wenchang"
    cap_len_mm = float(spec.get("cap_length_mm", (9000.0 if is_abutment else 8800.0) if is_wenchang else (11750.0 if is_abutment else 10950.0)))
    cap_h_mm = float(spec.get("cap_height_mm", 1400.0 if is_wenchang else (1400.0 if is_abutment else 1500.0)))
    cap_w_mm = float(spec.get("cap_width_mm", 1800.0 if is_abutment else (1600.0 if is_wenchang else 1700.0)))
    col_spacing_mm = float(spec.get("column_spacing_mm", (5400.0 if is_abutment else 5200.0) if is_wenchang else (6800.0 if is_abutment else 6650.0)))
    col_d_mm = float(spec.get("column_diameter_mm", 1200.0))
    col_h_mm = float(spec.get("column_height_mm", 5500.0))
    tie_h_mm = float(spec.get("tie_beam_height_mm", 1200.0))
    pile_d_mm = float(spec.get("pile_diameter_mm", 1400.0))
    bw_thk_mm = float(spec.get("backwall_thick_mm", 550.0))
    bw_h_mm = float(spec.get("backwall_height_mm", 1750.0))
    ew_len_mm = float(spec.get("earwall_length_mm", 2000.0))
    n_pedestals = int(spec.get("n_pedestals", 5))
    ped_spacing_mm = float(spec.get("pedestal_spacing_mm", 1950.0 if is_wenchang else 2348.0))
    bearing_model = str(spec.get("bearing_model", "GJZ 300×400×74" if is_wenchang else "GJZ 300×450×85"))
    slope = float(spec.get("cross_slope_pct", 2.0)) / 100.0

    calc_pa = spec.get("_calc_report") if isinstance(spec.get("_calc_report"), Mapping) else None
    sup_pa = (calc_pa or {}).get("support_layout") or {}
    default_title = "桩柱式桥台一般构造及支座布置图" if is_abutment else "双柱式桥墩一般构造及支座布置图"
    doc, msp, b = setup_civil_dxf_document(
        sheet_size=str(spec.get("sheet_size", "A3")),
        title=str(spec.get("title", default_title)),
        project=str(spec.get("project", "4×25m 预应力混凝土简支T梁桥")),
        drawing_no=str(spec.get("drawing_no", "QL-15" if is_abutment else "QL-14")),
        unit_note="mm",
        scale_label="1:80" if is_abutment else "1:25 / 1:80",
        style_overrides=_resolve_style_overrides(spec),
    )

    half_cap = 0.5 * cap_len_mm
    edge_drop = half_cap * slope
    half_cw = 0.5 * cap_w_mm

    if not is_abutment:
        # ---- QL-14: Double-Column Pier + 1200mm Pile-Top Tie Beam + Bored Piles (100% consistent with QL-16 & QL-17!) ----
        vb_el = ViewBox("substructure_elev", 102.0, 234.0, 80.0, "mm")
        overhang = max(200.0, 0.5 * (cap_len_mm - col_spacing_mm - col_d_mm))
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
                [vb_el.pt(rxp - 250.0, ry_top), vb_el.pt(rxp + 250.0, ry_top), vb_el.pt(rxp + 250.0, ry_top + 150.0), vb_el.pt(rxp - 250.0, ry_top + 150.0)],
                close=True,
                dxfattribs={"layer": "OUTLINE"},
            )
        draw_slope_arrow(msp, vb_el.x(-0.48 * half_cap), vb_el.y(0.0) + 5.2, slope_text="2%", direction="left")
        draw_slope_arrow(msp, vb_el.x(0.48 * half_cap), vb_el.y(0.0) + 5.2, slope_text="2%", direction="right")

        ry_col_top = -cap_h_mm
        ry_col_bot = ry_col_top - col_h_mm
        ry_pile_top = ry_col_bot - tie_h_mm
        ry_pile_bot = ry_pile_top - 3000.0
        collar_hw = 0.5 * pile_d_mm + 150.0
        for rxc in (-0.5 * col_spacing_mm, 0.5 * col_spacing_mm):
            msp.add_lwpolyline(
                [vb_el.pt(rxc - 0.5 * col_d_mm, ry_col_bot), vb_el.pt(rxc + 0.5 * col_d_mm, ry_col_bot), vb_el.pt(rxc + 0.5 * col_d_mm, ry_col_top), vb_el.pt(rxc - 0.5 * col_d_mm, ry_col_top)],
                close=True,
                dxfattribs={"layer": "OUTLINE"},
            )
            msp.add_line(vb_el.pt(rxc - 0.5 * pile_d_mm, ry_pile_top), vb_el.pt(rxc - 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
            msp.add_line(vb_el.pt(rxc + 0.5 * pile_d_mm, ry_pile_top), vb_el.pt(rxc + 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
            draw_pile_s_break(msp, vb_el.x(rxc), vb_el.y(ry_pile_bot), vb_el.span(pile_d_mm))
            msp.add_line((vb_el.x(rxc), vb_el.y(0.0) + 8.0), (vb_el.x(rxc), vb_el.y(ry_pile_bot) - 4.5), dxfattribs={"layer": "CENTER"})

        # Full-depth 1200mm Pile-Top Tie Beam + Collars connecting both columns & piles!
        msp.add_lwpolyline(
            [
                vb_el.pt(-0.5 * col_spacing_mm - collar_hw, ry_pile_top),
                vb_el.pt(0.5 * col_spacing_mm + collar_hw, ry_pile_top),
                vb_el.pt(0.5 * col_spacing_mm + collar_hw, ry_col_bot),
                vb_el.pt(-0.5 * col_spacing_mm - collar_hw, ry_col_bot),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        draw_ground_line(msp, vb_el.x(-half_cap) - 4.0, vb_el.y(ry_col_bot) + 4.0, vb_el.x(-half_cap) + 18.0, vb_el.y(ry_col_bot) + 4.0, label="地面线", scale=0.82)

        add_arch_dim(msp, (vb_el.x(-half_cap), vb_el.y(0.0)), (vb_el.x(half_cap), vb_el.y(0.0)), (vb_el.x(0.0), vb_el.y(0.0) + 13.5), f"盖梁长 {cap_len_mm:g}")
        add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm, ry_col_top), vb_el.pt(0.5 * col_spacing_mm, ry_col_top), (vb_el.x(0.0), vb_el.y(ry_col_top) - 7.5), f"墩距 {col_spacing_mm:g}")
        add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm - 0.5 * col_d_mm, ry_col_bot + 1600.0), vb_el.pt(-0.5 * col_spacing_mm + 0.5 * col_d_mm, ry_col_bot + 1600.0), (vb_el.x(-0.5 * col_spacing_mm), vb_el.y(ry_col_bot + 1600.0) + 6.0), f"墩径 {col_d_mm:g}")
        add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm - 0.5 * pile_d_mm, ry_pile_bot), vb_el.pt(-0.5 * col_spacing_mm + 0.5 * pile_d_mm, ry_pile_bot), (vb_el.x(-0.5 * col_spacing_mm), vb_el.y(ry_pile_bot) - 9.5), f"桩径 {pile_d_mm:g}")
        x_r_dim = vb_el.x(half_cap) + 7.5
        add_arch_dim(msp, (vb_el.x(half_cap), vb_el.y(ry_col_top)), (vb_el.x(half_cap), vb_el.y(0.0)), (x_r_dim, vb_el.y(0.5 * ry_col_top)), f"{cap_h_mm:g}", angle=90.0)
        add_arch_dim(msp, vb_el.pt(0.5 * col_spacing_mm + 0.5 * col_d_mm, ry_col_bot), vb_el.pt(0.5 * col_spacing_mm + 0.5 * col_d_mm, ry_col_top), (x_r_dim, vb_el.y(0.5 * (ry_col_bot + ry_col_top))), f"墩高 {col_h_mm:g}", angle=90.0)
        add_arch_dim(msp, vb_el.pt(0.5 * col_spacing_mm + collar_hw, ry_pile_top), vb_el.pt(0.5 * col_spacing_mm + collar_hw, ry_col_bot), (x_r_dim, vb_el.y(0.5 * (ry_pile_top + ry_col_bot))), f"系梁 {tie_h_mm:g}", angle=90.0)
        add_civil_text(msp, "双柱墩正立面图 (1:80)", vb_el.x(0.0), 48.0, height=2.5, align="CENTER")
        vb_el.register(doc, msp, paper_bounds=(vb_el.x(-half_cap) - 8.0, 52.0, x_r_dim + 6.0, vb_el.y(0.0) + 18.0))

        # Side View (1:80)
        vb_sd = ViewBox("substructure_side", 236.0, 234.0, 80.0, "mm")
        msp.add_lwpolyline([vb_sd.pt(-half_cw, ry_col_top), vb_sd.pt(half_cw, ry_col_top), vb_sd.pt(half_cw, 0.0), vb_sd.pt(-half_cw, 0.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_lwpolyline([vb_sd.pt(-0.5 * col_d_mm, ry_col_bot), vb_sd.pt(0.5 * col_d_mm, ry_col_bot), vb_sd.pt(0.5 * col_d_mm, ry_col_top), vb_sd.pt(-0.5 * col_d_mm, ry_col_top)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_lwpolyline([vb_sd.pt(-collar_hw, ry_pile_top), vb_sd.pt(collar_hw, ry_pile_top), vb_sd.pt(collar_hw, ry_col_bot), vb_sd.pt(-collar_hw, ry_col_bot)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb_sd.pt(-0.5 * pile_d_mm, ry_pile_top), vb_sd.pt(-0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb_sd.pt(0.5 * pile_d_mm, ry_pile_top), vb_sd.pt(0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb_sd.x(0.0), vb_sd.y(ry_pile_bot), vb_sd.span(pile_d_mm))
        add_arch_dim(msp, vb_sd.pt(-half_cw, 0.0), vb_sd.pt(half_cw, 0.0), (vb_sd.x(0.0), vb_sd.y(0.0) + 13.5), f"盖梁宽 {cap_w_mm:g}")
        add_civil_text(msp, "桥墩侧面图 (1:80)", vb_sd.x(0.0), 48.0, height=2.5, align="CENTER")
        vb_sd.register(doc, msp, paper_bounds=(204.0, 52.0, 268.0, 254.0))

    else:
        # ---- QL-15: Pile-Cap Abutment + 550 Backwall + 2000 Earwalls + Direct Φ1400 Piles (100% consistent with QL-18!) ----
        vb_el = ViewBox("substructure_elev", 102.0, 206.0, 80.0, "mm")
        # Cap with real 2% bridge-seat cross-slope (y in [-cap_h_mm .. 0]) + Backwall/Earwalls behind [0 .. +bw_h_mm]
        cap_pts = [
            vb_el.pt(-half_cap, -edge_drop),
            vb_el.pt(0.0, 0.0),
            vb_el.pt(half_cap, -edge_drop),
            vb_el.pt(half_cap, -cap_h_mm),
            vb_el.pt(-half_cap, -cap_h_mm),
        ]
        msp.add_lwpolyline(cap_pts, close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_lwpolyline(
            [
                vb_el.pt(-half_cap, -edge_drop),
                vb_el.pt(-half_cap, bw_h_mm - edge_drop),
                vb_el.pt(0.0, bw_h_mm),
                vb_el.pt(half_cap, bw_h_mm - edge_drop),
                vb_el.pt(half_cap, -edge_drop),
            ],
            close=False,
            dxfattribs={"layer": "THIN"},
        )
        # Inner earwall vertical edge lines at ±(half_cap - 450)
        for sx_ew in (-1.0, 1.0):
            rx_ew = sx_ew * (half_cap - 450.0)
            msp.add_line(vb_el.pt(rx_ew, -abs(rx_ew) * slope), vb_el.pt(rx_ew, bw_h_mm - abs(rx_ew) * slope), dxfattribs={"layer": "HIDDEN"})
        for i in range(n_pedestals):
            rxp = (i - 0.5 * (n_pedestals - 1)) * ped_spacing_mm
            ry_top = -abs(rxp) * slope
            msp.add_lwpolyline(
                [vb_el.pt(rxp - 250.0, ry_top), vb_el.pt(rxp + 250.0, ry_top), vb_el.pt(rxp + 250.0, ry_top + 150.0), vb_el.pt(rxp - 250.0, ry_top + 150.0)],
                close=True,
                dxfattribs={"layer": "OUTLINE"},
            )
        draw_slope_arrow(msp, vb_el.x(-0.48 * half_cap), vb_el.y(0.0) + 5.2, slope_text="2%", direction="left")
        draw_slope_arrow(msp, vb_el.x(0.48 * half_cap), vb_el.y(0.0) + 5.2, slope_text="2%", direction="right")

        # Twin Φ1400 Bored Piles connecting directly into bottom of 1400mm Abutment Cap!
        ry_cap_bot = -cap_h_mm
        ry_pile_bot = ry_cap_bot - 4600.0
        for rxc in (-0.5 * col_spacing_mm, 0.5 * col_spacing_mm):
            msp.add_line(vb_el.pt(rxc - 0.5 * pile_d_mm, ry_cap_bot), vb_el.pt(rxc - 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
            msp.add_line(vb_el.pt(rxc + 0.5 * pile_d_mm, ry_cap_bot), vb_el.pt(rxc + 0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
            draw_pile_s_break(msp, vb_el.x(rxc), vb_el.y(ry_pile_bot), vb_el.span(pile_d_mm))
            msp.add_line((vb_el.x(rxc), vb_el.y(bw_h_mm) + 4.0), (vb_el.x(rxc), vb_el.y(ry_pile_bot) - 4.5), dxfattribs={"layer": "CENTER"})

        draw_ground_line(msp, vb_el.x(-half_cap) - 4.0, vb_el.y(ry_cap_bot) + 3.0, vb_el.x(-half_cap) + 18.0, vb_el.y(ry_cap_bot) + 3.0, label="地面线", scale=0.82)
        add_arch_dim(msp, (vb_el.x(-half_cap), vb_el.y(bw_h_mm)), (vb_el.x(half_cap), vb_el.y(bw_h_mm)), (vb_el.x(0.0), vb_el.y(bw_h_mm) + 8.5), f"台帽长 {cap_len_mm:g}")
        add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm, ry_cap_bot - 1400.0), vb_el.pt(0.5 * col_spacing_mm, ry_cap_bot - 1400.0), (vb_el.x(0.0), vb_el.y(ry_cap_bot - 1400.0) - 6.0), f"桩距 {col_spacing_mm:g}")
        add_arch_dim(msp, vb_el.pt(-0.5 * col_spacing_mm - 0.5 * pile_d_mm, ry_pile_bot), vb_el.pt(-0.5 * col_spacing_mm + 0.5 * pile_d_mm, ry_pile_bot), (vb_el.x(-0.5 * col_spacing_mm), vb_el.y(ry_pile_bot) - 9.5), f"桩径 {pile_d_mm:g}")
        x_r_ab = vb_el.x(half_cap) + 7.5
        add_arch_dim(msp, (vb_el.x(half_cap), vb_el.y(ry_cap_bot)), (vb_el.x(half_cap), vb_el.y(0.0)), (x_r_ab, vb_el.y(-0.5 * cap_h_mm)), f"{cap_h_mm:g}", angle=90.0)
        add_arch_dim(msp, (vb_el.x(half_cap), vb_el.y(0.0)), (vb_el.x(half_cap), vb_el.y(bw_h_mm)), (x_r_ab, vb_el.y(0.5 * bw_h_mm)), f"背墙 {bw_h_mm:g}", angle=90.0)
        abut_len_mm_15 = float(spec.get("abutment_pile_length_mm", 15000.0))
        add_civil_text(msp, f"桥台正立面图 (1:80，设计桩长 {abut_len_mm_15/1000:g}m)", vb_el.x(0.0), 114.0, height=2.35, align="CENTER")
        vb_el.register(doc, msp, paper_bounds=(vb_el.x(-half_cap) - 8.0, 112.0, x_r_ab + 6.0, vb_el.y(bw_h_mm) + 14.0))

        # Side View (1:80): Cap 1800×1400 + Backwall 550×1750 + Corbel + Earwall 2000 + Direct Pile Φ1400!
        vb_sd = ViewBox("substructure_side", 242.0, 206.0, 80.0, "mm")
        msp.add_lwpolyline([vb_sd.pt(-half_cw, ry_cap_bot), vb_sd.pt(half_cw, ry_cap_bot), vb_sd.pt(half_cw, 0.0), vb_sd.pt(-half_cw, 0.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        # Backwall (550×1750) + approach corbel (-300) + Bearing pedestal (+500×150)
        msp.add_lwpolyline(
            [
                vb_sd.pt(-half_cw, 0.0),
                vb_sd.pt(-half_cw + bw_thk_mm, 0.0),
                vb_sd.pt(-half_cw + bw_thk_mm, bw_h_mm),
                vb_sd.pt(-half_cw, bw_h_mm),
                vb_sd.pt(-half_cw, bw_h_mm - 300.0),
                vb_sd.pt(-half_cw - 300.0, bw_h_mm - 300.0),
                vb_sd.pt(-half_cw - 300.0, bw_h_mm - 750.0),
                vb_sd.pt(-half_cw, bw_h_mm - 950.0),
            ],
            close=True,
            dxfattribs={"layer": "OUTLINE"},
        )
        # Earwall extending 2000mm toward embankment (-half_cw - ew_len_mm .. -half_cw)
        msp.add_lwpolyline(
            [vb_sd.pt(-half_cw - ew_len_mm, 250.0), vb_sd.pt(-half_cw, -350.0), vb_sd.pt(-half_cw, bw_h_mm), vb_sd.pt(-half_cw - ew_len_mm, bw_h_mm)],
            close=True,
            dxfattribs={"layer": "HIDDEN"},
        )
        msp.add_lwpolyline([vb_sd.pt(80.0, 0.0), vb_sd.pt(580.0, 0.0), vb_sd.pt(580.0, 150.0), vb_sd.pt(80.0, 150.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb_sd.pt(-0.5 * pile_d_mm, ry_cap_bot), vb_sd.pt(-0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        msp.add_line(vb_sd.pt(0.5 * pile_d_mm, ry_cap_bot), vb_sd.pt(0.5 * pile_d_mm, ry_pile_bot), dxfattribs={"layer": "OUTLINE"})
        draw_pile_s_break(msp, vb_sd.x(0.0), vb_sd.y(ry_pile_bot), vb_sd.span(pile_d_mm))

        add_arch_dim(msp, vb_sd.pt(-half_cw, ry_cap_bot), vb_sd.pt(half_cw, ry_cap_bot), (vb_sd.x(0.0), vb_sd.y(ry_cap_bot) - 6.5), f"台帽宽 {cap_w_mm:g}")
        add_arch_dim(msp, vb_sd.pt(-half_cw, bw_h_mm), vb_sd.pt(-half_cw + bw_thk_mm, bw_h_mm), (vb_sd.x(-half_cw + 0.5 * bw_thk_mm), vb_sd.y(bw_h_mm) + 6.5), f"{bw_thk_mm:g}")
        add_arch_dim(msp, vb_sd.pt(-half_cw - ew_len_mm, bw_h_mm), vb_sd.pt(-half_cw, bw_h_mm), (vb_sd.x(-half_cw - 0.5 * ew_len_mm), vb_sd.y(bw_h_mm) + 6.5), f"耳墙 {ew_len_mm:g}")
        add_civil_text(msp, "桥台侧面图 (1:80)", vb_sd.x(-400.0), 114.0, height=2.5, align="CENTER")
        vb_sd.register(doc, msp, paper_bounds=(198.0, 112.0, 272.0, 252.0))

    # 3. Right: Bearing Layout Plan (+ Shared 1:25 Pier-Top Support Chain 480+40+480=1000mm on Pier Sheet!)
    bx0, by0, bw, bh = (282.0, 96.0, 118.0, 134.0) if is_abutment else (282.0, 148.0, 118.0, 98.0)
    msp.add_lwpolyline([(bx0, by0), (bx0 + bw, by0), (bx0 + bw, by0 + bh), (bx0, by0 + bh)], close=True, dxfattribs={"layer": "THIN"})
    add_civil_text(msp, "单跨支座平面布置图", bx0 + 0.5 * bw, by0 + bh + 4.5, height=2.4, align="CENTER")
    add_civil_text(msp, f"支座型号：{bearing_model}", bx0 + 0.5 * bw, by0 - 5.5, height=2.0, align="CENTER")
    x_z1, x_z2 = bx0 + 24.0, bx0 + bw - 24.0
    msp.add_line((x_z1, by0 + 10.0), (x_z1, by0 + bh - 10.0), dxfattribs={"layer": "CENTER"})
    msp.add_line((x_z2, by0 + 10.0), (x_z2, by0 + bh - 10.0), dxfattribs={"layer": "CENTER"})
    for i in range(n_pedestals):
        yg = by0 + 16.0 + i * ((bh - 32.0) / max(1, n_pedestals - 1))
        msp.add_line((x_z1 - 8.0, yg), (x_z2 + 8.0, yg), dxfattribs={"layer": "THIN"})
        draw_bearing_symbol(msp, x_z1, yg, kind="fixed", size=3.1)
        draw_bearing_symbol(msp, x_z2, yg, kind="sliding_longitudinal", size=3.1)
        add_civil_text(msp, f"{i+1}#", bx0 + 7.0, yg - 1.0, height=1.9)

    if not is_abutment:
        d_end_15 = float(sup_pa.get("beam_end_to_bearing_mm", 480.0))
        gap_15 = float(sup_pa.get("expansion_gap_mm", 40.0))
        pitch_15 = float(sup_pa.get("pier_bearing_row_pitch_mm", 2.0 * d_end_15 + gap_15))
        vstack_15 = float((sup_pa.get("vertical_stack_mm") or {}).get("total_support_height_mm", 244.0))
        vb_p15 = ViewBox("pier_top_support_chain", 341.0, 92.0, 25.0, "mm")
        gh_15 = 0.5 * gap_15
        bx_15 = 0.5 * pitch_15
        msp.add_lwpolyline([vb_p15.pt(-0.5 * cap_w_mm, -244.0), vb_p15.pt(0.5 * cap_w_mm, -244.0), vb_p15.pt(0.5 * cap_w_mm, -94.0), vb_p15.pt(-0.5 * cap_w_mm, -94.0)], close=True, dxfattribs={"layer": "OUTLINE"})
        for sx_15 in (-1.0, 1.0):
            msp.add_lwpolyline([vb_p15.pt(sx_15 * gh_15, 0.0), vb_p15.pt(sx_15 * (bx_15 + 240.0), 0.0), vb_p15.pt(sx_15 * (bx_15 + 240.0), 260.0), vb_p15.pt(sx_15 * gh_15, 260.0)], close=True, dxfattribs={"layer": "OUTLINE"})
            draw_bearing_symbol(msp, vb_p15.x(sx_15 * bx_15), vb_p15.y(0.0) - 1.5, kind="fixed" if sx_15 < 0 else "sliding_longitudinal", size=2.3)
        add_arch_dim(msp, vb_p15.pt(-bx_15, 260.0), vb_p15.pt(-gh_15, 260.0), (vb_p15.x(-0.5 * (bx_15 + gh_15)), vb_p15.y(260.0) + 5.0), f"{d_end_15:g}")
        add_arch_dim(msp, vb_p15.pt(-gh_15, 260.0), vb_p15.pt(gh_15, 260.0), (vb_p15.x(0.0), vb_p15.y(260.0) + 5.0), f"{gap_15:g}")
        add_arch_dim(msp, vb_p15.pt(gh_15, 260.0), vb_p15.pt(bx_15, 260.0), (vb_p15.x(0.5 * (bx_15 + gh_15)), vb_p15.y(260.0) + 5.0), f"{d_end_15:g}")
        add_arch_dim(msp, vb_p15.pt(-bx_15, -244.0), vb_p15.pt(bx_15, -244.0), (vb_p15.x(0.0), vb_p15.y(-244.0) - 5.2), f"支座纵距 {pitch_15:g}")
        add_civil_text(msp, f"盖梁顶支承尺寸链 (1:25, 总高{vstack_15:g}mm)", vb_p15.x(0.0), 70.0, height=1.82, align="CENTER")
        vb_p15.register(doc, msp, paper_bounds=(276.0, 68.0, 406.0, 128.0))
        attach_sheet_metadata(doc, msp, "support_layout", dict(sup_pa))

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
        raw_paragraphs = [
            f"一、工程概况：{ps['project_name']}，跨径布置 {ps['n_spans']}×{ps['standard_span_m']:g}m，计算跨径 {ps['calc_span_m']:g}m，预制梁长 {ps['beam_length_m']:g}m。",
            f"二、横断面标准：桥面全宽 {ps['deck_width_m']*1000:g}mm，护栏内净宽 {ps['clear_width_m']*1000:g}mm，行车道宽 {ps['roadway_width_m']*1000:g}mm，双向桥面横坡 {ps['cross_slope_pct']:g}%。",
            f"三、桥面铺装与防水：{ps.get('asphalt_thick_mm', 100):g}mm 密级配沥青混凝土 + 防水粘结层 + {ps.get('concrete_paving_thick_mm', 80):g}mm C40混凝土调平层。",
            "四、主要材料：预制T梁及湿接缝采用 C50 混凝土；支座垫石采用 C40 混凝土；预应力钢束采用低松弛钢绞线 Φs15.2 (fpk=1860MPa)；墩台盖梁、系梁、耳背墙、搭板及水下钻孔桩采用 C30 混凝土；普通钢筋采用 HPB300 (GB 1499.1-2024) 和 HRB400 (GB 1499.2-2024)。",
            "五、设计规范：JTG B01-2014《公路工程技术标准》、JTG D60-2015《公路桥涵设计通用规范》、JTG 3362-2018《公路钢筋混凝土及预应力混凝土桥涵设计规范》、JTG 3363-2019《公路桥涵地基与基础设计规范》。",
            "六、施工要点：预制T梁混凝土强度及龄期达标后对称张拉并真空压浆；架梁后先浇筑墩顶湿接缝及横隔梁湿接缝，再施工桥面连续及调平层。",
        ]
        calc_st_01 = "通过" if calc.get("calculation_status", calc.get("status")) == "passed" else "未通过"
        grad_st_01 = (
            "资料齐套"
            if calc.get("graduation_readiness_status") == "ready"
            else ("存在超限" if calc.get("graduation_readiness_status") == "failed" else "待确认齐套")
        )
        raw_paragraphs.append(
            f"七、双状态总览：当前参数结构计算状态【{calc_st_01}】；毕业设计资料齐套状态【{grad_st_01}】。"
        )
        if calc.get("unresolved_assumptions"):
            miss_zh = "、".join(_UNRESOLVED_FIELD_LABELS_ZH.get(str(u["field"]), str(u["field"])) for u in calc["unresolved_assumptions"])
            raw_paragraphs.append(f"八、待实测核定假定项：{miss_zh}（跨径净空、内力包络及墩台桩长结论仍基于初拟参数，补齐实测水文地勘后自动同步全套图表）。")
        pending_deliv = [d["title"] for d in calc.get("pending_graduation_deliverables", []) if d.get("status") != "completed"]
        if pending_deliv:
            raw_paragraphs.append(f"九、毕业设计待完成交付项：{'；'.join(pending_deliv)}。")
        if calc.get("failed_checks"):
            fc_summary = "；".join(
                f"{fc['component']}({fc['demand']}>{fc['allowable']}{fc['unit']}，关联{','.join(fc['related_drawings'])})"
                for fc in calc["failed_checks"][:3]
            )
            raw_paragraphs.append(f"【警示】结构验算未通过项：{fc_summary}！")

        # Left Panel: Wrapped General Design Specification Notes (x=22..204, y=18..264)
        lx0, lx1, ly0, ly1 = 22.0, 204.0, 18.0, 264.0
        msp.add_lwpolyline([(lx0, ly0), (lx1, ly0), (lx1, ly1), (lx0, ly1)], close=True, dxfattribs={"layer": "FRAME"})
        msp.add_line((lx0, ly1 - 8.0), (lx1, ly1 - 8.0), dxfattribs={"layer": "FRAME"})
        add_civil_text(msp, "一、桥梁施工图设计总说明", 0.5 * (lx0 + lx1), ly1 - 5.8, height=2.5, align="CENTER")

        wrapped_lines: list[str] = []
        for para in raw_paragraphs:
            wrapped_lines.extend(_wrap_zh_text(para, width_chars=36))
        line_step = min(5.4, (ly1 - ly0 - 14.0) / max(1, len(wrapped_lines)))
        for idx, wline in enumerate(wrapped_lines):
            wy = ly1 - 13.5 - idx * line_step
            if wy >= ly0 + 2.5:
                add_civil_text(msp, wline, lx0 + 3.5, wy, height=1.88)

        # Right Panel: Complete Construction Drawing Index Table (co-reads calc['sheet_manifest']!)
        manifest_tuples = ((calc.get("sheet_manifest") or {}).get("index_tuples")) or DEFAULT_18_SHEET_INDEX
        sheet_index = list(spec.get("sheet_index") or manifest_tuples)
        rx0, rx1, ry_top = 210.0, 406.0, 264.0
        rw = rx1 - rx0
        title_h = 7.5
        hdr_h = 6.2
        row_h = min(10.6, (212.0 - title_h - hdr_h) / max(1, len(sheet_index)))
        ry_bot = ry_top - title_h - hdr_h - len(sheet_index) * row_h
        msp.add_lwpolyline([(rx0, ry_bot), (rx1, ry_bot), (rx1, ry_top), (rx0, ry_top)], close=True, dxfattribs={"layer": "FRAME"})
        msp.add_line((rx0, ry_top - title_h), (rx1, ry_top - title_h), dxfattribs={"layer": "FRAME"})
        msp.add_line((rx0, ry_top - title_h - hdr_h), (rx1, ry_top - title_h - hdr_h), dxfattribs={"layer": "FRAME"})
        add_civil_text(msp, f"二、全套施工图目录（共 {len(sheet_index)} 张 A3）", rx0 + 0.5 * rw, ry_top - 5.4, height=2.5, align="CENTER")

        r_cols = (rx0, rx0 + 14.0, rx0 + 36.0, rx0 + 146.0, rx0 + 180.0, rx1)
        for cx_col in r_cols[1:-1]:
            msp.add_line((cx_col, ry_bot), (cx_col, ry_top - title_h), dxfattribs={"layer": "THIN"})
        idx_headers = ("序号", "图号", "图纸名称", "比例", "图幅")
        y_hdr_b = ry_top - title_h - hdr_h
        for ci, hs in enumerate(idx_headers):
            add_civil_text(msp, hs, 0.5 * (r_cols[ci] + r_cols[ci + 1]), y_hdr_b + 1.8, height=1.9, align="CENTER")

        for si, (dno_s, dtitle_s, dscale_s) in enumerate(sheet_index):
            ry_r = y_hdr_b - (si + 1) * row_h
            if si < len(sheet_index) - 1:
                msp.add_line((rx0, ry_r), (rx1, ry_r), dxfattribs={"layer": "THIN"})
            cells_idx = (f"{si + 1}", str(dno_s), str(dtitle_s), str(dscale_s), "A3")
            for ci, vs in enumerate(cells_idx):
                th = 1.72 if (ci == 2 and len(vs) > 18) else 1.82
                add_civil_text(msp, vs, 0.5 * (r_cols[ci] + r_cols[ci + 1]), ry_r + 0.28 * row_h, height=th, align="CENTER")

        attach_sheet_metadata(doc, msp, "sheet_index", {"sheets": [{"drawing_no": d, "title": t, "scale": s} for d, t, s in sheet_index]})
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
    x0, y_top, w = 28.0, 258.0, 372.0
    rows = boq["rows"]
    row_h = min(10.8, 178.0 / (len(rows) + 1))
    y_bot = y_top - (len(rows) + 1) * row_h
    msp.add_lwpolyline([(x0, y_bot), (x0 + w, y_bot), (x0 + w, y_top), (x0, y_top)], close=True, dxfattribs={"layer": "FRAME"})
    cols = (x0, x0 + 102.0, x0 + 198.0, x0 + 236.0, x0 + 306.0, x0 + w)
    for cx in cols[1:-1]:
        msp.add_line((cx, y_bot), (cx, y_top), dxfattribs={"layer": "THIN"})
    headers = ("工程部位", "材料名称及规格", "单位", "全桥数量", "对应图号")
    for ci, h_s in enumerate(headers):
        add_civil_text(msp, h_s, 0.5 * (cols[ci] + cols[ci + 1]), y_top - 0.68 * row_h, height=2.3, align="CENTER")
    msp.add_line((x0, y_top - row_h), (x0 + w, y_top - row_h), dxfattribs={"layer": "FRAME"})

    for ri, r in enumerate(rows):
        ry = y_top - (ri + 2) * row_h
        if ri < len(rows) - 1:
            msp.add_line((x0, ry), (x0 + w, ry), dxfattribs={"layer": "THIN"})
        vals = (str(r["category"]), str(r["material"]), str(r["unit"]), f"{r['quantity']}", str(r["sheet_ref"]))
        for ci, vs in enumerate(vals):
            add_civil_text(msp, vs, 0.5 * (cols[ci] + cols[ci + 1]), ry + 0.30 * row_h, height=2.05, align="CENTER")

    vbk = boq.get("volume_breakdown") or {}
    if vbk:
        add_civil_text(
            msp,
            (
                f"算量附注：1. 下部C30砼含盖梁 {vbk['pier_cap_m3']} + 双柱 {vbk['pier_col_m3']} + 桩顶系梁 {vbk['tie_beam_m3']} "
                f"+ 台帽 {vbk['abut_cap_m3']} + 耳背墙牛腿 {vbk['abut_wall_m3']} = {boq['c30_substructure_m3']} m3；C40垫石独立计列 {vbk['pedestal_m3']} m3；"
            ),
            28.0,
            34.0,
            height=1.78,
        )
        add_civil_text(
            msp,
            (
                f"2. 水下钻孔桩C30砼含桥墩桩 {vbk['pier_pile_m3']} m3 (桩长 {vbk['pier_pile_length_m']:g}m) "
                f"+ 桥台桩 {vbk['abut_pile_m3']} m3 (桩长 {vbk['abut_pile_length_m']:g}m) = {boq['c30_underwater_pile_m3']} m3。"
            ),
            28.0,
            26.0,
            height=1.82,
        )

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
    """Generate Civil/Bridge DXF + 1:1 A3 B&W Print PDF + high-DPI PNG + Reopen-DXF verification + rubric score."""
    cfg = dict(spec or {})
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    dxf_path = generate_civil_bridge_dxf(drawing_type, cfg, out_root, stem=stem)

    from cad_skill_core.mcp.industry_adapters import build_auto_style_font_map

    font_map = build_auto_style_font_map(dxf_path)
    pdf_path = out_root / f"{dxf_path.stem}.pdf"
    png_path = out_root / f"{dxf_path.stem}.png"
    if render_lock is not None:
        with render_lock:
            export_civil_a3_print_pdf_png(dxf_path, pdf_path, png_path, font_map=font_map, dpi=200)
    else:
        export_civil_a3_print_pdf_png(dxf_path, pdf_path, png_path, font_map=font_map, dpi=200)

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
        for p in (dxf_path, pdf_path, png_path, audit_json_path)
    ]
    return {
        "status": status,
        "drawing_type": drawing_type,
        "geometry_verification": geom_verify,
        "rubric_score": rubric,
        "artifacts": artifacts,
        "print_pdf_path": str(pdf_path.resolve()),
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
    template_memory_dir: str | Path | None = None,
    thesis_template: Mapping[str, Any] | str | Path | None = None,
    album_template: Mapping[str, Any] | str | Path | None = None,
    baseline_report: Mapping[str, Any] | str | Path | None = None,
    generate_figures: bool = True,
    generate_thesis_docx: bool = True,
    student_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a unified 18-sheet A3 bridge construction drawing set + calculation book + Word thesis + figures + change impact."""
    out_root = Path(output_dir).expanduser().resolve()

    from .bridge_change_impact import compute_design_change_impact, compute_dxf_semantic_digest, save_change_impact_report

    # Capture existing baseline report & semantic DXF content hashes BEFORE overwriting (if present)
    prev_calc: dict[str, Any] | None = None
    prev_dxf_hashes: dict[str, str] = {}
    if isinstance(baseline_report, Mapping):
        prev_calc = dict(baseline_report)
    elif baseline_report is not None and Path(baseline_report).expanduser().is_file():
        prev_calc = json.loads(Path(baseline_report).expanduser().read_text(encoding="utf-8-sig"))
    elif (out_root / "bridge_calculation_report.json").is_file():
        try:
            prev_calc = json.loads((out_root / "bridge_calculation_report.json").read_text(encoding="utf-8-sig"))
        except Exception:
            prev_calc = None

    prev_manifest_file = out_root / "drawing_set_manifest.json"
    if prev_manifest_file.is_file():
        try:
            prev_man = json.loads(prev_manifest_file.read_text(encoding="utf-8-sig"))
            for sh_p in prev_man.get("sheets", []):
                dno_prev = str(sh_p.get("drawing_no") or "")
                if not dno_prev:
                    continue
                if sh_p.get("dxf_content_sha256"):
                    prev_dxf_hashes[dno_prev] = str(sh_p["dxf_content_sha256"])
                elif sh_p.get("dxf_path") and Path(str(sh_p["dxf_path"])).is_file():
                    prev_dxf_hashes[dno_prev] = compute_dxf_semantic_digest(sh_p["dxf_path"])["content_sha256"]
        except Exception:
            prev_dxf_hashes = {}

    cfg = dict(params or {"preset": "wenchang"})
    if student_metadata:
        cfg["student_metadata"] = {**dict(cfg.get("student_metadata") or {}), **dict(student_metadata)}
    eff_mem_dir = template_memory_dir or cfg.get("template_memory_dir")
    if eff_mem_dir is None and preference_memory_path is not None:
        cand_mem = Path(preference_memory_path).expanduser().resolve().parent
        if (cand_mem / "album-template.json").is_file() or (cand_mem / "thesis-template.json").is_file():
            eff_mem_dir = cand_mem
    eff_album_tpl = album_template or cfg.get("album_template") or eff_mem_dir
    eff_thesis_tpl = thesis_template or cfg.get("thesis_template") or eff_mem_dir
    if eff_album_tpl is not None and "album_template" not in cfg:
        cfg["album_template"] = eff_album_tpl
    calc = run_full_bridge_calculation(cfg)
    ps = calc["project_summary"]
    lat = calc["lateral_distribution"]
    sub = calc["substructure_checks"]
    ledger = calc["rebar_and_tendon_ledger"]
    sheet_manifest = calc["sheet_manifest"]

    out_root.mkdir(parents=True, exist_ok=True)

    base_spec = {
        "project": ps["project_name"],
        "sheet_size": "A3",
        "preset": cfg.get("preset", ""),
        "_calc_report": calc,
        "preference_memory_path": str(preference_memory_path) if preference_memory_path else None,
        "album_template": eff_album_tpl,
        "style_overrides": cfg.get("style_overrides"),
    }

    n_spans = int(ps["n_spans"])
    span_cm = float(ps["standard_span_m"]) * 100.0
    deck_w_mm = float(ps["deck_width_m"]) * 1000.0
    spacing_mm = float(lat["girder_spacing_m"]) * 1000.0
    edge_cant_mm = float(lat["edge_cantilever_m"]) * 1000.0
    girder_h_mm = float(calc["t_girder_section"]["mid_span_mm"]["height"])
    pile_len_cm = float(sub["bored_pile"]["design_length_m"]) * 100.0
    abut_pile_len_cm = float(calc["abutment_checks"]["bored_pile"]["design_length_m"]) * 100.0

    # Role-specific parametric spec builders driven from a single `sheet_manifest`
    role_specs: dict[str, tuple[dict[str, Any], list[str]]] = {
        "general_notes": ({"variant": "notes", "sheet_index": sheet_manifest["index_tuples"]}, []),
        "boq_summary": ({"variant": "boq"}, []),
        "bridge_elevation": (
            {
                "n_spans": n_spans,
                "span_cm": span_cm,
                "girder_depth_cm": girder_h_mm / 10.0,
                "pile_length_cm": pile_len_cm,
                "abutment_pile_length_cm": abut_pile_len_cm,
                "deck_elevation_m": ps["deck_elevation_m"],
                "flood_elevation_m": ps["flood_elevation_m"],
            },
            [],
        ),
        "bridge_plan": (
            {
                "n_spans": n_spans,
                "span_cm": span_cm,
                "deck_width_cm": deck_w_mm / 10.0,
                "roadway_width_cm": float(ps["roadway_width_m"]) * 100.0,
            },
            [],
        ),
        "deck_cross_section": (
            {
                "n_girders": lat["n_girders"],
                "girder_spacing_mm": spacing_mm,
                "single_deck_width_mm": deck_w_mm,
                "roadway_width_mm": float(ps["roadway_width_m"]) * 1000.0,
                "lateral_clearance_mm": float(lat["lateral_clearance_m"]) * 1000.0,
                "edge_cantilever_mm": edge_cant_mm,
                "height_mm": girder_h_mm,
                "cross_slope_pct": ps["cross_slope_pct"],
                "asphalt_thick_mm": float(ps["asphalt_thick_mm"]),
                "concrete_paving_thick_mm": float(ps["concrete_paving_thick_mm"]),
            },
            [],
        ),
        "girder_longitudinal": (
            {
                "variant": "longitudinal",
                "beam_length_mm": float(ps["beam_length_m"]) * 1000.0,
                "calc_span_mm": float(ps["calc_span_m"]) * 1000.0,
                "height_mm": girder_h_mm,
                "girder_spacing_mm": spacing_mm,
            },
            [],
        ),
        "girder_cross_details": (
            {
                "variant": "cross_details",
                "height_mm": girder_h_mm,
                "girder_spacing_mm": spacing_mm,
                "edge_cantilever_mm": max(400.0, edge_cant_mm),
            },
            [],
        ),
        "tendon_layout": (
            {
                "variant": "layout",
                "height_mm": girder_h_mm,
                "flange_width_mm": spacing_mm,
                "beam_length_mm": float(ps["beam_length_m"]) * 1000.0,
            },
            [t["tendon_id"] for t in ledger["tendons"]],
        ),
        "tendon_coordinates": (
            {
                "variant": "coordinates",
                "height_mm": girder_h_mm,
                "flange_width_mm": spacing_mm,
                "beam_length_mm": float(ps["beam_length_m"]) * 1000.0,
            },
            [t["tendon_id"] for t in ledger["tendons"]]
            + [b["bar_id"] for b in ledger["rebar_groups"].get("tendon_positioning_rebar", [])],
        ),
        "girder_web_rebar": ({"component": "t_girder_web"}, [b["bar_id"] for b in ledger["rebar_groups"]["t_girder_web_rebar"]]),
        "girder_flange_rebar": ({"component": "t_girder_flange"}, [b["bar_id"] for b in ledger["rebar_groups"]["t_girder_flange_rebar"]]),
        "diaphragm_rebar": ({"component": "diaphragm"}, [b["bar_id"] for b in ledger["rebar_groups"]["diaphragm_rebar"]]),
        "bearing_layout": ({}, []),
        "deck_ancillary": ({}, [b["bar_id"] for b in ledger["rebar_groups"]["deck_ancillary_rebar"]]),
        "pier_general": (
            {
                "variant": "pier",
                "cap_length_mm": float(calc["pier_cap_beam_1d"]["cap_length_m"]) * 1000.0,
                "cap_width_mm": float(sub["cap_beam"].get("width_m", 1.60)) * 1000.0,
                "cap_height_mm": float(sub["cap_beam"].get("height_m", 1.40)) * 1000.0,
                "column_spacing_mm": round((calc["pier_cap_beam_1d"]["column_positions_m"][-1] - calc["pier_cap_beam_1d"]["column_positions_m"][0]) * 1000.0, 1),
                "column_diameter_mm": float(sub["pier_column"]["diameter_m"]) * 1000.0,
                "column_height_mm": float(sub["pier_column"]["height_m"]) * 1000.0,
                "pile_diameter_mm": float(sub["bored_pile"]["diameter_m"]) * 1000.0,
                "pedestal_spacing_mm": spacing_mm,
                "n_pedestals": lat["n_girders"],
                "bearing_model": calc["bearing_checks"]["bearing_model"],
            },
            [],
        ),
        "abutment_general": (
            {
                "variant": "abutment",
                "cap_length_mm": deck_w_mm,
                "column_spacing_mm": round(max(3600.0, deck_w_mm - 3600.0), 1),
                "pile_diameter_mm": float(calc["abutment_checks"]["bored_pile"]["diameter_m"]) * 1000.0,
                "abutment_pile_length_mm": abut_pile_len_cm * 10.0,
                "pedestal_spacing_mm": spacing_mm,
                "n_pedestals": lat["n_girders"],
                "bearing_model": calc["bearing_checks"]["bearing_model"],
            },
            [],
        ),
        "pier_cap_rebar": ({"component": "pier_cap"}, [b["bar_id"] for b in ledger["rebar_groups"]["pier_cap_rebar"]]),
        "pier_cap_secondary": ({"component": "pier_cap_secondary"}, [b["bar_id"] for b in ledger["rebar_groups"].get("pier_cap_secondary_rebar", [])]),
        "pier_column_pile_rebar": ({"component": "pier_column_pile"}, [b["bar_id"] for b in ledger["rebar_groups"]["pier_column_pile_rebar"]]),
        "tie_beam_joint_rebar": (
            {"component": "tie_beam_joint"},
            [b["bar_id"] for b in ledger["rebar_groups"]["pier_column_pile_rebar"] if b["bar_id"] in {"XL1", "XL2"}],
        ),
        "abutment_rebar": ({"component": "abutment_rebar"}, [b["bar_id"] for b in ledger["rebar_groups"]["abutment_rebar"]]),
        "abutment_ear_backwall_rebar": (
            {"component": "abutment_ear_backwall"},
            [b["bar_id"] for b in ledger["rebar_groups"].get("abutment_ear_backwall_rebar", [])],
        ),
    }

    generated_sheets: list[dict[str, Any]] = []
    for entry in sheet_manifest["entries"]:
        dno = str(entry["drawing_no"])
        role_k = str(entry["role_key"])
        cat_id = entry.get("category_id")
        dtype = str(entry["drawing_type"])
        role_sub, role_refs = role_specs.get(role_k, (dict(entry.get("sub_spec") or {}), []))
        merged_sub = {**role_sub, **dict(entry.get("sub_spec") or {})}
        if role_k == "general_notes":
            merged_sub["sheet_index"] = sheet_manifest["index_tuples"]
        resolved_title = str(entry["title"])
        merged_spec = {**base_spec, **merged_sub, "drawing_no": dno, "title": resolved_title, "referenced_ids": role_refs}
        stem = f"{dno}_{dtype}"
        sheet_dir = out_root / dno
        if render_png:
            bundle = generate_civil_bridge_bundle(dtype, merged_spec, sheet_dir, stem=stem)  # type: ignore[arg-type]
            ver = bundle["geometry_verification"]
            score = bundle["rubric_score"]["total_score"]
            dxf_file = sheet_dir / f"{stem}.dxf"
            pdf_file: Path | None = sheet_dir / f"{stem}.pdf"
            png_file: Path | None = sheet_dir / f"{stem}.png"
        else:
            dxf_file = generate_civil_bridge_dxf(dtype, merged_spec, sheet_dir, stem=stem)  # type: ignore[arg-type]
            ver = verify_civil_dxf(dxf_file, calc_report=calc, expected_spec=merged_spec)
            score = score_dxf_drawing(dxf_file).total_score
            pdf_file = None
            png_file = None

        dxf_hash = sha256_file(dxf_file)
        sem_digest = compute_dxf_semantic_digest(dxf_file)
        generated_sheets.append(
            {
                "drawing_no": dno,
                "role_key": role_k,
                "category_id": cat_id,
                "drawing_type": dtype,
                "title": resolved_title,
                "scale_label": entry["scale_label"],
                "sheet_size": "A3",
                "dxf_path": str(dxf_file),
                "dxf_sha256": dxf_hash,
                "dxf_content_sha256": sem_digest["content_sha256"],
                "dxf_section_digests": sem_digest["sections"],
                "pdf_path": str(pdf_file) if pdf_file else None,
                "png_path": str(png_file) if png_file else None,
                "verify_status": ver["status"],
                "verify_issues": ver["issues"],
                "rubric_score": score,
                "referenced_ids": role_refs,
            }
        )

    from .bridge_calc_book import generate_bridge_calculation_book_markdown
    from .bridge_fem_solver import generate_drbridge_modeling_guide
    from .bridge_figures import generate_bridge_calculation_figures
    from .bridge_thesis_docx import generate_college_graduation_thesis_docx

    print_album_pdf_path: str | None = None
    sheet_pdfs = [(sh["drawing_no"], sh["title"], Path(str(sh["pdf_path"]))) for sh in generated_sheets if sh.get("pdf_path") and Path(str(sh["pdf_path"])).is_file()]
    if render_png and sheet_pdfs:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        page_idx = 0
        for dno_p, title_p, spdf in sheet_pdfs:
            reader = PdfReader(str(spdf))
            first_pg_of_sheet = page_idx
            for pg in reader.pages:
                writer.add_page(pg)
                page_idx += 1
            writer.add_outline_item(f"{dno_p} {title_p}", first_pg_of_sheet)
        first_id = generated_sheets[0]["drawing_no"]
        last_id = generated_sheets[-1]["drawing_no"]
        album_file = out_root / f"{first_id}_to_{last_id}_A3_print_album.pdf"
        with album_file.open("wb") as fh:
            writer.write(fh)
        print_album_pdf_path = str(album_file)

    drbridge_guide = generate_drbridge_modeling_guide(calc, out_root)

    new_dxf_hashes = {sh["drawing_no"]: sh["dxf_content_sha256"] for sh in generated_sheets}
    change_impact = compute_design_change_impact(
        prev_calc,
        calc,
        before_dxf_hashes=prev_dxf_hashes if prev_calc is not None else None,
        after_dxf_hashes=new_dxf_hashes if prev_calc is not None else None,
    )
    change_impact_path = out_root / "change_impact_report.json"
    save_change_impact_report(change_impact, change_impact_path)

    figures_summary: dict[str, Any] | None = None
    if generate_figures:
        figures_summary = generate_bridge_calculation_figures(calc, out_root / "figures")

    coverage = audit_task_book_coverage(generated_sheets, calc)
    calc_out_path = out_root / "bridge_calculation_report.json"
    calc_out_path.write_text(json.dumps(calc, ensure_ascii=False, indent=2), encoding="utf-8")
    calc_book_path = out_root / "bridge_calculation_book.md"
    generate_bridge_calculation_book_markdown(
        calc,
        output_path=calc_book_path,
        figures_summary=figures_summary,
        change_impact=change_impact,
    )

    thesis_summary: dict[str, Any] | None = None
    if generate_thesis_docx:
        resolved_meta = dict(student_metadata or cfg.get("student_metadata") or {})
        thesis_summary = generate_college_graduation_thesis_docx(
            calc,
            out_root / "graduation_thesis.docx",
            figures_summary=figures_summary,
            change_impact=change_impact,
            student_metadata=resolved_meta,
            thesis_template=eff_thesis_tpl,
        )

    failed_checks = list(calc.get("failed_checks", []))
    failed_drawings = sorted(
        {d for fc in failed_checks for d in fc.get("related_drawings", [])}
        | set(coverage.get("failed_sheets", []))
    )
    overall_passed = (
        calc.get("calculation_status", calc.get("status", "passed")) == "passed"
        and coverage["status"] == "passed"
        and calc["consistency_audit"]["status"] == "passed"
        and change_impact["internal_consistency"]["status"] == "passed"
        and not failed_checks
        and not failed_drawings
    )
    calc_status_final = "passed" if overall_passed else "failed"
    grad_status_final = (
        "failed"
        if not overall_passed
        else str(calc.get("graduation_readiness_status", "pending_confirmation"))
    )

    manifest_out_path = out_root / "drawing_set_manifest.json"
    manifest_payload = {
        "status": calc_status_final,
        "calculation_status": calc_status_final,
        "graduation_readiness_status": grad_status_final,
        "status_summary_zh": calc.get("status_summary_zh", ""),
        "project_summary": ps,
        "failed_checks": failed_checks,
        "failed_drawings": failed_drawings,
        "unresolved_assumptions": calc["unresolved_assumptions"],
        "assumption_dependent_conclusions": calc.get("assumption_dependent_conclusions", []),
        "pending_graduation_deliverables": calc.get("pending_graduation_deliverables", []),
        "seismic_verification": calc.get("seismic_verification"),
        "independent_cross_check": calc.get("independent_cross_check"),
        "defense_verification_table": calc.get("defense_verification_table"),
        "references_audit": calc.get("references_audit"),
        "change_impact": change_impact,
        "calculation_figures": figures_summary,
        "graduation_thesis": thesis_summary,
        "sheet_manifest": sheet_manifest,
        "support_layout": calc.get("support_layout"),
        "tendon_clearance_audit": calc.get("tendon_clearance_audit"),
        "coverage_audit": coverage,
        "sheets": generated_sheets,
        "print_album_pdf_path": print_album_pdf_path,
        "merged_pdf_album_path": print_album_pdf_path,
        "drbridge_modeling_guide_path": drbridge_guide.get("guide_markdown_path"),
        "drbridge_import_template_path": drbridge_guide.get("template_json_path"),
        "calculation_report_path": str(calc_out_path),
        "calculation_book_path": str(calc_book_path),
        "graduation_thesis_docx_path": thesis_summary["docx_path"] if thesis_summary else None,
        "change_impact_report_path": str(change_impact_path),
    }
    manifest_out_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest_payload, "calculation_report": calc, "manifest_path": str(manifest_out_path)}
