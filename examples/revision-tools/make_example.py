"""Create a disposable existing-DXF revision example, never a production drawing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide


def create_example(directory: Path) -> tuple[Path, Path]:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    doc = ezdxf.new('R2013')
    doc.units = 4
    doc.appids.add('CAD_SKILL_ID')
    m = doc.modelspace()

    def identify(entity, name):
        entity.set_xdata('CAD_SKILL_ID', [(1000, name)])
        return {'id': name}

    def rect(name, points):
        return identify(m.add_lwpolyline(points, close=True), name)

    aligned = identify(m.add_circle((5, 35), radius=3), 'aligned-circle')
    shoe = rect('contact-shoe', [(0, 4), (10, 4), (10, 8), (0, 8)])
    reference = identify(m.add_line((0, 0), (10, 0)), 'contact-reference')
    head = rect('fixed-bottom-head', [(20, 0), (30, 0), (30, 5), (20, 5)])
    stem = identify(m.add_line((40, 0), (40, 5)), 'restore-length')
    move_dim = m.add_linear_dim(base=(50, 8), p1=(50, 0), p2=(70, 0), text='<>')
    move_dim.render()
    moving = identify(move_dim.dimension, 'moving-dimension')
    mode_dim = m.add_linear_dim(base=(50, 28), p1=(50, 20), p2=(70, 20), text='<>')
    mode_dim.render()
    mode = identify(mode_dim.dimension, 'text-mode-dimension')
    leader = m.add_multileader_mtext()
    leader.set_content('4-%%c12 +0.2/0', char_height=2)
    leader.add_leader_line(ConnectionSide.left, [Vec2(80, 0), Vec2(84, 6)])
    leader.build(insert=Vec2(90, 8))
    linked = identify(leader.multileader, 'moving-leader')
    label = identify(m.add_text('2-%%c8 H7', dxfattribs={'insert': (115, 10), 'height': 2}), 'callout-text')
    chain = [identify(m.add_line((105, 0), (110, 10)), 'callout-leg'),
             identify(m.add_line((110, 10), (115, 10)), 'callout-landing')]
    cross = [identify(m.add_line((0, 50), (10, 50), dxfattribs={'color': 3}), 'cross-horizontal'),
             identify(m.add_line((5, 45), (5, 55)), 'cross-vertical')]
    hatch = m.add_hatch()
    hatch.set_pattern_fill('ANSI31', scale=1)
    hatch.paths.add_polyline_path([(20, 45), (30, 45), (30, 55), (20, 55)], is_closed=True)
    identify(hatch, 'preserved-hatch')
    m.add_text('UNCHANGED', dxfattribs={'insert': (40, 50), 'height': 2})
    source = directory / 'baseline.dxf'
    doc.saveas(source)
    plan = {
        'schema': 'cad-revision/v1',
        'baseline_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'units': 'mm', 'tolerance': 1e-7,
        'note': 'Synthetic API example only; dimensions are not manufacturing values.',
        'operations': [
            {'id': 'align', 'type': 'align', 'targets': [aligned], 'anchor': 'center_x', 'coordinate': 15},
            {'id': 'contact', 'type': 'contact', 'targets': [shoe], 'anchor': 'min_y', 'reference': reference, 'reference_anchor': 'max_y'},
            {'id': 'head', 'type': 'resize_fixed_edge', 'targets': [head], 'axis': 'y', 'fixed': 'min', 'size': 9},
            {'id': 'length', 'type': 'set_length', 'target': stem, 'fixed': 'start', 'length': 12},
            {'id': 'dim-position', 'type': 'dimension_move', 'target': moving, 'position': [60, 12]},
            {'id': 'dim-mode', 'type': 'dimension_text_mode', 'target': mode, 'mode': 0},
            {'id': 'leader-position', 'type': 'leader_move', 'target': linked, 'position': [95, 15]},
            {'id': 'callout', 'type': 'link_callout', 'text': label, 'lines': chain,
             'arrow_target': [105, 0], 'arrow_name': 'OPEN', 'arrow_size': 1.2},
            {'id': 'segments', 'type': 'split_intersections', 'targets': cross},
        ],
        'checks': [{'type': 'set_length', 'target': stem, 'fixed': 'start', 'length': 12}],
    }
    plan_path = directory / 'plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return source, plan_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='New directory for synthetic input and plan')
    args = parser.parse_args()
    for path in create_example(args.directory):
        print(path)
