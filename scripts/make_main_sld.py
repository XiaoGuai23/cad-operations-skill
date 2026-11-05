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

def draw_ground(d: Diagram, x: float, y: float, s: float = 1.0):
    d.line(x, y, x, y - 2.0 * s, "EQUIP")
    d.line(x - 3.0 * s, y - 2.0 * s, x + 3.0 * s, y - 2.0 * s, "EQUIP")
    d.line(x - 2.0 * s, y - 3.2 * s, x + 2.0 * s, y - 3.2 * s, "EQUIP")
    d.line(x - 1.0 * s, y - 4.3 * s, x + 1.0 * s, y - 4.3 * s, "EQUIP")


def draw_disconnector(d: Diagram, x: float, y: float, orientation: str = "vertical", s: float = 1.0):
    # Open knife contact; port-to-port length is about 12*s.
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
    # Simplified circuit breaker: a small X/contact mark in series.
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
    # Tap from a vertical conductor to an earthing switch and ground.
    sign = 1 if side == "right" else -1
    d.line(x, y, x + sign * 5 * s, y, "EQUIP")
    d.line(x + sign * 5 * s, y, x + sign * 8.5 * s, y - 4 * s, "EQUIP")
    d.line(x + sign * 8.5 * s, y - 4 * s, x + sign * 8.5 * s, y - 9 * s, "EQUIP")
    draw_ground(d, x + sign * 8.5 * s, y - 9 * s, s=0.75 * s)


def draw_transformer(d: Diagram, x: float, y: float, label: str = "主变", s: float = 1.0):
    r = 5.5 * s
    d.circle(x, y + r * 0.85, r, "EQUIP")
    d.circle(x, y - r * 0.85, r, "EQUIP")
    d.circle(x - r * 0.85, y, r, "EQUIP")
    d.line(x, y + 2 * r, x, y + 3.2 * r, "WIRE")
    d.line(x, y - 2 * r, x, y - 3.2 * r, "WIRE")
    # winding marks
    d.line(x - 2.8 * s, y + r * 0.85, x + 2.8 * s, y + r * 0.85, "EQUIP")
    d.line(x - 2.8 * s, y - r * 0.85, x + 2.8 * s, y - r * 0.85, "EQUIP")
    d.text(label, x + 11 * s, y + 2 * s, 3.2 * s, align="left")


def draw_pt(d: Diagram, x: float, y_bus: float, label: str = "PT", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    y = y_bus + dirn * 18 * s
    d.line(x, y_bus, x, y + dirn * 2 * s, "WIRE")
    draw_disconnector(d, x, y + dirn * 7 * s, "vertical", s=0.8 * s)
    d.line(x, y + dirn * 12 * s, x, y + dirn * 18 * s, "WIRE")
    d.circle(x - 4 * s, y + dirn * 23 * s, 4 * s, "EQUIP")
    d.circle(x + 4 * s, y + dirn * 23 * s, 4 * s, "EQUIP")
    d.circle(x, y + dirn * 28 * s, 4 * s, "EQUIP")
    d.line(x, y + dirn * 27 * s, x, y + dirn * 34 * s, "WIRE")
    draw_ground(d, x, y + dirn * 34 * s, s=0.8 * s)
    d.text(label, x, y + dirn * 43 * s, 3.0 * s)


def draw_reactor(d: Diagram, x: float, y_bus: float, label: str = "电抗器", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    d.line(x, y_bus, x, y_bus + dirn * 10 * s, "WIRE")
    draw_breaker(d, x, y_bus + dirn * 16 * s, "vertical", s=0.75 * s)
    y0 = y_bus + dirn * 28 * s
    # coil-like loops as repeated small circles around conductor
    for row in range(3):
        for col in range(3):
            d.circle(x + (col - 1) * 3.2 * s, y0 + dirn * row * 3.2 * s, 1.6 * s, "EQUIP")
    d.line(x, y_bus + dirn * 21 * s, x, y0 - dirn * 4 * s, "WIRE")
    d.line(x, y0 + dirn * 10 * s, x, y0 + dirn * 16 * s, "WIRE")
    d.text(label, x, y0 + dirn * 25 * s, 3.0 * s)


def draw_capacitor(d: Diagram, x: float, y_bus: float, label: str = "电容器", s: float = 1.0, downward: bool = True):
    dirn = -1 if downward else 1
    d.line(x, y_bus, x, y_bus + dirn * 9 * s, "WIRE")
    draw_disconnector(d, x, y_bus + dirn * 15 * s, "vertical", s=0.75 * s)
    draw_breaker(d, x, y_bus + dirn * 24 * s, "vertical", s=0.75 * s)
    y = y_bus + dirn * 36 * s
    d.line(x, y_bus + dirn * 29 * s, x, y - dirn * 4 * s, "WIRE")
    d.line(x - 6 * s, y, x + 6 * s, y, "EQUIP")
    d.line(x - 4 * s, y + dirn * 3 * s, x + 4 * s, y + dirn * 3 * s, "EQUIP")
    d.line(x, y + dirn * 3 * s, x, y + dirn * 8 * s, "WIRE")
    draw_ground(d, x, y + dirn * 8 * s, s=0.75 * s)
    d.text(label, x, y + dirn * 19 * s, 3.0 * s)


def draw_vertical_bay(d: Diagram, x: float, y_bus: float, label: str, direction: str = "up", s: float = 1.0, with_ground: bool = True):
    up = direction == "up"
    dirn = 1 if up else -1
    end_y = y_bus + dirn * 55 * s
    d.line(x, y_bus, x, y_bus + dirn * 7 * s, "WIRE")
    draw_disconnector(d, x, y_bus + dirn * 13 * s, "vertical", s=0.75 * s)
    if with_ground:
        draw_earthing_switch(d, x, y_bus + dirn * 18 * s, side="right", s=0.7 * s)
    draw_breaker(d, x, y_bus + dirn * 26 * s, "vertical", s=0.75 * s)
    draw_disconnector(d, x, y_bus + dirn * 39 * s, "vertical", s=0.75 * s)
    d.line(x, y_bus + dirn * 45 * s, x, end_y, "WIRE")
    # terminal arrow
    if up:
        d.line(x, end_y, x - 3 * s, end_y - 7 * s, "WIRE")
        d.line(x, end_y, x + 3 * s, end_y - 7 * s, "WIRE")
        d.text(label, x, end_y + 5 * s, 3.4 * s)
    else:
        d.line(x, end_y, x - 3 * s, end_y + 7 * s, "WIRE")
        d.line(x, end_y, x + 3 * s, end_y + 7 * s, "WIRE")
        d.text(label, x, end_y - 10 * s, 3.4 * s)


def draw_horizontal_feeder(d: Diagram, x_bus: float, y: float, label: str, direction: str = "left", s: float = 1.0):
    left = direction == "left"
    sign = -1 if left else 1
    d.line(x_bus, y, x_bus + sign * 12 * s, y, "WIRE")
    draw_disconnector(d, x_bus + sign * 22 * s, y, "horizontal", s=0.75 * s)
    draw_breaker(d, x_bus + sign * 34 * s, y, "horizontal", s=0.7 * s)
    draw_disconnector(d, x_bus + sign * 47 * s, y, "horizontal", s=0.75 * s)
    end_x = x_bus + sign * 60 * s
    d.line(x_bus + sign * 53 * s, y, end_x, y, "WIRE")
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


def draw_title_block(d: Diagram, spec: Dict[str, Any]):
    tb = spec.get("title_block") or {}
    title = spec.get("title") or tb.get("title") or "电气主接线图"
    x0 = d.width - 148
    y0 = 18
    w = 130
    h = 52
    d.rect(x0, y0, w, h, "TITLE", width=0.8)
    # top empty project field and title field
    d.line(x0, y0 + 18, x0 + w, y0 + 18, "TITLE", width=0.3)
    d.line(x0, y0 + 32, x0 + w, y0 + 32, "TITLE", width=0.3)
    left_w = 70
    d.line(x0 + left_w, y0, x0 + left_w, y0 + 32, "TITLE", width=0.3)
    # left table rows
    for yy in [8, 16, 24]:
        d.line(x0, y0 + yy, x0 + left_w, y0 + yy, "TITLE", width=0.25)
    d.line(x0 + 12, y0, x0 + 12, y0 + 32, "TITLE", width=0.25)
    d.line(x0 + 48, y0, x0 + 48, y0 + 32, "TITLE", width=0.25)
    d.line(x0 + 60, y0, x0 + 60, y0 + 32, "TITLE", width=0.25)
    # right title and figure number
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
    # outer paper margin and heavy drawing frame, similar to AutoCAD plot style.
    d.rect(5, 5, d.width - 10, d.height - 10, "BORDER", width=0.25)
    d.rect(18, 18, d.width - 36, d.height - 36, "BORDER", width=1.2)


def draw_level_bus(d: Diagram, level: Dict[str, Any], idx: int, total: int, spec: Dict[str, Any]):
    margin = 40
    title_keepout = 125 if idx == total - 1 else 30
    content_w = d.width - 2 * margin - (title_keepout if idx == total - 1 else 0)
    name = str(level.get("name", f"L{idx+1}"))
    bus = str(level.get("bus", "单母线"))
    feeders = [str(x) for x in as_list(level.get("feeders"))]
    transformers = [str(x) for x in as_list(level.get("transformers"))]
    y_positions = [d.height - 76, d.height - 145, 102]
    if total == 1:
        y_bus = d.height * 0.55
    elif total == 2:
        y_bus = [d.height - 95, 105][idx]
    else:
        y_bus = y_positions[idx] if idx < len(y_positions) else max(80, d.height - 75 - idx * 62)

    is_split = any(k in bus for k in ["分段", "section", "Section"])
    x1 = margin + 25
    x2 = margin + content_w - 5
    if is_split:
        gap = 12
        mid = (x1 + x2) / 2
        d.line(x1, y_bus, mid - gap, y_bus, "BUS", width=0.55)
        d.line(mid + gap, y_bus, x2, y_bus, "BUS", width=0.55)
        d.text(f"{name} {level.get('conductor', '')}".strip(), (x1 + mid - gap) / 2, y_bus + 4.5, 3.4)
        d.text(f"{name} {level.get('conductor', '')}".strip(), (mid + gap + x2) / 2, y_bus + 4.5, 3.4)
        # bus coupler/section bay below bus
        d.line(mid - gap, y_bus, mid - gap, y_bus - 28, "WIRE")
        d.line(mid + gap, y_bus, mid + gap, y_bus - 28, "WIRE")
        d.line(mid - gap, y_bus - 28, mid + gap, y_bus - 28, "WIRE")
        draw_breaker(d, mid, y_bus - 28, "horizontal", s=0.9)
        sections = [(x1, mid - gap), (mid + gap, x2)]
    else:
        d.line(x1, y_bus, x2, y_bus, "BUS", width=0.55)
        d.text(f"{name} {level.get('conductor', '')}".strip(), (x1 + x2) / 2, y_bus + 4.5, 3.4)
        sections = [(x1, x2)]

    # top/high voltage levels: draw feeders upward; bottom levels: draw downward. For a left-side compact 110kV style, user can set orientation=left.
    orientation = str(level.get("orientation", ""))
    if not orientation:
        orientation = "up" if idx == 0 else ("down" if idx == total - 1 else "up")

    if orientation in ["left", "right"]:
        x_bus = x1 if orientation == "left" else x2
        d.line(x_bus, y_bus - 55, x_bus, y_bus + 55, "BUS", width=0.55)
        step = 14
        top_y = y_bus + (len(feeders) - 1) * step / 2
        for i, lab in enumerate(feeders):
            draw_horizontal_feeder(d, x_bus, top_y - i * step, lab, direction=orientation, s=0.85)
    else:
        # Distribute feeders across sections and draw vertical bays.
        count = max(len(feeders), 1)
        available = []
        for sx1, sx2 in sections:
            n = max(1, math.ceil(count / len(sections)))
            for j in range(n):
                available.append(sx1 + (j + 1) * (sx2 - sx1) / (n + 1))
        if len(available) < len(feeders):
            available = [x1 + (i + 1) * (x2 - x1) / (len(feeders) + 1) for i in range(len(feeders))]
        for i, lab in enumerate(feeders):
            draw_vertical_bay(d, available[i], y_bus, lab, direction=orientation, s=0.8, with_ground=True)

    # PT/reactor/capacitor devices normally hang from lower-voltage buses.
    downward_devices = orientation != "up"
    pt_n = int(level.get("pt", 0) or 0)
    reac_n = int(level.get("reactors", 0) or 0)
    cap_n = int(level.get("capacitors", 0) or 0)
    device_labels = []
    device_labels += [("pt", f"PT{i+1 if pt_n>1 else ''}") for i in range(pt_n)]
    device_labels += [("reactor", f"电抗器{i+1 if reac_n>1 else ''}") for i in range(reac_n)]
    device_labels += [("capacitor", f"电容器{i+1 if cap_n>1 else ''}") for i in range(cap_n)]
    if device_labels:
        n = len(device_labels)
        # place devices centered on bus, avoid title block area
        dev_x1 = x1 + 35
        dev_x2 = x2 - 35
        for i, (kind, lab) in enumerate(device_labels):
            x = dev_x1 + (i + 0.5) * (dev_x2 - dev_x1) / n
            if kind == "pt":
                draw_pt(d, x, y_bus, lab.strip(), s=0.75, downward=downward_devices)
            elif kind == "reactor":
                draw_reactor(d, x, y_bus, lab, s=0.75, downward=downward_devices)
            else:
                draw_capacitor(d, x, y_bus, lab, s=0.75, downward=downward_devices)

    # Transformers listed on this level: draw them below this bus unless a target is explicitly handled later.
    if transformers and idx < total - 1:
        n = len(transformers)
        for i, lab in enumerate(transformers):
            x = x1 + (i + 1) * (x2 - x1) / (n + 1)
            y_t = y_bus - 58
            d.line(x, y_bus, x, y_t + 18, "WIRE")
            draw_disconnector(d, x, y_bus - 12, "vertical", s=0.75)
            draw_breaker(d, x, y_bus - 27, "vertical", s=0.75)
            draw_transformer(d, x, y_t, lab, s=0.85)


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
    for idx, level in enumerate(levels[:3]):
        draw_level_bus(d, level, idx, min(len(levels), 3), spec)
    # Optional left compact vertical bus, useful for matching sample with many 110kV outgoing lines.
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
    for name, cfg in d.layers.items():
        if name not in doc.layers:
            doc.layers.add(name, color=cfg["color"], lineweight=cfg["lineweight"])
    # Try to create a Chinese-capable style; consumers may substitute if absent.
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
                # ezdxf >= 1.x
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
    # validation pass
    ezdxf.readfile(path)


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
            # approximate arc with polyline
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
