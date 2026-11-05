"""Read-only analytic diagnostics for planar circular contours and hatch edges.

Uses the same finite curve/intersection representation as splitting, without
mutating CAD or flattening arcs into sampled polygons.
"""
from __future__ import annotations

import math

from ezdxf.math import arc_angle_span_deg
from ezdxf.entities import factory

from .segments import _Curve, _intersections, _pairs


def _finite(values):
    return all(math.isfinite(float(v)) for v in values)


def _line(a, b, tol):
    if not _finite([*a, *b]) or math.dist(a, b) <= tol:
        raise ValueError('degenerate/nonfinite edge')
    return _Curve('line', tuple(a), tuple(b))


def _arc(center, radius, start, end, tol, full=False):
    if not _finite([*center, radius, start, end]) or radius <= tol:
        raise ValueError('invalid circular edge')
    span = math.tau if full else math.radians(arc_angle_span_deg(start, end))
    if span * radius <= tol:
        raise ValueError('degenerate arc')
    return _Curve('arc', tuple(center), radius=radius, start=math.radians(start) % math.tau,
                  span=span, full=span == math.tau)


def entity_curves(entity, tol):
    """Extract XY locus only; reactors do not inhibit a read-only diagnostic."""
    kind = entity.dxftype()
    if (entity.dxf.get('extrusion', (0, 0, 1)) != (0, 0, 1)
            or entity.dxf.get('thickness', 0) != 0):
        raise ValueError('unsupported extrusion/thickness')
    if kind == 'LINE':
        a, b = entity.dxf.start, entity.dxf.end
        if abs(a.z) > tol or abs(b.z) > tol:
            raise ValueError('nonplanar edge')
        return [_line(tuple(a)[:2], tuple(b)[:2], tol)]
    if kind in {'ARC', 'CIRCLE'}:
        c = entity.dxf.center
        if abs(c.z) > tol:
            raise ValueError('nonplanar circle')
        start, end = (0, 360) if kind == 'CIRCLE' else (entity.dxf.start_angle, entity.dxf.end_angle)
        return [_arc(tuple(c)[:2], entity.dxf.radius, start, end, tol, kind == 'CIRCLE')]
    if kind == 'LWPOLYLINE':
        if entity.has_width or not math.isfinite(entity.dxf.elevation) or abs(entity.dxf.elevation) > tol:
            raise ValueError('nonplanar/width polyline')
        if not all(_finite(p) for p in entity.get_points()):
            raise ValueError('nonfinite polyline')
        parts = list(entity.virtual_entities())
        if len(parts) != len(entity) - (not entity.closed) or not parts:
            raise ValueError('degenerate polyline')
        return [g for p in parts for g in entity_curves(p, tol)]
    raise ValueError('unsupported contour')


def hatch_curves(hatch, tol):
    if (hatch.dxf.extrusion != (0, 0, 1) or not _finite(hatch.dxf.elevation)
            or abs(hatch.dxf.elevation.z) > tol):
        raise ValueError('nonplanar hatch')
    result = []
    for path in hatch.paths:
        if hasattr(path, 'vertices'):
            if not path.is_closed:
                raise ValueError('open hatch polyline')
            poly = factory.new('LWPOLYLINE')
            poly.set_points(path.vertices, format='xyb')
            poly.closed = True
            result.extend(entity_curves(poly, tol))
        else:
            for edge in path.edges:
                if edge.EDGE_TYPE == 'LineEdge':
                    result.append(_line(edge.start, edge.end, tol))
                elif edge.EDGE_TYPE == 'ArcEdge':
                    # ezdxf exposes both CW and CCW hatch arcs with CCW start/end
                    # parameters; ccw controls traversal, not the covered locus.
                    result.append(_arc(edge.center, edge.radius, edge.start_angle, edge.end_angle, tol))
                else:
                    raise ValueError('unsupported hatch edge')
    return result


def extract(doc, tol):
    rows, hatches, handled, extras = [], {}, set(), set()
    for e in doc.modelspace():
        try:
            if e.dxftype() == 'HATCH':
                hatches[e.dxf.handle] = hatch_curves(e, tol)
            elif e.dxftype() in {'LINE', 'LWPOLYLINE', 'ARC', 'CIRCLE'}:
                curves = entity_curves(e, tol)
                rows.extend({'handle': e.dxf.handle, 'part': i, 'g': g} for i, g in enumerate(curves))
                if any(g.kind == 'arc' for g in curves):
                    extras.add(e.dxf.handle)
            else:
                continue
            handled.add(e.dxf.handle)
        except (ValueError, TypeError, ArithmeticError):
            pass
    return rows, hatches, handled, extras


def overlap(a, b, tol):
    if a.kind != b.kind:
        return []
    epsilon = tol / a.scale
    if a.kind == 'line':
        vx, vy = a.b[0] - a.a[0], a.b[1] - a.a[1]
        if any(abs(vx * (p[1] - a.a[1]) - vy * (p[0] - a.a[0])) / a.scale > tol for p in [b.a, b.b]):
            return []
        lo, hi = sorted(((p[0] - a.a[0]) * vx + (p[1] - a.a[1]) * vy) / a.scale**2 for p in [b.a, b.b])
        intervals = [(max(0., lo), min(1., hi))]
    else:
        if math.dist(a.a, b.a) > tol or abs(a.radius - b.radius) > tol:
            return []
        start = (b.start - a.start) % math.tau
        intervals = [(max(0., start + shift), min(a.span, start + shift + b.span))
                     for shift in (-math.tau, 0., math.tau)]
    return [(lo, hi) for lo, hi in intervals if hi - lo > epsilon]


def covers(curve, others, tol):
    intervals = sorted(iv for other in others for iv in overlap(curve, other, tol))
    end = 0.
    epsilon = tol / curve.scale
    for lo, hi in intervals:
        if lo > end + epsilon:
            return False
        end = max(end, hi)
    return end >= curve.span - epsilon


def portion(curve, lo, hi):
    if curve.kind == 'line':
        return _Curve('line', curve.point(lo), curve.point(hi))
    return _Curve('arc', curve.a, radius=curve.radius, start=(curve.start + lo) % math.tau,
                  span=hi - lo, full=hi - lo == math.tau)


def contacts(curve, others, tol):
    return [portion(curve, lo, hi) for other in others for lo, hi in overlap(curve, other, tol)]


def nearest(curve, point, tol):
    if curve.kind == 'line':
        dx, dy = curve.b[0] - curve.a[0], curve.b[1] - curve.a[1]
        t = ((point[0] - curve.a[0]) * dx + (point[1] - curve.a[1]) * dy) / curve.scale**2
        return curve.point(max(0., min(1., t)))
    angle = math.atan2(point[1] - curve.a[1], point[0] - curve.a[0])
    t = (angle - curve.start) % math.tau
    if curve.full or t <= curve.span:
        return curve.point(t)
    return min([curve.point(0), curve.point(curve.span)], key=lambda p: math.dist(p, point))


def cuts_box(curve, box, tol):
    lowx, lowy, highx, highy = box[0] + tol, box[1] + tol, box[2] - tol, box[3] - tol
    if lowx >= highx or lowy >= highy:
        return False
    vertices = [(lowx, lowy), (highx, lowy), (highx, highy), (lowx, highy)]
    cuts = [0., curve.span]
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        edge = _Curve('line', a, b)
        for p in _intersections(curve, edge, tol):
            t = curve.parameter(p, tol)
            if t is not None:
                cuts.append(t)
    cuts.sort()
    return any(lowx < p[0] < highx and lowy < p[1] < highy
               for lo, hi in zip(cuts, cuts[1:]) if hi > lo
               for p in [curve.point((lo + hi) / 2)])


def diagnose(before, after, regions, tol, old_texts, texts):
    old, old_hatches, old_handled, old_extra = extract(before, tol)
    new, hatches, handled, extra = extract(after, tol)
    inside = lambda box: any(box[0] <= r[2] and r[0] <= box[2] and box[1] <= r[3] and r[1] <= box[3] for r in regions)
    old = [r for r in old if inside(r['g'].bounds)]
    new = [r for r in new if inside(r['g'].bounds)]
    now = {(r['handle'], r['part']): r['g'] for r in new}
    new_geometry = [r['g'] for r in new]
    issues = []
    def add(code, handles, point, **details):
        issues.append(dict(code=code, handles=sorted(set(handles)), location=list(point),
                           severity='review', introduced=True, **details))
    def overlaps(rows, extras):
        out = []
        for i, j in _pairs([r['g'] for r in rows], tol):
            a, b = rows[i], rows[j]
            if not {a['handle'], b['handle']} & extras:
                continue
            for contact in contacts(a['g'], [b['g']], tol):
                out.append((a, b, contact))
        return out
    old_overlaps = [g for _, _, g in overlaps(old, old_extra)]
    for a, b, g in overlaps(new, extra):
        add('overlapping_edges', [a['handle'], b['handle']], g.point(g.span / 2),
            overlap_length=g.scale * g.span)
        issues[-1]['introduced'] = not covers(g, old_overlaps, tol)
    for i, j in _pairs([r['g'] for r in old], tol):
        a, b = old[i], old[j]
        if not {a['handle'], b['handle']} & old_extra or contacts(a['g'], [b['g']], tol):
            continue
        points = _intersections(a['g'], b['g'], tol)
        if not points or (covers(a['g'], new_geometry, tol) and covers(b['g'], new_geometry, tol)):
            continue
        aa, bb = now.get((a['handle'], a['part'])), now.get((b['handle'], b['part']))
        if aa is None or bb is None:
            add('removed_connected_edge', [a['handle'], b['handle']], points[0])
            continue
        # A pair can have two distinct contacts. Retaining one does not prove
        # the other survived; follow each original contact separately.
        for p in points:
            t = a['g'].parameter(p, tol)
            q = aa.point(t / a['g'].span * aa.span)
            r = nearest(bb, q, tol)
            gap = math.dist(q, r)
            if gap > tol and not covers(_Curve('line', q, r), new_geometry, tol):
                add('opened_joint', [a['handle'], b['handle']], ((q[0] + r[0]) / 2, (q[1] + r[1]) / 2), gap=gap)
    for handle, edges in old_hatches.items():
        if not any(inside(g.bounds) for g in edges):
            continue
        curved_hatch = any(g.kind == 'arc' for g in edges)
        if handle not in hatches:
            if curved_hatch and (after.entitydb.get(handle) is None or not after.entitydb[handle].is_alive):
                add('removed_hatch', [handle], edges[0].point(0))
            continue
        for row in old:
            if not curved_hatch and row['handle'] not in old_extra:
                continue
            g = row['g']
            current = now.get((row['handle'], row['part']))
            for contact in contacts(g, edges, tol):
                persistent = contacts(contact, hatches[handle], tol)
                lost = next((part for part in persistent if not covers(part, new_geometry, tol)), None)
                if lost is not None:
                    add('hatch_boundary_detached', [handle, row['handle']], lost.point(lost.span / 2))
                    break
                if covers(contact, new_geometry, tol) and covers(contact, hatches[handle], tol):
                    continue
                if current is None:
                    add('hatch_boundary_detached', [handle, row['handle']], contact.point(0))
                    break
                lo = g.parameter(contact.point(0), tol)
                if lo is None:
                    continue
                span = contact.scale / g.scale if g.kind == 'line' else contact.span
                moved = portion(current, lo / g.span * current.span,
                                min(current.span, (lo + span) / g.span * current.span))
                if not covers(moved, hatches[handle], tol):
                    add('hatch_boundary_detached', [handle, row['handle']], moved.point(moved.span / 2))
                    break
    prior_text = [(h, row['handle']) for h, box in old_texts for row in old
                  if row['handle'] in old_extra and cuts_box(row['g'], box, tol)]
    for h, box in texts:
        if not inside(box):
            continue
        for row in new:
            if row['handle'] in extra and cuts_box(row['g'], box, tol):
                add('text_contour_interference', [h, row['handle']], ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), approximate=True)
                issues[-1]['introduced'] = (h, row['handle']) not in prior_text
    return issues, old_handled, handled
