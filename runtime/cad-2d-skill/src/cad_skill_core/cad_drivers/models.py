"""Immutable contracts and protocols for multi-version, multi-vendor native CAD drivers."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any, Literal, Protocol

from cad_skill_core.autocad.models import (
    ExecutableIdentity,
    _reject_reparse_components,
    _safe_filename_stem,
)

VendorId = Literal["autodesk", "zwsoft", "gstarsoft", "caxa", "oda"]
DriverStatus = Literal[
    "available",
    "not-installed",
    "untrusted",
    "inaccessible",
    "wrong-version",
]
NativeJobKind = Literal[
    "convert-and-plot",
    "dwg-to-dxf",
    "build-3dsolid",
    "probe-native-associativity",
]


def _abs_path(path: Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class CADReleaseSpec:
    """Release metadata for one supported CAD product version."""

    vendor: VendorId
    product_family: str
    version: str
    release_code: str
    version_pattern: str
    registry_subkeys: tuple[str, ...] = ()
    default_dwg_magic: bytes = b"AC1032"


@dataclass(frozen=True, slots=True)
class NativeCADInstallation:
    """Signature-verified local CAD installation (AutoCAD, ZWCAD, GstarCAD, CAXA, or ODA)."""

    driver_id: str
    vendor: VendorId
    product_name: str
    version: str
    release_code: str
    root_dir: Path
    primary_exe: Path
    console_exe: Path | None
    fonts_dir: Path | None
    primary_identity: ExecutableIdentity
    console_identity: ExecutableIdentity | None = None
    com_progid: str | None = None
    trusted: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_dir", _abs_path(self.root_dir))
        object.__setattr__(self, "primary_exe", _abs_path(self.primary_exe))
        if self.console_exe is not None:
            object.__setattr__(self, "console_exe", _abs_path(self.console_exe))
        if self.fonts_dir is not None:
            object.__setattr__(self, "fonts_dir", _abs_path(self.fonts_dir))
        if not self.trusted:
            raise ValueError("untrusted executable cannot form a NativeCADInstallation")
        if self.primary_identity.path != self.primary_exe:
            raise ValueError("primary_identity path does not match primary_exe")
        if self.console_exe is not None and self.console_identity is not None:
            if self.console_identity.path != self.console_exe:
                raise ValueError("console_identity path does not match console_exe")

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "vendor": self.vendor,
            "product_name": self.product_name,
            "version": self.version,
            "release_code": self.release_code,
            "root_dir": str(self.root_dir),
            "primary_exe": str(self.primary_exe),
            "console_exe": str(self.console_exe) if self.console_exe else None,
            "fonts_dir": str(self.fonts_dir) if self.fonts_dir and self.fonts_dir.is_dir() else None,
            "com_progid": self.com_progid,
            "publisher": self.primary_identity.signature_publisher,
            "trusted": self.trusted,
        }


@dataclass(frozen=True, slots=True)
class DriverProbeReport:
    """Read-only discovery summary for one CAD vendor driver."""

    driver_id: str
    vendor: VendorId
    display_name: str
    status: DriverStatus
    installations: tuple[NativeCADInstallation, ...] = ()
    stale_registry_paths: tuple[str, ...] = ()
    com_progid: str | None = None
    supports_headless_console: bool = False
    supports_batch_script: bool = False
    supports_com_automation: bool = False
    supports_dwg_roundtrip: bool = False
    supports_3dsolid: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        fonts_dirs = tuple(
            str(inst.fonts_dir)
            for inst in self.installations
            if inst.fonts_dir is not None and inst.fonts_dir.is_dir()
        )
        return {
            "driver_id": self.driver_id,
            "vendor": self.vendor,
            "display_name": self.display_name,
            "status": self.status,
            "installations": [inst.to_summary_dict() for inst in self.installations],
            "stale_registry_paths": list(self.stale_registry_paths),
            "com_progid": self.com_progid,
            "capabilities": {
                "headless_console": self.supports_headless_console,
                "batch_script": self.supports_batch_script,
                "com_automation": self.supports_com_automation,
                "dwg_roundtrip": self.supports_dwg_roundtrip,
                "solid_3d": self.supports_3dsolid,
            },
            "fonts_dirs": list(dict.fromkeys(fonts_dirs)),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DWGRoundTripIssue:
    """Single structural discrepancy detected across a DWG <-> DXF round-trip."""

    code: str
    severity: Literal["error", "warning", "info"]
    category: Literal["audit", "layers", "blocks", "layouts", "styles", "entities", "extents"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DWGRoundTripReport:
    """Complete structural preservation report between baseline and round-tripped DXF."""

    status: Literal["passed", "failed"]
    before_path: str
    after_path: str
    before_summary: dict[str, Any]
    after_summary: dict[str, Any]
    issues: tuple[DWGRoundTripIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        return {
            "schema": "cad-dwg-roundtrip-audit/v1",
            "status": self.status,
            "before_path": self.before_path,
            "after_path": self.after_path,
            "summary": {
                "errors": errors,
                "warnings": warnings,
                "before": self.before_summary,
                "after": self.after_summary,
            },
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "details": i.details,
                }
                for i in self.issues
            ],
        }


@dataclass(frozen=True, slots=True)
class NativeJobRequest:
    """Immutable request for controlled headless native CAD execution."""

    job_kind: NativeJobKind
    output_dir: Path
    task_work_dir: Path | None = None
    input_path: Path | None = None
    script_text: str | None = None
    output_stem: str = "drawing"
    plot_layout: str = "Layout1"
    include_pdf: bool = True
    timeout_seconds: float = 120.0
    driver_preference: Literal["auto", "autocad", "zwcad", "gstarcad", "oda"] = "auto"
    allowed_versions: tuple[str, ...] | None = None
    configured_path: Path | None = None

    def __post_init__(self) -> None:
        if self.job_kind not in {
            "convert-and-plot",
            "dwg-to-dxf",
            "build-3dsolid",
            "probe-native-associativity",
        }:
            raise ValueError(f"unsupported native CAD job_kind: {self.job_kind}")
        _reject_reparse_components(Path(self.output_dir), "output_dir")
        out_dir = _abs_path(self.output_dir)
        work_dir = _abs_path(
            self.task_work_dir if self.task_work_dir is not None else out_dir.parent / "work"
        )
        _reject_reparse_components(work_dir, "task_work_dir")
        object.__setattr__(self, "output_dir", out_dir)
        object.__setattr__(self, "task_work_dir", work_dir)
        if self.input_path is not None:
            object.__setattr__(self, "input_path", _abs_path(self.input_path))
        if self.configured_path is not None:
            object.__setattr__(self, "configured_path", _abs_path(self.configured_path))
        if not _safe_filename_stem(self.output_stem):
            raise ValueError(f"unsafe output_stem: {self.output_stem!r}")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")


@dataclass(frozen=True, slots=True)
class NativeJobResult:
    """Structured outcome from a headless native CAD execution."""

    status: Literal["succeeded", "failed", "skipped"]
    job_kind: NativeJobKind
    driver_id: str | None
    driver_version: str | None
    return_code: int | None
    outputs: tuple[Path, ...]
    script_path: Path | None
    stdout_path: Path | None
    stderr_path: Path | None
    duration_seconds: float
    error_code: str | None = None
    error_message: str | None = None
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "job_kind": self.job_kind,
            "driver_id": self.driver_id,
            "driver_version": self.driver_version,
            "return_code": self.return_code,
            "outputs": [str(p) for p in self.outputs],
            "script_path": str(self.script_path) if self.script_path else None,
            "stdout_path": str(self.stdout_path) if self.stdout_path else None,
            "stderr_path": str(self.stderr_path) if self.stderr_path else None,
            "duration_seconds": round(self.duration_seconds, 4),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "evidence": list(self.evidence),
        }


class NativeCADDriver(Protocol):
    """Unified read-only discovery & command-builder protocol for local CAD vendors."""

    driver_id: str
    vendor: VendorId

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport: ...
