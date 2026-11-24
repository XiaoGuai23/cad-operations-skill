"""Learn reusable DrawingStandard profiles from sample DXF drawings."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import bbox
import yaml

from .profile import (
    DimensionStyle,
    DrawingStandard,
    LayerStyle,
    ProtectedZone,
    SheetSize,
    Spacing,
    TextStyle,
    load_standard,
)

_DEFAULT_PROFILE_PATH = (
    Path(__file__).resolve().parents[3] / "knowledge" / "standards" / "gb-mechanical.yaml"
)

_LAYER_ALIASES: dict[str, tuple[str, ...]] = {
    "OUTLINE": ("OUTLINE", "STRUCT", "PAVEMENT", "VISIBLE", "CONTOUR", "粗实线", "轮廓", "轮廓线"),
    "THIN": ("THIN", "WIRE", "EQUIP", "FINE", "细实线"),
    "CENTER": ("CENTER", "AXIS", "CENTERLINE", "中心线", "轴线"),
    "HIDDEN": ("HIDDEN", "DASH", "DASHED", "虚线"),
    "DIM": ("DIM", "DIMENSION", "DIMS", "尺寸", "标注"),
    "TEXT": ("TEXT", "NOTE", "NOTES", "ANNOTATION", "文字", "说明"),
    "HATCH": ("HATCH", "SECTION", "FILL", "剖面", "填充"),
    "FRAME": ("FRAME", "BORDER", "TITLE", "TITLEBLOCK", "图框", "标题栏"),
}

_STANDARD_SHEETS: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}


@dataclass(frozen=True, slots=True)
class SampleLearningResult:
    """Learned DrawingStandard plus diagnostic evidence from a sample DXF."""

    standard: DrawingStandard
    matched_layers: dict[str, str]
    detected_sheet: str | None
    sample_extents_mm: tuple[float, float] | None
    sample_styles: tuple[str, ...]
    sample_dimstyles: tuple[str, ...]
    detected_habits: dict[str, Any] = field(default_factory=dict)


def learn_standard_from_dxf(
    dxf_path: Path,
    *,
    base_standard: DrawingStandard | None = None,
    profile_name: str | None = None,
    preference_memory_path: Path | None = None,
    confirm_preferences: bool = False,
) -> SampleLearningResult:
    """Inspect a sample DXF read-only and derive a validated DrawingStandard."""

    path = Path(dxf_path)
    if not path.is_file():
        raise FileNotFoundError(f"sample DXF does not exist: {path}")
    doc = ezdxf.readfile(path)
    base = (
        base_standard.model_copy(deep=True)
        if base_standard is not None
        else load_standard(_DEFAULT_PROFILE_PATH)
    )

    sample_layers = {
        layer.dxf.name.upper(): layer
        for layer in doc.layers
        if layer.dxf.name.upper() not in {"0", "DEFPOINTS"}
    }
    learned_layers: dict[str, LayerStyle] = {}
    matched_layers: dict[str, str] = {}

    for canonical, aliases in _LAYER_ALIASES.items():
        fallback = base.layers[canonical]
        chosen = None
        for alias in aliases:
            if alias.upper() in sample_layers:
                chosen = sample_layers[alias.upper()]
                matched_layers[canonical] = chosen.dxf.name
                break
        if chosen is None:
            learned_layers[canonical] = fallback
            continue
        raw_color = abs(int(chosen.color)) if int(chosen.color) != 0 else fallback.color
        color = min(max(raw_color, 1), 255)
        raw_lw = int(chosen.dxf.get("lineweight", -1))
        lineweight_mm = round(raw_lw / 100.0, 2) if raw_lw > 0 else fallback.lineweight_mm
        raw_lt = str(chosen.dxf.get("linetype", fallback.linetype)).strip().upper()
        linetype = raw_lt if raw_lt in {"CONTINUOUS", "CENTER", "HIDDEN"} else fallback.linetype
        learned_layers[canonical] = LayerStyle(
            linetype=linetype,
            lineweight_mm=lineweight_mm,
            color=color,
        )

    # Extract text styles
    learned_text_styles = {
        key: style.model_copy(deep=True) for key, style in base.text_styles.items()
    }
    sample_style_names: list[str] = []
    preferred_text_style = None
    for style in doc.styles:
        sname = style.dxf.name
        sample_style_names.append(sname)
        if sname.upper() in {"NORMAL", "CN", "CNSTYLE", "FANGSONG", "TEXT", "HZ"}:
            preferred_text_style = style
            break
    if preferred_text_style is not None:
        raw_h = float(preferred_text_style.dxf.get("height", 0.0) or 0.0)
        raw_w = float(preferred_text_style.dxf.get("width", 1.0) or 1.0)
        height_mm = raw_h if (isfinite(raw_h) and 1.5 <= raw_h <= 20.0) else base.text_styles["normal"].height_mm
        width_factor = raw_w if (isfinite(raw_w) and 0.3 <= raw_w <= 2.0) else base.text_styles["normal"].width_factor
        learned_text_styles["normal"] = TextStyle(
            font=base.text_styles["normal"].font,
            height_mm=round(height_mm, 2),
            width_factor=round(width_factor, 2),
        )

    # Extract dimension style parameters (prefer user/custom dimstyles over built-in defaults)
    sample_dimstyle_names = [ds.dxf.name for ds in doc.dimstyles]
    builtin_dimstyles = {"STANDARD", "ISO-25", "ANNOTATIVE", "EZDXF"}
    chosen_dim = next(
        (
            ds
            for ds in doc.dimstyles
            if ds.dxf.name.upper() not in builtin_dimstyles
            and not ds.dxf.name.upper().startswith("EZ_")
        ),
        None,
    )
    if chosen_dim is None:
        chosen_dim = next(
            (ds for ds in doc.dimstyles if ds.dxf.name.upper() != "STANDARD"),
            doc.dimstyles.get("Standard") if "Standard" in doc.dimstyles else None,
        )

    dim_base = base.dimension
    if chosen_dim is not None:
        dimtxt = float(chosen_dim.dxf.get("dimtxt", dim_base.text_height_mm) or dim_base.text_height_mm)
        dimgap = float(chosen_dim.dxf.get("dimgap", dim_base.gap_mm) or dim_base.gap_mm)
        dimasz = float(chosen_dim.dxf.get("dimasz", dim_base.arrow_size_mm) or dim_base.arrow_size_mm)
        learned_dim = DimensionStyle(
            text_height_mm=round(dimtxt if 1.5 <= dimtxt <= 20.0 else dim_base.text_height_mm, 2),
            gap_mm=round(dimgap if 0.5 <= dimgap <= 25.0 else dim_base.gap_mm, 2),
            arrow_size_mm=round(dimasz if 1.0 <= dimasz <= 20.0 else dim_base.arrow_size_mm, 2),
            lineweight_mm=dim_base.lineweight_mm,
        )
    else:
        learned_dim = dim_base.model_copy(deep=True)

    # Detect sheet extents & title block zone if modelspace contains frame geometry
    ext = bbox.extents(doc.modelspace())
    detected_sheet: str | None = None
    sample_extents_mm: tuple[float, float] | None = None
    protected_zones = {
        key: zone.model_copy(deep=True) for key, zone in base.protected_zones.items()
    }
    if ext.has_data:
        width = float(ext.extmax.x - ext.extmin.x)
        height = float(ext.extmax.y - ext.extmin.y)
        if isfinite(width) and isfinite(height) and width > 0 and height > 0:
            sample_extents_mm = (round(width, 2), round(height, 2))
            span_long, span_short = max(width, height), min(width, height)
            best_name = None
            best_err = float("inf")
            for sheet_name, (sw, sh) in _STANDARD_SHEETS.items():
                err = abs(span_long - sw) + abs(span_short - sh)
                if err < best_err and err <= 35.0:
                    best_err = err
                    best_name = sheet_name
            detected_sheet = best_name
            tb_zone = _detect_title_block_zone(doc, ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
            if tb_zone is not None:
                protected_zones["title_block"] = tb_zone

    sheets = {
        name: SheetSize(width_mm=size.width_mm, height_mm=size.height_mm)
        for name, size in base.sheets.items()
    }
    standard = DrawingStandard(
        name=profile_name or f"learned-{path.stem}",
        description=(
            f"Configurable project profile learned from sample DXF '{path.name}'; "
            "not a complete reproduction of any standards publication."
        ),
        profile_type="configurable-project-profile",
        scope="automation-defaults",
        family="GB/T",
        units="mm",
        projection="first-angle",
        layers=learned_layers,
        text_styles=learned_text_styles,
        dimension=learned_dim,
        spacing=Spacing(
            view_gap_mm=base.spacing.view_gap_mm,
            dimension_gap_mm=base.spacing.dimension_gap_mm,
        ),
        sheets=sheets,
        protected_zones=protected_zones,
    )
    from .preference_memory import extract_habits_from_sample_dxf, learn_preferences_from_sample_dxf

    habit_info = extract_habits_from_sample_dxf(path)
    if preference_memory_path is not None:
        learn_preferences_from_sample_dxf(
            path,
            preference_memory_path,
            confirm=confirm_preferences,
        )
    return SampleLearningResult(
        standard=standard,
        matched_layers=matched_layers,
        detected_sheet=detected_sheet,
        sample_extents_mm=sample_extents_mm,
        sample_styles=tuple(sample_style_names),
        sample_dimstyles=tuple(sample_dimstyle_names),
        detected_habits=habit_info["habits"],
    )


def _detect_title_block_zone(
    doc: ezdxf.document.Drawing,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> ProtectedZone | None:
    sheet_w = max_x - min_x
    sheet_h = max_y - min_y
    if sheet_w < 180.0 or sheet_h < 120.0:
        return None
    candidates: list[tuple[float, float]] = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).upper()
        if layer not in {"FRAME", "BORDER", "TITLE", "TITLEBLOCK"}:
            continue
        if entity.dxftype() == "LWPOLYLINE" and entity.closed:
            e_box = bbox.extents([entity])
            if not e_box.has_data:
                continue
            bw = float(e_box.extmax.x - e_box.extmin.x)
            bh = float(e_box.extmax.y - e_box.extmin.y)
            if (
                80.0 <= bw <= sheet_w * 0.65
                and 20.0 <= bh <= sheet_h * 0.45
                and float(e_box.extmax.x) >= max_x - 30.0
                and float(e_box.extmin.y) <= min_y + 30.0
            ):
                candidates.append((round(bw, 2), round(bh, 2)))
    if not candidates:
        return None
    bw, bh = max(candidates, key=lambda item: item[0] * item[1])
    return ProtectedZone(anchor="bottom-right", width_mm=bw, height_mm=bh, margin_mm=0.0)


def save_standard_yaml(standard: DrawingStandard, path: Path) -> Path:
    """Serialize a validated DrawingStandard to UTF-8 YAML."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = standard.model_dump(mode="json")
    destination.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return destination
