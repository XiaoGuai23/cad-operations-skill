"""Native GB/T 50001 & JTG Civil/Bridge CAD graphic symbols and sheet setup via ezdxf."""

from __future__ import annotations

import math
from typing import Literal

import ezdxf
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Modelspace

FORBIDDEN_GB_AXIS_LETTERS = frozenset({"I", "O", "Z"})

SHEET_SIZES_MM: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}


def setup_civil_dxf_document(
    *,
    sheet_size: str = "A3",
    font: str = "simfang.ttf",
    title: str = "桥梁工程构造图",
    project: str = "4×25m 预应力混凝土简支T梁桥",
    drawing_no: str = "QL-01",
    organization: str = "土木与交通工程设计院",
    unit_note: str = "mm",
    scale_label: str = "图示比例",
) -> tuple[Drawing, Modelspace, dict[str, float]]:
    """Create a GB/T civil construction DXF with ARCHTICK dimstyle, lineweights, and title block."""

    size_key = sheet_size.upper()
    if size_key not in SHEET_SIZES_MM:
        raise ValueError(f"unsupported sheet_size {sheet_size!r}; expected one of {sorted(SHEET_SIZES_MM)}")
    sheet_w, sheet_h = SHEET_SIZES_MM[size_key]

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM

    if "CIVIL_TEXT" not in doc.styles:
        st = doc.styles.add("CIVIL_TEXT", font=font)
        st.dxf.width = 0.80
    if "CIVIL_TITLE" not in doc.styles:
        st_t = doc.styles.add("CIVIL_TITLE", font=font)
        st_t.dxf.width = 0.85

    # Explicit millimeter lineweights (in 1/100 mm) matching gb-civil-construction.yaml
    layer_defs = [
        ("FRAME", 7, "CONTINUOUS", 70),
        ("OUTLINE", 4, "CONTINUOUS", 60),
        ("THIN", 8, "CONTINUOUS", 25),
        ("REBAR", 1, "CONTINUOUS", 50),
        ("TENDON", 6, "CONTINUOUS", 50),
        ("CENTER", 1, "CENTER" if "CENTER" in doc.linetypes else "CONTINUOUS", 18),
        ("HIDDEN", 2, "HIDDEN" if "HIDDEN" in doc.linetypes else "CONTINUOUS", 25),
        ("DIM", 3, "CONTINUOUS", 18),
        ("TEXT", 7, "CONTINUOUS", 18),
        ("HATCH", 9, "CONTINUOUS", 15),
        ("TERRAIN", 32, "CONTINUOUS", 35),
    ]
    for name, color, ltype, lw in layer_defs:
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=ltype, lineweight=lw)
        else:
            lyr = doc.layers.get(name)
            lyr.dxf.color = color
            lyr.dxf.lineweight = lw

    if "CIVIL_ARCH" not in doc.dimstyles:
        doc.dimstyles.add(
            "CIVIL_ARCH",
            dxfattribs={
                "dimtxsty": "CIVIL_TEXT",
                "dimblk": ezdxf.ARROWS.architectural_tick,
                "dimtxt": 2.4,
                "dimasz": 1.8,
                "dimgap": 0.7,
                "dimexo": 0.8,
                "dimexe": 1.2,
                "dimtad": 1,
                "dimdec": 0,
                "dimclrd": 3,
                "dimclre": 3,
                "dimclrt": 7,
            },
        )

    msp = doc.modelspace()
    margin_left = 25.0 if size_key in {"A0", "A1", "A2"} else 15.0
    margin_other = 10.0

    # Outer paper edge & inner thick border
    msp.add_lwpolyline(
        [(0.0, 0.0), (sheet_w, 0.0), (sheet_w, sheet_h), (0.0, sheet_h)],
        close=True,
        dxfattribs={"layer": "THIN"},
    )
    ix0, iy0 = margin_left, margin_other
    ix1, iy1 = sheet_w - margin_other, sheet_h - margin_other
    msp.add_lwpolyline(
        [(ix0, iy0), (ix1, iy0), (ix1, iy1), (ix0, iy1)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )

    # Bottom-right GB/T JTG Highway/Bridge Engineering Title Block (180 x 36 mm)
    tb_w, tb_h = 180.0, 36.0
    tb_x0, tb_y0 = ix1 - tb_w, iy0
    msp.add_lwpolyline(
        [(tb_x0, tb_y0), (ix1, tb_y0), (ix1, tb_y0 + tb_h), (tb_x0, tb_y0 + tb_h)],
        close=True,
        dxfattribs={"layer": "FRAME"},
    )
    for row_dy in (12.0, 24.0):
        msp.add_line((tb_x0, tb_y0 + row_dy), (ix1, tb_y0 + row_dy), dxfattribs={"layer": "FRAME"})
    msp.add_line((tb_x0 + 88.0, tb_y0), (tb_x0 + 88.0, tb_y0 + tb_h), dxfattribs={"layer": "FRAME"})
    msp.add_line((tb_x0 + 134.0, tb_y0), (tb_x0 + 134.0, tb_y0 + 24.0), dxfattribs={"layer": "FRAME"})

    add_civil_text(msp, organization, tb_x0 + 44.0, tb_y0 + 28.5, height=2.7, align="CENTER")
    add_civil_text(msp, project, tb_x0 + 134.0, tb_y0 + 28.5, height=2.5, align="CENTER")
    add_civil_text(msp, f"图名：{title}", tb_x0 + 4.0, tb_y0 + 16.5, height=2.6, align="LEFT")
    add_civil_text(msp, f"比例 {scale_label}", tb_x0 + 91.0, tb_y0 + 16.5, height=2.4, align="LEFT")
    add_civil_text(msp, f"单位 {unit_note}", tb_x0 + 137.0, tb_y0 + 16.5, height=2.4, align="LEFT")
    add_civil_text(msp, "设计：CAD-Skill   复核：JTG规范引擎", tb_x0 + 4.0, tb_y0 + 4.5, height=2.3, align="LEFT")
    add_civil_text(msp, "图号", tb_x0 + 91.0, tb_y0 + 4.5, height=2.4, align="LEFT")
    add_civil_text(msp, drawing_no, tb_x0 + 157.0, tb_y0 + 4.5, height=2.6, align="CENTER")

    # Top centered sheet header title
    add_civil_text(
        msp,
        title,
        0.5 * (ix0 + ix1),
        iy1 - 9.5,
        height=4.8,
        layer="TEXT",
        style="CIVIL_TITLE",
        align="CENTER",
    )

    doc.header["$EXTMIN"] = (0.0, 0.0, 0.0)
    doc.header["$EXTMAX"] = (sheet_w, sheet_h, 0.0)

    bounds = {
        "sheet_w": sheet_w,
        "sheet_h": sheet_h,
        "ix0": ix0,
        "iy0": iy0,
        "ix1": ix1,
        "iy1": iy1,
        "tb_x0": tb_x0,
        "tb_y1": tb_y0 + tb_h,
        "content_top": iy1 - 16.0,
    }
    return doc, msp, bounds


def add_civil_text(
    msp: Modelspace,
    text: str,
    x: float,
    y: float,
    *,
    height: float = 2.6,
    layer: str = "TEXT",
    style: str = "CIVIL_TEXT",
    align: Literal["LEFT", "CENTER", "RIGHT", "MIDDLE_CENTER"] = "LEFT",
    rotation: float = 0.0,
):
    """Add width-controlled civil text compatible with both Windows FangSong and Linux Unifont."""

    ent = msp.add_text(
        str(text),
        dxfattribs={
            "layer": layer,
            "height": height,
            "style": style,
            "width": 0.80,
            "rotation": rotation,
        },
    )
    align_map = {
        "LEFT": TextEntityAlignment.LEFT,
        "CENTER": TextEntityAlignment.CENTER,
        "RIGHT": TextEntityAlignment.RIGHT,
        "MIDDLE_CENTER": TextEntityAlignment.MIDDLE_CENTER,
    }
    ent.set_placement((x, y), align=align_map[align])
    return ent


def add_arch_dim(
    msp: Modelspace,
    p1: tuple[float, float],
    p2: tuple[float, float],
    base: tuple[float, float],
    label: str,
    *,
    angle: float = 0.0,
    layer: str = "DIM",
):
    """Add an architectural oblique-tick native linear DIMENSION entity."""

    if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-4:
        return None
    dim = msp.add_linear_dim(
        base=base,
        p1=p1,
        p2=p2,
        angle=angle,
        text=str(label),
        dimstyle="CIVIL_ARCH",
        dxfattribs={"layer": layer},
    )
    dim.render()
    return dim.dimension


def draw_pile_s_break(
    msp: Modelspace,
    x_center: float,
    y_break: float,
    diameter: float,
    *,
    layer: str = "OUTLINE",
    hatch_layer: str = "HATCH",
) -> None:
    """Draw cylindrical bored-pile S-curve (figure-8) break symbol at pile bottom."""

    r_half = 0.25 * diameter
    if r_half <= 1e-4:
        return
    # Left lobe: bottom semicircle + top semicircle (closed lens) showing cross-section hatch
    left_cx = x_center - r_half
    right_cx = x_center + r_half
    msp.add_arc((left_cx, y_break), r_half, 180.0, 360.0, dxfattribs={"layer": layer})
    msp.add_arc((left_cx, y_break), r_half, 0.0, 180.0, dxfattribs={"layer": layer})
    # Right lobe: bottom semicircle completing the S-curve
    msp.add_arc((right_cx, y_break), r_half, 180.0, 360.0, dxfattribs={"layer": layer})

    # Subtle 45-degree hatch strokes inside left lobe
    for frac in (-0.45, 0.0, 0.45):
        dx = frac * r_half
        dy_max = math.sqrt(max(0.0, r_half * r_half - dx * dx)) * 0.75
        if dy_max > 0.15:
            msp.add_line(
                (left_cx + dx - 0.35 * dy_max, y_break - dy_max),
                (left_cx + dx + 0.35 * dy_max, y_break + dy_max),
                dxfattribs={"layer": hatch_layer},
            )


def draw_ground_line(
    msp: Modelspace,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    label: str | None = "地面线",
    scale: float = 1.0,
    layer: str = "TERRAIN",
) -> None:
    """Draw natural ground line with grouped 3-stroke 45-degree civil soil hatches underneath."""

    seg_len = math.hypot(x2 - x1, y2 - y1)
    if seg_len < 1e-4:
        return
    msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer})

    ux = (x2 - x1) / seg_len
    uy = (y2 - y1) / seg_len
    # Downward normal
    nx, ny = uy, -ux

    group_pitch = 14.0 * scale
    stroke_gap = 1.6 * scale
    stroke_len = 2.6 * scale
    n_groups = max(1, int(seg_len // group_pitch))

    for g in range(n_groups):
        base_s = (g + 0.35) * group_pitch
        for k in range(3):
            s = base_s + k * stroke_gap
            if s + stroke_len > seg_len:
                break
            px = x1 + ux * s
            py = y1 + uy * s
            qx = px + (-0.707 * ux + 0.707 * nx) * stroke_len
            qy = py + (-0.707 * uy + 0.707 * ny) * stroke_len
            msp.add_line((px, py), (qx, qy), dxfattribs={"layer": "HATCH"})

    if label:
        add_civil_text(msp, label, x1 + 2.0 * scale, y1 + 1.2 * scale, height=2.3 * scale)


def draw_slope_arrow(
    msp: Modelspace,
    x_mid: float,
    y_mid: float,
    *,
    slope_text: str = "2%",
    direction: Literal["left", "right"] = "right",
    length: float = 12.0,
    layer: str = "DIM",
) -> None:
    """Draw single-sided directional bridge deck / cap-beam cross-slope arrow (e.g. '2%')."""

    sign = 1.0 if direction == "right" else -1.0
    x_start = x_mid - 0.5 * sign * length
    x_tip = x_mid + 0.5 * sign * length
    y_tip = y_mid - 0.35  # gentle visual downward tilt
    msp.add_line((x_start, y_mid + 0.35), (x_tip, y_tip), dxfattribs={"layer": layer})
    # Half-arrowhead on top side (classic JTG bridge drawing convention)
    msp.add_line(
        (x_tip, y_tip),
        (x_tip - sign * 2.2, y_tip + 1.0),
        dxfattribs={"layer": layer},
    )
    add_civil_text(msp, slope_text, x_mid, y_mid + 1.0, height=2.2, layer="TEXT", align="CENTER")


def draw_elevation_marker(
    msp: Modelspace,
    x: float,
    y: float,
    elevation_m: float,
    *,
    prefix: str = "",
    scale: float = 1.0,
    layer: str = "DIM",
) -> None:
    """Draw GB/T 50001 triangular elevation symbol (▽) with 3-decimal meter value."""

    tri_h = 2.6 * scale
    tri_half_w = 1.8 * scale
    shelf_w = 12.0 * scale
    msp.add_lwpolyline(
        [(x, y), (x - tri_half_w, y + tri_h), (x + tri_half_w, y + tri_h)],
        close=True,
        dxfattribs={"layer": layer},
    )
    msp.add_line((x + tri_half_w, y + tri_h), (x + tri_half_w + shelf_w, y + tri_h), dxfattribs={"layer": layer})
    val_str = "±0.000" if abs(elevation_m) < 1e-6 else f"{elevation_m:+.3f}"
    full_txt = f"{prefix}{val_str}" if prefix else val_str
    add_civil_text(
        msp,
        full_txt,
        x + tri_half_w + 0.8 * scale,
        y + tri_h + 0.7 * scale,
        height=2.2 * scale,
        align="LEFT",
    )


def draw_axis_bubble(
    msp: Modelspace,
    x: float,
    y: float,
    label: str,
    *,
    radius: float = 3.2,
    stem_to_y: float | None = None,
    layer: str = "CENTER",
) -> None:
    """Draw circled pier/abutment/grid axis number, rejecting GB/T forbidden letters I, O, Z."""

    clean = str(label).strip()
    if clean.upper() in FORBIDDEN_GB_AXIS_LETTERS:
        raise ValueError(f"axis label {clean!r} is forbidden by GB/T 50001 (I, O, Z not allowed)")
    msp.add_circle((x, y), radius, dxfattribs={"layer": layer})
    add_civil_text(msp, clean, x, y, height=radius * 1.15, layer="TEXT", align="MIDDLE_CENTER")
    if stem_to_y is not None:
        y_edge = y - radius if stem_to_y < y else y + radius
        if abs(stem_to_y - y_edge) > 0.2:
            msp.add_line((x, y_edge), (x, stem_to_y), dxfattribs={"layer": layer})


def draw_boxed_tag(
    msp: Modelspace,
    x: float,
    y: float,
    tag: str,
    *,
    leader_to: tuple[float, float] | None = None,
    box_w: float = 6.2,
    box_h: float = 3.8,
    layer: str = "TENDON",
) -> None:
    """Draw boxed prestressing tendon callout ([N1]..[N5]) with optional leader line."""

    x0, y0 = x - 0.5 * box_w, y - 0.5 * box_h
    msp.add_lwpolyline(
        [(x0, y0), (x0 + box_w, y0), (x0 + box_w, y0 + box_h), (x0, y0 + box_h)],
        close=True,
        dxfattribs={"layer": layer},
    )
    add_civil_text(msp, tag, x, y, height=box_h * 0.58, layer="TEXT", align="MIDDLE_CENTER")
    if leader_to is not None:
        tx, ty = leader_to
        # Connect from nearest horizontal edge of box
        anchor_x = x0 if tx < x else x0 + box_w
        if math.hypot(tx - anchor_x, ty - y) > 0.3:
            msp.add_line((anchor_x, y), (tx, ty), dxfattribs={"layer": "THIN"})


def draw_bearing_symbol(
    msp: Modelspace,
    x: float,
    y: float,
    *,
    kind: Literal["fixed", "sliding_longitudinal", "sliding_multidirectional"] = "fixed",
    size: float = 2.4,
    layer: str = "OUTLINE",
) -> None:
    """Draw bridge elastomeric/pot bearing plan symbol: fixed vs unidirectional/multidirectional sliding."""

    half = 0.5 * size
    msp.add_lwpolyline(
        [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)],
        close=True,
        dxfattribs={"layer": layer},
    )
    if kind == "fixed":
        # Crossed diagonals + inner circle denoting fixed point
        msp.add_line((x - half, y - half), (x + half, y + half), dxfattribs={"layer": layer})
        msp.add_line((x - half, y + half), (x + half, y - half), dxfattribs={"layer": layer})
        msp.add_circle((x, y), 0.32 * size, dxfattribs={"layer": "REBAR"})
    elif kind == "sliding_longitudinal":
        # Horizontal longitudinal movement arrow through bearing pad
        ext = 0.95 * size
        msp.add_line((x - ext, y), (x + ext, y), dxfattribs={"layer": "REBAR"})
        msp.add_line((x + ext, y), (x + ext - 0.9, y + 0.5), dxfattribs={"layer": "REBAR"})
        msp.add_line((x - ext, y), (x - ext + 0.9, y + 0.5), dxfattribs={"layer": "REBAR"})
    else:
        # Bidirectional movement arrows
        ext = 0.90 * size
        msp.add_line((x - ext, y), (x + ext, y), dxfattribs={"layer": "REBAR"})
        msp.add_line((x, y - ext), (x, y + ext), dxfattribs={"layer": "REBAR"})
