import hashlib
import importlib
import json

import ezdxf
import pytest


def workflow():
    return importlib.import_module('cad_skill_core.revision.workflow')


def fixture(tmp_path):
    doc = ezdxf.new('R2013')
    doc.units = 4
    m = doc.modelspace()
    line = m.add_line((0, 0), (3, 4), dxfattribs={'color': 3})
    other = m.add_text('保留 Φ12')
    path = tmp_path / 'existing.dxf'
    doc.saveas(path)
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'units': 'mm', 'tolerance': 1e-7,
            'operations': [{'id': 'comment-1', 'type': 'set_length', 'target': {'handle': line.dxf.handle},
                            'length': 10, 'fixed': 'start'}]}
    return path, plan, line.dxf.handle, other.dxf.handle


def test_revision_edits_existing_file_and_preserves_baseline_and_unrelated_text(tmp_path):
    source, plan, handle, text = fixture(tmp_path)
    original = source.read_bytes()
    result = workflow().apply_revision(source, plan, tmp_path / 'revised')
    assert result['status'] == 'passed'
    output = ezdxf.readfile(result['output_path'])
    assert tuple(output.entitydb[handle].dxf.start) == (0, 0, 0)
    assert tuple(output.entitydb[handle].dxf.end) == (6, 8, 0)
    assert output.entitydb[text].dxf.text == '保留 Φ12'
    assert source.read_bytes() == original
    report = json.loads((tmp_path / 'revised' / 'revision-report.json').read_text(encoding='utf-8'))
    assert report['preservation']['unexpected_handles'] == []
    assert report['baseline_sha256'] == plan['baseline_sha256']
    assert report['checks_passed'] is True


def test_identical_request_resumes_verified_result_without_reapplying(tmp_path):
    source, plan, _, _ = fixture(tmp_path)
    first = workflow().apply_revision(source, plan, tmp_path / 'revised')
    before = (tmp_path / 'revised' / 'drawing.dxf').read_bytes()
    second = workflow().apply_revision(source, plan, tmp_path / 'revised')
    assert second['reused'] is True
    assert second['output_sha256'] == first['output_sha256']
    assert (tmp_path / 'revised' / 'drawing.dxf').read_bytes() == before


def test_stale_baseline_plan_and_tampered_previous_result_are_rejected(tmp_path):
    source, plan, _, _ = fixture(tmp_path)
    stale = dict(plan, baseline_sha256='0' * 64)
    with pytest.raises(ValueError, match='baseline'):
        workflow().apply_revision(source, stale, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()
    result = workflow().apply_revision(source, plan, tmp_path / 'revised')
    with open(result['output_path'], 'ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='changed|modified|hash'):
        workflow().apply_revision(source, plan, tmp_path / 'revised')


def test_failed_later_operation_never_publishes_partial_edits(tmp_path):
    source, plan, _, _ = fixture(tmp_path)
    original = source.read_bytes()
    plan['operations'].append({'id': 'comment-2', 'type': 'set_length', 'target': {'handle': 'FFFFFF'},
                               'length': 11, 'fixed': 'start'})
    with pytest.raises(ValueError):
        workflow().apply_revision(source, plan, tmp_path / 'failed')
    assert source.read_bytes() == original
    assert not (tmp_path / 'failed').exists()


def test_dry_run_and_foreign_output_directory_do_not_modify_files(tmp_path):
    source, plan, _, _ = fixture(tmp_path)
    result = workflow().apply_revision(source, plan, tmp_path / 'preview', dry_run=True)
    assert result['status'] == 'passed' and result['dry_run'] is True
    assert not (tmp_path / 'preview').exists()
    output = tmp_path / 'foreign'
    output.mkdir()
    (output / 'keep.txt').write_text('user file')
    with pytest.raises(ValueError):
        workflow().apply_revision(source, plan, output)
    assert (output / 'keep.txt').read_text() == 'user file'


@pytest.mark.parametrize('change', [{'units': 'in'}, {'tolerance': True}, {'unknown': 1}])
def test_invalid_plan_does_not_change_baseline(tmp_path, change):
    source, plan, _, _ = fixture(tmp_path)
    plan.update(change)
    with pytest.raises(ValueError):
        workflow().apply_revision(source, plan, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_inventory_records_actual_file_hash_and_live_entities(tmp_path):
    source, plan, handle, _ = fixture(tmp_path)
    data = workflow().inventory_drawing(source)
    assert data['sha256'] == plan['baseline_sha256']
    assert data['units'] == 'mm'
    item = next(e for e in data['entities'] if e['handle'] == handle)
    assert item['type'] == 'LINE' and item['geometry']['end'] == [3, 4, 0]


@pytest.mark.parametrize('change', [{'units': []}, {'operations': [{'id': 'a', 'type': []}]},
                                  {'checks': [{'type': {}}]}])
def test_malformed_field_types_raise_revision_error(tmp_path, change):
    from cad_skill_core.revision.common import RevisionError
    source, plan, _, _ = fixture(tmp_path)
    plan.update(change)
    with pytest.raises(RevisionError):
        workflow().apply_revision(source, plan, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_link_then_move_keeps_active_arrow_and_text_requirements(tmp_path):
    from cad_skill_core.revision.common import sha256_file
    doc = ezdxf.new('R2013')
    doc.units = 4
    doc.appids.add('CAD_SKILL_ID')
    text = doc.modelspace().add_text('4-Φ12 +0.2/0', dxfattribs={'insert': (10, 10), 'height': 2})
    text.set_xdata('CAD_SKILL_ID', [(1000, 'callout')])
    line = doc.modelspace().add_line((0, 0), (10, 10))
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': [
                {'id': 'link', 'type': 'link_callout', 'text': {'id': 'callout'},
                 'lines': [{'handle': line.dxf.handle}], 'arrow_target': [0, 0],
                 'arrow_name': '', 'arrow_size': 1},
                {'id': 'move', 'type': 'leader_move', 'target': {'id': 'callout'}, 'position': [20, 20]}]}
    result = workflow().apply_revision(source, plan, tmp_path / 'out')
    assert len(result['active_requirements']) == 2
    output = ezdxf.readfile(result['output_path'])
    leader = output.modelspace().query('MULTILEADER')[0]
    assert leader.context.mtext.default_content == '4-Φ12 +0.2/0'
    assert tuple(leader.context.mtext.insert) == (20, 20, 0)
    assert tuple(leader.context.leaders[0].lines[0].vertices[0]) == (0, 0, 0)


def test_baseline_requiring_audit_repairs_is_not_silently_rewritten(tmp_path):
    from cad_skill_core.revision.common import RevisionError, sha256_file
    source, plan, handle, _ = fixture(tmp_path)
    doc = ezdxf.readfile(source)
    doc.entitydb[handle].dxf.linetype = 'MISSING_LINETYPE'
    doc.saveas(source)
    original = source.read_bytes()
    plan['baseline_sha256'] = sha256_file(source)
    with pytest.raises(RevisionError, match='audit|repair'):
        workflow().apply_revision(source, plan, tmp_path / 'out')
    assert source.read_bytes() == original
    assert not (tmp_path / 'out').exists()


def test_native_dimension_mode_and_position_compose_and_split_preserves_them(tmp_path):
    from cad_skill_core.revision.common import sha256_file
    doc = ezdxf.new('R2013')
    doc.units = 4
    m = doc.modelspace()
    dim = m.add_linear_dim(base=(0, 5), p1=(0, 0), p2=(20, 0))
    dim.render()
    handle = dim.dimension.dxf.handle
    a = m.add_line((30, 0), (40, 0))
    b = m.add_line((35, -5), (35, 5))
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': [
                {'id': 'mode', 'type': 'dimension_text_mode', 'target': {'handle': handle}, 'mode': 1},
                {'id': 'move', 'type': 'dimension_move', 'target': {'handle': handle}, 'position': [0, 10]},
                {'id': 'split', 'type': 'split_intersections',
                 'targets': [{'handle': a.dxf.handle}, {'handle': b.dxf.handle}]}]}
    report = workflow().apply_revision(source, plan, tmp_path / 'out')
    out = ezdxf.readfile(report['output_path'])
    dim = out.entitydb[handle]
    assert dim.get_measurement() == 20
    assert dim.dxf.defpoint.y == 10
    assert dim.override().get('dimtmove') == 1
    assert len(out.modelspace().query('LINE')) == 4
    assert len(report['active_requirements']) == 2
