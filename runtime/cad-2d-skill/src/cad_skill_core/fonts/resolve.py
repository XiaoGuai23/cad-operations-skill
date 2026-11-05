"""Script-aware resolution that refuses to silently lose glyphs."""

from __future__ import annotations

from .models import FontFace, FontInventory, FontPolicy, ResolvedFont, ResolvedFontRun


class MissingGlyphError(ValueError):
    """Raised when no configured installed font can represent text safely."""

    def __init__(self, characters: tuple[str, ...], attempted_fonts: tuple[str, ...]) -> None:
        self.characters = characters
        self.attempted_fonts = attempted_fonts
        details = "".join(characters)
        attempted = ", ".join(attempted_fonts) or "(none)"
        super().__init__(f"missing glyphs: {details}; attempted fonts: {attempted}")


def resolve_font(text: str, inventory: FontInventory, policy: FontPolicy) -> ResolvedFont:
    """Select fonts in policy order, or raise instead of producing garbled text."""

    selections: list[FontFace | None] = []
    scripts: list[str] = []
    selected: list[FontFace] = []
    missing: list[str] = []
    attempted: list[str] = []

    for character in text:
        if character.isspace():
            selections.append(None)
            scripts.append("whitespace")
            continue
        script = _script_group(character)
        face, candidates = _resolve_character(character, inventory, policy, script)
        selections.append(face)
        scripts.append(script)
        for candidate in candidates:
            if candidate not in attempted:
                attempted.append(candidate)
        if face is None:
            if character not in missing:
                missing.append(character)
            continue
        if all(existing.name.casefold() != face.name.casefold() for existing in selected):
            selected.append(face)

    if missing:
        raise MissingGlyphError(tuple(missing), tuple(attempted))

    _assign_whitespace_fonts(selections)
    runs = _resolved_runs(text, selections, scripts)

    return ResolvedFont(
        primary=selected[0].name if selected else None,
        fallbacks=tuple(face.name for face in selected[1:]),
        missing_characters=(),
        requires_render_verification=any(face.is_shx for face in selected),
        resolved_runs=runs,
        primary_path=selected[0].path if selected else None,
        fallback_paths=tuple(face.path for face in selected[1:]),
    )


def _resolve_character(
    character: str, inventory: FontInventory, policy: FontPolicy, script: str
) -> tuple[FontFace | None, tuple[str, ...]]:
    candidates = policy.ordered_for(script)
    for name in candidates:
        face = inventory.find(name)
        if face is None:
            continue
        if face.is_shx or character in face.cmap:
            return face, candidates
    return None, candidates


def _assign_whitespace_fonts(selections: list[FontFace | None]) -> None:
    """Attach whitespace to the preceding, or for leading space next, font."""

    previous: FontFace | None = None
    for index, face in enumerate(selections):
        if face is not None:
            previous = face
        elif previous is not None:
            selections[index] = previous

    following: FontFace | None = None
    for index in range(len(selections) - 1, -1, -1):
        face = selections[index]
        if face is not None:
            following = face
        elif following is not None:
            selections[index] = following


def _resolved_runs(
    text: str, selections: list[FontFace | None], scripts: list[str]
) -> tuple[ResolvedFontRun, ...]:
    runs: list[ResolvedFontRun] = []
    for index, (character, face, script) in enumerate(zip(text, selections, scripts, strict=True)):
        if face is None:
            continue
        if runs and runs[-1].font == face.name and runs[-1].script == script:
            previous = runs[-1]
            runs[-1] = ResolvedFontRun(
                start=previous.start,
                end=index + 1,
                text=previous.text + character,
                script=script,
                font=face.name,
            )
            continue
        runs.append(
            ResolvedFontRun(
                start=index,
                end=index + 1,
                text=character,
                script=script,
                font=face.name,
            )
        )
    return tuple(runs)


def _script_group(character: str) -> str:
    codepoint = ord(character)
    if (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    ):
        return "cjk"
    if character.isascii() and (character.isalnum() or character in "._-/:()[]"):
        return "latin"
    return "engineering"
