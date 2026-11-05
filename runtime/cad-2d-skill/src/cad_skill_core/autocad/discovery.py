"""Bounded, exact AutoCAD 2023 installation discovery."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .models import (
    AutoCADErrorDetail,
    AutoCADInaccessibleError,
    AutoCADInstallation,
    AutoCADNotInstalledError,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
    ExecutableIdentity,
    SignatureEvidence,
)
from .system_tools import trusted_system_directory as _trusted_system_directory


_AUTOCAD_2023_RELEASE = re.compile(r"^R?24\.2(?:\.|$)", re.IGNORECASE)


def discover_autocad(configured_path: Path | None = None) -> AutoCADInstallation:
    """Find an exact AutoCAD 2023 installation without recursive drive scans."""

    if configured_path is not None:
        return _validate_candidate(Path(configured_path), supplied=True)

    failures: list[AutoCADInaccessibleError | AutoCADWrongVersionError | AutoCADUntrustedError] = []
    for variable in ("ACCORECONSOLE_EXE", "AUTOCAD_EXE"):
        value = os.environ.get(variable)
        if value:
            try:
                return _validate_candidate(Path(value), supplied=True)
            except (
                AutoCADInaccessibleError,
                AutoCADWrongVersionError,
                AutoCADUntrustedError,
            ) as error:
                failures.append(error)

    fixed = _fixed_install_root()
    candidates = (
        (() if fixed is None else (fixed,))
        + tuple(_registry_install_roots())
        + tuple(_program_files_install_roots())
    )
    for candidate in _deduplicate_paths(candidates):
        try:
            return _validate_candidate(candidate, supplied=False)
        except (
            AutoCADInaccessibleError,
            AutoCADWrongVersionError,
            AutoCADUntrustedError,
        ) as error:
            failures.append(error)

    untrusted = next(
        (item for item in failures if isinstance(item, AutoCADUntrustedError)), None
    )
    if untrusted is not None:
        raise untrusted
    wrong = next((item for item in failures if isinstance(item, AutoCADWrongVersionError)), None)
    if wrong is not None:
        raise wrong
    inaccessible = next(
        (item for item in failures if isinstance(item, AutoCADInaccessibleError)), None
    )
    if inaccessible is not None:
        configured = any(
            os.environ.get(name) for name in ("ACCORECONSOLE_EXE", "AUTOCAD_EXE")
        )
        existing_candidate = (
            inaccessible.detail.path is not None and inaccessible.detail.path.exists()
        )
        if configured or existing_candidate:
            raise inaccessible
    raise AutoCADNotInstalledError(
        AutoCADErrorDetail(
            "not-installed",
            "AutoCAD 2023 Core Console was not found in configured or bounded install locations",
        )
    )


def _validate_candidate(path: Path, *, supplied: bool) -> AutoCADInstallation:
    supplied_path = path.expanduser().resolve(strict=False)
    root = _installation_root(path)
    core_console = root / "accoreconsole.exe"
    acad = root / "acad.exe"
    try:
        _require_readable_file(core_console)
        _require_readable_file(acad)
        core_product, core_version = _read_version_metadata(core_console)
        acad_product, acad_version = _read_version_metadata(acad)
    except (OSError, PermissionError) as error:
        error_path = supplied_path if supplied else root.resolve(strict=False)
        detail = str(error).strip() or type(error).__name__
        raise AutoCADInaccessibleError(
            AutoCADErrorDetail(
                "inaccessible",
                f"AutoCAD candidate is missing or inaccessible at {error_path}: {detail}",
                error_path,
            )
        ) from error

    products_are_exact = (
        core_product.casefold() == "accoreconsole"
        and acad_product.casefold() == "autocad"
    )
    versions_are_2023 = all(
        _AUTOCAD_2023_RELEASE.match(version.strip())
        for version in (core_version, acad_version)
    )
    if not products_are_exact or not versions_are_2023:
        discovered = f"{core_product} {core_version}; {acad_product} {acad_version}"
        error_path = supplied_path if supplied else root.resolve(strict=False)
        raise AutoCADWrongVersionError(
            AutoCADErrorDetail(
                "wrong-version",
                f"candidate is not exact AutoCAD 2023 (R24.2): {discovered}",
                error_path,
                discovered,
            )
        )

    try:
        core_identity = _trusted_identity(core_console)
        acad_identity = _trusted_identity(acad)
    except AutoCADUntrustedError:
        raise
    except OSError as error:
        target = core_console if "core" in str(error).casefold() else root
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "untrusted-executable",
                f"Authenticode verification unavailable or unstable: {error}",
                target,
            )
        ) from error

    return AutoCADInstallation(
        acad,
        core_console,
        "2023",
        root / "Fonts",
        acad_identity,
        core_identity,
        True,
    )


def _installation_root(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.name.casefold() in {"acad.exe", "accoreconsole.exe"}:
        return expanded.parent.resolve(strict=False)
    return expanded.resolve(strict=False)


def _require_readable_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as stream:
        stream.read(1)


def _fixed_install_root() -> Path:
    return Path(r"D:\CAD\CAD2023\AutoCAD 2023")


def _program_files_install_roots() -> tuple[Path, ...]:
    roots = [Path(r"C:\Program Files\Autodesk\AutoCAD 2023")]
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        roots.append(Path(program_files) / "Autodesk" / "AutoCAD 2023")
    return _deduplicate_paths(roots)


def _registry_install_roots() -> tuple[Path, ...]:
    """Read only the documented R24.2 registry level and its direct products."""

    try:
        import winreg
    except ImportError:
        return ()

    roots: list[Path] = []
    base_names = (
        r"SOFTWARE\Autodesk\AutoCAD\R24.2",
        r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.2",
    )
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base_name in base_names:
            try:
                with winreg.OpenKey(hive, base_name) as base:
                    index = 0
                    while True:
                        try:
                            child_name = winreg.EnumKey(base, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(base, child_name) as child:
                                value, _ = winreg.QueryValueEx(child, "InstallLocation")
                        except OSError:
                            continue
                        if value:
                            roots.append(Path(value))
            except OSError:
                continue
    return _deduplicate_paths(roots)


def _deduplicate_paths(paths) -> tuple[Path, ...]:
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = Path(path).expanduser().resolve(strict=False)
        unique.setdefault(str(resolved).casefold(), resolved)
    return tuple(unique.values())


def _read_version_metadata(path: Path) -> tuple[str, str]:
    """Read ProductName/ProductVersion from the Windows executable resource."""

    if os.name != "nt":
        raise OSError("Windows version resources are unavailable")
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise OSError(f"no Windows version resource: {path}")
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise OSError(f"could not read Windows version resource: {path}")

    translations = _query_version_value(version, buffer, r"\VarFileInfo\Translation")
    if translations is None:
        language, codepage = 0x0409, 0x04B0
    else:
        pointer, length = translations
        if length < 4:
            raise OSError(f"invalid Windows version translation: {path}")
        values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))
        language, codepage = int(values[0]), int(values[1])
    prefix = f"\\StringFileInfo\\{language:04x}{codepage:04x}"
    product = _query_string(version, buffer, prefix + r"\ProductName")
    product_version = _query_string(version, buffer, prefix + r"\ProductVersion")
    if not product or not product_version:
        raise OSError(f"incomplete Windows version resource: {path}")
    return product.strip(), product_version.strip()


def _query_version_value(version, buffer, name: str):
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not version.VerQueryValueW(buffer, name, ctypes.byref(pointer), ctypes.byref(length)):
        return None
    return pointer, length.value


def _query_string(version, buffer, name: str) -> str:
    result = _query_version_value(version, buffer, name)
    if result is None:
        return ""
    pointer, _ = result
    return ctypes.wstring_at(pointer)


def _trusted_identity(path: Path) -> ExecutableIdentity:
    before = path.stat()
    try:
        signature = _verify_authenticode(path)
    except OSError as error:
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "untrusted-executable",
                f"signature verification unavailable for {path}: {error}",
                path,
            )
        ) from error
    if signature.status.casefold() != "valid" or not _is_autodesk_publisher(
        signature.publisher
    ):
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "untrusted-executable",
                f"invalid Autodesk Authenticode signature for {path}: "
                f"status={signature.status}, publisher={signature.publisher}",
                path,
            )
        )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    before_key = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_key != after_key:
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "untrusted-executable",
                f"executable identity changed during signature verification: {path}",
                path,
            )
        )
    return ExecutableIdentity(
        path,
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
        digest.hexdigest(),
        signature.publisher,
        signature.thumbprint,
    )


def _is_autodesk_publisher(publisher: str) -> bool:
    return bool(
        re.search(
            r'(?:CN|O)="?Autodesk, Inc\."?(?:,|$)', publisher, re.IGNORECASE
        )
    )


def _verify_authenticode(path: Path) -> SignatureEvidence:
    """Fail-closed Authenticode probe behind an injectable subprocess boundary."""

    system_directory = _trusted_system_directory()
    system_root = system_directory.parent
    powershell = (
        system_directory
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell.is_file():
        raise OSError(f"trusted PowerShell verifier is unavailable: {powershell}")
    command = (
        "$s=Get-AuthenticodeSignature -LiteralPath $env:CAD_SKILL_SIGNATURE_TARGET;"
        "[PSCustomObject]@{Status=$s.Status.ToString();"
        "Publisher=$s.SignerCertificate.Subject;"
        "Thumbprint=$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"
    )
    environment = {
        "SystemRoot": str(system_root),
        "WINDIR": str(system_root),
        "PATH": str(system_directory),
        "CAD_SKILL_SIGNATURE_TARGET": str(path),
    }
    completed = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise OSError(
            f"Authenticode verifier exited {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
        return SignatureEvidence(
            str(payload.get("Status") or "Unavailable"),
            str(payload.get("Publisher") or "<no signer>"),
            str(payload.get("Thumbprint") or ""),
        )
    except (ValueError, AttributeError) as error:
        raise OSError(f"invalid Authenticode verifier output: {error}") from error


def revalidate_installation(installation: AutoCADInstallation) -> None:
    """Recheck product, signature, and immutable identity before every launch."""

    core_product, core_version = _read_version_metadata(installation.core_console_exe)
    acad_product, acad_version = _read_version_metadata(installation.acad_exe)
    metadata_valid = (
        core_product.casefold() == "accoreconsole"
        and acad_product.casefold() == "autocad"
        and _AUTOCAD_2023_RELEASE.match(core_version.strip()) is not None
        and _AUTOCAD_2023_RELEASE.match(acad_version.strip()) is not None
    )
    if not metadata_valid:
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "identity-drift",
                "AutoCAD executable product/version changed after discovery",
                installation.core_console_exe,
            )
        )
    current_core = _trusted_identity(installation.core_console_exe)
    current_acad = _trusted_identity(installation.acad_exe)
    if current_core != installation.core_console_identity:
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "identity-drift",
                "Core Console executable identity changed after discovery",
                installation.core_console_exe,
            )
        )
    if current_acad != installation.acad_identity:
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "identity-drift",
                "AutoCAD executable identity changed after discovery",
                installation.acad_exe,
            )
        )
