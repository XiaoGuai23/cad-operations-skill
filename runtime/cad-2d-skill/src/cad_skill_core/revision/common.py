"""Shared identity and evidence helpers for local CAD operations."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re

from ezdxf.lldxf.tagwriter import TagCollector


class RevisionError(ValueError):
    """A plan cannot be safely applied or its result could not be verified."""


def validate_tolerance(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RevisionError('tolerance must be a finite positive number')
    try:
        number = float(value)
    except OverflowError as error:
        raise RevisionError('tolerance must be a finite positive number') from error
    if not math.isfinite(number) or number <= 0:
        raise RevisionError('tolerance must be a finite positive number')
    return number


def stable_id(entity) -> str | None:
    if entity.has_xdata('CAD_SKILL_ID'):
        tags = entity.get_xdata('CAD_SKILL_ID')
        if tags and tags[0].code == 1000 and tags[0].value:
            return str(tags[0].value)
    return None


def resolve_entity(doc, selector: dict):
    if not isinstance(selector, dict) or set(selector) not in ({'handle'}, {'id'}):
        raise RevisionError('selector requires exactly one handle or id')
    key = next(iter(selector))
    value = selector[key]
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RevisionError('selector value must be a nonempty string')
    if key == 'handle':
        if not re.fullmatch(r'[0-9a-fA-F]+', value):
            raise RevisionError('invalid entity handle')
        matches = [e for e in doc.modelspace() if e.is_alive and e.dxf.handle == value.upper()]
    else:
        matches = [e for e in doc.modelspace() if e.is_alive and stable_id(e) == value]
    if len(matches) != 1:
        raise RevisionError(f'selector must resolve to one live modelspace entity: {selector}; matched {len(matches)}')
    return matches[0]


def resolve_entities(doc, selectors: list[dict]) -> list:
    if not isinstance(selectors, list) or not selectors:
        raise RevisionError('targets must be a nonempty selector list')
    entities = [resolve_entity(doc, s) for s in selectors]
    handles = [e.dxf.handle for e in entities]
    if len(handles) != len(set(handles)):
        raise RevisionError('duplicate target entities')
    return entities


def sha256_file(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _plain(value):
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, bytes):
        return value.hex()
    try:
        return [_plain(x) for x in value]
    except TypeError:
        return str(value)


def snapshot_modelspace(doc) -> dict:
    """Capture full entity tags, including hatch paths, text and XDATA."""
    return {
        e.dxf.handle: [[tag.code, _plain(tag.value)] for tag in TagCollector.dxftags(e)]
        for e in doc.modelspace() if e.is_alive
    }


def values_close(a, b, tolerance: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_close(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_close(a[k], b[k], tolerance) for k in a)
    return a == b


def compare_untouched(before: dict, after: dict, allowed_handles: set[str], tolerance: float) -> list[str]:
    """Find unexplained creations/deletions or changes in serialized entities."""
    return sorted(h for h in before.keys() | after.keys()
                  if h not in allowed_handles and
                  (h not in before or h not in after or not values_close(before[h], after[h], tolerance)))
