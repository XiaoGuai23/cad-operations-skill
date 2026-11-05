from __future__ import annotations

import json
from dataclasses import replace

import ezdxf
import pytest

from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.inspection.models import AuditIssue, AuditReport, RepairAction
from cad_skill_core.inspection.repair import (
    EngineeringPayloadMutationError,
    GenerationContext,
    UnsafeRepairError,
    _RepairState,
    _apply_action,
    _write_spec_snapshot,
    plan_repairs,
    run_repair_loop,
)
from cad_skill_core.rendering.preview import PreviewError


def _issue(
    code: str,
    *,
    message: str = "repair candidate",
    handles: tuple[str, ...] = ("A",),
) -> AuditIssue:
    return AuditIssue(code, "error", "layout", message, handles, True)


def _force_report(monkeypatch, report: AuditReport) -> None:
    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.inspect_drawing",
        lambda *args, **kwargs: report,
    )


def test_plan_repairs_maps_only_the_five_approved_repair_classes_in_fixed_order():
    report = AuditReport(
        (
            _issue("LAYOUT_VIEW_SPACING", message="views front and top are too close"),
            _issue("SEMANTIC_MISSING_DIMENSION"),
            _issue(
                "LAYOUT_VIEW_COLLISION",
                message="annotation collided with view front: dimension-0",
                handles=("D1",),
            ),
            _issue("STANDARD_ENTITY_LAYER", handles=("10",)),
            _issue(
                "FONT_MISSING_SELECTION",
                message="font role has no selected primary font: TEXT",
                handles=("T1",),
            ),
            _issue("LAYOUT_COLLISION", handles=("T2", "T1")),
        )
    )

    assert plan_repairs(report) == (
        RepairAction("REASSIGN_LAYER", "10", {"strategy": "entity-kind"}),
        RepairAction("SELECT_VERIFIED_FALLBACK_FONT", "TEXT", {}),
        RepairAction("SHIFT_TEXT", "T1,T2", {"distance_mm": 1.0}),
        RepairAction("INCREASE_DIMENSION_OFFSET", "D1", {"increment_mm": 1.0}),
        RepairAction("INCREASE_VIEW_GAP", "front,top", {"increment_mm": 1.0}),
    )


def test_plan_repairs_rejects_ambiguous_or_nonrepairable_findings():
    report = AuditReport(
        (
            AuditIssue(
                "STANDARD_ENTITY_LAYER",
                "error",
                "standard",
                "entity has no handle",
                (),
                True,
            ),
            AuditIssue(
                "FONT_MISSING_SELECTION",
                "error",
                "font",
                "font selection lacks a role",
                ("T1",),
                True,
            ),
            AuditIssue(
                "LAYOUT_COLLISION",
                "error",
                "layout",
                "collision is marked nonrepairable",
                ("T1",),
                False,
            ),
        )
    )

    assert plan_repairs(report) == ()


def test_layer_reassignment_uses_the_spec_owned_layer_not_the_bad_dxf_layer(
    valid_context
):
    document = ezdxf.readfile(valid_context.dxf_path)
    handle = valid_context.plan.views[0].source_handles[0]
    entity = next(item for item in document.modelspace() if item.dxf.handle == handle)
    entity.dxf.layer = "BAD-UNAPPROVED"
    state = _RepairState(
        valid_context.standard.model_copy(deep=True),
        dict(valid_context.font_result),
        {},
        {},
    )

    _apply_action(
        valid_context.spec.model_copy(deep=True),
        state,
        RepairAction("REASSIGN_LAYER", handle, {"strategy": "entity-kind"}),
        valid_context.plan,
        document,
    )

    source_index = valid_context.plan.views[0].source_indices[0]
    assert state.layer_overrides[f"source:{source_index}"] == (
        valid_context.spec.geometry.entities[source_index].layer
    )


def test_fallback_selection_requires_role_wide_verified_run_evidence(valid_context):
    state = _RepairState(
        valid_context.standard.model_copy(deep=True),
        {
            "TEXT": ResolvedFont(
                primary="Primary",
                fallbacks=("LatinOnly",),
                missing_characters=(),
                requires_render_verification=False,
                resolved_runs=(
                    ResolvedFontRun(0, 1, "技", "cjk", "CJKFont"),
                    ResolvedFontRun(1, 2, "A", "latin", "LatinOnly"),
                ),
            )
        },
        {},
        {},
    )

    with pytest.raises(UnsafeRepairError, match="verified fallback"):
        _apply_action(
            valid_context.spec.model_copy(deep=True),
            state,
            RepairAction("SELECT_VERIFIED_FALLBACK_FONT", "TEXT", {}),
            valid_context.plan,
            ezdxf.readfile(valid_context.dxf_path),
        )


def test_fallback_selection_promotes_matching_name_and_path_atomically(
    valid_context, tmp_path
):
    fallback_path = valid_context.font_result["TEXT"].primary_path
    assert fallback_path is not None
    primary_path = fallback_path
    later_path = fallback_path
    resolved = ResolvedFont(
        primary="Primary",
        fallbacks=("DejaVu Sans", "DejaVu Sans"),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "DejaVu Sans"),),
        primary_path=primary_path,
        fallback_paths=(fallback_path, later_path),
    )
    state = _RepairState(
        valid_context.standard.model_copy(deep=True), {"TEXT": resolved}, {}, {}
    )

    _apply_action(
        valid_context.spec.model_copy(deep=True),
        state,
        RepairAction("SELECT_VERIFIED_FALLBACK_FONT", "TEXT", {}),
        valid_context.plan,
        ezdxf.readfile(valid_context.dxf_path),
    )

    promoted = state.fonts["TEXT"]
    assert promoted.primary == "DejaVu Sans"
    assert promoted.primary_path == fallback_path
    assert promoted.fallbacks == ("DejaVu Sans",)
    assert promoted.fallback_paths == (later_path,)
    assert all(run.font == promoted.primary for run in promoted.resolved_runs)


def test_fallback_selection_without_matching_verified_path_fails_without_mutation(
    valid_context, tmp_path
):
    resolved = ResolvedFont(
        primary="Primary",
        fallbacks=("Fallback",),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "Fallback"),),
        primary_path=tmp_path / "primary.ttf",
        fallback_paths=(tmp_path / "missing-fallback.ttf",),
    )
    state = _RepairState(
        valid_context.standard.model_copy(deep=True), {"TEXT": resolved}, {}, {}
    )

    with pytest.raises(UnsafeRepairError, match="verified fallback"):
        _apply_action(
            valid_context.spec.model_copy(deep=True),
            state,
            RepairAction("SELECT_VERIFIED_FALLBACK_FONT", "TEXT", {}),
            valid_context.plan,
            ezdxf.readfile(valid_context.dxf_path),
        )

    assert state.fonts["TEXT"] == resolved


def test_fallback_selection_rejects_existing_path_for_a_different_font(
    valid_context
):
    wrong_path = valid_context.font_result["TEXT"].primary_path
    assert wrong_path is not None
    resolved = ResolvedFont(
        primary="Primary",
        fallbacks=("Definitely Not DejaVu Sans",),
        missing_characters=(),
        requires_render_verification=False,
        resolved_runs=(
            ResolvedFontRun(0, 1, "A", "latin", "Definitely Not DejaVu Sans"),
        ),
        primary_path=wrong_path,
        fallback_paths=(wrong_path,),
    )
    state = _RepairState(
        valid_context.standard.model_copy(deep=True), {"TEXT": resolved}, {}, {}
    )

    with pytest.raises(UnsafeRepairError, match="verified fallback"):
        _apply_action(
            valid_context.spec.model_copy(deep=True),
            state,
            RepairAction("SELECT_VERIFIED_FALLBACK_FONT", "TEXT", {}),
            valid_context.plan,
            ezdxf.readfile(valid_context.dxf_path),
        )

    assert state.fonts["TEXT"] == resolved


def test_repair_loop_preserves_engineering_payload_and_every_iteration_artifact(
    valid_context, tmp_path
):
    work = tmp_path / "task" / "work"
    source_document = tmp_path / "task" / "input" / "project.md"
    source_document.parent.mkdir(parents=True)
    source_document.write_text("user source remains untouched", encoding="utf-8")
    before = valid_context.spec.model_dump(mode="json")

    result = run_repair_loop(valid_context.generation_context(work))

    assert result.status == "passed"
    assert result.iterations == 1
    assert result.spec.model_dump(mode="json") == before
    assert valid_context.spec.model_dump(mode="json") == before
    assert source_document.read_text(encoding="utf-8") == "user source remains untouched"
    iteration = work / "iterations" / "01"
    assert result.last_dxf == iteration / "drawing.dxf"
    assert result.last_dxf.stat().st_size > 0
    assert (iteration / "audit.json").stat().st_size > 0
    assert (iteration / "drawing-spec.json").stat().st_size > 0
    assert (iteration / "drawing.svg").stat().st_size > 0
    assert (iteration / "drawing.png").stat().st_size > 0
    manifest = json.loads((iteration / "iteration.json").read_text(encoding="utf-8"))
    assert manifest["dxf_validation"] == "audit-clean"
    assert manifest["validated_dxf"] == "drawing.dxf"
    assert json.loads((iteration / "audit.json").read_text(encoding="utf-8"))[
        "status"
    ] == "passed"


def test_generation_context_has_no_audit_override(valid_context, tmp_path):
    with pytest.raises(TypeError, match="audit_input"):
        GenerationContext(
            valid_context.spec,
            valid_context.standard,
            valid_context.font_result,
            tmp_path / "work",
            audit_input=AuditReport(),
        )


def test_nonrepairable_issue_stops_failed_with_last_dxf_and_report(
    valid_context, tmp_path, monkeypatch
):
    report = AuditReport(
        (
            AuditIssue(
                "SEMANTIC_MISSING_DIMENSION",
                "error",
                "semantic",
                "missing dimension",
                (),
                False,
            ),
        )
    )
    _force_report(monkeypatch, report)
    context = valid_context.generation_context(tmp_path / "task" / "work")

    result = run_repair_loop(context)

    assert result.status == "failed"
    assert result.iterations == 1
    assert result.last_dxf.stat().st_size > 0
    assert [issue.code for issue in result.report.issues] == [
        "SEMANTIC_MISSING_DIMENSION"
    ]
    assert (result.last_dxf.parent / "audit.json").stat().st_size > 0


def test_repair_loop_is_bounded_to_three_atomic_iteration_directories(
    valid_context, tmp_path, monkeypatch
):
    persistent = AuditReport(
        (
            AuditIssue(
                "LAYOUT_COLLISION",
                "error",
                "layout",
                "measured annotation bounds collide",
                ("AB",),
                True,
            ),
        )
    )
    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.inspect_drawing",
        lambda *args, **kwargs: persistent,
    )
    work = tmp_path / "task" / "work"

    result = run_repair_loop(valid_context.generation_context(work), max_iterations=3)

    assert result.status == "failed"
    assert result.iterations == 3
    assert sorted(path.name for path in (work / "iterations").iterdir()) == [
        "01",
        "02",
        "03",
    ]
    assert not any(path.name.endswith("partial") for path in work.rglob("*"))


def test_immutable_payload_is_asserted_after_every_repair_action(
    valid_context, tmp_path, monkeypatch
):
    report = AuditReport(
        (
            AuditIssue(
                "LAYOUT_VIEW_SPACING",
                "error",
                "layout",
                "views front and section-A have 0 mm clearance",
                ("1", "2"),
                True,
            ),
        )
    )

    def corrupt_payload(spec, *args, **kwargs):
        spec.geometry.parameters["invented"] = 99

    monkeypatch.setattr("cad_skill_core.inspection.repair._apply_action", corrupt_payload)
    _force_report(monkeypatch, report)
    context = valid_context.generation_context(tmp_path / "task" / "work")

    with pytest.raises(EngineeringPayloadMutationError, match="geometry"):
        run_repair_loop(context)

    assert "invented" not in valid_context.spec.geometry.parameters


def test_immutable_payload_assertion_runs_even_when_the_action_itself_raises(
    valid_context, tmp_path, monkeypatch
):
    report = AuditReport(
        (
            AuditIssue(
                "LAYOUT_VIEW_SPACING",
                "error",
                "layout",
                "views front and section-A have 0 mm clearance",
                ("1", "2"),
                True,
            ),
        )
    )

    def corrupt_then_fail(spec, *args, **kwargs):
        spec.provenance.clear()
        raise RuntimeError("action failed after mutation")

    monkeypatch.setattr(
        "cad_skill_core.inspection.repair._apply_action", corrupt_then_fail
    )
    _force_report(monkeypatch, report)
    context = valid_context.generation_context(tmp_path / "task" / "work")

    with pytest.raises(EngineeringPayloadMutationError, match="provenance"):
        run_repair_loop(context)


def test_successful_preview_resolves_only_render_roles_with_explicit_evidence(
    valid_context, tmp_path
):
    render_fonts = {
        role: replace(font, requires_render_verification=True)
        for role, font in valid_context.font_result.items()
    }
    context = GenerationContext(
        valid_context.spec,
        valid_context.standard,
        render_fonts,
        tmp_path / "task" / "work",
    )

    result = run_repair_loop(context)

    assert result.status == "passed"
    assert result.verified_roles == ("DIM", "TEXT")
    assert any(issue.code == "FONT_RENDER_VERIFIED" for issue in result.report.issues)
    assert not any(
        issue.code == "FONT_RENDER_VERIFICATION_REQUIRED"
        for issue in result.report.issues
    )
    assert any("without backend warnings" in item for item in result.evidence)


def test_preview_failure_is_failed_and_retains_dxf_without_partial_previews(
    valid_context, tmp_path, monkeypatch
):
    def fail_preview(*args, **kwargs):
        raise PreviewError("controlled render failure")

    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.render_preview", fail_preview
    )

    result = run_repair_loop(
        valid_context.generation_context(tmp_path / "task" / "work")
    )

    assert result.status == "failed"
    assert result.last_dxf.stat().st_size > 0
    assert any(issue.code == "PREVIEW_RENDER_FAILED" for issue in result.report.issues)
    assert not (result.last_dxf.parent / "drawing.svg").exists()
    assert not (result.last_dxf.parent / "drawing.png").exists()


def test_missing_verified_fallback_stops_failed_without_losing_iteration(
    valid_context, tmp_path, monkeypatch
):
    report = AuditReport(
        (
            AuditIssue(
                "FONT_MISSING_SELECTION",
                "error",
                "font",
                "font role has no selected primary font: TEXT",
                ("T1",),
                True,
            ),
        )
    )
    _force_report(monkeypatch, report)
    context = valid_context.generation_context(tmp_path / "task" / "work")

    result = run_repair_loop(context)

    assert result.status == "failed"
    assert result.iterations == 1
    assert result.last_dxf.stat().st_size > 0
    assert [issue.code for issue in result.report.issues] == [
        "FONT_MISSING_SELECTION"
    ]


def test_later_iteration_failure_returns_the_last_existing_dxf(
    valid_context, tmp_path, monkeypatch
):
    first_report = AuditReport(
        (
            AuditIssue(
                "LAYOUT_VIEW_SPACING",
                "error",
                "layout",
                "views front and section-A have 0 mm clearance",
                ("1", "2"),
                True,
            ),
        )
    )
    original_write = write_dxf
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("second generation failed")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.write_dxf", fail_second_write
    )
    inspect_calls = 0

    def first_issue_then_real(*args, **kwargs):
        nonlocal inspect_calls
        inspect_calls += 1
        if inspect_calls == 1:
            return first_report
        raise AssertionError("second inspection should not run after writer failure")

    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.inspect_drawing", first_issue_then_real
    )
    work = tmp_path / "task" / "work"
    context = valid_context.generation_context(work)

    result = run_repair_loop(context)

    assert result.status == "failed"
    assert result.iterations == 2
    assert result.last_dxf == work / "iterations" / "01" / "drawing.dxf"
    assert result.last_dxf.stat().st_size > 0
    assert (work / "iterations" / "02" / "audit.json").stat().st_size > 0
    assert (work / "iterations" / "02" / "drawing-spec.json").stat().st_size > 0


def test_writer_garbage_is_quarantined_and_never_returned_as_last_dxf(
    valid_context, tmp_path, monkeypatch
):
    def write_garbage_then_fail(spec, standard, fonts, output):
        output.write_bytes(b"not a dxf")
        raise ValueError("writer failed after partial output")

    monkeypatch.setattr(
        "cad_skill_core.inspection.repair.write_dxf", write_garbage_then_fail
    )
    work = tmp_path / "task" / "work"

    result = run_repair_loop(valid_context.generation_context(work))

    iteration = work / "iterations" / "01"
    assert result.status == "failed"
    assert result.last_dxf is None
    assert not (iteration / "source.dxf").exists()
    assert (iteration / "source.dxf.failed").read_bytes() == b"not a dxf"
    manifest = json.loads((iteration / "iteration.json").read_text(encoding="utf-8"))
    assert manifest["dxf_validation"] == "not-validated"


def test_spec_snapshot_is_canonical_across_mapping_insertion_order(
    valid_context, tmp_path
):
    first = valid_context.spec.model_copy(deep=True)
    second = valid_context.spec.model_copy(deep=True)
    first.geometry.parameters.update({"b": 2, "a": 1})
    second.geometry.parameters.update({"a": 1, "b": 2})
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    _write_spec_snapshot(first, first_path)
    _write_spec_snapshot(second, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_path.read_bytes().endswith(b"\n")
    assert b"\n  " not in first_path.read_bytes()


@pytest.mark.parametrize("fact,payload_key", [
    ("material", "material"), ("note", "technical_requirements"), ("part", "parts_list")
])
def test_repair_cannot_change_manufacturing_fact_source(valid_context, tmp_path, monkeypatch, fact, payload_key):
    from cad_skill_core.schema.models import SourcedText

    spec = valid_context.spec.model_copy(deep=True)
    spec.requirements.material = SourcedText(text="Body", source_id="source")
    context = GenerationContext(spec, valid_context.standard, valid_context.font_result, tmp_path / "work")
    _force_report(monkeypatch, AuditReport((_issue("LAYOUT_COLLISION"),)))

    def corrupt_source(spec, *args, **kwargs):
        target = {
            "material": spec.requirements.material,
            "note": spec.requirements.technical_notes[0],
            "part": spec.parts_list[0],
        }[fact]
        target.source_id = "changed-source"

    monkeypatch.setattr("cad_skill_core.inspection.repair._apply_action", corrupt_source)
    with pytest.raises(EngineeringPayloadMutationError, match=payload_key):
        run_repair_loop(context)
    assert spec.requirements.material.source_id == "source"
    assert spec.requirements.technical_notes[0].source_id == "source"
    assert spec.parts_list[0].source_id == "source"
