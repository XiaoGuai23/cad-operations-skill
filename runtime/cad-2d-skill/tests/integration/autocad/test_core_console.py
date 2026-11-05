from pathlib import Path

import pytest

from cad_skill_core.autocad.discovery import discover_autocad
from cad_skill_core.autocad.models import AutoCADNotInstalledError, AutoCADUntrustedError


@pytest.mark.autocad
def test_real_autocad_2023_discovery_is_read_only_and_exact():
    """Probe only executable metadata; never launch AutoCAD in the test suite."""

    try:
        installation = discover_autocad()
    except AutoCADNotInstalledError:
        pytest.skip("AutoCAD 2023 is not installed in a bounded location")
    except AutoCADUntrustedError as error:
        assert error.detail.code == "untrusted-executable"
        assert error.detail.path is not None
        assert "signature" in error.detail.message.casefold()
        return

    assert installation.version == "2023"
    assert installation.trusted is True
    assert installation.core_console_exe.name.casefold() == "accoreconsole.exe"
    assert installation.acad_exe.name.casefold() == "acad.exe"
    assert installation.core_console_exe.is_file()
    assert installation.acad_exe.is_file()
    assert Path(installation.fonts_dir).is_absolute()
