"""Deterministic local font discovery and annotation resolution."""

from .inventory import build_font_search_roots, discover_fonts
from .metrics import measure_text_width_mm
from .models import FontFace, FontInventory, FontPolicy, ResolvedFont, ResolvedFontRun, load_font_policy
from .resolve import MissingGlyphError, resolve_font

__all__ = [
    "FontFace",
    "FontInventory",
    "FontPolicy",
    "load_font_policy",
    "MissingGlyphError",
    "ResolvedFont",
    "ResolvedFontRun",
    "build_font_search_roots",
    "discover_fonts",
    "measure_text_width_mm",
    "resolve_font",
]
