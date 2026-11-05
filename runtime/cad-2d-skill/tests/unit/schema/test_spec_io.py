import pytest
from pydantic import ValidationError

from cad_skill_core.schema.io import load_spec, save_spec
from cad_skill_core.schema.models import DrawingSpec


@pytest.mark.parametrize("field", ["material-text", "material-source", "note-source", "part-source", "provenance-id", "duplicate-id"])
def test_rejects_blank_or_ambiguous_manufacturing_provenance(field):
    from pathlib import Path

    values = load_spec(Path("examples/flange-assembly/work/drawing-spec.json")).model_dump(mode="json")
    if field == "material-text":
        values["requirements"]["material"]["text"] = " \t"
    elif field == "material-source":
        values["requirements"]["material"]["source_id"] = " "
    elif field == "note-source":
        values["requirements"]["technical_notes"][0]["source_id"] = " "
    elif field == "part-source":
        values["parts_list"][0]["source_id"] = " "
    elif field == "provenance-id":
        values["provenance"][0]["id"] = " "
    else:
        values["provenance"].append(dict(values["provenance"][0], locator="different line"))

    with pytest.raises(ValidationError, match="unique|pattern"):
        DrawingSpec.model_validate(values)


def test_round_trip_preserves_traceable_manufacturing_facts(tmp_path):
    spec = DrawingSpec.model_validate({
        "schema_version": "1.0",
        "project": {"name": "法兰", "drawing_type": "part"},
        "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
        "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
        "requirements": {
            "material": {"text": "Q235", "source_id": "src-material"},
            "views": ["front"],
            "technical_notes": [
                {"text": "Deburr all edges", "source_id": "src-note"}
            ],
        },
        "geometry": {"parameters": {"outer_diameter": 200}},
        "dimensions": [{"kind": "diameter", "value": 200, "source_id": "src-1"}],
        "provenance": [
            {
                "id": "src-1",
                "file": "project.md",
                "locator": "line 8",
                "confidence": 1.0,
            },
            {
                "id": "src-material",
                "file": "project.md",
                "locator": "line 9",
                "confidence": 1.0,
            },
            {
                "id": "src-note",
                "file": "project.md",
                "locator": "line 10",
                "confidence": 1.0,
            },
        ],
    })
    path = tmp_path / "drawing-spec.json"

    save_spec(spec, path)
    loaded = load_spec(path)

    assert loaded.dimensions[0].source_id == "src-1"
    assert loaded.requirements.material.source_id == "src-material"
    assert loaded.requirements.technical_notes[0].source_id == "src-note"
    assert loaded.standard.units == "mm"


def test_rejects_dimension_with_unknown_provenance_id():
    with pytest.raises(ValidationError, match="dimension source_id values are unknown: src-404"):
        DrawingSpec.model_validate({
            "schema_version": "1.0",
            "project": {"name": "法兰", "drawing_type": "part"},
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {"views": ["front"]},
            "geometry": {"parameters": {"outer_diameter": 200}},
            "dimensions": [{"kind": "diameter", "value": 200, "source_id": "src-404"}],
            "provenance": [
                {
                    "id": "src-1",
                    "file": "project.md",
                    "locator": "line 8",
                    "confidence": 1.0,
                }
            ],
        })


@pytest.mark.parametrize(
    ("requirements", "message"),
    [
        (
            {"material": "Q235", "views": ["front"]},
            "material must include text and source_id",
        ),
        (
            {"views": ["front"], "technical_notes": ["Deburr all edges"]},
            "technical_notes must include text and source_id",
        ),
    ],
)
def test_rejects_unsourced_material_or_technical_requirement(requirements, message):
    with pytest.raises(ValidationError, match=message):
        DrawingSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "法兰", "drawing_type": "part"},
                "standard": {
                    "family": "GB/T",
                    "units": "mm",
                    "projection": "first-angle",
                },
                "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
                "requirements": requirements,
                "geometry": {},
                "provenance": [],
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "material",
            {"text": "Q235", "source_id": "src-404"},
            "material source_id values are unknown: src-404",
        ),
        (
            "technical_notes",
            [{"text": "Deburr all edges", "source_id": "src-404"}],
            "technical note source_id values are unknown: src-404",
        ),
    ],
)
def test_rejects_manufacturing_fact_with_unknown_provenance(field, value, message):
    requirements = {"views": ["front"], field: value}
    with pytest.raises(ValidationError, match=message):
        DrawingSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "法兰", "drawing_type": "part"},
                "standard": {
                    "family": "GB/T",
                    "units": "mm",
                    "projection": "first-angle",
                },
                "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
                "requirements": requirements,
                "geometry": {},
                "provenance": [
                    {
                        "id": "src-1",
                        "file": "project.md",
                        "locator": "line 1",
                        "confidence": 1.0,
                    }
                ],
            }
        )


def test_rejects_assembly_quantity_without_provenance():
    with pytest.raises(ValidationError, match="source_id"):
        DrawingSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "总成", "drawing_type": "assembly"},
                "standard": {
                    "family": "GB/T",
                    "units": "mm",
                    "projection": "first-angle",
                },
                "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
                "requirements": {"views": ["front"]},
                "geometry": {},
                "parts_list": [{"item": "法兰", "quantity": 2}],
                "provenance": [],
            }
        )


def test_rejects_assembly_quantity_with_unknown_provenance():
    with pytest.raises(
        ValidationError,
        match="part source_id values are unknown: src-404",
    ):
        DrawingSpec.model_validate(
            {
                "schema_version": "1.0",
                "project": {"name": "总成", "drawing_type": "assembly"},
                "standard": {
                    "family": "GB/T",
                    "units": "mm",
                    "projection": "first-angle",
                },
                "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
                "requirements": {"views": ["front"]},
                "geometry": {},
                "parts_list": [
                    {"item": "法兰", "quantity": 2, "source_id": "src-404"}
                ],
                "provenance": [
                    {
                        "id": "src-1",
                        "file": "project.md",
                        "locator": "line 1",
                        "confidence": 1.0,
                    }
                ],
            }
        )
