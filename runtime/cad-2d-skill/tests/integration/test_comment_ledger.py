import copy
import hashlib
import json

import ezdxf
import pytest

from cad_skill_core.revision.workflow import apply_revision


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    doc = ezdxf.new('R2018', setup=True)
    doc.units = 4
    a = doc.modelspace().add_line((0, 0), (10, 0))
    b = doc.modelspace().add_line((30, 0), (40, 0))
    path = tmp_path / 'base.dxf'
    doc.saveas(path)
    ledger = {'schema': 'cad-comment-ledger/v1', 'drawing_sha256': sha(path), 'units': 'mm',
              'tolerance': 1e-6, 'comments': [
        {'id': 'C1', 'text': '左侧长度保持为10', 'source': {'message': 'user comment 1'},
         'targets': [{'handle': a.dxf.handle}], 'checks': [
             {'type': 'set_length', 'target': {'handle': a.dxf.handle}, 'fixed': 'start', 'length': 10}],
         'manual_checks': []}]}
    return path, ledger, a.dxf.handle, b.dxf.handle


def plan(path, target, length):
    return {'schema': 'cad-revision/v1', 'baseline_sha256': sha(path), 'units': 'mm', 'tolerance': 1e-6,
            'operations': [{'id': 'edit', 'type': 'set_length', 'target': {'handle': target},
                            'fixed': 'start', 'length': length}]}


def test_ledger_automatically_rechecks_old_comments_across_rounds(tmp_path):
    source, ledger, a, b = fixture(tmp_path)
    original = source.read_bytes()
    out = tmp_path / 'first'
    result = apply_revision(source, plan(source, b, 15), out, ledger=ledger)
    assert result['comment_checks']['status'] == 'checks_passed'
    propagated = json.loads((out / 'comment-ledger.json').read_text(encoding='utf-8'))
    next_source = out / 'drawing.dxf'
    assert propagated['drawing_sha256'] == sha(next_source)
    assert propagated['comments'] == ledger['comments']
    report = json.loads((out / 'comments/comment-report.json').read_text(encoding='utf-8'))
    assert report['comments'][0]['id'] == 'C1'
    assert report['comments'][0]['view']['before']
    assert (out / 'comments/index.html').is_file()
    second = tmp_path / 'second'
    with pytest.raises(ValueError, match='C1'):
        apply_revision(next_source, plan(next_source, a, 9), second, ledger=propagated)
    # Continuing from a delivered revision must also retain checks when the
    # caller omits the optional flag.
    with pytest.raises(ValueError, match='C1'):
        apply_revision(next_source, plan(next_source, a, 9), second)
    assert not second.exists() and source.read_bytes() == original
    assert sha(next_source) == propagated['drawing_sha256']


def test_carried_comments_cannot_be_silently_removed_or_rewritten(tmp_path):
    source, ledger, a, b = fixture(tmp_path)
    out = tmp_path / 'first'
    apply_revision(source, plan(source, b, 15), out, ledger=ledger)
    carried = json.loads((out / 'comment-ledger.json').read_text(encoding='utf-8'))
    carried['comments'][0]['checks'][0]['length'] = 9
    current = out / 'drawing.dxf'
    with pytest.raises(ValueError, match='previous comments'):
        apply_revision(current, plan(current, a, 9), tmp_path / 'next', ledger=carried)


def test_explicit_supersession_and_manual_checks_are_not_claimed_verified(tmp_path):
    source, ledger, a, b = fixture(tmp_path)
    ledger['comments'].append({'id': 'C2', 'text': '长度改为12', 'source': {'message': 'new instruction'},
        'targets': [{'handle': a}], 'checks': [{'type': 'set_length', 'target': {'handle': a}, 'fixed': 'start', 'length': 12}],
        'manual_checks': ['查看局部线宽'], 'supersedes': ['C1']})
    result = apply_revision(source, plan(source, a, 12), tmp_path / 'out', ledger=ledger)
    assert result['comment_checks']['status'] == 'needs_review'
    assert result['comment_checks']['comments'][0]['status'] == 'superseded'
    assert result['comment_checks']['comments'][1]['status'] == 'needs_review'
    assert not result['native_interaction_tested']


@pytest.mark.parametrize('change', ['wrong_hash', 'duplicate_id', 'future_supersession', 'no_checks', 'unknown_check'])
def test_invalid_ledger_cannot_publish(tmp_path, change):
    source, ledger, a, b = fixture(tmp_path)
    if change == 'wrong_hash':
        ledger['drawing_sha256'] = '0' * 64
    elif change == 'duplicate_id':
        ledger['comments'].append(copy.deepcopy(ledger['comments'][0]))
    elif change == 'future_supersession':
        ledger['comments'][0]['supersedes'] = ['later']
    elif change == 'no_checks':
        ledger['comments'][0]['checks'] = []
    else:
        ledger['comments'][0]['checks'] = [{'type': 'pretend'}]
    with pytest.raises(ValueError):
        apply_revision(source, plan(source, b, 15), tmp_path / 'out', ledger=ledger)
    assert not (tmp_path / 'out').exists()


def test_revision_reuse_is_bound_to_ledger_and_evidence(tmp_path):
    source, ledger, a, b = fixture(tmp_path)
    revision = plan(source, b, 15)
    out = tmp_path / 'out'
    apply_revision(source, revision, out, ledger=ledger)
    assert apply_revision(source, revision, out, ledger=ledger)['reused']
    changed = copy.deepcopy(ledger)
    changed['comments'][0]['text'] = 'new comment'
    with pytest.raises(ValueError, match='ledger'):
        apply_revision(source, revision, out, ledger=changed)
    (out / 'comment-ledger.json').write_text('{}')
    with pytest.raises(ValueError, match='evidence'):
        apply_revision(source, revision, out, ledger=ledger)


def test_check_comments_cli_reports_failed_absence_and_keeps_sources(tmp_path, capsys):
    from cad_skill_core.cli import main
    source, ledger, a, b = fixture(tmp_path)
    ledger['comments'][0]['checks'] = [{'type': 'absent', 'target': {'handle': a}}]
    ledger_path = tmp_path / 'ledger.json'
    ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
    original = source.read_bytes()
    out = tmp_path / 'check'
    assert main(['check-comments', str(source), str(source), str(ledger_path), '--out', str(out), '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'failed'
    assert source.read_bytes() == original
    doc = ezdxf.readfile(source)
    doc.modelspace().delete_entity(doc.entitydb[a])
    after = tmp_path / 'after.dxf'
    doc.saveas(after)
    assert main(['check-comments', str(source), str(after), str(ledger_path), '--out', str(tmp_path / 'pass'), '--json']) == 0


def test_missing_target_never_passes_geometry_or_property_check(tmp_path):
    from cad_skill_core.revision.ledger import check_ledger
    source, ledger, a, b = fixture(tmp_path)
    doc = ezdxf.readfile(source)
    doc.modelspace().delete_entity(doc.entitydb[a])
    assert check_ledger(doc, ledger)['status'] == 'failed'
    ledger['comments'][0]['checks'] = [{'type': 'property', 'target': {'handle': b}, 'attribute': 'start', 'value': [30, 0, 0]}]
    assert check_ledger(doc, ledger)['status'] == 'checks_passed'
    doc.entitydb[b].dxf.start = (31, 0, 0)
    assert check_ledger(doc, ledger)['status'] == 'failed'


@pytest.mark.parametrize('attribute,value', [('color', 256), ('linetype', 'BYLAYER'), ('lineweight', -1)])
def test_property_predicate_uses_effective_dxf_defaults(tmp_path, attribute, value):
    from cad_skill_core.revision.ledger import check_ledger
    source, ledger, a, b = fixture(tmp_path)
    doc = ezdxf.readfile(source)
    ledger['comments'][0]['checks'] = [{'type': 'property', 'target': {'handle': a}, 'attribute': attribute, 'value': value}]
    assert check_ledger(doc, ledger)['status'] == 'checks_passed'


def test_standalone_review_preserves_carried_requirement_history(tmp_path):
    from cad_skill_core.revision.ledger import publish_comment_review
    source, ledger, a, b = fixture(tmp_path)
    out = tmp_path / 'first'
    apply_revision(source, plan(source, b, 15), out, ledger=ledger)
    carried = json.loads((out / 'comment-ledger.json').read_text(encoding='utf-8'))
    current = out / 'drawing.dxf'
    doc = ezdxf.readfile(current)
    doc.entitydb[a].dxf.end = (9, 0, 0)
    after = tmp_path / 'external-after.dxf'
    doc.saveas(after)
    carried['comments'][0]['checks'][0]['length'] = 9
    with pytest.raises(ValueError, match='previous comments'):
        publish_comment_review(current, after, carried, tmp_path / 'review')


@pytest.mark.parametrize('replacement_kind', ['CIRCLE', 'LINE'])
def test_reused_handle_cannot_satisfy_old_object_requirement(tmp_path, replacement_kind):
    from cad_skill_core.revision.ledger import publish_comment_review
    source, ledger, a, b = fixture(tmp_path)
    before = ezdxf.readfile(source)
    before.appids.new('CAD_SKILL_ID')
    before.entitydb[a].set_xdata('CAD_SKILL_ID', [(1000, 'original-item')])
    before.saveas(source)
    ledger['drawing_sha256'] = sha(source)
    ledger['comments'][0]['checks'] = [{'type': 'present', 'target': {'handle': a}}]
    after = copy.deepcopy(before)
    after.modelspace().delete_entity(after.entitydb[a])
    after.entitydb.purge()
    replacement = (after.modelspace().add_circle((50, 50), 2) if replacement_kind == 'CIRCLE'
                   else after.modelspace().add_line((50, 50), (60, 50)))
    replacement.set_xdata('CAD_SKILL_ID', [(1000, 'replacement-item')])
    assert after.entitydb.reset_handle(replacement, a)
    path = tmp_path / 'reused.dxf'
    after.saveas(path)
    report = publish_comment_review(source, path, ledger, tmp_path / 'review')
    assert report['status'] == 'failed'
    assert report['comments'][0]['identity_errors']
