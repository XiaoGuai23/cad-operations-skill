from pathlib import Path
from decimal import Decimal
from dataclasses import replace

import ezdxf
import pytest
from ezdxf import bbox

from cad_skill_core.layout import LayoutOverflowError, apply_annotations, plan_layout
from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.schema.io import load_spec
from cad_skill_core.schema.models import Annotation, DrawingSpec, SourcedText
from cad_skill_core.standards.profile import load_standard


PROFILE_PATH = Path("knowledge/standards/gb-mechanical.yaml")


def gb_profile():
    return load_standard(PROFILE_PATH)


def assembly_spec(parts: int = 2) -> DrawingSpec:
    return DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "name": "Valve assembly",
                "drawing_number": "ASM-001",
                "drawing_type": "assembly",
            },
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {
                "material": {"text": "Steel", "source_id": "source"},
                "views": ["front", "top", "section-A"],
                "technical_notes": [{"text": "Deburr all edges.", "source_id": "source"}],
            },
            "geometry": {
                "parameters": {
                    "view_sizes_mm": {
                        "front": [80.0, 50.0],
                        "top": [80.0, 35.0],
                        "section-A": [60.0, 50.0],
                    }
                }
            },
            "dimensions": [{"kind": "linear", "value": 80.0, "source_id": "source"}],
            "annotations": [{"text": "ASSEMBLE DRY", "source_id": "source"}],
            "parts_list": [
                {"item": f"Part {item_no}", "quantity": item_no, "source_id": "source"}
                for item_no in range(1, parts + 1)
            ],
            "provenance": [
                {"id": "source", "file": "brief.md", "locator": "line 1", "confidence": 1.0}
            ],
        }
    )


def test_layout_keeps_views_out_of_title_block():
    plan = plan_layout(assembly_spec(), gb_profile())

    assert all(not view.bounds.intersects(plan.title_block_bounds) for view in plan.views)


def test_parts_list_rows_match_assembly_items():
    plan = plan_layout(assembly_spec(parts=3), gb_profile())

    assert [row.item_no for row in plan.parts_list.rows] == [1, 2, 3]


def test_layout_is_deterministic_and_uses_first_angle_top_view_placement():
    first = plan_layout(assembly_spec(), gb_profile())
    second = plan_layout(assembly_spec(), gb_profile())
    views = {view.name: view for view in first.views}

    assert first == second
    assert views["top"].bounds.max_y <= views["front"].bounds.min_y - gb_profile().spacing.view_gap_mm
    assert {view.label for view in first.views} >= {"FRONT", "TOP", "SECTION A-A"}


def test_layout_keeps_all_view_bounds_separated_by_profile_gap():
    plan = plan_layout(assembly_spec(), gb_profile())

    for index, view in enumerate(plan.views):
        for other in plan.views[index + 1 :]:
            assert view.bounds.clearance_to(other.bounds) >= gb_profile().spacing.view_gap_mm


def test_layout_suggests_smallest_standard_scale_that_fits():
    spec = assembly_spec()
    spec.geometry.parameters["view_sizes_mm"] = {"front": [800.0, 500.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == "1:1"
    assert error.value.suggested_scale == "1:5"


def test_apply_annotations_adds_drawing_annotations_using_standard_layers_and_styles():
    spec = assembly_spec()
    plan = plan_layout(spec, gb_profile())
    document = ezdxf.new("R2018")
    document.styles.add("normal", font="Arial", dxfattribs={"height": 3.5, "width": 1.0})
    document.styles.add("DIM", font="Arial")

    apply_annotations(document, plan, spec, gb_profile(), resolved_font_map())

    entities = list(document.modelspace())
    assert {entity.dxf.layer for entity in entities} >= {"DIM", "TEXT", "FRAME"}
    assert any(entity.dxftype() == "DIMENSION" for entity in entities)
    assert any(entity.dxftype() == "LEADER" for entity in entities)
    assert any(entity.dxftype() == "TEXT" and entity.dxf.style == "normal" for entity in entities)
    assert any(entity.dxftype() == "TEXT" and entity.dxf.text == "Valve assembly" for entity in entities)


def test_apply_annotations_centers_measured_view_labels_over_their_views():
    spec = assembly_spec(parts=0)
    plan = plan_layout(spec, gb_profile())
    document = ezdxf.new("R2018")

    finalized = apply_annotations(
        document,
        plan,
        spec,
        gb_profile(),
        resolved_font_map(),
    )

    views = {view.name: view.bounds for view in finalized.views}
    labels = [
        annotation
        for annotation in finalized.annotations
        if annotation.kind == "view-label"
    ]
    assert labels
    assert all(
        label.owner_view is not None
        and label.bounds.center[0] == pytest.approx(
            views[label.owner_view].center[0], abs=1e-6
        )
        for label in labels
    )


def test_narrow_edge_view_label_keeps_visible_clearance_from_sheet_frame():
    spec = assembly_spec(parts=0)
    spec.project.drawing_type = "part"
    spec.requirements.views = ["section-A"]
    spec.geometry.parameters["view_sizes_mm"] = {"section-A": [20.0, 20.0]}
    spec.dimensions = []
    spec.annotations = []
    plan = plan_layout(spec, gb_profile())
    document = ezdxf.new("R2018")

    finalized = apply_annotations(
        document,
        plan,
        spec,
        gb_profile(),
        resolved_font_map(),
    )

    label = next(
        annotation
        for annotation in finalized.annotations
        if annotation.kind == "view-label"
    )
    assert label.bounds.min_x >= finalized.printable_bounds.min_x + 2.0


def test_layout_transforms_grouped_fixture_geometry_into_the_planned_view(tmp_path):
    spec = load_spec(Path("tests/fixtures/specs/simple-flange.json"))
    spec.sheet.scale = "1:2"
    spec.geometry.view_entity_indices = {"front": [0, 1, 2, 3, 4, 5]}
    standard = gb_profile()
    document = ezdxf.readfile(write_dxf(spec, standard, resolved_font_map(), tmp_path / "source.dxf").path)
    plan = plan_layout(spec, standard)

    finalized = apply_annotations(document, plan, spec, standard, resolved_font_map())
    document.saveas(tmp_path / "laid-out.dxf")
    reopened = ezdxf.readfile(tmp_path / "laid-out.dxf")
    geometry_bounds = bbox.extents(list(reopened.modelspace())[:6])
    view = plan.views[0]

    assert view.source_bounds == (-100.0, -100.0, 100.0, 100.0)
    assert (geometry_bounds.extmin.x, geometry_bounds.extmin.y) == (view.bounds.min_x, view.bounds.min_y)
    assert (geometry_bounds.extmax.x, geometry_bounds.extmax.y) == (view.bounds.max_x, view.bounds.max_y)
    assert not view.bounds.intersects(plan.title_block_bounds)
    assert finalized.views[0].source_handles == tuple(
        entity.dxf.handle for entity in list(reopened.modelspace())[:6]
    )


def test_terminal_overflow_at_smallest_scale_reports_layout_error():
    spec = assembly_spec()
    spec.sheet.scale = "1:100"
    spec.geometry.parameters["view_sizes_mm"] = {"front": [100000.0, 100000.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == "1:100"
    assert error.value.suggested_scale == "1:1000"


def test_terminal_overflow_below_smallest_supported_scale_uses_sentinel():
    spec = assembly_spec()
    spec.sheet.scale = "1:200"
    spec.geometry.parameters["view_sizes_mm"] = {"front": [100000.0, 100000.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "1:1000"


def test_every_planned_annotation_stays_inside_the_printable_sheet():
    plan = plan_layout(assembly_spec(), gb_profile())

    assert all(
        plan.printable_bounds.min_x <= annotation.bounds.min_x
        and plan.printable_bounds.min_y <= annotation.bounds.min_y
        and annotation.bounds.max_x <= plan.printable_bounds.max_x
        and annotation.bounds.max_y <= plan.printable_bounds.max_y
        for annotation in plan.annotations
    )


def test_layout_prefers_visible_frame_clearance_when_content_fits():
    plan = plan_layout(assembly_spec(), gb_profile())
    frame = plan.printable_bounds
    content = [view.bounds for view in plan.views]
    content.extend(
        annotation.bounds
        for annotation in plan.annotations
        if annotation.kind == "dimension"
    )

    assert content
    assert all(
        bounds.min_x >= frame.min_x + 2.0
        and bounds.min_y >= frame.min_y + 2.0
        and bounds.max_x <= frame.max_x - 2.0
        and bounds.max_y <= frame.max_y - 2.0
        for bounds in content
    )


def test_simple_flange_one_to_one_requires_explicit_one_to_two_replan():
    spec = load_spec(Path("examples/simple-flange/work/drawing-spec.json"))
    spec.sheet.scale = "1:1"

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == "1:1"
    assert error.value.suggested_scale == "1:2"


def test_tracked_simple_flange_one_to_two_keeps_view_content_two_mm_from_frame():
    spec = load_spec(Path("examples/simple-flange/work/drawing-spec.json"))
    assert spec.sheet.scale == "1:2"

    plan = plan_layout(spec, gb_profile())
    frame = plan.printable_bounds
    content = [view.bounds for view in plan.views]
    content.extend(
        annotation.bounds
        for annotation in plan.annotations
        if annotation.owner_view is not None
        and annotation.kind in {"dimension", "view-label"}
    )

    assert any(
        annotation.kind == "view-label" for annotation in plan.annotations
    )
    assert all(
        bounds.min_x >= frame.min_x + 2.0
        and bounds.min_y >= frame.min_y + 2.0
        and bounds.max_x <= frame.max_x - 2.0
        and bounds.max_y <= frame.max_y - 2.0
        for bounds in content
    )


def test_scale_search_skips_simple_flange_edge_only_candidate():
    spec = load_spec(Path("examples/simple-flange/work/drawing-spec.json"))
    spec.sheet.scale = "2:1"

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "1:2"


def test_fixed_scale_long_annotation_reports_fixed_content_overflow():
    spec = load_spec(Path("examples/simple-flange/work/drawing-spec.json"))
    assert spec.sheet.scale == "1:2"
    spec.annotations.append(Annotation(text="TRACEABLE NOTE " * 80))

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == "1:2"
    assert error.value.suggested_scale == "wrap-content-or-larger-sheet"
    assert error.value.reason == "fixed-content-overflow"
    assert "wrap fixed text or use a larger sheet" in str(error.value)


def test_layout_rejects_notes_and_parts_that_exceed_reserved_column_capacity():
    spec = assembly_spec(parts=30)
    spec.requirements.technical_notes = [
        SourcedText(text=f"Note {number}", source_id="source") for number in range(30)
    ]

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "larger-sheet-required"


def test_layout_rejects_free_annotations_that_exceed_technical_zone_capacity():
    spec = assembly_spec()
    spec.annotations = [Annotation(text=f"Callout {number}", source_id="source") for number in range(30)]

    with pytest.raises(LayoutOverflowError):
        plan_layout(spec, gb_profile())


def test_first_angle_right_view_is_left_of_front_without_grid_fallback():
    spec = assembly_spec()
    spec.requirements.views = ["front", "right"]
    plan = plan_layout(spec, gb_profile())
    views = {view.name: view for view in plan.views}

    assert views["right"].bounds.max_x <= views["front"].bounds.min_x - gb_profile().spacing.view_gap_mm


def test_fresh_annotation_document_uses_profile_layer_properties_without_project_default_font():
    spec = assembly_spec()
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    document = ezdxf.new("R2018")
    apply_annotations(document, plan, spec, standard, resolved_font_map())

    for name in standard.layers:
        layer = document.layers.get(name)
        profile_layer = standard.layers[name]
        assert layer.color == profile_layer.color
        assert layer.dxf.linetype == profile_layer.linetype
        assert layer.dxf.lineweight == round(profile_layer.lineweight_mm * 100)
    assert document.styles.get("normal").dxf.font == "Arial"
    assert document.styles.get("DIM").dxf.font == "Arial"


def test_source_id_reuse_and_index_grouping_transform_each_entity_once(tmp_path):
    spec = DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {"name": "Two lines", "drawing_type": "part"},
            "standard": {"family": "GB/T", "units": "mm", "projection": "first-angle"},
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {"views": ["front"]},
            "geometry": {
                "view_entity_indices": {"front": [0, 1]},
                "entities": [
                    {"kind": "line", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [20.0, 0.0], "source_id": "shared"},
                    {"kind": "line", "layer": "OUTLINE", "start": [0.0, 10.0], "end": [20.0, 10.0], "source_id": "shared"},
                ],
            },
            "provenance": [{"id": "shared", "file": "brief", "locator": "1", "confidence": 1.0}],
        }
    )
    standard = gb_profile()
    doc = ezdxf.readfile(write_dxf(spec, standard, resolved_font_map(), tmp_path / "lines.dxf").path)
    plan = plan_layout(spec, standard)

    apply_annotations(doc, plan, spec, standard, resolved_font_map())

    bounds = bbox.extents(list(doc.modelspace())[:2])
    assert (bounds.extmin.x, bounds.extmax.x) == (plan.views[0].bounds.min_x, plan.views[0].bounds.max_x)


@pytest.mark.parametrize("indices", [{"front": [0]}, {"front": [0, 0]}, {"front": [0, 2]}])
def test_index_grouping_must_be_a_total_partition(indices):
    values = assembly_spec().model_dump(mode="python")
    values["geometry"] = {
        "entities": [
            {"kind": "line", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [10.0, 0.0], "source_id": "source"},
            {"kind": "line", "layer": "OUTLINE", "start": [0.0, 10.0], "end": [10.0, 10.0], "source_id": "source"},
        ],
        "view_entity_indices": indices,
    }

    with pytest.raises(ValueError, match="view entity indices"):
        DrawingSpec.model_validate(values)


def test_index_grouping_rejects_a_view_not_requested_by_the_spec():
    values = assembly_spec().model_dump(mode="python")
    values["geometry"] = {
        "view_entity_indices": {"ghost": []},
        "entities": [],
    }

    with pytest.raises(ValueError, match="requested views"):
        DrawingSpec.model_validate(values)


def test_quarter_arc_and_degenerate_line_bounds_are_tight_but_nonzero():
    values = assembly_spec().model_dump(mode="python")
    values["project"]["drawing_type"] = "part"
    values["requirements"]["views"] = ["front"]
    values["geometry"] = {
        "view_entity_indices": {"front": [0, 1, 2]},
        "entities": [
            {"kind": "arc", "layer": "OUTLINE", "center": [0.0, 0.0], "radius": 10.0, "start_angle": 0.0, "end_angle": 90.0, "source_id": "source"},
            {"kind": "line", "layer": "OUTLINE", "start": [20.0, 0.0], "end": [40.0, 0.0], "source_id": "source"},
            {"kind": "line", "layer": "OUTLINE", "start": [30.0, 5.0], "end": [30.0, 25.0], "source_id": "source"},
        ],
    }
    spec = DrawingSpec.model_validate(values)

    plan = plan_layout(spec, gb_profile())

    assert plan.views[0].source_bounds == (0.0, -0.0005, 40.0, 25.0)


def test_degenerate_geometry_bounds_are_exact_while_reserved_view_bounds_stay_positive(tmp_path):
    values = assembly_spec().model_dump(mode="python")
    values["project"]["drawing_type"] = "part"
    values["requirements"]["views"] = ["front"]
    values["geometry"] = {
        "view_entity_indices": {"front": [0, 1]},
        "entities": [
            {"kind": "line", "layer": "OUTLINE", "start": [0.0, 0.0], "end": [20.0, 0.0], "source_id": "source"},
            {"kind": "line", "layer": "OUTLINE", "start": [10.0, 0.0], "end": [10.0, 30.0], "source_id": "source"},
        ],
    }
    spec = DrawingSpec.model_validate(values)
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    doc = ezdxf.readfile(write_dxf(spec, standard, resolved_font_map(), tmp_path / "degenerate.dxf").path)

    apply_annotations(doc, plan, spec, standard, resolved_font_map())
    actual = bbox.extents(list(doc.modelspace())[:2])

    assert plan.views[0].bounds.width > 0 and plan.views[0].bounds.height > 0
    assert (actual.extmin.x, actual.extmin.y, actual.extmax.x, actual.extmax.y) == plan.views[0].geometry_bounds.as_tuple()


def test_scale_suggestion_is_a_replannable_smaller_engineering_scale():
    spec = assembly_spec()
    spec.sheet.scale = "1:200"
    spec.geometry.parameters["view_sizes_mm"] = {"front": [100000.0, 50000.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "1:1000"
    spec.sheet.scale = error.value.suggested_scale
    assert plan_layout(spec, gb_profile()).views


def test_unbounded_engineering_scale_search_finds_a_fit_for_large_geometry():
    spec = assembly_spec()
    spec.geometry.parameters["view_sizes_mm"] = {"front": [100000000.0, 50000000.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "1:1000000"
    spec.sheet.scale = error.value.suggested_scale
    assert plan_layout(spec, gb_profile()).views


def test_engineering_scale_search_reaches_beyond_one_to_the_fifteenth():
    spec = assembly_spec()
    spec.geometry.parameters["view_sizes_mm"] = {"front": [100000000000000000.0, 50000000000000000.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "1:1000000000000000"
    spec.sheet.scale = error.value.suggested_scale
    assert plan_layout(spec, gb_profile()).views


def test_enlargement_scale_search_considers_5_to_1_before_reductions():
    spec = assembly_spec()
    spec.sheet.scale = "10:1"
    spec.requirements.views = ["front"]
    spec.project.drawing_type = "part"
    spec.geometry.parameters["view_sizes_mm"] = {"front": [30.0, 20.0]}

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.suggested_scale == "5:1"


def test_multiple_dimensions_have_staggered_nonoverlapping_planned_bands():
    spec = assembly_spec()
    spec.dimensions.append(spec.dimensions[0].model_copy(update={"value": 60.0}))

    plan = plan_layout(spec, gb_profile())
    dimensions = [annotation for annotation in plan.annotations if annotation.kind == "dimension"]

    assert len(dimensions) == 2
    assert dimensions[0].bounds != dimensions[1].bounds
    assert not dimensions[0].bounds.intersects(dimensions[1].bounds)


def test_reopened_dimension_stays_attached_to_its_line_and_clear_of_view(tmp_path):
    spec = assembly_spec(parts=0)
    spec.project.drawing_type = "part"
    spec.requirements.views = ["front"]
    spec.geometry.parameters["view_sizes_mm"] = {"front": [80.0, 50.0]}
    spec.annotations = []
    values = spec.model_dump(mode="python")
    values["geometry"]["entities"] = [
        {
            "kind": "polyline",
            "layer": "OUTLINE",
            "vertices": [[0.0, 0.0], [80.0, 0.0], [80.0, 50.0], [0.0, 50.0]],
            "closed": True,
            "source_id": "source",
        }
    ]
    values["geometry"]["view_entity_indices"] = {"front": [0]}
    spec = DrawingSpec.model_validate(values)
    standard = gb_profile()
    source = write_dxf(
        spec,
        standard,
        resolved_font_map(),
        tmp_path / "dimension-source.dxf",
    )
    document = ezdxf.readfile(source.path)

    finalized = apply_annotations(
        document,
        plan_layout(spec, standard),
        spec,
        standard,
        resolved_font_map(),
    )

    dimension = document.modelspace().query("DIMENSION").first
    assert dimension is not None
    text_entities = [
        entity
        for entity in dimension.virtual_entities()
        if entity.dxftype() in {"TEXT", "MTEXT"}
    ]
    assert text_entities
    text_bounds = bbox.extents(text_entities)
    front = next(view for view in finalized.views if view.name == "front")
    assert document.dimstyles.get("DIM").dxf.dimtad == 0
    assert dimension.dxf.text_midpoint.y == pytest.approx(dimension.dxf.defpoint.y)
    assert text_bounds.extmax.y < front.bounds.min_y
    extension_lines = [
        entity
        for entity in dimension.virtual_entities()
        if entity.dxftype() == "LINE"
        and abs(entity.dxf.start.x - entity.dxf.end.x) < 1e-9
    ]
    assert extension_lines
    assert max(
        max(entity.dxf.start.y, entity.dxf.end.y) for entity in extension_lines
    ) >= front.bounds.min_y - 1.0


def test_fresh_resources_include_profile_linetypes_and_title_style():
    spec = assembly_spec()
    standard = gb_profile()
    doc = ezdxf.new("R2018")

    apply_annotations(doc, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert {"CENTER", "HIDDEN"} <= {line_type.dxf.name for line_type in doc.linetypes}
    assert doc.styles.get("title").dxf.font == "Arial"


def test_wrong_existing_dimension_resources_require_explicit_verified_fonts():
    spec = assembly_spec()
    standard = gb_profile()
    doc = ezdxf.new("R2018")
    doc.styles.add("normal", font="Arial")
    doc.styles.add("title", font="Arial")
    doc.dimstyles.add("DIM", dxfattribs={"dimtxt": 99.0})

    with pytest.raises(ValueError, match="DIM"):
        apply_annotations(doc, plan_layout(spec, standard), spec, standard)


def test_conflicting_existing_center_linetype_is_rejected_before_annotation_emission():
    spec = assembly_spec()
    standard = gb_profile()
    doc = ezdxf.new("R2018")
    doc.linetypes.add("CENTER", pattern=[1.0, -1.0])

    with pytest.raises(ValueError, match="CENTER"):
        apply_annotations(doc, plan_layout(spec, standard), spec, standard, resolved_font_map())


def test_annotation_application_returns_measured_finalized_plan(tmp_path):
    spec = assembly_spec()
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    doc = ezdxf.new("R2018")

    finalized = apply_annotations(doc, plan, spec, standard, resolved_font_map())
    doc.saveas(tmp_path / "finalized.dxf")
    reopened = ezdxf.readfile(tmp_path / "finalized.dxf")

    assert finalized is not plan
    assert len(finalized.annotations) == len(plan.annotations)
    assert all(finalized.printable_bounds.contains(annotation.bounds) for annotation in finalized.annotations)
    assert bbox.extents(reopened.modelspace()).has_data
    reopened_handles = {entity.dxf.handle for entity in reopened.modelspace()}
    assert all(annotation.entity_handles for annotation in finalized.annotations)
    assert {
        handle
        for annotation in finalized.annotations
        for handle in annotation.entity_handles
    } <= reopened_handles


def test_excessively_long_title_fails_as_layout_overflow():
    spec = assembly_spec()
    spec.project.name = "X" * 1000

    with pytest.raises(LayoutOverflowError):
        plan_layout(spec, gb_profile())


def _document_structure(document):
    """Return caller-visible state without serialising/mutating the document."""

    return {
        "layers": tuple(
            sorted(
                (layer.dxf.name, layer.color, layer.dxf.linetype, layer.dxf.lineweight)
                for layer in document.layers
            )
        ),
        "linetypes": tuple(
            sorted(
                (
                    line_type.dxf.name,
                    tuple(tag.value for tag in line_type.pattern_tags.tags if tag.code in {40, 49}),
                )
                for line_type in document.linetypes
            )
        ),
        "styles": tuple(
            sorted(
                (style.dxf.name, style.dxf.font, style.dxf.height, style.dxf.width)
                for style in document.styles
            )
        ),
        "dimstyles": tuple(
            sorted(
                (
                    style.dxf.name,
                    style.dxf.dimtxsty,
                    style.dxf.dimtxt,
                    style.dxf.dimgap,
                    style.dxf.dimasz,
                )
                for style in document.dimstyles
            )
        ),
        "entities": tuple(
            (entity.dxftype(), tuple(sorted(entity.dxfattribs().items())))
            for entity in document.modelspace()
        ),
    }


def test_measurement_failure_is_atomic(monkeypatch):
    spec = assembly_spec()
    standard = gb_profile()
    document = ezdxf.new("R2018")
    before = _document_structure(document)

    def fail_measurement(*_args, **_kwargs):
        raise RuntimeError("measurement probe failed")

    monkeypatch.setattr("cad_skill_core.layout.annotations.bbox.extents", fail_measurement)

    with pytest.raises(RuntimeError, match="measurement probe failed"):
        apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert _document_structure(document) == before


def test_final_measurement_failure_rolls_back_the_committed_clone(monkeypatch):
    spec = assembly_spec()
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    document = ezdxf.new("R2018")
    before = _document_structure(document)
    real_extents = bbox.extents
    calls = 0

    def fail_after_scratch_measurement(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > len(plan.annotations):
            raise RuntimeError("final measurement probe failed")
        return real_extents(*args, **kwargs)

    monkeypatch.setattr(
        "cad_skill_core.layout.annotations.bbox.extents",
        fail_after_scratch_measurement,
    )

    with pytest.raises(RuntimeError, match="final measurement probe failed"):
        apply_annotations(document, plan, spec, standard, resolved_font_map())

    assert _document_structure(document) == before


@pytest.mark.parametrize(
    ("attributes", "message"),
    [
        ({"dimtxsty": "Standard"}, "dimtxsty"),
        ({"dimtxt": 99.0}, "dimtxt"),
        ({"dimgap": 99.0}, "dimgap"),
        ({"dimasz": 99.0}, "dimasz"),
        ({"dimtad": 1}, "dimtad"),
    ],
)
def test_late_dimension_resource_conflicts_are_atomic(attributes, message):
    spec = assembly_spec()
    standard = gb_profile()
    document = ezdxf.new("R2018")
    expected = {"dimtxsty": "DIM", "dimtxt": 3.5, "dimgap": 5.0, "dimasz": 3.5}
    expected.update(attributes)
    document.dimstyles.add("DIM", dxfattribs=expected)
    before = _document_structure(document)

    with pytest.raises(ValueError, match=message):
        apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert _document_structure(document) == before


@pytest.mark.parametrize(
    ("attribute", "bad_value"),
    [("font", "Courier New"), ("height", 9.0), ("width", 0.5)],
)
def test_existing_profile_text_style_conflicts_are_read_only(attribute, bad_value):
    spec = assembly_spec()
    standard = gb_profile()
    document = ezdxf.new("R2018")
    values = {"height": 3.5, "width": 1.0}
    font = "Arial"
    if attribute == "font":
        font = bad_value
    else:
        values[attribute] = bad_value
    document.styles.add("normal", font=font, dxfattribs=values)
    before = _document_structure(document)

    with pytest.raises(ValueError, match="normal"):
        apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert _document_structure(document) == before


@pytest.mark.parametrize(
    ("attribute", "bad_value"), [("height", 2.0), ("width", 0.75)]
)
def test_existing_dim_text_style_geometry_conflicts_are_read_only(attribute, bad_value):
    spec = assembly_spec()
    standard = gb_profile()
    document = ezdxf.new("R2018")
    values = {"height": 0.0, "width": 1.0}
    values[attribute] = bad_value
    document.styles.add("DIM", font="Arial", dxfattribs=values)
    before = _document_structure(document)

    with pytest.raises(ValueError, match=f"DIM text style {attribute}"):
        apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert _document_structure(document) == before


@pytest.mark.parametrize(
    ("attribute", "bad_value"),
    [("color", 2), ("linetype", "HIDDEN"), ("lineweight", 25)],
)
def test_existing_required_layer_conflicts_are_read_only(attribute, bad_value):
    spec = assembly_spec()
    standard = gb_profile()
    document = ezdxf.new("R2018")
    values = {"color": 7, "linetype": "CONTINUOUS", "lineweight": 70}
    values[attribute] = bad_value
    document.layers.add(
        "OUTLINE",
        color=values["color"],
        linetype=values["linetype"],
        lineweight=values["lineweight"],
    )
    before = _document_structure(document)

    with pytest.raises(ValueError, match=f"OUTLINE layer {attribute}"):
        apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())

    assert _document_structure(document) == before


def test_six_part_front_right_balloons_are_collision_solved_outside_views():
    spec = assembly_spec(parts=6)
    spec.requirements.views = ["front", "right"]
    plan = plan_layout(spec, gb_profile())
    finalized = apply_annotations(
        ezdxf.new("R2018"), plan, spec, gb_profile(), resolved_font_map()
    )
    outside = [
        annotation
        for annotation in finalized.annotations
        if annotation.group_id.startswith("balloon-")
        and annotation.kind in {"balloon", "balloon-text", "leader"}
    ]

    assert {annotation.group_id for annotation in outside} == {
        f"balloon-{number}" for number in range(1, 7)
    }
    assert all(
        not annotation.bounds.intersects(view.bounds)
        for annotation in outside
        for view in finalized.views
    )
    for index, annotation in enumerate(outside):
        for other in outside[index + 1 :]:
            if annotation.group_id != other.group_id:
                assert not annotation.bounds.intersects(other.bounds)


@pytest.mark.parametrize("views", [["front"], ["front", "right"]])
def test_extreme_finite_geometry_returns_compact_replannable_scale(views):
    spec = assembly_spec()
    spec.project.drawing_type = "part"
    spec.parts_list = []
    spec.requirements.views = views
    spec.geometry.parameters["view_sizes_mm"] = {
        name: [1e300, 5e299] for name in views
    }

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    suggestion = error.value.suggested_scale
    assert len(suggestion) < 32
    left, right = suggestion.split(":", 1)
    assert Decimal(left) > 0 and Decimal(right) > 0
    spec.sheet.scale = suggestion
    assert plan_layout(spec, gb_profile()).views


def test_direct_scale_below_binary64_range_is_a_controlled_layout_error():
    spec = assembly_spec()
    spec.project.drawing_type = "part"
    spec.parts_list = []
    spec.requirements.views = ["front"]
    spec.sheet.scale = "1:1e400"

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == "1:1e400"
    assert error.value.suggested_scale == "representable-scale-required"
    assert error.value.reason == "scale-not-representable"
    assert "choose a positive finite representable scale" in str(error.value)
    assert "representable-scale-required" not in str(error.value)


def test_generated_candidate_below_binary64_range_is_controlled():
    spec = assembly_spec()
    spec.project.drawing_type = "part"
    spec.parts_list = []
    spec.requirements.views = ["front"]
    spec.sheet.scale = "1:1e10"
    standard = gb_profile().model_copy(deep=True)
    standard.spacing.view_gap_mm = 1000.0

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, standard)

    assert error.value.requested_scale == "1:1e10"
    assert error.value.suggested_scale == "representable-scale-required"
    assert error.value.reason == "scale-not-representable"


@pytest.mark.parametrize(
    "requested",
    ["0:1", "-1:1", "1:0", "NaN:1", "Infinity:1", "1:Infinity", "not-a-scale"],
)
def test_malformed_or_nonpositive_requested_scale_uses_representation_sentinel(requested):
    spec = assembly_spec()
    spec.sheet.scale = requested

    with pytest.raises(LayoutOverflowError) as error:
        plan_layout(spec, gb_profile())

    assert error.value.requested_scale == requested
    assert error.value.suggested_scale == "representable-scale-required"
    assert error.value.reason == "scale-not-representable"
    assert "choose a positive finite representable scale" in str(error.value)


def test_annotation_application_rejects_unrepresentable_view_scale_atomically():
    spec = assembly_spec()
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    invalid = replace(
        plan,
        views=(replace(plan.views[0], scale=0.0), *plan.views[1:]),
    )
    document = ezdxf.new("R2018")
    before = _document_structure(document)

    with pytest.raises(LayoutOverflowError) as error:
        apply_annotations(document, invalid, spec, standard, resolved_font_map())

    assert error.value.reason == "scale-not-representable"
    assert error.value.suggested_scale == "representable-scale-required"
    assert _document_structure(document) == before


def test_annotation_application_rejects_negative_view_scale_atomically():
    spec = assembly_spec()
    standard = gb_profile()
    plan = plan_layout(spec, standard)
    invalid = replace(
        plan,
        views=(replace(plan.views[0], scale=-1.0), *plan.views[1:]),
    )
    document = ezdxf.new("R2018")
    before = _document_structure(document)

    with pytest.raises(LayoutOverflowError) as error:
        apply_annotations(document, invalid, spec, standard, resolved_font_map())

    assert error.value.reason == "scale-not-representable"
    assert error.value.suggested_scale == "representable-scale-required"
    assert _document_structure(document) == before


def test_render_dependent_source_entities_remain_measured_inside_their_views(tmp_path):
    values = assembly_spec().model_dump(mode="python")
    values["project"]["drawing_type"] = "part"
    values["parts_list"] = []
    values["dimensions"] = []
    values["annotations"] = []
    values["sheet"]["scale"] = "1:2"
    values["requirements"]["views"] = ["front", "right", "top", "section-A"]
    values["geometry"] = {
        "view_entity_indices": {
            "front": [0],
            "right": [1],
            "top": [2],
            "section-A": [3],
        },
        "entities": [
            {"kind": "mtext", "layer": "TEXT", "text": "LONG MTEXT CONTENT " * 4, "insert": [0.0, 30.0], "height": 3.5, "width": 24.0, "source_id": "source"},
            {"kind": "dimension", "layer": "DIM", "start": [0.0, 0.0], "end": [50.0, 0.0], "offset": 12.0, "text": "A VERY LONG DIMENSION LABEL", "source_id": "source"},
            {"kind": "text", "layer": "TEXT", "text": "ROTATED TEXT", "insert": [0.0, 0.0], "height": 5.0, "rotation": 37.0, "style": "normal", "source_id": "source"},
            {"kind": "leader", "layer": "DIM", "vertices": [[0.0, 0.0], [20.0, 15.0], [35.0, 15.0]], "source_id": "source"},
        ],
    }
    spec = DrawingSpec.model_validate(values)
    standard = gb_profile()
    source = write_dxf(spec, standard, resolved_font_map(), tmp_path / "render-dependent.dxf")
    document = ezdxf.readfile(source.path)

    finalized = apply_annotations(document, plan_layout(spec, standard), spec, standard, resolved_font_map())
    document.saveas(tmp_path / "render-dependent-final.dxf")
    reopened = ezdxf.readfile(tmp_path / "render-dependent-final.dxf")

    entities = list(reopened.modelspace())
    for view in finalized.views:
        measured = bbox.extents([entities[index] for index in view.source_indices])
        assert view.geometry_bounds.min_x <= measured.extmin.x
        assert view.geometry_bounds.min_y <= measured.extmin.y
        assert measured.extmax.x <= view.geometry_bounds.max_x
        assert measured.extmax.y <= view.geometry_bounds.max_y
        assert view.bounds.min_x <= measured.extmin.x
        assert view.bounds.min_y <= measured.extmin.y
        assert measured.extmax.x <= view.bounds.max_x
        assert measured.extmax.y <= view.bounds.max_y


def resolved_font_map() -> dict[str, ResolvedFont]:
    return {
        role: ResolvedFont(
            primary="Arial",
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(ResolvedFontRun(0, 1, "A", "latin", "Arial"),),
        )
        for role in ("TEXT", "DIM")
    }
