"""Source-bound subtitle cues; metadata, clocks and inline anchors stay immutable."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape

from forma.core.models import FormaError, Issue, Segment, plain_text
from forma.documents.text import MAX_TEXT_BYTES, MAX_TEXT_CHARACTERS, SourceDocument
from forma.persistence.files import atomic_bytes
from forma.translation.quality import parse_translation

MAX_CUES = 100000
MAX_CUE_CHARACTERS = 20000
MAX_CUE_SOURCE_CHARACTERS = 65536
MAX_CUE_SLOTS = 256
MAX_TAG_DEPTH = 128
MAX_SUBTITLE_LINES = 500000
_LINE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|$)")
_ALIGNMENT = re.compile(r"\{\\(?:an[1-9]|a(?:[1-9]|10|11))\}")
_INJECTION = "Переклад субтитрів містить недопустимі переноси рядків, керівні символи або стрілку таймінгу."
_PLAN = re.compile(r"subtitle-v(?:11|12):[0-9]+:l([0-9a-f]{1,64})\.([0-9a-f]{1,64})(?:\.([0-9a-f]{1,64})\.([0-9a-f]{1,64}))?")


@dataclass(frozen=True)
class Cue:
    index: int
    start: int
    end: int
    start_ms: int
    end_ms: int
    payload_start: int
    payload_end: int
    identifier: str = ""


@dataclass(frozen=True)
class CaptionSlot:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class CaptionPayload:
    slots: tuple[CaptionSlot, ...]
    events: tuple[tuple[str, int, str], ...]
    supported: bool = True

    @property
    def visible(self):
        return "".join(value for _, _, value in self.events)


def source_lines(text, check_cancel=lambda: None):
    """Exact physical CR/LF lines: Unicode caption characters are not separators."""
    lines = []
    for match in _LINE.finditer(text):
        if not len(lines) % 1024:
            check_cancel()
        if match.end() > match.start():
            lines.append((match.start(), match.end(), match.group(1), match.group(2)))
            if len(lines) > MAX_SUBTITLE_LINES:
                raise FormaError("Файл субтитрів містить забагато фізичних рядків. Розділіть його на менші файли.")
    return lines


def _unsafe_text(value):
    return "-->" in value or any(ord(char) < 32 or 0x7F <= ord(char) < 0xA0 or char in "\u2028\u2029" for char in value)


def _bounded_keys(values):
    """Reports are untrusted: validate tiny anchor indices before integer shifts."""
    if len(values) > MAX_CUE_SLOTS * 2 - 1:
        return False
    markers = 0
    for key in values:
        suffix = key[1:]
        if len(suffix) > 3 or not suffix.isascii() or not suffix.isdigit():
            return False
        try:
            if int(suffix) >= MAX_CUE_SLOTS:
                return False
        except ValueError:
            return False
        markers += key.startswith("k")
    return markers < MAX_CUE_SLOTS


class _SrtTag(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.events = []
        self.feed(raw)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.events.append((tag, False, tag == "br", attrs))

    def handle_startendtag(self, tag, attrs):
        self.events.append((tag, False, True, attrs))

    def handle_endtag(self, tag):
        self.events.append((tag, True, False, []))

    def handle_data(self, data):
        if data:
            self.events.append(None)

    def handle_comment(self, data):
        self.events.append(None)


def caption_payload(text, cue, *, tag_validator=None, format="srt"):
    """Parse immutable anchors and decoded text runs at absolute source offsets.

    A custom validator lets WebVTT supply its own tag/stack/timestamp grammar.
    Unknown SRT extensions preserve the whole cue rather than exposing metadata.
    """
    raw = text[cue.payload_start:cue.payload_end]
    if len(raw) > MAX_CUE_SOURCE_CHARACTERS:
        raise FormaError("Репліка субтитрів завелика або містить забагато розмітки.")
    slots, events, stack = [], [], []
    pos, run_start, markers = 0, 0, 0

    def run(end):
        try:
            value = html.unescape(raw[run_start:end])
        except ValueError:
            return False
        if _unsafe_text(value):
            return False
        slots.append(CaptionSlot(cue.payload_start + run_start, cue.payload_start + end, value))
        events.append(("r", len(slots) - 1, value))
        if len(slots) > MAX_CUE_SLOTS:
            raise FormaError("Репліка субтитрів завелика або містить забагато розмітки.")
        return True

    while pos < len(raw):
        anchor_end, visible = None, ""
        if raw[pos] in "\r\n\t":
            anchor_end = pos + (2 if raw.startswith("\r\n", pos) else 1)
            visible = "\n" if raw[pos] in "\r\n" else "\t"
        elif format == "srt" and raw.startswith("{\\", pos):
            match = _ALIGNMENT.match(raw, pos)
            if match is None:
                return CaptionPayload((), (), False)
            anchor_end = match.end()
        elif raw[pos] == "<" and pos + 1 < len(raw) and (raw[pos + 1].isalnum() or raw[pos + 1] in "/!?"):
            end, quote = pos + 1, ""
            while end < len(raw):
                char = raw[end]
                if quote:
                    if char == quote:
                        quote = ""
                elif format != "vtt" and char in "\"'":
                    quote = char
                elif char == ">":
                    break
                end += 1
                if end - pos > 4096:
                    return CaptionPayload((), (), False)
            if end >= len(raw):
                return CaptionPayload((), (), False)
            tag = raw[pos:end + 1]
            if tag_validator is not None:
                if not tag_validator(tag, cue):
                    return CaptionPayload((), (), False)
            else:
                parsed = _SrtTag(tag).events
                if len(parsed) != 1 or parsed[0] is None:
                    return CaptionPayload((), (), False)
                name, closing, void, attrs = parsed[0]
                if closing and re.fullmatch(r"</\s*[A-Za-z][A-Za-z0-9]*\s*>", tag) is None:
                    return CaptionPayload((), (), False)
                if name not in {"b", "i", "u", "s", "font", "br"} or len(dict(attrs)) != len(attrs):
                    return CaptionPayload((), (), False)
                if attrs and (name != "font" or any(key not in {"color", "face", "size"} or value is None for key, value in attrs)):
                    return CaptionPayload((), (), False)
                if name == "br":
                    if closing:
                        return CaptionPayload((), (), False)
                    visible = "\n"
                elif void:
                    return CaptionPayload((), (), False)
                elif closing:
                    if not stack or stack.pop() != name:
                        return CaptionPayload((), (), False)
                else:
                    stack.append(name)
                    if len(stack) > MAX_TAG_DEPTH:
                        raise FormaError("Репліка субтитрів завелика або містить забагато розмітки.")
            anchor_end = end + 1
        if anchor_end is None:
            pos += 1
            continue
        if not run(pos):
            return CaptionPayload((), (), False)
        events.append(("k", markers, visible))
        markers += 1
        pos = run_start = anchor_end
    if stack or not run(len(raw)):
        return CaptionPayload((), (), False)
    if sum(len(value) for _, _, value in events) > MAX_CUE_CHARACTERS:
        raise FormaError("Текст репліки завеликий. Ліміт — 20 000 символів.")
    return CaptionPayload(tuple(slots), tuple(events))


def candidate_issues(segment, translated):
    """Reject subtitle injection during normal validation/retry/recovery."""
    if not segment.id.startswith(("subtitle-v11:", "subtitle-v12:")):
        return []
    try:
        values = parse_translation(segment.source, translated)
    except (FormaError, UnicodeError) as exc:
        return [Issue("formatting", str(exc) if isinstance(exc, FormaError) else _INJECTION, segment.id, "error")]
    if not _bounded_keys(values):
        return [Issue("formatting", "Некоректна структура або таймінг субтитрів. Перевірте файл перед перекладом.", segment.id, "error")]
    runs = [value for key, value in values.items() if key.startswith("r")]
    if any(_unsafe_text(value) for value in runs):
        return [Issue("formatting", _INJECTION, segment.id, "error")]
    if sum(len(value) for value in runs) > MAX_CUE_CHARACTERS:
        return [Issue("formatting", "Текст репліки завеликий. Ліміт — 20 000 символів.", segment.id, "error")]
    source = parse_translation(segment.source, segment.source)
    if any(key.startswith("r") and not original.strip() and values[key] != original
           for key, original in source.items()):
        return [Issue("formatting", "Переклад змінив захищену структуру субтитрів. Перевірте розмітку й переноси рядків.", segment.id, "error")]
    if "::fragment:" not in segment.id:
        if any(char.isalpha() for key, value in source.items() if key.startswith("r") for char in value) and not any(char.isalpha() for value in runs for char in value):
            return [Issue("formatting", "Переклад втратив текст: залишилися лише числа, символи або захищені значення.", segment.id, "error")]
        plan = _PLAN.fullmatch(segment.id)
        if plan is not None:
            breaks, protected = (int(value, 16) for value in plan.groups()[:2])
            line, text = 0, ""
            for key, value in values.items():
                if key.startswith("r"):
                    text += value
                elif breaks & (1 << int(key[1:])):
                    if not text.strip(" \t") and not protected & (1 << line):
                        return [Issue("formatting", "Переклад створив порожній рядок усередині репліки субтитрів. Збережіть текст початкових рядків.", segment.id, "error")]
                    line, text = line + 1, ""
            if not text.strip(" \t") and not protected & (1 << line):
                return [Issue("formatting", "Переклад створив порожній рядок усередині репліки субтитрів. Збережіть текст початкових рядків.", segment.id, "error")]
    return []


def caption_plain_text(segment, translated=None):
    """Render actual caption text from persisted, bounded anchor-kind masks."""
    markup = segment.source if translated is None else translated
    if not isinstance(markup, str):
        return ""
    plan = _PLAN.fullmatch(segment.id.split("::fragment:", 1)[0])
    if plan is None:
        return plain_text(markup)
    try:
        values = parse_translation(segment.source, markup)
    except (FormaError, UnicodeError):
        return plain_text(markup)
    if not _bounded_keys(values):
        return plain_text(markup)
    breaks = int(plan.group(3) or plan.group(1), 16)
    tabs = int(plan.group(4) or "0", 16)
    pieces = []
    for key, value in values.items():
        if key.startswith("r"):
            pieces.append(value)
        elif breaks & (1 << int(key[1:])):
            pieces.append("\n")
        elif tabs & (1 << int(key[1:])):
            pieces.append("\t")
    return "".join(pieces)


def _line_plan(text, payload):
    """Only raw physical newlines delimit cues; preserved tags keep lines nonblank."""
    breaks, protected, line = 0, 0, 0
    line_start = payload.slots[0].start if payload.slots else 0

    def original_whitespace_line(end):
        raw_line = text[line_start:end]
        return bool(raw_line and not html.unescape(raw_line).strip())

    for index in range(len(payload.slots) - 1):
        raw = text[payload.slots[index].end:payload.slots[index + 1].start]
        if raw in {"\r\n", "\r", "\n"}:
            if original_whitespace_line(payload.slots[index].end):
                protected |= 1 << line
            breaks |= 1 << index
            line += 1
            line_start = payload.slots[index + 1].start
        elif raw.strip(" \t"):
            protected |= 1 << line
    # VTT only treats zero-length physical lines as separators. An original
    # space/TAB/entity-only line stays nonempty because its runs are pinned.
    if payload.slots and original_whitespace_line(payload.slots[-1].end):
        protected |= 1 << line
    visible_breaks, tabs = 0, 0
    for kind, index, visible in payload.events:
        if kind == "k" and visible == "\n":
            visible_breaks |= 1 << index
        elif kind == "k" and visible == "\t":
            tabs |= 1 << index
    return f"{breaks:x}.{protected:x}.{visible_breaks:x}.{tabs:x}"


class SubtitleDocument(SourceDocument):
    layout_issue_codes = frozenset({"subtitle_line_length", "subtitle_line_count", "subtitle_reading_speed"})

    def __init__(self, path, *, extraction_profile=None, check_cancel=lambda: None):
        super().__init__(path, extraction_profile=extraction_profile, check_cancel=check_cancel)
        self.cues = tuple(cue for cue, _ in self._analyze(self.text)[0])
        self.caption_targets = {}
        analysis = self._analyze(self.text)[0]
        visible = [payload.visible if payload.supported else "" for _, payload in analysis]
        for index, (cue, payload) in enumerate(analysis):
            self.check_cancel()
            if not payload.supported or not any(char.isalpha() for slot in payload.slots for char in slot.text):
                continue
            sid = f"subtitle-v{self.extraction_profile}:{cue.index}:l{_line_plan(self.text, payload)}"
            markup = "".join(f"<r{number}>{escape(value)}</r{number}>" if kind == "r" else f"<k{number}/>"
                             for kind, number, value in payload.events)
            context = "\n".join(value[:500] for value in visible[max(0, index - 1):index + 2] if value)[:1800]
            self.segments.append(Segment(sid, f"Cue {cue.index}", markup, context))
            self.caption_targets[sid] = cue, payload

    def _validate_encoding(self):
        pass

    def _extract_spans(self, text):
        self._validate_encoding()
        return []

    def _parse_cues(self, text):
        raise NotImplementedError

    def _payload(self, text, cue):
        return caption_payload(text, cue)

    def _analyze(self, text):
        if getattr(self, "_subtitle_cached_text", None) == text:
            return self._subtitle_cached_analysis
        cues = self._parse_cues(text)
        if len(cues) > MAX_CUES:
            raise FormaError("Файл містить забагато реплік субтитрів. Розділіть його на менші файли.")
        analysis, gaps, cursor, previous_end = [], [], 0, 0
        for cue in cues:
            self.check_cancel()
            if not previous_end <= cue.start <= cue.payload_start <= cue.payload_end <= cue.end <= len(text) or cue.end_ms <= cue.start_ms:
                raise FormaError("Некоректна структура або таймінг субтитрів. Перевірте файл перед перекладом.")
            previous_end = cue.end
            payload = self._payload(text, cue)
            analysis.append((cue, payload))
            if payload.supported and any(char.isalpha() for slot in payload.slots for char in slot.text):
                for slot in payload.slots:
                    gaps.append(text[cursor:slot.start])
                    cursor = slot.end
            elif not payload.supported and not any(issue.code == "subtitle_unsupported_markup" for issue in self.issues):
                self.issues.append(Issue("subtitle_unsupported_markup", "Репліки з непідтримуваною розміткою субтитрів залишено без перекладу.", severity="info"))
        gaps.append(text[cursor:])
        self._subtitle_cached_text, self._subtitle_cached_analysis = text, (analysis, tuple(gaps))
        return self._subtitle_cached_analysis

    def _structure_for_text(self, text):
        return self._analyze(text)[1]

    def comparison_text(self):
        return "\n".join(payload.visible for _, payload in self._analyze(self.text)[0] if payload.supported)

    def _visible_translation(self, segment, translated):
        _, payload = self.caption_targets[segment.id]
        values = parse_translation(segment.source, translated)
        return "".join(values[f"r{number}"] if kind == "r" else value for kind, number, value in payload.events)

    def layout_issues(self, translations):
        issues = []
        for segment in self.segments:
            self.check_cancel()
            cue, _ = self.caption_targets[segment.id]
            value = self._visible_translation(segment, translations.get(segment.id, segment.source))
            lines = value.split("\n")
            if any(len(line.expandtabs(4)) > 42 for line in lines):
                issues.append(Issue("subtitle_line_length", "Рядок субтитрів може бути задовгим (орієнтир — 42 символи). Перевірте його у відео.", segment.id))
            if len(lines) > 2:
                issues.append(Issue("subtitle_line_count", "Репліка містить понад два рядки. Перевірте розміщення субтитрів у відео.", segment.id))
            if len(value.replace("\n", "")) / ((cue.end_ms - cue.start_ms) / 1000) > 20:
                issues.append(Issue("subtitle_reading_speed", "Репліка може читатися надто швидко (орієнтир — 20 символів за секунду). Це приблизна оцінка.", segment.id))
        return issues

    def write(self, translations, output: Path, *, decimal_comma_to_point=False):
        self.check_cancel()
        output = Path(output)
        if output.resolve() == self.path.resolve():
            raise FormaError("Результат має зберігатися в окремий файл.")
        if set(translations) != set(self.caption_targets):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        replacements = []
        for segment in self.segments:
            issues = candidate_issues(segment, translations[segment.id])
            if issues:
                raise FormaError(issues[0].message)
            values = parse_translation(segment.source, translations[segment.id])
            _, payload = self.caption_targets[segment.id]
            for index, slot in enumerate(payload.slots):
                value = values[f"r{index}"]
                replacement = self.text[slot.start:slot.end] if value == slot.text else html.escape(value, quote=False)
                replacements.append((slot.start, slot.end, replacement))
        cursor, pieces, size = 0, [], 0
        for start, end, replacement in replacements:
            self.check_cancel()
            prefix = self.text[cursor:start]
            size += len(prefix) + len(replacement)
            if size > MAX_TEXT_CHARACTERS:
                raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
            pieces.extend((prefix, replacement))
            cursor = end
        pieces.append(self.text[cursor:])
        candidate = "".join(pieces)
        if len(candidate) > MAX_TEXT_CHARACTERS:
            raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
        try:
            encoded = self.bom + candidate.encode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise FormaError("Переклад містить некоректні символи Unicode.") from exc
        if len(encoded) > MAX_TEXT_BYTES:
            raise FormaError("Текстовий файл завеликий. Ліміт — 16 МБ.")
        if self._structure_for_text(candidate) != self._initial_structure:
            raise FormaError("Переклад змінив захищену структуру субтитрів. Перевірте розмітку й переноси рядків.")
        self.validate_package()
        self.check_cancel()
        atomic_bytes(output, encoded, overwrite=False)
