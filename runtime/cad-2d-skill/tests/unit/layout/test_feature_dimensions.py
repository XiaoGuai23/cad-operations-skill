from __future__ import annotations

from pathlib import Path

import ezdxf
from matplotlib.font_manager import findfont
from pydantic import ValidationError
import pytest

from cad_skill_core.fonts.metrics import measure_text_width_mm
from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.inspection.checks import inspect_drawing
from cad_skill_core.layout import apply_annotations, plan_layout
from cad_skill_core.schema.models import Dimension, DrawingSpec
from cad_skill_core.standards.profile import load_standard
from tests.helpers import RUNTIME_ROOT

PROFILE_PATH = RUNTIME_ROOT / "knowledge/standards/gb-mechanical.yaml"


def _fonts() -> dict[str, ResolvedFont]:
    fp = Path(findfont("DejaVu Sans")).resolve()
    return {
        role: ResolvedFont(
            primary=fp.name,
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", fp.name),),
            primary_path=fp,
        )
        for role in ("TEXT", "DIM")
    }


def test_feature_bound_horizontal_vertical_aligned_diameter_and_radius_dimensions_round_trip(tmp_path):
    spec = DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "Feature Bound Plate", "drawing_number": "FB-01", "drawing_type": "part"},
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {"views": ["front"]},
            "geometry": {
                "view_entity_indices": {"front": [0, 1, 2, 3]},
                "entities": [
                    {
                        "kind": "polyline",
                        "layer": "OUTLINE",
                        "vertices": [[-60.0, -40.0], [60.0, -40.0], [60.0, 40.0], [-60.0, 40.0]],
                        "closed": True,
                        "source_id": "src",
                    },
                    {
                        "kind": "circular",
                        "layer": "OUTLINE",
                        "center": [0.0, 0.0],
                        "radius": 20.0,
                        "source_id": "src",
                    },
                    {
                        "kind": "circular",
                        "layer": "OUTLINE",
                        "center": [45.0, 25.0],
                        "radius": 8.0,
                        "start_angle": 0.0,
                        "sweep": 90.0,
                        "source_id": "src",
                    },
                    {
                        "kind": "line",
                        "layer": "OUTLINE",
                        "start": [-60.0, 20.0],
                        "end": [-40.0, 40.0],
                        "source_id": "src",
                    },
                ],
            },
            "dimensions": [
                {
                    "kind": "linear",
                    "value": 120.0,
                    "source_id": "src",
                    "owner_view": "front",
                    "target_entity_index": 0,
                    "orientation": "horizontal",
                    "tolerance_upper": 0.1,
                    "tolerance_lower": -0.1,
                },
                {
                    "kind": "linear",
                    "value": 80.0,
                    "source_id": "src",
                    "owner_view": "front",
                    "target_entity_index": 0,
                    "orientation": "vertical",
                    "tolerance_upper": 0.05,
                    "tolerance_lower": -0.02,
                },
                {
                    "kind": "diameter",
                    "value": 40.0,
                    "source_id": "src",
                    "owner_view": "front",
                    "target_entity_index": 1,
                    "orientation": "diameter",
                },
                {
                    "kind": "radius",
                    "value": 8.0,
                    "source_id": "src",
                    "owner_view": "front",
                    "target_entity_index": 2,
                    "orientation": "radius",
                },
                {
                    "kind": "aligned",
                    "value": (20.0**2 + 20.0**2) ** 0.5,
                    "source_id": "src",
                    "owner_view": "front",
                    "target_entity_index": 3,
                    "orientation": "aligned",
                },
            ],
            "provenance": [{"id": "src", "file": "brief.md", "locator": "line 1", "confidence": 1.0}],
        }
    )

    standard = load_standard(PROFILE_PATH)
    fonts = _fonts()
    src = write_dxf(spec, standard, fonts, tmp_path / "src.dxf")
    doc = ezdxf.readfile(src.path)
    finalized = apply_annotations(doc, plan_layout(spec, standard), spec, standard, fonts)
    out = tmp_path / "final.dxf"
    doc.saveas(out)

    report = inspect_drawing(spec, out, finalized, standard, fonts)
    assert report.passed, report.issues

    reopened = ezdxf.readfile(out)
    dims = list(reopened.modelspace().query("DIMENSION"))
    assert len(dims) == 5
    override_texts = {d.dxf.text for d in dims}
    assert {"120±0.1", "80 (+0.05/-0.02)", "⌀40", "R8"} <= override_texts


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"kind": "linear", "value": 50.0, "source_id": "s"}, "50"),
        ({"kind": "diameter", "value": 32.0, "source_id": "s", "orientation": "diameter"}, "⌀32"),
        ({"kind": "radius", "value": 6.5, "source_id": "s", "orientation": "radius"}, "R6.5"),
        ({"kind": "linear", "value": 20.0, "source_id": "s", "tolerance_upper": 0.05}, "20 +0.05"),
        ({"kind": "linear", "value": 20.0, "source_id": "s", "tolerance_lower": -0.05}, "20 -0.05"),
    ],
)
def test_dimension_format_label_variants(kwargs, expected):
    assert Dimension(**kwargs).format_label() == expected


@pytest.mark.parametrize(
    ("bad_dim", "err_match"),
    [
        ({"kind": "linear", "value": 20.0, "source_id": "src", "owner_view": "ghost"}, "owner_view"),
        ({"kind": "linear", "value": 20.0, "source_id": "src", "target_entity_index": 9}, "target_entity_index"),
    ],
)
def test_drawing_spec_rejects_invalid_dimension_view_or_entity_reference(bad_dim, err_match):
    with pytest.raises(ValidationError, match=err_match):
        DrawingSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "Invalid", "drawing_type": "part"},
                "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
                "sheet": {"size": "A4", "orientation": "landscape", "scale": "1:1"},
                "requirements": {"views": ["front"]},
                "geometry": {
                    "entities": [
                        {"kind": "circle", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 10.0, "source_id": "src"}
                    ]
                },
                "dimensions": [bad_dim],
                "provenance": [{"id": "src", "file": "b.md", "locator": "1", "confidence": 1.0}],
            }
        )


def test_font_glyph_metrics_distinguish_narrow_latin_from_fullwidth_cjk_and_validate_inputs():
    fp = Path(findfont("DejaVu Sans")).resolve()
    narrow = measure_text_width_mm("iiii1111", 3.5, font_path=fp)
    wide_latin = measure_text_width_mm("WWWWMMMM", 3.5, font_path=fp)
    cjk = measure_text_width_mm("技术要求未注公差", 3.5)
    multiline = measure_text_width_mm("SHORT\n技术要求未注公差", 3.5)

    assert narrow < wide_latin < cjk
    assert cjk == pytest.approx(8 * 3.5, rel=0.05)
    assert multiline == pytest.approx(cjk)

    with pytest.raises(ValueError, match="height_mm"):
        measure_text_width_mm("abc", 0.0)
    with pytest.raises(ValueError, match="width_factor"):
        measure_text_width_mm("abc", 3.5, width_factor=-1.0)
