from pathlib import Path
from types import SimpleNamespace

import pytest


def make_fake_installation(tmp_path: Path, name: str = "AutoCAD 2023"):
    root = tmp_path / "Program Files" / "Autodesk" / name
    root.mkdir(parents=True)
    core_console_exe = root / "accoreconsole.exe"
    acad_exe = root / "acad.exe"
    core_console_exe.write_bytes(b"fake core console")
    acad_exe.write_bytes(b"fake AutoCAD")
    fonts_dir = root / "Fonts"
    fonts_dir.mkdir()
    return SimpleNamespace(
        root=root,
        core_console_exe=core_console_exe,
        acad_exe=acad_exe,
        fonts_dir=fonts_dir,
    )


@pytest.fixture
def fake_installation(tmp_path):
    return make_fake_installation(tmp_path)


@pytest.fixture
def autocad_2023_metadata(monkeypatch):
    def read_metadata(path):
        if Path(path).name.casefold() == "accoreconsole.exe":
            return "AcCoreConsole", "24.2.53.0.0"
        return "AutoCAD", "R24.2.53.0.0"

    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._read_version_metadata", read_metadata
    )

    def valid_signature(_path):
        from cad_skill_core.autocad.models import SignatureEvidence

        return SignatureEvidence(
            "Valid",
            'CN="Autodesk, Inc.", O="Autodesk, Inc.", C=US',
            "B0E1E4FEF69EABDAC44CB9AD1C30E59E1C9066D0",
        )

    monkeypatch.setattr(
        "cad_skill_core.autocad.discovery._verify_authenticode", valid_signature
    )
