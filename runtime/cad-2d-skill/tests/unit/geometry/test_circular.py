"""A circular entity is one kind in the spec and a native CIRCLE/ARC in the file."""
import ezdxf
import pytest

from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.geometry.entities import (
    ArcEntity,
    CircleEntity,
    CircularEntity,
    to_circular,
)
from pathlib import Path

from cad_skill_core.standards.profile import load_standard

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.schema.models import DrawingSpec


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


def test_circular_full_sweep_writes_native_circle_and_partial_writes_arc(tmp_path):
    spec = _spec_with_entities([
        {"kind": "circular", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 5.0,
         "start_angle": 0.0, "sweep": 360.0, "source_id": "src"},
        {"kind": "circular", "layer": "OUTLINE", "center": [20.0, 0.0], "radius": 5.0,
         "start_angle": 30.0, "sweep": 90.0, "source_id": "src"},
    ])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    result = write_dxf(spec, standard, resolved_font_map(), tmp_path / "circular.dxf")
    entities = list(ezdxf.readfile(result.path).modelspace())

    assert [entity.dxftype() for entity in entities] == ["CIRCLE", "ARC"]
    assert result.entity_counts == {"ARC": 1, "CIRCLE": 1}
    arc = entities[1]
    assert arc.dxf.start_angle == pytest.approx(30.0)
    assert arc.dxf.end_angle == pytest.approx(120.0)


def test_circular_defaults_to_full_circle():
    entity = CircularEntity(kind="circular", center=(0.0, 0.0), radius=2.0,
                            layer="OUTLINE", source_id="src")

    assert entity.is_full_circle
    assert entity.end_angle == 360.0


@pytest.mark.parametrize("sweep", [0.0, -10.0, 360.5])
def test_circular_rejects_out_of_range_sweep(tmp_path, sweep):
    spec = _spec_with_entities([
        {"kind": "circular", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 5.0,
         "start_angle": 0.0, "sweep": sweep, "source_id": "src"},
    ])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))

    with pytest.raises(ValueError, match="sweep"):
        write_dxf(spec, standard, resolved_font_map(), tmp_path / "bad.dxf")


def test_to_circular_normalises_circle_and_arc_to_one_kind():
    circle = to_circular(CircleEntity(kind="circle", center=(1.0, 2.0), radius=3.0,
                                      layer="OUTLINE", source_id="src"))
    assert circle.is_full_circle and circle.sweep == 360.0 and circle.center == (1.0, 2.0)

    arc = to_circular(ArcEntity(kind="arc", center=(1.0, 2.0), radius=3.0,
                                start_angle=270.0, end_angle=90.0,
                                layer="OUTLINE", source_id="src"))
    assert arc.sweep == 180.0 and arc.start_angle == 270.0 and arc.end_angle == 450.0
    assert not arc.is_full_circle


def test_to_circular_rejects_zero_sweep_arc():
    with pytest.raises(ValueError, match="sweep"):
        to_circular(ArcEntity(kind="arc", center=(0.0, 0.0), radius=3.0,
                              start_angle=10.0, end_angle=10.0,
                              layer="OUTLINE", source_id="src"))


@pytest.mark.parametrize("start,sweep", [(0.0, 359.9999999995), (1e20, 90.0), (-1e20, 90.0)])
def test_partial_circle_preserves_sweep_and_native_arc_type(tmp_path, start, sweep):
    spec = _spec_with_entities([
        {"kind": "circular", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 5.0,
         "start_angle": start, "sweep": sweep, "source_id": "src"},
    ])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    result = write_dxf(spec, standard, {}, tmp_path / "partial.dxf")
    arc = ezdxf.readfile(result.path).modelspace()[0]
    assert arc.dxftype() == "ARC"
    assert arc.dxf.start_angle == pytest.approx(start % 360.0)
    assert (arc.dxf.end_angle - arc.dxf.start_angle) % 360 == pytest.approx(sweep)


def test_unrepresentable_partial_arc_is_rejected_before_publication(tmp_path):
    spec = _spec_with_entities([
        {"kind": "circular", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 5.0,
         "start_angle": 30.0, "sweep": 360 - 1e-10, "source_id": "src"},
    ])
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    output = tmp_path / "ambiguous.dxf"
    with pytest.raises(ValueError, match="precision"):
        write_dxf(spec, standard, {}, output)
    assert not output.exists()
