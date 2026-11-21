"""Traceable source-document extraction."""

from .extract import extract_sources
from .models import SourceExtractionError, SourceFragment, UnsupportedSourceError
from .spec_compiler import compile_spec_from_files, compile_spec_from_fragments

__all__ = [
    "SourceExtractionError",
    "SourceFragment",
    "UnsupportedSourceError",
    "compile_spec_from_files",
    "compile_spec_from_fragments",
    "extract_sources",
]
