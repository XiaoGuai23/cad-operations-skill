from __future__ import annotations

from pathlib import Path
import pytest

from cad_skill_core.autocad.models import (
    AutoCADInaccessibleError,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
    SignatureEvidence,
)
from cad_skill_core.cad_drivers import (
    AUTOCAD_RELEASE_MATRIX,
    AutoCADMultiVersionDriver,
    CAXADriver,
    DomesticCOMSession,
    GstarCADDriver,
    ODAConverterDriver,
    ZWCADDriver,
    build_console_command,
    build_oda_command,
    discover_autocad_installations,
)


def _create_fake_dir(root: Path, exe_names: tuple[str, ...], with_fonts: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in exe_names:
        (root / name).write_bytes(f"PE-FAKE-{name}".encode("ascii"))
    if with_fonts:
        fdir = root / "Fonts"
        fdir.mkdir(exist_ok=True)
        (fdir / "gbcbig.shx").write_bytes(b"SHX")
    return root


@pytest.mark.parametrize(
    ("year", "rel_code", "ver_str"),
    [
        ("2021", "R24.0", "24.0.47.0.0"),
        ("2022", "R24.1", "24.1.51.0.0"),
        ("2023", "R24.2", "24.2.53.0.0"),
        ("2024", "R24.3", "24.3.61.0.0"),
        ("2025", "R25.0", "25.0.58.0.0"),
        ("2026", "R25.1", "25.1.12.0.0"),
    ],
)
def test_autocad_multiversion_discovers_all_2021_to_2026_releases(
    tmp_path, monkeypatch, year, rel_code, ver_str
):
    assert len(AUTOCAD_RELEASE_MATRIX) == 6
    install_dir = _create_fake_dir(tmp_path / f"AutoCAD {year}", ("acad.exe", "accoreconsole.exe"))

    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda p: (
            ("AcCoreConsole", ver_str)
            if Path(p).name.casefold() == "accoreconsole.exe"
            else ("AutoCAD", f"R{ver_str}")
        ),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence(
            "Valid", 'CN="Autodesk, Inc.", O="Autodesk, Inc.", C=US', "ABCDEF123456"
        ),
    )

    installs, stale = discover_autocad_installations(configured_path=install_dir)
    assert len(installs) == 1
    assert stale == ()
    inst = installs[0]
    assert inst.version == year
    assert inst.release_code == rel_code
    assert inst.driver_id == "autocad"
    assert inst.com_progid == f"AutoCAD.Application.{rel_code.lstrip('R')}"


def test_autocad_multiversion_orders_newest_first_and_filters_allowed_versions(tmp_path, monkeypatch):
    dir_2022 = _create_fake_dir(tmp_path / "AutoCAD 2022", ("acad.exe", "accoreconsole.exe"))
    dir_2025 = _create_fake_dir(tmp_path / "AutoCAD 2025", ("acad.exe", "accoreconsole.exe"))

    monkeypatch.delenv("CAD_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("ACCORECONSOLE_EXE", raising=False)
    monkeypatch.delenv("AUTOCAD_EXE", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.cad_drivers.autocad_driver._registry_candidates_for_releases",
        lambda _specs: ((dir_2022, dir_2025), (r"C:\Stale\AutoCAD 2021",)),
    )
    monkeypatch.setattr(
        "cad_skill_core.cad_drivers.autocad_driver._bounded_standard_roots",
        lambda _specs: (),
    )

    def fake_meta(path: Path):
        ver = "25.0.1.0" if "2025" in str(path) else "24.1.1.0"
        prod = "AcCoreConsole" if path.name.casefold() == "accoreconsole.exe" else "AutoCAD"
        return prod, ver

    monkeypatch.setattr("cad_skill_core.autocad.discovery._read_version_metadata", fake_meta)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence("Valid", 'CN="Autodesk, Inc."', "THUMB1"),
    )

    installs, stale = discover_autocad_installations()
    assert [i.version for i in installs] == ["2025", "2022"]
    assert stale == (r"C:\Stale\AutoCAD 2021",)

    only_2022, _ = discover_autocad_installations(allowed_versions=("2022",))
    assert [i.version for i in only_2022] == ["2022"]

    with pytest.raises(AutoCADWrongVersionError):
        discover_autocad_installations(configured_path=dir_2025, allowed_versions=("2023",))

    with pytest.raises(AutoCADInaccessibleError):
        discover_autocad_installations(configured_path=tmp_path / "nonexistent_cad")


def test_zwcad_driver_discovers_zwcoconsole_fonts_and_rejects_forged_signature(tmp_path, monkeypatch):
    zw_dir = _create_fake_dir(tmp_path / "ZWCAD 2025 zh-CN", ("ZWCAD.exe", "ZWCoConsole.exe"))
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda _p: ("ZWCAD", "25.0.2024.08"),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence(
            "Valid", 'CN="广州中望龙腾软件股份有限公司", O="ZWSOFT"', "ZWTHUMB99"
        ),
    )

    report = ZWCADDriver().probe(zw_dir)
    assert report.status == "available"
    assert report.supports_headless_console is True
    assert report.com_progid == "ZWCAD.Application"
    inst = report.installations[0]
    assert inst.version == "2025"
    assert inst.fonts_dir == zw_dir / "Fonts"

    cmd = build_console_command(inst, tmp_path / "in.dxf", tmp_path / "job.scr")
    assert cmd == [
        str(zw_dir / "ZWCoConsole.exe"),
        "/i",
        str(tmp_path / "in.dxf"),
        "/s",
        str(tmp_path / "job.scr"),
        "/l",
        "zh-CN",
    ]

    # Without ZWCoConsole.exe -> falls back cleanly to ZWCAD.exe /nologo <in> /b <scr>
    zw_gui_only = _create_fake_dir(tmp_path / "ZWCAD 2022", ("ZWCAD.exe",))
    rep_gui = ZWCADDriver().probe(zw_gui_only)
    assert rep_gui.status == "available"
    assert rep_gui.supports_headless_console is False
    assert build_console_command(rep_gui.installations[0], tmp_path / "a.dxf", tmp_path / "b.scr") == [
        str(zw_gui_only / "ZWCAD.exe"),
        "/nologo",
        str(tmp_path / "a.dxf"),
        "/b",
        str(tmp_path / "b.scr"),
    ]

    # Forged non-ZWSOFT certificate is rejected fail-closed!
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence("Valid", 'CN="Untrusted Corp"', "BAD"),
    )
    untrusted_rep = ZWCADDriver().probe(zw_dir)
    assert untrusted_rep.status == "untrusted"


def test_gstarcad_driver_supports_chinese_paths_and_ignores_stale_uninstalled_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("GSTARCAD_CONSOLE_EXE", raising=False)
    monkeypatch.delenv("GSTARCAD_EXE", raising=False)
    monkeypatch.delenv("GSTARCAD_ROOT", raising=False)

    # 1. Stale registry leftover (uninstalled path) must report not-installed, NOT crash/block
    monkeypatch.setattr(
        "cad_skill_core.cad_drivers.vendors._scan_registry_two_levels",
        lambda *_args: ((), (r"C:\Program Files\浩辰软件\浩辰CAD2023",)),
    )
    monkeypatch.setattr(
        "cad_skill_core.cad_drivers.vendors._bounded_vendor_subdirs",
        lambda *_args: (),
    )
    stale_rep = GstarCADDriver().probe()
    assert stale_rep.status == "not-installed"
    assert r"C:\Program Files\浩辰软件\浩辰CAD2023" in stale_rep.stale_registry_paths

    # 2. Installed Chinese directory with GcadCoreConsole.exe + Gstarsoft signature
    gs_dir = _create_fake_dir(tmp_path / "浩辰软件" / "浩辰CAD2024", ("gcad.exe", "GcadCoreConsole.exe"))
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda _p: ("浩辰CAD 2024", "24.0.0.0"),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence("Valid", 'CN="苏州浩辰软件股份有限公司", O="Gstarsoft"', "GSTHUMB"),
    )
    ok_rep = GstarCADDriver().probe(gs_dir)
    assert ok_rep.status == "available"
    assert ok_rep.supports_headless_console is True
    assert ok_rep.installations[0].version == "2024"


def test_caxa_oda_and_com_progid_security_guards(tmp_path, monkeypatch):
    oda_dir = _create_fake_dir(tmp_path / "ODAFileConverter 25.4.0", ("ODAFileConverter.exe",), with_fonts=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda _p: ("ODAFileConverter", "25.4.0.0"),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _p: SignatureEvidence("Valid", 'CN="Open Design Alliance"', "ODATHUMB"),
    )

    oda_rep = ODAConverterDriver().probe(oda_dir)
    assert oda_rep.status == "available"

    argv = build_oda_command(
        oda_dir / "ODAFileConverter.exe",
        tmp_path / "in",
        tmp_path / "out",
        output_version="ACAD2018",
        output_format="DXF",
    )
    assert argv[3:7] == ["ACAD2018", "DXF", "0", "1"]

    with pytest.raises(ValueError, match="output_version"):
        build_oda_command(oda_dir / "ODAFileConverter.exe", tmp_path / "in", tmp_path / "out", output_version="BAD")
    with pytest.raises(ValueError, match="file_filter"):
        build_oda_command(oda_dir / "ODAFileConverter.exe", tmp_path / "in", tmp_path / "out", file_filter="../bad.dwg")

    # COM ProgID allowlist rejects arbitrary ActiveX objects (e.g. WScript.Shell)
    with pytest.raises(ValueError, match="disallowed COM ProgID"):
        DomesticCOMSession("WScript.Shell")
    session = DomesticCOMSession("ZWCAD.Application.2025")
    assert session.progid == "ZWCAD.Application.2025"

    # CAXA missing explicit path reports inaccessible
    caxa_rep = CAXADriver().probe(tmp_path / "missing_caxa")
    assert caxa_rep.status == "inaccessible"
    assert AutoCADMultiVersionDriver().probe(tmp_path / "missing_acad").status == "inaccessible"
