"""Explicit, deterministic extractors for drawing-source documents."""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeAlias

from docx import Document
from PIL import Image
from pypdf import PdfReader

from .models import SourceExtractionError, SourceFragment, UnsupportedSourceError

OCRResult: TypeAlias = str | tuple[str, float]
OCRCallable: TypeAlias = Callable[[Path], OCRResult]

_TEXT_MEDIA_TYPES = {".md": "text/markdown", ".txt": "text/plain"}
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MEDIA_TYPE = "application/pdf"


def extract_sources(
    paths: Sequence[Path], *, ocr: OCRCallable | None = None
) -> list[SourceFragment]:
    """Extract independently traceable fragments from supported source files.

    ``ocr`` is optional so image files remain usable without an OCR engine. It
    receives the image path and may return text alone or ``(text, confidence)``.
    OCR-derived fragments are capped at 0.7 confidence.
    """

    fragments: list[SourceFragment] = []
    for supplied_path in paths:
        path = Path(supplied_path)
        if not path.is_file():
            raise SourceExtractionError(path, None, "file does not exist or is not a regular file")

        suffix = path.suffix.lower()
        if suffix in _TEXT_MEDIA_TYPES:
            fragments.extend(_extract_text(path, _TEXT_MEDIA_TYPES[suffix]))
        elif suffix == ".docx":
            fragments.extend(_extract_docx(path))
        elif suffix == ".pdf":
            fragments.extend(_extract_pdf(path))
        elif suffix in _IMAGE_MEDIA_TYPES:
            fragments.extend(_extract_image(path, _IMAGE_MEDIA_TYPES[suffix], ocr))
        else:
            raise UnsupportedSourceError(path)
    return fragments


def _extract_text(path: Path, media_type: str) -> list[SourceFragment]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SourceExtractionError(path, None, str(error)) from error

    fragments: list[SourceFragment] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        content = _normalize_content(line)
        if content:
            locator = f"lines {line_number}-{line_number}"
            fragments.append(_fragment(path, locator, media_type, content, 1.0))
    return fragments


def _extract_docx(path: Path) -> list[SourceFragment]:
    locator: str | None = None
    try:
        document = Document(path)
        fragments: list[SourceFragment] = []
        for paragraph_number, paragraph in enumerate(document.paragraphs, start=1):
            locator = f"paragraph {paragraph_number}"
            content = _normalize_content(paragraph.text)
            if content:
                fragments.append(_fragment(path, locator, _DOCX_MEDIA_TYPE, content, 1.0))

        for table_number, table in enumerate(document.tables, start=1):
            for row_number, row in enumerate(table.rows, start=1):
                locator = f"table {table_number} row {row_number}"
                content = _normalize_content(" | ".join(cell.text for cell in row.cells))
                if content:
                    fragments.append(_fragment(path, locator, _DOCX_MEDIA_TYPE, content, 1.0))
        return fragments
    except SourceExtractionError:
        raise
    except Exception as error:  # DOCX XML and relationship parsers raise library-specific errors.
        raise SourceExtractionError(path, locator, str(error)) from error


def _extract_pdf(path: Path) -> list[SourceFragment]:
    locator: str | None = None
    try:
        reader = PdfReader(path)
        fragments: list[SourceFragment] = []
        for page_number, page in enumerate(reader.pages, start=1):
            locator = f"page {page_number}"
            content = _normalize_content(page.extract_text() or "")
            if content:
                fragments.append(_fragment(path, locator, _PDF_MEDIA_TYPE, content, 1.0))
        return fragments
    except SourceExtractionError:
        raise
    except Exception as error:  # pypdf exposes several parser and page-tree exception types.
        raise SourceExtractionError(path, locator, str(error)) from error


def _extract_image(
    path: Path, media_type: str, ocr: OCRCallable | None
) -> list[SourceFragment]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format or path.suffix.lstrip(".").upper()
    except (OSError, ValueError) as error:
        raise SourceExtractionError(path, None, str(error)) from error

    fragments = [
        _fragment(
            path,
            "image metadata",
            media_type,
            f"format: {image_format}; dimensions: {width} x {height}",
            1.0,
        )
    ]
    if ocr is None:
        return fragments

    try:
        result = ocr(path)
    except Exception as error:
        raise SourceExtractionError(path, "OCR text", str(error)) from error
    text, confidence = _unpack_ocr_result(path, result)
    content = _normalize_content(text)
    if content:
        fragments.append(
            _fragment(path, "OCR text", media_type, content, min(max(confidence, 0.0), 0.7))
        )
    return fragments


def _unpack_ocr_result(path: Path, result: OCRResult) -> tuple[str, float]:
    if isinstance(result, str):
        return result, 0.7
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
        try:
            confidence = float(result[1])
        except (TypeError, ValueError) as error:
            raise SourceExtractionError(path, "OCR text", "OCR confidence must be numeric") from error
        if not math.isfinite(confidence):
            raise SourceExtractionError(path, "OCR text", "OCR confidence must be finite")
        return result[0], confidence
    raise SourceExtractionError(path, "OCR text", "OCR callable must return text or (text, confidence)")


def _fragment(
    path: Path,
    locator: str,
    media_type: str,
    text: str,
    confidence: float,
) -> SourceFragment:
    resolved_path = str(path.resolve())
    normalized_text = _normalize_content(text)
    digest = hashlib.sha256(
        f"{resolved_path}\n{locator}\n{normalized_text}".encode("utf-8")
    ).hexdigest()
    return SourceFragment(
        id=digest,
        file=resolved_path,
        locator=locator,
        media_type=media_type,
        text=normalized_text,
        confidence=confidence,
    )


def _normalize_content(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
