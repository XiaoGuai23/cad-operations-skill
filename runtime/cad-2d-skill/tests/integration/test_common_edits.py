"""Real workflow regressions for requested geometry and native annotations."""
import json

import ezdxf
import pytest

from cad_skill_core.revision.common import RevisionError, sha256_file, stable_id
from cad_skill_core.revision.workflow import apply_revision


def run(tmp_path, doc, operations, *, checks=None, ledger=None):
    doc.units = 4
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    before = source.read_bytes()
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': operations, 'checks': checks or []}
    if ledger:
        ledger['drawing_sha256'] = plan['baseline_sha256']
    report = apply_revision(source, plan, tmp_path / 'out', ledger=ledger)
    assert source.read_bytes() == before
    return report, ezdxf.readfile(report['output_path'])


def test_delete_native_dimension_removes_display_and_preserves_neighbor(tmp_path):
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(20, 0))
    dim.render()
    handle, cache = dim.dimension.dxf.handle, dim.dimension.dxf.geometry
    neighbor = doc.modelspace().add_line((0, 0), (20, 0), dxfattribs={'color': 3})
    report, out = run(tmp_path, doc, [{'id': 'remove', 'type': 'delete_entities', 'targets': [{'handle': handle}]}])
    assert len(out.modelspace().query('DIMENSION')) == 0
    assert cache not in out.blocks
    assert out.entitydb[neighbor.dxf.handle].dxf.end == (20, 0, 0)
    assert out.entitydb[neighbor.dxf.handle].dxf.color == 3
    assert report['operations'][0]['saved_file_check']['status'] == 'passed'


def test_delete_native_multileader_removes_text_and_leader_together(tmp_path):
    from ezdxf.math import Vec2
    from ezdxf.render.mleader import ConnectionSide
    doc = ezdxf.new('R2018')
    builder = doc.modelspace().add_multileader_mtext()
    builder.set_content('4-M10', char_height=2.5)
    builder.add_leader_line(ConnectionSide.left, [Vec2(0, 0), Vec2(5, 5)])
    builder.build(insert=Vec2(10, 10))
    neighbor = doc.modelspace().add_line((0, 0), (20, 0))
    report, out = run(tmp_path, doc, [{'id': 'remove-callout', 'type': 'delete_entities',
                                     'targets': [{'handle': builder.multileader.dxf.handle}]}])
    assert len(out.modelspace().query('MULTILEADER TEXT MTEXT LEADER')) == 0
    assert out.entitydb[neighbor.dxf.handle].dxf.end == (20, 0, 0)
    assert report['operations'][0]['saved_file_check']['status'] == 'passed'


def test_deleted_anonymous_dimension_block_name_can_be_reused_by_new_display(tmp_path):
    doc = ezdxf.new('R2018')
    kept = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(20, 0))
    kept.render()
    removed = doc.modelspace().add_linear_dim(base=(0, 10), p1=(0, 0), p2=(75, 0))
    removed.render()
    deleted_name = removed.dimension.dxf.geometry
    deleted_record = doc.blocks[deleted_name].block_record.dxf.handle
    removed_handle = removed.dimension.dxf.handle
    report, out = run(tmp_path, doc, [
        {'id': 'remove', 'type': 'delete_entities', 'targets': [{'handle': removed_handle}]},
        {'id': 'remeasure', 'type': 'dimension_remeasure', 'target': {'handle': kept.dimension.dxf.handle},
         'p1': [0, 0], 'p2': [30, 0]}])
    assert len(out.modelspace().query('DIMENSION')) == 1
    assert out.entitydb[kept.dimension.dxf.handle].get_measurement() == 30
    assert deleted_name in out.blocks  # A fresh native cache legitimately reuses the name.
    assert out.blocks[deleted_name].block_record.dxf.handle != deleted_record
    assert report['active_requirements'][0]['result']['status'] == 'passed'


def test_replacement_then_split_retains_complete_final_mapping_and_checks(tmp_path):
    doc = ezdxf.new('R2013')
    old = doc.modelspace().add_line((0, 0), (3, 0), dxfattribs={'color': 2})
    old_handle = old.dxf.handle
    crossing = doc.modelspace().add_line((5, -5), (5, 5))
    report, out = run(tmp_path, doc, [
        {'id': 'outline', 'type': 'replace_outline', 'targets': [{'handle': old_handle}],
         'replacements': [{'id': 'new-edge', 'source': {'handle': old_handle}, 'type': 'LINE', 'start': [0, 0], 'end': [10, 0]}]},
        {'id': 'split', 'type': 'split_intersections', 'targets': [{'id': 'new-edge'}, {'handle': crossing.dxf.handle}]}])
    edit = report['operations'][0]
    leaves = edit['result']['final_mapping'][old_handle]
    assert len(leaves) == 2
    assert sum((out.entitydb[h].dxf.end - out.entitydb[h].dxf.start).magnitude for h in leaves) == 10
    assert all(stable_id(out.entitydb[h]).startswith('new-edge/segment/') for h in leaves)
    assert all(out.entitydb[h].dxf.color == 2 for h in leaves)
    assert any(c['id'] == 'outline' and c['result']['status'] == 'passed' for c in report['active_requirements'])


@pytest.mark.parametrize('kind', [0, 1])
@pytest.mark.parametrize('text', ['<>', 'REF <>', '4-Φ12 +0.2/0', ' '])
def test_remeasure_changes_real_dimension_and_preserves_text_modes_and_tolerances(tmp_path, kind, text):
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0), text=text)
    dim.dimension.dxf.dimtype = 32 + kind
    dim.update({'dimtol': 1, 'dimtp': .2, 'dimtm': .1, 'dimtmove': 1})
    dim.render()
    handle = dim.dimension.dxf.handle
    p2 = [6, 8] if kind else [20, 7]
    report, out = run(tmp_path, doc, [
        {'id': 'remeasure', 'type': 'dimension_remeasure', 'target': {'handle': handle}, 'p1': [0, 0], 'p2': p2},
        {'id': 'mode', 'type': 'dimension_text_mode', 'target': {'handle': handle}, 'mode': 2}])
    changed = out.entitydb[handle]
    assert changed.dimtype == kind
    assert changed.get_measurement() == pytest.approx(10 if kind else 20)
    assert changed.dxf.defpoint3 == (*p2, 0)
    assert changed.dxf.text == text
    assert changed.dxf.dimstyle == 'Standard'
    assert changed.override().get('dimtmove') == 2
    assert changed.override().get('dimtol') == 1
    assert changed.override().get('dimtp') == .2
    assert changed.override().get('dimtm') == .1
    assert len(out.blocks[changed.dxf.geometry]) > 0
    assert len(report['active_requirements']) == 2


def test_delete_and_remeasure_are_comment_ledger_predicates(tmp_path):
    from matplotlib import font_manager

    doc = ezdxf.new('R2013')
    doc.styles.get('Standard').dxf.font = font_manager.findfont('DejaVu Sans')
    line = doc.modelspace().add_line((0, 0), (2, 0))
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    delete = {'type': 'delete_entities', 'targets': [{'handle': line.dxf.handle}]}
    measure = {'type': 'dimension_remeasure', 'target': {'handle': dim.dimension.dxf.handle}, 'p1': [0, 0], 'p2': [20, 0]}
    ledger = {'schema': 'cad-comment-ledger/v1', 'units': 'mm', 'tolerance': 1e-7, 'comments': [
        {'id': 'c1', 'text': 'Remove old line; use measured endpoints.', 'source': {'user': 'approved'},
         'targets': [{'handle': line.dxf.handle}], 'checks': [delete, measure]}]}
    report, _ = run(tmp_path, doc, [dict(delete, id='delete'), dict(measure, id='measure')], ledger=ledger)
    assert report['comment_checks']['status'] == 'checks_passed'


def test_split_replacement_checks_carry_verified_lineage_and_reject_later_shortening(tmp_path):
    doc = ezdxf.new('R2013')
    old = doc.modelspace().add_line((0, 0), (3, 0), dxfattribs={'color': 2})
    handle = old.dxf.handle
    crossing = doc.modelspace().add_line((5, -5), (5, 5))
    replace = {'type': 'replace_outline', 'targets': [{'handle': handle}], 'replacements': [
        {'id': 'new-edge', 'source': {'handle': handle}, 'type': 'LINE', 'start': [0, 0], 'end': [10, 0]}]}
    ledger = {'schema': 'cad-comment-ledger/v1', 'units': 'mm', 'tolerance': 1e-7, 'comments': [
        {'id': 'C1', 'text': 'Replace this edge and preserve its full final coverage.',
         'source': {'user': 'approved'}, 'targets': [{'handle': handle}], 'checks': [replace]}]}
    report, out = run(tmp_path, doc, [dict(replace, id='outline'),
        {'id': 'split', 'type': 'split_intersections',
         'targets': [{'id': 'new-edge'}, {'handle': crossing.dxf.handle}]}], checks=[replace], ledger=ledger)
    assert report['final_checks'][0]['status'] == 'passed'
    assert report['comment_checks']['status'] == 'checks_passed'
    carried = json.loads((tmp_path / 'out/comment-ledger.json').read_text(encoding='utf-8'))
    assert carried['comments'] == ledger['comments']
    from cad_skill_core.revision.ledger import check_ledger
    assert check_ledger(out, carried)['status'] == 'checks_passed'
    leaf = report['operations'][0]['result']['final_mapping'][handle][0]
    source = tmp_path / 'out/drawing.dxf'
    before = source.read_bytes()
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source), 'units': 'mm',
            'tolerance': 1e-7, 'operations': [{'id': 'shorten', 'type': 'set_length',
                'target': {'handle': leaf}, 'fixed': 'start', 'length': 4}]}
    with pytest.raises(RevisionError, match='C1'):
        apply_revision(source, plan, tmp_path / 'next')
    assert not (tmp_path / 'next').exists()
    assert source.read_bytes() == before
    out.entitydb[leaf].dxf.color = 7
    assert check_ledger(out, carried)['status'] == 'failed'
    out.entitydb[leaf].dxf.color = 2
    out.entitydb[leaf].set_xdata('CAD_SKILL_ID', [(1000, 'unrelated-reused-handle')])
    assert check_ledger(out, carried)['status'] == 'failed'
