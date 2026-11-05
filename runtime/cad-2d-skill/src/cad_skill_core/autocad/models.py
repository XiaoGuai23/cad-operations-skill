"""Immutable contracts for the AutoCAD subprocess boundary."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from math import isfinite
from typing import Literal


AutoCADErrorCode = Literal[
    "not-installed",
    "wrong-version",
    "inaccessible",
    "process-failed",
    "timeout",
    "output-invalid",
    "untrusted-executable",
    "identity-drift",
    "invalid-request",
    "workdir-failed",
    "staging-failed",
]
ExecutionStatus = Literal["succeeded", "failed", "skipped"]
AutoCADOperation = Literal["dxf-to-dwg", "plot-pdf"]


def _absolute(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class SignatureEvidence:
    status: str
    publisher: str
    thumbprint: str

    def __post_init__(self) -> None:
        if not self.status or not self.publisher:
            raise ValueError("signature status and publisher must not be empty")
        object.__setattr__(self, "thumbprint", self.thumbprint.upper())


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    path: Path
    device: int
    file_id: int
    size: int
    mtime_ns: int
    sha256: str
    signature_publisher: str
    signature_thumbprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _absolute(self.path))
        object.__setattr__(self, "sha256", self.sha256.lower())
        object.__setattr__(
            self, "signature_thumbprint", self.signature_thumbprint.upper()
        )
        if self.size <= 0 or len(self.sha256) != 64:
            raise ValueError("executable identity requires a non-empty SHA-256 file")


@dataclass(frozen=True, slots=True)
class AutoCADInstallation:
    acad_exe: Path
    core_console_exe: Path
    version: str
    fonts_dir: Path
    acad_identity: ExecutableIdentity
    core_console_identity: ExecutableIdentity
    trusted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "acad_exe", _absolute(self.acad_exe))
        object.__setattr__(self, "core_console_exe", _absolute(self.core_console_exe))
        object.__setattr__(self, "fonts_dir", _absolute(self.fonts_dir))
        if self.version != "2023":
            raise ValueError("AutoCAD installation version must be exactly 2023")
        if not self.trusted:
            raise ValueError("untrusted executables cannot form an AutoCADInstallation")
        if self.acad_identity.path != self.acad_exe:
            raise ValueError("acad.exe identity path does not match installation")
        if self.core_console_identity.path != self.core_console_exe:
            raise ValueError("Core Console identity path does not match installation")


@dataclass(frozen=True, slots=True)
class AutoCADErrorDetail:
    code: AutoCADErrorCode
    message: str
    path: Path | None = None
    discovered_version: str | None = None

    def __post_init__(self) -> None:
        if self.code not in {
            "not-installed",
            "wrong-version",
            "inaccessible",
            "process-failed",
            "timeout",
            "output-invalid",
            "untrusted-executable",
            "identity-drift",
            "invalid-request",
            "workdir-failed",
            "staging-failed",
        }:
            raise ValueError(f"unsupported AutoCAD error code: {self.code}")
        if not self.message:
            raise ValueError("AutoCAD error message must not be empty")
        if self.path is not None:
            object.__setattr__(self, "path", _absolute(self.path))


class AutoCADDiscoveryError(RuntimeError):
    """Base discovery error carrying an immutable machine-readable detail."""

    __slots__ = ("_detail",)

    def __init__(self, detail: AutoCADErrorDetail):
        super().__init__(detail.message)
        self._detail = detail

    @property
    def detail(self) -> AutoCADErrorDetail:
        return self._detail


class AutoCADNotInstalledError(AutoCADDiscoveryError):
    pass


class AutoCADWrongVersionError(AutoCADDiscoveryError):
    pass


class AutoCADInaccessibleError(AutoCADDiscoveryError):
    pass


class AutoCADUntrustedError(AutoCADDiscoveryError):
    pass


class AutoCADExecutionError(RuntimeError):
    """Controlled request validation or execution failure."""


@dataclass(frozen=True, slots=True)
class AutoCADRequest:
    input_dxf: Path
    output_dir: Path
    task_work_dir: Path | None = None
    drawing_stem: str = "drawing"
    plot_layout: str = "Layout1"
    timeout_seconds: float = 120.0
    configured_path: Path | None = None
    operations: tuple[AutoCADOperation, ...] = ("dxf-to-dwg", "plot-pdf")

    def __post_init__(self) -> None:
        _reject_reparse_components(Path(self.output_dir), "output_dir")
        if self.task_work_dir is not None:
            _reject_reparse_components(Path(self.task_work_dir), "task_work_dir")
        output_dir = _absolute(self.output_dir)
        work_dir = _absolute(
            self.task_work_dir
            if self.task_work_dir is not None
            else output_dir.parent / "work"
        )
        object.__setattr__(self, "input_dxf", _absolute(self.input_dxf))
        object.__setattr__(self, "output_dir", output_dir)
        object.__setattr__(self, "task_work_dir", work_dir)
        if self.configured_path is not None:
            object.__setattr__(self, "configured_path", _absolute(self.configured_path))
        operations = tuple(self.operations)
        if not operations or len(set(operations)) != len(operations):
            raise ValueError("AutoCAD operations must be a non-empty unique tuple")
        if any(item not in {"dxf-to-dwg", "plot-pdf"} for item in operations):
            raise ValueError("unsupported AutoCAD operation")
        object.__setattr__(self, "operations", operations)
        if not _safe_filename_stem(self.drawing_stem):
            raise ValueError("drawing_stem must be a safe filename stem without traversal")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,126}", self.plot_layout):
            raise ValueError("plot_layout must be a non-empty safe ASCII layout name")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("AutoCAD timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class AutoCADResult:
    status: ExecutionStatus
    return_code: int | None
    dwg_path: Path | None
    pdf_path: Path | None
    script_path: Path | None
    stdout_path: Path | None
    stderr_path: Path | None
    duration_seconds: float
    error: AutoCADErrorDetail | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "skipped"}:
            raise ValueError(f"unsupported AutoCAD result status: {self.status}")
        for name in ("dwg_path", "pdf_path", "script_path", "stdout_path", "stderr_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _absolute(value))
        if self.duration_seconds < 0:
            raise ValueError("AutoCAD duration must not be negative")
        if self.status == "succeeded" and (
            self.error is not None
            or self.return_code != 0
            or (self.dwg_path is None and self.pdf_path is None)
        ):
            raise ValueError("succeeded AutoCAD result requires verified output and no error")
        if self.status == "skipped" and (
            self.return_code is not None
            or self.dwg_path is not None
            or self.pdf_path is not None
            or self.error is None
            or self.error.code != "not-installed"
        ):
            raise ValueError("skipped AutoCAD result requires a not-installed error only")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed AutoCAD result requires structured error detail")
        object.__setattr__(self, "evidence", tuple(self.evidence))


def _safe_filename_stem(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value in {".", ".."}
    ):
        return False
    if value[-1:] in {" ", "."} or any(ord(character) < 32 for character in value):
        return False
    reserved = {"con", "prn", "aux", "nul"} | {
        f"{prefix}{number}"
        for prefix in ("com", "lpt")
        for number in range(1, 10)
    }
    if value.split(".", 1)[0].casefold() in reserved:
        return False
    return not any(character in '<>:"/\\|?*' for character in value)


def _reject_reparse_components(path: Path, field: str) -> None:
    """Reject existing symlink/junction components before path normalization."""

    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    for component in (lexical, *lexical.parents):
        try:
            metadata = component.lstat()
        except OSError:
            continue
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
            raise ValueError(f"{field} contains a symlink or reparse point: {component}")
