"""Unified multi-vendor CAD driver registry and font-root aggregator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .autocad_driver import AutoCADMultiVersionDriver
from .models import DriverProbeReport, NativeCADInstallation
from .vendors import CAXADriver, GstarCADDriver, ODAConverterDriver, ZWCADDriver


def probe_all_cad_drivers(
    *,
    autocad_path: Path | None = None,
    zwcad_path: Path | None = None,
    gstarcad_path: Path | None = None,
    caxa_path: Path | None = None,
    oda_path: Path | None = None,
    allowed_autocad_versions: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Probe AutoCAD (2021-2026), ZWCAD, GstarCAD, CAXA, and ODA without launching any CAD process."""

    reports: dict[str, DriverProbeReport] = {
        "autocad": AutoCADMultiVersionDriver(allowed_versions=allowed_autocad_versions).probe(autocad_path),
        "zwcad": ZWCADDriver().probe(zwcad_path),
        "gstarcad": GstarCADDriver().probe(gstarcad_path),
        "caxa": CAXADriver().probe(caxa_path),
        "oda": ODAConverterDriver().probe(oda_path),
    }

    available_drivers = [drv_id for drv_id, rep in reports.items() if rep.status == "available"]
    preferred_console = next(
        (
            drv_id
            for drv_id in ("autocad", "zwcad", "gstarcad")
            if reports[drv_id].status == "available" and reports[drv_id].supports_headless_console
        ),
        None,
    )
    preferred_script = preferred_console or next(
        (
            drv_id
            for drv_id in ("autocad", "zwcad", "gstarcad")
            if reports[drv_id].status == "available" and reports[drv_id].supports_batch_script
        ),
        None,
    )

    fonts_dirs: list[str] = []
    for rep in reports.values():
        for inst in rep.installations:
            if inst.fonts_dir is not None and inst.fonts_dir.is_dir():
                s = str(inst.fonts_dir)
                if s not in fonts_dirs:
                    fonts_dirs.append(s)

    return {
        "status": "available" if available_drivers else "not-installed",
        "available_drivers": available_drivers,
        "preferred_console_driver": preferred_console,
        "preferred_script_driver": preferred_script,
        "fonts_dirs": fonts_dirs,
        "drivers": {drv_id: rep.to_dict() for drv_id, rep in reports.items()},
    }


def discover_installed_cad_font_roots() -> tuple[Path, ...]:
    """Fast, non-Authenticode filesystem/registry scan of installed CAD Fonts directories (AutoCAD/ZWCAD/GstarCAD)."""

    from .autocad_driver import AUTOCAD_RELEASE_MATRIX, _bounded_standard_roots, _registry_candidates_for_releases
    from .vendors import _bounded_vendor_subdirs, _scan_registry_two_levels

    roots: list[Path] = []
    acad_reg, _ = _registry_candidates_for_releases(AUTOCAD_RELEASE_MATRIX)
    acad_std = _bounded_standard_roots(AUTOCAD_RELEASE_MATRIX)

    zw_reg, _ = _scan_registry_two_levels(
        (r"SOFTWARE\ZWSOFT\ZWCAD", r"SOFTWARE\WOW6432Node\ZWSOFT\ZWCAD"),
        ("Location", "InstallLocation", "Path"),
    )
    zw_std = _bounded_vendor_subdirs(("ZWSOFT", "中望软件"), ("ZWCAD", "中望CAD"))

    gs_reg, _ = _scan_registry_two_levels(
        (r"SOFTWARE\Gstarsoft\GstarCAD", r"SOFTWARE\WOW6432Node\Gstarsoft\GstarCAD"),
        ("LOCATION", "Location", "InstallLocation"),
    )
    gs_std = _bounded_vendor_subdirs(("浩辰软件", "Gstarsoft"), ("浩辰CAD", "GstarCAD"))

    seen: set[str] = set()
    for cand in (*acad_reg, *acad_std, *zw_reg, *zw_std, *gs_reg, *gs_std):
        fonts = cand / "Fonts"
        if fonts.is_dir():
            key = str(fonts.resolve(strict=False)).casefold()
            if key not in seen:
                seen.add(key)
                roots.append(fonts.resolve(strict=False))
    return tuple(roots)


def select_preferred_installation(
    *,
    driver_preference: Literal["auto", "autocad", "zwcad", "gstarcad", "oda"] = "auto",
    allowed_versions: tuple[str, ...] | None = None,
    configured_path: Path | None = None,
    require_3dsolid: bool = False,
    allow_oda: bool = False,
) -> tuple[NativeCADInstallation | None, str, str | None]:
    """Select the highest-priority trusted installation matching caller constraints."""

    order: tuple[str, ...]
    if driver_preference == "auto":
        order = ("autocad", "zwcad", "gstarcad", *(["oda"] if allow_oda and not require_3dsolid else []))
    else:
        order = (driver_preference,)

    probe_map = {
        "autocad": lambda p: AutoCADMultiVersionDriver(allowed_versions=allowed_versions).probe(p),
        "zwcad": lambda p: ZWCADDriver().probe(p),
        "gstarcad": lambda p: GstarCADDriver().probe(p),
        "oda": lambda p: ODAConverterDriver().probe(p),
    }

    reasons: list[str] = []
    blocking_code: str | None = None

    for drv_id in order:
        if drv_id == "oda" and require_3dsolid:
            reasons.append("ODA File Converter does not support 3DSOLID modeling.")
            continue
        rep = probe_map[drv_id](configured_path if len(order) == 1 else None)
        if rep.status == "available" and rep.installations:
            # Prefer an installation with a dedicated headless console when multiple exist
            with_console = next((i for i in rep.installations if i.console_exe is not None), None)
            chosen = with_console or rep.installations[0]
            return chosen, rep.reason, None
        reasons.append(f"{drv_id}: {rep.reason}")
        if rep.status in {"untrusted", "inaccessible", "wrong-version"} and blocking_code is None:
            blocking_code = rep.status

    return None, "; ".join(reasons) or "No compatible native CAD driver installed.", blocking_code or "not-installed"
