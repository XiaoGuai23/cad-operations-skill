"""Read-only structural, standard, semantic, layout, and font inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose, isfinite
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf import bbox, units

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.geometry.dxf_writer import GenerationResult
from cad_skill_core.layout.material_binding import resolve_material_handles
from cad_skill_core.layout.models import AnnotationLayout, Bounds, GeometryBounds, LayoutPlan
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard

from .models import AuditIssue, AuditReport


_LINETYPE_PATTERNS = {
    "CENTER": (1.25, -0.25, 0.25, -0.25),
    "HIDDEN": (0.5, -0.5),
}
_SYSTEM_LAYERS = {"0", "DEFPOINTS"}
_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class _MeasuredEntity:
    entity: object
    bounds: tuple[float, float, float, float] | None
    annotation: AnnotationLayout | None
    source_index: int | None

    @property
    def handle(self) -> str | None:
        return _handle(self.entity)


def inspect_drawing(
    spec: DrawingSpec,
    dxf_path: Path,
    plan: LayoutPlan,
    standard: DrawingStandard,
    font_result: ResolvedFont | Mapping[str, ResolvedFont] | GenerationResult,
) -> AuditReport:
    """Reopen and inspect one finalized DXF without mutating caller inputs."""

    path_label = str(dxf_path)
    try:
        path = Path(dxf_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        document = ezdxf.readfile(path)
    except Exception as error:
        detail = str(error).strip() or type(error).__name__
        return AuditReport(
            (
                AuditIssue(
                    "STRUCTURE_DXF_OPEN",
                    "error",
                    "structure",
                    f"could not inspect DXF path {path_label}: {detail}",
                    (),
                    False,
                ),
            )
        )

    issues: list[AuditIssue] = []
    issues.extend(_structural_issues(document, path))
    issues.extend(_standard_issues(document, spec, plan, standard, font_result))
    measured = _measure_entities(document, spec, plan)
    issues.extend(_semantic_issues(document, spec, plan, measured))
    issues.extend(_layout_issues(spec, plan, standard, measured))
    issues.extend(_font_issues(document, font_result))
    return AuditReport(tuple(issues))


def _structural_issues(
    document: ezdxf.document.Drawing,
    dxf_path: Path,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    try:
        auditor = document.audit()
    except Exception as error:
        return [
            AuditIssue(
                "STRUCTURE_AUDIT_FAILED",
                "error",
                "structure",
                f"ezdxf Auditor could not complete for {dxf_path}: {type(error).__name__}: {error}",
                (),
                False,
            )
        ]
    for finding in auditor.errors:
        issues.append(
            AuditIssue(
                "STRUCTURE_AUDIT_ERROR",
                "error",
                "structure",
                f"ezdxf Auditor error {int(finding.code)}: {finding.message}",
                _handles(getattr(finding, "entity", None)),
                False,
            )
        )
    for finding in auditor.fixes:
        issues.append(
            AuditIssue(
                "STRUCTURE_AUDIT_FIX",
                "warning",
                "structure",
                f"ezdxf Auditor repaired issue {int(finding.code)} while inspecting: {finding.message}",
                _handles(getattr(finding, "entity", None)),
                True,
            )
        )
    return issues


def _standard_issues(
    document: ezdxf.document.Drawing,
    spec: DrawingSpec,
    plan: LayoutPlan,
    standard: DrawingStandard,
    font_result: ResolvedFont | Mapping[str, ResolvedFont] | GenerationResult,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    expected_layers = standard.layers
    actual_layer_names = {layer.dxf.name for layer in document.layers}
    for name in sorted(expected_layers):
        if name not in actual_layer_names:
            issues.append(_standard_issue("STANDARD_MISSING_LAYER", f"missing required layer: {name}"))
            continue
        actual = document.layers.get(name)
        expected = expected_layers[name]
        if actual.color != expected.color:
            issues.append(_standard_issue("STANDARD_LAYER_COLOR", f"layer color differs from profile: {name}"))
        if actual.dxf.linetype.casefold() != expected.linetype.casefold():
            issues.append(_standard_issue("STANDARD_LAYER_LINETYPE", f"layer linetype differs from profile: {name}"))
        if actual.dxf.lineweight != round(expected.lineweight_mm * 100):
            issues.append(_standard_issue("STANDARD_LAYER_LINEWEIGHT", f"layer lineweight differs from profile: {name}"))

    expected_layer_names = {name.casefold() for name in expected_layers}
    system_layer_names = {name.casefold() for name in _SYSTEM_LAYERS}
    for name in sorted(actual_layer_names):
        if name.casefold() not in expected_layer_names | system_layer_names:
            issues.append(
                _standard_issue(
                    "STANDARD_UNALLOWED_LAYER",
                    f"layer is outside the allowed profile: {name}",
                )
            )

    allowed = {name.casefold() for name in expected_layers} | {
        name.casefold() for name in _SYSTEM_LAYERS
    }
    for entity in document.modelspace():
        layer = entity.dxf.get("layer", "0")
        if layer.casefold() not in allowed:
            issues.append(
                _standard_issue(
                    "STANDARD_ENTITY_LAYER",
                    f"entity uses a layer outside the profile: {layer}",
                    _handles(entity),
                )
            )
        if entity.dxftype() in {"TEXT", "MTEXT"}:
            style = entity.dxf.get("style", "Standard")
            if style.casefold() not in {
                name.casefold() for name in standard.text_styles
            }:
                issues.append(
                    _standard_issue(
                        "STANDARD_ENTITY_TEXT_STYLE",
                        f"text entity uses a style outside the profile: {style}",
                        _handles(entity),
                    )
                )
        if entity.dxftype() == "DIMENSION":
            dimstyle = entity.dxf.get("dimstyle", "Standard")
            if dimstyle.casefold() != "dim":
                issues.append(
                    _standard_issue(
                        "STANDARD_ENTITY_DIM_STYLE",
                        f"dimension entity uses the wrong profile style: {dimstyle}",
                        _handles(entity),
                    )
                )

    required_linetypes = sorted(
        {
            style.linetype
            for style in expected_layers.values()
            if style.linetype.casefold() != "continuous"
        }
    )
    for name in required_linetypes:
        if name not in document.linetypes:
            issues.append(_standard_issue("STANDARD_MISSING_LINETYPE", f"missing required linetype: {name}"))
            continue
        expected_pattern = _normalized_pattern(_LINETYPE_PATTERNS.get(name, (0.5, -0.25)))
        line_type = document.linetypes.get(name)
        actual_pattern = _normalized_pattern(
            tuple(tag.value for tag in line_type.pattern_tags.tags if tag.code == 49)
        )
        if actual_pattern != expected_pattern:
            issues.append(
                _standard_issue(
                    "STANDARD_LINETYPE_PATTERN",
                    f"linetype dash pattern differs from the generation contract: {name}",
                )
            )

    fonts = _font_mapping(font_result)
    text_font = _selected_font_name(fonts.get("TEXT"))
    dim_font = _selected_font_name(fonts.get("DIM"))
    for name, expected in sorted(standard.text_styles.items()):
        if name not in document.styles:
            issues.append(_standard_issue("STANDARD_MISSING_TEXT_STYLE", f"missing required text style: {name}"))
            continue
        actual = document.styles.get(name)
        expected_font = text_font if expected.font == "project-default" and text_font else expected.font
        mismatches = []
        if expected_font != "project-default" and actual.dxf.font.casefold() != expected_font.casefold():
            mismatches.append("font")
        if not isclose(actual.dxf.height, expected.height_mm, abs_tol=1e-9):
            mismatches.append("height")
        if not isclose(actual.dxf.width, expected.width_factor, abs_tol=1e-9):
            mismatches.append("width")
        if mismatches:
            issues.append(
                _standard_issue(
                    "STANDARD_TEXT_STYLE",
                    f"text style {name} differs in {', '.join(mismatches)}",
                )
            )

    if "DIM" not in document.styles:
        issues.append(_standard_issue("STANDARD_MISSING_TEXT_STYLE", "missing required text style: DIM"))
    else:
        style = document.styles.get("DIM")
        mismatches = []
        if dim_font and style.dxf.font.casefold() != dim_font.casefold():
            mismatches.append("font")
        if not isclose(style.dxf.height, 0.0, abs_tol=1e-9):
            mismatches.append("height")
        if not isclose(style.dxf.width, 1.0, abs_tol=1e-9):
            mismatches.append("width")
        if mismatches:
            issues.append(_standard_issue("STANDARD_TEXT_STYLE", f"text style DIM differs in {', '.join(mismatches)}"))

    if "DIM" not in document.dimstyles:
        issues.append(_standard_issue("STANDARD_MISSING_DIM_STYLE", "missing required dimension style: DIM"))
    else:
        dimstyle = document.dimstyles.get("DIM")
        expected_fields = (
            ("dimtxsty", dimstyle.dxf.dimtxsty, "DIM"),
            ("dimtxt", dimstyle.dxf.dimtxt, standard.dimension.text_height_mm),
            ("dimgap", dimstyle.dxf.dimgap, standard.dimension.gap_mm),
            ("dimasz", dimstyle.dxf.dimasz, standard.dimension.arrow_size_mm),
            ("dimtad", dimstyle.dxf.dimtad, 0),
        )
        mismatches = [
            field
            for field, actual, expected in expected_fields
            if not _equal_resource_value(actual, expected)
        ]
        if mismatches:
            issues.append(_standard_issue("STANDARD_DIM_STYLE", f"dimension style DIM differs in {', '.join(mismatches)}"))

    expected_width, expected_height = _sheet_dimensions(spec, standard)
    expected_printable = (10.0, 10.0, expected_width - 10.0, expected_height - 10.0)
    expected_title = _protected_zone_tuple(
        expected_printable,
        standard.protected_zones["title_block"],
    )
    sheet_contract_ok = (
        isclose(plan.sheet_bounds.min_x, 0.0, abs_tol=1e-9)
        and isclose(plan.sheet_bounds.min_y, 0.0, abs_tol=1e-9)
        and isclose(plan.sheet_bounds.max_x, expected_width, abs_tol=1e-9)
        and isclose(plan.sheet_bounds.max_y, expected_height, abs_tol=1e-9)
        and _same_bounds(_bounds_tuple(plan.printable_bounds), expected_printable)
        and _same_bounds(_bounds_tuple(plan.title_block_bounds), expected_title)
        and plan.sheet_bounds.contains(plan.printable_bounds)
        and spec.standard.family == standard.family
        and spec.standard.units == standard.units
        and spec.standard.projection == standard.projection
        and document.header.get("$INSUNITS", 0) == units.MM
    )
    expected_layer_contract = tuple(
        (name, style.linetype, style.lineweight_mm, style.color)
        for name, style in sorted(standard.layers.items())
    )
    expected_text_contract = tuple(
        (name, style.font, style.height_mm, style.width_factor)
        for name, style in sorted(standard.text_styles.items())
    )
    if not sheet_contract_ok:
        issues.append(_standard_issue("STANDARD_SHEET_CONTRACT", "sheet size, units, orientation, or profile identity differs from the specification"))
    if plan.layer_styles != expected_layer_contract or plan.text_styles != expected_text_contract:
        issues.append(_standard_issue("STANDARD_PLAN_CONTRACT", "finalized layout resource contract differs from the drawing profile"))
    if not (
        isclose(plan.dimension_text_height_mm, standard.dimension.text_height_mm, abs_tol=1e-9)
        and isclose(plan.dimension_gap_mm, standard.dimension.gap_mm, abs_tol=1e-9)
        and isclose(plan.dimension_arrow_size_mm, standard.dimension.arrow_size_mm, abs_tol=1e-9)
    ):
        issues.append(_standard_issue("STANDARD_PLAN_CONTRACT", "finalized layout dimension contract differs from the drawing profile"))
    return issues


def _semantic_issues(
    document: ezdxf.document.Drawing,
    spec: DrawingSpec,
    plan: LayoutPlan,
    measured: tuple[_MeasuredEntity, ...],
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    planned_views = {view.name: view for view in plan.views}
    measured_by_handle = {
        item.handle: item for item in measured if item.handle is not None
    }
    annotation_entities = _annotation_entity_map(measured)
    for name in sorted(set(spec.requirements.views)):
        view = planned_views.get(name)
        label = annotation_entities.get(f"view-label-{name}")
        source_group = (
            [measured_by_handle.get(handle) for handle in view.source_handles]
            if view is not None
            else []
        )
        source_bounds = _union(
            item.bounds
            for item in source_group
            if item is not None and item.bounds is not None
        )
        has_geometry = (
            view is not None
            and bool(view.source_indices)
            and len(view.source_handles) == len(view.source_indices)
            and all(item is not None and item.bounds is not None for item in source_group)
            and source_bounds is not None
            and (
                source_bounds[2] - source_bounds[0] > _TOLERANCE
                or source_bounds[3] - source_bounds[1] > _TOLERANCE
            )
        )
        has_label = (
            view is not None
            and label is not None
            and _entity_text(label.entity) == view.label
        )
        if not has_geometry or not has_label:
            stored_handles = tuple(view.source_handles) if view is not None else ()
            label_handles = (
                _handles(label.entity)
                if label is not None
                else _annotation_handles(plan, f"view-label-{name}")
            )
            issues.append(
                AuditIssue(
                    "SEMANTIC_MISSING_VIEW",
                    "error",
                    "semantic",
                    f"requested view is not represented by the finalized plan and DXF: {name}",
                    (*stored_handles, *label_handles),
                    False,
                )
            )

    provenance_ids = {source.id for source in spec.provenance}
    for index, dimension in enumerate(spec.dimensions, start=1):
        annotation_id = f"dimension-{index}"
        measured_dimension = annotation_entities.get(annotation_id)
        handles = (
            _handles(measured_dimension.entity)
            if measured_dimension
            else _annotation_handles(plan, annotation_id)
        )
        if dimension.source_id not in provenance_ids:
            issues.append(
                AuditIssue(
                    "SEMANTIC_DIMENSION_PROVENANCE",
                    "error",
                    "semantic",
                    f"dimension {index} source is not resolvable: {dimension.source_id}",
                    handles,
                    False,
                )
            )
        planned_dimension = next(
            (
                annotation
                for annotation in plan.annotations
                if annotation.annotation_id == annotation_id
            ),
            None,
        )
        owner_view = (
            planned_views.get(planned_dimension.owner_view)
            if planned_dimension is not None and planned_dimension.owner_view is not None
            else None
        )
        if (
            measured_dimension is None
            or measured_dimension.entity.dxftype() != "DIMENSION"
            or owner_view is None
            or not _dimension_matches(
                measured_dimension.entity,
                dimension.value,
                owner_view.scale,
            )
        ):
            issues.append(
                AuditIssue(
                    "SEMANTIC_DIMENSION_VALUE",
                    "error",
                    "semantic",
                    f"dimension {index} value is not represented as {dimension.value:g}",
                    handles,
                    False,
                )
            )

    drawing_text = "\n".join(
        text for text in (_entity_text(item.entity) for item in measured) if text
    ).casefold()
    material = spec.requirements.material
    if material and material.source_id not in provenance_ids:
        issues.append(
            AuditIssue(
                "SEMANTIC_MATERIAL_PROVENANCE",
                "error",
                "semantic",
                f"material source is not resolvable: {material.source_id}",
                (),
                False,
            )
        )
    material_handles = (
        resolve_material_handles(document, spec, plan)
        if material and material.source_id in provenance_ids
        else ()
    )
    if material and material.source_id in provenance_ids and not material_handles:
        issues.append(
            AuditIssue(
                "SEMANTIC_MISSING_MATERIAL",
                "error",
                "semantic",
                "requested material has no source-bound declared technical note or "
                f"annotation: {material.text}",
                (),
                False,
            )
        )
    for index, note in enumerate(spec.requirements.technical_notes, start=1):
        if note.source_id not in provenance_ids:
            issues.append(
                AuditIssue(
                    "SEMANTIC_TECHNICAL_REQUIREMENT_PROVENANCE",
                    "error",
                    "semantic",
                    f"technical requirement {index} source is not resolvable: "
                    f"{note.source_id}",
                    (),
                    False,
                )
            )
        if note.text.casefold() not in drawing_text:
            issues.append(
                AuditIssue(
                    "SEMANTIC_MISSING_TECHNICAL_REQUIREMENT",
                    "error",
                    "semantic",
                    f"technical requirement {index} is not represented: {note.text}",
                    (),
                    False,
                )
            )

    if spec.project.drawing_type == "assembly":
        issues.extend(_assembly_issues(spec, plan, annotation_entities))
    return issues


def _assembly_issues(
    spec: DrawingSpec,
    plan: LayoutPlan,
    annotation_entities: Mapping[str, _MeasuredEntity],
) -> list[AuditIssue]:
    expected_rows = tuple(
        (number, item.item, item.quantity)
        for number, item in enumerate(spec.parts_list, start=1)
    )
    actual_rows = tuple((row.item_no, row.item, row.quantity) for row in plan.parts_list.rows)
    provenance_ids = {source.id for source in spec.provenance}
    bad_handles: set[str] = set()
    inconsistent = actual_rows != expected_rows
    unresolved = tuple(
        (item_no, item.source_id)
        for item_no, item in enumerate(spec.parts_list, start=1)
        if item.source_id not in provenance_ids
    )
    if unresolved:
        return [
            AuditIssue(
                "SEMANTIC_ASSEMBLY_PROVENANCE",
                "error",
                "semantic",
                "assembly quantity sources are not resolvable: "
                + ", ".join(
                    f"item {item_no}={source_id}"
                    for item_no, source_id in unresolved
                ),
                (),
                False,
            )
        ]
    for item_no, item, quantity in expected_rows:
        expectations = {
            f"parts-{item_no}-item": ("TEXT", str(item_no)),
            f"parts-{item_no}-description": ("TEXT", item),
            f"parts-{item_no}-qty": ("TEXT", str(quantity)),
            f"balloon-{item_no}-circle": ("CIRCLE", None),
            f"balloon-{item_no}-text": ("TEXT", str(item_no)),
            f"balloon-{item_no}-leader": ("LEADER", None),
        }
        for annotation_id, (entity_type, text) in expectations.items():
            measured = annotation_entities.get(annotation_id)
            matches = measured is not None and measured.entity.dxftype() == entity_type
            if matches and text is not None:
                matches = _entity_text(measured.entity) == text
            if not matches:
                inconsistent = True
                if measured and measured.handle:
                    bad_handles.add(measured.handle)
                else:
                    bad_handles.update(_annotation_handles(plan, annotation_id))
    expected_balloon_ids = {
        f"balloon-{item_no}-{suffix}"
        for item_no, _, _ in expected_rows
        for suffix in ("circle", "text", "leader")
    }
    for annotation_id, measured in annotation_entities.items():
        if annotation_id.startswith("balloon-") and annotation_id not in expected_balloon_ids:
            inconsistent = True
            if measured.handle:
                bad_handles.add(measured.handle)
    if not inconsistent:
        return []
    return [
        AuditIssue(
            "SEMANTIC_ASSEMBLY_CONSISTENCY",
            "error",
            "semantic",
            "assembly item numbers, quantities, parts-table rows, and balloons are inconsistent",
            tuple(bad_handles),
            False,
        )
    ]


def _layout_issues(
    spec: DrawingSpec,
    plan: LayoutPlan,
    standard: DrawingStandard,
    measured: tuple[_MeasuredEntity, ...],
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    source_count = len(spec.geometry.entities)
    source_by_index = {
        item.source_index: item
        for item in measured
        if item.source_index is not None
    }
    source_entities = tuple(source_by_index.values())
    measured_by_handle = {
        item.handle: item for item in measured if item.handle is not None
    }
    actual_handles = set(measured_by_handle)
    protected = (
        plan.title_block_bounds,
        plan.technical_note_bounds,
        plan.parts_list.bounds,
    )

    expected_assignments = _expected_view_assignments(spec, plan)
    actual_assignments = {view.name: view.source_indices for view in plan.views}
    flat_indices = tuple(
        index for view in plan.views for index in view.source_indices
    )
    if (
        actual_assignments != expected_assignments
        or sorted(flat_indices) != list(range(source_count))
        or len(flat_indices) != len(set(flat_indices))
    ):
        issues.append(
            AuditIssue(
                "LAYOUT_VIEW_ASSIGNMENT",
                "error",
                "layout",
                "finalized view entity indices differ from the specification's total nonoverlapping partition",
                _measured_handles(source_entities),
                False,
            )
        )

    for annotation in plan.annotations:
        expected_handles = annotation.entity_handles or (annotation.annotation_id,)
        missing_handles = tuple(
            handle for handle in expected_handles if handle not in actual_handles
        )
        if missing_handles:
            issues.append(
                AuditIssue(
                    "LAYOUT_MISSING_ENTITY",
                    "error",
                    "layout",
                    f"finalized annotation entity is missing: {annotation.annotation_id}",
                    missing_handles,
                    False,
                )
            )

    for index, view in enumerate(plan.views):
        expected_handles = view.source_handles or ((view.name,) if view.source_indices else ())
        missing_handles = tuple(
            handle for handle in expected_handles if handle not in actual_handles
        )
        if missing_handles:
            issues.append(
                AuditIssue(
                    "LAYOUT_MISSING_ENTITY",
                    "error",
                    "layout",
                    f"finalized source entity is missing for view {view.name}",
                    missing_handles,
                    False,
                )
            )
        for other in plan.views[index + 1 :]:
            clearance = view.bounds.clearance_to(other.bounds)
            if clearance + _TOLERANCE < standard.spacing.view_gap_mm:
                issues.append(
                    AuditIssue(
                        "LAYOUT_VIEW_SPACING",
                        "error",
                        "layout",
                        (
                            f"views {view.name} and {other.name} have {clearance:g} mm "
                            f"clearance; profile requires {standard.spacing.view_gap_mm:g} mm"
                        ),
                        (*view.source_handles, *other.source_handles),
                        True,
                    )
                )

    for view in plan.views:
        group = [
            measured_by_handle[handle]
            for handle in view.source_handles
            if handle in measured_by_handle
        ]
        if len(group) != len(view.source_indices) or any(item.bounds is None for item in group):
            issues.append(
                AuditIssue(
                    "LAYOUT_VIEW_BOUNDS",
                    "error",
                    "layout",
                    f"reopened source entities for view {view.name} are missing or unmeasurable",
                    _measured_handles(group),
                    True,
                )
            )
            continue
        if not group:
            continue
        group_bounds = _union(item.bounds for item in group if item.bounds is not None)
        plan_geometry = _geometry_tuple(view.geometry_bounds)
        if (
            group_bounds is None
            or plan_geometry is None
            or not _contains(plan_geometry, group_bounds)
            or not _contains(_bounds_tuple(view.bounds), group_bounds)
        ):
            issues.append(
                AuditIssue(
                    "LAYOUT_VIEW_BOUNDS",
                    "error",
                    "layout",
                    f"reopened source bounds escaped finalized view {view.name}",
                    _measured_handles(group),
                    True,
                )
            )

    for item in measured:
        if item.bounds is None:
            issues.append(
                AuditIssue(
                    "LAYOUT_UNMEASURABLE_ENTITY",
                    "error",
                    "layout",
                    "DXF entity bounds could not be measured",
                    _handles(item.entity),
                    False,
                )
            )
            continue
        if not _contains(_bounds_tuple(plan.sheet_bounds), item.bounds):
            issues.append(
                AuditIssue(
                    "LAYOUT_SHEET_OVERFLOW",
                    "error",
                    "layout",
                    "entity bounds overflow the sheet",
                    _handles(item.entity),
                    True,
                )
            )
        if not _contains(_bounds_tuple(plan.printable_bounds), item.bounds):
            issues.append(
                AuditIssue(
                    "LAYOUT_PRINTABLE_BOUNDS",
                    "error",
                    "layout",
                    "entity bounds overflow the printable sheet area",
                    _handles(item.entity),
                    True,
                )
            )

        annotation = item.annotation
        if item.source_index is not None:
            if any(_intersects(item.bounds, _bounds_tuple(zone)) for zone in protected):
                issues.append(
                    AuditIssue(
                        "LAYOUT_PROTECTED_ZONE",
                        "error",
                        "layout",
                        "source geometry entered a protected zone",
                        _handles(item.entity),
                        True,
                    )
                )
            continue
        if annotation is None:
            issues.append(
                AuditIssue(
                    "LAYOUT_UNPLANNED_ENTITY",
                    "error",
                    "layout",
                    "DXF entity is not owned by source geometry or a finalized annotation group",
                    _handles(item.entity),
                    False,
                )
            )
            if any(_intersects(item.bounds, _bounds_tuple(zone)) for zone in protected):
                issues.append(
                    AuditIssue(
                        "LAYOUT_PROTECTED_ZONE",
                        "error",
                        "layout",
                        "unplanned entity entered a protected zone",
                        _handles(item.entity),
                        True,
                    )
                )
            continue
        if not _contains(_bounds_tuple(annotation.bounds), item.bounds):
            issues.append(
                AuditIssue(
                    "LAYOUT_ANNOTATION_BOUNDS",
                    "error",
                    "layout",
                    f"reopened annotation escaped its finalized bounds: {annotation.annotation_id}",
                    _handles(item.entity),
                    True,
                )
            )
        owner_zone = _owner_zone(plan, annotation.owner_zone)
        if owner_zone is not None:
            if not _contains(_bounds_tuple(owner_zone), item.bounds):
                issues.append(
                    AuditIssue(
                        "LAYOUT_PROTECTED_ZONE",
                        "error",
                        "layout",
                        f"annotation escaped its protected owner zone: {annotation.annotation_id}",
                        _handles(item.entity),
                        True,
                    )
                )
        elif any(_intersects(item.bounds, _bounds_tuple(zone)) for zone in protected):
            issues.append(
                AuditIssue(
                    "LAYOUT_PROTECTED_ZONE",
                    "error",
                    "layout",
                    f"annotation entered a protected zone: {annotation.annotation_id}",
                    _handles(item.entity),
                    True,
                )
            )
        if annotation.owner_zone is None:
            for view in plan.views:
                if not _intersects(item.bounds, _bounds_tuple(view.bounds)):
                    continue
                if (
                    annotation.kind == "dimension"
                    and annotation.owner_view == view.name
                    and _shallow_edge_overlap(
                        item.bounds,
                        _bounds_tuple(view.bounds),
                        plan.dimension_arrow_size_mm,
                    )
                ):
                    continue
                issues.append(
                    AuditIssue(
                        "LAYOUT_VIEW_COLLISION",
                        "error",
                        "layout",
                        f"annotation collided with view {view.name}: {annotation.annotation_id}",
                        _handles(item.entity),
                        True,
                    )
                )

    annotation_items = [
        item
        for item in measured
        if item.annotation is not None and item.bounds is not None
    ]
    for index, item in enumerate(annotation_items):
        for other in annotation_items[index + 1 :]:
            if not _intersects(item.bounds, other.bounds):
                continue
            if _allowed_pair(item.annotation, other.annotation):
                continue
            issues.append(
                AuditIssue(
                    "LAYOUT_COLLISION",
                    "error",
                    "layout",
                    "measured annotation bounds collide",
                    _handles(item.entity, other.entity),
                    True,
                )
            )
    return issues


def _font_issues(
    document: ezdxf.document.Drawing,
    font_result: ResolvedFont | Mapping[str, ResolvedFont] | GenerationResult,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    fonts = _font_mapping(font_result)
    role_entities: dict[str, list[object]] = {"TEXT": [], "DIM": []}
    for entity in document.modelspace():
        if entity.dxftype() == "DIMENSION":
            role_entities["DIM"].append(entity)
        elif entity.dxftype() in {"TEXT", "MTEXT"}:
            role = "DIM" if entity.dxf.get("style", "").casefold() == "dim" else "TEXT"
            role_entities[role].append(entity)
    used_roles = {role for role, entities in role_entities.items() if entities}
    role_handles = {
        role: _handles(*entities) for role, entities in role_entities.items()
    }
    for role in sorted(used_roles):
        resolved = fonts.get(role)
        if resolved is None:
            issues.append(
                AuditIssue(
                    "FONT_MISSING_ROLE",
                    "error",
                    "font",
                    f"drawing uses text role without explicit ResolvedFont evidence: {role}",
                    role_handles[role],
                    True,
                )
            )
            continue
        if resolved.primary is None:
            issues.append(
                AuditIssue(
                    "FONT_MISSING_SELECTION",
                    "error",
                    "font",
                    f"font role has no selected primary font: {role}",
                    role_handles[role],
                    True,
                )
            )
        if resolved.missing_characters:
            issues.append(
                AuditIssue(
                    "FONT_MISSING_GLYPH",
                    "error",
                    "font",
                    f"font role {role} is missing glyphs: {''.join(resolved.missing_characters)}",
                    role_handles[role],
                    True,
                )
            )

    render_roles = {
        role
        for role, resolved in fonts.items()
        if resolved.requires_render_verification
    }
    if isinstance(font_result, GenerationResult):
        render_roles.update(font_result.roles_requiring_render_verification)
    metadata_roles = document.ezdxf_metadata().get(
        "LAYOUT_FONT_ROLES_REQUIRING_RENDER_VERIFICATION", ""
    )
    render_roles.update(role for role in metadata_roles.split(",") if role)
    if render_roles:
        roles = ", ".join(sorted(render_roles))
        handles = tuple(
            handle
            for role in sorted(render_roles)
            for handle in role_handles.get(role, ())
        )
        issues.append(
            AuditIssue(
                "FONT_RENDER_VERIFICATION_REQUIRED",
                "warning",
                "font",
                f"font roles still require rendered preview verification: {roles}",
                handles,
                True,
            )
        )
    return issues


def _measure_entities(
    document: ezdxf.document.Drawing,
    spec: DrawingSpec,
    plan: LayoutPlan,
) -> tuple[_MeasuredEntity, ...]:
    del spec  # correlation is owned by the finalized plan, not entity order
    annotation_by_handle = {
        handle: annotation
        for annotation in plan.annotations
        for handle in annotation.entity_handles
    }
    source_index_by_handle = {
        handle: index
        for view in plan.views
        for index, handle in zip(view.source_indices, view.source_handles)
    }
    return tuple(
        _MeasuredEntity(
            entity,
            _entity_bounds(entity),
            annotation_by_handle.get(str(entity.dxf.handle)),
            source_index_by_handle.get(str(entity.dxf.handle)),
        )
        for entity in document.modelspace()
    )


def _annotation_entity_map(
    measured: tuple[_MeasuredEntity, ...],
) -> dict[str, _MeasuredEntity]:
    return {
        item.annotation.annotation_id: item
        for item in measured
        if item.annotation is not None
    }


def _entity_bounds(entity: object) -> tuple[float, float, float, float] | None:
    try:
        extents = bbox.extents([entity])
    except Exception:
        return None
    if not extents.has_data:
        return None
    values = (
        float(extents.extmin.x),
        float(extents.extmin.y),
        float(extents.extmax.x),
        float(extents.extmax.y),
    )
    return values if all(isfinite(value) for value in values) else None


def _entity_text(entity: object) -> str:
    if entity is None:
        return ""
    if entity.dxftype() == "TEXT":
        return entity.dxf.get("text", "")
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            return entity.dxf.get("text", "")
    return ""


def _dimension_matches(entity: object, expected: float, view_scale: float) -> bool:
    """Compare engineering geometry, never the editable display override.

    ezdxf reports the finalized sheet-space measurement. Dividing by the
    finalized uniform view scale returns the DrawingSpec engineering value.
    The tolerance is 1e-6 relative or 1e-6 mm absolute in engineering space.
    """

    if not isfinite(view_scale) or view_scale <= 0.0:
        return False
    try:
        engineering_value = float(entity.get_measurement()) / view_scale
        return isclose(
            engineering_value,
            expected,
            rel_tol=1e-6,
            abs_tol=1e-6,
        )
    except Exception:
        return False


def _annotation_handles(plan: LayoutPlan, annotation_id: str) -> tuple[str, ...]:
    annotation = next(
        (
            annotation
            for annotation in plan.annotations
            if annotation.annotation_id == annotation_id
        ),
        None,
    )
    if annotation is None:
        return (annotation_id,)
    return annotation.entity_handles or (annotation.annotation_id,)


def _font_mapping(
    font_result: ResolvedFont | Mapping[str, ResolvedFont] | GenerationResult,
) -> dict[str, ResolvedFont]:
    if isinstance(font_result, ResolvedFont):
        return {"TEXT": font_result, "DIM": font_result}
    if isinstance(font_result, GenerationResult):
        return {}
    if isinstance(font_result, Mapping):
        return {
            str(role): resolved
            for role, resolved in font_result.items()
            if isinstance(resolved, ResolvedFont)
        }
    return {}


def _selected_font_name(resolved: ResolvedFont | None) -> str | None:
    """Return the concrete DXF style value written by Tasks 5 and 6."""

    if resolved is None:
        return None
    if resolved.primary_path is not None:
        return resolved.primary_path.name
    return resolved.primary


def _standard_issue(
    code: str,
    message: str,
    handles: tuple[str, ...] = (),
) -> AuditIssue:
    return AuditIssue(code, "error", "standard", message, handles, True)


def _sheet_dimensions(
    spec: DrawingSpec, standard: DrawingStandard
) -> tuple[float, float]:
    sheet = standard.sheets[spec.sheet.size]
    if spec.sheet.orientation == "portrait":
        return sheet.height_mm, sheet.width_mm
    return sheet.width_mm, sheet.height_mm


def _protected_zone_tuple(
    printable: tuple[float, float, float, float],
    zone: object,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = printable
    margin = zone.margin_mm
    if zone.anchor == "bottom-right":
        return (
            max_x - margin - zone.width_mm,
            min_y + margin,
            max_x - margin,
            min_y + margin + zone.height_mm,
        )
    if zone.anchor == "bottom-left":
        return (
            min_x + margin,
            min_y + margin,
            min_x + margin + zone.width_mm,
            min_y + margin + zone.height_mm,
        )
    if zone.anchor == "top-right":
        return (
            max_x - margin - zone.width_mm,
            max_y - margin - zone.height_mm,
            max_x - margin,
            max_y - margin,
        )
    return (
        min_x + margin,
        max_y - margin - zone.height_mm,
        min_x + margin + zone.width_mm,
        max_y - margin,
    )


def _expected_view_assignments(
    spec: DrawingSpec,
    plan: LayoutPlan,
) -> dict[str, tuple[int, ...]]:
    names = tuple(view.name for view in plan.views)
    if spec.geometry.view_entity_indices:
        return {
            name: tuple(spec.geometry.view_entity_indices.get(name, ()))
            for name in names
        }
    default = "front" if "front" in names else (names[0] if names else "front")
    all_indices = tuple(range(len(spec.geometry.entities)))
    return {name: all_indices if name == default else () for name in names}


def _equal_resource_value(actual: object, expected: object) -> bool:
    if isinstance(expected, str):
        return str(actual).casefold() == expected.casefold()
    return isclose(float(actual), float(expected), abs_tol=1e-9)


def _normalized_pattern(values: Iterable[float]) -> tuple[float, ...]:
    values = tuple(float(value) for value in values)
    total = sum(abs(value) for value in values)
    if total == 0:
        return ()
    return tuple(round(value / total, 9) for value in values)


def _owner_zone(plan: LayoutPlan, name: str | None) -> Bounds | None:
    return {
        "printable": plan.printable_bounds,
        "title": plan.title_block_bounds,
        "technical": plan.technical_note_bounds,
        "parts": plan.parts_list.bounds,
    }.get(name)


def _allowed_pair(
    first: AnnotationLayout | None,
    second: AnnotationLayout | None,
) -> bool:
    if first is None or second is None:
        return False
    if first.kind == "frame" and first.owner_zone == "printable":
        return True
    if second.kind == "frame" and second.owner_zone == "printable":
        return True
    if first.kind == "frame" and second.owner_zone == first.owner_zone:
        return True
    if second.kind == "frame" and first.owner_zone == second.owner_zone:
        return True
    if first.group_id != second.group_id:
        return False
    kinds = {first.kind, second.kind}
    if kinds == {"balloon", "balloon-text"}:
        return "own-balloon-text" in first.allowed_contact or "own-balloon-text" in second.allowed_contact
    if kinds == {"balloon", "leader"}:
        return "own-leader" in first.allowed_contact or "own-leader" in second.allowed_contact
    return first.owner_zone is not None


def _shallow_edge_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    maximum: float,
) -> bool:
    overlap_x = min(first[2], second[2]) - max(first[0], second[0])
    overlap_y = min(first[3], second[3]) - max(first[1], second[1])
    return overlap_x > 0 and overlap_y > 0 and min(overlap_x, overlap_y) <= maximum + _TOLERANCE


def _bounds_tuple(bounds: Bounds) -> tuple[float, float, float, float]:
    return bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y


def _geometry_tuple(
    bounds: GeometryBounds | None,
) -> tuple[float, float, float, float] | None:
    if bounds is None:
        return None
    return bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y


def _contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    return (
        outer[0] - _TOLERANCE <= inner[0]
        and outer[1] - _TOLERANCE <= inner[1]
        and inner[2] <= outer[2] + _TOLERANCE
        and inner[3] <= outer[3] + _TOLERANCE
    )


def _same_bounds(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return all(isclose(actual, expected, abs_tol=1e-9) for actual, expected in zip(first, second, strict=True))


def _intersects(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return (
        min(first[2], second[2]) - max(first[0], second[0]) > _TOLERANCE
        and min(first[3], second[3]) - max(first[1], second[1]) > _TOLERANCE
    )


def _union(
    bounds: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    values = tuple(bounds)
    if not values:
        return None
    return (
        min(value[0] for value in values),
        min(value[1] for value in values),
        max(value[2] for value in values),
        max(value[3] for value in values),
    )


def _handle(entity: object | None) -> str | None:
    if entity is None:
        return None
    try:
        handle = entity.dxf.get("handle")
    except Exception:
        return None
    return str(handle) if handle else None


def _handles(*entities: object | None) -> tuple[str, ...]:
    return tuple(handle for entity in entities if (handle := _handle(entity)) is not None)


def _measured_handles(items: Iterable[_MeasuredEntity]) -> tuple[str, ...]:
    return tuple(item.handle for item in items if item.handle is not None)
