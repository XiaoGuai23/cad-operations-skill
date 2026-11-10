"""Hash-bound, incremental DXF revision with verified atomic publication."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import ezdxf

from .common import (RevisionError, compare_untouched, sha256_file,
                     snapshot_modelspace, stable_id, validate_tolerance)
from .edits import EDITS

CONSTRAINTS = {'align', 'contact', 'resize_fixed_edge', 'set_length'}
ANNOTATIONS = {'dimension_move', 'dimension_text_mode', 'leader_move', 'link_callout'}
UNITS = {'unitless': 0, 'in': 1, 'ft': 2, 'mm': 4, 'cm': 5, 'm': 6}
META = {'id', 'comment', 'source', 'supersedes'}


def _json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise RevisionError(f'plan/report must contain finite JSON values: {error}') from error


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RevisionError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def load_revision_plan(path: Path) -> dict:
    def invalid_constant(value):
        raise RevisionError(f'non-finite JSON constant: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs,
                      parse_constant=invalid_constant)


def _source(path: Path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.dxf':
        raise RevisionError('this incremental backend accepts DXF; edit DWG through native CAD tools or an explicitly verified exchange copy')
    digest = sha256_file(path)
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    if audit.has_errors or audit.has_fixes:
        raise RevisionError('baseline DXF audit failed; repair a copy before revision')
    return path, digest, doc


def inventory_drawing(path: Path) -> dict:
    path, digest, doc = _source(path)
    entities = []
    for e in doc.modelspace():
        attrs = e.dxfattribs()
        geometry = {}
        for key in ('start', 'end', 'center', 'insert', 'defpoint', 'defpoint2', 'defpoint3', 'text_midpoint'):
            if key in attrs:
                geometry[key] = list(attrs[key])
        for key in ('radius', 'start_angle', 'end_angle', 'angle', 'text'):
            if key in attrs:
                geometry[key] = attrs[key]
        if e.dxftype() == 'LWPOLYLINE':
            geometry['vertices'] = [list(v) for v in e.get_points()]
            geometry['closed'] = e.closed
        if e.dxftype() == 'MTEXT':
            geometry['text'] = e.text
        if e.dxftype() == 'MULTILEADER' and e.context.mtext is not None:
            geometry['text'] = e.context.mtext.default_content
            geometry['text_position'] = list(e.context.mtext.insert)
        entities.append({'handle': e.dxf.handle, 'id': stable_id(e), 'type': e.dxftype(),
                         'layer': e.dxf.layer, 'geometry': geometry})
    return {'schema': 'cad-inventory/v1', 'path': str(path), 'sha256': digest,
            'units': next((k for k, v in UNITS.items() if v == doc.units), f'code:{doc.units}'),
            'entity_counts': dict(Counter(e['type'] for e in entities)), 'entities': entities}


def _validate(plan: dict, digest: str, doc) -> float:
    if not isinstance(plan, dict) or set(plan) - {'schema', 'baseline_sha256', 'units', 'tolerance', 'operations', 'checks', 'note'}:
        raise RevisionError('unknown revision plan fields')
    if plan.get('schema') != 'cad-revision/v1':
        raise RevisionError('unsupported revision plan schema')
    if not isinstance(plan.get('baseline_sha256'), str) or plan['baseline_sha256'].lower() != digest:
        raise RevisionError('baseline hash mismatch; inspect the current CAD and rebase the plan')
    if not isinstance(plan.get('units'), str) or plan['units'] not in UNITS or UNITS[plan['units']] != doc.units:
        raise RevisionError('plan units do not match baseline $INSUNITS; no implicit conversion')
    tolerance = validate_tolerance(plan.get('tolerance'))
    operations = plan.get('operations')
    if not isinstance(operations, list) or not operations:
        raise RevisionError('operations must be a nonempty list')
    known = set()
    for index, op in enumerate(operations):
        if not isinstance(op, dict) or not isinstance(op.get('type'), str) or op['type'] not in CONSTRAINTS | ANNOTATIONS | EDITS | {'split_intersections'}:
            raise RevisionError('unsupported revision operation')
        identity = op.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in known:
            raise RevisionError('each operation requires a unique nonempty id')
        supersedes = op.get('supersedes', [])
        if not isinstance(supersedes, list) or any(not isinstance(s, str) or s not in known for s in supersedes):
            raise RevisionError('supersedes must refer to earlier operation ids')
        known.add(identity)
        if op['type'] == 'split_intersections':
            if index != len(operations) - 1:
                raise RevisionError('split_intersections must be last; use its mapping in a subsequent revision')
            if set(op) - (META | {'type', 'targets', 'include_annotation_intersections'}):
                raise RevisionError('unknown split_intersections fields')
            if not isinstance(op.get('include_annotation_intersections', False), bool):
                raise RevisionError('include_annotation_intersections must be boolean')
    checks = plan.get('checks', [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) or not isinstance(c.get('type'), str) or c['type'] not in CONSTRAINTS | ANNOTATIONS | EDITS for c in checks):
        raise RevisionError('checks must be geometric/annotation postcondition operations')
    _json(plan)
    return tolerance


def _check(doc, op, tolerance):
    if op['type'] in EDITS:
        from .edits import check_edit
        return check_edit(doc, op, tolerance)
    if op['type'] in CONSTRAINTS:
        from .constraints import check_constraint
        return check_constraint(doc, op, tolerance)
    from .annotations import check_annotation
    return check_annotation(doc, op, tolerance)


def _active_checks(doc, entries, tolerance, *, only_edits=False):
    superseded = {s for entry in entries for s in entry['supersedes']}
    results = []
    for index, entry in enumerate(entries):
        if entry['id'] in superseded or entry['operation']['type'] == 'split_intersections':
            continue
        if only_edits and entry['operation']['type'] not in EDITS:
            continue
        verification = deepcopy(entry['verification'])
        later_handles = {h for later in entries[index + 1:]
                         for h in later['result'].get('touched_handles', [])}
        if (set(entry['result'].get('touched_handles', [])) |
                set(entry['result'].get('created_handles', []))) & later_handles:
            # Each step's fixed/original geometry was already checked after save.
            # When later authorized edits affect it, recheck the active predicate.
            if entry['operation']['type'] in {'dimension_remeasure', 'dimension_drive'}:
                # A later native move/mode operation may change placement and
                # text movement, while endpoints, labels and tolerances remain
                # protected by the remeasurement requirement.
                later_types = {later['operation']['type'] for later in entries[index + 1:]
                               if set(entry['result'].get('touched_handles', [])) & set(later['result'].get('touched_handles', []))}
                if 'dimension_move' in later_types:
                    verification.get('expected', {}).pop('line_point', None)
                    verification.pop('cache', None)
                if 'dimension_text_mode' in later_types:
                    verification.get('expected', {}).pop('text_mode', None)
                    verification.get('expected', {}).get('overrides', {}).pop('dimtmove', None)
                    verification.pop('cache', None)
            elif entry['operation']['type'] not in EDITS:
                verification.pop('expected_geometry', None)
                verification.pop('expected', None)
        result = _check(doc, verification, tolerance)
        results.append({'id': entry['id'], 'result': result})
        if result['status'] != 'passed':
            raise RevisionError(f'active requirement no longer holds: {entry["id"]}')
    return results


def _reuse(output: Path, digest: str, plan_hash: str, ledger_hash=None):
    report_path = output / 'revision-report.json'
    if not output.is_dir() or not report_path.is_file():
        raise RevisionError('output directory already exists and is not a matching verified revision')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    drawing = output / 'drawing.dxf'
    if report.get('status') != 'passed' or report.get('baseline_sha256') != digest or report.get('plan_sha256') != plan_hash:
        raise RevisionError('existing output does not match this baseline and plan')
    if not drawing.is_file() or sha256_file(drawing) != report.get('output_sha256'):
        raise RevisionError('verified output changed: output hash mismatch; inspect before continuing')
    if report.get('ledger_sha256') != ledger_hash:
        raise RevisionError('existing output does not match this comment ledger')
    for name, expected in report.get('comment_artifacts', {}).items():
        artifact = (output / name).resolve()
        if not artifact.is_relative_to(output.resolve()) or not artifact.is_file() or sha256_file(artifact) != expected:
            raise RevisionError('comment evidence changed; refusing output reuse')
    report['reused'] = True
    return report


def apply_revision(source: Path, plan: dict | Path, output_dir: Path, *, dry_run: bool = False, ledger=None) -> dict:
    """Publish a verified revision; never overwrite source or unrelated outputs."""
    if isinstance(plan, (Path, str)):
        plan = load_revision_plan(Path(plan))
    source, digest, doc = _source(source)
    tolerance = _validate(plan, digest, doc)
    from .ledger import carried_ledger
    previous_ledger = carried_ledger(source, digest, doc)
    if ledger is None and previous_ledger is not None:
        ledger = previous_ledger
    ledger_hash = None
    if ledger is not None:
        from .ledger import load_ledger, check_ledger, write_evidence, next_ledger
        ledger, ledger_hash = load_ledger(ledger, digest, doc)
        if previous_ledger is not None and ledger['comments'][:len(previous_ledger['comments'])] != previous_ledger['comments']:
            raise RevisionError('preserve previous comments; append new comments with explicit supersedes')
    plan_text = _json(plan)
    plan_hash = hashlib.sha256(plan_text.encode('utf-8')).hexdigest()
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and not dry_run:
        return _reuse(output, digest, plan_hash, ledger_hash)
    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
    original = snapshot_modelspace(doc)
    entries = []
    allowed = set()
    active = []
    # Only a newly allocated directory is ever cleaned. The user source/output
    # directories are not deleted or recursively rewritten on any failure.
    with tempfile.TemporaryDirectory(prefix='.cad-revision-', dir=output.parent if not dry_run else None) as temp:
        stage = Path(temp)
        publish = stage / 'publish'
        publish.mkdir()
        for number, requested in enumerate(plan['operations']):
            op = {k: v for k, v in requested.items() if k not in META}
            before = snapshot_modelspace(doc)
            if op['type'] == 'split_intersections':
                active = _active_checks(doc, entries, tolerance)
                from .segments import split_intersections, verify_split
                before_path = stage / f'before-{number}.dxf'
                doc.saveas(before_path)
                before_doc = ezdxf.readfile(before_path)
                result = split_intersections(doc, op.get('targets'), tolerance,
                    include_annotation_intersections=op.get('include_annotation_intersections', False))
                verification = op
            elif op['type'] in CONSTRAINTS:
                from .constraints import apply_constraint
                result = apply_constraint(doc, op, tolerance)
                verification = result.get('verification_operation', result.get('verification', op))
            elif op['type'] in EDITS:
                from .edits import apply_edit
                result = apply_edit(doc, op, tolerance)
                verification = result['verification']
            else:
                from .annotations import apply_annotation
                result = apply_annotation(doc, op, tolerance)
                verification = result.get('verification_operation', result.get('verification', op))
            if result['status'] != 'passed':
                raise RevisionError(f'operation {requested["id"]} is not fully supported/verified: {_json(result)}')
            candidate = stage / f'step-{number}.dxf'
            doc.saveas(candidate)
            doc = ezdxf.readfile(candidate)
            audit = doc.audit()
            if audit.has_errors or audit.has_fixes:
                raise RevisionError(f'saved candidate requires DXF audit repair at {requested["id"]}')
            if op['type'] == 'split_intersections':
                check = verify_split(before_doc, doc, result, tolerance)
            else:
                check = _check(doc, verification, tolerance)
            if check['status'] != 'passed':
                raise RevisionError(f'post-save verification failed for {requested["id"]}: {_json(check)}')
            if op['type'] == 'split_intersections':
                from .edits import remap_after_split
                remap_after_split(doc, entries, result)
            touched = set(result.get('touched_handles', [])) | set(result.get('created_handles', [])) | set(result.get('deleted_handles', []))
            unexpected = compare_untouched(before, snapshot_modelspace(doc), touched, tolerance)
            if unexpected:
                raise RevisionError(f'unexpected entities changed at {requested["id"]}: {unexpected}')
            allowed.update(touched)
            entries.append({'id': requested['id'], 'comment': requested.get('comment'), 'operation': op,
                            'supersedes': requested.get('supersedes', []), 'result': result,
                            'verification': verification, 'saved_file_check': check})
        if entries[-1]['operation']['type'] != 'split_intersections':
            active = _active_checks(doc, entries, tolerance)
        else:
            edit_checks = _active_checks(doc, entries, tolerance, only_edits=True)
            active = [check for check in active if check['id'] not in {e['id'] for e in edit_checks}] + edit_checks
        from .edits import bind_replacement_check, replacement_verifications
        edit_verifications = replacement_verifications(entries)
        explicit = [_check(doc, bind_replacement_check(check, edit_verifications), tolerance)
                    for check in plan.get('checks', [])]
        if any(c['status'] != 'passed' for c in explicit):
            raise RevisionError('a final/preserved requirement failed')
        comment_checks = check_ledger(doc, ledger, before=ezdxf.readfile(source),
                                     edit_verifications=edit_verifications) if ledger is not None else None
        if comment_checks is not None and comment_checks['status'] == 'failed':
            failed = [c['id'] for c in comment_checks['comments'] if c['status'] == 'failed']
            raise RevisionError(f'active comment requirements failed: {failed}')
        unexpected = compare_untouched(original, snapshot_modelspace(doc), allowed, tolerance)
        if unexpected:
            raise RevisionError(f'unchanged-content verification failed: {unexpected}')
        if sha256_file(source) != digest:
            raise RevisionError('baseline changed while revision was running')
        # Publish the already-reopened bytes, avoiding an extra unverified save.
        final = publish / 'drawing.dxf'
        final.write_bytes(candidate.read_bytes())
        report = {'schema': 'cad-revision-report/v1', 'status': 'passed', 'dry_run': dry_run, 'reused': False,
                  'source_path': str(source), 'baseline_sha256': digest, 'plan_sha256': plan_hash,
                  'output_path': str(output / 'drawing.dxf') if not dry_run else None,
                  'output_sha256': sha256_file(final), 'units': plan['units'], 'tolerance': tolerance,
                  'operations': entries, 'active_requirements': active, 'final_checks': explicit,
                  'checks_passed': True, 'preservation': {'unexpected_handles': [], 'changed_handles': sorted(allowed)},
                  'entity_counts': dict(Counter(e.dxftype() for e in doc.modelspace())),
                  'native_interaction_tested': False}
        if ledger is not None:
            report.update(ledger_sha256=ledger_hash, comment_checks=comment_checks)
            if not dry_run:
                comment_checks.update(before_path=str(source), after_path=str(output / 'drawing.dxf'),
                                      before_sha256=digest, after_sha256=report['output_sha256'], ledger_sha256=ledger_hash)
                write_evidence(ezdxf.readfile(source), doc, ledger, comment_checks, publish / 'comments')
                (publish / 'comment-ledger.json').write_text(_json(next_ledger(
                    ledger, report['output_sha256'], edit_verifications=edit_verifications)), encoding='utf-8')
                report['comment_artifacts'] = {p.relative_to(publish).as_posix(): sha256_file(p)
                    for p in publish.rglob('*') if p.is_file() and p != final}
        (publish / 'revision-plan.json').write_text(plan_text, encoding='utf-8')
        (publish / 'revision-report.json').write_text(_json(report), encoding='utf-8')
        if not dry_run:
            if sha256_file(source) != digest:
                raise RevisionError('baseline changed while evidence was being generated')
            if output.exists():
                raise RevisionError('output appeared during revision; refusing to overwrite')
            os.rename(publish, output)
        return report
