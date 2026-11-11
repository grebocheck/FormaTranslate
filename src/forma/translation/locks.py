"""Keep identifiers and links out of the model's editable vocabulary."""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from lxml import etree

from forma.core.models import ValidationError
from forma.translation.quality import immutable_spans

_LOCK_TOKEN = re.compile(r"__FORMA_LOCK_\d+__")


def _parse(markup: str):
    try:
        return etree.fromstring(
            f"<root>{markup}</root>".encode(),
            etree.XMLParser(resolve_entities=False, no_network=True),
        )
    except etree.XMLSyntaxError as exc:
        raise ValidationError("Модель повернула пошкоджені позначки форматування.") from exc


class _Locks(dict):
    def __init__(self):
        super().__init__()
        self.tags = {}


def _protected_nodes(root):
    # Formatting can split one identifier across several text nodes. Match the
    # visible stream once, then intersect spans with their original nodes.
    bounds, parts, offset = [], [], 0
    for node in root:
        text = (node.text or "") if node.tag.startswith("r") else "\n"
        if node.tag.startswith("r"):
            bounds.append((node, offset, offset + len(text)))
        parts.append(text)
        offset += len(text)
    spans = immutable_spans("".join(parts))
    first = 0
    for node, start, end in bounds:
        while first < len(spans) and spans[first][1] <= start:
            first += 1
        selected, index = [], first
        while index < len(spans) and spans[index][0] < end:
            left, right = max(start, spans[index][0]), min(end, spans[index][1])
            if left < right:
                selected.append((left - start, right - start, (node.text or "")[left - start:right - start]))
            index += 1
        yield node, selected


def lock_markup(markup: str) -> tuple[str, dict[str, str]]:
    root = _parse(markup)
    locks = _Locks()
    for node, spans in _protected_nodes(root):
        text = node.text or ""
        chunks, cursor = [], 0
        for start, end, value in spans:
            token = f"__FORMA_LOCK_{len(locks)}__"
            if token in markup:
                raise ValidationError("Текст містить зарезервовану позначку захисту.")
            locks[token] = value
            locks.tags[token] = node.tag
            chunks.extend([text[cursor:start], token])
            cursor = end
        chunks.append(text[cursor:])
        node.text = "".join(chunks)
    result = "".join(etree.tostring(node, encoding="unicode", with_tail=False) for node in root)
    return result, locks


def lock_draft(markup: str, locks: dict[str, str]) -> str:
    if isinstance(locks, _Locks):
        root = _parse(markup)
        available = {}
        for token, value in locks.items():
            available.setdefault((locks.tags[token], value), []).append(token)
        for node, spans in _protected_nodes(root):
            text, chunks, cursor = node.text or "", [], 0
            for start, end, value in spans:
                tokens = available.get((node.tag, value))
                if tokens:
                    chunks.extend([text[cursor:start], tokens.pop(0)])
                    cursor = end
            chunks.append(text[cursor:])
            node.text = "".join(chunks)
        return "".join(etree.tostring(node, encoding="unicode", with_tail=False) for node in root)
    result = markup
    for token, value in sorted(locks.items(), key=lambda item: len(item[1]), reverse=True):
        result = result.replace(escape(value), token, 1)
    return result


def unlock_markup(markup: str, locks: dict[str, str]) -> str:
    if isinstance(locks, _Locks):
        root = _parse(markup)
        owners = {token: [] for token in locks}
        for node in root:
            for match in _LOCK_TOKEN.finditer(node.text or ""):
                if match[0] in owners:
                    owners[match[0]].append(node.tag)
        if any(owners[token] != [locks.tags[token]] for token in locks):
            raise ValidationError("Модель змінила захищений ідентифікатор або посилання.")
    for token, value in locks.items():
        if markup.count(token) != 1:
            raise ValidationError("Модель змінила захищений ідентифікатор або посилання.")
        markup = markup.replace(token, escape(value))
    return markup


def english_decimals(markup: str) -> str:
    """Normalize decimal punctuation while locked references and IDs remain opaque."""
    root = _parse(markup)
    for node in root:
        if node.tag.startswith("r"):
            node.text = re.sub(r"(?<=\d),(?=\d)", ".", node.text or "")
    return "".join(etree.tostring(node, encoding="unicode", with_tail=False) for node in root)
