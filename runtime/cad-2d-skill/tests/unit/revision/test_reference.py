import importlib
import json
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.revision.common import RevisionError, sha256_file


def compare(*args):
    return importlib.import_module('cad_skill_core.revision.reference').compare_reference(*args)


def case(tmp_path, current_length=20, reference_length=10):
    selectors = []
    for name, length in [('current', current_length), ('reference', reference_length)]:
        doc = ezdxf.new('R2018')
        doc.units = 4
        line = doc.modelspace().add_line((0, 0), (length, 0))
        selectors.append({'handle': line.dxf.handle})
        doc.saveas(tmp_path / f'{name}.dxf')
    current, reference = tmp_path / 'current.dxf', tmp_path / 'reference.dxf'
    mapping = {'schema': 'cad-reference-map/v1', 'units': 'mm',
               'current_sha256': sha256_file(current), 'reference_sha256': sha256_file(reference),
               'regions': [{'id': 'slot', 'current_bounds': [-2, -3, 23, 4],
                            'reference_bounds': [-1, -2, 11, 3], 'mode': 'depiction'}],
               'parameters': [{'id': 'depth', 'current': selectors[0], 'reference': selectors[1],
                               'measure': 'length', 'mode': 'preserve_current',
                               'current_expected': 20, 'tolerance': 0.001}]}
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps(mapping), encoding='utf-8')
    return current, reference, path, mapping


def write(path, mapping):
    path.write_text(json.dumps(mapping), encoding='utf-8')


def test_preserve_current_reports_difference_without_changing_either_source(tmp_path):
    c, r, p, mapping = case(tmp_path)
    report = compare(c, r, p, tmp_path / 'out')
    assert report['status'] == 'checked'
    item = report['parameters'][0]
    assert item['current_actual'] == 20
    assert item['reference_actual'] == 10
    assert item['difference'] == 10
    assert item['expected'] == 20
    assert item['status'] == 'checked'
    assert sha256_file(c) == mapping['current_sha256']
    assert sha256_file(r) == mapping['reference_sha256']
    assert report['visual_review'] == 'required'
    regions = report['regions']
    a, b = regions[0]['current_render_bounds'], regions[0]['reference_render_bounds']
    assert a[2]-a[0] == b[2]-b[0] and a[3]-a[1] == b[3]-b[1]
    assert report['render']['current']['drawing_pass_count'] == 1
    assert (tmp_path / 'out' / 'reference-review.html').is_file()
    assert list((tmp_path / 'out').glob('*.png'))


@pytest.mark.parametrize('authorization,status', [(None, 'needs_authorization'),
                                                   ('User approved slot depth synchronization.', 'needs_update')])
def test_adopting_sample_requires_evidence_and_checks_actual_current(tmp_path, authorization, status):
    c, r, p, mapping = case(tmp_path)
    item = mapping['parameters'][0]
    item.pop('current_expected')
    item['mode'] = 'adopt_reference'
    if authorization:
        item['authorization'] = authorization
    write(p, mapping)
    report = compare(c, r, p, tmp_path / 'out')
    assert report['status'] == status
    assert report['parameters'][0]['expected'] == 10
    assert sha256_file(c) == mapping['current_sha256']


def test_authorized_adoption_passes_only_when_actual_matches(tmp_path):
    c, r, p, mapping = case(tmp_path, 10, 10)
    mapping['parameters'][0].update(mode='adopt_reference', authorization='Explicit user approval')
    mapping['parameters'][0].pop('current_expected')
    write(p, mapping)
    assert compare(c, r, p, tmp_path / 'out')['status'] == 'checked'


@pytest.mark.parametrize('bad', ['hash', 'units', 'selector', 'unknown', 'bounds', 'duplicate', 'bool', 'authorization'])
def test_invalid_binding_or_mapping_does_not_publish(tmp_path, bad):
    c, r, p, mapping = case(tmp_path)
    if bad == 'hash': mapping['reference_sha256'] = '0' * 64
    if bad == 'units': mapping['units'] = 'in'
    if bad == 'selector': mapping['parameters'][0]['reference'] = {'handle': 'FFF'}
    if bad == 'unknown': mapping['parameters'][0]['scaling'] = 2
    if bad == 'bounds': mapping['regions'][0]['current_bounds'][2] = -3
    if bad == 'duplicate': mapping['parameters'] *= 2
    if bad == 'bool': mapping['parameters'][0]['current_expected'] = True
    if bad == 'authorization': mapping['parameters'][0]['authorization'] = True
    write(p, mapping)
    with pytest.raises(RevisionError): compare(c, r, p, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_preserved_dimension_is_checked_against_explicit_invariant(tmp_path):
    c, r, p, mapping = case(tmp_path)
    mapping['parameters'][0]['current_expected'] = 21
    write(p, mapping)
    assert compare(c, r, p, tmp_path / 'out')['status'] == 'needs_update'


def test_depiction_only_is_not_claimed_checked(tmp_path):
    c, r, p, mapping = case(tmp_path)
    mapping['parameters'] = []
    write(p, mapping)
    assert compare(c, r, p, tmp_path / 'out')['status'] == 'needs_visual_review'


def test_duplicate_json_and_nonfinite_values_rejected(tmp_path):
    c, r, p, _ = case(tmp_path)
    for raw in ['{"schema":"a","schema":"b"}', '{"x":NaN}']:
        p.write_text(raw, encoding='utf-8')
        with pytest.raises(RevisionError): compare(c, r, p, tmp_path / 'out')


def test_same_file_alias_and_existing_output_rejected(tmp_path):
    c, r, p, mapping = case(tmp_path)
    with pytest.raises(RevisionError): compare(c, c, p, tmp_path / 'out')
    out = tmp_path / 'out'
    out.mkdir()
    marker = out / 'keep.txt'
    marker.write_text('keep')
    with pytest.raises(RevisionError): compare(c, r, p, out)
    assert marker.read_text() == 'keep'


def test_unitless_pair_cannot_implicitly_claim_a_physical_scale(tmp_path):
    c, r, p, mapping = case(tmp_path)
    for path in (c, r):
        doc = ezdxf.readfile(path)
        doc.units = 0
        doc.saveas(path)
    mapping.update(units='unitless', current_sha256=sha256_file(c), reference_sha256=sha256_file(r))
    write(p, mapping)
    with pytest.raises(RevisionError, match='units'):
        compare(c, r, p, tmp_path / 'out')


def test_input_changes_during_render_prevent_publication(tmp_path, monkeypatch):
    c, r, p, mapping = case(tmp_path)
    renderer = importlib.import_module('cad_skill_core.revision.preview')
    real_render = renderer.render_regions
    def changed(*args, **kwargs):
        result = real_render(*args, **kwargs)
        c.write_bytes(c.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(renderer, 'render_regions', changed)
    with pytest.raises(RevisionError, match='changed'):
        compare(c, r, p, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
