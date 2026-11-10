"""Saved revision workflows for explicit dimension/geometry synchronization."""
import json

import ezdxf
import pytest

from cad_skill_core.cli import main
from cad_skill_core.revision.common import RevisionError, sha256_file
from cad_skill_core.revision.workflow import apply_revision


def _fixture(mode):
    doc = ezdxf.new('R2013')
    doc.units = 4
    if mode == 'diameter':
        geometry = doc.modelspace().add_circle((10, 0), 5)
        builder = doc.modelspace().add_diameter_dim(center=(10, 0), radius=5, angle=90)
    elif mode == 'radius':
        geometry = doc.modelspace().add_arc((10, 0), 5, 0, 90)
        builder = doc.modelspace().add_radius_dim(center=(10, 0), radius=5, angle=45)
    else:
        geometry = doc.modelspace().add_line((0, 0), (10, 0))
        builder = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    builder.render()
    operation = {'type': 'dimension_drive', 'target': {'handle': builder.dimension.dxf.handle},
                 'mode': mode, 'value': 20 if mode == 'linear' else 8,
                 'geometry': [{'handle': geometry.dxf.handle}]}
    if mode == 'linear':
        operation['fixed'] = 'p1'
    return doc, builder.dimension.dxf.handle, geometry.dxf.handle, operation


def _run(tmp_path, doc, operations, checks=None):
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    original = source.read_bytes()
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': operations,
            'checks': checks or []}
    report = apply_revision(source, plan, tmp_path / 'out')
    assert source.read_bytes() == original
    return report, ezdxf.readfile(report['output_path'])


@pytest.mark.parametrize('mode', ['diameter', 'radius', 'linear'])
def test_drive_workflow_reopens_and_checks_geometry_with_unchanged_neighbor(tmp_path, mode):
    doc, dim_handle, geometry_handle, drive = _fixture(mode)
    neighbor = doc.modelspace().add_line((50, 50), (75, 50), dxfattribs={'color': 3})
    report, out = _run(tmp_path, doc, [dict(drive, id='drive')], [drive])
    assert out.entitydb[dim_handle].get_measurement() == pytest.approx(drive['value'])
    geometry = out.entitydb[geometry_handle]
    if mode == 'linear':
        assert geometry.dxf.start == (0, 0, 0)
        assert geometry.dxf.end == (20, 0, 0)
    else:
        assert geometry.dxf.center == (10, 0, 0)
        assert geometry.dxf.radius == pytest.approx(4 if mode == 'diameter' else 8)
    assert out.entitydb[neighbor.dxf.handle].dxf.color == 3
    assert out.entitydb[neighbor.dxf.handle].dxf.end == (75, 50, 0)
    assert report['operations'][0]['saved_file_check']['status'] == 'passed'
    assert report['active_requirements'][0]['result']['status'] == 'passed'
    assert report['final_checks'][0]['status'] == 'passed'
    assert report['native_interaction_tested'] is False


@pytest.mark.parametrize('later', [
    {'type': 'dimension_move', 'position': [0, 9]},
    {'type': 'dimension_text_mode', 'mode': 2},
])
def test_linear_drive_remains_active_after_supported_annotation_change(tmp_path, later):
    doc, dim_handle, geometry_handle, drive = _fixture('linear')
    annotation = dict(later, id='annotation', target={'handle': dim_handle})
    report, out = _run(tmp_path, doc, [dict(drive, id='drive'), annotation], [drive])
    assert out.entitydb[geometry_handle].dxf.end == (20, 0, 0)
    assert out.entitydb[dim_handle].get_measurement() == pytest.approx(20)
    assert all(check['result']['status'] == 'passed' for check in report['active_requirements'])


def test_later_geometry_edit_cannot_silently_break_active_drive(tmp_path):
    doc, _, geometry_handle, drive = _fixture('linear')
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    original = source.read_bytes()
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': [dict(drive, id='drive'),
                {'id': 'shorten', 'type': 'set_length', 'target': {'handle': geometry_handle},
                 'length': 15, 'fixed': 'start'}]}
    with pytest.raises(RevisionError, match='active requirement'):
        apply_revision(source, plan, tmp_path / 'out')
    assert source.read_bytes() == original
    assert not (tmp_path / 'out').exists()


def test_cli_catalog_describes_explicit_dimension_drive(capsys):
    assert main(['operations', '--json']) == 0
    catalog = json.loads(capsys.readouterr().out)
    drive = next(entry for entry in catalog['operations'] if entry['type'] == 'dimension_drive')
    assert drive['required'] == ['target', 'mode', 'value', 'geometry']
    assert 'fixed' in drive['optional']
    assert drive['documentation'] == 'docs/edit-operations.md'
    assert catalog['native_interaction_tested'] is False


@pytest.mark.parametrize('mode', ['diameter', 'radius'])
def test_geometry_radius_edit_remeasures_circular_dimension_through_saved_workflow(tmp_path, mode):
    doc, dim_handle, geometry_handle, _ = _fixture(mode)
    doc.entitydb[geometry_handle].dxf.radius = 12
    operation = {'id': 'sync', 'type': 'dimension_remeasure', 'mode': mode,
                 'target': {'handle': dim_handle}, 'geometry': [{'handle': geometry_handle}]}
    report, reopened = _run(tmp_path, doc, [operation])
    assert reopened.entitydb[dim_handle].get_measurement() == pytest.approx(24 if mode == 'diameter' else 12)
    assert reopened.entitydb[geometry_handle].dxf.radius == 12
    assert report['operations'][0]['saved_file_check']['status'] == 'passed'


def test_conflicting_circular_binding_rejects_without_publication(tmp_path):
    doc, dim_handle, geometry_handle, drive = _fixture('diameter')
    doc.entitydb[geometry_handle].dxf.center = (100, 100, 0)
    source = tmp_path / 'original.dxf'
    doc.saveas(source)
    original = source.read_bytes()
    plan = {'schema': 'cad-revision/v1', 'baseline_sha256': sha256_file(source),
            'units': 'mm', 'tolerance': 1e-7, 'operations': [dict(drive, id='drive')]}
    with pytest.raises(RevisionError):
        apply_revision(source, plan, tmp_path / 'out')
    assert source.read_bytes() == original
    assert not (tmp_path / 'out').exists()
