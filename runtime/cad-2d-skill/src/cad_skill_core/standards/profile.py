"""Strict, project-configurable drawing standards loaded from YAML.

The profile is intentionally an automation configuration. It captures the
defaults approved for this project and is not a reproduction of a standards
publication.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProfileModel(BaseModel):
    """Base for profile records; unknown keys are configuration errors."""

    model_config = ConfigDict(extra="forbid", strict=True)


class LayerStyle(ProfileModel):
    """CAD layer appearance used by the project profile."""

    linetype: str
    lineweight_mm: float = Field(gt=0)
    color: int = Field(ge=1, le=255)


class TextStyle(ProfileModel):
    """Text style defaults for generated annotations."""

    font: str
    height_mm: float = Field(gt=0)
    width_factor: float = Field(gt=0)


class DimensionStyle(ProfileModel):
    """Dimension annotation defaults in millimetres."""

    text_height_mm: float = Field(gt=0)
    gap_mm: float = Field(gt=0)
    arrow_size_mm: float = Field(gt=0)
    lineweight_mm: float = Field(gt=0)


class Spacing(ProfileModel):
    """Minimum clearances used by the deterministic layout planner."""

    view_gap_mm: float = Field(gt=0)
    dimension_gap_mm: float = Field(gt=0)


class SheetSize(ProfileModel):
    """Printable sheet dimensions, expressed in millimetres."""

    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)


class ProtectedZone(ProfileModel):
    """A reserved sheet region that geometry and views must not enter."""

    anchor: Literal["bottom-right", "bottom-left", "top-right", "top-left"]
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    margin_mm: float = Field(ge=0)


class DrawingStandard(ProfileModel):
    """Validated GB/T-oriented project profile consumed by CAD generation."""

    name: str
    description: str
    profile_type: Literal["configurable-project-profile"]
    scope: Literal["automation-defaults"]
    family: Literal["GB/T"]
    units: Literal["mm"]
    projection: Literal["first-angle"]
    layers: dict[str, LayerStyle]
    text_styles: dict[str, TextStyle]
    dimension: DimensionStyle
    spacing: Spacing
    sheets: dict[Literal["A0", "A1", "A2", "A3", "A4"], SheetSize]
    protected_zones: dict[str, ProtectedZone]

    @model_validator(mode="after")
    def require_project_defaults(self) -> DrawingStandard:
        """Require every consumer-facing default while allowing named extras."""

        required_layers = {
            "OUTLINE",
            "THIN",
            "CENTER",
            "HIDDEN",
            "DIM",
            "TEXT",
            "HATCH",
            "FRAME",
        }
        missing_layers = sorted(required_layers - self.layers.keys())
        missing_styles = [] if "normal" in self.text_styles else ["normal"]
        required_sheets = {"A0", "A1", "A2", "A3", "A4"}
        missing_sheets = sorted(required_sheets - self.sheets.keys())
        missing_zones = [] if "title_block" in self.protected_zones else ["title_block"]

        missing = []
        if missing_layers:
            missing.append(f"missing required layers: {', '.join(missing_layers)}")
        if missing_styles:
            missing.append(f"missing required text styles: {', '.join(missing_styles)}")
        if missing_sheets:
            missing.append(f"missing required sheets: {', '.join(missing_sheets)}")
        if missing_zones:
            missing.append(f"missing required protected zones: {', '.join(missing_zones)}")
        if missing:
            raise ValueError("; ".join(missing))
        return self


def load_standard(path: Path) -> DrawingStandard:
    """Load and strictly validate a UTF-8 YAML project profile."""

    with path.open(encoding="utf-8") as source:
        raw = yaml.safe_load(source)
    return DrawingStandard.model_validate(raw)


BUILTIN_STANDARD_PROFILES: dict[str, dict[str, str]] = {
    "gb-mechanical": {
        "filename": "gb-mechanical.yaml",
        "label_zh": "GB/T 机械精加工标准 (0.70/0.35mm)",
    },
    "gb-heavy-machinery": {
        "filename": "gb-heavy-machinery.yaml",
        "label_zh": "GB/T 重工与夹具总装标准 (0.80mm高对比)",
    },
    "gb-civil-construction": {
        "filename": "gb-civil-construction.yaml",
        "label_zh": "GB/T 市政与结构施工图标准 (彩色分层)",
    },
    "iso-international": {
        "filename": "iso-international.yaml",
        "label_zh": "ISO 128 国际外贸蓝图标准 (英文/0.50mm)",
    },
}


def resolve_standard_profile_path(profile_id: str | None = None, *, standards_dir: Path | None = None) -> Path:
    """Resolve a built-in standard preset ID to its validated YAML file path."""
    root = standards_dir or (Path(__file__).resolve().parents[3] / "knowledge" / "standards")
    key = (profile_id or "gb-mechanical").strip()
    if key not in BUILTIN_STANDARD_PROFILES:
        raise ValueError(f"unknown standard_profile {key!r}; valid presets: {sorted(BUILTIN_STANDARD_PROFILES)}")
    return root / BUILTIN_STANDARD_PROFILES[key]["filename"]


def list_builtin_standards(*, standards_dir: Path | None = None) -> list[dict[str, str]]:
    """Return metadata for all built-in engineering drawing standard presets."""
    items: list[dict[str, str]] = []
    for pid, meta in BUILTIN_STANDARD_PROFILES.items():
        path = resolve_standard_profile_path(pid, standards_dir=standards_dir)
        std = load_standard(path)
        items.append(
            {
                "id": pid,
                "label_zh": meta["label_zh"],
                "name": std.name,
                "description": std.description,
                "outline_lineweight_mm": f"{std.layers['OUTLINE'].lineweight_mm:.2f}",
            }
        )
    return items
