"""UTF-8 JSON persistence for drawing specifications."""

from __future__ import annotations

import json
from pathlib import Path

from .models import DrawingSpec


def load_spec(path: Path) -> DrawingSpec:
    """Load and validate a drawing specification from UTF-8 JSON."""

    with path.open(encoding="utf-8") as source:
        return DrawingSpec.model_validate(json.load(source))


def save_spec(spec: DrawingSpec, path: Path) -> None:
    """Save a validated drawing specification as readable UTF-8 JSON."""

    with path.open("w", encoding="utf-8") as destination:
        json.dump(spec.model_dump(mode="json"), destination, ensure_ascii=False, indent=2)
        destination.write("\n")
