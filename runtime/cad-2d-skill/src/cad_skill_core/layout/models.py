"""Immutable layout records for deterministic mechanical drawing sheets."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot


@dataclass(frozen=True, slots=True)
class Bounds:
    """Axis-aligned sheet-space bounds expressed in millimetres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("bounds must have positive width and height")

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    def intersects(self, other: Bounds) -> bool:
        """Return whether two areas overlap; a shared edge is permitted."""

        return not (
            self.max_x <= other.min_x
            or self.min_x >= other.max_x
            or self.max_y <= other.min_y
            or self.min_y >= other.max_y
        )

    def clearance_to(self, other: Bounds) -> float:
        """Return the shortest edge-to-edge clearance between two bounds."""

        if self.intersects(other):
            return 0.0
        horizontal = max(other.min_x - self.max_x, self.min_x - other.max_x, 0.0)
        vertical = max(other.min_y - self.max_y, self.min_y - other.max_y, 0.0)
        return hypot(horizontal, vertical)

    def contains(self, other: Bounds) -> bool:
        return (
            self.min_x <= other.min_x
            and self.min_y <= other.min_y
            and other.max_x <= self.max_x
            and other.max_y <= self.max_y
        )


@dataclass(frozen=True, slots=True)
class GeometryBounds:
    """Transformed geometry envelope; analytic primitives are exact, while
    rendered text, dimensions, leaders, and splines are conservative.

    Consumers must assert containment of reopened DXF bounds, not equality,
    except for analytic primitives. Unlike :class:`Bounds`, axes may be zero.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.min_x, self.min_y, self.max_x, self.max_y


@dataclass(frozen=True, slots=True)
class ViewLayout:
    """A requested orthographic or sectional view and its reserved bounds."""

    name: str
    bounds: Bounds
    label: str
    source_bounds: tuple[float, float, float, float] | None = None
    geometry_bounds: GeometryBounds | None = None
    source_indices: tuple[int, ...] = ()
    scale: float = 1.0
    translation: tuple[float, float] = (0.0, 0.0)
    annotation_bounds: tuple[Bounds, ...] = ()
    source_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PartListRow:
    """One ordered assembly parts-list row."""

    item_no: int
    item: str
    quantity: int


@dataclass(frozen=True, slots=True)
class PartsListLayout:
    """Bounds and serialised rows of the assembly parts list."""

    bounds: Bounds
    rows: tuple[PartListRow, ...]


@dataclass(frozen=True, slots=True)
class AnnotationLayout:
    """A text or drawing annotation reserved by the sheet planner."""

    kind: str
    text: str
    bounds: Bounds
    layer: str
    style: str = "normal"
    points: tuple[tuple[float, float], ...] = ()
    radius: float | None = None
    annotation_id: str = ""
    group_id: str = ""
    owner_view: str | None = None
    owner_zone: str | None = None
    allowed_contact: tuple[str, ...] = ()
    entity_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LayoutPlan:
    """Deterministic placement plan, finalized from rendered entity bounds."""

    sheet_bounds: Bounds
    printable_bounds: Bounds
    title_block_bounds: Bounds
    technical_note_bounds: Bounds
    parts_list: PartsListLayout
    views: tuple[ViewLayout, ...]
    annotations: tuple[AnnotationLayout, ...]
    text_height_mm: float
    dimension_gap_mm: float
    dimension_text_height_mm: float
    dimension_arrow_size_mm: float
    layer_styles: tuple[tuple[str, str, float, int], ...]
    text_styles: tuple[tuple[str, str, float, float], ...]


class LayoutOverflowError(ValueError):
    """Raised when requested views cannot fit at the requested drawing scale."""

    def __init__(
        self,
        requested_scale: str,
        suggested_scale: str,
        *,
        reason: str | None = None,
    ) -> None:
        self.requested_scale = requested_scale
        self.suggested_scale = suggested_scale
        self.reason = reason
        if reason == "scale-not-representable" and suggested_scale == "representable-scale-required":
            message = (
                f"scale-not-representable: drawing scale {requested_scale} is not usable; "
                "choose a positive finite representable scale"
            )
        elif reason == "fixed-content-overflow":
            message = (
                f"fixed-content-overflow: fixed-size content does not fit at "
                f"{requested_scale}; wrap fixed text or use a larger sheet"
            )
        else:
            detail = f"{reason}: " if reason else ""
            message = f"{detail}views do not fit at {requested_scale}; use {suggested_scale} or a larger sheet"
        super().__init__(message)
