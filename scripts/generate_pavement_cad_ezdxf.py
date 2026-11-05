#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate AutoCAD-friendly pavement structure DXF with ezdxf.
This is the preferred generator because AutoCAD rejects many hand-written DXF variants.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from datetime import date
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
    for name, color in [('BORDER',7),('STRUCT',7),('TEXT',7),('DIM',2),('HATCH',8),('CENTER',1),('TITLE',7)]:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    msp = doc.modelspace()

    def line(x1, y1, x2, y2, layer='0'):
        return msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': layer})

    def rect(x1, y1, x2, y2, layer='BORDER'):
        line(x1,y1,x2,y1,layer); line(x2,y1,x2,y2,layer); line(x2,y2,x1,y2,layer); line(x1,y2,x1,y1,layer)

    def text(s, x, y, h=3.5, layer='TEXT', align='LEFT'):
        e = msp.add_text(str(s), dxfattribs={'layer': layer, 'height': h, 'style': 'FANGSONG'})
        amap = {'LEFT': TextEntityAlignment.LEFT, 'CENTER': TextEntityAlignment.CENTER, 'RIGHT': TextEntityAlignment.RIGHT}
        e.set_placement((x, y), align=amap.get(align, TextEntityAlignment.LEFT))
        return e

    def hatch_rect(x1, y_top, x2, y_bot, spacing=5.0):
        # Use line hatch instead of HATCH entity for maximum compatibility.
        c_min = y_bot - x2
        c_max = y_top - x1
        c = c_min
        while c <= c_max:
            pts = []
            yl = x1 + c
            if y_bot <= yl <= y_top: pts.append((x1, yl))
            yr = x2 + c
            if y_bot <= yr <= y_top: pts.append((x2, yr))
            xt = y_top - c
            if x1 <= xt <= x2: pts.append((xt, y_top))
            xb = y_bot - c
            if x1 <= xb <= x2: pts.append((xb, y_bot))
            if len(pts) >= 2:
                p1, p2 = pts[0], pts[-1]
                line(p1[0], p1[1], p2[0], p2[1], 'HATCH')
            c += spacing

    def arrow(x, y, angle, size=2.5):
        for da in (math.radians(150), -math.radians(150)):
            line(x, y, x + size*math.cos(angle+da), y + size*math.sin(angle+da), 'DIM')

    def dim_v(x, y1, y2, label):
        line(x,y1,x,y2,'DIM'); arrow(x,y1,-math.pi/2); arrow(x,y2,math.pi/2)
        text(label,x+2,(y1+y2)/2-1.2,2.6,'DIM')

    def dim_h(x1, x2, y, label):
        line(x1,y,x2,y,'DIM'); arrow(x1,y,0); arrow(x2,y,math.pi)
        text(label,(x1+x2)/2,y+2.8,2.8,'DIM','CENTER')

    # A3 clean frame by default; if user later needs exact school frame, open this DXF and paste into template manually.
    rect(0,0,420,297,'BORDER')
    rect(10,10,410,287,'BORDER')
    rect(240,10,410,42,'BORDER')
    for x in [275,315,350,380]: line(x,10,x,42,'BORDER')
    line(240,26,410,26,'BORDER')

    title = spec.get('title', '路面结构设计图')
    project = spec.get('project', '')
    drawing_no = spec.get('drawing_no', 'LM-01')
    text(spec.get('school', '青海大学土木水利学院'),250,31,3.2,'TEXT')
    text('图名',242,17,2.8,'TEXT')
    text(title,277,17,3.0,'TEXT')
    text('图号',352,17,2.8,'TEXT')
    text(drawing_no,383,17,3.0,'TEXT')
    text('设计',317,31,2.8,'TEXT')
    text('审核',352,31,2.8,'TEXT')
    if project:
        text(project,35,282,3.2,'TEXT')
    text(title,210,270,7.0,'TITLE','CENTER')
    text(spec.get('scheme', '方案一（柔性基层方案）'),35,252,4.2,'TEXT')

    x0 = float(spec.get('paper_x0', 45))
    x1 = float(spec.get('paper_x1', 235))
    ytop = float(spec.get('paper_ytop', 225))
    vscale = float(spec.get('paper_vscale', 0.10))
    label_x = float(spec.get('label_x', 250))
    dim_x = float(spec.get('dim_x', x1 + 6))
    y = ytop
    total = 0.0
    for layer in layers:
        name = layer.get('name','结构层')
        mat = layer.get('material','')
        th = float(layer.get('thickness',0))
        if th <= 0:
            raise ValueError(f'结构层厚度必须大于0: {layer}')
        h = th * vscale
        y2 = y - h
        rect(x0,y,x1,y2,'STRUCT')
        hatch_rect(x0,y,x1,y2,float(spec.get('hatch_spacing',4.0)))
        mid = (y+y2)/2
        text(f'{name}：{mat}，厚 {fmt_th(th)}',label_x,mid-1.3,3.0,'TEXT')
        line(x1,mid,label_x-2,mid,'TEXT')
        dim_v(dim_x,y,y2,fmt_th(th))
        y = y2
        total += th

    if spec.get('centerline', True):
        cx=(x0+x1)/2
        line(cx,ytop+8,cx,y-6,'CENTER')
        text('道路中心线',cx+3,ytop+4,2.8,'CENTER')
    width = float(spec.get('width',10000))
    dim_h(x0,x1,y-10,f'路面宽度{width/1000:g}m（示意）')
    text(f'总厚度：{total:g}mm',45,y-23,3.5,'TEXT')
    text(spec.get('scale_note','注：未注明单位均为mm；竖向按1:10放大示意，结构层厚度以标注为准。'),45,y-32,3.0,'TEXT')
    for i, ann in enumerate(spec.get('annotations', [])):
        text(str(ann),45,y-40-i*7,3.0,'TEXT')

    doc.header['$EXTMIN'] = (0,0,0)
    doc.header['$EXTMAX'] = (420,297,0)
    doc.saveas(path)
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
