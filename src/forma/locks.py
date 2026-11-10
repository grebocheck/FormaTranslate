"""Keep identifiers and links out of the model's editable vocabulary."""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from lxml import etree

from forma.models import ValidationError
from forma.quality import immutable_spans


def _parse(markup: str):
    try:
        return etree.fromstring(
            f"<root>{markup}</root>".encode(),
            etree.XMLParser(resolve_entities=False, no_network=True),
        )
    except etree.XMLSyntaxError as exc:
        raise ValidationError("Модель повернула пошкоджені позначки форматування.") from exc


def lock_markup(markup: str) -> tuple[str, dict[str, str]]:
    root = _parse(markup)
    locks = {}
    for node in root:
        if not node.tag.startswith("r"):
            continue
        text = node.text or ""
        spans = immutable_spans(text)
        chunks, cursor = [], 0
        for start, end, value in spans:
            token = f"__FORMA_LOCK_{len(locks)}__"
            if token in markup:
                raise ValidationError("Текст містить зарезервовану позначку захисту.")
            locks[token] = value
            chunks.extend([text[cursor:start], token])
            cursor = end
        chunks.append(text[cursor:])
        node.text = "".join(chunks)
    result = "".join(etree.tostring(node, encoding="unicode", with_tail=False) for node in root)
    return result, locks


def lock_draft(markup: str, locks: dict[str, str]) -> str:
    result = markup
    for token, value in sorted(locks.items(), key=lambda item: len(item[1]), reverse=True):
        result = result.replace(escape(value), token, 1)
    return result


def unlock_markup(markup: str, locks: dict[str, str]) -> str:
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
