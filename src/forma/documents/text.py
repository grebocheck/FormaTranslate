"""Source-bound text spans with explicit Unicode encodings and lossless splicing."""

from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from forma.core.models import FormaError, Segment
from forma.persistence.files import atomic_bytes
from forma.translation.quality import parse_translation

MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARACTERS = 4_000_000
MAX_SPAN_CHARACTERS = 20000
MAX_SPANS = 100000
_NEWLINES = re.compile(r"\r\n|\r|\n")


def normalized_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class EditableSpan:
    start: int
    end: int
    location: str = ""
    context: str = ""
    text: str | None = None


def subtract_ranges(start: int, end: int, protected):
    """Yield the pieces of an interval outside sorted protected intervals."""
    cursor = start
    for left, right in protected:
        if right <= cursor:
            continue
        if left >= end:
            break
        if cursor < left:
            yield cursor, min(left, end)
        cursor = max(cursor, right)
        if cursor >= end:
            return
    if cursor < end:
        yield cursor, end


class SourceDocument:
    extraction_profile = 5
    engine = "forma-text"

    def __init__(self, path: Path, *, extraction_profile=None, check_cancel=lambda: None):
        if extraction_profile is not None and (type(extraction_profile) is not int or extraction_profile != self.extraction_profile):
            raise ValueError("Unsupported text extraction profile")
        self.path = Path(path)
        self.check_cancel = check_cancel
        self.raw = self._read()
        self.source_sha256 = hashlib.sha256(self.raw).hexdigest()
        self.bom, self.encoding = b"", "utf-8"
        for marker, encoding in ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
                                 (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                                 (codecs.BOM_UTF16_BE, "utf-16-be")):
            if self.raw.startswith(marker):
                self.bom, self.encoding = marker, encoding
                break
        try:
            self.text = self.raw[len(self.bom):].decode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise FormaError("Не вдалося прочитати кодування текстового файла. Збережіть його як UTF-8 або Unicode з BOM.") from exc
        if len(self.text) > MAX_TEXT_CHARACTERS:
            raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
        if any(ord(char) < 32 and char not in "\t\n\r\f" or 0x7F <= ord(char) < 0xA0 for char in self.text):
            raise FormaError("Файл містить двійкові або недопустимі керівні дані. Збережіть текстову копію як UTF-8.")
        self.issues = []
        self.segments = []
        self.spans = {}
        for index, span in enumerate(self._checked_spans(self.text)):
            self.check_cancel()
            value = self._visible(span, self.text)
            if not any(char.isalpha() for char in value):
                continue
            if len(value) > MAX_SPAN_CHARACTERS:
                raise FormaError("Текстовий фрагмент завеликий. Розділіть рядок або абзац на частини до 20 000 символів.")
            sid = f"text-v{self.extraction_profile}:{index}"
            self.spans[sid] = span
            self.segments.append(Segment(sid, span.location or f"Fragment {index + 1}",
                                         "<r0>" + escape(value) + "</r0>", span.context[:4000]))
        self._initial_structure = self._structure_for_text(self.text)
        self.check_cancel()

    def _read(self):
        chunks, size = [], 0
        with self.path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                self.check_cancel()
                size += len(chunk)
                if size > MAX_TEXT_BYTES:
                    raise FormaError("Текстовий файл завеликий. Ліміт — 16 МБ.")
                chunks.append(chunk)
        return b"".join(chunks)

    def _extract_spans(self, text: str) -> list[EditableSpan]:
        raise NotImplementedError

    @staticmethod
    def _visible(span, text):
        return normalized_newlines(text[span.start:span.end] if span.text is None else span.text)

    def _checked_spans(self, text):
        spans = sorted(self._extract_spans(text), key=lambda span: (span.start, span.end))
        if len(spans) > MAX_SPANS:
            raise FormaError("Текстовий документ містить забагато фрагментів. Розділіть його на менші файли.")
        previous = 0
        for span in spans:
            if type(span.start) is not int or type(span.end) is not int or not previous <= span.start < span.end <= len(text):
                raise FormaError("Не вдалося безпечно зіставити текст зі структурою документа.")
            previous = span.end
        return spans

    def _structure_for_text(self, text):
        spans = [span for span in self._checked_spans(text) if any(char.isalpha() for char in self._visible(span, text))]
        cursor, gaps = 0, []
        for span in spans:
            gaps.append(text[cursor:span.start])
            cursor = span.end
        gaps.append(text[cursor:])
        return tuple(gaps)

    def structure_signature(self):
        return self.extraction_profile, self.encoding, self.bom, self._initial_structure

    def comparison_text(self):
        """Include visible numeric-only spans even though they need no translation."""
        return "\n".join(self._visible(span, self.text) for span in self._checked_spans(self.text))

    def validate_package(self):
        """Reject source changes before a paid request or publishing a result."""
        if hashlib.sha256(self._read()).hexdigest() != self.source_sha256:
            raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")

    def layout_issues(self, translations):
        return []

    def _encode_translation(self, value):
        return value

    def write(self, translations, output: Path, *, decimal_comma_to_point=False):
        self.check_cancel()
        output = Path(output)
        if output.resolve() == self.path.resolve():
            raise FormaError("Результат має зберігатися в окремий файл.")
        if set(translations) != set(self.spans):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        cursor, pieces = 0, []
        for segment in self.segments:
            self.check_cancel()
            span = self.spans[segment.id]
            try:
                value = parse_translation(segment.source, translations[segment.id])["r0"]
            except UnicodeError as exc:
                raise FormaError("Переклад містить некоректні символи Unicode.") from exc
            original = self._visible(span, self.text)
            if any(char.isalpha() for char in original) and not any(char.isalpha() for char in value):
                raise FormaError("Переклад втратив текст: залишилися лише числа, символи або захищені значення.")
            pieces.append(self.text[cursor:span.start])
            if value == original:
                pieces.append(self.text[span.start:span.end])
            else:
                if value.count("\n") != original.count("\n") or "\r" in value:
                    raise FormaError("Переклад змінив переноси рядків текстового фрагмента. Збережіть початкову структуру.")
                replacement = self._encode_translation(value)
                endings = _NEWLINES.findall(self.text[span.start:span.end])
                if endings and len(endings) == replacement.count("\n"):
                    iterator = iter(endings)
                    replacement = re.sub("\n", lambda _, endings=iterator: next(endings), replacement)
                pieces.append(replacement)
            cursor = span.end
        pieces.append(self.text[cursor:])
        candidate = "".join(pieces)
        if len(candidate) > MAX_TEXT_CHARACTERS:
            raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
        if any(ord(char) < 32 and char not in "\t\n\r\f" or 0x7F <= ord(char) < 0xA0 for char in candidate):
            raise FormaError("Переклад містить недопустимі керівні символи.")
        try:
            encoded = self.bom + candidate.encode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise FormaError("Переклад містить некоректні символи Unicode.") from exc
        if len(encoded) > MAX_TEXT_BYTES:
            raise FormaError("Текстовий файл завеликий. Ліміт — 16 МБ.")
        if self._structure_for_text(candidate) != self._initial_structure:
            raise FormaError("Переклад змінив захищену структуру текстового документа. Перевірте форматування.")
        self.validate_package()
        self.check_cancel()
        atomic_bytes(output, encoded, overwrite=False)


class TextDocument(SourceDocument):
    """Each nonempty prose line is editable; every separator stays immutable."""

    def _extract_spans(self, text):
        result, offset = [], 0
        lines = text.splitlines(keepends=True)
        for line_number, line in enumerate(lines, 1):
            self.check_cancel()
            body = line.rstrip("\r\n\f")
            start = len(body) - len(body.lstrip(" \t"))
            end = len(body.rstrip(" \t"))
            if start < end:
                context = "\n".join(value.rstrip("\r\n")[:700] for value in lines[max(0, line_number - 2):line_number + 1])
                result.append(EditableSpan(offset + start, offset + end,
                                           f"Line {line_number} · fragment 1", context))
            offset += len(line)
        return result
