from __future__ import annotations

import json
from pathlib import Path
import shutil

import ezdxf
from PIL import Image
import pytest

from cad_skill_core import pipeline
from cad_skill_core.autocad import AutoCADResult
from cad_skill_core.pipeline import PipelineOptions, run_task
from tests.helpers import copy_example


REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "golden" / "flange-assembly.snapshot.json"


@pytest.fixture(scope="module")
def generated_partial_flange(tmp_path_factory):
    task = copy_example(
        "flange-assembly", tmp_path_factory.mktemp("semantic-inventory")
    )
    result = run_task(task, PipelineOptions(run_autocad=False))
    assert result.status == "partial"
    return task, result.audit_path


def test_flange_assembly_rule_source_matches_reviewed_sheet_configuration() -> None:
    task = REPO_ROOT / "examples" / "flange-assembly"
    spec = json.loads((task / "work" / "drawing-spec.json").read_text(encoding="utf-8"))
    rules = (task / "input" / "drawing-rules.md").read_text(encoding="utf-8")

    assert (
        f"{spec['sheet']['size']} 横向图幅和 {spec['sheet']['scale']} 比例"
        in rules
    )


def _deterministic_fake_autocad(request) -> AutoCADResult:
    """Exercise the pipeline boundary without starting AutoCAD or a GUI."""

    request.output_dir.mkdir(parents=True, exist_ok=True)
    dwg = request.output_dir / "drawing.dwg"
    pdf = request.output_dir / "drawing.pdf"
    dwg.write_bytes(b"AC1032" + bytes(range(256)) * 4)
    with Image.open(request.input_dxf.with_name("drawing.png")) as preview:
        preview.convert("RGB").save(pdf, "PDF", resolution=100.0)
    return AutoCADResult(
        "succeeded",
        0,
        dwg,
        pdf,
        None,
        None,
        None,
        0.01,
        evidence=(
            "deterministic injected boundary wrote an AC1032 fixture",
            "deterministic injected boundary wrote a one-page PDF from the verified PNG",
            "no AutoCAD executable or GUI was started by this contract test",
        ),
    )


def test_flange_assembly_matches_reviewed_semantics(tmp_path, monkeypatch) -> None:
    task = copy_example("flange-assembly", tmp_path)
    monkeypatch.setattr(pipeline, "execute_autocad", _deterministic_fake_autocad)

    result = run_task(task, PipelineOptions(run_autocad=True))

    assert result.status == "complete"
    with Image.open(task / "output" / "drawing.png") as preview:
        rgb = preview.convert("RGB")
        dark_pixels = sum(
            max(pixel) < 80 for pixel in rgb.get_flattened_data()
        )
        assert dark_pixels >= (rgb.width + rgb.height) // 2
    from tests.golden.snapshot import semantic_snapshot

    actual = semantic_snapshot(task / "output" / "drawing.dxf", result.audit_path)
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert actual == expected
    for suffix in (".dxf", ".dwg", ".pdf", ".svg", ".png"):
        assert (task / "output" / f"drawing{suffix}").stat().st_size > 0


def test_flange_assembly_without_autocad_is_truthfully_partial(tmp_path) -> None:
    task = copy_example("flange-assembly", tmp_path)

    result = run_task(task, PipelineOptions(run_autocad=False))

    assert result.status == "partial"
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["status"] == "partial"
    assert audit["autocad"]["status"] == "disabled"
    assert {path.suffix for path in result.outputs}.isdisjoint({".dwg", ".pdf"})


def test_final_audit_routes_golden_semantics_to_committed_dxf_handles(
    generated_partial_flange,
) -> None:
    task, audit_path = generated_partial_flange
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    document = ezdxf.readfile(task / "output" / "drawing.dxf")
    inventory = audit["semantic_inventory"]

    assert {
        "view_source",
        "view_label",
        "dimension",
        "part_description",
        "part_quantity",
        "balloon_number",
        "technical_note",
    } <= {record["semantic_kind"] for record in inventory}
    assert all(record["handles"] for record in inventory)
    assert all(
        handle in document.entitydb
        for record in inventory
        for handle in record["handles"]
    )

    canonical = json.loads((task / audit["provenance"]["canonical_spec"]).read_text(encoding="utf-8"))
    expected_sources = {
        "material": [canonical["requirements"]["material"]["source_id"]],
        "technical_note": [note["source_id"] for note in canonical["requirements"]["technical_notes"]],
        "part_quantity": [part["source_id"] for part in canonical["parts_list"]],
    }
    known = {source["id"] for source in canonical["provenance"]}
    for kind, expected in expected_sources.items():
        records = [record for record in inventory if record["semantic_kind"] == kind]
        assert [record["source_id"] for record in records] == expected
        assert all(record["source_id"] in known for record in records)
    material = next(record for record in inventory if record["semantic_kind"] == "material")
    assert any(
        canonical["requirements"]["material"]["text"] in document.entitydb[handle].dxf.text
        for handle in material["handles"]
    )


@pytest.mark.parametrize(
    "semantic_kind",
    [
        "part_description",
        "part_quantity",
        "dimension",
        "balloon_number",
        "technical_note",
        "view_label",
    ],
)
def test_semantic_snapshot_changes_when_final_dxf_semantic_is_mutated(
    generated_partial_flange,
    tmp_path,
    semantic_kind,
) -> None:
    task, audit_path = generated_partial_flange
    source = task / "output" / "drawing.dxf"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = [
        record
        for record in audit["semantic_inventory"]
        if record["semantic_kind"] == semantic_kind
    ]
    assert records

    from tests.golden.snapshot import semantic_snapshot

    baseline = semantic_snapshot(source, audit_path)
    for index, target in enumerate(records):
        mutated = tmp_path / f"mutated-{semantic_kind}-{index}.dxf"
        shutil.copy2(source, mutated)
        document = ezdxf.readfile(mutated)
        entity = document.entitydb[target["handles"][0]]
        if semantic_kind == "balloon_number":
            assert len(records) >= 2
            other = records[1] if index == 0 else records[0]
            replacement = str(
                document.entitydb[other["handles"][0]].dxf.text
            )
        elif semantic_kind == "part_quantity":
            replacement = str(int(entity.dxf.text) + 10)
        else:
            replacement = {
                "part_description": "MUTATED PART",
                "dimension": "999",
                "technical_note": "MUTATED TECHNICAL NOTE",
                "view_label": "MUTATED VIEW",
            }[semantic_kind]
        entity.dxf.text = replacement
        document.saveas(mutated)

        changed = semantic_snapshot(mutated, audit_path)
        assert changed != baseline


def test_semantic_snapshot_rejects_singleton_handle_aliasing(
    generated_partial_flange,
    tmp_path,
) -> None:
    task, audit_path = generated_partial_flange
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    quantities = [
        record
        for record in audit["semantic_inventory"]
        if record["semantic_kind"] == "part_quantity"
    ]
    assert len(quantities) >= 2
    quantities[1]["handles"] = list(quantities[0]["handles"])
    aliased_audit = tmp_path / "aliased-audit.json"
    aliased_audit.write_text(
        json.dumps(audit, ensure_ascii=False), encoding="utf-8"
    )

    from tests.golden.snapshot import semantic_snapshot

    with pytest.raises(ValueError, match="aliases singleton semantic records"):
        semantic_snapshot(task / "output" / "drawing.dxf", aliased_audit)


def test_two_view_same_type_handle_swap_changes_or_invalidates_snapshot(tmp_path) -> None:
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    first_line = modelspace.add_line((0, 0), (10, 0))
    second_line = modelspace.add_line((100, 0), (130, 0))
    first_label = modelspace.add_text("VIEW A").set_placement((0, 10))
    second_label = modelspace.add_text("VIEW B").set_placement((100, 10))
    dxf_path = tmp_path / "two-views.dxf"
    document.saveas(dxf_path)
    inventory = [
        {
            "semantic_kind": "view_source",
            "semantic_id": "view-a",
            "source_membership": [
                {"entity_index": 0, "source_id": "source-a"}
            ],
            "normalized_source_bounds_mm": [0.0, 0.0, 10.0, 0.0],
            "handles": [first_line.dxf.handle],
        },
        {
            "semantic_kind": "view_label",
            "semantic_id": "view-a",
            "annotation_id": "view-label-a",
            "handles": [first_label.dxf.handle],
        },
        {
            "semantic_kind": "view_source",
            "semantic_id": "view-b",
            "source_membership": [
                {"entity_index": 1, "source_id": "source-b"}
            ],
            "normalized_source_bounds_mm": [100.0, 0.0, 130.0, 0.0],
            "handles": [second_line.dxf.handle],
        },
        {
            "semantic_kind": "view_label",
            "semantic_id": "view-b",
            "annotation_id": "view-label-b",
            "handles": [second_label.dxf.handle],
        },
    ]
    audit = {
        "status": "partial",
        "summary": {"errors": 0, "warnings": 1, "info": 0},
        "issues": [],
        "semantic_inventory": inventory,
        "font_render": {"verified_roles": [], "evidence": []},
        "provenance": {
            "canonical_spec_file_sha256": "fixture",
            "source_files": [],
        },
        "artifacts": [],
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    from tests.golden.snapshot import semantic_snapshot

    baseline = semantic_snapshot(dxf_path, audit_path)
    inventory[0]["handles"], inventory[2]["handles"] = (
        inventory[2]["handles"],
        inventory[0]["handles"],
    )
    swapped_path = tmp_path / "swapped-audit.json"
    swapped_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ValueError, match="source bounds do not match"):
        semantic_snapshot(dxf_path, swapped_path)
    assert baseline["semantics"]["views"][0]["source_membership"] == [
        {"entity_index": 0, "source_id": "source-a"}
    ]
