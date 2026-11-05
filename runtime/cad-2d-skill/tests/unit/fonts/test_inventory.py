from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTLibError

from cad_skill_core.fonts import inventory


def test_unreadable_font_closes_caller_owned_stream(tmp_path: Path, monkeypatch) -> None:
    font_path = tmp_path / "broken.ttf"
    font_path.write_bytes(b"not a font")
    observed_streams = []

    def fail_after_open(stream, **_kwargs):
        observed_streams.append(stream)
        stream.read(1)
        raise TTLibError("broken font")

    monkeypatch.setattr(inventory, "TTFont", fail_after_open)

    assert inventory._read_font(font_path) is None
    assert len(observed_streams) == 1
    assert observed_streams[0].closed
