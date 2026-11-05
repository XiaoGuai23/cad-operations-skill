"""Deterministic R2018 millimetre DXF generation for declarative 2D geometry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Iterable, Mapping

import ezdxf
from ezdxf import bbox, units
from ezdxf.sections.classes import ClassesSection
from ezdxf.tools.juliandate import juliandate

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard

from .entities import (
    ArcEntity,
    CircleEntity,
    DimensionEntity,
    DrawingEntity,
    HatchEntity,
    LeaderEntity,
    LineEntity,
    MTextEntity,
    Point2D,
    PolylineEntity,
    SplineEntity,
    TextEntity,
)


_FIXED_METADATA_DATE = juliandate(datetime(2000, 1, 1, 0, 0))
_FIXED_METADATA_GUID = "{00000000-0000-0000-0000-000000000000}"
_FIXED_EZDXF_MARKER = "0.0 @ 2000-01-01T00:00:00.000000+00:00"


@dataclass(frozen=True, slots=True)
class Bounds:
    """Axis-aligned bounds of the requested entities in millimetres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The persisted DXF and deterministic summary of its requested geometry."""

    path: Path
    entity_counts: dict[str, int]
    extents: Bounds
    roles_requiring_render_verification: tuple[str, ...]


def write_dxf(
    spec: DrawingSpec,
    standard: DrawingStandard,
    fonts: Mapping[str, ResolvedFont],
    output: Path,
) -> GenerationResult:
    """Write ``spec`` as a validated, re-openable R2018 millimetre DXF."""

    entities = tuple(spec.geometry.entities)
    if not entities:
        raise ValueError("geometry requires at least one entity")
    used_font_roles = _validate_entities(entities, standard, fonts)

    document = ezdxf.new("R2018")
    document.units = units.MM
    document.header["$INSUNITS"] = units.MM
    _add_linetypes(document, standard)
    _add_layers(document, standard)
    _add_styles(document, standard, fonts)
    modelspace = document.modelspace()

    counts: Counter[str] = Counter()
    for entity in entities:
        _emit_entity(modelspace, entity)
        counts[_dxf_type(entity)] += 1

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_dxf_deterministic(document, destination)
    reopened = ezdxf.readfile(destination)
    auditor = reopened.audit()
    if auditor.has_errors:
        raise ValueError("generated DXF failed ezdxf audit")
    extents = _modelspace_bounds(reopened)
    roles_requiring_render_verification = tuple(
        role
        for role in used_font_roles
        if fonts[role].requires_render_verification
    )

    return GenerationResult(
        destination,
        dict(sorted(counts.items())),
        extents,
        roles_requiring_render_verification,
    )


def save_dxf_deterministic(
    document: ezdxf.document.Drawing, destination: Path
) -> None:
    """Persist a drawing with all known ezdxf volatile metadata normalized."""

    _configure_deterministic_metadata(document)
    _canonicalize_dxf_class_order(document)
    document.saveas(Path(destination))


def _canonicalize_dxf_class_order(document: ezdxf.document.Drawing) -> None:
    """Order the document-local CLASS table independently of hash randomization."""

    document.classes.add_required_classes(document.dxfversion)
    classes = document.classes.classes
    required = ClassesSection()
    required.add_required_classes(document.dxfversion)
    required_order = {key: index for index, key in enumerate(required.classes)}
    ordered = sorted(
        classes.items(),
        key=lambda item: (required_order.get(item[0], len(required_order)), item[0]),
    )
    classes.clear()
    classes.update(ordered)


def _configure_deterministic_metadata(document: ezdxf.document.Drawing) -> None:
    """Normalize volatile metadata on this document without global ezdxf state."""

    if getattr(document, "_cad_skill_deterministic_metadata", False):
        return

    update_metadata = document._update_metadata

    def fixed_update_metadata() -> None:
        update_metadata()
        document.header["$TDCREATE"] = _FIXED_METADATA_DATE
        document.header["$TDUCREATE"] = _FIXED_METADATA_DATE
        document.header["$TDUPDATE"] = _FIXED_METADATA_DATE
        document.header["$TDUUPDATE"] = _FIXED_METADATA_DATE
        document.header["$VERSIONGUID"] = _FIXED_METADATA_GUID
        document.header["$FINGERPRINTGUID"] = _FIXED_METADATA_GUID
        metadata = document.ezdxf_metadata()
        metadata["CREATED_BY_EZDXF"] = _FIXED_EZDXF_MARKER
        metadata["WRITTEN_BY_EZDXF"] = _FIXED_EZDXF_MARKER

    document._update_metadata = fixed_update_metadata
    document._cad_skill_deterministic_metadata = True


def _add_linetypes(document: ezdxf.document.Drawing, standard: DrawingStandard) -> None:
    patterns = {
        "CENTER": [2.0, 1.25, -0.25, 0.25, -0.25],
        "HIDDEN": [1.0, 0.5, -0.5],
    }
    for name in sorted({style.linetype for style in standard.layers.values()}):
        if name.casefold() == "continuous" or name in document.linetypes:
            continue
        pattern = patterns.get(name, [0.5, -0.25])
        document.linetypes.add(name, pattern=pattern, description=f"{name} generated")


def _add_layers(document: ezdxf.document.Drawing, standard: DrawingStandard) -> None:
    for name, style in sorted(standard.layers.items()):
        document.layers.add(
            name,
            color=style.color,
            linetype=style.linetype,
            lineweight=round(style.lineweight_mm * 100),
        )


def _add_styles(
    document: ezdxf.document.Drawing,
    standard: DrawingStandard,
    fonts: Mapping[str, ResolvedFont],
) -> None:
    text_font = _selected_font(fonts, "TEXT")
    dimension_font = _selected_font(fonts, "DIM")
    style_definitions = {
        name: (text_font or style.font, style.height_mm, style.width_factor)
        for name, style in standard.text_styles.items()
    }
    style_definitions["DIM"] = (dimension_font or text_font or "project-default", 0.0, 1.0)
    for name, (font, height, width) in sorted(style_definitions.items()):
        document.styles.add(
            name,
            font=font,
            dxfattribs={"height": height, "width": width},
        )
    document.dimstyles.add(
        "DIM",
        dxfattribs={
            "dimtxsty": "DIM",
            "dimtxt": standard.dimension.text_height_mm,
            "dimgap": standard.dimension.gap_mm,
            "dimasz": standard.dimension.arrow_size_mm,
            "dimtad": 0,
            "dimclrd": 256,
            "dimclre": 256,
            "dimclrt": 256,
        },
    )


def _selected_font(fonts: Mapping[str, ResolvedFont], role: str) -> str | None:
    resolved = fonts.get(role)
    if resolved is None:
        return None
    if resolved.missing_characters or resolved.primary is None:
        raise ValueError(f"{role} font selection is not verified")
    return resolved.primary_path.name if resolved.primary_path is not None else resolved.primary


def _validate_entities(
    entities: Iterable[DrawingEntity],
    standard: DrawingStandard,
    fonts: Mapping[str, ResolvedFont],
) -> tuple[str, ...]:
    used_font_roles: set[str] = set()
    for entity in entities:
        if entity.layer not in standard.layers:
            raise ValueError(f"unknown layer: {entity.layer}")
        _validate_entity_numbers(entity)
        _validate_entity_length(entity)
        if isinstance(entity, (TextEntity, MTextEntity)) and entity.style not in standard.text_styles:
            raise ValueError(f"unknown text style: {entity.style}")
        if isinstance(entity, (TextEntity, MTextEntity)):
            used_font_roles.add("TEXT")
        if isinstance(entity, DimensionEntity):
            used_font_roles.add("DIM")
    roles = tuple(sorted(used_font_roles))
    for role in roles:
        _required_font(fonts, role)
    return roles


def _required_font(fonts: Mapping[str, ResolvedFont], role: str) -> ResolvedFont:
    resolved = fonts.get(role)
    if resolved is None:
        raise ValueError(f"missing {role} font selection")
    if resolved.missing_characters or resolved.primary is None:
        raise ValueError(f"{role} font selection is not verified")
    return resolved


def _validate_entity_numbers(entity: DrawingEntity) -> None:
    for value in _numbers(entity):
        if not isfinite(value):
            raise ValueError("coordinates and geometry values must be finite")
    if isinstance(entity, (CircleEntity, ArcEntity)) and entity.radius < 0:
        raise ValueError("negative radius is not allowed")
    if isinstance(entity, (TextEntity, MTextEntity)) and entity.height <= 0:
        raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, MTextEntity) and entity.width is not None and entity.width <= 0:
        raise ValueError("zero-length geometry is not allowed")


def _numbers(entity: DrawingEntity) -> Iterable[float]:
    values = entity.model_dump()

    def visit(value: object) -> Iterable[float]:
        if isinstance(value, float):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from visit(item)

    return visit(values)


def _validate_entity_length(entity: DrawingEntity) -> None:
    if isinstance(entity, (CircleEntity, ArcEntity)) and entity.radius == 0:
        raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, LineEntity) and entity.start == entity.end:
        raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, (PolylineEntity, LeaderEntity, SplineEntity)):
        points = _entity_points(entity)
        closed = isinstance(entity, PolylineEntity) and entity.closed
        if len(points) < 2 or _has_zero_length_segment(points, closed=closed):
            raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, HatchEntity):
        if (
            len(entity.boundary) < 3
            or _has_zero_length_segment(entity.boundary, closed=True)
            or _polygon_area(entity.boundary) == 0
        ):
            raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, ArcEntity) and (entity.end_angle - entity.start_angle) % 360 == 0:
        raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, DimensionEntity) and entity.start == entity.end:
        raise ValueError("zero-length geometry is not allowed")
    if isinstance(entity, DimensionEntity) and entity.offset == 0:
        raise ValueError("zero-length geometry is not allowed")


def _entity_points(entity: PolylineEntity | LeaderEntity | SplineEntity) -> list[Point2D]:
    if isinstance(entity, PolylineEntity):
        return entity.vertices
    if isinstance(entity, LeaderEntity):
        return entity.vertices
    return entity.control_points


def _polygon_area(points: list[Point2D]) -> float:
    return sum(
        point[0] * following[1] - following[0] * point[1]
        for point, following in zip(points, [*points[1:], points[0]], strict=True)
    ) / 2


def _has_zero_length_segment(points: list[Point2D], *, closed: bool) -> bool:
    pairs = zip(points, points[1:])
    if any(start == end for start, end in pairs):
        return True
    return closed and points[-1] == points[0]


def _emit_entity(modelspace: ezdxf.layouts.Modelspace, entity: DrawingEntity) -> None:
    attributes = {"layer": entity.layer}
    if isinstance(entity, LineEntity):
        modelspace.add_line(entity.start, entity.end, dxfattribs=attributes)
    elif isinstance(entity, PolylineEntity):
        modelspace.add_lwpolyline(entity.vertices, close=entity.closed, dxfattribs=attributes)
    elif isinstance(entity, CircleEntity):
        modelspace.add_circle(entity.center, entity.radius, dxfattribs=attributes)
    elif isinstance(entity, ArcEntity):
        modelspace.add_arc(entity.center, entity.radius, entity.start_angle, entity.end_angle, dxfattribs=attributes)
    elif isinstance(entity, SplineEntity):
        modelspace.add_open_spline(
            entity.control_points,
            degree=min(3, len(entity.control_points) - 1),
            dxfattribs=attributes,
        )
    elif isinstance(entity, HatchEntity):
        hatch = modelspace.add_hatch(dxfattribs=attributes)
        hatch.set_pattern_fill(entity.pattern)
        hatch.paths.add_polyline_path(entity.boundary, is_closed=True)
    elif isinstance(entity, TextEntity):
        modelspace.add_text(
            entity.text,
            dxfattribs={**attributes, "height": entity.height, "rotation": entity.rotation, "style": entity.style},
        ).set_placement(entity.insert)
    elif isinstance(entity, MTextEntity):
        mtext = modelspace.add_mtext(entity.text, dxfattribs={**attributes, "char_height": entity.height, "style": entity.style})
        if entity.width is not None:
            mtext.dxf.width = entity.width
        mtext.set_location(entity.insert)
    elif isinstance(entity, LeaderEntity):
        modelspace.add_leader(entity.vertices, dxfattribs=attributes)
    elif isinstance(entity, DimensionEntity):
        dimension = modelspace.add_aligned_dim(
            entity.start,
            entity.end,
            entity.offset,
            text=entity.text or "<>",
            dimstyle="DIM",
            override={"dimclrd": 256, "dimclre": 256, "dimclrt": 256},
            dxfattribs=attributes,
        )
        dimension.render()
    else:  # pragma: no cover - exhaustive union guard
        raise TypeError(f"unsupported entity: {entity!r}")


def _dxf_type(entity: DrawingEntity) -> str:
    return {
        "line": "LINE",
        "polyline": "LWPOLYLINE",
        "circle": "CIRCLE",
        "arc": "ARC",
        "spline": "SPLINE",
        "hatch": "HATCH",
        "text": "TEXT",
        "mtext": "MTEXT",
        "leader": "LEADER",
        "dimension": "DIMENSION",
    }[entity.kind]


def _modelspace_bounds(document: ezdxf.document.Drawing) -> Bounds:
    bounds = bbox.extents(document.modelspace())
    if not bounds.has_data:
        raise ValueError("generated DXF has no modelspace bounds")
    return Bounds(
        min_x=float(bounds.extmin.x),
        min_y=float(bounds.extmin.y),
        max_x=float(bounds.extmax.x),
        max_y=float(bounds.extmax.y),
    )
