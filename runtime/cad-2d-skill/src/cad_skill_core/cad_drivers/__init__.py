"""Multi-version (AutoCAD 2021-2026) and multi-vendor (ZWCAD/GstarCAD/CAXA/ODA) CAD drivers."""

from .autocad_driver import (
    AUTOCAD_RELEASE_MATRIX,
    AutoCADMultiVersionDriver,
    discover_autocad_installations,
)
from .models import (
    CADReleaseSpec,
    DWGRoundTripIssue,
    DWGRoundTripReport,
    DriverProbeReport,
    NativeCADDriver,
    NativeCADInstallation,
    NativeJobKind,
    NativeJobRequest,
    NativeJobResult,
)
from .native_ops import execute_native_cad_job, validate_3dsolid_script_allowlist
from .registry import (
    discover_installed_cad_font_roots,
    probe_all_cad_drivers,
    select_preferred_installation,
)
from .roundtrip import verify_dxf_structural_equivalence
from .vendors import (
    CAXADriver,
    DomesticCOMSession,
    GstarCADDriver,
    ODAConverterDriver,
    ZWCADDriver,
    build_console_command,
    build_oda_command,
)

__all__ = [
    "AUTOCAD_RELEASE_MATRIX",
    "AutoCADMultiVersionDriver",
    "CAXADriver",
    "CADReleaseSpec",
    "DWGRoundTripIssue",
    "DWGRoundTripReport",
    "DomesticCOMSession",
    "DriverProbeReport",
    "GstarCADDriver",
    "NativeCADDriver",
    "NativeCADInstallation",
    "NativeJobKind",
    "NativeJobRequest",
    "NativeJobResult",
    "ODAConverterDriver",
    "ZWCADDriver",
    "build_console_command",
    "build_oda_command",
    "discover_autocad_installations",
    "discover_installed_cad_font_roots",
    "execute_native_cad_job",
    "probe_all_cad_drivers",
    "select_preferred_installation",
    "validate_3dsolid_script_allowlist",
    "verify_dxf_structural_equivalence",
]
