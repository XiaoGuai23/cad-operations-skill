from __future__ import annotations

import hashlib
import json

import ezdxf
import pytest

from cad_skill_core.cli import main


def drawing_and_plan(tmp_path):
    doc = ezdxf.new('R2013')
    doc.units = 4
    line = doc.modelspace().add_line((0, 0), (3, 4))
    doc.modelspace().add_text('保留 Φ12')
    source = tmp_path / '原图.dxf'
    doc.saveas(source)
    plan = {
        'schema': 'cad-revision/v1',
        'baseline_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'units': 'mm',
        'tolerance': 1e-7,
        'operations': [{'id': 'comment-1', 'type': 'set_length',
                        'target': {'handle': line.dxf.handle},
                        'length': 10, 'fixed': 'start'}],
    }
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    return source, plan_path, plan, line.dxf.handle


def test_inventory_exposes_current_hash_and_editable_object_handles(tmp_path, capsys):
    source, _, plan, handle = drawing_and_plan(tmp_path)
    original = source.read_bytes()

    assert main(['inventory', str(source), '--json']) == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert captured.err == ''
    assert data['command'] == 'inventory'
    assert data['sha256'] == plan['baseline_sha256']
    assert data['units'] == 'mm'
    assert data['entity_counts'] == {'LINE': 1, 'TEXT': 1}
    line = next(e for e in data['entities'] if e['handle'] == handle)
    assert line['geometry']['end'] == [3, 4, 0]
    assert source.read_bytes() == original


def test_revise_publishes_verified_geometry_and_reuses_matching_output(tmp_path, capsys):
    source, plan_path, _, handle = drawing_and_plan(tmp_path)
    original = source.read_bytes()
    output = tmp_path / 'revised'
    args = ['revise', str(source), str(plan_path), '--out', str(output), '--json']

    assert main(args) == 0

    data = json.loads(capsys.readouterr().out)
    assert data['command'] == 'revise'
    assert data['status'] == 'passed' and data['checks_passed'] is True
    assert data['reused'] is False
    result = ezdxf.readfile(output / 'drawing.dxf')
    assert tuple(result.entitydb[handle].dxf.end) == (6, 8, 0)
    assert source.read_bytes() == original
    before = (output / 'drawing.dxf').read_bytes()

    assert main(args) == 0

    repeated = json.loads(capsys.readouterr().out)
    assert repeated['reused'] is True
    assert (output / 'drawing.dxf').read_bytes() == before


def test_revise_dry_run_validates_without_publishing(tmp_path, capsys):
    source, plan_path, _, _ = drawing_and_plan(tmp_path)
    original = source.read_bytes()
    output = tmp_path / 'not-created' / 'preview'

    assert main(['revise', str(source), str(plan_path), '--out', str(output),
                 '--dry-run', '--json']) == 0

    data = json.loads(capsys.readouterr().out)
    assert data['dry_run'] is True
    assert data['checks_passed'] is True
    assert data['output_path'] is None
    assert not output.parent.exists()
    assert source.read_bytes() == original


@pytest.mark.parametrize('failure', ['stale', 'missing-target', 'unknown-field'])
def test_revise_rejects_invalid_requests_without_partial_output(tmp_path, capsys, failure):
    source, plan_path, plan, _ = drawing_and_plan(tmp_path)
    original = source.read_bytes()
    if failure == 'stale':
        plan['baseline_sha256'] = '0' * 64
    elif failure == 'missing-target':
        plan['operations'].append({'id': 'comment-2', 'type': 'set_length',
                                   'target': {'handle': 'FFFFFF'},
                                   'length': 20, 'fixed': 'start'})
    else:
        plan['operations'][0]['typo'] = 'do not ignore'
    plan_path.write_text(json.dumps(plan), encoding='utf-8')
    output = tmp_path / 'failed'

    assert main(['revise', str(source), str(plan_path), '--out', str(output), '--json']) == 2

    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err)['status'] == 'error'
    assert 'Traceback' not in captured.err
    assert source.read_bytes() == original
    assert not output.exists()


@pytest.mark.parametrize('kind', ['missing', 'dwg', 'malformed-dxf', 'malformed-plan'])
def test_invalid_input_reports_a_clean_validation_error(tmp_path, capsys, kind):
    source, plan_path, _, _ = drawing_and_plan(tmp_path)
    if kind == 'missing':
        source = tmp_path / 'missing.dxf'
    elif kind == 'dwg':
        source = tmp_path / 'native.dwg'
        source.write_bytes(b'AC1032')
    elif kind == 'malformed-dxf':
        source.write_text('not a CAD exchange file', encoding='utf-8')
    else:
        plan_path.write_text('{ broken JSON', encoding='utf-8')

    args = ['revise', str(source), str(plan_path), '--out', str(tmp_path / 'output'), '--json']
    assert main(args) == 2

    captured = capsys.readouterr()
    assert captured.out == ''
    error = json.loads(captured.err)
    assert error['status'] == 'error'
    if kind == 'dwg':
        assert 'DXF' in error['error'] and 'native CAD' in error['error']
    assert 'Traceback' not in captured.err
    assert not (tmp_path / 'output').exists()


def test_plain_text_commands_identify_objects_and_output_without_json(tmp_path, capsys):
    source, plan_path, plan, handle = drawing_and_plan(tmp_path)

    assert main(['inventory', str(source)]) == 0
    inventory = capsys.readouterr().out
    assert plan['baseline_sha256'] in inventory
    assert handle in inventory and 'LINE' in inventory

    assert main(['revise', str(source), str(plan_path), '--out', str(tmp_path / 'result')]) == 0
    result = capsys.readouterr().out
    assert 'passed' in result and 'drawing.dxf' in result
    assert 'revision-report.json' in result
