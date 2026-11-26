"""Publication-Grade Word & Defense-PPT Calculation Figures Generator for Bridge Engineering.

Generates high-DPI PNG (300 DPI) + vector SVG figures directly from live `calc_report` data
without mutating global Matplotlib state or pyplot backends, sized and font-scaled specifically
so that 15.2 cm Word printouts remain crisp without standalone half-empty figure pages:
1. `fig0_scheme_comparison`: Three Opening-Report Bridge Schemes (6 panels: 3 elevations + 3 cross-sections);
2. `fig_girder_detail`: Dimensioned Exterior & Interior T-Girder End/Mid-Span Cross-Sections (4 panels, mm);
3. `fig1_structural_model`: Structural Calculation Model Diagram (1# Simply Supported T-Girder + 2# Double-Column Pier);
4. `fig2_moment_shear_envelope`: Bending Moment & Shear Force Envelopes (enlarged legend & governing callouts);
5. `fig3_tendon_and_prestress`: Prestressing Tendon Parabolic Profile & Effective Prestress Distribution along Span;
6. `fig4_pile_soil_profile`: Pier & Abutment Bored Pile Soil Strata & Bearing Socket Profile Comparison.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import os
from pathlib import Path
import shutil
from typing import Any

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle


def _resolve_cjk_font_path() -> Path | None:
    """Locate a reliable CJK + Unicode TTF/TTC/OTF font across Windows, CI, macOS, and Linux."""
    candidates: list[Path] = []
    windir = os.environ.get("WINDIR")
    if windir:
        wf = Path(windir) / "Fonts"
        candidates.extend(
            [
                wf / "msyh.ttc",
                wf / "simhei.ttf",
                wf / "simsun.ttc",
                wf / "cad-ci-unifont-16.0.04.otf",
            ]
        )
    candidates.extend(
        [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/STHeiti Light.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/opentype/unifont/unifont.otf"),
        ]
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


def _make_fp(font_path: Path | None, size: float = 10.5, weight: str = "normal") -> FontProperties:
    if font_path is not None:
        return FontProperties(fname=str(font_path), size=size, weight=weight)
    return FontProperties(size=size, weight=weight)


def _style_axes(ax: Any, fp_tick: FontProperties) -> None:
    ax.set_facecolor("#FFFFFF")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#334155")
        ax.spines[spine].set_linewidth(1.0)
    ax.grid(True, linestyle="--", linewidth=0.55, color="#CBD5E1", alpha=0.8)
    for lbl in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lbl.set_fontproperties(fp_tick)
        lbl.set_color("#0F172A")


def _save_fig(
    fig: Figure,
    out_dir: Path,
    stem: str,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, str]:
    saved: dict[str, str] = {}
    fig.set_dpi(dpi)
    if "png" in formats:
        png_path = out_dir / f"{stem}.png"
        canvas_agg = FigureCanvasAgg(fig)
        canvas_agg.print_png(str(png_path))
        saved["png_path"] = str(png_path.resolve())
    if "svg" in formats:
        svg_path = out_dir / f"{stem}.svg"
        canvas_svg = FigureCanvasSVG(fig)
        canvas_svg.print_svg(str(svg_path))
        saved["svg_path"] = str(svg_path.resolve())
    return saved


# ======================================================================
# Figure 0: Three Opening-Report Bridge Schemes (6 Panels: 3 Elev + 3 Sec)
# ======================================================================
def _render_fig0_scheme_comparison(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    ps = calc_report["project_summary"]
    schemes = list(calc_report.get("bridge_schemes") or [])
    n_spans = int(ps["n_spans"])
    std_span = float(ps["standard_span_m"])
    deck_w = float(ps["deck_width_m"])
    total_len = n_spans * std_span

    reused_images: list[dict[str, str]] = []
    for sc in schemes:
        for key_img, suffix in (("elevation_image_path", "elev"), ("section_image_path", "sec")):
            raw_p = sc.get(key_img)
            if raw_p and Path(raw_p).expanduser().is_file():
                src_p = Path(raw_p).expanduser().resolve()
                dst_p = out_dir / f"{sc['scheme_id']}_{suffix}{src_p.suffix.lower()}"
                if src_p != dst_p:
                    shutil.copy2(src_p, dst_p)
                reused_images.append({"scheme_id": str(sc["scheme_id"]), "kind": suffix, "path": str(dst_p)})

    # Compact aspect ratio (10.6 x 6.6) without redundant top project title so span/dimension labels are large & crisp!
    fig = Figure(figsize=(10.6, 6.6), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 10.6, "bold")
    fp_txt = _make_fp(font_path, 9.6)
    fp_tk = _make_fp(font_path, 9.4)

    for row_i, sc in enumerate((schemes + schemes[:1] * 3)[:3]):
        y_top = 0.945 - row_i * 0.322
        ax_el = fig.add_axes([0.035, y_top - 0.255, 0.59, 0.245])
        ax_sc = fig.add_axes([0.655, y_top - 0.255, 0.315, 0.245])
        for ax_c in (ax_el, ax_sc):
            ax_c.set_facecolor("#FFFFFF")
            for sp in ax_c.spines.values():
                sp.set_color("#94A3B8")
                sp.set_linewidth(0.85)
            ax_c.set_xticks([])
            ax_c.set_yticks([])

        gh = float(sc.get("girder_height_m", 1.5))
        sname = f"{sc.get('scheme_no', '')}：{sc.get('name', '')}"
        tag_color = "#0F766E" if sc.get("is_recommended") else "#1E293B"

        ax_el.set_xlim(-7.0, total_len + 7.0)
        ax_el.set_ylim(-9.4, 5.1)
        bed_x = [-5.5, 4.0, 22.0, 0.5 * total_len, total_len - 22.0, total_len - 4.0, total_len + 5.5]
        bed_y = [0.0, -0.6, -4.6, -5.4, -4.6, -0.6, 0.0]
        ax_el.plot(bed_x, bed_y, color="#78716C", linewidth=1.3)
        ax_el.plot([3.5, total_len - 3.5], [-2.2, -2.2], color="#0284C7", linestyle="--", linewidth=1.0)
        ax_el.text(total_len - 4.0, -1.9, "设计洪水位", fontproperties=fp_tk, color="#0284C7", ha="right")

        if "连续箱梁" in sc.get("name", ""):
            ax_el.add_patch(Rectangle((0.0, 0.0), total_len, gh, facecolor="#DBEAFE", edgecolor="#1D4ED8", linewidth=1.3))
        else:
            for k in range(n_spans):
                x0_s = k * std_span + 0.08
                ax_el.add_patch(
                    Rectangle((x0_s, 0.0), std_span - 0.16, gh, facecolor="#E0F2FE" if row_i == 0 else "#F1F5F9", edgecolor=tag_color, linewidth=1.25)
                )
        for k in range(n_spans + 1):
            xp = k * std_span
            if k in (0, n_spans):
                ax_el.add_patch(Rectangle((xp - 0.9, -2.0), 1.8, 2.0 + gh, facecolor="#E2E8F0", edgecolor="#475569", linewidth=1.0))
                ax_el.plot([xp, xp], [-2.0, -7.2], color="#475569", linewidth=1.7)
            else:
                ax_el.add_patch(Rectangle((xp - 0.55, -5.0), 1.1, 5.0, facecolor="#CBD5E1", edgecolor="#334155", linewidth=1.0))
                ax_el.plot([xp, xp], [-5.0, -8.4], color="#334155", linewidth=1.7)
            if k < n_spans:
                xm = (k + 0.5) * std_span
                ax_el.annotate(
                    "",
                    xy=(xp + std_span, gh + 1.15),
                    xytext=(xp, gh + 1.15),
                    arrowprops={"arrowstyle": "<->", "color": "#334155", "lw": 1.0},
                )
                ax_el.text(xm, gh + 1.40, f"{std_span:g} m", ha="center", va="bottom", fontproperties=fp_tk, color="#0F172A")

        ax_el.set_title(f"({2*row_i+1}) {sname} (总长 {total_len:g}m, h={gh:.2f}m)", fontproperties=fp_sub, color=tag_color, pad=4)
        mech_raw = str(sc.get("structural_mechanics", ""))
        if len(mech_raw) > 36:
            cut_pos = max(mech_raw.rfind("；", 0, 38), mech_raw.rfind("，", 0, 38))
            mech_note = mech_raw[:cut_pos] if cut_pos >= 14 else mech_raw[:36]
        else:
            mech_note = mech_raw
        ax_el.text(0.5 * total_len, -8.85, mech_note, ha="center", fontproperties=fp_txt, color="#334155")

        ax_sc.set_xlim(-0.58 * deck_w, 0.58 * deck_w)
        ax_sc.set_ylim(-gh - 0.95, 1.50)
        ax_sc.add_patch(Rectangle((-0.5 * deck_w, 0.0), deck_w, 0.18, facecolor="#94A3B8", edgecolor="#334155", linewidth=1.0))
        ax_sc.add_patch(Rectangle((-0.5 * deck_w, 0.18), 0.50, 0.75, facecolor="#CBD5E1", edgecolor="#334155", linewidth=0.9))
        ax_sc.add_patch(Rectangle((0.5 * deck_w - 0.50, 0.18), 0.50, 0.75, facecolor="#CBD5E1", edgecolor="#334155", linewidth=0.9))

        if row_i == 0 or "T梁" in sc.get("name", ""):
            ng_s = 5
            sp_s = (deck_w - 1.2) / max(1, ng_s - 1)
            for gi in range(ng_s):
                gx = (gi - 0.5 * (ng_s - 1)) * sp_s
                ax_sc.add_patch(Rectangle((gx - 0.12, -gh), 0.24, gh, facecolor="#BAE6FD", edgecolor="#0369A1", linewidth=1.0))
                ax_sc.add_patch(Rectangle((gx - 0.28, -gh), 0.56, 0.22, facecolor="#BAE6FD", edgecolor="#0369A1", linewidth=0.9))
            sec_note = f"5片T梁, 间距{sp_s:.2f}m, 梁高{gh:.2f}m"
        elif "连续箱梁" in sc.get("name", ""):
            bw_top = deck_w - 1.6
            bw_bot = deck_w * 0.52
            trap = Polygon(
                [(-0.5 * bw_top, 0.0), (0.5 * bw_top, 0.0), (0.5 * bw_bot, -gh), (-0.5 * bw_bot, -gh)],
                closed=True,
                facecolor="#DBEAFE",
                edgecolor="#1D4ED8",
                linewidth=1.15,
            )
            ax_sc.add_patch(trap)
            for cell_x in (-0.21 * bw_bot, 0.21 * bw_bot):
                ax_sc.add_patch(Rectangle((cell_x - 0.16 * bw_bot, -gh + 0.22), 0.32 * bw_bot, gh - 0.44, facecolor="#FFFFFF", edgecolor="#1D4ED8", linewidth=0.9))
            sec_note = f"单箱双室连续箱梁, 梁高{gh:.2f}m"
        else:
            nb = 3 if deck_w <= 10.5 else 4
            sp_b = (deck_w - 1.4) / max(1, nb - 1)
            for bi in range(nb):
                bx = (bi - 0.5 * (nb - 1)) * sp_b
                ax_sc.add_patch(Rectangle((bx - 0.58, -gh), 1.16, gh, facecolor="#E2E8F0", edgecolor="#334155", linewidth=1.0))
                ax_sc.add_patch(Rectangle((bx - 0.36, -gh + 0.20), 0.72, gh - 0.40, facecolor="#FFFFFF", edgecolor="#475569", linewidth=0.85))
            sec_note = f"{nb}片预制小箱梁, 梁高{gh:.2f}m"

        ax_sc.annotate(
            "",
            xy=(0.5 * deck_w, 1.05),
            xytext=(-0.5 * deck_w, 1.05),
            arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.0},
        )
        ax_sc.text(0.0, 1.12, f"桥面全宽 {deck_w:g} m", ha="center", va="bottom", fontproperties=fp_tk, color="#0F172A")
        ax_sc.set_title(f"({2*row_i+2}) 横断面图 ({sc.get('verdict', '')})", fontproperties=fp_sub, color=tag_color, pad=4)
        ax_sc.text(0.0, -gh - 0.62, sec_note, ha="center", fontproperties=fp_txt, color="#1E293B")

    paths = _save_fig(fig, out_dir, "fig0_scheme_comparison", formats, dpi)
    return {
        "figure_id": "fig0_scheme_comparison",
        "title_zh": "三种桥型方案立面及横断面对比",
        "caption_zh": "图 2-1 三种桥型方案立面及横断面对比（单位：m）",
        "reused_opening_report_images": reused_images,
        **paths,
    }


# ======================================================================
# Figure 3-1 (NEW): Exterior & Interior T-Girder Dimensioned Sections
# Extracted directly from bridge construction drawings QL-05 & QL-07
# ======================================================================
def _render_fig_girder_detail(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    sec = calc_report["t_girder_section"]
    lat = calc_report["lateral_distribution"]
    ps = calc_report["project_summary"]

    h_mm = float(sec["mid_span_mm"]["height"])
    bf_mm = float(sec["effective_flange_width_mm"])
    sp_mm = float(lat["girder_spacing_m"]) * 1000.0
    cant_mm = float(lat["edge_cantilever_m"]) * 1000.0
    wet_mm = 400.0
    precast_int_w = max(1200.0, sp_mm - wet_mm)
    precast_ext_right = 0.5 * precast_int_w
    b_mid = 200.0
    b_bulb = 560.0 if sp_mm <= 2000.0 else 600.0
    h_bulb = 200.0
    h_taper = 200.0
    hf_tip = 200.0
    hh = 70.0
    bh = 210.0
    yb_mm = float(sec["mid_span_mm"]["y_bot"])

    fig = Figure(figsize=(10.5, 6.2), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 10.8, "bold")
    fp_dim = _make_fp(font_path, 9.5)
    fp_note = _make_fp(font_path, 9.2)

    specs = [
        (fig.add_axes([0.04, 0.53, 0.43, 0.40]), "(a) 外边梁梁端断面 (加厚腹板)", True, True),
        (fig.add_axes([0.53, 0.53, 0.43, 0.40]), "(b) 外边梁跨中断面 (马蹄标准段)", True, False),
        (fig.add_axes([0.04, 0.05, 0.43, 0.40]), "(c) 中梁梁端断面 (加厚腹板)", False, True),
        (fig.add_axes([0.53, 0.05, 0.43, 0.40]), "(d) 中梁跨中断面 (马蹄标准段)", False, False),
    ]

    for ax, title_s, is_exterior, is_end in specs:
        ax.set_facecolor("#FFFFFF")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])

        xl = -cant_mm if is_exterior else -0.5 * precast_int_w
        xr = precast_ext_right if is_exterior else 0.5 * precast_int_w
        bw = b_bulb if is_end else b_mid

        if is_end:
            poly_pts = [
                (xl, h_mm),
                (xr, h_mm),
                (xr, h_mm - hf_tip),
                (0.5 * bw + bh, h_mm - hf_tip - hh),
                (0.5 * bw, h_mm - hf_tip - hh),
                (0.5 * bw, 0.0),
                (-0.5 * bw, 0.0),
                (-0.5 * bw, h_mm - hf_tip - hh),
                (-0.5 * bw - bh, h_mm - hf_tip - hh),
                (xl, h_mm - hf_tip),
            ]
        else:
            poly_pts = [
                (xl, h_mm),
                (xr, h_mm),
                (xr, h_mm - hf_tip),
                (0.5 * bw + bh, h_mm - hf_tip - hh),
                (0.5 * bw, h_mm - hf_tip - hh),
                (0.5 * bw, h_bulb + h_taper),
                (0.5 * b_bulb, h_bulb),
                (0.5 * b_bulb, 0.0),
                (-0.5 * b_bulb, 0.0),
                (-0.5 * b_bulb, h_bulb),
                (-0.5 * bw, h_bulb + h_taper),
                (-0.5 * bw, h_mm - hf_tip - hh),
                (-0.5 * bw - bh, h_mm - hf_tip - hh),
                (xl, h_mm - hf_tip),
            ]

        ax.add_patch(Polygon(poly_pts, closed=True, facecolor="#F1F5F9", edgecolor="#0F172A", linewidth=1.4))
        # Right wet joint dashed strip (half of 400mm wet joint = 200mm)
        ax.add_patch(Rectangle((xr, h_mm - hf_tip), 0.5 * wet_mm, hf_tip, facecolor="none", edgecolor="#0284C7", linestyle="--", linewidth=1.0))
        if not is_exterior:
            ax.add_patch(Rectangle((xl - 0.5 * wet_mm, h_mm - hf_tip), 0.5 * wet_mm, hf_tip, facecolor="none", edgecolor="#0284C7", linestyle="--", linewidth=1.0))
        ax.axvline(0.0, ymin=0.06, ymax=0.92, color="#DC2626", linestyle="-.", linewidth=0.8)

        # Centroid line on mid-span standard panels
        if not is_end:
            ax.plot([xl * 0.82, xr * 0.82], [yb_mm, yb_mm], color="#0D9488", linestyle=":", linewidth=1.1)
            ax.text(xr * 0.92, yb_mm + 45.0, f"yb={yb_mm:.0f}", fontproperties=fp_note, color="#0F766E", ha="right")

        # Top total width dimension (including wet joint center-to-center)
        x_tot_l = xl if is_exterior else -0.5 * sp_mm
        x_tot_r = xr + 0.5 * wet_mm if is_exterior else 0.5 * sp_mm
        tot_w = x_tot_r - x_tot_l
        ax.annotate("", xy=(x_tot_r, h_mm + 165.0), xytext=(x_tot_l, h_mm + 165.0), arrowprops={"arrowstyle": "<->", "color": "#1E293B", "lw": 1.0})
        ax.text(0.5 * (x_tot_l + x_tot_r), h_mm + 195.0, f"计算宽 {tot_w:g} (预制 {xr - xl:g})", ha="center", va="bottom", fontproperties=fp_dim, color="#0F172A")

        # Girder height dimension on left
        x_dim_h = x_tot_l - 135.0
        ax.annotate("", xy=(x_dim_h, h_mm), xytext=(x_dim_h, 0.0), arrowprops={"arrowstyle": "<->", "color": "#1E293B", "lw": 1.0})
        ax.text(x_dim_h - 40.0, 0.5 * h_mm, f"{h_mm:g}", rotation=90, ha="right", va="center", fontproperties=fp_dim, color="#0F172A")

        # Flange tip thickness
        ax.text(xr + 0.5 * wet_mm + 35.0, h_mm - 0.5 * hf_tip, f"翼厚 {hf_tip:g}", ha="left", va="center", fontproperties=fp_note, color="#334155")

        # Web & bottom bulb dimensions
        ax.text(0.0, 0.52 * h_mm, f"腹板 {bw:g}", ha="center", va="center", fontproperties=fp_dim, color="#1E3A8A", bbox={"boxstyle": "round,pad=0.15", "fc": "#FFFFFF", "ec": "#93C5FD", "lw": 0.7})
        ax.annotate("", xy=(0.5 * b_bulb, -135.0), xytext=(-0.5 * b_bulb, -135.0), arrowprops={"arrowstyle": "<->", "color": "#1E293B", "lw": 1.0})
        ax.text(0.0, -165.0, f"{b_bulb:g}" + ("" if is_end else f" (马蹄高{h_bulb:g})"), ha="center", va="top", fontproperties=fp_dim, color="#0F172A")

        # 2% cross-slope note (placed cleanly over left flange away from x=0 axis)
        ax.text(-0.55 * abs(xl) if is_exterior else -0.38 * sp_mm, h_mm + 25.0, f"横坡 {ps['cross_slope_pct']:g}%", ha="center", fontproperties=fp_note, color="#475569")

        ax.set_xlim(-1380.0, 1520.0)
        ax.set_ylim(-380.0, h_mm + 460.0)
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(title_s, fontproperties=fp_sub, color="#0F172A", pad=4)

    paths = _save_fig(fig, out_dir, "fig_girder_detail", formats, dpi)
    return {
        "figure_id": "fig_girder_detail",
        "title_zh": "预制T梁边梁与中梁细部构造尺寸图",
        "caption_zh": "图 3-1 预制T梁外边梁与中梁构造尺寸图（单位：mm）",
        **paths,
    }


# ======================================================================
# Figure 1: Structural Calculation Model (1# T-Girder + 2# Pier Cap)
# ======================================================================
def _render_fig1_structural_model(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    ps = calc_report["project_summary"]
    lat = calc_report["lateral_distribution"]
    env = calc_report["superstructure_envelopes"]
    cap = calc_report["pier_cap_beam_1d"]
    sub = calc_report["substructure_checks"]

    span_l = float(ps["calc_span_m"])
    beam_l = float(ps["beam_length_m"])
    g_dead = float(env["dead_load_kn_m"])
    qk = float(env["lane_qk_kn_m"])
    pk = float(env["lane_pk_kn"])
    mu = float(env["impact_factor_mu"])
    mcq = float(lat["eccentric_compression"][0]["m_cq"])
    moq = float(lat["lever_rule"][0]["m_oq"])

    fig = Figure(figsize=(10.5, 6.1), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 11.0, "bold")
    fp_txt = _make_fp(font_path, 10.0)
    fp_tk = _make_fp(font_path, 9.5)

    # Top panel: 1# Simply Supported T-Girder 10-Element Model
    ax1 = fig.add_axes([0.05, 0.54, 0.90, 0.39])
    ax1.set_facecolor("#FFFFFF")
    for sp in ax1.spines.values():
        sp.set_visible(False)
    ax1.set_xticks([])
    ax1.set_yticks([])
    ax1.set_xlim(-2.3, span_l + 2.3)
    ax1.set_ylim(-2.65, 3.85)

    ax1.plot([0.0, span_l], [1.45, 1.45], color="#0284C7", linewidth=1.3)
    for k in range(21):
        xk = span_l * k / 20.0
        ax1.annotate("", xy=(xk, 0.30), xytext=(xk, 1.45), arrowprops={"arrowstyle": "->", "color": "#0284C7", "lw": 0.95})
    ax1.annotate("", xy=(0.5 * span_l, 0.32), xytext=(0.5 * span_l, 2.75), arrowprops={"arrowstyle": "-|>", "color": "#DC2626", "lw": 2.1})
    ax1.text(
        0.5 * span_l,
        2.85,
        f"公路-I级集中荷载 Pk = {pk:g} kN (剪力×1.2，1+μ = {1+mu:.3f})",
        ha="center",
        va="bottom",
        fontproperties=fp_txt,
        color="#DC2626",
    )
    ax1.text(
        0.15 * span_l,
        1.62,
        f"恒载 g = {g_dead:.2f} kN/m + 车道均布 qk = {qk:g} kN/m (mcq1={mcq:.3f}, moq1={moq:.3f})",
        ha="left",
        va="bottom",
        fontproperties=fp_txt,
        color="#0369A1",
    )

    end_ext = 0.5 * (beam_l - span_l)
    ax1.plot([-end_ext, span_l + end_ext], [0.0, 0.0], color="#0F172A", linewidth=3.2)
    node_names = ["Z1(0)", "B1", "B2", "BK", "4#", "KZ(L/2)", "6#", "KB", "B3", "B4", "Z2(L)"]
    for idx in range(11):
        xn = span_l * idx / 10.0
        ax1.plot([xn], [0.0], marker="o", markersize=5.6, color="#1D4ED8")
        lbl = node_names[idx] if idx in (0, 2, 5, 8, 10) else f"{idx}"
        ax1.text(xn, -0.35, lbl, ha="center", va="top", fontproperties=fp_tk, color="#0F172A")
        if idx < 10:
            ax1.text(xn + 0.05 * span_l, 0.12, f"E{idx+1}", ha="center", va="bottom", fontproperties=fp_tk, color="#475569")

    ax1.add_patch(Polygon([(0.0, -0.05), (-0.45, -0.85), (0.45, -0.85)], closed=True, facecolor="#E2E8F0", edgecolor="#0F172A", linewidth=1.25))
    ax1.plot([-0.65, 0.65], [-0.90, -0.90], color="#0F172A", linewidth=1.4)
    ax1.text(0.0, -1.16, "固定铰支座\n(约束 Dx, Dy, Dz)", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    ax1.add_patch(Polygon([(span_l, -0.05), (span_l - 0.45, -0.75), (span_l + 0.45, -0.75)], closed=True, facecolor="#E2E8F0", edgecolor="#0F172A", linewidth=1.25))
    ax1.plot([span_l - 0.65, span_l + 0.65], [-0.95, -0.95], color="#0F172A", linewidth=1.4)
    ax1.plot([span_l - 0.22, span_l + 0.22], [-0.85, -0.85], marker="o", linestyle="None", markersize=4.2, color="#0F172A")
    ax1.text(span_l, -1.16, "纵向滑动铰支座\n(约束 Dy, Dz；释放 Dx)", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    ax1.annotate("", xy=(span_l, -2.15), xytext=(0.0, -2.15), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.15})
    ax1.text(
        0.5 * span_l,
        -2.25,
        f"计算跨径 L = {span_l:g} m (预制梁全长 Lbeam = {beam_l:g} m，10单元划分)",
        ha="center",
        va="top",
        fontproperties=fp_txt,
        color="#0F172A",
    )
    ax1.set_title("(a) 1#代表性预制T梁计算简图（简支静定体系，C50混凝土）", fontproperties=fp_sub, color="#0F172A", loc="left")

    # Bottom panel: 2# Intermediate Double-Column Pier Overhanging Cap Beam 1D Model
    ax2 = fig.add_axes([0.05, 0.06, 0.90, 0.41])
    ax2.set_facecolor("#FFFFFF")
    for sp in ax2.spines.values():
        sp.set_visible(False)
    ax2.set_xticks([])
    ax2.set_yticks([])

    cap_l = float(cap["cap_length_m"])
    col_xs = [float(x) for x in cap["column_positions_m"]]
    g_xs = [float(x) for x in cap["girder_positions_m"]]
    g_rs = [float(x) for x in cap["girder_reactions_kn"]]
    col_rs = [float(r["reaction_kn"]) for r in cap["support_reactions"]]
    col_h = float(sub["pier_column"]["height_m"])

    ax2.set_xlim(-1.2, cap_l + 1.2)
    ax2.set_ylim(-3.65, 3.05)
    ax2.add_patch(Rectangle((0.0, -0.35), cap_l, 0.35, facecolor="#DBEAFE", edgecolor="#1E40AF", linewidth=1.45))

    for idx_g, (gx, gr) in enumerate(zip(g_xs, g_rs), start=1):
        ax2.annotate("", xy=(gx, 0.02), xytext=(gx, 1.82), arrowprops={"arrowstyle": "-|>", "color": "#B91C1C", "lw": 1.7})
        ax2.text(gx, 1.92, f"{idx_g}#支点\nRu={gr:.1f} kN", ha="center", va="bottom", fontproperties=fp_tk, color="#991B1B")

    for idx_c, (cx, cr) in enumerate(zip(col_xs, col_rs), start=1):
        ax2.add_patch(Rectangle((cx - 0.25, -2.30), 0.50, 1.95, facecolor="#E2E8F0", edgecolor="#334155", linewidth=1.25))
        ax2.plot([cx - 0.55, cx + 0.55], [-2.30, -2.30], color="#0F172A", linewidth=2.0)
        ax2.text(cx, -2.50, f"{idx_c}#墩柱 (Dc={sub['pier_column']['diameter_m']:g}m, H={col_h:g}m)\n柱顶反力 Rcol={cr:.1f} kN", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    ax2.annotate("", xy=(col_xs[-1], -1.22), xytext=(col_xs[0], -1.22), arrowprops={"arrowstyle": "<->", "color": "#1E40AF", "lw": 1.1})
    ax2.text(0.5 * (col_xs[0] + col_xs[-1]), -1.12, f"双柱中心距 {col_xs[-1]-col_xs[0]:.2f} m", ha="center", va="bottom", fontproperties=fp_tk, color="#1E40AF")
    ax2.annotate("", xy=(cap_l, -3.28), xytext=(0.0, -3.28), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.1})
    ax2.text(
        0.5 * cap_l,
        -3.35,
        f"盖梁总长 Lcap = {cap_l:g} m (截面 {sub['cap_beam']['width_m']*1000:g}×{sub['cap_beam']['height_m']*1000:g} mm，双跨总反力 ∑Ru = {sum(g_rs):.1f} kN)",
        ha="center",
        va="top",
        fontproperties=fp_txt,
        color="#0F172A",
    )
    ax2.set_title("(b) 2#中间双柱式桥墩盖梁连续双悬臂受荷计算简图（C30混凝土，基本组合）", fontproperties=fp_sub, color="#0F172A", loc="left")

    paths = _save_fig(fig, out_dir, "fig1_structural_model", formats, dpi)
    return {
        "figure_id": "fig1_structural_model",
        "title_zh": "结构计算简图（主梁与中间双柱墩）",
        "caption_zh": "图 4-1 代表性简支T梁及双柱墩计算简图（单位：m, kN）",
        **paths,
    }


# ======================================================================
# Figure 2: Bending Moment & Shear Force Envelopes (Enlarged Legends/Text)
# ======================================================================
def _render_fig2_moment_shear_envelope(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    env = calc_report["superstructure_envelopes"]
    gc = calc_report["girder_checks"]
    stations = env["stations"]
    span_l = float(env["calc_span_m"])

    xs = [float(s["x_m"]) for s in stations]
    m_dead = [float(s["m_dead_knm"]) for s in stations]
    m_live = [float(s["m_live_knm"]) for s in stations]
    m_sls = [float(s["m_sls_char_knm"]) for s in stations]
    m_uls = [float(s["m_uls_knm"]) for s in stations]
    mu_cap = float(gc["uls_flexure"]["capacity_mu_knm"])

    gamma0 = round(m_uls[5] / max(1.0, 1.2 * m_dead[5] + 1.4 * m_live[5]), 2)
    q_uls_pos = [round(gamma0 * (1.2 * float(s["q_dead_kn"]) + 1.4 * float(s["q_live_pos_kn"])), 2) for s in stations]
    q_uls_neg = [round(gamma0 * (1.2 * float(s["q_dead_kn"]) + 1.4 * float(s["q_live_neg_kn"])), 2) for s in stations]
    q_sls = [float(s["q_dead_kn"]) + float(s["q_live_pos_kn"]) if i <= 5 else float(s["q_dead_kn"]) + float(s["q_live_neg_kn"]) for i, s in enumerate(stations)]
    vu_cap = float(gc["uls_shear"]["capacity_vu_kn"])

    fig = Figure(figsize=(10.5, 6.2), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 11.2, "bold")
    fp_lbl = _make_fp(font_path, 10.5)
    fp_leg = _make_fp(font_path, 10.0)
    fp_tk = _make_fp(font_path, 9.6)

    ax_m = fig.add_axes([0.10, 0.56, 0.87, 0.37])
    _style_axes(ax_m, fp_tk)
    ax_m.plot(xs, m_dead, "--", color="#64748B", linewidth=1.6, label="恒载 Mg")
    ax_m.plot(xs, m_live, "-.", color="#0284C7", linewidth=1.6, label="活载 Mq (含冲击1+μ)")
    ax_m.plot(xs, m_sls, "-", color="#0D9488", linewidth=2.0, label="标准组合 Ms = Mg + Mq")
    ax_m.plot(xs, m_uls, "-o", color="#DC2626", linewidth=2.4, markersize=5.0, label=f"基本组合 Md = {gamma0:g}(1.2Mg+1.4Mq)")
    ax_m.axhline(mu_cap, color="#1E3A8A", linestyle=":", linewidth=2.0, label=f"正截面抗弯抗力 Mu = {mu_cap:.1f} kN·m")
    ax_m.fill_between(xs, 0.0, m_uls, color="#FEE2E2", alpha=0.35)

    md_max = m_uls[5]
    ax_m.annotate(
        f"跨中控制截面 KZ(x={0.5*span_l:g}m): Md = {md_max:.1f} kN·m ≤ Mu = {mu_cap:.1f} kN·m\n四分点截面 (x={0.25*span_l:g}m): Md = {env['quarter_span_m_uls_knm']:.1f} kN·m",
        xy=(0.5 * span_l, md_max),
        xytext=(0.46 * span_l, md_max * 0.48),
        ha="center",
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.32", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.95},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.25},
    )
    ax_m.set_xlim(0.0, span_l)
    ax_m.set_ylim(0.0, max(mu_cap, md_max) * 1.22)
    ax_m.set_ylabel("弯矩 M (kN·m)", fontproperties=fp_lbl, color="#0F172A")
    ax_m.set_title(f"(a) 1#代表性T梁正截面弯矩包络图 (L = {span_l:g} m)", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_m.legend(prop=fp_leg, loc="upper right", ncol=2, framealpha=0.94)

    ax_v = fig.add_axes([0.10, 0.09, 0.87, 0.38])
    _style_axes(ax_v, fp_tk)
    ax_v.axhline(0.0, color="#334155", linewidth=1.0)
    ax_v.plot(xs, q_uls_pos, "-s", color="#DC2626", linewidth=2.2, markersize=4.5, label="基本组合最大正剪力 Vd+")
    ax_v.plot(xs, q_uls_neg, "-^", color="#2563EB", linewidth=2.2, markersize=4.5, label="基本组合最大负剪力 Vd-")
    ax_v.plot(xs, q_sls, "--", color="#0D9488", linewidth=1.6, label="正常使用标准组合剪力 Vs")
    ax_v.axhline(vu_cap, color="#15803D", linestyle=":", linewidth=1.8, label=f"斜截面抗剪抗力 ±Vu = {vu_cap:.1f} kN")
    ax_v.axhline(-vu_cap, color="#15803D", linestyle=":", linewidth=1.8)
    ax_v.fill_between(xs, q_uls_neg, q_uls_pos, color="#DBEAFE", alpha=0.28)

    vd_sup = abs(float(gc["uls_shear"]["demand_vd_kn"]))
    ax_v.annotate(
        f"支点控制截面 Z1(x=0): Vd = {vd_sup:.1f} kN ≤ Vu = {vu_cap:.1f} kN",
        xy=(0.0, q_uls_pos[0]),
        xytext=(0.15 * span_l, q_uls_pos[0] * 1.15),
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.30", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.95},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.15},
    )
    ax_v.set_xlim(0.0, span_l)
    v_lim = max(vu_cap, vd_sup) * 1.25
    ax_v.set_ylim(-v_lim, v_lim)
    ax_v.set_xlabel("顺桥向距左支点距离 x (m)", fontproperties=fp_lbl, color="#0F172A")
    ax_v.set_ylabel("剪力 V (kN)", fontproperties=fp_lbl, color="#0F172A")
    ax_v.set_title("(b) 1#代表性T梁斜截面剪力正负包络图", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_v.legend(prop=fp_leg, loc="upper right", ncol=2, framealpha=0.94)

    paths = _save_fig(fig, out_dir, "fig2_moment_shear_envelope", formats, dpi)
    return {
        "figure_id": "fig2_moment_shear_envelope",
        "title_zh": "主梁弯矩与剪力组合包络图",
        "caption_zh": "图 4-2 1#代表性T梁弯矩与剪力包络图（单位：kN·m, kN）",
        **paths,
    }


# ======================================================================
# Figure 3: Prestressing Tendon Profile & Effective Prestress Curve
# ======================================================================
def _render_fig3_tendon_and_prestress(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    ps = calc_report["project_summary"]
    pre = calc_report["prestress"]
    sec = calc_report["t_girder_section"]

    beam_l = float(ps["beam_length_m"])
    h_mm = float(sec["mid_span_mm"]["height"])
    n_tendons = int(pre["recommended_tendons"])
    strands_per = int(pre["strands_per_tendon"])
    sigma_con = float(pre["sigma_con_mpa"])
    lf_m = float(pre["anchorage_influence_length_m"])
    loss_rows = pre["loss_profile"]

    fig = Figure(figsize=(10.5, 6.2), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 11.2, "bold")
    fp_lbl = _make_fp(font_path, 10.5)
    fp_leg = _make_fp(font_path, 9.8)
    fp_tag = _make_fp(font_path, 9.8, "bold")
    fp_tk = _make_fp(font_path, 9.6)

    ax_t = fig.add_axes([0.10, 0.56, 0.87, 0.37])
    _style_axes(ax_t, fp_tk)
    ax_t.add_patch(Rectangle((0.0, 0.0), beam_l, h_mm, facecolor="#F8FAFC", edgecolor="#334155", linewidth=1.35))
    ax_t.axhline(float(sec["mid_span_mm"]["y_bot"]), color="#64748B", linestyle="--", linewidth=1.0, label=f"毛截面形心 yb = {sec['mid_span_mm']['y_bot']:.0f} mm")

    avail_end = h_mm - 420.0
    end_step = 300.0 if n_tendons <= 1 or (n_tendons - 1) * 300.0 <= avail_end else math.floor((avail_end / (n_tendons - 1)) / 10.0) * 10.0
    palette = ["#DC2626", "#2563EB", "#0D9488", "#D97706", "#7C3AED", "#DB2777", "#059669", "#4F46E5"]
    sample_xs = [beam_l * k / 60.0 for k in range(61)]
    half_b = 0.5 * beam_l

    for idx in range(n_tendons):
        ey = 240.0 + idx * end_step if n_tendons > 1 else 0.35 * h_mm
        my = 100.0 if idx < 3 else 100.0 + (idx - 2) * 115.0
        ys_c = [my + (ey - my) * (((x - half_b) / half_b) ** 2) for x in sample_xs]
        col = palette[idx % len(palette)]
        ax_t.plot(sample_xs, ys_c, color=col, linewidth=2.1, label=f"N{idx+1} (端{ey:g}→中{my:g} mm)")
        # Direct enlarged N-tag near left beam end!
        ax_t.text(-0.22, ey, f"N{idx+1}", ha="right", va="center", fontproperties=fp_tag, color=col)

    ax_t.set_xlim(-1.45, beam_l + 0.5)
    ax_t.set_ylim(-90.0, h_mm + 160.0)
    ax_t.set_ylabel("距梁底高度 y (mm)", fontproperties=fp_lbl, color="#0F172A")
    ax_t.set_title(f"(a) 预制T梁预应力钢束抛物线线形图（{pre['tendon_designation']}，梁高 {h_mm:g} mm）", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_t.legend(prop=fp_leg, loc="upper right", ncol=2, framealpha=0.94)

    ax_s = fig.add_axes([0.10, 0.09, 0.87, 0.38])
    _style_axes(ax_s, fp_tk)
    lx = [float(r["x_m"]) for r in loss_rows]
    s_after_f = [sigma_con - float(r["sigma_l1_friction_mpa"]) for r in loss_rows]
    s_after_a = [sigma_con - float(r["sigma_l1_friction_mpa"]) - float(r["sigma_l2_anchorage_mpa"]) for r in loss_rows]
    s_pe = [float(r["effective_prestress_mpa"]) for r in loss_rows]

    ax_s.axhline(sigma_con, color="#475569", linestyle=":", linewidth=1.6, label=f"张拉控制应力 σcon = {sigma_con:g} MPa")
    ax_s.plot(lx, s_after_f, "--o", color="#0284C7", linewidth=1.7, markersize=4.2, label="扣摩阻损失后 (σcon - σl1)")
    ax_s.plot(lx, s_after_a, "-.s", color="#D97706", linewidth=1.8, markersize=4.2, label=f"扣锚固回缩后 (lf = {lf_m:.2f} m)")
    ax_s.plot(lx, s_pe, "-D", color="#DC2626", linewidth=2.5, markersize=5.0, label="运营有效预应力 σpe")
    ax_s.fill_between(lx, min(s_pe) * 0.94, s_pe, color="#FEE2E2", alpha=0.28)

    pe_end = s_pe[0]
    pe_mid = s_pe[len(s_pe) // 2]
    ax_s.annotate(
        f"跨中 σpe(L/2) = {pe_mid:.1f} MPa | 梁端 σpe(0) = {pe_end:.1f} MPa",
        xy=(lx[len(lx) // 2], pe_mid),
        xytext=(0.5 * beam_l, pe_mid - 64.0),
        ha="center",
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.30", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.95},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.15},
    )
    ax_s.set_xlim(-0.5, beam_l + 0.5)
    ax_s.set_ylim(min(s_pe) - 110.0, sigma_con + 55.0)
    ax_s.set_xlabel("沿预制梁长坐标 x (m)", fontproperties=fp_lbl, color="#0F172A")
    ax_s.set_ylabel("钢束应力 (MPa)", fontproperties=fp_lbl, color="#0F172A")
    ax_s.set_title("(b) 沿梁长分阶段预应力损失及有效预应力 σpe 曲线", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_s.legend(prop=fp_leg, loc="upper right", ncol=2, framealpha=0.94)

    paths = _save_fig(fig, out_dir, "fig3_tendon_and_prestress", formats, dpi)
    return {
        "figure_id": "fig3_tendon_and_prestress",
        "title_zh": "预应力钢束线形及有效预应力分布图",
        "caption_zh": "图 5-1 预应力钢束线形及有效预应力分布图（单位：mm, MPa）",
        **paths,
    }


# ======================================================================
# Figure 4: Pier & Abutment Bored Pile Soil Strata & Pile-Tip Position
# ======================================================================
def _render_fig4_pile_soil_profile(
    calc_report: Mapping[str, Any],
    out_dir: Path,
    font_path: Path | None,
    formats: Sequence[str],
    dpi: int,
) -> dict[str, Any]:
    sub_bp = calc_report["substructure_checks"]["bored_pile"]
    ab_bp = calc_report["abutment_checks"]["bored_pile"]
    unresolved_fields = {str(u.get("field")) for u in (calc_report.get("unresolved_assumptions") or [])}
    is_prelim_soil = "soil_layers" in unresolved_fields
    soil_tag_zh = "初拟地层" if is_prelim_soil else "实测钻孔地层"

    fig = Figure(figsize=(10.5, 6.2), dpi=dpi, facecolor="#FFFFFF")
    fp_sub = _make_fp(font_path, 11.0, "bold")
    fp_lbl = _make_fp(font_path, 10.2)
    fp_txt = _make_fp(font_path, 9.5)
    fp_tk = _make_fp(font_path, 9.4)

    soil_colors = ["#FEF3C7", "#FDE68A", "#E0F2FE", "#BBF7D0", "#DDD6FE"]
    panels = [
        (
            fig.add_axes([0.07, 0.08, 0.40, 0.84]),
            f"(a) 中间双柱墩钻孔桩 (ZJ1/ZJ2, L={sub_bp['design_length_m']:g}m)",
            sub_bp,
            "中间墩系梁底",
        ),
        (
            fig.add_axes([0.55, 0.08, 0.40, 0.84]),
            f"(b) 桩柱式桥台钻孔桩 (QT3/QT4, L={ab_bp['design_length_m']:g}m)",
            ab_bp,
            "桥台台帽底",
        ),
    ]

    max_depth = max(float(sub_bp["design_length_m"]), float(ab_bp["design_length_m"])) + 4.8

    for ax, sub_title, bp_data, cap_label in panels:
        _style_axes(ax, fp_tk)
        pile_l = float(bp_data["design_length_m"])
        pile_d = float(bp_data["diameter_m"])
        layers = list(bp_data.get("layer_contributions") or [])

        cur_z = 0.0
        for idx_l, lyr in enumerate(layers):
            trav = float(lyr["traversed_m"])
            draw_thk = trav + (3.2 if idx_l == len(layers) - 1 else 0.0)
            col = soil_colors[idx_l % len(soil_colors)]
            ax.add_patch(
                Rectangle((-3.2, -(cur_z + draw_thk)), 6.4, draw_thk, facecolor=col, edgecolor="#64748B", linewidth=0.85, alpha=0.68)
            )
            mid_z = -(cur_z + 0.5 * trav)
            lyr_label = (
                f"{lyr['name']} (li={trav:.1f}m | qik={lyr['qik_kpa']:g}kPa)"
                if trav < 1.8
                else f"{lyr['name']}\nli={trav:.2f} m | qik={lyr['qik_kpa']:g} kPa | fa0={lyr['fa0_kpa']:g} kPa"
            )
            ax.text(
                0.95,
                mid_z,
                lyr_label,
                ha="left",
                va="center",
                fontproperties=fp_txt,
                color="#0F172A",
            )
            cur_z += trav

        ax.add_patch(Rectangle((-1.55, 0.0), 3.10, 1.30, facecolor="#CBD5E1", edgecolor="#334155", linewidth=1.25))
        ax.text(0.0, 0.65, cap_label, ha="center", va="center", fontproperties=fp_txt, color="#0F172A")
        ax.add_patch(Rectangle((-0.5 * pile_d, -pile_l), pile_d, pile_l, facecolor="#93C5FD", edgecolor="#1E3A8A", linewidth=1.7))
        ax.axhline(-pile_l, color="#DC2626", linestyle="--", linewidth=1.25)
        ax.text(
            -0.80,
            -pile_l - 0.35,
            f"桩端 z = -{pile_l:g} m (D={pile_d:g} m)",
            ha="left",
            va="top",
            fontproperties=fp_txt,
            color="#B91C1C",
        )

        ax.annotate("", xy=(-2.30, -pile_l), xytext=(-2.30, 0.0), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.25})
        ax.text(-2.45, -0.5 * pile_l, f"桩长 L = {pile_l:g} m", rotation=90, ha="right", va="center", fontproperties=fp_lbl, color="#0F172A")

        r_dem = float(bp_data["pile_top_sls_demand_kn"])
        ra_cap = float(bp_data["allowable_ra_kn"])
        min_sock = float(bp_data.get("min_required_socket_m", round(2.5 * pile_d, 2)))
        sock_txt = (
            f"\n入持力层 {bp_data['bearing_stratum_socket_m']:.2f} m ≥ {min_sock:g} m ({soil_tag_zh})"
            if "bearing_stratum_socket_m" in bp_data
            else ""
        )
        summary_box = FancyBboxPatch(
            (-2.90, -max_depth + 0.32),
            8.10,
            2.00,
            boxstyle="round,pad=0.12",
            facecolor="#F0FDF4" if r_dem <= ra_cap else "#FEF2F2",
            edgecolor="#16A34A" if r_dem <= ra_cap else "#DC2626",
            linewidth=1.05,
        )
        ax.add_patch(summary_box)
        ax.text(
            1.15,
            -max_depth + 1.32,
            f"Rsls = {r_dem:.1f} kN ≤ [Ra] = {ra_cap:.1f} kN{sock_txt}",
            ha="center",
            va="center",
            fontproperties=fp_txt,
            color="#14532D" if r_dem <= ra_cap else "#991B1B",
        )

        ax.set_xlim(-3.2, 5.5)
        ax.set_ylim(-max_depth, 2.1)
        ax.set_xticks([])
        ax.set_ylabel("自承台/系梁底算起深度 z (m)", fontproperties=fp_lbl, color="#0F172A")
        ax.set_title(sub_title, fontproperties=fp_sub, color="#0F172A", loc="left")

    paths = _save_fig(fig, out_dir, "fig4_pile_soil_profile", formats, dpi)
    return {
        "figure_id": "fig4_pile_soil_profile",
        "title_zh": f"桥墩与桥台桩基土层及桩端位置对比图（{soil_tag_zh}）",
        "caption_zh": f"图 8-1 桥墩与桥台桩基土层及桩端对比图（{soil_tag_zh}）",
        **paths,
    }


def generate_bridge_calculation_figures(
    calc_report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    formats: Sequence[str] = ("png", "svg"),
    dpi: int = 300,
    include_scheme_figure: bool = True,
    include_girder_detail_figure: bool = True,
) -> dict[str, Any]:
    """Generate Word/PPT-ready bridge engineering calculation figures from live calculation data."""
    out_p = Path(output_dir).expanduser().resolve()
    out_p.mkdir(parents=True, exist_ok=True)
    font_path = _resolve_cjk_font_path()

    figures: list[dict[str, Any]] = []
    if include_scheme_figure:
        figures.append(_render_fig0_scheme_comparison(calc_report, out_p, font_path, formats, dpi))
    if include_girder_detail_figure:
        figures.append(_render_fig_girder_detail(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig1_structural_model(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig2_moment_shear_envelope(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig3_tendon_and_prestress(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig4_pile_soil_profile(calc_report, out_p, font_path, formats, dpi))

    by_id = {f["figure_id"]: f for f in figures}
    return {
        "output_dir": str(out_p),
        "dpi": dpi,
        "figure_count": len(figures),
        "figures": figures,
        "by_id": by_id,
    }
