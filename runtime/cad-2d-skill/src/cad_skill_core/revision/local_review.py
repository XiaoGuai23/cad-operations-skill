"""Read-only, bounded neighboring-change diagnostics and matched CAD views.

Findings are review candidates. Geometry checks cover straight planar boundaries;
display rendering and approximate text boxes are not engineering acceptance.
"""
from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
import tempfile

import ezdxf
from ezdxf import bbox
from ezdxf.math import Vec2

from .common import RevisionError, compare_untouched, sha256_file, snapshot_modelspace, validate_tolerance


def _box(entity):
    box = bbox.extents([entity], fast=True)
    if not box.has_data:
        return None
    values = [box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y]
    return values if all(math.isfinite(v) for v in values) else None


def _intersects(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _segment_box(segment):
    a, b = segment['a'], segment['b']
    return [min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)]


def _straight(entity, tol):
    kind = entity.dxftype()
    if kind == 'LINE':
        a, b = entity.dxf.start, entity.dxf.end
        if abs(a.z) > tol or abs(b.z) > tol or abs(entity.dxf.get('thickness', 0)) > tol:
            return None
        pairs = [(Vec2(a), Vec2(b))]
    elif kind == 'LWPOLYLINE':
        points = list(entity.get_points('xyseb'))
        if (abs(entity.dxf.elevation) > tol or entity.dxf.extrusion != (0, 0, 1)
                or entity.dxf.get('const_width', 0) or any(p[2] or p[3] or p[4] for p in points)):
            return None
        vertices = [Vec2(p[:2]) for p in points]
        pairs = list(zip(vertices, vertices[1:] + (vertices[:1] if entity.closed else [])))
    else:
        return None
    return [{'handle': entity.dxf.handle, 'part': i, 'a': a, 'b': b}
            for i, (a, b) in enumerate(pairs) if a.distance(b) > tol]


def _hatch_edges(entity, tol):
    if abs(entity.dxf.elevation.z) > tol or entity.dxf.extrusion != (0, 0, 1):
        return None
    result = []
    for path in entity.paths:
        if hasattr(path, 'vertices'):
            if not path.is_closed or any(p[2] for p in path.vertices):
                return None
            vertices = [Vec2(p[:2]) for p in path.vertices]
            pairs = list(zip(vertices, vertices[1:] + vertices[:1]))
        else:
            if any(e.EDGE_TYPE != 'LineEdge' for e in path.edges):
                return None
            pairs = [(Vec2(e.start), Vec2(e.end)) for e in path.edges]
        result.extend({'handle': entity.dxf.handle, 'part': len(result) + i, 'a': a, 'b': b}
                      for i, (a, b) in enumerate(pairs) if a.distance(b) > tol)
    return result


def _interval(target, other, tol):
    a, b = target['a'], target['b']
    delta = b - a
    length = delta.magnitude
    unit = delta / length
    if any(abs(unit.det(p - a)) > tol for p in [other['a'], other['b']]):
        return None
    lo, hi = sorted(unit.dot(p - a) for p in [other['a'], other['b']])
    lo, hi = max(0, lo), min(length, hi)
    return (lo, hi) if hi - lo > tol else None


def _covers(target, lines, tol):
    intervals = sorted(x for line in lines if (x := _interval(target, line, tol)) is not None)
    end = 0.0
    for lo, hi in intervals:
        if lo > end + tol:
            return False
        end = max(end, hi)
    return end >= target['a'].distance(target['b']) - tol


def _overlap_parts(target, lines, tol):
    unit = (target['b'] - target['a']).normalize()
    return [{'a': target['a'] + unit * interval[0],
             'b': target['a'] + unit * interval[1]}
            for line in lines if (interval := _interval(target, line, tol)) is not None]


def _cuts_box(line, box, tol):
    # Clip to the interior: touching a text-box edge alone is not interference.
    lo, hi = 0., 1.
    for index in range(2):
        start = line['a'][index]
        delta = line['b'][index] - start
        low, high = box[index] + tol, box[index + 2] - tol
        if low >= high:
            return False
        if abs(delta) <= tol:
            if not low < start < high:
                return False
        else:
            a, b = sorted(((low - start) / delta, (high - start) / delta))
            lo, hi = max(lo, a), min(hi, b)
    return hi > lo and hi > 0 and lo < 1


def _nearest(point, line):
    delta = line['b'] - line['a']
    fraction = max(0., min(1., (point - line['a']).dot(delta) / delta.dot(delta)))
    return line['a'] + delta * fraction


def _objects(doc, tol):
    lines, hatches, texts, unsupported = [], {}, [], []
    for e in doc.modelspace():
        kind = e.dxftype()
        if kind in {'LINE', 'LWPOLYLINE'}:
            segments = _straight(e, tol)
            if segments is not None:
                lines.extend(segments)
                continue
        elif kind == 'HATCH':
            edges = _hatch_edges(e, tol)
            if edges is not None:
                hatches[e.dxf.handle] = edges
                continue
        elif kind in {'TEXT', 'MTEXT'}:
            if (box := _box(e)) is not None:
                texts.append((e.dxf.handle, box))
            else:
                unsupported.append({'handle': e.dxf.handle, 'type': kind, 'reason': 'text bounds unavailable'})
            continue
        elif kind in {'DIMENSION', 'MULTILEADER', 'LEADER'}:
            try:
                for part in e.virtual_entities():
                    if part.dxftype() in {'TEXT', 'MTEXT'} and (box := _box(part)) is not None:
                        texts.append((e.dxf.handle, box))
            except (AttributeError, NotImplementedError, ValueError):
                unsupported.append({'handle': e.dxf.handle, 'type': kind, 'reason': 'text display unavailable'})
            continue
        unsupported.append({'handle': e.dxf.handle, 'type': kind, 'reason': 'geometric diagnostic does not support this object'})
    return lines, hatches, texts, unsupported


def _regions(before, after, changed, padding, bounds):
    if bounds is not None:
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4
                or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise RevisionError('bounds require finite xmin ymin xmax ymax with positive area')
        return [list(bounds)]
    boxes = []
    for h in sorted(changed):
        for doc in (before, after):
            entity = doc.entitydb.get(h)
            if entity is not None and entity.is_alive and (b := _box(entity)) is not None:
                boxes.append([b[0] - padding, b[1] - padding, b[2] + padding, b[3] + padding])
    merged = []
    for box in boxes:
        while True:
            hit = next((i for i, old in enumerate(merged) if _intersects(box, old)), None)
            if hit is None:
                break
            old = merged.pop(hit)
            box = [min(box[0], old[0]), min(box[1], old[1]), max(box[2], old[2]), max(box[3], old[3])]
        merged.append(box)
    if len(merged) > 30:
        raise RevisionError('more than 30 changed regions; review explicit bounds in separate batches')
    return sorted(merged)


def analyze_documents(before, after, *, bounds=None, padding=5., tolerance=1e-6):
    tol = validate_tolerance(tolerance)
    padding = validate_tolerance(padding)
    if before.units != after.units:
        raise RevisionError('before/after units differ; no implicit coordinate conversion')
    changed = set(compare_untouched(snapshot_modelspace(before), snapshot_modelspace(after), set(), tol))
    regions = _regions(before, after, changed, padding, bounds)
    old_lines, old_hatches, old_texts, old_unsupported = _objects(before, tol)
    lines, hatches, texts, unsupported = _objects(after, tol)
    in_region = lambda box: any(_intersects(box, r) for r in regions)
    old_lines = [l for l in old_lines if in_region(_segment_box(l))]
    lines = [l for l in lines if in_region(_segment_box(l))]
    issues = []
    def add(code, handles, point, *, introduced=True, **details):
        issues.append({'code': code, 'handles': sorted(set(handles)), 'location': list(point),
                       'introduced': introduced, 'severity': 'review', **details})
    def overlaps(segments):
        result = []
        for i, a in enumerate(segments):
            for b in segments[i + 1:]:
                if _intersects(_segment_box(a), _segment_box(b)) and (iv := _interval(a, b, tol)):
                    start = a['a'] + (a['b'] - a['a']).normalize() * iv[0]
                    end = a['a'] + (a['b'] - a['a']).normalize() * iv[1]
                    result.append((a, b, start, end))
        return result
    previous = overlaps(old_lines)
    for a, b, start, end in overlaps(lines):
        existed = any(_covers({'a': start, 'b': end}, [{'a': p, 'b': q}], tol) for _, _, p, q in previous)
        add('overlapping_edges', [a['handle'], b['handle']], (start + end) / 2,
            introduced=not existed, overlap_length=start.distance(end))
    by_part = {(l['handle'], l['part']): l for l in lines}
    seen_joints = set()
    for a in old_lines:
        for b in old_lines:
            if a is b:
                continue
            for ia in ('a', 'b'):
                if a[ia].distance(_nearest(a[ia], b)) > tol:
                    continue
                aa, bb = by_part.get((a['handle'], a['part'])), by_part.get((b['handle'], b['part']))
                joint = (tuple(sorted([a['handle'], b['handle']])), tuple(a[ia]))
                if joint in seen_joints:
                    continue
                if aa is None or bb is None:
                    missing = a if aa is None else b
                    if (aa is not None or bb is not None) and not _covers(missing, lines, tol):
                        seen_joints.add(joint)
                        add('removed_connected_edge', [a['handle'], b['handle']], a[ia])
                    continue
                nearest = _nearest(aa[ia], bb)
                gap = aa[ia].distance(nearest)
                if gap > tol and not _covers({'a': aa[ia], 'b': nearest}, lines, tol):
                    seen_joints.add(joint)
                    add('opened_joint', [a['handle'], b['handle']], (aa[ia] + nearest) / 2, gap=gap)
    for handle, edges in old_hatches.items():
        if not any(in_region(_segment_box(e)) for e in edges):
            continue
        if handle not in hatches:
            if after.entitydb.get(handle) is None or not after.entitydb[handle].is_alive:
                add('removed_hatch', [handle], edges[0]['a'] if edges else (0, 0))
            continue
        for line in old_lines:
            contacts = _overlap_parts(line, edges, tol)
            if not contacts:
                continue
            current = by_part.get((line['handle'], line['part']))
            for contact in contacts:
                # Check both directions: a shortened contour can still lie on
                # the hatch while leaving previously outlined hatch uncovered.
                lost = next((part for part in _overlap_parts(contact, hatches[handle], tol)
                             if not _covers(part, lines, tol)), None)
                if lost is not None:
                    add('hatch_boundary_detached', [handle, line['handle']], (lost['a'] + lost['b']) / 2,
                        reason='remaining hatch boundary lost previous contour coverage')
                    break
                if _covers(contact, lines, tol) and _covers(contact, hatches[handle], tol):
                    continue  # equivalent split/replacement retains the contact
                if current is None:
                    add('hatch_boundary_detached', [handle, line['handle']], (contact['a'] + contact['b']) / 2,
                        reason='previous boundary contour is no longer present')
                    break
                # Only follow the portion that originally touched this hatch;
                # a longer contour may legitimately extend past its boundary.
                old_delta = line['b'] - line['a']
                delta = current['b'] - current['a']
                moved = {key: current['a'] + delta * ((contact[key] - line['a']).dot(old_delta) / old_delta.dot(old_delta))
                         for key in ('a', 'b')}
                if not _covers(moved, hatches[handle], tol):
                    add('hatch_boundary_detached', [handle, line['handle']], (moved['a'] + moved['b']) / 2)
                    break
    old_interference = {(h, l['handle']) for h, box in old_texts for l in old_lines if _cuts_box(l, box, tol)}
    for h, box in texts:
        if not in_region(box):
            continue
        for line in lines:
            if _cuts_box(line, box, tol):
                add('text_contour_interference', [h, line['handle']], ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
                    introduced=(h, line['handle']) not in old_interference, approximate=True)
    from .review_curves import diagnose
    curve_issues, old_handled, handled = diagnose(before, after, regions, tol, old_texts, texts)
    issues.extend(curve_issues)
    unsupported = [i for i in unsupported if i['handle'] not in handled]
    old_unsupported = [i for i in old_unsupported if i['handle'] not in old_handled]
    return {'schema': 'cad-local-review/v1', 'status': 'needs_review' if any(i['introduced'] for i in issues) else 'checked_with_limits',
            'changed_handles': sorted(changed), 'regions': regions, 'issues': issues, 'units': before.units,
            'coverage': {'checked': ['planar straight/circular overlaps', 'retained endpoint and circular contacts',
                                    'line/arc hatch-contour alignment', 'approximate text boxes'],
                         'not_analyzed': unsupported, 'baseline_not_analyzed': old_unsupported},
            'manual_visual_review_required': True, 'native_interaction_tested': False,
            'limitations': ['Findings need intent-aware review; no geometry is changed.',
                            'Ellipse/spline topology, removed-edge endpoint correspondence and geometric associativity are not proved.',
                            'Font/text extents and displayed colors may differ in native CAD.']}


def _render(doc, regions, output, label, *, font_map=None, evidence=None):
    from .preview import render_regions
    report = render_regions(doc, regions, output, label, font_map=font_map)
    if evidence is not None:
        evidence[label] = report
    return report['images']


def review_files(before_path, after_path, output_dir, **options):
    from .workflow import _source
    before_path, before_hash, before = _source(Path(before_path))
    after_path, after_hash, after = _source(Path(after_path))
    font_map = options.pop('font_map', None)
    report = analyze_documents(before, after, **options)
    output = Path(output_dir).resolve()
    if output.exists():
        raise RevisionError('review output must be a new directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cad-review-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'publish'
        stage.mkdir()
        render_evidence = {}
        images = {label: _render(doc, report['regions'], stage, label, font_map=font_map, evidence=render_evidence)
                  for label, doc in [('before', before), ('after', after)]}
        report.update(before_path=str(before_path), after_path=str(after_path),
                      before_sha256=before_hash, after_sha256=after_hash, images=images,
                      render_evidence=render_evidence)
        report['artifacts'] = {p.name: sha256_file(p) for p in stage.glob('*.png')}
        content = ['<!doctype html><meta charset="utf-8"><title>CAD 局部修改核对</title>',
                   '<style>body{font:16px sans-serif;margin:24px}.pair{display:flex;gap:12px}.pair img{width:49%;object-fit:contain}td,th{padding:8px;border:1px solid #ccc}table{border-collapse:collapse}</style>',
                   '<h1>CAD 局部修改核对</h1><p>左右图使用相同坐标范围与比例。候选问题需要结合修改意图核对；本检查不改动 CAD。</p>']
        for index, region in enumerate(report['regions']):
            content.append(f'<h2>区域 {index + 1}</h2><p>坐标范围：{html.escape(str(region))}</p><div class="pair">'
                           f'<img alt="修改前" src="{images["before"][index]}"><img alt="修改后" src="{images["after"][index]}"></div>')
        content.append('<h2>检查记录</h2><table><tr><th>候选问题</th><th>对象</th><th>是否新增</th></tr>')
        for issue in report['issues']:
            content.append(f'<tr><td>{html.escape(issue["code"])}</td><td>{html.escape(", ".join(issue["handles"]))}</td><td>{"新增" if issue["introduced"] else "原有"}</td></tr>')
        content.append('</table><h2>检查边界</h2><pre>' + html.escape(json.dumps(report['coverage'], ensure_ascii=False, indent=2)) + '</pre>')
        (stage / 'index.html').write_text('\n'.join(content), encoding='utf-8')
        (stage / 'review-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        if sha256_file(before_path) != before_hash or sha256_file(after_path) != after_hash:
            raise RevisionError('CAD input changed during review')
        if output.exists():
            raise RevisionError('review output appeared during execution')
        os.rename(stage, output)
    return report
