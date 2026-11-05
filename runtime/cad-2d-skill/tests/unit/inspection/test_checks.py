from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import ezdxf
import pytest

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.geometry.dxf_writer import GenerationResult
from cad_skill_core.inspection import AuditIssue, AuditReport, inspect_drawing
from cad_skill_core.layout.models import Bounds


def _issue(report: AuditReport, code: str) -> AuditIssue:
    return next(issue for issue in report.issues if issue.code == code)


def test_audit_contract_is_immutable_and_sorted_by_severity_category_code_handles():
    original = (
        AuditIssue("Z", "info", "font", "font info", ("B", "A"), True),
        AuditIssue("B", "error", "semantic", "semantic error", (), False),
        AuditIssue("A", "error", "structure", "structure error", ("2",), False),
        AuditIssue("A", "warning", "layout", "layout warning", ("1",), True),
    )

    report = AuditReport(original)

    assert [issue.category for issue in report.issues] == [
        "structure",
        "semantic",
        "layout",
        "font",
    ]
    assert report.issues[-1].entity_handles == ("A", "B")
    assert tuple(report.by_category) == (
        "structure",
        "standard",
        "semantic",
        "layout",
        "font",
    )
    assert report.passed is False
    assert original[0].entity_handles == ("A", "B")
    with pytest.raises(FrozenInstanceError):
        report.issues = ()


def test_valid_finalized_drawing_has_no_audit_issues(valid_context):
    report = inspect_drawing(**valid_context.arguments())

    assert report.passed is True
    assert report.issues == ()


def test_missing_requested_section_is_semantic_error(valid_context):
    report = inspect_drawing(**valid_context.without_view("section-A"))

    issue = _issue(report, "SEMANTIC_MISSING_VIEW")
    assert issue.severity == "error"
    assert issue.repairable is False
    assert "section-A" in issue.message


def test_label_only_section_without_measurable_source_group_is_missing(valid_context):
    views = tuple(
        replace(view, source_indices=(), source_handles=())
        if view.name == "section-A"
        else view
        for view in valid_context.plan.views
    )
    arguments = valid_context.arguments()
    arguments["plan"] = replace(valid_context.plan, views=views)

    report = inspect_drawing(**arguments)

    issue = _issue(report, "SEMANTIC_MISSING_VIEW")
    assert issue.repairable is False
    assert "section-A" in issue.message


def test_nonzero_horizontal_source_geometry_is_a_present_view(valid_context):
    section_handle = next(
        view.source_handles[0]
        for view in valid_context.plan.views
        if view.name == "section-A"
    )

    def make_horizontal(document):
        entity = next(
            entity
            for entity in document.modelspace()
            if entity.dxf.handle == section_handle
        )
        entity.dxf.end = (entity.dxf.end.x, entity.dxf.start.y, entity.dxf.end.z)

    changed = valid_context.with_dxf_mutation("horizontal-section", make_horizontal)

    report = inspect_drawing(**changed.arguments())

    assert not any(issue.code == "SEMANTIC_MISSING_VIEW" for issue in report.issues)


def test_text_inside_title_block_is_layout_error_with_exact_handle(valid_context):
    changed = valid_context.with_text_in_title_block()
    document = ezdxf.readfile(changed["dxf_path"])
    intruder_handle = list(document.modelspace())[-1].dxf.handle

    report = inspect_drawing(**changed)

    issue = _issue(report, "LAYOUT_PROTECTED_ZONE")
    assert issue.entity_handles == (intruder_handle,)


@pytest.mark.parametrize("kind", ["missing", "malformed"])
def test_unreadable_paths_return_path_aware_structural_issue_without_raw_exception(
    valid_context, tmp_path, kind
):
    path = tmp_path / f"{kind}.dxf"
    if kind == "malformed":
        path.write_text("this is not a DXF", encoding="utf-8")
    arguments = valid_context.arguments()
    arguments["dxf_path"] = path

    report = inspect_drawing(**arguments)

    issue = _issue(report, "STRUCTURE_DXF_OPEN")
    assert issue.severity == "error"
    assert str(path) in issue.message
    assert issue.entity_handles == ()


def test_ezdxf_auditor_findings_include_the_entity_handle(valid_context):
    def use_undefined_linetype(document):
        entity = list(document.modelspace())[0]
        entity.dxf.linetype = "UNDEFINED_AUDIT_TEST"

    changed = valid_context.with_dxf_mutation("auditor", use_undefined_linetype)
    handle = list(ezdxf.readfile(changed.dxf_path).modelspace())[0].dxf.handle

    report = inspect_drawing(**changed.arguments())

    issue = _issue(report, "STRUCTURE_AUDIT_FIX")
    assert issue.entity_handles == (handle,)
    assert issue.severity == "warning"


def test_ezdxf_auditor_failure_includes_the_inspected_path(valid_context, monkeypatch):
    def fail_audit(_document):
        raise RuntimeError("audit probe failed")

    monkeypatch.setattr(ezdxf.document.Drawing, "audit", fail_audit)

    report = inspect_drawing(**valid_context.arguments())

    issue = _issue(report, "STRUCTURE_AUDIT_FAILED")
    assert str(valid_context.dxf_path) in issue.message


@pytest.mark.parametrize(
    ("suffix", "mutation", "code"),
    [
        (
            "layer-color",
            lambda document: setattr(document.layers.get("OUTLINE").dxf, "color", 2),
            "STANDARD_LAYER_COLOR",
        ),
        (
            "layer-lineweight",
            lambda document: setattr(document.layers.get("OUTLINE").dxf, "lineweight", 25),
            "STANDARD_LAYER_LINEWEIGHT",
        ),
        (
            "layer-linetype",
            lambda document: setattr(document.layers.get("OUTLINE").dxf, "linetype", "HIDDEN"),
            "STANDARD_LAYER_LINETYPE",
        ),
        (
            "text-style",
            lambda document: setattr(document.styles.get("normal").dxf, "width", 0.5),
            "STANDARD_TEXT_STYLE",
        ),
        (
            "dim-style",
            lambda document: setattr(document.dimstyles.get("DIM").dxf, "dimtxt", 99.0),
            "STANDARD_DIM_STYLE",
        ),
        (
            "dim-style-text-placement",
            lambda document: setattr(document.dimstyles.get("DIM").dxf, "dimtad", 1),
            "STANDARD_DIM_STYLE",
        ),
    ],
)
def test_standard_resource_mismatches_are_reported(valid_context, suffix, mutation, code):
    changed = valid_context.with_dxf_mutation(suffix, mutation)

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, code).severity == "error"


def test_used_text_style_must_be_allowed_and_reports_exact_handle(valid_context):
    source_count = len(valid_context.spec.geometry.entities)
    annotation_index = next(
        index
        for index, annotation in enumerate(valid_context.plan.annotations)
        if annotation.annotation_id == "view-label-front"
    )

    def use_rogue_style(document):
        document.styles.add(
            "rogue",
            font="Arial",
            dxfattribs={"height": 3.5, "width": 1.0},
        )
        list(document.modelspace())[source_count + annotation_index].dxf.style = "rogue"

    changed = valid_context.with_dxf_mutation("rogue-text-style", use_rogue_style)
    handle = list(ezdxf.readfile(changed.dxf_path).modelspace())[
        source_count + annotation_index
    ].dxf.handle

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "STANDARD_ENTITY_TEXT_STYLE").entity_handles == (handle,)


def test_used_dimension_style_must_be_dim_and_reports_exact_handle(valid_context):
    def use_rogue_dimstyle(document):
        document.dimstyles.add(
            "ROGUE_DIM",
            dxfattribs={
                "dimtxsty": "DIM",
                "dimtxt": 3.5,
                "dimgap": 5.0,
                "dimasz": 3.5,
            },
        )
        dimension = next(
            entity for entity in document.modelspace() if entity.dxftype() == "DIMENSION"
        )
        dimension.dxf.dimstyle = "ROGUE_DIM"

    changed = valid_context.with_dxf_mutation("rogue-dim-style", use_rogue_dimstyle)
    handle = next(
        entity.dxf.handle
        for entity in ezdxf.readfile(changed.dxf_path).modelspace()
        if entity.dxftype() == "DIMENSION"
    )

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "STANDARD_ENTITY_DIM_STYLE").entity_handles == (handle,)


def test_missing_required_layer_and_wrong_linetype_pattern_are_reported(valid_context):
    def corrupt_resources(document):
        document.layers.remove("HATCH")
        document.linetypes.get("CENTER").setup_pattern([1.0, -1.0])

    changed = valid_context.with_dxf_mutation("resource-contract", corrupt_resources)

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "STANDARD_MISSING_LAYER").message.endswith("HATCH")
    assert _issue(report, "STANDARD_LINETYPE_PATTERN").message.endswith("CENTER")


def test_sheet_and_profile_contract_mismatch_is_reported(valid_context):
    wrong_plan = replace(
        valid_context.plan,
        sheet_bounds=Bounds(0.0, 0.0, 297.0, 210.0),
    )
    arguments = valid_context.arguments()
    arguments["plan"] = wrong_plan

    report = inspect_drawing(**arguments)

    assert _issue(report, "STANDARD_SHEET_CONTRACT").severity == "error"


def test_profile_title_block_contract_cannot_be_redefined_by_the_plan(valid_context):
    wrong_title = Bounds(10.0, 10.0, 190.0, 65.0)
    arguments = valid_context.arguments()
    arguments["plan"] = replace(valid_context.plan, title_block_bounds=wrong_title)

    report = inspect_drawing(**arguments)

    assert _issue(report, "STANDARD_SHEET_CONTRACT").severity == "error"


def test_extra_custom_layer_is_not_part_of_the_allowed_profile(valid_context):
    changed = valid_context.with_dxf_mutation(
        "extra-layer",
        lambda document: document.layers.add("UNAPPROVED"),
    )

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "STANDARD_UNALLOWED_LAYER").message.endswith("UNAPPROVED")


def test_requested_material_must_be_represented_in_the_drawing(valid_context):
    from cad_skill_core.schema.models import SourcedText

    requirements = valid_context.spec.requirements.model_copy(
        update={"material": SourcedText(text="42CrMo", source_id="source")}
    )
    spec = valid_context.spec.model_copy(update={"requirements": requirements})
    arguments = valid_context.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    assert _issue(report, "SEMANTIC_MISSING_MATERIAL").repairable is False


def test_material_rejects_same_text_technical_note_from_another_source(valid_context):
    from cad_skill_core.schema.models import Provenance, SourcedText

    spec = valid_context.spec.model_copy(deep=True)
    spec.requirements.material = SourcedText(
        text="Deburr all edges.", source_id="source"
    )
    spec.requirements.technical_notes[0] = SourcedText(
        text="Deburr all edges.", source_id="foreign"
    )
    spec.provenance.append(
        Provenance(
            id="foreign", file="foreign.md", locator="line 1", confidence=1.0
        )
    )
    arguments = valid_context.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    issue = _issue(report, "SEMANTIC_MISSING_MATERIAL")
    assert issue.repairable is False
    assert "source-bound" in issue.message


def test_material_rejects_substring_in_same_source_technical_note(valid_context):
    from cad_skill_core.schema.models import SourcedText

    requirements = valid_context.spec.requirements.model_copy(
        update={"material": SourcedText(text="42CrMo", source_id="source")}
    )
    spec = valid_context.spec.model_copy(update={"requirements": requirements})
    changed = valid_context.with_annotation_text("technical-note-1", "not 42CrMo")
    arguments = changed.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    issue = _issue(report, "SEMANTIC_MISSING_MATERIAL")
    assert issue.repairable is False
    assert "declared" in issue.message


def test_material_rejects_same_source_note_when_only_its_dxf_text_declares_material(
    valid_context,
):
    from cad_skill_core.schema.models import SourcedText

    requirements = valid_context.spec.requirements.model_copy(
        update={"material": SourcedText(text="42CrMo", source_id="source")}
    )
    spec = valid_context.spec.model_copy(update={"requirements": requirements})
    changed = valid_context.with_annotation_text("technical-note-1", "42CrMo")
    arguments = changed.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    issue = _issue(report, "SEMANTIC_MISSING_MATERIAL")
    assert issue.repairable is False


def test_material_accepts_same_source_material_label_in_technical_note(valid_context):
    from cad_skill_core.schema.models import SourcedText

    requirements = valid_context.spec.requirements.model_copy(
        update={
            "material": SourcedText(text="Deburr all edges.", source_id="source"),
            "technical_notes": [
                SourcedText(
                    text="MATERIAL: Deburr all edges.", source_id="source"
                )
            ],
        }
    )
    spec = valid_context.spec.model_copy(update={"requirements": requirements})
    changed = valid_context.with_annotation_text(
        "technical-note-1", "MATERIAL: Deburr all edges."
    )
    arguments = changed.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    assert not any(
        issue.code == "SEMANTIC_MISSING_MATERIAL" for issue in report.issues
    )


def test_material_accepts_same_source_material_label_in_free_annotation(valid_context, tmp_path):
    from cad_skill_core.layout.annotations import apply_annotations
    from cad_skill_core.layout.plan import plan_layout
    from cad_skill_core.schema.models import Annotation, SourcedText

    spec = valid_context.spec.model_copy(deep=True)
    spec.requirements.material = SourcedText(text="42CrMo", source_id="source")
    spec.annotations.append(Annotation(text="MATERIAL: 42CrMo", source_id="source"))
    source = valid_context.dxf_path.with_name("annotation-material-source.dxf")
    from cad_skill_core.geometry.dxf_writer import write_dxf

    written = write_dxf(spec, valid_context.standard, valid_context.font_result, source)
    document = ezdxf.readfile(written.path)
    plan = apply_annotations(
        document,
        plan_layout(spec, valid_context.standard),
        spec,
        valid_context.standard,
        valid_context.font_result,
    )
    destination = tmp_path / "annotation-material-final.dxf"
    document.saveas(destination)

    report = inspect_drawing(
        spec=spec,
        dxf_path=destination,
        plan=plan,
        standard=valid_context.standard,
        font_result=valid_context.font_result,
    )

    assert not any(
        issue.code == "SEMANTIC_MISSING_MATERIAL" for issue in report.issues
    )


@pytest.mark.parametrize("fact,code", [
    ("material", "SEMANTIC_MATERIAL_PROVENANCE"),
    ("note", "SEMANTIC_TECHNICAL_REQUIREMENT_PROVENANCE"),
    ("part", "SEMANTIC_ASSEMBLY_PROVENANCE"),
])
def test_inspection_rejects_unresolvable_fact_sources(valid_context, fact, code):
    from cad_skill_core.schema.models import SourcedText

    spec = valid_context.spec.model_copy(deep=True)
    if fact == "material":
        spec.requirements.material = SourcedText(text="Body", source_id="missing")
    elif fact == "note":
        spec.requirements.technical_notes[0].source_id = "missing"
    else:
        spec.parts_list[0].source_id = "missing"
    arguments = valid_context.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    assert _issue(report, code).repairable is False
    assert _issue(report, code).severity == "error"


def test_dimension_requires_resolvable_provenance_and_matching_value(valid_context):
    bad_dimension = valid_context.spec.dimensions[0].model_copy(
        update={"source_id": "unknown", "value": 41.0}
    )
    spec = valid_context.spec.model_copy(update={"dimensions": [bad_dimension]})
    arguments = valid_context.arguments()
    arguments["spec"] = spec

    report = inspect_drawing(**arguments)

    assert _issue(report, "SEMANTIC_DIMENSION_PROVENANCE").repairable is False
    value_issue = _issue(report, "SEMANTIC_DIMENSION_VALUE")
    assert value_issue.entity_handles
    assert value_issue.repairable is False


def test_dimension_override_cannot_mask_wrong_measured_geometry(valid_context):
    def change_geometry_but_keep_display(document):
        dimension = next(
            entity for entity in document.modelspace() if entity.dxftype() == "DIMENSION"
        )
        start = dimension.dxf.defpoint2
        dimension.dxf.defpoint3 = (start.x + 140.0, start.y, start.z)
        dimension.dxf.text = "40"

    changed = valid_context.with_dxf_mutation("dimension-geometry", change_geometry_but_keep_display)
    handle = next(
        entity.dxf.handle
        for entity in ezdxf.readfile(changed.dxf_path).modelspace()
        if entity.dxftype() == "DIMENSION"
    )

    report = inspect_drawing(**changed.arguments())

    issue = _issue(report, "SEMANTIC_DIMENSION_VALUE")
    assert issue.entity_handles == (handle,)


def test_custom_dimension_display_is_allowed_when_measured_geometry_matches(valid_context):
    def change_display_only(document):
        dimension = next(
            entity for entity in document.modelspace() if entity.dxftype() == "DIMENSION"
        )
        dimension.dxf.text = "40 REF"

    changed = valid_context.with_dxf_mutation("dimension-display", change_display_only)

    report = inspect_drawing(**changed.arguments())

    assert not any(issue.code == "SEMANTIC_DIMENSION_VALUE" for issue in report.issues)


def test_assembly_parts_table_and_balloon_must_match(valid_context):
    changed = valid_context.with_annotation_text("balloon-2-text", "9")
    source_count = len(changed.spec.geometry.entities)
    annotation_index = next(
        index
        for index, annotation in enumerate(changed.plan.annotations)
        if annotation.annotation_id == "balloon-2-text"
    )
    handle = list(ezdxf.readfile(changed.dxf_path).modelspace())[
        source_count + annotation_index
    ].dxf.handle

    report = inspect_drawing(**changed.arguments())

    issue = _issue(report, "SEMANTIC_ASSEMBLY_CONSISTENCY")
    assert handle in issue.entity_handles
    assert issue.repairable is False


def test_reopened_source_bounds_must_stay_inside_finalized_view_plan(valid_context):
    changed = valid_context.with_source_translation(0, 250.0, 0.0)
    handle = list(ezdxf.readfile(changed.dxf_path).modelspace())[0].dxf.handle

    report = inspect_drawing(**changed.arguments())

    issue = _issue(report, "LAYOUT_VIEW_BOUNDS")
    assert issue.entity_handles == (handle,)


def test_finalized_view_indices_must_remain_a_total_nonoverlapping_partition(valid_context):
    first_view = replace(valid_context.plan.views[0], source_indices=(0, 1))
    second_view = replace(valid_context.plan.views[1], source_indices=())
    arguments = valid_context.arguments()
    arguments["plan"] = replace(valid_context.plan, views=(first_view, second_view))

    report = inspect_drawing(**arguments)

    assert _issue(report, "LAYOUT_VIEW_ASSIGNMENT").severity == "error"


def test_extra_printable_entity_is_reported_as_unplanned_with_its_handle(valid_context):
    def add_unplanned(document):
        document.modelspace().add_circle((200.0, 200.0), 1.0, dxfattribs={"layer": "OUTLINE"})

    changed = valid_context.with_dxf_mutation("unplanned", add_unplanned)
    handle = list(ezdxf.readfile(changed.dxf_path).modelspace())[-1].dxf.handle

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "LAYOUT_UNPLANNED_ENTITY").entity_handles == (handle,)


def test_actual_annotation_bounds_and_collisions_are_measured_from_reopened_dxf(valid_context):
    source_count = len(valid_context.spec.geometry.entities)
    first_id = "parts-1-description"
    second_id = "parts-2-description"
    first_index = next(
        index for index, annotation in enumerate(valid_context.plan.annotations)
        if annotation.annotation_id == first_id
    )
    second_index = next(
        index for index, annotation in enumerate(valid_context.plan.annotations)
        if annotation.annotation_id == second_id
    )

    def collide(document):
        entities = list(document.modelspace())
        first = entities[source_count + first_index]
        second = entities[source_count + second_index]
        second.dxf.insert = first.dxf.insert

    changed = valid_context.with_dxf_mutation("collision", collide)

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "LAYOUT_ANNOTATION_BOUNDS").entity_handles
    assert len(_issue(report, "LAYOUT_COLLISION").entity_handles) == 2


def test_reordered_modelspace_entities_remain_correlated_by_stable_handle(valid_context):
    def reorder(document):
        modelspace = document.modelspace()
        first = list(modelspace)[0]
        modelspace.unlink_entity(first)
        modelspace.add_entity(first)

    changed = valid_context.with_dxf_mutation("reordered", reorder)

    report = inspect_drawing(**changed.arguments())

    assert report.issues == ()


def test_deleted_planned_entity_reports_its_stored_handle(valid_context):
    annotation = next(
        annotation
        for annotation in valid_context.plan.annotations
        if annotation.annotation_id == "technical-note-1"
    )
    stored_handle = annotation.entity_handles[0]

    def delete_planned(document):
        entity = next(
            entity
            for entity in document.modelspace()
            if entity.dxf.handle == stored_handle
        )
        document.modelspace().delete_entity(entity)

    changed = valid_context.with_dxf_mutation("deleted-planned", delete_planned)

    report = inspect_drawing(**changed.arguments())

    assert _issue(report, "LAYOUT_MISSING_ENTITY").entity_handles == (stored_handle,)


def test_finalized_views_must_keep_profile_clearance(valid_context):
    first, second = valid_context.plan.views
    moved_bounds = Bounds(
        second.bounds.min_x - 1.0,
        second.bounds.min_y,
        second.bounds.max_x - 1.0,
        second.bounds.max_y,
    )
    arguments = valid_context.arguments()
    arguments["plan"] = replace(
        valid_context.plan,
        views=(first, replace(second, bounds=moved_bounds)),
    )

    report = inspect_drawing(**arguments)

    issue = _issue(report, "LAYOUT_VIEW_SPACING")
    assert "20" in issue.message


def test_missing_glyphs_are_font_errors(valid_context):
    fonts = dict(valid_context.font_result)
    fonts["TEXT"] = replace(fonts["TEXT"], missing_characters=("字",))
    arguments = valid_context.arguments()
    arguments["font_result"] = fonts

    report = inspect_drawing(**arguments)

    issue = _issue(report, "FONT_MISSING_GLYPH")
    assert issue.severity == "error"
    assert "字" in issue.message


def test_empty_explicit_font_mapping_reports_each_used_role_with_entity_handles(valid_context):
    arguments = valid_context.arguments()
    arguments["font_result"] = {}

    report = inspect_drawing(**arguments)

    issues = [issue for issue in report.issues if issue.code == "FONT_MISSING_ROLE"]
    assert {"TEXT", "DIM"} == {
        role
        for issue in issues
        for role in ("TEXT", "DIM")
        if role in issue.message
    }
    assert all(issue.entity_handles for issue in issues)


def test_unverified_shx_role_remains_a_warning_until_preview(valid_context):
    fonts = dict(valid_context.font_result)
    fonts["DIM"] = replace(
        fonts["DIM"],
        primary="simplex.shx",
        requires_render_verification=True,
    )
    arguments = valid_context.arguments()
    arguments["font_result"] = fonts

    report = inspect_drawing(**arguments)

    issue = _issue(report, "FONT_RENDER_VERIFICATION_REQUIRED")
    assert issue.severity == "warning"
    assert "DIM" in issue.message
    assert report.passed is False


def test_generation_result_render_roles_are_consumed_as_font_evidence(valid_context):
    evidence = GenerationResult(
        path=valid_context.dxf_path,
        entity_counts={},
        extents=valid_context.plan.sheet_bounds,
        roles_requiring_render_verification=("TEXT",),
    )
    arguments = valid_context.arguments()
    arguments["font_result"] = evidence

    report = inspect_drawing(**arguments)

    issue = _issue(report, "FONT_RENDER_VERIFICATION_REQUIRED")
    assert issue.severity == "warning"
    assert "TEXT" in issue.message


def test_generation_result_without_role_evidence_cannot_verify_final_dxf_fonts(valid_context):
    evidence = GenerationResult(
        path=valid_context.dxf_path,
        entity_counts={},
        extents=valid_context.plan.sheet_bounds,
        roles_requiring_render_verification=(),
    )
    arguments = valid_context.arguments()
    arguments["font_result"] = evidence

    report = inspect_drawing(**arguments)

    issues = [issue for issue in report.issues if issue.code == "FONT_MISSING_ROLE"]
    assert len(issues) == 2
    assert all(issue.entity_handles for issue in issues)


def test_inspection_does_not_mutate_spec_plan_standard_or_font_mapping(valid_context):
    before_spec = valid_context.spec.model_dump(mode="python")
    before_plan = valid_context.plan
    before_standard = valid_context.standard.model_dump(mode="python")
    before_fonts = dict(valid_context.font_result)

    inspect_drawing(**valid_context.arguments())

    assert valid_context.spec.model_dump(mode="python") == before_spec
    assert valid_context.plan == before_plan
    assert valid_context.standard.model_dump(mode="python") == before_standard
    assert valid_context.font_result == before_fonts
