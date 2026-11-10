"""Declarative 2D entities accepted by the deterministic DXF writer."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


Point2D: TypeAlias = Annotated[
    tuple[float, float],
    BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
]


class EntityModel(BaseModel):
    """Strict shared fields for source-traceable drawing entities."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str
    layer: str
    source_id: str


class LineEntity(EntityModel):
    kind: Literal["line"]
    start: Point2D
    end: Point2D


class PolylineEntity(EntityModel):
    kind: Literal["polyline"]
    vertices: list[Point2D]
    closed: bool = False


class ContourLineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["line"]
    start: Point2D
    end: Point2D


class ContourArcSegment(BaseModel):
    """Exact circular arc; a negative sweep traverses clockwise."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["arc"]
    center: Point2D
    radius: float
    start_angle: float
    sweep: float


ContourSegment: TypeAlias = Annotated[
    ContourLineSegment | ContourArcSegment, Field(discriminator="kind")
]


class BulgeVertex(BaseModel):
    """Bulge belongs to the segment from this vertex to the next vertex."""

    model_config = ConfigDict(extra="forbid", strict=True)

    point: Point2D
    bulge: float = 0.0


class ContourEntity(EntityModel):
    """Opt-in simple closed boundary emitted as one true-arc LWPOLYLINE.

    Provide exactly one of ordered ``segments`` or ``vertices``. Validation
    is performed by ``contours.compile_contour`` and the DXF writer. Do not
    repeat the first vertex at the end; closure is implicit.
    """

    kind: Literal["contour"]
    segments: list[ContourSegment] | None = None
    vertices: list[BulgeVertex] | None = None
    require_tangent: bool = False
    tolerance: float = 1e-7


class CircleEntity(EntityModel):
    kind: Literal["circle"]
    center: Point2D
    radius: float


class ArcEntity(EntityModel):
    kind: Literal["arc"]
    center: Point2D
    radius: float
    start_angle: float
    end_angle: float


class CircularEntity(EntityModel):
    """One unified circular curve: an arc when ``sweep`` < 360, a full circle at 360.

    ``start_angle`` is the start direction in degrees and ``sweep`` is the
    counter-clockwise span in degrees, ``0 < sweep <= 360``.  Keeping circle
    and arc in one kind means revision, splitting and dimension driving never
    have to guess whether a 360-degree arc is a circle: the writer emits a
    native CIRCLE for sweep 360 and a native ARC otherwise, and
    :func:`to_circular` normalises a native ``circle``/``arc`` pair into the
    same representation.
    """

    kind: Literal["circular"]
    center: Point2D
    radius: float
    start_angle: float = 0.0
    sweep: float = 360.0

    @property
    def end_angle(self) -> float:
        return self.start_angle + self.sweep

    @property
    def is_full_circle(self) -> bool:
        # Full circles are explicit; never close a deliberate near-full arc.
        return self.sweep == 360.0


def to_circular(entity: CircleEntity | ArcEntity | CircularEntity) -> CircularEntity:
    """Normalise a circle/arc/circular entity into one ``circular`` entity."""

    if isinstance(entity, CircularEntity):
        return entity
    if isinstance(entity, CircleEntity):
        return CircularEntity(
            kind="circular",
            center=entity.center,
            radius=entity.radius,
            start_angle=0.0,
            sweep=360.0,
            layer=entity.layer,
            source_id=entity.source_id,
        )
    sweep = (entity.end_angle - entity.start_angle) % 360.0
    if sweep <= 1e-9:
        raise ValueError("arc sweep must be greater than zero and at most 360 degrees")
    return CircularEntity(
        kind="circular",
        center=entity.center,
        radius=entity.radius,
        start_angle=entity.start_angle % 360.0,
        sweep=sweep,
        layer=entity.layer,
        source_id=entity.source_id,
    )


class SplineEntity(EntityModel):
    kind: Literal["spline"]
    control_points: list[Point2D]


class HatchEntity(EntityModel):
    kind: Literal["hatch"]
    boundary: list[Point2D]
    pattern: str = "ANSI31"


class TextEntity(EntityModel):
    kind: Literal["text"]
    text: str
    insert: Point2D
    height: float
    rotation: float = 0.0
    style: str = "normal"


class MTextEntity(EntityModel):
    kind: Literal["mtext"]
    text: str
    insert: Point2D
    height: float
    width: float | None = None
    style: str = "normal"


class LeaderEntity(EntityModel):
    kind: Literal["leader"]
    vertices: list[Point2D]


class DimensionEntity(EntityModel):
    kind: Literal["dimension"]
    start: Point2D
    end: Point2D
    offset: float
    text: str | None = None


DrawingEntity: TypeAlias = Annotated[
    LineEntity
    | PolylineEntity
    | ContourEntity
    | CircleEntity
    | ArcEntity
    | CircularEntity
    | SplineEntity
    | HatchEntity
    | TextEntity
    | MTextEntity
    | LeaderEntity
    | DimensionEntity,
    Field(discriminator="kind"),
]
