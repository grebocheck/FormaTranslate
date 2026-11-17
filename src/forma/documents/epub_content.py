"""Offline EPUB XHTML/NCX prose edits over exact source character ranges."""
from __future__ import annotations

import codecs
import hashlib
import html
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from html.entities import name2codepoint
from xml.sax.saxutils import escape

from lxml import etree

from forma.core.models import FormaError, Issue, Segment
from forma.documents.html import _HIDDEN_STYLE
from forma.documents.text import MAX_TEXT_BYTES, MAX_TEXT_CHARACTERS
from forma.translation.quality import immutable_spans, parse_translation

XHTML = "http://www.w3.org/1999/xhtml"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XML = "http://www.w3.org/XML/1998/namespace"
MAX_RUNS = 256
MAX_EVENTS = 512
MAX_BLOCK_CHARACTERS = 20000
MAX_BLOCKS = 100000
MAX_CONTENT_TOKENS = 200000
_MARKUP = "EPUB містить некоректний або непідтримуваний XHTML/NCX."
_ENCODING = "Кодування вмісту EPUB не підтримується або суперечить XML-декларації. Потрібен UTF-8 або UTF-16 з BOM."
_DTD = "Вміст EPUB містить непідтримувані DTD або сутності. Зовнішні визначення не завантажуються."
_LIMIT = "Текстовий фрагмент EPUB завеликий або містить забагато стилів. Розділіть абзац на менші частини."
_STRUCTURE = "Переклад змінив захищену структуру документа. Перевірте розмітку й службові значення."
_XML_NAMES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_XHTML_NAMES = {**{name: chr(value) for name, value in name2codepoint.items()}, **_XML_NAMES}
_ENTITY = re.compile(r"&(?:\#[xX][0-9a-fA-F]+|\#[0-9]+|[A-Za-z][A-Za-z0-9]*);")
_NAMED = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_DECLARATION = re.compile(
    r"<\?xml[ \t\r\n]+version[ \t\r\n]*=[ \t\r\n]*(?P<q>['\"])1\.0(?P=q)"
    r"(?:[ \t\r\n]+encoding[ \t\r\n]*=[ \t\r\n]*(?P<e>['\"])(?P<encoding>[A-Za-z][A-Za-z0-9._-]*)(?P=e))?"
    r"(?:[ \t\r\n]+standalone[ \t\r\n]*=[ \t\r\n]*(?P<s>['\"])(?:yes|no)(?P=s))?[ \t\r\n]*\?>"
)
_ATTRIBUTE = re.compile(r"([^\s=<>/'\"]+)[ \t\r\n]*=[ \t\r\n]*(?:\"([^\"]*)\"|'([^']*)')")
_BLOCK = frozenset({"body", "article", "section", "div", "nav", "aside", "blockquote", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "td", "th", "caption", "figcaption", "address", "summary", "legend"})
_EXCLUDED = frozenset({"head", "script", "style", "code", "pre", "textarea", "template", "noscript", "iframe", "object", "embed", "audio", "video", "canvas", "select", "button", "input", "form"})
_INVISIBLE = frozenset({"head", "script", "style", "template"})
_PREFORMATTED = re.compile(r"(?:^|;)\s*white-space\s*:\s*(?:pre|pre-wrap|pre-line|break-spaces)\s*(?:!\s*important\s*)?(?:;|$)", re.I)
_TEMPLATE_START = re.compile(r"\{\{|\{%|\$\{")
_URI_START = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9+.-]{0,63}://|mailto:|tel:|data:|urn:)", re.I)
_DTDS = {
    "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd": ("html", "-//W3C//DTD XHTML 1.1//EN"),
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd": ("html", "-//W3C//DTD XHTML 1.0 Strict//EN"),
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd": ("html", "-//W3C//DTD XHTML 1.0 Transitional//EN"),
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-frameset.dtd": ("html", "-//W3C//DTD XHTML 1.0 Frameset//EN"),
    "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd": ("ncx", "-//NISO//DTD ncx 2005-1//EN"),
}


@dataclass(frozen=True)
class _Token:
    kind: str
    start: int
    end: int


def _tokens(text, check_cancel):
    position = 0
    while position < len(text):
        check_cancel()
        start = text.find("<", position)
        if start < 0:
            yield _Token("text", position, len(text))
            return
        if start > position:
            yield _Token("text", position, start)
        if text.startswith("<!--", start):
            end = text.find("-->", start + 4)
            kind, width = "comment", 3
        elif text.startswith("<![CDATA[", start):
            end = text.find("]]>", start + 9)
            kind, width = "cdata", 3
        elif text.startswith("<?", start):
            end = text.find("?>", start + 2)
            kind, width = "pi", 2
        else:
            kind, width, end, quote = "tag", 1, start + 1, ""
            if text.startswith("<!DOCTYPE", start):
                kind = "doctype"
            elif text.startswith("<!", start):
                raise FormaError(_DTD)
            while end < len(text):
                character = text[end]
                if quote:
                    if character == quote:
                        quote = ""
                elif character in "\"'":
                    quote = character
                elif kind == "doctype" and character == "[":
                    raise FormaError(_DTD)
                elif character == ">":
                    break
                end += 1
            if end >= len(text):
                end = -1
        if end < 0:
            raise FormaError(_MARKUP)
        position = end + width
        yield _Token(kind, start, position)


def _decode(raw):
    if len(raw) > MAX_TEXT_BYTES:
        raise FormaError("Текстовий файл завеликий. Ліміт — 16 МБ.")
    bom, encoding = b"", "utf-8"
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise FormaError(_ENCODING)
    for marker, label in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(marker):
            bom, encoding = marker, label
            break
    try:
        text = raw[len(bom):].decode(encoding, errors="strict")
    except UnicodeError as exc:
        raise FormaError(_ENCODING) from exc
    if len(text) > MAX_TEXT_CHARACTERS:
        raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
    if re.match(r"<\?xml[ \t\r\n]", text):
        declaration = _DECLARATION.match(text)
        if declaration is None:
            raise FormaError(_MARKUP)
        label = declaration["encoding"]
        if label:
            try:
                declared = codecs.lookup(label).name
            except LookupError:
                raise FormaError(_ENCODING) from None
            if declared != encoding and not (declared == "utf-16" and encoding.startswith("utf-16-")):
                raise FormaError(_ENCODING)
    return text, encoding, bom


def _doctype(value, role):
    match = re.fullmatch(r"<!DOCTYPE[ \t\r\n]+(html|ncx)[ \t\r\n]*(?:(PUBLIC|SYSTEM)[ \t\r\n]+(['\"])(.*?)\3(?:[ \t\r\n]+(['\"])(.*?)\5)?)?[ \t\r\n]*>", value, re.S)
    expected = "ncx" if role == "ncx" else "html"
    if match is None or match[1] != expected:
        raise FormaError(_DTD)
    if match[2] is None:
        return _XML_NAMES
    public, system = (match[4], match[6]) if match[2] == "PUBLIC" else (None, match[4])
    if match[2] == "SYSTEM" and match[6] is not None:
        raise FormaError(_DTD)
    canonical = (system or "").replace("https://", "http://", 1)
    trusted = _DTDS.get(canonical)
    if trusted is None or trusted[0] != expected or public is not None and public != trusted[1]:
        raise FormaError(_DTD)
    return _XHTML_NAMES if expected == "html" else _XML_NAMES


def _validated(raw, role, check_cancel):
    text, encoding, bom = _decode(raw)
    tokens = []
    for token in _tokens(text, check_cancel):
        tokens.append(token)
        if len(tokens) > MAX_CONTENT_TOKENS:
            raise FormaError(_LIMIT)
    names, seen_doctype, seen_root = _XML_NAMES, False, False
    for token in tokens:
        if token.kind == "doctype":
            if seen_doctype or seen_root:
                raise FormaError(_DTD)
            names = _doctype(text[token.start:token.end], role)
            seen_doctype = True
        elif token.kind == "tag" and not text.startswith("</", token.start):
            seen_root = True
    pieces = []

    def reference(match):
        if match[1] not in names:
            raise FormaError(_DTD)
        return "".join(f"&#{ord(character)};" for character in names[match[1]])

    for token in tokens:
        check_cancel()
        value = text[token.start:token.end]
        if token.kind == "doctype":
            value = re.sub(r"[^\r\n]", " ", value)
        elif token.kind == "pi" and token.start == 0 and re.match(r"<\?xml[ \t\r\n]", value):
            declaration = _DECLARATION.fullmatch(value)
            if declaration is None:
                raise FormaError(_MARKUP)
            value = '<?xml version="1.0" encoding="UTF-8"?>'
        elif token.kind in {"tag", "text"}:
            value = _NAMED.sub(reference, value)
        pieces.append(value)
    try:
        tree = etree.ElementTree(etree.fromstring("".join(pieces).encode("utf-8"), etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False,
            remove_comments=False, remove_pis=False, strip_cdata=False,
        )))
    except (etree.XMLSyntaxError, UnicodeError, ValueError, RecursionError) as exc:
        raise FormaError(_MARKUP) from exc
    expected = f"{{{NCX}}}ncx" if role == "ncx" else f"{{{XHTML}}}html"
    if tree.getroot().tag != expected:
        raise FormaError(_MARKUP)
    if any(node.tag == "{http://www.w3.org/2000/09/xmldsig#}Signature" for node in tree.iter()):
        raise FormaError("Документ має цифровий підпис. Для перекладу створіть непідписану копію.")
    if role != "ncx":
        if len(tree.getroot().findall(f"{{{XHTML}}}body")) != 1:
            raise FormaError(_MARKUP)
        if any(len(node) or node.text for node in tree.iter(f"{{{XHTML}}}br")):
            raise FormaError(_MARKUP)
    check_cancel()
    return text, encoding, bom, tokens, names, tree


def validate_content_xml(raw, *, role="spine", check_cancel=lambda: None):
    """Validate a safe surrogate; no DTD, URI, entity or stylesheet is loaded."""
    return _validated(raw, role, check_cancel)[-1]


def _unescape(value, names):
    def replace(match):
        reference = match.group()[1:-1]
        if reference.startswith(("#x", "#X")):
            return chr(int(reference[2:], 16))
        if reference.startswith("#"):
            return chr(int(reference[1:]))
        return names[reference]
    try:
        return _ENTITY.sub(replace, value.replace("\r\n", "\n").replace("\r", "\n")).replace("\r", "\n")
    except (KeyError, ValueError, OverflowError) as exc:
        raise FormaError(_DTD) from exc


@dataclass(frozen=True)
class _Run:
    start: int
    end: int
    value: str
    maps: tuple[tuple[int, int, int, int], ...]
    map_starts: tuple[int, ...]

    def source_position(self, position, *, right=False):
        index = bisect_right(self.map_starts, position) - 1
        if index < 0:
            return self.start + position
        left, end, raw_left, raw_end = self.maps[index]
        if position == left:
            return raw_left
        if left < position < end:
            return raw_end if right else raw_left
        return position + raw_end - end


def _run(text, start, end, names):
    raw, chunks, maps, cursor, decoded = text[start:end], [], [], 0, 0
    for match in re.finditer(r"&(?:\#[xX][0-9a-fA-F]+|\#[0-9]+|[A-Za-z][A-Za-z0-9]*);|\r\n|\r", raw):
        prefix = raw[cursor:match.start()]
        chunks.append(prefix)
        decoded += len(prefix)
        value = "\n" if match.group().startswith("\r") else _unescape(match.group(), names)
        chunks.append(value)
        maps.append((decoded, decoded + len(value), start + match.start(), start + match.end()))
        decoded += len(value)
        cursor = match.end()
    chunks.append(raw[cursor:])
    return _Run(start, end, "".join(chunks), tuple(maps), tuple(item[0] for item in maps))


@dataclass
class _Frame:
    qname: str
    namespace: str
    local: str
    namespaces: dict[str, str]
    blocked: bool
    block: int
    barrier: bool = False
    hard: bool = False
    hidden: bool = False
    navigation: bool = False


def _protected(value, check_cancel=lambda: None):
    # Only bounded tokens reach the general quality regexes. Unbounded words,
    # URI schemes and unfinished templates remain opaque instead of inviting
    # quadratic backtracking or exposing template metadata to a provider.
    ranges = []
    cursor = 0
    while match := _TEMPLATE_START.search(value, cursor):
        check_cancel()
        closing = {"{{": "}}", "{%": "%}", "${": "}"}[match.group()]
        end = value.find(closing, match.end())
        end = len(value) if end < 0 else end + len(closing)
        ranges.append((match.start(), end))
        cursor = end
    for match in _URI_START.finditer(value):
        check_cancel()
        end = re.search(r"[\s<>]", value[match.end():])
        ranges.append((match.start(), match.end() + end.start() if end else len(value)))
    for index, match in enumerate(re.finditer(r"\S+", value)):
        if not index % 256:
            check_cancel()
        if len(match.group()) > 256:
            ranges.append(match.span())
        else:
            ranges.extend((match.start() + left, match.start() + right) for left, right, _ in immutable_spans(match.group()))
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], right)
        else:
            merged.append((left, right))
    return merged


def candidate_issues(segment, translated):
    """Pure EPUB response validation, also usable before recovery/checkpoints."""
    if not segment.id.startswith("epub-v13:"):
        return []
    try:
        values = parse_translation(segment.source, translated)
        original = parse_translation(segment.source, segment.source)
    except (FormaError, UnicodeError) as exc:
        return [Issue("formatting", str(exc) if isinstance(exc, FormaError) else _STRUCTURE, segment.id, "error")]
    if (len(values) > MAX_EVENTS or any(len(key[1:]) > 3 or not key[1:].isascii() or not key[1:].isdigit() or int(key[1:]) >= MAX_RUNS for key in values)):
        return [Issue("formatting", _STRUCTURE, segment.id, "error")]
    source = "".join(value if key.startswith("r") else "\n" for key, value in original.items())
    visible = "".join(value if key.startswith("r") else "\n" for key, value in values.items())
    if len(visible) > MAX_BLOCK_CHARACTERS:
        return [Issue("formatting", _LIMIT, segment.id, "error")]
    if any(character.isalpha() for character in source) and not any(character.isalpha() for character in visible):
        return [Issue("formatting", "Переклад втратив текст: залишилися лише числа, символи або захищені значення.", segment.id, "error")]
    if _protected(visible):
        return [Issue("formatting", _STRUCTURE, segment.id, "error")]
    return []


class EpubContent:
    """One XHTML/NCX resource, independent of ZIP/container publication."""

    def __init__(self, raw, *, part, role="spine", check_cancel=lambda: None):
        self.raw, self.part, self.role, self.check_cancel = raw, part, role, check_cancel
        self.text, self.encoding, self.bom, self.tokens, self.names, _ = _validated(raw, role, check_cancel)
        self.segments, self.issues, self.targets = [], [], {}
        self._comparison = []
        self._extract()
        self.tokens = ()
        self._signature = self._gaps()
        self.issues.append(Issue("epub_static_content", "EPUB: перекладається текст підтримуваних XHTML-сторінок і навігації. Метадані видавця, код, приховані та вбудовані області збережено; ресурси не завантажуються, видимість за CSS не обчислюється.", severity="info"))

    def _extract(self):
        stack, blocks, next_block, comparison = [], {}, 0, []
        for token in self.tokens:
            self.check_cancel()
            if token.kind == "text":
                if stack and not stack[-1].blocked and stack[-1].block:
                    run = _run(self.text, token.start, token.end, self.names)
                    blocks.setdefault(stack[-1].block, []).append(("r", run))
                continue
            if token.kind == "cdata":
                if stack and not stack[-1].blocked and stack[-1].block:
                    blocks.setdefault(stack[-1].block, []).append(("barrier", None))
                continue
            if token.kind != "tag":
                continue
            raw = self.text[token.start:token.end]
            if raw.startswith("</"):
                frame = stack.pop()
                if frame.barrier and stack and stack[-1].block:
                    blocks.setdefault(stack[-1].block, []).append(("barrier", None))
                elif stack and frame.block and stack[-1].block != frame.block and stack[-1].block:
                    blocks.setdefault(stack[-1].block, []).append(("barrier", None))
                continue
            match = re.match(r"<([^\s/>]+)", raw)
            if match is None:
                raise FormaError(_MARKUP)
            qname = match[1]
            attrs = {item[1]: _unescape(item[2] if item[2] is not None else item[3], self.names) for item in _ATTRIBUTE.finditer(raw, match.end())}
            namespaces = stack[-1].namespaces if stack else {"xml": XML}
            if any(name == "xmlns" or name.startswith("xmlns:") for name in attrs):
                namespaces = dict(namespaces)
            for name, value in attrs.items():
                if name == "xmlns":
                    namespaces[""] = value
                elif name.startswith("xmlns:"):
                    namespaces[name[6:]] = value
            prefix, separator, local = qname.partition(":")
            namespace = namespaces.get(prefix if separator else "", "")
            local = local if separator else prefix
            inherited = bool(stack and stack[-1].blocked)
            hidden = ("hidden" in attrs or "inert" in attrs
                      or attrs.get("aria-hidden", "").strip().lower() == "true")
            style = re.sub(r"/\*.*?\*/", "", attrs.get("style", ""), flags=re.S)
            hidden |= bool(_HIDDEN_STYLE.search(style))
            preformatted = attrs.get("xml:space") == "preserve" or bool(_PREFORMATTED.search(style))
            hard = bool(stack and stack[-1].hard) or attrs.get("translate", "").strip().lower() == "no" or preformatted
            hidden |= bool(stack and stack[-1].hidden)
            navigation = bool(stack and stack[-1].navigation)
            if self.role == "ncx":
                navigation |= bool(namespace == NCX and local in {"navMap", "pageList", "navList"} and len(stack) == 1)
                eligible = (namespace == NCX and local == "text" and len(stack) >= 2 and navigation
                            and stack[-1].namespace == NCX and stack[-1].local == "navLabel"
                            and stack[-2].namespace == NCX and stack[-2].local in {"navPoint", "pageTarget", "navTarget"})
                block = next_block + 1 if eligible else 0
                if eligible:
                    next_block = block
                hard |= namespace != NCX
                blocked = hard or hidden or (not eligible and bool(stack and stack[-1].block))
                barrier = False
            else:
                semantic_nav = (self.role == "nav" and namespace == XHTML and local == "nav" and any(
                    name.partition(":")[2] == "type" and namespaces.get(name.partition(":")[0]) == "http://www.idpf.org/2007/ops"
                    and {"toc", "page-list", "landmarks"}.intersection(value.split()) for name, value in attrs.items()
                ))
                navigation |= semantic_nav
                hard |= namespace != XHTML or local in _EXCLUDED
                blocked = hard or hidden and not navigation
                block = stack[-1].block if stack else 0
                if not blocked and local in _BLOCK:
                    if block:
                        blocks.setdefault(block, []).append(("barrier", None))
                    next_block += 1
                    block = next_block
                barrier = bool(blocked and not inherited and not hidden and local not in _INVISIBLE and block)
                if barrier:
                    blocks.setdefault(block, []).append(("barrier", None))
                if not blocked and local == "br" and block:
                    blocks.setdefault(block, []).append(("k", (token.start, token.end)))
            frame = _Frame(qname, namespace, local, namespaces, blocked, block, barrier, hard, hidden, navigation)
            if not raw.rstrip().endswith("/>"):
                stack.append(frame)
                if len(stack) > 256:
                    raise FormaError(_MARKUP)
            elif barrier:
                blocks.setdefault(block, []).append(("barrier", None))
        if len(blocks) > MAX_BLOCKS:
            raise FormaError(_LIMIT)
        digest = hashlib.sha256(self.part.encode("utf-8")).hexdigest()
        fragment = 0
        for block, events in blocks.items():
            self.check_cancel()
            visible = "".join(run.value if kind == "r" else "\n" for kind, run in events)
            chunk, position, run_count, marker_count = [], None, 0, 0
            for kind, value in events:
                if kind == "barrier":
                    if chunk:
                        if sum(map(len, chunk)) > MAX_BLOCK_CHARACTERS:
                            raise FormaError(_LIMIT)
                        comparison.append((position, "".join(chunk)))
                    chunk, position, run_count, marker_count = [], None, 0, 0
                else:
                    run_count += kind == "r"
                    marker_count += kind == "k"
                    if max(run_count, marker_count) > MAX_RUNS or run_count + marker_count > MAX_EVENTS:
                        raise FormaError(_LIMIT)
                    if position is None:
                        position = value.start if kind == "r" else value[0]
                    chunk.append(value.value if kind == "r" else "\n")
            if chunk:
                if sum(map(len, chunk)) > MAX_BLOCK_CHARACTERS:
                    raise FormaError(_LIMIT)
                comparison.append((position, "".join(chunk)))
            ranges, protected, cursor = [], _protected(visible, self.check_cancel), 0
            for kind, value in events:
                if kind != "r":
                    ranges.append((kind, value))
                    cursor += 1
                    continue
                left, right, offset = cursor, cursor + len(value.value), 0
                for start, end in protected:
                    if end <= left:
                        continue
                    if start >= right:
                        break
                    local_start, local_end = max(start, left) - left, min(end, right) - left
                    if local_start > offset:
                        ranges.append(("r", _run(self.text, value.source_position(offset), value.source_position(local_start, right=True), self.names)))
                    ranges.append(("barrier", None))
                    offset = local_end
                if offset < len(value.value):
                    ranges.append(("r", _run(self.text, value.source_position(offset), value.end, self.names)))
                cursor = right
            context = " ".join(value.value if kind == "r" else " " for kind, value in ranges)[:1600]
            zones, zone = [], []
            for kind, value in ranges:
                if kind == "barrier":
                    if zone:
                        zones.append(zone)
                    zone = []
                else:
                    zone.append((kind, value))
            if zone:
                zones.append(zone)
            for ordinal, zone in enumerate(zones, 1):
                if not any(character.isalpha() for kind, value in zone if kind == "r" for character in value.value):
                    continue
                if sum(len(value.value) if kind == "r" else 1 for kind, value in zone) > MAX_BLOCK_CHARACTERS or len(zone) > MAX_EVENTS:
                    raise FormaError(_LIMIT)
                runs, markers, markup, targets = 0, 0, [], []
                for kind, value in zone:
                    if kind == "r":
                        markup.append(f"<r{runs}>{escape(value.value)}</r{runs}>")
                        targets.append(_Run(value.start, value.end, value.value, (), ()))
                        runs += 1
                    else:
                        markup.append(f"<k{markers}/>")
                        markers += 1
                if max(runs, markers) > MAX_RUNS:
                    raise FormaError(_LIMIT)
                fragment += 1
                sid = f"epub-v13:{digest}:{block}:{ordinal}"
                self.segments.append(Segment(sid, f"EPUB · fragment {fragment}", "".join(markup), json.dumps({"format": "epub", "role": self.role, "block_text": context}, ensure_ascii=False, sort_keys=True)))
                self.targets[sid] = tuple(targets)
        self.segments.sort(key=lambda segment: self.targets[segment.id][0].start)
        for index, segment in enumerate(self.segments, 1):
            segment.location = f"EPUB · fragment {index}"
        self._comparison = [value for _, value in sorted(comparison)]

    def _gaps(self):
        runs = sorted((run for targets in self.targets.values() for run in targets), key=lambda run: run.start)
        gaps, cursor = [], 0
        for run in runs:
            if not cursor <= run.start < run.end <= len(self.text):
                raise FormaError(_MARKUP)
            gaps.append(self.text[cursor:run.start])
            cursor = run.end
        gaps.append(self.text[cursor:])
        return tuple(gaps)

    def structure_signature(self):
        return self.role, self.encoding, self.bom, self._signature

    def comparison_text(self):
        return "\n".join(self._comparison)

    def validate_package(self):
        self.check_cancel()

    def write(self, translations):
        if set(translations) != set(self.targets):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        replacements = []
        for segment in self.segments:
            self.check_cancel()
            issues = candidate_issues(segment, translations[segment.id])
            if issues:
                raise FormaError(issues[0].message)
            values = parse_translation(segment.source, translations[segment.id])
            for index, run in enumerate(self.targets[segment.id]):
                value = values[f"r{index}"]
                replacement = self.text[run.start:run.end] if value == run.value else html.escape(value, quote=False)
                if value != run.value:
                    endings = re.findall(r"\r\n|\r|\n", self.text[run.start:run.end])
                    if endings and len(endings) == value.count("\n"):
                        iterator = iter(endings)
                        replacement = re.sub("\n", lambda _, endings=iterator: next(endings), replacement)
                replacements.append((run.start, run.end, replacement))
        cursor, pieces = 0, []
        for start, end, replacement in sorted(replacements):
            pieces.extend((self.text[cursor:start], replacement))
            cursor = end
        pieces.append(self.text[cursor:])
        candidate = "".join(pieces)
        if len(candidate) > MAX_TEXT_CHARACTERS:
            raise FormaError("Текстовий документ завеликий. Ліміт — 4 мільйони символів.")
        try:
            encoded = self.bom + candidate.encode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise FormaError(_ENCODING) from exc
        if len(encoded) > MAX_TEXT_BYTES:
            raise FormaError("Текстовий файл завеликий. Ліміт — 16 МБ.")
        other = EpubContent(encoded, part=self.part, role=self.role, check_cancel=self.check_cancel)
        if other.structure_signature() != self.structure_signature():
            raise FormaError(_STRUCTURE)
        self.check_cancel()
        return encoded
