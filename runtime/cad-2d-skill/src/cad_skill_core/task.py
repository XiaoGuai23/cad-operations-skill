"""Safe task-directory lifecycle contracts for CAD pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Literal


class PipelineInputError(ValueError):
    """A controlled user/task-directory error suitable for CLI exit code 2."""


@dataclass(frozen=True, slots=True)
class PipelineOptions:
    """Bounded execution choices for one pipeline run."""

    run_autocad: bool = False
    max_repair_iterations: int = 3
    autocad_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_autocad, bool):
            raise TypeError("run_autocad must be boolean")
        if (
            isinstance(self.max_repair_iterations, bool)
            or not isinstance(self.max_repair_iterations, int)
            or not 0 <= self.max_repair_iterations <= 3
        ):
            raise ValueError("max_repair_iterations must be an integer from 0 through 3")
        if self.autocad_path is not None:
            path = Path(self.autocad_path).expanduser()
            if not path.is_absolute():
                raise ValueError("autocad_path must be explicit and absolute")
            object.__setattr__(self, "autocad_path", path.resolve(strict=False))


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Published, verified outcome of one controlled pipeline run."""

    status: Literal["complete", "partial", "failed"]
    task_dir: Path
    outputs: tuple[Path, ...]
    audit_path: Path


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    """Stable identity captured for a task-owned publication directory."""

    path: Path
    device: int
    file_id: int


@dataclass(frozen=True, slots=True)
class TaskPaths:
    root: Path
    input: Path
    work: Path
    extracted: Path
    iterations: Path
    output: Path
    work_identity: DirectoryIdentity
    output_identity: DirectoryIdentity


def prepare_task(task_dir: Path) -> TaskPaths:
    """Resolve one task and create only its owned lifecycle directories."""

    supplied = Path(task_dir).expanduser()
    lexical = Path(os.path.abspath(os.fspath(supplied)))
    _reject_reparse_chain(lexical, stop=None)
    if not lexical.exists():
        raise PipelineInputError(f"task directory does not exist: {lexical}")
    if not lexical.is_dir():
        raise PipelineInputError(f"task path is not a directory: {lexical}")
    root = lexical.resolve(strict=True)
    paths = {
        "input": root / "input",
        "work": root / "work",
        "extracted": root / "work" / "extracted",
        "iterations": root / "work" / "iterations",
        "output": root / "output",
    }
    for label in ("input", "work", "extracted", "iterations", "output"):
        path = paths[label]
        _require_contained(path, root, label)
        if path.exists():
            _reject_reparse_chain(path, stop=root)
            if not path.is_dir():
                raise PipelineInputError(f"task {label} path is not a directory: {path}")
        else:
            try:
                path.mkdir()
            except OSError as error:
                raise PipelineInputError(f"could not create task {label} directory: {path}: {error}") from error
            _reject_reparse_chain(path, stop=root)
        _require_contained(path.resolve(strict=True), root, label)
    work_identity = capture_directory_identity(paths["work"], root, "work")
    output_identity = capture_directory_identity(paths["output"], root, "output")
    return TaskPaths(
        root=root,
        input=paths["input"],
        work=paths["work"],
        extracted=paths["extracted"],
        iterations=paths["iterations"],
        output=paths["output"],
        work_identity=work_identity,
        output_identity=output_identity,
    )


def capture_directory_identity(
    path: Path, root: Path, label: str
) -> DirectoryIdentity:
    """Capture a contained, non-reparse directory identity."""

    candidate = Path(os.path.abspath(os.fspath(path)))
    _require_contained(candidate, root, label)
    _reject_reparse_chain(candidate, stop=root)
    try:
        metadata = candidate.stat()
    except OSError as error:
        raise PipelineInputError(
            f"task {label} directory identity is inaccessible: {candidate}: {error}"
        ) from error
    if not candidate.is_dir():
        raise PipelineInputError(f"task {label} path is not a directory: {candidate}")
    resolved = candidate.resolve(strict=True)
    _require_contained(resolved, root, label)
    return DirectoryIdentity(resolved, int(metadata.st_dev), int(metadata.st_ino))


def validate_output_identity(paths: TaskPaths) -> None:
    """Fail closed if the prepared output path was replaced or redirected."""

    current = capture_directory_identity(paths.output, paths.root, "output")
    if current != paths.output_identity:
        raise PipelineInputError(
            "task output directory identity changed after preparation"
        )


def validate_work_identity(paths: TaskPaths) -> None:
    """Fail closed if the prepared work path was replaced or redirected."""

    current = capture_directory_identity(paths.work, paths.root, "work")
    if current != paths.work_identity:
        raise PipelineInputError(
            "task work directory identity changed after preparation"
        )


def require_task_file(path: Path, root: Path, label: str) -> Path:
    """Require an existing regular file that cannot resolve outside the task."""

    candidate = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    _require_contained(candidate, root, label)
    _reject_reparse_chain(candidate, stop=root)
    if not candidate.is_file():
        raise PipelineInputError(f"task {label} is not a regular file: {candidate}")
    resolved = candidate.resolve(strict=True)
    _require_contained(resolved, root, label)
    return resolved


def _require_contained(path: Path, root: Path, label: str) -> None:
    try:
        Path(path).resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise PipelineInputError(f"task {label} path escapes task directory: {path}") from error


def _reject_reparse_chain(path: Path, *, stop: Path | None) -> None:
    current = Path(path)
    while True:
        try:
            metadata = current.lstat()
        except OSError:
            pass
        else:
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
                raise PipelineInputError(f"task path contains a symlink or reparse point: {current}")
        if stop is not None and current == stop:
            return
        if current.parent == current:
            return
        current = current.parent
