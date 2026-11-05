"""Build disposable examples for comment continuity and circular review."""
from pathlib import Path
import json
import sys

import ezdxf

from cad_skill_core.revision.common import sha256_file
from cad_skill_core.revision.workflow import apply_revision
from cad_skill_core.revision.local_review import review_files


def main(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    doc = ezdxf.new('R2018', setup=True)
    doc.units = 4
    circle = doc.modelspace().add_circle((10, 10), 4)
    hatch = doc.modelspace().add_hatch()
    hatch.set_pattern_fill('ANSI31', scale=.5)
    hatch.paths.add_edge_path().add_arc((10, 10), 4, 0, 360)
    line = doc.modelspace().add_line((30, 0), (40, 0))
    doc.modelspace().add_text('TEST FIXTURE', dxfattribs={'insert': (0, 25), 'height': 2})
    baseline = output / 'baseline.dxf'
    doc.saveas(baseline)
    ledger = {'schema': 'cad-comment-ledger/v1', 'drawing_sha256': sha256_file(baseline),
              'units': 'mm', 'tolerance': 1e-6, 'padding': 3,
              'comments': [
        {'id': 'C1', 'text': '测试圆的半径保持4，检查轮廓与填充', 'source': {'message': 'synthetic fixture'},
         'targets': [{'handle': circle.dxf.handle}, {'handle': hatch.dxf.handle}],
         'checks': [{'type': 'property', 'target': {'handle': circle.dxf.handle}, 'attribute': 'radius', 'value': 4}],
         'manual_checks': ['查看轮廓线宽及填充外观']},
        {'id': 'C2', 'text': '测试线段长度改为15，起点固定', 'source': {'message': 'synthetic fixture'},
         'targets': [{'handle': line.dxf.handle}], 'checks': [
             {'type': 'set_length', 'target': {'handle': line.dxf.handle}, 'fixed': 'start', 'length': 15},
             {'type': 'property', 'target': {'handle': line.dxf.handle}, 'attribute': 'start', 'value': [30, 0, 0]}]}]}
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(baseline), 'units': 'mm', 'tolerance': 1e-6,
            'operations': [{'id': 'length-edit', 'type': 'set_length', 'target': {'handle': line.dxf.handle},
                            'fixed': 'start', 'length': 15}]}
    for name, value in [('ledger', ledger), ('plan', plan)]:
        (output / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    apply_revision(baseline, plan, output / 'round-1', ledger=ledger)
    source = output / 'round-1/drawing.dxf'
    plan['baseline_sha256'] = sha256_file(source)
    plan['operations'][0]['length'] = 18
    try:
        apply_revision(source, plan, output / 'rejected-round')
    except ValueError as error:
        assert 'C2' in str(error)
        (output / 'rejected-round.json').write_text(json.dumps({'reason': str(error), 'output_published': False}), encoding='utf-8')
    else:
        raise AssertionError('old requirement was silently bypassed')
    carried = json.loads((output / 'round-1/comment-ledger.json').read_text(encoding='utf-8'))
    carried['comments'].append({'id': 'C3', 'text': '后续要求长度改为18', 'source': {'message': 'new synthetic instruction'},
        'targets': [{'handle': line.dxf.handle}], 'supersedes': ['C2'], 'checks': [
            {'type': 'set_length', 'target': {'handle': line.dxf.handle}, 'fixed': 'start', 'length': 18},
            {'type': 'property', 'target': {'handle': line.dxf.handle}, 'attribute': 'start', 'value': [30, 0, 0]}]})
    apply_revision(source, plan, output / 'round-2', ledger=carried)
    bad = ezdxf.readfile(baseline)
    bad.entitydb[circle.dxf.handle].dxf.radius = 5
    bad_path = output / 'stale-circular-hatch.dxf'
    bad.saveas(bad_path)
    report = review_files(baseline, bad_path, output / 'curve-review')
    assert any(i['code'] == 'hatch_boundary_detached' for i in report['issues'])
    return output


if __name__ == '__main__':
    print(main(sys.argv[1]))
