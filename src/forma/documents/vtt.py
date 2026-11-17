"""Static WebVTT captions with immutable headers, clocks, settings and markup."""
from __future__ import annotations

import html
import re
from decimal import Decimal

from forma.core.models import FormaError, Issue
from forma.documents.subtitles import (
    MAX_CUE_SOURCE_CHARACTERS,
    MAX_CUES,
    MAX_TAG_DEPTH,
    Cue,
    SubtitleDocument,
    caption_payload,
    source_lines,
)

_SYNTAX = "Некоректна структура WebVTT. Перевірте заголовок, блоки та ідентифікатори реплік."
_TIMING_ERROR = "Некоректний таймінг WebVTT. Репліки мають бути впорядковані за початком, а кінець — пізнішим за початок."
_SETTINGS = "Некоректні або повторені параметри репліки чи регіону WebVTT."
_MARKUP = "Некоректна розмітка або внутрішні часові позначки репліки WebVTT."
_STAMP = r"(?:[0-9]{2,9}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"
_TIMING = re.compile(rf"(?P<start>{_STAMP})[ \t]+-->[ \t]+(?P<end>{_STAMP})(?:[ \t]+(?P<settings>[^\r\n]*))?")
_START_TAG = re.compile(r"(?P<tag>c|i|b|u|ruby|rt|v|lang)(?:\.[^ \t\r\n&<>.]+)*(?:[ \t]+(?P<annotation>[^>\r\n]+))?")
_END_TAG = re.compile(r"</(c|i|b|u|ruby|rt|v|lang)>")
_PERCENT = re.compile(r"[0-9]+(?:\.[0-9]+)?%")
_SETTING_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
_LANGUAGE = re.compile(r"(?:[A-Za-z]{2,8}|[ixIX])(?:-[A-Za-z0-9]{1,8})*")


def _timestamp(value):
    if re.fullmatch(_STAMP, value) is None:
        raise FormaError(_TIMING_ERROR)
    parts = value.split(":")
    hours = int(parts[0]) if len(parts) == 3 else 0
    minutes = int(parts[-2])
    seconds, milliseconds = parts[-1].split(".")
    return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _percentage(value):
    return bool(_PERCENT.fullmatch(value) and 0 <= Decimal(value[:-1]) <= 100)


def _settings(value, *, region=False):
    result = {}
    for token in re.split(r"[ \t]+", value.strip(" \t")) if value.strip(" \t") else ():
        key, separator, content = token.partition(":")
        if not separator or not _SETTING_NAME.fullmatch(key) or not content or key in result or "-->" in content:
            raise FormaError(_SETTINGS)
        result[key] = content
        valid = True
        if region:
            if key == "width":
                valid = _percentage(content)
            elif key == "lines":
                valid = bool(re.fullmatch(r"[0-9]+", content))
            elif key in {"regionanchor", "viewportanchor"}:
                pair = content.split(",")
                valid = len(pair) == 2 and all(_percentage(item) for item in pair)
            elif key == "scroll":
                valid = content == "up"
        elif key == "vertical":
            valid = content in {"rl", "lr"}
        elif key in {"line", "position"}:
            offset, comma, alignment = content.partition(",")
            valid = _percentage(offset) or (key == "line" and bool(re.fullmatch(r"-?[0-9]+", offset)))
            if comma:
                valid &= alignment in ({"start", "center", "end"} if key == "line" else {"line-left", "center", "line-right"})
        elif key == "size":
            valid = _percentage(content)
        elif key == "align":
            valid = content in {"start", "center", "end", "left", "right"}
        if not valid:
            raise FormaError(_SETTINGS)
    return result


def _decode(value):
    try:
        return html.unescape(value)
    except ValueError as exc:
        raise FormaError(_MARKUP) from exc


def _validate_payload(text, cue, check_cancel):
    raw = text[cue.payload_start:cue.payload_end]
    if len(raw) > MAX_CUE_SOURCE_CHARACTERS:
        raise FormaError("Репліка субтитрів завелика або містить забагато розмітки.")
    if "-->" in raw:
        raise FormaError(_SYNTAX)
    stack, ruby_readings, pending_ruby_bases, position, previous_timestamp = [], {}, set(), 0, cue.start_ms

    def base_content(value=True):
        if not value:
            return
        for name, offset in reversed(stack):
            if name == "rt":
                return
            if name == "ruby":
                if ruby_readings.get(offset):
                    pending_ruby_bases.add(offset)
                return

    while position < len(raw):
        check_cancel()
        next_tag = raw.find("<", position)
        if next_tag < 0:
            base_content(_decode(raw[position:]).strip(" \t\r\n"))
            break
        base_content(_decode(raw[position:next_tag]).strip(" \t\r\n"))
        end = raw.find(">", next_tag + 1)
        if end < 0 or end - next_tag > 4096:
            raise FormaError(_MARKUP)
        token = raw[next_tag:end + 1]
        inside = token[1:-1]
        if inside[:1].isascii() and inside[:1].isdigit():
            try:
                timestamp = _timestamp(inside)
            except FormaError:
                raise FormaError(_MARKUP) from None
            if not previous_timestamp < timestamp < cue.end_ms:
                raise FormaError(_MARKUP)
            previous_timestamp = timestamp
            base_content()
        elif token.startswith("</"):
            match = _END_TAG.fullmatch(token)
            if match is None:
                raise FormaError(_MARKUP)
            name = match.group(1)
            if name == "ruby" and stack and stack[-1][0] == "rt":
                stack.pop()  # The final ruby text end tag may be omitted.
            if not stack or stack[-1][0] != name:
                raise FormaError(_MARKUP)
            frame = stack.pop()
            if name == "ruby" and (not ruby_readings.get(frame[1]) or frame[1] in pending_ruby_bases):
                raise FormaError(_MARKUP)
        else:
            match = _START_TAG.fullmatch(inside)
            if match is None:
                raise FormaError(_MARKUP)
            name, annotation = match.group("tag", "annotation")
            if name in {"v", "lang"}:
                if annotation is None or not _decode(annotation).strip(" \t"):
                    raise FormaError(_MARKUP)
                if name == "lang" and not _LANGUAGE.fullmatch(_decode(annotation).strip(" \t")):
                    raise FormaError(_MARKUP)
            elif annotation is not None:
                raise FormaError(_MARKUP)
            if name == "rt":
                if not stack or stack[-1][0] != "ruby":
                    raise FormaError(_MARKUP)
                ruby_readings[stack[-1][1]] = True
                pending_ruby_bases.discard(stack[-1][1])
            else:
                base_content()
            stack.append((name, next_tag))
            if len(stack) > MAX_TAG_DEPTH:
                raise FormaError(_MARKUP)
        position = end + 1
    if stack and not (len(stack) == 1 and stack[0][0] == "v" and not raw[:stack[0][1]].strip(" \t\r\n")):
        raise FormaError(_MARKUP)


class VttDocument(SubtitleDocument):
    extraction_profile = 12
    engine = "forma-text"

    def _validate_encoding(self):
        if self.encoding != "utf-8":
            raise FormaError("WebVTT потребує кодування UTF-8 (з BOM або без нього).")

    def _parse_cues(self, text):
        if "\f" in text:
            raise FormaError(_SYNTAX)
        lines = source_lines(text, self.check_cancel)
        if not lines or re.fullmatch(r"WEBVTT(?:[ \t].*)?", lines[0][2]) is None:
            raise FormaError(_SYNTAX)
        index = 1
        # Common producer metadata (Kind/Language/X-TIMESTAMP-MAP) remains
        # opaque; it must never consume a missing-separator timing line.
        while index < len(lines) and lines[index][2]:
            value = lines[index][2]
            if "-->" in value or re.match(r"[A-Za-z][A-Za-z0-9_-]*[=:]", value) is None:
                raise FormaError(_SYNTAX)
            if re.fullmatch(r"Kind:[ \t]*metadata[ \t]*", value, re.I):
                raise FormaError("WebVTT з явно позначеним типом metadata не перекладається як субтитри.")
            index += 1
        if index >= len(lines):
            return []
        cues, identifiers, regions, previous_start = [], set(), set(), -1
        while index < len(lines):
            self.check_cancel()
            if not lines[index][2]:
                index += 1
                continue
            first = index
            while index < len(lines) and lines[index][2]:
                index += 1
            block = lines[first:index]
            header = block[0][2]
            note = header == "NOTE" or header.startswith(("NOTE ", "NOTE\t"))
            metadata = header.rstrip(" \t") in {"STYLE", "REGION"} and not cues
            if note or metadata:
                if any("-->" in line[2] for line in block):
                    raise FormaError(_SYNTAX)
                if metadata and header.rstrip(" \t") == "REGION":
                    settings = _settings(" ".join(line[2] for line in block[1:]), region=True)
                    region_id = settings.get("id")
                    if not region_id or region_id in regions:
                        raise FormaError(_SETTINGS)
                    regions.add(region_id)
                continue
            timing_index = 0 if "-->" in header else 1
            if timing_index >= len(block):
                raise FormaError(_SYNTAX)
            identifier = header if timing_index else ""
            if identifier and ("-->" in identifier or identifier in identifiers):
                raise FormaError(_SYNTAX)
            timing = _TIMING.fullmatch(block[timing_index][2])
            if timing is None:
                raise FormaError(_TIMING_ERROR)
            start_ms, end_ms = _timestamp(timing["start"]), _timestamp(timing["end"])
            if start_ms < previous_start or end_ms <= start_ms:
                raise FormaError(_TIMING_ERROR)
            _settings(timing["settings"] or "")
            if identifier:
                identifiers.add(identifier)
            payload_start = block[timing_index][1]
            payload_end = block[-1][1] - len(block[-1][3]) if len(block) > timing_index + 1 else payload_start
            cues.append(Cue(len(cues) + 1, block[0][0], block[-1][1], start_ms, end_ms,
                            payload_start, payload_end, identifier))
            previous_start = start_ms
            if len(cues) > MAX_CUES:
                raise FormaError("Файл містить забагато реплік субтитрів. Розділіть його на менші файли.")
        return cues

    def _payload(self, text, cue):
        _validate_payload(text, cue, self.check_cancel)
        return caption_payload(text, cue, tag_validator=lambda *_: True, format="vtt")

    def __init__(self, path, *, extraction_profile=None, check_cancel=lambda: None):
        super().__init__(path, extraction_profile=extraction_profile, check_cancel=check_cancel)
        self.issues.append(Issue(
            "vtt_captions",
            "WebVTT: перекладається лише текст підтримуваних реплік. Заголовки, коментарі, стилі, регіони, голоси, мовні позначки й таймінги збережені; файл не відтворюється.",
            severity="info",
        ))
