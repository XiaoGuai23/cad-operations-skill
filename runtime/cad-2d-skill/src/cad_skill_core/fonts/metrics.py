"""Glyph-accurate text advance measurement for CJK, Latin, and CAD symbols."""

from __future__ import annotations

from functools import lru_cache
from math import isfinite
from pathlib import Path
import unicodedata

from fontTools.ttLib import TTFont


@lru_cache(maxsize=64)
def _cached_glyph_advances(
    resolved_path: str, size: int, mtime_ns: int
) -> tuple[int, dict[int, int], int]:
    del size, mtime_ns
    path = Path(resolved_path)
    font_number = 0 if path.suffix.casefold() == ".ttc" else -1
    with TTFont(path, lazy=True, fontNumber=font_number) as font:
        units_per_em = int(font["head"].unitsPerEm)
        hmtx = font["hmtx"].metrics
        default_advance = int(units_per_em * 0.6)
        codepoint_advances: dict[int, int] = {}
        for table in font["cmap"].tables:
            if not table.isUnicode():
                continue
            for codepoint, glyph_name in table.cmap.items():
                if codepoint not in codepoint_advances and glyph_name in hmtx:
                    codepoint_advances[int(codepoint)] = int(hmtx[glyph_name][0])
        return units_per_em, codepoint_advances, default_advance


def _heuristic_char_ratio(ch: str) -> float:
    if ch.isspace():
        return 0.35
    eaw = unicodedata.east_asian_width(ch)
    if eaw in {"W", "F"}:
        return 1.0
    if ch in {"⌀", "∅", "Φ", "φ", "±", "°", "×"}:
        return 0.82
    if ch in {"i", "l", "I", "1", ".", ",", ":", ";", "!", "|", "-", "'"}:
        return 0.38
    if ch in {"m", "w", "M", "W", "%", "@"}:
        return 0.88
    return 0.62


def measure_text_width_mm(
    text: str,
    height_mm: float,
    *,
    font_path: Path | None = None,
    width_factor: float = 1.0,
) -> float:
    """Measure rendered single-line or multi-line text width in millimetres.

    Uses real TrueType/OpenType ``hmtx`` advance widths when ``font_path`` is
    available and readable; falls back to East-Asian-Width + CAD symbol ratios
    when no concrete font file path is supplied.
    """

    if not isfinite(height_mm) or height_mm <= 0:
        raise ValueError("height_mm must be a positive finite number")
    if not isfinite(width_factor) or width_factor <= 0:
        raise ValueError("width_factor must be a positive finite number")
    if not text:
        return height_mm

    lines = text.splitlines() or [text]
    font_metrics: tuple[int, dict[int, int], int] | None = None
    if font_path is not None:
        candidate = Path(font_path)
        if candidate.is_file() and candidate.suffix.casefold() in {".ttf", ".otf", ".ttc"}:
            try:
                stat = candidate.stat()
                font_metrics = _cached_glyph_advances(
                    str(candidate.resolve()), stat.st_size, stat.st_mtime_ns
                )
            except Exception:
                font_metrics = None

    max_line_em = 0.0
    for line in lines:
        if font_metrics is not None:
            upm, advances, default_adv = font_metrics
            line_em = sum(
                advances.get(ord(ch), int(upm * _heuristic_char_ratio(ch)) if ord(ch) > 127 else default_adv)
                for ch in line
            ) / max(1, upm)
        else:
            line_em = sum(_heuristic_char_ratio(ch) for ch in line)
        max_line_em = max(max_line_em, line_em)

    measured = max_line_em * height_mm * width_factor
    return max(height_mm * 0.5, measured)
