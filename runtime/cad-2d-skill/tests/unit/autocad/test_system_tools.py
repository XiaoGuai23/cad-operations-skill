from pathlib import Path

from cad_skill_core.autocad.system_tools import trusted_system_directory


def test_trusted_system_directory_uses_injected_winapi_boundary(tmp_path):
    system_directory = tmp_path / "Windows" / "System32"
    system_directory.mkdir(parents=True)
    calls = []

    def fake_get_system_directory(buffer, size):
        calls.append(size)
        buffer.value = str(system_directory)
        return len(str(system_directory))

    found = trusted_system_directory(
        get_system_directory=fake_get_system_directory
    )

    assert found == Path(system_directory).resolve(strict=True)
    assert calls == [260]
