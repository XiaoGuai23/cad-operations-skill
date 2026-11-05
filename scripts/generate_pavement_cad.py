#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a CAD-ready DXF pavement structure cross-section from a JSON spec.
No third-party dependencies required.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Iterable

DEFAULT_AUTOCAD = os.environ.get("AUTOCAD_EXE", "acad.exe")

ACI_COLORS = [1, 30, 3, 5, 6, 2, 4, 8, 9, 140]


def clean_basename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
    return name or "pavement_structure"


def mm_label(mm: float) -> str:
    if abs(mm % 10) < 1e-9:
        cm = mm / 10
        if abs(cm - round(cm)) < 1e-9:
            return f"{int(round(cm))}cm"
        return f"{cm:g}cm"
    return f"{mm:g}mm"


def esc_dxf_text(s: Any) -> str:
    # Keep Unicode text directly; escape a few DXF-sensitive control chars.
    return str(s).replace("\r", " ").replace("\n", " ").replace("\\", "\\\\")


class DXF:
    def __init__(self) -> None:
        self.entities: list[str] = []
        self.layers: dict[str, int] = {
            "0": 7,
            "OUTLINE": 7,
            "HATCH": 8,
            "TEXT": 7,
            "DIM": 2,
            "CENTER": 1,
            "TITLE": 7,
        }

    def layer(self, name: str, color: int = 7) -> str:
        self.layers.setdefault(name, int(color))
        return name

    def line(self, x1: float, y1: float, x2: float, y2: float, layer: str = "0", color: int | None = None) -> None:
        if color is not None:
            self.layer(layer, color)
        self.entities.append(
            f"0\nLINE\n8\n{layer}\n10\n{x1:.3f}\n20\n{y1:.3f}\n30\n0\n11\n{x2:.3f}\n21\n{y2:.3f}\n31\n0\n"
        )

    def rect(self, x: float, y: float, w: float, h: float, layer: str = "OUTLINE") -> None:
        self.line(x, y, x + w, y, layer)
        self.line(x + w, y, x + w, y - h, layer)
        self.line(x + w, y - h, x, y - h, layer)
        self.line(x, y - h, x, y, layer)

    def text(self, x: float, y: float, text: Any, height: float = 120, layer: str = "TEXT", align: str = "L") -> None:
        # align: L, C, R
        halign = {"L": 0, "C": 1, "R": 2}.get(align.upper(), 0)
        parts = [
            "0", "TEXT", "8", layer, "10", f"{x:.3f}", "20", f"{y:.3f}", "30", "0",
            "40", f"{height:.3f}", "1", esc_dxf_text(text), "7", "CNSTYLE",
        ]
        if halign:
            parts += ["72", str(halign), "11", f"{x:.3f}", "21", f"{y:.3f}", "31", "0"]
        self.entities.append("\n".join(parts) + "\n")

    def polyline(self, pts: Iterable[tuple[float, float]], layer: str = "OUTLINE", closed: bool = False) -> None:
        pts = list(pts)
        self.entities.append(
            "0\nLWPOLYLINE\n8\n{}\n90\n{}\n70\n{}\n{}".format(
                layer,
                len(pts),
                1 if closed else 0,
                "".join(f"10\n{x:.3f}\n20\n{y:.3f}\n" for x, y in pts),
            )
        )

    def arrow(self, x: float, y: float, angle: float, size: float = 90, layer: str = "DIM") -> None:
        # two simple arrowhead lines pointing in angle direction
        for da in (math.radians(150), -math.radians(150)):
            x2 = x + size * math.cos(angle + da)
            y2 = y + size * math.sin(angle + da)
            self.line(x, y, x2, y2, layer)

    def dim_horizontal(self, x1: float, x2: float, y: float, label: str, ext_top: float, layer: str = "DIM") -> None:
        self.line(x1, ext_top, x1, y, layer)
        self.line(x2, ext_top, x2, y, layer)
        self.line(x1, y, x2, y, layer)
        self.arrow(x1, y, 0, layer=layer)
        self.arrow(x2, y, math.pi, layer=layer)
        self.text((x1 + x2) / 2, y + 70, label, 120, layer, "C")

    def dim_vertical(self, x: float, y_top: float, y_bot: float, label: str, layer: str = "DIM") -> None:
        self.line(x, y_top, x, y_bot, layer)
        self.arrow(x, y_top, -math.pi / 2, layer=layer)
        self.arrow(x, y_bot, math.pi / 2, layer=layer)
        self.text(x + 80, (y_top + y_bot) / 2 - 45, label, 100, layer, "L")

    def hatch_diagonal(self, x: float, y_top: float, w: float, h: float, spacing: float = 180, layer: str = "HATCH") -> None:
        # Draw 45-degree hatch clipped to rectangle using line equation y = x + c.
        y_bot = y_top - h
        c_min = y_bot - (x + w)
        c_max = y_top - x
        c = c_min
        while c <= c_max + 1e-6:
            candidates: list[tuple[float, float]] = []
            # Intersections with left/right/top/bottom boundaries.
            yl = x + c
            if y_bot <= yl <= y_top:
                candidates.append((x, yl))
            yr = x + w + c
            if y_bot <= yr <= y_top:
                candidates.append((x + w, yr))
            xt = y_top - c
            if x <= xt <= x + w:
                candidates.append((xt, y_top))
            xb = y_bot - c
            if x <= xb <= x + w:
                candidates.append((xb, y_bot))
            if len(candidates) >= 2:
                p1, p2 = candidates[0], candidates[-1]
                if abs(p1[0] - p2[0]) > 1e-6 or abs(p1[1] - p2[1]) > 1e-6:
                    self.line(p1[0], p1[1], p2[0], p2[1], layer)
            c += spacing

    def write(self, path: Path) -> None:
        layer_defs = "".join(
            f"0\nLAYER\n2\n{name}\n70\n0\n62\n{color}\n6\nCONTINUOUS\n"
            for name, color in self.layers.items()
        )
        content = f"""0
SECTION
2
HEADER
9
$ACADVER
1
AC1021
9
$DWGCODEPAGE
3
ANSI_936
0
ENDSEC
0
SECTION
2
TABLES
0
TABLE
2
LTYPE
70
1
0
LTYPE
2
CONTINUOUS
70
0
3
Solid line
72
65
73
0
40
0
0
ENDTAB
0
TABLE
2
STYLE
70
1
0
STYLE
2
CNSTYLE
70
0
40
0
41
1
50
0
71
0
42
2.5
3
simsun.ttc
4

0
ENDTAB
0
TABLE
2
LAYER
70
{len(self.layers)}
{layer_defs}0
ENDTAB
0
ENDSEC
0
SECTION
2
ENTITIES
{''.join(self.entities)}0
ENDSEC
0
EOF
"""
        path.write_text(content, encoding="utf-8")


def make_drawing(spec: dict[str, Any], outdir: Path) -> Path:
    layers = spec.get("layers") or []
    if not layers:
        raise ValueError("spec.layers 不能为空")

    outdir.mkdir(parents=True, exist_ok=True)
    basename = clean_basename(spec.get("output_basename") or spec.get("title") or "pavement_structure")
    dxf_path = outdir / f"{basename}.dxf"

    width = float(spec.get("width", 10000))
    vex = float(spec.get("vertical_exaggeration", 5))
    title = spec.get("title", "路面结构图")
    units = spec.get("units", "mm")

    left = 1000.0
    top = 0.0
    label_x = left + width + 700
    dim_x = left + width + 300

    d = DXF()
    d.layer("PAVEMENT", 7)
    d.layer("HATCH", 8)
    d.layer("DIM", 2)
    d.layer("TEXT", 7)
    d.layer("CENTER", 1)
    d.layer("TITLE", 7)

    # Title and project block
    d.text(left + width / 2, top + 900, title, 300, "TITLE", "C")
    meta = []
    for key, label in [("project", "工程"), ("drawing_no", "图号"), ("designer", "设计"), ("reviewer", "复核")]:
        if spec.get(key):
            meta.append(f"{label}: {spec[key]}")
    meta.append(f"日期: {spec.get('date') or date.today().isoformat()}")
    d.text(left, top + 520, "    ".join(meta), 120, "TEXT", "L")

    # Optional crowned surface slope. Keep layer rectangles horizontal below; top line can show slope.
    slope = float(spec.get("surface_slope_percent", 0) or 0)
    if slope:
        crown_x = left + width / 2
        edge_drop = (width / 2) * slope / 100.0
        # exaggerated only lightly for display; label true slope
        d.line(left, top - edge_drop * 0.25, crown_x, top, "PAVEMENT")
        d.line(crown_x, top, left + width, top - edge_drop * 0.25, "PAVEMENT")
        d.text(crown_x, top + 120, f"路拱横坡 {slope:g}%", 110, "TEXT", "C")
    else:
        d.line(left, top, left + width, top, "PAVEMENT")

    y = top
    total = 0.0
    for idx, layer in enumerate(layers):
        th = float(layer.get("thickness", 0))
        if th <= 0:
            raise ValueError(f"第 {idx+1} 层 thickness 必须大于 0")
        draw_h = th * vex
        color = int(layer.get("color", ACI_COLORS[idx % len(ACI_COLORS)]))
        lname = d.layer(f"LAYER_{idx+1:02d}", color)
        d.rect(left, y, width, draw_h, "PAVEMENT")
        d.hatch_diagonal(left, y, width, draw_h, max(120.0, min(260.0, draw_h / 2)), "HATCH")

        material = layer.get("material", "")
        name = layer.get("name", f"结构层{idx+1}")
        note = layer.get("note", "")
        label = f"{material} {name}".strip()
        label = f"{label}  厚 {mm_label(th)}"
        if note:
            label += f"（{note}）"
        d.text(label_x, y - draw_h / 2 - 45, label, 115, "TEXT", "L")
        d.line(left + width, y - draw_h / 2, label_x - 100, y - draw_h / 2, "TEXT")
        d.dim_vertical(dim_x, y, y - draw_h, mm_label(th), "DIM")

        y -= draw_h
        total += th

    bottom = y

    # Centerline
    if spec.get("centerline", True):
        cx = left + width / 2
        d.line(cx, top + 300, cx, bottom - 450, "CENTER")
        d.text(cx + 80, top + 170, "道路中心线", 110, "CENTER", "L")

    # Dimensions and notes
    d.dim_horizontal(left, left + width, bottom - 380, f"路面宽度 {width:g}{units}", top, "DIM")
    d.text(left, bottom - 760, f"总结构厚度：{mm_label(total)}；竖向放大：{vex:g} 倍。", 130, "TEXT", "L")
    scale_note = spec.get("scale_note") or ("水平按实际尺寸绘制，竖向放大示意。" if vex != 1 else "按实际尺寸绘制。")
    d.text(left, bottom - 980, scale_note, 110, "TEXT", "L")
    ann_y = bottom - 1200
    for ann in spec.get("annotations", []):
        d.text(left, ann_y, f"注：{ann}", 105, "TEXT", "L")
        ann_y -= 180

    # Simple border around used area
    right = label_x + 5200
    low = min(ann_y - 200, bottom - 1300)
    d.rect(left - 500, top + 1050, right - left + 900, (top + 1050) - low, "OUTLINE")

    d.write(dxf_path)
    return dxf_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate pavement structure DXF from JSON spec")
    ap.add_argument("spec", help="Path to JSON spec")
    ap.add_argument("--out", default="output", help="Output directory")
    ap.add_argument("--open", action="store_true", help="Open generated DXF in AutoCAD if available")
    ap.add_argument("--acad", default=DEFAULT_AUTOCAD, help="AutoCAD acad.exe path")
    args = ap.parse_args()

    spec_path = Path(args.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    outdir = Path(args.out).resolve()
    dxf = make_drawing(spec, outdir)
    print(str(dxf))

    if args.open:
        acad = Path(args.acad)
        if acad.exists():
            subprocess.Popen([str(acad), str(dxf)], cwd=str(outdir))
        else:
            print(f"AutoCAD not found: {acad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
