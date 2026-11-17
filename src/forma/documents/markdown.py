"""CommonMark-guided source spans; original Markdown is never regenerated."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import replace
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.rules_inline import autolink as autolink_rule
from markdown_it.rules_inline import html_inline as html_rule
from markdown_it.rules_inline import image as image_rule
from markdown_it.rules_inline import link as link_rule
from markdown_it.rules_inline import text as text_rule
from markdown_it.rules_inline.backticks import backtick as backticks_rule

from forma.core.models import Issue
from forma.documents.html import _HIDDEN_STYLE, _VOID
from forma.documents.text import EditableSpan, SourceDocument, subtract_ranges
from forma.translation.quality import immutable_spans

_PUNCTUATION = re.compile(r"([\\`*_{}\[\]()#+.!<>|~=\-$&])")
_NEWLINE = re.compile(r"\r\n|\r|\n")
_HTML_PROTECTED = {"script", "style", "code", "pre", "kbd", "samp", "textarea", "template", "svg", "math"}


class _TagDescription(HTMLParser):
    def __init__(self, raw, *, strict):
        super().__init__(convert_charrefs=False)
        self.tag, self.strict = "", strict
        self.feed(raw)

    def handle_starttag(self, tag, attrs):
        if self.strict and tag in _VOID:
            return
        if self.strict:
            # HTML keeps the first value when an attribute is repeated.
            attributes = {}
            for key, value in attrs:
                attributes.setdefault(key, value)
            style = re.sub(r"/\*.*?\*/", "", attributes.get("style") or "", flags=re.S)
            hidden = (
                "hidden" in attributes or "inert" in attributes
                or (attributes.get("translate") or "").strip().lower() == "no"
                or (attributes.get("aria-hidden") or "").strip().lower() == "true"
                or bool(_HIDDEN_STYLE.search(style))
            )
            if tag in _HTML_PROTECTED or hidden:
                self.tag = tag
            return
        attrs = dict(attrs)
        if tag in _HTML_PROTECTED or "hidden" in attrs or (attrs.get("translate") or "").lower() == "no":
            self.tag = tag

    def handle_startendtag(self, tag, attrs):
        if self.strict and tag not in _VOID | {"svg", "math"}:
            self.handle_starttag(tag, attrs)


class _RegionDone(Exception):
    pass


class _HTMLRegion(HTMLParser):
    def __init__(self, text, start, tag, check_cancel, *, strict):
        super().__init__(convert_charrefs=False)
        self.text, self.start, self.tag, self.depth = text, start, tag, 0
        self.strict = strict
        self.end, self.offsets = len(text), [0]
        try:
            for offset in range(start, len(text), 4096):
                check_cancel()
                chunk = text[offset:offset + 4096]
                self.offsets.extend(offset - start + match.end() for match in re.finditer("\n", chunk))
                self.feed(chunk)
        except _RegionDone:
            pass

    def handle_starttag(self, tag, attrs):
        if tag == self.tag:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.strict and tag not in _VOID | {"svg", "math"}:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.depth -= 1
            if not self.depth:
                line, column = self.getpos()
                offset = self.start + self.offsets[line - 1] + column
                self.end = self.text.find(">", offset) + 1
                raise _RegionDone


def _protected_ranges(text, check_cancel=lambda: None):
    """Protect template delimiters and explicit front matter as source ranges."""
    protected = [(start, end) for start, end, _ in immutable_spans(text)]
    protected.extend((match.start(), match.end()) for match in re.finditer(r"\$[A-Za-z_]\w*|%[A-Za-z_]\w*%|%(?:\d+\$)?[sdf]", text))
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip() in {"---", "+++"}:
        marker = lines[0].strip()
        end = len(lines[0])
        for line in lines[1:]:
            end += len(line)
            if line.strip() in ({"---", "..."} if marker == "---" else {"+++"}):
                break
        protected.append((0, end))
    pairs = (("{{", "}}"), ("{%", "%}"), ("{#", "#}"), ("${", "}"), ("<%", "%>"))
    pos = 0
    while pos < len(text):
        if not pos % 65536:
            check_cancel()
        pair = next(((opening, closing) for opening, closing in pairs if text.startswith(opening, pos)), None)
        if pair is None:
            pos += 1
            continue
        opening, closing = pair
        start, cursor, depth, quote, brackets = pos, pos + len(opening), 1, "", []
        while cursor < len(text) and depth:
            if not cursor % 65536:
                check_cancel()
            char = text[cursor]
            if quote:
                if char == "\\":
                    cursor += 2
                    continue
                if char == quote:
                    quote = ""
            elif opening != "{#" and char in {"'", '"', "`"}:
                quote = char
            elif opening == "${" and char == "{":
                depth += 1
            elif opening == "${" and char == "}":
                depth -= 1
            elif opening != "${" and not brackets and text.startswith(opening, cursor):
                depth += 1
                cursor += len(opening)
                continue
            elif opening != "${" and not brackets and text.startswith(closing, cursor):
                depth -= 1
                cursor += len(closing)
                continue
            elif opening != "{#" and char in "{[(":
                brackets.append(char)
            elif brackets and char == {"{": "}", "[": "]", "(": ")"}[brackets[-1]]:
                brackets.pop()
            cursor += 1
        protected.append((start, cursor))
        pos = cursor
    protected.sort()
    merged = []
    for start, end in protected:
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((start, end))
    return merged


def _parser():
    parser = MarkdownIt("commonmark", {"html": True, "maxNesting": 64}).enable(["table", "strikethrough"])

    def captured_text(state, silent):
        start = state.pos
        result = text_rule(state, silent)
        if result and not silent and state.src is state.env.get("_forma_source"):
            state.env["_forma_spans"].append((start, state.pos))
        return result

    def captured_link(state, silent):
        start = state.pos
        label_end = state.md.helpers.parseLinkLabel(state, start, True)
        captured = state.env.get("_forma_spans", [])
        previous = len(captured)
        result = link_rule(state, silent)
        if result and not silent and state.src is state.env.get("_forma_source"):
            suffix = state.src[label_end + 1:state.pos]
            # Shortcut/collapsed labels are reference identifiers themselves.
            # Translating them would disconnect the preserved reference definition.
            explicit = suffix.startswith("(") or suffix.startswith("[") and bool(suffix[1:-1].strip())
            if not explicit:
                del captured[previous:]
                state.env["_forma_protected"].append((start, state.pos))
            else:
                state.env["_forma_protected"].extend(((start, start + 1), (label_end, state.pos)))
        return result

    def protect_rule(rule):
        def protected_rule(state, silent):
            start = state.pos
            result = rule(state, silent)
            if result and not silent and state.src is state.env.get("_forma_source"):
                state.env["_forma_protected"].append((start, state.pos))
                if rule is html_rule:
                    state.env["_forma_html"].append((start, state.pos))
            return result
        return protected_rule

    def captured_inline(state):
        for token in state.tokens:
            if token.type != "inline":
                continue
            state.env["_forma_source"] = token.content
            state.env["_forma_spans"] = []
            state.env["_forma_protected"] = []
            state.env["_forma_html"] = []
            token.children = []
            state.md.inline.parse(token.content, state.md, state.env, token.children)
            token.meta["forma_spans"] = tuple(state.env["_forma_spans"])
            token.meta["forma_protected"] = tuple(state.env["_forma_protected"])
            token.meta["forma_html"] = tuple(state.env["_forma_html"])
        state.env.pop("_forma_source", None)
        state.env.pop("_forma_spans", None)
        state.env.pop("_forma_protected", None)
        state.env.pop("_forma_html", None)

    parser.inline.ruler.at("text", captured_text)
    parser.inline.ruler.at("link", captured_link)
    for name, rule in (("backticks", backticks_rule), ("html_inline", html_rule),
                       ("image", image_rule), ("autolink", autolink_rule)):
        parser.inline.ruler.at(name, protect_rule(rule))
    parser.core.ruler.at("inline", captured_inline)
    return parser


def _token_structure(token):
    content = token.content
    if token.type == "inline" or token.type == "text" and any(char.isalpha() for char in content):
        content = ""
    return (token.type, token.tag, token.nesting, token.markup, tuple(sorted(token.attrs.items())),
            token.info, content, tuple(_token_structure(child) for child in token.children or ()))


class MarkdownDocument(SourceDocument):
    extraction_profile = 14

    def __init__(self, path, *, extraction_profile=None, check_cancel=lambda: None):
        if extraction_profile is not None and (type(extraction_profile) is not int or extraction_profile not in {6, 14}):
            raise ValueError("Unsupported Markdown extraction profile")
        self.extraction_profile = 14 if extraction_profile is None else extraction_profile
        super().__init__(path, extraction_profile=self.extraction_profile, check_cancel=check_cancel)

    def _analyze(self, text):
        if getattr(self, "_markdown_cached_text", None) == text:
            return self._markdown_cached_analysis
        self.check_cancel()
        protected = _protected_ranges(text, self.check_cancel)
        environment = {}
        tokens = _parser().parse(text, environment)
        lines = text.splitlines(keepends=True)
        line_offsets, offset = [], 0
        for line in lines:
            line_offsets.append(offset)
            offset += len(line)
        used = {}
        spans, unmatched, frames, inline_protected = [], False, [], []
        html_regions, span_contexts = [], []
        for token in tokens:
            self.check_cancel()
            if token.type != "inline" or token.map is None:
                continue
            mapping, source_line = [], token.map[0]
            content_lines = token.content.split("\n")
            for part_number, content_line in enumerate(content_lines):
                found = None
                for index in range(source_line, min(len(lines), token.map[1])):
                    raw = lines[index].rstrip("\r\n")
                    position = raw.find(content_line, used.get(index, 0))
                    if position >= 0:
                        found = index, position
                        break
                if found is None:
                    mapping.extend([None] * len(content_line))
                    unmatched = True
                else:
                    index, position = found
                    frames.append((index, raw[used.get(index, 0):position]))
                    mapping.extend(line_offsets[index] + position + char for char in range(len(content_line)))
                    used[index] = position + len(content_line)
                    source_line = index + 1
                if part_number + 1 < len(content_lines):
                    mapping.append(None)  # Line endings are never an editable run.
            for start, end in token.meta.get("forma_html", ()):
                positions = mapping[start:end]
                if positions and all(position is not None for position in positions):
                    tag = _TagDescription(token.content[start:end], strict=self.extraction_profile == 14).tag
                    if tag:
                        region = _HTMLRegion(text, positions[0], tag, self.check_cancel,
                                             strict=self.extraction_profile == 14)
                        html_regions.append((positions[0], region.end))
            for start, end in token.meta.get("forma_protected", ()):
                positions = mapping[start:end]
                if positions and all(position is not None for position in positions):
                    inline_protected.append(token.content[start:end])
            for start, end in token.meta.get("forma_spans", ()):
                positions = mapping[start:end]
                if not positions or any(position is None for position in positions):
                    unmatched = True
                    continue
                if positions[-1] - positions[0] + 1 != len(positions):
                    unmatched = True
                    continue
                for left, right in subtract_ranges(positions[0], positions[-1] + 1, protected):
                    raw = text[left:right]
                    left += len(raw) - len(raw.lstrip())
                    right -= len(raw) - len(raw.rstrip())
                    if left >= right:
                        continue
                    line = bisect_right(line_offsets, left)
                    spans.append(EditableSpan(left, right, f"Line {line} · fragment {len(spans) + 1}"))
                    span_contexts.append(id(token))
        merged_html = []
        for start, end in sorted(html_regions):
            if merged_html and start <= merged_html[-1][1]:
                merged_html[-1] = merged_html[-1][0], max(merged_html[-1][1], end)
            else:
                merged_html.append((start, end))
        filtered, context_parts = [], {}
        for span, token_id in zip(spans, span_contexts, strict=True):
            for start, end in subtract_ranges(span.start, span.end, merged_html):
                if start < end:
                    filtered.append((replace(span, start=start, end=end), token_id))
                    context_parts.setdefault(token_id, []).append(text[start:end])
        # Context follows the exact final extraction surface. Raw inline content
        # also contains code, URL destinations and template expressions, even
        # when those values never became translation segments themselves.
        rendered_contexts = {token_id: "\n".join(parts)[:1800] for token_id, parts in context_parts.items()}
        spans = [replace(span, context=rendered_contexts[token_id]) for span, token_id in filtered]
        spans.sort(key=lambda span: span.start)
        if unmatched and not any(issue.code == "markdown_unmapped" for issue in self.issues):
            self.issues.append(Issue("markdown_unmapped", "Деякі фрагменти Markdown не вдалося безпечно зіставити з джерелом. Їх залишено без перекладу.", severity="info"))
        signature = (tuple(_token_structure(token) for token in tokens),
                     tuple(text[start:end] for start, end in protected), tuple(_NEWLINE.findall(text)),
                     tuple(text[start:end] for start, end in html_regions),
                     tuple(text[span.start:span.end] for span in spans
                           if not any(char.isalpha() for char in text[span.start:span.end])),
                     tuple(frames), tuple(inline_protected),
                     tuple((index, line.rstrip("\r\n")[used[index]:] if index in used else line)
                           for index, line in enumerate(lines)),
                     tuple(sorted((key, value["href"], value.get("title", ""),
                                   "".join(lines[value["map"][0]:value["map"][1]]))
                                  for key, value in environment.get("references", {}).items())))
        self._markdown_cached_text = text
        self._markdown_cached_analysis = spans, signature
        return spans, signature

    def _extract_spans(self, text):
        return self._analyze(text)[0]

    def _structure_for_text(self, text):
        return self._analyze(text)[1]

    def _encode_translation(self, value):
        return _PUNCTUATION.sub(r"\\\1", value)
