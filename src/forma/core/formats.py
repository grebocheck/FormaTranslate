"""Document capabilities shared by workflows, persistence and presentation.

An accepted extension does not imply that every operation supports that format.
In particular, PDF review and Office reference import have narrower contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FormatCapabilities:
    suffix: str
    engine: str = "forma-ooxml"
    office: bool = False
    page_preview: bool = False
    manual_export: bool = False
    memory: bool = False
    memory_import: bool = False
    memory_approval: bool = False
    recovery: bool = False
    comparison: bool = True
    pdf_filter: str = ""
    extraction_profiles: tuple[int, ...] = ()

    @property
    def code(self) -> str:
        return self.suffix[1:].upper()


DOCUMENT_FORMATS = (
    FormatCapabilities(".docx", office=True, page_preview=True, manual_export=True,
                       memory=True, memory_import=True, memory_approval=True, recovery=True,
                       pdf_filter="writer_pdf_Export", extraction_profiles=(1, 3)),
    FormatCapabilities(".xlsx", office=True, manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(1, 2)),
    FormatCapabilities(".pptx", office=True, page_preview=True, manual_export=True,
                       memory=True, recovery=True, pdf_filter="impress_pdf_Export",
                       extraction_profiles=(4,)),
    FormatCapabilities(".odt", engine="forma-odf", page_preview=True, manual_export=True,
                       memory=True, recovery=True, pdf_filter="writer_pdf_Export",
                       extraction_profiles=(8, 15)),
    FormatCapabilities(".ods", engine="forma-odf", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(9,)),
    FormatCapabilities(".odp", engine="forma-odf", page_preview=True, manual_export=True,
                       memory=True, recovery=True, pdf_filter="impress_pdf_Export",
                       extraction_profiles=(10, 16)),
    FormatCapabilities(".pdf", engine="babeldoc", page_preview=True),
    FormatCapabilities(".txt", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(5,)),
    FormatCapabilities(".md", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(6, 14)),
    FormatCapabilities(".markdown", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(6, 14)),
    FormatCapabilities(".html", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(7,)),
    FormatCapabilities(".htm", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(7,)),
    FormatCapabilities(".srt", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(11,)),
    FormatCapabilities(".vtt", engine="forma-text", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(12,)),
    FormatCapabilities(".epub", engine="forma-epub", manual_export=True, memory=True,
                       recovery=True, extraction_profiles=(13,)),
)
_FORMATS = {item.suffix: item for item in DOCUMENT_FORMATS}
SUPPORTED_SUFFIXES = frozenset(_FORMATS)
SUPPORTED_FORMATS = frozenset(item.code for item in DOCUMENT_FORMATS)
OFFICE_SUFFIXES = frozenset(item.suffix for item in DOCUMENT_FORMATS if item.office)
EDITABLE_SUFFIXES = frozenset(item.suffix for item in DOCUMENT_FORMATS if item.manual_export)
MEMORY_SUFFIXES = frozenset(item.suffix for item in DOCUMENT_FORMATS if item.memory)
PREVIEW_SUFFIXES = frozenset(item.suffix for item in DOCUMENT_FORMATS if item.page_preview)
RECOVERABLE_SUFFIXES = frozenset(item.suffix for item in DOCUMENT_FORMATS if item.recovery)
EXTRACTION_PROFILES = frozenset(profile for item in DOCUMENT_FORMATS for profile in item.extraction_profiles)
DOCUMENT_FILTER = "Документи (" + " ".join("*" + item.suffix for item in DOCUMENT_FORMATS) + ")"


def get_format(value: str | Path) -> FormatCapabilities | None:
    text = str(value).strip().lower()
    return _FORMATS.get(text) or _FORMATS.get(Path(text).suffix) or _FORMATS.get("." + text)


def format_capabilities(value: str | Path) -> dict[str, bool]:
    """Expose only presentation capabilities, without document parsing or I/O."""
    item = get_format(value)
    return {
        key: bool(item and getattr(item, key))
        for key in ("page_preview", "manual_export", "memory", "memory_import", "memory_approval", "recovery", "comparison")
    }
