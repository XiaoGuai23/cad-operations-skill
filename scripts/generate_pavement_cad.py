#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a CAD-ready DXF pavement structure cross-section from a JSON spec.

Uses ezdxf (R2010 audited DXF with native HATCH/DIMENSION/LWPOLYLINE) when
available, and falls back to standards-compliant AC1009 (R12) ASCII DXF when
running in a pure-stdlib environment without third-party packages.
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

try:
    import ezdxf  # type: ignore
    from ezdxf.enums import TextEntityAlignment  # type: ignore
except Exception:  # pragma: no cover
    ezdxf = None
    TextEntityAlignment = None

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
    # Keep Unicode text directly; escape DXF-sensitive control chars.
    return str(s).replace("\r", " ").replace("\n", " ").replace("\\", "\\\\")


class DXF:
    """Dual-backend DXF builder (ezdxf R2010 primary + stdlib AC1009 R12 fallback)."""

    def __init__(self) -> None:
        self.entities: list[str] = []
        self._records: list[tuple[str, dict[str, Any]]] = []
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
        self._records.append(("line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer}))
        self.entities.append(
            f"0\nLINE\n8\n{layer}\n10\n{x1:.3f}\n20\n{y1:.3f}\n30\n0\n11\n{x2:.3f}\n21\n{y2:.3f}\n31\n0\n"
        )

    def rect(self, x: float, y: float, w: float, h: float, layer: str = "OUTLINE") -> None:
        pts = [(x, y), (x + w, y), (x + w, y - h), (x, y - h)]
        self.polyline(pts, layer=layer, closed=True)

    def text(self, x: float, y: float, text: Any, height: float = 120, layer: str = "TEXT", align: str = "L") -> None:
        halign = {"L": 0, "C": 1, "R": 2}.get(align.upper(), 0)
        self._records.append(
            ("text", {"x": x, "y": y, "text": str(text), "height": height, "layer": layer, "align": align.upper()})
        )
        parts = [
            "0", "TEXT", "8", layer, "10", f"{x:.3f}", "20", f"{y:.3f}", "30", "0",
            "40", f"{height:.3f}", "1", esc_dxf_text(text), "7", "CNSTYLE",
        ]
        if halign:
            parts += ["72", str(halign), "11", f"{x:.3f}", "21", f"{y:.3f}", "31", "0"]
        self.entities.append("\n".join(parts) + "\n")

    def polyline(self, pts: Iterable[tuple[float, float]], layer: str = "OUTLINE", closed: bool = False) -> None:
        pts_list = [(float(px), float(py)) for px, py in pts]
        if len(pts_list) < 2:
            return
        self._records.append(("polyline", {"pts": pts_list, "layer": layer, "closed": closed}))
        # R12-compatible segment emission (AC1009 does not support LWPOLYLINE)
        pairs = list(zip(pts_list, pts_list[1:]))
        if closed and len(pts_list) >= 3:
            pairs.append((pts_list[-1], pts_list[0]))
        for (x1, y1), (x2, y2) in pairs:
            self.entities.append(
                f"0\nLINE\n8\n{layer}\n10\n{x1:.3f}\n20\n{y1:.3f}\n30\n0\n11\n{x2:.3f}\n21\n{y2:.3f}\n31\n0\n"
            )

    def arrow(self, x: float, y: float, angle: float, size: float = 90, layer: str = "DIM") -> None:
        for da in (math.radians(150), -math.radians(150)):
            x2 = x + size * math.cos(angle + da)
            y2 = y + size * math.sin(angle + da)
            self.line(x, y, x2, y2, layer)

    def dim_horizontal(self, x1: float, x2: float, y: float, label: str, ext_top: float, layer: str = "DIM") -> None:
        self._records.append(
            ("dim_h", {"x1": x1, "x2": x2, "y": y, "label": str(label), "ext_top": ext_top, "layer": layer})
        )
        # Fallback R12 primitives
        for px, py, qx, qy in [(x1, ext_top, x1, y), (x2, ext_top, x2, y), (x1, y, x2, y)]:
            self.entities.append(
                f"0\nLINE\n8\n{layer}\n10\n{px:.3f}\n20\n{py:.3f}\n30\n0\n11\n{qx:.3f}\n21\n{qy:.3f}\n31\n0\n"
            )
        for ax, ay, ang in [(x1, y, 0.0), (x2, y, math.pi)]:
            for da in (math.radians(150), -math.radians(150)):
                bx = ax + 90.0 * math.cos(ang + da)
                by = ay + 90.0 * math.sin(ang + da)
                self.entities.append(
                    f"0\nLINE\n8\n{layer}\n10\n{ax:.3f}\n20\n{ay:.3f}\n30\n0\n11\n{bx:.3f}\n21\n{by:.3f}\n31\n0\n"
                )
        tx, ty = (x1 + x2) / 2.0, y + 70.0
        self.entities.append(
            f"0\nTEXT\n8\n{layer}\n10\n{tx:.3f}\n20\n{ty:.3f}\n30\n0\n40\n120.000\n1\n{esc_dxf_text(label)}\n7\nCNSTYLE\n72\n1\n11\n{tx:.3f}\n21\n{ty:.3f}\n31\n0\n"
        )

    def dim_vertical(self, x: float, y_top: float, y_bot: float, label: str, layer: str = "DIM", x_ref: float | None = None) -> None:
        self._records.append(
            ("dim_v", {"x": x, "x_ref": x if x_ref is None else x_ref, "y_top": y_top, "y_bot": y_bot, "label": str(label), "layer": layer})
        )
        # Fallback R12 primitives
        self.entities.append(
            f"0\nLINE\n8\n{layer}\n10\n{x:.3f}\n20\n{y_top:.3f}\n30\n0\n11\n{x:.3f}\n21\n{y_bot:.3f}\n31\n0\n"
        )
        for ay, ang in [(y_top, -math.pi / 2), (y_bot, math.pi / 2)]:
            for da in (math.radians(150), -math.radians(150)):
                bx = x + 90.0 * math.cos(ang + da)
                by = ay + 90.0 * math.sin(ang + da)
                self.entities.append(
                    f"0\nLINE\n8\n{layer}\n10\n{x:.3f}\n20\n{ay:.3f}\n30\n0\n11\n{bx:.3f}\n21\n{by:.3f}\n31\n0\n"
                )
        tx, ty = x + 80.0, (y_top + y_bot) / 2.0 - 45.0
        self.entities.append(
            f"0\nTEXT\n8\n{layer}\n10\n{tx:.3f}\n20\n{ty:.3f}\n30\n0\n40\n100.000\n1\n{esc_dxf_text(label)}\n7\nCNSTYLE\n"
        )

    def hatch_diagonal(self, x: float, y_top: float, w: float, h: float, spacing: float = 180, layer: str = "HATCH") -> None:
        self._records.append(
            ("hatch", {"x": x, "y_top": y_top, "w": w, "h": h, "spacing": spacing, "layer": layer})
        )
        # Fallback R12 clipped hatch lines
        y_bot = y_top - h
        c_min = y_bot - (x + w)
        c_max = y_top - x
        c = c_min
        while c <= c_max + 1e-6:
            candidates: list[tuple[float, float]] = []
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
                    self.entities.append(
                        f"0\nLINE\n8\n{layer}\n10\n{p1[0]:.3f}\n20\n{p1[1]:.3f}\n30\n0\n11\n{p2[0]:.3f}\n21\n{p2[1]:.3f}\n31\n0\n"
                    )
            c += spacing

    def _write_ezdxf(self, path: Path) -> None:
        doc = ezdxf.new("R2010", setup=True)
        doc.units = ezdxf.units.MM
        if "CNSTYLE" not in doc.styles:
            doc.styles.add("CNSTYLE", font="simsun.ttc")
        dimstyle = "PAVEMENT_MODEL_DIM"
        if dimstyle not in doc.dimstyles:
            doc.dimstyles.add(
                dimstyle,
                dxfattribs={
                    "dimtxsty": "CNSTYLE",
                    "dimtxt": 100.0,
                    "dimasz": 80.0,
                    "dimgap": 35.0,
                    "dimexo": 30.0,
                    "dimexe": 45.0,
                    "dimtad": 1,
                    "dimdec": 0,
                    "dimclrd": 2,
                    "dimclre": 2,
                    "dimclrt": 2,
                },
            )
        for name, color in self.layers.items():
            if name not in doc.layers:
                ltype = "CENTER" if name == "CENTER" and "CENTER" in doc.linetypes else "CONTINUOUS"
                doc.layers.add(name, color=color, linetype=ltype)
        msp = doc.modelspace()
        amap = {
            "L": TextEntityAlignment.LEFT,
            "C": TextEntityAlignment.CENTER,
            "R": TextEntityAlignment.RIGHT,
        }
        for kind, data in self._records:
            layer = data["layer"]
            if kind == "line":
                msp.add_line((data["x1"], data["y1"]), (data["x2"], data["y2"]), dxfattribs={"layer": layer})
            elif kind == "polyline":
                msp.add_lwpolyline(data["pts"], close=data["closed"], dxfattribs={"layer": layer})
            elif kind == "text":
                ent = msp.add_text(
                    data["text"],
                    dxfattribs={"layer": layer, "height": data["height"], "style": "CNSTYLE"},
                )
                ent.set_placement((data["x"], data["y"]), align=amap.get(data["align"], TextEntityAlignment.LEFT))
            elif kind == "hatch":
                hatch = msp.add_hatch(color=self.layers.get(layer, 8), dxfattribs={"layer": layer})
                hatch.set_pattern_fill("ANSI31", scale=max(1.0, data["spacing"] / 3.175))
                x, y_top, w, h = data["x"], data["y_top"], data["w"], data["h"]
                hatch.paths.add_polyline_path(
                    [(x, y_top - h), (x + w, y_top - h), (x + w, y_top), (x, y_top)],
                    is_closed=True,
                )
            elif kind == "dim_h":
                ov = msp.add_linear_dim(
                    base=((data["x1"] + data["x2"]) / 2.0, data["y"]),
                    p1=(data["x1"], data["ext_top"]),
                    p2=(data["x2"], data["ext_top"]),
                    angle=0.0,
                    text=data["label"],
                    dimstyle=dimstyle,
                    dxfattribs={"layer": layer},
                )
                ov.render()
            elif kind == "dim_v":
                ov = msp.add_linear_dim(
                    base=(data["x"], (data["y_top"] + data["y_bot"]) / 2.0),
                    p1=(data["x_ref"], data["y_top"]),
                    p2=(data["x_ref"], data["y_bot"]),
                    angle=90.0,
                    text=data["label"],
                    dimstyle=dimstyle,
                    dxfattribs={"layer": layer},
                )
                ov.render()
        doc.saveas(path)
        reopened = ezdxf.readfile(path)
        auditor = reopened.audit()
        if auditor.has_errors:
            raise RuntimeError(f"DXF audit failed with {len(auditor.errors)} error(s)")

    def _write_r12_ascii(self, path: Path) -> None:
        # Valid AC1009 (R12) DXF requires no handles (5), owners (330), subclass markers (100), or OBJECTS section.
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
AC1009
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
0.0
0
ENDTAB
0
TABLE
2
STYLE
70
2
0
STYLE
2
STANDARD
70
0
40
0.0
41
1.0
50
0.0
71
0
42
2.5
3
txt
4

0
STYLE
2
CNSTYLE
70
0
40
0.0
41
1.0
50
0.0
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

    def write(self, path: Path, *, force_stdlib: bool = False) -> None:
        if ezdxf is not None and not force_stdlib:
            self._write_ezdxf(path)
        else:
            self._write_r12_ascii(path)


def make_drawing(spec: dict[str, Any], outdir: Path, *, force_stdlib: bool = False) -> Path:
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

    # Optional crowned surface slope
    slope = float(spec.get("surface_slope_percent", 0) or 0)
    if slope:
        crown_x = left + width / 2
        edge_drop = (width / 2) * slope / 100.0
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
        d.layer(f"LAYER_{idx+1:02d}", color)
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
        d.dim_vertical(dim_x, y, y - draw_h, mm_label(th), "DIM", x_ref=left + width)

        y -= draw_h
        total += th

    bottom = y

    # Centerline
    if spec.get("centerline", True):
        cx = left + width / 2
        d.line(cx, top + 300, cx, bottom - 450, "CENTER")
        d.text(cx + 80, top + 170, "道路中心线", 110, "CENTER", "L")

    # Dimensions and notes
    d.dim_horizontal(left, left + width, bottom - 380, f"路面宽度 {width:g}{units}", bottom, "DIM")
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

    d.write(dxf_path, force_stdlib=force_stdlib)
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
            print(f"WARN: AutoCAD executable not found: {acad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
