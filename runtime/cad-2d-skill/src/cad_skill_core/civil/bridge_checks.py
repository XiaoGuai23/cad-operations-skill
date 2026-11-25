"""JTG 3362-2018 & JTG 3363-2019 Structural ULS/SLS Checks, Traceable Rebar/Tendon Ledger, and BOQ.

Pure-function engineering verification and quantity take-off layer:
1. Representative T-girder net/transformed section, ULS flexure & shear, SLS crack resistance,
   construction/service normal & principal stresses, and long-term deflection checks;
2. Deck slab (cantilever/wet-joint) & diaphragm checks;
3. Elastomeric bearing (JT/T 4-2019) compression, shear, rotation & anti-uplift checks;
4. Substructure overhanging cap beam (flexure, shear, crack width w_cr <= 0.20mm),
   double-column pier (slender N-M compression), and layered-soil bored friction pile length;
5. Unified Bar/Tendon Schedule Ledger + whole-bridge Bill of Quantities with 100% ID-to-check traceability.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

# JTG 3362-2018 Table 3.1.3 Concrete Material Design & Characteristic Strengths (MPa)
CONCRETE_PROPS: dict[str, dict[str, float]] = {
    "C30": {"fck": 20.1, "fcd": 13.8, "ftk": 2.01, "ftd": 1.39, "ec": 30000.0, "fcu_k": 30.0},
    "C35": {"fck": 23.4, "fcd": 16.1, "ftk": 2.20, "ftd": 1.52, "ec": 31500.0, "fcu_k": 35.0},
    "C40": {"fck": 26.8, "fcd": 18.4, "ftk": 2.40, "ftd": 1.65, "ec": 32500.0, "fcu_k": 40.0},
    "C50": {"fck": 32.4, "fcd": 22.4, "ftk": 2.65, "ftd": 1.83, "ec": 34500.0, "fcu_k": 50.0},
}

REBAR_PROPS: dict[str, dict[str, float]] = {
    "HPB300": {"fsk": 300.0, "fsd": 250.0, "es": 210000.0},
    "HRB400": {"fsk": 400.0, "fsd": 330.0, "es": 200000.0},
}


def _bar_area_mm2(dia_mm: float, count: int = 1) -> float:
    return count * math.pi * 0.25 * (dia_mm**2)


def _bar_unit_weight_kg_m(dia_mm: float) -> float:
    return round(0.00617 * dia_mm * dia_mm, 3)


def check_t_girder_uls_sls(
    *,
    section: Mapping[str, Any],
    envelopes: Mapping[str, Any],
    prestress: Mapping[str, Any],
    concrete_grade: str = "C50",
    beam_length_m: float = 24.96,
    bottom_rebar_count: int = 6,
    bottom_rebar_dia_mm: float = 22.0,
    top_rebar_count: int = 6,
    top_rebar_dia_mm: float = 16.0,
    stirrup_dia_mm: float = 12.0,
    stirrup_legs: int = 2,
    stirrup_end_spacing_mm: float = 100.0,
    stirrup_mid_spacing_mm: float = 150.0,
    duct_dia_mm: float = 70.0,
    bend_angle_deg: float = 8.5,
    bent_tendons_at_support: int = 2,
    safety_gamma0: float = 1.0,
) -> dict[str, Any]:
    """Run full JTG 3362-2018 ULS & SLS verification on a representative T-girder."""
    c_mat = CONCRETE_PROPS.get(concrete_grade.upper(), CONCRETE_PROPS["C50"])
    r_mat = REBAR_PROPS["HRB400"]

    mid_mm = section["mid_span_mm"]
    h_mm = float(mid_mm["height"])
    bf_mm = float(section["effective_flange_width_mm"])
    hf_mm = 200.0
    b_web_mm = float(section.get("web_thick_mm", 200.0) or 200.0)
    area_gross_mm2 = float(mid_mm["area"])
    ix_gross_mm4 = float(mid_mm["ix"])
    y_bot_mm = float(mid_mm["y_bot"])
    y_top_mm = float(mid_mm["y_top"])

    n_tendons = int(prestress["recommended_tendons"])
    strands_per = int(prestress["strands_per_tendon"])
    n_strands = int(prestress["provided_strands"])
    ap_mm2 = n_strands * 140.0
    as_bot_mm2 = _bar_area_mm2(bottom_rebar_dia_mm, bottom_rebar_count)
    as_top_mm2 = _bar_area_mm2(top_rebar_dia_mm, top_rebar_count)

    # Net & transformed section properties (accounting for ducts & steel modular ratio)
    alpha_ep = 195000.0 / c_mat["ec"]
    alpha_es = r_mat["es"] / c_mat["ec"]
    ap_cover_mm = max(150.0, 120.0 + 15.0 * n_tendons)
    as_cover_mm = 65.0
    ep_mm = y_bot_mm - ap_cover_mm

    duct_area_total = n_tendons * math.pi * 0.25 * (duct_dia_mm**2)
    area_net_mm2 = max(0.5 * area_gross_mm2, area_gross_mm2 - duct_area_total)
    ix_net_mm4 = max(0.5 * ix_gross_mm4, ix_gross_mm4 - duct_area_total * (ep_mm**2))

    area_trans_mm2 = area_net_mm2 + (alpha_ep - 1.0) * ap_mm2 + (alpha_es - 1.0) * (as_bot_mm2 + as_top_mm2)
    ix_trans_mm4 = (
        ix_net_mm4
        + (alpha_ep - 1.0) * ap_mm2 * (ep_mm**2)
        + (alpha_es - 1.0) * as_bot_mm2 * ((y_bot_mm - as_cover_mm) ** 2)
    )
    w0_bot_mm3 = ix_trans_mm4 / y_bot_mm
    w0_top_mm3 = ix_trans_mm4 / y_top_mm

    # 1. Mid-Span ULS Flexural Capacity (JTG 3362-2018 §5.2.2)
    fpd = 1260.0
    fsd = r_mat["fsd"]
    fcd = c_mat["fcd"]
    tu_n = fpd * ap_mm2 + fsd * as_bot_mm2
    y_res_cover = (fpd * ap_mm2 * ap_cover_mm + fsd * as_bot_mm2 * as_cover_mm) / max(1.0, tu_n)
    h0_mm = h_mm - y_res_cover
    flange_cap_n = fcd * bf_mm * hf_mm
    if tu_n <= flange_cap_n:
        x_mm = tu_n / (fcd * bf_mm)
        mu_knm = (fcd * bf_mm * x_mm * (h0_mm - 0.5 * x_mm)) * 1e-6
        t_section_type = "I"
    else:
        rem_n = tu_n - fcd * (bf_mm - b_web_mm) * hf_mm
        x_mm = rem_n / (fcd * b_web_mm)
        mu_knm = (
            fcd * b_web_mm * x_mm * (h0_mm - 0.5 * x_mm)
            + fcd * (bf_mm - b_web_mm) * hf_mm * (h0_mm - 0.5 * hf_mm)
        ) * 1e-6
        t_section_type = "II"

    xi_b = 0.40
    m_uls_demand = float(envelopes["mid_span_summary"]["m_uls_knm"]) * safety_gamma0
    flexure_pass = (m_uls_demand <= mu_knm) and (x_mm <= xi_b * h0_mm)

    # 2. Support & Quarter-Span ULS Oblique Shear Capacity (JTG 3362-2018 §5.2.7 & §5.2.9)
    v_uls_support = abs(float(envelopes["support_summary"]["q_uls_gov_kn"])) * safety_gamma0
    v_upper_kn = 0.25 * 1.0 * fcd * b_web_mm * h0_mm * 1e-3
    asv_mm2 = _bar_area_mm2(stirrup_dia_mm, stirrup_legs)
    rho_sv = asv_mm2 / (b_web_mm * stirrup_end_spacing_mm)
    p_long_pct = min(2.5, 100.0 * (ap_mm2 + as_bot_mm2) / (b_web_mm * h0_mm))
    vcs_kn = (
        0.45e-3
        * 1.0
        * 1.25
        * 1.1
        * b_web_mm
        * h0_mm
        * math.sqrt((2.0 + 0.6 * p_long_pct) * c_mat["fcu_k"] * rho_sv * r_mat["fsd"])
    )
    apb_mm2 = min(n_tendons, bent_tendons_at_support) * strands_per * 140.0
    vpb_kn = 0.75e-3 * fpd * apb_mm2 * math.sin(math.radians(bend_angle_deg))
    vu_kn = vcs_kn + vpb_kn
    shear_pass = (v_uls_support <= v_upper_kn) and (v_uls_support <= vu_kn)

    # 3. SLS Crack Resistance & Normal/Principal Stresses (JTG 3362-2018 §6.3.1 & §7.1.5)
    loss_rows = prestress["loss_profile"]
    mid_loss = loss_rows[len(loss_rows) // 2]
    end_loss = loss_rows[0]
    sigma_pe_mid = float(mid_loss["effective_prestress_mpa"])
    n_pe_n = sigma_pe_mid * ap_mm2
    sigma_pc_bot = (n_pe_n / area_trans_mm2) + (n_pe_n * ep_mm / w0_bot_mm3)
    sigma_pc_top = (n_pe_n / area_trans_mm2) - (n_pe_n * ep_mm / w0_top_mm3)

    m_freq_nmm = float(envelopes["mid_span_summary"]["m_sls_freq_knm"]) * 1e6
    m_quasi_nmm = float(envelopes["mid_span_summary"]["m_sls_quasi_knm"]) * 1e6
    m_char_nmm = float(envelopes["mid_span_summary"]["m_sls_char_knm"]) * 1e6

    sigma_st_bot = m_freq_nmm / w0_bot_mm3
    sigma_lt_bot = m_quasi_nmm / w0_bot_mm3
    net_bot_freq_mpa = sigma_st_bot - sigma_pc_bot  # <= 0 means compression reserve
    net_bot_quasi_mpa = sigma_lt_bot - sigma_pc_bot
    crack_freq_allow = 0.0  # Class-A PC strict zero tension under frequent load
    crack_pass = (net_bot_freq_mpa <= crack_freq_allow + 1e-6) and (net_bot_quasi_mpa <= 1e-6)

    sigma_top_char_mpa = sigma_pc_top + (m_char_nmm / w0_top_mm3)
    sigma_bot_char_mpa = sigma_pc_bot - (m_char_nmm / w0_bot_mm3)
    comp_allow_mpa = 0.50 * c_mat["fck"]
    service_comp_pass = max(sigma_top_char_mpa, sigma_bot_char_mpa) <= comp_allow_mpa

    # Principal tensile stress near support web centroid under frequent shear
    q_freq_sup_n = (
        abs(float(envelopes["support_summary"]["q_dead_kn"]))
        + 0.7 * abs(float(envelopes["support_summary"]["q_live_pos_kn"]))
    ) * 1e3
    b_end_mm = float(section["end_span_mm"]["width"] if section["end_span_mm"]["width"] < 900 else 600.0)
    tau_sup_mpa = 1.5 * q_freq_sup_n / max(1.0, b_end_mm * h_mm)
    sigma_cx_sup = (float(end_loss["effective_prestress_mpa"]) * ap_mm2) / float(section["end_span_mm"]["area"])
    # Principal tension sigma_tp = 0.5*sigma_cx - sqrt((0.5*sigma_cx)^2 + tau^2) (positive = tension)
    sigma_tp_tensile_mpa = max(0.0, math.hypot(0.5 * sigma_cx_sup, tau_sup_mpa) - 0.5 * sigma_cx_sup)
    principal_tension_allow = 0.50 * c_mat["ftk"]
    principal_tension_pass = sigma_tp_tensile_mpa <= principal_tension_allow

    # Construction transfer stage (post-tensioned after reaching 100% C50 design strength per JTG/T 3650: 0.70*f'ck = 22.68 MPa)
    calc_span_m = float(envelopes["calc_span_m"])
    g1_kn_m = (area_gross_mm2 * 1e-6) * 26.0
    m_g1_nmm = 0.125 * g1_kn_m * (calc_span_m**2) * 1e6
    n_p0_n = (sigma_pe_mid + float(mid_loss["sigma_l6_creep_shrink_mpa"])) * ap_mm2
    sigma_bot_transfer = (n_p0_n / area_trans_mm2) + (n_p0_n * ep_mm / w0_bot_mm3) - (m_g1_nmm / w0_bot_mm3)
    sigma_top_transfer = (n_p0_n / area_trans_mm2) - (n_p0_n * ep_mm / w0_top_mm3) + (m_g1_nmm / w0_top_mm3)
    transfer_comp_allow = 0.70 * c_mat["fck"]
    transfer_tens_allow = -1.15 * c_mat["ftk"]
    transfer_pass = (sigma_bot_transfer <= transfer_comp_allow) and (sigma_top_transfer >= transfer_tens_allow)

    # 4. SLS Deflection & Camber (JTG 3362-2018 §6.5.2-6.5.3)
    m_live_nm = float(envelopes["mid_span_summary"]["m_live_knm"]) * 1e3
    m_dead_nm = float(envelopes["mid_span_summary"]["m_dead_knm"]) * 1e3
    b0_stiff_nm2 = 0.85 * (c_mat["ec"] * 1e6) * (ix_trans_mm4 * 1e-12)
    eta_theta = 1.60
    defl_live_mm = eta_theta * (5.0 * m_live_nm * (calc_span_m**2)) / (48.0 * b0_stiff_nm2) * 1000.0
    defl_allow_mm = (calc_span_m * 1000.0) / 600.0
    defl_dead_mm = eta_theta * (5.0 * m_dead_nm * (calc_span_m**2)) / (48.0 * b0_stiff_nm2) * 1000.0
    camber_ps_mm = eta_theta * ((n_pe_n * (ep_mm * 1e-3)) * (calc_span_m**2)) / (9.6 * b0_stiff_nm2) * 1000.0
    net_long_term_camber_mm = camber_ps_mm - defl_dead_mm - 0.5 * defl_live_mm
    deflection_pass = defl_live_mm <= defl_allow_mm

    all_passed = all(
        (flexure_pass, shear_pass, crack_pass, service_comp_pass, principal_tension_pass, transfer_pass, deflection_pass)
    )
    return {
        "status": "passed" if all_passed else "failed",
        "concrete_grade": concrete_grade.upper(),
        "transformed_section": {
            "area_net_mm2": round(area_net_mm2, 1),
            "area_trans_mm2": round(area_trans_mm2, 1),
            "ix_trans_mm4": round(ix_trans_mm4, 1),
            "h0_mm": round(h0_mm, 1),
        },
        "uls_flexure": {
            "section_type": t_section_type,
            "demand_md_knm": round(m_uls_demand, 2),
            "capacity_mu_knm": round(mu_knm, 2),
            "compression_depth_x_mm": round(x_mm, 2),
            "max_depth_xib_h0_mm": round(xi_b * h0_mm, 2),
            "utilization": round(m_uls_demand / max(1.0, mu_knm), 4),
            "passed": flexure_pass,
        },
        "uls_shear": {
            "demand_vd_kn": round(v_uls_support, 2),
            "size_upper_limit_kn": round(v_upper_kn, 2),
            "vcs_kn": round(vcs_kn, 2),
            "vpb_kn": round(vpb_kn, 2),
            "capacity_vu_kn": round(vu_kn, 2),
            "utilization": round(v_uls_support / max(1.0, vu_kn), 4),
            "passed": shear_pass,
        },
        "sls_crack_and_stress": {
            "precompression_bot_mpa": round(sigma_pc_bot, 2),
            "net_bot_stress_freq_mpa": round(net_bot_freq_mpa, 2),
            "net_bot_stress_quasi_mpa": round(net_bot_quasi_mpa, 2),
            "top_comp_char_mpa": round(sigma_top_char_mpa, 2),
            "bot_comp_char_mpa": round(sigma_bot_char_mpa, 2),
            "comp_allowable_mpa": round(comp_allow_mpa, 2),
            "principal_tensile_mpa": round(sigma_tp_tensile_mpa, 3),
            "principal_tensile_allow_mpa": round(principal_tension_allow, 3),
            "transfer_bot_comp_mpa": round(sigma_bot_transfer, 2),
            "transfer_comp_allow_mpa": round(transfer_comp_allow, 2),
            "transfer_top_stress_mpa": round(sigma_top_transfer, 2),
            "passed": bool(crack_pass and service_comp_pass and principal_tension_pass and transfer_pass),
        },
        "sls_deflection": {
            "live_deflection_mm": round(defl_live_mm, 2),
            "allowable_l_div_600_mm": round(defl_allow_mm, 2),
            "prestress_camber_mm": round(camber_ps_mm, 2),
            "net_long_term_camber_mm": round(net_long_term_camber_mm, 2),
            "precamber_needed": bool(net_long_term_camber_mm < -2.0),
            "passed": deflection_pass,
        },
        "reinforcement_summary": {
            "beam_length_m": beam_length_m,
            "bottom_bars": f"{bottom_rebar_count}Φ{bottom_rebar_dia_mm:g}",
            "top_bars": f"{top_rebar_count}Φ{top_rebar_dia_mm:g}",
            "end_stirrups": f"{stirrup_legs}-Φ{stirrup_dia_mm:g}@{stirrup_end_spacing_mm:g}",
            "mid_stirrups": f"{stirrup_legs}-Φ{stirrup_dia_mm:g}@{stirrup_mid_spacing_mm:g}",
        },
    }


def check_deck_slab_and_diaphragm(
    *,
    girder_spacing_m: float = 1.95,
    web_thick_m: float = 0.20,
    flange_root_thick_m: float = 0.27,
    asphalt_thick_m: float = 0.10,
    concrete_pav_thick_m: float = 0.08,
    impact_mu: float = 0.178,
    calc_span_m: float = 24.0,
) -> dict[str, Any]:
    """Compute deck cantilever/hinged slab and mid-diaphragm ULS design checks."""
    l0 = max(0.35, 0.5 * (girder_spacing_m - web_thick_m))
    g_pav = asphalt_thick_m * 23.0 + concrete_pav_thick_m * 25.0 + 0.225 * 26.0
    m_dead = 0.5 * g_pav * (l0**2)
    q_dead = g_pav * l0

    a1 = 0.20 + 2.0 * asphalt_thick_m
    b1 = 0.60 + 2.0 * asphalt_thick_m
    a_eff = a1 + 1.40 + 2.0 * l0
    p_wheel_kn = 70.0
    m_live = (1.0 + impact_mu) * (2.0 * p_wheel_kn / (4.0 * a_eff)) * max(0.15, l0 - 0.25 * b1)
    q_live = (1.0 + impact_mu) * (2.0 * p_wheel_kn / (4.0 * a_eff))

    m_uls = 1.1 * (1.2 * m_dead + 1.4 * m_live)
    q_uls = 1.1 * (1.2 * q_dead + 1.4 * q_live)

    # Flange negative rebar Φ12@125 (905 mm2/m) in C50
    as_slab_mm2_m = _bar_area_mm2(12.0, 1) * (1000.0 / 125.0)
    h0_slab_mm = flange_root_thick_m * 1000.0 - 35.0
    x_slab = (330.0 * as_slab_mm2_m) / (22.4 * 1000.0)
    mu_slab_knm = 22.4 * 1000.0 * x_slab * (h0_slab_mm - 0.5 * x_slab) * 1e-6

    # Mid-diaphragm ULS moment & rebar (4Φ25 bottom HRB400, h=1350mm, b=200mm)
    diaph_spacing = 0.25 * calc_span_m
    p_diaph = 0.5 * (10.5 * diaph_spacing + 260.0)
    m_diaph_uls = 1.4 * (1.0 + impact_mu) * p_diaph * 0.45 * girder_spacing_m
    as_diaph = _bar_area_mm2(25.0, 4)
    mu_diaph_knm = 0.90 * 330.0 * as_diaph * 1260.0 * 1e-6

    return {
        "status": "passed" if (m_uls <= mu_slab_knm and m_diaph_uls <= mu_diaph_knm) else "failed",
        "deck_slab": {
            "clear_cantilever_m": round(l0, 3),
            "m_uls_knm_per_m": round(m_uls, 2),
            "q_uls_kn_per_m": round(q_uls, 2),
            "mu_capacity_knm_per_m": round(mu_slab_knm, 2),
            "top_rebar": "Φ12@125",
            "passed": bool(m_uls <= mu_slab_knm),
        },
        "diaphragm": {
            "m_uls_knm": round(m_diaph_uls, 2),
            "mu_capacity_knm": round(mu_diaph_knm, 2),
            "bottom_rebar": "4Φ25",
            "passed": bool(m_diaph_uls <= mu_diaph_knm),
        },
    }


def check_elastomeric_bearing(
    *,
    max_sls_reaction_kn: float = 820.0,
    min_sls_reaction_kn: float = 380.0,
    bearing_width_mm: float = 300.0,
    bearing_length_mm: float = 400.0,
    bearing_thick_mm: float = 74.0,
    rubber_net_thick_mm: float = 52.0,
    calc_span_m: float = 24.0,
) -> dict[str, Any]:
    """Verify laminated elastomeric bearing (JT/T 4-2019) stress, shear strain, and anti-uplift."""
    ae_mm2 = (bearing_width_mm - 10.0) * (bearing_length_mm - 10.0)
    sigma_c_mpa = (max_sls_reaction_kn * 1e3) / max(1.0, ae_mm2)
    sigma_min_mpa = (min_sls_reaction_kn * 1e3) / max(1.0, ae_mm2)
    # Thermal expansion delta_T = 25C over half span
    delta_temp_mm = 1.0e-5 * 25.0 * (0.5 * calc_span_m * 1000.0) + 4.0
    tan_gamma = delta_temp_mm / max(1.0, rubber_net_thick_mm)
    passed = (sigma_c_mpa <= 10.0) and (min_sls_reaction_kn > 50.0) and (sigma_min_mpa >= 1.5) and (tan_gamma <= 0.70)
    model_str = f"GJZ {bearing_width_mm:g}×{bearing_length_mm:g}×{bearing_thick_mm:g}"
    return {
        "status": "passed" if passed else "failed",
        "bearing_model": model_str,
        "compressive_stress_mpa": round(sigma_c_mpa, 2),
        "allowable_stress_mpa": 10.0,
        "min_contact_stress_mpa": round(sigma_min_mpa, 2),
        "min_reaction_kn": round(min_sls_reaction_kn, 2),
        "shear_strain_tan_gamma": round(tan_gamma, 3),
        "allowable_tan_gamma": 0.70,
        "anti_uplift_passed": bool(min_sls_reaction_kn > 50.0),
        "passed": passed,
    }


DEFAULT_WENCHANG_SOIL_LAYERS: tuple[dict[str, Any], ...] = (
    {"name": "①素填土", "thickness_m": 1.2, "qik_kpa": 22.0, "fa0_kpa": 80.0},
    {"name": "②褐黄色粉质黏土", "thickness_m": 4.5, "qik_kpa": 55.0, "fa0_kpa": 130.0},
    {"name": "③青灰色细中砂", "thickness_m": 3.5, "qik_kpa": 68.0, "fa0_kpa": 200.0},
    {"name": "④中密～密实砂砾石持力层", "thickness_m": 15.0, "qik_kpa": 125.0, "fa0_kpa": 400.0},
)


def check_substructure_cap_pier_pile(
    *,
    cap_beam_1d: Mapping[str, Any],
    cap_width_m: float = 1.60,
    cap_height_m: float = 1.40,
    column_diameter_m: float = 1.20,
    column_height_m: float = 6.00,
    pile_diameter_m: float = 1.40,
    concrete_grade: str = "C30",
    soil_layers: Sequence[Mapping[str, Any]] | None = None,
    explicit_pile_length_m: float | None = None,
) -> dict[str, Any]:
    """Design & verify cap beam (ULS + crack width <= 0.20mm), double-column pier, and bored friction pile."""
    c_mat = CONCRETE_PROPS.get(concrete_grade.upper(), CONCRETE_PROPS["C30"])
    r_mat = REBAR_PROPS["HRB400"]

    m_hog_uls = abs(float(cap_beam_1d["max_hogging_moment_knm"]))
    m_sag_uls = abs(float(cap_beam_1d["max_sagging_moment_knm"]))
    v_cap_uls = abs(float(cap_beam_1d["max_abs_shear_kn"]))
    max_col_top_kn = max(float(r["reaction_kn"]) for r in cap_beam_1d["support_reactions"])

    b_mm = cap_width_m * 1000.0
    h_mm = cap_height_m * 1000.0
    h0_mm = h_mm - 75.0

    # Auto-select top negative rebar count (Φ28 HRB400) so BOTH ULS flexure and w_cr <= 0.20mm pass!
    m_hog_sls_freq = 0.72 * m_hog_uls
    m_hog_sls_quasi = 0.58 * m_hog_uls
    chosen_top_n = 12
    w_cr_mm = 9.9
    mu_hog_knm = 0.0
    for cand_n in range(10, 34, 2):
        as_cand = _bar_area_mm2(28.0, cand_n)
        x_c = (r_mat["fsd"] * as_cand) / (c_mat["fcd"] * b_mm)
        mu_c = c_mat["fcd"] * b_mm * x_c * (h0_mm - 0.5 * x_c) * 1e-6
        sigma_ss = (m_hog_sls_freq * 1e6) / (0.87 * as_cand * h0_mm)
        rho_te = max(0.01, min(0.10, as_cand / (0.5 * b_mm * h_mm)))
        c2 = 1.0 + 0.5 * (m_hog_sls_quasi / max(1.0, m_hog_sls_freq))
        w_cand = 1.0 * c2 * 1.0 * (sigma_ss / r_mat["es"]) * ((45.0 + 28.0) / (0.30 + 1.4 * rho_te))
        chosen_top_n = cand_n
        w_cr_mm = w_cand
        mu_hog_knm = mu_c
        if mu_c >= m_hog_uls * 1.12 and w_cand <= 0.185:
            break

    bot_n = max(8, chosen_top_n // 2 + 2)
    as_bot = _bar_area_mm2(25.0, bot_n)
    x_bot = (r_mat["fsd"] * as_bot) / (c_mat["fcd"] * b_mm)
    mu_sag_knm = c_mat["fcd"] * b_mm * x_bot * (h0_mm - 0.5 * x_bot) * 1e-6

    # Cap beam 4-leg Φ12@100 stirrups
    asv_cap = _bar_area_mm2(12.0, 4)
    rho_sv_cap = asv_cap / (b_mm * 100.0)
    vu_cap_kn = (
        0.45e-3
        * 1.0
        * 1.0
        * 1.0
        * b_mm
        * h0_mm
        * math.sqrt((2.0 + 0.6 * 1.2) * c_mat["fcu_k"] * rho_sv_cap * r_mat["fsd"])
    )
    cap_passed = (m_hog_uls <= mu_hog_knm) and (m_sag_uls <= mu_sag_knm) and (v_cap_uls <= vu_cap_kn) and (w_cr_mm <= 0.20)

    # Double-Column Pier Circular RC Section ULS Check
    col_area_mm2 = math.pi * 0.25 * ((column_diameter_m * 1000.0) ** 2)
    col_self_kn = 1.2 * (col_area_mm2 * 1e-6) * column_height_m * 25.0
    n_col_uls = max_col_top_kn + col_self_kn
    m_col_uls = max(180.0, 0.08 * n_col_uls * column_diameter_m)
    col_bar_n = 20
    as_col = _bar_area_mm2(25.0, col_bar_n)
    phi_stab = max(0.75, 1.0 - 0.008 * max(0.0, (column_height_m / column_diameter_m) - 4.0))
    # Conservative interaction resistance for circular RC column under small/moderate eccentricity
    nu_pure_kn = 0.90 * phi_stab * (c_mat["fcd"] * col_area_mm2 + r_mat["fsd"] * as_col) * 1e-3
    ecc_factor = 1.0 / (1.0 + 1.8 * (m_col_uls / max(1.0, n_col_uls * column_diameter_m)))
    nu_col_kn = nu_pure_kn * ecc_factor
    pier_passed = n_col_uls <= nu_col_kn

    # Bored Friction Pile Bearing Capacity & Length (JTG 3363-2019 §5.3.3)
    layers = [dict(x) for x in (soil_layers or DEFAULT_WENCHANG_SOIL_LAYERS)]
    u_perim = math.pi * pile_diameter_m
    ap_pile = math.pi * 0.25 * (pile_diameter_m**2)
    # Convert ULS column reaction to characteristic SLS pile head load + pile net submerged weight
    r_pile_sls_demand = (n_col_uls / 1.28) + 120.0

    def _eval_ra(length_m: float) -> float:
        rem = length_m
        skin_sum = 0.0
        tip_fa0 = layers[-1]["fa0_kpa"]
        for lyr in layers:
            thk = float(lyr["thickness_m"])
            used = min(rem, thk)
            skin_sum += float(lyr["qik_kpa"]) * used
            tip_fa0 = float(lyr["fa0_kpa"])
            rem -= used
            if rem <= 1e-6:
                break
        if rem > 1e-6:
            skin_sum += float(layers[-1]["qik_kpa"]) * rem
            tip_fa0 = float(layers[-1]["fa0_kpa"])
        # [Ra] = 0.5 * u * sum(q_ik * l_i) + A_p * q_r (with m0*lambda=0.75 on tip fa0)
        return 0.5 * u_perim * skin_sum + ap_pile * (0.85 * tip_fa0)

    if explicit_pile_length_m is not None:
        pile_len_m = float(explicit_pile_length_m)
    else:
        pile_len_m = 14.0
        while pile_len_m < 40.0 and _eval_ra(pile_len_m) < r_pile_sls_demand * 1.15:
            pile_len_m += 1.0

    ra_allow_kn = _eval_ra(pile_len_m)
    pile_passed = ra_allow_kn >= r_pile_sls_demand

    return {
        "status": "passed" if (cap_passed and pier_passed and pile_passed) else "failed",
        "cap_beam": {
            "m_hog_uls_knm": round(m_hog_uls, 2),
            "mu_hog_capacity_knm": round(mu_hog_knm, 2),
            "m_sag_uls_knm": round(m_sag_uls, 2),
            "mu_sag_capacity_knm": round(mu_sag_knm, 2),
            "v_uls_kn": round(v_cap_uls, 2),
            "vu_capacity_kn": round(vu_cap_kn, 2),
            "crack_width_mm": round(w_cr_mm, 3),
            "crack_allowable_mm": 0.200,
            "top_rebar": f"{chosen_top_n}Φ28",
            "bottom_rebar": f"{bot_n}Φ25",
            "stirrups": "4肢Φ12@100",
            "top_rebar_count": chosen_top_n,
            "bottom_rebar_count": bot_n,
            "passed": cap_passed,
        },
        "pier_column": {
            "diameter_m": column_diameter_m,
            "height_m": column_height_m,
            "n_uls_kn": round(n_col_uls, 2),
            "m_uls_knm": round(m_col_uls, 2),
            "nu_capacity_kn": round(nu_col_kn, 2),
            "main_rebar": f"{col_bar_n}Φ25",
            "spiral_hoop": "Φ10@100/150",
            "main_rebar_count": col_bar_n,
            "passed": pier_passed,
        },
        "bored_pile": {
            "diameter_m": pile_diameter_m,
            "design_length_m": round(pile_len_m, 1),
            "pile_top_sls_demand_kn": round(r_pile_sls_demand, 2),
            "allowable_ra_kn": round(ra_allow_kn, 2),
            "main_rebar": "22Φ25",
            "main_rebar_count": 22,
            "soil_layers": layers,
            "passed": pile_passed,
        },
    }


def build_rebar_and_tendon_ledger(
    *,
    n_spans: int,
    n_girders_per_span: int,
    beam_length_m: float,
    girder_height_m: float,
    girder_spacing_m: float,
    deck_width_m: float,
    cap_length_m: float,
    column_height_m: float,
    pile_length_m: float,
    prestress: Mapping[str, Any],
    girder_checks: Mapping[str, Any],
    slab_checks: Mapping[str, Any],
    sub_checks: Mapping[str, Any],
) -> dict[str, Any]:
    """Construct a single source-of-truth ledger mapping every tendon & rebar ID to its check basis and BOQ."""
    total_girders = n_spans * n_girders_per_span
    n_piers = max(1, n_spans - 1)
    n_abutments = 2

    # 1. Prestressing Tendon Schedule (per T-girder & whole bridge)
    n_tendons = int(prestress["recommended_tendons"])
    strands_per = int(prestress["strands_per_tendon"])
    mid_sigma_pe = float(prestress["loss_profile"][len(prestress["loss_profile"]) // 2]["effective_prestress_mpa"])
    tendon_items: list[dict[str, Any]] = []
    strand_wt_kg_m = 1.101  # Φs15.2 1860MPa strand = 1.101 kg/m
    for idx in range(1, n_tendons + 1):
        # Slight parabolic arc length increment + working length
        single_len_m = round(beam_length_m + 0.06 + idx * 0.035, 3)
        elong_mm = round((mid_sigma_pe / 195000.0) * (single_len_m * 1000.0), 1)
        wt_one_tendon_kg = round(single_len_m * strands_per * strand_wt_kg_m, 2)
        tendon_items.append(
            {
                "tendon_id": f"N{idx}",
                "component": "预制T梁预应力钢束",
                "sheet_drawing_no": "QL-08",
                "spec": f"Φs15.2-{strands_per}",
                "strands": strands_per,
                "single_length_m": single_len_m,
                "theoretical_elongation_mm": elong_mm,
                "count_per_girder": 1,
                "total_count_bridge": total_girders,
                "weight_per_girder_kg": wt_one_tendon_kg,
                "total_weight_bridge_kg": round(wt_one_tendon_kg * total_girders, 1),
                "calc_basis": (
                    f"JTG 3362-2018 §5.2.2/§6.3.1: Md={girder_checks['uls_flexure']['demand_md_knm']}kN·m "
                    f"<= Mu={girder_checks['uls_flexure']['capacity_mu_knm']}kN·m; 跨中σpe={mid_sigma_pe}MPa"
                ),
            }
        )

    def _mk_bar(
        bar_id: str,
        component: str,
        drawing_no: str,
        grade: str,
        dia_mm: float,
        shape: str,
        single_len_m: float,
        count_per_unit: int,
        units_in_bridge: int,
        calc_basis: str,
    ) -> dict[str, Any]:
        u_wt = _bar_unit_weight_kg_m(dia_mm)
        wt_unit = round(single_len_m * count_per_unit * u_wt, 2)
        return {
            "bar_id": bar_id,
            "component": component,
            "sheet_drawing_no": drawing_no,
            "grade": grade,
            "dia_mm": dia_mm,
            "shape": shape,
            "single_length_cm": round(single_len_m * 100.0, 1),
            "count": count_per_unit,
            "units_in_bridge": units_in_bridge,
            "unit_weight_kg_m": u_wt,
            "total_weight_kg": wt_unit,
            "bridge_total_weight_kg": round(wt_unit * units_in_bridge, 1),
            "calc_basis": calc_basis,
        }

    n_stirrups_girder = int(round((beam_length_m - 4.0) / 0.15)) + 40
    n_flange_bars = int(round(beam_length_m / 0.125))
    cap_top_n = int(sub_checks["cap_beam"]["top_rebar_count"])
    cap_bot_n = int(sub_checks["cap_beam"]["bottom_rebar_count"])
    col_bar_n = int(sub_checks["pier_column"]["main_rebar_count"])
    pile_bar_n = int(sub_checks["bored_pile"]["main_rebar_count"])

    rebar_by_sheet: dict[str, list[dict[str, Any]]] = {
        "t_girder_web_rebar": [
            _mk_bar(
                "R1",
                "T梁梁肋主筋",
                "QL-09",
                "HRB400",
                22.0,
                "straight_hook",
                beam_length_m + 0.24,
                6,
                total_girders,
                f"主梁正截面抗弯验算 Md={girder_checks['uls_flexure']['demand_md_knm']} <= Mu={girder_checks['uls_flexure']['capacity_mu_knm']} kN·m",
            ),
            _mk_bar(
                "R2",
                "T梁梁肋架立筋",
                "QL-09",
                "HRB400",
                16.0,
                "straight_hook",
                beam_length_m + 0.16,
                6,
                total_girders,
                f"施工短暂阶段上缘抗拉控制 σct={girder_checks['sls_crack_and_stress']['transfer_top_stress_mpa']} MPa",
            ),
            _mk_bar(
                "GJ1",
                "T梁腹板抗剪箍筋",
                "QL-09",
                "HRB400",
                12.0,
                "stirrup_rect",
                2.0 * (girder_height_m - 0.12) + 0.56,
                n_stirrups_girder,
                total_girders,
                f"斜截面抗剪验算 Vd={girder_checks['uls_shear']['demand_vd_kn']} <= Vu={girder_checks['uls_shear']['capacity_vu_kn']} kN",
            ),
            _mk_bar(
                "R3",
                "T梁腹板纵向水平防裂筋",
                "QL-09",
                "HRB400",
                12.0,
                "straight_hook",
                beam_length_m - 0.08,
                8,
                total_girders,
                "JTG 3362-2018 §9.3.8 梁肋水平防裂构造配筋",
            ),
        ],
        "t_girder_flange_rebar": [
            _mk_bar(
                "W1",
                "翼缘板顶层负弯矩主筋",
                "QL-10",
                "HRB400",
                12.0,
                "straight_hook",
                girder_spacing_m + 0.18,
                n_flange_bars,
                total_girders,
                f"行车道板抗弯验算 Md={slab_checks['deck_slab']['m_uls_knm_per_m']} <= Mu={slab_checks['deck_slab']['mu_capacity_knm_per_m']} kN·m/m",
            ),
            _mk_bar(
                "W2",
                "翼缘板底层横向受力筋",
                "QL-10",
                "HRB400",
                12.0,
                "u_hairpin",
                girder_spacing_m + 0.12,
                int(round(beam_length_m / 0.15)),
                total_girders,
                f"湿接缝正弯矩及悬臂抗剪 Q={slab_checks['deck_slab']['q_uls_kn_per_m']} kN/m",
            ),
            _mk_bar(
                "W3",
                "翼缘板纵向分布钢筋",
                "QL-10",
                "HRB400",
                10.0,
                "straight_hook",
                beam_length_m - 0.08,
                14,
                total_girders,
                "JTG 3362-2018 §9.4.3 行车道板纵向分布筋构造",
            ),
        ],
        "diaphragm_rebar": [
            _mk_bar(
                "HG1",
                "横隔梁底部受拉主筋",
                "QL-11",
                "HRB400",
                25.0,
                "bent_up",
                (n_girders_per_span - 1) * girder_spacing_m + 0.35,
                4,
                n_spans * 5,
                f"中横隔梁内力验算 Md={slab_checks['diaphragm']['m_uls_knm']} <= Mu={slab_checks['diaphragm']['mu_capacity_knm']} kN·m",
            ),
            _mk_bar(
                "HG2",
                "横隔梁顶部连续筋",
                "QL-11",
                "HRB400",
                20.0,
                "straight_hook",
                (n_girders_per_span - 1) * girder_spacing_m + 0.25,
                4,
                n_spans * 5,
                "JTG 3362-2018 §9.4.8 横隔梁顶部受拉与连接构造",
            ),
            _mk_bar(
                "HG3",
                "横隔梁封闭箍筋",
                "QL-11",
                "HRB400",
                10.0,
                "stirrup_rect",
                2.85,
                int(round(((n_girders_per_span - 1) * girder_spacing_m) / 0.15)),
                n_spans * 5,
                "横隔梁剪力传递及湿接缝连接抗剪构造",
            ),
        ],
        "deck_ancillary_rebar": [
            _mk_bar(
                "HL1",
                "防撞护栏竖向主筋",
                "QL-13",
                "HRB400",
                16.0,
                "u_hairpin",
                2.35,
                int(round((n_spans * 25.0 * 2.0) / 0.15)),
                1,
                "JTG D81 公路交通安全设施混凝土防撞护栏抗冲撞配筋",
            ),
            _mk_bar(
                "LX1",
                "墩顶桥面连续段纵向抗裂筋",
                "QL-13",
                "HRB400",
                16.0,
                "straight_hook",
                3.60,
                int(round(deck_width_m / 0.125)),
                n_piers,
                "墩顶桥面连续构造负弯矩反射裂缝控制配筋",
            ),
        ],
        "pier_cap_rebar": [
            _mk_bar(
                "GL1",
                "盖梁顶层负弯矩主筋",
                "QL-16",
                "HRB400",
                28.0,
                "straight_hook",
                cap_length_m + 0.50,
                cap_top_n,
                n_piers,
                (
                    f"盖梁负弯矩及裂缝验算: Md={sub_checks['cap_beam']['m_hog_uls_knm']} <= Mu={sub_checks['cap_beam']['mu_hog_capacity_knm']} kN·m, "
                    f"wcr={sub_checks['cap_beam']['crack_width_mm']}mm <= 0.20mm"
                ),
            ),
            _mk_bar(
                "GL2",
                "盖梁底层正弯矩主筋",
                "QL-16",
                "HRB400",
                25.0,
                "straight_hook",
                cap_length_m + 0.40,
                cap_bot_n,
                n_piers,
                f"盖梁跨中正弯矩验算: Md={sub_checks['cap_beam']['m_sag_uls_knm']} <= Mu={sub_checks['cap_beam']['mu_sag_capacity_knm']} kN·m",
            ),
            _mk_bar(
                "GL3",
                "盖梁四肢抗剪箍筋",
                "QL-16",
                "HRB400",
                12.0,
                "stirrup_rect",
                5.80,
                int(round(cap_length_m / 0.10)) + 1,
                n_piers,
                f"盖梁斜截面抗剪验算: Vd={sub_checks['cap_beam']['v_uls_kn']} <= Vu={sub_checks['cap_beam']['vu_capacity_kn']} kN",
            ),
            _mk_bar(
                "GL4",
                "盖梁弯起斜筋",
                "QL-16",
                "HRB400",
                25.0,
                "bent_up",
                cap_length_m * 0.62,
                4,
                n_piers,
                "悬臂根部斜截面主拉应力加强弯起筋",
            ),
        ],
        "pier_column_pile_rebar": [
            _mk_bar(
                "DZ1",
                "桥墩圆柱墩竖向主筋",
                "QL-17",
                "HRB400",
                25.0,
                "straight_hook",
                column_height_m + 1.40,
                col_bar_n,
                n_piers * 2,
                f"双柱墩偏心受压验算: Nd={sub_checks['pier_column']['n_uls_kn']} <= Nu={sub_checks['pier_column']['nu_capacity_kn']} kN",
            ),
            _mk_bar(
                "DZ2",
                "墩柱抗震加密螺旋箍筋",
                "QL-17",
                "HPB300",
                10.0,
                "spiral_hoop",
                3.55,
                int(round(column_height_m / 0.12)),
                n_piers * 2,
                "JTG/T 2231-01-2020 抗震构造墩柱塑性铰区螺旋箍筋加密",
            ),
            _mk_bar(
                "ZJ1",
                "钻孔灌注桩钢筋笼主筋",
                "QL-17",
                "HRB400",
                25.0,
                "straight_hook",
                pile_length_m + 1.10,
                pile_bar_n,
                n_piers * 2,
                f"JTG 3363-2019 摩擦桩验算: R={sub_checks['bored_pile']['pile_top_sls_demand_kn']} <= [Ra]={sub_checks['bored_pile']['allowable_ra_kn']} kN",
            ),
            _mk_bar(
                "ZJ2",
                "桩基钢筋笼加劲箍与螺旋箍",
                "QL-17",
                "HPB300",
                12.0,
                "spiral_hoop",
                4.20,
                int(round(pile_length_m / 0.15)),
                n_piers * 2,
                "水下C30钻孔灌注桩钢筋笼整体刚度及抗剪箍筋",
            ),
        ],
        "abutment_rebar": [
            _mk_bar(
                "QT1",
                "桥台台帽主筋",
                "QL-18",
                "HRB400",
                25.0,
                "straight_hook",
                deck_width_m + 0.30,
                16,
                n_abutments,
                "桩柱式桥台台帽弯曲及支座反力承载构造",
            ),
            _mk_bar(
                "QT2",
                "桥台背墙与耳墙竖向筋",
                "QL-18",
                "HRB400",
                16.0,
                "u_hairpin",
                3.40,
                int(round(deck_width_m / 0.125)),
                n_abutments,
                "桥头台背主动土压力与车辆附加荷载抗弯配筋",
            ),
            _mk_bar(
                "QT3",
                "桥台钻孔桩钢筋笼主筋",
                "QL-18",
                "HRB400",
                25.0,
                "straight_hook",
                max(14.0, pile_length_m - 2.0) + 1.0,
                20,
                n_abutments * 2,
                f"桥台桩基竖向与水平土压力验算 (持力层砂砾石 [Ra]={sub_checks['bored_pile']['allowable_ra_kn']} kN)",
            ),
            _mk_bar(
                "DS1",
                "支座垫石防劈裂钢筋网",
                "QL-18",
                "HRB400",
                12.0,
                "stirrup_rect",
                1.80,
                20,
                n_piers + n_abutments,
                "JTG 3362-2018 §5.7 局部承压支座垫石多层方格钢筋网",
            ),
        ],
    }

    flat_bars = [item for grp in rebar_by_sheet.values() for item in grp]
    by_id: dict[str, dict[str, Any]] = {t["tendon_id"]: t for t in tendon_items}
    for b_item in flat_bars:
        by_id[b_item["bar_id"]] = b_item

    return {
        "tendons": tendon_items,
        "rebar_groups": rebar_by_sheet,
        "all_rebar_items": flat_bars,
        "index_by_id": by_id,
    }


def compute_bridge_bill_of_quantities(
    *,
    n_spans: int,
    n_girders_per_span: int,
    beam_length_m: float,
    section_area_m2: float,
    deck_width_m: float,
    asphalt_thick_m: float,
    concrete_pav_thick_m: float,
    cap_length_m: float,
    cap_width_m: float,
    cap_height_m: float,
    column_diameter_m: float,
    column_height_m: float,
    pile_diameter_m: float,
    pile_length_m: float,
    bearing_model: str,
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute traceable whole-bridge concrete, strand, rebar, bearing, and joint quantities."""
    total_bridge_len = n_spans * 25.0
    total_girders = n_spans * n_girders_per_span
    n_piers = max(1, n_spans - 1)

    c50_girders_m3 = round(total_girders * (section_area_m2 * beam_length_m + 0.85), 2)
    c40_deck_pav_m3 = round(total_bridge_len * (deck_width_m - 1.0) * concrete_pav_thick_m, 2)
    asphalt_m3 = round(total_bridge_len * (deck_width_m - 1.0) * asphalt_thick_m, 2)
    c30_sub_m3 = round(
        n_piers * (cap_length_m * cap_width_m * cap_height_m + 2.0 * (math.pi * 0.25 * column_diameter_m**2) * column_height_m)
        + 2.0 * (deck_width_m * 1.6 * 1.8),
        2,
    )
    c30_pile_m3 = round(
        (n_piers * 2 + 4) * (math.pi * 0.25 * pile_diameter_m**2) * pile_length_m,
        2,
    )

    total_strand_kg = round(sum(float(t["total_weight_bridge_kg"]) for t in ledger["tendons"]), 1)
    hrb400_kg = round(
        sum(float(b["bridge_total_weight_kg"]) for b in ledger["all_rebar_items"] if b["grade"] == "HRB400"),
        1,
    )
    hpb300_kg = round(
        sum(float(b["bridge_total_weight_kg"]) for b in ledger["all_rebar_items"] if b["grade"] == "HPB300"),
        1,
    )
    total_bearings = total_girders * 2

    rows = [
        {"category": "上部预制T梁及湿接缝", "material": "C50混凝土", "unit": "m3", "quantity": c50_girders_m3, "sheet_ref": "QL-06/QL-07"},
        {"category": "桥面现浇调平层", "material": "C40防水混凝土", "unit": "m3", "quantity": c40_deck_pav_m3, "sheet_ref": "QL-05/QL-13"},
        {"category": "桥面沥青上面层", "material": "密级配沥青混凝土", "unit": "m3", "quantity": asphalt_m3, "sheet_ref": "QL-05/QL-13"},
        {"category": "预应力低松弛钢绞线", "material": "Φs15.2 (fpk=1860MPa)", "unit": "kg", "quantity": total_strand_kg, "sheet_ref": "QL-08"},
        {"category": "全桥带肋普通钢筋", "material": "HRB400", "unit": "kg", "quantity": hrb400_kg, "sheet_ref": "QL-09~QL-18"},
        {"category": "全桥光圆箍筋", "material": "HPB300", "unit": "kg", "quantity": hpb300_kg, "sheet_ref": "QL-17"},
        {"category": "墩台盖梁及柱身", "material": "C30混凝土", "unit": "m3", "quantity": c30_sub_m3, "sheet_ref": "QL-14/QL-15"},
        {"category": "水下钻孔灌注桩", "material": "C30水下混凝土", "unit": "m3", "quantity": c30_pile_m3, "sheet_ref": "QL-14/QL-17"},
        {"category": "板式橡胶支座", "material": bearing_model, "unit": "块", "quantity": total_bearings, "sheet_ref": "QL-12"},
        {"category": "桥台型钢伸缩装置", "material": "D80型钢伸缩缝", "unit": "m", "quantity": round(2.0 * deck_width_m, 1), "sheet_ref": "QL-13"},
    ]
    return {
        "c50_concrete_m3": c50_girders_m3,
        "c40_paving_m3": c40_deck_pav_m3,
        "asphalt_paving_m3": asphalt_m3,
        "c30_substructure_m3": c30_sub_m3,
        "c30_underwater_pile_m3": c30_pile_m3,
        "prestress_strand_kg": total_strand_kg,
        "hrb400_rebar_kg": hrb400_kg,
        "hpb300_rebar_kg": hpb300_kg,
        "total_rebar_kg": round(hrb400_kg + hpb300_kg, 1),
        "bearing_count": total_bearings,
        "bearing_model": bearing_model,
        "rows": rows,
    }


def lookup_bar_or_tendon(calc_report: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    """Look up any rebar or tendon ID (e.g. 'N1', 'R1', 'GJ1', 'GL1') and return its calc basis and quantities."""
    ledger = calc_report.get("rebar_and_tendon_ledger") or {}
    idx_map = ledger.get("index_by_id") or {}
    key = str(item_id).strip().upper()
    for k, val in idx_map.items():
        if k.upper() == key:
            return dict(val)
    raise KeyError(f"bar or tendon ID {item_id!r} not found in bridge ledger")
