"""Domestic CAD (ZWCAD, GstarCAD, CAXA) & ODA File Converter bounded drivers and COM bridge."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any

from cad_skill_core.autocad import discovery as _sig_tools
from cad_skill_core.autocad.models import (
    AutoCADErrorDetail,
    AutoCADUntrustedError,
    ExecutableIdentity,
)

from .models import DriverProbeReport, NativeCADInstallation, VendorId

_PUBLISHER_PATTERNS: dict[VendorId, re.Pattern[str]] = {
    "zwsoft": re.compile(
        r'(?:CN|O)="?[^",]*(?:ZWSOFT|ZWCAD|广州中望龙腾软件|中望龙腾|中望软件)[^",]*(?:"|,|$)',
        re.IGNORECASE,
    ),
    "gstarsoft": re.compile(
        r'(?:CN|O)="?[^",]*(?:Gstarsoft|GstarCAD|苏州浩辰软件|浩辰软件)[^",]*(?:"|,|$)',
        re.IGNORECASE,
    ),
    "caxa": re.compile(
        r'(?:CN|O)="?[^",]*(?:CAXA|数码大方|北京数码大方)[^",]*(?:"|,|$)',
        re.IGNORECASE,
    ),
    "oda": re.compile(
        r'(?:CN|O)="?[^",]*(?:Open Design Alliance)[^",]*(?:"|,|$)',
        re.IGNORECASE,
    ),
}


def _trusted_vendor_identity(path: Path, vendor: VendorId) -> ExecutableIdentity:
    """Verify Authenticode signature against expected vendor certificate & hash binary."""

    before = path.stat()
    signature = _sig_tools._verify_authenticode(path)
    pattern = _PUBLISHER_PATTERNS[vendor]
    if signature.status.casefold() != "valid" or not pattern.search(signature.publisher):
        raise AutoCADUntrustedError(
            AutoCADErrorDetail(
                "untrusted-executable",
                f"invalid {vendor} Authenticode signature for {path}: "
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
                f"executable identity changed during verification: {path}",
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


def _program_files_roots() -> tuple[Path, ...]:
    candidates = [
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
        Path(r"D:\Program Files"),
        Path(r"D:\Program Files (x86)"),
        Path(r"D:\CAD"),
    ]
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        val = os.environ.get(env_key)
        if val:
            candidates.append(Path(val))
    return _sig_tools._deduplicate_paths([p for p in candidates if p.is_dir()])


def _scan_registry_two_levels(
    base_subkeys: tuple[str, ...],
    value_names: tuple[str, ...],
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Bounded 2-level registry reader (e.g. R23 -> zh-cn -> LOCATION) with stale-entry tracking."""

    try:
        import winreg
    except ImportError:
        return (), ()

    valid: list[Path] = []
    stale: list[str] = []

    def _check_key(key_handle) -> None:
        for val_name in value_names:
            try:
                raw_val, _ = winreg.QueryValueEx(key_handle, val_name)
            except OSError:
                continue
            if raw_val:
                resolved = Path(str(raw_val)).expanduser().resolve(strict=False)
                if resolved.is_dir():
                    valid.append(resolved)
                else:
                    stale.append(str(resolved))
                return

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base_subkey in base_subkeys:
            try:
                with winreg.OpenKey(hive, base_subkey) as base:
                    _check_key(base)
                    i = 0
                    while i < 32:
                        try:
                            lvl1_name = winreg.EnumKey(base, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(base, lvl1_name) as lvl1:
                                _check_key(lvl1)
                                j = 0
                                while j < 16:
                                    try:
                                        lvl2_name = winreg.EnumKey(lvl1, j)
                                    except OSError:
                                        break
                                    j += 1
                                    try:
                                        with winreg.OpenKey(lvl1, lvl2_name) as lvl2:
                                            _check_key(lvl2)
                                    except OSError:
                                        continue
                        except OSError:
                            continue
            except OSError:
                continue

    return _sig_tools._deduplicate_paths(valid), tuple(dict.fromkeys(stale))


def _bounded_vendor_subdirs(
    vendor_folders: tuple[str, ...],
    product_prefixes: tuple[str, ...],
) -> tuple[Path, ...]:
    """Check immediate child folders under known vendor directories (zero recursive scan)."""

    matches: list[Path] = []
    for pf in _program_files_roots():
        for v_folder in vendor_folders:
            v_dir = pf / v_folder
            if not v_dir.is_dir():
                continue
            try:
                children = list(v_dir.iterdir())
            except OSError:
                continue
            for child in children[:32]:
                if child.is_dir() and any(
                    child.name.casefold().startswith(pref.casefold())
                    for pref in product_prefixes
                ):
                    matches.append(child)
    return _sig_tools._deduplicate_paths(matches)


def _infer_version_label(root: Path, exe_path: Path) -> tuple[str, str]:
    try:
        _, prod_ver = _sig_tools._read_version_metadata(exe_path)
    except OSError:
        prod_ver = ""
    year_match = re.search(r"(202\d)", root.name)
    if year_match:
        return year_match.group(1), prod_ver or year_match.group(1)
    if prod_ver:
        return prod_ver.split(".")[0], prod_ver
    return "unknown", "unknown"


def _probe_generic_vendor(
    *,
    driver_id: str,
    vendor: VendorId,
    display_name: str,
    primary_exe_names: tuple[str, ...],
    console_exe_names: tuple[str, ...],
    env_vars: tuple[str, ...],
    registry_subkeys: tuple[str, ...],
    registry_values: tuple[str, ...],
    vendor_folders: tuple[str, ...],
    product_prefixes: tuple[str, ...],
    com_progid: str | None,
    supports_batch_script: bool,
    supports_dwg_roundtrip: bool,
    supports_3dsolid: bool,
    configured_path: Path | None = None,
) -> DriverProbeReport:
    explicit_candidates: list[Path] = []
    if configured_path is not None:
        explicit_candidates.append(Path(configured_path).expanduser().resolve(strict=False))
    else:
        for env_key in env_vars:
            val = os.environ.get(env_key)
            if val:
                explicit_candidates.append(Path(val).expanduser().resolve(strict=False))

    reg_roots, stale_paths = _scan_registry_two_levels(registry_subkeys, registry_values)
    dir_roots = _bounded_vendor_subdirs(vendor_folders, product_prefixes)

    all_candidates: list[tuple[Path, bool]] = [
        *((p, True) for p in explicit_candidates),
        *(
            (p, False)
            for p in (*reg_roots, *dir_roots)
            if configured_path is None
        ),
    ]

    installations: list[NativeCADInstallation] = []
    seen_roots: set[str] = set()
    first_untrusted: str | None = None
    first_inaccessible: str | None = None

    for raw_cand, is_explicit in all_candidates:
        root = (
            raw_cand.parent
            if raw_cand.suffix.casefold() == ".exe"
            else raw_cand
        )
        key = str(root).casefold()
        if key in seen_roots:
            continue

        if not root.is_dir():
            if is_explicit:
                first_inaccessible = first_inaccessible or f"{display_name} path does not exist: {raw_cand}"
            continue

        primary_exe = next(
            (root / name for name in primary_exe_names if (root / name).is_file()),
            None,
        )
        if primary_exe is None:
            if is_explicit:
                first_inaccessible = (
                    first_inaccessible
                    or f"{display_name} primary executable {primary_exe_names} missing in {root}"
                )
            continue

        console_exe = next(
            (root / name for name in console_exe_names if (root / name).is_file()),
            None,
        )
        try:
            primary_id = _trusted_vendor_identity(primary_exe, vendor)
            console_id = (
                _trusted_vendor_identity(console_exe, vendor)
                if console_exe is not None
                else None
            )
        except AutoCADUntrustedError as err:
            first_untrusted = first_untrusted or err.detail.message
            continue
        except OSError as err:
            first_inaccessible = first_inaccessible or f"could not read {primary_exe}: {err}"
            continue

        ver_label, rel_code = _infer_version_label(root, primary_exe)
        fonts_dir = root / "Fonts" if (root / "Fonts").is_dir() else None
        seen_roots.add(key)
        installations.append(
            NativeCADInstallation(
                driver_id=driver_id,
                vendor=vendor,
                product_name=f"{display_name.split(' (')[0]} {ver_label}".strip(),
                version=ver_label,
                release_code=rel_code,
                root_dir=root,
                primary_exe=primary_exe,
                console_exe=console_exe,
                fonts_dir=fonts_dir,
                primary_identity=primary_id,
                console_identity=console_id,
                com_progid=com_progid,
                trusted=True,
            )
        )

    if installations:
        has_console = any(inst.console_exe is not None for inst in installations)
        return DriverProbeReport(
            driver_id=driver_id,
            vendor=vendor,
            display_name=display_name,
            status="available",
            installations=tuple(installations),
            stale_registry_paths=stale_paths,
            com_progid=com_progid,
            supports_headless_console=has_console,
            supports_batch_script=supports_batch_script,
            supports_com_automation=com_progid is not None,
            supports_dwg_roundtrip=supports_dwg_roundtrip,
            supports_3dsolid=supports_3dsolid,
            reason=f"Found {len(installations)} trusted {display_name} installation(s).",
        )

    if first_untrusted is not None:
        return DriverProbeReport(
            driver_id=driver_id,
            vendor=vendor,
            display_name=display_name,
            status="untrusted",
            stale_registry_paths=stale_paths,
            com_progid=com_progid,
            reason=first_untrusted,
        )
    if first_inaccessible is not None:
        return DriverProbeReport(
            driver_id=driver_id,
            vendor=vendor,
            display_name=display_name,
            status="inaccessible",
            stale_registry_paths=stale_paths,
            com_progid=com_progid,
            reason=first_inaccessible,
        )

    stale_note = (
        f" (ignored {len(stale_paths)} uninstalled registry leftover(s))"
        if stale_paths
        else ""
    )
    return DriverProbeReport(
        driver_id=driver_id,
        vendor=vendor,
        display_name=display_name,
        status="not-installed",
        stale_registry_paths=stale_paths,
        com_progid=com_progid,
        reason=f"{display_name} was not found in bounded install locations{stale_note}.",
    )


class ZWCADDriver:
    """Domestic CAD driver for ZWSOFT ZWCAD (中望 CAD 2020–2026, ZWCoConsole & COM)."""

    driver_id = "zwcad"
    vendor: VendorId = "zwsoft"

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport:
        return _probe_generic_vendor(
            driver_id="zwcad",
            vendor="zwsoft",
            display_name="中望 CAD (ZWCAD)",
            primary_exe_names=("ZWCAD.exe",),
            console_exe_names=("ZWCoConsole.exe",),
            env_vars=("ZWCOCONSOLE_EXE", "ZWCAD_EXE", "ZWCAD_ROOT"),
            registry_subkeys=(
                r"SOFTWARE\ZWSOFT\ZWCAD",
                r"SOFTWARE\WOW6432Node\ZWSOFT\ZWCAD",
            ),
            registry_values=("Location", "InstallLocation", "Path"),
            vendor_folders=("ZWSOFT", "中望软件"),
            product_prefixes=("ZWCAD", "中望CAD"),
            com_progid="ZWCAD.Application",
            supports_batch_script=True,
            supports_dwg_roundtrip=True,
            supports_3dsolid=True,
            configured_path=configured_path,
        )


class GstarCADDriver:
    """Domestic CAD driver for Gstarsoft GstarCAD (浩辰 CAD 2020–2026)."""

    driver_id = "gstarcad"
    vendor: VendorId = "gstarsoft"

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport:
        return _probe_generic_vendor(
            driver_id="gstarcad",
            vendor="gstarsoft",
            display_name="浩辰 CAD (GstarCAD)",
            primary_exe_names=("gcad.exe",),
            console_exe_names=("GcadCoreConsole.exe", "gcadconsole.exe"),
            env_vars=("GSTARCAD_CONSOLE_EXE", "GSTARCAD_EXE", "GSTARCAD_ROOT"),
            registry_subkeys=(
                r"SOFTWARE\Gstarsoft\GstarCAD",
                r"SOFTWARE\WOW6432Node\Gstarsoft\GstarCAD",
            ),
            registry_values=("LOCATION", "Location", "InstallLocation"),
            vendor_folders=("浩辰软件", "Gstarsoft"),
            product_prefixes=("浩辰CAD", "GstarCAD"),
            com_progid="GstarCAD.Application",
            supports_batch_script=True,
            supports_dwg_roundtrip=True,
            supports_3dsolid=True,
            configured_path=configured_path,
        )


class CAXADriver:
    """Domestic CAD driver for CAXA Electronic Board (CAXA 电子图板)."""

    driver_id = "caxa"
    vendor: VendorId = "caxa"

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport:
        return _probe_generic_vendor(
            driver_id="caxa",
            vendor="caxa",
            display_name="CAXA CAD 电子图板",
            primary_exe_names=("CxDraft.exe", "EB.exe"),
            console_exe_names=(),
            env_vars=("CAXA_EXE", "CAXA_ROOT"),
            registry_subkeys=(
                r"SOFTWARE\CAXA",
                r"SOFTWARE\WOW6432Node\CAXA",
            ),
            registry_values=("InstallLocation", "Path", "InstallDir"),
            vendor_folders=("CAXA", "数码大方"),
            product_prefixes=("CAXA", "电子图板"),
            com_progid="CAXA.Application",
            supports_batch_script=False,
            supports_dwg_roundtrip=True,
            supports_3dsolid=False,
            configured_path=configured_path,
        )


class ODAConverterDriver:
    """Optional headless batch DWG <-> DXF converter driver for ODA File Converter."""

    driver_id = "oda"
    vendor: VendorId = "oda"

    def probe(self, configured_path: Path | None = None) -> DriverProbeReport:
        report = _probe_generic_vendor(
            driver_id="oda",
            vendor="oda",
            display_name="ODA File Converter",
            primary_exe_names=("ODAFileConverter.exe",),
            console_exe_names=("ODAFileConverter.exe",),
            env_vars=("ODA_FILE_CONVERTER_EXE",),
            registry_subkeys=(
                r"SOFTWARE\Open Design Alliance",
                r"SOFTWARE\WOW6432Node\Open Design Alliance",
            ),
            registry_values=("InstallLocation", "Path"),
            vendor_folders=("ODA",),
            product_prefixes=("ODAFileConverter",),
            com_progid=None,
            supports_batch_script=True,
            supports_dwg_roundtrip=True,
            supports_3dsolid=False,
            configured_path=configured_path,
        )
        return report


def build_console_command(
    installation: NativeCADInstallation,
    input_file: Path,
    script_file: Path,
    *,
    locale: str | None = None,
) -> list[str]:
    """Build literal argv list for AutoCAD Core Console, ZWCAD ZWCoConsole, or GstarCAD."""

    if installation.console_exe is not None and installation.driver_id in {"autocad", "zwcad", "gstarcad"}:
        lang = locale or ("zh-CN" if installation.driver_id in {"zwcad", "gstarcad"} else "en-US")
        return [
            str(installation.console_exe),
            "/i",
            str(input_file),
            "/s",
            str(script_file),
            "/l",
            lang,
        ]
    if installation.driver_id in {"zwcad", "gstarcad"}:
        return [
            str(installation.primary_exe),
            "/nologo",
            str(input_file),
            "/b",
            str(script_file),
        ]
    raise ValueError(f"driver {installation.driver_id!r} does not expose a console/script CLI")


def build_oda_command(
    oda_exe: Path,
    input_dir: Path,
    output_dir: Path,
    *,
    output_version: str = "ACAD2018",
    output_format: str = "DXF",
    audit: bool = True,
    file_filter: str = "*.dwg",
) -> list[str]:
    """Build safe literal argv for ODAFileConverter headless directory conversion."""

    allowed_versions = {"ACAD2018", "ACAD2013", "ACAD2010", "ACAD2007", "ACAD2004", "ACAD2000", "ACAD14", "ACAD12"}
    allowed_formats = {"DWG", "DXF", "DXB"}
    if output_version not in allowed_versions:
        raise ValueError(f"unsupported ODA output_version: {output_version!r}")
    if output_format not in allowed_formats:
        raise ValueError(f"unsupported ODA output_format: {output_format!r}")
    if any(ch in file_filter for ch in ('"', "\r", "\n", "/", "\\", "..")):
        raise ValueError(f"unsafe ODA file_filter: {file_filter!r}")
    return [
        str(Path(oda_exe).resolve()),
        str(Path(input_dir).resolve()),
        str(Path(output_dir).resolve()),
        output_version,
        output_format,
        "0",  # non-recursive
        "1" if audit else "0",
        file_filter,
    ]


class DomesticCOMSession:
    """Explicit opt-in Windows COM session bridge for ZWCAD / GstarCAD / AutoCAD."""

    ALLOWED_PROGIDS = frozenset(
        {
            "ZWCAD.Application",
            "GstarCAD.Application",
            "CAXA.Application",
            "AutoCAD.Application",
        }
    )

    def __init__(self, progid: str, *, visible: bool = False) -> None:
        base_progid = ".".join(progid.split(".")[:2])
        if base_progid not in self.ALLOWED_PROGIDS:
            raise ValueError(f"disallowed COM ProgID: {progid!r}")
        self.progid = progid
        self.visible = visible
        self._app: Any = None

    def __enter__(self) -> "DomesticCOMSession":
        import win32com.client  # type: ignore[import-untyped]

        self._app = win32com.client.DispatchEx(self.progid)
        try:
            self._app.Visible = bool(self.visible)
        except Exception:
            pass
        return self

    def convert_drawing(self, source_path: Path, destination_path: Path, *, audit: bool = True) -> Path:
        if self._app is None:
            raise RuntimeError("COM session is not open")
        src = str(Path(source_path).resolve(strict=True))
        dst = str(Path(destination_path).resolve(strict=False))
        doc = self._app.Documents.Open(src, True)
        try:
            if audit:
                try:
                    doc.AuditInfo(True)
                except Exception:
                    pass
            doc.SaveAs(dst)
        finally:
            doc.Close(False)
        return Path(dst)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._app is not None:
            try:
                self._app.Quit()
            except Exception:
                pass
            self._app = None
