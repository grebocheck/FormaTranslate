"""Edit static HTML text by source ranges, without rendering or rewriting markup."""
from __future__ import annotations

import codecs
import html
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from html.parser import HTMLParser

from forma.core.models import FormaError, Issue
from forma.documents.text import EditableSpan, SourceDocument, subtract_ranges
from forma.translation.quality import immutable_spans

_MARKUP_ERROR = "Документ містить некоректну або неоднозначну розмітку HTML."
_ENCODING_ERROR = "Кодування HTML не підтримується або суперечить декларації. Збережіть документ як UTF-8."
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_EXCLUDED = frozenset({
    "head", "title", "script", "style", "code", "pre", "textarea", "template", "noscript",
    "iframe", "object", "embed", "svg", "math", "xmp", "noembed", "noframes", "plaintext",
})
_RAW = frozenset({"script", "style", "textarea", "title", "iframe", "xmp", "noembed", "noframes", "plaintext"})
_HEAD_CONTENT = frozenset({"base", "basefont", "bgsound", "link", "meta", "title", "noscript", "noframes", "style", "script", "template"})
_OPTIONAL = frozenset({"html", "head", "body", "li", "dt", "dd", "p", "rt", "rp", "optgroup", "option", "colgroup", "thead", "tbody", "tfoot", "tr", "td", "th"})
_BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details",
    "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "menu",
    "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul",
})
_ENTITY = re.compile(r"&(?:\#[xX][0-9a-fA-F]+|\#[0-9]+|[a-zA-Z][a-zA-Z0-9]+);?")
_TEMPLATE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{[^{}]*\}", re.S)
_URI = re.compile(r"\b(?:[a-z][a-z0-9+.-]*://|mailto:|tel:|data:|urn:)[^\s<>]+", re.I)
_HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|content-visibility\s*:\s*hidden)\s*(?:!\s*important\s*)?(?:;|$)", re.I)


def _unescape(value):
    try:
        return html.unescape(value)
    except ValueError as exc:
        raise FormaError(_MARKUP_ERROR) from exc


@dataclass(frozen=True)
class _Frame:
    tag: str
    blocked: bool
    index: int
    start: int


class _TextRanges(HTMLParser):
    """Conservative static nesting; optional end tags have explicit rules."""

    def __init__(self, text, check_cancel):
        super().__init__(convert_charrefs=False)
        self.source, self.check_cancel = text, check_cancel
        self.lines = [0] + [match.end() for match in re.finditer("\n", text)]
        self.stack: list[_Frame] = []
        self.ranges: list[tuple[int, int, int, str]] = []
        self.markup: list[tuple[int, int]] = []
        self.encodings: list[str] = []
        self._frames = 0

    def _position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def _raw(self):
        return self.stack[-1].tag if self.stack and self.stack[-1].tag in _RAW else ""

    def _tag_end(self):
        start, quote = self._position(), ""
        for index in range(start + 1, len(self.source)):
            char = self.source[index]
            if quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == ">":
                return index + 1
        raise FormaError(_MARKUP_ERROR)

    def _close_optional(self, tags):
        while self.stack and self.stack[-1].tag in tags:
            self.stack.pop()

    def handle_starttag(self, tag, attrs):
        self.check_cancel()
        if self._raw():
            return
        self.markup.append((self._position(), self._tag_end()))
        if self.stack and self.stack[-1].tag == "head" and tag not in _HEAD_CONTENT:
            self.stack.pop()
        if tag in _BLOCK:
            self._close_optional({"p"})
        if tag == "li":
            self._close_optional({"li"})
        elif tag in {"dt", "dd"}:
            self._close_optional({"dt", "dd"})
        elif tag in {"rt", "rp"}:
            self._close_optional({"rt", "rp"})
        elif tag in {"option", "optgroup"}:
            self._close_optional({"option"} | ({"optgroup"} if tag == "optgroup" else set()))
        elif tag in {"thead", "tbody", "tfoot", "tr"}:
            self._close_optional({"td", "th", "tr"} | ({"thead", "tbody", "tfoot"} if tag != "tr" else set()))
        elif tag in {"td", "th"}:
            self._close_optional({"td", "th"})
        elif tag in {"body", "colgroup"}:
            self._close_optional({"head"} if tag == "body" else {"colgroup"})
        attributes = {}
        for key, value in attrs:
            if key in attributes and key in {"hidden", "inert", "translate", "style", "aria-hidden", "charset"}:
                raise FormaError(_MARKUP_ERROR)
            attributes.setdefault(key, value)
        if tag == "meta":
            if "charset" in attributes:
                self.encodings.append(attributes["charset"] or "")
            elif (attributes.get("http-equiv") or "").strip().lower() == "content-type":
                match = re.search(r"(?:^|;)\s*charset\s*=\s*([^;\s]+)", attributes.get("content") or "", re.I)
                if match:
                    self.encodings.append(match.group(1).strip("\"'"))
        style = re.sub(r"/\*.*?\*/", "", attributes.get("style") or "", flags=re.S)
        blocked = (
            bool(self.stack and self.stack[-1].blocked) or tag in _EXCLUDED
            or "hidden" in attributes or "inert" in attributes
            or (attributes.get("translate") or "").strip().lower() == "no"
            or (attributes.get("aria-hidden") or "").strip().lower() == "true"
            or bool(_HIDDEN_STYLE.search(style))
        )
        if tag not in _VOID:
            self._frames += 1
            self.stack.append(_Frame(tag, blocked, self._frames, self._position()))
            if len(self.stack) > 512:
                raise FormaError(_MARKUP_ERROR)

    def handle_startendtag(self, tag, attrs):
        if self._raw():
            return
        # In HTML, a trailing slash does not close an ordinary text container.
        # Foreign SVG/MathML is excluded, but its XML-style empty tags are safe.
        foreign = tag in {"svg", "math"} or any(frame.tag in {"svg", "math"} for frame in self.stack)
        if tag not in _VOID and not foreign:
            raise FormaError(_MARKUP_ERROR)
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.check_cancel()
        raw = self._raw()
        if raw and (tag != raw or raw == "plaintext"):
            return
        if raw:
            self.markup.append((self.stack[-1].start, self._position()))
        self.markup.append((self._position(), self._tag_end()))
        if tag in _VOID:
            return
        matches = [index for index, frame in enumerate(self.stack) if frame.tag == tag]
        if not matches:
            raise FormaError(_MARKUP_ERROR)
        index = matches[-1]
        if any(frame.tag not in _OPTIONAL for frame in self.stack[index + 1:]):
            raise FormaError(_MARKUP_ERROR)
        del self.stack[index:]

    def _text(self, start, end):
        self.check_cancel()
        if start == end or (self.stack and self.stack[-1].blocked):
            return
        frame = next((item for item in reversed(self.stack) if item.tag in _BLOCK), None)
        block = frame.index if frame else 0
        path = "/".join(item.tag for item in self.stack)
        if self.ranges and self.ranges[-1][1] == start and self.ranges[-1][2:] == (block, path):
            previous = self.ranges.pop()
            start = previous[0]
        self.ranges.append((start, end, block, path))

    def handle_data(self, data):
        start = self._position()
        # HTMLParser otherwise treats unfinished tags as text at EOF. Reject
        # those cases rather than risk translating attribute or hidden content.
        if not (self.stack and self.stack[-1].blocked) and re.search(r"<[!/?a-zA-Z]", data):
            raise FormaError(_MARKUP_ERROR)
        self._text(start, start + len(data))

    def handle_entityref(self, name):
        start = self._position()
        end = start + len(name) + 1
        self._text(start, end + (self.source[end:end + 1] == ";"))

    def handle_charref(self, name):
        start = self._position()
        end = start + len(name) + 2
        self._text(start, end + (self.source[end:end + 1] == ";"))

    def handle_decl(self, decl):
        if self._raw():
            return
        if not decl.lower().startswith("doctype html") or "[" in decl:
            raise FormaError(_MARKUP_ERROR)
        self.markup.append((self._position(), self._tag_end()))

    def handle_comment(self, data):
        if self._raw():
            return
        start = self._position()
        if not self.source.startswith("<!--", start):
            raise FormaError(_MARKUP_ERROR)
        match = re.search(r"--!?\s*>", self.source[start + 4:])
        if match is None:
            raise FormaError(_MARKUP_ERROR)
        self.markup.append((start, start + 4 + match.end()))

    def handle_pi(self, data):
        if self._raw():
            return
        self.markup.append((self._position(), self._tag_end()))
        if data.lower().startswith("xml "):
            match = re.search(r"\bencoding\s*=\s*(['\"])(.*?)\1", data, re.I)
            if match:
                self.encodings.append(match.group(2))

    def unknown_decl(self, data):
        if not (self.stack and self.stack[-1].blocked):
            raise FormaError(_MARKUP_ERROR)
        self.markup.append((self._position(), self._tag_end()))

    def finish(self):
        self.feed(self.source)
        self.close()
        if any(frame.tag not in _OPTIONAL | {"plaintext"} for frame in self.stack):
            raise FormaError(_MARKUP_ERROR)
        self.markup.extend((frame.start, len(self.source)) for frame in self.stack if frame.tag == "plaintext")
        protected = []
        for start, end in sorted(self.markup):
            if protected and start <= protected[-1][1]:
                protected[-1] = protected[-1][0], max(end, protected[-1][1])
            else:
                protected.append((start, end))
        starts = [item[0] for item in protected]
        for match in re.finditer(r"<[!/?a-zA-Z]", self.source):
            index = bisect_right(starts, match.start()) - 1
            if index < 0 or match.start() >= protected[index][1]:
                raise FormaError(_MARKUP_ERROR)
        if any(re.search(r"<[!/?a-zA-Z]", self.source[start:end]) for start, end, _, _ in self.ranges):
            raise FormaError(_MARKUP_ERROR)
        return self


def _trim_visible(text, start, end):
    """Leave indentation and whitespace entities outside editable spans."""
    while start < end:
        if text[start].isspace():
            start += 1
            continue
        entity = _ENTITY.match(text, start, end)
        if entity and _unescape(entity.group()).isspace():
            start = entity.end()
            continue
        break
    while end > start:
        if text[end - 1].isspace():
            end -= 1
            continue
        ampersand = text.rfind("&", start, end)
        entity = _ENTITY.fullmatch(text, ampersand, end) if ampersand >= 0 else None
        if entity and _unescape(entity.group()).isspace():
            end = ampersand
            continue
        break
    return start, end


def _protected_ranges(text, start, end):
    """Map decoded URLs/placeholders back to whole source entity spellings."""
    raw = text[start:end]
    visible = _unescape(raw)
    protected = [(left, right) for left, right, _ in immutable_spans(visible)]
    protected.extend((match.start(), match.end()) for match in _TEMPLATE.finditer(visible))
    protected.extend((match.start(), match.end()) for match in _URI.finditer(visible))
    if not protected:
        return []
    entities, decoded_cursor, raw_cursor = [], 0, 0
    for match in _ENTITY.finditer(raw):
        decoded_cursor += match.start() - raw_cursor
        decoded_end = decoded_cursor + len(_unescape(match.group()))
        entities.append((decoded_cursor, decoded_end, match.start(), match.end()))
        decoded_cursor, raw_cursor = decoded_end, match.end()
    starts = [item[0] for item in entities]

    def position(offset, *, right=False):
        index = bisect_right(starts, offset) - 1
        if index < 0:
            return start + offset
        decoded_start, decoded_end, raw_start, raw_end = entities[index]
        if offset == decoded_start:
            return start + raw_start
        if decoded_start < offset < decoded_end:
            return start + (raw_end if right else raw_start)
        return start + offset + raw_end - decoded_end

    ranges = sorted((position(left), position(right, right=True)) for left, right in protected)
    merged = []
    for left, right in ranges:
        if merged and left <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], right)
        else:
            merged.append((left, right))
    return merged


class HtmlDocument(SourceDocument):
    extraction_profile = 7
    engine = "forma-text"

    def _parse(self, text):
        try:
            parser = _TextRanges(text, self.check_cancel).finish()
        except (AssertionError, ValueError, IndexError) as exc:
            raise FormaError(_MARKUP_ERROR) from exc
        encoding = codecs.lookup(self.encoding).name
        if encoding.startswith("utf-32"):
            raise FormaError(_ENCODING_ERROR)
        for label in parser.encodings:
            try:
                declared = codecs.lookup(label.strip()).name
            except LookupError:
                raise FormaError(_ENCODING_ERROR) from None
            compatible = declared == encoding or (declared == "utf-16" and encoding.startswith("utf-16-"))
            if not compatible:
                raise FormaError(_ENCODING_ERROR)
        return parser

    def _extract_spans(self, text):
        parser = self._parse(text)
        blocks = {}
        eligible = []
        for start, end, block, path in parser.ranges:
            for left, right in subtract_ranges(start, end, _protected_ranges(text, start, end)):
                left, right = _trim_visible(text, left, right)
                if left < right:
                    eligible.append((left, right, block, path))
                    blocks.setdefault(block, []).append(_unescape(text[left:right]))
        block_text = {key: " ".join(values)[:1600] for key, values in blocks.items()}
        spans = []
        for left, right, block, path in eligible:
            visible = _unescape(text[left:right])
            if not any(character.isalpha() for character in visible):
                continue
            line = bisect_right(parser.lines, left)
            context = json.dumps({"format": "html", "element_path": path,
                                  "block_text": block_text[block]}, ensure_ascii=False, sort_keys=True)
            spans.append(EditableSpan(left, right, f"HTML · text {len(spans) + 1} · line {line}", context, visible))
        return spans

    def _encode_translation(self, value):
        return html.escape(value, quote=False)

    def comparison_text(self):
        """Include visible numeric-only nodes without exposing hidden/code text."""
        parser = self._parse(self.text)
        return "\n".join(_unescape(self.text[start:end]) for start, end, _, _ in parser.ranges)

    def _structure_for_text(self, text):
        spans = self._extract_spans(text)
        gaps, cursor = [], 0
        for span in spans:
            gaps.append(text[cursor:span.start])
            cursor = span.end
        gaps.append(text[cursor:])
        return tuple(gaps)

    def __init__(self, path, *, extraction_profile=None, check_cancel=lambda: None):
        super().__init__(path, extraction_profile=extraction_profile, check_cancel=check_cancel)
        self.issues.append(Issue(
            "html_static_text",
            "HTML: перекладається лише текст сторінки. Атрибути, код і явно приховані елементи збережено; зовнішні ресурси не завантажуються, видимість за CSS не обчислюється.",
            severity="info",
        ))
