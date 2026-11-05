"""Shared, read-only CAD review renderer with exact font and cache evidence.

One entity drawing pass creates a recorder. PNG crops and vector PDF pages replay
that recording; none of these artifacts proves native DWG or grip behavior.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import warnings

import ezdxf
from ezdxf import disassemble
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.recorder import Recorder
from ezdxf.addons.drawing.unified_text_renderer import UnifiedTextRenderer
from ezdxf.fonts import fonts
from ezdxf.fonts.font_manager import get_ttf_font_face
from ezdxf.tools.text import MTextContext, MTextParser, TokenType
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from cad_skill_core.rendering.preview import (
    PreviewError, _FontBinding, _FONT_RENDER_LOCK, _WarningCollector,
    _bind_exact_backend_font, _post_render_role_evidence, _restore_font_caches,
    _snapshot_font_caches, _verify_glyphs,
)
from .common import RevisionError, sha256_file

_WIDTH, _HEIGHT, _DPI = 1120, 840, 140
_RENDER_VERSION = 'cad-region-renderer/v2'


def _regions(regions):
    if not isinstance(regions, (list, tuple)) or len(regions) > 100:
        raise RevisionError('regions require a list of at most 100 finite CAD bounds')
    result = []
    for index, region in enumerate(regions, 1):
        box = region.get('bounds') if isinstance(region, dict) else region
        name = region.get('id', str(index)) if isinstance(region, dict) else str(index)
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise RevisionError('region bounds require finite xmin ymin xmax ymax with positive area')
        if not isinstance(name, str) or not name:
            raise RevisionError('region id must be a nonempty string')
        if max(abs(v) for v in box) > 1e12 or min(box[2] - box[0], box[3] - box[1]) < 1e-9:
            raise RevisionError('region bounds exceed supported coordinate/precision limits')
        result.append({'id': name, 'bounds': list(map(float, box))})
    return result


def _font_path(style, mapping):
    name = style.dxf.name
    requested = str(style.dxf.get('font', ''))
    explicit = mapping.get(name.casefold())
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
    elif requested and Path(requested).is_file():
        path = Path(requested).resolve()
    elif requested and fonts.font_manager.has_font(Path(requested).name):
        path = fonts.font_manager._font_cache.get(Path(requested).name, '').file_path.resolve()
    else:
        raise RevisionError(f'font unavailable for style {name!r}: {requested!r}; provide font_map with an actual TTF/OTF/TTC file')
    if not path.is_file() or path.suffix.lower() not in {'.ttf', '.ttc', '.otf'}:
        raise RevisionError(f'font missing or unsupported for style {name!r}: {path}; map a verifiable TTF/OTF/TTC file')
    return path


def _inline_face(face):
    # Match exactly as MTEXT layout does, including style and italic flags.
    # UnifiedTextRenderer's default family-only lookup can choose Regular for
    # Italic before it considers the italic flag, changing the rendered glyphs.
    def slanted(candidate):
        return candidate.is_italic or 'oblique' in candidate.style.casefold()
    # ezdxf's family matcher treats Oblique as non-italic and can choose Regular.
    # Both Italic and Oblique are deliberate slanted faces, never a regular fallback.
    candidates = [entry.font_face for entry in fonts.font_manager._font_cache._cache.values()
                  if entry.font_face.family.casefold() == face.family.casefold()
                  and slanted(entry.font_face) == slanted(face)
                  and entry.font_face.is_bold == face.is_bold]
    if not candidates:
        raise RevisionError(f'inline font unavailable: {face.family} {face.style}; no fallback is permitted')
    return min(candidates, key=lambda item: (item.style.casefold() != face.style.casefold(),
                                             abs(item.weight-face.weight), abs(item.width-face.width), item.filename))


class _VerifiedTextRenderer(UnifiedTextRenderer):
    def __init__(self, bindings):
        super().__init__()
        self.paths = {binding.path.name.casefold(): binding.path for binding in bindings}

    def get_font(self, face):
        if not face.filename:
            face = _inline_face(face)
        font = super().get_font(face)
        try:
            actual = Path(font.glyph_cache.font.reader.file.name).resolve()
        except AttributeError as error:
            raise RevisionError('text renderer used an unverified font backend') from error
        if self.paths.get(actual.name.casefold()) != actual:
            raise RevisionError(f'text renderer used an unverified font file: {actual}')
        return font

    def get_text_glyph_paths(self, text, face, cap_height=1.):
        font = self.get_font(face)
        missing = ''.join(c for c in dict.fromkeys(text) if not c.isspace()
                          and font.glyph_cache.cmap.get(ord(c)) in {None, '.notdef'})
        if missing:
            raise RevisionError(f'actual text renderer is missing glyphs: {missing}')
        return super().get_text_glyph_paths(text, face, cap_height)


def _prepare(doc, font_map):
    """Copy before decomposition: virtual entities and renderer caches may mutate."""
    cloned = copy.deepcopy(doc)
    # Decompose before changing any styles. Native virtual TEXT creation can
    # look up a style by its original font; lazy traversal would create new SHX
    # styles after an earlier entity's style has already been remapped.
    entities = list(disassemble.recursive_decompose(cloned.modelspace()))
    if font_map is not None and not isinstance(font_map, dict):
        raise RevisionError('font_map must map DXF style names to font file paths')
    mapping = {}
    for name, path in (font_map or {}).items():
        if not isinstance(name, str) or not isinstance(path, (str, Path)):
            raise RevisionError('font_map must map DXF style names to font file paths')
        if name.casefold() in mapping:
            raise RevisionError(f'duplicate font_map style: {name}')
        if name not in cloned.styles:
            raise RevisionError(f'font_map style does not exist: {name}')
        mapping[name.casefold()] = path
    usages, style_paths, originals = {}, {}, {}
    for entity in entities:
        if entity.dxftype() not in {'TEXT', 'ATTRIB', 'ATTDEF', 'MTEXT'}:
            continue
        style_name = str(entity.dxf.get('style', 'Standard'))
        style = cloned.styles.get(style_name)
        key = style_name.casefold()
        if key not in style_paths:
            style_paths[key] = _font_path(style, mapping)
            originals[key] = str(style.dxf.get('font', ''))
        path = style_paths[key]
        # MTEXT layout also needs the base style for empty lines and spaces,
        # even when every visible word has an inline font override.
        usages.setdefault((style_name, path), {'source_font': originals[key], 'characters': ''})
        # The basename is essential: ezdxf interprets a full path as an unknown
        # cache key and may silently substitute its fallback font.
        style.dxf.font = path.name
        if entity.dxftype() == 'MTEXT':
            ctx = MTextContext()
            ctx.font_face = get_ttf_font_face(path)
            tokens = MTextParser(entity.text, ctx=ctx)
            chunks = []
            for token in tokens:
                if token.type == TokenType.WORD:
                    text = token.data
                elif token.type == TokenType.STACK:
                    text = ''.join(token.data[:2])
                else:
                    continue
                face = token.ctx.font_face
                if face == ctx.font_face:
                    chunks.append((style_name, path, text, originals[key]))
                    continue
                matched = _inline_face(face)
                inline_path = fonts.font_manager._font_cache.get(Path(matched.filename).name, '').file_path.resolve()
                chunks.append((f'{style_name}:inline:{face.family}:{face.weight}', inline_path, text, face.family))
        else:
            chunks = [(style_name, path, entity.plain_text(), originals[key])]
        for role, selected, text, original in chunks:
            entry = usages.setdefault((role, selected), {'source_font': original, 'characters': ''})
            entry['characters'] += text
    bindings, evidence, basenames = [], [], {}
    for (role, path), usage in sorted(usages.items(), key=lambda item: str(item[0])):
        prior = basenames.setdefault(path.name.casefold(), path)
        if prior != path:
            raise RevisionError(f'same font basename resolves to different files: {prior} and {path}')
        characters = ''.join(dict.fromkeys(usage['characters']))
        if any(c in characters for c in ('\ufffd', '\u25a1')):
            raise RevisionError(f'font text contains missing glyph replacement markers in {role}')
        try:
            _verify_glyphs(path, characters, role)
        except PreviewError as error:
            raise RevisionError(str(error)) from error
        bindings.append(_FontBinding(role, usage['source_font'], path, path.name, characters))
        evidence.append({'style': role, 'source_font': usage['source_font'], 'path': str(path),
                         'sha256': sha256_file(path), 'characters': characters})
    return cloned, tuple(bindings), evidence


def _record(prepared):
    doc, bindings, evidence = prepared
    snapshots = _snapshot_font_caches(bindings)
    matches = dict(fonts.font_manager._match_cache)
    collector = _WarningCollector()
    root_logger = logging.getLogger()
    root_logger.addHandler(collector)
    try:
        fonts.font_manager._match_cache.clear()
        for binding in bindings:
            _bind_exact_backend_font(binding)
        recorder = Recorder()
        # MONOCHROME retains white; BLACK would turn MTEXT canvas masks black.
        config = Configuration(color_policy=ColorPolicy.MONOCHROME, background_policy=BackgroundPolicy.WHITE)
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            context = RenderContext(doc)
            # Native virtual text may recreate a font-named style when its
            # original style was mapped. Resolve only unambiguous, verified
            # original-font aliases; never let that virtual style use fallback.
            aliases = {}
            for binding in bindings:
                if ':inline:' not in binding.role and binding.font:
                    aliases.setdefault(binding.font.casefold(), set()).add(binding.path)
            for name, paths in aliases.items():
                if name not in context.fonts and len(paths) == 1:
                    context.fonts[name] = get_ttf_font_face(next(iter(paths)))
            frontend = Frontend(context, recorder, config=config)
            # The library default is a class-level cache of AbstractFont objects;
            # it survives backend rebinding between sequential render requests.
            frontend.pipeline.text_engine = _VerifiedTextRenderer(bindings)
            frontend.draw_layout(doc.modelspace(), finalize=True)
        issues = collector.messages + [str(w.message) for w in captured]
        if issues:
            raise RevisionError('render warning prevented verification: ' + '; '.join(issues))
        for binding, item in zip(bindings, evidence, strict=True):
            actual = _post_render_role_evidence(binding)
            item.update(backend_path=actual.backend_font, backend_exact=actual.backend_exact,
                        glyphs_verified=actual.glyphs_verified)
            if sha256_file(binding.path) != item['sha256']:
                raise RevisionError(f'font changed during rendering: {binding.path}')
        return recorder.player()
    except PreviewError as error:
        raise RevisionError(str(error)) from error
    finally:
        root_logger.removeHandler(collector)
        _restore_font_caches(snapshots, bindings)
        fonts.font_manager._match_cache.clear()
        fonts.font_manager._match_cache.update(matches)


def _default_region(player):
    box = player.bbox()
    if not box.has_data:
        raise RevisionError('drawing has no renderable extents; provide an explicit region')
    x0, y0, x1, y1 = box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y
    pad = max(x1 - x0, y1 - y0, 1.) * .03
    return _regions([[x0 - pad, y0 - pad, x1 + pad, y1 + pad]])


def _export(player, regions, output, label, pdf):
    regions = _default_region(player) if regions is None else regions
    scale = min((_WIDTH * .95) / max((r['bounds'][2] - r['bounds'][0] for r in regions), default=1),
                (_HEIGHT * .90) / max((r['bounds'][3] - r['bounds'][1] for r in regions), default=1))
    images, views = [], []
    pdf_name = f'{label}.pdf' if pdf else None
    pdf_pages = PdfPages(output / pdf_name) if pdf else None
    try:
        for index, region in enumerate(regions, 1):
            box = region['bounds']
            width, height = (box[2] - box[0]) * scale / _WIDTH, (box[3] - box[1]) * scale / _HEIGHT
            fig = plt.figure(figsize=(8, 6), dpi=_DPI)
            try:
                rect = [(1 - width) / 2, .025 + (.90 - height) / 2, width, height]
                ax = fig.add_axes(rect)
                # Clip the recorded vectors, not a raster screenshot. Cropping a
                # copy leaves the one master recording available for every view.
                crop = player.copy()
                crop.crop_rect(box[:2], box[2:], distance=.1 / scale)
                crop.replay(MatplotlibBackend(ax))
                fig.set_size_inches(8, 6, forward=True)
                ax.set_position(rect)
                ax.set_xlim(box[0], box[2])
                ax.set_ylim(box[1], box[3])
                ax.set_aspect('equal', adjustable='box')
                ax.axis('off')
                fig.suptitle(f'{label} | region {index}', fontsize=12)
                name = f'region-{index:02d}-{label}.png'
                fig.savefig(output / name, dpi=_DPI, facecolor='white')
                if pdf_pages is not None:
                    # PDF backend serializes the vector artists independently;
                    # no PNG image is embedded to manufacture a review PDF.
                    pdf_pages.savefig(fig, facecolor='white')
                images.append(name)
                views.append({**region, 'image': name, 'pixels_per_unit': scale,
                              'canvas_pixels': [_WIDTH, _HEIGHT], 'axes_fraction': rect})
            finally:
                plt.close(fig)
    finally:
        if pdf_pages is not None:
            pdf_pages.close()
    return {'schema': 'cad-render/v1', 'status': 'rendered', 'images': images, 'pdf': pdf_name,
            'regions': regions, 'views': views, 'drawing_pass_count': 1,
            'native_interaction_tested': False, 'native_dwg_tested': False,
            'manual_visual_review_required': True,
            'limitations': ['Review graphics only; native CAD fonts, grips and DWG behavior are not verified.',
                            'Cropped curves crossing a crop boundary are approximated within 0.1 output pixel.']}


def _validate_label(label):
    if not isinstance(label, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', label) is None:
        raise RevisionError('render label requires 1-64 ASCII letters, digits, underscores or hyphens')


def render_regions(doc, regions, output: Path, label: str, *, font_map=None) -> dict:
    """Render ordered bounds or ``{id, bounds}`` regions at one common scale.

    Missing/unsupported fonts and missing glyphs raise RevisionError. Explicit
    font mappings apply only to a document copy; source text styles are intact.
    """
    _validate_label(label)
    regions = _regions(regions)
    output = Path(output)
    with _FONT_RENDER_LOCK:
        prepared = _prepare(doc, font_map)
        player = _record(prepared)
        output.mkdir(parents=True, exist_ok=True)
        report = _export(player, regions, output, label, False)
        report['font_evidence'] = prepared[2]
        if any(sha256_file(Path(item['path'])) != item['sha256'] for item in prepared[2]):
            raise RevisionError('font changed during preview')
    return report


def _cache_report(directory, key, options):
    try:
        from .workflow import load_revision_plan
        manifest = directory / 'preview-report.json'
        if directory.resolve() != directory or manifest.resolve() != manifest:
            return None
        report = load_revision_plan(manifest)
        if (not isinstance(report, dict) or report.get('schema') != 'cad-preview/v1'
                or report.get('cache_key') != key or report.get('render_options') != options
                or report.get('source_sha256') != options['source_sha256']
                or report.get('status') != 'rendered' or report.get('native_interaction_tested') is not False
                or report.get('native_dwg_tested') is not False
                or report.get('manual_visual_review_required') is not True):
            return None
        count = len(options['regions']) if options['regions'] is not None else 1
        images = [f'region-{i:02d}-preview.png' for i in range(1, count + 1)]
        pdf = 'preview.pdf' if options['pdf'] else None
        if report.get('images') != images or report.get('pdf') != pdf:
            return None
        evidence = report.get('font_evidence')
        if not isinstance(evidence, list) or len(evidence) != len(options['fonts']):
            return None
        for item, expected in zip(evidence, options['fonts'], strict=True):
            if (not isinstance(item, dict) or any(item.get(k) != v for k, v in expected.items())
                    or item.get('backend_path') != expected['path']
                    or item.get('backend_exact') is not True or item.get('glyphs_verified') is not True):
                return None
        artifacts = report['artifacts']
        required = images + ([pdf] if pdf else [])
        if not isinstance(artifacts, dict) or set(required) != set(artifacts):
            return None
        for name, digest in artifacts.items():
            artifact = directory / name
            if artifact.resolve() != artifact or not artifact.is_file() or sha256_file(artifact) != digest:
                return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


def preview_file(source, output_dir, *, regions=None, font_map=None, pdf=False, cache=None) -> dict:
    """Atomically publish a DXF preview; optional cache is a dedicated directory."""
    from .drawing_set import read_dxf_snapshot
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.dxf':
        raise RevisionError('preview accepts DXF source files only')
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    doc = read_dxf_snapshot(source_bytes)
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise RevisionError('preview output must be a new directory')
    if not isinstance(pdf, bool):
        raise RevisionError('pdf must be true or false')
    normalized = _regions(regions) if regions is not None else None
    if normalized == []:
        raise RevisionError('preview requires at least one region')
    output.parent.mkdir(parents=True, exist_ok=True)
    with _FONT_RENDER_LOCK:
        prepared = _prepare(doc, font_map)
        options = {'renderer': _RENDER_VERSION, 'ezdxf': ezdxf.__version__, 'regions': normalized,
                   'pdf': pdf, 'source_sha256': digest, 'fonts': prepared[2],
                   'canvas_pixels': [_WIDTH, _HEIGHT], 'dpi': _DPI,
                   'color_policy': 'MONOCHROME', 'background': 'WHITE'}
        key = hashlib.sha256(json.dumps(options, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
        cache_root = Path(cache).expanduser().resolve() if cache is not None else None
        if cache_root is not None and (cache_root == output or cache_root.is_relative_to(output)):
            raise RevisionError('cache must be outside the new preview output directory')
        entry = cache_root / key if cache_root is not None else None
        if cache_root is not None and cache_root.exists() and not cache_root.is_dir():
            raise RevisionError('cache must be a directory')
        cached = _cache_report(entry, key, options) if entry is not None and entry.is_dir() else None
        with tempfile.TemporaryDirectory(prefix='.cad-preview-', dir=output.parent) as temporary:
            stage = Path(temporary) / 'publish'
            stage.mkdir()
            if cached is not None:
                try:
                    for name, artifact_digest in cached['artifacts'].items():
                        shutil.copyfile(entry / name, stage / name)
                        if sha256_file(stage / name) != artifact_digest:
                            cached = None
                            break
                except OSError:
                    cached = None
            if cached is not None:
                report = dict(cached, cache_hit=True, drawing_pass_count=0)
            else:
                player = _record(prepared)
                report = _export(player, normalized, stage, 'preview', pdf)
                report.update(font_evidence=prepared[2], cache_hit=False)
            report.update(schema='cad-preview/v1', source_path=str(source), source_sha256=digest,
                          cache_key=key, render_options=options, units=doc.units)
            names = report['images'] + ([report['pdf']] if report['pdf'] else [])
            report['artifacts'] = {name: sha256_file(stage / name) for name in names}
            if sha256_file(source) != digest:
                raise RevisionError('CAD input changed during preview')
            if any(sha256_file(Path(item['path'])) != item['sha256'] for item in prepared[2]):
                raise RevisionError('font changed during preview')
            (stage / 'preview-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            if output.exists():
                raise RevisionError('preview output appeared during execution')
            os.rename(stage, output)
        if entry is not None and cached is None:
            # Do not recursively replace an existing untrusted/stale cache tree.
            # A stale entry is ignored; leave it available for diagnosis.
            cache_root.mkdir(parents=True, exist_ok=True)
            if not entry.exists():
                with tempfile.TemporaryDirectory(prefix='.cad-cache-', dir=cache_root) as temporary:
                    staged_cache = Path(temporary) / 'entry'
                    shutil.copytree(output, staged_cache)
                    if not entry.exists():
                        os.rename(staged_cache, entry)
    return report
