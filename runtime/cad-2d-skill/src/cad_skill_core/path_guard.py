"""Shared single-segment filename/task_id validation and directory containment guards."""

from __future__ import annotations

from pathlib import Path
import re

_SAFE_SINGLE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff._-]{0,100}$")
_WIN_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class MCPSandboxError(ValueError):
    """Raised when a path or single-segment identifier escapes allowed workspace boundaries."""


def validate_safe_single_name(value: str, *, label: str = "name") -> str:
    """Validate that `value` is a single safe directory/file stem with no traversal."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise MCPSandboxError(f"invalid {label}: must be a non-empty trimmed string")
    if value in {".", ".."} or value.endswith(".") or any(sep in value for sep in ("/", "\\", ":", "\x00")):
        raise MCPSandboxError(f"invalid {label} {value!r}: path separators and traversal tokens are forbidden")
    if any(ord(ch) < 32 for ch in value):
        raise MCPSandboxError(f"invalid {label} {value!r}: control characters are forbidden")
    if not _SAFE_SINGLE_NAME_RE.fullmatch(value):
        raise MCPSandboxError(f"invalid {label} {value!r}: contains disallowed characters")
    stem_head = value.split(".", 1)[0].upper()
    if stem_head in _WIN_RESERVED_NAMES:
        raise MCPSandboxError(f"invalid {label} {value!r}: reserved Windows device name")
    return value


def resolve_safe_child_path(base_dir: Path, single_name: str, *, label: str = "name") -> Path:
    """Resolve `base_dir / single_name` and verify the resolved target remains a direct child of `base_dir`."""
    clean = validate_safe_single_name(single_name, label=label)
    root = Path(base_dir).expanduser().resolve()
    target = (root / clean).resolve()
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise MCPSandboxError(f"{label} {single_name!r} escaped output directory {root}") from exc
    if len(rel.parts) != 1 or target.parent != root:
        raise MCPSandboxError(f"{label} {single_name!r} must stay directly inside {root}")
    return target
