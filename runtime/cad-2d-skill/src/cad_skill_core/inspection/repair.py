"""Bounded generation, inspection, preview, and safe repair orchestration."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import ezdxf
from fontTools.ttLib import TTFont

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import save_dxf_deterministic, write_dxf
from cad_skill_core.layout.annotations import apply_annotations
from cad_skill_core.layout.models import AnnotationLayout, Bounds, LayoutPlan
from cad_skill_core.layout.plan import plan_layout
from cad_skill_core.rendering.preview import PreviewError, PreviewResult, render_preview
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard

from .checks import inspect_drawing
from .models import (
    AuditIssue,
    AuditReport,
    GenerationContext,
    RepairAction,
    RepairResult,
)


_PRIORITY = {
    "REASSIGN_LAYER": 0,
    "SELECT_VERIFIED_FALLBACK_FONT": 1,
    "SHIFT_TEXT": 2,
    "INCREASE_DIMENSION_OFFSET": 3,
    "INCREASE_VIEW_GAP": 4,
}


class EngineeringPayloadMutationError(RuntimeError):
    """Raised immediately if any repair changes engineering intent."""


class UnsafeRepairError(ValueError):
    """Raised when an approved class lacks enough verified repair evidence."""


@dataclass(slots=True)
class _RepairState:
    standard: DrawingStandard
    fonts: dict[str, ResolvedFont]
    text_shifts: dict[str, float]
    layer_overrides: dict[str, str]


def plan_repairs(report: AuditReport) -> tuple[RepairAction, ...]:
    """Map only unambiguous, explicitly approved audit codes to repairs."""

    actions = tuple(
        action
        for issue in report.issues
        if issue.repairable
        if (action := _planned_action(issue)) is not None
    )
    return tuple(
        sorted(actions, key=lambda action: (_PRIORITY[action.code], action.target))
    )


def run_repair_loop(
    context: GenerationContext,
    max_iterations: int = 3,
) -> RepairResult:
    """Generate, audit, preview, and apply at most three safe repair rounds."""

    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    limit = min(max_iterations, 3)
    working_spec = context.spec.model_copy(deep=True)
    immutable = _engineering_payload(working_spec)
    state = _RepairState(
        context.standard.model_copy(deep=True),
        deepcopy(dict(context.fonts)),
        {},
        {},
    )
    iterations_root = context.task_work_dir / "iterations"
    iterations_root.mkdir(parents=True, exist_ok=True)
    last_result: RepairResult | None = None
    last_existing_dxf: Path | None = None

    for number in range(1, limit + 1):
        staging, final = _start_iteration(iterations_root, number)
        source_dxf = staging / "source.dxf"
        drawing_dxf = staging / "drawing.dxf"
        preview: PreviewResult | None = None
        plan: LayoutPlan | None = None
        try:
            _write_spec_snapshot(working_spec, staging / "drawing-spec.json")
            write_dxf(working_spec, state.standard, state.fonts, source_dxf)
            document = ezdxf.readfile(source_dxf)
            plan = _apply_plan_state(
                plan_layout(working_spec, state.standard), state.text_shifts
            )
            finalized = apply_annotations(
                document,
                plan,
                working_spec,
                state.standard,
                state.fonts,
            )
            _apply_layer_overrides(document, finalized, state.layer_overrides)
            save_dxf_deterministic(document, drawing_dxf)
            _require_validated_dxf(drawing_dxf)
            report = inspect_drawing(
                working_spec,
                drawing_dxf,
                finalized,
                state.standard,
                state.fonts,
            )
            try:
                preview = render_preview(
                    drawing_dxf,
                    staging / "drawing.svg",
                    staging / "drawing.png",
                    fonts=state.fonts,
                )
            except PreviewError as error:
                report = AuditReport(
                    (
                        *report.issues,
                        AuditIssue(
                            "PREVIEW_RENDER_FAILED",
                            "error",
                            "font",
                            f"preview rendering failed for {drawing_dxf}: {error}",
                            (),
                            False,
                        ),
                    )
                )
            else:
                report = _resolve_render_verification(report, preview)
            _write_audit(report, staging / "audit.json")
            _write_manifest(
                staging / "iteration.json",
                number,
                "audit-clean",
                "drawing.dxf",
                "passed" if report.passed else "failed",
            )
            _publish_iteration(staging, final)
        except EngineeringPayloadMutationError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as error:
            validated_name = _quarantine_or_validate(staging / "drawing.dxf")
            if validated_name is None:
                validated_name = _quarantine_or_validate(staging / "source.dxf")
            report = AuditReport(
                (
                    AuditIssue(
                        "REPAIR_ITERATION_FAILED",
                        "error",
                        "structure",
                        f"iteration {number:02d} failed in {staging}: "
                        f"{type(error).__name__}: {error}",
                        (),
                        False,
                    ),
                )
            )
            _write_audit(report, staging / "audit.json")
            if not (staging / "drawing-spec.json").exists():
                _write_spec_snapshot(working_spec, staging / "drawing-spec.json")
            _write_manifest(
                staging / "iteration.json",
                number,
                "audit-clean" if validated_name else "not-validated",
                validated_name,
                "failed",
            )
            _publish_iteration(staging, final)
            current_validated = final / validated_name if validated_name else None
            last_dxf = current_validated or last_existing_dxf
            return RepairResult(
                "failed",
                working_spec.model_copy(deep=True),
                number,
                last_dxf,
                report,
            )

        preview = _relocate_preview(preview, staging, final)
        last_existing_dxf = final / "drawing.dxf"
        last_result = RepairResult(
            "passed" if report.passed else "failed",
            working_spec.model_copy(deep=True),
            number,
            last_existing_dxf,
            report,
            plan=finalized,
            verified_roles=(preview.verified_roles if preview is not None else ()),
            evidence=(preview.evidence if preview is not None else ()),
        )
        if report.passed:
            return last_result
        if any(not issue.repairable for issue in report.issues if issue.severity != "info"):
            return last_result
        actions = plan_repairs(report)
        if not actions or number == limit:
            return last_result
        assert plan is not None
        finalized_document = ezdxf.readfile(final / "drawing.dxf")
        for action in actions:
            try:
                try:
                    _apply_action(
                        working_spec,
                        state,
                        action,
                        finalized,
                        finalized_document,
                    )
                finally:
                    _assert_engineering_payload(working_spec, immutable)
            except UnsafeRepairError:
                return last_result

    assert last_result is not None
    return last_result


def _engineering_payload(spec: DrawingSpec) -> dict[str, object]:
    return {
        "geometry": spec.geometry.model_dump(mode="json"),
        "dimensions": [item.model_dump(mode="json") for item in spec.dimensions],
        "material": (
            spec.requirements.material.model_dump(mode="json")
            if spec.requirements.material is not None
            else None
        ),
        "technical_requirements": [
            item.model_dump(mode="json")
            for item in spec.requirements.technical_notes
        ],
        "parts_list": [item.model_dump(mode="json") for item in spec.parts_list],
        "provenance": [item.model_dump(mode="json") for item in spec.provenance],
    }


def _assert_engineering_payload(
    spec: DrawingSpec,
    expected: dict[str, object],
) -> None:
    actual = _engineering_payload(spec)
    changed = tuple(key for key in expected if actual[key] != expected[key])
    if changed:
        raise EngineeringPayloadMutationError(
            f"repair changed immutable engineering payload: {', '.join(changed)}"
        )


def _start_iteration(root: Path, number: int) -> tuple[Path, Path]:
    final = root / f"{number:02d}"
    if final.exists():
        raise FileExistsError(f"iteration directory already exists: {final}")
    staging = Path(tempfile.mkdtemp(prefix=f".{number:02d}-", dir=root))
    return staging, final


def _publish_iteration(staging: Path, final: Path) -> None:
    os.replace(staging, final)


def _write_spec_snapshot(spec: DrawingSpec, path: Path) -> None:
    value = spec.model_dump(mode="json")
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_audit(report: AuditReport, path: Path) -> None:
    value = {
        "status": "passed" if report.passed else "failed",
        "issues": [asdict(issue) for issue in report.issues],
    }
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_manifest(
    path: Path,
    iteration: int,
    validation: str,
    validated_dxf: str | None,
    status: str,
) -> None:
    value = {
        "dxf_validation": validation,
        "iteration": iteration,
        "status": status,
        "validated_dxf": validated_dxf,
    }
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _require_validated_dxf(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("file is missing or empty")
        document = ezdxf.readfile(path)
        if document.audit().has_errors:
            raise ValueError("ezdxf audit reported errors")
    except Exception as error:
        raise ValueError(f"DXF is not reopen/audit-clean: {path}: {error}") from error


def _quarantine_or_validate(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        _require_validated_dxf(path)
    except ValueError:
        failed = path.with_name(path.name + ".failed")
        os.replace(path, failed)
        return None
    return path.name


def _relocate_preview(
    preview: PreviewResult | None,
    staging: Path,
    final: Path,
) -> PreviewResult | None:
    if preview is None:
        return None
    return replace(
        preview,
        svg_path=final / preview.svg_path.relative_to(staging),
        png_path=final / preview.png_path.relative_to(staging),
    )


def _resolve_render_verification(
    report: AuditReport,
    preview: PreviewResult,
) -> AuditReport:
    retained: list[AuditIssue] = []
    resolved: set[str] = set()
    verified = set(preview.verified_roles)
    for issue in report.issues:
        if issue.code != "FONT_RENDER_VERIFICATION_REQUIRED":
            retained.append(issue)
            continue
        match = re.search(r"verification:\s*(.+)$", issue.message)
        roles = set(part.strip() for part in match.group(1).split(",")) if match else set()
        if roles and roles <= verified:
            resolved.update(roles)
        else:
            retained.append(issue)
    if resolved:
        retained.append(
            AuditIssue(
                "FONT_RENDER_VERIFIED",
                "info",
                "font",
                "preview verified mapped roles without replacement markers or backend warnings: "
                + ", ".join(sorted(resolved)),
                (),
                False,
            )
        )
    return AuditReport(tuple(retained))


def _apply_plan_state(
    plan: LayoutPlan,
    text_shifts: dict[str, float],
) -> LayoutPlan:
    annotations = tuple(
        _shift_annotation(annotation, text_shifts.get(annotation.annotation_id, 0.0))
        for annotation in plan.annotations
    )
    return replace(plan, annotations=annotations)


def _shift_annotation(annotation: AnnotationLayout, distance: float) -> AnnotationLayout:
    if distance == 0.0:
        return annotation
    bounds = annotation.bounds
    shifted_bounds = Bounds(
        bounds.min_x + distance,
        bounds.min_y + distance,
        bounds.max_x + distance,
        bounds.max_y + distance,
    )
    points = tuple((x + distance, y + distance) for x, y in annotation.points)
    return replace(annotation, bounds=shifted_bounds, points=points)


def _verified_fallback_path(
    path: Path, candidate: str, resolved_runs: tuple[ResolvedFontRun, ...]
) -> bool:
    path = Path(path)
    if not path.is_file() or path.suffix.casefold() not in {".ttf", ".otf", ".ttc"}:
        return False
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
            identities = {path.name.casefold(), path.stem.casefold()}
            identities.update(
                record.toUnicode().strip().casefold()
                for record in font["name"].names
                if record.nameID in {1, 4, 6, 16}
            )
    except Exception:
        return False
    return candidate.casefold() in identities and all(
        character.isspace() or ord(character) in cmap
        for run in resolved_runs
        for character in run.text
    )


def _apply_action(
    spec: DrawingSpec,
    state: _RepairState,
    action: RepairAction,
    plan: LayoutPlan,
    document: ezdxf.document.Drawing,
) -> None:
    if action.code == "SELECT_VERIFIED_FALLBACK_FONT":
        resolved = state.fonts.get(action.target)
        candidate = resolved.fallbacks[0] if resolved and resolved.fallbacks else None
        candidate_path = (
            resolved.fallback_paths[0]
            if resolved and resolved.fallback_paths
            else None
        )
        if (
            resolved is None
            or resolved.requires_render_verification
            or resolved.missing_characters
            or candidate is None
            or candidate_path is None
            or len(resolved.fallback_paths) != len(resolved.fallbacks)
            or not resolved.resolved_runs
            or any(run.font != candidate for run in resolved.resolved_runs)
            or not _verified_fallback_path(
                candidate_path, candidate, resolved.resolved_runs
            )
        ):
            raise UnsafeRepairError(
                f"no verified fallback font is available for role {action.target}"
            )
        state.fonts[action.target] = replace(
            resolved,
            primary=candidate,
            primary_path=candidate_path,
            fallbacks=resolved.fallbacks[1:],
            fallback_paths=resolved.fallback_paths[1:],
        )
        return
    if action.code == "INCREASE_DIMENSION_OFFSET":
        increment = float(action.delta["increment_mm"])
        state.standard = state.standard.model_copy(
            update={
                "dimension": state.standard.dimension.model_copy(
                    update={"gap_mm": state.standard.dimension.gap_mm + increment}
                )
            },
            deep=True,
        )
        return
    if action.code == "INCREASE_VIEW_GAP":
        increment = float(action.delta["increment_mm"])
        state.standard = state.standard.model_copy(
            update={
                "spacing": state.standard.spacing.model_copy(
                    update={"view_gap_mm": state.standard.spacing.view_gap_mm + increment}
                )
            },
            deep=True,
        )
        return
    handles = set(action.target.split(","))
    if action.code == "SHIFT_TEXT":
        distance = float(action.delta["distance_mm"])
        for annotation in plan.annotations:
            if handles.intersection(annotation.entity_handles) and annotation.kind in {
                "title-label",
                "title",
                "drawing-number",
                "scale",
                "projection",
                "notes-header",
                "parts-header",
                "parts-column",
                "parts-row",
                "view-label",
                "technical-note",
                "annotation",
                "balloon-text",
            }:
                state.text_shifts[annotation.annotation_id] = (
                    state.text_shifts.get(annotation.annotation_id, 0.0) + distance
                )
        return
    if action.code == "REASSIGN_LAYER":
        annotation_by_handle = {
            handle: annotation
            for annotation in plan.annotations
            for handle in annotation.entity_handles
        }
        source_by_handle = {
            handle: index
            for view in plan.views
            for index, handle in zip(view.source_indices, view.source_handles, strict=True)
        }
        for handle in sorted(handles):
            if handle in annotation_by_handle:
                annotation = annotation_by_handle[handle]
                state.layer_overrides[
                    f"annotation:{annotation.annotation_id}"
                ] = annotation.layer
            elif handle in source_by_handle:
                index = source_by_handle[handle]
                state.layer_overrides[f"source:{index}"] = spec.geometry.entities[index].layer
        return
    raise UnsafeRepairError(f"unsupported repair action: {action.code}")


def _apply_layer_overrides(
    document: ezdxf.document.Drawing,
    plan: LayoutPlan,
    overrides: dict[str, str],
) -> None:
    by_handle = {str(entity.dxf.handle): entity for entity in document.modelspace()}
    for view in plan.views:
        for index, handle in zip(view.source_indices, view.source_handles, strict=True):
            layer = overrides.get(f"source:{index}")
            if layer is not None and handle in by_handle:
                by_handle[handle].dxf.layer = layer
    for annotation in plan.annotations:
        layer = overrides.get(f"annotation:{annotation.annotation_id}")
        if layer is None:
            continue
        for handle in annotation.entity_handles:
            if handle in by_handle:
                by_handle[handle].dxf.layer = layer


def _planned_action(issue: AuditIssue) -> RepairAction | None:
    target = ",".join(issue.entity_handles)
    if issue.code == "STANDARD_ENTITY_LAYER" and target:
        return RepairAction("REASSIGN_LAYER", target, {"strategy": "entity-kind"})
    if issue.code == "FONT_MISSING_SELECTION":
        match = re.search(r"primary font:\s*(TEXT|DIM)\s*$", issue.message)
        if match:
            return RepairAction("SELECT_VERIFIED_FALLBACK_FONT", match.group(1), {})
    if issue.code == "LAYOUT_COLLISION" and target:
        return RepairAction("SHIFT_TEXT", target, {"distance_mm": 1.0})
    if (
        issue.code == "LAYOUT_VIEW_COLLISION"
        and target
        and re.search(r":\s*dimension-[^\s]+\s*$", issue.message)
    ):
        return RepairAction(
            "INCREASE_DIMENSION_OFFSET", target, {"increment_mm": 1.0}
        )
    if issue.code == "LAYOUT_VIEW_SPACING":
        match = re.search(r"views\s+(\S+)\s+and\s+(\S+)\s+", issue.message)
        if match:
            return RepairAction(
                "INCREASE_VIEW_GAP",
                ",".join(sorted(match.groups())),
                {"increment_mm": 1.0},
            )
    return None
