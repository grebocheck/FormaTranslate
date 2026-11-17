"""Native document loading without changing existing Office extraction identities."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from forma.core.formats import get_format
from forma.core.models import FormaError, Issue, Segment, plain_text


class DocumentAdapter(Protocol):
    path: Path
    segments: list[Segment]
    issues: list[Issue]
    extraction_profile: int
    engine: str

    def validate_package(self) -> None: ...

    def layout_issues(self, translations: dict[str, str]) -> list[Issue]: ...

    def write(self, translations: dict[str, str], output: Path, *,
              decimal_comma_to_point: bool = False) -> None: ...


def segment_plain_text(segment: Segment, translated: str | None = None) -> str:
    """Display caption line breaks while leaving canonical markup untouched."""
    if segment.id.startswith(("subtitle-v11:", "subtitle-v12:")):
        from forma.documents.subtitles import caption_plain_text

        return caption_plain_text(segment, translated)
    return plain_text(segment.source if translated is None else translated)


def open_document(path: Path, *, extraction_profile: int | None = None,
                  check_cancel: Callable[[], None] = lambda: None) -> DocumentAdapter:
    path = Path(path)
    capability = get_format(path)
    if capability is None or not capability.manual_export:
        raise FormaError("Непідтримуваний формат документа. Виберіть файл зі списку підтримуваних форматів.")
    if capability.office:
        from forma.documents.ooxml import OfficeDocument

        adapter = OfficeDocument
    elif capability.engine == "forma-odf":
        from forma.documents.odf import OpenDocument

        adapter = OpenDocument
    elif capability.engine == "forma-epub":
        from forma.documents.epub import EpubDocument

        adapter = EpubDocument
    elif capability.extraction_profiles == (5,):
        from forma.documents.text import TextDocument

        adapter = TextDocument
    elif capability.suffix in {".md", ".markdown"}:
        from forma.documents.markdown import MarkdownDocument

        adapter = MarkdownDocument
    elif capability.extraction_profiles == (7,):
        from forma.documents.html import HtmlDocument

        adapter = HtmlDocument
    elif capability.extraction_profiles == (11,):
        from forma.documents.srt import SrtDocument

        adapter = SrtDocument
    elif capability.extraction_profiles == (12,):
        from forma.documents.vtt import VttDocument

        adapter = VttDocument
    else:
        raise FormaError("Непідтримуваний формат документа. Виберіть файл зі списку підтримуваних форматів.")
    return adapter(path, extraction_profile=extraction_profile, check_cancel=check_cancel)
