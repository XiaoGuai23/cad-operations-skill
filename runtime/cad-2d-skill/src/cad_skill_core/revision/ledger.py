"""Hash-bound comments with repeatable checks and per-comment visual evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .common import RevisionError, sha256_file, stable_id, validate_tolerance, values_close, _plain
from .workflow import UNITS, CONSTRAINTS, ANNOTATIONS, EDITS, _check, _json, _source, load_revision_plan

SPECIAL = {'present', 'absent', 'property'}
PROPERTIES = {'start', 'end', 'center', 'radius', 'text', 'insert', 'layer', 'text_midpoint',
              'start_angle', 'end_angle', 'linetype', 'lineweight', 'color'}


def _selector(selector):
    if not isinstance(selector, dict) or set(selector) not in ({'handle'}, {'id'}):
        raise RevisionError('comment selector requires handle or id')
    value = next(iter(selector.values()))
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RevisionError('invalid comment selector value')
    if 'handle' in selector and not re.fullmatch(r'[0-9a-fA-F]+', value):
        raise RevisionError('invalid comment handle')


def _matches(doc, selector):
    _selector(selector)
    return [e for e in doc.modelspace() if
            (e.dxf.handle == selector['handle'].upper() if 'handle' in selector else stable_id(e) == selector['id'])]


def validate_ledger(ledger, digest, doc):
    allowed = {'schema', 'drawing_sha256', 'units', 'tolerance', 'padding', 'comments', 'history', 'edit_evidence'}
    if not isinstance(ledger, dict) or set(ledger) - allowed or ledger.get('schema') != 'cad-comment-ledger/v1':
        raise RevisionError('invalid comment ledger schema/fields')
    if ledger.get('drawing_sha256') != digest:
        raise RevisionError('ledger drawing hash mismatch; inspect and explicitly rebase comments')
    if ledger.get('units') not in UNITS or UNITS[ledger['units']] != doc.units:
        raise RevisionError('ledger units differ from drawing')
    validate_tolerance(ledger.get('tolerance'))
    validate_tolerance(ledger.get('padding', 5.))
    comments = ledger.get('comments')
    if not isinstance(comments, list) or not comments or len(comments) > 100:
        raise RevisionError('ledger requires 1 to 100 comments; batch larger reviews')
    known = set()
    for c in comments:
        if not isinstance(c, dict) or set(c) - {'id', 'text', 'source', 'targets', 'checks', 'manual_checks', 'supersedes', 'bounds'}:
            raise RevisionError('invalid comment fields')
        if any(not isinstance(c.get(k), str) or not c[k].strip() for k in ('id', 'text')) or c['id'] in known:
            raise RevisionError('comment requires unique id and original text')
        if not isinstance(c.get('source'), dict) or not c['source']:
            raise RevisionError('comment requires source provenance')
        prior = c.get('supersedes', [])
        if not isinstance(prior, list) or any(not isinstance(x, str) or x not in known for x in prior):
            raise RevisionError('supersedes must name earlier comment IDs')
        known.add(c['id'])
        if not isinstance(c.get('targets'), list) or not c['targets']:
            raise RevisionError('comment targets must be a nonempty list')
        for target in c['targets']:
            _selector(target)
        checks, manual = c.get('checks', []), c.get('manual_checks', [])
        if (not isinstance(checks, list) or not isinstance(manual, list)
                or any(not isinstance(x, str) or not x.strip() for x in manual) or not (checks or manual)):
            raise RevisionError('comment requires executable or manual checks')
        for check in checks:
            if not isinstance(check, dict) or check.get('type') not in CONSTRAINTS | ANNOTATIONS | EDITS | SPECIAL:
                raise RevisionError('unsupported comment check')
            if check['type'] in SPECIAL:
                fields = {'type', 'target', 'entity_type'} if check['type'] != 'property' else {'type', 'target', 'attribute', 'value'}
                if set(check) - fields:
                    raise RevisionError('unknown comment predicate field')
                _selector(check.get('target'))
                if 'entity_type' in check and not isinstance(check['entity_type'], str):
                    raise RevisionError('entity_type must be a string')
                if check['type'] == 'property' and (check.get('attribute') not in PROPERTIES or 'value' not in check):
                    raise RevisionError('unsupported property predicate')
        if 'bounds' in c:
            b = c['bounds']
            if (not isinstance(b, list) or len(b) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in b)
                    or b[0] >= b[2] or b[1] >= b[3]):
                raise RevisionError('comment bounds must be finite drawing coordinates with positive area')
    if not isinstance(ledger.get('history', []), list):
        raise RevisionError('ledger history must be a list')
    if 'edit_evidence' in ledger:
        evidence = ledger['edit_evidence']
        if (not isinstance(evidence, dict) or set(evidence) != {'drawing_sha256', 'replacements'}
                or evidence['drawing_sha256'] != digest or not isinstance(evidence['replacements'], list)):
            raise RevisionError('replacement evidence must be bound to the ledger drawing hash')
        seen = []
        for binding in evidence['replacements']:
            if not isinstance(binding, dict) or set(binding) != {'operation', 'verification'}:
                raise RevisionError('invalid replacement evidence binding')
            operation, verification = binding['operation'], binding['verification']
            if (not isinstance(operation, dict) or operation.get('type') != 'replace_outline'
                    or not isinstance(verification, dict)
                    or {k: v for k, v in verification.items() if k != 'evidence'} != operation
                    or not isinstance(verification.get('evidence'), dict)
                    or operation in seen):
                raise RevisionError('replacement evidence must match one original predicate')
            records = verification['evidence'].get('records', [])
            if (not isinstance(records, list) or not records
                    or any(not isinstance(record, dict) for record in records)
                    or [record.get('geometry') for record in records] != operation.get('replacements')):
                raise RevisionError('replacement evidence geometry differs from the original predicate')
            seen.append(operation)
    _json(ledger)
    return ledger


def load_ledger(value, digest, doc):
    data = load_revision_plan(Path(value)) if isinstance(value, (str, Path)) else deepcopy(value)
    validate_ledger(data, digest, doc)
    return data, hashlib.sha256(_json(data).encode('utf-8')).hexdigest()


def carried_ledger(source, digest, doc):
    """Recognize our delivered revision and recover its bound requirements."""
    report_path = source.parent / 'revision-report.json'
    if not report_path.is_file() or source.name != 'drawing.dxf':
        return None
    report = load_revision_plan(report_path)
    if report.get('schema') != 'cad-revision-report/v1' or not report.get('ledger_sha256'):
        return None
    path = source.parent / 'comment-ledger.json'
    expected = report.get('comment_artifacts', {}).get('comment-ledger.json')
    if not expected or not path.is_file() or sha256_file(path) != expected:
        raise RevisionError('previous comment ledger evidence is missing or changed')
    if report.get('output_sha256') != digest:
        raise RevisionError('previous drawing changed; explicitly reconcile comments before revision')
    return load_ledger(path, digest, doc)[0]


def _predicate(doc, check, tol):
    if check['type'] not in SPECIAL:
        return _check(doc, check, tol)
    matches = _matches(doc, check['target'])
    if check['type'] == 'absent':
        passed = not matches
    else:
        passed = len(matches) == 1
        if passed and check['type'] == 'present':
            passed = 'entity_type' not in check or matches[0].dxftype() == check['entity_type']
        elif passed:
            e, attribute = matches[0], check['attribute']
            value = e.text if attribute == 'text' and e.dxftype() == 'MTEXT' else getattr(e.dxf, attribute)
            passed = values_close(_plain(value), check['value'], tol)
    return {'status': 'passed' if passed else 'failed', 'matches': len(matches)}


def _selectors(value):
    if isinstance(value, dict):
        if set(value) in ({'handle'}, {'id'}):
            yield value
        else:
            for child in value.values():
                yield from _selectors(child)
    elif isinstance(value, list):
        for child in value:
            yield from _selectors(child)


def _identity_errors(before, after, comment):
    errors = []
    for selector in _selectors([comment['targets'], comment.get('checks', [])]):
        old, new = _matches(before, selector), _matches(after, selector)
        if len(old) == len(new) == 1:
            old_id = stable_id(old[0])
            if old[0].dxftype() != new[0].dxftype() or (old_id is not None and old_id != stable_id(new[0])):
                errors.append({'selector': selector, 'reason': 'entity type or known stable ID changed; possible handle reuse'})
    return errors


def check_ledger(doc, ledger, *, before=None, edit_verifications=None):
    validate_ledger(ledger, ledger.get('drawing_sha256'), doc)
    from .edits import bind_replacement_check
    bindings = [*ledger.get('edit_evidence', {}).get('replacements', []), *(edit_verifications or [])]
    superseded = {old for c in ledger['comments'] for old in c.get('supersedes', [])}
    results = []
    for c in ledger['comments']:
        record = {key: deepcopy(c[key]) for key in ('id', 'text', 'source', 'targets')}
        record.update(checks=[], manual_checks=list(c.get('manual_checks', [])))
        if c['id'] in superseded:
            record['status'] = 'superseded'
            results.append(record)
            continue
        for predicate in c.get('checks', []):
            try:
                result = _predicate(doc, bind_replacement_check(predicate, bindings), ledger['tolerance'])
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                result = {'status': 'failed', 'reason': str(error)}
            record['checks'].append({'predicate': deepcopy(predicate), 'result': result})
        record['status'] = ('failed' if any(v['result']['status'] != 'passed' for v in record['checks'])
                            else 'needs_review' if record['manual_checks'] else 'checks_passed')
        record['identity_errors'] = _identity_errors(before, doc, c) if before is not None else []
        if record['identity_errors']:
            record['status'] = 'failed'
        results.append(record)
    status = ('failed' if any(c['status'] == 'failed' for c in results)
              else 'needs_review' if any(c['status'] == 'needs_review' for c in results) else 'checks_passed')
    return {'schema': 'cad-comment-report/v1', 'status': status, 'comments': results,
            'native_interaction_tested': False, 'machine_checks_only': True,
            'identity_scope': 'type and known CAD_SKILL_ID compared with baseline; same-type handle reuse without a stable ID is not provable'}


def write_evidence(before, after, ledger, report, directory):
    from .local_review import _box, _render, analyze_documents
    directory = Path(directory)
    directory.mkdir()
    pages = ['<!doctype html><meta charset="utf-8"><title>批注逐条核对</title>',
             '<style>body{font:16px sans-serif;margin:24px}.pair{display:flex;gap:12px}.pair img{width:49%;object-fit:contain}</style>',
             '<h1>批注逐条核对</h1><p>自动条件、局部候选和人工检查分别记录；图片左右为修改前、修改后，坐标范围与比例一致。</p>']
    for index, (comment, result) in enumerate(zip(ledger['comments'], report['comments']), 1):
        pages.append(f'<h2>{html.escape(comment["id"])} · {html.escape(result["status"])}</h2><p>{html.escape(comment["text"])}</p>')
        if result['status'] == 'superseded':
            continue
        bounds = comment.get('bounds')
        if bounds is None:
            boxes = [box for doc in (before, after) for s in comment['targets'] for e in _matches(doc, s)
                     if (box := _box(e)) is not None]
            if boxes:
                pad = ledger.get('padding', 5.)
                bounds = [min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                          max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad]
        result['view'] = None
        if bounds is not None:
            folder = directory / f'comment-{index:03d}'
            folder.mkdir()
            old = _render(before, [bounds], folder, 'before')[0]
            new = _render(after, [bounds], folder, 'after')[0]
            local = analyze_documents(before, after, bounds=bounds, tolerance=ledger['tolerance'])
            result['view'] = {'bounds': bounds, 'before': f'{folder.name}/{old}', 'after': f'{folder.name}/{new}'}
            result['local_review'] = {'status': local['status'], 'issues': local['issues'], 'coverage': local['coverage'],
                                      'manual_visual_review_required': True}
            pages.append(f'<div class="pair"><img alt="修改前" src="{folder.name}/{old}"><img alt="修改后" src="{folder.name}/{new}"></div>')
        else:
            result['view_unavailable_reason'] = 'No resolvable target bounds; specify an explicit drawing-coordinate bounds.'
        pages.append('<pre>' + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre>')
    (directory / 'index.html').write_text('\n'.join(pages), encoding='utf-8')
    (directory / 'comment-report.json').write_text(_json(report), encoding='utf-8')


def next_ledger(ledger, output_hash, *, edit_verifications=None):
    result = deepcopy(ledger)
    result['drawing_sha256'] = output_hash
    result.setdefault('history', []).append({'before_sha256': ledger['drawing_sha256'], 'after_sha256': output_hash})
    bindings = deepcopy(ledger.get('edit_evidence', {}).get('replacements', []))
    predicates = [check for comment in ledger['comments'] for check in comment.get('checks', [])]
    for binding in edit_verifications or []:
        if binding['operation'] in predicates:
            bindings = [old for old in bindings if old['operation'] != binding['operation']]
            bindings.append(deepcopy(binding))
    if bindings:
        result['edit_evidence'] = {'drawing_sha256': output_hash, 'replacements': bindings}
    return result


def publish_comment_review(before_path, after_path, ledger, output_dir):
    before_path, digest, before = _source(before_path)
    after_path, after_hash, after = _source(after_path)
    ledger, ledger_hash = load_ledger(ledger, digest, before)
    prior = carried_ledger(before_path, digest, before)
    if prior is not None and ledger['comments'][:len(prior['comments'])] != prior['comments']:
        raise RevisionError('preserve previous comments; append new comments with explicit supersedes')
    if before.units != after.units:
        raise RevisionError('before/after drawing units differ')
    report = check_ledger(after, ledger, before=before)
    report.update(before_path=str(before_path), after_path=str(after_path), before_sha256=digest,
                  after_sha256=after_hash, ledger_sha256=ledger_hash)
    output = Path(output_dir).resolve()
    if output.exists():
        raise RevisionError('comment review output must be a new directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cad-comments-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'publish'
        write_evidence(before, after, ledger, report, stage)
        # Preserve failed ledgers' baseline; only successful machine checks can
        # advance the hash. Pending manual checks remain pending in either case.
        carried = next_ledger(ledger, after_hash) if report['status'] != 'failed' else ledger
        (stage / 'comment-ledger.json').write_text(_json(carried), encoding='utf-8')
        if sha256_file(before_path) != digest or sha256_file(after_path) != after_hash:
            raise RevisionError('CAD input changed during comment review')
        if output.exists():
            raise RevisionError('comment output appeared during review')
        os.rename(stage, output)
    return report
