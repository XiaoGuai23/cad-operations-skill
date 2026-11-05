"""Deterministic discovery of locally installed AutoCAD and Windows fonts."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from fontTools.ttLib import TTFont, TTLibError

from .models import FontFace, FontInventory


_FONT_SUFFIXES = {".otf", ".shx", ".ttc", ".ttf"}


def build_font_search_roots(
    *, autocad_roots: Sequence[Path], windows_roots: Sequence[Path]
) -> tuple[Path, ...]:
    """Validate configured roots and return AutoCAD locations before Windows.

    The separate parameters make precedence a package contract rather than a
    convention imposed on every caller. Paths may be absent on a given machine;
    discovery safely ignores absent roots so configuration remains portable.
    """

    roots = tuple(autocad_roots) + tuple(windows_roots)
    if any(not isinstance(root, Path) for root in roots):
        raise TypeError("font search roots must be pathlib.Path instances")
    return roots


def discover_fonts(search_roots: Sequence[Path]) -> FontInventory:
    """Discover fonts in root order, keeping AutoCAD roots ahead of Windows.

    Callers pass configured AutoCAD roots before the Windows Fonts directory.
    A duplicate family from a later root is ignored, so the order is stable and
    licenses remain local to the machine rather than copied into this package.
    """

    faces: list[FontFace] = []
    seen_names: set[str] = set()
    for root in search_roots:
        for path in _font_paths(root):
            face = _read_font(path)
            if face is None or face.name.casefold() in seen_names:
                continue
            seen_names.add(face.name.casefold())
            faces.append(face)
    return FontInventory(tuple(faces))


def _font_paths(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    try:
        paths = (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in _FONT_SUFFIXES)
        return tuple(sorted(paths, key=lambda path: (str(path.relative_to(root)).casefold(), str(path))))
    except OSError:
        return ()


def _read_font(path: Path) -> FontFace | None:
    extension = path.suffix.casefold()
    if extension == ".shx":
        # SHX is an AutoCAD shape format. It has no Unicode cmap we can
        # truthfully inspect, so its later selection requires render checking.
        return FontFace(name=path.stem, path=path, format="shx")
    try:
        # Own the stream so it is closed even when FontTools raises partway
        # through TTFont construction (before its context manager exists).
        with path.open("rb") as stream:
            with TTFont(
                stream,
                lazy=True,
                fontNumber=0 if extension == ".ttc" else -1,
            ) as font:
                return FontFace(
                    name=_font_name(font, path),
                    path=path,
                    format=extension.removeprefix("."),
                    cmap=frozenset(
                        chr(codepoint)
                        for table in font["cmap"].tables
                        if table.isUnicode()
                        for codepoint in table.cmap
                    ),
                )
    except (OSError, TTLibError, KeyError):
        return None


def _font_name(font: TTFont, path: Path) -> str:
    names = font["name"].names
    for name_id in (16, 1):
        for record in names:
            if record.nameID == name_id:
                try:
                    value = record.toUnicode().strip()
                except UnicodeError:
                    continue
                if value:
                    return value
    return path.stem
