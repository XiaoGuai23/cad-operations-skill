"""Validated, traceable source data for a 2D mechanical drawing."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from cad_skill_core.geometry.entities import DrawingEntity


class SchemaModel(BaseModel):
    """Base model that rejects undeclared drawing-spec fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Project(SchemaModel):
    name: str
    drawing_number: str | None = None
    drawing_type: Literal["part", "assembly"]


class Standard(SchemaModel):
    family: Literal["GB/T"]
    units: Literal["mm"]
    projection: Literal["first-angle"]


class Sheet(SchemaModel):
    size: Literal["A0", "A1", "A2", "A3", "A4"]
    orientation: Literal["landscape", "portrait"]
    scale: str
    title_block_language: Literal["en", "zh"] = "en"


NonBlankText = Annotated[str, StringConstraints(pattern=r"\S")]


class SourcedText(SchemaModel):
    text: NonBlankText
    source_id: NonBlankText


class Requirements(SchemaModel):
    material: SourcedText | None = None
    views: list[str] = Field(default_factory=list)
    technical_notes: list[SourcedText] = Field(default_factory=list)

    @field_validator("material", mode="before")
    @classmethod
    def material_requires_source(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError("material must include text and source_id")
        return value

    @field_validator("technical_notes", mode="before")
    @classmethod
    def technical_notes_require_sources(cls, value: Any) -> Any:
        if isinstance(value, list) and any(isinstance(item, str) for item in value):
            raise ValueError("technical_notes must include text and source_id")
        return value


class Geometry(SchemaModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    holes: list[dict[str, Any]] = Field(default_factory=list)
    centerlines: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[DrawingEntity] = Field(default_factory=list)
    view_entity_indices: dict[str, list[int]] = Field(default_factory=dict)


class Dimension(SchemaModel):
    kind: str
    value: float
    source_id: str
    owner_view: str | None = None
    target_entity_index: int | None = None
    orientation: Literal["horizontal", "vertical", "aligned", "diameter", "radius"] | None = None
    prefix: str | None = None
    tolerance_upper: float | None = None
    tolerance_lower: float | None = None

    def format_label(self) -> str:
        auto_prefix = self.prefix
        if auto_prefix is None:
            if self.orientation == "diameter":
                auto_prefix = "⌀"
            elif self.orientation == "radius":
                auto_prefix = "R"
            else:
                auto_prefix = ""
        base = f"{auto_prefix}{self.value:g}"
        if self.tolerance_upper is not None and self.tolerance_lower is not None:
            if abs(self.tolerance_upper + self.tolerance_lower) <= 1e-9 and self.tolerance_upper > 0:
                return f"{base}±{abs(self.tolerance_upper):g}"
            up = f"+{self.tolerance_upper:g}" if self.tolerance_upper >= 0 else f"{self.tolerance_upper:g}"
            lo = f"+{self.tolerance_lower:g}" if self.tolerance_lower > 0 else f"{self.tolerance_lower:g}"
            return f"{base} ({up}/{lo})"
        if self.tolerance_upper is not None:
            up = f"+{self.tolerance_upper:g}" if self.tolerance_upper >= 0 else f"{self.tolerance_upper:g}"
            return f"{base} {up}"
        if self.tolerance_lower is not None:
            lo = f"+{self.tolerance_lower:g}" if self.tolerance_lower > 0 else f"{self.tolerance_lower:g}"
            return f"{base} {lo}"
        return base


class Annotation(SchemaModel):
    text: str
    source_id: str | None = None


class PartListItem(SchemaModel):
    item: str
    quantity: int
    source_id: NonBlankText
    material: str | None = None


class SurfaceFinish(SchemaModel):
    """GB/T 131 / ISO 1302 surface roughness symbol specification."""

    value: NonBlankText
    source_id: NonBlankText
    machining_required: bool = True
    is_general: bool = False
    owner_view: str | None = None
    placement: Literal["top-left", "top-right", "left-upper", "right-upper"] = "top-left"


class DatumSymbol(SchemaModel):
    """GB/T 1182 / ISO 1101 datum feature symbol (triangle + boxed letter)."""

    letter: NonBlankText
    source_id: NonBlankText
    owner_view: str = "front"
    placement: Literal["left-center", "bottom-left", "top-right"] = "left-center"


class GDTFrame(SchemaModel):
    """GB/T 1182 / ISO 1101 geometric tolerance feature control frame."""

    symbol: Literal[
        "perpendicularity",
        "coaxiality",
        "parallelism",
        "runout",
        "flatness",
    ] = "perpendicularity"
    tolerance: NonBlankText
    datum_refs: list[NonBlankText] = Field(default_factory=list)
    source_id: NonBlankText
    owner_view: str = "front"
    placement: Literal["top-right", "above-label", "right-upper"] = "above-label"


class Provenance(SchemaModel):
    id: NonBlankText
    file: str
    locator: str
    confidence: float = Field(ge=0.0, le=1.0)


class Validation(SchemaModel):
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class DrawingSpec(SchemaModel):
    schema_version: Literal["1.0"]
    project: Project
    standard: Standard
    sheet: Sheet
    requirements: Requirements
    geometry: Geometry
    dimensions: list[Dimension] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    parts_list: list[PartListItem] = Field(default_factory=list)
    surface_finishes: list[SurfaceFinish] = Field(default_factory=list)
    datums: list[DatumSymbol] = Field(default_factory=list)
    gdt_frames: list[GDTFrame] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)

    @model_validator(mode="after")
    def manufacturing_facts_reference_known_provenance(self) -> DrawingSpec:
        provenance_ids = {source.id for source in self.provenance}
        if len(provenance_ids) != len(self.provenance):
            raise ValueError("provenance id values must be unique")
        unknown_source_ids = {
            dimension.source_id
            for dimension in self.dimensions
            if dimension.source_id not in provenance_ids
        }
        if unknown_source_ids:
            unknown = ", ".join(sorted(unknown_source_ids))
            raise ValueError(f"dimension source_id values are unknown: {unknown}")
        unknown_entity_source_ids = {
            entity.source_id
            for entity in self.geometry.entities
            if entity.source_id not in provenance_ids
        }
        if unknown_entity_source_ids:
            unknown = ", ".join(sorted(unknown_entity_source_ids))
            raise ValueError(f"geometry entity source_id values are unknown: {unknown}")
        material = self.requirements.material
        if material is not None and material.source_id not in provenance_ids:
            raise ValueError(
                "material source_id values are unknown: " f"{material.source_id}"
            )
        unknown_note_source_ids = {
            note.source_id
            for note in self.requirements.technical_notes
            if note.source_id not in provenance_ids
        }
        if unknown_note_source_ids:
            unknown = ", ".join(sorted(unknown_note_source_ids))
            raise ValueError(
                f"technical note source_id values are unknown: {unknown}"
            )
        unknown_part_source_ids = {
            item.source_id
            for item in self.parts_list
            if item.source_id not in provenance_ids
        }
        if unknown_part_source_ids:
            unknown = ", ".join(sorted(unknown_part_source_ids))
            raise ValueError(f"part source_id values are unknown: {unknown}")
        grouped_indices = [
            index
            for indices in self.geometry.view_entity_indices.values()
            for index in indices
        ]
        entity_indices = set(range(len(self.geometry.entities)))
        unknown_view_names = set(self.geometry.view_entity_indices) - set(self.requirements.views)
        if unknown_view_names:
            unknown = ", ".join(sorted(unknown_view_names))
            raise ValueError(f"view entity indices may only use requested views: {unknown}")
        if self.geometry.view_entity_indices and set(grouped_indices) != entity_indices:
            raise ValueError("view entity indices must cover every geometry entity exactly once")
        if len(grouped_indices) != len(set(grouped_indices)):
            raise ValueError("view entity indices must be assigned exactly once")
        if any(index not in entity_indices for index in grouped_indices):
            raise ValueError("view entity indices must be in range for geometry entities")
        requested_views = set(self.requirements.views)
        for dim_idx, dimension in enumerate(self.dimensions, start=1):
            if dimension.owner_view is not None and dimension.owner_view not in requested_views:
                raise ValueError(
                    f"dimension {dim_idx} owner_view is not in requested views: {dimension.owner_view}"
                )
            if dimension.target_entity_index is not None and dimension.target_entity_index not in entity_indices:
                raise ValueError(
                    f"dimension {dim_idx} target_entity_index is out of range: {dimension.target_entity_index}"
                )
        for sf in self.surface_finishes:
            if sf.source_id not in provenance_ids:
                raise ValueError(f"surface_finish source_id is unknown: {sf.source_id}")
            if sf.owner_view is not None and sf.owner_view not in requested_views:
                raise ValueError(f"surface_finish owner_view is not in requested views: {sf.owner_view}")
        for dat in self.datums:
            if dat.source_id not in provenance_ids:
                raise ValueError(f"datum source_id is unknown: {dat.source_id}")
            if dat.owner_view not in requested_views:
                raise ValueError(f"datum owner_view is not in requested views: {dat.owner_view}")
        for gdt in self.gdt_frames:
            if gdt.source_id not in provenance_ids:
                raise ValueError(f"gdt_frame source_id is unknown: {gdt.source_id}")
            if gdt.owner_view not in requested_views:
                raise ValueError(f"gdt_frame owner_view is not in requested views: {gdt.owner_view}")
        return self
