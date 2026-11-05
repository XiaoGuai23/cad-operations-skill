"""Atomic rendered-entity layout finalization for ezdxf documents."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import replace
from io import StringIO
from math import isclose, isfinite
from sys import float_info

import ezdxf
from ezdxf import bbox
from ezdxf.lldxf.tagger import ascii_tags_loader, tag_compiler
from ezdxf.math import Matrix44

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard

from .models import AnnotationLayout, Bounds, GeometryBounds, LayoutOverflowError, LayoutPlan


_LINETYPE_PATTERNS = {
    "CENTER": [2.0, 1.25, -0.25, 0.25, -0.25],
    "HIDDEN": [1.0, 0.5, -0.5],
}
_FRAME_TEXT_CLEARANCE_MM = 2.0


class _CollisionError(ValueError):
    pass


def apply_annotations(
    doc: ezdxf.document.Drawing,
    plan: LayoutPlan,
    spec: DrawingSpec,
    standard: DrawingStandard | None = None,
    fonts: Mapping[str, ResolvedFont] | None = None,
) -> LayoutPlan:
    """Solve, measure, validate, and atomically apply a layout.

    The caller document is never used as a staging area. Existing resources
    are checked read-only, then a scratch clone receives additions, source
    transforms, candidate annotations, rendering, and collision checks.
    """

    del standard  # the plan is the immutable standard-derived resource contract
    scratch_base = deepcopy(doc)
    _validate_plan_scales(plan, spec.sheet.scale)
    _validate_existing_resources(scratch_base, plan, fonts)
    _install_missing_resources(scratch_base, plan, fonts)
    _transform_source_entities(scratch_base, plan, spec)
    plan = _center_measured_source_geometry(scratch_base, plan)

    solved: tuple[ezdxf.document.Drawing, LayoutPlan, tuple[tuple[int, int], ...]] | None = None
    last_collision: _CollisionError | None = None
    for candidate in _candidate_plans(plan):
        scratch = deepcopy(scratch_base)
        finalized, spans = _emit_measure(scratch, candidate)
        try:
            _validate_final_layout(finalized)
        except _CollisionError as error:
            last_collision = error
            continue
        solved = scratch, finalized, spans
        break

    if solved is None:
        raise LayoutOverflowError(spec.sheet.scale, "larger-sheet-required") from last_collision

    scratch, finalized, spans = solved
    success_text = _serialized(scratch)
    ezdxf.read(StringIO(success_text))
    backup = deepcopy(doc)
    try:
        _reload_document(doc, success_text)
        finalized = _measure_document(doc, finalized, spans, capture_handles=True)
        _validate_final_layout(finalized)
    except Exception:
        _reload_document(doc, _serialized(backup))
        raise
    return finalized


def _emit_measure(
    doc: ezdxf.document.Drawing, plan: LayoutPlan
) -> tuple[LayoutPlan, tuple[tuple[int, int], ...]]:
    modelspace = doc.modelspace()
    spans: list[tuple[int, int]] = []
    for annotation in plan.annotations:
        start = len(modelspace)
        _emit(modelspace, annotation, plan)
        spans.append((start, len(modelspace)))
    span_tuple = tuple(spans)
    measured = _measure_document(doc, plan, span_tuple)
    centered = _center_measured_view_labels(doc, measured, span_tuple)
    return _measure_document(doc, centered, span_tuple), span_tuple


def _center_measured_view_labels(
    doc: ezdxf.document.Drawing,
    plan: LayoutPlan,
    spans: tuple[tuple[int, int], ...],
) -> LayoutPlan:
    """Center view labels from their real rendered bounds, not text estimates."""

    entities = list(doc.modelspace())
    views = {view.name: view.bounds for view in plan.views}
    updated: list[AnnotationLayout] = []
    for annotation, (start, end) in zip(plan.annotations, spans, strict=True):
        if annotation.kind != "view-label" or annotation.owner_view is None:
            updated.append(annotation)
            continue
        desired_dx = views[annotation.owner_view].center[0] - annotation.bounds.center[0]
        lower = (
            plan.printable_bounds.min_x
            + _FRAME_TEXT_CLEARANCE_MM
            - annotation.bounds.min_x
        )
        upper = (
            plan.printable_bounds.max_x
            - _FRAME_TEXT_CLEARANCE_MM
            - annotation.bounds.max_x
        )
        if lower > upper:
            lower = plan.printable_bounds.min_x - annotation.bounds.min_x
            upper = plan.printable_bounds.max_x - annotation.bounds.max_x
        dx = _clamp(desired_dx, lower, upper)
        if isclose(dx, 0.0, abs_tol=1e-12):
            updated.append(annotation)
            continue
        matrix = Matrix44.translate(dx, 0.0, 0.0)
        for entity in entities[start:end]:
            entity.transform(matrix)
        updated.append(
            replace(
                annotation,
                bounds=Bounds(
                    annotation.bounds.min_x + dx,
                    annotation.bounds.min_y,
                    annotation.bounds.max_x + dx,
                    annotation.bounds.max_y,
                ),
                points=tuple((x + dx, y) for x, y in annotation.points),
            )
        )
    return replace(plan, annotations=tuple(updated))


def _measure_document(
    doc: ezdxf.document.Drawing,
    plan: LayoutPlan,
    spans: tuple[tuple[int, int], ...],
    *,
    capture_handles: bool = False,
) -> LayoutPlan:
    entities = list(doc.modelspace())
    measured_annotations = tuple(
        replace(
            annotation,
            bounds=_measured_bounds(entities[start:end], annotation.bounds),
            entity_handles=(
                _entity_handles(entities[start:end])
                if capture_handles
                else annotation.entity_handles
            ),
        )
        for annotation, (start, end) in zip(plan.annotations, spans, strict=True)
    )
    measured_views = []
    for view in plan.views:
        if not view.source_indices:
            measured_views.append(view)
            continue
        measured_views.append(
            replace(
                view,
                geometry_bounds=_measured_geometry_bounds(
                    [entities[index] for index in view.source_indices], view.geometry_bounds
                ),
                source_handles=(
                    _entity_handles([entities[index] for index in view.source_indices])
                    if capture_handles
                    else view.source_handles
                ),
            )
        )
    return replace(plan, annotations=measured_annotations, views=tuple(measured_views))


def _entity_handles(entities: list[object]) -> tuple[str, ...]:
    """Capture handles only from the successfully committed real document."""

    return tuple(str(entity.dxf.handle) for entity in entities)


def _measured_bounds(entities: list[object], fallback: Bounds) -> Bounds:
    measured = bbox.extents(entities)
    if not measured.has_data:
        return fallback
    min_x, min_y = float(measured.extmin.x), float(measured.extmin.y)
    max_x, max_y = float(measured.extmax.x), float(measured.extmax.y)
    epsilon = 1e-6
    if max_x <= min_x:
        min_x -= epsilon
        max_x += epsilon
    if max_y <= min_y:
        min_y -= epsilon
        max_y += epsilon
    return Bounds(min_x, min_y, max_x, max_y)


def _measured_geometry_bounds(
    entities: list[object], fallback: GeometryBounds | None
) -> GeometryBounds | None:
    measured = bbox.extents(entities)
    if not measured.has_data:
        return fallback
    return GeometryBounds(
        float(measured.extmin.x),
        float(measured.extmin.y),
        float(measured.extmax.x),
        float(measured.extmax.y),
    )


def _center_measured_source_geometry(
    doc: ezdxf.document.Drawing, plan: LayoutPlan
) -> LayoutPlan:
    """Center scratch-rendered source bboxes in their reserved view envelopes."""

    entities = list(doc.modelspace())
    views = []
    for view in plan.views:
        if not view.source_indices:
            views.append(view)
            continue
        measured = _measured_geometry_bounds(
            [entities[index] for index in view.source_indices], view.geometry_bounds
        )
        assert measured is not None
        if _contains_geometry(view.bounds, measured):
            views.append(replace(view, geometry_bounds=measured))
            continue
        width = measured.max_x - measured.min_x
        height = measured.max_y - measured.min_y
        if width > view.bounds.width + 1e-8 or height > view.bounds.height + 1e-8:
            raise LayoutOverflowError("measured-source", "smaller-scale-required")
        dx = view.bounds.center[0] - (measured.min_x + measured.max_x) / 2
        dy = view.bounds.center[1] - (measured.min_y + measured.max_y) / 2
        if abs(dx) > 1e-12 or abs(dy) > 1e-12:
            matrix = Matrix44.translate(dx, dy, 0.0)
            for index in view.source_indices:
                entities[index].transform(matrix)
            measured = GeometryBounds(
                measured.min_x + dx,
                measured.min_y + dy,
                measured.max_x + dx,
                measured.max_y + dy,
            )
        views.append(
            replace(
                view,
                translation=(view.translation[0] + dx, view.translation[1] + dy),
                geometry_bounds=measured,
            )
        )
    return replace(plan, views=tuple(views))


def _candidate_plans(plan: LayoutPlan) -> Iterable[LayoutPlan]:
    balloon_groups = sorted(
        {annotation.group_id for annotation in plan.annotations if annotation.group_id.startswith("balloon-")},
        key=lambda value: int(value.rsplit("-", 1)[1]),
    )
    if not balloon_groups:
        yield plan
        return
    for lane in ("top", "right", "bottom", "left"):
        yield _place_balloon_lane(plan, balloon_groups, lane)


def _place_balloon_lane(
    plan: LayoutPlan, groups: list[str], lane: str
) -> LayoutPlan:
    owner = next(
        annotation.owner_view
        for annotation in plan.annotations
        if annotation.group_id == groups[0]
    )
    view = next(view for view in plan.views if view.name == owner)
    radius = 3.5
    pitch = 10.0
    if lane in {"top", "bottom"}:
        start = view.bounds.center[0] - pitch * (len(groups) - 1) / 2
        y = view.bounds.max_y + 12.0 + radius if lane == "top" else view.bounds.min_y - 12.0 - radius
        centers = {group: (start + index * pitch, y) for index, group in enumerate(groups)}
    else:
        start = view.bounds.center[1] - pitch * (len(groups) - 1) / 2
        x = view.bounds.max_x + 12.0 + radius if lane == "right" else view.bounds.min_x - 12.0 - radius
        centers = {group: (x, start + index * pitch) for index, group in enumerate(groups)}

    updated: list[AnnotationLayout] = []
    for annotation in plan.annotations:
        center = centers.get(annotation.group_id)
        if center is None:
            updated.append(annotation)
            continue
        if lane == "top":
            start_point = (center[0], center[1] - radius)
            end_point = (_clamp(center[0], view.bounds.min_x, view.bounds.max_x), view.bounds.max_y)
        elif lane == "bottom":
            start_point = (center[0], center[1] + radius)
            end_point = (_clamp(center[0], view.bounds.min_x, view.bounds.max_x), view.bounds.min_y)
        elif lane == "right":
            start_point = (center[0] - radius, center[1])
            end_point = (view.bounds.max_x, _clamp(center[1], view.bounds.min_y, view.bounds.max_y))
        else:
            start_point = (center[0] + radius, center[1])
            end_point = (view.bounds.min_x, _clamp(center[1], view.bounds.min_y, view.bounds.max_y))
        if annotation.kind == "balloon":
            updated.append(replace(annotation, bounds=Bounds(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), points=(center,), radius=radius))
        elif annotation.kind == "balloon-text":
            width, height = annotation.bounds.width, annotation.bounds.height
            point = (center[0] - width / 2, center[1] - height / 2)
            updated.append(replace(annotation, bounds=Bounds(point[0], point[1], point[0] + width, point[1] + height), points=(point,)))
        elif annotation.kind == "leader":
            updated.append(replace(annotation, bounds=_line_bounds(start_point, end_point), points=(start_point, end_point)))
        else:
            updated.append(annotation)
    return replace(plan, annotations=tuple(updated))


def _line_bounds(start: tuple[float, float], end: tuple[float, float]) -> Bounds:
    epsilon = 1e-6
    min_x, max_x = sorted((start[0], end[0]))
    min_y, max_y = sorted((start[1], end[1]))
    if min_x == max_x:
        min_x -= epsilon
        max_x += epsilon
    if min_y == max_y:
        min_y -= epsilon
        max_y += epsilon
    return Bounds(min_x, min_y, max_x, max_y)


def _validate_final_layout(plan: LayoutPlan) -> None:
    zones = {
        "printable": plan.printable_bounds,
        "title": plan.title_block_bounds,
        "technical": plan.technical_note_bounds,
        "parts": plan.parts_list.bounds,
    }
    views = {view.name: view.bounds for view in plan.views}
    protected = (plan.title_block_bounds, plan.technical_note_bounds, plan.parts_list.bounds)
    for view in plan.views:
        if view.geometry_bounds is not None and not _contains_geometry(view.bounds, view.geometry_bounds):
            raise _CollisionError(f"measured source geometry escaped view {view.name}")
    for annotation in plan.annotations:
        if not plan.printable_bounds.contains(annotation.bounds):
            raise _CollisionError(f"annotation {annotation.annotation_id} escaped printable bounds")
        if annotation.owner_zone is not None:
            if not zones[annotation.owner_zone].contains(annotation.bounds):
                raise _CollisionError(f"annotation {annotation.annotation_id} escaped its {annotation.owner_zone} zone")
            continue
        if any(annotation.bounds.intersects(zone) for zone in protected):
            raise _CollisionError(f"outside annotation {annotation.annotation_id} entered a protected zone")
        for view_name, view_bounds in views.items():
            if annotation.bounds.intersects(view_bounds):
                if (
                    annotation.kind == "dimension"
                    and annotation.owner_view == view_name
                    and _shallow_edge_overlap(
                        annotation.bounds,
                        view_bounds,
                        plan.dimension_arrow_size_mm,
                    )
                ):
                    continue
                raise _CollisionError(f"annotation {annotation.annotation_id} collided with view {view_name}")

    for index, annotation in enumerate(plan.annotations):
        for other in plan.annotations[index + 1 :]:
            if annotation.bounds.intersects(other.bounds) and not _allowed_pair(annotation, other):
                raise _CollisionError(f"annotations {annotation.annotation_id} and {other.annotation_id} collide")
    leaders = [annotation for annotation in plan.annotations if annotation.kind == "leader" and len(annotation.points) >= 2]
    for index, leader in enumerate(leaders):
        for other in leaders[index + 1 :]:
            if leader.group_id != other.group_id and _segments_cross(leader.points[-2:], other.points[-2:]):
                raise _CollisionError(f"leaders {leader.annotation_id} and {other.annotation_id} cross")


def _allowed_pair(first: AnnotationLayout, second: AnnotationLayout) -> bool:
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


def _shallow_edge_overlap(first: Bounds, second: Bounds, maximum: float) -> bool:
    overlap_x = min(first.max_x, second.max_x) - max(first.min_x, second.min_x)
    overlap_y = min(first.max_y, second.max_y) - max(first.min_y, second.min_y)
    return overlap_x > 0 and overlap_y > 0 and min(overlap_x, overlap_y) <= maximum + 1e-8


def _segments_cross(
    first: tuple[tuple[float, float], ...], second: tuple[tuple[float, float], ...]
) -> bool:
    a, b = first
    c, d = second

    def orientation(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(c, d, a) * orientation(c, d, b) < 0


def _contains_geometry(bounds: Bounds, geometry: GeometryBounds) -> bool:
    tolerance = 1e-8
    return bounds.min_x - tolerance <= geometry.min_x and bounds.min_y - tolerance <= geometry.min_y and geometry.max_x <= bounds.max_x + tolerance and geometry.max_y <= bounds.max_y + tolerance


def _validate_existing_resources(
    doc: ezdxf.document.Drawing,
    plan: LayoutPlan,
    fonts: Mapping[str, ResolvedFont] | None,
) -> None:
    text_font = _verified_font(fonts, "TEXT")
    dim_font = _verified_font(fonts, "DIM")
    if text_font is None or dim_font is None:
        raise ValueError("verified TEXT and DIM font selections are required")
    layers = {name: (linetype, lineweight, color) for name, linetype, lineweight, color in plan.layer_styles}
    for linetype in sorted({values[0] for values in layers.values()}):
        if linetype.casefold() == "continuous" or linetype not in doc.linetypes:
            continue
        expected = _normalized_pattern(_LINETYPE_PATTERNS.get(linetype, [0.5, -0.25])[1:])
        actual = _normalized_pattern([tag.value for tag in doc.linetypes.get(linetype).pattern_tags.tags if tag.code == 49])
        if actual != expected:
            raise ValueError(f"existing {linetype} linetype pattern conflicts with the layout standard")
    for name, (linetype, lineweight, color) in layers.items():
        if name not in doc.layers:
            continue
        layer = doc.layers.get(name)
        if layer.color != color:
            raise ValueError(f"existing {name} layer color conflicts with the layout standard")
        if layer.dxf.linetype.casefold() != linetype.casefold():
            raise ValueError(f"existing {name} layer linetype conflicts with the layout standard")
        if layer.dxf.lineweight != round(lineweight * 100):
            raise ValueError(f"existing {name} layer lineweight conflicts with the layout standard")

    styles = {name: (font, height, width) for name, font, height, width in plan.text_styles}
    for name, (font, height, width) in styles.items():
        if name not in doc.styles:
            continue
        style = doc.styles.get(name)
        expected_font = text_font if font == "project-default" else font
        if style.dxf.font != expected_font:
            raise ValueError(f"existing {name} text style font conflicts with the layout standard")
        if not isclose(style.dxf.height, height, abs_tol=1e-9):
            raise ValueError(f"existing {name} text style height conflicts with the layout standard")
        if not isclose(style.dxf.width, width, abs_tol=1e-9):
            raise ValueError(f"existing {name} text style width conflicts with the layout standard")
    if "DIM" in doc.styles:
        style = doc.styles.get("DIM")
        if style.dxf.font != dim_font:
            raise ValueError("existing DIM text style font conflicts with the verified DIM font")
        if not isclose(style.dxf.height, 0.0, abs_tol=1e-9):
            raise ValueError("existing DIM text style height conflicts with the layout standard")
        if not isclose(style.dxf.width, 1.0, abs_tol=1e-9):
            raise ValueError("existing DIM text style width conflicts with the layout standard")
    if "DIM" in doc.dimstyles:
        dimstyle = doc.dimstyles.get("DIM")
        expected = (("dimtxsty", dimstyle.dxf.dimtxsty, "DIM"), ("dimtxt", dimstyle.dxf.dimtxt, plan.dimension_text_height_mm), ("dimgap", dimstyle.dxf.dimgap, plan.dimension_gap_mm), ("dimasz", dimstyle.dxf.dimasz, plan.dimension_arrow_size_mm), ("dimtad", dimstyle.dxf.dimtad, 0))
        for field, actual, wanted in expected:
            equal = actual == wanted if isinstance(wanted, str) else isclose(actual, wanted, abs_tol=1e-9)
            if not equal:
                raise ValueError(f"existing DIM dimension style {field} conflicts with the layout plan")


def _install_missing_resources(
    doc: ezdxf.document.Drawing,
    plan: LayoutPlan,
    fonts: Mapping[str, ResolvedFont] | None,
) -> None:
    text_font = _verified_font(fonts, "TEXT")
    dim_font = _verified_font(fonts, "DIM")
    assert text_font is not None and dim_font is not None
    layers = {name: (linetype, lineweight, color) for name, linetype, lineweight, color in plan.layer_styles}
    for name in sorted({value[0] for value in layers.values()}):
        if name.casefold() != "continuous" and name not in doc.linetypes:
            doc.linetypes.add(name, pattern=_LINETYPE_PATTERNS.get(name, [0.5, -0.25]), description=f"{name} generated")
    for name, (linetype, lineweight, color) in layers.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=linetype, lineweight=round(lineweight * 100))
    styles = {name: (font, height, width) for name, font, height, width in plan.text_styles}
    for name, (font, height, width) in styles.items():
        if name not in doc.styles:
            doc.styles.add(name, font=text_font if font == "project-default" else font, dxfattribs={"height": height, "width": width})
    if "DIM" not in doc.styles:
        doc.styles.add("DIM", font=dim_font, dxfattribs={"height": 0.0, "width": 1.0})
    if "DIM" not in doc.dimstyles:
        doc.dimstyles.add("DIM", dxfattribs={"dimtxsty": "DIM", "dimtxt": plan.dimension_text_height_mm, "dimgap": plan.dimension_gap_mm, "dimasz": plan.dimension_arrow_size_mm, "dimtad": 0})
    render_roles = ",".join(role for role in ("TEXT", "DIM") if fonts and fonts[role].requires_render_verification)
    if render_roles:
        doc.ezdxf_metadata()["LAYOUT_FONT_ROLES_REQUIRING_RENDER_VERIFICATION"] = render_roles


def _validate_plan_scales(plan: LayoutPlan, requested_scale: str) -> None:
    for view in plan.views:
        if (
            not isfinite(view.scale)
            or view.scale <= 0.0
            or abs(view.scale) < float_info.min
        ):
            raise LayoutOverflowError(
                requested_scale,
                "representable-scale-required",
                reason="scale-not-representable",
            )


def _normalized_pattern(values: list[float]) -> tuple[float, ...]:
    total = sum(abs(value) for value in values)
    return () if total == 0 else tuple(round(value / total, 9) for value in values)


def _verified_font(fonts: Mapping[str, ResolvedFont] | None, role: str) -> str | None:
    resolved = fonts.get(role) if fonts else None
    if resolved is None or resolved.primary is None or resolved.missing_characters:
        return None
    return resolved.primary_path.name if resolved.primary_path is not None else resolved.primary


def _transform_source_entities(
    doc: ezdxf.document.Drawing, plan: LayoutPlan, spec: DrawingSpec
) -> None:
    source_entities = tuple(spec.geometry.entities)
    if not source_entities:
        return
    drawing_entities = list(doc.modelspace())
    if len(drawing_entities) < len(source_entities):
        raise ValueError("document does not contain the source geometry required by the layout plan")
    for view in plan.views:
        if view.source_indices:
            matrix = Matrix44.chain(
                Matrix44.scale(view.scale, view.scale, 1.0),
                Matrix44.translate(view.translation[0], view.translation[1], 0.0),
            )
            for index in view.source_indices:
                drawing_entities[index].transform(matrix)


def _emit(modelspace: ezdxf.layouts.Modelspace, annotation: AnnotationLayout, plan: LayoutPlan) -> None:
    if annotation.kind == "frame":
        bounds = annotation.bounds
        modelspace.add_lwpolyline([(bounds.min_x, bounds.min_y), (bounds.max_x, bounds.min_y), (bounds.max_x, bounds.max_y), (bounds.min_x, bounds.max_y)], close=True, dxfattribs={"layer": annotation.layer})
    elif annotation.kind in {"title-label", "title", "drawing-number", "scale", "projection", "notes-header", "parts-header", "parts-column", "parts-row", "view-label", "technical-note", "annotation", "balloon-text"}:
        modelspace.add_text(annotation.text, dxfattribs={"layer": annotation.layer, "style": annotation.style, "height": plan.text_height_mm}).set_placement(annotation.points[0])
    elif annotation.kind == "dimension":
        dimension = modelspace.add_aligned_dim(
            annotation.points[0],
            (annotation.points[1][0], annotation.points[0][1]),
            annotation.points[1][1] - annotation.points[0][1],
            text=annotation.text,
            dimstyle="DIM",
            dxfattribs={"layer": annotation.layer},
        )
        dimension.render()
    elif annotation.kind == "leader":
        modelspace.add_leader(annotation.points, dxfattribs={"layer": annotation.layer})
    elif annotation.kind == "balloon":
        modelspace.add_circle(annotation.points[0], annotation.radius, dxfattribs={"layer": annotation.layer})
    else:
        raise ValueError(f"unsupported planned annotation: {annotation.kind}")


def _serialized(doc: ezdxf.document.Drawing) -> str:
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue()


def _reload_document(doc: ezdxf.document.Drawing, content: str) -> None:
    doc.__init__()
    doc._load(tag_compiler(ascii_tags_loader(StringIO(content))))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)
