#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate AutoCAD-friendly pavement structure DXF with ezdxf.
This is the preferred generator because AutoCAD rejects many hand-written DXF variants.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.enums import TextEntityAlignment

DEFAULT_AUTOCAD = os.environ.get("AUTOCAD_EXE", "acad.exe")


def safe_name(s: str) -> str:
    for ch in '<>:"/\\|?*':
        s = s.replace(ch, '_')
    return s.strip() or 'pavement_structure'


def fmt_th(mm: float) -> str:
    if abs(mm % 10) < 1e-9:
        cm = mm / 10
        return f"{int(cm)}cm" if abs(cm - int(cm)) < 1e-9 else f"{cm:g}cm"
    return f"{mm:g}mm"


def _ensure_dimstyle(doc: ezdxf.document.Drawing, style_name: str = 'FANGSONG') -> str:
    dim_name = 'PAVEMENT_DIM'
    if dim_name in doc.dimstyles:
        return dim_name
    doc.dimstyles.add(
        dim_name,
        dxfattribs={
            'dimtxsty': style_name,
            'dimtxt': 2.6,
            'dimasz': 2.0,
            'dimgap': 0.8,
            'dimexo': 0.8,
            'dimexe': 1.2,
            'dimtad': 1,
            'dimdec': 0,
            'dimclrd': 2,
            'dimclre': 2,
            'dimclrt': 2,
        },
    )
    return dim_name


def make_dxf(spec: dict[str, Any], outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    basename = safe_name(spec.get('output_basename') or spec.get('title') or 'pavement_structure')
    path = outdir / f'{basename}.dxf'
    layers = spec.get('layers') or []
    if not layers:
        raise ValueError('spec.layers 不能为空')

    doc = ezdxf.new('R2010', setup=True)
    doc.units = ezdxf.units.MM
    if 'FANGSONG' not in doc.styles:
        doc.styles.add('FANGSONG', font=spec.get('font', 'simfang.ttf'))
    dimstyle = _ensure_dimstyle(doc, 'FANGSONG')

    layer_defs = [
        ('BORDER', 7, 'CONTINUOUS'),
        ('STRUCT', 7, 'CONTINUOUS'),
        ('TEXT', 7, 'CONTINUOUS'),
        ('DIM', 2, 'CONTINUOUS'),
        ('HATCH', 8, 'CONTINUOUS'),
        ('CENTER', 1, 'CENTER' if 'CENTER' in doc.linetypes else 'CONTINUOUS'),
        ('TITLE', 7, 'CONTINUOUS'),
    ]
    for name, color, linetype in layer_defs:
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=linetype)
    msp = doc.modelspace()

    def line(x1: float, y1: float, x2: float, y2: float, layer: str = '0'):
        return msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': layer})

    def rect(x1: float, y1: float, x2: float, y2: float, layer: str = 'BORDER'):
        return msp.add_lwpolyline(
            [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
            close=True,
            dxfattribs={'layer': layer},
        )

    def text(s: Any, x: float, y: float, h: float = 3.5, layer: str = 'TEXT', align: str = 'LEFT'):
        e = msp.add_text(str(s), dxfattribs={'layer': layer, 'height': h, 'style': 'FANGSONG'})
        amap = {
            'LEFT': TextEntityAlignment.LEFT,
            'CENTER': TextEntityAlignment.CENTER,
            'RIGHT': TextEntityAlignment.RIGHT,
        }
        e.set_placement((x, y), align=amap.get(align, TextEntityAlignment.LEFT))
        return e

    def hatch_rect(x1: float, y_top: float, x2: float, y_bot: float, spacing: float = 4.0):
        hatch = msp.add_hatch(color=8, dxfattribs={'layer': 'HATCH'})
        hatch.set_pattern_fill('ANSI31', scale=max(0.25, spacing / 3.175))
        hatch.paths.add_polyline_path(
            [(x1, y_bot), (x2, y_bot), (x2, y_top), (x1, y_top)],
            is_closed=True,
        )
        return hatch

    def dim_v(x: float, x_ref: float, y1: float, y2: float, label: str):
        override = msp.add_linear_dim(
            base=(x, (y1 + y2) / 2.0),
            p1=(x_ref, y1),
            p2=(x_ref, y2),
            angle=90.0,
            text=str(label),
            dimstyle=dimstyle,
            dxfattribs={'layer': 'DIM'},
        )
        override.render()
        return override.dimension

    def dim_h(x1: float, x2: float, y: float, y_ref: float, label: str):
        override = msp.add_linear_dim(
            base=((x1 + x2) / 2.0, y),
            p1=(x1, y_ref),
            p2=(x2, y_ref),
            angle=0.0,
            text=str(label),
            dimstyle=dimstyle,
            dxfattribs={'layer': 'DIM'},
        )
        override.render()
        return override.dimension

    # Standard A3 clean frame and title block
    rect(0, 0, 420, 297, 'BORDER')
    rect(10, 10, 410, 287, 'BORDER')
    rect(240, 10, 410, 42, 'BORDER')
    for x in [275, 315, 350, 380]:
        line(x, 10, x, 42, 'BORDER')
    line(240, 26, 410, 26, 'BORDER')

    title = spec.get('title', '路面结构设计图')
    project = spec.get('project', '')
    drawing_no = spec.get('drawing_no', 'LM-01')
    organization = spec.get('school') or spec.get('organization', '工程设计单位')
    text(organization, 244, 31, 3.0, 'TEXT')
    text('图名', 242, 17, 2.8, 'TEXT')
    text(title, 277, 17, 3.0, 'TEXT')
    text('图号', 352, 17, 2.8, 'TEXT')
    text(drawing_no, 383, 17, 3.0, 'TEXT')
    text('设计', 317, 31, 2.8, 'TEXT')
    text('审核', 352, 31, 2.8, 'TEXT')
    if project:
        text(project, 35, 280, 3.2, 'TEXT')
    text(title, 210, 270, 7.0, 'TITLE', 'CENTER')
    text(spec.get('scheme', '方案一（柔性基层方案）'), 35, 252, 4.2, 'TEXT')

    x0 = float(spec.get('paper_x0', 45))
    x1 = float(spec.get('paper_x1', 235))
    ytop = float(spec.get('paper_ytop', 225))
    vscale = float(spec.get('paper_vscale', 0.10))
    label_x = float(spec.get('label_x', 252))
    dim_x = float(spec.get('dim_x', x1 + 8))
    y = ytop
    total = 0.0
    for layer in layers:
        name = layer.get('name', '结构层')
        mat = layer.get('material', '')
        th = float(layer.get('thickness', 0))
        if th <= 0:
            raise ValueError(f'结构层厚度必须大于0: {layer}')
        h = th * vscale
        y2 = y - h
        rect(x0, y, x1, y2, 'STRUCT')
        hatch_rect(x0, y, x1, y2, float(spec.get('hatch_spacing', 4.0)))
        mid = (y + y2) / 2
        text(f'{name}：{mat}，厚 {fmt_th(th)}', label_x, mid - 1.3, 3.0, 'TEXT')
        line(x1, mid, label_x - 2, mid, 'TEXT')
        dim_v(dim_x, x1, y, y2, fmt_th(th))
        y = y2
        total += th

    if spec.get('centerline', True):
        cx = (x0 + x1) / 2
        line(cx, ytop + 8, cx, y - 6, 'CENTER')
        text('道路中心线', cx + 3, ytop + 4, 2.8, 'CENTER')
    width = float(spec.get('width', 10000))
    dim_h(x0, x1, y - 10, y, f'路面宽度{width/1000:g}m（示意）')
    text(f'总厚度：{total:g}mm', 45, y - 23, 3.5, 'TEXT')
    text(spec.get('scale_note', '注：未注明单位均为mm；竖向按1:10放大示意，结构层厚度以标注为准。'), 45, y - 32, 3.0, 'TEXT')
    for i, ann in enumerate(spec.get('annotations', [])):
        text(str(ann), 45, y - 40 - i * 7, 3.0, 'TEXT')

    doc.header['$EXTMIN'] = (0, 0, 0)
    doc.header['$EXTMAX'] = (420, 297, 0)
    doc.saveas(path)
    reopened = ezdxf.readfile(path)
    auditor = reopened.audit()
    if auditor.has_errors:
        raise RuntimeError(f'DXF audit failed with {len(auditor.errors)} error(s)')
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('spec')
    ap.add_argument('--out', default='output')
    ap.add_argument('--open', action='store_true')
    ap.add_argument('--acad', default=DEFAULT_AUTOCAD)
    args = ap.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding='utf-8-sig'))
    dxf = make_dxf(spec, Path(args.out))
    print(dxf)
    if args.open and Path(args.acad).exists():
        subprocess.Popen([args.acad, str(dxf)], cwd=str(Path(args.out).resolve()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
