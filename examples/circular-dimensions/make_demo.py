"""Create new, verified examples; never edits an existing user drawing.

Run with Python 3.12 and the skill runtime on PYTHONPATH, or installed editable.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf

from cad_skill_core.geometry.contours import compile_contour
from cad_skill_core.geometry.parametric import (
    RoundedRectangleParameters, derive_rounded_rectangle,
    rounded_rectangle, update_rounded_rectangle,
)
from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.workflow import apply_revision


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def make_demo(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    doc = ezdxf.new('R2018')
    doc.units = 4
    doc.styles.get('Standard').dxf.font = 'arial.ttf'
    msp = doc.modelspace()
    circle = msp.add_circle((0, 0), 10)
    diameter = msp.add_diameter_dim(center=(0, 0), radius=10, angle=45)
    diameter.render()
    arc = msp.add_arc((60, 0), 10, 0, 90)
    radial = msp.add_radius_dim(center=(60, 0), radius=10, angle=45)
    radial.render()
    line = msp.add_line((0, -35), (60, -35))
    length = msp.add_linear_dim(base=(0, -45), p1=(0, -35), p2=(60, -35))
    length.render()
    baseline = output / '01-original.dxf'
    doc.saveas(baseline)
    source_hash = sha256_file(baseline)
    operations = []
    for mode, geometry, dim, value in [('diameter', circle, diameter.dimension, 30),
                                      ('radius', arc, radial.dimension, 20),
                                      ('linear', line, length.dimension, 80)]:
        op = {'id': mode, 'type': 'dimension_drive', 'mode': mode, 'value': value,
              'target': {'handle': dim.dxf.handle}, 'geometry': [{'handle': geometry.dxf.handle}]}
        if mode == 'linear':
            op['fixed'] = 'p1'
        operations.append(op)
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': source_hash,
            'units': 'mm', 'tolerance': 1e-7, 'operations': operations}
    write_json(output / 'drive-plan.json', plan)
    drive_report = apply_revision(baseline, plan, output / '02-dimension-driven')
    assert sha256_file(baseline) == source_hash

    # Demonstrate reverse synchronization after an explicit external geometry edit.
    changed = ezdxf.readfile(drive_report['output_path'])
    changed.entitydb[circle.dxf.handle].dxf.radius = 18
    stale = output / '03-geometry-edited-before-sync.dxf'
    changed.saveas(stale)
    sync = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(stale),
            'units': 'mm', 'tolerance': 1e-7, 'operations': [
                {'id': 'sync-diameter', 'type': 'dimension_remeasure', 'mode': 'diameter',
                 'target': {'handle': diameter.dimension.dxf.handle},
                 'geometry': [{'handle': circle.dxf.handle}]}]}
    write_json(output / 'remeasure-plan.json', sync)
    sync_report = apply_revision(stale, sync, output / '04-geometry-synchronized')
    reopened = ezdxf.readfile(sync_report['output_path'])
    assert math.isclose(reopened.entitydb[diameter.dimension.dxf.handle].get_measurement(), 36, abs_tol=1e-7)

    # Exact, one-entity rounded contours. This is a bounded Python parameter API,
    # separate from ordinary DIMENSION editing and from AutoCAD native constraints.
    original = rounded_rectangle(RoundedRectangleParameters(width=100., height=60., radius=10.))
    resized = update_rounded_rectangle(original, radius=20.)
    measured = []
    for name, contour in [('05-contour-R10', original), ('06-contour-R20', resized)]:
        drawing = ezdxf.new('R2018')
        drawing.units = 4
        drawing.modelspace().add_lwpolyline(compile_contour(contour), format='xyb', close=True)
        path = output / (name + '.dxf')
        drawing.saveas(path)
        poly = ezdxf.readfile(path).modelspace()[0]
        assert poly.closed and len(poly) == 8
        parts = list(poly.virtual_entities())
        assert sum(e.dxftype() == 'ARC' for e in parts) == 4
        measured.append(derive_rounded_rectangle(contour).model_dump())
    summary = {'status': 'passed', 'native_interaction_tested': False,
               'drive': {'diameter': 30, 'radius': 20, 'length': 80},
               'reverse_diameter': 36, 'rounded_rectangles': measured,
               'limits': 'Explicit DXF edits; no real-time native constraints or general sketch solver.'}
    write_json(output / 'demo-results.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    print(json.dumps(make_demo(parser.parse_args().out), ensure_ascii=False, indent=2))
