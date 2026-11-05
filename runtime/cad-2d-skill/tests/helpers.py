"""Shared black-box fixture helpers."""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_example(name: str, destination_root: Path) -> Path:
    """Copy one repository example without mutating its checked-in source."""

    source = Path(__file__).parents[1] / "examples" / name
    destination = Path(destination_root) / name
    # Examples are also used for manual runs. Their output/iterations are not
    # fixtures and may contain newer user work; leave them in place, uncopied.
    destination.mkdir(parents=True)
    shutil.copytree(source / 'input', destination / 'input')
    (destination / 'work').mkdir()
    shutil.copy2(source / 'work/drawing-spec.json', destination / 'work/drawing-spec.json')
    return destination
