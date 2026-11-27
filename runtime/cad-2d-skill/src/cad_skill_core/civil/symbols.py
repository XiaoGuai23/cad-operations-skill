"""Native GB/T 50001 & JTG Civil/Bridge CAD graphic symbols and sheet setup via ezdxf."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from typing import Any, Literal

import ezdxf
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Modelspace

FORBIDDEN_GB_AXIS_LETTERS = frozenset({"I", "O", "Z"})
VIEW_XDATA_APPID = "CAD_SKILL_VIEW"
META_XDATA_APPID = "CAD_SKILL_META"

_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
}

SHEET_SIZES_MM: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}


@dataclass(frozen=True, slots=True)
class ViewBox:
    """Isotropic true-scale coordinate mapper from physical engineering units to paper mm."""

    view_id: str
    origin_x: float
    origin_y: float
    scale_denom: float
    real_unit: Literal["mm", "cm", "m"] = "mm"

    def __post_init__(self) -> None:
        if self.scale_denom <= 0:
            raise ValueError(f"scale_denom must be > 0, got {self.scale_denom}")
        if self.real_unit not in _UNIT_TO_MM:
            raise ValueError(f"unsupported real_unit {self.real_unit!r}")

    @property
    def paper_per_real(self) -> float:
        return _UNIT_TO_MM[self.real_unit] / float(self.scale_denom)

    @property
    def paper_per_mm(self) -> float:
        return 1.0 / float(self.scale_denom)

    def x(self, real_x: float) -> float:
        return self.origin_x + float(real_x) * self.paper_per_real

    def y(self, real_y: float) -> float:
        return self.origin_y + float(real_y) * self.paper_per_real

    def pt(self, real_x: float, real_y: float) -> tuple[float, float]:
        s = self.paper_per_real
        return (self.origin_x + float(real_x) * s, self.origin_y + float(real_y) * s)

    def span(self, real_len: float) -> float:
        return float(real_len) * self.paper_per_real

    def register(
        self,
        doc: Drawing,
        msp: Modelspace,
        *,
        paper_bounds: tuple[float, float, float, float] | None = None,
    ) -> None:
        """Persist machine-readable viewport scale metadata on the sheet FRAME for reopen-DXF verification."""
        payload = {
            "view_id": self.view_id,
            "origin_x": round(self.origin_x, 6),
            "origin_y": round(self.origin_y, 6),
            "scale_denom": float(self.scale_denom),
            "real_unit": self.real_unit,
            "paper_bounds": [round(v, 4) for v in paper_bounds] if paper_bounds is not None else None,
        }
        _append_xdata_json(doc, msp, VIEW_XDATA_APPID, payload)


def _find_carrier_entity(msp: Modelspace):
    for ent in msp:
        if ent.dxftype() == "LWPOLYLINE" and str(ent.dxf.get("layer", "")).upper() == "FRAME":
            return ent
    return next(iter(msp), None)


def _append_xdata_json(doc: Drawing, msp: Modelspace, appid: str, obj: Mapping[str, Any]) -> None:
    if appid not in doc.appids:
        doc.appids.add(appid)
    carrier = _find_carrier_entity(msp)
    if carrier is None:
        return
    raw = json.dumps(dict(obj), ensure_ascii=False)
    # Encode one JSON record as chunked 1000 strings separated by a 1002 control brace or sentinel prefix
    chunks = [(1000, ("@@REC@@" if i == 0 else "@@CONT@@") + raw[i : i + 190]) for i in range(0, len(raw), 190)]
    existing = list(carrier.get_xdata(appid)) if carrier.has_xdata(appid) else []
    carrier.set_xdata(appid, [*existing, *chunks])


def attach_sheet_metadata(doc: Drawing, msp: Modelspace, key: str, data: Mapping[str, Any]) -> None:
    """Attach structured sheet/calc/schedule metadata onto the sheet FRAME so reopen-DXF audits are self-contained."""
    _append_xdata_json(doc, msp, META_XDATA_APPID, {"key": key, "data": dict(data)})


def setup_civil_dxf_document(
    *,
    sheet_size: str = "A3",
    font: str = "Deng.ttf",
    title: str = "桥梁工程构造图",
    project: str = "4×25m 预应力混凝土简支T梁桥",
    drawing_no: str = "QL-01",
    organization: str = "土木与交通工程设计院",
    unit_note: str = "mm",
    scale_label: str = "图示比例",
    style_overrides: Mapping[str, Any] | None = None,
) -> tuple[Drawing, Modelspace, dict[str, float]]:
    """Create a GB/T civil construction DXF with ARCHTICK dimstyle, lineweights, and title block."""

    overrides = dict(style_overrides or {})
    eff_font = str(overrides.get("font") or font)
    eff_title_font = str(
        overrides.get("title_font")
        or ("simhei.ttf" if "font" not in overrides and eff_font == "Deng.ttf" else eff_font)
    )
    eff_width_factor = float(overrides.get("text_width_factor", 0.85))
    layer_color_map = {str(k).upper(): int(v) for k, v in dict(overrides.get("layer_colors") or {}).items() if int(v) > 0}
    layer_lw_map_mm = {str(k).upper(): float(v) for k, v in dict(overrides.get("layer_lineweights_mm") or {}).items() if float(v) > 0}
    dim_stack_step = float(overrides.get("dim_stack_step_mm", 8.5))
    schedule_dock = str(overrides.get("schedule_dock", "bottom_right"))

    size_key = sheet_size.upper()
    if size_key not in SHEET_SIZES_MM:
        raise ValueError(f"unsupported sheet_size {sheet_size!r}; expected one of {sorted(SHEET_SIZES_MM)}")
    sheet_w, sheet_h = SHEET_SIZES_MM[size_key]

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$LIMMIN"] = (0.0, 0.0)
    doc.header["$LIMMAX"] = (sheet_w, sheet_h)
    doc.header["$EXTMIN"] = (0.0, 0.0, 0.0)
    doc.header["$EXTMAX"] = (sheet_w, sheet_h, 0.0)

    if "CIVIL_TEXT" not in doc.styles:
        st = doc.styles.add("CIVIL_TEXT", font=eff_font)
        st.dxf.width = eff_width_factor
    if "CIVIL_TITLE" not in doc.styles:
        st_t = doc.styles.add("CIVIL_TITLE", font=eff_title_font)
        st_t.dxf.width = min(1.0, round(eff_width_factor + 0.05, 2))

    # Explicit millimeter lineweights (in 1/100 mm): Main Rebar (0.70mm) > Frame (0.50mm) > Stirrup (0.35mm) > Concrete (0.25mm) > Dim (0.15mm)
    layer_defs = [
        ("FRAME", 7, "CONTINUOUS", 50),
        ("OUTLINE", 4, "CONTINUOUS", 40),
        ("CONCRETE", 4, "CONTINUOUS", 25),
        ("THIN", 8, "CONTINUOUS", 15),
        ("REBAR", 1, "CONTINUOUS", 70),
        ("REBAR_STIRRUP", 30, "CONTINUOUS", 35),
        ("TENDON", 6, "CONTINUOUS", 60),
        ("CENTER", 1, "CENTER" if "CENTER" in doc.linetypes else "CONTINUOUS", 15),
        ("HIDDEN", 2, "HIDDEN" if "HIDDEN" in doc.linetypes else "CONTINUOUS", 20),
        ("DIM", 3, "CONTINUOUS", 15),
        ("TEXT", 7, "CONTINUOUS", 25),
        ("HATCH", 9, "CONTINUOUS", 13),
        ("TERRAIN", 32, "CONTINUOUS", 35),
    ]
    _valid_dxf_lws = (5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
    for name, color, ltype, lw in layer_defs:
        eff_color = layer_color_map.get(name, color)
        if name in layer_lw_map_mm:
            raw_lw_100 = int(round(layer_lw_map_mm[name] * 100.0))
            eff_lw = min(_valid_dxf_lws, key=lambda cand: abs(cand - raw_lw_100))
        else:
            eff_lw = lw
        if name not in doc.layers:
            doc.layers.add(name, color=eff_color, linetype=ltype, lineweight=eff_lw)
        else:
            lyr = doc.layers.get(name)
            lyr.dxf.color = eff_color
            lyr.dxf.lineweight = eff_lw

    if "CIVIL_ARCH" not in doc.dimstyles:
        arrow_mode = str(overrides.get("dim_arrow_style", "archtick")).strip().lower()
        dim_blk_val = "" if arrow_mode == "closed_filled" else ezdxf.ARROWS.architectural_tick
        dim_col = layer_color_map.get("DIM", 3)
        txt_col = layer_color_map.get("TEXT", 7)
        doc.dimstyles.add(
            "CIVIL_ARCH",
            dxfattribs={
                "dimtxsty": "CIVIL_TEXT",
                "dimblk": dim_blk_val,
                "dimtxt": float(overrides.get("dim_text_height_mm", 2.4)),
                "dimasz": float(overrides.get("dim_arrow_size_mm", 1.8)),
                "dimgap": float(overrides.get("dim_gap_mm", 0.7)),
                "dimexo": 0.8,
                "dimexe": 1.2,
                "dimtad": 1,
                "dimdec": 0,
                "dimclrd": dim_col,
                "dimclre": dim_col,
                "dimclrt": txt_col,
            },
        )

    msp = doc.modelspace()
    default_ml = 25.0 if size_key in {"A0", "A1", "A2"} else 15.0
    margin_left = float(overrides.get("margin_left_mm", default_ml))
    margin_other = float(overrides.get("margin_other_mm", 10.0))

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
    tb_title_h = 2.6 if len(title) <= 15 else max(1.65, round(41.0 / len(title), 2))
    add_civil_text(msp, f"图名：{title}", tb_x0 + 4.0, tb_y0 + 16.5, height=tb_title_h, align="LEFT")
    add_civil_text(msp, f"比例 {scale_label}", tb_x0 + 91.0, tb_y0 + 16.5, height=2.4, align="LEFT")
    add_civil_text(msp, f"单位 {unit_note}", tb_x0 + 137.0, tb_y0 + 16.5, height=2.4, align="LEFT")
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

    attach_sheet_metadata(
        doc,
        msp,
        "sheet_identity",
        {
            "drawing_no": drawing_no,
            "title": title,
            "project": project,
            "sheet_size": size_key,
            "unit_note": unit_note,
            "scale_label": scale_label,
            "font": eff_font,
            "dim_stack_step_mm": dim_stack_step,
            "schedule_dock": schedule_dock,
        },
    )

    bounds: dict[str, Any] = {
        "sheet_w": sheet_w,
        "sheet_h": sheet_h,
        "ix0": ix0,
        "iy0": iy0,
        "ix1": ix1,
        "iy1": iy1,
        "tb_x0": tb_x0,
        "tb_y1": tb_y0 + tb_h,
        "content_top": iy1 - 16.0,
        "dim_stack_step": dim_stack_step,
        "schedule_dock": schedule_dock,
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


def draw_comb_leader(
    msp: Modelspace,
    probe_x: float,
    layer_ys: Sequence[float],
    labels: Sequence[str],
    *,
    shelf_x: float,
    shelf_top_y: float,
    shelf_width: float = 46.0,
    row_pitch: float = 4.6,
    layer: str = "DIM",
) -> None:
    """Draw a GB/T multi-layer comb callout piercing thin pavement/structural layers without text overlap."""
    if not layer_ys or not labels:
        return
    y_lo = min(layer_ys) - 0.8
    y_hi = max(max(layer_ys), shelf_top_y)
    msp.add_line((probe_x, y_lo), (probe_x, y_hi), dxfattribs={"layer": layer})
    for py in layer_ys:
        msp.add_line((probe_x - 0.85, py - 0.85), (probe_x + 0.85, py + 0.85), dxfattribs={"layer": layer})

    sign = 1.0 if shelf_x >= probe_x else -1.0
    msp.add_line((probe_x, shelf_top_y), (shelf_x, shelf_top_y), dxfattribs={"layer": layer})
    for idx, txt in enumerate(labels):
        ry = shelf_top_y - idx * row_pitch
        if idx > 0:
            msp.add_line((shelf_x, shelf_top_y), (shelf_x, ry), dxfattribs={"layer": layer})
        x_end = shelf_x + sign * shelf_width
        msp.add_line((shelf_x, ry), (x_end, ry), dxfattribs={"layer": layer})
        tx = shelf_x + 1.2 if sign > 0 else shelf_x - 1.2
        add_civil_text(msp, txt, tx, ry + 0.7, height=2.1, align="LEFT" if sign > 0 else "RIGHT")


def draw_detail_callout(
    msp: Modelspace,
    cx: float,
    cy: float,
    radius: float,
    label: str,
    *,
    leader_to: tuple[float, float],
    layer: str = "HIDDEN",
) -> None:
    """Draw a dashed detail zoom ring with an external callout shelf (e.g. 'I (见1:10详图)')."""
    msp.add_circle((cx, cy), radius, dxfattribs={"layer": layer})
    lx, ly = leader_to
    dx, dy = lx - cx, ly - cy
    dist = max(1e-4, math.hypot(dx, dy))
    sx, sy = cx + dx * (radius / dist), cy + dy * (radius / dist)
    msp.add_line((sx, sy), (lx, ly), dxfattribs={"layer": "DIM"})
    sign = 1.0 if lx >= cx else -1.0
    msp.add_line((lx, ly), (lx + sign * 16.0, ly), dxfattribs={"layer": "DIM"})
    add_civil_text(
        msp,
        label,
        lx + (1.0 if sign > 0 else -1.0),
        ly + 0.8,
        height=2.1,
        align="LEFT" if sign > 0 else "RIGHT",
    )


def draw_section_cut_symbol(
    msp: Modelspace,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    label: str,
    *,
    look_dx: float = 0.0,
    look_dy: float = 1.0,
    tick_len: float = 4.2,
    arrow_len: float = 3.8,
) -> None:
    """Draw a GB/T 50001 section-cut marker (e.g. A-A, B-B) with bold end ticks and viewing-direction arrows."""
    dx, dy = x2 - x1, y2 - y1
    seg = max(1e-4, math.hypot(dx, dy))
    ux, uy = dx / seg, dy / seg
    ld_norm = max(1e-4, math.hypot(look_dx, look_dy))
    vx, vy = look_dx / ld_norm, look_dy / ld_norm

    for px, py, sign in ((x1, y1, -1.0), (x2, y2, 1.0)):
        # Bold cut tick outside member
        tx_out = px + sign * ux * tick_len
        ty_out = py + sign * uy * tick_len
        msp.add_line((px, py), (tx_out, ty_out), dxfattribs={"layer": "FRAME"})
        # Viewing-direction arrow from outer end of tick
        ax_tip = tx_out + vx * arrow_len
        ay_tip = ty_out + vy * arrow_len
        msp.add_line((tx_out, ty_out), (ax_tip, ay_tip), dxfattribs={"layer": "DIM"})
        msp.add_line((ax_tip, ay_tip), (ax_tip - 1.2 * vx + 0.55 * ux, ay_tip - 1.2 * vy + 0.55 * uy), dxfattribs={"layer": "DIM"})
        msp.add_line((ax_tip, ay_tip), (ax_tip - 1.2 * vx - 0.55 * ux, ay_tip - 1.2 * vy - 0.55 * uy), dxfattribs={"layer": "DIM"})
        add_civil_text(
            msp,
            label,
            ax_tip + vx * 1.9 + sign * ux * 1.4,
            ay_tip + vy * 1.9 + sign * uy * 1.4,
            height=2.35,
            align="MIDDLE_CENTER",
        )


def _draw_mini_rebar_shape(
    msp: Modelspace,
    shape: str,
    cx: float,
    cy: float,
    w: float,
    h: float,
) -> None:
    hw, hh = 0.42 * w, 0.34 * h
    st = (shape or "straight_hook").lower()
    if st == "stirrup_4leg":
        # Outer 2-leg closed stirrup + inner 2-leg closed stirrup + 135-deg corner hooks
        msp.add_lwpolyline(
            [(cx - 0.62 * hw, cy - hh), (cx + 0.62 * hw, cy - hh), (cx + 0.62 * hw, cy + hh), (cx - 0.62 * hw, cy + hh)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        msp.add_lwpolyline(
            [(cx - 0.23 * hw, cy - hh), (cx + 0.23 * hw, cy - hh), (cx + 0.23 * hw, cy + hh), (cx - 0.23 * hw, cy + hh)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
        msp.add_line((cx + 0.62 * hw, cy + hh), (cx + 0.45 * hw, cy + 0.55 * hh), dxfattribs={"layer": "REBAR_STIRRUP"})
        msp.add_line((cx + 0.23 * hw, cy + hh), (cx + 0.08 * hw, cy + 0.55 * hh), dxfattribs={"layer": "REBAR_STIRRUP"})
    elif st == "stirrup_rect":
        msp.add_lwpolyline(
            [(cx - 0.55 * hw, cy - hh), (cx + 0.55 * hw, cy - hh), (cx + 0.55 * hw, cy + hh), (cx - 0.55 * hw, cy + hh)],
            close=True,
            dxfattribs={"layer": "REBAR_STIRRUP"},
        )
    elif st == "bent_up":
        msp.add_lwpolyline(
            [
                (cx - hw, cy + 0.75 * hh),
                (cx - 0.62 * hw, cy + 0.75 * hh),
                (cx - 0.28 * hw, cy - 0.75 * hh),
                (cx + 0.28 * hw, cy - 0.75 * hh),
                (cx + 0.62 * hw, cy + 0.75 * hh),
                (cx + hw, cy + 0.75 * hh),
            ],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
    elif st == "u_hairpin":
        msp.add_lwpolyline(
            [(cx - 0.75 * hw, cy + hh), (cx - 0.75 * hw, cy - hh), (cx + 0.75 * hw, cy - hh), (cx + 0.75 * hw, cy + hh)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )
    elif st == "spiral_hoop":
        msp.add_circle((cx, cy), min(0.45 * hw, hh), dxfattribs={"layer": "REBAR_STIRRUP"})
    else:
        msp.add_lwpolyline(
            [(cx - hw, cy + 0.65 * hh), (cx - hw, cy - 0.35 * hh), (cx + hw, cy - 0.35 * hh), (cx + hw, cy + 0.65 * hh)],
            close=False,
            dxfattribs={"layer": "REBAR"},
        )


def draw_rebar_schedule_table(
    doc: Drawing,
    msp: Modelspace,
    items: Sequence[Mapping[str, Any]],
    *,
    x0: float,
    y0: float,
    width: float = 180.0,
    title: str = "钢筋数量明细表",
    dock_to_title_block: bool = False,
    unit_count_header: str = "每墩件数",
    unit_weight_header: str = "每墩重(kg)",
    summary_prefix: str | None = None,
    bridge_units_count: int = 0,
    bridge_units_noun: str = "座",
) -> dict[str, float]:
    """Render a standard JTG bridge rebar schedule table (with optional multi-member substructure grouping)."""
    rows = list(items)
    has_unit_mult = any(int(it.get("members_per_unit", 0) or 0) > 0 for it in rows)
    hdr_h = 6.2
    title_h = 6.6
    row_h = 5.4
    sum_h = 5.8
    total_h = title_h + hdr_h + len(rows) * row_h + sum_h

    # Outer table border: when fused atop the Title Block (x0=tb_x0, y0=tb_y1, x1=ix1),
    # share the Title Block's top border and the sheet's right inner frame without duplicate strokes.
    if dock_to_title_block:
        msp.add_lwpolyline(
            [(x0, y0), (x0, y0 + total_h), (x0 + width, y0 + total_h)],
            close=False,
            dxfattribs={"layer": "FRAME"},
        )
    else:
        msp.add_lwpolyline(
            [(x0, y0), (x0 + width, y0), (x0 + width, y0 + total_h), (x0, y0 + total_h)],
            close=True,
            dxfattribs={"layer": "FRAME"},
        )
    y_title_bot = y0 + total_h - title_h
    y_hdr_bot = y_title_bot - hdr_h
    msp.add_line((x0, y_title_bot), (x0 + width, y_title_bot), dxfattribs={"layer": "FRAME"})
    msp.add_line((x0, y_hdr_bot), (x0 + width, y_hdr_bot), dxfattribs={"layer": "THIN"})

    add_civil_text(msp, title, x0 + 0.5 * width, y_title_bot + 1.8, height=2.35, align="CENTER")

    if has_unit_mult:
        col_fracs: tuple[float, ...] = (0.122, 0.068, 0.085, 0.110, 0.098, 0.085, 0.104, 0.098, 0.090, 0.140)
        col_headers: tuple[str, ...] = (
            "构件部位",
            "编号",
            "规格(mm)",
            "大样简图",
            "单长(cm)",
            "单件根数",
            "单件长(m)",
            "单件重(kg)",
            unit_count_header,
            unit_weight_header,
        )
        hdr_txt_h = 1.66
        cell_txt_h = 1.70
        shape_col_idx = 3
    else:
        col_fracs = (0.11, 0.13, 0.22, 0.14, 0.10, 0.14, 0.16)
        col_headers = ("编号", "规格(mm)", "大样简图", "单长(cm)", "根数", "总长(m)", "总重(kg)")
        hdr_txt_h = 1.95
        cell_txt_h = 1.90
        shape_col_idx = 2

    col_xs = [x0]
    acc = x0
    for f in col_fracs[:-1]:
        acc += f * width
        col_xs.append(acc)
    col_xs.append(x0 + width)

    y_sum_top = y0 + sum_h
    for cx in col_xs[1:-1]:
        msp.add_line((cx, y_sum_top), (cx, y_title_bot), dxfattribs={"layer": "THIN"})
    msp.add_line((x0, y_sum_top), (x0 + width, y_sum_top), dxfattribs={"layer": "THIN"})

    for c_i, h_txt in enumerate(col_headers):
        mcx = 0.5 * (col_xs[c_i] + col_xs[c_i + 1])
        add_civil_text(msp, h_txt, mcx, y_hdr_bot + 1.8, height=hdr_txt_h, align="CENTER")

    total_kg = 0.0
    substructure_total_kg = 0.0
    serialized_rows: list[dict[str, Any]] = []
    group_spans: list[tuple[int, int, str]] = []
    for r_i, item in enumerate(rows):
        ry_top = y_hdr_bot - r_i * row_h
        ry_bot = ry_top - row_h
        sub_part = str(item.get("sub_part", "")).strip()
        if r_i > 0:
            prev_part = str(rows[r_i - 1].get("sub_part", "")).strip()
            line_x0 = col_xs[1] if (has_unit_mult and sub_part and sub_part == prev_part) else x0
            msp.add_line((line_x0, ry_top), (x0 + width, ry_top), dxfattribs={"layer": "THIN"})
        if has_unit_mult:
            if group_spans and group_spans[-1][2] == sub_part and sub_part:
                group_spans[-1] = (group_spans[-1][0], r_i, sub_part)
            else:
                group_spans.append((r_i, r_i, sub_part))

        bar_id = str(item.get("bar_id", f"N{r_i+1}"))
        grade = str(item.get("grade", "HRB400"))
        dia = float(item.get("dia_mm", 16.0))
        shape = str(item.get("shape", "straight_hook"))
        single_cm = float(item.get("single_length_cm", round(float(item.get("single_length_m", 1.0)) * 100.0, 1)))
        cnt = int(item.get("count", 1))
        tot_m = round((single_cm / 100.0) * cnt, 2)
        unit_kg_m = float(item.get("unit_weight_kg_m", round(0.00617 * dia * dia, 3)))
        wt_kg = float(item.get("total_weight_kg", round(tot_m * unit_kg_m, 1)))
        total_kg += wt_kg
        mult_n = max(1, int(item.get("members_per_unit", 1) or 1))
        mult_lbl = str(item.get("unit_label", f"{mult_n}"))
        sub_wt_kg = round(wt_kg * mult_n, 1)
        substructure_total_kg += sub_wt_kg
        prefix_sym = "φ" if grade.upper() == "HPB300" else "Φ"

        if has_unit_mult:
            cells: tuple[str, ...] = (
                "",
                bar_id,
                f"{prefix_sym}{dia:g}",
                "",
                f"{single_cm:.1f}",
                f"{cnt}",
                f"{tot_m:.2f}",
                f"{wt_kg:.1f}",
                mult_lbl,
                f"{sub_wt_kg:.1f}",
            )
        else:
            cells = (
                bar_id,
                f"{prefix_sym}{dia:g}",
                "",
                f"{single_cm:.1f}",
                f"{cnt}",
                f"{tot_m:.2f}",
                f"{wt_kg:.1f}",
            )

        for c_i, val_s in enumerate(cells):
            if has_unit_mult and c_i == 0:
                continue
            mcx = 0.5 * (col_xs[c_i] + col_xs[c_i + 1])
            if c_i == shape_col_idx:
                _draw_mini_rebar_shape(msp, shape, mcx, 0.5 * (ry_bot + ry_top), col_xs[c_i + 1] - col_xs[c_i], row_h)
            else:
                add_civil_text(msp, val_s, mcx, ry_bot + 1.55, height=cell_txt_h, align="CENTER")

        row_meta: dict[str, Any] = {
            "bar_id": bar_id,
            "grade": grade,
            "dia_mm": dia,
            "single_length_cm": single_cm,
            "count": cnt,
            "total_length_m": tot_m,
            "total_weight_kg": round(wt_kg, 2),
            "calc_basis": str(item.get("calc_basis", "")),
        }
        if has_unit_mult:
            row_meta["sub_part"] = sub_part
            row_meta["members_per_unit"] = mult_n
            row_meta["substructure_weight_kg"] = sub_wt_kg
        serialized_rows.append(row_meta)

    if has_unit_mult:
        part_cx = 0.5 * (col_xs[0] + col_xs[1])
        for g_start, g_end, g_lbl in group_spans:
            if not g_lbl:
                continue
            gy_top = y_hdr_bot - g_start * row_h
            gy_bot = y_hdr_bot - (g_end + 1) * row_h
            add_civil_text(msp, g_lbl, part_cx, 0.5 * (gy_top + gy_bot) - 0.85, height=cell_txt_h, align="CENTER")

        reported_total_kg = round(substructure_total_kg, 1)
        prefix_s = summary_prefix or "单座下部构件钢筋合计"
        if bridge_units_count > 1:
            footer_txt = f"{prefix_s}：{reported_total_kg:.1f} kg（全桥{bridge_units_count}{bridge_units_noun}共 {reported_total_kg * bridge_units_count:.1f} kg）"
        else:
            footer_txt = f"{prefix_s}：{reported_total_kg:.1f} kg"
        add_civil_text(msp, footer_txt, x0 + 0.5 * width, y0 + 1.75, height=1.85, align="CENTER")
    else:
        reported_total_kg = round(total_kg, 1)
        add_civil_text(
            msp,
            f"本构件钢筋合计：{reported_total_kg:.1f} kg",
            x0 + 0.5 * width,
            y0 + 1.65,
            height=2.05,
            align="CENTER",
        )

    attach_sheet_metadata(
        doc,
        msp,
        "rebar_schedule",
        {"title": title, "total_weight_kg": round(reported_total_kg, 2), "items": serialized_rows},
    )
    return {"x0": x0, "y0": y0, "x1": x0 + width, "y1": y0 + total_h, "total_weight_kg": round(reported_total_kg, 2)}


def draw_tendon_schedule_table(
    doc: Drawing,
    msp: Modelspace,
    items: Sequence[Mapping[str, Any]],
    *,
    x0: float,
    y0: float,
    width: float = 180.0,
    title: str = "预应力钢束数量及张拉参数表",
    end_ys_mm: Sequence[float] | None = None,
    mid_ys_mm: Sequence[float] | None = None,
    sigma_con_mpa: float = 1395.0,
    mid_sigma_pe_mpa: float = 1235.0,
    dock_to_title_block: bool = False,
) -> dict[str, float]:
    """Render a visible JTG prestressing tendon & jacking parameter table (optionally docked atop Title Block)."""
    rows = list(items)
    hdr_h = 6.0
    title_h = 6.4
    row_h = 5.2
    sum_h = 5.6
    total_h = title_h + hdr_h + len(rows) * row_h + sum_h

    if dock_to_title_block:
        msp.add_lwpolyline(
            [(x0, y0), (x0, y0 + total_h), (x0 + width, y0 + total_h)],
            close=False,
            dxfattribs={"layer": "FRAME"},
        )
    else:
        msp.add_lwpolyline(
            [(x0, y0), (x0 + width, y0), (x0 + width, y0 + total_h), (x0, y0 + total_h)],
            close=True,
            dxfattribs={"layer": "FRAME"},
        )
    y_title_bot = y0 + total_h - title_h
    y_hdr_bot = y_title_bot - hdr_h
    msp.add_line((x0, y_title_bot), (x0 + width, y_title_bot), dxfattribs={"layer": "FRAME"})
    msp.add_line((x0, y_hdr_bot), (x0 + width, y_hdr_bot), dxfattribs={"layer": "THIN"})
    add_civil_text(msp, title, x0 + 0.5 * width, y_title_bot + 1.7, height=2.4, align="CENTER")

    col_fracs = (0.10, 0.19, 0.14, 0.14, 0.14, 0.14, 0.15)
    col_headers = ("束号", "规格", "单束长(m)", "梁端高", "跨中高", "伸长(mm)", "单梁重(kg)")
    col_xs = [x0]
    acc = x0
    for f in col_fracs[:-1]:
        acc += f * width
        col_xs.append(acc)
    col_xs.append(x0 + width)

    y_sum_top = y0 + sum_h
    for cx in col_xs[1:-1]:
        msp.add_line((cx, y_sum_top), (cx, y_title_bot), dxfattribs={"layer": "THIN"})
    msp.add_line((x0, y_sum_top), (x0 + width, y_sum_top), dxfattribs={"layer": "THIN"})

    for c_i, h_txt in enumerate(col_headers):
        mcx = 0.5 * (col_xs[c_i] + col_xs[c_i + 1])
        add_civil_text(msp, h_txt, mcx, y_hdr_bot + 1.7, height=1.85, align="CENTER")

    tot_girder_kg = 0.0
    for r_i, t_item in enumerate(rows):
        ry_top = y_hdr_bot - r_i * row_h
        ry_bot = ry_top - row_h
        if r_i > 0:
            msp.add_line((x0, ry_top), (x0 + width, ry_top), dxfattribs={"layer": "THIN"})
        tid = str(t_item.get("tendon_id", f"N{r_i+1}"))
        spec_s = str(t_item.get("spec", "Φs15.2-6"))
        len_m = float(t_item.get("single_length_m", 25.0))
        elong = float(t_item.get("theoretical_elongation_mm", 160.0))
        wt_one = float(t_item.get("weight_per_girder_kg", 100.0))
        tot_girder_kg += wt_one
        ey_v = float(end_ys_mm[r_i]) if end_ys_mm and r_i < len(end_ys_mm) else 240.0 + r_i * 300.0
        my_v = float(mid_ys_mm[r_i]) if mid_ys_mm and r_i < len(mid_ys_mm) else 100.0
        cells = (
            tid,
            spec_s,
            f"{len_m:.2f}",
            f"{ey_v:g}",
            f"{my_v:g}",
            f"{elong:.1f}",
            f"{wt_one:.1f}",
        )
        for c_i, vs in enumerate(cells):
            mcx = 0.5 * (col_xs[c_i] + col_xs[c_i + 1])
            add_civil_text(msp, vs, mcx, ry_bot + 1.4, height=1.82, align="CENTER")

    add_civil_text(
        msp,
        f"单梁钢束合计：{tot_girder_kg:.1f} kg | σcon={sigma_con_mpa:g}MPa | 跨中有效预应力={mid_sigma_pe_mpa:.2f}MPa",
        x0 + 0.5 * width,
        y0 + 1.6,
        height=1.80,
        align="CENTER",
    )
    return {"x0": x0, "y0": y0, "x1": x0 + width, "y1": y0 + total_h, "total_weight_kg": round(tot_girder_kg, 2)}


def export_civil_a3_print_pdf_png(
    dxf_path: Any,
    pdf_path: Any,
    png_path: Any,
    *,
    font_map: Mapping[str, Any] | None = None,
    dpi: int = 200,
) -> dict[str, Any]:
    """Export a glyph-verified, true 1:1 physical sheet (e.g. 420×297 mm A3) monochrome B&W PDF + PNG."""
    from pathlib import Path
    import warnings

    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.fonts import fonts
    from ezdxf.fonts.font_manager import get_ttf_font_face
    from matplotlib import pyplot as plt

    from cad_skill_core.rendering.preview import (
        _FONT_RENDER_LOCK,
        _bind_exact_backend_font,
        _restore_font_caches,
        _snapshot_font_caches,
    )
    from cad_skill_core.revision.drawing_set import read_dxf_snapshot
    from cad_skill_core.revision.preview import _VerifiedTextRenderer, _prepare

    src = Path(dxf_path).expanduser().resolve(strict=True)
    out_pdf = Path(pdf_path).expanduser().resolve()
    out_png = Path(png_path).expanduser().resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    raw_doc = read_dxf_snapshot(src.read_bytes())
    extmin = raw_doc.header.get("$EXTMIN", (0.0, 0.0, 0.0))
    extmax = raw_doc.header.get("$EXTMAX", (420.0, 297.0, 0.0))
    x0, y0 = float(extmin[0]), float(extmin[1])
    x1, y1 = float(extmax[0]), float(extmax[1])
    if x1 <= x0 or y1 <= y0 or (x1 - x0) > 5000.0:
        from ezdxf import bbox as ez_bbox

        calc_ext = ez_bbox.extents(raw_doc.modelspace())
        if calc_ext.has_data:
            x0, y0 = float(calc_ext.extmin.x), float(calc_ext.extmin.y)
            x1, y1 = float(calc_ext.extmax.x), float(calc_ext.extmax.y)
        else:
            x0, y0, x1, y1 = 0.0, 0.0, 420.0, 297.0
    sheet_w_mm = max(50.0, x1 - x0)
    sheet_h_mm = max(50.0, y1 - y0)

    with _FONT_RENDER_LOCK:
        prepared_doc, bindings, evidence = _prepare(raw_doc, dict(font_map) if font_map else None)
        snapshots = _snapshot_font_caches(bindings)
        matches = dict(fonts.font_manager._match_cache)
        try:
            fonts.font_manager._match_cache.clear()
            for bnd in bindings:
                _bind_exact_backend_font(bnd)

            fig = plt.figure(figsize=(sheet_w_mm / 25.4, sheet_h_mm / 25.4), dpi=dpi, frameon=False)
            try:
                ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
                ax.set_axis_off()
                cfg = Configuration(
                    color_policy=ColorPolicy.BLACK,
                    background_policy=BackgroundPolicy.WHITE,
                    lineweight_scaling=1.35,
                    min_lineweight=0.40,
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ctx = RenderContext(prepared_doc)
                    for bnd in bindings:
                        if ":inline:" not in bnd.role and bnd.font:
                            ctx.fonts.setdefault(bnd.font.casefold(), get_ttf_font_face(bnd.path))
                    fe = Frontend(ctx, MatplotlibBackend(ax, adjust_figure=False), config=cfg)
                    fe.pipeline.text_engine = _VerifiedTextRenderer(bindings)
                    fe.draw_layout(prepared_doc.modelspace(), finalize=True)
                ax.set_xlim(x0, x1)
                ax.set_ylim(y0, y1)
                ax.set_aspect("equal", adjustable="box")
                fig.savefig(out_pdf, format="pdf", facecolor="white")
                fig.savefig(out_png, format="png", dpi=dpi, facecolor="white")
            finally:
                plt.close(fig)
        finally:
            _restore_font_caches(snapshots, bindings)
            fonts.font_manager._match_cache.clear()
            fonts.font_manager._match_cache.update(matches)

    return {
        "pdf_path": str(out_pdf),
        "png_path": str(out_png),
        "sheet_size_mm": (sheet_w_mm, sheet_h_mm),
        "dpi": dpi,
        "font_evidence": evidence,
    }
