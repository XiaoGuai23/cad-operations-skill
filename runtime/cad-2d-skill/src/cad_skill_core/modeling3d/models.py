"""Validated, provenance-traceable 3D mechanical modeling specification."""

from __future__ import annotations

from math import hypot, isfinite, pi, sin
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

Point2D: TypeAlias = Annotated[
    tuple[float, float],
    BeforeValidator(lambda v: tuple(v) if isinstance(v, list) else v),
]

HoleXY: TypeAlias = Annotated[
    tuple[float, float, float],
    BeforeValidator(lambda v: tuple(v) if isinstance(v, list) else v),
]

ShaftStep: TypeAlias = Annotated[
    tuple[float, float],
    BeforeValidator(lambda v: tuple(v) if isinstance(v, list) else v),
]

NonBlankStr = Annotated[str, StringConstraints(pattern=r"\S")]


class Strict3DModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Project3D(Strict3DModel):
    name: NonBlankStr
    drawing_number: str | None = None
    units: Literal["mm"] = "mm"


class SourcedMaterial3D(Strict3DModel):
    text: NonBlankStr
    source_id: NonBlankStr


class Provenance3D(Strict3DModel):
    id: NonBlankStr
    file: str
    locator: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Validation3D(Strict3DModel):
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Box3D(Strict3DModel):
    """Axis-aligned rectangular block centered on XY origin, Z in [0, height], with optional Z through-holes."""

    kind: Literal["box"]
    width: float
    depth: float
    height: float
    through_holes: list[HoleXY] = Field(default_factory=list)
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_box(self) -> Box3D:
        if not all(isfinite(v) for v in (self.width, self.depth, self.height)):
            raise ValueError("box dimensions must be finite")
        if self.width <= 0 or self.depth <= 0 or self.height <= 0:
            raise ValueError("box width, depth, and height must be positive")
        half_w, half_d = self.width / 2.0, self.depth / 2.0
        for idx, (hx, hy, hd) in enumerate(self.through_holes):
            if not all(isfinite(v) for v in (hx, hy, hd)) or hd <= 0:
                raise ValueError(f"through_hole {idx} must have finite coordinates and positive diameter")
            r = hd / 2.0
            if abs(hx) + r >= half_w - 0.5 or abs(hy) + r >= half_d - 0.5:
                raise ValueError(f"through_hole {idx} breaks through outer box wall")
            for prev_x, prev_y, prev_d in self.through_holes[:idx]:
                if hypot(hx - prev_x, hy - prev_y) <= (hd + prev_d) / 2.0 + 0.5:
                    raise ValueError("box through_holes overlap each other")
        return self


class Cylinder3D(Strict3DModel):
    """Coaxial solid or hollow cylinder along Z in [0, height]."""

    kind: Literal["cylinder"]
    outer_diameter: float
    bore_diameter: float = 0.0
    height: float
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_cylinder(self) -> Cylinder3D:
        if not all(isfinite(v) for v in (self.outer_diameter, self.bore_diameter, self.height)):
            raise ValueError("cylinder dimensions must be finite")
        if self.outer_diameter <= 0 or self.height <= 0:
            raise ValueError("cylinder outer_diameter and height must be positive")
        if self.bore_diameter < 0 or self.bore_diameter >= self.outer_diameter:
            raise ValueError("cylinder bore_diameter must satisfy 0 <= bore_diameter < outer_diameter")
        return self


class ExtrudePolygon3D(Strict3DModel):
    """2.5D linear extrusion along Z in [0, height] from a simple outer polygon and disjoint inner loops."""

    kind: Literal["extrude_polygon"]
    outer_loop: list[Point2D] = Field(min_length=3)
    inner_loops: list[list[Point2D]] = Field(default_factory=list)
    height: float
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_extrude(self) -> ExtrudePolygon3D:
        if not isfinite(self.height) or self.height <= 0:
            raise ValueError("extrude height must be positive and finite")
        for pt in self.outer_loop:
            if not (isfinite(pt[0]) and isfinite(pt[1])):
                raise ValueError("outer_loop coordinates must be finite")
        for loop in self.inner_loops:
            if len(loop) < 3:
                raise ValueError("each inner_loop requires at least 3 vertices")
            for pt in loop:
                if not (isfinite(pt[0]) and isfinite(pt[1])):
                    raise ValueError("inner_loop coordinates must be finite")
        return self


class Flange3D(Strict3DModel):
    """3D circular flange with optional raised neck/hub, central bore, and bolt-circle through-holes."""

    kind: Literal["flange_3d"]
    outer_diameter: float
    bore_diameter: float = 0.0
    thickness: float = 20.0
    hub_diameter: float | None = None
    hub_height: float = 0.0
    bolt_circle_diameter: float | None = None
    hole_diameter: float | None = None
    hole_count: int = 0
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_flange(self) -> Flange3D:
        nums = [self.outer_diameter, self.bore_diameter, self.thickness, self.hub_height]
        for opt in (self.hub_diameter, self.bolt_circle_diameter, self.hole_diameter):
            if opt is not None:
                nums.append(opt)
        if not all(isfinite(v) for v in nums):
            raise ValueError("flange_3d parameters must be finite")
        if self.outer_diameter <= 0 or self.thickness <= 0:
            raise ValueError("flange_3d outer_diameter and thickness must be positive")
        if self.bore_diameter < 0 or self.bore_diameter >= self.outer_diameter:
            raise ValueError("flange_3d bore_diameter must satisfy 0 <= bore_diameter < outer_diameter")
        if self.hub_height < 0:
            raise ValueError("flange_3d hub_height cannot be negative")
        if self.hub_height > 0:
            if self.hub_diameter is None:
                raise ValueError("hub_diameter is required when hub_height > 0")
            if not (self.bore_diameter + 2.0 <= self.hub_diameter <= self.outer_diameter - 4.0):
                raise ValueError("hub_diameter must lie strictly between bore_diameter and outer_diameter")
        if self.hole_count < 0:
            raise ValueError("hole_count cannot be negative")
        if self.hole_count > 0:
            if self.bolt_circle_diameter is None or self.hole_diameter is None or self.hole_diameter <= 0:
                raise ValueError("bolt_circle_diameter and positive hole_diameter are required when hole_count > 0")
            inner_limit = max(self.bore_diameter, self.hub_diameter or 0.0)
            if not (inner_limit + self.hole_diameter + 2.0 <= self.bolt_circle_diameter <= self.outer_diameter - self.hole_diameter - 2.0):
                raise ValueError("bolt holes interfere with bore/hub or outer flange rim")
            if self.hole_count >= 2 and self.bolt_circle_diameter * sin(pi / self.hole_count) <= self.hole_diameter * 1.1:
                raise ValueError("adjacent bolt holes overlap on bolt circle")
        return self


class SteppedShaft3D(Strict3DModel):
    """Coaxial multi-stepped cylindrical shaft along Z in [0, sum(lengths)], with optional axial bore."""

    kind: Literal["stepped_shaft_3d"]
    steps: list[ShaftStep] = Field(min_length=1)
    bore_diameter: float = 0.0
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_shaft(self) -> SteppedShaft3D:
        if not isfinite(self.bore_diameter) or self.bore_diameter < 0:
            raise ValueError("bore_diameter must be non-negative and finite")
        for idx, (length, dia) in enumerate(self.steps, start=1):
            if not (isfinite(length) and isfinite(dia)) or length <= 0 or dia <= 0:
                raise ValueError(f"step {idx} length and diameter must be positive finite numbers")
            if self.bore_diameter > 0 and self.bore_diameter >= dia - 1.0:
                raise ValueError(f"bore_diameter exceeds or leaves insufficient wall on step {idx}")
        return self


class SlottedBracket3D(Strict3DModel):
    """3D rounded rectangular plate with central obround through-slot and optional 4 mounting holes."""

    kind: Literal["slotted_bracket_3d"]
    width: float
    height: float
    thickness: float = 12.0
    corner_radius: float = 8.0
    slot_length: float = 40.0
    slot_width: float = 14.0
    hole_diameter: float | None = None
    hole_inset: float = 16.0
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_bracket(self) -> SlottedBracket3D:
        vals = [
            self.width,
            self.height,
            self.thickness,
            self.corner_radius,
            self.slot_length,
            self.slot_width,
            self.hole_inset,
        ]
        if self.hole_diameter is not None:
            vals.append(self.hole_diameter)
        if not all(isfinite(v) for v in vals):
            raise ValueError("slotted_bracket_3d dimensions must be finite")
        if self.width <= 0 or self.height <= 0 or self.thickness <= 0:
            raise ValueError("width, height, and thickness must be positive")
        if not (0 < self.corner_radius < min(self.width, self.height) / 2.0):
            raise ValueError("corner_radius must satisfy 0 < corner_radius < min(width, height)/2")
        if self.slot_width <= 0 or self.slot_length <= self.slot_width:
            raise ValueError("slot requires slot_length > slot_width > 0")
        if self.slot_length >= self.width - 8.0 or self.slot_width >= self.height - 8.0:
            raise ValueError("slot leaves insufficient wall thickness inside plate")
        if self.hole_diameter is not None:
            if self.hole_diameter <= 0:
                raise ValueError("hole_diameter must be positive")
            r = self.hole_diameter / 2.0
            hx = self.width / 2.0 - self.hole_inset
            hy = self.height / 2.0 - self.hole_inset
            if hx - r <= 2.0 or hy - r <= 2.0 or self.hole_inset <= r + 2.0:
                raise ValueError("hole_inset places mounting holes outside valid plate margin")
            # Ensure mounting holes do not intersect central slot
            if hx - r <= self.slot_length / 2.0 + 1.0 and hy - r <= self.slot_width / 2.0 + 1.0:
                raise ValueError("mounting holes interfere with central slot")
        return self


class CylinderBoss3D(Strict3DModel):
    """Additive cylindrical boss rising from the top face of a CompoundSolid3D base."""

    kind: Literal["cylinder_boss"] = "cylinder_boss"
    x: float = 0.0
    y: float = 0.0
    diameter: float
    height: float
    bore_diameter: float = 0.0


class BoxBoss3D(Strict3DModel):
    """Additive rectangular boss rising from the top face of a CompoundSolid3D base."""

    kind: Literal["box_boss"] = "box_boss"
    x: float = 0.0
    y: float = 0.0
    width: float
    depth: float
    height: float


class CounterboreHole3D(Strict3DModel):
    """Subtractive counterbored hole from top face (cbore_diameter x cbore_depth) + through shaft."""

    x: float
    y: float
    through_diameter: float
    cbore_diameter: float
    cbore_depth: float


BossFeature3D: TypeAlias = Annotated[
    CylinderBoss3D | BoxBoss3D,
    Field(discriminator="kind"),
]


class CompoundSolid3D(Strict3DModel):
    """Multi-feature CSG-like solid: box/cylinder base + multiple bosses + through-holes + counterbores."""

    kind: Literal["compound_3d"]
    base_shape: Literal["box", "cylinder"] = "box"
    width: float = 120.0
    depth: float = 80.0
    outer_diameter: float = 120.0
    base_height: float = 16.0
    corner_radius: float = 0.0
    bosses: list[BossFeature3D] = Field(default_factory=list)
    through_holes: list[HoleXY] = Field(default_factory=list)
    counterbores: list[CounterboreHole3D] = Field(default_factory=list)
    source_id: NonBlankStr

    @model_validator(mode="after")
    def validate_compound(self) -> CompoundSolid3D:
        if not isfinite(self.base_height) or self.base_height <= 0:
            raise ValueError("compound_3d base_height must be positive and finite")
        if self.base_shape == "box":
            if not all(isfinite(v) for v in (self.width, self.depth, self.corner_radius)):
                raise ValueError("compound_3d box dimensions must be finite")
            if self.width <= 0 or self.depth <= 0:
                raise ValueError("compound_3d box width and depth must be positive")
            if self.corner_radius < 0 or self.corner_radius >= min(self.width, self.depth) / 2.0:
                raise ValueError("compound_3d corner_radius must satisfy 0 <= r < min(width, depth)/2")
        else:
            if not isfinite(self.outer_diameter) or self.outer_diameter <= 0:
                raise ValueError("compound_3d cylinder outer_diameter must be positive")

        def _within_base(cx: float, cy: float, rx: float, ry: float, label: str) -> None:
            margin = 1.0
            if self.base_shape == "box":
                hw, hd = self.width / 2.0, self.depth / 2.0
                if abs(cx) + rx >= hw - margin or abs(cy) + ry >= hd - margin:
                    raise ValueError(f"{label} exceeds base plate boundary")
            else:
                r_base = self.outer_diameter / 2.0
                if hypot(abs(cx) + rx, abs(cy) + ry) >= r_base - margin:
                    raise ValueError(f"{label} exceeds cylindrical base boundary")

        # Collect top-face footprints (cx, cy, bounding_radius) to guarantee disjoint manifold stitching
        top_footprints: list[tuple[float, float, float, str]] = []
        bot_footprints: list[tuple[float, float, float, str]] = []

        for idx, boss in enumerate(self.bosses):
            if isinstance(boss, CylinderBoss3D):
                if not all(isfinite(v) for v in (boss.x, boss.y, boss.diameter, boss.height, boss.bore_diameter)):
                    raise ValueError(f"boss {idx} parameters must be finite")
                if boss.diameter <= 0 or boss.height <= 0:
                    raise ValueError(f"boss {idx} diameter and height must be positive")
                if boss.bore_diameter < 0 or (boss.bore_diameter > 0 and boss.bore_diameter >= boss.diameter - 1.5):
                    raise ValueError(f"boss {idx} bore_diameter invalid")
                rb = boss.diameter / 2.0
                _within_base(boss.x, boss.y, rb, rb, f"cylinder_boss {idx}")
                top_footprints.append((boss.x, boss.y, rb, f"boss[{idx}]"))
                if boss.bore_diameter > 0:
                    bot_footprints.append((boss.x, boss.y, boss.bore_diameter / 2.0, f"boss_bore[{idx}]"))
            else:
                if not all(isfinite(v) for v in (boss.x, boss.y, boss.width, boss.depth, boss.height)):
                    raise ValueError(f"box_boss {idx} parameters must be finite")
                if boss.width <= 0 or boss.depth <= 0 or boss.height <= 0:
                    raise ValueError(f"box_boss {idx} dimensions must be positive")
                hw_b, hd_b = boss.width / 2.0, boss.depth / 2.0
                _within_base(boss.x, boss.y, hw_b, hd_b, f"box_boss {idx}")
                top_footprints.append((boss.x, boss.y, hypot(hw_b, hd_b), f"box_boss[{idx}]"))

        for idx, (hx, hy, hd) in enumerate(self.through_holes):
            if not all(isfinite(v) for v in (hx, hy, hd)) or hd <= 0:
                raise ValueError(f"through_hole {idx} must be positive and finite")
            rh = hd / 2.0
            _within_base(hx, hy, rh, rh, f"through_hole {idx}")
            top_footprints.append((hx, hy, rh, f"through_hole[{idx}]"))
            bot_footprints.append((hx, hy, rh, f"through_hole[{idx}]"))

        for idx, cb in enumerate(self.counterbores):
            if not all(isfinite(v) for v in (cb.x, cb.y, cb.through_diameter, cb.cbore_diameter, cb.cbore_depth)):
                raise ValueError(f"counterbore {idx} parameters must be finite")
            if cb.through_diameter <= 0 or cb.cbore_diameter <= cb.through_diameter + 1.0:
                raise ValueError(f"counterbore {idx} requires cbore_diameter > through_diameter + 1.0")
            if not (0 < cb.cbore_depth < self.base_height - 1.0):
                raise ValueError(f"counterbore {idx} cbore_depth must lie strictly inside base_height")
            rcb = cb.cbore_diameter / 2.0
            rth = cb.through_diameter / 2.0
            _within_base(cb.x, cb.y, rcb, rcb, f"counterbore {idx}")
            top_footprints.append((cb.x, cb.y, rcb, f"counterbore[{idx}]"))
            bot_footprints.append((cb.x, cb.y, rth, f"counterbore[{idx}]"))

        for group in (top_footprints, bot_footprints):
            for i in range(len(group)):
                x1, y1, r1, n1 = group[i]
                for j in range(i + 1, len(group)):
                    x2, y2, r2, n2 = group[j]
                    if hypot(x1 - x2, y1 - y2) <= r1 + r2 + 0.8:
                        raise ValueError(f"compound_3d features {n1} and {n2} overlap on base face")
        return self


Body3D: TypeAlias = Annotated[
    Box3D
    | Cylinder3D
    | ExtrudePolygon3D
    | Flange3D
    | SteppedShaft3D
    | SlottedBracket3D
    | CompoundSolid3D,
    Field(discriminator="kind"),
]


class Model3DSpec(Strict3DModel):
    """Complete, traceable 3D mechanical model specification."""

    schema_version: Literal["1.0"] = "1.0"
    project: Project3D
    material: SourcedMaterial3D | None = None
    technical_notes: list[SourcedMaterial3D] = Field(default_factory=list)
    body: Body3D
    provenance: list[Provenance3D] = Field(min_length=1)
    validation: Validation3D = Field(default_factory=Validation3D)

    @model_validator(mode="after")
    def verify_provenance_links(self) -> Model3DSpec:
        ids = {p.id for p in self.provenance}
        if len(ids) != len(self.provenance):
            raise ValueError("provenance id values must be unique")
        if self.body.source_id not in ids:
            raise ValueError(f"body source_id is unknown: {self.body.source_id}")
        if self.material is not None and self.material.source_id not in ids:
            raise ValueError(f"material source_id is unknown: {self.material.source_id}")
        for note in self.technical_notes:
            if note.source_id not in ids:
                raise ValueError(f"technical_note source_id is unknown: {note.source_id}")
        return self
