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


class TranslationFailure(FormaError):
    """Validated paid work is preserved; unresolved fragments need inspection."""

    def __init__(self, message, segments, candidates, issues, *, stage):
        super().__init__(message)
        self.segments = list(segments)
        self.candidates = dict(candidates)
        self.issues = list(issues)
        self.stage = stage
        self.report = None


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
    memory_project_id: str = ""

    def validate(self) -> None:
        validate_instructions(self.instructions)
        pattern = r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*"
        if self.source_language != "auto" and not re.fullmatch(pattern, self.source_language):
            raise FormaError("Вкажіть код мови оригіналу, наприклад en або auto.")
        if not re.fullmatch(pattern, self.target_language):
            raise FormaError("Вкажіть код мови перекладу, наприклад uk або en.")
        if not isinstance(self.memory_project_id, str) or (
            self.memory_project_id and not re.fullmatch(r"[0-9a-f]{32}", self.memory_project_id)
        ):
            raise FormaError("Виберіть коректний проєкт пам’яті перекладів.")
        if self.memory_project_id and self.source_language == "auto":
            raise FormaError("Для пам’яті проєкту виберіть явну мову оригіналу.")


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
    memory_project_id: str = ""
    memory_segments: int = 0
    repeated_segments: int = 0
    draft_memory_segments: int = 0
    adapted_segments: int = 0
    memory_revision: int = 0
    memory_references: dict[str, dict] = field(default_factory=dict)
    effective_glossary: dict[str, str] | None = None
    source_sha256: str = ""
    output_sha256: str = ""
    preparation: dict = field(default_factory=dict)

    def serializable(self) -> dict:
        result = {
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
        if self.effective_glossary is not None:
            result["effective_glossary"] = dict(self.effective_glossary)
        if self.source_sha256:
            result["source_sha256"] = self.source_sha256
        if self.output_sha256:
            result["output_sha256"] = self.output_sha256
        if self.preparation:
            result["preparation"] = dict(self.preparation)
        if self.memory_project_id:
            result.update(
                memory_project_id=self.memory_project_id,
                memory_segments=self.memory_segments,
                repeated_segments=self.repeated_segments,
                draft_memory_segments=self.draft_memory_segments,
                adapted_segments=self.adapted_segments,
                memory_revision=self.memory_revision,
                memory_references=self.memory_references,
            )
        return result
