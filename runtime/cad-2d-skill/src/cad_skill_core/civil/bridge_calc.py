"""Zero-dependency deterministic Civil & Bridge Engineering calculation & QA engine.

Implements traceable Chinese Highway Bridge Code formulas (JTG D60-2015 / JTG 3362-2018):
1. Hydrologic hydraulic radius, Manning velocity, discharge, and net bridge opening.
2. Exact polygonal cross-section properties (Green's theorem) + T-girder effective flange
   width b'_f and section efficiency index rho = (K_s + K_x) / h >= 0.5.
3. Lateral load distribution influence lines & factors:
   - Lever Rule (杠杆原理法) at supports;
   - Courbon's Eccentric Compression / Rigid Crossbeam Method (偏心压力法) at mid-span.
4. Highway-I / Highway-II lane load interpolation, fundamental frequency & impact factor
   (1 + mu), 10-element (L..KZ..R) dead/live M & Q envelopes, and ULS/SLS combinations.
5. Prestressing strand/tendon estimation (SLS decompression & ULS flexural capacity) and
   parabolic-tangent friction + anchorage draw-in prestress loss tables.
6. Pure-Python 1D Euler-Bernoulli continuous overhanging cap-beam stiffness solver.
7. Cross-document / cross-drawing consistency & limit-state table QA auditor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
import io
import math
from pathlib import Path
import re
from typing import Any, Literal


def compute_hydrology_and_span(
    *,
    area_m2: float,
    wetted_perimeter_m: float,
    roughness_inv: float | None = None,
    manning_n: float | None = None,
    slope_permille: float = 0.5,
    floodplain_area_m2: float = 0.0,
    floodplain_velocity_m_s: float = 0.0,
    channel_width_m: float = 100.0,
    design_discharge_m3_s: float | None = None,
    stability_k: float = 0.90,
    stability_exp_n: float = 0.84,
    standard_span_m: float = 25.0,
) -> dict[str, Any]:
    """Compute river hydraulic velocity, discharge, net waterway opening, and span layout."""

    if area_m2 <= 0 or wetted_perimeter_m <= 0:
        raise ValueError("area_m2 and wetted_perimeter_m must be > 0")
    if roughness_inv is None and manning_n is None:
        roughness_inv = 22.22
    if roughness_inv is None:
        assert manning_n is not None
        if manning_n <= 0:
            raise ValueError("manning_n must be > 0")
        inv_n = 1.0 / manning_n
    else:
        if roughness_inv <= 0:
            raise ValueError("roughness_inv must be > 0")
        inv_n = float(roughness_inv)

    if slope_permille <= 0:
        raise ValueError("slope_permille must be > 0")
    slope_i = slope_permille / 1000.0

    hydraulic_radius_m = area_m2 / wetted_perimeter_m
    velocity_m_s = inv_n * (hydraulic_radius_m ** (2.0 / 3.0)) * math.sqrt(slope_i)
    channel_discharge_q_c = area_m2 * velocity_m_s
    total_discharge_q = channel_discharge_q_c + floodplain_area_m2 * floodplain_velocity_m_s
    if design_discharge_m3_s is not None and design_discharge_m3_s <= 0:
        raise ValueError("design_discharge_m3_s must be > 0")
    q_s = float(design_discharge_m3_s) if design_discharge_m3_s is not None else total_discharge_q

    net_opening_l_j = stability_k * ((q_s / channel_discharge_q_c) ** stability_exp_n) * channel_width_m
    recommended_spans = max(1, math.ceil(net_opening_l_j / standard_span_m))
    total_bridge_length_m = recommended_spans * standard_span_m

    return {
        "area_m2": round(area_m2, 3),
        "wetted_perimeter_m": round(wetted_perimeter_m, 3),
        "hydraulic_radius_m": round(hydraulic_radius_m, 4),
        "roughness_inv": round(inv_n, 4),
        "slope_i": slope_i,
        "main_channel_velocity_m_s": round(velocity_m_s, 4),
        "channel_discharge_m3_s": round(channel_discharge_q_c, 3),
        "natural_total_discharge_m3_s": round(total_discharge_q, 3),
        "design_discharge_m3_s": round(q_s, 3),
        "net_opening_length_m": round(net_opening_l_j, 3),
        "standard_span_m": round(standard_span_m, 3),
        "recommended_spans": recommended_spans,
        "recommended_layout": f"{recommended_spans}x{standard_span_m:g}m",
        "total_bridge_length_m": round(total_bridge_length_m, 3),
    }


def compute_polygon_section_properties(vertices: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Compute exact area, centroid, second moments, kern distances, and efficiency for a 2D polygon.

    Vertices may be supplied in clockwise or counter-clockwise order (closed or unclosed).
    Coordinate convention: +y points upward from the bottom fiber.
    """

    pts = [(float(p[0]), float(p[1])) for p in vertices]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("polygon requires at least 3 distinct vertices")

    signed_area2 = 0.0
    cx6 = 0.0
    cy6 = 0.0
    ix_origin12 = 0.0
    iy_origin12 = 0.0
    ixy_origin24 = 0.0

    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        signed_area2 += cross
        cx6 += (x0 + x1) * cross
        cy6 += (y0 + y1) * cross
        ix_origin12 += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iy_origin12 += (x0 * x0 + x0 * x1 + x1 * x1) * cross
        ixy_origin24 += (x0 * y1 + 2.0 * x0 * y0 + 2.0 * x1 * y1 + x1 * y0) * cross

    if abs(signed_area2) < 1e-9:
        raise ValueError("degenerate zero-area section polygon")

    if signed_area2 < 0:
        signed_area2 = -signed_area2
        cx6 = -cx6
        cy6 = -cy6
        ix_origin12 = -ix_origin12
        iy_origin12 = -iy_origin12
        ixy_origin24 = -ixy_origin24

    area = 0.5 * signed_area2
    xc = cx6 / (6.0 * area)
    yc = cy6 / (6.0 * area)
    ix_origin = ix_origin12 / 12.0
    iy_origin = iy_origin12 / 12.0
    ixy_origin = ixy_origin24 / 24.0

    ix_c = ix_origin - area * yc * yc
    iy_c = iy_origin - area * xc * xc
    ixy_c = ixy_origin - area * xc * yc

    ys = [p[1] for p in pts]
    xs = [p[0] for p in pts]
    y_min, y_max = min(ys), max(ys)
    x_min, x_max = min(xs), max(xs)
    total_h = y_max - y_min
    total_w = x_max - x_min

    # Distance from centroid to top fiber (y_s) and bottom fiber (y_x)
    y_top = y_max - yc
    y_bot = yc - y_min
    w_top = ix_c / y_top if y_top > 1e-9 else 0.0
    w_bot = ix_c / y_bot if y_bot > 1e-9 else 0.0

    # Upper kern distance K_s = I_x / (A * y_bot), lower kern distance K_x = I_x / (A * y_top)
    k_upper = ix_c / (area * y_bot) if y_bot > 1e-9 else 0.0
    k_lower = ix_c / (area * y_top) if y_top > 1e-9 else 0.0
    rho = (k_upper + k_lower) / total_h if total_h > 1e-9 else 0.0

    return {
        "area": round(area, 4),
        "xc": round(xc, 4),
        "yc": round(yc, 4),
        "ix": round(ix_c, 4),
        "iy": round(iy_c, 4),
        "ixy": round(ixy_c, 4),
        "height": round(total_h, 4),
        "width": round(total_w, 4),
        "y_top": round(y_top, 4),
        "y_bot": round(y_bot, 4),
        "w_top": round(w_top, 4),
        "w_bot": round(w_bot, 4),
        "kern_upper_ks": round(k_upper, 4),
        "kern_lower_kx": round(k_lower, 4),
        "efficiency_rho": round(rho, 4),
        "passes_efficiency": bool(rho >= 0.5),
    }


def build_t_girder_polygon_mm(
    *,
    flange_width_mm: float = 2348.0,
    height_mm: float = 1700.0,
    flange_tip_thick_mm: float = 200.0,
    haunch_width_mm: float = 210.0,
    haunch_height_mm: float = 70.0,
    web_thick_mm: float = 200.0,
    bulb_width_mm: float = 600.0,
    bulb_height_mm: float = 200.0,
    bulb_chamfer_w_mm: float = 200.0,
    bulb_chamfer_h_mm: float = 200.0,
) -> list[tuple[float, float]]:
    """Construct counter-clockwise vertices (mm) for a symmetric T-girder with optional bottom bulb."""

    half_bf = flange_width_mm / 2.0
    half_web = web_thick_mm / 2.0
    half_bulb = max(half_web, bulb_width_mm / 2.0)

    y_top = height_mm
    y_tip_bot = height_mm - flange_tip_thick_mm
    y_root_bot = y_tip_bot - haunch_height_mm
    x_root = half_web + haunch_width_mm

    if bulb_width_mm <= web_thick_mm + 1e-6 or bulb_height_mm <= 0:
        # Rectangular web (e.g. thickened girder-end section)
        pts = [
            (-half_web, 0.0),
            (half_web, 0.0),
            (half_web, y_root_bot),
            (x_root, y_tip_bot),
            (half_bf, y_tip_bot),
            (half_bf, y_top),
            (-half_bf, y_top),
            (-half_bf, y_tip_bot),
            (-x_root, y_tip_bot),
            (-half_web, y_root_bot),
        ]
    else:
        y_bulb_top = bulb_height_mm
        y_bulb_chamfer = bulb_height_mm + bulb_chamfer_h_mm
        pts = [
            (-half_bulb, 0.0),
            (half_bulb, 0.0),
            (half_bulb, y_bulb_top),
            (half_web, y_bulb_chamfer),
            (half_web, y_root_bot),
            (x_root, y_tip_bot),
            (half_bf, y_tip_bot),
            (half_bf, y_top),
            (-half_bf, y_top),
            (-half_bf, y_tip_bot),
            (-x_root, y_tip_bot),
            (-half_web, y_root_bot),
            (-half_web, y_bulb_chamfer),
            (-half_bulb, y_bulb_top),
        ]
    # Deduplicate consecutive identical points
    cleaned: list[tuple[float, float]] = []
    for pt in pts:
        if not cleaned or math.hypot(pt[0] - cleaned[-1][0], pt[1] - cleaned[-1][1]) > 1e-6:
            cleaned.append(pt)
    return cleaned


def compute_t_girder_section(
    *,
    calc_span_mm: float = 24500.0,
    girder_spacing_mm: float = 2348.0,
    flange_width_mm: float = 2348.0,
    height_mm: float = 1700.0,
    flange_tip_thick_mm: float = 200.0,
    haunch_width_mm: float = 210.0,
    haunch_height_mm: float = 70.0,
    flange_avg_cantilever_thick_mm: float | None = None,
    web_thick_mm: float = 200.0,
    bulb_width_mm: float = 600.0,
    bulb_height_mm: float = 200.0,
    bulb_chamfer_w_mm: float = 200.0,
    bulb_chamfer_h_mm: float = 200.0,
    end_web_thick_mm: float = 600.0,
) -> dict[str, Any]:
    """Compute JTG 3362-2018 effective compression flange width b'_f and mid/end T-girder properties."""

    h_f_prime = (
        flange_avg_cantilever_thick_mm
        if flange_avg_cantilever_thick_mm is not None
        else flange_tip_thick_mm + 0.5 * haunch_height_mm
    )
    cand_span = calc_span_mm / 3.0
    cand_spacing = girder_spacing_mm
    cand_overhang = web_thick_mm + 2.0 * haunch_width_mm + 12.0 * h_f_prime
    b_f_effective_mm = min(cand_span, cand_spacing, cand_overhang)

    mid_pts = build_t_girder_polygon_mm(
        flange_width_mm=b_f_effective_mm,
        height_mm=height_mm,
        flange_tip_thick_mm=flange_tip_thick_mm,
        haunch_width_mm=haunch_width_mm,
        haunch_height_mm=haunch_height_mm,
        web_thick_mm=web_thick_mm,
        bulb_width_mm=bulb_width_mm,
        bulb_height_mm=bulb_height_mm,
        bulb_chamfer_w_mm=bulb_chamfer_w_mm,
        bulb_chamfer_h_mm=bulb_chamfer_h_mm,
    )
    end_pts = build_t_girder_polygon_mm(
        flange_width_mm=b_f_effective_mm,
        height_mm=height_mm,
        flange_tip_thick_mm=flange_tip_thick_mm,
        haunch_width_mm=max(0.0, haunch_width_mm - 0.5 * (end_web_thick_mm - web_thick_mm)),
        haunch_height_mm=haunch_height_mm,
        web_thick_mm=end_web_thick_mm,
        bulb_width_mm=end_web_thick_mm,
        bulb_height_mm=0.0,
    )
    mid_props = compute_polygon_section_properties(mid_pts)
    end_props = compute_polygon_section_properties(end_pts)

    return {
        "effective_flange_width_mm": round(b_f_effective_mm, 3),
        "effective_flange_candidates_mm": {
            "l_div_3": round(cand_span, 3),
            "girder_spacing": round(cand_spacing, 3),
            "b_plus_2bh_plus_12hf": round(cand_overhang, 3),
        },
        "mid_span_mm": mid_props,
        "end_span_mm": end_props,
        "mid_span_m": {
            "area_m2": round(mid_props["area"] * 1e-6, 5),
            "ix_m4": round(mid_props["ix"] * 1e-12, 6),
            "y_top_m": round(mid_props["y_top"] * 1e-3, 4),
            "y_bot_m": round(mid_props["y_bot"] * 1e-3, 4),
            "efficiency_rho": mid_props["efficiency_rho"],
        },
        "mid_span_polygon_mm": mid_pts,
        "end_span_polygon_mm": end_pts,
    }


def _multi_lane_reduction(n_lanes: int) -> float:
    """JTG D60-2015 Table 4.3.1-4 transverse multi-lane reduction factor xi."""

    if n_lanes <= 2:
        return 1.00
    if n_lanes == 3:
        return 0.78
    if n_lanes == 4:
        return 0.67
    if n_lanes == 5:
        return 0.60
    if n_lanes == 6:
        return 0.55
    if n_lanes == 7:
        return 0.52
    return 0.50


def _maximize_vehicle_wheels_on_influence_line(
    eval_eta,
    x_road_left: float,
    x_road_right: float,
    max_lanes: int,
    wheel_gauge_m: float = 1.80,
    curb_clearance_m: float = 0.50,
    adjacent_vehicle_wheel_gap_m: float = 1.30,
) -> tuple[float, list[float], int]:
    """Find worst-case transverse vehicle wheel placement maximizing 0.5 * xi * sum(eta(x_wheel))."""

    x_min_w = x_road_left + curb_clearance_m
    x_max_w = x_road_right - curb_clearance_m
    avail = x_max_w - x_min_w
    if avail < wheel_gauge_m - 1e-6:
        return 0.0, [], 0

    best_m = -1e9
    best_wheels: list[float] = []
    best_lanes = 0

    # Test placing 1..max_lanes vehicles packed near left curb, right curb, or centered on peak
    for k_lanes in range(1, max_lanes + 1):
        span_needed = k_lanes * wheel_gauge_m + (k_lanes - 1) * adjacent_vehicle_wheel_gap_m
        if span_needed > avail + 1e-6:
            break
        xi = _multi_lane_reduction(k_lanes)
        slack = max(0.0, avail - span_needed)
        steps = max(1, int(round(slack / 0.02)) + 1)
        for s_idx in range(steps + 1):
            offset = (slack * s_idx / steps) if steps > 0 else 0.0
            wheels: list[float] = []
            cur = x_min_w + offset
            for v_i in range(k_lanes):
                if v_i > 0:
                    cur += adjacent_vehicle_wheel_gap_m
                wheels.append(cur)
                wheels.append(cur + wheel_gauge_m)
                cur += wheel_gauge_m
            eta_sum = sum(eval_eta(wx) for wx in wheels)
            m_val = 0.5 * xi * eta_sum
            if m_val > best_m:
                best_m = m_val
                best_wheels = [round(w, 4) for w in wheels]
                best_lanes = k_lanes

    return max(0.0, best_m), best_wheels, best_lanes


def compute_lateral_distribution(
    *,
    n_girders: int = 5,
    girder_spacing_m: float = 2.348,
    deck_width_m: float = 11.75,
    left_barrier_m: float = 0.50,
    right_barrier_m: float = 0.50,
    lateral_clearance_m: float = 0.0,
    edge_cantilever_m: float | None = None,
    calc_span_m: float = 24.50,
    design_lanes: int = 2,
    it_to_ix_ratio: float = 0.055,
) -> dict[str, Any]:
    """Compute Lever Rule (m_oq), Courbon Eccentric Compression (m_cq), and Torsion-Corrected (G-M) factors."""

    if n_girders < 2:
        raise ValueError("n_girders must be >= 2")
    if girder_spacing_m <= 0 or deck_width_m <= 0:
        raise ValueError("girder_spacing_m and deck_width_m must be > 0")

    # Transverse coordinate x measured from bridge/carriageway centerline [-B/2, +B/2]
    half_deck = deck_width_m / 2.0
    x_clear_left = -half_deck + left_barrier_m
    x_clear_right = half_deck - right_barrier_m
    clear_width_m = x_clear_right - x_clear_left
    roadway_width_m = clear_width_m - 2.0 * lateral_clearance_m
    inferred_cantilever_m = (
        float(edge_cantilever_m)
        if edge_cantilever_m is not None
        else 0.5 * (deck_width_m - (n_girders - 1) * girder_spacing_m)
    )

    # Girder coordinates: girder 1 at leftmost (-), girder n at rightmost (+)
    girder_x = [((i - 0.5 * (n_girders - 1)) * girder_spacing_m) for i in range(n_girders)]
    # In Courbon notation a_1 > 0 for girder 1: a_i = -girder_x[i]
    a_offsets = [-gx for gx in girder_x]
    sum_a2 = sum(a * a for a in a_offsets)

    # Torsional stiffness correction beta = 1 / (1 + gamma) for rigid-jointed T-girders
    b_eff = n_girders * girder_spacing_m
    gamma_torsion = 5.8 * it_to_ix_ratio * ((b_eff / max(1.0, calc_span_m)) ** 2)
    beta_torsion = 1.0 / (1.0 + gamma_torsion)

    lever_results: list[dict[str, Any]] = []
    courbon_results: list[dict[str, Any]] = []

    sample_xs = [-half_deck + (deck_width_m * k / 48.0) for k in range(49)]

    for idx in range(n_girders):
        g_no = idx + 1

        # 1. Lever Rule influence line eta_lever(x)
        def _lever_eta(x_pos: float, g_i: int = idx) -> float:
            gx = girder_x[g_i]
            if g_i == 0:
                gx_next = girder_x[1]
                if x_pos <= gx_next:
                    return (gx_next - x_pos) / (gx_next - gx)
                return 0.0
            if g_i == n_girders - 1:
                gx_prev = girder_x[n_girders - 2]
                if x_pos >= gx_prev:
                    return (x_pos - gx_prev) / (gx - gx_prev)
                return 0.0
            gx_prev = girder_x[g_i - 1]
            gx_next = girder_x[g_i + 1]
            if gx_prev <= x_pos <= gx:
                return (x_pos - gx_prev) / (gx - gx_prev)
            if gx < x_pos <= gx_next:
                return (gx_next - x_pos) / (gx_next - gx)
            return 0.0

        m_oq, wheels_oq, lanes_oq = _maximize_vehicle_wheels_on_influence_line(
            _lever_eta, x_clear_left, x_clear_right, design_lanes
        )
        lever_results.append(
            {
                "girder_no": g_no,
                "x_m": round(girder_x[idx], 4),
                "m_oq": round(m_oq, 4),
                "governing_lanes": lanes_oq,
                "wheel_positions_m": wheels_oq,
                "influence_line": [[round(xv, 4), round(_lever_eta(xv), 4)] for xv in sample_xs],
            }
        )

        # 2. Courbon Eccentric Compression influence line: eta_i(x) = 1/n + (a_i * (-x)) / sum(a^2)
        ai = a_offsets[idx]

        def _courbon_eta(x_pos: float, a_val: float = ai) -> float:
            return (1.0 / n_girders) + (a_val * (-x_pos)) / sum_a2

        def _mod_courbon_eta(x_pos: float, a_val: float = ai) -> float:
            return (1.0 / n_girders) + beta_torsion * (a_val * (-x_pos)) / sum_a2

        eta_at_g1 = _courbon_eta(girder_x[0])
        eta_at_gn = _courbon_eta(girder_x[-1])
        m_cq, wheels_cq, lanes_cq = _maximize_vehicle_wheels_on_influence_line(
            _courbon_eta, x_clear_left, x_clear_right, design_lanes
        )
        m_cq_mod, _, _ = _maximize_vehicle_wheels_on_influence_line(
            _mod_courbon_eta, x_clear_left, x_clear_right, design_lanes
        )
        courbon_results.append(
            {
                "girder_no": g_no,
                "a_m": round(ai, 4),
                "eta_g1": round(eta_at_g1, 4),
                "eta_gn": round(eta_at_gn, 4),
                "m_cq": round(m_cq, 4),
                "m_cq_torsion_corrected": round(m_cq_mod, 4),
                "governing_lanes": lanes_cq,
                "wheel_positions_m": wheels_cq,
                "influence_line": [[round(xv, 4), round(_courbon_eta(xv), 4)] for xv in sample_xs],
            }
        )

    total_girder_span_w = n_girders * girder_spacing_m
    width_span_ratio = total_girder_span_w / calc_span_m if calc_span_m > 0 else 0.0

    return {
        "n_girders": n_girders,
        "girder_spacing_m": round(girder_spacing_m, 4),
        "edge_cantilever_m": round(inferred_cantilever_m, 4),
        "deck_width_m": round(deck_width_m, 4),
        "clear_width_m": round(clear_width_m, 4),
        "lateral_clearance_m": round(lateral_clearance_m, 4),
        "roadway_width_m": round(roadway_width_m, 4),
        "sum_a2_m2": round(sum_a2, 4),
        "torsion_beta": round(beta_torsion, 4),
        "width_to_span_ratio": round(width_span_ratio, 4),
        "eccentric_compression_applicable": bool(width_span_ratio <= 0.5),
        "lever_rule": lever_results,
        "eccentric_compression": courbon_results,
    }


def _highway_lane_load(
    calc_span_m: float,
    load_grade: Literal["highway-I", "highway-II"] = "highway-I",
    code_edition: Literal["JTG-D60-2015", "JTG-D60-2004"] = "JTG-D60-2015",
) -> tuple[float, float]:
    """Return (q_k in kN/m, P_k in kN) for Highway-I or Highway-II lane load."""

    q_k = 10.5
    if code_edition == "JTG-D60-2004":
        if calc_span_m <= 5.0:
            p_k = 180.0
        elif calc_span_m >= 50.0:
            p_k = 360.0
        else:
            p_k = 180.0 + 4.0 * (calc_span_m - 5.0)
    else:
        if calc_span_m <= 5.0:
            p_k = 270.0
        elif calc_span_m >= 50.0:
            p_k = 360.0
        else:
            p_k = 270.0 + 2.0 * (calc_span_m - 5.0)

    if load_grade == "highway-II":
        q_k *= 0.75
        p_k *= 0.75
    return q_k, p_k


def compute_impact_factor_jtg(
    calc_span_m: float,
    ec_mpa: float,
    ic_m4: float,
    mass_per_m_kg: float,
) -> tuple[float, float]:
    """Compute simply-supported beam fundamental frequency f (Hz) and JTG D60-2015 mu."""

    if calc_span_m <= 0 or ec_mpa <= 0 or ic_m4 <= 0 or mass_per_m_kg <= 0:
        raise ValueError("span, E_c, I_c, and mass_per_m must be > 0")
    ei_nm2 = (ec_mpa * 1e6) * ic_m4
    freq_hz = (math.pi / (2.0 * calc_span_m * calc_span_m)) * math.sqrt(ei_nm2 / mass_per_m_kg)
    if freq_hz < 1.5:
        mu = 0.05
    elif freq_hz > 14.0:
        mu = 0.45
    else:
        mu = 0.1767 * math.log(freq_hz) - 0.0157
    return round(freq_hz, 4), round(mu, 4)


def compute_superstructure_envelopes(
    *,
    calc_span_m: float = 24.0,
    section_area_m2: float = 0.9196,
    section_ix_m4: float = 0.32366,
    concrete_ec_mpa: float = 34500.0,
    concrete_unit_weight_kn_m3: float = 26.0,
    diaphragm_equiv_kn_m: float = 3.20,
    superimposed_dead_kn_m: float = 11.20,
    m_oq: float = 0.884,
    m_cq: float = 0.789,
    load_grade: Literal["highway-I", "highway-II"] = "highway-I",
    code_edition: Literal["JTG-D60-2015", "JTG-D60-2004"] = "JTG-D60-2015",
    safety_factor_gamma0: float = 1.1,
) -> dict[str, Any]:
    """Compute 10-element dead + live M/Q envelopes and ULS/SLS load combinations."""

    g1_beam = section_area_m2 * concrete_unit_weight_kn_m3
    g_total = g1_beam + diaphragm_equiv_kn_m + superimposed_dead_kn_m
    mass_per_m_kg = (g1_beam + diaphragm_equiv_kn_m) * 1000.0 / 9.81

    freq_hz, mu = compute_impact_factor_jtg(calc_span_m, concrete_ec_mpa, section_ix_m4, mass_per_m_kg)
    q_k, p_k = _highway_lane_load(calc_span_m, load_grade=load_grade, code_edition=code_edition)

    station_labels = ["L", "Z1", "B1", "J1", "Z2", "KZ", "Z4", "J2", "B2", "Z5", "R"]
    stations: list[dict[str, Any]] = []

    for i, label in enumerate(station_labels):
        xi = i / 10.0
        x_m = xi * calc_span_m
        # Dead load simply supported M_g(x) and Q_g(x)
        m_dead = 0.5 * g_total * x_m * (calc_span_m - x_m)
        q_dead = g_total * (0.5 * calc_span_m - x_m)

        # Lateral distribution factor transitions from m_oq at support to m_cq over [0, L/4]
        dist_end = min(x_m, calc_span_m - x_m)
        quarter = 0.25 * calc_span_m
        if dist_end >= quarter or quarter <= 1e-9:
            m_lat_q = m_cq
        else:
            # Effective weighted transition for shear near support
            m_lat_q = m_oq - (m_oq - m_cq) * (dist_end / quarter) * 0.5

        # Bending moment influence line area omega_M = 0.5 * x * (L - x), peak ordinate y_M = x*(L-x)/L
        omega_m = 0.5 * x_m * (calc_span_m - x_m)
        y_m_peak = (x_m * (calc_span_m - x_m)) / calc_span_m
        m_live = (1.0 + mu) * m_cq * (q_k * omega_m + p_k * y_m_peak)

        # Shear positive/negative influence line areas; JTG D60 §4.3.1 multiplies P_k by 1.2 for shear!
        len_pos = calc_span_m - x_m
        y_q_pos = len_pos / calc_span_m
        omega_q_pos = 0.5 * len_pos * y_q_pos
        q_live_pos = (1.0 + mu) * m_lat_q * (q_k * omega_q_pos + 1.2 * p_k * y_q_pos)

        len_neg = x_m
        y_q_neg = len_neg / calc_span_m
        omega_q_neg = 0.5 * len_neg * y_q_neg
        q_live_neg = -(1.0 + mu) * m_lat_q * (q_k * omega_q_neg + 1.2 * p_k * y_q_neg)

        q_live_abs_max = q_live_pos if abs(q_dead + q_live_pos) >= abs(q_dead + q_live_neg) else q_live_neg

        m_sls_char = m_dead + m_live
        m_sls_freq = m_dead + 0.7 * m_live
        m_sls_quasi = m_dead + 0.4 * m_live
        m_uls = safety_factor_gamma0 * (1.2 * m_dead + 1.4 * m_live)

        q_uls_max = safety_factor_gamma0 * (1.2 * q_dead + 1.4 * q_live_pos)
        q_uls_min = safety_factor_gamma0 * (1.2 * q_dead + 1.4 * q_live_neg)
        q_uls_gov = q_uls_max if abs(q_uls_max) >= abs(q_uls_min) else q_uls_min

        stations.append(
            {
                "station": label,
                "x_m": round(x_m, 3),
                "x_ratio": round(xi, 2),
                "m_dead_knm": round(m_dead, 2),
                "m_live_knm": round(m_live, 2),
                "m_sls_char_knm": round(m_sls_char, 2),
                "m_sls_freq_knm": round(m_sls_freq, 2),
                "m_sls_quasi_knm": round(m_sls_quasi, 2),
                "m_uls_knm": round(m_uls, 2),
                "q_dead_kn": round(q_dead, 2),
                "q_live_pos_kn": round(q_live_pos, 2),
                "q_live_neg_kn": round(q_live_neg, 2),
                "q_sls_char_kn": round(q_dead + q_live_abs_max, 2),
                "q_uls_gov_kn": round(q_uls_gov, 2),
            }
        )

    # Quarter-span exact point (x = L/4)
    x_q = 0.25 * calc_span_m
    m_dead_q = 0.5 * g_total * x_q * (calc_span_m - x_q)
    m_live_q = (1.0 + mu) * m_cq * (q_k * 0.5 * x_q * (calc_span_m - x_q) + p_k * (x_q * (calc_span_m - x_q) / calc_span_m))

    return {
        "calc_span_m": round(calc_span_m, 3),
        "load_grade": load_grade,
        "code_edition": code_edition,
        "dead_load_kn_m": round(g_total, 3),
        "fundamental_freq_hz": freq_hz,
        "impact_factor_mu": mu,
        "lane_qk_kn_m": round(q_k, 3),
        "lane_pk_kn": round(p_k, 3),
        "mid_span_summary": stations[5],
        "support_summary": stations[0],
        "quarter_span_m_uls_knm": round(safety_factor_gamma0 * (1.2 * m_dead_q + 1.4 * m_live_q), 2),
        "stations": stations,
    }


def compute_prestress_estimation_and_loss(
    *,
    m_sls_char_knm: float = 4549.0,
    m_uls_knm: float = 6399.525,
    girder_height_m: float = 1.70,
    y_bot_m: float = 1.131,
    kern_upper_ks_m: float = 0.2685,
    centroid_cover_ap_m: float = 0.15,
    strand_area_mm2: float = 140.0,
    fpk_mpa: float = 1860.0,
    fpd_mpa: float = 1260.0,
    sigma_con_mpa: float = 1395.0,
    strands_per_tendon: int = 6,
    n_tendons: int | None = None,
    tendon_count: int | None = None,
    beam_length_m: float = 24.607,
    bend_angle_deg: float = 8.5,
    friction_mu: float = 0.25,
    wobble_k: float = 0.0015,
    anchorage_slip_mm: float = 6.0,
    ep_mpa: float = 195000.0,
) -> dict[str, Any]:
    """Estimate required 1860MPa strands/tendons and compute longitudinal prestress loss profile."""

    if strands_per_tendon < 1:
        raise ValueError(f"strands_per_tendon must be >= 1, got {strands_per_tendon}")

    c1_emp = 0.51
    a_uls_emp = 0.76
    ep_ecc_m = y_bot_m - centroid_cover_ap_m
    ap1_m2 = strand_area_mm2 * 1e-6

    n_strands_sls = (m_sls_char_knm * 1e3) / (c1_emp * ap1_m2 * (fpk_mpa * 1e6) * (kern_upper_ks_m + ep_ecc_m))
    n_strands_uls = (m_uls_knm * 1e3) / (a_uls_emp * girder_height_m * (fpd_mpa * 1e6) * ap1_m2)

    gov_strands = max(n_strands_sls, n_strands_uls)
    auto_n_tendons = max(2, math.ceil(gov_strands / strands_per_tendon))

    explicit_raw = n_tendons if n_tendons is not None else tendon_count
    if explicit_raw is not None:
        if isinstance(explicit_raw, bool) or (isinstance(explicit_raw, float) and not explicit_raw.is_integer()):
            raise ValueError(f"n_tendons must be an integer, got {explicit_raw!r}")
        adopted_tendons = int(explicit_raw)
        if adopted_tendons < 1 or adopted_tendons > 10:
            raise ValueError(f"n_tendons must be between 1 and 10, got {adopted_tendons}")
        tendon_count_source = "explicit_override"
    else:
        adopted_tendons = auto_n_tendons
        tendon_count_source = "auto_envelope"

    provided_strands = adopted_tendons * strands_per_tendon

    # Symmetric parabolic tendon profile loss table across 11 stations [0 .. beam_length_m]
    half_len = 0.5 * beam_length_m
    theta_end_rad = math.radians(bend_angle_deg)

    # Friction slope d(sigma_l1)/dx near anchorage for reverse-friction anchorage slip length l_f
    sigma_mid_no_slip = sigma_con_mpa * (1.0 - math.exp(-(friction_mu * theta_end_rad + wobble_k * half_len)))
    avg_friction_slope = sigma_mid_no_slip / max(0.1, half_len)  # MPa / m
    # Reverse friction length l_f = sqrt(Delta_l * E_p / avg_friction_slope) (with Delta_l in m)
    lf_m = min(
        half_len,
        math.sqrt(((anchorage_slip_mm * 1e-3) * ep_mpa) / max(1e-6, avg_friction_slope)),
    )
    delta_sigma_anchor = 2.0 * avg_friction_slope * lf_m

    loss_rows: list[dict[str, float]] = []
    for idx in range(11):
        x_m = beam_length_m * (idx / 10.0)
        dist_from_near_end = min(x_m, beam_length_m - x_m)
        theta_x = theta_end_rad * (dist_from_near_end / max(1e-6, half_len))
        sigma_l1 = sigma_con_mpa * (1.0 - math.exp(-(friction_mu * theta_x + wobble_k * dist_from_near_end)))
        sigma_l2 = max(0.0, delta_sigma_anchor * (1.0 - dist_from_near_end / max(1e-6, lf_m)))
        sigma_after_anchor = sigma_con_mpa - sigma_l1 - sigma_l2
        # Relaxation loss sigma_l5 (low-relaxation strand psi=0.3) & creep/shrinkage sigma_l6
        sigma_l5 = max(0.0, 0.3 * (0.52 * (sigma_after_anchor / fpk_mpa) - 0.26) * sigma_after_anchor)
        sigma_l6 = 35.0 + 6.0 * (dist_from_near_end / max(1e-6, half_len))
        total_loss = sigma_l1 + sigma_l2 + sigma_l5 + sigma_l6
        sigma_pe = sigma_con_mpa - total_loss
        loss_rows.append(
            {
                "x_m": round(x_m, 3),
                "sigma_l1_friction_mpa": round(sigma_l1, 2),
                "sigma_l2_anchorage_mpa": round(sigma_l2, 2),
                "sigma_l5_relaxation_mpa": round(sigma_l5, 2),
                "sigma_l6_creep_shrink_mpa": round(sigma_l6, 2),
                "total_loss_mpa": round(total_loss, 2),
                "effective_prestress_mpa": round(sigma_pe, 2),
            }
        )

    return {
        "sigma_con_mpa": round(sigma_con_mpa, 2),
        "eccentricity_ep_m": round(ep_ecc_m, 4),
        "required_strands_sls": round(n_strands_sls, 2),
        "required_strands_uls": round(n_strands_uls, 2),
        "auto_estimated_tendons": auto_n_tendons,
        "recommended_tendons": adopted_tendons,
        "tendon_count_source": tendon_count_source,
        "strands_per_tendon": strands_per_tendon,
        "provided_strands": provided_strands,
        "tendon_designation": f"{adopted_tendons}-Φs15.2-{strands_per_tendon}",
        "anchorage_influence_length_m": round(lf_m, 3),
        "loss_profile": loss_rows,
    }


def _solve_linear_system(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Pure-Python Gaussian elimination with partial pivoting."""

    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("singular stiffness matrix in cap-beam solver")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        piv_val = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= piv_val
        for r in range(n):
            if r != col and abs(aug[r][col]) > 0.0:
                factor = aug[r][col]
                for j in range(col, n + 1):
                    aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def solve_continuous_cap_beam_1d(
    *,
    cap_length_m: float = 10.95,
    cap_width_m: float = 1.70,
    cap_height_m: float = 1.50,
    column_spacing_m: float = 6.65,
    n_columns: int = 2,
    girder_reactions_kn: Sequence[float] = (1091.64, 977.89, 849.48, 977.89, 1091.64),
    girder_spacing_m: float = 2.348,
    concrete_ec_mpa: float = 30000.0,
    self_weight_factor: float = 1.2,
) -> dict[str, Any]:
    """Pure-Python 1D Euler-Bernoulli continuous overhanging cap-beam finite element solver."""

    if n_columns < 2:
        raise ValueError("n_columns must be >= 2")
    if cap_length_m <= 0:
        raise ValueError("cap_length_m must be > 0")
    n_girders = len(girder_reactions_kn)
    if n_girders < 1:
        raise ValueError("girder_reactions_kn must contain at least 1 girder reaction")
    if (n_girders - 1) * girder_spacing_m > cap_length_m + 1e-6:
        raise ValueError(
            f"girder span {(n_girders - 1) * girder_spacing_m:.3f}m exceeds cap_length_m {cap_length_m:.3f}m"
        )
    if (n_columns - 1) * column_spacing_m > cap_length_m + 1e-6:
        raise ValueError(
            f"column span {(n_columns - 1) * column_spacing_m:.3f}m exceeds cap_length_m {cap_length_m:.3f}m"
        )
    half_cap = 0.5 * cap_length_m

    # Support coordinates (centered on cap beam [0, cap_length_m])
    col_xs = [
        round(half_cap + (i - 0.5 * (n_columns - 1)) * column_spacing_m, 6)
        for i in range(n_columns)
    ]
    girder_xs = [
        round(half_cap + (i - 0.5 * (n_girders - 1)) * girder_spacing_m, 6)
        for i in range(n_girders)
    ]

    # Build sorted mesh nodes; prevent near-zero-length sliver elements (<80mm) next to supports/loads
    key_nodes = sorted({0.0, round(cap_length_m, 6), round(half_cap, 6), *col_xs, *girder_xs})
    raw_nodes = set(key_nodes)
    for k in range(21):
        cand = round(cap_length_m * k / 20.0, 6)
        if min(abs(cand - kn) for kn in key_nodes) >= 0.08:
            raw_nodes.add(cand)
    nodes = sorted(x for x in raw_nodes if -1e-6 <= x <= cap_length_m + 1e-6)
    n_nodes = len(nodes)
    ndof = 2 * n_nodes  # [w_0, theta_0, w_1, theta_1, ...]

    ei = (concrete_ec_mpa * 1e3) * (cap_width_m * (cap_height_m ** 3) / 12.0)  # kN * m^2
    q_self = self_weight_factor * cap_width_m * cap_height_m * 25.0  # kN / m downward

    k_global = [[0.0] * ndof for _ in range(ndof)]
    f_global = [0.0] * ndof
    elem_data: list[tuple[int, int, float]] = []

    for e_idx in range(n_nodes - 1):
        L = nodes[e_idx + 1] - nodes[e_idx]
        elem_data.append((e_idx, e_idx + 1, L))
        c = ei / (L ** 3)
        ke = [
            [12.0 * c, 6.0 * L * c, -12.0 * c, 6.0 * L * c],
            [6.0 * L * c, 4.0 * L * L * c, -6.0 * L * c, 2.0 * L * L * c],
            [-12.0 * c, -6.0 * L * c, 12.0 * c, -6.0 * L * c],
            [6.0 * L * c, 2.0 * L * L * c, -6.0 * L * c, 4.0 * L * L * c],
        ]
        # Downward uniform load -> negative w direction
        fe = [-0.5 * q_self * L, -(q_self * L * L) / 12.0, -0.5 * q_self * L, (q_self * L * L) / 12.0]
        dofs = [2 * e_idx, 2 * e_idx + 1, 2 * (e_idx + 1), 2 * (e_idx + 1) + 1]
        for r in range(4):
            f_global[dofs[r]] += fe[r]
            for s in range(4):
                k_global[dofs[r]][dofs[s]] += ke[r][s]

    # Apply concentrated girder reactions downward
    for gx, p_kn in zip(girder_xs, girder_reactions_kn):
        node_idx = min(range(n_nodes), key=lambda idx: abs(nodes[idx] - gx))
        f_global[2 * node_idx] -= float(p_kn)

    # Constrain vertical displacement w_i = 0 at column support nodes
    fixed_dofs = set()
    support_node_indices: list[int] = []
    for cx in col_xs:
        s_idx = min(range(n_nodes), key=lambda idx: abs(nodes[idx] - cx))
        support_node_indices.append(s_idx)
        fixed_dofs.add(2 * s_idx)

    free_dofs = [d for d in range(ndof) if d not in fixed_dofs]
    k_red = [[k_global[r][c] for c in free_dofs] for r in free_dofs]
    f_red = [f_global[r] for r in free_dofs]
    u_free = _solve_linear_system(k_red, f_red)

    u_full = [0.0] * ndof
    for d, val in zip(free_dofs, u_free):
        u_full[d] = val

    # Compute support reactions and element end internal forces (sagging M > 0, hogging over pier M < 0)
    reactions_kn: list[dict[str, float]] = []
    for col_no, s_idx in enumerate(support_node_indices, start=1):
        dof_w = 2 * s_idx
        r_val = sum(k_global[dof_w][j] * u_full[j] for j in range(ndof)) - f_global[dof_w]
        reactions_kn.append({"column_no": col_no, "x_m": round(nodes[s_idx], 3), "reaction_kn": round(r_val, 2)})

    diagram_points: list[dict[str, float]] = []
    max_sag_m = 0.0
    min_hog_m = 0.0
    max_abs_q = 0.0

    for i0, i1, L in elem_data:
        dofs = [2 * i0, 2 * i0 + 1, 2 * i1, 2 * i1 + 1]
        ue = [u_full[d] for d in dofs]
        c = ei / (L ** 3)
        ke = [
            [12.0 * c, 6.0 * L * c, -12.0 * c, 6.0 * L * c],
            [6.0 * L * c, 4.0 * L * L * c, -6.0 * L * c, 2.0 * L * L * c],
            [-12.0 * c, -6.0 * L * c, 12.0 * c, -6.0 * L * c],
            [6.0 * L * c, 2.0 * L * L * c, -6.0 * L * c, 4.0 * L * L * c],
        ]
        fe = [-0.5 * q_self * L, -(q_self * L * L) / 12.0, -0.5 * q_self * L, (q_self * L * L) / 12.0]
        pe = [sum(ke[r][s] * ue[s] for s in range(4)) - fe[r] for r in range(4)]
        # Beam sign convention: left end shear V_left = pe[0], sagging moment M_left = -pe[1];
        # right end shear V_right = -pe[2], sagging moment M_right = pe[3]
        v_left, m_left = pe[0], -pe[1]
        v_right, m_right = -pe[2], pe[3]
        max_sag_m = max(max_sag_m, m_left, m_right)
        min_hog_m = min(min_hog_m, m_left, m_right)
        max_abs_q = max(max_abs_q, abs(v_left), abs(v_right))
        diagram_points.append({"x_m": round(nodes[i0], 3), "m_knm": round(m_left, 2), "q_kn": round(v_left, 2)})
        diagram_points.append({"x_m": round(nodes[i1], 3), "m_knm": round(m_right, 2), "q_kn": round(v_right, 2)})

    return {
        "cap_length_m": round(cap_length_m, 3),
        "column_positions_m": col_xs,
        "girder_positions_m": girder_xs,
        "girder_reactions_kn": [round(float(x), 2) for x in girder_reactions_kn],
        "support_reactions": reactions_kn,
        "max_sagging_moment_knm": round(max_sag_m, 2),
        "max_hogging_moment_knm": round(min_hog_m, 2),
        "max_abs_shear_kn": round(max_abs_q, 2),
        "diagram": diagram_points,
    }


def audit_bridge_design_consistency(spec: dict[str, Any]) -> dict[str, Any]:
    """Cross-audit deck width, girder/bearing alignment, pavement units, and limit-state tables."""

    issues: list[dict[str, Any]] = []

    # 1. Deck width consistency check (separates roadway_width, clear_width, and single/total deck_width!)
    n_decks = int(spec.get("n_carriageways", 2))
    single_deck_m = spec.get("single_deck_width_m")
    clear_w_m = spec.get("clear_width_m")
    roadway_m = spec.get("roadway_width_m")
    lat_clear_m = float(spec.get("lateral_clearance_m", 0.0))
    barrier_m = float(spec.get("barrier_width_m", 0.50))
    median_m = float(spec.get("median_width_m", 1.0 if n_decks == 2 else 0.0))
    total_deck_m = spec.get("total_deck_width_m")

    if clear_w_m is not None and roadway_m is not None:
        expected_clear = float(roadway_m) + 2.0 * lat_clear_m
        if abs(float(clear_w_m) - expected_clear) > 0.01:
            issues.append(
                {
                    "code": "DECK_CLEAR_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"护栏内净宽 ({clear_w_m}m) 与 行车道宽+两侧侧向余宽 "
                        f"({roadway_m} + 2×{lat_clear_m} = {expected_clear:.3f}m) 不一致"
                    ),
                }
            )

    if single_deck_m is not None and roadway_m is not None:
        expected_single = float(roadway_m) + 2.0 * lat_clear_m + 2.0 * barrier_m
        if abs(float(single_deck_m) - expected_single) > 0.01:
            issues.append(
                {
                    "code": "DECK_SINGLE_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"单幅桥面宽 ({single_deck_m}m) 与 行车道+余宽+两侧护栏 "
                        f"({roadway_m} + 2×{lat_clear_m} + 2×{barrier_m} = {expected_single:.3f}m) 不一致"
                    ),
                }
            )

    if total_deck_m is not None and single_deck_m is not None:
        expected_total = n_decks * float(single_deck_m) + (median_m if n_decks > 1 else 0.0)
        if abs(float(total_deck_m) - expected_total) > 0.01:
            issues.append(
                {
                    "code": "DECK_TOTAL_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"桥梁总宽 ({total_deck_m}m) 与 {n_decks}×单幅({single_deck_m}m)+中央分隔带({median_m if n_decks > 1 else 0.0}m) "
                        f"= {expected_total:.3f}m 冲突"
                    ),
                }
            )

    # 1b. Girder structural width chain ((n-1)*spacing + 2*edge_cantilever == single_deck_width) & span hierarchy
    edge_cant_mm = spec.get("edge_cantilever_mm")
    if single_deck_m is not None and spec.get("n_girders") is not None and spec.get("girder_spacing_mm") is not None and edge_cant_mm is not None:
        girder_total_mm = (int(spec["n_girders"]) - 1) * float(spec["girder_spacing_mm"]) + 2.0 * float(edge_cant_mm)
        if abs(girder_total_mm - float(single_deck_m) * 1000.0) > 2.0:
            issues.append(
                {
                    "code": "GIRDER_DECK_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"主梁横向总宽 ((n-1)×梁距+2×悬臂 = {girder_total_mm:g}mm) 与 桥面全宽 ({float(single_deck_m)*1000:g}mm) 不闭合"
                    ),
                }
            )

    std_span_m = spec.get("standard_span_m")
    calc_span_m = spec.get("calc_span_m")
    if std_span_m is not None and calc_span_m is not None:
        if float(calc_span_m) <= 0 or float(calc_span_m) >= float(std_span_m):
            issues.append(
                {
                    "code": "SPAN_HIERARCHY_INVALID",
                    "severity": "error",
                    "message": f"计算跨径 ({calc_span_m}m) 必须严格小于标准跨径 ({std_span_m}m)",
                }
            )

    # 2. Superstructure girder spacing vs pier/abutment bearing pedestal spacing
    n_girders = spec.get("n_girders")
    girder_spacing_mm = spec.get("girder_spacing_mm")
    pier_bearing_spacing_mm = spec.get("pier_bearing_spacing_mm")
    abutment_bearing_spacing_mm = spec.get("abutment_bearing_spacing_mm")
    n_pier_pedestals = spec.get("n_pier_pedestals")

    if girder_spacing_mm is not None and pier_bearing_spacing_mm is not None:
        if abs(float(girder_spacing_mm) - float(pier_bearing_spacing_mm)) > 1.0:
            issues.append(
                {
                    "code": "PIER_BEARING_SPACING_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"主梁间距 ({girder_spacing_mm}mm) 与 桥墩盖梁支座垫石间距 ({pier_bearing_spacing_mm}mm) 不匹配"
                    ),
                }
            )
    if girder_spacing_mm is not None and abutment_bearing_spacing_mm is not None:
        if abs(float(girder_spacing_mm) - float(abutment_bearing_spacing_mm)) > 1.0:
            issues.append(
                {
                    "code": "ABUTMENT_BEARING_SPACING_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"主梁间距 ({girder_spacing_mm}mm) 与 桥台支座垫石间距 ({abutment_bearing_spacing_mm}mm) 不匹配"
                    ),
                }
            )
    if n_girders is not None and n_pier_pedestals is not None and int(n_girders) != int(n_pier_pedestals):
        issues.append(
            {
                "code": "PEDESTAL_COUNT_MISMATCH",
                "severity": "error",
                "message": f"主梁片数 ({n_girders}) 与 盖梁支座垫石数量 ({n_pier_pedestals}) 不一致",
            }
        )
    cap_length_m = spec.get("cap_length_m")
    if cap_length_m is not None and n_girders is not None and girder_spacing_mm is not None:
        req_span_m = (int(n_girders) - 1) * (float(girder_spacing_mm) / 1000.0)
        if float(cap_length_m) + 1e-6 < req_span_m:
            issues.append(
                {
                    "code": "CAP_BEAM_LENGTH_INSUFFICIENT",
                    "severity": "error",
                    "message": f"盖梁长度 ({cap_length_m}m) 小于最外侧主梁中心距 ({req_span_m:.3f}m)",
                }
            )

    # 3. Deck pavement thickness 10x unit typo check (catches '110cm 厚沥青' / '100cm 厚混凝土')
    for layer in spec.get("pavement_layers", []):
        lname = str(layer.get("name", "铺装层"))
        raw_val = float(layer.get("thickness", 0.0))
        unit = str(layer.get("unit", "mm")).strip().lower()
        thick_mm = raw_val * 10.0 if unit == "cm" else (raw_val * 1000.0 if unit == "m" else raw_val)
        if thick_mm > 350.0:
            issues.append(
                {
                    "code": "PAVEMENT_THICKNESS_UNIT_TYPO",
                    "severity": "error",
                    "message": (
                        f"桥面铺装层 '{lname}' 折算厚度高达 {thick_mm:g}mm (原始输入 {raw_val:g}{unit})，"
                        f"疑似将 mm 误写为 cm（10倍量级笔误）"
                    ),
                }
            )

    # 4. Limit-state check rows (catches '0.314mm <= 0.200mm -> 是' false pass claims!)
    for idx, row in enumerate(spec.get("limit_checks", []), start=1):
        sec = str(row.get("section", f"row-{idx}"))
        metric = str(row.get("metric", "验算项"))
        demand = float(row.get("value", 0.0))
        allow = float(row.get("allowable", 0.0))
        claimed_pass = row.get("claimed_pass")
        mode = str(row.get("mode", "max"))  # 'max': abs(demand) <= abs(allow)
        actual_pass = abs(demand) <= abs(allow) + 1e-6 if mode == "max" else demand >= allow - 1e-6
        if not actual_pass:
            issues.append(
                {
                    "code": "LIMIT_STATE_EXCEEDED",
                    "severity": "error",
                    "message": (
                        f"截面 {sec} [{metric}] 计算值 {demand:g} 超出规范容许限值 {allow:g}"
                        + ("（原表误判为“是/满足”！）" if claimed_pass is True else "")
                    ),
                }
            )

    error_count = sum(1 for item in issues if item["severity"] == "error")
    return {
        "status": "passed" if error_count == 0 else "failed",
        "error_count": error_count,
        "issue_count": len(issues),
        "issues": issues,
    }


_STRICT_CELL_NUM_RE = re.compile(
    r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(?:[a-zA-Z°%‰μµΩ/·⋅²³\-\(\)]+)?\s*$"
)
_CLAIM_TOKENS = frozenset({"是", "否", "满足", "不满足", "合格", "不合格", "PASS", "FAIL", "true", "false"})
_PASS_TOKENS = frozenset({"是", "满足", "合格", "PASS", "true"})
_STATUS_HEADER_RE = re.compile(r"是否|满足|合格|结论|结果|status|pass|verdict", re.IGNORECASE)


def _parse_table_number(cell: str) -> float | None:
    s = (cell or "").strip()
    if not s:
        return None
    # Strip thousands commas like '1,250.5' while leaving non-numeric text untouched
    s_norm = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", s)
    m = _STRICT_CELL_NUM_RE.match(s_norm)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def ingest_and_audit_calc_table(
    *,
    table_text: str | None = None,
    file_path: str | Path | None = None,
) -> dict[str, Any]:
    """Parse external FEA/spreadsheet calculation check tables (CSV/TSV/Markdown) and audit pass/fail claims."""

    if not table_text and file_path is None:
        raise ValueError("provide table_text or file_path")
    if file_path is not None:
        p = Path(file_path)
        raw = p.read_text(encoding="utf-8-sig")
    else:
        assert table_text is not None
        raw = table_text

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("calculation table is empty")

    rows: list[list[str]] = []
    if "|" in lines[0]:
        for ln in lines:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells and all(set(c) <= {"-", ":", " "} for c in cells if c):
                continue
            rows.append(cells)
    elif "\t" in lines[0]:
        rows = [[c.strip() for c in ln.split("\t")] for ln in lines]
    else:
        reader = csv.reader(io.StringIO(raw))
        rows = [[c.strip() for c in r] for r in reader if any(c.strip() for c in r)]

    if len(rows) < 2:
        raise ValueError("table requires a header row and at least one data row")

    headers = rows[0]
    data_rows = rows[1:]
    max_cols = max(len(r) for r in rows)

    # Determine expected column groups [(val_col, lim_col, status_col_or_None), ...] after section col 0
    col_groups: list[tuple[int, int, int | None]] = []
    col_i = 1
    while col_i + 1 < max_cols:
        has_status_col = False
        if col_i + 2 < max_cols:
            hdr_c2 = headers[col_i + 2] if col_i + 2 < len(headers) else ""
            if _STATUS_HEADER_RE.search(hdr_c2) or any(
                (col_i + 2 < len(dr) and dr[col_i + 2] in _CLAIM_TOKENS) for dr in data_rows
            ):
                has_status_col = True
        if has_status_col:
            col_groups.append((col_i, col_i + 1, col_i + 2))
            col_i += 3
        else:
            col_groups.append((col_i, col_i + 1, None))
            col_i += 2

    parsed_checks: list[dict[str, Any]] = []
    unverified_rows: list[dict[str, Any]] = []
    table_issues: list[dict[str, Any]] = []

    for r_idx, r in enumerate(data_rows, start=1):
        sec_label = r[0] if r else f"row-{r_idx}"
        if not col_groups or len(r) < 3:
            unverified_rows.append({"row": r_idx, "section": sec_label, "reason": "insufficient_columns"})
            table_issues.append(
                {
                    "code": "UNVERIFIED_TABLE_ROW",
                    "severity": "error",
                    "message": f"第 {r_idx} 行截面 {sec_label!r} 缺少完整的（计算值, 容许限值）列，未完成校核",
                }
            )
            continue

        for g_idx, (c_val, c_lim, c_claim) in enumerate(col_groups, start=1):
            raw_val = r[c_val] if c_val < len(r) else ""
            raw_lim = r[c_lim] if c_lim < len(r) else ""
            metric_name = headers[c_val] if c_val < len(headers) and headers[c_val] else f"check_{g_idx}"
            val = _parse_table_number(raw_val)
            lim = _parse_table_number(raw_lim)
            if val is None or lim is None:
                unverified_rows.append(
                    {
                        "row": r_idx,
                        "section": sec_label,
                        "metric": metric_name,
                        "raw_value": raw_val,
                        "raw_allowable": raw_lim,
                    }
                )
                table_issues.append(
                    {
                        "code": "UNVERIFIED_TABLE_VALUE",
                        "severity": "error",
                        "message": (
                            f"第 {r_idx} 行截面 {sec_label!r} [{metric_name}] 存在缺失或不可解析数据 "
                            f"(计算值={raw_val!r}, 容许值={raw_lim!r})，拒绝判为通过"
                        ),
                    }
                )
                continue

            claimed: bool | None = None
            if c_claim is not None and c_claim < len(r) and r[c_claim] in _CLAIM_TOKENS:
                claimed = r[c_claim] in _PASS_TOKENS
            parsed_checks.append(
                {
                    "section": sec_label,
                    "metric": metric_name,
                    "value": val,
                    "allowable": lim,
                    "claimed_pass": claimed,
                }
            )

    if not parsed_checks and not table_issues:
        table_issues.append(
            {
                "code": "EMPTY_VERIFIED_CHECKS",
                "severity": "error",
                "message": "计算表中未解析出任何有效校核项，拒绝判为通过",
            }
        )

    audit_res = audit_bridge_design_consistency({"limit_checks": parsed_checks})
    merged_issues = [*table_issues, *audit_res["issues"]]
    err_cnt = sum(1 for item in merged_issues if item["severity"] == "error")
    merged_audit = {
        "status": "passed" if (err_cnt == 0 and len(parsed_checks) > 0) else "failed",
        "error_count": err_cnt,
        "issue_count": len(merged_issues),
        "issues": merged_issues,
    }
    return {
        "row_count": len(data_rows),
        "parsed_check_count": len(parsed_checks),
        "unverified_count": len(unverified_rows),
        "unverified_rows": unverified_rows,
        "parsed_checks": parsed_checks,
        "audit": merged_audit,
    }


def default_opening_report_schemes(
    n_spans: int = 4,
    standard_span_m: float = 25.0,
    deck_width_m: float = 9.0,
) -> list[dict[str, Any]]:
    """Return the three determined bridge comparison schemes aligned with the project Opening Report (开题报告).

    Scheme 1 (Recommended): 4×25 m 装配式预应力混凝土简支T梁桥（桥面连续）
    Scheme 2 (Alternative): 4×25 m 预应力混凝土连续箱梁桥
    Scheme 3 (Alternative): 4×25 m 装配式预应力混凝土简支箱梁桥
    """
    layout_str = f"{n_spans}×{standard_span_m:g} m"
    n_box = 3 if deck_width_m <= 10.5 else 4
    return [
        {
            "scheme_id": "scheme_1",
            "scheme_no": "方案一（推荐方案）",
            "name": f"{layout_str} 装配式预应力混凝土简支T梁桥",
            "span_layout": layout_str,
            "is_recommended": True,
            "superstructure_type": "5片预制C50预应力混凝土简支T梁（墩顶桥面连续）",
            "girder_height_m": 1.75 if standard_span_m >= 24.0 else round(standard_span_m / 14.5, 2),
            "substructure_type": "双柱式圆柱桥墩（Φ1.2m）+ 桩柱式桥台 + Φ1.4m钻孔灌注桩",
            "structural_mechanics": "静定简支受力明确，对地基不均匀沉降不敏感；预应力高效消压抗裂，墩顶设桥面连续改善行车平顺性",
            "construction_and_schedule": "预制场标准化台座批量预制、架桥机吊装，上、下部结构平行施工，不受河道汛期水位制约，工期最短",
            "economy_and_maintenance": "钢模周转率高、造价最经济；湿接缝、支座与桥面连续构造通用成熟，运营期检查及更换支座便捷",
            "elevation_figure_caption": f"{layout_str} 装配式预应力混凝土简支T梁桥立面布置图",
            "section_figure_caption": f"{layout_str} 装配式预应力混凝土简支T梁桥标准横断面图",
            "elevation_image_path": None,
            "section_image_path": None,
            "verdict": "推荐采用",
        },
        {
            "scheme_id": "scheme_2",
            "scheme_no": "方案二（比选方案）",
            "name": f"{layout_str} 预应力混凝土连续箱梁桥",
            "span_layout": layout_str,
            "is_recommended": False,
            "superstructure_type": "C50预应力混凝土连续箱梁（单箱双室/先简支后结构连续）",
            "girder_height_m": 1.45 if standard_span_m >= 24.0 else round(standard_span_m / 17.0, 2),
            "substructure_type": "双柱式桥墩 + 桩柱式桥台 + 钻孔灌注桩基础",
            "structural_mechanics": "超静定连续体系，墩顶负弯矩卸载跨中正弯矩，整体抗扭刚度大、桥面伸缩缝少，但受温度梯度、收缩徐变及基础沉降次内力影响明显",
            "construction_and_schedule": "满堂支架现浇受河道行洪期影响大，或体系转换需二次张拉墩顶负弯矩钢束并压浆，工序较复杂、施工周期较长",
            "economy_and_maintenance": "支架与内模摊销较高、预应力锚具及负弯矩束用量增加，综合造价高于简支T梁；闭合箱室内部巡检相对不便",
            "elevation_figure_caption": f"{layout_str} 预应力混凝土连续箱梁桥立面布置图",
            "section_figure_caption": f"{layout_str} 预应力混凝土连续箱梁桥标准横断面图",
            "elevation_image_path": None,
            "section_image_path": None,
            "verdict": "备选方案",
        },
        {
            "scheme_id": "scheme_3",
            "scheme_no": "方案三（比选方案）",
            "name": f"{layout_str} 装配式预应力混凝土简支箱梁桥",
            "span_layout": layout_str,
            "is_recommended": False,
            "superstructure_type": f"{n_box}片预制C50预应力混凝土简支小箱梁（桥面连续）",
            "girder_height_m": 1.40 if standard_span_m >= 24.0 else round(standard_span_m / 17.5, 2),
            "substructure_type": "双柱式桥墩 + 桩柱式桥台 + 钻孔灌注桩基础",
            "structural_mechanics": "简支静定体系适应变形能力好，单梁闭口截面抗扭刚度较T梁大，但腹板较薄、混凝土浇筑振捣密实度控制要求高",
            "construction_and_schedule": "工厂预制吊装可行，但箱梁内模拼拆烦琐、单片预制梁自重及吊装吨位较大，大型架桥设备进场费用较高",
            "economy_and_maintenance": "模板及大型吊装机械单价偏高，总造价略高于装配式T梁；小箱梁内腔狭小，后期内部病害检测与维修难度较大",
            "elevation_figure_caption": f"{layout_str} 装配式预应力混凝土简支箱梁桥立面布置图",
            "section_figure_caption": f"{layout_str} 装配式预应力混凝土简支箱梁桥标准横断面图",
            "elevation_image_path": None,
            "section_image_path": None,
            "verdict": "比选不采用",
        },
    ]


def default_graduation_references() -> list[dict[str, Any]]:
    """Return 12 curated GB/T 7714 references (>=10 Chinese + >=2 English) aligned with both Task Book and Opening Report."""
    return [
        {
            "ref_no": 1,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路工程技术标准: JTG B01—2014[S]. 北京: 人民交通出版社, 2014.",
            "used_in_sections": ["1.1", "1.3"],
        },
        {
            "ref_no": 2,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路桥涵设计通用规范: JTG D60—2015[S]. 北京: 人民交通出版社, 2015.",
            "used_in_sections": ["1.1", "1.2", "2.5", "4.2"],
        },
        {
            "ref_no": 3,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路钢筋混凝土及预应力混凝土桥涵设计规范: JTG 3362—2018[S]. 北京: 人民交通出版社, 2018.",
            "used_in_sections": ["1.1", "2.1", "2.6", "3.2", "3.3", "4.3"],
        },
        {
            "ref_no": 4,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路桥涵地基与基础设计规范: JTG 3363—2019[S]. 北京: 人民交通出版社, 2019.",
            "used_in_sections": ["1.1", "3.4", "4.4"],
        },
        {
            "ref_no": 5,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路桥梁抗震设计规范: JTG/T 2231-01—2020[S]. 北京: 人民交通出版社, 2020.",
            "used_in_sections": ["1.1", "3.3"],
        },
        {
            "ref_no": 6,
            "lang": "zh",
            "citation": "邵旭东. 桥梁工程（第5版）[M]. 北京: 人民交通出版社, 2021.",
            "used_in_sections": ["1.3", "2.2", "2.4"],
        },
        {
            "ref_no": 7,
            "lang": "zh",
            "citation": "范立础. 桥梁工程（上册）（第3版）[M]. 北京: 人民交通出版社, 2017.",
            "used_in_sections": ["1.2", "1.3"],
        },
        {
            "ref_no": 8,
            "lang": "zh",
            "citation": "姚玲森. 桥梁工程（第3版）[M]. 北京: 人民交通出版社, 2016.",
            "used_in_sections": ["2.4", "5.1"],
        },
        {
            "ref_no": 9,
            "lang": "zh",
            "citation": "闫志刚. 钢筋混凝土及预应力混凝土简支梁桥结构设计[M]. 北京: 机械工业出版社, 2018.",
            "used_in_sections": ["2.3", "2.6", "3.1"],
        },
        {
            "ref_no": 10,
            "lang": "zh",
            "citation": "中华人民共和国交通运输部. 公路桥梁板式橡胶支座: JT/T 4—2019[S]. 北京: 人民交通出版社, 2019.",
            "used_in_sections": ["2.6", "4.3"],
        },
        {
            "ref_no": 11,
            "lang": "en",
            "citation": "Barker R M, Puckett J A. Design of Highway Bridges: An LRFD Approach (4th ed.)[M]. Hoboken: John Wiley & Sons, 2021.",
            "used_in_sections": ["1.3", "5.2"],
        },
        {
            "ref_no": 12,
            "lang": "en",
            "citation": "AASHTO. AASHTO LRFD Bridge Design Specifications (9th ed.)[S]. Washington, D.C.: American Association of State Highway and Transportation Officials, 2020.",
            "used_in_sections": ["2.5", "5.2"],
        },
    ]


def audit_references_compliance(
    references: Sequence[Mapping[str, Any]],
    *,
    body_text: str | None = None,
) -> dict[str, Any]:
    """Audit bibliography against Task Book (>=10 total, >=1 foreign) and Opening Report (>=10 Chinese, >=1 foreign)."""
    zh_cnt = 0
    foreign_cnt = 0
    ref_nos: list[int] = []
    for idx, item in enumerate(references, start=1):
        rno = int(item.get("ref_no", idx))
        ref_nos.append(rno)
        lang = str(item.get("lang", "")).strip().lower()
        cit = str(item.get("citation", ""))
        if lang in {"en", "foreign", "de", "fr", "ja"} or (not lang and not re.search(r"[\u4e00-\u9fff]", cit)):
            foreign_cnt += 1
        else:
            zh_cnt += 1

    total = len(ref_nos)
    task_book_ok = (total >= 10) and (foreign_cnt >= 1)
    opening_report_ok = (zh_cnt >= 10) and (foreign_cnt >= 1)

    cited_in_body: list[int] = []
    uncited_in_body: list[int] = []
    if body_text is not None:
        # Match [3], [1,2], [1-5], [1～5] inline citations
        found_set: set[int] = set()
        for m in re.finditer(r"\[(\d+(?:\s*[,，\-~～]\s*\d+)*)\]", body_text):
            token_grp = m.group(1)
            for part in re.split(r"[,，]", token_grp):
                part = part.strip()
                rng = re.split(r"[\-~～]", part)
                if len(rng) == 2 and rng[0].strip().isdigit() and rng[1].strip().isdigit():
                    a_i, b_i = int(rng[0]), int(rng[1])
                    for k_i in range(min(a_i, b_i), max(a_i, b_i) + 1):
                        found_set.add(k_i)
                elif part.isdigit():
                    found_set.add(int(part))
        cited_in_body = sorted(r for r in ref_nos if r in found_set)
        uncited_in_body = [r for r in ref_nos if r not in found_set]

    both_ok = task_book_ok and opening_report_ok and (body_text is None or len(uncited_in_body) == 0)
    return {
        "status": "passed" if both_ok else "failed",
        "total_count": total,
        "chinese_count": zh_cnt,
        "foreign_count": foreign_cnt,
        "task_book_compliant": task_book_ok,
        "opening_report_compliant": opening_report_ok,
        "cited_ref_nos": cited_in_body,
        "uncited_ref_nos": uncited_in_body,
    }


_FEM_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "girder_mid_moment_uls_knm": (
        "girder_mid_moment_uls_knm",
        "mid_moment_uls_knm",
        "mid_moment_knm",
        "m_uls_mid",
        "主梁跨中弯矩",
        "跨中基本组合弯矩",
        "跨中弯矩",
    ),
    "girder_support_shear_uls_kn": (
        "girder_support_shear_uls_kn",
        "support_shear_uls_kn",
        "support_shear_kn",
        "v_uls_support",
        "主梁支点剪力",
        "支点基本组合剪力",
        "支点剪力",
    ),
    "girder_live_deflection_mm": (
        "girder_live_deflection_mm",
        "live_deflection_mm",
        "deflection_live_mm",
        "主梁活载挠度",
        "消除自重活载挠度",
        "活载挠度",
    ),
    "pier_top_reaction_uls_kn": (
        "pier_top_reaction_uls_kn",
        "pier_reaction_uls_kn",
        "pier_top_reaction_kn",
        "中墩墩顶反力",
        "中间墩盖梁顶总反力",
        "墩顶总反力",
        "墩顶反力",
    ),
    "cap_hogging_moment_uls_knm": (
        "cap_hogging_moment_uls_knm",
        "盖梁墩顶负弯矩",
        "盖梁负弯矩",
    ),
    "pier_column_axial_uls_kn": (
        "pier_column_axial_uls_kn",
        "墩柱柱底轴力",
        "墩柱轴力",
    ),
}


def ingest_independent_fem_results(
    fem_input: Mapping[str, Any] | str | Path | None,
) -> dict[str, Any] | None:
    """Ingest independent commercial/external FEM results (Midas Civil / 桥梁博士 Dr.Bridge) from dict, JSON, CSV, TSV, or Markdown."""
    if fem_input is None:
        return None

    raw_dict: dict[str, Any] = {}
    raw_table_text: str | None = None

    if isinstance(fem_input, Mapping):
        raw_dict = dict(fem_input)
    elif isinstance(fem_input, Path) or (
        isinstance(fem_input, str) and "\n" not in fem_input and Path(fem_input).expanduser().is_file()
    ):
        p = Path(fem_input).expanduser().resolve()
        txt = p.read_text(encoding="utf-8-sig")
        if p.suffix.lower() == ".json":
            raw_dict = json.loads(txt) if "json" in globals() else __import__("json").loads(txt)
        else:
            raw_table_text = txt
    elif isinstance(fem_input, str):
        s_strip = fem_input.strip()
        if s_strip.startswith("{"):
            raw_dict = __import__("json").loads(s_strip)
        else:
            raw_table_text = s_strip
    else:
        raise TypeError(f"unsupported independent_fem_results type: {type(fem_input).__name__}")

    metrics: dict[str, float] = {}
    if raw_table_text is not None:
        lines_t = [ln.strip() for ln in raw_table_text.splitlines() if ln.strip()]
        rows_t: list[list[str]] = []
        if any("|" in ln for ln in lines_t):
            for ln in lines_t:
                if "|" not in ln:
                    continue
                cells = [c.strip() for c in ln.strip("|").split("|")]
                if cells and all(set(c) <= {"-", ":", " "} for c in cells):
                    continue
                rows_t.append(cells)
        else:
            delim = "\t" if "\t" in raw_table_text else ","
            rows_t = [[c.strip() for c in r] for r in csv.reader(io.StringIO(raw_table_text), delimiter=delim) if any(c.strip() for c in r)]

        for row in rows_t:
            if len(row) < 2:
                continue
            label_cell = row[0]
            # Find first numeric value in remaining columns
            num_val: float | None = None
            for cell in row[1:]:
                cleaned = cell.replace(",", "").replace("kN·m", "").replace("kNm", "").replace("kN", "").replace("mm", "").strip()
                try:
                    num_val = float(cleaned)
                    break
                except ValueError:
                    continue
            if num_val is None:
                continue
            for canon_key, aliases in _FEM_METRIC_ALIASES.items():
                if any(alias.lower() in label_cell.lower() for alias in aliases):
                    metrics[canon_key] = abs(num_val) if canon_key != "girder_live_deflection_mm" else abs(num_val)
                    break

    # Extract from dict (metrics nested under 'metrics'/'results' or top-level)
    src_metrics = dict(raw_dict.get("metrics") or raw_dict.get("results") or raw_dict)
    for canon_key, aliases in _FEM_METRIC_ALIASES.items():
        for alias in aliases:
            if alias in src_metrics and isinstance(src_metrics[alias], (int, float)) and not isinstance(src_metrics[alias], bool):
                metrics[canon_key] = abs(float(src_metrics[alias]))
                break

    model_card_in = dict(raw_dict.get("model_card") or {})
    software_name = str(
        raw_dict.get("software_name")
        or model_card_in.get("software_name")
        or "桥梁博士 Dr.Bridge V4.4 / Midas Civil 独立杆系模型"
    ).strip()
    tolerance_pct = float(raw_dict.get("tolerance_pct", model_card_in.get("tolerance_pct", 8.0)))
    fig_paths_raw = raw_dict.get("model_figure_paths") or model_card_in.get("model_figure_paths") or []
    fig_paths = [str(p) for p in fig_paths_raw] if isinstance(fig_paths_raw, (list, tuple)) else []

    return {
        "software_name": software_name,
        "tolerance_pct": tolerance_pct,
        "checked_by": str(raw_dict.get("checked_by") or model_card_in.get("checked_by") or "").strip(),
        "model_card": {
            "software_name": software_name,
            "girder_model": str(model_card_in.get("girder_model", "1#代表性边T梁空间梁格/10单元简支梁有限元模型")),
            "pier_model": str(model_card_in.get("pier_model", "2#中间双柱式桥墩盖梁—双柱空间杆系模型")),
            "boundary_conditions": str(
                model_card_in.get(
                    "boundary_conditions",
                    "主梁左支点固定铰支座(约束Dx,Dy,Dz)、右支点纵向滑动铰支座(约束Dy,Dz)；双柱墩盖梁与墩柱刚接、桩土效应等效嵌固",
                )
            ),
            "load_and_combinations": str(
                model_card_in.get(
                    "load_and_combinations",
                    "一期自重+横隔梁+二期铺装护栏恒载；公路-I级车道荷载计入冲击系数(1+μ)；按 JTG D60-2015 基本组合 γ0(1.2G+1.4Q) 与正常使用频遇/标准组合包络",
                )
            ),
            "model_figure_paths": fig_paths,
        },
        "metrics": metrics,
    }


def wenchang_bridge_preset() -> dict[str, Any]:
    """Opening-report parameters for 信阳市文昌中桥 (4×25m 装配式预应力混凝土简支T梁, 单幅9.00m双向两车道)."""
    return {
        "project_name": "信阳市文昌中桥设计（4×25m装配式预应力简支T梁）",
        "n_spans": 4,
        "standard_span_m": 25.0,
        "beam_length_m": 24.96,
        "calc_span_m": 24.0,
        "n_carriageways": 1,
        "cross_slope_pct": 2.0,
        "asphalt_thick_mm": 100.0,
        "concrete_paving_thick_mm": 80.0,
        "barriers_total_kn_m": 18.0,
        "bridge_schemes": default_opening_report_schemes(4, 25.0, 9.0),
        "references": default_graduation_references(),
        "hydrology": {
            "area_m2": 325.0,
            "wetted_perimeter_m": 64.5,
            "roughness_inv": 25.0,
            "slope_permille": 0.8,
            "channel_width_m": 96.0,
            "standard_span_m": 25.0,
        },
        "t_girder": {
            "girder_spacing_mm": 1950.0,
            "flange_width_mm": 1950.0,
            "height_mm": 1750.0,
            "flange_tip_thick_mm": 200.0,
            "haunch_width_mm": 210.0,
            "haunch_height_mm": 70.0,
            "web_thick_mm": 200.0,
            "bulb_width_mm": 560.0,
            "bulb_height_mm": 200.0,
            "end_web_thick_mm": 560.0,
        },
        "lateral": {
            "n_girders": 5,
            "girder_spacing_m": 1.95,
            "edge_cantilever_m": 0.60,
            "deck_width_m": 9.00,
            "left_barrier_m": 0.50,
            "right_barrier_m": 0.50,
            "lateral_clearance_m": 0.50,
            "design_lanes": 2,
        },
        "superstructure": {
            "diaphragm_equiv_kn_m": 2.35,
            "load_grade": "highway-I",
        },
        "prestress": {
            "sigma_con_mpa": 1395.0,
            "strands_per_tendon": 6,
            "beam_length_m": 24.96,
        },
        "cap_beam": {
            "cap_length_m": 8.80,
            "cap_width_m": 1.60,
            "cap_height_m": 1.40,
            "column_spacing_m": 5.20,
        },
        "substructure": {
            "column_diameter_m": 1.20,
            "column_height_m": 5.50,
            "pile_diameter_m": 1.40,
            "concrete_grade": "C30",
        },
    }


def _deep_merge_dict(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_soil_layers(raw_layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_layers):
        lyr = dict(item)
        if "thickness_m" in lyr:
            thk = float(lyr["thickness_m"])
        elif "top_elev_m" in lyr and "bot_elev_m" in lyr:
            thk = float(lyr["top_elev_m"]) - float(lyr["bot_elev_m"])
        else:
            raise ValueError(f"soil layer #{idx + 1} requires 'thickness_m' or ('top_elev_m', 'bot_elev_m')")
        if thk <= 0:
            raise ValueError(f"soil layer #{idx + 1} thickness_m must be > 0 (got {thk})")
        normalized.append(
            {
                "name": str(lyr.get("name", f"Layer-{idx + 1}")),
                "thickness_m": round(thk, 3),
                "qik_kpa": float(lyr["qik_kpa"]),
                "fa0_kpa": float(lyr["fa0_kpa"]),
            }
        )
    if not normalized:
        raise ValueError("soil_layers must contain at least 1 soil stratum")
    return normalized


def ingest_bridge_survey_data(survey_data: Mapping[str, Any]) -> dict[str, Any]:
    """Ingest real site hydrology cross-section, route profile, and borehole geotechnical survey data.

    Supports:
    - `river_cross_section`: `stations` (`[(x_m, z_m), ...]` or `[{"x_m": .., "z_m": ..}]`), `flood_elevation_m`
      (exact submerged polygonal integration of area A, wetted perimeter chi, and water surface width Bw),
      `roughness_n` (Manning n) or `roughness_inv` (1/n), `slope_permille` (or `hydraulic_slope`), `design_discharge_m3_s`.
    - `route_profile`: `deck_elevation_m`, `min_freeboard_m`.
    - `pier_borehole` / `pier_soil_layers` & `abutment_borehole` / `abutment_soil_layers` & `backfill`.
    - `bearing_positioning`: `beam_length_m`, `bearing_center_offset_m`.
    """
    sd = dict(survey_data or {})
    overrides: dict[str, Any] = {}
    summary: dict[str, Any] = {"ingested_sections": []}

    # 1. Flood elevation & Route deck elevation
    river_sec = dict(sd.get("river_cross_section") or {})
    route_prof = dict(sd.get("route_profile") or {})

    flood_z: float | None = None
    for src_d, k_f in (
        (sd, "flood_elevation_m"),
        (sd, "design_flood_level_m"),
        (river_sec, "flood_elevation_m"),
        (river_sec, "design_flood_level_m"),
    ):
        if k_f in src_d and src_d[k_f] is not None:
            flood_z = float(src_d[k_f])
            break
    if flood_z is not None:
        overrides["flood_elevation_m"] = round(flood_z, 3)
        summary["flood_elevation_m"] = overrides["flood_elevation_m"]

    deck_z: float | None = None
    for src_d, k_d in (
        (sd, "deck_elevation_m"),
        (route_prof, "deck_elevation_m"),
        (route_prof, "center_stake_elevation_m"),
    ):
        if k_d in src_d and src_d[k_d] is not None:
            deck_z = float(src_d[k_d])
            break
    if deck_z is not None:
        overrides["deck_elevation_m"] = round(deck_z, 3)
        summary["deck_elevation_m"] = overrides["deck_elevation_m"]
        summary["ingested_sections"].append("route_profile")

    # 2. River cross-section polygonal integration below flood_z
    raw_stations = river_sec.get("stations") or sd.get("river_stations")
    if raw_stations:
        if flood_z is None:
            raise ValueError("flood_elevation_m is required when river_cross_section.stations is provided")
        pts: list[tuple[float, float]] = []
        for st in raw_stations:
            if isinstance(st, Mapping):
                pts.append((float(st["x_m"]), float(st["z_m"])))
            else:
                pts.append((float(st[0]), float(st[1])))
        pts.sort(key=lambda p: p[0])
        if len(pts) < 3:
            raise ValueError("river_cross_section.stations requires at least 3 (x_m, z_m) stations")

        area_m2 = 0.0
        wetted_p_m = 0.0
        wet_xs: list[float] = []
        for (x0, z0), (x1, z1) in zip(pts[:-1], pts[1:]):
            dx = x1 - x0
            if dx <= 1e-9:
                continue
            d0 = flood_z - z0
            d1 = flood_z - z1
            if d0 <= 0.0 and d1 <= 0.0:
                continue
            if d0 >= 0.0 and d1 >= 0.0:
                area_m2 += 0.5 * (d0 + d1) * dx
                wetted_p_m += math.hypot(dx, z1 - z0)
                wet_xs.extend((x0, x1))
            elif d0 > 0.0 > d1:
                frac = d0 / (d0 - d1)
                xi = x0 + frac * dx
                area_m2 += 0.5 * d0 * (xi - x0)
                wetted_p_m += math.hypot(xi - x0, flood_z - z0)
                wet_xs.extend((x0, xi))
            else:
                frac = (-d0) / (d1 - d0)
                xi = x0 + frac * dx
                area_m2 += 0.5 * d1 * (x1 - xi)
                wetted_p_m += math.hypot(x1 - xi, z1 - flood_z)
                wet_xs.extend((xi, x1))

        if area_m2 <= 0.0 or wetted_p_m <= 0.0 or not wet_xs:
            raise ValueError(f"flood_elevation_m ({flood_z}m) does not submerge the provided river_cross_section")

        b_water = max(wet_xs) - min(wet_xs)
        if "roughness_n" in river_sec and river_sec["roughness_n"]:
            r_inv = round(1.0 / float(river_sec["roughness_n"]), 4)
        else:
            r_inv = float(river_sec.get("roughness_inv", 22.22))
        if "hydraulic_slope" in river_sec and river_sec["hydraulic_slope"] is not None:
            slp_pm = float(river_sec["hydraulic_slope"]) * 1000.0
        else:
            slp_pm = float(river_sec.get("slope_permille", 0.5))

        hydro_dict: dict[str, Any] = {
            "area_m2": round(area_m2, 3),
            "wetted_perimeter_m": round(wetted_p_m, 3),
            "channel_width_m": round(float(river_sec.get("channel_width_m", b_water)), 3),
            "roughness_inv": r_inv,
            "slope_permille": round(slp_pm, 4),
        }
        if "design_discharge_m3_s" in river_sec and river_sec["design_discharge_m3_s"] is not None:
            hydro_dict["design_discharge_m3_s"] = float(river_sec["design_discharge_m3_s"])
        if "standard_span_m" in river_sec:
            hydro_dict["standard_span_m"] = float(river_sec["standard_span_m"])
        overrides["hydrology"] = hydro_dict
        summary["river_hydraulics"] = hydro_dict
        summary["ingested_sections"].append("river_cross_section")
    elif "hydrology" in sd and isinstance(sd["hydrology"], Mapping):
        overrides["hydrology"] = dict(sd["hydrology"])
        summary["ingested_sections"].append("hydrology")

    # 3. Geotechnical boreholes (pier & abutment) + backfill
    pier_bh = dict(sd.get("pier_borehole") or {})
    abut_bh = dict(sd.get("abutment_borehole") or {})
    raw_pier_layers = pier_bh.get("layers") or sd.get("pier_soil_layers") or sd.get("soil_layers")
    raw_abut_layers = abut_bh.get("layers") or sd.get("abutment_soil_layers")

    if raw_pier_layers:
        overrides["soil_layers"] = _normalize_soil_layers(raw_pier_layers)
        summary["pier_borehole_id"] = str(pier_bh.get("borehole_id", "ZK-PIER"))
        summary["ingested_sections"].append("pier_geotech")
    if raw_abut_layers:
        overrides["abutment_soil_layers"] = _normalize_soil_layers(raw_abut_layers)
        summary["abutment_borehole_id"] = str(abut_bh.get("borehole_id", "ZK-ABUT"))
        summary["ingested_sections"].append("abutment_geotech")

    if "confirmed_pile_length_m" in pier_bh:
        overrides["pile_length_m"] = float(pier_bh["confirmed_pile_length_m"])
    elif "pile_length_m" in sd:
        overrides["pile_length_m"] = float(sd["pile_length_m"])

    if "confirmed_pile_length_m" in abut_bh:
        overrides["abutment_pile_length_m"] = float(abut_bh["confirmed_pile_length_m"])
    elif "abutment_pile_length_m" in sd:
        overrides["abutment_pile_length_m"] = float(sd["abutment_pile_length_m"])

    if "backfill" in sd and isinstance(sd["backfill"], Mapping):
        overrides["abutment_backfill"] = dict(sd["backfill"])
        summary["ingested_sections"].append("abutment_backfill")

    # 4. Bearing positioning / span confirmation
    brg_pos = dict(sd.get("bearing_positioning") or {})
    if "beam_length_m" in brg_pos:
        overrides["beam_length_m"] = float(brg_pos["beam_length_m"])
    if "bearing_center_offset_m" in brg_pos:
        overrides["bearing_center_offset_m"] = float(brg_pos["bearing_center_offset_m"])
        summary["ingested_sections"].append("bearing_positioning")
    elif "bearing_center_offset_m" in sd:
        overrides["bearing_center_offset_m"] = float(sd["bearing_center_offset_m"])
        summary["ingested_sections"].append("bearing_positioning")

    return {"overrides": overrides, "summary": summary}


def _evaluate_seismic_verification(raw_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Audit E1/E2 seismic verification strictly from load cases, numerical demand/capacity results, and review record.

    Passing `seismic_checks: {"status": "pending"}` or an incomplete dict without E1 & E2 numerical results
    and review record MUST stay 'pending' (never falsely marked 'completed').
    """
    sc_raw = raw_cfg.get("seismic_checks")
    if not isinstance(sc_raw, Mapping) or not sc_raw:
        return {
            "status": "pending",
            "verified": False,
            "reason": "尚未提交 E1 弹性抗震与 E2 延性/抗剪及防落梁挡块验算工况、数值结果及复核记录",
        }

    raw_st = str(sc_raw.get("status", "")).strip().lower()
    if raw_st in {"pending", "todo", "incomplete", "draft", "unverified", ""}:
        return {
            "status": "pending",
            "verified": False,
            "reason": f"seismic_checks.status 为 '{raw_st or 'empty'}'，尚未完成实际抗震工况验算",
        }

    def _extract_case(case_obj: Any) -> tuple[bool, float | None, float | None, str]:
        if not isinstance(case_obj, Mapping):
            return False, None, None, "缺少工况结果字典"
        dem_val: float | None = None
        cap_val: float | None = None
        for dk in ("demand", "demand_knm", "demand_kn", "demand_mm", "displacement_mm"):
            if isinstance(case_obj.get(dk), (int, float)) and not isinstance(case_obj.get(dk), bool):
                dem_val = float(case_obj[dk])
                break
        for ck in ("capacity", "capacity_knm", "capacity_kn", "capacity_mm", "allowable_mm", "allowable"):
            if isinstance(case_obj.get(ck), (int, float)) and not isinstance(case_obj.get(ck), bool):
                cap_val = float(case_obj[ck])
                break
        if dem_val is None or cap_val is None or cap_val <= 0:
            return False, dem_val, cap_val, "缺少有效的数值型需求值(demand)或承载能力/容许值(capacity)"
        return True, dem_val, cap_val, str(case_obj.get("unit", "-"))

    ok_e1, dem_e1, cap_e1, unit_e1 = _extract_case(sc_raw.get("e1"))
    ok_e2, dem_e2, cap_e2, unit_e2 = _extract_case(sc_raw.get("e2"))
    reviewer = str(sc_raw.get("checked_by") or sc_raw.get("review_record") or "").strip()

    if raw_st == "failed" or (ok_e1 and dem_e1 > cap_e1) or (ok_e2 and dem_e2 > cap_e2):  # type: ignore[operator]
        bad_case = "E1" if (ok_e1 and dem_e1 > cap_e1) else "E2"  # type: ignore[operator]
        b_dem = dem_e1 if bad_case == "E1" else dem_e2
        b_cap = cap_e1 if bad_case == "E1" else cap_e2
        b_unit = unit_e1 if bad_case == "E1" else unit_e2
        return {
            "status": "failed",
            "verified": False,
            "failed_case": bad_case,
            "demand": b_dem,
            "capacity": b_cap,
            "unit": b_unit,
            "reason": f"{bad_case} 地震作用抗震验算超限：需求 {b_dem} > 能力 {b_cap} {b_unit}",
        }

    if not ok_e1 or not ok_e2:
        return {
            "status": "pending",
            "verified": False,
            "reason": "需同时提供 E1 设防地震与 E2 罕遇地震的数值型需求与抗力验算结果",
        }

    if not reviewer:
        return {
            "status": "pending",
            "verified": False,
            "reason": "E1/E2 数值结果已输入，但缺少复核人或复核记录 (checked_by / review_record)",
        }

    if raw_st in {"passed", "completed", "verified"}:
        return {
            "status": "completed",
            "verified": True,
            "e1": {"demand": dem_e1, "capacity": cap_e1, "unit": unit_e1, "passed": True},
            "e2": {"demand": dem_e2, "capacity": cap_e2, "unit": unit_e2, "passed": True},
            "review_record": reviewer,
            "reason": f"E1 ({dem_e1}≤{cap_e1}{unit_e1}) 与 E2 ({dem_e2}≤{cap_e2}{unit_e2}) 抗震验算通过，已复核：{reviewer}",
        }

    return {
        "status": "pending",
        "verified": False,
        "reason": f"未识别的抗震验算状态 '{raw_st}'",
    }


def collect_failed_structural_checks(
    *,
    section: dict[str, Any],
    girder_checks: dict[str, Any],
    slab_checks: dict[str, Any],
    bearing_checks: dict[str, Any],
    substructure_checks: dict[str, Any],
    abutment_checks: dict[str, Any] | None = None,
    seismic_verification: dict[str, Any] | None = None,
    consistency: dict[str, Any],
) -> list[dict[str, Any]]:
    """Aggregate every failed structural or consistency check with governing demand, allowable limit, and sheet IDs."""
    failed: list[dict[str, Any]] = []

    def _add(
        component: str,
        check_key: str,
        demand: float | str,
        allowable: float | str,
        unit: str,
        related_drawings: list[str],
        message: str,
    ) -> None:
        failed.append(
            {
                "component": component,
                "check_key": check_key,
                "demand": demand,
                "allowable": allowable,
                "unit": unit,
                "related_drawings": related_drawings,
                "message": message,
            }
        )

    mid_mm = section["mid_span_mm"]
    if not mid_mm.get("passes_efficiency", True):
        _add(
            "主梁跨中截面效率指标",
            "t_girder_section.efficiency_rho",
            mid_mm["efficiency_rho"],
            0.50,
            "-",
            ["QL-05", "QL-06", "QL-07"],
            f"T梁跨中截面效率指标 ρ={mid_mm['efficiency_rho']} < 0.50",
        )

    uf = girder_checks["uls_flexure"]
    if not uf["passed"]:
        _add(
            "主梁正截面抗弯承载力",
            "girder_checks.uls_flexure",
            uf["demand_md_knm"],
            uf["capacity_mu_knm"],
            "kN·m",
            ["QL-06", "QL-08", "QL-09"],
            f"主梁跨中抗弯不满足：Md={uf['demand_md_knm']} kN·m, Mu={uf['capacity_mu_knm']} kN·m (x={uf['compression_depth_x_mm']}/{uf['max_depth_xib_h0_mm']}mm)",
        )

    us = girder_checks["uls_shear"]
    if not us["passed"]:
        _add(
            "主梁斜截面抗剪承载力",
            "girder_checks.uls_shear",
            us["demand_vd_kn"],
            min(us["capacity_vu_kn"], us["size_upper_limit_kn"]),
            "kN",
            ["QL-06", "QL-08", "QL-09"],
            f"主梁支点抗剪不满足：Vd={us['demand_vd_kn']} kN > Vu={min(us['capacity_vu_kn'], us['size_upper_limit_kn'])} kN",
        )

    sc = girder_checks["sls_crack_and_stress"]
    if not sc["passed"]:
        _add(
            "主梁抗裂及施工/使用阶段应力",
            "girder_checks.sls_crack_and_stress",
            sc["net_bot_stress_freq_mpa"],
            0.0,
            "MPa",
            ["QL-08", "QL-09"],
            f"主梁正常使用抗裂或压/主拉应力超限 (频遇下缘净应力={sc['net_bot_stress_freq_mpa']} MPa, 压应力={max(sc['top_comp_char_mpa'], sc['bot_comp_char_mpa'])}/{sc['comp_allowable_mpa']} MPa)",
        )

    sd = girder_checks["sls_deflection"]
    if not sd["passed"]:
        _add(
            "主梁正常使用挠度",
            "girder_checks.sls_deflection",
            sd["live_deflection_mm"],
            sd["allowable_l_div_600_mm"],
            "mm",
            ["QL-06", "QL-08"],
            f"主梁活载挠度 f={sd['live_deflection_mm']} mm > L/600={sd['allowable_l_div_600_mm']} mm",
        )

    ds = slab_checks["deck_slab"]
    if not ds["passed"]:
        _add(
            "行车道翼缘板抗弯承载力",
            "slab_and_diaphragm_checks.deck_slab",
            ds["m_uls_knm_per_m"],
            ds["mu_capacity_knm_per_m"],
            "kN·m/m",
            ["QL-05", "QL-10"],
            f"翼缘板悬臂抗弯超限：Md={ds['m_uls_knm_per_m']} > Mu={ds['mu_capacity_knm_per_m']} kN·m/m",
        )

    dp = slab_checks["diaphragm"]
    if not dp["passed"]:
        _add(
            "中横隔梁抗弯承载力",
            "slab_and_diaphragm_checks.diaphragm",
            dp["m_uls_knm"],
            dp["mu_capacity_knm"],
            "kN·m",
            ["QL-06", "QL-11"],
            f"横隔梁抗弯超限：Md={dp['m_uls_knm']} > Mu={dp['mu_capacity_knm']} kN·m",
        )

    if not bearing_checks["passed"]:
        if not bearing_checks.get("stress_passed", True):
            _add(
                "板式橡胶支座抗压应力",
                "bearing_checks.compressive_stress",
                bearing_checks["compressive_stress_mpa"],
                bearing_checks["allowable_stress_mpa"],
                "MPa",
                ["QL-12", "QL-14", "QL-15"],
                f"支座压应力超限：σc={bearing_checks['compressive_stress_mpa']} MPa > {bearing_checks['allowable_stress_mpa']} MPa",
            )
        if not bearing_checks.get("shear_strain_passed", True):
            _add(
                "板式橡胶支座剪切变形",
                "bearing_checks.shear_strain",
                bearing_checks["shear_strain_tan_gamma"],
                bearing_checks["allowable_tan_gamma"],
                "-",
                ["QL-12"],
                f"支座水平剪切变形超限：tanγ={bearing_checks['shear_strain_tan_gamma']} > {bearing_checks['allowable_tan_gamma']}",
            )
        if not bearing_checks.get("min_stress_passed", True) or not bearing_checks.get("anti_uplift_passed", True):
            _add(
                "板式橡胶支座最小压应力及抗脱空",
                "bearing_checks.anti_uplift",
                bearing_checks["min_contact_stress_mpa"],
                bearing_checks.get("min_allowable_stress_mpa", 1.5),
                "MPa",
                ["QL-12", "QL-14", "QL-15"],
                f"支座最小压应力或抗脱空不满足：σmin={bearing_checks['min_contact_stress_mpa']} MPa, Rmin={bearing_checks['min_reaction_kn']} kN",
            )

    cb = substructure_checks["cap_beam"]
    if not cb["passed"]:
        if cb["m_hog_uls_knm"] > cb["mu_hog_capacity_knm"] or cb["m_sag_uls_knm"] > cb["mu_sag_capacity_knm"]:
            _add(
                "桥墩盖梁正截面抗弯承载力",
                "substructure_checks.cap_beam.flexure",
                cb["m_hog_uls_knm"],
                cb["mu_hog_capacity_knm"],
                "kN·m",
                ["QL-14", "QL-16"],
                f"盖梁抗弯承载力不足：Md={cb['m_hog_uls_knm']} kN·m > Mu={cb['mu_hog_capacity_knm']} kN·m",
            )
        if cb["v_uls_kn"] > cb["vu_capacity_kn"]:
            _add(
                "桥墩盖梁斜截面抗剪承载力",
                "substructure_checks.cap_beam.shear",
                cb["v_uls_kn"],
                cb["vu_capacity_kn"],
                "kN",
                ["QL-14", "QL-16"],
                f"盖梁抗剪承载力不足：Vd={cb['v_uls_kn']} kN > Vu={cb['vu_capacity_kn']} kN",
            )
        if cb["crack_width_mm"] > cb["crack_allowable_mm"]:
            _add(
                "桥墩盖梁最大裂缝宽度",
                "substructure_checks.cap_beam.crack_width",
                cb["crack_width_mm"],
                cb["crack_allowable_mm"],
                "mm",
                ["QL-14", "QL-16"],
                f"盖梁裂缝超限：wcr={cb['crack_width_mm']} mm > {cb['crack_allowable_mm']} mm",
            )

    pc = substructure_checks["pier_column"]
    if not pc["passed"]:
        _add(
            "桥墩双柱式墩柱偏心受压承载力",
            "substructure_checks.pier_column",
            pc["n_uls_kn"],
            pc["nu_capacity_kn"],
            "kN",
            ["QL-14", "QL-17"],
            f"墩柱偏心受压承载力不足：Nd={pc['n_uls_kn']} kN > Nu={pc['nu_capacity_kn']} kN",
        )

    bp = substructure_checks["bored_pile"]
    if not bp["passed"]:
        _add(
            "桥墩钻孔灌注桩单桩竖向承载力",
            "substructure_checks.bored_pile",
            bp["pile_top_sls_demand_kn"],
            bp["allowable_ra_kn"],
            "kN",
            ["QL-03", "QL-14", "QL-17"],
            f"桥墩钻孔灌注桩竖向承载力不足 (桩长 {bp['design_length_m']}m)：桩顶需求 R={bp['pile_top_sls_demand_kn']} kN > 容许值 [Ra]={bp['allowable_ra_kn']} kN",
        )

    if abutment_checks is not None:
        ab_cap = abutment_checks["abutment_cap"]
        if not ab_cap["passed"]:
            _add(
                "桩柱式桥台台帽承载力及裂缝验算",
                "abutment_checks.abutment_cap",
                max(ab_cap["m_hog_uls_knm"], ab_cap["m_sag_uls_knm"]),
                ab_cap["mu_hog_capacity_knm"],
                "kN·m",
                ["QL-15", "QL-18"],
                f"桥台台帽抗弯/抗剪或裂缝超限：Md={max(ab_cap['m_hog_uls_knm'], ab_cap['m_sag_uls_knm'])} kN·m, Mu={ab_cap['mu_hog_capacity_knm']} kN·m, wcr={ab_cap['crack_width_mm']} mm",
            )
        ab_bw = abutment_checks["backwall"]
        if not ab_bw["passed"]:
            _add(
                "桥台背墙台后土压力抗弯及锚固验算",
                "abutment_checks.backwall",
                ab_bw["m_uls_total_knm"],
                ab_bw["mu_capacity_total_knm"],
                "kN·m",
                ["QL-15", "QL-18"],
                f"桥台背墙抗弯或锚固不足：Md={ab_bw['m_uls_total_knm']} kN·m > Mu={ab_bw['mu_capacity_total_knm']} kN·m",
            )
        ab_ped = abutment_checks["pedestal_local_bearing"]
        if not ab_ped["passed"]:
            _add(
                "支座垫石局部承压承载力",
                "abutment_checks.pedestal_local_bearing",
                ab_ped["demand_fld_kn"],
                ab_ped["capacity_flu_kn"],
                "kN",
                ["QL-12", "QL-15", "QL-18"],
                f"支座垫石局压承载力不足：Fld={ab_ped['demand_fld_kn']} kN > Flu={ab_ped['capacity_flu_kn']} kN",
            )
        ab_pile = abutment_checks["bored_pile"]
        if not ab_pile["passed"]:
            if not ab_pile.get("bearing_passed", True):
                _add(
                    "桥台钻孔灌注桩单桩竖向承载力",
                    "abutment_checks.bored_pile",
                    ab_pile["pile_top_sls_demand_kn"],
                    ab_pile["allowable_ra_kn"],
                    "kN",
                    ["QL-03", "QL-15", "QL-18"],
                    f"桥台钻孔桩竖向承载力不足 (桩长 {ab_pile['design_length_m']}m)：R={ab_pile['pile_top_sls_demand_kn']} kN > [Ra]={ab_pile['allowable_ra_kn']} kN",
                )
            if not ab_pile.get("section_passed", True):
                _add(
                    "桥台钻孔灌注桩桩身偏压承载力",
                    "abutment_checks.bored_pile_section",
                    ab_pile["n_uls_kn"],
                    ab_pile["nu_section_capacity_kn"],
                    "kN",
                    ["QL-15", "QL-18"],
                    f"桥台钻孔桩桩身弯压承载力不足：Nd={ab_pile['n_uls_kn']} kN > Nu={ab_pile['nu_section_capacity_kn']} kN",
                )

    if seismic_verification is not None and seismic_verification.get("status") == "failed":
        _add(
            "桥梁 E1/E2 抗震性能验算",
            "seismic_verification",
            seismic_verification.get("demand", "exceeded"),
            seismic_verification.get("capacity", "allowable"),
            str(seismic_verification.get("unit", "-")),
            ["QL-01", "QL-14", "QL-17"],
            str(seismic_verification.get("reason", "E1/E2 抗震验算未通过")),
        )

    for iss in consistency.get("issues", []):
        if iss.get("severity") == "error":
            _add(
                "总体与构造尺寸一致性校核",
                f"consistency_audit.{iss['code']}",
                "conflict",
                "consistent",
                "-",
                ["QL-01", "QL-05", "QL-14", "QL-15"],
                str(iss["message"]),
            )

    return failed


def run_full_bridge_calculation(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an end-to-end Highway PC T-Girder Bridge calculation, ULS/SLS checks, abutment check, ledger, BOQ, and audit."""
    from .bridge_checks import (
        build_rebar_and_tendon_ledger,
        check_deck_slab_and_diaphragm,
        check_elastomeric_bearing,
        check_pile_bent_abutment,
        check_substructure_cap_pier_pile,
        check_t_girder_uls_sls,
        compute_bridge_bill_of_quantities,
    )

    user_raw_cfg = dict(params or {})
    survey_ingestion_summary: dict[str, Any] | None = None
    if isinstance(user_raw_cfg.get("survey_data"), Mapping):
        ingested = ingest_bridge_survey_data(user_raw_cfg["survey_data"])
        survey_ingestion_summary = ingested["summary"]
        # Explicit top-level keys take precedence over survey_data defaults, while survey_data counts as real basis!
        raw_cfg = _deep_merge_dict(ingested["overrides"], user_raw_cfg)
    else:
        raw_cfg = dict(user_raw_cfg)

    raw_sup_cfg = dict(raw_cfg.get("superstructure") or {})
    raw_sub_cfg = dict(raw_cfg.get("substructure") or {})
    raw_abut_cfg = dict(raw_cfg.get("abutment") or {})
    preset_name = str(raw_cfg.get("preset", "")).strip().lower()
    if preset_name == "wenchang":
        cfg = _deep_merge_dict(wenchang_bridge_preset(), raw_cfg)
    else:
        cfg = dict(raw_cfg)

    hydro_cfg = dict(
        cfg.get(
            "hydrology",
            {
                "area_m2": 898.525,
                "wetted_perimeter_m": 127.86,
                "roughness_inv": 22.22,
                "slope_permille": 0.5,
                "channel_width_m": 103.08,
                "standard_span_m": 25.0,
            },
        )
    )
    if "standard_span_m" in cfg:
        hydro_cfg.setdefault("standard_span_m", float(cfg["standard_span_m"]))
    hydro = compute_hydrology_and_span(**hydro_cfg)
    n_spans = int(cfg.get("n_spans", hydro["recommended_spans"]))
    std_span_m = float(cfg.get("standard_span_m", hydro["standard_span_m"]))

    ps_cfg = dict(cfg.get("prestress", {}))
    beam_len_m = float(cfg.get("beam_length_m", ps_cfg.get("beam_length_m", round(std_span_m - 0.04, 3))))

    sup_cfg = dict(cfg.get("superstructure", {}))
    if "bearing_center_offset_m" in cfg and "calc_span_m" not in raw_cfg and "calc_span_m" not in raw_sup_cfg:
        sup_cfg["calc_span_m"] = round(beam_len_m - 2.0 * float(cfg["bearing_center_offset_m"]), 4)
    elif "calc_span_m" in cfg:
        sup_cfg["calc_span_m"] = float(cfg["calc_span_m"])
    calc_span_m = float(sup_cfg.get("calc_span_m", 24.0))

    # Track unresolved / assumed site & basis parameters against raw_cfg (including ingested survey_data!)
    unresolved_assumptions: list[dict[str, Any]] = []
    if "flood_elevation_m" not in raw_cfg:
        unresolved_assumptions.append(
            {
                "field": "flood_elevation_m",
                "assumed_value": float(cfg.get("flood_elevation_m", 81.200)),
                "unit": "m",
                "impact": "控制梁底最低标高与立面图设计洪水位线",
                "required_source": "桥位河道1/100洪水频率水文断面实测资料",
            }
        )
    if "deck_elevation_m" not in raw_cfg:
        unresolved_assumptions.append(
            {
                "field": "deck_elevation_m",
                "assumed_value": float(cfg.get("deck_elevation_m", 86.500)),
                "unit": "m",
                "impact": "控制桥面设计高程及墩柱高度",
                "required_source": "路线纵断面设计高程表",
            }
        )
    if "hydrology" not in raw_cfg:
        unresolved_assumptions.append(
            {
                "field": "hydrology",
                "assumed_value": "preliminary_channel_hydraulic_model",
                "unit": "m3/s",
                "impact": "设计流量与桥孔净长复核依据（当前采用开题阶段概略河槽参数）",
                "required_source": "桥位河道实测横断面、糙率及水文频率计算资料",
            }
        )
    has_geotech_basis = (
        ("soil_layers" in raw_cfg)
        or ("soil_layers" in raw_sub_cfg)
        or ("abutment_soil_layers" in raw_cfg)
    )
    if not has_geotech_basis:
        unresolved_assumptions.append(
            {
                "field": "soil_layers",
                "assumed_value": "default_xinyang_alluvial_4_layers",
                "unit": "kPa",
                "impact": "桥墩与桥台钻孔灌注桩分层侧摩阻力 q_ik 与桩端承载力 f_a0 验算依据（当前采用开题报告概略地层初拟值）",
                "required_source": "桥位钻孔地质柱状图及地勘报告分层物理力学指标表",
            }
        )
    has_pile_len_basis = (
        ("explicit_pile_length_m" in raw_sub_cfg)
        or ("pile_length_m" in raw_cfg)
    )
    if not has_pile_len_basis:
        unresolved_assumptions.append(
            {
                "field": "pile_length_m",
                "assumed_value": "auto_friction_pile_pier_and_abutment",
                "unit": "m",
                "impact": "桥墩与桥台钻孔灌注桩设计桩长及水下C30混凝土工程量（当前分别按各自荷载及JTG 3363-2019摩擦桩公式初算）",
                "required_source": "结合实测钻孔地勘持力层顶面标高核定墩台最终桩长",
            }
        )
    has_span_basis = (
        ("calc_span_m" in raw_cfg)
        or ("calc_span_m" in raw_sup_cfg)
        or ("bearing_center_offset_m" in raw_cfg)
    )
    if not has_span_basis:
        unresolved_assumptions.append(
            {
                "field": "calc_span_m",
                "assumed_value": calc_span_m,
                "unit": "m",
                "impact": "主梁恒活载内力包络与挠度（开题报告仅给25m标准跨径，需由梁端至支座中心线定位距离核定实际计算跨径）",
                "required_source": "预制梁长及支座中心线距梁端构造定位尺寸确认",
            }
        )

    t_cfg = dict(cfg.get("t_girder", {}))
    lat_cfg = dict(cfg.get("lateral", {}))
    if "girder_spacing_m" in lat_cfg and "girder_spacing_mm" not in t_cfg:
        t_cfg["girder_spacing_mm"] = float(lat_cfg["girder_spacing_m"]) * 1000.0
    elif "girder_spacing_mm" in t_cfg and "girder_spacing_m" not in lat_cfg:
        lat_cfg["girder_spacing_m"] = float(t_cfg["girder_spacing_mm"]) / 1000.0
    t_cfg.setdefault("calc_span_mm", calc_span_m * 1000.0)
    lat_cfg.setdefault("calc_span_m", calc_span_m)

    # Auto-adapt default deck width when n_girders != 5 and deck_width_m was not explicitly supplied
    n_g_req = int(lat_cfg.get("n_girders", 5))
    sp_m_req = float(lat_cfg.get("girder_spacing_m", 2.348))
    if "deck_width_m" not in lat_cfg and (n_g_req != 5 or abs(sp_m_req - 2.348) > 1e-6):
        lat_cfg["deck_width_m"] = round((n_g_req - 1) * sp_m_req + 2.358, 4)

    section = compute_t_girder_section(**t_cfg)
    lateral = compute_lateral_distribution(**lat_cfg)

    # Upstream unified deck pavement + barrier superimposed dead load g2 (per girder, kN/m)
    asphalt_mm = float(cfg.get("asphalt_thick_mm", 110.0))
    conc_pav_mm = float(cfg.get("concrete_paving_thick_mm", 100.0))
    asphalt_unit_wt = float(cfg.get("asphalt_unit_weight_kn_m3", 24.0))
    conc_pav_unit_wt = float(cfg.get("concrete_paving_unit_weight_kn_m3", 25.0))
    barriers_total_kn_m = float(cfg.get("barriers_total_kn_m", 18.0))
    paving_total_kn_m2 = (asphalt_mm * 1e-3) * asphalt_unit_wt + (conc_pav_mm * 1e-3) * conc_pav_unit_wt
    q_asphalt_deck_kn_m = lateral["clear_width_m"] * (asphalt_mm * 1e-3) * asphalt_unit_wt
    q_conc_pav_deck_kn_m = lateral["clear_width_m"] * (conc_pav_mm * 1e-3) * conc_pav_unit_wt
    derived_g2_kn_m = round(
        (q_asphalt_deck_kn_m + q_conc_pav_deck_kn_m + barriers_total_kn_m) / lateral["n_girders"],
        4,
    )

    extra_consistency_issues: list[dict[str, Any]] = []
    if "superimposed_dead_kn_m" not in raw_sup_cfg:
        sup_cfg["superimposed_dead_kn_m"] = derived_g2_kn_m
    elif ("asphalt_thick_mm" in raw_cfg or "concrete_paving_thick_mm" in raw_cfg) and abs(
        float(raw_sup_cfg["superimposed_dead_kn_m"]) - derived_g2_kn_m
    ) > 1.5:
        extra_consistency_issues.append(
            {
                "code": "SUPERIMPOSED_DEAD_PAVING_MISMATCH",
                "severity": "error",
                "message": (
                    f"显式输入的二期恒载 ({raw_sup_cfg['superimposed_dead_kn_m']} kN/m) 与桥面铺装+护栏折算值 "
                    f"({derived_g2_kn_m} kN/m) 冲突"
                ),
            }
        )

    m_oq_gov = max(item["m_oq"] for item in lateral["lever_rule"])
    m_cq_gov = max(item["m_cq"] for item in lateral["eccentric_compression"])

    envelopes = compute_superstructure_envelopes(
        section_area_m2=section["mid_span_m"]["area_m2"],
        section_ix_m4=section["mid_span_m"]["ix_m4"],
        m_oq=m_oq_gov,
        m_cq=m_cq_gov,
        **sup_cfg,
    )
    envelopes["dead_load_breakdown"] = {
        "g1_girder_self_kn_m": round(
            section["mid_span_m"]["area_m2"] * float(sup_cfg.get("concrete_unit_weight_kn_m3", 26.0)),
            4,
        ),
        "diaphragm_equiv_kn_m": round(float(sup_cfg.get("diaphragm_equiv_kn_m", 3.20)), 4),
        "asphalt_paving_per_girder_kn_m": round(q_asphalt_deck_kn_m / lateral["n_girders"], 4),
        "concrete_paving_per_girder_kn_m": round(q_conc_pav_deck_kn_m / lateral["n_girders"], 4),
        "barriers_per_girder_kn_m": round(barriers_total_kn_m / lateral["n_girders"], 4),
        "superimposed_dead_kn_m": round(float(sup_cfg["superimposed_dead_kn_m"]), 4),
        "total_dead_kn_m": envelopes["dead_load_kn_m"],
    }

    if "sigma_con_mpa" in cfg:
        ps_cfg["sigma_con_mpa"] = float(cfg["sigma_con_mpa"])
    if "n_tendons" in cfg and cfg["n_tendons"] is not None:
        ps_cfg["n_tendons"] = cfg["n_tendons"]
    elif "tendon_count" in cfg and cfg["tendon_count"] is not None:
        ps_cfg["n_tendons"] = cfg["tendon_count"]
    if "strands_per_tendon" in cfg and cfg["strands_per_tendon"] is not None:
        ps_cfg["strands_per_tendon"] = int(cfg["strands_per_tendon"])
    ps_cfg.setdefault("beam_length_m", beam_len_m)

    prestress = compute_prestress_estimation_and_loss(
        m_sls_char_knm=envelopes["mid_span_summary"]["m_sls_char_knm"],
        m_uls_knm=envelopes["mid_span_summary"]["m_uls_knm"],
        girder_height_m=section["mid_span_mm"]["height"] * 1e-3,
        y_bot_m=section["mid_span_mm"]["y_bot"] * 1e-3,
        kern_upper_ks_m=section["mid_span_mm"]["kern_upper_ks"] * 1e-3,
        **ps_cfg,
    )

    # Distinguish single-span bearing reactions vs intermediate pier (left + right adjacent spans!)
    n_adj_spans = 2 if n_spans >= 2 else 1
    gamma0 = float(sup_cfg.get("safety_factor_gamma0", 1.1))
    load_grade = sup_cfg.get("load_grade", "highway-I")
    code_edition = sup_cfg.get("code_edition", "JTG-D60-2015")
    qk_2span, pk_2span = _highway_lane_load(
        n_adj_spans * envelopes["calc_span_m"],
        load_grade=load_grade,
        code_edition=code_edition,
    )
    mu_imp = float(envelopes["impact_factor_mu"])

    single_span_dead_per_girder_kn = round(abs(float(envelopes["support_summary"]["q_dead_kn"])), 2)
    left_span_dead_per_girder_kn = single_span_dead_per_girder_kn
    right_span_dead_per_girder_kn = single_span_dead_per_girder_kn if n_adj_spans == 2 else 0.0
    pier_dead_per_girder_kn = round(left_span_dead_per_girder_kn + right_span_dead_per_girder_kn, 2)

    per_girder_pier_uls_kn: list[float] = []
    per_girder_pier_sls_kn: list[float] = []
    per_girder_single_sls_max_kn: list[float] = []
    per_girder_single_sls_min_kn: list[float] = []
    per_girder_1span_live_kn: list[float] = []

    for g_idx in range(lateral["n_girders"]):
        m_oq_i = float(lateral["lever_rule"][g_idx]["m_oq"])
        m_cq_i = float(lateral["eccentric_compression"][g_idx]["m_cq"])
        g_env = compute_superstructure_envelopes(
            section_area_m2=section["mid_span_m"]["area_m2"],
            section_ix_m4=section["mid_span_m"]["ix_m4"],
            m_oq=m_oq_i,
            m_cq=m_cq_i,
            **sup_cfg,
        )
        r_live_1 = abs(float(g_env["support_summary"]["q_live_pos_kn"]))
        per_girder_1span_live_kn.append(round(r_live_1, 2))
        per_girder_single_sls_max_kn.append(round(abs(float(g_env["support_summary"]["q_sls_char_kn"])), 2))
        per_girder_single_sls_min_kn.append(round(abs(float(g_env["support_summary"]["q_dead_kn"])), 2))

        if n_adj_spans == 2:
            # Two-span intermediate pier influence line [-L, +L]: area = L, peak ordinate at pier = 1.0
            r_live_pier = (1.0 + mu_imp) * (m_cq_i * qk_2span * envelopes["calc_span_m"] + m_oq_i * 1.2 * pk_2span)
        else:
            r_live_pier = r_live_1

        per_girder_pier_sls_kn.append(round(pier_dead_per_girder_kn + r_live_pier, 2))
        per_girder_pier_uls_kn.append(round(gamma0 * (1.2 * pier_dead_per_girder_kn + 1.4 * r_live_pier), 2))

    cap_cfg = dict(cfg.get("cap_beam", {}))
    cap_cfg.setdefault("girder_reactions_kn", per_girder_pier_uls_kn)
    cap_cfg.setdefault("girder_spacing_m", lateral["girder_spacing_m"])
    default_cap_len = round((len(cap_cfg["girder_reactions_kn"]) - 1) * float(cap_cfg["girder_spacing_m"]) + 1.558, 4)
    cap_cfg.setdefault("cap_length_m", default_cap_len)
    cap_cfg.setdefault("column_spacing_m", round(max(2.0, float(cap_cfg["cap_length_m"]) - 4.30), 4))

    cap_beam = solve_continuous_cap_beam_1d(**cap_cfg)

    sub_user_cfg = dict(cfg.get("substructure", {}))
    n_cols = int(cap_cfg.get("n_columns", 2))
    cap_h_val = float(cap_cfg.get("cap_height_m", 1.50))
    col_h_val = float(sub_user_cfg.get("column_height_m", 6.00))
    bearing_ecc_m = float(cfg.get("pier_bearing_row_eccentricity_m", 0.28))
    # Single-span asymmetric live load + braking horizontal force longitudinal moment on pier column
    one_span_ecc_col_moment_uls_knm = (
        round(
            (
                gamma0 * 1.4 * sum(per_girder_1span_live_kn) * bearing_ecc_m
                + gamma0 * 1.4 * 85.0 * 0.5 * (cap_h_val + col_h_val)
            )
            / max(1, n_cols),
            2,
        )
        if n_adj_spans == 2
        else 0.0
    )

    pier_reaction_summary = {
        "n_adjacent_spans": n_adj_spans,
        "left_span_dead_per_girder_kn": left_span_dead_per_girder_kn,
        "right_span_dead_per_girder_kn": right_span_dead_per_girder_kn,
        "left_span_total_unfactored_dead_kn": round(left_span_dead_per_girder_kn * lateral["n_girders"], 2),
        "right_span_total_unfactored_dead_kn": round(right_span_dead_per_girder_kn * lateral["n_girders"], 2),
        "pier_total_unfactored_dead_kn": round(pier_dead_per_girder_kn * lateral["n_girders"], 2),
        "two_span_pier_sls_per_girder_kn": per_girder_pier_sls_kn,
        "two_span_pier_uls_per_girder_kn": per_girder_pier_uls_kn,
        "single_bearing_sls_max_per_girder_kn": per_girder_single_sls_max_kn,
        "single_bearing_sls_min_per_girder_kn": per_girder_single_sls_min_kn,
        "one_span_eccentric_col_moment_uls_knm": one_span_ecc_col_moment_uls_knm,
    }

    girder_checks = check_t_girder_uls_sls(
        section=section,
        envelopes=envelopes,
        prestress=prestress,
        beam_length_m=beam_len_m,
        **dict(cfg.get("girder_check_overrides", {})),
    )
    slab_checks = check_deck_slab_and_diaphragm(
        girder_spacing_m=lateral["girder_spacing_m"],
        asphalt_thick_m=asphalt_mm * 1e-3,
        concrete_pav_thick_m=conc_pav_mm * 1e-3,
        impact_mu=envelopes["impact_factor_mu"],
        calc_span_m=envelopes["calc_span_m"],
    )

    brg_cfg = dict(cfg.get("bearing", {}))
    brg_cfg.setdefault("max_sls_reaction_kn", max(per_girder_single_sls_max_kn))
    brg_cfg.setdefault("min_sls_reaction_kn", min(per_girder_single_sls_min_kn))
    brg_cfg.setdefault("bearing_length_mm", 450.0 if brg_cfg["max_sls_reaction_kn"] > 880.0 else 400.0)
    brg_cfg.setdefault("bearing_thick_mm", 85.0 if brg_cfg["max_sls_reaction_kn"] > 880.0 else 74.0)
    brg_cfg.setdefault("calc_span_m", envelopes["calc_span_m"])
    bearing_checks = check_elastomeric_bearing(**brg_cfg)

    pier_soil_layers = sub_user_cfg.get("soil_layers", cfg.get("soil_layers"))
    explicit_pile_m = cfg.get("pile_length_m", sub_user_cfg.get("explicit_pile_length_m"))
    substructure_checks = check_substructure_cap_pier_pile(
        cap_beam_1d=cap_beam,
        cap_width_m=float(cap_cfg.get("cap_width_m", 1.70)),
        cap_height_m=cap_h_val,
        column_diameter_m=float(sub_user_cfg.get("column_diameter_m", 1.20)),
        column_height_m=col_h_val,
        pile_diameter_m=float(sub_user_cfg.get("pile_diameter_m", 1.40)),
        concrete_grade=str(sub_user_cfg.get("concrete_grade", "C30")),
        soil_layers=pier_soil_layers,
        explicit_pile_length_m=float(explicit_pile_m) if explicit_pile_m is not None else None,
        longitudinal_moment_uls_knm=one_span_ecc_col_moment_uls_knm,
    )

    # Independent Pile-Bent Abutment structural & foundation calculation (0# / N# abutments)
    abut_user_cfg = dict(cfg.get("abutment", {}))
    backfill_cfg = dict(cfg.get("abutment_backfill") or abut_user_cfg.get("backfill") or {})
    abut_soil_layers = (
        cfg.get("abutment_soil_layers")
        or raw_abut_cfg.get("soil_layers")
        or pier_soil_layers
    )
    explicit_abut_pile_m = cfg.get("abutment_pile_length_m", abut_user_cfg.get("explicit_pile_length_m"))
    brg_parts = bearing_checks["bearing_model"].replace("GJZ", "").strip().split("×")
    brg_w_mm = float(brg_parts[0]) if len(brg_parts) == 3 else 300.0
    brg_l_mm = float(brg_parts[1]) if len(brg_parts) == 3 else 400.0

    abutment_checks = check_pile_bent_abutment(
        single_span_dead_per_girder_kn=single_span_dead_per_girder_kn,
        single_span_live_per_girder_kn=per_girder_1span_live_kn,
        girder_spacing_m=lateral["girder_spacing_m"],
        deck_width_m=lateral["deck_width_m"],
        clear_width_m=lateral["clear_width_m"],
        cap_width_m=float(abut_user_cfg.get("cap_width_m", 1.80)),
        cap_height_m=float(abut_user_cfg.get("cap_height_m", 1.40)),
        backwall_thick_m=float(abut_user_cfg.get("backwall_thick_m", 0.55)),
        backwall_height_m=float(abut_user_cfg.get("backwall_height_m", 1.75)),
        earwall_length_m=float(abut_user_cfg.get("earwall_length_m", 2.00)),
        earwall_thick_m=float(abut_user_cfg.get("earwall_thick_m", 0.45)),
        paving_total_kn_m2=paving_total_kn_m2,
        n_piles=int(abut_user_cfg.get("n_piles", 2)),
        pile_spacing_m=abut_user_cfg.get("pile_spacing_m"),
        pile_diameter_m=float(abut_user_cfg.get("pile_diameter_m", sub_user_cfg.get("pile_diameter_m", 1.40))),
        concrete_grade=str(abut_user_cfg.get("concrete_grade", sub_user_cfg.get("concrete_grade", "C30"))),
        backfill_gamma_kn_m3=float(backfill_cfg.get("gamma_kn_m3", abut_user_cfg.get("backfill_gamma_kn_m3", 19.0))),
        backfill_phi_deg=float(backfill_cfg.get("phi_deg", abut_user_cfg.get("backfill_phi_deg", 32.0))),
        embankment_fill_height_m=float(backfill_cfg.get("fill_height_m", abut_user_cfg.get("embankment_fill_height_m", 4.50))),
        safety_factor_gamma0=gamma0,
        soil_layers=abut_soil_layers,
        explicit_pile_length_m=float(explicit_abut_pile_m) if explicit_abut_pile_m is not None else None,
        bearing_width_mm=brg_w_mm,
        bearing_length_mm=brg_l_mm,
    )

    unresolved_field_set = {str(u["field"]) for u in unresolved_assumptions}
    pier_pile_len_m = float(substructure_checks["bored_pile"]["design_length_m"])
    abut_pile_len_m = float(abutment_checks["bored_pile"]["design_length_m"])
    for u_item in unresolved_assumptions:
        if u_item["field"] == "pile_length_m":
            u_item["assumed_value"] = f"桥墩桩长 {pier_pile_len_m:g}m / 桥台桩长 {abut_pile_len_m:g}m"

    ledger = build_rebar_and_tendon_ledger(
        n_spans=n_spans,
        n_girders_per_span=lateral["n_girders"],
        beam_length_m=beam_len_m,
        girder_height_m=section["mid_span_mm"]["height"] * 1e-3,
        girder_spacing_m=lateral["girder_spacing_m"],
        deck_width_m=lateral["deck_width_m"],
        cap_length_m=cap_beam["cap_length_m"],
        column_height_m=float(substructure_checks["pier_column"]["height_m"]),
        pile_length_m=pier_pile_len_m,
        prestress=prestress,
        girder_checks=girder_checks,
        slab_checks=slab_checks,
        sub_checks=substructure_checks,
        abutment_checks=abutment_checks,
        envelopes=envelopes,
        lateral=lateral,
        unresolved_fields=unresolved_field_set,
        standard_span_m=std_span_m,
    )
    boq = compute_bridge_bill_of_quantities(
        n_spans=n_spans,
        n_girders_per_span=lateral["n_girders"],
        beam_length_m=beam_len_m,
        section_area_m2=section["mid_span_m"]["area_m2"],
        deck_width_m=lateral["deck_width_m"],
        asphalt_thick_m=asphalt_mm * 1e-3,
        concrete_pav_thick_m=conc_pav_mm * 1e-3,
        cap_length_m=cap_beam["cap_length_m"],
        cap_width_m=float(cap_cfg.get("cap_width_m", 1.70)),
        cap_height_m=cap_h_val,
        column_diameter_m=float(substructure_checks["pier_column"]["diameter_m"]),
        column_height_m=float(substructure_checks["pier_column"]["height_m"]),
        pile_diameter_m=float(substructure_checks["bored_pile"]["diameter_m"]),
        pile_length_m=pier_pile_len_m,
        abutment_pile_length_m=abut_pile_len_m,
        bearing_model=bearing_checks["bearing_model"],
        ledger=ledger,
        standard_span_m=std_span_m,
        clear_width_m=lateral["clear_width_m"],
    )

    actual_pedestals = len(cap_beam["girder_positions_m"])
    actual_ped_spacing_mm = (
        round((cap_beam["girder_positions_m"][1] - cap_beam["girder_positions_m"][0]) * 1000.0, 3)
        if actual_pedestals >= 2
        else lateral["girder_spacing_m"] * 1000.0
    )
    n_carr = int(cfg.get("n_carriageways", 2))
    med_m = float(cfg.get("median_width_m", 1.00 if n_carr > 1 else 0.0))
    default_consistency = {
        "n_carriageways": n_carr,
        "single_deck_width_m": lateral["deck_width_m"],
        "clear_width_m": lateral["clear_width_m"],
        "lateral_clearance_m": lateral["lateral_clearance_m"],
        "roadway_width_m": lateral["roadway_width_m"],
        "barrier_width_m": float(lat_cfg.get("left_barrier_m", 0.50)),
        "median_width_m": med_m,
        "total_deck_width_m": n_carr * lateral["deck_width_m"] + (med_m if n_carr > 1 else 0.0),
        "n_girders": lateral["n_girders"],
        "girder_spacing_mm": lateral["girder_spacing_m"] * 1000.0,
        "edge_cantilever_mm": lateral["edge_cantilever_m"] * 1000.0,
        "standard_span_m": std_span_m,
        "calc_span_m": envelopes["calc_span_m"],
        "pier_bearing_spacing_mm": actual_ped_spacing_mm,
        "n_pier_pedestals": actual_pedestals,
        "cap_length_m": cap_beam["cap_length_m"],
        "pavement_layers": [
            {"name": "沥青混凝土面层", "thickness": asphalt_mm, "unit": "mm"},
            {"name": "C40混凝土铺装层", "thickness": conc_pav_mm, "unit": "mm"},
        ],
    }
    if "consistency" in cfg:
        merged_cons = {**default_consistency, **cfg["consistency"]}
    else:
        merged_cons = default_consistency
    consistency = audit_bridge_design_consistency(merged_cons)
    if extra_consistency_issues:
        consistency["issues"].extend(extra_consistency_issues)
        consistency["issue_count"] = len(consistency["issues"])
        consistency["error_count"] = sum(1 for i in consistency["issues"] if i["severity"] == "error")
        consistency["status"] = "passed" if consistency["error_count"] == 0 else "failed"

    seismic_verification = _evaluate_seismic_verification(raw_cfg)
    failed_checks = collect_failed_structural_checks(
        section=section,
        girder_checks=girder_checks,
        slab_checks=slab_checks,
        bearing_checks=bearing_checks,
        substructure_checks=substructure_checks,
        abutment_checks=abutment_checks,
        seismic_verification=seismic_verification,
        consistency=consistency,
    )

    confirmed_deliv = dict(raw_cfg.get("deliverables_confirmed") or {})
    thesis_req = dict(raw_cfg.get("thesis_requirements") or {})
    thesis_note = str(
        thesis_req.get(
            "note",
            "开题报告写明正文约一万字（任务书未列1.5万字硬性下限）；生成毕业设计说明书时应按学校模板及指导教师确认的要求执行。",
        )
    )
    survey_complete = all(
        k in raw_cfg for k in ("flood_elevation_m", "deck_elevation_m", "hydrology")
    ) and has_geotech_basis
    seismic_deliv_status = "completed" if seismic_verification["verified"] else (
        "failed" if seismic_verification["status"] == "failed" else "pending"
    )
    pending_graduation_deliverables = [
        {
            "id": "design_specification_thesis",
            "title": "毕业设计完整计算说明书（含中英文摘要、桥型比选、计算过程、对算校核与参考文献）",
            "status": "completed" if confirmed_deliv.get("thesis_document") is True else "pending",
            "note": thesis_note,
        },
        {
            "id": "site_hydrology_and_geotech_survey",
            "title": "桥位实测水文断面及钻孔地质勘察依据核定",
            "status": "completed" if survey_complete else "pending",
            "note": "需补充实测河床断面、1/100设计洪水位、路线纵断面设计高程及墩台钻孔柱状图分层摩阻力实测值。",
        },
        {
            "id": "seismic_e1_e2_verification",
            "title": "开题报告列出的 E1 / E2 地震作用抗震验算",
            "status": seismic_deliv_status,
            "note": (
                seismic_verification["reason"]
                if seismic_verification["verified"] or seismic_verification["status"] == "failed"
                else f"需凭实际 E1/E2 地震工况、数值需求/抗力结果及复核记录确认完成（{seismic_verification['reason']}）。"
            ),
        },
    ]

    # Build explicit list of engineering conclusions still resting on preliminary assumptions
    assumption_dependent_conclusions: list[dict[str, Any]] = []
    if unresolved_field_set & {"hydrology", "flood_elevation_m", "deck_elevation_m"}:
        dep_h = sorted(unresolved_field_set & {"hydrology", "flood_elevation_m", "deck_elevation_m"})
        assumption_dependent_conclusions.append(
            {
                "conclusion_id": "span_layout_and_freeboard",
                "title": "桥孔跨径布置与梁底净空高程合规性",
                "current_conclusion": f"推荐桥孔布置 {n_spans}×{std_span_m:g}m，桥面高程 {float(cfg.get('deck_elevation_m', 86.5)):.3f}m，设计洪水位 {float(cfg.get('flood_elevation_m', 81.2)):.3f}m",
                "depends_on_fields": dep_h,
                "status_note": "仍基于开题概略河槽水力参数与初拟高程，待桥位实测水文横断面与路线纵断面核定。",
            }
        )
    if "calc_span_m" in unresolved_field_set:
        assumption_dependent_conclusions.append(
            {
                "conclusion_id": "superstructure_internal_forces_and_tendons",
                "title": "预制T梁跨中弯矩/支点剪力包络及预应力配束结论",
                "current_conclusion": f"按初拟计算跨径 L={envelopes['calc_span_m']:g}m 求得跨中 Md={girder_checks['uls_flexure']['demand_md_knm']} kN·m ≤ Mu={girder_checks['uls_flexure']['capacity_mu_knm']} kN·m，配 {prestress['recommended_tendons']}束×{prestress['strands_per_tendon']}股钢绞线",
                "depends_on_fields": ["calc_span_m"],
                "status_note": "仍基于初拟计算跨径，待梁端至支座中心线定位尺寸最终核定。",
            }
        )
    if unresolved_field_set & {"soil_layers", "pile_length_m"}:
        dep_g = sorted(unresolved_field_set & {"soil_layers", "pile_length_m"})
        assumption_dependent_conclusions.append(
            {
                "conclusion_id": "pier_and_abutment_pile_foundation",
                "title": "桥墩与桥台钻孔灌注桩桩长及水下混凝土工程量",
                "current_conclusion": (
                    f"桥墩桩长 {pier_pile_len_m:g}m (R={substructure_checks['bored_pile']['pile_top_sls_demand_kn']}≤[Ra]={substructure_checks['bored_pile']['allowable_ra_kn']} kN)；"
                    f"桥台桩长 {abut_pile_len_m:g}m (R={abutment_checks['bored_pile']['pile_top_sls_demand_kn']}≤[Ra]={abutment_checks['bored_pile']['allowable_ra_kn']} kN)"
                ),
                "depends_on_fields": dep_g,
                "status_note": "仍基于开题冲洪积四层地层初拟摩阻力参数按 JTG 3363-2019 推导，待墩台位实测钻孔柱状图核定。",
            }
        )

    calculation_status = "passed" if (not failed_checks and consistency["status"] == "passed") else "failed"
    incomplete_delivs = [d for d in pending_graduation_deliverables if d["status"] != "completed"]
    if calculation_status == "failed":
        graduation_readiness_status = "failed"
        status_summary_zh = f"结构计算未通过（共 {len(failed_checks)} 项验算或一致性超限）"
    elif unresolved_assumptions or incomplete_delivs:
        graduation_readiness_status = "pending_confirmation"
        status_summary_zh = (
            f"当前参数下结构计算通过；毕业设计资料待齐套确认"
            f"（待核定初拟参数 {len(unresolved_assumptions)} 项，待完成毕设交付物 {len(incomplete_delivs)} 项）"
        )
    else:
        graduation_readiness_status = "ready"
        status_summary_zh = "结构计算通过，且实测水文地勘依据与毕业设计交付项已全部确认齐套"

    from .bridge_calc_book import (
        build_defense_verification_table,
        compute_independent_hand_cross_check,
    )

    bridge_schemes = list(
        cfg.get("bridge_schemes")
        or default_opening_report_schemes(n_spans, std_span_m, lateral["deck_width_m"])
    )
    references_list = list(cfg.get("references") or default_graduation_references())
    references_audit = audit_references_compliance(references_list)
    fem_ingested = ingest_independent_fem_results(raw_cfg.get("independent_fem_results"))

    report_out: dict[str, Any] = {
        "status": calculation_status,
        "calculation_status": calculation_status,
        "graduation_readiness_status": graduation_readiness_status,
        "status_summary_zh": status_summary_zh,
        "failed_checks": failed_checks,
        "project_summary": {
            "project_name": str(cfg.get("project_name", f"{n_spans}×{std_span_m:g}m 预应力混凝土简支T梁桥")),
            "n_spans": n_spans,
            "standard_span_m": std_span_m,
            "beam_length_m": beam_len_m,
            "calc_span_m": envelopes["calc_span_m"],
            "deck_width_m": lateral["deck_width_m"],
            "clear_width_m": lateral["clear_width_m"],
            "roadway_width_m": lateral["roadway_width_m"],
            "cross_slope_pct": float(cfg.get("cross_slope_pct", 2.0)),
            "asphalt_thick_mm": asphalt_mm,
            "concrete_paving_thick_mm": conc_pav_mm,
            "flood_elevation_m": float(cfg.get("flood_elevation_m", 81.200)),
            "deck_elevation_m": float(cfg.get("deck_elevation_m", 86.500)),
            "pier_pile_length_m": pier_pile_len_m,
            "abutment_pile_length_m": abut_pile_len_m,
            "n_tendons": int(prestress["recommended_tendons"]),
            "strands_per_tendon": int(prestress["strands_per_tendon"]),
        },
        "bridge_schemes": bridge_schemes,
        "references": references_list,
        "references_audit": references_audit,
        "unresolved_assumptions": unresolved_assumptions,
        "assumption_dependent_conclusions": assumption_dependent_conclusions,
        "pending_graduation_deliverables": pending_graduation_deliverables,
        "seismic_verification": seismic_verification,
        "survey_ingestion_summary": survey_ingestion_summary,
        "independent_fem_results": fem_ingested,
        "hydrology": hydro,
        "t_girder_section": section,
        "lateral_distribution": lateral,
        "superstructure_envelopes": envelopes,
        "prestress": prestress,
        "pier_reaction_summary": pier_reaction_summary,
        "pier_cap_beam_1d": cap_beam,
        "girder_checks": girder_checks,
        "slab_and_diaphragm_checks": slab_checks,
        "bearing_checks": bearing_checks,
        "substructure_checks": substructure_checks,
        "abutment_checks": abutment_checks,
        "rebar_and_tendon_ledger": ledger,
        "bill_of_quantities": boq,
        "consistency_audit": consistency,
    }
    report_out["independent_cross_check"] = compute_independent_hand_cross_check(report_out)
    report_out["defense_verification_table"] = build_defense_verification_table(report_out, fem_ingested)
    return report_out
