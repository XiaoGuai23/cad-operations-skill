import copy
import math

import ezdxf
import pytest

from cad_skill_core.revision.local_review import analyze_documents


def codes(before, after):
    return {i['code'] for i in analyze_documents(before, after)['issues'] if i['introduced']}


def drawing():
    doc = ezdxf.new('R2018', setup=True)
    doc.units = 4
    return doc


def test_circle_and_wrapping_arc_overlap_are_analytic_and_baseline_aware():
    before = drawing()
    circle = before.modelspace().add_circle((0, 0), 10)
    before.modelspace().add_arc((0, 0), 10, 350, 10)
    after = copy.deepcopy(before)
    after.modelspace().add_arc((0, 0), 10, 90, 180)
    report = analyze_documents(before, after)
    overlaps = [i for i in report['issues'] if i['code'] == 'overlapping_edges']
    assert any(i['introduced'] and i['overlap_length'] == pytest.approx(5 * math.pi) for i in overlaps)
    assert any(not i['introduced'] for i in overlaps)
    assert not any(i['type'] in {'CIRCLE', 'ARC'} for i in report['coverage']['not_analyzed'])


def test_tangent_line_contact_and_arc_endpoint_opening():
    before = drawing()
    before.modelspace().add_circle((0, 0), 10)
    line = before.modelspace().add_line((10, 0), (10, 20))
    after = copy.deepcopy(before)
    after.entitydb[line.dxf.handle].dxf.start = (11, 0)
    after.entitydb[line.dxf.handle].dxf.end = (11, 20)
    assert 'opened_joint' in codes(before, after)
    before = drawing()
    before.modelspace().add_arc((0, 0), 10, 0, 90)
    other = before.modelspace().add_arc((0, 0), 10, 90, 180)
    after = copy.deepcopy(before)
    after.entitydb[other.dxf.handle].dxf.start_angle = 95
    assert 'opened_joint' in codes(before, after)


def test_tangent_circles_and_equivalent_arc_split():
    before = drawing()
    c = before.modelspace().add_circle((0, 0), 10)
    other = before.modelspace().add_circle((20, 0), 10)
    after = copy.deepcopy(before)
    after.entitydb[other.dxf.handle].dxf.center = (21, 0)
    assert 'opened_joint' in codes(before, after)
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[c.dxf.handle])
    after.modelspace().add_arc((0, 0), 10, 0, 180)
    after.modelspace().add_arc((0, 0), 10, 180, 360)
    assert not codes(before, after)


@pytest.mark.parametrize('ccw', [True, False])
def test_circular_hatch_stale_radius_and_updated_boundary(ccw):
    before = drawing()
    c = before.modelspace().add_circle((0, 0), 10)
    h = before.modelspace().add_hatch()
    edge = h.paths.add_edge_path().add_arc((0, 0), 10, 0, 360, ccw=ccw)
    after = copy.deepcopy(before)
    after.entitydb[c.dxf.handle].dxf.radius = 11
    assert 'hatch_boundary_detached' in codes(before, after)
    after.entitydb[h.dxf.handle].paths[0].edges[0].radius = 11
    assert 'hatch_boundary_detached' not in codes(before, after)


def test_circle_replaced_by_incomplete_arcs_reports_lost_hatch_coverage():
    before = drawing()
    c = before.modelspace().add_circle((0, 0), 10)
    h = before.modelspace().add_hatch()
    h.paths.add_edge_path().add_arc((0, 0), 10)
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[c.dxf.handle])
    after.modelspace().add_arc((0, 0), 10, 0, 170)
    after.modelspace().add_arc((0, 0), 10, 180, 360)
    assert 'hatch_boundary_detached' in codes(before, after)
    after.modelspace().add_arc((0, 0), 10, 170, 180)
    assert 'hatch_boundary_detached' not in codes(before, after)


def test_bulge_polyline_hatch_contact_is_checked():
    before = drawing()
    c = before.modelspace().add_lwpolyline([(-10, 0, 1), (10, 0, 1)], format='xyb', close=True)
    h = before.modelspace().add_hatch()
    h.paths.add_polyline_path([(-10, 0, 1), (10, 0, 1)], is_closed=True)
    after = copy.deepcopy(before)
    after.entitydb[c.dxf.handle].translate(0, -2, 0)
    assert 'hatch_boundary_detached' in codes(before, after)
    report = analyze_documents(before, after)
    assert not any(i['handle'] in {c.dxf.handle, h.dxf.handle} for i in report['coverage']['not_analyzed'])


def test_text_crossing_circle_and_nonplanar_coverage():
    before = drawing()
    before.modelspace().add_circle((0, 0), 10)
    after = copy.deepcopy(before)
    after.modelspace().add_text('TEST', dxfattribs={'height': 3, 'insert': (8, -1)})
    tilted = after.modelspace().add_arc((0, 0), 10, 0, 90, dxfattribs={'extrusion': (0, 1, 0)})
    report = analyze_documents(before, after)
    assert 'text_contour_interference' in codes(before, after)
    assert any(i['handle'] == tilted.dxf.handle for i in report['coverage']['not_analyzed'])


def test_touching_arc_endpoints_are_not_overlaps():
    before = drawing()
    before.modelspace().add_arc((0, 0), 10, 350, 10)
    after = copy.deepcopy(before)
    after.modelspace().add_arc((0, 0), 10, 10, 90)
    assert 'overlapping_edges' not in codes(before, after)


def test_one_remaining_arc_contact_does_not_hide_another_open_endpoint():
    before = drawing()
    before.modelspace().add_arc((0, 0), 10, 0, 180)
    other = before.modelspace().add_arc((0, 0), 10, 180, 360)
    after = copy.deepcopy(before)
    after.entitydb[other.dxf.handle].dxf.start_angle = 185
    assert 'opened_joint' in codes(before, after)


def test_mixed_hatch_partial_line_contact_can_move_together():
    before = drawing()
    line = before.modelspace().add_line((-20, 0), (20, 0))
    h = before.modelspace().add_hatch()
    path = h.paths.add_edge_path()
    path.add_line((-10, 0), (10, 0))
    path.add_arc((0, 0), 10, 0, 180)
    after = copy.deepcopy(before)
    after.entitydb[line.dxf.handle].translate(0, -2, 0)
    after.entitydb[h.dxf.handle].translate(0, -2, 0)
    assert 'hatch_boundary_detached' not in codes(before, after)
