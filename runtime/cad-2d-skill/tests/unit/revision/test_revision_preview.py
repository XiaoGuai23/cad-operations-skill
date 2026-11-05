"""Shared review rendering: real pixels, exact fonts, immutable sources and cache."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from matplotlib import font_manager
from PIL import Image
from pypdf import PdfReader

from cad_skill_core.revision.common import RevisionError, sha256_file


@pytest.fixture
def preview():
    name = 'cad_skill_core.revision.preview'
    assert importlib.util.find_spec(name) is not None, 'shared revision renderer is missing'
    return importlib.import_module(name)


@pytest.fixture
def font():
    return Path(font_manager.findfont('DejaVu Sans')).resolve()


def drawing(text=None):
    doc = ezdxf.new('R2018')
    doc.units = 4
    doc.modelspace().add_line((0, 0), (20, 0))
    if text:
        doc.modelspace().add_text(text, dxfattribs={'height': 2, 'insert': (0, 3)})
    return doc


def test_multiple_crops_draw_entities_once_and_have_equal_paper_scale(preview, tmp_path, monkeypatch):
    from ezdxf.addons.drawing import Frontend
    original = Frontend.draw_layout
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(args[0])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Frontend, 'draw_layout', counted)
    report = preview.render_regions(drawing(), [[-1, -1, 21, 9], {'id': 'small', 'bounds': [0, -1, 11, 4]}], tmp_path, 'test')
    assert len(calls) == report['drawing_pass_count'] == 1
    assert len(report['images']) == 2
    assert len(set(v['pixels_per_unit'] for v in report['views'])) == 1
    assert [Image.open(tmp_path / p).size for p in report['images']] == [(1120, 840)] * 2


@pytest.mark.parametrize('fill', ['canvas', (255, 255, 255)])
def test_white_mtext_mask_stays_white_and_hides_line(preview, tmp_path, font, fill):
    doc = drawing()
    # The mask occupies most of this crop; BLACK color policy produces >50% dark pixels.
    doc.modelspace().add_line((-4, 0), (24, 0))
    text = doc.modelspace().add_mtext('TEST', dxfattribs={'insert': (0, 3), 'char_height': 3, 'width': 16})
    text.set_bg_color(fill, scale=1.5)
    result = preview.render_regions(doc, [[-2, -3, 19, 6]], tmp_path, 'mask', font_map={'Standard': font})
    pixels = np.array(Image.open(tmp_path / result['images'][0]).convert('RGB'))
    assert (pixels.mean(axis=2) < 60).mean() < 0.05
    # Compare with no mask to verify it is not merely omitted.
    text.dxf.bg_fill = 0
    other = preview.render_regions(doc, [[-2, -3, 19, 6]], tmp_path, 'unmasked', font_map={'Standard': font})
    unmasked = np.array(Image.open(tmp_path / other['images'][0]).convert('RGB'))
    assert ((unmasked.mean(axis=2) < 60) & (pixels.mean(axis=2) > 240)).sum() > 100


def test_font_evidence_is_actual_backend_file_and_source_metadata_is_unchanged(preview, tmp_path, font):
    doc = drawing('TEST')
    doc.styles.get('Standard').dxf.font = 'original.shx'
    doc.appids.add('PRIVATE')
    doc.styles.get('Standard').set_xdata('PRIVATE', [(1000, 'preserve me')])
    before = copy.deepcopy(doc.styles.get('Standard').dxfattribs())
    header = dict(doc.header.hdrvars)
    report = preview.render_regions(doc, [[-1, -1, 21, 10]], tmp_path, 'font', font_map={'Standard': font})
    evidence = report['font_evidence'][0]
    assert Path(evidence['path']) == Path(evidence['backend_path']) == font
    assert evidence['sha256'] == sha256_file(font)
    assert evidence['backend_exact'] and evidence['glyphs_verified']
    assert evidence['source_font'] == 'original.shx'
    assert doc.styles.get('Standard').dxfattribs() == before
    assert doc.styles.get('Standard').get_xdata('PRIVATE')[0].value == 'preserve me'
    assert dict(doc.header.hdrvars) == header


def test_font_missing_glyph_is_not_silently_substituted(preview, tmp_path, font):
    with pytest.raises(RevisionError, match='missing.*glyph'):
        preview.render_regions(drawing('中文'), [[-1, -1, 21, 10]], tmp_path, 'missing', font_map={'Standard': font})
    assert not list(tmp_path.glob('*.png'))


def test_missing_font_does_not_fall_back_to_arial(preview, tmp_path):
    doc = drawing('TEST')
    doc.styles.get('Standard').dxf.font = 'no-such-cad-font-7654321.ttf'
    with pytest.raises(RevisionError, match='font.*(missing|unavailable|resolve|map)'):
        preview.render_regions(doc, [[-1, -1, 21, 10]], tmp_path, 'missing')


def test_preview_pdf_is_vector_source_unchanged_and_cache_rejects_stale_artifact(preview, tmp_path):
    source = tmp_path / 'source.dxf'
    drawing().saveas(source)
    digest = sha256_file(source)
    cache = tmp_path / 'cache'
    first = preview.preview_file(source, tmp_path / 'one', regions=[[-1, -1, 21, 9]], pdf=True, cache=cache)
    assert first['status'] == 'rendered' and not first['cache_hit']
    assert first['drawing_pass_count'] == 1
    assert not first['native_interaction_tested']
    pdf = PdfReader(tmp_path / 'one' / first['pdf'])
    assert len(pdf.pages) == 1
    assert b' m' in pdf.pages[0].get_contents().get_data()
    assert all(obj.get_object().get('/Subtype') != '/Image'
               for obj in pdf.pages[0]['/Resources'].get('/XObject', {}).values())
    second = preview.preview_file(source, tmp_path / 'two', regions=[[-1, -1, 21, 9]], pdf=True, cache=cache)
    assert second['cache_hit'] and second['drawing_pass_count'] == 0
    cached_png = next(cache.rglob('*.png'))
    cached_png.write_bytes(b'stale image')
    third = preview.preview_file(source, tmp_path / 'three', regions=[[-1, -1, 21, 9]], pdf=True, cache=cache)
    assert not third['cache_hit']
    assert Image.open(tmp_path / 'three' / third['images'][0]).size == (1120, 840)
    assert sha256_file(source) == digest


def test_cache_key_binds_regions_source_and_font_contents(preview, tmp_path, font):
    import shutil
    source = tmp_path / 'source.dxf'
    drawing('TEST').saveas(source)
    mapped = tmp_path / 'mapped.ttf'
    shutil.copyfile(font, mapped)
    cache = tmp_path / 'cache'
    def run(name, region):
        return preview.preview_file(source, tmp_path / name, regions=[region], font_map={'Standard': mapped}, cache=cache)
    first = run('one', [-1, -1, 21, 9])
    other_region = run('two', [-2, -1, 21, 9])
    assert not other_region['cache_hit'] and first['cache_key'] != other_region['cache_key']
    # An extra harmless SFNT trailing byte changes the file fingerprint.
    with mapped.open('ab') as stream:
        stream.write(b'\0')
    changed_font = run('three', [-1, -1, 21, 9])
    assert not changed_font['cache_hit'] and first['cache_key'] != changed_font['cache_key']
    changed = ezdxf.readfile(source)
    changed.modelspace().add_circle((5, 5), 1)
    changed.saveas(source)
    changed_source = run('four', [-1, -1, 21, 9])
    assert not changed_source['cache_hit'] and changed_font['cache_key'] != changed_source['cache_key']


def test_review_exposes_font_and_single_pass_evidence_without_changing_status(preview, tmp_path):
    from cad_skill_core.revision.local_review import review_files
    source, target = tmp_path / 'before.dxf', tmp_path / 'after.dxf'
    doc = drawing()
    doc.saveas(source)
    doc.modelspace().add_line((100, 100), (120, 100))
    doc.saveas(target)
    report = review_files(source, target, tmp_path / 'review')
    assert report['status'] == 'checked_with_limits'
    assert report['render_evidence']['before']['drawing_pass_count'] == 1
    assert report['render_evidence']['after']['drawing_pass_count'] == 1
    assert not report['native_interaction_tested']
    assert json.loads((tmp_path / 'review' / 'review-report.json').read_text(encoding='utf-8'))['render_evidence']


def test_same_font_basename_in_sequential_previews_uses_current_file(preview, tmp_path, font, monkeypatch):
    import shutil
    from ezdxf.addons.drawing.unified_text_renderer import UnifiedTextRenderer
    actual_paths = []
    original = UnifiedTextRenderer.get_font

    def observed(self, face):
        result = original(self, face)
        actual_paths.append(Path(result.glyph_cache.font.reader.file.name).resolve())
        return result

    monkeypatch.setattr(UnifiedTextRenderer, 'get_font', observed)
    for index in range(2):
        directory = tmp_path / str(index)
        directory.mkdir()
        mapped = directory / 'same-basename.ttf'
        shutil.copyfile(font, mapped)
        actual_paths.clear()
        report = preview.render_regions(drawing('TEST'), [[-1, -1, 21, 9]], directory, 'font',
                                        font_map={'Standard': mapped})
        assert actual_paths and set(actual_paths) == {mapped.resolve()}
        assert report['font_evidence'][0]['backend_path'] == str(mapped.resolve())


def test_inline_italic_uses_the_font_reported_in_evidence(preview, tmp_path, monkeypatch):
    from ezdxf.addons.drawing.unified_text_renderer import UnifiedTextRenderer
    from ezdxf.fonts import fonts
    from ezdxf.fonts.font_manager import get_ttf_font_face
    # Choose an installed family with both faces; no implicit test-only fallback.
    regular = Path(font_manager.findfont('DejaVu Sans'))
    italic = Path(font_manager.findfont(font_manager.FontProperties(family='DejaVu Sans', style='oblique')))
    for path in (regular, italic):
        fonts.font_manager._font_cache.add_entry(path, get_ttf_font_face(path))
    actual_paths = []
    original = UnifiedTextRenderer.get_font

    def observed(self, face):
        result = original(self, face)
        actual_paths.append(Path(result.glyph_cache.font.reader.file.name).resolve())
        return result

    monkeypatch.setattr(UnifiedTextRenderer, 'get_font', observed)
    doc = drawing()
    doc.modelspace().add_mtext(r'{\fDejaVu Sans|i1;ITALIC}',
                              dxfattribs={'insert': (0, 4), 'char_height': 2, 'width': 20})
    report = preview.render_regions(doc, [[-1, -1, 21, 9]], tmp_path, 'italic',
                                    font_map={'Standard': regular})
    inline = next(item for item in report['font_evidence'] if ':inline:' in item['style'])
    assert Path(inline['backend_path']) == italic.resolve()
    assert actual_paths and set(actual_paths) == {italic.resolve()}


@pytest.mark.parametrize('corruption', ['non-object', 'options', 'status'])
def test_cache_report_metadata_must_match_request(preview, tmp_path, corruption):
    source = tmp_path / 'source.dxf'
    drawing().saveas(source)
    cache = tmp_path / 'cache'
    preview.preview_file(source, tmp_path / 'one', cache=cache)
    manifest = next(cache.rglob('preview-report.json'))
    report = json.loads(manifest.read_text(encoding='utf-8'))
    if corruption == 'non-object':
        report = []
    elif corruption == 'options':
        report['render_options']['source_sha256'] = '0' * 64
    else:
        report['native_interaction_tested'] = True
    manifest.write_text(json.dumps(report), encoding='utf-8')
    second = preview.preview_file(source, tmp_path / 'two', cache=cache)
    assert not second['cache_hit']
    assert second['status'] == 'rendered' and not second['native_interaction_tested']


def test_cache_artifact_changed_during_copy_is_not_published(preview, tmp_path, monkeypatch):
    source = tmp_path / 'source.dxf'
    drawing().saveas(source)
    cache = tmp_path / 'cache'
    preview.preview_file(source, tmp_path / 'one', cache=cache)
    original = preview.shutil.copyfile

    def changed_copy(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if Path(src).suffix == '.png' and Path(src).is_relative_to(cache):
            Path(dst).write_bytes(b'changed while copying')
        return result

    monkeypatch.setattr(preview.shutil, 'copyfile', changed_copy)
    result = preview.preview_file(source, tmp_path / 'two', cache=cache)
    assert not result['cache_hit']
    assert Image.open(tmp_path / 'two' / result['images'][0]).size == (1120, 840)
