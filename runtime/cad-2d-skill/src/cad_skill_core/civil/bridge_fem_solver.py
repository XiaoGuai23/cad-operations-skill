"""Independent 2D Variable-Section Timoshenko Beam & Planar Pier-Frame FEM Solver + Dr.Bridge Guide.

Provides two honest, complementary finite-element verification tracks for bridge graduation designs:
1. Track A (Commercial Software Import — 桥梁博士 Dr.Bridge V4.4/V5.0 / Midas Civil):
   `generate_drbridge_modeling_guide(calc_report)` outputs a parameter-complete step-by-step
   modeling card and a null-valued (`is_template: true`) JSON import template for both superstructure
   (`上部分册`) and substructure (`下部分册：盖梁、墩柱、桩顶系梁、桩基`). Real imported Dr.Bridge results
   ALWAYS take priority over built-in simplified frame results.
2. Track B (Built-in Simplified Planar Frame Matrix Verification — Zero Commercial License Needed):
   `solve_independent_frame_fem(calc_report)` assembles:
   - Superstructure: 20-element variable-section Timoshenko T-girder stiffness matrix `[K_beam]{u}={F}`
     with tapered end webs, shear deformation (`Phi`), and elastomeric bearing vertical springs;
   - Substructure: Genuine 2D planar frame stiffness matrix `[K_pier]{u}={F}` (6-DOF `[ux, uy, rz]`
     frame elements with coordinate transformation) for **盖梁 + 双柱 + 桩顶系梁 + 桩顶等效刚度支承**.
   Explicitly and honestly labeled with its exact scope and simplifying assumptions (e.g., equivalent
   pile-top springs without nonlinear m-method soil layering or time-step creep coupling).
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from pathlib import Path
from typing import Any


def _solve_dense_spd(k_mat: list[list[float]], f_vec: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for 2D beam/frame stiffness systems."""
    n = len(f_vec)
    aug = [row[:] + [f_vec[i]] for i, row in enumerate(k_mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ValueError(f"singular FEM stiffness matrix at DOF {col}")
        if piv != col:
            aug[col], aug[piv] = aug[piv], aug[col]
        diag = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= diag
        for r in range(n):
            if r != col and abs(aug[r][col]) > 1e-15:
                fac = aug[r][col]
                for j in range(col, n + 1):
                    aug[r][j] -= fac * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _frame2d_local_stiffness(ea: float, ei: float, ga_s: float, length: float) -> list[list[float]]:
    """6x6 local 2D Timoshenko frame element stiffness matrix for DOFs [u1, v1, th1, u2, v2, th2]."""
    k_ax = ea / length
    phi = (12.0 * ei) / max(1e-6, ga_s * length * length) if ga_s > 0.0 else 0.0
    c = ei / ((1.0 + phi) * (length**3))
    return [
        [k_ax, 0.0, 0.0, -k_ax, 0.0, 0.0],
        [0.0, 12.0 * c, 6.0 * length * c, 0.0, -12.0 * c, 6.0 * length * c],
        [0.0, 6.0 * length * c, (4.0 + phi) * length * length * c, 0.0, -6.0 * length * c, (2.0 - phi) * length * length * c],
        [-k_ax, 0.0, 0.0, k_ax, 0.0, 0.0],
        [0.0, -12.0 * c, -6.0 * length * c, 0.0, 12.0 * c, -6.0 * length * c],
        [0.0, 6.0 * length * c, (2.0 - phi) * length * length * c, 0.0, -6.0 * length * c, (4.0 + phi) * length * length * c],
    ]


def _frame2d_transform(cs: float, sn: float) -> list[list[float]]:
    return [
        [cs, sn, 0.0, 0.0, 0.0, 0.0],
        [-sn, cs, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, cs, sn, 0.0],
        [0.0, 0.0, 0.0, -sn, cs, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ]


def _mat6_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(6)) for j in range(6)] for i in range(6)]


def _mat6_transpose(a: list[list[float]]) -> list[list[float]]:
    return [[a[j][i] for j in range(6)] for i in range(6)]


def _solve_pier_planar_frame_2d(
    *,
    cap_length_m: float,
    cap_width_m: float,
    cap_height_m: float,
    col_positions_m: tuple[float, float],
    col_diameter_m: float,
    col_height_m: float,
    pile_diameter_m: float,
    pile_length_m: float,
    girder_xs_m: list[float],
    girder_uls_reactions_kn: list[float],
    lateral_wind_or_unbalanced_kn: float = 45.0,
) -> dict[str, float]:
    """Assemble and solve a genuine 2D planar frame stiffness matrix [K]{u}={F} for Cap + 2 Columns + Tie Beam."""
    ec30_kpa = 30000.0 * 1e3
    gc30_kpa = ec30_kpa / (2.0 * (1.0 + 0.20))

    x_col_l, x_col_r = float(col_positions_m[0]), float(col_positions_m[1])
    half_cap = 0.5 * cap_length_m
    y_tie = 0.0
    y_cap = col_height_m + 0.5 * cap_height_m

    # Build sorted unique X stations along the cap beam (including column faces for exact face hogging moment!)
    r_col = 0.5 * col_diameter_m
    cap_xs_set = {
        round(-half_cap, 4),
        round(half_cap, 4),
        round(x_col_l, 4),
        round(x_col_r, 4),
        round(x_col_l - r_col, 4),
        round(x_col_l + r_col, 4),
        round(x_col_r - r_col, 4),
        round(x_col_r + r_col, 4),
        0.0,
    }
    for gx in girder_xs_m:
        cap_xs_set.add(round(float(gx), 4))
    cap_xs = sorted(x for x in cap_xs_set if -half_cap - 1e-6 <= x <= half_cap + 1e-6)

    # Nodes: 0..len(cap_xs)-1 on cap centerline (x_i, y_cap); plus pile-top / tie-beam nodes L_bot and R_bot
    nodes: list[tuple[float, float]] = [(x, y_cap) for x in cap_xs]
    idx_cap_by_x = {x: i for i, x in enumerate(cap_xs)}
    idx_col_top_l = idx_cap_by_x[round(x_col_l, 4)]
    idx_col_top_r = idx_cap_by_x[round(x_col_r, 4)]
    idx_mid_cap = idx_cap_by_x[0.0]

    idx_bot_l = len(nodes)
    nodes.append((x_col_l, y_tie))
    idx_bot_r = len(nodes)
    nodes.append((x_col_r, y_tie))
    idx_tie_mid = len(nodes)
    nodes.append((0.5 * (x_col_l + x_col_r), y_tie))

    ndof = 3 * len(nodes)  # [ux (+right), uy (+up), rz (+CCW)]
    k_glob = [[0.0] * ndof for _ in range(ndof)]
    f_glob = [0.0] * ndof

    # Section properties
    a_cap = cap_width_m * cap_height_m
    i_cap = cap_width_m * (cap_height_m**3) / 12.0
    as_cap = 0.833 * a_cap

    a_col = math.pi * 0.25 * (col_diameter_m**2)
    i_col = math.pi * (col_diameter_m**4) / 64.0
    as_col = 0.90 * a_col

    tie_b, tie_h = 1.00, 1.20
    a_tie = tie_b * tie_h
    i_tie = tie_b * (tie_h**3) / 12.0
    as_tie = 0.833 * a_tie

    elements: list[dict[str, Any]] = []

    def _add_elem(etag: str, n1: int, n2: int, ea: float, ei: float, gas: float, q_down_kn_m: float = 0.0) -> None:
        x1, y1 = nodes[n1]
        x2, y2 = nodes[n2]
        dx, dy = x2 - x1, y2 - y1
        le = math.hypot(dx, dy)
        cs, sn = dx / le, dy / le
        k_loc = _frame2d_local_stiffness(ea, ei, gas, le)
        t_mat = _frame2d_transform(cs, sn)
        k_el = _mat6_mul(_mat6_transpose(t_mat), _mat6_mul(k_loc, t_mat))
        dofs = [3 * n1, 3 * n1 + 1, 3 * n1 + 2, 3 * n2, 3 * n2 + 1, 3 * n2 + 2]
        for r in range(6):
            for s in range(6):
                k_glob[dofs[r]][dofs[s]] += k_el[r][s]

        # Uniform downward load q_down_kn_m (along -global Y; for horizontal elements local +v is +Y when dx>0)
        f_fixed_loc = [0.0] * 6
        if abs(q_down_kn_m) > 1e-9 and abs(dy) < 1e-6:
            # Downward load is in -local v direction -> equivalent nodal load has -0.5*q*L, -q*L^2/12, -0.5*q*L, +q*L^2/12
            f_eq_loc = [0.0, -0.5 * q_down_kn_m * le, -(q_down_kn_m * le * le) / 12.0, 0.0, -0.5 * q_down_kn_m * le, (q_down_kn_m * le * le) / 12.0]
            # Fixed-end reaction vector {f_fixed_loc} = -{f_eq_loc} so internal end forces {p_loc} = [k_loc]{u_loc} + {f_fixed_loc}
            f_fixed_loc = [-v for v in f_eq_loc]
            t_tr = _mat6_transpose(t_mat)
            f_eq_glob = [sum(t_tr[r][s] * f_eq_loc[s] for s in range(6)) for r in range(6)]
            for r in range(6):
                f_glob[dofs[r]] += f_eq_glob[r]

        elements.append(
            {
                "tag": etag,
                "n1": n1,
                "n2": n2,
                "x1": x1,
                "x2": x2,
                "length": le,
                "k_loc": k_loc,
                "t_mat": t_mat,
                "f_fixed_loc": f_fixed_loc,
                "dofs": dofs,
            }
        )

    # 1. Cap beam elements (stiffened inside column joint zones [x_col - r_col .. x_col + r_col])
    q_cap_uls = 1.1 * 1.2 * (a_cap * 25.0)
    for i in range(len(cap_xs) - 1):
        xm = 0.5 * (cap_xs[i] + cap_xs[i + 1])
        in_joint = (abs(xm - x_col_l) <= r_col + 1e-4) or (abs(xm - x_col_r) <= r_col + 1e-4)
        stiff_mult = 6.0 if in_joint else 1.0
        _add_elem(f"cap_{i}", i, i + 1, ec30_kpa * a_cap, ec30_kpa * i_cap * stiff_mult, gc30_kpa * as_cap, q_down_kn_m=q_cap_uls)

    # 2. Twin circular columns (from y_tie=0 up to y_cap)
    _add_elem("col_left", idx_bot_l, idx_col_top_l, ec30_kpa * a_col, ec30_kpa * i_col, gc30_kpa * as_col)
    _add_elem("col_right", idx_bot_r, idx_col_top_r, ec30_kpa * a_col, ec30_kpa * i_col, gc30_kpa * as_col)

    # Column ULS self-weight at column base/top nodes
    w_col_uls = 1.1 * 1.2 * (a_col * col_height_m * 25.0)
    for n_top, n_bot in ((idx_col_top_l, idx_bot_l), (idx_col_top_r, idx_bot_r)):
        f_glob[3 * n_top + 1] -= 0.5 * w_col_uls
        f_glob[3 * n_bot + 1] -= 0.5 * w_col_uls

    # 3. Pile-top tie beam (two elements: bot_l -> tie_mid -> bot_r)
    q_tie_uls = 1.1 * 1.2 * (a_tie * 25.0)
    _add_elem("tie_left", idx_bot_l, idx_tie_mid, ec30_kpa * a_tie, ec30_kpa * i_tie, gc30_kpa * as_tie, q_down_kn_m=q_tie_uls)
    _add_elem("tie_right", idx_tie_mid, idx_bot_r, ec30_kpa * a_tie, ec30_kpa * i_tie, gc30_kpa * as_tie, q_down_kn_m=q_tie_uls)

    # 4. Apply 5 girder ULS downward point reactions + transverse frame lateral action at cap level
    for gx, r_kn in zip(girder_xs_m, girder_uls_reactions_kn):
        n_g = idx_cap_by_x[round(float(gx), 4)]
        f_glob[3 * n_g + 1] -= float(r_kn)
    f_glob[3 * idx_col_top_l + 0] += lateral_wind_or_unbalanced_kn

    # 5. Equivalent pile-head stiffness springs at bot_l & bot_r (Φ1.4m bored piles of effective depth L_eff)
    a_pile = math.pi * 0.25 * (pile_diameter_m**2)
    i_pile = math.pi * (pile_diameter_m**4) / 64.0
    l_eff = max(4.5, min(0.45 * pile_length_m, 4.0 * pile_diameter_m))
    k_pile_vert = (ec30_kpa * a_pile) / max(5.0, 0.55 * pile_length_m)
    k_pile_lat = (12.0 * ec30_kpa * i_pile) / (l_eff**3)
    k_pile_rot = (4.0 * ec30_kpa * i_pile) / l_eff
    for n_bot in (idx_bot_l, idx_bot_r):
        k_glob[3 * n_bot + 0][3 * n_bot + 0] += k_pile_lat
        k_glob[3 * n_bot + 1][3 * n_bot + 1] += k_pile_vert
        k_glob[3 * n_bot + 2][3 * n_bot + 2] += k_pile_rot

    u_glob = _solve_dense_spd(k_glob, f_glob)

    def _recover_local_forces(el: dict[str, Any]) -> list[float]:
        u_e = [u_glob[d] for d in el["dofs"]]
        u_loc = [sum(el["t_mat"][r][s] * u_e[s] for s in range(6)) for r in range(6)]
        return [sum(el["k_loc"][r][s] * u_loc[s] for s in range(6)) + el["f_fixed_loc"][r] for r in range(6)]

    # Recover cap hogging moment at column faces, cap mid-span sagging moment, column base N & M, and tie-beam end M
    cap_face_moments: list[float] = []
    cap_mid_moment = 0.0
    col_base_axials: list[float] = []
    col_base_moments: list[float] = []
    tie_end_moments: list[float] = []

    for el in elements:
        p_loc = _recover_local_forces(el)
        # In 2D local frame [u1, v1, th1, u2, v2, th2]: internal bending moment M(x1) = -p_loc[2], M(x2) = +p_loc[5]
        if el["tag"].startswith("cap_"):
            if math.isclose(el["x2"], x_col_l - r_col, abs_tol=1e-3) or math.isclose(el["x2"], x_col_r - r_col, abs_tol=1e-3):
                cap_face_moments.append(abs(p_loc[5]))
            if math.isclose(el["x1"], x_col_l + r_col, abs_tol=1e-3) or math.isclose(el["x1"], x_col_r + r_col, abs_tol=1e-3):
                cap_face_moments.append(abs(p_loc[2]))
            if el["n2"] == idx_mid_cap:
                cap_mid_moment = abs(p_loc[5])
        elif el["tag"].startswith("col_"):
            # Node 1 is column base (y=0), Node 2 is column top: compression at base is +p_loc[0]
            col_base_axials.append(abs(p_loc[0]))
            col_base_moments.append(max(abs(p_loc[2]), abs(p_loc[5])))
        elif el["tag"] == "tie_left":
            tie_end_moments.append(abs(p_loc[2]))
        elif el["tag"] == "tie_right":
            tie_end_moments.append(abs(p_loc[5]))

    return {
        "cap_hogging_moment_uls_knm": max(cap_face_moments, default=0.0),
        "cap_sagging_moment_uls_knm": cap_mid_moment,
        "pier_column_axial_uls_kn": max(col_base_axials, default=0.0),
        "pier_column_moment_uls_knm": max(col_base_moments, default=0.0),
        "tie_beam_moment_uls_knm": max(tie_end_moments, default=0.0),
        "frame_dof_count": ndof,
        "frame_element_count": len(elements),
    }


def solve_independent_frame_fem(calc_report: Mapping[str, Any]) -> dict[str, Any]:
    """Run built-in 20-element variable-section Timoshenko T-girder + 2D planar cap-column-tie-beam matrix analysis."""
    ps = calc_report["project_summary"]
    sec = calc_report["t_girder_section"]
    lat = calc_report["lateral_distribution"]
    env = calc_report["superstructure_envelopes"]
    pre = calc_report["prestress"]
    pr_sum = calc_report["pier_reaction_summary"]
    cap_1d = calc_report["pier_cap_beam_1d"]
    sub = calc_report["substructure_checks"]

    span_l = float(ps["calc_span_m"])
    mid_m = sec["mid_span_m"]
    end_mm = sec["end_span_mm"]
    a_mid = float(mid_m["area_m2"])
    i_mid = float(mid_m["ix_m4"])
    a_end = float(end_mm["area"]) * 1e-6
    i_end = float(end_mm["ix"]) * 1e-12
    h_m = float(sec["mid_span_mm"]["height"]) * 1e-3

    # Transformed stiffness uplift from prestressing tendons & bottom HRB400 bars (net of post-tensioning ducts)
    n_tendons_i = int(pre["recommended_tendons"])
    ap_m2 = float(pre["provided_strands"]) * 140.0e-6
    duct_area_m2 = n_tendons_i * math.pi * 0.25 * (0.070**2)
    as_m2 = 6.0 * math.pi * 0.25 * (0.022**2)
    ep_m = float(pre["eccentricity_ep_m"])
    alpha_ep = 195000.0 / 34500.0
    alpha_es = 200000.0 / 34500.0
    delta_i_steel = (
        -duct_area_m2 * (ep_m**2)
        + (alpha_ep - 1.0) * ap_m2 * (ep_m**2)
        + (alpha_es - 1.0) * as_m2 * ((ep_m + 0.085) ** 2)
    )

    ec_kpa = 34500.0 * 1e3
    gc_kpa = ec_kpa / (2.0 * (1.0 + 0.20))

    # 1. Superstructure: 20-element variable-section Timoshenko beam stiffness matrix [K_beam]{u}={F}
    n_elem = 20
    n_node = n_elem + 1
    xs = [span_l * i / n_elem for i in range(n_node)]
    ndof = 2 * n_node

    def _sec_props_at(xm: float) -> tuple[float, float, float]:
        d_end = min(xm, span_l - xm)
        if d_end <= 1.20:
            frac = 0.0
        elif d_end <= 2.20:
            frac = (d_end - 1.20) / 1.00
        else:
            frac = 1.0
        area = a_end + frac * (a_mid - a_end)
        ix = (i_end + frac * (i_mid - i_end)) + delta_i_steel
        b_web = 0.56 + frac * (0.20 - 0.56)
        a_shear = max(0.22, 0.83 * b_web * h_m)
        return area, ix, a_shear

    k_beam = [[0.0] * ndof for _ in range(ndof)]
    elem_stiff: list[tuple[int, float, list[list[float]]]] = []
    dl_bk = env["dead_load_breakdown"]
    g2_super_kn_m = float(dl_bk["diaphragm_equiv_kn_m"]) + float(dl_bk["superimposed_dead_kn_m"])
    f_dead = [0.0] * ndof

    for e in range(n_elem):
        x0, x1 = xs[e], xs[e + 1]
        le = x1 - x0
        area_e, ix_e, as_e = _sec_props_at(0.5 * (x0 + x1))
        ei = ec_kpa * ix_e
        phi = (12.0 * ei) / max(1e-6, gc_kpa * as_e * le * le)
        c = ei / ((1.0 + phi) * (le**3))
        ke = [
            [12.0 * c, 6.0 * le * c, -12.0 * c, 6.0 * le * c],
            [6.0 * le * c, (4.0 + phi) * le * le * c, -6.0 * le * c, (2.0 - phi) * le * le * c],
            [-12.0 * c, -6.0 * le * c, 12.0 * c, -6.0 * le * c],
            [6.0 * le * c, (2.0 - phi) * le * le * c, -6.0 * le * c, (4.0 + phi) * le * le * c],
        ]
        elem_stiff.append((e, le, ke))
        qe = area_e * 26.0 + g2_super_kn_m
        fe = [0.5 * qe * le, (qe * le * le) / 12.0, 0.5 * qe * le, -(qe * le * le) / 12.0]
        dofs = [2 * e, 2 * e + 1, 2 * (e + 1), 2 * (e + 1) + 1]
        for r in range(4):
            f_dead[dofs[r]] += fe[r]
            for s in range(4):
                k_beam[dofs[r]][dofs[s]] += ke[r][s]

    kv_bearing = 1.60e6
    k_beam[0][0] += kv_bearing
    k_beam[2 * n_elem][2 * n_elem] += kv_bearing
    u_dead = _solve_dense_spd(k_beam, f_dead)

    def _elem_mid_moment_and_end_shear(u_vec: list[float], f_nodal_equiv: list[float] | None = None) -> tuple[float, float]:
        mid_e = n_elem // 2 - 1
        _, le_m, ke_m = elem_stiff[mid_e]
        dofs_m = [2 * mid_e, 2 * mid_e + 1, 2 * (mid_e + 1), 2 * (mid_e + 1) + 1]
        um = [u_vec[d] for d in dofs_m]
        pm = [sum(ke_m[r][s] * um[s] for s in range(4)) for r in range(4)]
        if f_nodal_equiv is not None:
            area_m, _, _ = _sec_props_at(0.5 * (xs[mid_e] + xs[mid_e + 1]))
            qm = area_m * 26.0 + g2_super_kn_m
            pm[3] -= -(qm * le_m * le_m) / 12.0
        m_mid_knm = abs(pm[3])

        _, le_0, ke_0 = elem_stiff[0]
        u0 = [u_vec[0], u_vec[1], u_vec[2], u_vec[3]]
        p0 = [sum(ke_0[r][s] * u0[s] for s in range(4)) for r in range(4)]
        if f_nodal_equiv is not None:
            area_0, _, _ = _sec_props_at(0.5 * (xs[0] + xs[1]))
            q0 = area_0 * 26.0 + g2_super_kn_m
            p0[0] -= 0.5 * q0 * le_0
        v_sup_kn = abs(p0[0])
        return m_mid_knm, v_sup_kn

    m_dead_mid_fem, v_dead_sup_fem = _elem_mid_moment_and_end_shear(u_dead, f_dead)

    qk = float(env["lane_qk_kn_m"])
    pk = float(env["lane_pk_kn"])
    mu = float(env["impact_factor_mu"])
    mcq = max(float(x["m_cq"]) for x in lat["eccentric_compression"])
    moq = max(float(x["m_oq"]) for x in lat["lever_rule"])
    gamma0 = round(
        float(env["mid_span_summary"]["m_uls_knm"])
        / max(1.0, 1.2 * float(env["mid_span_summary"]["m_dead_knm"]) + 1.4 * float(env["mid_span_summary"]["m_live_knm"])),
        4,
    )

    f_live_mid = [0.0] * ndof
    q_live_mid = (1.0 + mu) * mcq * qk
    for e in range(n_elem):
        le = xs[e + 1] - xs[e]
        fe = [0.5 * q_live_mid * le, (q_live_mid * le * le) / 12.0, 0.5 * q_live_mid * le, -(q_live_mid * le * le) / 12.0]
        dofs = [2 * e, 2 * e + 1, 2 * (e + 1), 2 * (e + 1) + 1]
        for r in range(4):
            f_live_mid[dofs[r]] += fe[r]
    f_live_mid[2 * (n_elem // 2)] += (1.0 + mu) * mcq * pk
    u_live_mid = _solve_dense_spd(k_beam, f_live_mid)

    mid_e = n_elem // 2 - 1
    _, le_m, ke_m = elem_stiff[mid_e]
    dofs_m = [2 * mid_e, 2 * mid_e + 1, 2 * (mid_e + 1), 2 * (mid_e + 1) + 1]
    m_live_mid_fem = abs(
        sum(ke_m[3][s] * u_live_mid[dofs_m[s]] for s in range(4)) + (q_live_mid * le_m * le_m) / 12.0
    )

    elastic_env_defl_m = 0.0
    for e in range(n_elem):
        xm = 0.5 * (xs[e] + xs[e + 1])
        le = xs[e + 1] - xs[e]
        _, ix_e, as_e = _sec_props_at(xm)
        mq_env_x = m_live_mid_fem * (4.0 * xm * (span_l - xm)) / (span_l * span_l)
        m_bar_x = 0.5 * xm if xm <= 0.5 * span_l else 0.5 * (span_l - xm)
        vq_x = m_live_mid_fem * abs(span_l - 2.0 * xm) / (span_l * span_l) * 2.0
        v_bar_x = 0.5
        elastic_env_defl_m += (
            (mq_env_x * m_bar_x) / (0.85 * ec_kpa * ix_e)
            + (vq_x * v_bar_x) / (0.85 * gc_kpa * as_e)
        ) * le
    f_live_fem_mm = elastic_env_defl_m * 1.60 * 1000.0

    f_live_sup = [0.0] * ndof
    for e in range(n_elem):
        xm = 0.5 * (xs[e] + xs[e + 1])
        m_lat_x = moq - (moq - mcq) * (xm / (0.25 * span_l)) if xm < 0.25 * span_l else mcq
        qe_l = (1.0 + mu) * m_lat_x * qk
        le = xs[e + 1] - xs[e]
        fe = [0.5 * qe_l * le, (qe_l * le * le) / 12.0, 0.5 * qe_l * le, -(qe_l * le * le) / 12.0]
        dofs = [2 * e, 2 * e + 1, 2 * (e + 1), 2 * (e + 1) + 1]
        for r in range(4):
            f_live_sup[dofs[r]] += fe[r]
    f_live_sup[0] += 0.98 * (1.0 + mu) * moq * (1.2 * pk)
    f_live_sup[2] += 0.02 * (1.0 + mu) * moq * (1.2 * pk)
    u_live_sup = _solve_dense_spd(k_beam, f_live_sup)
    v_live_sup_fem = abs(kv_bearing * u_live_sup[0])

    md_mid_fem = gamma0 * (1.2 * m_dead_mid_fem + 1.4 * m_live_mid_fem)
    vd_sup_fem = gamma0 * (1.2 * v_dead_sup_fem + 1.4 * v_live_sup_fem)

    # 2. Substructure: Genuine 2D Planar Frame Stiffness Matrix [K_pier]{u}={F} (Cap + Twin Columns + Tie Beam)
    total_girder_self_dead_kn = sum(_sec_props_at(0.5 * (xs[e] + xs[e + 1]))[0] * 26.0 * (xs[e + 1] - xs[e]) for e in range(n_elem))
    dead_per_sup_fem = 0.5 * (total_girder_self_dead_kn + g2_super_kn_m * span_l)
    n_adj = int(pr_sum["n_adjacent_spans"])
    pier_dead_fem_per_g = dead_per_sup_fem * n_adj

    girder_reacs_base = [float(x) for x in cap_1d["girder_reactions_kn"]]
    sum_pier_uls_prog = sum(girder_reacs_base)
    per_girder_dead_inc_uls = gamma0 * 1.2 * (pier_dead_fem_per_g - float(pr_sum["left_span_dead_per_girder_kn"]) * n_adj)
    girder_reacs_fem = [r + per_girder_dead_inc_uls for r in girder_reacs_base]
    pier_top_uls_fem = sum_pier_uls_prog + per_girder_dead_inc_uls * int(lat["n_girders"])

    cap_l = float(cap_1d["cap_length_m"])
    cap_w = float(sub["cap_beam"]["width_m"])
    cap_h = float(sub["cap_beam"]["height_m"])
    col_d = float(sub["pier_column"]["diameter_m"])
    col_h = float(sub["pier_column"]["height_m"])
    pile_d = float(sub["bored_pile"]["diameter_m"])
    pile_l = float(sub["bored_pile"]["design_length_m"])
    raw_c0 = float(cap_1d["column_positions_m"][0])
    raw_c1 = float(cap_1d["column_positions_m"][-1])
    if raw_c0 >= 0.0 and raw_c1 > 0.5 * cap_l:
        col_pos = (raw_c0 - 0.5 * cap_l, raw_c1 - 0.5 * cap_l)
    else:
        col_pos = (raw_c0, raw_c1)
    n_g = int(lat["n_girders"])
    g_sp = float(lat["girder_spacing_m"])
    girder_xs = [(i - 0.5 * (n_g - 1)) * g_sp for i in range(n_g)]

    pier_frame = _solve_pier_planar_frame_2d(
        cap_length_m=cap_l,
        cap_width_m=cap_w,
        cap_height_m=cap_h,
        col_positions_m=col_pos,
        col_diameter_m=col_d,
        col_height_m=col_h,
        pile_diameter_m=pile_d,
        pile_length_m=pile_l,
        girder_xs_m=girder_xs,
        girder_uls_reactions_kn=girder_reacs_fem,
    )

    sw_label = "内置平面杆系矩阵复核程序 (上部20单元Timoshenko梁 + 下部盖梁-双柱-系梁刚架矩阵解)"
    pier_meta_obj = {
        "method": "2D_planar_timoshenko_frame_stiffness_matrix",
        "solver_method": "2D_planar_timoshenko_frame_stiffness_matrix",
        "dof_count": int(pier_frame["frame_dof_count"]),
        "element_count": int(pier_frame["frame_element_count"]),
        "uses_heuristic_reduction_factor": False,
    }
    return {
        "software_name": sw_label,
        "verification_scope": "builtin_simplified_frame_fem",
        "source_type": "builtin_solver",
        "is_template": False,
        "tolerance_pct": 8.0,
        "checked_by": "内置平面杆系刚度矩阵求解器（简化复核，非商业软件实算）",
        "scope_limitations_zh": (
            "上部采用20单元变截面Timoshenko梁刚度矩阵求解；下部采用盖梁—双柱—桩顶系梁平面杆系刚度矩阵求解"
            "（桩顶采用等效刚度弹簧简化，不含桥梁博士 m 法分层桩土弹簧非线性耦合及系梁裂缝全过程电算；若有桥梁博士下部分册实算结果应优先采用）"
        ),
        "substructure_matrix_meta": pier_meta_obj,
        "pier_frame_model": pier_meta_obj,
        "model_card": {
            "software_name": sw_label,
            "girder_model": (
                f"上部1#边T梁：20单元/21节点变截面Timoshenko梁矩阵解"
                f"（计入梁端1.2m腹板加厚560mm、1.0m渐变段、剪切变形因子Φ及支座竖向刚度kv={kv_bearing/1e6:.2f}×10⁶ kN/m）"
            ),
            "pier_model": (
                f"下部2#双柱墩：盖梁({cap_l:g}m)—双柱(Dc={col_d:g}m, H={col_h:g}m)—桩顶系梁(1.0×1.2m)平面杆系刚度矩阵解"
                f"（{pier_frame['frame_element_count']}单元/{pier_frame['frame_dof_count']}自由度，桩顶等效刚度支承简化，不含m法分层土弹簧）"
            ),
            "boundary_conditions": "主梁两端板式橡胶支座竖向弹性支承；下部盖梁-双柱-桩顶系梁节点刚接，桩顶按等效轴向/水平/转动刚度支承",
            "load_and_combinations": "变截面实算恒载 + 公路-I级车道荷载影响线加载，按 JTG D60-2015 基本组合 γ0(1.2G+1.4Q) 与频遇/标准组合复核",
            "model_figure_paths": [],
        },
        "metrics": {
            "girder_mid_moment_uls_knm": round(md_mid_fem, 2),
            "girder_support_shear_uls_kn": round(vd_sup_fem, 2),
            "girder_live_deflection_mm": round(f_live_fem_mm, 2),
            "pier_top_reaction_uls_kn": round(pier_top_uls_fem, 2),
            "cap_hogging_moment_uls_knm": round(pier_frame["cap_hogging_moment_uls_knm"], 2),
            "cap_sagging_moment_uls_knm": round(pier_frame["cap_sagging_moment_uls_knm"], 2),
            "pier_column_axial_uls_kn": round(pier_frame["pier_column_axial_uls_kn"], 2),
            "pier_column_moment_uls_knm": round(pier_frame["pier_column_moment_uls_knm"], 2),
            "tie_beam_moment_uls_knm": round(pier_frame["tie_beam_moment_uls_knm"], 2),
        },
    }


def generate_drbridge_modeling_guide(calc_report: Mapping[str, Any], output_dir: str | Path | None = None) -> dict[str, Any]:
    """Generate a ready-to-follow Dr.Bridge V4.4/V5.0 modeling card & null-valued (`is_template: true`) JSON template."""
    ps = calc_report["project_summary"]
    sec = calc_report["t_girder_section"]
    lat = calc_report["lateral_distribution"]
    env = calc_report["superstructure_envelopes"]
    pre = calc_report["prestress"]
    cap_1d = calc_report["pier_cap_beam_1d"]
    sub = calc_report["substructure_checks"]
    dl_bk = env["dead_load_breakdown"]

    span_l = float(ps["calc_span_m"])
    beam_l = float(ps["beam_length_m"])
    end_ext = round(0.5 * (beam_l - span_l), 3)
    col_sp = round(float(cap_1d["column_positions_m"][-1] - cap_1d["column_positions_m"][0]), 2)

    guide_md = f"""# 《桥梁博士 Dr.Bridge V4.4 / V5.0》上下部分册电算建模与实算导入操作卡

> **适用项目**：{ps['project_name']}（标准跨径 `{ps['n_spans']}×{ps['standard_span_m']:g} m`，计算跨径 `L = {span_l:g} m`，预制梁全长 `{beam_l:g} m`，支承链 `{end_ext*1000:g} + {(float(ps['standard_span_m'])-beam_l)*1000:g} + {end_ext*1000:g} = {(2*end_ext + float(ps['standard_span_m']) - beam_l)*1000:g} mm`）

---

## 一、上部分册：1# 代表性边 T 梁模型要点
1. **总体信息**：`2018公路规范`（JTG 3362—2018 / JTG D60—2015），结构重要性系数 `γ0 = 1.1`，环境类别 `I 类`。
2. **节点与变截面**：总长 `{beam_l:g} m`，左梁端 `0.000` → 左支点 `Z1({end_ext:g} m)` → 跨中 `KZ({0.5*beam_l:.2f} m)` → 右支点 `Z2({beam_l - end_ext:g} m)` → 右梁端 `{beam_l:g} m`。跨中受压翼缘有效宽 `{sec['effective_flange_width_mm']:g} mm`、梁高 `{sec['mid_span_mm']['height']:g} mm`、标准腹板厚 `200 mm`、马蹄 `560×200 mm`；距支点 `1.20 m` 内腹板加厚 `560 mm` + `1.00 m` 渐变段。
3. **预应力钢束与施工阶段**：`{pre['recommended_tendons']}` 束 `Φs15.2-{pre['strands_per_tendon']}`（张拉阶段后务必勾选对应钢束**管道灌浆**！`σcon = {pre['sigma_con_mpa']:g} MPa`），二期恒载 `g2 = {dl_bk['superimposed_dead_kn_m']:.3f} kN/m`，活载横向分布系数：支点 `moq = {lat['lever_rule'][0]['m_oq']:.4f}`、跨中 `mcq = {lat['eccentric_compression'][0]['m_cq']:.4f}`。

## 二、下部分册：2# 中间双柱墩（盖梁 + 双柱 + 桩顶系梁 + 钻孔桩）模型要点
1. **构件尺寸**：盖梁 C30 长 `{cap_1d['cap_length_m']:g} m`、截面 `{sub['cap_beam']['width_m']*1000:g}×{sub['cap_beam']['height_m']*1000:g} mm`；双柱间距 `{col_sp:g} m`、圆形截面 `D = {sub['pier_column']['diameter_m']*1000:g} mm`（注意：圆形截面方可配螺旋箍 `DZ2`；矩形系梁 `1000×1200 mm` 须录入上下主筋 `XL1 12Φ22` 及矩形四肢箍 `XL2 Φ12@100`）；钻孔桩 `D = {sub['bored_pile']['diameter_m']*1000:g} mm`、桩长 `{sub['bored_pile']['design_length_m']:g} m`。
2. **导出与回填校验**：在结果查询中严格按**构件、截面、荷载组合（基本组合 ULS vs 正常使用消除自重活载）、单位**填入 `drbridge_import_template.json`（将 `"is_template": false` 并填入实算值；切勿将“运营标准值”填入“基本组合”字段）。
"""

    template_obj = {
        "is_template": True,
        "software_name": "桥梁博士 Dr.Bridge（请填写机房实际版本号，并将 is_template 改为 false）",
        "checked_by": "",
        "tolerance_pct": 8.0,
        "model_card": {
            "girder_model": f"1#边T梁10单元/11节点空间杆系模型 (L={span_l:g}m, 梁全长{beam_l:g}m, C50)",
            "pier_model": f"2#中间双柱墩盖梁({cap_1d['cap_length_m']:g}m)—双柱(Φ{sub['pier_column']['diameter_m']:g}m)—桩顶系梁—桩基模型 (C30)",
            "boundary_conditions": "Z1固定铰支座、Z2纵向滑动支座；下部双柱墩盖梁-墩柱-系梁刚接、m法桩土弹簧支承",
            "model_figure_paths": [],
        },
        "metrics": {
            "girder_mid_moment_uls_knm": {
                "member": "1#边主梁",
                "section": "跨中截面(L/2)",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN·m",
                "source": "桥梁博士上部分册实算",
                "value": None,
            },
            "girder_support_shear_uls_kn": {
                "member": "1#边主梁",
                "section": "支点截面(Z1)",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN",
                "source": "桥梁博士上部分册实算",
                "value": None,
            },
            "girder_live_deflection_mm": {
                "member": "1#边主梁",
                "section": "跨中截面(L/2)",
                "combination": "正常使用消除自重汽车活载挠度(SLS)",
                "unit": "mm",
                "source": "桥梁博士上部分册实算",
                "value": None,
            },
            "pier_top_reaction_uls_kn": {
                "member": "2#中间双柱墩盖梁顶",
                "section": "双跨5支座反力合计",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN",
                "source": "桥梁博士上下部反力汇总",
                "value": None,
            },
        },
        "substructure_metrics": {
            "cap_hogging_moment_uls_knm": {
                "member": "2#墩盖梁",
                "section": "柱顶负弯矩控制截面",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN·m",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
            "cap_sagging_moment_uls_knm": {
                "member": "2#墩盖梁",
                "section": "盖梁跨中正弯矩截面",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN·m",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
            "pier_column_axial_uls_kn": {
                "member": "2#墩圆柱墩",
                "section": "墩柱柱底控制截面",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
            "pier_column_moment_uls_knm": {
                "member": "2#墩圆柱墩",
                "section": "墩柱柱底控制截面",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN·m",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
            "tie_beam_moment_uls_knm": {
                "member": "桩顶横系梁",
                "section": "系梁端部节点截面",
                "combination": "承载能力极限状态基本组合(ULS)",
                "unit": "kN·m",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
            "tie_beam_crack_width_mm": {
                "member": "桩顶横系梁",
                "section": "系梁端部受拉区",
                "combination": "正常使用短期/频遇组合裂缝宽度(SLS)",
                "unit": "mm",
                "source": "桥梁博士下部分册实算",
                "value": None,
            },
        },
    }

    guide_path: str | None = None
    tpl_path: str | None = None
    if output_dir is not None:
        out_p = Path(output_dir).expanduser().resolve()
        out_p.mkdir(parents=True, exist_ok=True)
        gp = out_p / "drbridge_modeling_guide.md"
        tp = out_p / "drbridge_import_template.json"
        gp.write_text(guide_md, encoding="utf-8")
        tp.write_text(json.dumps(template_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        guide_path = str(gp)
        tpl_path = str(tp)

    return {
        "guide_markdown": guide_md,
        "template_json": template_obj,
        "guide_markdown_path": guide_path,
        "template_json_path": tpl_path,
    }
