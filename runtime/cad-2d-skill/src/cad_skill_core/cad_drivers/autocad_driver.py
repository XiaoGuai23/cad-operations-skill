"""Multi-version AutoCAD (2021-2026, R24.0-R25.1) bounded, Authenticode-verified driver."""

from __future__ import annotations

import os
from pathlib import Path
import re

from cad_skill_core.autocad import discovery as _legacy_discovery
from cad_skill_core.autocad.models import (
    AutoCADErrorDetail,
    AutoCADInaccessibleError,
    AutoCADNotInstalledError,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
)

from .models import CADReleaseSpec, DriverProbeReport, NativeCADInstallation

AUTOCAD_RELEASE_MATRIX: tuple[CADReleaseSpec, ...] = (
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2026",
        release_code="R25.1",
        version_pattern=r"^R?25\.1(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R25.1", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R25.1"),
    ),
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2025",
        release_code="R25.0",
        version_pattern=r"^R?25\.0(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R25.0", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R25.0"),
    ),
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2024",
        release_code="R24.3",
        version_pattern=r"^R?24\.3(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R24.3", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.3"),
    ),
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2023",
        release_code="R24.2",
        version_pattern=r"^R?24\.2(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R24.2", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.2"),
    ),
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2022",
        release_code="R24.1",
        version_pattern=r"^R?24\.1(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R24.1", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.1"),
    ),
    CADReleaseSpec(
        vendor="autodesk",
        product_family="AutoCAD",
        version="2021",
        release_code="R24.0",
        version_pattern=r"^R?24\.0(?:\.|$)",
        registry_subkeys=(r"SOFTWARE\Autodesk\AutoCAD\R24.0", r"SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.0"),
    ),
)

_RELEASE_BY_VERSION = {spec.version: spec for spec in AUTOCAD_RELEASE_MATRIX}


def _installation_root(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.name.casefold() in {"acad.exe", "accoreconsole.exe"}:
        return expanded.parent.resolve(strict=False)
    return expanded.resolve(strict=False)


def _match_autocad_release(core_ver: str, acad_ver: str) -> CADReleaseSpec | None:
    for spec in AUTOCAD_RELEASE_MATRIX:
        rx = re.compile(spec.version_pattern, re.IGNORECASE)
        if rx.match(core_ver.strip()) and rx.match(acad_ver.strip()):
            return spec
    return None


def _registry_candidates_for_releases(
    specs: tuple[CADReleaseSpec, ...]
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    try:
        import winreg
    except ImportError:
        return (), ()

    valid_roots: list[Path] = []
    stale_roots: list[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for spec in specs:
            for base_name in spec.registry_subkeys:
                try:
                    with winreg.OpenKey(hive, base_name) as base:
                        idx = 0
                        while True:
                            try:
                                child_name = winreg.EnumKey(base, idx)
                            except OSError:
                                break
                            idx += 1
                            for value_name in ("InstallLocation", "AcadLocation"):
                                try:
                                    with winreg.OpenKey(base, child_name) as child:
                                        val, _ = winreg.QueryValueEx(child, value_name)
                                except OSError:
                                    continue
                                if val:
                                    p = Path(str(val)).expanduser().resolve(strict=False)
                                    if p.is_dir():
                                        valid_roots.append(p)
                                    else:
                                        stale_roots.append(str(p))
                                    break
                except OSError:
                    continue
    return _legacy_discovery._deduplicate_paths(valid_roots), tuple(dict.fromkeys(stale_roots))


def _bounded_standard_roots(specs: tuple[CADReleaseSpec, ...]) -> tuple[Path, ...]:
    program_bases = [Path(r"C:\Program Files\Autodesk")]
    pf_env = os.environ.get("ProgramFiles")
    if pf_env:
        program_bases.append(Path(pf_env) / "Autodesk")
    d_cad = Path(r"D:\CAD")

    candidates: list[Path] = []
    for spec in specs:
        yr = spec.version
        for base in program_bases:
            cand = base / f"AutoCAD {yr}"
            if cand.is_dir():
                candidates.append(cand)
        for custom in (d_cad / f"CAD{yr}" / f"AutoCAD {yr}", d_cad / f"AutoCAD {yr}"):
            if custom.is_dir():
                candidates.append(custom)
    return _legacy_discovery._deduplicate_paths(candidates)


def _validate_multiversion_candidate(
    path: Path,
    *,
    supplied: bool,
    allowed_versions: set[str] | None,
) -> NativeCADInstallation:
    supplied_path = Path(path).expanduser().resolve(strict=False)
    root = _installation_root(path)
    core_console = root / "accoreconsole.exe"
    acad = root / "acad.exe"
    try:
        _legacy_discovery._require_readable_file(core_console)
        _legacy_discovery._require_readable_file(acad)
        core_product, core_version = _legacy_discovery._read_version_metadata(core_console)
        acad_product, acad_version = _legacy_discovery._read_version_metadata(acad)
    except (OSError, PermissionError) as error:
        error_path = supplied_path if supplied else root
        detail = str(error).strip() or type(error).__name__
        raise AutoCADInaccessibleError(
            AutoCADErrorDetail(
                "inaccessible",
                f"AutoCAD candidate is missing or inaccessible at {error_path}: {detail}",
                error_path,
            )
        ) from error

    products_ok = (
        core_product.casefold() == "accoreconsole"
        and acad_product.casefold() == "autocad"
    )
    matched_spec = _match_autocad_release(core_version, acad_version) if products_ok else None
    if matched_spec is None or (allowed_versions is not None and matched_spec.version not in allowed_versions):
        discovered = f"{core_product} {core_version}; {acad_product} {acad_version}"
        error_path = supplied_path if supplied else root
        allowed_desc = sorted(allowed_versions) if allowed_versions else [s.version for s in AUTOCAD_RELEASE_MATRIX]
        raise AutoCADWrongVersionError(
            AutoCADErrorDetail(
                "wrong-version",
                f"candidate is not in allowed AutoCAD versions {allowed_desc}: {discovered}",
                error_path,
                discovered,
            )
        )

    core_identity = _legacy_discovery._trusted_identity(core_console)
    acad_identity = _legacy_discovery._trusted_identity(acad)
    release_major_minor = matched_spec.release_code.lstrip("Rr")
    return NativeCADInstallation(
        driver_id="autocad",
        vendor="autodesk",
        product_name=f"AutoCAD {matched_spec.version}",
        version=matched_spec.version,
        release_code=matched_spec.release_code,
        root_dir=root,
        primary_exe=acad,
        console_exe=core_console,
        fonts_dir=root / "Fonts",
        primary_identity=acad_identity,
        console_identity=core_identity,
        com_progid=f"AutoCAD.Application.{release_major_minor}",
        trusted=True,
    )


def discover_autocad_installations(
    *,
    configured_path: Path | None = None,
    allowed_versions: tuple[str, ...] | None = None,
) -> tuple[tuple[NativeCADInstallation, ...], tuple[str, ...]]:
    """Discover all trusted AutoCAD installations across 2021-2026 (or allowed_versions)."""

    allowed_set: set[str] | None = None
    if allowed_versions is not None:
        unknown = [v for v in allowed_versions if v not in _RELEASE_BY_VERSION]
        if unknown:
            raise ValueError(f"unsupported AutoCAD versions requested: {unknown}")
        allowed_set = set(allowed_versions)

    target_specs = tuple(
        s for s in AUTOCAD_RELEASE_MATRIX if allowed_set is None or s.version in allowed_set
    )

    if configured_path is not None:
        inst = _validate_multiversion_candidate(
            configured_path, supplied=True, allowed_versions=allowed_set
        )
        return (inst,), ()

    found: list[NativeCADInstallation] = []
    seen_roots: set[str] = set()
    failures: list[Exception] = []

    for env_name in ("CAD_INSTALL_ROOT", "ACCORECONSOLE_EXE", "AUTOCAD_EXE"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            inst = _validate_multiversion_candidate(
                Path(raw), supplied=True, allowed_versions=allowed_set
            )
            key = str(inst.root_dir).casefold()
            if key not in seen_roots:
                seen_roots.add(key)
                found.append(inst)
        except (AutoCADInaccessibleError, AutoCADWrongVersionError, AutoCADUntrustedError) as err:
            failures.append(err)

    reg_roots, stale_paths = _registry_candidates_for_releases(target_specs)
    std_roots = _bounded_standard_roots(target_specs)

    for cand in _legacy_discovery._deduplicate_paths((*reg_roots, *std_roots)):
        key = str(cand).casefold()
        if key in seen_roots:
            continue
        try:
            inst = _validate_multiversion_candidate(
                cand, supplied=False, allowed_versions=allowed_set
            )
            seen_roots.add(str(inst.root_dir).casefold())
            found.append(inst)
        except (AutoCADInaccessibleError, AutoCADWrongVersionError, AutoCADUntrustedError) as err:
            failures.append(err)

    if found:
        version_rank = {s.version: idx for idx, s in enumerate(AUTOCAD_RELEASE_MATRIX)}
        found.sort(key=lambda item: version_rank.get(item.version, 99))
        return tuple(found), stale_paths

    for exc_type in (AutoCADUntrustedError, AutoCADWrongVersionError, AutoCADInaccessibleError):
        matched_err = next((e for e in failures if isinstance(e, exc_type)), None)
        if matched_err is not None:
            if isinstance(matched_err, AutoCADInaccessibleError):
                has_env = any(
                    os.environ.get(k) for k in ("CAD_INSTALL_ROOT", "ACCORECONSOLE_EXE", "AUTOCAD_EXE")
                )
                exists_on_disk = matched_err.detail.path is not None and matched_err.detail.path.exists()
                if not (has_env or exists_on_disk):
                    continue
            raise matched_err

    raise AutoCADNotInstalledError(
        AutoCADErrorDetail(
            "not-installed",
            "No trusted AutoCAD (2021-2026) installation was found in bounded install locations.",
        )
    )


class AutoCADMultiVersionDriver:
    """NativeCADDriver implementation for AutoCAD 2021-2026."""

    driver_id = "autocad"
    vendor = "autodesk"  # type: ignore[assignment]

    def __init__(self, *, allowed_versions: tuple[str, ...] | None = None) -> None:
        self.allowed_versions = allowed_versions

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport:
        try:
            installs, stale = discover_autocad_installations(
                configured_path=configured_path,
                allowed_versions=self.allowed_versions,
            )
            primary = installs[0]
            return DriverProbeReport(
                driver_id="autocad",
                vendor="autodesk",
                display_name="Autodesk AutoCAD (2021–2026)",
                status="available",
                installations=installs,
                stale_registry_paths=stale,
                com_progid=primary.com_progid,
                supports_headless_console=True,
                supports_batch_script=True,
                supports_com_automation=True,
                supports_dwg_roundtrip=True,
                supports_3dsolid=True,
                reason=f"Found {len(installs)} signature-verified AutoCAD installation(s); preferred={primary.version} ({primary.release_code}).",
            )
        except AutoCADNotInstalledError as err:
            _, stale = _registry_candidates_for_releases(AUTOCAD_RELEASE_MATRIX)
            return DriverProbeReport(
                driver_id="autocad",
                vendor="autodesk",
                display_name="Autodesk AutoCAD (2021–2026)",
                status="not-installed",
                stale_registry_paths=stale,
                com_progid="AutoCAD.Application",
                reason=err.detail.message,
            )
        except AutoCADUntrustedError as err:
            return DriverProbeReport(
                driver_id="autocad",
                vendor="autodesk",
                display_name="Autodesk AutoCAD (2021–2026)",
                status="untrusted",
                reason=err.detail.message,
            )
        except AutoCADWrongVersionError as err:
            return DriverProbeReport(
                driver_id="autocad",
                vendor="autodesk",
                display_name="Autodesk AutoCAD (2021–2026)",
                status="wrong-version",
                reason=err.detail.message,
            )
        except AutoCADInaccessibleError as err:
            return DriverProbeReport(
                driver_id="autocad",
                vendor="autodesk",
                display_name="Autodesk AutoCAD (2021–2026)",
                status="inaccessible",
                reason=err.detail.message,
            )
