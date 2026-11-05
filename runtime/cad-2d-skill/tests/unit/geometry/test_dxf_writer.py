from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading

import ezdxf
import pytest
from ezdxf import bbox
from matplotlib.font_manager import findfont

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry import dxf_writer
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.schema.io import load_spec
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import load_standard


def resolved_font_map() -> dict[str, ResolvedFont]:
    return {
        "TEXT": ResolvedFont(
            primary="Arial",
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "Arial"),),
        ),
        "DIM": ResolvedFont(
            primary="Arial",
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "Arial"),),
        ),
    }


def test_flange_dxf_contains_expected_geometry_and_layers(tmp_path):
    spec = load_spec(Path("tests/fixtures/specs/simple-flange.json"))
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    result = write_dxf(spec, standard, resolved_font_map(), tmp_path / "flange.dxf")
    doc = ezdxf.readfile(result.path)
    entities = list(doc.modelspace())

    assert sum(entity.dxftype() == "CIRCLE" for entity in entities) == 6
    assert {"OUTLINE", "CENTER", "DIM", "TEXT"} <= {layer.dxf.name for layer in doc.layers}
    assert doc.header["$INSUNITS"] == 4
    assert result.entity_counts == {"CIRCLE": 6}
    assert result.extents.min_x == -100.0
    assert result.extents.min_y == -100.0
    assert result.extents.max_x == 100.0
    assert result.extents.max_y == 100.0


def test_writer_produces_byte_identical_dxf_in_specification_entity_order(tmp_path):
    spec = load_spec(Path("tests/fixtures/specs/simple-flange.json"))
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    first = write_dxf(spec, standard, resolved_font_map(), tmp_path / "first.dxf")
    second = write_dxf(spec, standard, resolved_font_map(), tmp_path / "second.dxf")

    assert first.path.read_bytes() == second.path.read_bytes()
    assert [entity.dxftype() for entity in ezdxf.readfile(first.path).modelspace()] == ["CIRCLE"] * 6


def test_writer_bytes_are_stable_across_python_hash_seeds(tmp_path):
    script = """
from pathlib import Path
import sys

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.schema.io import load_spec
from cad_skill_core.standards.profile import load_standard

font = ResolvedFont(
    primary="Arial",
    fallbacks=(),
    missing_characters=(),
    requires_render_verification=False,
    resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "Arial"),),
)
write_dxf(
    load_spec(Path("tests/fixtures/specs/simple-flange.json")),
    load_standard(Path("knowledge/standards/gb-mechanical.yaml")),
    {"TEXT": font, "DIM": font},
    Path(sys.argv[1]),
)
"""
    outputs = []
    for seed in ("0", "4"):
        output = tmp_path / f"seed-{seed}" / "flange.dxf"
        subprocess.run(
            [sys.executable, "-c", script, str(output)],
            check=True,
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outputs.append(output.read_bytes())

    assert outputs[0] == outputs[1]


def test_overlapping_writes_are_byte_deterministic_and_preserve_ezdxf_options(tmp_path, monkeypatch):
    spec = load_spec(Path("tests/fixtures/specs/simple-flange.json"))
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    first_at_save = threading.Event()
    second_at_save = threading.Event()
    first_metadata_scope_finished = threading.Event()
    writer_role = threading.local()
    original_saveas = ezdxf.document.Drawing.saveas
    original_readfile = dxf_writer.ezdxf.readfile
    ambient_option = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = False

    def controlled_saveas(document, filename, *args, **kwargs):
        if writer_role.name == "first":
            first_at_save.set()
            assert second_at_save.wait(timeout=5)
        else:
            second_at_save.set()
            assert first_metadata_scope_finished.wait(timeout=5)
        return original_saveas(document, filename, *args, **kwargs)

    def controlled_readfile(filename, *args, **kwargs):
        if writer_role.name == "first":
            first_metadata_scope_finished.set()
        return original_readfile(filename, *args, **kwargs)

    def generate(role, output):
        writer_role.name = role
        return write_dxf(spec, standard, resolved_font_map(), output)

    monkeypatch.setattr(ezdxf.document.Drawing, "saveas", controlled_saveas)
    monkeypatch.setattr(dxf_writer.ezdxf, "readfile", controlled_readfile)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dxf") as executor:
            first_future = executor.submit(generate, "first", tmp_path / "first.dxf")
            assert first_at_save.wait(timeout=5)
            second_future = executor.submit(generate, "second", tmp_path / "second.dxf")
            first = first_future.result(timeout=10)
            second = second_future.result(timeout=10)

        assert first.path.read_bytes() == second.path.read_bytes()
        assert ezdxf.options.write_fixed_meta_data_for_testing is False
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = ambient_option


def test_standard_linetypes_have_visible_dash_patterns(tmp_path):
    spec = load_spec(Path("tests/fixtures/specs/simple-flange.json"))
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    document = ezdxf.readfile(write_dxf(spec, standard, resolved_font_map(), tmp_path / "flange.dxf").path)

    for name in ("CENTER", "HIDDEN"):
        pattern = document.linetypes.get(name).simplified_line_pattern()
        assert pattern
        assert pattern[0] > 0


def test_result_extents_enclose_reopened_text_and_dimension_bounds(tmp_path):
    spec = _spec_with_entities([
        {"kind": "text", "layer": "TEXT", "text": "WIDE", "insert": [100.0, 100.0], "height": 10.0, "source_id": "src"},
        {"kind": "dimension", "layer": "DIM", "start": [0.0, 0.0], "end": [10.0, 0.0], "offset": 20.0, "source_id": "src"},
    ])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    result = write_dxf(spec, standard, resolved_font_map(), tmp_path / "annotated.dxf")
    actual = bbox.extents(ezdxf.readfile(result.path).modelspace())

    assert result.extents.min_x <= actual.extmin.x
    assert result.extents.min_y <= actual.extmin.y
    assert result.extents.max_x >= actual.extmax.x
    assert result.extents.max_y >= actual.extmax.y


@pytest.mark.parametrize(
    ("entity", "role"),
    [
        ({"kind": "text", "layer": "TEXT", "text": "A", "insert": [0.0, 0.0], "height": 3.5, "source_id": "src"}, "TEXT"),
        ({"kind": "dimension", "layer": "DIM", "start": [0.0, 0.0], "end": [10.0, 0.0], "offset": 5.0, "source_id": "src"}, "DIM"),
    ],
)
def test_writer_rejects_missing_used_font_role(tmp_path, entity, role):
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    fonts = resolved_font_map()
    del fonts[role]

    with pytest.raises(ValueError, match=f"missing {role} font selection"):
        write_dxf(_spec_with_entities([entity]), standard, fonts, tmp_path / "missing-font.dxf")


def test_writer_reports_used_font_roles_needing_render_verification(tmp_path):
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    fonts = resolved_font_map()
    fonts["TEXT"] = ResolvedFont(
        primary="simplex.shx",
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=True,
        resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "simplex.shx"),),
    )

    result = write_dxf(
        _spec_with_entities([{"kind": "text", "layer": "TEXT", "text": "A", "insert": [0.0, 0.0], "height": 3.5, "source_id": "src"}]),
        standard,
        fonts,
        tmp_path / "unverified-font.dxf",
    )

    assert result.roles_requiring_render_verification == ("TEXT",)


def test_writer_uses_concrete_primary_path_after_fallback_promotion(tmp_path):
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    concrete = Path(findfont("DejaVu Sans")).resolve()
    fonts = resolved_font_map()
    fonts["TEXT"] = ResolvedFont(
        primary="Promoted Family Label",
        fallbacks=(),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(
            ResolvedFontRun(0, 1, "A", "latin", "Promoted Family Label"),
        ),
        primary_path=concrete,
    )

    result = write_dxf(
        _spec_with_entities(
            [
                {
                    "kind": "text",
                    "layer": "TEXT",
                    "text": "A",
                    "insert": [0.0, 0.0],
                    "height": 3.5,
                    "source_id": "src",
                }
            ]
        ),
        standard,
        fonts,
        tmp_path / "promoted-font.dxf",
    )

    document = ezdxf.readfile(result.path)
    assert document.styles.get("normal").dxf.font == concrete.name


@pytest.mark.parametrize(
    ("entity", "match"),
    [
        ({"kind": "line", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [0.0, 0.0], "source_id": "src"}, "zero-length"),
        ({"kind": "circle", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": -1.0, "source_id": "src"}, "negative radius"),
        ({"kind": "circle", "layer": "OUTLINE", "center": [float("nan"), 0.0], "radius": 1.0, "source_id": "src"}, "finite"),
        ({"kind": "circle", "layer": "MISSING", "center": [0.0, 0.0], "radius": 1.0, "source_id": "src"}, "unknown layer"),
    ],
)
def test_writer_rejects_invalid_geometry(tmp_path, entity, match):
    spec = _spec_with_entities([entity])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    with pytest.raises(ValueError, match=match):
        write_dxf(spec, standard, resolved_font_map(), tmp_path / "invalid.dxf")


def test_writer_supports_every_declared_2d_entity(tmp_path):
    entities = [
        {"kind": "line", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [10.0, 0.0], "source_id": "src"},
        {"kind": "polyline", "layer": "OUTLINE", "vertices": [[0.0, 1.0], [10.0, 1.0]], "source_id": "src"},
        {"kind": "circle", "layer": "OUTLINE", "center": [5.0, 5.0], "radius": 2.0, "source_id": "src"},
        {"kind": "arc", "layer": "OUTLINE", "center": [10.0, 5.0], "radius": 2.0, "start_angle": 0.0, "end_angle": 90.0, "source_id": "src"},
        {"kind": "spline", "layer": "OUTLINE", "control_points": [[0.0, 10.0], [5.0, 12.0], [10.0, 10.0]], "source_id": "src"},
        {"kind": "hatch", "layer": "HATCH", "boundary": [[0.0, 20.0], [5.0, 20.0], [0.0, 25.0]], "source_id": "src"},
        {"kind": "text", "layer": "TEXT", "text": "A", "insert": [0.0, 30.0], "height": 3.5, "source_id": "src"},
        {"kind": "mtext", "layer": "TEXT", "text": "B", "insert": [10.0, 30.0], "height": 3.5, "source_id": "src"},
        {"kind": "leader", "layer": "DIM", "vertices": [[0.0, 35.0], [5.0, 40.0]], "source_id": "src"},
        {"kind": "dimension", "layer": "DIM", "start": [0.0, 45.0], "end": [10.0, 45.0], "offset": 5.0, "source_id": "src"},
    ]
    spec = _spec_with_entities(entities)
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    result = write_dxf(spec, standard, resolved_font_map(), tmp_path / "all-entities.dxf")
    entities_in_dxf = [entity.dxftype() for entity in ezdxf.readfile(result.path).modelspace()]

    assert set(result.entity_counts) == {"ARC", "CIRCLE", "DIMENSION", "HATCH", "LEADER", "LINE", "LWPOLYLINE", "MTEXT", "SPLINE", "TEXT"}
    assert entities_in_dxf == ["LINE", "LWPOLYLINE", "CIRCLE", "ARC", "SPLINE", "HATCH", "TEXT", "MTEXT", "LEADER", "DIMENSION"]


def _spec_with_entities(entities: list[dict[str, object]]) -> DrawingSpec:
    return DrawingSpec.model_validate({
        "schema_version": "1.0",
        "project": {"name": "Part", "drawing_type": "part"},
        "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
        "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
        "requirements": {"views": ["front"]},
        "geometry": {"entities": entities},
        "provenance": [{"id": "src", "file": "project.md", "locator": "line 1", "confidence": 1.0}],
    })
