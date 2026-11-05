"""Read-only, hash-bound comparison of explicit drawing/reference features."""
from __future__ import annotations

import html
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .common import RevisionError, sha256_file, validate_tolerance
from .drawing_set import UNITS, measure_feature, read_dxf_snapshot
from .workflow import _json, _pairs


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise RevisionError(f'invalid reference fields; required: {sorted(required)}')


def _number(value):
    try:
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError
        return float(value)
    except (ValueError, OverflowError):
        raise RevisionError('reference values must be finite numbers') from None


def _bounds(value):
    if not isinstance(value, list) or len(value) != 4:
        raise RevisionError('reference bounds require [xmin, ymin, xmax, ymax]')
    result = [_number(v) for v in value]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise RevisionError('reference bounds must have positive width and height')
    return result


def _identity(value, known):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or value in known:
        raise RevisionError('reference item ids must be unique nonempty strings')
    known.add(value)


def _paired_bounds(a, b):
    width, height = max(a[2]-a[0], b[2]-b[0]), max(a[3]-a[1], b[3]-b[1])
    def expand(box):
        x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
        return [x-width/2, y-height/2, x+width/2, y+height/2]
    return expand(a), expand(b)


def _validate(mapping):
    _fields(mapping, {'schema', 'units', 'current_sha256', 'reference_sha256', 'regions', 'parameters'}, {'font_maps'})
    if mapping['schema'] != 'cad-reference-map/v1':
        raise RevisionError('unsupported reference map schema')
    if not isinstance(mapping['units'], str) or mapping['units'] not in UNITS:
        raise RevisionError('unsupported reference units')
    for key in ('current_sha256', 'reference_sha256'):
        if not isinstance(mapping[key], str) or not re.fullmatch('[a-fA-F0-9]{64}', mapping[key]):
            raise RevisionError('reference file binding requires a SHA256')
    if not isinstance(mapping['regions'], list) or not mapping['regions']:
        raise RevisionError('reference map requires at least one paired region')
    if not isinstance(mapping['parameters'], list):
        raise RevisionError('reference parameters must be a list')
    known = set()
    regions = []
    for region in mapping['regions']:
        _fields(region, {'id', 'current_bounds', 'reference_bounds', 'mode'})
        _identity(region['id'], known)
        if region['mode'] != 'depiction':
            raise RevisionError('region mode must be depiction; geometry adoption belongs to parameter mappings')
        a, b = _bounds(region['current_bounds']), _bounds(region['reference_bounds'])
        ar, br = _paired_bounds(a, b)
        regions.append({**region, 'current_render_bounds': ar, 'reference_render_bounds': br,
                        'drawing_scale_ratio': 1, 'visual_review': 'required'})
    known = set()
    for parameter in mapping['parameters']:
        _fields(parameter, {'id', 'current', 'reference', 'measure', 'mode', 'tolerance'},
                {'current_expected', 'authorization'})
        _identity(parameter['id'], known)
        validate_tolerance(parameter['tolerance'])
        if parameter['measure'] not in ('dimension', 'length', 'radius', 'diameter'):
            raise RevisionError('unsupported reference measure')
        if parameter['mode'] == 'preserve_current':
            if 'current_expected' not in parameter or 'authorization' in parameter:
                raise RevisionError('preserve_current requires current_expected, with no adoption authorization')
            _number(parameter['current_expected'])
        elif parameter['mode'] == 'adopt_reference':
            if 'current_expected' in parameter:
                raise RevisionError('adopt_reference uses the measured reference value, not current_expected')
            if 'authorization' in parameter and (not isinstance(parameter['authorization'], str) or
                                                 not parameter['authorization'].strip()):
                raise RevisionError('authorization must quote or identify the explicit user approval')
        else:
            raise RevisionError('unsupported parameter reference mode')
    maps = mapping.get('font_maps', {})
    if not isinstance(maps, dict) or set(maps) - {'current', 'reference'}:
        raise RevisionError('font_maps accepts current and reference mappings only')
    for value in maps.values():
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise RevisionError('font_maps must map style names to actual font paths')
    return regions


def _html_report(report):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = ''.join('<tr>' + ''.join(f'<td>{esc(item[key])}</td>' for key in
                   ('id', 'mode', 'current_actual', 'reference_actual', 'expected', 'status')) + '</tr>'
                   for item in report['parameters'])
    figures = []
    for region, current, reference in zip(report['regions'], report['render']['current']['images'],
                                        report['render']['reference']['images']):
        figures.append(f'<h2>{esc(region["id"])}</h2><div class="pair">'
                       f'<figure><figcaption>Current / 本图</figcaption><img src="{esc(current)}"></figure>'
                       f'<figure><figcaption>Reference / 样图</figcaption><img src="{esc(reference)}"></figure></div>')
    return ('<!doctype html><meta charset="utf-8"><title>CAD reference comparison</title>'
            '<style>body{font:16px sans-serif;margin:24px}table{border-collapse:collapse}'
            'td,th{border:1px solid #ccc;padding:8px}.pair{display:flex}figure{width:50%;margin:4px}'
            'img{max-width:100%}</style><h1>CAD 样图对照</h1>'
            f'<p>数值检查：{esc(report["status"])}。画法仍需目视核对；两图单位一致、比例一致，未修改源文件。</p>'
            '<table><tr><th>参数</th><th>规则</th><th>本图实测</th><th>样图实测</th><th>应有数值</th><th>状态</th></tr>'
            + rows + '</table>' + ''.join(figures))


def compare_reference(current_path: Path, reference_path: Path, mapping_path: Path, output_dir: Path) -> dict:
    """Publish paired views and actual numerical evidence without editing either DXF."""
    mapping_path = Path(mapping_path).expanduser().resolve(strict=True)
    raw = mapping_path.read_bytes()
    map_hash = hashlib.sha256(raw).hexdigest()
    def invalid_constant(value):
        raise RevisionError(f'non-finite JSON constant: {value}')
    mapping = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    regions = _validate(mapping)
    def source(path):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != '.dxf':
            raise RevisionError('reference comparison accepts DXF source files only')
        data = path.read_bytes()
        return path, hashlib.sha256(data).hexdigest(), read_dxf_snapshot(data)
    current, current_hash, current_doc = source(current_path)
    reference, reference_hash, reference_doc = source(reference_path)
    if os.path.samefile(current, reference):
        raise RevisionError('current and reference must be distinct files')
    if current_hash != mapping['current_sha256'].lower() or reference_hash != mapping['reference_sha256'].lower():
        raise RevisionError('reference baseline hash mismatch; re-inspect the input files')
    if current_doc.units != UNITS[mapping['units']] or reference_doc.units != UNITS[mapping['units']]:
        raise RevisionError('reference units do not match both drawings; no implicit conversion')
    parameters = []
    for item in mapping['parameters']:
        actual = measure_feature(current_doc, item['current'], item['measure'])
        sample = measure_feature(reference_doc, item['reference'], item['measure'])
        expected = _number(item['current_expected']) if item['mode'] == 'preserve_current' else sample
        status = 'checked' if abs(actual-expected) <= item['tolerance'] else 'needs_update'
        if item['mode'] == 'adopt_reference' and not item.get('authorization'):
            status = 'needs_authorization'
        parameters.append({**item, 'current_actual': actual, 'reference_actual': sample,
                           'expected': expected, 'difference': actual-sample,
                           'error': actual-expected, 'status': status})
    statuses = {p['status'] for p in parameters}
    status = next((s for s in ('needs_authorization', 'needs_update') if s in statuses),
                  'checked' if parameters else 'needs_visual_review')
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise RevisionError('reference output directory must be new; existing results are preserved')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'cad-reference-report/v1', 'status': status, 'units': mapping['units'],
              'current': {'path': str(current), 'sha256': current_hash},
              'reference': {'path': str(reference), 'sha256': reference_hash},
              'mapping': {'path': str(mapping_path), 'sha256': map_hash},
              'parameters': parameters, 'regions': regions, 'visual_review': 'required',
              'source_modified': False, 'native_interaction_tested': False}
    from .preview import render_regions
    with tempfile.TemporaryDirectory(prefix='.reference-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'publish'
        staging.mkdir()
        report['render'] = {}
        for label, doc in [('current', current_doc), ('reference', reference_doc)]:
            boxes = [{'id': r['id'], 'bounds': r[f'{label}_render_bounds']} for r in regions]
            report['render'][label] = render_regions(doc, boxes, staging, label,
                                                     font_map=mapping.get('font_maps', {}).get(label))
        for path, expected_hash in [(current, current_hash), (reference, reference_hash), (mapping_path, map_hash)]:
            if sha256_file(path) != expected_hash:
                raise RevisionError('reference input changed during comparison; no output published')
        report['artifacts'] = [{'path': path.name, 'sha256': sha256_file(path)}
                               for path in sorted(staging.iterdir()) if path.is_file()]
        report['output_dir'] = str(output)
        (staging / 'reference-report.json').write_text(_json(report), encoding='utf-8')
        (staging / 'reference-review.html').write_text(_html_report(report), encoding='utf-8')
        staging.rename(output)
    return report
