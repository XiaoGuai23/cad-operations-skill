import importlib

import ezdxf
import pytest


def api():
    return importlib.import_module('cad_skill_core.revision.common')


def test_selector_requires_a_unique_live_modelspace_entity():
    doc = ezdxf.new()
    e = doc.modelspace().add_line((0, 0), (1, 0))
    p = doc.layouts.get('Layout1').add_line((0, 0), (1, 0))
    assert api().resolve_entity(doc, {'handle': e.dxf.handle.lower()}) is e
    with pytest.raises(api().RevisionError):
        api().resolve_entity(doc, {'handle': p.dxf.handle})
    for selector in ({}, {'handle': 'missing'}, {'handle': e.dxf.handle, 'id': 'x'}, {'handle': 2}):
        with pytest.raises(api().RevisionError):
            api().resolve_entity(doc, selector)


def test_stable_id_lookup_rejects_ambiguous_ids_and_duplicate_targets():
    doc = ezdxf.new()
    doc.appids.add('CAD_SKILL_ID')
    a = doc.modelspace().add_line((0, 0), (1, 0))
    b = doc.modelspace().add_line((2, 0), (3, 0))
    a.set_xdata('CAD_SKILL_ID', [(1000, 'screw.top')])
    assert api().resolve_entity(doc, {'id': 'screw.top'}) is a
    with pytest.raises(api().RevisionError):
        api().resolve_entities(doc, [{'id': 'screw.top'}, {'handle': a.dxf.handle}])
    b.set_xdata('CAD_SKILL_ID', [(1000, 'screw.top')])
    with pytest.raises(api().RevisionError):
        api().resolve_entity(doc, {'id': 'screw.top'})


def test_snapshot_detects_hatch_boundary_and_native_text_changes():
    doc = ezdxf.new()
    m = doc.modelspace()
    hatch = m.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10)], is_closed=True)
    text = m.add_text('Φ12 +0.2/0')
    before = api().snapshot_modelspace(doc)
    text.dxf.text = 'Φ13 +0.2/0'
    hatch.paths.paths[0].vertices[1] = (11, 0, 0)
    changed = api().compare_untouched(before, api().snapshot_modelspace(doc), set(), 1e-7)
    assert set(changed) == {text.dxf.handle, hatch.dxf.handle}


def test_fingerprint_survives_real_save_and_reopen(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_line((0.123456789, 2), (3, 4))
    m.add_text('孔径 Φ12')
    path = tmp_path / 'source.dxf'
    doc.saveas(path)
    baseline = ezdxf.readfile(path)
    before = api().snapshot_modelspace(baseline)
    baseline.saveas(tmp_path / 'result.dxf')
    after = api().snapshot_modelspace(ezdxf.readfile(tmp_path / 'result.dxf'))
    assert api().compare_untouched(before, after, set(), 1e-7) == []


def test_tolerance_does_not_hide_integer_flags_or_color_changes():
    doc = ezdxf.new()
    e = doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={'color': 1})
    before = api().snapshot_modelspace(doc)
    e.dxf.color = 2
    assert api().compare_untouched(before, api().snapshot_modelspace(doc), set(), 2) == [e.dxf.handle]


def test_unrepresentable_tolerance_raises_revision_error():
    with pytest.raises(api().RevisionError):
        api().validate_tolerance(10 ** 1000)
