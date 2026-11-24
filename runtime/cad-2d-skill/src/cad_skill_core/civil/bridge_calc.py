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

import csv
import io
import math
from pathlib import Path
import re
from typing import Any, Literal, Sequence


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
    q_s = design_discharge_m3_s if design_discharge_m3_s is not None else total_discharge_q

    net_opening_l_j = stability_k * ((q_s / channel_discharge_q_c) ** stability_exp_n) * channel_width_m
    recommended_spans = max(1, math.ceil(net_opening_l_j / standard_span_m))
    total_bridge_length_m = recommended_spans * standard_span_m

    return {
        "hydraulic_radius_m": round(hydraulic_radius_m, 4),
        "roughness_inv": round(inv_n, 4),
        "slope_i": slope_i,
        "main_channel_velocity_m_s": round(velocity_m_s, 4),
        "channel_discharge_m3_s": round(channel_discharge_q_c, 3),
        "design_discharge_m3_s": round(total_discharge_q, 3),
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
    calc_span_m: float = 24.50,
    design_lanes: int = 2,
) -> dict[str, Any]:
    """Compute Lever Rule (m_oq) and Courbon Eccentric Compression (m_cq) lateral distribution factors."""

    if n_girders < 2:
        raise ValueError("n_girders must be >= 2")
    if girder_spacing_m <= 0 or deck_width_m <= 0:
        raise ValueError("girder_spacing_m and deck_width_m must be > 0")

    # Transverse coordinate x measured from bridge/carriageway centerline [-B/2, +B/2]
    half_deck = deck_width_m / 2.0
    x_road_left = -half_deck + left_barrier_m
    x_road_right = half_deck - right_barrier_m

    # Girder coordinates: girder 1 at leftmost (-), girder n at rightmost (+)
    girder_x = [((i - 0.5 * (n_girders - 1)) * girder_spacing_m) for i in range(n_girders)]
    # In Courbon notation a_1 > 0 for girder 1: a_i = -girder_x[i]
    a_offsets = [-gx for gx in girder_x]
    sum_a2 = sum(a * a for a in a_offsets)

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
            _lever_eta, x_road_left, x_road_right, design_lanes
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

        eta_at_g1 = _courbon_eta(girder_x[0])
        eta_at_gn = _courbon_eta(girder_x[-1])
        m_cq, wheels_cq, lanes_cq = _maximize_vehicle_wheels_on_influence_line(
            _courbon_eta, x_road_left, x_road_right, design_lanes
        )
        courbon_results.append(
            {
                "girder_no": g_no,
                "a_m": round(ai, 4),
                "eta_g1": round(eta_at_g1, 4),
                "eta_gn": round(eta_at_gn, 4),
                "m_cq": round(m_cq, 4),
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
        "deck_width_m": round(deck_width_m, 4),
        "roadway_width_m": round(x_road_right - x_road_left, 4),
        "sum_a2_m2": round(sum_a2, 4),
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
    beam_length_m: float = 24.607,
    bend_angle_deg: float = 8.5,
    friction_mu: float = 0.25,
    wobble_k: float = 0.0015,
    anchorage_slip_mm: float = 6.0,
    ep_mpa: float = 195000.0,
) -> dict[str, Any]:
    """Estimate required 1860MPa strands/tendons and compute longitudinal prestress loss profile."""

    c1_emp = 0.51
    a_uls_emp = 0.76
    ep_ecc_m = y_bot_m - centroid_cover_ap_m
    ap1_m2 = strand_area_mm2 * 1e-6

    n_strands_sls = (m_sls_char_knm * 1e3) / (c1_emp * ap1_m2 * (fpk_mpa * 1e6) * (kern_upper_ks_m + ep_ecc_m))
    n_strands_uls = (m_uls_knm * 1e3) / (a_uls_emp * girder_height_m * (fpd_mpa * 1e6) * ap1_m2)

    gov_strands = max(n_strands_sls, n_strands_uls)
    n_tendons = max(2, math.ceil(gov_strands / strands_per_tendon))
    provided_strands = n_tendons * strands_per_tendon

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
        "eccentricity_ep_m": round(ep_ecc_m, 4),
        "required_strands_sls": round(n_strands_sls, 2),
        "required_strands_uls": round(n_strands_uls, 2),
        "recommended_tendons": n_tendons,
        "strands_per_tendon": strands_per_tendon,
        "provided_strands": provided_strands,
        "tendon_designation": f"{n_tendons}-Φs15.2-{strands_per_tendon}",
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
    half_cap = 0.5 * cap_length_m

    # Support coordinates (centered on cap beam [0, cap_length_m])
    col_xs = [
        round(half_cap + (i - 0.5 * (n_columns - 1)) * column_spacing_m, 6)
        for i in range(n_columns)
    ]
    n_girders = len(girder_reactions_kn)
    girder_xs = [
        round(half_cap + (i - 0.5 * (n_girders - 1)) * girder_spacing_m, 6)
        for i in range(n_girders)
    ]

    # Build sorted mesh nodes including ends, columns, girders, quarter/mid points
    raw_nodes = {0.0, round(cap_length_m, 6), round(half_cap, 6), *col_xs, *girder_xs}
    for k in range(21):
        raw_nodes.add(round(cap_length_m * k / 20.0, 6))
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
        "support_reactions": reactions_kn,
        "max_sagging_moment_knm": round(max_sag_m, 2),
        "max_hogging_moment_knm": round(min_hog_m, 2),
        "max_abs_shear_kn": round(max_abs_q, 2),
        "diagram": diagram_points,
    }


def audit_bridge_design_consistency(spec: dict[str, Any]) -> dict[str, Any]:
    """Cross-audit deck width, girder/bearing alignment, pavement units, and limit-state tables."""

    issues: list[dict[str, Any]] = []

    # 1. Deck width consistency check (catches the 11.5m vs 11.75m / 24.0m vs 24.5m thesis discrepancy!)
    n_decks = int(spec.get("n_carriageways", 2))
    single_deck_m = spec.get("single_deck_width_m")
    roadway_m = spec.get("roadway_width_m")
    barrier_m = float(spec.get("barrier_width_m", 0.50))
    median_m = float(spec.get("median_width_m", 1.0 if n_decks == 2 else 0.0))
    total_deck_m = spec.get("total_deck_width_m")

    if single_deck_m is not None and roadway_m is not None:
        expected_single = float(roadway_m) + 2.0 * barrier_m
        if abs(float(single_deck_m) - expected_single) > 0.01:
            issues.append(
                {
                    "code": "DECK_SINGLE_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"单幅桥面宽 ({single_deck_m}m) 与 行车道宽+两侧护栏 "
                        f"({roadway_m} + 2×{barrier_m} = {expected_single:.3f}m) 不一致"
                    ),
                }
            )

    if total_deck_m is not None and single_deck_m is not None:
        expected_total = n_decks * float(single_deck_m) + median_m
        if abs(float(total_deck_m) - expected_total) > 0.01:
            issues.append(
                {
                    "code": "DECK_TOTAL_WIDTH_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"桥梁总宽 ({total_deck_m}m) 与 {n_decks}×单幅({single_deck_m}m)+中央分隔带({median_m}m) "
                        f"= {expected_total:.3f}m 冲突"
                    ),
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


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


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
    parsed_checks: list[dict[str, Any]] = []
    for r_idx, r in enumerate(rows[1:], start=1):
        if len(r) < 2:
            continue
        sec_label = r[0]
        # Pair up (value, allowable, optional pass/fail) columns
        i = 1
        pair_idx = 1
        while i + 1 < len(r):
            m1 = _NUM_RE.search(r[i])
            m2 = _NUM_RE.search(r[i + 1])
            if m1 and m2:
                val = float(m1.group(0))
                lim = float(m2.group(0))
                claimed: bool | None = None
                step = 2
                if i + 2 < len(r) and r[i + 2] in {"是", "否", "满足", "不满足", "PASS", "FAIL", "true", "false"}:
                    claimed = r[i + 2] in {"是", "满足", "PASS", "true"}
                    step = 3
                metric_name = headers[i] if i < len(headers) else f"check_{pair_idx}"
                parsed_checks.append(
                    {
                        "section": sec_label,
                        "metric": metric_name,
                        "value": val,
                        "allowable": lim,
                        "claimed_pass": claimed,
                    }
                )
                pair_idx += 1
                i += step
            else:
                i += 1

    audit_res = audit_bridge_design_consistency({"limit_checks": parsed_checks})
    return {
        "row_count": len(rows) - 1,
        "parsed_check_count": len(parsed_checks),
        "parsed_checks": parsed_checks,
        "audit": audit_res,
    }


def run_full_bridge_calculation(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an end-to-end Highway PC T-Girder Bridge calculation + consistency audit."""

    cfg = dict(params or {})
    hydro = compute_hydrology_and_span(**cfg.get("hydrology", {
        "area_m2": 898.525,
        "wetted_perimeter_m": 127.86,
        "roughness_inv": 22.22,
        "slope_permille": 0.5,
        "channel_width_m": 103.08,
        "standard_span_m": 25.0,
    }))
    section = compute_t_girder_section(**cfg.get("t_girder", {}))
    lateral = compute_lateral_distribution(**cfg.get("lateral", {}))

    m_oq_gov = max(item["m_oq"] for item in lateral["lever_rule"])
    m_cq_gov = max(item["m_cq"] for item in lateral["eccentric_compression"])

    envelopes = compute_superstructure_envelopes(
        section_area_m2=section["mid_span_m"]["area_m2"],
        section_ix_m4=section["mid_span_m"]["ix_m4"],
        m_oq=m_oq_gov,
        m_cq=m_cq_gov,
        **cfg.get("superstructure", {}),
    )
    prestress = compute_prestress_estimation_and_loss(
        m_sls_char_knm=envelopes["mid_span_summary"]["m_sls_char_knm"],
        m_uls_knm=envelopes["mid_span_summary"]["m_uls_knm"],
        girder_height_m=section["mid_span_mm"]["height"] * 1e-3,
        y_bot_m=section["mid_span_mm"]["y_bot"] * 1e-3,
        kern_upper_ks_m=section["mid_span_mm"]["kern_upper_ks"] * 1e-3,
        **cfg.get("prestress", {}),
    )
    cap_beam = solve_continuous_cap_beam_1d(**cfg.get("cap_beam", {}))
    consistency = audit_bridge_design_consistency(
        cfg.get(
            "consistency",
            {
                "n_carriageways": 2,
                "single_deck_width_m": lateral["deck_width_m"],
                "roadway_width_m": lateral["roadway_width_m"],
                "barrier_width_m": 0.50,
                "median_width_m": 1.00,
                "total_deck_width_m": 2.0 * lateral["deck_width_m"] + 1.00,
                "n_girders": lateral["n_girders"],
                "girder_spacing_mm": lateral["girder_spacing_m"] * 1000.0,
                "pier_bearing_spacing_mm": lateral["girder_spacing_m"] * 1000.0,
                "n_pier_pedestals": lateral["n_girders"],
                "pavement_layers": [
                    {"name": "沥青混凝土面层", "thickness": 110, "unit": "mm"},
                    {"name": "C40混凝土铺装层", "thickness": 100, "unit": "mm"},
                ],
            },
        )
    )
    return {
        "hydrology": hydro,
        "t_girder_section": section,
        "lateral_distribution": lateral,
        "superstructure_envelopes": envelopes,
        "prestress": prestress,
        "pier_cap_beam_1d": cap_beam,
        "consistency_audit": consistency,
    }
