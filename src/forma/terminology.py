"""Explicit English normalization for units and technical abbreviations."""

import re

from lxml import etree

from forma.locks import lock_markup, unlock_markup

TERMS = {
    "кВа": "kVA",
    "кВА": "kVA",
    "кВ": "kV",
    "КВПіА": "I&C",
}


def curate_english(markup: str) -> tuple[str, list[str]]:
    locked, keys = lock_markup(markup)
    root = etree.fromstring(
        f"<root>{locked}</root>".encode(), etree.XMLParser(resolve_entities=False, no_network=True)
    )
    changes = []
    for node in root:
        if not node.tag.startswith("r"):
            continue
        text = node.text or ""
        for source, target in TERMS.items():
            updated = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, text)
            if updated != text:
                changes.append(f"{source} → {target}")
                text = updated
        node.text = text
    result = "".join(etree.tostring(n, encoding="unicode", with_tail=False) for n in root)
    return unlock_markup(result, keys), sorted(set(changes))
