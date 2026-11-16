"""Bound chart labels to their embedded workbook cells before translating either.

Only local, simple A1 references with matching text are supported. Unsupported
or stale bindings remain unchanged and are reported by the Office extractor.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from zipfile import BadZipFile, ZipFile

from lxml import etree

from forma.core.models import FormaError
from forma.translation.quality import immutable_spans

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MAX_BOOK_BYTES = 64 * 1024 * 1024
_REFERENCE = re.compile(r"(?P<sheet>'(?:[^']|'')+'|[^'!\[\]:]+)!\$?(?P<col>[A-Z]{1,3})\$?(?P<row>[1-9]\d{0,6})(?::\$?(?P<endcol>[A-Z]{1,3})\$?(?P<endrow>[1-9]\d{0,6}))?\Z")


class UnsupportedChart(ValueError):
    pass


def translatable(value):
    text = value.strip()
    return any(char.isalpha() for char in text) and not any(
        start == 0 and end == len(text) for start, end, _ in immutable_spans(text)
    )


def _related(parts, part, identifier):
    from forma.documents.ooxml import parse_xml

    if not isinstance(identifier, str) or not identifier:
        raise UnsupportedChart
    directory, filename = posixpath.split(part)
    data = parts.get(f"{directory}/_rels/{filename}.rels")
    if data is None:
        raise UnsupportedChart
    root = parse_xml(data).getroot()
    if root.tag != f"{{{PR}}}Relationships":
        raise UnsupportedChart
    matches = [node for node in root if node.tag == f"{{{PR}}}Relationship" and node.get("Id") == identifier]
    if len(matches) != 1 or matches[0].get("TargetMode", "Internal") != "Internal":
        raise UnsupportedChart
    node = matches[0]
    target = node.get("Target", "")
    if "\\" in target or ":" in target or not target:
        raise UnsupportedChart
    resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else f"{directory}/{target}")
    if resolved not in parts or resolved.startswith("../"):
        raise UnsupportedChart
    return resolved, node.get("Type", "")


def _column(value):
    result = 0
    for char in value:
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def reference_cell(formula, index):
    match = _REFERENCE.fullmatch(formula or "")
    if match is None or index < 0:
        raise UnsupportedChart
    sheet, column, row, end_column, end_row = match.groups()
    row, end_row = int(row), int(end_row or row)
    start, end = _column(column), _column(end_column or column)
    if (start != end and row != end_row) or not 1 <= start <= end <= 16384 or not 1 <= row <= end_row <= 1048576:
        raise UnsupportedChart
    if index > max(end - start, end_row - row):
        raise UnsupportedChart
    current = start + index if start != end else start
    letters = ""
    while current:
        current, digit = divmod(current - 1, 26)
        letters = chr(65 + digit) + letters
    return sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet, f"{letters}{row + index if row != end_row else row}"


class EmbeddedBook:
    def __init__(self, data):
        from forma.documents.ooxml import parse_xml

        self.data = data
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                self.infos, self.comment = archive.infolist(), archive.comment
                if len(self.infos) > 2000 or sum(item.file_size for item in self.infos) > MAX_BOOK_BYTES or len({item.filename for item in self.infos}) != len(self.infos):
                    raise UnsupportedChart
                self.parts = {item.filename: archive.read(item) for item in self.infos}
            if any(name.startswith("_xmlsignatures/") for name in self.parts):
                raise UnsupportedChart
            self.trees = {name: parse_xml(raw) for name, raw in self.parts.items() if name.endswith(".xml")}
            workbook = self.trees["xl/workbook.xml"]
            if workbook.getroot().tag != f"{{{S}}}workbook":
                raise UnsupportedChart
            self.sheets = {}
            for node in workbook.iter(f"{{{S}}}sheet"):
                part, kind = _related(self.parts, "xl/workbook.xml", node.get(f"{{{R}}}id"))
                if kind != R + "/worksheet" or node.get("name") in self.sheets:
                    raise UnsupportedChart
                self.sheets[node.get("name")] = part
            shared = self.trees.get("xl/sharedStrings.xml")
            self.shared_binding_valid = False
            rels = parse_xml(self.parts["xl/_rels/workbook.xml.rels"]).getroot()
            shared_links = [node for node in rels if node.tag == f"{{{PR}}}Relationship" and node.get("Type") == R + "/sharedStrings"]
            if len(shared_links) == 1:
                try:
                    shared_part, _ = _related(self.parts, "xl/workbook.xml", shared_links[0].get("Id"))
                    self.shared_binding_valid = shared_part == "xl/sharedStrings.xml"
                except UnsupportedChart:
                    pass
            self.shared = list(shared.getroot().findall(f"{{{S}}}si")) if shared is not None else []
            self.cells = {}
            for sheet, part in self.sheets.items():
                for cell in self.trees[part].iter(f"{{{S}}}c"):
                    key = sheet, cell.get("r")
                    if key in self.cells:
                        raise UnsupportedChart
                    self.cells[key] = cell
        except (BadZipFile, KeyError, RuntimeError, etree.LxmlError, FormaError) as exc:
            raise UnsupportedChart from exc

    def text_node(self, key):
        cell = self.cells.get(key)
        if cell is None or cell.find(f"{{{S}}}f") is not None:
            raise UnsupportedChart
        kind = cell.get("t")
        if kind == "s":
            if not self.shared_binding_valid:
                raise UnsupportedChart
            try:
                index = int(cell.findtext(f"{{{S}}}v", ""))
                if not 0 <= index < len(self.shared):
                    raise UnsupportedChart
            except ValueError as exc:
                raise UnsupportedChart from exc
            nodes = list(self.shared[index].iter(f"{{{S}}}t"))
        elif kind == "inlineStr":
            nodes = list(cell.iter(f"{{{S}}}t"))
        elif kind == "str":
            nodes = list(cell.findall(f"{{{S}}}v"))
        else:
            raise UnsupportedChart
        # A cache has no rich-text run mapping. Preserve multi-run cells until
        # a separately validated mapping is available, instead of flattening it.
        if len(nodes) != 1:
            raise UnsupportedChart
        return nodes[0]

    def write(self, replacements):
        staged = {}
        clones = {}

        def editable(part, node):
            if part not in staged:
                staged[part] = deepcopy(self.trees[part])
            original = list(self.trees[part].iter())
            return list(staged[part].iter())[original.index(node)]

        for key, value in replacements.items():
            node = self.text_node(key)
            if value == (node.text or ""):
                continue
            cell = self.cells[key]
            part = self.sheets[key[0]]
            if cell.get("t") == "s":
                index = int(cell.findtext(f"{{{S}}}v"))
                clone_key = index, value
                if clone_key not in clones:
                    root = editable("xl/sharedStrings.xml", self.trees["xl/sharedStrings.xml"].getroot())
                    clone = deepcopy(self.shared[index])
                    text = next(clone.iter(f"{{{S}}}t"))
                    text.text = value
                    if value != value.strip():
                        text.set(SPACE, "preserve")
                    clones[clone_key] = len(self.shared) + len(clones)
                    anchor = next((child for child in root if child.tag != f"{{{S}}}si"), None)
                    if anchor is None:
                        root.append(clone)
                    else:
                        anchor.addprevious(clone)
                    if "uniqueCount" in root.attrib:
                        root.set("uniqueCount", str(len(self.shared) + len(clones)))
                editable(part, cell).find(f"{{{S}}}v").text = str(clones[clone_key])
            else:
                changed = editable(part, node)
                changed.text = value
                if value != value.strip():
                    changed.set(SPACE, "preserve")
        if not staged:
            return self.data
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.comment = self.comment
            for info in self.infos:
                data = self.parts[info.filename]
                if info.filename in staged:
                    data = etree.tostring(staged[info.filename], encoding="UTF-8", xml_declaration=data.lstrip().startswith(b"<?xml"), standalone=self.trees[info.filename].docinfo.standalone)
                archive.writestr(info, data)
        return buffer.getvalue()


@dataclass
class ChartLabel:
    key: str
    nodes: list
    workbook: str = ""
    cell: tuple = ()


class ChartLabels:
    def __init__(self, document, chart_parts):
        self.document, self.chart_parts = document, chart_parts
        self.groups, self.books, self.unsupported = {}, {}, False
        self._contexts = {}
        candidates = defaultdict(list)
        rejected = set()
        unbound_books = set()
        for part in chart_parts:
            tree = document._tree(part)
            external = list(tree.iter(f"{{{C}}}externalData"))
            workbook = ""
            if external:
                try:
                    if len(external) != 1:
                        raise UnsupportedChart
                    workbook, kind = _related(document.parts, part, external[0].get(f"{{{R}}}id"))
                    if kind != R + "/package" or not workbook.endswith(".xlsx"):
                        raise UnsupportedChart
                    if workbook not in self.books:
                        self.books[workbook] = EmbeddedBook(document.parts[workbook])
                except (UnsupportedChart, FormaError, etree.LxmlError):
                    workbook = ""
            for node in tree.iter(f"{{{C}}}v"):
                parent = node.getparent()
                container = parent.getparent() if parent is not None else None
                key, cell = (), ()
                if parent is not None and parent.tag == f"{{{C}}}tx":
                    key = (part, tree.getpath(node))
                elif parent is not None and parent.tag == f"{{{C}}}pt" and container is not None and container.tag in {f"{{{C}}}strCache", f"{{{C}}}strLit"}:
                    if container.tag == f"{{{C}}}strLit":
                        key = (part, tree.getpath(node))
                    else:
                        try:
                            if not workbook:
                                raise UnsupportedChart
                            ref = container.getparent()
                            cell = reference_cell(ref.findtext(f"{{{C}}}f"), int(parent.get("idx", "-1")))
                            key = (workbook, *cell)
                            source = self.books[workbook].text_node(cell).text or ""
                            if source != (node.text or ""):
                                rejected.add(key)
                                raise UnsupportedChart
                        except (UnsupportedChart, ValueError):
                            # An unsupported range/index could refer to any cell
                            # in this workbook. Updating another proved cache
                            # must not leave that unproved mirror stale.
                            if workbook and not key:
                                unbound_books.add(workbook)
                            self.unsupported |= translatable(node.text or "")
                            continue
                elif any(ancestor.tag in {f"{{{C}}}strCache", f"{{{C}}}multiLvlStrCache"} for ancestor in node.iterancestors()):
                    if workbook:
                        unbound_books.add(workbook)
                    self.unsupported |= translatable(node.text or "")
                if key:
                    candidates[key].append((part, node, workbook if cell else "", cell))
        for identity, items in candidates.items():
            if identity in rejected or items[0][2] in unbound_books or len({node.text or "" for _, node, _, _ in items}) != 1:
                self.unsupported |= any(translatable(node.text or "") for _, node, _, _ in items)
                continue
            if not translatable(items[0][1].text or ""):
                continue
            key = hashlib.sha256(repr(identity).encode()).hexdigest()
            self.groups[key] = ChartLabel(key, [(part, node) for part, node, _, _ in items], items[0][2], items[0][3])

    def _context_facts(self, part):
        if part not in self._contexts:
            drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
            tree = self.document._tree(part)
            facts = {"drawing_text": [], "series": [], "categories": [], "values": [], "formulas": []}
            for node in tree.iter():
                if node.tag == f"{{{drawing}}}t":
                    kind = "drawing_text"
                elif node.tag == f"{{{C}}}f":
                    kind = "formulas"
                elif node.tag == f"{{{C}}}v":
                    ancestors = {ancestor.tag for ancestor in node.iterancestors()}
                    kind = "series" if f"{{{C}}}tx" in ancestors else "categories" if f"{{{C}}}cat" in ancestors else "values"
                else:
                    continue
                facts[kind].append([tree.getpath(node), node.text or ""])
            serialized = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

            def excerpt(items):
                selected = items if len(items) <= 3 else [*items[:2], items[-1]]
                return [text if len(text) <= 80 else text[:55] + "…" + text[-24:] for _, text in selected]

            self._contexts[part] = (
                hashlib.sha256(serialized.encode()).hexdigest(),
                {key: excerpt(items) for key, items in facts.items() if items},
            )
        return self._contexts[part]

    def context(self, group):
        """Bound readable chart facts; hash every omitted point and linked chart."""
        parts = sorted({part for part, _ in group.nodes})
        facts = [(part, *self._context_facts(part)) for part in parts]
        complete = {
            "cell": group.cell, "charts": [(part, digest) for part, digest, _ in facts],
            "occurrences": [(part, self.document._tree(part).getpath(node)) for part, node in group.nodes],
        }
        digest = hashlib.sha256(json.dumps(complete, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        selected = facts if len(facts) <= 2 else [facts[0], facts[-1]]
        readable = {"cell": group.cell, "charts": [preview for _, _, preview in selected]}
        # Sheet names, titles and formulas can be arbitrarily long. Bound their
        # readable form while retaining the complete context digest above.
        if group.cell:
            readable["cell"] = [str(value)[:100] for value in group.cell]
        return (
            "Chart data label. All linked occurrences share this source cell/label. "
            "Use titles, series and categories to disambiguate its meaning. "
            + json.dumps(readable, ensure_ascii=False, separators=(",", ":"))
            + " Full chart context SHA256: " + digest
        )

    def write_workbooks(self, replacements):
        edits = defaultdict(dict)
        for key, text in replacements.items():
            group = self.groups[key]
            if group.workbook:
                edits[group.workbook][group.cell] = text
        return {part: self.books[part].write(values) for part, values in edits.items()}

    def layout_signature(self):
        from forma.documents.comparison import _xlsx_logical_parts
        from forma.documents.ooxml import A, parse_xml

        signatures = {}
        editable_nodes = defaultdict(set)
        for group in self.groups.values():
            for part, node in group.nodes:
                editable_nodes[part].add(self.document._tree(part).getpath(node))
        for part in self.chart_parts:
            tree = deepcopy(self.document._tree(part))
            for node in tree.iter():
                if node.tag == f"{{{A}}}t" or tree.getpath(node) in editable_nodes[part]:
                    node.text = "__TEXT__"
                    node.attrib.pop(SPACE, None)
            signatures[part] = etree.tostring(tree, method="c14n")
            directory, name = posixpath.split(part)
            relationships = f"{directory}/_rels/{name}.rels"
            signatures[relationships] = self.document.parts.get(relationships)
        for part, book in self.books.items():
            for member, raw in _xlsx_logical_parts(book.parts).items():
                if member.endswith(".xml"):
                    tree = parse_xml(raw)
                    for node in tree.iter():
                        parent = node.getparent()
                        if node.tag == f"{{{S}}}t" or (
                            node.tag == f"{{{S}}}v" and parent is not None
                            and parent.tag == f"{{{S}}}c" and parent.get("t") == "str"
                            and parent.find(f"{{{S}}}f") is None
                        ):
                            node.text = "__TEXT__"
                            node.attrib.pop(SPACE, None)
                    raw = etree.tostring(tree, method="c14n")
                signatures[part, member] = raw
        return signatures

    def compatible_layout(self, other):
        return (other is not None and self.groups.keys() == other.groups.keys()
                and self.layout_signature() == other.layout_signature())
