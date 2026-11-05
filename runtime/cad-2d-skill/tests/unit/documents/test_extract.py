from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from cad_skill_core.documents.extract import (
    SourceExtractionError,
    UnsupportedSourceError,
    extract_sources,
)


def test_markdown_fragments_keep_line_locations(tmp_path):
    source = tmp_path / "project.md"
    source.write_text("# 法兰\n外径：200 mm\n材料：Q235\n", encoding="utf-8")

    fragments = extract_sources([source])

    assert any(f.locator == "lines 2-2" and "200 mm" in f.text for f in fragments)
    assert all(f.confidence == 1.0 for f in fragments)


def test_unknown_extension_is_rejected(tmp_path):
    source = tmp_path / "project.bin"
    source.write_bytes(b"x")

    with pytest.raises(UnsupportedSourceError, match=r"\.bin"):
        extract_sources([source])


def test_text_fragments_keep_exact_line_locations(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("first line\nsecond line\n\nthird line\n", encoding="utf-8")

    fragments = extract_sources([source])

    assert [(fragment.locator, fragment.text) for fragment in fragments] == [
        ("lines 1-1", "first line"),
        ("lines 2-2", "second line"),
        ("lines 4-4", "third line"),
    ]


def test_fragment_id_is_stable_for_normalized_content(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("value: 200 mm  \n", encoding="utf-8")

    first = extract_sources([source])[0]
    source.write_text("value: 200 mm\n", encoding="utf-8")
    second = extract_sources([source])[0]

    assert first.id == second.id


def test_docx_fragments_include_paragraph_and_table_row_locations(tmp_path):
    source = tmp_path / "requirements.docx"
    document = Document()
    document.add_paragraph("材料：Q235")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "外径"
    table.cell(0, 1).text = "200 mm"
    document.save(source)

    fragments = extract_sources([source])

    assert [(fragment.locator, fragment.text) for fragment in fragments] == [
        ("paragraph 1", "材料：Q235"),
        ("table 1 row 1", "外径 | 200 mm"),
    ]


def test_pdf_fragment_uses_page_locator(tmp_path):
    source = tmp_path / "requirements.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 10 Td (Diameter 200 mm) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with source.open("wb") as output:
        writer.write(output)

    fragments = extract_sources([source])

    assert [(fragment.locator, fragment.text) for fragment in fragments] == [
        ("page 1", "Diameter 200 mm")
    ]


def test_image_returns_dimensions_without_ocr(tmp_path):
    source = tmp_path / "drawing.png"
    Image.new("RGB", (320, 240), "white").save(source)

    fragments = extract_sources([source])

    assert len(fragments) == 1
    assert fragments[0].locator == "image metadata"
    assert "320" in fragments[0].text
    assert "240" in fragments[0].text
    assert fragments[0].confidence == 1.0


def test_image_ocr_confidence_is_capped(tmp_path):
    source = tmp_path / "drawing.jpg"
    Image.new("RGB", (10, 10), "white").save(source)

    fragments = extract_sources([source], ocr=lambda _: ("Ø200", 0.99))

    ocr_fragment = next(fragment for fragment in fragments if fragment.locator == "OCR text")
    assert ocr_fragment.text == "Ø200"
    assert ocr_fragment.confidence == 0.7


def test_image_ocr_rejects_non_finite_confidence(tmp_path):
    source = tmp_path / "drawing.png"
    Image.new("RGB", (10, 10), "white").save(source)

    with pytest.raises(SourceExtractionError, match="confidence must be finite"):
        extract_sources([source], ocr=lambda _: ("Ø200", float("nan")))


def test_pdf_page_tree_failure_has_path_aware_extraction_error(tmp_path, monkeypatch):
    source = tmp_path / "broken-pages.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    class BrokenReader:
        def __init__(self, _: Path) -> None:
            pass

        @property
        def pages(self):
            raise RuntimeError("broken page tree")

    monkeypatch.setattr("cad_skill_core.documents.extract.PdfReader", BrokenReader)

    with pytest.raises(SourceExtractionError, match=r"broken-pages\.pdf"):
        extract_sources([source])


def test_malformed_docx_xml_has_path_aware_extraction_error(tmp_path):
    source = tmp_path / "malformed.docx"
    document = Document()
    document.add_paragraph("valid before corruption")
    document.save(source)
    with ZipFile(source, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
        archive.writestr("word/document.xml", b"<w:document")

    with pytest.raises(SourceExtractionError, match=r"malformed\.docx"):
        extract_sources([source])


def test_missing_file_has_path_aware_extraction_error(tmp_path):
    source = tmp_path / "missing.pdf"

    with pytest.raises(SourceExtractionError, match=r"missing\.pdf"):
        extract_sources([source])


def test_unreadable_docx_has_path_aware_extraction_error(tmp_path):
    source = tmp_path / "corrupt.docx"
    source.write_bytes(b"not a docx")

    with pytest.raises(SourceExtractionError, match=r"corrupt\.docx"):
        extract_sources([source])
