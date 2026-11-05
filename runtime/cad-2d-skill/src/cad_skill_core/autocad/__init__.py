"""Headless AutoCAD 2023 Core Console boundary."""

from .discovery import discover_autocad
from .executor import build_core_console_command, execute_autocad
from .models import (
    AutoCADErrorDetail,
    AutoCADExecutionError,
    AutoCADInaccessibleError,
    AutoCADInstallation,
    AutoCADNotInstalledError,
    AutoCADRequest,
    AutoCADResult,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
    ExecutableIdentity,
    SignatureEvidence,
)

__all__ = [
    "AutoCADErrorDetail",
    "AutoCADExecutionError",
    "AutoCADInaccessibleError",
    "AutoCADInstallation",
    "AutoCADNotInstalledError",
    "AutoCADRequest",
    "AutoCADResult",
    "AutoCADUntrustedError",
    "AutoCADWrongVersionError",
    "ExecutableIdentity",
    "SignatureEvidence",
    "build_core_console_command",
    "discover_autocad",
    "execute_autocad",
]
