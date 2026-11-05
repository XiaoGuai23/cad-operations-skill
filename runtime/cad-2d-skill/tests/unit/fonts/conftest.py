from __future__ import annotations

import pytest

from cad_skill_core.fonts.models import FontFace, FontInventory, FontPolicy


class FakeInventory(FontInventory):
    def without_symbol(self, symbol: str) -> "FakeInventory":
        return FakeInventory(
            tuple(
                FontFace(font.name, font.path, font.format, font.cmap - {symbol})
                for font in self.fonts
            )
        )


@pytest.fixture
def fake_inventory() -> FakeInventory:
    latin = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .")
    engineering = frozenset("⌀±°⌖")
    return FakeInventory(
        (
            FontFace("Arial", None, "ttf", latin | engineering),
            FontFace(
                "Microsoft YaHei",
                None,
                "ttf",
                latin | engineering | frozenset("技术要求"),
            ),
        )
    )


@pytest.fixture
def font_policy() -> FontPolicy:
    return FontPolicy.from_mapping(
        {
            "cjk": ["Microsoft YaHei", "Arial"],
            "latin": ["Arial", "Microsoft YaHei"],
            "engineering": ["Microsoft YaHei", "Arial"],
        }
    )
