"""Explicit Word document/section scopes, independent of sampled body text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from forma.documents.ooxml import W


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Scope:
    sha256: str
    prompt: str


def _outline(properties):
    node = properties.find(f"{{{W}}}outlineLvl") if properties is not None else None
    if node is None:
        return None
    value = node.get(f"{{{W}}}val", "")
    return int(value) if value in {str(i) for i in range(10)} else 9


def _paragraph_text(paragraph):
    values = []
    for node in paragraph.iter():
        if next(node.iterancestors(f"{{{W}}}p"), None) is not paragraph:
            continue
        if any(parent.tag == f"{{{W}}}del" for parent in node.iterancestors()):
            continue
        if node.tag == f"{{{W}}}t":
            values.append(node.text or "")
        elif node.tag in {f"{{{W}}}tab", f"{{{W}}}br", f"{{{W}}}cr"}:
            values.append("\n")
    return "".join(values)


class _Styles:
    def __init__(self, document):
        self.styles, self.cache, self.default = {}, {}, ""
        parts = document._related_parts("word/document.xml", "styles")
        part = parts[0] if len(parts) == 1 else "word/styles.xml"
        self.defaults = None
        if part not in document.parts:
            return
        root = document._tree(part).getroot()
        self.defaults = _outline(root.find(f"{{{W}}}docDefaults/{{{W}}}pPrDefault/{{{W}}}pPr"))
        for node in root.findall(f"{{{W}}}style"):
            if node.get(f"{{{W}}}type") != "paragraph":
                continue
            key = node.get(f"{{{W}}}styleId", "")
            self.styles[key] = node
            if node.get(f"{{{W}}}default", "").lower() in {"1", "true", "on"}:
                self.default = key

    def role(self, key):
        if key in self.cache:
            return self.cache[key]
        seen, current, outline, title = set(), key, None, ""
        while current in self.styles and current not in seen and len(seen) < 128:
            seen.add(current)
            style = self.styles[current]
            name = style.find(f"{{{W}}}name")
            # Title/Subtitle are explicit roles, not font/name substring
            # guess. Derived paragraph styles inherit that role as well.
            declared = [current.casefold(), name.get(f"{{{W}}}val", "").casefold() if name is not None else ""]
            if not title:
                title = next((value for value in declared if value in {"title", "subtitle"}), "")
            if outline is None:
                outline = _outline(style.find(f"{{{W}}}pPr"))
            parent = style.find(f"{{{W}}}basedOn")
            current = parent.get(f"{{{W}}}val", "") if parent is not None else ""
        result = (self.defaults if outline is None else outline), title
        self.cache[key] = result
        return result

    def paragraph(self, paragraph):
        properties = paragraph.find(f"{{{W}}}pPr")
        style = properties.find(f"{{{W}}}pStyle") if properties is not None else None
        key = style.get(f"{{{W}}}val", self.default) if style is not None else self.default
        inherited, title = self.role(key)
        direct = _outline(properties)
        return (inherited if direct is None else direct), title


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = (limit - 3) * 2 // 3
    return text[:head] + " … " + text[-(limit - 3 - head):]


def _text_binding(text, bindings):
    if text not in bindings:
        bindings[text] = _digest(text)
    return bindings[text]


def _anchor_scope(title, anchor_kind, bindings):
    identity = {"kind": anchor_kind, "anchors": [(kind, _text_binding(text, bindings)) for kind, text in title]}
    lines = []
    chosen = sorted({round(i * (len(title) - 1) / max(1, min(4, len(title)) - 1)) for i in range(min(4, len(title)))})
    for index in chosen:
        kind, text = title[index]
        label = {"lead": "Document lead: ", "title": "Document title: ", "subtitle": "Document subtitle: "}[kind]
        lines.append(label + _excerpt(text, 900 // max(1, len(chosen))))
    return Scope(_digest(identity), "\n".join(lines))


def _make_scope(title, headings, bindings, *, story="body", aggregate=None):
    # Hash complete texts once per document. Long titles shared by thousands
    # of sections must not be serialized again for every section/paragraph.
    identity = {"version": 2, "story": story, "title": title.sha256,
                "headings": [(level, _text_binding(text, bindings)) for level, text in headings]}
    if aggregate is not None:
        identity["all_section_headings"] = [(level, _text_binding(text, bindings)) for level, text in aggregate]
    lines = [title.prompt] if title.prompt else []
    for level, text in headings:
        lines.append(f"Section level {level + 1}: " + _excerpt(text, 280))
    if aggregate:
        for level, text in aggregate[:6]:
            lines.append(f"Document section level {level + 1}: " + _excerpt(text, 300))
    return Scope(_digest(identity), "\n".join(lines)[:4000])


def word_scope(document, segment):
    cached = getattr(document, "_memory_word_scopes", None)
    if cached is None:
        styles = _Styles(document)
        tree = document._tree("word/document.xml")
        paragraphs = list(tree.iter(f"{{{W}}}p"))
        roles = {paragraph: styles.paragraph(paragraph) for paragraph in paragraphs}
        outside_tables = [paragraph for paragraph in paragraphs if not any(parent.tag in {f"{{{W}}}tc", f"{{{W}}}p"} for parent in paragraph.iterancestors())]
        title_node = None
        for paragraph in outside_tables:
            level, kind = roles[paragraph]
            if not _paragraph_text(paragraph).strip():
                continue
            if kind:
                title_node = paragraph
                break
            if level is not None and level < 9:
                break
        anchor_kind, anchors = ("title" if title_node is not None else "lead"), {}
        if title_node is not None:
            seen_title = False
            for paragraph in outside_tables[outside_tables.index(title_node):]:
                level, kind = roles[paragraph]
                if kind == "title" and seen_title or not kind and level is not None and level < 9:
                    break
                if kind:
                    text = _paragraph_text(paragraph)
                    if text.strip():
                        anchors[paragraph] = kind, text
                        seen_title |= kind == "title"
        else:
            title_node = next((paragraph for paragraph in outside_tables or paragraphs if _paragraph_text(paragraph).strip()), None)
            if title_node is not None and roles[title_node][0] is not None and roles[title_node][0] < 9:
                # An outline heading scopes its own section. Promoting it to
                # a global lead would invalidate independent sibling sections.
                title_node = None
            if title_node is not None:
                anchors[title_node] = "lead", _paragraph_text(title_node)
        bindings = {}
        title = _anchor_scope(list(anchors.values()), anchor_kind, bindings)
        headings, all_headings, scopes = [], [], {}
        current = _make_scope(title, headings, bindings)
        for paragraph in paragraphs:
            level, is_title = roles[paragraph]
            nested = any(parent.tag in {f"{{{W}}}tc", f"{{{W}}}p"} for parent in paragraph.iterancestors())
            text = _paragraph_text(paragraph)
            if paragraph in anchors:
                # Normal cover blocks have only a handful of anchors. Keep a
                # very large metadata block fully bound, including self, rather
                # than constructing a quadratic series of exclusion scopes.
                own = _anchor_scope([value for node, value in anchors.items() if node is not paragraph], anchor_kind, bindings) if len(anchors) <= 16 else title
                scopes[paragraph] = _make_scope(own, [], bindings)
                continue
            if not nested and (is_title == "title" or level is not None and level < 9) and text.strip():
                level = 0 if is_title == "title" else level
                headings = [(old_level, old_text) for old_level, old_text in headings if old_level < level]
                scopes[paragraph] = _make_scope(title, headings, bindings)
                headings.append((level, text))
                all_headings.append((level, text))
                current = _make_scope(title, headings, bindings)
            else:
                scopes[paragraph] = current
        cached = scopes, title, all_headings, {}, bindings
        document._memory_word_scopes = cached
    scopes, title, all_headings, stories, bindings = cached
    target = document.targets[segment.id]
    if target.part == "word/document.xml":
        paragraph = next((node for node in target.nodes[0].iterancestors(f"{{{W}}}p") if node in scopes), None)
        if paragraph is not None:
            return scopes[paragraph]
    # A shared header/note/chart can serve several body sections. Bind all
    # explicit headings rather than inventing a single owning section.
    story = re.sub(r"\d+(?=\.xml$)", "", target.part)
    if story not in stories:
        stories[story] = _make_scope(title, [], bindings, story=story, aggregate=all_headings)
    return stories[story]
