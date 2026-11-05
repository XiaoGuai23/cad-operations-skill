"""Models and errors for traceable source-document extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceFragment:
    """A source fragment that can later become drawing provenance."""

    id: str
    file: str
    locator: str
    media_type: str
    text: str
    confidence: float


class UnsupportedSourceError(ValueError):
    """Raised when an input's extension does not have an extractor."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__(f"unsupported source file: {self.path}")


class SourceExtractionError(ValueError):
    """Raised when a supported source cannot be read or extracted."""

    def __init__(self, path: Path, locator: str | None, detail: str) -> None:
        self.path = Path(path)
        self.locator = locator
        self.detail = detail
        location = f" at {locator}" if locator else ""
        super().__init__(f"could not extract source {self.path}{location}: {detail}")
