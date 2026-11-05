from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import warnings
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import ezdxf
import matplotlib
import pytest
from ezdxf.addons.drawing import Frontend
from ezdxf.fonts import fonts as drawing_fonts
from ezdxf.render import hatching
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from matplotlib.font_manager import findfont
from PIL import Image

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.rendering import preview as preview_module
from cad_skill_core.rendering.preview import (
    PreviewError,
    _rendered_dimension_text,
    render_preview,
)


def _font_evidence(*, requires_render_verification: bool = False):
    path = Path(findfont("DejaVu Sans")).resolve()
    resolved = ResolvedFont(
        primary=path.name,
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=requires_render_verification,
        resolved_runs=(
            ResolvedFontRun(0, 8, "A ± 0.1°", "latin", path.name),
        ),
        primary_path=path,
    )
    return {"TEXT": resolved, "DIM": resolved}


def _renderable_dxf(path: Path, text: str = "A ± 0.1°") -> Path:
    font = _font_evidence()["TEXT"]
    document = ezdxf.new("R2018")
    document.styles.add("normal", font=font.primary)
    document.modelspace().add_line((0, 0), (40, 20))
    document.modelspace().add_text(
        text,
        dxfattribs={"height": 3.5, "style": "normal"},
    ).set_placement((5, 5))
    document.saveas(path)
    return path


def _pattern_hatch_dxf(path: Path) -> Path:
    boundary = (
        (0.0, 0.0),
        (80.0, 0.0),
        (80.0, 45.0),
        (55.0, 45.0),
        (55.0, 60.0),
        (20.0, 60.0),
        (20.0, 45.0),
        (0.0, 45.0),
    )
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(boundary, close=True)
    hatch = modelspace.add_hatch()
    hatch.set_pattern_fill("ANSI31", scale=2.0)
    hatch.paths.add_polyline_path(boundary, is_closed=True)
    document.saveas(path)
    return path


def test_pattern_hatch_preview_is_identical_across_fresh_processes_and_rng_seeds(
    tmp_path,
):
    source = _pattern_hatch_dxf(tmp_path / "pattern-hatch.dxf")
    original_dxf = source.read_bytes()
    child_script = """
import json
import random
import sys
from pathlib import Path
from cad_skill_core.rendering.preview import render_preview

source, svg, png, report = map(Path, sys.argv[1:5])
seed = int(sys.argv[5])
random.seed(seed)
before = random.getstate()
render_preview(source, svg, png)
Path(report).write_text(
    json.dumps({"rng_unchanged": random.getstate() == before}),
    encoding="utf-8",
)
"""
    outputs = []
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    for index, seed in enumerate((11, 29, 47, 83)):
        svg = tmp_path / f"fresh-{index}.svg"
        png = tmp_path / f"fresh-{index}.png"
        report = tmp_path / f"fresh-{index}.json"
        subprocess.run(
            [
                sys.executable,
                "-c",
                child_script,
                str(source),
                str(svg),
                str(png),
                str(report),
                str(seed),
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=child_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        with Image.open(png) as image:
            pixels = image.convert("RGBA").tobytes()
        outputs.append(
            (
                hashlib.sha256(svg.read_bytes()).hexdigest(),
                hashlib.sha256(png.read_bytes()).hexdigest(),
                hashlib.sha256(pixels).hexdigest(),
                json.loads(report.read_text(encoding="utf-8")),
            )
        )

    assert len({png_hash for _, png_hash, _, _ in outputs}) == 1
    assert len({pixel_hash for _, _, pixel_hash, _ in outputs}) == 1
    assert len({svg_hash for svg_hash, _, _, _ in outputs}) == 1
    assert all(report["rng_unchanged"] for _, _, _, report in outputs)
    assert source.read_bytes() == original_dxf


def test_hatch_jiggle_factors_are_stable_nonzero_and_in_upstream_safety_range(
    tmp_path,
):
    source = _pattern_hatch_dxf(tmp_path / "factor-hatch.dxf")
    document = ezdxf.readfile(source)
    hatch = next(entity for entity in document.modelspace() if entity.dxftype() == "HATCH")
    paths = hatching.hatch_boundary_paths(hatch)

    first = preview_module._deterministic_hatch_factors(hatch, paths)
    second = preview_module._deterministic_hatch_factors(hatch, paths)

    assert first == second
    assert all(0.0003 <= factor <= 0.0010 for factor in first)


def test_concurrent_hatch_previews_are_stable_without_mutating_random_state(
    tmp_path,
):
    source = _pattern_hatch_dxf(tmp_path / "concurrent-hatch.dxf")
    original_random_state = random.getstate()
    random.seed(101)
    caller_state = random.getstate()
    try:
        def render(index: int) -> tuple[str, str]:
            svg = tmp_path / f"thread-{index}.svg"
            png = tmp_path / f"thread-{index}.png"
            render_preview(source, svg, png)
            return (
                hashlib.sha256(svg.read_bytes()).hexdigest(),
                hashlib.sha256(png.read_bytes()).hexdigest(),
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            hashes = tuple(executor.map(render, range(4)))

        assert len(set(hashes)) == 1
        assert random.getstate() == caller_state
    finally:
        random.setstate(original_random_state)


def test_hatch_rendering_fails_closed_for_an_unsupported_ezdxf_minor(
    tmp_path,
    monkeypatch,
):
    source = _pattern_hatch_dxf(tmp_path / "unsupported-hatch.dxf")
    svg = tmp_path / "unsupported.svg"
    png = tmp_path / "unsupported.png"
    svg.write_bytes(b"existing svg")
    png.write_bytes(b"existing png")
    monkeypatch.setattr(ezdxf, "__version__", "1.5.0")

    with pytest.raises(PreviewError, match="unsupported ezdxf hatch rendering API"):
        render_preview(source, svg, png)

    assert svg.read_bytes() == b"existing svg"
    assert png.read_bytes() == b"existing png"


def test_hatch_timeout_preserves_existing_preview_pair(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cad_skill_core.rendering import deterministic_hatch

    source = _pattern_hatch_dxf(tmp_path / "timeout-hatch.dxf")
    original_dxf = source.read_bytes()
    svg, png = tmp_path / "drawing.svg", tmp_path / "drawing.png"
    svg.write_bytes(b"existing svg")
    png.write_bytes(b"existing png")
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 1e9

    # Replace only our adapter's clock binding, never global time or upstream code.
    monkeypatch.setattr(deterministic_hatch, "time", SimpleNamespace(perf_counter=clock))
    with pytest.raises(PreviewError, match="HatchTimeoutError.*hatching timeout"):
        render_preview(source, svg, png)

    assert calls >= 2
    assert source.read_bytes() == original_dxf
    assert svg.read_bytes() == b"existing svg"
    assert png.read_bytes() == b"existing png"


def _dimension_dxf(
    path: Path,
    font_path: Path,
    *,
    dimpost: str,
    tolerance: bool = False,
) -> Path:
    document = ezdxf.new("R2018")
    document.styles.add("dimfont", font=font_path.name)
    attributes = {"dimtxsty": "dimfont", "dimpost": dimpost}
    if tolerance:
        attributes.update({"dimtol": 1, "dimtp": 0.1, "dimtm": 0.2, "dimdec": 2})
    document.dimstyles.add("TEST-DIM", dxfattribs=attributes)
    dimension = document.modelspace().add_linear_dim(
        base=(0, 10), p1=(0, 0), p2=(20, 0), dimstyle="TEST-DIM"
    )
    dimension.render()
    document.saveas(path)
    return path


def _single_font_evidence(path: Path) -> dict[str, ResolvedFont]:
    return {
        "DIM": ResolvedFont(
            primary=path.name,
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(ResolvedFontRun(0, 1, "0", "latin", path.name),),
            primary_path=path,
        )
    }


def _outline_glyph(points: tuple[tuple[int, int], ...]):
    pen = TTGlyphPen(None)
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()
    return pen.glyph()


def _write_exact_backend_test_font(
    path: Path,
    family: str,
    character: str,
    *,
    character_points: tuple[tuple[int, int], ...] = (
        (50, 0),
        (550, 0),
        (300, 700),
    ),
) -> None:
    glyph_order = [".notdef", "A", "p", "x", f"uni{ord(character):04X}"]
    font = FontBuilder(1000, isTTF=True)
    font.setupGlyphOrder(glyph_order)
    font.setupCharacterMap(
        {
            ord("A"): "A",
            ord("p"): "p",
            ord("x"): "x",
            ord(character): f"uni{ord(character):04X}",
        }
    )
    font.setupGlyf(
        {
            ".notdef": _outline_glyph(((50, 0), (550, 0), (550, 700), (50, 700))),
            "A": _outline_glyph(((50, 0), (300, 700), (550, 0))),
            "p": _outline_glyph(((50, -200), (50, 500), (500, 500), (500, 0))),
            "x": _outline_glyph(((50, 0), (300, 500), (550, 0))),
            f"uni{ord(character):04X}": _outline_glyph(character_points),
        }
    )
    font.setupHorizontalMetrics({name: (600, 0) for name in glyph_order})
    font.setupHorizontalHeader(ascent=800, descent=-200)
    font.setupNameTable({"familyName": family, "styleName": "Regular"})
    font.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    font.setupPost()
    font.setupMaxp()
    font.save(path)


def _resolved_exact_font(path: Path, family: str, text: str) -> ResolvedFont:
    return ResolvedFont(
        primary=family,
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(ResolvedFontRun(0, len(text), text, "test", family),),
        primary_path=path,
    )


def _text_dxf_with_font(path: Path, font_name: str, text: str) -> Path:
    document = ezdxf.new("R2018")
    document.styles.add("normal", font=font_name)
    document.modelspace().add_text(
        text,
        dxfattribs={"height": 5.0, "style": "normal"},
    ).set_placement((0, 0))
    document.saveas(path)
    return path


def _identified_text_dxf_with_font(
    path: Path, font_name: str, text: str, identifier: str
) -> Path:
    document = ezdxf.new("R2018")
    document.header["$PROJECTNAME"] = identifier
    document.styles.add("normal", font=font_name)
    document.modelspace().add_text(
        text,
        dxfattribs={"height": 5.0, "style": "normal"},
    ).set_placement((0, 0))
    document.saveas(path)
    return path


def _font_global_state() -> tuple[dict, dict, dict]:
    manager = drawing_fonts.font_manager
    return (
        dict(manager._font_cache._cache),
        dict(manager._loaded_ttf_fonts),
        dict(drawing_fonts.TrueTypeFont._glyph_caches),
    )


def _restore_font_global_state(state: tuple[dict, dict, dict]) -> None:
    manager = drawing_fonts.font_manager
    for target, saved in zip(
        (
            manager._font_cache._cache,
            manager._loaded_ttf_fonts,
            drawing_fonts.TrueTypeFont._glyph_caches,
        ),
        state,
        strict=True,
    ):
        target.clear()
        target.update(saved)


def _font_state_for(basename: str) -> tuple[tuple[tuple[str, int], ...], ...]:
    wanted = basename.casefold()

    def matching(cache: dict) -> tuple[tuple[str, int], ...]:
        return tuple(
            sorted(
                (str(key), id(value))
                for key, value in cache.items()
                if Path(str(key)).name.casefold() == wanted
            )
        )

    manager = drawing_fonts.font_manager
    return (
        matching(manager._font_cache._cache),
        matching(manager._loaded_ttf_fonts),
        matching(drawing_fonts.TrueTypeFont._glyph_caches),
    )


def test_render_preview_creates_bounded_deterministic_outputs_and_role_evidence(tmp_path):
    source = _renderable_dxf(tmp_path / "source.dxf")

    fonts = _font_evidence()
    first = render_preview(
        source, tmp_path / "first.svg", tmp_path / "first.png", fonts=fonts
    )
    second = render_preview(
        source, tmp_path / "second.svg", tmp_path / "second.png", fonts=fonts
    )

    assert 0 < first.width_px <= 2048
    assert 0 < first.height_px <= 2048
    assert first.svg_path.stat().st_size > 0
    assert first.png_path.stat().st_size > 0
    assert first.verified_roles == ("TEXT",)
    assert first.role_evidence[0].font == fonts["TEXT"].primary
    assert first.role_evidence[0].path == fonts["TEXT"].primary_path
    assert first.role_evidence[0].glyphs_verified is True
    assert first.role_evidence[0].backend_exact is True
    assert any("ezdxf audit passed" in item for item in first.evidence)
    assert any("without backend warnings" in item for item in first.evidence)
    assert first.svg_path.read_bytes() == second.svg_path.read_bytes()
    assert first.png_path.read_bytes() == second.png_path.read_bytes()


def test_preview_uses_exact_font_then_restores_preexisting_stale_fallback(tmp_path):
    selected_path = tmp_path / "exact-backend-glyph-test.ttf"
    selected_family = "Exact Backend Glyph Test"
    character = "法"
    _write_exact_backend_test_font(selected_path, selected_family, character)

    cache_key = selected_path.name.casefold()
    drawing_fonts.TrueTypeFont._glyph_caches.pop(cache_key, None)
    stale = drawing_fonts.make_font(selected_path.name, 1.0)
    assert stale.glyph_cache.font_name != selected_family

    document = ezdxf.new("R2018")
    document.styles.add("exact", font=selected_path.name)
    document.modelspace().add_text(
        character,
        dxfattribs={"height": 5.0, "style": "exact"},
    ).set_placement((0, 0))
    source = tmp_path / "stale-cache.dxf"
    document.saveas(source)
    resolved = ResolvedFont(
        primary=selected_family,
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(
            ResolvedFontRun(0, 1, character, "cjk", selected_family),
        ),
        primary_path=selected_path,
    )

    result = render_preview(
        source,
        tmp_path / "stale-cache.svg",
        tmp_path / "stale-cache.png",
        fonts={"TEXT": resolved},
    )

    assert result.role_evidence[0].backend_exact is True
    assert Path(result.role_evidence[0].backend_font) == selected_path.resolve()
    restored = drawing_fonts.make_font(selected_path.name, 1.0)
    assert restored.glyph_cache is stale.glyph_cache


def test_preview_rejects_same_basename_bound_to_different_paths_before_mutation(
    tmp_path,
):
    basename = "conflicting-project-font.ttf"
    first_path = tmp_path / "first" / basename
    second_path = tmp_path / "second" / basename
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    _write_exact_backend_test_font(first_path, "Conflict First", "法")
    _write_exact_backend_test_font(second_path, "Conflict Second", "法")

    document = ezdxf.new("R2018")
    document.styles.add("normal", font=basename)
    document.styles.add("DIM", font=basename)
    document.modelspace().add_text(
        "法", dxfattribs={"height": 5.0, "style": "normal"}
    ).set_placement((0, 0))
    document.modelspace().add_text(
        "法", dxfattribs={"height": 5.0, "style": "DIM"}
    ).set_placement((10, 0))
    source = tmp_path / "conflict.dxf"
    document.saveas(source)
    svg = tmp_path / "conflict.svg"
    png = tmp_path / "conflict.png"
    svg.write_bytes(b"existing svg")
    png.write_bytes(b"existing png")
    before = _font_state_for(basename)

    with pytest.raises(PreviewError, match="same font basename.*different paths"):
        render_preview(
            source,
            svg,
            png,
            fonts={
                "TEXT": _resolved_exact_font(first_path, "Conflict First", "法"),
                "DIM": _resolved_exact_font(second_path, "Conflict Second", "法"),
            },
        )

    assert svg.read_bytes() == b"existing svg"
    assert png.read_bytes() == b"existing png"
    assert _font_state_for(basename) == before


def test_preview_restores_affected_global_font_caches_after_success(tmp_path):
    basename = "success-restoration-font.ttf"
    font_path = tmp_path / basename
    _write_exact_backend_test_font(font_path, "Success Restoration", "法")
    source = _text_dxf_with_font(tmp_path / "success.dxf", basename, "法")
    before = _font_state_for(basename)

    render_preview(
        source,
        tmp_path / "success.svg",
        tmp_path / "success.png",
        fonts={
            "TEXT": _resolved_exact_font(font_path, "Success Restoration", "法")
        },
    )

    assert _font_state_for(basename) == before


def test_preview_restores_affected_global_font_caches_after_render_failure(
    tmp_path, monkeypatch
):
    basename = "failure-restoration-font.ttf"
    font_path = tmp_path / basename
    _write_exact_backend_test_font(font_path, "Failure Restoration", "法")
    source = _text_dxf_with_font(tmp_path / "failure.dxf", basename, "法")
    svg = tmp_path / "failure.svg"
    png = tmp_path / "failure.png"
    svg.write_bytes(b"existing svg")
    png.write_bytes(b"existing png")
    before = _font_state_for(basename)

    def fail_after_font_binding(*args, **kwargs):
        raise PreviewError("forced render failure after font binding")

    monkeypatch.setattr(preview_module, "_render", fail_after_font_binding)

    with pytest.raises(PreviewError, match="forced render failure"):
        render_preview(
            source,
            svg,
            png,
            fonts={
                "TEXT": _resolved_exact_font(
                    font_path, "Failure Restoration", "法"
                )
            },
        )

    assert svg.read_bytes() == b"existing svg"
    assert png.read_bytes() == b"existing png"
    assert _font_state_for(basename) == before


def test_concurrent_previews_with_same_basename_cannot_cross_contaminate(
    tmp_path, monkeypatch
):
    basename = "concurrent-project-font.ttf"
    first_path = tmp_path / "first" / basename
    second_path = tmp_path / "second" / basename
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    _write_exact_backend_test_font(
        first_path,
        "Concurrent First",
        "法",
        character_points=((50, 0), (300, 700), (550, 0)),
    )
    _write_exact_backend_test_font(
        second_path,
        "Concurrent Second",
        "法",
        character_points=((50, 0), (550, 0), (550, 700), (50, 700)),
    )
    first_source = _identified_text_dxf_with_font(
        tmp_path / "first.dxf", basename, "法", "first"
    )
    second_source = _identified_text_dxf_with_font(
        tmp_path / "second.dxf", basename, "法", "second"
    )
    first_fonts = {
        "TEXT": _resolved_exact_font(first_path, "Concurrent First", "法")
    }
    second_fonts = {
        "TEXT": _resolved_exact_font(second_path, "Concurrent Second", "法")
    }
    original_state = _font_global_state()
    try:
        original_render = preview_module._render
        first_render_entered = threading.Event()
        gate = threading.Barrier(2)
        observed_paths: dict[str, Path] = {}

        def observe_bound_font(document, *args, **kwargs):
            identifier = str(document.header.get("$PROJECTNAME", ""))
            if identifier == "first":
                first_render_entered.set()
            try:
                gate.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            backend_font = drawing_fonts.font_manager.get_ttf_font(basename)
            observed_paths[identifier] = Path(backend_font.reader.file.name).resolve()
            return original_render(document, *args, **kwargs)

        monkeypatch.setattr(preview_module, "_render", observe_bound_font)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                render_preview,
                first_source,
                tmp_path / "concurrent-first.svg",
                tmp_path / "concurrent-first.png",
                fonts=first_fonts,
            )
            assert first_render_entered.wait(timeout=5.0)
            second_future = executor.submit(
                render_preview,
                second_source,
                tmp_path / "concurrent-second.svg",
                tmp_path / "concurrent-second.png",
                fonts=second_fonts,
            )
            first_result = first_future.result(timeout=15.0)
            second_result = second_future.result(timeout=15.0)

        assert first_result.role_evidence[0].backend_exact is True
        assert second_result.role_evidence[0].backend_exact is True
        assert observed_paths == {
            "first": first_path.resolve(),
            "second": second_path.resolve(),
        }
        assert _font_global_state() == original_state
    finally:
        _restore_font_global_state(original_state)


def test_render_preview_reports_source_path_and_never_publishes_partial_outputs(tmp_path):
    missing = tmp_path / "missing source.dxf"
    svg = tmp_path / "preview.svg"
    png = tmp_path / "preview.png"

    with pytest.raises(PreviewError, match=r"missing source\.dxf") as error:
        render_preview(missing, svg, png, fonts=_font_evidence())

    assert str(svg) in str(error.value)
    assert str(png) in str(error.value)
    assert not svg.exists()
    assert not png.exists()


def test_render_preview_rejects_replacement_markers_without_publishing(tmp_path):
    source = _renderable_dxf(tmp_path / "replacement.dxf", "bad � glyph")
    svg = tmp_path / "preview.svg"
    png = tmp_path / "preview.png"

    with pytest.raises(PreviewError, match="replacement or missing glyph marker"):
        render_preview(source, svg, png, fonts=_font_evidence())

    assert not svg.exists()
    assert not png.exists()


def test_render_preview_does_not_turn_backend_warnings_into_verified_roles(
    tmp_path, monkeypatch
):
    source = _renderable_dxf(tmp_path / "warning.dxf")
    svg = tmp_path / "preview.svg"
    png = tmp_path / "preview.png"
    original = Frontend.draw_layout

    def warn_then_render(self, *args, **kwargs):
        warnings.warn("backend substituted a glyph", UserWarning)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Frontend, "draw_layout", warn_then_render)

    with pytest.raises(PreviewError, match="backend warning"):
        render_preview(source, svg, png, fonts=_font_evidence())

    assert not svg.exists()
    assert not png.exists()


def test_font_backend_warning_prevents_exact_role_verification(tmp_path, monkeypatch):
    source = _renderable_dxf(tmp_path / "source.dxf")
    svg = tmp_path / "preview.svg"
    png = tmp_path / "preview.png"
    from ezdxf.fonts import fonts as ezdxf_fonts

    original = ezdxf_fonts.font_manager.get_ttf_font

    def warn_then_resolve(*args, **kwargs):
        warnings.warn("font backend substituted during lookup", RuntimeWarning)
        return original(*args, **kwargs)

    monkeypatch.setattr(ezdxf_fonts.font_manager, "get_ttf_font", warn_then_resolve)

    with pytest.raises(PreviewError, match="font backend warning"):
        render_preview(source, svg, png, fonts=_font_evidence())

    assert not svg.exists()
    assert not png.exists()


def test_render_preview_never_overwrites_its_source_dxf(tmp_path):
    source = _renderable_dxf(tmp_path / "source.dxf")
    before = source.read_bytes()
    png = tmp_path / "preview.png"

    with pytest.raises(PreviewError, match="must not overwrite source"):
        render_preview(source, source, png, fonts=_font_evidence())

    assert source.read_bytes() == before
    assert ezdxf.readfile(source).modelspace().query("LINE")
    assert not png.exists()


def test_preview_rejects_missing_exact_font_path_and_does_not_verify_shx(tmp_path):
    source = _renderable_dxf(tmp_path / "missing-font.dxf")
    document = ezdxf.readfile(source)
    document.styles.get("normal").dxf.font = "missing-font.shx"
    document.saveas(source)
    missing = tmp_path / "missing-font.shx"
    resolved = ResolvedFont(
        primary="missing-font.shx",
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=True,
        resolved_runs=(
            ResolvedFontRun(0, 1, "A", "latin", "missing-font.shx"),
        ),
        primary_path=missing,
    )

    with pytest.raises(PreviewError, match=r"missing-font\.shx"):
        render_preview(
            source,
            tmp_path / "missing.svg",
            tmp_path / "missing.png",
            fonts={"TEXT": resolved},
        )


def test_preview_accepts_family_label_only_when_style_uses_its_exact_font_file(tmp_path):
    source = _renderable_dxf(tmp_path / "family.dxf")
    fonts = {
        role: replace(resolved, primary="DejaVu Sans")
        for role, resolved in _font_evidence().items()
    }

    result = render_preview(
        source,
        tmp_path / "family.svg",
        tmp_path / "family.png",
        fonts=fonts,
    )

    assert result.role_evidence[0].font == "DejaVu Sans"
    assert result.role_evidence[0].backend_font.endswith("DejaVuSans.ttf")


def test_preview_uses_unique_temps_and_preserves_predictable_partial_collision(tmp_path):
    source = _renderable_dxf(tmp_path / "source.dxf")
    svg = tmp_path / "out.svg"
    png = tmp_path / "out.png"
    collision = tmp_path / ".out.png.partial"
    collision.write_bytes(b"unrelated sentinel")

    render_preview(source, svg, png, fonts=_font_evidence())

    assert collision.read_bytes() == b"unrelated sentinel"
    assert svg.stat().st_size > 0
    assert png.stat().st_size > 0


def test_preview_rolls_back_both_destinations_if_final_reopen_fails(
    tmp_path, monkeypatch
):
    source = _renderable_dxf(tmp_path / "source.dxf")
    svg = tmp_path / "out.svg"
    png = tmp_path / "out.png"
    svg.write_bytes(b"previous svg")
    png.write_bytes(b"previous png")

    def fail_final_reopen(*args, **kwargs):
        raise PreviewError("final reopen failed")

    monkeypatch.setattr(
        "cad_skill_core.rendering.preview._validate_published", fail_final_reopen
    )

    with pytest.raises(PreviewError, match="final reopen failed"):
        render_preview(source, svg, png, fonts=_font_evidence())

    assert svg.read_bytes() == b"previous svg"
    assert png.read_bytes() == b"previous png"


def test_preview_rejects_resolved_output_aliases_before_writing(tmp_path):
    source = _renderable_dxf(tmp_path / "source.dxf")
    svg = tmp_path / "same.out"
    png = tmp_path / "folder" / ".." / "same.out"

    with pytest.raises(PreviewError, match="output paths must differ"):
        render_preview(source, svg, png, fonts=_font_evidence())

    assert not svg.exists()


def test_preview_rejects_extreme_model_span_before_emitting_unbounded_svg(tmp_path):
    source = tmp_path / "extreme.dxf"
    document = ezdxf.new("R2018")
    document.modelspace().add_line((0, 0), (1e12, 1))
    document.saveas(source)

    with pytest.raises(PreviewError, match="model span"):
        render_preview(source, tmp_path / "extreme.svg", tmp_path / "extreme.png")

    assert not (tmp_path / "extreme.svg").exists()
    assert not (tmp_path / "extreme.png").exists()


def test_preview_allows_ordinary_degenerate_horizontal_geometry(tmp_path):
    source = tmp_path / "horizontal.dxf"
    document = ezdxf.new("R2018")
    document.modelspace().add_line((0, 0), (40, 0))
    document.saveas(source)

    result = render_preview(
        source,
        tmp_path / "horizontal.svg",
        tmp_path / "horizontal.png",
    )

    assert result.width_px == 480
    assert result.height_px == 12


def test_svg_page_and_viewbox_share_the_reported_pixel_bound(tmp_path):
    source = _renderable_dxf(tmp_path / "source.dxf")
    result = render_preview(
        source,
        tmp_path / "bounded.svg",
        tmp_path / "bounded.png",
        fonts=_font_evidence(),
    )
    root = ElementTree.parse(result.svg_path).getroot()
    width = float(root.attrib["width"].removesuffix("px"))
    height = float(root.attrib["height"].removesuffix("px"))
    view_box = tuple(float(value) for value in root.attrib["viewBox"].split())

    assert (width, height) == (result.width_px, result.height_px)
    assert view_box[2] <= 2048
    assert view_box[3] <= 2048


def test_dimension_evidence_uses_rendered_dimpost_and_tolerance_text(tmp_path):
    font_path = Path(findfont("DejaVu Sans")).resolve()
    source = _dimension_dxf(
        tmp_path / "dimension.dxf",
        font_path,
        dimpost="PRE<>%%c %%d %%p SUF",
        tolerance=True,
    )

    result = render_preview(
        source,
        tmp_path / "dimension.svg",
        tmp_path / "dimension.png",
        fonts=_single_font_evidence(font_path),
    )

    evidence = result.role_evidence[0]
    assert evidence.role == "DIM"
    assert set("PRESUFØ°±+-").issubset(set(evidence.characters))
    assert "^" not in evidence.characters


def test_dimension_diameter_suffix_must_exist_in_exact_font_cmap(tmp_path):
    font_path = Path(findfont("DejaVu Serif")).resolve()
    source = _dimension_dxf(
        tmp_path / "diameter.dxf", font_path, dimpost="<>⌀"
    )

    with pytest.raises(PreviewError, match="missing used glyphs.*⌀"):
        render_preview(
            source,
            tmp_path / "diameter.svg",
            tmp_path / "diameter.png",
            fonts=_single_font_evidence(font_path),
        )


def test_dimension_plus_minus_suffix_must_exist_in_exact_font_cmap(tmp_path):
    font_path = (
        Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSansDisplay.ttf"
    ).resolve()
    source = _dimension_dxf(
        tmp_path / "tolerance-symbol.dxf", font_path, dimpost="<>±0.1"
    )

    with pytest.raises(PreviewError, match="missing used glyphs.*±"):
        render_preview(
            source,
            tmp_path / "tolerance-symbol.svg",
            tmp_path / "tolerance-symbol.png",
            fonts=_single_font_evidence(font_path),
        )


def test_dimension_replacement_marker_never_becomes_verified_evidence(tmp_path):
    font_path = Path(findfont("DejaVu Sans")).resolve()
    source = _dimension_dxf(
        tmp_path / "replacement-dimension.dxf", font_path, dimpost="<>�"
    )

    with pytest.raises(PreviewError, match="replacement or missing glyph marker"):
        render_preview(
            source,
            tmp_path / "replacement-dimension.svg",
            tmp_path / "replacement-dimension.png",
            fonts=_single_font_evidence(font_path),
        )


def test_unrendered_dimension_fallback_includes_dimpost_and_active_tolerance():
    document = ezdxf.new("R2018")
    dimstyle = document.dimstyles.add(
        "FALLBACK-DIM",
        dxfattribs={
            "dimpost": "PRE<>°SUF",
            "dimtol": 1,
            "dimtp": 0.1,
            "dimtm": 0.2,
            "dimdec": 2,
            "dimtdec": 2,
        },
    )
    override = document.modelspace().add_linear_dim(
        base=(0, 10), p1=(0, 0), p2=(20, 0), dimstyle="FALLBACK-DIM"
    )

    text = _rendered_dimension_text(override.dimension, dimstyle)

    assert text.startswith("PRE20.00°SUF")
    assert text.endswith("+0.10-0.20")
