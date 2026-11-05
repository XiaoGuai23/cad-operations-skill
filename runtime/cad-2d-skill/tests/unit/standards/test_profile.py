from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from cad_skill_core.standards.profile import DrawingStandard, load_standard


PROFILE_PATH = Path("knowledge/standards/gb-mechanical.yaml")


def test_gb_profile_has_required_mechanical_layers():
    profile = load_standard(PROFILE_PATH)

    assert set(profile.layers) >= {
        "OUTLINE",
        "THIN",
        "CENTER",
        "HIDDEN",
        "DIM",
        "TEXT",
        "HATCH",
        "FRAME",
    }
    assert profile.units == "mm"
    assert profile.projection == "first-angle"
    assert profile.dimension.text_height_mm == 3.5
    assert profile.dimension.gap_mm == 5
    assert profile.spacing.view_gap_mm == 20


def test_gb_profile_has_a_series_sheet_sizes_and_title_block_zone():
    profile = load_standard(PROFILE_PATH)

    assert set(profile.sheets) == {"A0", "A1", "A2", "A3", "A4"}
    assert profile.sheets["A3"].width_mm == 420
    assert profile.sheets["A3"].height_mm == 297
    assert "title_block" in profile.protected_zones
    assert profile.protected_zones["title_block"].width_mm == 180


def test_profile_models_reject_unknown_fields():
    values = load_standard(PROFILE_PATH).model_dump(mode="python")
    values["unexpected"] = True

    with pytest.raises(ValidationError):
        DrawingStandard.model_validate(values)

    nested_values = deepcopy(values)
    nested_values.pop("unexpected")
    nested_values["layers"]["OUTLINE"]["unexpected"] = True
    with pytest.raises(ValidationError):
        DrawingStandard.model_validate(nested_values)


def test_profile_yaml_is_deterministic_and_marked_as_project_configuration():
    profile = load_standard(PROFILE_PATH)

    assert profile.profile_type == "configurable-project-profile"
    assert profile.scope == "automation-defaults"
    assert "not a complete" in profile.description.lower()


@pytest.mark.parametrize(
    ("mapping", "missing"),
    [
        ("layers", "OUTLINE"),
        ("layers", "THIN"),
        ("layers", "CENTER"),
        ("layers", "HIDDEN"),
        ("layers", "DIM"),
        ("layers", "TEXT"),
        ("layers", "HATCH"),
        ("layers", "FRAME"),
        ("text_styles", "normal"),
        ("sheets", "A0"),
        ("sheets", "A1"),
        ("sheets", "A2"),
        ("sheets", "A3"),
        ("sheets", "A4"),
        ("protected_zones", "title_block"),
    ],
)
def test_profile_rejects_missing_required_entry(mapping, missing):
    values = load_standard(PROFILE_PATH).model_dump(mode="python")
    del values[mapping][missing]

    with pytest.raises(ValidationError, match=missing):
        DrawingStandard.model_validate(values)
