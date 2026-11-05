"""Immutable records shared by the local font inventory and resolver."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class FontFace:
    """A locally installed font and any glyph coverage that was verified."""

    name: str
    path: Path | None
    format: str
    cmap: frozenset[str] = frozenset()

    @property
    def is_shx(self) -> bool:
        """Whether coverage must be confirmed from an AutoCAD render."""

        return self.format.casefold() == "shx"

    @property
    def glyphs(self) -> frozenset[str]:
        """Alias for the verified cmap, retained for readable consumers."""

        return self.cmap


@dataclass(frozen=True, slots=True)
class FontInventory:
    """Ordered, deduplicated inventory; earlier search roots have priority."""

    fonts: tuple[FontFace, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fonts", tuple(self.fonts))

    def find(self, name: str) -> FontFace | None:
        """Find a font by name without changing the configured order."""

        target = name.casefold()
        return next((font for font in self.fonts if font.name.casefold() == target), None)

    @property
    def names(self) -> tuple[str, ...]:
        """Installed font names in deterministic preference order."""

        return tuple(font.name for font in self.fonts)


@dataclass(frozen=True, slots=True)
class FontPolicy:
    """Ordered project choices for the script classes used in CAD text."""

    cjk: tuple[str, ...]
    latin: tuple[str, ...]
    engineering: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FontPolicy:
        """Build a policy from either a direct or ``fonts``-nested mapping."""

        raw = value.get("fonts", value)
        if not isinstance(raw, Mapping):
            raise ValueError("font policy must contain a mapping of script groups")

        def ordered(group: str) -> tuple[str, ...]:
            names = raw.get(group)
            if not isinstance(names, (list, tuple)) or not names:
                raise ValueError(f"font policy requires a non-empty {group!r} list")
            if not all(isinstance(name, str) and name for name in names):
                raise ValueError(f"font policy {group!r} entries must be non-empty strings")
            return tuple(names)

        return cls(cjk=ordered("cjk"), latin=ordered("latin"), engineering=ordered("engineering"))

    def ordered_for(self, script: str) -> tuple[str, ...]:
        """Return the configured font order for a classified character."""

        try:
            return getattr(self, script)
        except AttributeError as error:
            raise ValueError(f"unknown font script group: {script}") from error


@dataclass(frozen=True, slots=True)
class ResolvedFont:
    """Font selections required to write one Unicode annotation safely."""

    primary: str | None
    fallbacks: tuple[str, ...]
    missing_characters: tuple[str, ...]
    requires_render_verification: bool
    resolved_runs: tuple[ResolvedFontRun, ...] = ()
    primary_path: Path | None = None
    fallback_paths: tuple[Path | None, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedFontRun:
    """One contiguous annotation segment and the font selected to write it."""

    start: int
    end: int
    text: str
    script: str
    font: str


def load_font_policy(path: Path) -> FontPolicy:
    """Load an ordered UTF-8 YAML font policy without changing its choices."""

    with path.open(encoding="utf-8") as source:
        value = yaml.safe_load(source)
    if not isinstance(value, Mapping):
        raise ValueError("font policy YAML must contain a mapping")
    return FontPolicy.from_mapping(value)
