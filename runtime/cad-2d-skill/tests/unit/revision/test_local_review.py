import copy
import importlib

import ezdxf
import pytest


def analyze(before, after, **kwargs):
    module = importlib.import_module('cad_skill_core.revision.local_review')
    return module.analyze_documents(before, after, **kwargs)


def fixture():
    doc = ezdxf.new('R2018', setup=True)
    doc.units = 4
    a = doc.modelspace().add_line((0, 0), (10, 0))
    b = doc.modelspace().add_line((10, 0), (10, 10))
    return doc, a.dxf.handle, b.dxf.handle


def new_codes(report):
    return {x['code'] for x in report['issues'] if x['introduced']}


def test_detects_opened_joint_without_mutating_either_document():
    before, a, b = fixture()
    after = copy.deepcopy(before)
    after.entitydb[b].dxf.start = (10, 0.4, 0)
    report = analyze(before, after)
    assert 'opened_joint' in new_codes(report)
    assert tuple(before.entitydb[b].dxf.start) == (10, 0, 0)
    assert tuple(after.entitydb[b].dxf.start) == (10, 0.4, 0)


def test_distinguishes_existing_overlap_from_new_overlap():
    before, a, b = fixture()
    before.modelspace().add_line((2, 0), (5, 0))
    after = copy.deepcopy(before)
    after.modelspace().add_line((10, 2), (10, 7))
    report = analyze(before, after)
    overlap = [x for x in report['issues'] if x['code'] == 'overlapping_edges']
    assert any(x['introduced'] for x in overlap)
    assert any(not x['introduced'] for x in overlap)


def test_shared_endpoint_is_not_an_overlap():
    before, a, b = fixture()
    after = copy.deepcopy(before)
    after.modelspace().add_line((10, 10), (20, 10))
    assert 'overlapping_edges' not in new_codes(analyze(before, after))


def test_flags_stale_hatch_edge_but_accepts_updated_hatch():
    before, a, b = fixture()
    h = before.modelspace().add_hatch()
    h.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    after = copy.deepcopy(before)
    after.entitydb[a].dxf.start = (0, -1, 0)
    after.entitydb[a].dxf.end = (10, -1, 0)
    assert 'hatch_boundary_detached' in new_codes(analyze(before, after))
    after.entitydb[h.dxf.handle].paths[0].vertices = [(0, -1, 0), (10, -1, 0), (10, 10, 0), (0, 10, 0)]
    assert 'hatch_boundary_detached' not in new_codes(analyze(before, after))


def test_removed_hatch_and_new_text_interference_are_reported():
    before, a, b = fixture()
    h = before.modelspace().add_hatch()
    h.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[h.dxf.handle])
    after.modelspace().add_text('TEST', dxfattribs={'insert': (3, -0.5), 'height': 2})
    codes = new_codes(analyze(before, after))
    assert 'removed_hatch' in codes
    assert 'text_contour_interference' in codes


def test_split_coverage_does_not_create_false_hatch_detachment_or_gap():
    before, a, b = fixture()
    h = before.modelspace().add_hatch()
    h.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[a])
    after.modelspace().add_line((0, 0), (4, 0))
    after.modelspace().add_line((4, 0), (10, 0))
    assert not {'opened_joint', 'hatch_boundary_detached'} & new_codes(analyze(before, after))


def test_distant_edits_get_separate_local_views():
    before, a, b = fixture()
    far = before.modelspace().add_line((1000, 1000), (1010, 1000))
    after = copy.deepcopy(before)
    after.entitydb[a].dxf.end = (9, 0)
    after.entitydb[far.dxf.handle].dxf.end = (1009, 1000)
    report = analyze(before, after, padding=2)
    assert len(report['regions']) == 2


def test_unsupported_curve_is_visible_in_coverage_not_claimed_checked():
    before, a, b = fixture()
    after = copy.deepcopy(before)
    after.modelspace().add_spline([(1, 1), (2, 3), (4, 2)])
    report = analyze(before, after)
    assert any(e['type'] == 'SPLINE' for e in report['coverage']['not_analyzed'])
    assert report['manual_visual_review_required']


@pytest.mark.parametrize('kwargs', [{'padding': 0}, {'tolerance': float('nan')}, {'bounds': [2, 2, 1, 1]}])
def test_rejects_invalid_review_geometry(kwargs):
    before, _, _ = fixture()
    with pytest.raises(ValueError):
        analyze(before, copy.deepcopy(before), **kwargs)


def test_units_must_match():
    before, _, _ = fixture()
    after = copy.deepcopy(before)
    after.units = 1
    with pytest.raises(ValueError, match='units'):
        analyze(before, after)


def test_t_junction_opening_and_removed_hatch_contour_are_not_missed():
    before, a, b = fixture()
    t = before.modelspace().add_line((5, 0), (5, -3))
    hatch = before.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    after = copy.deepcopy(before)
    after.entitydb[t.dxf.handle].dxf.start = (5, -0.5)
    assert 'opened_joint' in new_codes(analyze(before, after))
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[a])
    assert 'hatch_boundary_detached' in new_codes(analyze(before, after))


@pytest.mark.parametrize('extent', [(0, 40), (-10, 50)])
def test_hatch_checks_lost_coverage_and_partial_contour_contact(extent):
    before = ezdxf.new('R2018', setup=True)
    before.units = 4
    line = before.modelspace().add_line((extent[0], 0), (extent[1], 0))
    hatch = before.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (40, 0), (40, 20), (0, 20)], is_closed=True)
    after = copy.deepcopy(before)
    current = after.entitydb[line.dxf.handle]
    current.dxf.end = (25, 0)
    assert 'hatch_boundary_detached' in new_codes(analyze(before, after))
    after.modelspace().add_line((25, 0), (extent[1], 0))
    assert 'hatch_boundary_detached' not in new_codes(analyze(before, after))

    after = copy.deepcopy(before)
    current = after.entitydb[line.dxf.handle]
    current.dxf.start = (extent[0], -2)
    current.dxf.end = (extent[1], -2)
    assert 'hatch_boundary_detached' in new_codes(analyze(before, after))
    after.entitydb[hatch.dxf.handle].paths[0].vertices = [
        (0, -2, 0), (40, -2, 0), (40, 20, 0), (0, 20, 0)]
    assert 'hatch_boundary_detached' not in new_codes(analyze(before, after))
