from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from cad_skill_core.autocad.discovery import discover_autocad, _verify_authenticode
from cad_skill_core.autocad.models import (
    AutoCADErrorDetail,
    AutoCADInaccessibleError,
    AutoCADNotInstalledError,
    AutoCADUntrustedError,
    AutoCADWrongVersionError,
    SignatureEvidence,
)

from .conftest import make_fake_installation


def test_explicit_autocad_installation_wins(
    fake_installation, autocad_2023_metadata, monkeypatch
):
    monkeypatch.setenv("ACCORECONSOLE_EXE", r"Z:\stale\accoreconsole.exe")

    found = discover_autocad(fake_installation.core_console_exe)

    assert found.core_console_exe == fake_installation.core_console_exe.resolve()
    assert found.acad_exe == fake_installation.acad_exe.resolve()
    assert found.fonts_dir == fake_installation.fonts_dir.resolve()
    assert found.version == "2023"


def test_environment_path_precedes_bounded_known_roots(
    tmp_path, autocad_2023_metadata, monkeypatch
):
    configured = make_fake_installation(tmp_path / "configured")
    known = make_fake_installation(tmp_path / "known")
    monkeypatch.setenv("ACCORECONSOLE_EXE", str(configured.core_console_exe))
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: known.root
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots", lambda: ()
    )

    found = discover_autocad()

    assert found.core_console_exe == configured.core_console_exe.resolve()


def test_explicit_wrong_release_is_not_silently_replaced_by_known_install(
    tmp_path, monkeypatch
):
    wrong = make_fake_installation(tmp_path / "wrong", "AutoCAD 2024")
    known = make_fake_installation(tmp_path / "known")
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: known.root
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda path: (
            ("AcCoreConsole", "24.3.1.0")
            if Path(path).name.casefold() == "accoreconsole.exe"
            else ("AutoCAD", "R24.3.1.0")
        ),
    )

    with pytest.raises(AutoCADWrongVersionError) as error:
        discover_autocad(wrong.root)

    assert error.value.detail.code == "wrong-version"
    assert error.value.detail.path == wrong.root.resolve()


def test_missing_explicit_path_is_inaccessible_not_not_installed(tmp_path):
    missing = tmp_path / "missing AutoCAD" / "accoreconsole.exe"

    with pytest.raises(AutoCADInaccessibleError) as error:
        discover_autocad(missing)

    assert error.value.detail.code == "inaccessible"
    assert error.value.detail.path == missing.resolve()


def test_no_bounded_candidate_reports_not_installed(monkeypatch):
    monkeypatch.delenv("ACCORECONSOLE_EXE", raising=False)
    monkeypatch.delenv("AUTOCAD_EXE", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: None
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots", lambda: ()
    )

    with pytest.raises(AutoCADNotInstalledError) as error:
        discover_autocad()

    assert error.value.detail.code == "not-installed"
    assert error.value.detail.path is None


def test_discovery_never_recursively_scans_a_drive(monkeypatch):
    monkeypatch.delenv("ACCORECONSOLE_EXE", raising=False)
    monkeypatch.delenv("AUTOCAD_EXE", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: None
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        Path,
        "rglob",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded recursive scan")
        ),
    )

    with pytest.raises(AutoCADNotInstalledError):
        discover_autocad()


def test_existing_bounded_install_with_unreadable_metadata_is_inaccessible(
    tmp_path, monkeypatch
):
    candidate = make_fake_installation(tmp_path)
    monkeypatch.delenv("ACCORECONSOLE_EXE", raising=False)
    monkeypatch.delenv("AUTOCAD_EXE", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: candidate.root
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots", lambda: ()
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda _path: (_ for _ in ()).throw(PermissionError("metadata denied")),
    )

    with pytest.raises(AutoCADInaccessibleError) as error:
        discover_autocad()

    assert error.value.detail.code == "inaccessible"
    assert error.value.detail.path == candidate.root.resolve()


def test_forged_2023_product_strings_with_bad_signature_are_rejected(
    fake_installation, monkeypatch
):
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda path: (
            ("AcCoreConsole", "24.2.53.0.0")
            if Path(path).name.casefold() == "accoreconsole.exe"
            else ("AutoCAD", "R24.2.53.0.0")
        ),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _path: SignatureEvidence("HashMismatch", "CN=Autodesk, Inc.", "BAD"),
    )

    with pytest.raises(AutoCADUntrustedError) as error:
        discover_autocad(fake_installation.root)

    assert error.value.detail.code == "untrusted-executable"
    assert error.value.detail.path == fake_installation.core_console_exe.resolve()


def test_signature_verifier_unavailable_fails_closed(fake_installation, monkeypatch):
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata",
        lambda path: (
            ("AcCoreConsole", "24.2.53.0.0")
            if Path(path).name.casefold() == "accoreconsole.exe"
            else ("AutoCAD", "R24.2.53.0.0")
        ),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode",
        lambda _path: (_ for _ in ()).throw(OSError("verifier unavailable")),
    )

    with pytest.raises(AutoCADUntrustedError, match="verifier unavailable"):
        discover_autocad(fake_installation.root)


def test_signature_verifier_transports_arbitrary_path_without_command_interpolation(
    tmp_path, monkeypatch
):
    system_root = tmp_path / "trusted-windows"
    system_directory = system_root / "System32"
    powershell = (
        system_directory
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"fake")
    target = tmp_path / "cad files" / "签名 目标.exe"
    target.parent.mkdir()
    target.write_bytes(b"binary")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "attacker-windows"))
    monkeypatch.setenv("WINDIR", str(tmp_path / "attacker-windir"))
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._trusted_system_directory",
        lambda: system_directory,
        raising=False,
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == str(powershell)
        assert str(target) not in argv
        assert kwargs["env"]["CAD_SKILL_SIGNATURE_TARGET"] == str(target)
        assert kwargs["env"]["SystemRoot"] == str(system_root)
        assert "attacker" not in repr(kwargs["env"])
        assert "$env:CAD_SKILL_SIGNATURE_TARGET" in argv[4]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '{"Status":"Valid","Publisher":"CN=Autodesk, Inc.",'
                '"Thumbprint":"ABC123"}'
            ),
            stderr="",
        )

    monkeypatch.setattr("cad_skill_core.autocad.discovery.subprocess.run", fake_run)

    evidence = _verify_authenticode(target)

    assert evidence.status == "Valid"
    assert evidence.publisher == "CN=Autodesk, Inc."
    assert evidence.thumbprint == "ABC123"
    assert len(calls) == 1


def test_discovered_installation_records_frozen_file_identity(
    fake_installation, autocad_2023_metadata
):
    found = discover_autocad(fake_installation.root)

    assert found.trusted is True
    assert found.core_console_identity.path == fake_installation.core_console_exe.resolve()
    assert found.core_console_identity.size == fake_installation.core_console_exe.stat().st_size
    assert len(found.core_console_identity.sha256) == 64
    assert found.core_console_identity.signature_thumbprint == (
        "B0E1E4FEF69EABDAC44CB9AD1C30E59E1C9066D0"
    )
    with pytest.raises(FrozenInstanceError):
        found.core_console_identity.size = 0


def test_fixed_2023_root_precedes_registry_and_program_files(
    tmp_path, autocad_2023_metadata, monkeypatch
):
    fixed = make_fake_installation(tmp_path / "fixed")
    registry = make_fake_installation(tmp_path / "registry")
    program_files = make_fake_installation(tmp_path / "program-files")
    monkeypatch.delenv("ACCORECONSOLE_EXE", raising=False)
    monkeypatch.delenv("AUTOCAD_EXE", raising=False)
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._fixed_install_root", lambda: fixed.root
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._registry_install_roots",
        lambda: (registry.root,),
    )
    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._program_files_install_roots",
        lambda: (program_files.root,),
    )

    found = discover_autocad()

    assert found.core_console_exe == fixed.core_console_exe.resolve()


def test_discovery_error_detail_property_is_read_only():
    error = AutoCADUntrustedError(
        AutoCADErrorDetail("untrusted-executable", "bad signature")
    )

    with pytest.raises(AttributeError):
        error.detail = AutoCADErrorDetail("not-installed", "changed")
