"""Controlled workspace sandboxing, path boundary checks, and render locking for MCP."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any

from cad_skill_core.rendering.preview import _FONT_RENDER_LOCK

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,80}$")
_SAFE_FILENAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff. ()_-]{0,120}$")
_MAX_INPUT_BYTES = 32 * 1024 * 1024  # 32 MiB guard against oversized uploads


class MCPSandboxError(ValueError):
    """Raised when a path escapes allowed MCP workspace boundaries or limits."""


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def default_runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


class MCPWorkspaceSandbox:
    """Isolates MCP/API tasks inside a controlled root directory."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        *,
        allowed_read_roots: Sequence[Path] | None = None,
        max_dxf_entities: int = 100_000,
    ) -> None:
        self.max_dxf_entities = int(max_dxf_entities)
        env_ws = os.environ.get("CAD_MCP_WORKSPACE")
        raw_root = (
            Path(workspace_root)
            if workspace_root is not None
            else (Path(env_ws) if env_ws else default_repo_root() / "output" / "mcp_workspace")
        )
        self.workspace_root = raw_root.expanduser().resolve()
        self.tasks_root = self.workspace_root / "tasks"
        self.uploads_root = self.workspace_root / "uploads"
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self.uploads_root.mkdir(parents=True, exist_ok=True)

        repo_root = default_repo_root()
        runtime_root = default_runtime_root()
        default_reads = [
            self.workspace_root,
            repo_root / "examples",
            runtime_root / "examples",
            runtime_root / "knowledge",
        ]
        extras = [Path(p).expanduser().resolve() for p in (allowed_read_roots or ())]
        self.allowed_read_roots: tuple[Path, ...] = tuple(
            dict.fromkeys(p.resolve() for p in (*default_reads, *extras) if p.exists() or p == self.workspace_root)
        )
        self.render_lock = _FONT_RENDER_LOCK

    def allocate_task_dir(self, prefix: str = "task") -> tuple[str, Path]:
        clean_prefix = re.sub(r"[^A-Za-z0-9_-]", "", prefix)[:8] or "t"
        stamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
        task_id = f"{clean_prefix}-{stamp}-{uuid.uuid4().hex[:6]}"
        task_dir = (self.tasks_root / task_id).resolve()
        self._assert_within(task_dir, self.tasks_root)
        task_dir.mkdir(parents=True, exist_ok=False)
        return task_id, task_dir

    def resolve_task_dir(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise MCPSandboxError(f"invalid task_id format: {task_id!r}")
        candidate = (self.tasks_root / task_id).resolve()
        self._assert_within(candidate, self.tasks_root)
        if not candidate.is_dir():
            raise MCPSandboxError(f"unknown task_id: {task_id}")
        return candidate

    def resolve_task_artifact(self, task_id: str, filename: str) -> Path:
        if not isinstance(filename, str) or ".." in filename or "/" in filename or "\\" in filename:
            raise MCPSandboxError(f"invalid artifact filename: {filename!r}")
        if not _SAFE_FILENAME_RE.fullmatch(filename):
            raise MCPSandboxError(f"disallowed artifact filename: {filename!r}")
        task_dir = self.resolve_task_dir(task_id)
        for sub in (task_dir / "output" / filename, task_dir / filename):
            resolved = sub.resolve()
            self._assert_within(resolved, task_dir)
            if resolved.is_file():
                return resolved
        raise MCPSandboxError(f"artifact {filename!r} not found in task {task_id}")

    def resolve_readable_file(self, supplied_path: str | Path) -> Path:
        raw = str(supplied_path).strip()
        if not raw:
            raise MCPSandboxError("empty file path")
        path_obj = Path(raw).expanduser()
        candidates = (
            [path_obj]
            if path_obj.is_absolute()
            else [self.workspace_root / path_obj, default_repo_root() / path_obj]
        )
        for cand in candidates:
            try:
                resolved = cand.resolve(strict=False)
            except OSError as exc:
                raise MCPSandboxError(f"invalid path {raw!r}: {exc}") from exc
            if any(self._is_within(resolved, root) for root in self.allowed_read_roots):
                if not resolved.is_file():
                    raise MCPSandboxError(f"file does not exist: {raw}")
                if resolved.is_symlink():
                    raise MCPSandboxError(f"symlinks are not permitted in sandbox: {raw}")
                size = resolved.stat().st_size
                if size > _MAX_INPUT_BYTES:
                    raise MCPSandboxError(f"input file exceeds {_MAX_INPUT_BYTES} byte limit: {raw}")
                if resolved.suffix.casefold() == ".dxf":
                    from .security import inspect_dxf_safety_limits

                    inspect_dxf_safety_limits(resolved, max_entities=self.max_dxf_entities)
                return resolved
        raise MCPSandboxError(
            f"path {raw!r} escapes allowed sandbox roots {[str(r) for r in self.allowed_read_roots]}"
        )

    def write_task_metadata(self, task_id: str, metadata: dict[str, Any]) -> Path:
        task_dir = self.resolve_task_dir(task_id)
        meta_path = task_dir / "task-meta.json"
        payload = {
            "task_id": task_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta_path

    def read_task_metadata(self, task_id: str) -> dict[str, Any]:
        task_dir = self.resolve_task_dir(task_id)
        meta_path = task_dir / "task-meta.json"
        if not meta_path.is_file():
            raise MCPSandboxError(f"task metadata missing for {task_id}")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    @staticmethod
    def _is_within(target: Path, root: Path) -> bool:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            return False

    def _assert_within(self, target: Path, root: Path) -> None:
        if not self._is_within(target, root):
            raise MCPSandboxError(f"path {target} escapes sandbox root {root}")
