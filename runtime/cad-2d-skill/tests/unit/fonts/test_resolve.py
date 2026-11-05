import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from cad_skill_core.fonts.inventory import build_font_search_roots, discover_fonts
from cad_skill_core.fonts.models import FontFace, FontInventory, FontPolicy, load_font_policy
from cad_skill_core.fonts.resolve import MissingGlyphError, resolve_font


def test_chinese_text_prefers_available_cjk_font(fake_inventory, font_policy):
    resolved = resolve_font("技术要求 ⌀20 ±0.1°", fake_inventory, font_policy)

    assert resolved.primary == "Microsoft YaHei"
    assert resolved.missing_characters == ()


def test_missing_engineering_symbol_is_not_silenced(fake_inventory, font_policy):
    with pytest.raises(MissingGlyphError) as error:
        resolve_font("⌖", fake_inventory.without_symbol("⌖"), font_policy)

    assert error.value.characters == ("⌖",)


def test_discovery_reads_ttf_cmap_and_keeps_autocad_roots_first(tmp_path):
    autocad_fonts = tmp_path / "autocad"
    windows_fonts = tmp_path / "windows"
    autocad_fonts.mkdir()
    windows_fonts.mkdir()
    _write_ttf(autocad_fonts / "preferred.ttf", "Preferred Test Font", {"技"})
    _write_ttf(windows_fonts / "duplicate.ttf", "Preferred Test Font", {"文"})
    (autocad_fonts / "simplex.shx").write_bytes(b"not a font parser input")

    inventory = discover_fonts((autocad_fonts, windows_fonts))

    preferred = inventory.find("Preferred Test Font")
    shx = inventory.find("simplex")
    assert preferred is not None
    assert preferred.path == autocad_fonts / "preferred.ttf"
    assert preferred.cmap == frozenset({"技"})
    assert shx is not None
    assert shx.cmap == frozenset()


def test_root_builder_enforces_autocad_precedence_for_duplicate_families(tmp_path):
    autocad_fonts = tmp_path / "autocad"
    windows_fonts = tmp_path / "windows"
    autocad_fonts.mkdir()
    windows_fonts.mkdir()
    _write_ttf(autocad_fonts / "font.ttf", "Configured Test Font", {"技"})
    _write_ttf(windows_fonts / "font.ttf", "Configured Test Font", {"文"})

    roots = build_font_search_roots(
        autocad_roots=(autocad_fonts,), windows_roots=(windows_fonts,)
    )
    inventory = discover_fonts(roots)

    assert roots == (autocad_fonts, windows_fonts)
    selected = inventory.find("Configured Test Font")
    assert selected is not None
    assert selected.path == autocad_fonts / "font.ttf"


def test_shx_selection_requires_render_verification():
    inventory = FontInventory((FontFace("simplex", None, "shx"),))
    policy = FontPolicy.from_mapping(
        {"cjk": ["simplex"], "latin": ["simplex"], "engineering": ["simplex"]}
    )

    resolved = resolve_font("技术要求 ⌀", inventory, policy)

    assert resolved.primary == "simplex"
    assert resolved.requires_render_verification is True


def test_discovery_does_not_certify_a_non_unicode_cmap(tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    _write_ttf(fonts / "legacy.ttf", "Legacy Test Font", {"技"}, unicode_cmap=False)

    inventory = discover_fonts((fonts,))

    face = inventory.find("Legacy Test Font")
    assert face is not None
    assert face.cmap == frozenset()


def test_mixed_script_resolution_records_the_font_for_every_text_segment():
    inventory = FontInventory(
        (
            FontFace("Arial", None, "ttf", frozenset("A")),
            FontFace("Microsoft YaHei", None, "ttf", frozenset("技术")),
            FontFace("Engineering Symbols", None, "ttf", frozenset("⌀")),
        )
    )
    policy = FontPolicy.from_mapping(
        {
            "cjk": ["Microsoft YaHei"],
            "latin": ["Arial"],
            "engineering": ["Engineering Symbols"],
        }
    )

    resolved = resolve_font("A技术 ⌀", inventory, policy)

    assert [
        (run.start, run.end, run.text, run.script, run.font)
        for run in resolved.resolved_runs
    ] == [
        (0, 1, "A", "latin", "Arial"),
        (1, 3, "技术", "cjk", "Microsoft YaHei"),
        (3, 4, " ", "whitespace", "Microsoft YaHei"),
        (4, 5, "⌀", "engineering", "Engineering Symbols"),
    ]


def test_resolution_records_fallback_paths_in_exact_name_order(tmp_path):
    latin_path = tmp_path / "latin.ttf"
    symbols_path = tmp_path / "symbols.ttf"
    cjk_path = tmp_path / "cjk.ttf"
    inventory = FontInventory(
        (
            FontFace("Latin", latin_path, "ttf", frozenset("A")),
            FontFace("Symbols", symbols_path, "ttf", frozenset("±")),
            FontFace("CJK", cjk_path, "ttf", frozenset("技")),
        )
    )
    policy = FontPolicy.from_mapping(
        {
            "latin": ["Latin"],
            "engineering": ["Symbols"],
            "cjk": ["CJK"],
        }
    )

    resolved = resolve_font("A±技", inventory, policy)

    assert resolved.primary == "Latin"
    assert resolved.primary_path == latin_path
    assert resolved.fallbacks == ("Symbols", "CJK")
    assert resolved.fallback_paths == (symbols_path, cjk_path)


def test_utf8_yaml_policy_keeps_configured_script_order(tmp_path):
    policy_path = tmp_path / "font-policy.yaml"
    policy_path.write_text(
        "fonts:\n  cjk: [Microsoft YaHei, SimSun]\n"
        "  latin: [Arial]\n  engineering: [Microsoft YaHei, Arial]\n",
        encoding="utf-8",
    )

    policy = load_font_policy(policy_path)

    assert policy.cjk == ("Microsoft YaHei", "SimSun")
    assert policy.engineering == ("Microsoft YaHei", "Arial")


def _write_ttf(path, family, characters, unicode_cmap=True):
    glyph_names = [".notdef", *[f"uni{ord(character):04X}" for character in sorted(characters)]]
    font = FontBuilder(1000, isTTF=True)
    font.setupGlyphOrder(glyph_names)
    font.setupCharacterMap({ord(character): f"uni{ord(character):04X}" for character in characters})
    glyphs = {glyph_name: TTGlyphPen(None).glyph() for glyph_name in glyph_names}
    font.setupGlyf(glyphs)
    font.setupHorizontalMetrics({glyph_name: (600, 0) for glyph_name in glyph_names})
    font.setupHorizontalHeader(ascent=800, descent=-200)
    font.setupNameTable({"familyName": family, "styleName": "Regular"})
    font.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    font.setupPost()
    font.setupMaxp()
    font.save(path)
    if not unicode_cmap:
        legacy_font = TTFont(path)
        for index, table in enumerate(legacy_font["cmap"].tables):
            table.platformID = 1
            table.platEncID = index
            table.language = 0
        legacy_font.save(path)
