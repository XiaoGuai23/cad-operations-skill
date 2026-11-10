"""Edits must reject unsupported input atomically and verify real saved content."""
import importlib

import ezdxf
import pytest
from ezdxf.lldxf.tagwriter import TagCollector

from cad_skill_core.revision.common import RevisionError, stable_id


TOL = 1e-7


def edits():
    return importlib.import_module('cad_skill_core.revision.edits')


def serialized(doc):
    return {h: list(TagCollector.dxftags(e)) for h, e in doc.entitydb.items() if e.is_alive}


def setup_outline():
    doc = ezdxf.new('R2013')
    e = doc.modelspace().add_line((0, 0), (3, 0), dxfattribs={'color': 3, 'invisible': 1})
    selector = {'handle': e.dxf.handle}
    op = {'type': 'replace_outline', 'targets': [selector], 'replacements': [
        {'id': 'edge-new', 'source': selector, 'type': 'LINE', 'start': [0, 0], 'end': [10, 0]}]}
    return doc, e, op


@pytest.mark.parametrize('field,value', [('start', [True, 0]), ('end', [float('inf'), 0]),
                                        ('end', [0, 0]), ('end', [10, 0, 0]), ('unknown', 1)])
def test_invalid_replacement_geometry_leaves_document_unchanged(field, value):
    doc, _, op = setup_outline()
    op['replacements'].append(dict(op['replacements'][0], id='second'))
    op['replacements'][1][field] = value
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, op, TOL)
    assert serialized(doc) == before


@pytest.mark.parametrize('kind', ['LINE', 'ARC', 'CIRCLE'])
def test_replacements_keep_custom_xdata_and_extra_id_tags(tmp_path, kind):
    doc, source, op = setup_outline()
    for app in ('CAD_SKILL_ID', 'USER_DATA'):
        doc.appids.add(app)
    source.set_xdata('CAD_SKILL_ID', [(1000, 'old-edge'), (1000, 'keep-extra-id-note')])
    source.set_xdata('USER_DATA', [(1000, 'material-A'), (1040, .75)])
    op['replacements'] = [{'id': 'edge-new', 'source': op['targets'][0], 'type': kind,
        **({'start': [0, 0], 'end': [10, 0]} if kind == 'LINE' else
           {'center': [0, 0], 'radius': 5, **({'start_angle': 0, 'end_angle': 90} if kind == 'ARC' else {})})}]
    handle = source.dxf.handle
    result = edits().apply_edit(doc, op, TOL)
    path = tmp_path / 'out.dxf'
    doc.saveas(path)
    out = ezdxf.readfile(path)
    child = out.entitydb[result['mapping'][handle][0]]
    assert child.dxftype() == kind
    assert stable_id(child) == 'edge-new'
    assert child.get_xdata('CAD_SKILL_ID')[1].value == 'keep-extra-id-note'
    assert [(t.code, t.value) for t in child.get_xdata('USER_DATA')] == [(1000, 'material-A'), (1040, .75)]
    assert child.dxf.color == 3 and child.dxf.invisible == 1
    assert edits().check_edit(out, result['verification'], TOL)['status'] == 'passed'
    child.set_xdata('USER_DATA', [(1000, 'tampered')])
    assert edits().check_edit(out, result['verification'], TOL)['status'] == 'failed'


@pytest.mark.parametrize('fault', ['reactor', 'extension', 'appdata', 'proxy', 'id_collision', 'unmapped'])
def test_unsupported_outline_metadata_or_mapping_is_atomic(fault):
    doc, source, op = setup_outline()
    if fault == 'reactor':
        source.set_reactors(['ABC'])
    elif fault == 'extension':
        source.new_extension_dict().add_xrecord('custom').reset([(1, 'retain me')])
    elif fault == 'appdata':
        source.set_app_data('CUSTOM', [(1, 'retain me')])
    elif fault == 'proxy':
        source.proxy_graphic = b'opaque'
    elif fault == 'id_collision':
        doc.appids.add('CAD_SKILL_ID')
        source.set_xdata('CAD_SKILL_ID', [(1000, 'edge-new')])
    else:
        other = doc.modelspace().add_line((20, 0), (30, 0))
        op['targets'].append({'handle': other.dxf.handle})
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, op, TOL)
    assert serialized(doc) == before


def test_replacement_check_detects_displaced_shortened_or_missing_output():
    doc, _, op = setup_outline()
    report = edits().apply_edit(doc, op, TOL)
    child = doc.entitydb[report['created_handles'][0]]
    child.dxf.end = (9, 0, 0)
    assert edits().check_edit(doc, report['verification'], TOL)['status'] == 'failed'


@pytest.mark.parametrize('p2', [[True, 5], [float('nan'), 5], [0, 0], [0, 10], [2, 3, 4]])
def test_remeasure_rejects_invalid_and_zero_projected_measurements_without_mutation(p2):
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, {'type': 'dimension_remeasure', 'target': {'handle': dim.dimension.dxf.handle},
                                'p1': [0, 0], 'p2': p2}, TOL)
    assert serialized(doc) == before


def test_remeasure_native_cache_text_and_unknown_xdata_survive_and_tampering_fails(tmp_path):
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    doc.appids.add('USER_DATA')
    dim.dimension.set_xdata('USER_DATA', [(1000, 'owner record')])
    report = edits().apply_edit(doc, {'type': 'dimension_remeasure', 'target': {'handle': dim.dimension.dxf.handle},
                                    'p1': [0, 0], 'p2': [20, 0]}, TOL)
    path = tmp_path / 'out.dxf'
    doc.saveas(path)
    out = ezdxf.readfile(path)
    changed = out.entitydb[dim.dimension.dxf.handle]
    assert changed.get_xdata('USER_DATA')[0].value == 'owner record'
    cache_texts = [e for e in out.blocks[changed.dxf.geometry] if e.dxftype() == 'MTEXT']
    assert [e.text for e in cache_texts] == ['20']
    assert edits().check_edit(out, report['verification'], TOL)['status'] == 'passed'
    cache_texts[0].text = '10'
    assert edits().check_edit(out, report['verification'], TOL)['status'] == 'failed'


def test_delete_shared_dimension_cache_rejects_without_mutation():
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    doc.modelspace().add_blockref(dim.dimension.dxf.geometry, (100, 100))
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, {'type': 'delete_entities', 'targets': [{'handle': dim.dimension.dxf.handle}]}, TOL)
    assert serialized(doc) == before


@pytest.mark.parametrize('kind', ['delete_entities', 'dimension_remeasure'])
def test_dimension_cache_block_metadata_is_not_silently_discarded(kind):
    doc = ezdxf.new('R2013')
    dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    doc.appids.add('CUSTOM_CACHE')
    doc.blocks[dim.dimension.dxf.geometry].block_record.set_xdata('CUSTOM_CACHE', [(1000, 'retain me')])
    selector = {'handle': dim.dimension.dxf.handle}
    operation = ({'type': kind, 'targets': [selector]} if kind == 'delete_entities' else
                 {'type': kind, 'target': selector, 'p1': [0, 0], 'p2': [20, 0]})
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, operation, TOL)
    assert serialized(doc) == before


def _drive_fixture(tmp_path, mode):
    doc = ezdxf.new('R2013')
    if mode == 'diameter':
        circle = doc.modelspace().add_circle((10, 0), 5)
        dim = doc.modelspace().add_diameter_dim(center=(10, 0), radius=5, angle=90)
        target = circle
    elif mode == 'radius':
        arc = doc.modelspace().add_arc((10, 0), 5, 0, 90)
        dim = doc.modelspace().add_radius_dim(center=(10, 0), radius=5, angle=45)
        target = arc
    else:
        line = doc.modelspace().add_line((0, 0), (10, 0))
        dim = doc.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
        target = line
    dim.render()
    return doc, dim.dimension, target


@pytest.mark.parametrize('mode,value,check', [
    ('diameter', 16.0, lambda doc, target: target.dxf.radius == pytest.approx(8.0)),
    ('radius', 8.0, lambda doc, target: target.dxf.radius == pytest.approx(8.0)),
    ('linear', 20.0, lambda doc, target: tuple(target.dxf.end) == pytest.approx((20.0, 0.0, 0.0))),
])
def test_dimension_drive_changes_geometry_and_dimension_together(tmp_path, mode, value, check):
    doc, dim, target = _drive_fixture(tmp_path, mode)
    op = {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle}, 'mode': mode,
          'value': value, 'geometry': [{'handle': target.dxf.handle}],
          **({'fixed': 'p1'} if mode == 'linear' else {})}
    report = edits().apply_edit(doc, op, TOL)
    path = tmp_path / 'out.dxf'
    doc.saveas(path)
    out = ezdxf.readfile(path)
    changed_dim = out.entitydb[dim.dxf.handle]
    changed_target = out.entitydb[target.dxf.handle]
    assert float(changed_dim.get_measurement()) == pytest.approx(value)
    assert check(out, changed_target)
    assert edits().check_edit(out, report['verification'], TOL)['status'] == 'passed'
    # The reverse direction still works: editing the geometry and remeasuring
    # returns the dimension to the driven value.
    if mode == 'linear':
        changed_target.dxf.end = (15, 0, 0)
        edits().apply_edit(out, {'type': 'dimension_remeasure', 'target': {'handle': dim.dxf.handle},
                                  'p1': [0, 0], 'p2': [15, 0]}, TOL)
        assert float(out.entitydb[dim.dxf.handle].get_measurement()) == pytest.approx(15.0)


def test_dimension_drive_rejects_unmeasured_geometry_without_mutation():
    doc, dim, target = _drive_fixture(None, 'diameter')
    other = doc.modelspace().add_circle((100, 100), 5)
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
                                  'mode': 'diameter', 'value': 16.0,
                                  'geometry': [{'handle': other.dxf.handle}]}, TOL)
    assert serialized(doc) == before


def test_dimension_drive_rejects_manual_text_override_without_mutation():
    doc, dim, target = _drive_fixture(None, 'radius')
    dim.dxf.text = 'R5 FIXED'
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
                                  'mode': 'radius', 'value': 8.0,
                                  'geometry': [{'handle': target.dxf.handle}]}, TOL)
    assert serialized(doc) == before


def test_replace_outline_accepts_unified_circular_spec(tmp_path):
    doc, source, op = setup_outline()
    op['replacements'] = [{'id': 'edge-new', 'source': op['targets'][0], 'type': 'CIRCULAR',
                           'center': [0, 0], 'radius': 5, 'start_angle': 0, 'sweep': 90}]
    result = edits().apply_edit(doc, op, TOL)
    child = doc.entitydb[result['created_handles'][0]]
    assert child.dxftype() == 'ARC'
    assert child.dxf.start_angle == pytest.approx(0.0)
    assert child.dxf.end_angle == pytest.approx(90.0)


def test_dimension_drive_bare_check_passes_after_drive():
    doc, dim, target = _drive_fixture(None, 'diameter')
    op = {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
          'mode': 'diameter', 'value': 16.0, 'geometry': [{'handle': target.dxf.handle}]}
    assert edits().check_edit(doc, op, TOL)['status'] == 'failed'
    edits().apply_edit(doc, op, TOL)
    assert edits().check_edit(doc, op, TOL)['status'] == 'passed'


def test_dimension_drive_accepts_empty_default_text(tmp_path):
    doc, dim, target = _drive_fixture(tmp_path, 'radius')
    dim.dxf.text = ''
    op = {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
          'mode': 'radius', 'value': 8.0, 'geometry': [{'handle': target.dxf.handle}]}
    edits().apply_edit(doc, op, TOL)
    assert target.dxf.radius == pytest.approx(8.0)


def test_dimension_drive_rejects_arc_not_covering_dimension_point():
    doc, dim, target = _drive_fixture(None, 'radius')
    other_arc = doc.modelspace().add_arc((10, 0), 5, 180, 270)
    before = serialized(doc)
    with pytest.raises(RevisionError):
        edits().apply_edit(doc, {'type': 'dimension_drive', 'target': {'handle': dim.dxf.handle},
                                  'mode': 'radius', 'value': 8.0,
                                  'geometry': [{'handle': other_arc.dxf.handle}]}, TOL)
    assert serialized(doc) == before
