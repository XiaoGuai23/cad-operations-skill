import copy
import math

import ezdxf
import pytest

from cad_skill_core.revision.segments import split_intersections, verify_split
from cad_skill_core.revision.common import RevisionError


def sel(entity):
    return {'handle': entity.dxf.handle}


def clone(doc, tmp_path, name='before.dxf'):
    path = tmp_path / name
    doc.saveas(path)
    return ezdxf.readfile(path)


def test_crossing_lines_split_at_real_intersection_and_second_run_is_stable(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0), dxfattribs={'color': 3})
    b = m.add_line((5, -5), (5, 5))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert report['status'] == 'passed'
    assert len(m.query('LINE')) == 4
    assert sorted(round((e.dxf.end - e.dxf.start).magnitude, 8) for e in m) == [5, 5, 5, 5]
    assert all(e.dxf.start.isclose((5, 0, 0)) or e.dxf.end.isclose((5, 0, 0)) for e in m)
    final = clone(doc, tmp_path, 'after.dxf')
    assert verify_split(before, final, report, 1e-7)['status'] == 'passed'
    old_handles = [e.dxf.handle for e in m]
    second = split_intersections(doc, None, 1e-7)
    assert second['touched_handles'] == []
    assert [e.dxf.handle for e in m] == old_handles


def test_t_junction_preserves_endpoint_entity_handle(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    base = m.add_line((0, 0), (10, 0))
    stem = m.add_line((3, 0), (3, 2))
    original_stem = stem.dxf.handle
    report = split_intersections(doc, None, 1e-7)
    assert len(report['mapping'][base.dxf.handle if base.is_alive else report['deleted_handles'][0]]) == 2
    assert report['mapping'][original_stem] == [original_stem]
    assert len(m.query('LINE')) == 3


def test_tangent_circle_becomes_two_arcs_without_degenerate_fragments(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_circle((0, 0), 2)
    m.add_line((2, -3), (2, 3))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('ARC')) == 2
    assert len(m.query('LINE')) == 2
    assert not m.query('CIRCLE')
    assert verify_split(before, clone(doc, tmp_path, 'tangent.dxf'), report, 1e-7)['status'] == 'passed'
    assert split_intersections(doc, None, 1e-7)['touched_handles'] == []


def test_collinear_overlap_splits_boundaries_without_losing_distinct_styles(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0), dxfattribs={'color': 1})
    b = m.add_line((5, 0), (15, 0), dxfattribs={'color': 2})
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('LINE')) == 4
    assert sorted(e.dxf.color for e in m) == [1, 1, 2, 2]
    checked = verify_split(before, clone(doc, tmp_path, 'overlap.dxf'), report, 1e-7)
    assert checked['status'] == 'passed'
    assert checked['coincident_output_pairs'] == 1
    assert checked['introduced_duplicate_segments'] == 0


def test_hatch_dimension_text_and_unselected_geometry_are_unchanged(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0))
    b = m.add_line((5, -5), (5, 5))
    other = m.add_line((2, -5), (2, 5))
    hatch = m.add_hatch()
    hatch.paths.add_polyline_path([(20, 20), (30, 20), (30, 30), (20, 30)], is_closed=True)
    dim = m.add_linear_dim(base=(0, 7), p1=(0, 0), p2=(10, 0))
    dim.render()
    text = m.add_text('4-Φ12 +0.2/0')
    values = {e.dxf.handle: copy.deepcopy(e.dxfattribs()) for e in [other, hatch, dim.dimension, text]}
    report = split_intersections(doc, [sel(a), sel(b)], 1e-7)
    assert len(m.query('LINE')) == 5
    for handle, attrs in values.items():
        assert doc.entitydb[handle].dxfattribs() == attrs
    assert report['protected_count'] >= 3


def test_straight_polyline_gets_source_mapping_and_preserves_xdata(tmp_path):
    doc = ezdxf.new()
    doc.appids.add('CAD_SKILL_ID')
    doc.appids.add('CUSTOM_META')
    m = doc.modelspace()
    p = m.add_lwpolyline([(0, 0), (10, 0), (10, 10)])
    ph = p.dxf.handle
    p.set_xdata('CAD_SKILL_ID', [(1000, 'outline')])
    p.set_xdata('CUSTOM_META', [(1000, 'keep-me')])
    m.add_line((5, -1), (5, 1))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(report['mapping'][ph]) == 3
    assert report['source_ids'][ph] == 'outline'
    children = [doc.entitydb[h] for h in report['mapping'][ph]]
    assert all(e.get_xdata('CUSTOM_META')[0].value == 'keep-me' for e in children)
    ids = [e.get_xdata('CAD_SKILL_ID')[0].value for e in children]
    assert len(set(ids)) == 3
    assert verify_split(before, clone(doc, tmp_path, 'poly.dxf'), report, 1e-7)['status'] == 'passed'


def test_bulged_polyline_uses_analytic_arc_not_polyline_approximation(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    p = m.add_lwpolyline([(0, 0, 1), (4, 0, 0)], format='xyb')
    m.add_line((2, -3), (2, 1))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('ARC')) == 2
    assert verify_split(before, clone(doc, tmp_path, 'bulge.dxf'), report, 1e-7)['status'] == 'passed'


def test_explicit_unsupported_target_rejects_before_any_mutation():
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0))
    b = m.add_line((5, -1), (5, 1))
    spline = m.add_spline(fit_points=[(0, 0), (1, 2), (3, 0)])
    handles = [e.dxf.handle for e in m]
    with pytest.raises(RevisionError):
        split_intersections(doc, [sel(a), sel(b), sel(spline)], 1e-7)
    assert [e.dxf.handle for e in m] == handles


def test_default_unsupported_curve_is_retained_and_reported_partial(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    s = m.add_spline(fit_points=[(0, 0), (1, 2), (3, 0)])
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert report['status'] == 'partial'
    assert report['unsupported'][0]['handle'] == s.dxf.handle
    assert s.is_alive
    assert verify_split(before, doc, report, 1e-7)['status'] == 'partial'


@pytest.mark.parametrize('kind', ['nonplanar', 'extrusion', 'width'])
def test_unsupported_geometry_does_not_get_silently_flattened(kind):
    doc = ezdxf.new()
    m = doc.modelspace()
    if kind == 'nonplanar':
        e = m.add_line((0, 0, 2), (4, 4, 2))
    elif kind == 'extrusion':
        e = m.add_circle((0, 0), 2, dxfattribs={'extrusion': (0, 0, -1)})
    else:
        e = m.add_lwpolyline([(0, 0), (4, 4)], dxfattribs={'const_width': 2})
    original = copy.deepcopy(e.dxfattribs())
    with pytest.raises(RevisionError):
        split_intersections(doc, [sel(e)], 1e-7)
    assert e.dxfattribs() == original


def test_verifier_detects_translated_segment_despite_identical_total_length(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0))
    m.add_line((5, -5), (5, 5))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    target = next(e for e in m if e.dxf.start.y == e.dxf.end.y)
    target.translate(0, 1, 0)
    checked = verify_split(before, doc, report, 1e-7)
    assert checked['status'] == 'failed'
    assert checked['coverage_errors']


def test_annotation_geometry_only_cuts_when_explicitly_requested():
    doc = ezdxf.new()
    m = doc.modelspace()
    # At x=5 the native dimension has its text gap, so there is no true
    # intersection with its cached dimension line. x=2 crosses a real stroke.
    line = m.add_line((2, -1), (2, 3))
    dim = m.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(10, 0))
    dim.render()
    dh = dim.dimension.dxf.handle
    assert split_intersections(doc, None, 1e-7)['touched_handles'] == []
    result = split_intersections(doc, None, 1e-7, include_annotation_intersections=True)
    assert result['touched_handles'], result['unsupported']
    assert doc.entitydb[dh].dxftype() == 'DIMENSION'


@pytest.mark.parametrize('tol', [0, -1, float('nan'), float('inf'), True])
def test_invalid_tolerance_rejected_without_editing(tol):
    doc = ezdxf.new()
    e = doc.modelspace().add_line((0, 0), (1, 1))
    with pytest.raises(RevisionError):
        split_intersections(doc, None, tol)
    assert e.is_alive


def test_arc_circle_crossings_preserve_exact_circular_coverage(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_circle((0, 0), 5, dxfattribs={'color': 2})
    m.add_arc((4, 0), 5, 60, 300, dxfattribs={'color': 3})
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('ARC')) == 5
    assert all(e.dxf.radius == 5 for e in m)
    assert verify_split(before, clone(doc, tmp_path, 'circle_arc.dxf'), report, 1e-7)['status'] == 'passed'
    assert split_intersections(doc, None, 1e-7)['touched_handles'] == []


def test_coincident_arcs_split_only_overlap_endpoints(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_arc((0, 0), 5, 0, 180, dxfattribs={'color': 1})
    m.add_arc((0, 0), 5, 90, 270, dxfattribs={'color': 2})
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('ARC')) == 4
    checked = verify_split(before, clone(doc, tmp_path, 'arc_overlap.dxf'), report, 1e-7)
    assert checked['status'] == 'passed', checked
    assert checked['coincident_output_pairs'] == 1
    assert checked['introduced_duplicate_segments'] == 0


def test_closed_legacy_polyline_with_bulge_is_analytic(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    poly = m.add_polyline2d([(0, 0), (4, 0), (4, 4)], close=True)
    poly.vertices[0].dxf.bulge = 1
    m.add_line((2, -3), (2, 5))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert len(m.query('ARC')) == 2
    assert not m.query('POLYLINE')
    checked = verify_split(before, clone(doc, tmp_path, 'legacy_poly.dxf'), report, 1e-7)
    assert checked['status'] == 'passed', checked


def test_existing_overlapping_segments_within_one_polyline_keep_multiplicity(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0), (10, 0), (0, 0)])
    m.add_line((5, -1), (5, 1))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    checked = verify_split(before, doc, report, 1e-7)
    assert len(m.query('LINE')) == 6
    assert checked['status'] == 'passed', checked
    assert checked['coincident_output_pairs'] == 2
    assert checked['introduced_duplicate_segments'] == 0


def test_dimension_text_gap_is_not_an_intersection():
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_line((5, -1), (5, 3))
    m.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(10, 0)).render()
    result = split_intersections(doc, None, 1e-7, include_annotation_intersections=True)
    assert result['status'] == 'passed'
    assert not result['touched_handles']


@pytest.mark.parametrize('mutation', ['unmapped_new', 'missing_output', 'lost_source', 'shared_output', 'unsplit'])
def test_verifier_rejects_incomplete_mapping_and_output(tmp_path, mutation):
    doc = ezdxf.new()
    m = doc.modelspace()
    a = m.add_line((0, 0), (10, 0))
    b = m.add_line((5, -5), (5, 5))
    ah, bh = a.dxf.handle, b.dxf.handle
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    if mutation == 'unmapped_new':
        m.add_line((20, 20), (21, 21))
    elif mutation == 'missing_output':
        m.delete_entity(doc.entitydb[report['mapping'][ah][0]])
    elif mutation == 'lost_source':
        del report['mapping'][ah]
    elif mutation == 'shared_output':
        report['mapping'][bh] = report['mapping'][ah]
    else:
        report.update(mapping={ah: [ah], bh: [bh]}, touched_handles=[], created_handles=[], deleted_handles=[])
        doc = clone(before, tmp_path, 'unsplit.dxf')
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed', result
    if mutation == 'unsplit':
        assert result['unsplit_intersections']


def test_verifier_detects_unmapped_native_entity_change(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    hatch = m.add_hatch()
    hatch.paths.add_polyline_path([(20, 0), (21, 0), (21, 1)])
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    hatch.paths.clear()
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed'
    assert hatch.dxf.handle in result['preservation_errors']


def test_circle_to_arcs_preserves_application_data_and_extension_dictionary(tmp_path):
    doc = ezdxf.new()
    doc.appids.add('CUSTOM')
    m = doc.modelspace()
    circle = m.add_circle((0, 0), 2, dxfattribs={'color': 4, 'invisible': 1, 'ltscale': 2})
    handle = circle.dxf.handle
    circle.set_xdata('CUSTOM', [(1000, 'asset'), (1070, 7)])
    circle.set_app_data('CUSTOM', [(1000, 'private')])
    record = circle.new_extension_dict().add_xrecord('MY_RECORD')
    record.extend([(1, 'retain'), (90, 123)])
    m.add_line((-3, 0), (3, 0))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    reopened = clone(doc, tmp_path, 'circle_metadata.dxf')
    children = [reopened.entitydb[h] for h in report['mapping'][handle]]
    assert all(e.dxf.color == 4 and e.dxf.invisible == 1 and e.dxf.ltscale == 2 for e in children)
    assert all(e.get_xdata('CUSTOM') == before.entitydb[handle].get_xdata('CUSTOM') for e in children)
    assert all(e.get_app_data('CUSTOM') == before.entitydb[handle].get_app_data('CUSTOM') for e in children)
    assert all(list(e.get_extension_dict()['MY_RECORD'].tags) == list(before.entitydb[handle].get_extension_dict()['MY_RECORD'].tags) for e in children)
    assert verify_split(before, reopened, report, 1e-7)['status'] == 'passed'


def test_duplicate_output_is_reported_independently_of_mapping(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    m.add_line((0, 0), (10, 0))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    m.add_entity(next(iter(m)).copy())
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed'
    assert result['introduced_duplicate_segments'] == 1


def test_verifier_detects_changed_extension_record_on_a_child(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    source = m.add_line((0, 0), (10, 0))
    source.new_extension_dict().add_xrecord('DATA').extend([(1, 'original')])
    handle = source.dxf.handle
    m.add_line((5, -1), (5, 1))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    child = doc.entitydb[report['mapping'][handle][0]]
    child.get_extension_dict()['DATA'].reset([(1, 'changed')])
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed'
    assert result['metadata_errors']


def test_duplicate_count_respects_existing_pair_but_detects_third_copy(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    for color in (1, 2):
        m.add_line((0, 0), (10, 0), dxfattribs={'color': color})
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    assert verify_split(before, doc, report, 1e-7)['introduced_duplicate_segments'] == 0
    m.add_entity(next(iter(m)).copy())
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed'
    assert result['coincident_output_pairs'] == 3
    assert result['introduced_duplicate_segments'] == 2


def test_extension_xrecord_payload_reference_is_not_ignored_as_owner(tmp_path):
    doc = ezdxf.new()
    m = doc.modelspace()
    source = m.add_line((0, 0), (10, 0))
    a = m.add_text('first')
    b = m.add_text('second')
    source.new_extension_dict().add_xrecord('REF').extend([(330, a.dxf.handle)])
    source_handle = source.dxf.handle
    m.add_line((5, -1), (5, 1))
    before = clone(doc, tmp_path)
    report = split_intersections(doc, None, 1e-7)
    checked = verify_split(before, doc, report, 1e-7)
    assert checked['status'] == 'passed', str(checked)
    child = doc.entitydb[report['mapping'][source_handle][0]]
    child.get_extension_dict()['REF'].reset([(330, b.dxf.handle)])
    result = verify_split(before, doc, report, 1e-7)
    assert result['status'] == 'failed'
    assert result['metadata_errors']
