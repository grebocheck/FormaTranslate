from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

INFORMATION_CODES = {
    "fields_preserved",
    "external_engine",
    "pdf_visual_review",
    "shared_memory",
    "tracked_changes",
}

ISSUE_EXPLANATIONS = {
    "tracked_changes": "У документі є історія виправлень Word (Track Changes). Перекладено поточний видимий текст; фрагменти, позначені як видалені, збережено мовою оригіналу. Це не помилка перекладу. Якщо відновите видалений текст у Word, його потрібно буде перекласти окремо. Перед остаточним надсиланням документа перевірте ці виправлення у Word. Позначка «Це коректно» у Forma лише підтверджує перегляд цієї примітки й не приймає та не відхиляє виправлення Word.",
}


class FormaError(Exception):
    """An error safe to display in the interface."""


MAX_TRANSLATION_INSTRUCTIONS = 8000


def validate_instructions(value: str) -> None:
    if not isinstance(value, str) or len(value) > MAX_TRANSLATION_INSTRUCTIONS or "\x00" in value:
        raise FormaError("Власні інструкції: дозволено до 8 000 символів без нульових байтів.")


class Cancelled(FormaError):
    pass


class ValidationError(FormaError):
    """A translation response that can be repaired by asking the model again."""


@dataclass
class Segment:
    id: str
    location: str
    source: str
    context: str = ""

    @property
    def plain(self) -> str:
        return plain_text(self.source)


def plain_text(markup: str) -> str:
    with_breaks = re.sub(r"<k\d+\s*/>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", with_breaks))


@dataclass
class Issue:
    code: str
    message: str
    segment_id: str = ""
    severity: str = "warning"


@dataclass
class Options:
    source_language: str = "auto"
    target_language: str = "uk"
    domain: str = "Загальна тематика"
    review: bool = True
    auto_glossary: bool = True
    glossary: dict[str, str] = field(default_factory=dict)
    instructions: str = ""

    def validate(self) -> None:
        validate_instructions(self.instructions)
        pattern = r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*"
        if self.source_language != "auto" and not re.fullmatch(pattern, self.source_language):
            raise FormaError("Вкажіть код мови оригіналу, наприклад en або auto.")
        if not re.fullmatch(pattern, self.target_language):
            raise FormaError("Вкажіть код мови перекладу, наприклад uk або en.")


@dataclass
class TranslationResult:
    source: Path
    output: Path | None
    segments: list[Segment]
    translations: dict[str, str]
    issues: list[Issue]
    input_tokens: int = 0
    output_tokens: int = 0
    cached_segments: int = 0
    engine: str = "forma-ooxml"
    elapsed_seconds: float = 0
    report: Path | None = None
    usage_note: str = ""

    def serializable(self) -> dict:
        return {
            "source": str(self.source),
            "output": str(self.output) if self.output else None,
            "engine": self.engine,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_segments": self.cached_segments,
            "usage_note": self.usage_note,
            "issues": [asdict(issue) for issue in self.issues],
            "segments": [
                {**asdict(s), "translation": self.translations.get(s.id, "")} for s in self.segments
            ],
        }
