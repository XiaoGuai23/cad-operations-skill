"""Deterministic, headless SVG and PNG previews for reopened DXF files."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from xml.etree import ElementTree

import ezdxf
import matplotlib

matplotlib.use("Agg", force=True)

from ezdxf import bbox
from ezdxf.addons.drawing import RenderContext, layout, svg
from ezdxf.addons.drawing.config import ColorPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.fonts import fonts as ezdxf_fonts
from ezdxf.fonts.font_manager import get_ttf_font_face
from ezdxf.tools.text import MTextParser
from fontTools.ttLib import TTFont
from matplotlib import pyplot as plt
from PIL import Image

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.rendering.deterministic_hatch import (
    DeterministicFrontend,
    deterministic_hatch_factors as _deterministic_hatch_factors,
)


_MAX_PIXELS = 2048
_MAX_MODEL_SPAN = 1_000_000.0
_MAX_ASPECT_RATIO = 10_000.0
_DPI = 100
_MISSING_MARKERS = ("\ufffd", "\u25a1", ".notdef")
_FONT_RENDER_LOCK = threading.RLock()


class PreviewError(ValueError):
    """Controlled, path-aware preview failure."""


@dataclass(frozen=True, slots=True)
class PreviewResult:
    svg_path: Path
    png_path: Path
    width_px: int
    height_px: int
    verified_roles: tuple[str, ...]
    role_evidence: tuple[FontRoleEvidence, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FontRoleEvidence:
    role: str
    font: str
    path: Path
    characters: str
    glyphs_verified: bool
    backend_font: str
    backend_exact: bool


@dataclass(frozen=True, slots=True)
class _FontBinding:
    role: str
    font: str
    path: Path
    selected: str
    characters: str


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.messages.append(record.getMessage())


def render_preview(
    dxf_path: Path,
    svg_path: Path,
    png_path: Path,
    *,
    fonts: Mapping[str, ResolvedFont] | None = None,
) -> PreviewResult:
    """Audit and render one DXF without a GUI or partial published artifacts."""

    source = Path(dxf_path)
    svg_destination = Path(svg_path)
    png_destination = Path(png_path)
    resolved_source = source.resolve()
    resolved_svg = svg_destination.resolve()
    resolved_png = png_destination.resolve()
    if resolved_svg == resolved_png:
        raise PreviewError(f"preview output paths must differ: {svg_destination}")
    if resolved_source in {resolved_svg, resolved_png}:
        raise PreviewError(
            f"preview outputs must not overwrite source DXF {source}: "
            f"{svg_destination}, {png_destination}"
        )
    try:
        document = _reopen_and_audit(source)
        bindings = _resolve_role_fonts(document, fonts or {})
        _reject_missing_markers(document)
        with _FONT_RENDER_LOCK:
            cache_snapshot = _snapshot_font_caches(bindings)
            try:
                for binding in bindings:
                    _bind_exact_backend_font(binding)
                svg_destination.parent.mkdir(parents=True, exist_ok=True)
                png_destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="cad-preview-") as temporary:
                    temporary_path = Path(temporary)
                    staged_svg = temporary_path / "drawing.svg"
                    staged_png = temporary_path / "drawing.png"
                    width_px, height_px, backend_warnings = _render(
                        document, staged_svg, staged_png
                    )
                    if backend_warnings:
                        detail = "; ".join(backend_warnings)
                        raise PreviewError(
                            f"backend warning prevented verification: {detail}"
                        )
                    _validate_staged(staged_svg, staged_png, width_px, height_px)
                    role_evidence = tuple(
                        _post_render_role_evidence(binding) for binding in bindings
                    )
                    _publish_pair(
                        staged_svg,
                        svg_destination,
                        staged_png,
                        png_destination,
                    )
            finally:
                _restore_font_caches(cache_snapshot, bindings)
    except PreviewError as error:
        raise PreviewError(
            f"could not render DXF {source} to {svg_destination} and {png_destination}: {error}"
        ) from error
    except Exception as error:
        detail = str(error).strip() or type(error).__name__
        raise PreviewError(
            f"could not render DXF {source} to {svg_destination} and {png_destination}: "
            f"{type(error).__name__}: {detail}"
        ) from error

    verified_roles = tuple(item.role for item in role_evidence)
    mapping = ", ".join(
        f"{item.role}={item.font}@{item.path}" for item in role_evidence
    )
    evidence = (
        f"source {source} reopened and ezdxf audit passed",
        (
            f"all used roles mapped: {mapping}"
            if mapping
            else "drawing uses no text or dimension roles"
        ),
        "SVG and PNG rendered without backend warnings or replacement/missing glyph markers",
    )
    return PreviewResult(
        svg_destination,
        png_destination,
        width_px,
        height_px,
        verified_roles,
        role_evidence,
        evidence,
    )


def _reopen_and_audit(path: Path) -> ezdxf.document.Drawing:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
        document = ezdxf.readfile(path)
        auditor = document.audit()
    except Exception as error:
        detail = str(error).strip() or type(error).__name__
        raise PreviewError(f"could not reopen/audit DXF path {path}: {detail}") from error
    if auditor.has_errors:
        raise PreviewError(
            f"could not reopen/audit DXF path {path}: ezdxf audit reported errors"
        )
    return document


def _resolve_role_fonts(
    document: ezdxf.document.Drawing,
    fonts: Mapping[str, ResolvedFont],
) -> tuple[_FontBinding, ...]:
    roles: dict[str, dict[str, object]] = {}
    for entity in document.modelspace():
        entity_type = entity.dxftype()
        if entity_type == "DIMENSION":
            role = "DIM"
            dimstyle = document.dimstyles.get(entity.dxf.get("dimstyle", "Standard"))
            style_name = str(dimstyle.dxf.get("dimtxsty", "Standard"))
            text = _rendered_dimension_text(entity, dimstyle, document)
        elif entity_type in {"TEXT", "MTEXT"}:
            style_name = str(entity.dxf.get("style", "Standard"))
            role = "DIM" if style_name.casefold() == "dim" else "TEXT"
            text = (
                entity.plain_text()
                if entity_type == "MTEXT"
                else str(entity.dxf.get("text", ""))
            )
        else:
            continue
        try:
            font = str(document.styles.get(style_name).dxf.get("font", "")).strip()
        except Exception as error:
            raise PreviewError(
                f"used role {role} has no mapped text style {style_name}"
            ) from error
        if not font:
            raise PreviewError(f"used role {role} has no mapped font in style {style_name}")
        entry = roles.setdefault(role, {"fonts": set(), "characters": []})
        entry["fonts"].add(font)
        entry["characters"].extend(text)

    bindings: list[_FontBinding] = []
    for role, usage in sorted(roles.items()):
        resolved = fonts.get(role)
        if resolved is None:
            raise PreviewError(f"used role {role} lacks explicit ResolvedFont evidence")
        actual_fonts = tuple(sorted(usage["fonts"]))
        if resolved.primary is None:
            raise PreviewError(f"used role {role} has no resolved selected font")
        if resolved.missing_characters:
            raise PreviewError(
                f"used role {role} has unresolved missing glyphs: "
                f"{''.join(resolved.missing_characters)}"
            )
        font_path = resolved.primary_path
        if font_path is None:
            raise PreviewError(f"used role {role} resolved font has no concrete path")
        font_path = Path(font_path).resolve()
        try:
            with font_path.open("rb") as stream:
                if not stream.read(1):
                    raise OSError("font file is empty")
        except OSError as error:
            raise PreviewError(
                f"used role {role} font path is not readable: {font_path}: {error}"
            ) from error
        expected_style_font = font_path.name
        if actual_fonts != (expected_style_font,):
            raise PreviewError(
                f"used role {role} style fonts {actual_fonts} do not match "
                f"resolved concrete font {expected_style_font!r}"
            )
        characters = "".join(dict.fromkeys(usage["characters"]))
        bindings.append(
            _FontBinding(
                role,
                resolved.primary,
                font_path,
                expected_style_font,
                characters,
            )
        )
    paths_by_basename: dict[str, Path] = {}
    for binding in bindings:
        key = binding.selected.casefold()
        prior = paths_by_basename.setdefault(key, binding.path)
        if prior != binding.path:
            raise PreviewError(
                "same font basename resolves to different paths: "
                f"{binding.selected}: {prior} and {binding.path}"
            )
    for binding in bindings:
        _verify_glyphs(binding.path, binding.characters, binding.role)
    return tuple(bindings)


def _affected_cache_keys(cache: Mapping[object, object], basenames: set[str]) -> tuple[object, ...]:
    return tuple(
        key
        for key in cache
        if Path(str(key)).name.casefold() in basenames
    )


def _font_caches() -> tuple[dict, dict, dict]:
    manager = ezdxf_fonts.font_manager
    return (
        manager._font_cache._cache,
        manager._loaded_ttf_fonts,
        ezdxf_fonts.TrueTypeFont._glyph_caches,
    )


def _snapshot_font_caches(
    bindings: tuple[_FontBinding, ...],
) -> tuple[dict[object, object], ...]:
    basenames = {binding.selected.casefold() for binding in bindings}
    return tuple(
        {key: cache[key] for key in _affected_cache_keys(cache, basenames)}
        for cache in _font_caches()
    )


def _restore_font_caches(
    snapshots: tuple[dict[object, object], ...],
    bindings: tuple[_FontBinding, ...],
) -> None:
    basenames = {binding.selected.casefold() for binding in bindings}
    for cache, snapshot in zip(_font_caches(), snapshots, strict=True):
        for key in _affected_cache_keys(cache, basenames):
            cache.pop(key, None)
        cache.update(snapshot)


def _bind_exact_backend_font(binding: _FontBinding) -> None:
    selected = binding.selected
    collector = _WarningCollector()
    root_logger = logging.getLogger()
    root_logger.addHandler(collector)
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            face = get_ttf_font_face(binding.path)
            manager = ezdxf_fonts.font_manager
            manager._font_cache.add_entry(binding.path, face)
            for cached_name in _affected_cache_keys(
                manager._loaded_ttf_fonts, {selected.casefold()}
            ):
                manager._loaded_ttf_fonts.pop(cached_name, None)
            for cached_name in _affected_cache_keys(
                ezdxf_fonts.TrueTypeFont._glyph_caches, {selected.casefold()}
            ):
                ezdxf_fonts.TrueTypeFont._glyph_caches.pop(cached_name, None)
            manager.get_ttf_font(selected)
            ezdxf_fonts.make_font(selected, 1.0)
            _verify_bound_backend_font(binding)
    except Exception as error:
        raise PreviewError(
            f"used role {binding.role} backend could not resolve exact font "
            f"{selected}: {error}"
        ) from error
    finally:
        root_logger.removeHandler(collector)
    warning_messages = tuple(str(item.message) for item in captured) + tuple(
        collector.messages
    )
    if warning_messages:
        raise PreviewError(
            f"used role {binding.role} font backend warning prevented verification: "
            f"{'; '.join(warning_messages)}"
        )


def _verify_bound_backend_font(binding: _FontBinding) -> str:
    manager = ezdxf_fonts.font_manager
    try:
        backend_font = manager._loaded_ttf_fonts[binding.selected]
        backend_path = Path(backend_font.reader.file.name).resolve()
        glyph_cache = ezdxf_fonts.TrueTypeFont._glyph_caches[
            Path(binding.selected).name.casefold()
        ]
    except (KeyError, AttributeError) as error:
        raise PreviewError(
            f"used role {binding.role} renderer lost exact binding for {binding.selected}"
        ) from error
    if backend_path != binding.path:
        raise PreviewError(
            f"used role {binding.role} backend substituted {backend_path} "
            f"for {binding.path}"
        )
    if getattr(glyph_cache, "font", None) is not backend_font:
        raise PreviewError(
            f"used role {binding.role} renderer retained a stale fallback "
            f"for {binding.selected}"
        )
    renderer_cmap = getattr(glyph_cache, "cmap", {})
    missing = tuple(
        character
        for character in dict.fromkeys(binding.characters)
        if not character.isspace()
        and renderer_cmap.get(ord(character)) in {None, ".notdef"}
    )
    if missing:
        raise PreviewError(
            f"used role {binding.role} exact renderer is missing used glyphs: "
            f"{''.join(missing)}"
        )
    return str(backend_path)


def _post_render_role_evidence(binding: _FontBinding) -> FontRoleEvidence:
    return FontRoleEvidence(
        binding.role,
        binding.font,
        binding.path,
        binding.characters,
        True,
        _verify_bound_backend_font(binding),
        True,
    )


def _rendered_dimension_text(
    entity: object,
    dimstyle: object,
    document: ezdxf.document.Drawing | None = None,
) -> str:
    """Return the text the rendered DIMENSION block actually contains.

    DIMENSION.dxf.text usually contains only ``<>`` while prefixes, suffixes,
    measurement symbols, and tolerance rows live in the anonymous dimension
    block.  Virtual decomposition exposes that final representation after the
    source file has been reopened.
    """

    rendered = "".join(_virtual_text_content(entity))
    if not rendered and document is not None:
        try:
            block = document.blocks.get(str(entity.dxf.get("geometry", "")))
        except Exception:
            block = ()
        rendered = "".join(
            text
            for child in block
            for text in _virtual_text_content(child)
            if text
        )
    if rendered:
        return rendered
    return _fallback_dimension_text(entity, dimstyle)


def _virtual_text_content(entity: object, depth: int = 0) -> tuple[str, ...]:
    if depth > 8:
        return ()
    entity_type = getattr(entity, "dxftype", lambda: "")()
    if entity_type == "MTEXT":
        visible: list[str] = []
        for token in MTextParser(str(entity.dxf.get("text", ""))):
            if isinstance(token.data, str):
                visible.append(token.data)
            elif isinstance(token.data, tuple):
                visible.extend(str(part) for part in token.data[:2])
        return ("".join(visible),)
    if entity_type == "TEXT":
        return (str(entity.dxf.get("text", "")),)
    virtual_entities = getattr(entity, "virtual_entities", None)
    if not callable(virtual_entities):
        return ()
    try:
        children = tuple(virtual_entities())
    except Exception:
        return ()
    return tuple(
        text
        for child in children
        for text in _virtual_text_content(child, depth + 1)
        if text
    )


def _fallback_dimension_text(entity: object, dimstyle: object) -> str:
    measurement = float(entity.get_measurement())
    decimals = max(0, int(dimstyle.dxf.get("dimdec", 2)))
    measured_text = f"{measurement:.{decimals}f}"
    explicit = str(entity.dxf.get("text", ""))
    base = measured_text if not explicit or explicit == "<>" else explicit.replace("<>", measured_text)
    dimpost = str(dimstyle.dxf.get("dimpost", ""))
    if dimpost:
        base = dimpost.replace("<>", base) if "<>" in dimpost else f"{base}{dimpost}"
    if bool(dimstyle.dxf.get("dimtol", 0)):
        upper = float(dimstyle.dxf.get("dimtp", 0.0))
        lower = float(dimstyle.dxf.get("dimtm", 0.0))
        tolerance_decimals = max(0, int(dimstyle.dxf.get("dimtdec", decimals)))
        if upper == lower:
            base += f"±{upper:.{tolerance_decimals}f}"
        else:
            base += (
                f"+{upper:.{tolerance_decimals}f}"
                f"-{lower:.{tolerance_decimals}f}"
            )
    elif bool(dimstyle.dxf.get("dimlim", 0)):
        upper = measurement + float(dimstyle.dxf.get("dimtp", 0.0))
        lower = measurement - float(dimstyle.dxf.get("dimtm", 0.0))
        base += f"{upper:.{decimals}f}{lower:.{decimals}f}"
    return base


def _verify_glyphs(path: Path, characters: str, role: str) -> None:
    if path.suffix.casefold() == ".shx":
        raise PreviewError(
            f"used role {role} SHX glyph coverage cannot be proven exactly: {path}"
        )
    try:
        with TTFont(
            path,
            lazy=True,
            fontNumber=0 if path.suffix.casefold() == ".ttc" else -1,
        ) as font:
            cmap = {
                int(codepoint)
                for table in font["cmap"].tables
                if table.isUnicode()
                for codepoint in table.cmap
            }
    except Exception as error:
        raise PreviewError(
            f"used role {role} font glyph map is unreadable: {path}: {error}"
        ) from error
    missing = tuple(
        character
        for character in characters
        if not character.isspace() and ord(character) not in cmap
    )
    if missing:
        raise PreviewError(
            f"used role {role} font {path} is missing used glyphs: {''.join(missing)}"
        )


def _reject_missing_markers(document: ezdxf.document.Drawing) -> None:
    for entity in document.modelspace():
        entity_type = entity.dxftype()
        if entity_type == "DIMENSION":
            dimstyle = document.dimstyles.get(
                entity.dxf.get("dimstyle", "Standard")
            )
            text = _rendered_dimension_text(entity, dimstyle, document)
        elif entity_type == "MTEXT":
            text = entity.plain_text()
        elif entity_type == "TEXT":
            text = entity.dxf.get("text", "")
        else:
            continue
        if any(marker in text for marker in _MISSING_MARKERS):
            raise PreviewError(
                f"replacement or missing glyph marker found in DXF entity {entity.dxf.handle}"
            )


def _render(
    document: ezdxf.document.Drawing,
    svg_path: Path,
    png_path: Path,
) -> tuple[int, int, tuple[str, ...]]:
    extents = bbox.extents(document.modelspace())
    if not extents.has_data:
        raise PreviewError("DXF has no renderable modelspace extents")
    extent_values = (
        float(extents.extmin.x),
        float(extents.extmin.y),
        float(extents.extmax.x),
        float(extents.extmax.y),
    )
    if not all(isfinite(value) for value in extent_values):
        raise PreviewError("DXF model extents must be finite")
    raw_width = float(extents.size.x)
    raw_height = float(extents.size.y)
    span = max(raw_width, raw_height)
    shortest = max(min(raw_width, raw_height), 1.0)
    if span > _MAX_MODEL_SPAN:
        raise PreviewError(
            f"DXF model span {span:g} exceeds {_MAX_MODEL_SPAN:g} rendering limit"
        )
    if span / shortest > _MAX_ASPECT_RATIO:
        raise PreviewError(
            f"DXF model aspect ratio {span / shortest:g} exceeds "
            f"{_MAX_ASPECT_RATIO:g} rendering limit"
        )
    width = max(raw_width, 1.0)
    height = max(raw_height, 1.0)
    width_px, height_px = _pixel_size(width, height)
    page = layout.Page(
        width_px,
        height_px,
        layout.Units.px,
        layout.Margins(0, 0, 0, 0),
    )
    collector = _WarningCollector()
    root_logger = logging.getLogger()
    root_logger.addHandler(collector)
    caught: list[warnings.WarningMessage]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            print_configuration = Configuration(color_policy=ColorPolicy.BLACK)
            context = RenderContext(document)
            svg_backend = svg.SVGBackend()
            DeterministicFrontend(
                context,
                svg_backend,
                config=print_configuration,
            ).draw_layout(document.modelspace())
            svg_text = svg_backend.get_string(
                page,
                settings=layout.Settings(
                    output_coordinate_space=max(width_px, height_px)
                ),
            )
            svg_path.write_text(
                _normalize_svg(svg_text, width_px, height_px),
                encoding="utf-8",
                newline="\n",
            )

            matplotlib.rcParams["svg.hashsalt"] = "cad-skill-preview"
            figure = plt.figure(
                figsize=(width_px / _DPI, height_px / _DPI),
                dpi=_DPI,
                frameon=False,
            )
            try:
                axes = figure.add_axes((0, 0, 1, 1))
                axes.set_axis_off()
                DeterministicFrontend(
                    RenderContext(document),
                    MatplotlibBackend(axes, adjust_figure=False),
                    config=print_configuration,
                ).draw_layout(document.modelspace())
                axes.autoscale(enable=True)
                figure.savefig(
                    png_path,
                    dpi=_DPI,
                    format="png",
                    metadata={"Software": "cad-skill-preview"},
                    facecolor="white",
                )
            finally:
                plt.close(figure)
    finally:
        root_logger.removeHandler(collector)
    messages = [str(item.message) for item in caught]
    messages.extend(collector.messages)
    return width_px, height_px, tuple(sorted(set(messages)))


def _pixel_size(width: float, height: float) -> tuple[int, int]:
    longest = max(width, height)
    scale = min(12.0, _MAX_PIXELS / longest)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _normalize_svg(value: str, width_px: int, height_px: int) -> str:
    value = re.sub(r"<dc:date>.*?</dc:date>", "", value, flags=re.DOTALL)
    value = value.replace("\r\n", "\n")
    return re.sub(
        r'(<svg\b[^>]*\bwidth=")[^"]+("[^>]*\bheight=")[^"]+("[^>]*>)',
        rf"\g<1>{width_px}px\g<2>{height_px}px\g<3>",
        value,
        count=1,
    )


def _validate_staged(
    svg_path: Path,
    png_path: Path,
    width_px: int,
    height_px: int,
) -> None:
    if not svg_path.is_file() or svg_path.stat().st_size == 0:
        raise PreviewError(f"renderer produced an empty SVG: {svg_path}")
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise PreviewError(f"renderer produced an empty PNG: {png_path}")
    with Image.open(png_path) as image:
        if image.size != (width_px, height_px):
            raise PreviewError(
                f"renderer PNG dimensions {image.size} differ from expected {(width_px, height_px)}"
            )
        if image.width > _MAX_PIXELS or image.height > _MAX_PIXELS:
            raise PreviewError(f"renderer PNG exceeds {_MAX_PIXELS}px bound")
        image.verify()
    svg_text = svg_path.read_text(encoding="utf-8")
    if any(marker in svg_text for marker in _MISSING_MARKERS):
        raise PreviewError("replacement or missing glyph marker found in SVG output")
    _validate_svg(svg_path, width_px, height_px)


def _publish_pair(
    staged_svg: Path,
    svg_destination: Path,
    staged_png: Path,
    png_destination: Path,
) -> None:
    destinations = (svg_destination, png_destination)
    staged = (staged_svg, staged_png)
    partials: list[Path] = []
    backups: list[Path | None] = []
    existed = tuple(destination.exists() for destination in destinations)
    try:
        for source, destination in zip(staged, destinations, strict=True):
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".partial",
                dir=destination.parent,
            )
            os.close(descriptor)
            partial = Path(name)
            if partial.resolve() in {item.resolve() for item in destinations}:
                raise PreviewError(f"temporary preview path aliases destination: {partial}")
            shutil.copy2(source, partial)
            partials.append(partial)
        _validate_staged(partials[0], partials[1], *_png_size(partials[1]))

        for destination, had_file in zip(destinations, existed, strict=True):
            if not had_file:
                backups.append(None)
                continue
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".backup",
                dir=destination.parent,
            )
            os.close(descriptor)
            backup = Path(name)
            shutil.copy2(destination, backup)
            backups.append(backup)

        os.replace(partials[0], svg_destination)
        os.replace(partials[1], png_destination)
        _validate_published(svg_destination, png_destination)
    except Exception:
        for destination, backup, had_file in zip(
            destinations, backups + [None] * (2 - len(backups)), existed, strict=True
        ):
            if backup is not None and backup.exists():
                os.replace(backup, destination)
            elif not had_file:
                destination.unlink(missing_ok=True)
        raise
    finally:
        for path in (*partials, *(backup for backup in backups if backup is not None)):
            path.unlink(missing_ok=True)


def _png_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _validate_svg(path: Path, width_px: int, height_px: int) -> None:
    try:
        root = ElementTree.parse(path).getroot()
        width = float(root.attrib["width"].removesuffix("px"))
        height = float(root.attrib["height"].removesuffix("px"))
        view_box = tuple(float(value) for value in root.attrib["viewBox"].split())
    except Exception as error:
        raise PreviewError(f"renderer produced invalid SVG/XML {path}: {error}") from error
    if len(view_box) != 4:
        raise PreviewError(f"renderer SVG has invalid viewBox: {path}")
    if (round(width), round(height)) != (width_px, height_px):
        raise PreviewError(
            f"renderer SVG dimensions {(width, height)} differ from "
            f"expected {(width_px, height_px)}"
        )
    if any(not isfinite(value) for value in (*view_box, width, height)):
        raise PreviewError(f"renderer SVG dimensions must be finite: {path}")
    if max(width, height, view_box[2], view_box[3]) > _MAX_PIXELS:
        raise PreviewError(f"renderer SVG exceeds {_MAX_PIXELS}px bound: {path}")


def _validate_published(svg_path: Path, png_path: Path) -> None:
    if png_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise PreviewError(f"published PNG magic is invalid: {png_path}")
    width_px, height_px = _png_size(png_path)
    _validate_staged(svg_path, png_path, width_px, height_px)
