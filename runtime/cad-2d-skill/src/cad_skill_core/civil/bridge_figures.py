"""Publication-Grade Word & Defense-PPT Calculation Figures Generator for Bridge Engineering.

Generates high-DPI PNG (300 DPI) + vector SVG figures directly from live `calc_report` data
without mutating global Matplotlib state or pyplot backends:
1. `fig0_scheme_comparison`: Three Opening-Report Bridge Schemes (6 panels: 3 elevations + 3 cross-sections);
2. `fig1_structural_model`: Structural Calculation Model Diagram (1# Simply Supported T-Girder + 2# Double-Column Pier);
3. `fig2_moment_shear_envelope`: Bending Moment & Shear Force Envelopes (with load cases, units & governing values);
4. `fig3_tendon_and_prestress`: Prestressing Tendon Parabolic Profile & Effective Prestress Distribution along Span;
5. `fig4_pile_soil_profile`: Pier & Abutment Bored Pile Soil Strata & Bearing Socket Profile Comparison.
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


def _make_fp(font_path: Path | None, size: float = 10.0, weight: str = "normal") -> FontProperties:
    if font_path is not None:
        return FontProperties(fname=str(font_path), size=size, weight=weight)
    return FontProperties(size=size, weight=weight)


def _style_axes(ax: Any, fp_tick: FontProperties) -> None:
    ax.set_facecolor("#FFFFFF")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#334155")
        ax.spines[spine].set_linewidth(0.9)
    ax.grid(True, linestyle="--", linewidth=0.5, color="#CBD5E1", alpha=0.75)
    for lbl in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lbl.set_fontproperties(fp_tick)
        lbl.set_color("#1E293B")


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

    # Copy any user-supplied external opening-report images if present
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

    fig = Figure(figsize=(11.6, 8.2), dpi=dpi, facecolor="#FFFFFF")
    fp_h1 = _make_fp(font_path, 13.0, "bold")
    fp_sub = _make_fp(font_path, 9.5, "bold")
    fp_txt = _make_fp(font_path, 8.3)
    fp_tk = _make_fp(font_path, 7.8)

    fig.text(
        0.5,
        0.965,
        f"{ps['project_name']} — 开题报告三桥型方案立面与标准横断面比选图",
        ha="center",
        va="top",
        fontproperties=fp_h1,
        color="#0F172A",
    )

    for row_i, sc in enumerate((schemes + schemes[:1] * 3)[:3]):
        y_top = 0.90 - row_i * 0.295
        ax_el = fig.add_axes([0.055, y_top - 0.225, 0.56, 0.215])
        ax_sc = fig.add_axes([0.655, y_top - 0.225, 0.30, 0.215])
        for ax_c in (ax_el, ax_sc):
            ax_c.set_facecolor("#FFFFFF")
            for sp in ax_c.spines.values():
                sp.set_color("#CBD5E1")
                sp.set_linewidth(0.8)
            ax_c.set_xticks([])
            ax_c.set_yticks([])

        gh = float(sc.get("girder_height_m", 1.5))
        sname = f"{sc.get('scheme_no', '')}：{sc.get('name', '')}"
        tag_color = "#0F766E" if sc.get("is_recommended") else "#334155"

        # Left subplot: Bridge Elevation (4x25m)
        ax_el.set_xlim(-8.0, total_len + 8.0)
        ax_el.set_ylim(-9.5, 4.8)
        # Riverbed + water line
        bed_x = [-6.0, 4.0, 22.0, 0.5 * total_len, total_len - 22.0, total_len - 4.0, total_len + 6.0]
        bed_y = [0.0, -0.6, -4.6, -5.4, -4.6, -0.6, 0.0]
        ax_el.plot(bed_x, bed_y, color="#78716C", linewidth=1.2)
        ax_el.plot([3.5, total_len - 3.5], [-2.2, -2.2], color="#0284C7", linestyle="--", linewidth=0.9)
        ax_el.text(total_len - 4.5, -1.95, "设计洪水位", fontproperties=fp_tk, color="#0284C7", ha="right")

        # Superstructure girder band
        if "连续箱梁" in sc.get("name", ""):
            ax_el.add_patch(Rectangle((0.0, 0.0), total_len, gh, facecolor="#DBEAFE", edgecolor="#1D4ED8", linewidth=1.2))
        else:
            for k in range(n_spans):
                x0_s = k * std_span + 0.08
                ax_el.add_patch(
                    Rectangle((x0_s, 0.0), std_span - 0.16, gh, facecolor="#E0F2FE" if row_i == 0 else "#F1F5F9", edgecolor=tag_color, linewidth=1.15)
                )
        # Piers & Abutments
        for k in range(n_spans + 1):
            xp = k * std_span
            if k in (0, n_spans):
                ax_el.add_patch(Rectangle((xp - 0.9, -2.0), 1.8, 2.0 + gh, facecolor="#E2E8F0", edgecolor="#475569", linewidth=0.9))
                ax_el.plot([xp, xp], [-2.0, -7.2], color="#475569", linewidth=1.6)
            else:
                ax_el.add_patch(Rectangle((xp - 0.55, -5.0), 1.1, 5.0, facecolor="#CBD5E1", edgecolor="#334155", linewidth=0.95))
                ax_el.plot([xp, xp], [-5.0, -8.5], color="#334155", linewidth=1.6)
            if k < n_spans:
                xm = (k + 0.5) * std_span
                ax_el.annotate(
                    "",
                    xy=(xp + std_span, gh + 1.1),
                    xytext=(xp, gh + 1.1),
                    arrowprops={"arrowstyle": "<->", "color": "#475569", "lw": 0.8},
                )
                ax_el.text(xm, gh + 1.35, f"{std_span:g} m", ha="center", va="bottom", fontproperties=fp_tk, color="#1E293B")

        ax_el.set_title(f"({2*row_i+1}) {sname} — 立面图 (总长 {total_len:g} m，梁高 {gh:.2f} m)", fontproperties=fp_sub, color=tag_color, pad=5)
        mech_raw = str(sc.get("structural_mechanics", ""))
        if len(mech_raw) > 40:
            cut_pos = max(mech_raw.rfind("；", 0, 42), mech_raw.rfind("，", 0, 42))
            mech_note = mech_raw[:cut_pos] if cut_pos >= 16 else mech_raw[:40]
        else:
            mech_note = mech_raw
        ax_el.text(0.5 * total_len, -8.9, mech_note, ha="center", fontproperties=fp_txt, color="#475569")

        # Right subplot: Cross-Section
        ax_sc.set_xlim(-0.58 * deck_w, 0.58 * deck_w)
        ax_sc.set_ylim(-gh - 0.9, 1.35)
        # Deck slab + crash barriers
        ax_sc.add_patch(Rectangle((-0.5 * deck_w, 0.0), deck_w, 0.18, facecolor="#94A3B8", edgecolor="#334155", linewidth=0.9))
        ax_sc.add_patch(Rectangle((-0.5 * deck_w, 0.18), 0.50, 0.75, facecolor="#CBD5E1", edgecolor="#334155", linewidth=0.8))
        ax_sc.add_patch(Rectangle((0.5 * deck_w - 0.50, 0.18), 0.50, 0.75, facecolor="#CBD5E1", edgecolor="#334155", linewidth=0.8))

        if row_i == 0 or "T梁" in sc.get("name", ""):
            ng_s = 5
            sp_s = (deck_w - 1.2) / max(1, ng_s - 1)
            for gi in range(ng_s):
                gx = (gi - 0.5 * (ng_s - 1)) * sp_s
                ax_sc.add_patch(Rectangle((gx - 0.12, -gh), 0.24, gh, facecolor="#BAE6FD", edgecolor="#0369A1", linewidth=0.95))
                ax_sc.add_patch(Rectangle((gx - 0.28, -gh), 0.56, 0.22, facecolor="#BAE6FD", edgecolor="#0369A1", linewidth=0.85))
            sec_note = f"5片预制T梁，梁高 {gh:.2f} m"
        elif "连续箱梁" in sc.get("name", ""):
            bw_top = deck_w - 1.6
            bw_bot = deck_w * 0.52
            trap = Polygon(
                [(-0.5 * bw_top, 0.0), (0.5 * bw_top, 0.0), (0.5 * bw_bot, -gh), (-0.5 * bw_bot, -gh)],
                closed=True,
                facecolor="#DBEAFE",
                edgecolor="#1D4ED8",
                linewidth=1.1,
            )
            ax_sc.add_patch(trap)
            for cell_x in (-0.21 * bw_bot, 0.21 * bw_bot):
                ax_sc.add_patch(Rectangle((cell_x - 0.16 * bw_bot, -gh + 0.22), 0.32 * bw_bot, gh - 0.44, facecolor="#FFFFFF", edgecolor="#1D4ED8", linewidth=0.85))
            sec_note = f"单箱双室连续箱梁，梁高 {gh:.2f} m"
        else:
            nb = 3 if deck_w <= 10.5 else 4
            sp_b = (deck_w - 1.4) / max(1, nb - 1)
            for bi in range(nb):
                bx = (bi - 0.5 * (nb - 1)) * sp_b
                ax_sc.add_patch(Rectangle((bx - 0.58, -gh), 1.16, gh, facecolor="#E2E8F0", edgecolor="#334155", linewidth=0.95))
                ax_sc.add_patch(Rectangle((bx - 0.36, -gh + 0.20), 0.72, gh - 0.40, facecolor="#FFFFFF", edgecolor="#475569", linewidth=0.8))
            sec_note = f"{nb}片预制简支小箱梁，梁高 {gh:.2f} m"

        ax_sc.annotate(
            "",
            xy=(0.5 * deck_w, 1.02),
            xytext=(-0.5 * deck_w, 1.02),
            arrowprops={"arrowstyle": "<->", "color": "#334155", "lw": 0.85},
        )
        ax_sc.text(0.0, 1.08, f"桥面宽 {deck_w:g} m", ha="center", va="bottom", fontproperties=fp_tk, color="#0F172A")
        ax_sc.set_title(f"({2*row_i+2}) 标准横断面图 ({sc.get('verdict', '')})", fontproperties=fp_sub, color=tag_color, pad=5)
        ax_sc.text(0.0, -gh - 0.55, sec_note, ha="center", fontproperties=fp_txt, color="#334155")

    paths = _save_fig(fig, out_dir, "fig0_scheme_comparison", formats, dpi)
    return {
        "figure_id": "fig0_scheme_comparison",
        "title_zh": "三桥型方案立面与标准横断面比选图（6幅）",
        "caption_zh": f"图2-1 {ps['project_name']}三个比选桥型方案立面及横断面对比图（单位：m）",
        "reused_opening_report_images": reused_images,
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

    fig = Figure(figsize=(10.8, 6.8), dpi=dpi, facecolor="#FFFFFF")
    fp_h1 = _make_fp(font_path, 12.5, "bold")
    fp_sub = _make_fp(font_path, 10.2, "bold")
    fp_txt = _make_fp(font_path, 8.8)
    fp_tk = _make_fp(font_path, 8.2)

    fig.text(
        0.5,
        0.96,
        "结构计算简图（1#代表性简支T梁杆系模型与2#中间双柱墩盖梁受荷模型）",
        ha="center",
        va="top",
        fontproperties=fp_h1,
        color="#0F172A",
    )

    # Top panel: 1# Simply Supported T-Girder 10-Element Model
    ax1 = fig.add_axes([0.07, 0.55, 0.87, 0.34])
    ax1.set_facecolor("#FFFFFF")
    for sp in ax1.spines.values():
        sp.set_visible(False)
    ax1.set_xticks([])
    ax1.set_yticks([])
    ax1.set_xlim(-2.2, span_l + 2.2)
    ax1.set_ylim(-2.6, 3.8)

    # Uniform dead + lane load arrows on [0, L]
    ax1.plot([0.0, span_l], [1.45, 1.45], color="#0284C7", linewidth=1.2)
    for k in range(21):
        xk = span_l * k / 20.0
        ax1.annotate("", xy=(xk, 0.30), xytext=(xk, 1.45), arrowprops={"arrowstyle": "->", "color": "#0284C7", "lw": 0.85})
    # Concentrated lane load Pk at mid-span KZ
    ax1.annotate("", xy=(0.5 * span_l, 0.32), xytext=(0.5 * span_l, 2.75), arrowprops={"arrowstyle": "-|>", "color": "#DC2626", "lw": 2.0})
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
        0.18 * span_l,
        1.62,
        f"恒载 g = {g_dead:.2f} kN/m + 车道均布 qk = {qk:g} kN/m (mcq1={mcq:.3f}, moq1={moq:.3f})",
        ha="left",
        va="bottom",
        fontproperties=fp_txt,
        color="#0369A1",
    )

    # Beam axis + 10 elements (11 nodes)
    end_ext = 0.5 * (beam_l - span_l)
    ax1.plot([-end_ext, span_l + end_ext], [0.0, 0.0], color="#0F172A", linewidth=3.0)
    node_names = ["Z1(0)", "B1", "B2", "BK", "4#", "KZ(L/2)", "6#", "KB", "B3", "B4", "Z2(L)"]
    for idx in range(11):
        xn = span_l * idx / 10.0
        ax1.plot([xn], [0.0], marker="o", markersize=5.2, color="#1D4ED8")
        lbl = node_names[idx] if idx in (0, 2, 5, 8, 10) else f"{idx}"
        ax1.text(xn, -0.35, lbl, ha="center", va="top", fontproperties=fp_tk, color="#1E293B")
        if idx < 10:
            ax1.text(xn + 0.05 * span_l, 0.10, f"E{idx+1}", ha="center", va="bottom", fontproperties=fp_tk, color="#475569")

    # Left fixed hinge Z1 & Right sliding roller Z2
    ax1.add_patch(Polygon([(0.0, -0.05), (-0.45, -0.85), (0.45, -0.85)], closed=True, facecolor="#E2E8F0", edgecolor="#0F172A", linewidth=1.2))
    ax1.plot([-0.65, 0.65], [-0.90, -0.90], color="#0F172A", linewidth=1.3)
    ax1.text(0.0, -1.18, "固定铰支座\n(约束 Dx, Dy, Dz)", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    ax1.add_patch(Polygon([(span_l, -0.05), (span_l - 0.45, -0.75), (span_l + 0.45, -0.75)], closed=True, facecolor="#E2E8F0", edgecolor="#0F172A", linewidth=1.2))
    ax1.plot([span_l - 0.65, span_l + 0.65], [-0.95, -0.95], color="#0F172A", linewidth=1.3)
    ax1.plot([span_l - 0.22, span_l + 0.22], [-0.85, -0.85], marker="o", linestyle="None", markersize=4.0, color="#0F172A")
    ax1.text(span_l, -1.18, "纵向滑动铰支座\n(约束 Dy, Dz；释放 Dx)", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    # Calculation span dimension
    ax1.annotate("", xy=(span_l, -2.15), xytext=(0.0, -2.15), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.1})
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
    ax2 = fig.add_axes([0.07, 0.08, 0.87, 0.38])
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
    ax2.set_ylim(-3.6, 3.0)
    ax2.add_patch(Rectangle((0.0, -0.35), cap_l, 0.35, facecolor="#DBEAFE", edgecolor="#1E40AF", linewidth=1.4))

    for idx_g, (gx, gr) in enumerate(zip(g_xs, g_rs), start=1):
        ax2.annotate("", xy=(gx, 0.02), xytext=(gx, 1.85), arrowprops={"arrowstyle": "-|>", "color": "#B91C1C", "lw": 1.6})
        ax2.text(gx, 1.95, f"{idx_g}#支点\nRu={gr:.1f} kN", ha="center", va="bottom", fontproperties=fp_tk, color="#991B1B")

    for idx_c, (cx, cr) in enumerate(zip(col_xs, col_rs), start=1):
        ax2.add_patch(Rectangle((cx - 0.25, -2.35), 0.50, 2.0, facecolor="#E2E8F0", edgecolor="#334155", linewidth=1.2))
        ax2.plot([cx - 0.55, cx + 0.55], [-2.35, -2.35], color="#0F172A", linewidth=2.0)
        ax2.text(cx, -2.55, f"{idx_c}#墩柱 (Dc={sub['pier_column']['diameter_m']:g}m, H={col_h:g}m)\n柱顶反力 Rcol={cr:.1f} kN", ha="center", va="top", fontproperties=fp_tk, color="#0F172A")

    ax2.annotate("", xy=(col_xs[-1], -1.25), xytext=(col_xs[0], -1.25), arrowprops={"arrowstyle": "<->", "color": "#1E40AF", "lw": 1.0})
    ax2.text(0.5 * (col_xs[0] + col_xs[-1]), -1.15, f"双柱中心距 {col_xs[-1]-col_xs[0]:.2f} m", ha="center", va="bottom", fontproperties=fp_tk, color="#1E40AF")
    ax2.annotate("", xy=(cap_l, -3.25), xytext=(0.0, -3.25), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.0})
    ax2.text(
        0.5 * cap_l,
        -3.32,
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
        "caption_zh": "图4-1 代表性1#简支T梁及2#中间双柱墩盖梁结构计算简图（单位：m, kN）",
        **paths,
    }


# ======================================================================
# Figure 2: Bending Moment & Shear Force Envelopes
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

    fig = Figure(figsize=(10.6, 7.2), dpi=dpi, facecolor="#FFFFFF")
    fp_h1 = _make_fp(font_path, 12.5, "bold")
    fp_sub = _make_fp(font_path, 10.2, "bold")
    fp_lbl = _make_fp(font_path, 9.0)
    fp_leg = _make_fp(font_path, 8.3)
    fp_tk = _make_fp(font_path, 8.2)

    fig.text(
        0.5,
        0.965,
        f"1#代表性T梁弯矩与剪力作用效应包络图（计算跨径 L = {span_l:g} m，公路-I级）",
        ha="center",
        va="top",
        fontproperties=fp_h1,
        color="#0F172A",
    )

    # Top subplot: Bending Moment Envelope
    ax_m = fig.add_axes([0.10, 0.54, 0.85, 0.35])
    _style_axes(ax_m, fp_tk)
    ax_m.plot(xs, m_dead, "--", color="#64748B", linewidth=1.4, label="恒载弯矩 Mg")
    ax_m.plot(xs, m_live, "-.", color="#0284C7", linewidth=1.4, label="活载弯矩 Mq (含冲击1+μ)")
    ax_m.plot(xs, m_sls, "-", color="#0D9488", linewidth=1.8, label="标准组合 Ms = Mg + Mq")
    ax_m.plot(xs, m_uls, "-o", color="#DC2626", linewidth=2.2, markersize=4.5, label=f"基本组合包络 Md = {gamma0:g}(1.2Mg+1.4Mq)")
    ax_m.axhline(mu_cap, color="#1E3A8A", linestyle=":", linewidth=1.8, label=f"跨中正截面抗弯承载力 Mu = {mu_cap:.1f} kN·m")
    ax_m.fill_between(xs, 0.0, m_uls, color="#FEE2E2", alpha=0.35)

    md_max = m_uls[5]
    ax_m.annotate(
        f"跨中控制值 KZ: Md = {md_max:.1f} kN·m ≤ Mu = {mu_cap:.1f} kN·m\n(L/4截面 Md = {env['quarter_span_m_uls_knm']:.1f} kN·m)",
        xy=(0.5 * span_l, md_max),
        xytext=(0.5 * span_l, md_max * 0.56),
        ha="center",
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.3", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.8},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.1},
    )
    ax_m.set_xlim(0.0, span_l)
    ax_m.set_ylim(0.0, max(mu_cap, md_max) * 1.16)
    ax_m.set_ylabel("弯矩 M (kN·m)", fontproperties=fp_lbl, color="#0F172A")
    ax_m.set_title("(a) 主梁正截面弯矩包络与抗弯承载力对比图", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_m.legend(prop=fp_leg, loc="upper right", framealpha=0.92)

    # Bottom subplot: Shear Force Envelope
    ax_v = fig.add_axes([0.10, 0.09, 0.85, 0.36])
    _style_axes(ax_v, fp_tk)
    ax_v.axhline(0.0, color="#334155", linewidth=0.9)
    ax_v.plot(xs, q_uls_pos, "-s", color="#DC2626", linewidth=2.0, markersize=4.0, label="基本组合最大正剪力包络 Vd+")
    ax_v.plot(xs, q_uls_neg, "-^", color="#2563EB", linewidth=2.0, markersize=4.0, label="基本组合最大负剪力包络 Vd-")
    ax_v.plot(xs, q_sls, "--", color="#0D9488", linewidth=1.4, label="正常使用标准组合剪力 Vs")
    ax_v.axhline(vu_cap, color="#15803D", linestyle=":", linewidth=1.6, label=f"斜截面抗剪承载力 +Vu = {vu_cap:.1f} kN")
    ax_v.axhline(-vu_cap, color="#15803D", linestyle=":", linewidth=1.6)
    ax_v.fill_between(xs, q_uls_neg, q_uls_pos, color="#DBEAFE", alpha=0.28)

    vd_sup = abs(float(gc["uls_shear"]["demand_vd_kn"]))
    ax_v.annotate(
        f"支点控制剪力 Vd = {vd_sup:.1f} kN ≤ Vu = {vu_cap:.1f} kN",
        xy=(0.0, q_uls_pos[0]),
        xytext=(0.18 * span_l, q_uls_pos[0] * 0.88),
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.28", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.8},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.0},
    )
    ax_v.set_xlim(0.0, span_l)
    v_lim = max(vu_cap, vd_sup) * 1.22
    ax_v.set_ylim(-v_lim, v_lim)
    ax_v.set_xlabel("顺桥向距左支点距离 x (m)", fontproperties=fp_lbl, color="#0F172A")
    ax_v.set_ylabel("剪力 V (kN)", fontproperties=fp_lbl, color="#0F172A")
    ax_v.set_title("(b) 主梁斜截面剪力正负包络与抗剪承载力对比图", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_v.legend(prop=fp_leg, loc="upper right", framealpha=0.92)

    paths = _save_fig(fig, out_dir, "fig2_moment_shear_envelope", formats, dpi)
    return {
        "figure_id": "fig2_moment_shear_envelope",
        "title_zh": "主梁弯矩与剪力组合包络图",
        "caption_zh": "图4-2 1#代表性T梁弯矩与剪力作用效应包络及承载力包络图（单位：kN·m, kN）",
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

    fig = Figure(figsize=(10.6, 7.2), dpi=dpi, facecolor="#FFFFFF")
    fp_h1 = _make_fp(font_path, 12.5, "bold")
    fp_sub = _make_fp(font_path, 10.2, "bold")
    fp_lbl = _make_fp(font_path, 9.0)
    fp_leg = _make_fp(font_path, 8.2)
    fp_tk = _make_fp(font_path, 8.0)

    fig.text(
        0.5,
        0.965,
        f"预应力钢束纵断线形及有效预应力沿梁长变化图（配置 {pre['tendon_designation']}，σcon = {sigma_con:g} MPa）",
        ha="center",
        va="top",
        fontproperties=fp_h1,
        color="#0F172A",
    )

    # Top panel: Full-beam parabolic tendon profiles y(x) in mm
    ax_t = fig.add_axes([0.10, 0.55, 0.85, 0.34])
    _style_axes(ax_t, fp_tk)
    ax_t.add_patch(Rectangle((0.0, 0.0), beam_l, h_mm, facecolor="#F8FAFC", edgecolor="#334155", linewidth=1.3))
    ax_t.axhline(float(sec["mid_span_mm"]["y_bot"]), color="#64748B", linestyle="--", linewidth=0.9, label=f"毛截面形心轴 yb = {sec['mid_span_mm']['y_bot']:.0f} mm")

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
        ax_t.plot(sample_xs, ys_c, color=col, linewidth=1.9, label=f"N{idx+1} (Φs15.2-{strands_per}：端 {ey:g} → 跨中 {my:g} mm)")

    ax_t.set_xlim(-0.4, beam_l + 0.4)
    ax_t.set_ylim(-80.0, h_mm + 140.0)
    ax_t.set_ylabel("距梁底高度 y (mm)", fontproperties=fp_lbl, color="#0F172A")
    ax_t.set_title(f"(a) 预制T梁全跨预应力钢束抛物线布置图（梁高 {h_mm:g} mm，共 {n_tendons} 束 = {pre['provided_strands']} 股）", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_t.legend(prop=fp_leg, loc="upper right", ncol=2, framealpha=0.92)

    # Bottom panel: Stress after losses along beam length (MPa)
    ax_s = fig.add_axes([0.10, 0.09, 0.85, 0.36])
    _style_axes(ax_s, fp_tk)
    lx = [float(r["x_m"]) for r in loss_rows]
    s_after_f = [sigma_con - float(r["sigma_l1_friction_mpa"]) for r in loss_rows]
    s_after_a = [sigma_con - float(r["sigma_l1_friction_mpa"]) - float(r["sigma_l2_anchorage_mpa"]) for r in loss_rows]
    s_pe = [float(r["effective_prestress_mpa"]) for r in loss_rows]

    ax_s.axhline(sigma_con, color="#475569", linestyle=":", linewidth=1.4, label=f"张拉控制应力 σcon = {sigma_con:g} MPa")
    ax_s.plot(lx, s_after_f, "--o", color="#0284C7", linewidth=1.5, markersize=3.8, label="扣除摩阻损失后 (σcon - σl1)")
    ax_s.plot(lx, s_after_a, "-.s", color="#D97706", linewidth=1.6, markersize=3.8, label=f"扣除锚固回缩损失后 (反摩阻段 lf = {lf_m:.2f} m)")
    ax_s.plot(lx, s_pe, "-D", color="#DC2626", linewidth=2.3, markersize=4.6, label="运营期有效预应力 σpe (扣除松弛σl5+收缩徐变σl6)")
    ax_s.fill_between(lx, min(s_pe) * 0.94, s_pe, color="#FEE2E2", alpha=0.28)

    pe_end = s_pe[0]
    pe_mid = s_pe[len(s_pe) // 2]
    ax_s.annotate(
        f"跨中有效预应力 σpe(L/2) = {pe_mid:.1f} MPa\n梁端有效预应力 σpe(0) = {pe_end:.1f} MPa",
        xy=(lx[len(lx) // 2], pe_mid),
        xytext=(0.5 * beam_l, pe_mid - 68.0),
        ha="center",
        fontproperties=fp_leg,
        color="#991B1B",
        bbox={"boxstyle": "round,pad=0.28", "fc": "#FFFBEB", "ec": "#F59E0B", "lw": 0.8},
        arrowprops={"arrowstyle": "->", "color": "#DC2626", "lw": 1.0},
    )
    ax_s.set_xlim(-0.4, beam_l + 0.4)
    ax_s.set_ylim(min(s_pe) - 115.0, sigma_con + 45.0)
    ax_s.set_xlabel("沿预制梁长坐标 x (m)", fontproperties=fp_lbl, color="#0F172A")
    ax_s.set_ylabel("预应力钢束应力 (MPa)", fontproperties=fp_lbl, color="#0F172A")
    ax_s.set_title("(b) 沿梁长分阶段预应力损失及有效预应力 σpe 分布曲线", fontproperties=fp_sub, color="#0F172A", loc="left")
    ax_s.legend(prop=fp_leg, loc="upper right", framealpha=0.92)

    paths = _save_fig(fig, out_dir, "fig3_tendon_and_prestress", formats, dpi)
    return {
        "figure_id": "fig3_tendon_and_prestress",
        "title_zh": "预应力钢束线形及有效预应力变化图",
        "caption_zh": "图5-1 预应力钢束纵断面线形及沿梁长有效预应力变化图（单位：mm, MPa）",
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

    fig = Figure(figsize=(10.6, 6.8), dpi=dpi, facecolor="#FFFFFF")
    fp_h1 = _make_fp(font_path, 12.5, "bold")
    fp_sub = _make_fp(font_path, 10.2, "bold")
    fp_lbl = _make_fp(font_path, 9.0)
    fp_txt = _make_fp(font_path, 8.3)
    fp_tk = _make_fp(font_path, 8.0)

    fig.text(
        0.5,
        0.96,
        "中间双柱墩与桩柱式桥台钻孔灌注桩地层柱状及桩端持力层位置对比图（JTG 3363-2019）",
        ha="center",
        va="top",
        fontproperties=fp_h1,
        color="#0F172A",
    )

    soil_colors = ["#FEF3C7", "#FDE68A", "#E0F2FE", "#BBF7D0", "#DDD6FE"]
    panels = [
        (
            fig.add_axes([0.08, 0.10, 0.39, 0.78]),
            "(a) 中间双柱墩钻孔灌注桩 (ZJ1/ZJ2)",
            sub_bp,
            "中间墩桩顶系梁底",
        ),
        (
            fig.add_axes([0.56, 0.10, 0.39, 0.78]),
            "(b) 桩柱式桥台钻孔灌注桩 (QT3/QT4，独立求解)",
            ab_bp,
            "桥台台帽底",
        ),
    ]

    max_depth = max(float(sub_bp["design_length_m"]), float(ab_bp["design_length_m"])) + 4.5

    for ax, sub_title, bp_data, cap_label in panels:
        _style_axes(ax, fp_tk)
        pile_l = float(bp_data["design_length_m"])
        pile_d = float(bp_data["diameter_m"])
        layers = list(bp_data.get("layer_contributions") or [])

        # Reconstruct full stratum display down to max_depth
        cur_z = 0.0
        for idx_l, lyr in enumerate(layers):
            trav = float(lyr["traversed_m"])
            # Extend bottom bearing stratum below pile tip to show socket clearly
            draw_thk = trav + (3.2 if idx_l == len(layers) - 1 else 0.0)
            col = soil_colors[idx_l % len(soil_colors)]
            ax.add_patch(
                Rectangle((-3.2, -(cur_z + draw_thk)), 6.4, draw_thk, facecolor=col, edgecolor="#64748B", linewidth=0.8, alpha=0.65)
            )
            mid_z = -(cur_z + 0.5 * trav)
            lyr_label = (
                f"{lyr['name']} (li={trav:.2f} m | qik={lyr['qik_kpa']:g} kPa | fa0={lyr['fa0_kpa']:g} kPa)"
                if trav < 1.8
                else f"{lyr['name']}\n穿层 li={trav:.2f} m | qik={lyr['qik_kpa']:g} kPa | fa0={lyr['fa0_kpa']:g} kPa"
            )
            ax.text(
                1.05,
                mid_z,
                lyr_label,
                ha="left",
                va="center",
                fontproperties=fp_txt,
                color="#1E293B",
            )
            cur_z += trav

        # Cap / Tie beam block above z=0 + Pile shaft [-pile_d/2..+pile_d/2, -pile_l..0]
        ax.add_patch(Rectangle((-1.6, 0.0), 3.2, 1.25, facecolor="#CBD5E1", edgecolor="#334155", linewidth=1.2))
        ax.text(0.0, 0.62, cap_label, ha="center", va="center", fontproperties=fp_txt, color="#0F172A")
        ax.add_patch(Rectangle((-0.5 * pile_d, -pile_l), pile_d, pile_l, facecolor="#93C5FD", edgecolor="#1E3A8A", linewidth=1.6))
        ax.axhline(-pile_l, color="#DC2626", linestyle="--", linewidth=1.2)
        ax.text(
            -0.85,
            -pile_l - 0.35,
            f"桩端标尺 z = -{pile_l:g} m (D = {pile_d:g} m)",
            ha="left",
            va="top",
            fontproperties=fp_txt,
            color="#B91C1C",
        )

        # Dimension arrow for pile length
        ax.annotate("", xy=(-2.35, -pile_l), xytext=(-2.35, 0.0), arrowprops={"arrowstyle": "<->", "color": "#0F172A", "lw": 1.2})
        ax.text(-2.50, -0.5 * pile_l, f"设计桩长 L = {pile_l:g} m", rotation=90, ha="right", va="center", fontproperties=fp_lbl, color="#0F172A")

        # Load vs [Ra] summary box at bottom (centered across [-2.85, +5.05] with 2 crisp lines)
        r_dem = float(bp_data["pile_top_sls_demand_kn"])
        ra_cap = float(bp_data["allowable_ra_kn"])
        min_sock = float(bp_data.get("min_required_socket_m", round(2.5 * pile_d, 2)))
        sock_txt = (
            f"\n嵌入持力层深度 {bp_data['bearing_stratum_socket_m']:.2f} m ≥ 规范最小要求 {min_sock:g} m"
            if "bearing_stratum_socket_m" in bp_data
            else ""
        )
        summary_box = FancyBboxPatch(
            (-2.85, -max_depth + 0.30),
            7.90,
            1.85,
            boxstyle="round,pad=0.12",
            facecolor="#F0FDF4" if r_dem <= ra_cap else "#FEF2F2",
            edgecolor="#16A34A" if r_dem <= ra_cap else "#DC2626",
            linewidth=1.0,
        )
        ax.add_patch(summary_box)
        ax.text(
            1.10,
            -max_depth + 1.22,
            f"桩顶标准值荷载 Rsls = {r_dem:.1f} kN ≤ 容许承载力 [Ra] = {ra_cap:.1f} kN{sock_txt}",
            ha="center",
            va="center",
            fontproperties=fp_txt,
            color="#14532D" if r_dem <= ra_cap else "#991B1B",
        )

        ax.set_xlim(-3.2, 5.4)
        ax.set_ylim(-max_depth, 2.0)
        ax.set_xticks([])
        ax.set_ylabel("自承台/系梁底算起深度 z (m)", fontproperties=fp_lbl, color="#0F172A")
        ax.set_title(sub_title, fontproperties=fp_sub, color="#0F172A", loc="left")

    paths = _save_fig(fig, out_dir, "fig4_pile_soil_profile", formats, dpi)
    return {
        "figure_id": "fig4_pile_soil_profile",
        "title_zh": "桥墩与桥台桩基土层及桩端持力层位置图",
        "caption_zh": "图8-1 中间双柱墩与桩柱式桥台钻孔灌注桩分层土层参数及桩端嵌入持力层位置对比图（单位：m, kPa, kN）",
        **paths,
    }


def generate_bridge_calculation_figures(
    calc_report: Mapping[str, Any],
    output_dir: str | Path,
    *,
    formats: Sequence[str] = ("png", "svg"),
    dpi: int = 300,
    include_scheme_figure: bool = True,
) -> dict[str, Any]:
    """Generate Word/PPT-ready bridge engineering calculation figures from live calculation data."""
    out_p = Path(output_dir).expanduser().resolve()
    out_p.mkdir(parents=True, exist_ok=True)
    font_path = _resolve_cjk_font_path()

    figures: list[dict[str, Any]] = []
    if include_scheme_figure:
        figures.append(_render_fig0_scheme_comparison(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig1_structural_model(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig2_moment_shear_envelope(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig3_tendon_and_prestress(calc_report, out_p, font_path, formats, dpi))
    figures.append(_render_fig4_pile_soil_profile(calc_report, out_p, font_path, formats, dpi))

    by_id = {f["figure_id"]: f for f in figures}
    return {
        "output_dir": str(out_p),
        "font_used": str(font_path) if font_path else "matplotlib_default",
        "dpi": dpi,
        "figure_count": len(figures),
        "figures": figures,
        "by_id": by_id,
    }
