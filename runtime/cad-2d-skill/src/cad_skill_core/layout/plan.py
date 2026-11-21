"""Constraint-based deterministic placement for GB/T mechanical sheets."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from math import atan, atan2, ceil, cos, degrees, hypot, isfinite, radians, sin
from sys import float_info

from cad_skill_core.fonts.metrics import measure_text_width_mm
from cad_skill_core.geometry.contours import compile_contour
from cad_skill_core.geometry.entities import ArcEntity, CircleEntity, CircularEntity, ContourEntity, DimensionEntity, HatchEntity, LeaderEntity, LineEntity, MTextEntity, PolylineEntity, SplineEntity, TextEntity
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard, ProtectedZone

from .models import AnnotationLayout, Bounds, GeometryBounds, LayoutOverflowError, LayoutPlan, PartsListLayout, PartListRow, ViewLayout


_VIEW_PRIORITY = ("front", "top", "bottom", "right", "left")
_PRINT_MARGIN_MM = 10.0
_FRAME_CONTENT_CLEARANCE_MM = 2.0
_TABLE_ROW_HEIGHT_MM = 8.0
_TEXT_LINE_HEIGHT_MM = 8.0


class _ScaleRepresentationError(ValueError):
    pass


def plan_layout(spec: DrawingSpec, standard: DrawingStandard) -> LayoutPlan:
    """Return a deterministic geometry-aware sheet plan.

    ``Geometry.view_entity_indices`` explicitly assigns entities to views.
    Legacy specifications assign all entities to ``front`` (or the first view).
    Each view retains the uniform scale and translation used to transform its
    associated source DXF entities during annotation application.
    """
    requested_scale = spec.sheet.scale
    try:
        requested = _parse_scale(requested_scale)
        plan = _plan_at_scale(spec, standard, requested)
    except _ScaleRepresentationError as error:
        raise _unrepresentable_scale(requested_scale) from error
    if plan is not None:
        return plan
    fixed_constraint_issue = _fixed_constraint_issue(spec, standard)
    if fixed_constraint_issue == "larger-sheet-required":
        raise LayoutOverflowError(requested_scale, "larger-sheet-required")
    if fixed_constraint_issue == "fixed-content-overflow":
        raise LayoutOverflowError(
            requested_scale,
            "wrap-content-or-larger-sheet",
            reason="fixed-content-overflow",
        )
    for candidate, label in _fitting_scale_candidates(requested):
        try:
            if _plan_at_scale(spec, standard, candidate) is not None:
                raise LayoutOverflowError(requested_scale, label)
        except _ScaleRepresentationError as error:
            raise _unrepresentable_scale(requested_scale) from error
    raise AssertionError("unreachable engineering scale search")


def _fixed_constraint_issue(
    spec: DrawingSpec,
    standard: DrawingStandard,
) -> str | None:
    sheet = standard.sheets[spec.sheet.size]
    width, height = (sheet.height_mm, sheet.width_mm) if spec.sheet.orientation == "portrait" else (sheet.width_mm, sheet.height_mm)
    printable = Bounds(_PRINT_MARGIN_MM, _PRINT_MARGIN_MM, width - _PRINT_MARGIN_MM, height - _PRINT_MARGIN_MM)
    title = _protected_bounds(printable, standard.protected_zones["title_block"])
    zones = _side_zones(
        printable,
        title,
        len(_parts_rows(spec)),
        len(spec.requirements.technical_notes) + len(spec.annotations),
    )
    if zones is None:
        return "larger-sheet-required"
    parts_bounds, notes_bounds = zones
    text_height = standard.text_styles["normal"].height_mm
    width_factor = standard.text_styles["normal"].width_factor
    title_values = tuple(text for _, text, _ in _title_block_entries(spec, title))
    fixed_widths = (
        *((value, title.width - 10.0) for value in title_values),
        *((note.text, notes_bounds.width - 5.0) for note in spec.requirements.technical_notes),
        *((annotation.text, notes_bounds.width - 5.0) for annotation in spec.annotations),
        *((row.item, parts_bounds.width - 55.0) for row in _parts_rows(spec)),
        *((str(row.quantity), 20.0) for row in _parts_rows(spec)),
    )
    if any(
        max(text_height, measure_text_width_mm(value, text_height, width_factor=width_factor)) > available
        for value, available in fixed_widths
    ):
        return "fixed-content-overflow"
    return None


def _plan_at_scale(spec: DrawingSpec, standard: DrawingStandard, scale: Decimal) -> LayoutPlan | None:
    _representable_scale(scale)
    sheet = standard.sheets[spec.sheet.size]
    width, height = (sheet.height_mm, sheet.width_mm) if spec.sheet.orientation == "portrait" else (sheet.width_mm, sheet.height_mm)
    printable = Bounds(_PRINT_MARGIN_MM, _PRINT_MARGIN_MM, width - _PRINT_MARGIN_MM, height - _PRINT_MARGIN_MM)
    title = _protected_bounds(printable, standard.protected_zones["title_block"])
    rows = _parts_rows(spec)
    zones = _side_zones(printable, title, len(rows), len(spec.requirements.technical_notes) + len(spec.annotations))
    if zones is None:
        return None
    parts_bounds, notes_bounds = zones
    view_region = Bounds(
        printable.min_x + _FRAME_CONTENT_CLEARANCE_MM,
        printable.min_y + _FRAME_CONTENT_CLEARANCE_MM,
        printable.max_x - _FRAME_CONTENT_CLEARANCE_MM,
        printable.max_y - _FRAME_CONTENT_CLEARANCE_MM,
    )
    views = _place_views(
        spec,
        scale,
        standard.spacing.view_gap_mm,
        view_region,
        (title, parts_bounds, notes_bounds),
    )
    if views is None:
        return None
    parts = PartsListLayout(parts_bounds, rows)
    annotations = _annotation_layouts(spec, views, printable, title, notes_bounds, parts, standard)
    if not all(printable.contains(annotation.bounds) for annotation in annotations):
        return None
    return LayoutPlan(
        Bounds(0.0, 0.0, width, height), printable, title, notes_bounds, parts, views, annotations,
        standard.text_styles["normal"].height_mm, standard.dimension.gap_mm,
        standard.dimension.text_height_mm, standard.dimension.arrow_size_mm,
        tuple((name, style.linetype, style.lineweight_mm, style.color) for name, style in sorted(standard.layers.items())),
        tuple((name, style.font, style.height_mm, style.width_factor) for name, style in sorted(standard.text_styles.items())),
    )


def _protected_bounds(printable: Bounds, zone: ProtectedZone) -> Bounds:
    margin = zone.margin_mm
    if zone.anchor == "bottom-right":
        return Bounds(printable.max_x - margin - zone.width_mm, printable.min_y + margin, printable.max_x - margin, printable.min_y + margin + zone.height_mm)
    if zone.anchor == "bottom-left":
        return Bounds(printable.min_x + margin, printable.min_y + margin, printable.min_x + margin + zone.width_mm, printable.min_y + margin + zone.height_mm)
    if zone.anchor == "top-right":
        return Bounds(printable.max_x - margin - zone.width_mm, printable.max_y - margin - zone.height_mm, printable.max_x - margin, printable.max_y - margin)
    return Bounds(printable.min_x + margin, printable.max_y - margin - zone.height_mm, printable.min_x + margin + zone.width_mm, printable.max_y - margin)


def _side_zones(printable: Bounds, title: Bounds, row_count: int, note_count: int) -> tuple[Bounds, Bounds] | None:
    bottom = title.max_y + _PRINT_MARGIN_MM
    parts_height = 16.0 + (row_count + 2) * _TABLE_ROW_HEIGHT_MM
    notes_height = 8.0 + (note_count + 1) * _TEXT_LINE_HEIGHT_MM
    notes_min_y = bottom + parts_height + _PRINT_MARGIN_MM
    if notes_min_y + notes_height > printable.max_y:
        return None
    return (
        Bounds(title.min_x, bottom, printable.max_x, bottom + parts_height),
        Bounds(title.min_x, notes_min_y, printable.max_x, notes_min_y + notes_height),
    )


def _parts_rows(spec: DrawingSpec) -> tuple[PartListRow, ...]:
    if spec.project.drawing_type != "assembly":
        return ()
    seen: set[str] = set()
    rows: list[PartListRow] = []
    for item_no, item in enumerate(spec.parts_list, start=1):
        if item.item.casefold() in seen:
            raise ValueError(f"assembly parts list item is not unique: {item.item}")
        seen.add(item.item.casefold())
        rows.append(PartListRow(item_no, item.item, item.quantity))
    return tuple(rows)


def _place_views(spec: DrawingSpec, scale: Decimal, gap: float, printable: Bounds, protected: tuple[Bounds, ...]) -> tuple[ViewLayout, ...] | None:
    names = _ordered_view_names(spec.requirements.views)
    groups = _entity_groups(spec, names)
    exact_sources = {name: _exact_group_bounds([spec.geometry.entities[index] for index in group]) if group else None for name, group in groups.items()}
    sources = {name: _group_bounds(spec, group) for name, group in groups.items()}
    sizes = {name: _view_size(spec, name, sources[name], scale) for name in names}
    placed: dict[str, Bounds] = {}
    if "front" in sizes:
        right_width = sizes.get("right", (0.0, 0.0))[0]
        right_left_pad = 10.0 if ("right" in sizes and any(d.owner_view == "right" for d in spec.dimensions)) else 0.0
        top_height = sizes.get("top", (0.0, 0.0))[1]
        balloon_margin = 8.0 * len(spec.parts_list) + 12.0 if spec.project.drawing_type == "assembly" and spec.parts_list and "right" not in sizes else (12.0 if any(d.orientation == "aligned" for d in spec.dimensions) else 0.0)
        dimension_margin = 12.0 * len(spec.dimensions)
        front = _bounds(printable.min_x + (right_left_pad + right_width + gap if "right" in sizes else balloon_margin), printable.min_y + (top_height + gap if "top" in sizes else 0.0) + dimension_margin, sizes["front"])
        if not _valid(front, protected, printable, gap):
            return None
        placed["front"] = front
        if "right" in sizes:
            right = _bounds(printable.min_x + right_left_pad, front.min_y, sizes["right"])
            if not _valid(right, (*protected, front), printable, gap):
                return None
            placed["right"] = right
        if "top" in sizes:
            top_y = printable.min_y + (
                12.0 * sum(1 for d in spec.dimensions if d.owner_view == "top" and d.orientation in {None, "horizontal"})
                if any(d.owner_view == "top" for d in spec.dimensions)
                else 0.0
            )
            top = _bounds(front.min_x, top_y, sizes["top"])
            if not _valid(top, (*protected, *placed.values()), printable, gap):
                return None
            placed["top"] = top
    for name in names:
        if name in placed:
            continue
        candidate = next((item for item in (*_preferred(name, placed, sizes[name], gap), *_grid(printable, sizes[name], gap)) if _valid(item, (*protected, *placed.values()), printable, gap)), None)
        if candidate is None:
            return None
        placed[name] = candidate
    return tuple(_view_layout(name, placed[name], sources[name], exact_sources[name], groups[name], scale, language=spec.sheet.title_block_language) for name in names)


def _entity_groups(spec: DrawingSpec, names: tuple[str, ...]) -> dict[str, tuple[int, ...]]:
    explicit = {name: tuple(indices) for name, indices in spec.geometry.view_entity_indices.items()}
    if explicit:
        return {name: explicit.get(name, ()) for name in names}
    default = "front" if "front" in names else (names[0] if names else "front")
    indices = tuple(range(len(spec.geometry.entities)))
    return {name: indices if name == default else () for name in names}


def _group_bounds(spec: DrawingSpec, indices: tuple[int, ...]) -> tuple[float, float, float, float] | None:
    entities = [spec.geometry.entities[index] for index in indices]
    if not entities:
        return None
    all_bounds = [_entity_envelope(entity) for entity in entities]
    return min(item[0] for item in all_bounds), min(item[1] for item in all_bounds), max(item[2] for item in all_bounds), max(item[3] for item in all_bounds)


def _entity_envelope(entity: object) -> tuple[float, float, float, float]:
    exact = _entity_exact_bounds(entity)
    epsilon = 0.0005
    return (
        exact.min_x - epsilon if exact.min_x == exact.max_x else exact.min_x,
        exact.min_y - epsilon if exact.min_y == exact.max_y else exact.min_y,
        exact.max_x + epsilon if exact.min_x == exact.max_x else exact.max_x,
        exact.max_y + epsilon if exact.min_y == exact.max_y else exact.max_y,
    )


def _exact_group_bounds(entities: list[object]) -> GeometryBounds:
    all_bounds = [_entity_exact_bounds(entity) for entity in entities]
    return GeometryBounds(min(item.min_x for item in all_bounds), min(item.min_y for item in all_bounds), max(item.max_x for item in all_bounds), max(item.max_y for item in all_bounds))


def _arc_span_bounds(center: tuple[float, float], radius: float, start_deg: float, ccw_sweep_deg: float) -> GeometryBounds:
    start = start_deg % 360.0
    angles = [start, (start + ccw_sweep_deg) % 360.0]
    for quadrant in (0.0, 90.0, 180.0, 270.0):
        if (quadrant - start) % 360.0 <= ccw_sweep_deg + 1e-12:
            angles.append(quadrant)
    quadrant_unit = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0), 180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}
    points = [
        (
            center[0] + radius * quadrant_unit[angle][0],
            center[1] + radius * quadrant_unit[angle][1],
        )
        if angle in quadrant_unit
        else (
            center[0] + radius * cos(radians(angle)),
            center[1] + radius * sin(radians(angle)),
        )
        for angle in angles
    ]
    values = min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)
    return GeometryBounds(*(0.0 if abs(value) < 1e-12 else value for value in values))


def _contour_exact_bounds(entity: ContourEntity) -> GeometryBounds:
    vertices = compile_contour(entity)
    xs: list[float] = []
    ys: list[float] = []
    count = len(vertices)
    for index, (x1, y1, bulge) in enumerate(vertices):
        x2, y2, _ = vertices[(index + 1) % count]
        xs.extend((x1, x2))
        ys.extend((y1, y2))
        if bulge == 0.0:
            continue
        dx, dy = x2 - x1, y2 - y1
        chord = hypot(dx, dy)
        ux, uy = dx / chord, dy / chord
        offset = chord * (1.0 / bulge - bulge) / 4.0
        cx = 0.5 * (x1 + x2) - uy * offset
        cy = 0.5 * (y1 + y2) + ux * offset
        radius = hypot(0.5 * chord, offset)
        sweep_deg = degrees(4.0 * atan(bulge))
        start_deg = degrees(atan2(y1 - cy, x1 - cx)) % 360.0
        ccw_start = start_deg if sweep_deg >= 0.0 else (start_deg + sweep_deg) % 360.0
        arc_bounds = _arc_span_bounds((cx, cy), radius, ccw_start, abs(sweep_deg))
        xs.extend((arc_bounds.min_x, arc_bounds.max_x))
        ys.extend((arc_bounds.min_y, arc_bounds.max_y))
    values = min(xs), min(ys), max(xs), max(ys)
    return GeometryBounds(*(0.0 if abs(value) < 1e-12 else value for value in values))


def _entity_exact_bounds(entity: object) -> GeometryBounds:
    if isinstance(entity, CircleEntity):
        return GeometryBounds(entity.center[0] - entity.radius, entity.center[1] - entity.radius, entity.center[0] + entity.radius, entity.center[1] + entity.radius)
    if isinstance(entity, CircularEntity):
        if entity.is_full_circle:
            return GeometryBounds(entity.center[0] - entity.radius, entity.center[1] - entity.radius, entity.center[0] + entity.radius, entity.center[1] + entity.radius)
        return _arc_span_bounds(entity.center, entity.radius, entity.start_angle, entity.sweep)
    if isinstance(entity, ContourEntity):
        return _contour_exact_bounds(entity)
    if isinstance(entity, ArcEntity):
        angles = [entity.start_angle, entity.end_angle]
        start, end = entity.start_angle % 360, entity.end_angle % 360
        for angle in (0, 90, 180, 270):
            if start <= end and start <= angle <= end or start > end and (angle >= start or angle <= end):
                angles.append(angle)
        points = [(entity.center[0] + entity.radius * cos(radians(angle)), entity.center[1] + entity.radius * sin(radians(angle))) for angle in angles]
        values = min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)
        return GeometryBounds(*(0.0 if abs(value) < 1e-12 else value for value in values))
    if isinstance(entity, LineEntity): points = (entity.start, entity.end)
    elif isinstance(entity, PolylineEntity): points = tuple(entity.vertices)
    elif isinstance(entity, HatchEntity): points = tuple(entity.boundary)
    elif isinstance(entity, SplineEntity): points = tuple(entity.control_points)
    elif isinstance(entity, LeaderEntity):
        points = tuple(entity.vertices)
        min_x, min_y, max_x, max_y = min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)
        return GeometryBounds(min_x - 2.0, min_y - 2.0, max_x + 2.0, max_y + 2.0)
    elif isinstance(entity, DimensionEntity):
        min_x, min_y, max_x, max_y = min(entity.start[0], entity.end[0]), min(entity.start[1], entity.end[1]), max(entity.start[0], entity.end[0]), max(entity.start[1], entity.end[1])
        text_height = 3.5
        text_width = max(1, len(entity.text or "<>")) * text_height
        pad = abs(entity.offset) + text_width + text_height * 2
        return GeometryBounds(min_x - pad, min_y - pad, max_x + pad, max_y + pad)
    elif isinstance(entity, TextEntity):
        text_width = len(entity.text) * entity.height
        descent = entity.height * 0.35
        corners = ((0.0, -descent), (text_width, -descent), (text_width, entity.height), (0.0, entity.height))
        rotation = radians(entity.rotation)
        points = tuple((entity.insert[0] + x * cos(rotation) - y * sin(rotation), entity.insert[1] + x * sin(rotation) + y * cos(rotation)) for x, y in corners)
    elif isinstance(entity, MTextEntity):
        lines = entity.text.replace("\\P", "\n").splitlines() or [""]
        capacity = max(1, int((entity.width or (max(map(len, lines)) * entity.height)) / entity.height))
        line_count = sum(max(1, ceil(len(line) / capacity)) for line in lines)
        width = entity.width or max(map(len, lines)) * entity.height
        descent = entity.height * 0.35
        line_height = entity.height * 1.42
        return GeometryBounds(entity.insert[0] - entity.height * 0.1, entity.insert[1] - line_count * line_height - descent, entity.insert[0] + width + entity.height * 0.1, entity.insert[1] + entity.height * 1.1)
    else: raise TypeError(f"unsupported entity: {entity!r}")
    min_x, min_y, max_x, max_y = min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)
    return GeometryBounds(min_x, min_y, max_x, max_y)


def _view_size(spec: DrawingSpec, name: str, source: tuple[float, float, float, float] | None, scale: Decimal) -> tuple[float, float]:
    if source is not None:
        return _scaled(source[2] - source[0], scale), _scaled(source[3] - source[1], scale)
    configured = spec.geometry.parameters.get("view_sizes_mm", spec.geometry.parameters.get("view_sizes", {}))
    raw = configured.get(name) if isinstance(configured, dict) else None
    if isinstance(raw, dict): width, height = raw.get("width_mm", raw.get("width")), raw.get("height_mm", raw.get("height"))
    elif isinstance(raw, (list, tuple)) and len(raw) == 2: width, height = raw
    else: width, height = 80.0, 50.0
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or width <= 0 or height <= 0: raise ValueError(f"view size for {name} must contain positive width and height")
    return _scaled(float(width), scale), _scaled(float(height), scale)


def _view_layout(name: str, bounds: Bounds, source: tuple[float, float, float, float] | None, exact_source: GeometryBounds | None, source_indices: tuple[int, ...], scale: Decimal, *, language: str = "en") -> ViewLayout:
    numeric_scale = float(scale)
    translation = (bounds.min_x, bounds.min_y) if source is None else (bounds.min_x - source[0] * numeric_scale, bounds.min_y - source[1] * numeric_scale)
    geometry = None if exact_source is None else GeometryBounds(exact_source.min_x * numeric_scale + translation[0], exact_source.min_y * numeric_scale + translation[1], exact_source.max_x * numeric_scale + translation[0], exact_source.max_y * numeric_scale + translation[1])
    return ViewLayout(name, bounds, _view_label(name, language=language), source, geometry, source_indices, numeric_scale, translation, (_label_bounds(bounds),))


def _scaled(value: float, scale: Decimal) -> float:
    result = float(Decimal(str(value)) * scale)
    if not isfinite(result):
        raise _ScaleRepresentationError("scaled view size is not finite")
    return result


def _representable_scale(scale: Decimal) -> float:
    numeric = float(scale)
    if not isfinite(numeric) or numeric <= 0.0 or numeric < float_info.min:
        raise _ScaleRepresentationError("scale is outside the stable binary64 range")
    return numeric


def _unrepresentable_scale(requested_scale: str) -> LayoutOverflowError:
    return LayoutOverflowError(
        requested_scale,
        "representable-scale-required",
        reason="scale-not-representable",
    )


def _bounds(x: float, y: float, size: tuple[float, float]) -> Bounds:
    max_x, max_y = x + size[0], y + size[1]
    if max_x <= x or max_y <= y:
        raise _ScaleRepresentationError("scaled view size is below sheet-coordinate precision")
    return Bounds(x, y, max_x, max_y)
def _preferred(name: str, placed: dict[str, Bounds], size: tuple[float, float], gap: float) -> tuple[Bounds, ...]:
    front = placed.get("front")
    if front is None: return ()
    if name.startswith("section") or name == "left": return (_bounds(front.max_x + gap, front.min_y, size),)
    if name == "bottom": return (_bounds(front.min_x, front.max_y + gap, size),)
    return ()
def _grid(printable: Bounds, size: tuple[float, float], gap: float) -> Iterable[Bounds]:
    y = printable.min_y
    while y + size[1] <= printable.max_y:
        x = printable.min_x
        while x + size[0] <= printable.max_x:
            yield _bounds(x, y, size); x += gap
        y += gap
def _valid(candidate: Bounds, occupied: tuple[Bounds, ...], printable: Bounds, gap: float) -> bool: return printable.contains(candidate) and all(candidate.clearance_to(item) + 1e-9 >= gap for item in occupied)
def _ordered_view_names(views: Iterable[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(views)); return tuple([name for name in _VIEW_PRIORITY if name in requested] + sorted(name for name in requested if name not in _VIEW_PRIORITY))
def _view_label(name: str, *, language: str = "en") -> str:
    if language == "zh":
        zh_map = {
            "front": "主视图",
            "top": "俯视图",
            "bottom": "仰视图",
            "left": "左视图",
            "right": "右视图",
        }
        if name in zh_map:
            return zh_map[name]
        if name.startswith("section-"):
            section = name.removeprefix("section-").upper()
            return f"{section}-{section}剖视"
        return name.upper()
    if name.startswith("section-"):
        section = name.removeprefix("section-").upper()
        return f"SECTION {section}-{section}"
    return name.upper()


def _label_bounds(view: Bounds) -> Bounds:
    return Bounds(view.min_x, view.max_y + 2.0, view.max_x, view.max_y + 7.0)


def _title_block_entries(spec: DrawingSpec, title: Bounds) -> tuple[tuple[str, str, float], ...]:
    if spec.sheet.title_block_language == "zh":
        proj_zh = "第一角投影" if spec.standard.projection == "first-angle" else spec.standard.projection
        return (
            ("title-label", "图名", title.max_y - 8),
            ("title", spec.project.name, title.max_y - 18),
            ("drawing-number", f"图号: {spec.project.drawing_number or '-'}", title.max_y - 28),
            ("scale", f"比例: {spec.sheet.scale}", title.max_y - 38),
            ("projection", f"投影: {proj_zh}", title.max_y - 48),
        )
    return (
        ("title-label", "TITLE", title.max_y - 8),
        ("title", spec.project.name, title.max_y - 18),
        ("drawing-number", f"DRAWING: {spec.project.drawing_number or '-'}", title.max_y - 28),
        ("scale", f"SCALE: {spec.sheet.scale}", title.max_y - 38),
        ("projection", f"PROJECTION: {spec.standard.projection.upper()}", title.max_y - 48),
    )


def _annotation_layouts(spec: DrawingSpec, views: tuple[ViewLayout, ...], printable: Bounds, title: Bounds, notes: Bounds, parts: PartsListLayout, standard: DrawingStandard) -> tuple[AnnotationLayout, ...]:
    height = standard.text_styles["normal"].height_mm
    width_factor = standard.text_styles["normal"].width_factor
    is_zh = spec.sheet.title_block_language == "zh"
    result = [
        AnnotationLayout("frame", "", bounds, "FRAME", annotation_id=f"frame-{zone}", group_id=f"frame-{zone}", owner_zone=zone)
        for bounds, zone in ((printable, "printable"), (title, "title"), (notes, "technical"), (parts.bounds, "parts"))
    ]
    title_values = _title_block_entries(spec, title)
    result += [_text(kind, text, title.min_x + 5, y, height, width_factor=width_factor, annotation_id=kind, owner_zone="title") for kind, text, y in title_values]
    result += [
        _text("notes-header", "技术要求" if is_zh else "TECHNICAL NOTES", notes.min_x + 5, notes.max_y - 8, height, width_factor=width_factor, annotation_id="notes-header", owner_zone="technical"),
        _text("parts-header", "明细栏" if is_zh else "PARTS LIST", parts.bounds.min_x + 5, parts.bounds.max_y - 8, height, width_factor=width_factor, annotation_id="parts-header", owner_zone="parts"),
        _text("parts-column", "序号" if is_zh else "ITEM", parts.bounds.min_x + 5, parts.bounds.max_y - 16, height, width_factor=width_factor, annotation_id="parts-column-item", owner_zone="parts"),
        _text("parts-column", "名称" if is_zh else "DESCRIPTION", parts.bounds.min_x + 30, parts.bounds.max_y - 16, height, width_factor=width_factor, annotation_id="parts-column-description", owner_zone="parts"),
        _text("parts-column", "数量" if is_zh else "QTY", parts.bounds.max_x - 25, parts.bounds.max_y - 16, height, width_factor=width_factor, annotation_id="parts-column-qty", owner_zone="parts"),
    ]
    result += [_text("view-label", view.label, view.bounds.center[0], view.bounds.max_y + 2, height, width_factor=width_factor, annotation_id=f"view-label-{view.name}", owner_view=view.name) for view in views]
    result += [_text("technical-note", note.text, notes.min_x + 5, notes.max_y - 8 - index * _TEXT_LINE_HEIGHT_MM, height, width_factor=width_factor, annotation_id=f"technical-note-{index}", owner_zone="technical") for index, note in enumerate(spec.requirements.technical_notes, 1)]
    for row in parts.rows:
        y = parts.bounds.max_y - 16 - row.item_no * _TABLE_ROW_HEIGHT_MM
        result += [_text("parts-row", str(row.item_no), parts.bounds.min_x + 5, y, height, width_factor=width_factor, annotation_id=f"parts-{row.item_no}-item", owner_zone="parts"), _text("parts-row", row.item, parts.bounds.min_x + 30, y, height, width_factor=width_factor, annotation_id=f"parts-{row.item_no}-description", owner_zone="parts"), _text("parts-row", str(row.quantity), parts.bounds.max_x - 20, y, height, width_factor=width_factor, annotation_id=f"parts-{row.item_no}-qty", owner_zone="parts")]
    if views:
        front = views[0]
        views_by_name = {v.name: v for v in views}
        lane_counts: dict[tuple[str, str], int] = {}
        for index, dimension in enumerate(spec.dimensions, 1):
            result.append(_dimension_layout(index, dimension, spec, views_by_name, front, standard, lane_counts))
        for index, annotation in enumerate(spec.annotations):
            group = f"free-{index + 1}"
            y = notes.min_y + 2 + index * _TEXT_LINE_HEIGHT_MM
            result += [_text("annotation", annotation.text, notes.min_x + 5, y, height, width_factor=width_factor, annotation_id=f"{group}-text", group_id=group, owner_zone="technical"), AnnotationLayout("leader", "", Bounds(notes.min_x + 2, y, notes.min_x + 12, y + 4), "DIM", points=((notes.min_x + 2, y), (notes.min_x + 12, y + 4)), annotation_id=f"{group}-leader", group_id=group, owner_zone="technical")]
        for row in parts.rows:
            group = f"balloon-{row.item_no}"
            center = (front.bounds.min_x - 8 - (row.item_no - 1) * 8, front.bounds.max_y - 8)
            result += [AnnotationLayout("balloon", str(row.item_no), Bounds(center[0] - 3.5, center[1] - 3.5, center[0] + 3.5, center[1] + 3.5), "DIM", points=(center,), radius=3.5, annotation_id=f"{group}-circle", group_id=group, owner_view=front.name, allowed_contact=("own-balloon-text", "own-leader")), _text("balloon-text", str(row.item_no), center[0] - 1, center[1] - 1.5, height, width_factor=width_factor, annotation_id=f"{group}-text", group_id=group, owner_view=front.name, allowed_contact=("own-balloon",)), AnnotationLayout("leader", "", Bounds(min(center[0], front.bounds.min_x), center[1] - 0.001, max(center[0], front.bounds.min_x), center[1] + 0.001), "DIM", points=(center, (front.bounds.min_x, center[1])), annotation_id=f"{group}-leader", group_id=group, owner_view=front.name, allowed_contact=("own-balloon", "own-view-endpoint"))]
    return tuple(result)


def _dimension_layout(
    index: int,
    dimension: object,
    spec: DrawingSpec,
    views_by_name: dict[str, ViewLayout],
    default_view: ViewLayout,
    standard: DrawingStandard,
    lane_counts: dict[tuple[str, str], int],
) -> AnnotationLayout:
    band = standard.dimension.gap_mm + standard.dimension.text_height_mm + standard.dimension.arrow_size_mm
    label = dimension.format_label()
    is_legacy = (
        dimension.owner_view is None
        and dimension.target_entity_index is None
        and dimension.orientation is None
        and dimension.prefix is None
        and dimension.tolerance_upper is None
        and dimension.tolerance_lower is None
    )
    if is_legacy:
        lane_key = (default_view.name, "bottom")
        lane = lane_counts.get(lane_key, 0) + 1
        lane_counts[lane_key] = lane
        y = default_view.bounds.min_y - band * lane
        return AnnotationLayout(
            "dimension",
            label,
            Bounds(default_view.bounds.min_x, y, default_view.bounds.max_x, y + band),
            "DIM",
            points=((default_view.bounds.min_x, default_view.bounds.min_y), (default_view.bounds.max_x, y + standard.dimension.gap_mm)),
            annotation_id=f"dimension-{index}",
            group_id=f"dimension-{index}",
            owner_view=default_view.name,
            allowed_contact=("own-view-endpoint",),
            dim_kind="aligned",
        )

    view = views_by_name.get(dimension.owner_view or "", default_view)
    if dimension.owner_view is None and dimension.target_entity_index is not None:
        for cand_view in views_by_name.values():
            if dimension.target_entity_index in cand_view.source_indices:
                view = cand_view
                break

    target_entity = (
        spec.geometry.entities[dimension.target_entity_index]
        if dimension.target_entity_index is not None
        else None
    )
    orientation = dimension.orientation or "horizontal"

    if orientation in {"diameter", "radius"} and isinstance(target_entity, (CircleEntity, ArcEntity, CircularEntity)):
        cx = target_entity.center[0] * view.scale + view.translation[0]
        cy = target_entity.center[1] * view.scale + view.translation[1]
        r_sheet = target_entity.radius * view.scale
        lane_key = (view.name, "radial")
        radial_idx = lane_counts.get(lane_key, 0)
        lane_counts[lane_key] = radial_idx + 1
        angles = (35.0, 145.0, -35.0, -145.0, 65.0, 115.0)
        angle_deg = angles[radial_idx % len(angles)]
        pad = max(r_sheet, standard.dimension.text_height_mm * 2.0)
        return AnnotationLayout(
            "dimension",
            label,
            Bounds(cx - pad, cy - pad, cx + pad, cy + pad),
            "DIM",
            points=((cx, cy), (angle_deg, 0.0)),
            radius=r_sheet,
            annotation_id=f"dimension-{index}",
            group_id=f"dimension-{index}",
            owner_view=view.name,
            allowed_contact=("own-view-endpoint", "own-view-feature"),
            dim_kind=orientation,
        )

    if orientation == "aligned" and isinstance(target_entity, LineEntity):
        p1 = (
            target_entity.start[0] * view.scale + view.translation[0],
            target_entity.start[1] * view.scale + view.translation[1],
        )
        p2 = (
            target_entity.end[0] * view.scale + view.translation[0],
            target_entity.end[1] * view.scale + view.translation[1],
        )
        lane_key = (view.name, "aligned")
        lane = lane_counts.get(lane_key, 0) + 1
        lane_counts[lane_key] = lane
        dist = -(standard.dimension.gap_mm + (lane - 1) * band)
        min_x, max_x = min(p1[0], p2[0]) - abs(dist), max(p1[0], p2[0]) + abs(dist)
        min_y, max_y = min(p1[1], p2[1]) - abs(dist), max(p1[1], p2[1]) + abs(dist)
        return AnnotationLayout(
            "dimension",
            label,
            Bounds(min_x, min_y, max_x, max_y),
            "DIM",
            points=(p1, p2),
            radius=dist,
            annotation_id=f"dimension-{index}",
            group_id=f"dimension-{index}",
            owner_view=view.name,
            allowed_contact=("own-view-endpoint", "own-view-feature"),
            dim_kind="true_aligned",
        )

    if target_entity is not None:
        eb = _entity_exact_bounds(target_entity)
        x0 = eb.min_x * view.scale + view.translation[0]
        x1 = eb.max_x * view.scale + view.translation[0]
        y0 = eb.min_y * view.scale + view.translation[1]
        y1 = eb.max_y * view.scale + view.translation[1]
    else:
        span = dimension.value * view.scale
        cx, cy = view.bounds.center
        x0, x1 = cx - span / 2.0, cx + span / 2.0
        y0, y1 = cy - span / 2.0, cy + span / 2.0

    if orientation == "vertical":
        lane_key = (view.name, "right")
        lane = lane_counts.get(lane_key, 0) + 1
        lane_counts[lane_key] = lane
        x_start = view.bounds.max_x + band * (lane - 1)
        x_line = x_start + standard.dimension.gap_mm
        by0, by1 = (min(y0, y1), max(y0, y1)) if abs(y1 - y0) > 1e-6 else (y0 - 0.5, y0 + 0.5)
        return AnnotationLayout(
            "dimension",
            label,
            Bounds(x_start, by0, x_start + band, by1),
            "DIM",
            points=((view.bounds.max_x, y0), (x_line, y1)),
            annotation_id=f"dimension-{index}",
            group_id=f"dimension-{index}",
            owner_view=view.name,
            allowed_contact=("own-view-endpoint",),
            dim_kind="vertical",
        )

    lane_key = (view.name, "bottom")
    lane = lane_counts.get(lane_key, 0) + 1
    lane_counts[lane_key] = lane
    y = view.bounds.min_y - band * lane
    bx0, bx1 = (min(x0, x1), max(x0, x1)) if abs(x1 - x0) > 1e-6 else (x0 - 0.5, x0 + 0.5)
    return AnnotationLayout(
        "dimension",
        label,
        Bounds(bx0, y, bx1, y + band),
        "DIM",
        points=((x0, view.bounds.min_y), (x1, y + standard.dimension.gap_mm)),
        annotation_id=f"dimension-{index}",
        group_id=f"dimension-{index}",
        owner_view=view.name,
        allowed_contact=("own-view-endpoint",),
        dim_kind="aligned",
    )


def _text(kind: str, text: str, x: float, y: float, height: float, *, width_factor: float = 1.0, annotation_id: str = "", group_id: str = "", owner_view: str | None = None, owner_zone: str | None = None, allowed_contact: tuple[str, ...] = ()) -> AnnotationLayout:
    identifier = annotation_id or kind
    width_mm = max(height, measure_text_width_mm(text, height, width_factor=width_factor))
    return AnnotationLayout(kind, text, Bounds(x, y, x + width_mm, y + height), "TEXT", points=((x, y),), annotation_id=identifier, group_id=group_id or identifier, owner_view=owner_view, owner_zone=owner_zone, allowed_contact=allowed_contact)
def _parse_scale(value: str) -> Decimal:
    try:
        left, right = value.split(":", 1)
        numerator, denominator = Decimal(left), Decimal(right)
    except (AttributeError, InvalidOperation, ValueError) as error:
        raise _ScaleRepresentationError(f"invalid drawing scale: {value}") from error
    if not numerator.is_finite() or not denominator.is_finite() or numerator <= 0 or denominator <= 0:
        raise _ScaleRepresentationError(f"invalid drawing scale: {value}")
    return numerator / denominator


def _fitting_scale_candidates(requested: Decimal) -> Iterable[tuple[Decimal, str]]:
    for candidate, label in ((Decimal(10), "10:1"), (Decimal(5), "5:1"), (Decimal(2), "2:1"), (Decimal(1), "1:1")):
        if candidate < requested:
            yield candidate, label
    exponent = 0
    while True:
        for multiplier in (2, 5, 10):
            value = Decimal(multiplier).scaleb(exponent)
            candidate = Decimal(1) / value
            if candidate < requested:
                yield candidate, f"1:{_compact_decimal(value)}"
        exponent += 1


def _compact_decimal(value: Decimal) -> str:
    integral = value.to_integral_exact()
    if integral.adjusted() < 20:
        return format(integral, "f")
    coefficient = integral.scaleb(-integral.adjusted()).normalize()
    return f"{format(coefficient, 'f')}e{integral.adjusted()}"
