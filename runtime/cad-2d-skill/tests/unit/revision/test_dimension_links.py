"""Dimension links are explicit edits, with complete rollback and saved evidence."""
from copy import deepcopy
import importlib

import ezdxf
from ezdxf.entities.dimstyleoverride import DimStyleOverride
from ezdxf.lldxf.tagwriter import TagCollector
import pytest

from cad_skill_core.revision.common import RevisionError

edits = importlib.import_module('cad_skill_core.revision.edits')
TOL = 1e-7


def fixture(mode='radius'):
    doc = ezdxf.new('R2013')
    layout = doc.modelspace()
    if mode == 'linear':
        curve = layout.add_line((0, 0), (10, 0))
        override = layout.add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    else:
        curve = (layout.add_arc((10, 0), 5, 0, 90) if mode == 'radius'
                 else layout.add_circle((10, 0), 5))
        creator = layout.add_radius_dim if mode == 'radius' else layout.add_diameter_dim
        override = creator(center=(10, 0), radius=5, angle=45)
    override.render()
    doc.appids.add('CAD_SKILL_ID')
    dim = override.dimension
    dim.set_xdata('CAD_SKILL_ID', [(1000, 'dimension')])
    curve.set_xdata('CAD_SKILL_ID', [(1000, 'geometry')])
    op = {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
          'mode': mode, 'value': 16 if mode == 'diameter' else 8,
          'geometry': [{'handle': curve.dxf.handle}]}
    if mode == 'linear':
        op['fixed'] = 'p1'
    return doc, dim, curve, op


def state(doc):
    return ({h: list(TagCollector.dxftags(e)) for h, e in doc.entitydb.items() if e.is_alive},
            set(doc.entitydb.keys()), [b.name for b in doc.blocks],
            [e.dxf.handle for e in doc.modelspace()], str(doc.entitydb.handles))


@pytest.mark.parametrize('mode', ['linear', 'radius', 'diameter'])
@pytest.mark.parametrize('failure', ['render', 'verification'])
def test_actual_apply_failure_restores_everything_and_existing_references(monkeypatch, mode, failure):
    doc, dim, curve, op = fixture(mode)
    dim.dxf.discard('actual_measurement')
    before = state(doc)
    layout, database, namespace = doc.modelspace(), doc.entitydb, dim.dxf
    original_render = DimStyleOverride.render
    caller_renders = []
    original_check = edits.check_edit

    def render(self, *args, **kwargs):
        result = original_render(self, *args, **kwargs)
        if self.dimension.doc is doc:
            caller_renders.append(True)
            # Resource and unrelated entity side effects must roll back too.
            doc.layers.new('renderer-side-effect')
            layout.add_point((7, 8))
            if failure == 'render':
                raise RuntimeError('failure after caller render')
        return result

    def check(current, *args, **kwargs):
        result = original_check(current, *args, **kwargs)
        if current is doc:
            return {'status': 'failed', 'checks': {'forced_after_render': False}}
        return result

    monkeypatch.setattr(DimStyleOverride, 'render', render)
    if failure == 'verification':
        monkeypatch.setattr(edits, 'check_edit', check)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, op, TOL)
    assert caller_renders == [True]
    assert state(doc) == before
    assert doc.entitydb is database
    assert doc.modelspace() is layout
    assert doc.entitydb[dim.dxf.handle] is dim
    assert doc.entitydb[curve.dxf.handle] is curve
    assert dim.dxf is namespace
    assert not dim.dxf.hasattr('actual_measurement')
    assert 'renderer-side-effect' not in doc.layers


@pytest.mark.parametrize('mode', ['radius', 'diameter', 'linear'])
@pytest.mark.parametrize('fault', ['missing_stored', 'wrong_stored', 'duplicate_identity', 'extra_geometry', 'wrong_type'])
def test_standalone_check_requires_complete_link_and_stored_measurement(mode, fault):
    doc, dim, curve, op = fixture(mode)
    edits.apply_edit(doc, op, TOL)
    if fault == 'missing_stored':
        dim.dxf.discard('actual_measurement')
    elif fault == 'wrong_stored':
        dim.dxf.actual_measurement = 123
    elif fault == 'duplicate_identity':
        duplicate = doc.modelspace().add_circle((100, 100), 2)
        duplicate.set_xdata('CAD_SKILL_ID', [(1000, 'geometry')])
    elif fault == 'extra_geometry':
        extra = doc.modelspace().add_circle((100, 100), 8)
        op['geometry'].append({'handle': extra.dxf.handle})
    else:
        other = doc.modelspace().add_circle((10, 0), 8) if mode == 'linear' else doc.modelspace().add_line((0, 0), (8, 0))
        op['geometry'] = [{'handle': other.dxf.handle}]
    assert edits.check_edit(doc, op, TOL)['status'] == 'failed'


@pytest.mark.parametrize('mode', ['radius', 'diameter'])
def test_bare_check_rejects_shifted_geometry_center(mode):
    doc, dim, curve, op = fixture(mode)
    edits.apply_edit(doc, op, TOL)
    curve.dxf.center = (100, 100, 0)
    assert edits.check_edit(doc, op, TOL)['status'] == 'failed'


def test_bare_check_rejects_arc_not_covering_dimension_boundary():
    doc, _, arc, op = fixture()
    edits.apply_edit(doc, op, TOL)
    arc.dxf.start_angle, arc.dxf.end_angle = 180, 270
    assert edits.check_edit(doc, op, TOL)['status'] == 'failed'


@pytest.mark.parametrize('fault', ['span', 'identity'])
def test_saved_evidence_detects_geometry_identity_and_arc_span_tampering(fault):
    doc, _, arc, op = fixture()
    result = edits.apply_edit(doc, op, TOL)
    if fault == 'span':
        arc.dxf.end_angle = 100  # Still contains the boundary, but changes the selected arc.
    else:
        arc.set_xdata('CAD_SKILL_ID', [(1000, 'changed')])
    assert edits.check_edit(doc, result['verification'], TOL)['status'] == 'failed'


@pytest.mark.parametrize('mode', ['radius', 'diameter'])
def test_explicit_reverse_circular_edit_roundtrips_without_mutating_geometry(tmp_path, mode):
    doc, dim, curve, drive = fixture(mode)
    edits.apply_edit(doc, drive, TOL)
    curve.dxf.radius = 12
    geometry_before = list(TagCollector.dxftags(curve))
    op = {'type': 'dimension_remeasure', 'target': drive['target'],
          'mode': mode, 'geometry': drive['geometry']}
    result = edits.apply_edit(doc, op, TOL)
    assert list(TagCollector.dxftags(curve)) == geometry_before
    assert dim.get_measurement() == pytest.approx(24 if mode == 'diameter' else 12)
    assert dim.dxf.actual_measurement == pytest.approx(dim.get_measurement())
    path = tmp_path / 'roundtrip.dxf'
    doc.saveas(path)
    reopened = ezdxf.readfile(path)
    assert edits.check_edit(reopened, result['verification'], TOL)['status'] == 'passed'
    assert edits.check_edit(reopened, op, TOL)['status'] == 'passed'


@pytest.mark.parametrize('mode', ['radius', 'diameter'])
def test_reverse_rejects_unrelated_center_without_mutation(mode):
    doc, dim, _, drive = fixture(mode)
    other = doc.modelspace().add_circle((100, 100), 12)
    before = state(doc)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, {'type': 'dimension_remeasure', 'target': drive['target'],
                              'mode': mode, 'geometry': [{'handle': other.dxf.handle}]}, TOL)
    assert state(doc) == before


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -1, 0, [], '8'])
def test_malformed_drive_values_are_atomic(value):
    doc, _, _, op = fixture()
    op['value'] = value
    before = state(doc)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, op, TOL)
    assert state(doc) == before


@pytest.mark.parametrize('fields', [{'mode': 'linear'}, {'mode': 'radius', 'p1': [0, 0], 'p2': [1, 0]},
                                   {'mode': ['radius']}, {'geometry': []}, {'geometry': [{}]}])
def test_malformed_reverse_operations_are_atomic(fields):
    doc, _, _, drive = fixture()
    operation = {'type': 'dimension_remeasure', 'target': drive['target'],
                 'mode': 'radius', 'geometry': drive['geometry']}
    operation.update(fields)
    before = state(doc)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, operation, TOL)
    assert state(doc) == before


@pytest.mark.parametrize('mode', ['radius', 'diameter'])
@pytest.mark.parametrize('direction', ['drive', 'remeasure'])
@pytest.mark.parametrize('fault', ['manual_text', 'text_mode', 'reactor', 'extension_dict', 'scaled_units'])
def test_circular_unsupported_native_semantics_rejected_atomically(mode, direction, fault):
    doc, dim, curve, operation = fixture(mode)
    if fault == 'manual_text':
        dim.dxf.dimtype |= 128
    elif fault == 'text_mode':
        override = dim.override()
        override['dimtmove'] = 3
        override.commit()
    elif fault == 'reactor':
        dim.set_reactors(['DEAD'])
    elif fault == 'extension_dict':
        dim.new_extension_dict()
    else:
        override = dim.override()
        override['dimlfac'] = 2
        override.commit()
    if direction == 'remeasure':
        operation = {'type': 'dimension_remeasure', 'target': operation['target'],
                     'mode': mode, 'geometry': operation['geometry']}
        curve.dxf.radius = 7
    before = state(doc)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, operation, TOL)
    assert state(doc) == before


def test_linear_drive_rejects_scaled_dimension_without_mutation():
    doc, dim, _, operation = fixture('linear')
    override = dim.override()
    override['dimlfac'] = 2
    override.commit()
    before = state(doc)
    with pytest.raises(RevisionError):
        edits.apply_edit(doc, operation, TOL)
    assert state(doc) == before


@pytest.mark.parametrize('mode', ['radius', 'diameter', 'linear'])
def test_repeated_successful_drives_retire_only_previous_private_display(tmp_path, mode):
    doc, dim, _, operation = fixture(mode)
    unrelated = doc.blocks.new('KEEP-ME')
    unrelated.add_line((1, 2), (3, 4))
    before_count = len(doc.blocks)
    old_record = doc.blocks[dim.dxf.geometry].block_record
    for value in [12, 14, 16]:
        operation['value'] = value
        result = edits.apply_edit(doc, operation, TOL)
        assert edits.check_edit(doc, result['verification'], TOL)['status'] == 'passed'
        assert len(doc.blocks) == before_count
    assert not old_record.is_alive
    assert len(doc.blocks['KEEP-ME']) == 1
    path = tmp_path / 'roundtrip.dxf'
    doc.saveas(path)
    reopened = ezdxf.readfile(path)
    audit = reopened.audit()
    assert not audit.has_errors and not audit.has_fixes
    assert edits.check_edit(reopened, result['verification'], TOL)['status'] == 'passed'


@pytest.mark.parametrize('start,sweep', [(1e20, 45), (30, 359.9999)])
def test_circular_replacement_preserves_partial_span_at_extreme_angles(start, sweep):
    arc = edits._geometry({'type': 'CIRCULAR', 'center': [0, 0], 'radius': 5,
                           'start_angle': start, 'sweep': sweep}, TOL)
    assert arc.dxftype() == 'ARC'
    assert arc.dxf.start_angle == pytest.approx(start % 360)
    actual_sweep = (arc.dxf.end_angle - arc.dxf.start_angle) % 360
    assert actual_sweep == pytest.approx(sweep, abs=1e-12)


def test_replacement_rejects_partial_arc_collapsed_by_native_angle_precision():
    with pytest.raises(RevisionError, match='degenerate'):
        edits._geometry({'type': 'CIRCULAR', 'center': [0, 0], 'radius': 5,
                         'start_angle': 30, 'sweep': 360 - 1e-10}, TOL)


@pytest.mark.parametrize('mode', ['radius', 'diameter', 'linear'])
def test_bare_link_check_rejects_changed_display_scale(mode):
    doc, dim, _, operation = fixture(mode)
    edits.apply_edit(doc, operation, TOL)
    override = dim.override()
    override['dimlfac'] = 2
    override.render()
    assert edits.check_edit(doc, operation, TOL)['status'] == 'failed'
