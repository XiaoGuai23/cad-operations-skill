import json

import ezdxf
from PIL import Image
from pypdf import PdfReader

from cad_skill_core.cli import main
from cad_skill_core.revision.common import sha256_file


def test_related_drawings_cli_nonzero_for_actual_unsynchronized_features(tmp_path, capsys):
    drawings, members = [], []
    for name, length in [('assembly', 20), ('part', 10)]:
        doc = ezdxf.new('R2018')
        doc.units = 4
        e = doc.modelspace().add_line((0, 0), (length, 0))
        path = tmp_path / f'{name}.dxf'
        doc.saveas(path)
        drawings.append({'id': name, 'path': path.name, 'sha256': sha256_file(path), 'editable': name == 'assembly'})
        members.append({'drawing': name, 'selector': {'handle': e.dxf.handle}, 'measure': 'length'})
    manifest = {'schema': 'cad-drawing-set/v1', 'units': 'mm', 'drawings': drawings,
                'parameters': [{'id': 'depth', 'expected': 20, 'tolerance': 1e-6, 'members': members}]}
    path = tmp_path / 'set.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    assert main(['check-drawing-set', str(path), '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['parameters'][0]['members'][1]['actual'] == 10
    assert report['parameters'][0]['members'][1]['scope'] == 'out_of_scope'
    manifest['drawings'][1]['sha256'] = '0' * 64
    path.write_text(json.dumps(manifest), encoding='utf-8')
    assert main(['check-drawing-set', str(path), '--json']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'


def test_preview_cli_emits_readable_png_pdf_and_preserves_source(tmp_path, capsys):
    doc = ezdxf.new('R2018')
    doc.units = 4
    doc.modelspace().add_circle((0, 0), 10)
    source = tmp_path / 'drawing.dxf'
    doc.saveas(source)
    digest = sha256_file(source)
    output = tmp_path / 'preview'
    assert main(['preview', str(source), '--out', str(output), '--box', '-11', '-11', '11', '11', '--pdf', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['native_interaction_tested'] is False
    assert report['status'] == 'rendered'
    for path in output.glob('*.png'):
        with Image.open(path) as img:
            assert img.width > 100 and img.height > 100
    pdfs = list(output.glob('*.pdf'))
    assert pdfs and len(PdfReader(pdfs[0]).pages) == 1
    assert sha256_file(source) == digest


def test_operations_discovery_lists_existing_and_new_supported_operations(capsys):
    assert main(['operations', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    entries = {o['type']: o for o in result['operations']}
    assert set(entries) == {'align', 'contact', 'resize_fixed_edge', 'set_length',
                            'dimension_move', 'dimension_text_mode', 'leader_move', 'link_callout',
                            'split_intersections', 'delete_entities', 'replace_outline', 'dimension_remeasure',
                            'dimension_drive'}
    assert entries['dimension_remeasure']['required'] == ['target']
    assert entries['dimension_remeasure']['optional'] == ['p1', 'p2', 'mode', 'geometry']
    assert entries['dimension_drive']['required'] == ['target', 'mode', 'value', 'geometry']
    assert result['native_interaction_tested'] is False


def test_reference_cli_depiction_only_requires_review(tmp_path, capsys):
    paths = []
    for name in ['current', 'sample']:
        doc = ezdxf.new('R2018')
        doc.units = 4
        doc.modelspace().add_line((0, 0), (5, 0))
        path = tmp_path / f'{name}.dxf'
        doc.saveas(path)
        paths.append(path)
    mapping = {'schema': 'cad-reference-map/v1', 'units': 'mm', 'current_sha256': sha256_file(paths[0]),
               'reference_sha256': sha256_file(paths[1]), 'parameters': [],
               'regions': [{'id': 'outline', 'current_bounds': [-1, -1, 6, 2],
                            'reference_bounds': [-1, -1, 6, 2], 'mode': 'depiction'}]}
    plan = tmp_path / 'mapping.json'
    plan.write_text(json.dumps(mapping), encoding='utf-8')
    assert main(['compare-reference', *map(str, paths), str(plan), '--out', str(tmp_path / 'out'), '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'needs_visual_review'
