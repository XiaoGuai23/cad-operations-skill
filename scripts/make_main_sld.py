#!/usr/bin/env python3
"""Generate simplified electrical main single-line diagrams as DXF, SVG, and PDF.

Input: JSON spec describing voltage levels, bus topology, feeders, transformers and devices.
Output: editable DXF plus rendered SVG/PDF in an AutoCAD-like black-line style.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

try:
    import ezdxf  # type: ignore
except Exception as exc:  # pragma: no cover
    ezdxf = None
    EZDXF_IMPORT_ERROR = exc
else:
    EZDXF_IMPORT_ERROR = None

try:
    from reportlab.pdfgen import canvas  # type: ignore
    from reportlab.pdfbase import pdfmetrics  # type: ignore
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore
    from reportlab.pdfbase.ttfonts import TTFont  # type: ignore
except Exception as exc:  # pragma: no cover
    canvas = None
    pdfmetrics = None
    UnicodeCIDFont = None
    TTFont = None
    REPORTLAB_IMPORT_ERROR = exc
else:
    REPORTLAB_IMPORT_ERROR = None

MM_TO_PT = 72.0 / 25.4
DOUBLE_BUS_GAP = 6.0


@dataclass
class Entity:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)


class Diagram:
    def __init__(self, width: float, height: float):
        self.width = float(width)
        self.height = float(height)
        self.entities: List[Entity] = []
        self.layers = {
            "BORDER": {"color": 7, "lineweight": 50},
            "BUS": {"color": 7, "lineweight": 30},
            "WIRE": {"color": 7, "lineweight": 13},
            "EQUIP": {"color": 7, "lineweight": 13},
            "TEXT": {"color": 7, "lineweight": 13},
            "TITLE": {"color": 7, "lineweight": 25},
        }

    def line(self, x1: float, y1: float, x2: float, y2: float, layer: str = "WIRE", width: float = 0.25):
        self.entities.append(Entity("line", dict(x1=x1, y1=y1, x2=x2, y2=y2, layer=layer, width=width)))

    def polyline(self, pts: Iterable[Tuple[float, float]], layer: str = "WIRE", width: float = 0.25):
        pts = list(pts)
        for a, b in zip(pts, pts[1:]):
            self.line(a[0], a[1], b[0], b[1], layer=layer, width=width)

    def rect(self, x: float, y: float, w: float, h: float, layer: str = "WIRE", width: float = 0.25):
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], layer=layer, width=width)

    def circle(self, x: float, y: float, r: float, layer: str = "EQUIP", width: float = 0.25):
        self.entities.append(Entity("circle", dict(x=x, y=y, r=r, layer=layer, width=width)))

    def arc(self, x: float, y: float, r: float, start: float, end: float, layer: str = "EQUIP", width: float = 0.25):
        self.entities.append(Entity("arc", dict(x=x, y=y, r=r, start=start, end=end, layer=layer, width=width)))

    def text(self, text: str, x: float, y: float, size: float = 3.5, layer: str = "TEXT", align: str = "center", rotate: float = 0):
        self.entities.append(Entity("text", dict(text=str(text), x=x, y=y, size=size, layer=layer, align=align, rotate=rotate)))


# ----------------------------- primitives -----------------------------

def draw_junction(d: Diagram, x: float, y: float, r: float = 0.75):
    d.circle(x, y, r, "EQUIP", width=0.35)


def draw_ground(d: Diagram, x: float, y: float, s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    d.line(x, y, x, y + dirn * 2.0 * s, "EQUIP")
    d.line(x - 3.0 * s, y + dirn * 2.0 * s, x + 3.0 * s, y + dirn * 2.0 * s, "EQUIP")
    d.line(x - 2.0 * s, y + dirn * 3.2 * s, x + 2.0 * s, y + dirn * 3.2 * s, "EQUIP")
    d.line(x - 1.0 * s, y + dirn * 4.3 * s, x + 1.0 * s, y + dirn * 4.3 * s, "EQUIP")


def draw_disconnector(d: Diagram, x: float, y: float, orientation: str = "vertical", s: float = 1.0):
    # Open knife contact; port-to-port length is 12*s (ports at y +/- 6*s or x +/- 6*s).
    if orientation == "vertical":
        d.line(x, y + 6 * s, x, y + 2.2 * s, "WIRE")
        d.line(x, y - 6 * s, x, y - 2.2 * s, "WIRE")
        d.line(x - 1.8 * s, y + 2.2 * s, x + 1.8 * s, y + 2.2 * s, "EQUIP")
        d.line(x - 1.8 * s, y - 2.2 * s, x + 1.8 * s, y - 2.2 * s, "EQUIP")
        d.line(x, y + 2.2 * s, x + 4.2 * s, y - 1.8 * s, "EQUIP")
    else:
        d.line(x - 6 * s, y, x - 2.2 * s, y, "WIRE")
        d.line(x + 6 * s, y, x + 2.2 * s, y, "WIRE")
        d.line(x - 2.2 * s, y - 1.8 * s, x - 2.2 * s, y + 1.8 * s, "EQUIP")
        d.line(x + 2.2 * s, y - 1.8 * s, x + 2.2 * s, y + 1.8 * s, "EQUIP")
        d.line(x - 2.2 * s, y, x + 1.8 * s, y + 4.2 * s, "EQUIP")


def draw_breaker(d: Diagram, x: float, y: float, orientation: str = "vertical", s: float = 1.0):
    # Simplified circuit breaker: port-to-port length is 11*s (ports at y +/- 5.5*s or x +/- 5.5*s).
    if orientation == "vertical":
        d.line(x, y + 5.5 * s, x, y + 1.8 * s, "WIRE")
        d.line(x, y - 5.5 * s, x, y - 1.8 * s, "WIRE")
        d.line(x - 1.5 * s, y - 1.5 * s, x + 1.5 * s, y + 1.5 * s, "EQUIP")
        d.line(x - 1.5 * s, y + 1.5 * s, x + 1.5 * s, y - 1.5 * s, "EQUIP")
    else:
        d.line(x - 5.5 * s, y, x - 1.8 * s, y, "WIRE")
        d.line(x + 5.5 * s, y, x + 1.8 * s, y, "WIRE")
        d.line(x - 1.5 * s, y - 1.5 * s, x + 1.5 * s, y + 1.5 * s, "EQUIP")
        d.line(x - 1.5 * s, y + 1.5 * s, x + 1.5 * s, y - 1.5 * s, "EQUIP")


def draw_earthing_switch(d: Diagram, x: float, y: float, side: str = "right", s: float = 1.0):
    sign = 1 if side == "right" else -1
    d.line(x, y, x + sign * 5 * s, y, "EQUIP")
    d.line(x + sign * 5 * s, y, x + sign * 8.5 * s, y - 4 * s, "EQUIP")
    d.line(x + sign * 8.5 * s, y - 4 * s, x + sign * 8.5 * s, y - 9 * s, "EQUIP")
    draw_ground(d, x + sign * 8.5 * s, y - 9 * s, s=0.75 * s)


def draw_transformer(d: Diagram, x: float, y: float, label: str = "主变", s: float = 1.0):
    # Terminal ports at y +/- 3.2 * r = y +/- 17.6 * s.
    r = 5.5 * s
    d.circle(x, y + r * 0.85, r, "EQUIP")
    d.circle(x, y - r * 0.85, r, "EQUIP")
    d.circle(x - r * 0.85, y, r, "EQUIP")
    d.line(x, y + 1.85 * r, x, y + 3.2 * r, "WIRE")
    d.line(x, y - 1.85 * r, x, y - 3.2 * r, "WIRE")
    # winding marks
    d.line(x - 2.8 * s, y + r * 0.85, x + 2.8 * s, y + r * 0.85, "EQUIP")
    d.line(x - 2.8 * s, y - r * 0.85, x + 2.8 * s, y - r * 0.85, "EQUIP")
    d.text(label, x + 9.5 * s, y + 1.5 * s, 3.1 * s, align="left")


def draw_bus_tap(d: Diagram, x: float, y_bus: float, is_double: bool, direction: str, s: float = 1.0) -> float:
    """Connect a bay at x to a single or double busbar and return the outgoing y-port."""
    if not is_double:
        draw_junction(d, x, y_bus, r=0.7 * s)
        return y_bus

    y1 = y_bus
    y2 = y_bus + DOUBLE_BUS_GAP
    dx = 2.1 * s
    ds = 0.36 * s
    if direction == "up":
        y_merge = y2 + 7.2 * s
        y_disc = y2 + 3.6 * s
        draw_junction(d, x - dx, y1, r=0.65 * s)
        draw_junction(d, x + dx, y2, r=0.65 * s)
        d.line(x - dx, y1, x - dx, y_disc - 6 * ds, "WIRE")
        d.line(x + dx, y2, x + dx, y_disc - 6 * ds, "WIRE")
        draw_disconnector(d, x - dx, y_disc, "vertical", s=ds)
        draw_disconnector(d, x + dx, y_disc, "vertical", s=ds)
        d.line(x - dx, y_disc + 6 * ds, x - dx, y_merge, "WIRE")
        d.line(x + dx, y_disc + 6 * ds, x + dx, y_merge, "WIRE")
        d.line(x - dx, y_merge, x + dx, y_merge, "WIRE")
        draw_junction(d, x, y_merge, r=0.65 * s)
        return y_merge

    y_merge = y1 - 7.2 * s
    y_disc = y1 - 3.6 * s
    draw_junction(d, x - dx, y1, r=0.65 * s)
    draw_junction(d, x + dx, y2, r=0.65 * s)
    d.line(x - dx, y1, x - dx, y_disc + 6 * ds, "WIRE")
    d.line(x + dx, y2, x + dx, y_disc + 6 * ds, "WIRE")
    draw_disconnector(d, x - dx, y_disc, "vertical", s=ds)
    draw_disconnector(d, x + dx, y_disc, "vertical", s=ds)
    d.line(x - dx, y_disc - 6 * ds, x - dx, y_merge, "WIRE")
    d.line(x + dx, y_disc - 6 * ds, x + dx, y_merge, "WIRE")
    d.line(x - dx, y_merge, x + dx, y_merge, "WIRE")
    draw_junction(d, x, y_merge, r=0.65 * s)
    return y_merge


def draw_pt(d: Diagram, x: float, y_port: float, label: str = "PT", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    y = y_port + dirn * 18 * s
    d.line(x, y_port, x, y + dirn * 2 * s, "WIRE")
    draw_disconnector(d, x, y + dirn * 7 * s, "vertical", s=0.8 * s)
    d.line(x, y + dirn * 12 * s, x, y + dirn * 18 * s, "WIRE")
    d.circle(x - 3.4 * s, y + dirn * 22 * s, 3.4 * s, "EQUIP")
    d.circle(x + 3.4 * s, y + dirn * 22 * s, 3.4 * s, "EQUIP")
    d.circle(x, y + dirn * 26.5 * s, 3.4 * s, "EQUIP")
    d.line(x, y + dirn * 26 * s, x, y + dirn * 33 * s, "WIRE")
    draw_ground(d, x, y + dirn * 33 * s, s=0.75 * s, downward=downward)
    d.text(label, x, y + dirn * 42 * s, 2.9 * s)


def draw_reactor(d: Diagram, x: float, y_port: float, label: str = "电抗器", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    d.line(x, y_port, x, y_port + dirn * 10 * s, "WIRE")
    draw_breaker(d, x, y_port + dirn * 16 * s, "vertical", s=0.75 * s)
    y0 = y_port + dirn * 27 * s
    for row in range(3):
        for col in range(3):
            d.circle(x + (col - 1) * 2.8 * s, y0 + dirn * row * 2.8 * s, 1.4 * s, "EQUIP")
    d.line(x, y_port + dirn * 20.2 * s, x, y0 - dirn * 3 * s, "WIRE")
    d.line(x, y0 + dirn * 9 * s, x, y0 + dirn * 15 * s, "WIRE")
    d.text(label, x, y0 + dirn * 23 * s, 2.9 * s)


def draw_capacitor(d: Diagram, x: float, y_port: float, label: str = "电容器", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    d.line(x, y_port, x, y_port + dirn * 9 * s, "WIRE")
    draw_disconnector(d, x, y_port + dirn * 15 * s, "vertical", s=0.75 * s)
    draw_breaker(d, x, y_port + dirn * 24 * s, "vertical", s=0.75 * s)
    y = y_port + dirn * 35 * s
    d.line(x, y_port + dirn * 28.2 * s, x, y, "WIRE")
    d.line(x - 5.2 * s, y, x + 5.2 * s, y, "EQUIP")
    d.line(x - 3.6 * s, y + dirn * 2.8 * s, x + 3.6 * s, y + dirn * 2.8 * s, "EQUIP")
    d.line(x, y + dirn * 2.8 * s, x, y + dirn * 8 * s, "WIRE")
    draw_ground(d, x, y + dirn * 8 * s, s=0.72 * s, downward=downward)
    d.text(label, x, y + dirn * 18 * s, 2.9 * s)


def draw_vertical_bay(d: Diagram, x: float, y_port: float, label: str, direction: str = "up", s: float = 1.0, with_ground: bool = True):
    up = direction == "up"
    dirn = 1 if up else -1
    end_y = y_port + dirn * 54 * s
    d.line(x, y_port, x, y_port + dirn * 8.5 * s, "WIRE")
    draw_disconnector(d, x, y_port + dirn * 13 * s, "vertical", s=0.75 * s)
    if with_ground:
        draw_earthing_switch(d, x, y_port + dirn * 18 * s, side="right", s=0.65 * s)
    d.line(x, y_port + dirn * 17.5 * s, x, y_port + dirn * 21.8 * s, "WIRE")
    draw_breaker(d, x, y_port + dirn * 26 * s, "vertical", s=0.75 * s)
    d.line(x, y_port + dirn * 30.2 * s, x, y_port + dirn * 34.5 * s, "WIRE")
    draw_disconnector(d, x, y_port + dirn * 39 * s, "vertical", s=0.75 * s)
    d.line(x, y_port + dirn * 43.5 * s, x, end_y, "WIRE")
    # terminal arrow
    if up:
        d.line(x, end_y, x - 2.8 * s, end_y - 6.5 * s, "WIRE")
        d.line(x, end_y, x + 2.8 * s, end_y - 6.5 * s, "WIRE")
        d.text(label, x, end_y + 4.8 * s, 3.1 * s)
    else:
        d.line(x, end_y, x - 2.8 * s, end_y + 6.5 * s, "WIRE")
        d.line(x, end_y, x + 2.8 * s, end_y + 6.5 * s, "WIRE")
        d.text(label, x, end_y - 8.5 * s, 3.1 * s)


def draw_horizontal_feeder(d: Diagram, x_bus: float, y: float, label: str, direction: str = "left", s: float = 1.0):
    left = direction == "left"
    sign = -1 if left else 1
    draw_junction(d, x_bus, y, r=0.7 * s)
    d.line(x_bus, y, x_bus + sign * 17.5 * s, y, "WIRE")
    draw_disconnector(d, x_bus + sign * 22 * s, y, "horizontal", s=0.75 * s)
    d.line(x_bus + sign * 26.5 * s, y, x_bus + sign * 30.2 * s, y, "WIRE")
    draw_breaker(d, x_bus + sign * 34 * s, y, "horizontal", s=0.7 * s)
    d.line(x_bus + sign * 37.8 * s, y, x_bus + sign * 42.5 * s, y, "WIRE")
    draw_disconnector(d, x_bus + sign * 47 * s, y, "horizontal", s=0.75 * s)
    end_x = x_bus + sign * 60 * s
    d.line(x_bus + sign * 51.5 * s, y, end_x, y, "WIRE")
    # outgoing arrow
    d.line(end_x, y, end_x - sign * 6 * s, y + 2.8 * s, "WIRE")
    d.line(end_x, y, end_x - sign * 6 * s, y - 2.8 * s, "WIRE")
    d.text(label, end_x - sign * 6 * s, y + 4.5 * s, 3.3 * s, align="right" if left else "left")


# ----------------------------- drawing layout -----------------------------

def mm(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default


def as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def normalize_level(item: Any, idx: int) -> Dict[str, Any]:
    if isinstance(item, str):
        return {"name": item, "bus": "单母线", "feeders": []}
    if not isinstance(item, dict):
        return {"name": f"电压等级{idx+1}", "bus": "单母线", "feeders": []}
    out = dict(item)
    out.setdefault("name", f"电压等级{idx+1}")
    out.setdefault("bus", "单母线")
    out.setdefault("feeders", [])
    return out


def _is_double_bus(bus: str) -> bool:
    return any(k in bus for k in ["双母", "double", "Double"])


def _is_split_bus(bus: str) -> bool:
    return any(k in bus for k in ["分段", "section", "Section"])


def _compute_level_y_buses(total: int, height: float) -> List[float]:
    if total <= 1:
        return [height * 0.55]
    if total == 2:
        return [height - 95.0, 105.0]
    return [
        height - 74.0,
        height - 152.0,
        max(86.0, 96.0),
    ][:total]


def _level_bus_sections(d: Diagram, level: Dict[str, Any], idx: int, total: int) -> Tuple[float, float, List[Tuple[float, float]]]:
    x1 = 48.0
    # Bottom voltage level leaves clearance for the right-hand title block (x0 = d.width - 148).
    x2 = (d.width - 158.0) if idx == total - 1 else (d.width - 48.0)
    if x2 <= x1 + 60.0:
        x2 = d.width - 48.0
    bus = str(level.get("bus", "单母线"))
    if _is_split_bus(bus):
        gap = 11.0
        mid = (x1 + x2) / 2.0
        return x1, x2, [(x1, mid - gap), (mid + gap, x2)]
    return x1, x2, [(x1, x2)]


def _distribute_positions(sections: List[Tuple[float, float]], count: int) -> Tuple[List[float], float]:
    """Distribute `count` bay x-coordinates across bus `sections` without overlaps."""
    if count <= 0:
        return [], 40.0
    total_span = sum(max(1.0, sx2 - sx1) for sx1, sx2 in sections)
    raw_counts = [max(0, round(count * (sx2 - sx1) / total_span)) for sx1, sx2 in sections]
    while sum(raw_counts) < count:
        idx = min(range(len(sections)), key=lambda k: raw_counts[k] / max(1.0, sections[k][1] - sections[k][0]))
        raw_counts[idx] += 1
    while sum(raw_counts) > count:
        idx = max(range(len(sections)), key=lambda k: raw_counts[k])
        if raw_counts[idx] > 0:
            raw_counts[idx] -= 1

    positions: List[float] = []
    min_pitch = 40.0
    for (sx1, sx2), n_sec in zip(sections, raw_counts):
        if n_sec <= 0:
            continue
        pitch = (sx2 - sx1) / (n_sec + 1)
        min_pitch = min(min_pitch, pitch)
        for j in range(n_sec):
            positions.append(sx1 + (j + 1) * pitch)
    return positions, min_pitch


def draw_title_block(d: Diagram, spec: Dict[str, Any]):
    tb = spec.get("title_block") or {}
    title = spec.get("title") or tb.get("title") or "电气主接线图"
    x0 = d.width - 148
    y0 = 18
    w = 130
    h = 52
    d.rect(x0, y0, w, h, "TITLE", width=0.8)
    d.line(x0, y0 + 18, x0 + w, y0 + 18, "TITLE", width=0.3)
    d.line(x0, y0 + 32, x0 + w, y0 + 32, "TITLE", width=0.3)
    left_w = 70
    d.line(x0 + left_w, y0, x0 + left_w, y0 + 32, "TITLE", width=0.3)
    for yy in [8, 16, 24]:
        d.line(x0, y0 + yy, x0 + left_w, y0 + yy, "TITLE", width=0.25)
    d.line(x0 + 12, y0, x0 + 12, y0 + 32, "TITLE", width=0.25)
    d.line(x0 + 48, y0, x0 + 48, y0 + 32, "TITLE", width=0.25)
    d.line(x0 + 60, y0, x0 + 60, y0 + 32, "TITLE", width=0.25)
    d.text(title, x0 + left_w + (w - left_w) / 2, y0 + 24.5, 7.0, layer="TEXT")
    d.line(x0 + left_w + 14, y0, x0 + left_w + 14, y0 + 18, "TITLE", width=0.25)
    d.text("图号", x0 + left_w + 7, y0 + 9, 4.0, layer="TEXT")
    d.text(str(tb.get("图号", "")), x0 + left_w + 34, y0 + 9, 3.2, layer="TEXT")

    rows = [("专业", "学号"), ("班级", "制图"), ("导师", "比例"), ("校核", "日期")]
    y_centers = [28, 20, 12, 4]
    for (a, b), yy in zip(rows, y_centers):
        d.text(a, x0 + 6, y0 + yy, 4.0, layer="TEXT")
        d.text(str(tb.get(a, "")), x0 + 27, y0 + yy, 3.2, layer="TEXT")
        d.text(b, x0 + 54, y0 + yy, 4.0, layer="TEXT")
        d.text(str(tb.get(b, "")), x0 + 64, y0 + yy, 3.2, layer="TEXT", align="left")


def draw_frames(d: Diagram):
    d.rect(5, 5, d.width - 10, d.height - 10, "BORDER", width=0.25)
    d.rect(18, 18, d.width - 36, d.height - 36, "BORDER", width=1.2)


def _draw_transformer_link(
    d: Diagram,
    x: float,
    label: str,
    y_upper_bus: float,
    upper_is_double: bool,
    y_lower_bus: float,
    lower_is_double: bool,
):
    """Connect main transformer from upper voltage bus all the way to lower voltage bus."""
    y_top = draw_bus_tap(d, x, y_upper_bus, upper_is_double, direction="down", s=0.8)
    y_bot = draw_bus_tap(d, x, y_lower_bus, lower_is_double, direction="up", s=0.8)
    span = y_top - y_bot
    if span <= 24.0:
        d.line(x, y_top, x, y_bot, "WIRE")
        draw_transformer(d, x, (y_top + y_bot) / 2.0, label, s=0.45)
        return

    s = min(0.82, max(0.42, span / 128.0))
    y_mid = (y_top + y_bot) / 2.0
    t_half = 17.6 * s  # transformer wire ports at y_mid +/- 17.6*s

    # Upper HV switchgear (disconnector + breaker)
    hv_disc_y = y_top - 10.0 * s
    hv_brk_y = y_mid + t_half + 10.0 * s
    d.line(x, y_top, x, hv_disc_y + 6.0 * (0.72 * s), "WIRE")
    draw_disconnector(d, x, hv_disc_y, "vertical", s=0.72 * s)
    d.line(x, hv_disc_y - 6.0 * (0.72 * s), x, hv_brk_y + 5.5 * (0.72 * s), "WIRE")
    draw_breaker(d, x, hv_brk_y, "vertical", s=0.72 * s)
    d.line(x, hv_brk_y - 5.5 * (0.72 * s), x, y_mid + t_half, "WIRE")

    # Main transformer symbol
    draw_transformer(d, x, y_mid, label, s=s)

    # Lower LV switchgear (breaker + disconnector) down to lower busbar
    lv_brk_y = y_mid - t_half - 10.0 * s
    lv_disc_y = y_bot + 10.0 * s
    d.line(x, y_mid - t_half, x, lv_brk_y + 5.5 * (0.72 * s), "WIRE")
    draw_breaker(d, x, lv_brk_y, "vertical", s=0.72 * s)
    d.line(x, lv_brk_y - 5.5 * (0.72 * s), x, lv_disc_y + 6.0 * (0.72 * s), "WIRE")
    draw_disconnector(d, x, lv_disc_y, "vertical", s=0.72 * s)
    d.line(x, lv_disc_y - 6.0 * (0.72 * s), x, y_bot, "WIRE")


def draw_level_bus(
    d: Diagram,
    level: Dict[str, Any],
    idx: int,
    total: int,
    spec: Dict[str, Any],
    *,
    y_buses: Optional[List[float]] = None,
    next_level: Optional[Dict[str, Any]] = None,
):
    name = str(level.get("name", f"L{idx+1}"))
    bus = str(level.get("bus", "单母线"))
    conductor = str(level.get("conductor", "")).strip()
    bus_title = f"{name} {conductor}".strip()
    feeders = [str(x) for x in as_list(level.get("feeders"))]
    transformers = [str(x) for x in as_list(level.get("transformers"))]

    computed_ys = y_buses or _compute_level_y_buses(total, d.height)
    y_bus = computed_ys[idx]
    is_double = _is_double_bus(bus)
    is_split = _is_split_bus(bus)
    x1, x2, sections = _level_bus_sections(d, level, idx, total)

    # Draw single or double busbars (with optional section coupler / bus-tie)
    bus_ys = [y_bus, y_bus + DOUBLE_BUS_GAP] if is_double else [y_bus]
    for sec_idx, (sx1, sx2) in enumerate(sections):
        for bar_idx, yb in enumerate(bus_ys):
            d.line(sx1, yb, sx2, yb, "BUS", width=0.55)
            if is_double:
                roman = "I母" if bar_idx == 0 else "II母"
                sec_tag = f"{sec_idx + 1}段" if is_split else ""
                d.text(f"{name} {roman}{sec_tag}", sx1 - 2.0, yb, 2.7, align="right")
        label_y = bus_ys[-1] + 4.2
        sec_suffix = f" ({'I' if sec_idx == 0 else 'II'}段)" if is_split and not is_double else ""
        d.text(f"{bus_title}{sec_suffix}", (sx1 + sx2) / 2.0, label_y, 3.3)

    if is_split and len(sections) == 2:
        left_end = sections[0][1]
        right_start = sections[1][0]
        mid = (left_end + right_start) / 2.0
        coupler_y = y_bus - 18.0
        draw_junction(d, left_end, y_bus, r=0.7)
        draw_junction(d, right_start, y_bus, r=0.7)
        d.line(left_end, y_bus, left_end, coupler_y, "WIRE")
        d.line(right_start, y_bus, right_start, coupler_y, "WIRE")
        d.line(left_end, coupler_y, mid - 4.95, coupler_y, "WIRE")
        d.line(mid + 4.95, coupler_y, right_start, coupler_y, "WIRE")
        draw_breaker(d, mid, coupler_y, "horizontal", s=0.9)

    if is_double:
        # Bus-tie breaker (母联) at the right end of section 0
        tie_x = sections[0][1] - 5.0
        draw_junction(d, tie_x, y_bus, r=0.65)
        draw_junction(d, tie_x, y_bus + DOUBLE_BUS_GAP, r=0.65)
        draw_breaker(d, tie_x, y_bus + DOUBLE_BUS_GAP / 2.0, "vertical", s=DOUBLE_BUS_GAP / 11.0)
        d.text("母联", tie_x + 4.5, y_bus + DOUBLE_BUS_GAP / 2.0, 2.5, align="left")

    orientation = str(level.get("orientation", ""))
    if not orientation:
        orientation = "up" if idx == 0 else ("down" if idx == total - 1 else "up")

    pt_n = int(level.get("pt", 0) or 0)
    reac_n = int(level.get("reactors", 0) or 0)
    cap_n = int(level.get("capacitors", 0) or 0)
    device_bays: List[Tuple[str, str]] = []
    device_bays += [("pt", f"PT{i+1 if pt_n > 1 else ''}".strip()) for i in range(pt_n)]
    device_bays += [("reactor", f"电抗器{i+1 if reac_n > 1 else ''}") for i in range(reac_n)]
    device_bays += [("capacitor", f"电容器{i+1 if cap_n > 1 else ''}") for i in range(cap_n)]

    if orientation in ["left", "right"]:
        x_bus = x1 if orientation == "left" else x2
        d.line(x_bus, y_bus - 55, x_bus, y_bus + 55, "BUS", width=0.55)
        step = 14.0
        top_y = y_bus + (len(feeders) - 1) * step / 2.0
        for i, lab in enumerate(feeders):
            draw_horizontal_feeder(d, x_bus, top_y - i * step, lab, direction=orientation, s=0.85)
        bays_to_place = device_bays
        bay_direction = "down"
    else:
        # Unify feeders and shunt/PT devices along the same bus sections to prevent overlaps
        bays_to_place = [("feeder", lab) for lab in feeders] + device_bays
        bay_direction = orientation

    # Usable section spans (leave room for double-bus tie if present)
    usable_sections = [
        (sx1 + 4.0, sx2 - (10.0 if is_double and sec_i == 0 else 4.0))
        for sec_i, (sx1, sx2) in enumerate(sections)
    ]
    positions, min_pitch = _distribute_positions(usable_sections, len(bays_to_place))
    bay_scale = min(0.78, max(0.50, min_pitch / 21.0))
    downward_bay = bay_direction == "down"

    for x_pos, (kind, lab) in zip(positions, bays_to_place):
        y_port = draw_bus_tap(d, x_pos, y_bus, is_double, direction=bay_direction, s=bay_scale)
        if kind == "feeder":
            draw_vertical_bay(d, x_pos, y_port, lab, direction=bay_direction, s=bay_scale, with_ground=True)
        elif kind == "pt":
            draw_pt(d, x_pos, y_port, lab, s=bay_scale, downward=downward_bay)
        elif kind == "reactor":
            draw_reactor(d, x_pos, y_port, lab, s=bay_scale, downward=downward_bay)
        else:
            draw_capacitor(d, x_pos, y_port, lab, s=bay_scale, downward=downward_bay)

    # Connect main transformers continuously from this bus down to the next lower voltage bus
    if transformers and idx < total - 1:
        next_y_bus = computed_ys[idx + 1]
        next_lvl = next_level or {}
        next_is_double = _is_double_bus(str(next_lvl.get("bus", "单母线")))
        _, _, lower_sections = _level_bus_sections(d, next_lvl, idx + 1, total)

        # Intersect upper & lower section spans so every transformer lands solidly on both buses
        candidate_spans: List[Tuple[float, float]] = []
        for lx1, lx2 in lower_sections:
            for ux1, ux2 in sections:
                cx1 = max(lx1 + 8.0, ux1 + 8.0)
                cx2 = min(lx2 - 8.0, ux2 - 8.0)
                if cx2 > cx1 + 6.0:
                    candidate_spans.append((cx1, cx2))
        if not candidate_spans:
            candidate_spans = [(max(x1, lower_sections[0][0]) + 8.0, min(x2, lower_sections[-1][1]) - 8.0)]

        tx_positions, _ = _distribute_positions(candidate_spans, len(transformers))
        # Avoid exact x-coincidence with upward bays on the same bus if any
        for tx_raw, lab in zip(tx_positions, transformers):
            tx = tx_raw
            if any(abs(tx - px) < 4.5 for px in positions):
                tx += 5.5
            _draw_transformer_link(
                d,
                tx,
                lab,
                y_upper_bus=y_bus,
                upper_is_double=is_double,
                y_lower_bus=next_y_bus,
                lower_is_double=next_is_double,
            )


def draw_diagram(spec: Dict[str, Any]) -> Diagram:
    sheet = spec.get("sheet") or {}
    width = mm(sheet.get("width_mm", spec.get("width_mm")), 440.0)
    height = mm(sheet.get("height_mm", spec.get("height_mm")), 317.0)
    d = Diagram(width, height)
    draw_frames(d)
    levels = [normalize_level(x, i) for i, x in enumerate(as_list(spec.get("voltage_levels")))]
    if not levels:
        levels = [
            {"name": "110kV", "bus": "单母线", "feeders": ["出线1回", "出线2回", "出线3回"], "transformers": ["1#主变"]},
            {"name": "35kV", "bus": "单母分段", "feeders": ["出线1", "出线2", "出线3", "出线4"], "pt": 2, "reactors": 1, "capacitors": 2, "conductor": "TMY-125×10"},
        ]
    active_levels = levels[:3]
    total = len(active_levels)
    y_buses = _compute_level_y_buses(total, d.height)
    for idx, level in enumerate(active_levels):
        next_level = active_levels[idx + 1] if idx + 1 < total else None
        draw_level_bus(d, level, idx, total, spec, y_buses=y_buses, next_level=next_level)

    left = spec.get("left_vertical_bus")
    if isinstance(left, dict):
        x = mm(left.get("x"), 100)
        y1 = mm(left.get("y1"), 55)
        y2 = mm(left.get("y2"), d.height - 105)
        d.line(x, y1, x, y2, "BUS", width=0.55)
        feeders = [str(v) for v in as_list(left.get("feeders"))]
        if feeders:
            top = y2 - 45
            step = min(14, max(8, (y2 - y1 - 70) / max(1, len(feeders) - 1)))
            for i, lab in enumerate(feeders):
                draw_horizontal_feeder(d, x, top - i * step, lab, direction="left", s=0.8)
        d.text(str(left.get("label", "")), x + 4, y2 + 5, 3.4, align="left")
    draw_title_block(d, spec)
    return d


# ----------------------------- exporters -----------------------------

def export_dxf(d: Diagram, path: Path):
    if ezdxf is None:
        raise RuntimeError(f"ezdxf is required for DXF output: {EZDXF_IMPORT_ERROR}")
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimeters
    doc.header["$EXTMIN"] = (0.0, 0.0, 0.0)
    doc.header["$EXTMAX"] = (d.width, d.height, 0.0)
    for name, cfg in d.layers.items():
        if name not in doc.layers:
            doc.layers.add(name, color=cfg["color"], lineweight=cfg["lineweight"])
    if "CN" not in doc.styles:
        try:
            doc.styles.new("CN", dxfattribs={"font": "simsun.ttc"})
        except Exception:
            pass
    msp = doc.modelspace()
    for e in d.entities:
        a = e.data
        layer = a.get("layer", "0")
        if e.kind == "line":
            msp.add_line((a["x1"], a["y1"]), (a["x2"], a["y2"]), dxfattribs={"layer": layer})
        elif e.kind == "circle":
            msp.add_circle((a["x"], a["y"]), a["r"], dxfattribs={"layer": layer})
        elif e.kind == "arc":
            msp.add_arc((a["x"], a["y"]), a["r"], a["start"], a["end"], dxfattribs={"layer": layer})
        elif e.kind == "text":
            align = a.get("align", "center")
            halign = {"left": 0, "center": 1, "right": 2}.get(align, 1)
            txt = msp.add_text(a["text"], dxfattribs={"layer": layer, "height": a["size"], "style": "CN", "rotation": a.get("rotate", 0)})
            try:
                if align == "left":
                    txt.set_placement((a["x"], a["y"]), align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)
                elif align == "right":
                    txt.set_placement((a["x"], a["y"]), align=ezdxf.enums.TextEntityAlignment.MIDDLE_RIGHT)
                else:
                    txt.set_placement((a["x"], a["y"]), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
            except Exception:
                txt.dxf.insert = (a["x"], a["y"])
                txt.dxf.halign = halign
                txt.dxf.valign = 2
                txt.dxf.align_point = (a["x"], a["y"])
    doc.saveas(path)
    reopened = ezdxf.readfile(path)
    auditor = reopened.audit()
    if auditor.has_errors:
        raise RuntimeError(f"Generated SLD DXF failed audit with {len(auditor.errors)} error(s)")


def svg_y(d: Diagram, y: float) -> float:
    return d.height - y


def export_svg(d: Diagram, path: Path):
    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{d.width}mm" height="{d.height}mm" viewBox="0 0 {d.width} {d.height}">')
    parts.append('<rect width="100%" height="100%" fill="white"/>')
    parts.append('<g stroke="black" fill="none" stroke-linecap="square" stroke-linejoin="miter">')
    for e in d.entities:
        a = e.data
        sw = a.get("width", 0.25)
        if e.kind == "line":
            parts.append(f'<line x1="{a["x1"]:.3f}" y1="{svg_y(d,a["y1"]):.3f}" x2="{a["x2"]:.3f}" y2="{svg_y(d,a["y2"]):.3f}" stroke-width="{sw}"/>')
        elif e.kind == "circle":
            parts.append(f'<circle cx="{a["x"]:.3f}" cy="{svg_y(d,a["y"]):.3f}" r="{a["r"]:.3f}" stroke-width="{sw}"/>')
        elif e.kind == "arc":
            start = math.radians(a["start"])
            end = math.radians(a["end"])
            if end < start:
                end += math.tau
            pts = []
            for i in range(25):
                t = start + (end - start) * i / 24
                pts.append((a["x"] + a["r"] * math.cos(t), svg_y(d, a["y"] + a["r"] * math.sin(t))))
            ps = " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
            parts.append(f'<polyline points="{ps}" stroke-width="{sw}"/>')
    parts.append('</g>')
    parts.append('<g fill="black" font-family="SimSun, STSong, Noto Serif CJK SC, serif">')
    for e in d.entities:
        if e.kind != "text":
            continue
        a = e.data
        anchor = {"left": "start", "center": "middle", "right": "end"}.get(a.get("align", "center"), "middle")
        y = svg_y(d, a["y"])
        rot = a.get("rotate", 0)
        transform = f' transform="rotate({-rot:.3f} {a["x"]:.3f} {y:.3f})"' if rot else ""
        parts.append(f'<text x="{a["x"]:.3f}" y="{y:.3f}" font-size="{a["size"]:.3f}" text-anchor="{anchor}" dominant-baseline="middle"{transform}>{escape(a["text"])}</text>')
    parts.append('</g></svg>')
    path.write_text("\n".join(parts), encoding="utf-8")


def choose_pdf_font() -> str:
    """Return a Chinese-capable ReportLab font, embedding a local TTF/TTC when available."""
    candidates = [
        ("NotoSansSC", r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
        ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
        ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
        ("DengXian", r"C:\Windows\Fonts\Deng.ttf"),
        ("ArialUnicode", r"C:\Windows\Fonts\arialuni.ttf"),
    ]
    if TTFont is not None:
        for name, font_path in candidates:
            if Path(font_path).exists():
                try:
                    pdfmetrics.registerFont(TTFont(name, font_path))
                    return name
                except Exception:
                    continue
    if UnicodeCIDFont is not None:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            return "STSong-Light"
        except Exception:
            pass
    return "Helvetica"


def export_pdf(d: Diagram, path: Path):
    if canvas is None or pdfmetrics is None:
        raise RuntimeError(f"reportlab is required for PDF output: {REPORTLAB_IMPORT_ERROR}")
    font_name = choose_pdf_font()
    c = canvas.Canvas(str(path), pagesize=(d.width * MM_TO_PT, d.height * MM_TO_PT))
    c.setTitle("电气主接线图")
    for e in d.entities:
        a = e.data
        sw = a.get("width", 0.25) * MM_TO_PT
        c.setLineWidth(sw)
        if e.kind == "line":
            c.line(a["x1"] * MM_TO_PT, a["y1"] * MM_TO_PT, a["x2"] * MM_TO_PT, a["y2"] * MM_TO_PT)
        elif e.kind == "circle":
            c.circle(a["x"] * MM_TO_PT, a["y"] * MM_TO_PT, a["r"] * MM_TO_PT, stroke=1, fill=0)
        elif e.kind == "arc":
            c.arc((a["x"] - a["r"]) * MM_TO_PT, (a["y"] - a["r"]) * MM_TO_PT, (a["x"] + a["r"]) * MM_TO_PT, (a["y"] + a["r"]) * MM_TO_PT, startAng=a["start"], extent=a["end"] - a["start"])
        elif e.kind == "text":
            txt = a["text"]
            size_pt = a["size"] * MM_TO_PT
            c.setFont(font_name, size_pt)
            x = a["x"] * MM_TO_PT
            y = a["y"] * MM_TO_PT - size_pt * 0.35
            align = a.get("align", "center")
            rot = a.get("rotate", 0)
            c.saveState()
            c.translate(x, y)
            if rot:
                c.rotate(rot)
            if align == "left":
                c.drawString(0, 0, txt)
            elif align == "right":
                c.drawRightString(0, 0, txt)
            else:
                c.drawCentredString(0, 0, txt)
            c.restoreState()
    c.showPage()
    c.save()


def default_output_name(spec: Dict[str, Any]) -> str:
    title = str(spec.get("file_stem") or spec.get("title") or "main_sld")
    safe = "".join(ch if ch.isalnum() or ch in "-_#" else "_" for ch in title).strip("_")
    return safe or "main_sld"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate electrical main single-line diagram CAD/PDF files.")
    ap.add_argument("spec", help="JSON specification file")
    ap.add_argument("--outdir", default="output", help="output directory")
    ap.add_argument("--stem", default=None, help="output file stem")
    ap.add_argument("--no-dxf", action="store_true", help="skip DXF output")
    ap.add_argument("--no-pdf", action="store_true", help="skip PDF output")
    ap.add_argument("--no-svg", action="store_true", help="skip SVG output")
    args = ap.parse_args(argv)

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    d = draw_diagram(spec)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or default_output_name(spec)
    written = []
    if not args.no_dxf:
        p = outdir / f"{stem}.dxf"
        export_dxf(d, p)
        written.append(p)
    if not args.no_svg:
        p = outdir / f"{stem}.svg"
        export_svg(d, p)
        written.append(p)
    if not args.no_pdf:
        p = outdir / f"{stem}.pdf"
        export_pdf(d, p)
        written.append(p)
    print(json.dumps({"written": [str(p) for p in written], "width_mm": d.width, "height_mm": d.height, "entities": len(d.entities)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
