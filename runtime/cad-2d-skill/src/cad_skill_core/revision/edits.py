"""Explicit, atomic native deletion, outline replacement and remeasurement.

Every operation is rehearsed on a private document before changing its caller.
Verification checks saved geometry and native cache content, not reported counts.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from ezdxf.entities import factory
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.math import Vec3

from .common import (RevisionError, resolve_entity, resolve_entities, stable_id,
                     validate_tolerance, values_close, _plain)
from .annotations import _dimension, _dimension_snapshot
from .segments import _curves, _coverage_errors


EDITS = {'delete_entities', 'replace_outline', 'dimension_remeasure'}
_GEOMETRY = {'start', 'end', 'center', 'radius', 'start_angle', 'end_angle'}
_DIM_CHANGED = {'defpoint', 'defpoint2', 'defpoint3', 'text_midpoint', 'geometry',
                'actual_measurement', 'angle'}
_DELETE = {'LINE', 'ARC', 'CIRCLE', 'LWPOLYLINE', 'POINT', 'TEXT', 'MTEXT', 'DIMENSION', 'MULTILEADER'}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RevisionError(f'{name} must be a finite number')
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise RevisionError(f'{name} must be a finite number') from error
    if not math.isfinite(value):
        raise RevisionError(f'{name} must be a finite number')
    return value


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RevisionError(f'{name} requires exactly [x, y]')
    return Vec3(*[_number(v, name) for v in value], 0)


def _matches(doc, selector):
    if not isinstance(selector, dict) or set(selector) not in ({'handle'}, {'id'}):
        raise RevisionError('selector requires exactly one handle or id')
    value = next(iter(selector.values()))
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RevisionError('selector value must be a nonempty string')
    if 'handle' in selector and not re.fullmatch(r'[0-9a-fA-F]+', value):
        raise RevisionError('invalid entity handle')
    return [e for e in doc.modelspace() if
            (e.dxf.handle == value.upper() if 'handle' in selector else stable_id(e) == value)]


def _validate(operation, *, checking=False):
    fields = {'delete_entities': {'type', 'targets'},
              'replace_outline': {'type', 'targets', 'replacements'},
              'dimension_remeasure': {'type', 'target', 'p1', 'p2'}}
    if (not isinstance(operation, dict) or not isinstance(operation.get('type'), str)
            or operation['type'] not in fields):
        raise RevisionError('unknown edit operation')
    allowed = fields[operation['type']] | ({'evidence', 'expected', 'cache'} if checking else set())
    if set(operation) - allowed:
        raise RevisionError('unknown edit operation fields')
    if operation['type'] == 'dimension_remeasure':
        _point(operation.get('p1'), 'p1')
        _point(operation.get('p2'), 'p2')
    elif not isinstance(operation.get('targets'), list) or not operation['targets']:
        raise RevisionError('targets must be a nonempty selector list')


def _unsupported_metadata(entity):
    if (entity.appdata or entity.extension_dict or entity.reactors or entity.proxy_graphic):
        raise RevisionError('associated or opaque appdata/extension/reactor/proxy metadata is unsupported')
    if entity.xdata:
        for tags in entity.xdata.data.values():
            if any(t.code == 1005 for t in tags):
                raise RevisionError('XDATA handle associations require a native editing policy')


def _metadata(entity, *, dimension=False):
    excluded = {'handle', 'owner'} | (_DIM_CHANGED if dimension else _GEOMETRY)
    attrs = {k: _plain(v) for k, v in entity.dxfattribs().items() if k not in excluded}
    # Effective defaults avoid save/reopen materialization differences.
    for key, default in [('layer', '0'), ('color', 256), ('linetype', 'BYLAYER'),
                         ('lineweight', -1), ('invisible', 0), ('ltscale', 1.),
                         ('extrusion', (0, 0, 1)), ('thickness', 0.),
                         ('version', 0), ('attachment_point', 5)]:
        if entity.dxf.is_supported(key):
            attrs.setdefault(key, _plain(entity.dxf.get(key, default)))
    data = {}
    for app, tags in (entity.xdata.data.items() if entity.xdata else []):
        tags = list(tags)[1:]  # leading 1001 application marker
        if app == 'CAD_SKILL_ID':
            tags = tags[1:]  # explicit replacement identity is checked separately
        if app == 'ACAD' and dimension:
            # DSTYLE is checked through the decoded overrides. Preserve unrelated
            # ACAD application data, including unknown non-DSTYLE records.
            kept, index = [], 0
            while index < len(tags):
                if tags[index].code == 1000 and tags[index].value == 'DSTYLE':
                    index += 1
                    depth = 0
                    while index < len(tags):
                        tag = tags[index]
                        index += 1
                        if tag.code == 1002:
                            depth += 1 if tag.value == '{' else -1
                            if depth == 0:
                                break
                else:
                    kept.append(tags[index])
                    index += 1
            tags = kept
        if tags or app != 'ACAD':
            data[app] = [[t.code, _plain(t.value)] for t in tags]
    return {'attributes': attrs, 'xdata': data}


def _cache(entity):
    block = entity.doc.blocks.get(entity.dxf.get('geometry', ''))
    if block is None or not len(block):
        raise RevisionError('native dimension has no displayed geometry cache')
    content = []
    for child in block:
        # Match the file writer: explicit default attributes (BYLAYER color,
        # zero rotation, Standard text style) are omitted during DXF export.
        # Nondefault graphics, geometry and complete text remain in the check.
        collector = TagCollector(dxfversion=entity.doc.dxfversion, optional=False)
        child.export_dxf(collector)
        content.append([[t.code, _plain(t.value)] for t in collector.tags
                        if t.code not in {5, 330}])
    return content


def _private_cache(doc, entity, selected):
    name = entity.dxf.get('geometry', '')
    if not name:
        return None
    for other in doc.entitydb.values():
        if not other.is_alive or other.dxf.get('handle') in selected:
            continue
        if ((other.dxftype() == 'DIMENSION' and other.dxf.get('geometry', '').casefold() == name.casefold())
                or (other.dxftype() == 'INSERT' and other.dxf.name.casefold() == name.casefold())):
            raise RevisionError('native dimension cache is shared with an unselected entity')
    block = doc.blocks.get(name)
    if block is None or not name.upper().startswith('*D'):
        raise RevisionError('native dimension requires its own anonymous geometry block')
    for child in [block.block_record, block.block, block.endblk, *block]:
        _unsupported_metadata(child)
        if child.xdata:
            raise RevisionError('custom native cache XDATA cannot be discarded')
    return name


def _geometry(spec, tolerance):
    if not isinstance(spec, dict) or not isinstance(spec.get('type'), str):
        raise RevisionError('replacement requires an explicit primitive type')
    kind = spec['type']
    fields = {'LINE': {'start', 'end'}, 'ARC': {'center', 'radius', 'start_angle', 'end_angle'},
              'CIRCLE': {'center', 'radius'}}
    if kind not in fields or set(spec) - (fields.get(kind, set()) | {'type', 'id', 'source'}):
        raise RevisionError('replacement supports explicit LINE/ARC/CIRCLE fields only')
    attrs = {}
    for name in fields[kind]:
        attrs[name] = _point(spec.get(name), name) if name in {'start', 'end', 'center'} else _number(spec.get(name), name)
    if kind == 'LINE' and (attrs['end'] - attrs['start']).magnitude <= tolerance:
        raise RevisionError('replacement LINE length must exceed tolerance')
    if kind != 'LINE' and attrs['radius'] <= tolerance:
        raise RevisionError('replacement radius must exceed tolerance')
    if kind == 'ARC' and (attrs['end_angle'] - attrs['start_angle']) % 360 == 0:
        raise RevisionError('replacement ARC requires a nonzero sweep smaller than a full circle')
    entity = factory.new(kind, dxfattribs=attrs)
    _curves(entity, tolerance)
    return entity


def _delete(doc, operation, tolerance):
    selected = resolve_entities(doc, operation['targets'])
    handles = [e.dxf.handle for e in selected]
    caches = []
    cache_records = []
    for entity in selected:
        if entity.dxftype() not in _DELETE:
            raise RevisionError(f'unsupported deletion entity type: {entity.dxftype()}')
        _unsupported_metadata(entity)
        if entity.dxftype() == 'DIMENSION':
            name = _private_cache(doc, entity, handles)
            if name:
                caches.append(name)
                cache_records.append({'name': name, 'handle': doc.blocks[name].block_record.dxf.handle})
    for entity in selected:
        doc.modelspace().delete_entity(entity)
    for name in set(caches):
        doc.blocks.delete_block(name, safe=False)
    evidence = {'deleted_handles': handles, 'deleted_blocks': sorted(set(caches)),
                'deleted_block_records': cache_records}
    return {'touched_handles': handles, 'created_handles': [], 'deleted_handles': handles,
            'evidence': evidence}


def _replace(doc, operation, tolerance):
    selected = resolve_entities(doc, operation['targets'])
    sources = {e.dxf.handle: e for e in selected}
    for entity in selected:
        if entity.dxftype() not in {'LINE', 'ARC', 'CIRCLE'}:
            raise RevisionError('outline sources must be planar LINE/ARC/CIRCLE')
        _curves(entity, tolerance)
        _unsupported_metadata(entity)
    specs = operation.get('replacements')
    if not isinstance(specs, list) or not specs:
        raise RevisionError('replacements must be a nonempty list')
    used_ids = {stable_id(e) for e in doc.modelspace() if stable_id(e) is not None}
    pending = []
    for spec in specs:
        child = _geometry(spec, tolerance)
        identity = spec.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity != identity.strip() or identity in used_ids:
            raise RevisionError('replacement id must be a new globally unique nonempty stable ID')
        used_ids.add(identity)
        source = resolve_entity(doc, spec.get('source'))
        if source.dxf.handle not in sources:
            raise RevisionError('replacement source must belong to targets')
        for key, value in source.dxfattribs().items():
            if key in _GEOMETRY | {'handle', 'owner'}:
                continue
            if not child.dxf.is_supported(key):
                raise RevisionError(f'replacement cannot preserve source attribute {key}')
            setattr(child.dxf, key, value)
        child.xdata = deepcopy(source.xdata)
        id_tags = list(source.get_xdata('CAD_SKILL_ID')) if source.has_xdata('CAD_SKILL_ID') else []
        if id_tags and stable_id(source) is None:
            raise RevisionError('malformed CAD_SKILL_ID cannot be replaced')
        child.set_xdata('CAD_SKILL_ID', [(1000, identity), *id_tags[1:]])
        pending.append((source.dxf.handle, child, deepcopy(spec)))
    if {h for h, _, _ in pending} != set(sources):
        raise RevisionError('every selected source requires at least one replacement')
    doc.appids.add('CAD_SKILL_ID') if 'CAD_SKILL_ID' not in doc.appids else None
    mapping = {h: [] for h in sources}
    records = []
    for handle, child, spec in pending:
        doc.modelspace().add_entity(child)
        mapping[handle].append(child.dxf.handle)
        records.append({'geometry': spec, 'metadata': _metadata(child),
                        'outputs': [{'handle': child.dxf.handle, 'id': stable_id(child)}]})
    source_ids = {h: stable_id(e) for h, e in sources.items()}
    for entity in selected:
        doc.modelspace().delete_entity(entity)
    return {'touched_handles': list(sources), 'created_handles': [h for items in mapping.values() for h in items],
            'deleted_handles': list(sources), 'mapping': mapping, 'final_mapping': deepcopy(mapping),
            'source_ids': source_ids, 'evidence': {'deleted_handles': list(sources), 'records': records}}


def _remeasure(doc, operation, tolerance):
    entity = resolve_entity(doc, operation.get('target'))
    _dimension(entity, tolerance)
    _unsupported_metadata(entity)
    _private_cache(doc, entity, [entity.dxf.handle])
    override = entity.override()
    if override.get('dimalt', 0) or override.get('dimlunit', 2) != 2:
        raise RevisionError('native regeneration supports decimal dimensions without alternate units')
    p1, p2 = _point(operation['p1'], 'p1'), _point(operation['p2'], 'p2')
    delta = p2 - p1
    angle = (math.degrees(math.atan2(delta.y, delta.x)) if entity.dimtype == 1
             else _number(entity.dxf.get('angle', 0), 'dimension angle'))
    measurement = abs(delta.dot(Vec3(math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0)))
    if not math.isfinite(measurement) or measurement <= tolerance:
        raise RevisionError('actual dimension measurement must exceed tolerance')
    before = _metadata(entity, dimension=True)
    before_snapshot = _dimension_snapshot(entity)
    entity.dxf.defpoint2 = p1
    entity.dxf.defpoint3 = p2
    if entity.dimtype == 1:
        entity.dxf.angle = angle
    entity.dxf.actual_measurement = measurement
    override.render()
    expected = _dimension_snapshot(entity)
    for key in ('dimtype', 'text', 'dimstyle', 'text_mode', 'overrides'):
        if not values_close(before_snapshot[key], expected[key], tolerance):
            raise RevisionError(f'native renderer changed protected dimension {key}')
    if not values_close(before, _metadata(entity, dimension=True), tolerance):
        raise RevisionError('native renderer changed protected dimension metadata')
    return {'touched_handles': [entity.dxf.handle], 'created_handles': [], 'deleted_handles': [],
            'expected': expected, 'cache': _cache(entity),
            'evidence': {'handle': entity.dxf.handle, 'id': stable_id(entity), 'metadata': before}}


def _apply(doc, operation, tolerance):
    result = {'delete_entities': _delete, 'replace_outline': _replace,
              'dimension_remeasure': _remeasure}[operation['type']](doc, operation, tolerance)
    verification = deepcopy(operation)
    verification.update({k: deepcopy(result[k]) for k in ('evidence', 'expected', 'cache') if k in result})
    check = check_edit(doc, verification, tolerance)
    if check['status'] != 'passed':
        raise RevisionError(f'edit verification failed: {check}')
    result.update(status='passed', verification=verification)
    return result


def apply_edit(doc, operation, tolerance):
    """Apply one explicit edit, rejecting validation/render failures before mutation."""
    tolerance = validate_tolerance(tolerance)
    try:
        _validate(operation)
        _apply(deepcopy(doc), operation, tolerance)
        return _apply(doc, operation, tolerance)
    except RevisionError:
        raise
    except Exception as error:
        raise RevisionError(f'edit operation is unsupported: {error}') from error


def check_edit(doc, operation, tolerance):
    """Check actual entities/cache and, when supplied, saved preservation evidence."""
    tolerance = validate_tolerance(tolerance)
    checks = {}
    try:
        _validate(operation, checking=True)
        kind = operation['type']
        evidence = operation.get('evidence', {})
        if kind in {'delete_entities', 'replace_outline'}:
            checks['selected_sources_absent'] = all(not _matches(doc, s) for s in operation['targets'])
            checks['retired_handles_absent'] = all(not _matches(doc, {'handle': h}) for h in evidence.get('deleted_handles', []))
        if kind == 'delete_entities':
            if 'deleted_block_records' in evidence:
                # Anonymous *D names may be reused by a later dimension render.
                # Check the retired records, not a new cache sharing their name.
                checks['native_display_removed'] = all(
                    doc.entitydb.get(record['handle']) is None or not doc.entitydb[record['handle']].is_alive
                    for record in evidence['deleted_block_records'])
            else:
                checks['native_display_removed'] = all(name not in doc.blocks for name in evidence.get('deleted_blocks', []))
        elif kind == 'replace_outline':
            records = evidence.get('records')
            if records is None:
                specs = operation.get('replacements')
                if not isinstance(specs, list) or not specs:
                    raise RevisionError('replacement check requires replacements')
                records = [{'geometry': spec, 'outputs': [{'id': spec['id']}]} for spec in specs]
            outputs = []
            for i, record in enumerate(records):
                expected = _geometry(record['geometry'], tolerance)
                actual = []
                if not record['outputs']:
                    raise RevisionError('replacement evidence has no output coverage')
                for output in record['outputs']:
                    entity = resolve_entity(doc, {'handle': output['handle']} if 'handle' in output else {'id': output['id']})
                    outputs.append(entity.dxf.handle)
                    checks[f'identity_{i}_{entity.dxf.handle}'] = stable_id(entity) == output['id'] and len(_matches(doc, {'id': output['id']})) == 1
                    actual.extend(_curves(entity, tolerance))
                    if 'metadata' in record:
                        checks[f'metadata_{i}_{entity.dxf.handle}'] = values_close(_metadata(entity), record['metadata'], tolerance)
                checks[f'coverage_{i}'] = not _coverage_errors(_curves(expected, tolerance), actual, tolerance)
            checks['unique_outputs'] = len(outputs) == len(set(outputs))
        else:
            entity = resolve_entity(doc, operation.get('target'))
            _dimension(entity, tolerance)
            p1, p2 = _point(operation['p1'], 'p1'), _point(operation['p2'], 'p2')
            checks['measurement_origins'] = values_close(list(entity.dxf.defpoint2), list(p1), tolerance) and values_close(list(entity.dxf.defpoint3), list(p2), tolerance)
            delta = p2 - p1
            angle = math.degrees(math.atan2(delta.y, delta.x)) if entity.dimtype == 1 else entity.dxf.get('angle', 0)
            measurement = abs(delta.dot(Vec3(math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0)))
            checks['actual_measurement'] = measurement > tolerance and abs(float(entity.get_measurement()) - measurement) <= tolerance
            if entity.dxf.hasattr('actual_measurement'):
                checks['stored_measurement'] = abs(entity.dxf.actual_measurement - measurement) <= tolerance
            cache = _cache(entity)
            checks['native_display'] = bool(cache)
            if 'cache' in operation:
                checks['native_cache_content'] = values_close(cache, operation['cache'], tolerance)
            if evidence:
                checks['identity'] = entity.dxf.handle == evidence['handle'] and stable_id(entity) == evidence['id']
                checks['metadata'] = values_close(_metadata(entity, dimension=True), evidence['metadata'], tolerance)
            actual = _dimension_snapshot(entity)
            for key, value in operation.get('expected', {}).items():
                checks[f'preserved_{key}'] = (all(k in actual[key] and values_close(actual[key][k], v, tolerance) for k, v in value.items())
                                             if key == 'overrides' else values_close(actual[key], value, tolerance))
    except (RevisionError, AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        return {'status': 'failed', 'checks': checks, 'error': str(error)}
    return {'status': 'passed' if checks and all(checks.values()) else 'failed', 'checks': checks}


def remap_after_split(doc, entries, split_report):
    """Compose verified splitter mappings without dropping expected curve coverage."""
    mapping = split_report['mapping']
    for entry in entries:
        if entry['operation']['type'] != 'replace_outline':
            continue
        verification = entry['verification']
        for record in verification['evidence']['records']:
            record['outputs'] = [{'handle': h, 'id': stable_id(resolve_entity(doc, {'handle': h}))}
                                 for old in record['outputs'] for h in mapping.get(old['handle'], [old['handle']])]
        entry['result']['final_mapping'] = {old: [h for child in children for h in mapping.get(child, [child])]
                                            for old, children in entry['result']['final_mapping'].items()}


def replacement_verifications(entries):
    """Export only active, already verified replacement lineage for predicates."""
    superseded = {identity for entry in entries for identity in entry['supersedes']}
    return [{'operation': deepcopy(entry['operation']), 'verification': deepcopy(entry['verification'])}
            for entry in entries if entry['id'] not in superseded
            and entry['operation']['type'] == 'replace_outline']


def bind_replacement_check(operation, bindings):
    """Use exact predicate matches; never infer descendants from ID prefixes."""
    if operation.get('type') == 'replace_outline':
        for binding in reversed(bindings):
            if operation == binding['operation']:
                return deepcopy(binding['verification'])
    return operation
