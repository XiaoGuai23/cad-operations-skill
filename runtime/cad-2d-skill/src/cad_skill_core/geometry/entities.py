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
    | CircleEntity
    | ArcEntity
    | SplineEntity
    | HatchEntity
    | TextEntity
    | MTextEntity
    | LeaderEntity
    | DimensionEntity,
    Field(discriminator="kind"),
]
