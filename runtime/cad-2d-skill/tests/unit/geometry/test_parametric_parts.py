from __future__ import annotations

import ezdxf
import pytest

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.geometry.parametric import (
    FlangeParameters,
    SlottedBracketParameters,
    SteppedShaftParameters,
    build_flange_geometry,
    build_slotted_bracket_geometry,
    build_stepped_shaft_geometry,
    verify_orthographic_consistency,
)
from cad_skill_core.inspection.checks import inspect_drawing
from cad_skill_core.layout import apply_annotations, plan_layout
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import load_standard
from tests.helpers import RUNTIME_ROOT

PROFILE_PATH = RUNTIME_ROOT / "knowledge/standards/gb-mechanical.yaml"


def _fonts() -> dict[str, ResolvedFont]:
    from matplotlib.font_manager import findfont
    from pathlib import Path

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


def _build_spec_from_part(part, *, scale: str = "1:2") -> DrawingSpec:
    return DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "Parametric Part", "drawing_type": "part"},
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": scale},
            "requirements": {"views": part.views},
            "geometry": {
                "parameters": part.parameters,
                "entities": [e.model_dump(mode="json") for e in part.entities],
                "view_entity_indices": part.view_entity_indices,
            },
            "dimensions": part.dimensions,
            "provenance": [{"id": "src", "file": "spec.md", "locator": "line 1", "confidence": 1.0}],
        }
    )


@pytest.mark.parametrize(
    "builder",
    [
        lambda: build_flange_geometry(
            FlangeParameters(
                outer_diameter=140.0,
                bore_diameter=50.0,
                thickness=22.0,
                bolt_circle_diameter=96.0,
                hole_diameter=14.0,
                hole_count=4,
            ),
            source_id="src",
        ),
        lambda: build_stepped_shaft_geometry(
            SteppedShaftParameters(steps=[(35.0, 24.0), (55.0, 38.0), (30.0, 22.0)]),
            source_id="src",
        ),
        lambda: build_slotted_bracket_geometry(
            SlottedBracketParameters(
                width=120.0,
                height=70.0,
                thickness=12.0,
                corner_radius=10.0,
                slot_length=48.0,
                slot_width=16.0,
                hole_diameter=10.0,
                hole_inset=16.0,
            ),
            source_id="src",
        ),
    ],
)
def test_parametric_part_builders_pass_orthographic_and_full_inspection(tmp_path, builder):
    part = builder()
    assert verify_orthographic_consistency(part.entities, part.view_entity_indices) == []

    spec = _build_spec_from_part(part, scale="1:2")
    standard = load_standard(PROFILE_PATH)
    fonts = _fonts()
    src = write_dxf(spec, standard, fonts, tmp_path / "src.dxf")
    doc = ezdxf.readfile(src.path)
    finalized = apply_annotations(doc, plan_layout(spec, standard), spec, standard, fonts)
    out = tmp_path / "final.dxf"
    doc.saveas(out)

    report = inspect_drawing(spec, out, finalized, standard, fonts)
    assert report.passed, report.issues


def test_orthographic_projection_violation_is_flagged_by_inspect_drawing(tmp_path):
    part = build_slotted_bracket_geometry(
        SlottedBracketParameters(width=100.0, height=60.0, thickness=12.0, slot_length=40.0, slot_width=14.0),
        source_id="src",
        include_top_view=True,
    )
    spec_dict = _build_spec_from_part(part, scale="1:1").model_dump(mode="json")
    # Deliberately corrupt top view width from 100 mm to 75 mm (violates 长对正!)
    top_poly_idx = spec_dict["geometry"]["view_entity_indices"]["top"][0]
    spec_dict["geometry"]["entities"][top_poly_idx]["vertices"] = [
        [-37.5, 0.0],
        [37.5, 0.0],
        [37.5, 12.0],
        [-37.5, 12.0],
    ]
    spec = DrawingSpec.model_validate(spec_dict)
    standard = load_standard(PROFILE_PATH)
    fonts = _fonts()
    src = write_dxf(spec, standard, fonts, tmp_path / "bad_ortho_src.dxf")
    doc = ezdxf.readfile(src.path)
    finalized = apply_annotations(doc, plan_layout(spec, standard), spec, standard, fonts)
    out = tmp_path / "bad_ortho_final.dxf"
    doc.saveas(out)

    report = inspect_drawing(spec, out, finalized, standard, fonts)
    assert any(issue.code == "SEMANTIC_ORTHOGRAPHIC_PROJECTION" for issue in report.issues)


def test_parametric_validators_reject_interfering_features():
    with pytest.raises(ValueError, match="overlap|intersect"):
        FlangeParameters(
            outer_diameter=100.0,
            bore_diameter=60.0,
            bolt_circle_diameter=80.0,
            hole_diameter=25.0,
            hole_count=8,
        )
    with pytest.raises(ValueError, match="wall thickness"):
        SlottedBracketParameters(width=50.0, height=40.0, slot_length=46.0, slot_width=14.0)


def test_orthographic_consistency_detects_height_and_depth_mismatches_and_supports_opt_out(tmp_path):
    from cad_skill_core.geometry.entities import PolylineEntity

    front_box = PolylineEntity(
        kind="polyline",
        layer="OUTLINE",
        vertices=[(0.0, 0.0), (80.0, 0.0), (80.0, 50.0), (0.0, 50.0)],
        closed=True,
        source_id="src",
    )
    bad_right_box = PolylineEntity(
        kind="polyline",
        layer="OUTLINE",
        vertices=[(0.0, 0.0), (30.0, 0.0), (30.0, 42.0), (0.0, 42.0)],
        closed=True,
        source_id="src",
    )
    bad_top_box = PolylineEntity(
        kind="polyline",
        layer="OUTLINE",
        vertices=[(0.0, 0.0), (80.0, 0.0), (80.0, 25.0), (0.0, 25.0)],
        closed=True,
        source_id="src",
    )
    violations = verify_orthographic_consistency(
        [front_box, bad_right_box, bad_top_box],
        {"front": [0], "right": [1], "top": [2]},
    )
    assert any("高平齐" in v for v in violations)
    assert any("宽相等" in v for v in violations)

    # Verify orthographic_check=False suppresses SEMANTIC_ORTHOGRAPHIC_PROJECTION when intentionally schematic
    spec = DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "Schematic Views", "drawing_type": "part"},
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {"views": ["front", "right"]},
            "geometry": {
                "parameters": {"orthographic_check": False},
                "entities": [front_box.model_dump(mode="json"), bad_right_box.model_dump(mode="json")],
                "view_entity_indices": {"front": [0], "right": [1]},
            },
            "provenance": [{"id": "src", "file": "s.md", "locator": "1", "confidence": 1.0}],
        }
    )
    standard = load_standard(PROFILE_PATH)
    fonts = _fonts()
    src = write_dxf(spec, standard, fonts, tmp_path / "optout_src.dxf")
    doc = ezdxf.readfile(src.path)
    finalized = apply_annotations(doc, plan_layout(spec, standard), spec, standard, fonts)
    out = tmp_path / "optout_final.dxf"
    doc.saveas(out)
    report = inspect_drawing(spec, out, finalized, standard, fonts)
    assert report.passed

