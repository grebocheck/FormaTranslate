"""Portable Office identities; file-local segment IDs never identify a TM entry."""

from __future__ import annotations

import hashlib
import json
import re

from forma.core.models import Segment
from forma.documents.ooxml import OfficeDocument, S, W

_WORD_BOUNDARIES = frozenset(
    f"{{{W}}}{name}"
    for name in ("p", "tab", "br", "cr", "footnoteReference", "endnoteReference", "sym")
)


def _digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _ancestor(node, tag):
    return next(node.iterancestors(f"{{{W}}}{tag}"), None)


def _cells(row):
    return [cell for cell in row.iter(f"{{{W}}}tc") if _ancestor(cell, "tr") is row]


def _visible_text(node):
    # Runs are formatting, but paragraphs and inline controls separate words.
    # A plain concatenation can make two different instructions hash equally.
    tokens, text = [], []
    for item in node.iter():
        if any(parent.tag == f"{{{W}}}del" for parent in item.iterancestors()):
            continue
        if item.tag == f"{{{W}}}t":
            text.append(item.text or "")
        elif item.tag in _WORD_BOUNDARIES:
            if text:
                tokens.append(["text", "".join(text)])
                text = []
            tokens.append([item.tag, sorted(item.attrib.items()) if item.tag != f"{{{W}}}p" else []])
    if text:
        tokens.append(["text", "".join(text)])
    return tokens


def _row_context(document, row):
    if row is None:
        return None
    cached = getattr(document, "_memory_row_contexts", None)
    if cached is None:
        cached = {}
        document._memory_row_contexts = cached
    if row in cached:
        return cached[row]
    properties = row.find(f"{{{W}}}trPr")
    geometry = [] if properties is None else [
        (child.tag, sorted(child.attrib.items())) for child in properties
        if child.tag in {f"{{{W}}}gridBefore", f"{{{W}}}gridAfter"}
    ]
    cells = []
    for cell in _cells(row):
        properties = cell.find(f"{{{W}}}tcPr")
        spans = [] if properties is None else [
            (child.tag, sorted(child.attrib.items())) for child in properties
            if child.tag in {f"{{{W}}}gridSpan", f"{{{W}}}vMerge", f"{{{W}}}hMerge"}
        ]
        cells.append({"geometry": spans, "text": _visible_text(cell)})
    # A wide row/header may serve hundreds of segments; hash it once.
    cached[row] = _digest({"geometry": geometry, "cells": cells})
    return cached[row]


def _xlsx_contexts(document):
    """Cache full equivalents of the extractor's selected worksheet excerpts.

    Include every use of a shared string, not only the three prompt examples.
    Drawings/comments without a cell target use a conservative workbook digest.
    """
    cached = getattr(document, "_memory_xlsx_contexts", None)
    if cached is not None:
        return cached
    names = document._sheet_names(document._tree("xl/workbook.xml"))
    shared_part = "xl/sharedStrings.xml"
    shared_items = list(document._tree(shared_part).iter(f"{{{S}}}si")) if shared_part in document.parts else []
    shared_values = [
        "".join(node.text or "" for node in document._spreadsheet_text_nodes(item)) for item in shared_items
    ]
    result, usages, worksheets = {}, {}, []
    for part in document.parts:
        if part not in names and not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", part):
            continue
        tree = document._tree(part)
        name = names.get(part, part.rsplit("/", 1)[-1].removesuffix(".xml"))
        rows, columns, coordinates, records = {}, {}, {}, {}
        for cell in tree.iter(f"{{{S}}}c"):
            kind, address = cell.get("t", ""), cell.get("r", "")
            if kind == "s":
                value = shared_values[document._shared_index(cell, shared_values)]
            elif kind == "inlineStr":
                value = "".join(node.text or "" for node in document._spreadsheet_text_nodes(cell))
            else:
                value = cell.findtext(f"{{{S}}}v", "")
            formula = cell.find(f"{{{S}}}f")
            formula_identity = None if formula is None else (formula.text, sorted(formula.attrib.items()))
            records[cell] = (address, kind, value, formula_identity)
            match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", address)
            if not match or not value:
                continue
            letters, row = match.groups()
            column = 0
            for letter in letters:
                column = column * 26 + ord(letter) - ord("A") + 1
            coordinates[cell] = (int(row), column)
            rows.setdefault(int(row), []).append(cell)
            columns.setdefault(column, []).append(cell)
        positions = {}
        for groups, coordinate in ((rows, 1), (columns, 0)):
            for cells in groups.values():
                cells.sort(key=lambda cell: coordinates[cell][coordinate])
                for index, cell in enumerate(cells):
                    positions[cell, coordinate] = index
        merges = [node.get("ref", "") for node in tree.iter(f"{{{S}}}mergeCell")]
        worksheet_identity = _digest({"cells": list(records.values()), "merges": merges})
        worksheets.append((name, worksheet_identity))
        for cell, record in records.items():
            if cell.get("t") not in {"inlineStr", "s"} or cell.find(f"{{{S}}}f") is not None:
                continue
            full = {"sheet": name, "cell": record[0], "merges": merges}
            if cell in coordinates:
                row, column = coordinates[cell]
                for label, cells, coordinate in (("row", rows[row], 1), ("column", columns[column], 0)):
                    index = positions[cell, coordinate]
                    selected = sorted({0, 1, index - 2, index - 1, index + 1, index + 2} - {index})
                    full[label] = [records[cells[j]] for j in selected if 0 <= j < len(cells)]
            else:
                # Missing/implicit cell addresses cannot safely reconstruct
                # selected neighbors. Invalidate on any worksheet change.
                full["worksheet_fallback"] = worksheet_identity
            identity = _digest(full)
            if cell.get("t") == "s":
                index = document._shared_index(cell, shared_values)
                usages.setdefault(index, []).append((name, record[0], identity))
            else:
                result[cell] = identity
    for index, uses in usages.items():
        result[shared_items[index]] = _digest(sorted(uses))
    cached = result, _digest(sorted(worksheets))
    document._memory_xlsx_contexts = cached
    return cached


def segment_role(document: OfficeDocument, segment: Segment) -> str:
    target = document.targets[segment.id]
    part = re.sub(r"\d+(?=\.xml$)", "", target.part)
    role = {"format": document.path.suffix.lower(), "part": part}
    role["markers"] = [(node.tag, sorted(node.attrib.items())) for node in target.markers]
    node = target.nodes[0]
    if document.path.suffix.lower() == ".docx":
        cell, paragraph = _ancestor(node, "tc"), _ancestor(node, "p")
        role["table"] = cell is not None
        if cell is not None:
            row = _ancestor(cell, "tr")
            role["column"] = _cells(row).index(cell) if row is not None else None
        if paragraph is not None:
            properties = paragraph.find(f"{{{W}}}pPr")
            if properties is not None:
                role["paragraph_role"] = [
                    [(item.tag, sorted(item.attrib.items())) for item in child.iter() if isinstance(item.tag, str)]
                    for child in properties
                    if child.tag in {f"{{{W}}}pStyle", f"{{{W}}}outlineLvl", f"{{{W}}}numPr"}
                ]
    return _digest(role)


def segment_context(document: OfficeDocument, segment: Segment) -> str:
    """Hash full neighbors/row headers as well as the human-readable prompt context.

    Prompt excerpts are deliberately truncated. Their equality must not hide a
    changed negation, identifier or value beyond that excerpt in a TM identity.
    Only the digest of the full context is stored alongside the short excerpt.
    """
    indices = getattr(document, "_memory_segment_indices", None)
    if indices is None:
        indices = {item.id: index for index, item in enumerate(document.segments)}
        document._memory_segment_indices = indices
    target = document.targets[segment.id]
    full = {"local": segment.context}
    cell = _ancestor(target.nodes[0], "tc") if document.path.suffix.lower() == ".docx" else None
    if cell is not None:
        row, table = _ancestor(cell, "tr"), _ancestor(cell, "tbl")
        if row is not None and table is not None:
            first = next(
                (item for item in table.iter(f"{{{W}}}tr") if _ancestor(item, "tbl") is table),
                None,
            )
            full["row"] = _row_context(document, row)
            full["header"] = _row_context(document, first)
    else:
        index = indices[segment.id]
        full["neighbors"] = [
            document.segments[position].plain
            for position in (index - 1, index + 1)
            if 0 <= position < len(document.segments)
            and document.targets[document.segments[position].id].part == target.part
        ]
        if document.path.suffix.lower() == ".xlsx":
            contexts, fallback = _xlsx_contexts(document)
            container = next(target.nodes[0].iterancestors(f"{{{S}}}c"), None)
            if container is None:
                container = next(target.nodes[0].iterancestors(f"{{{S}}}si"), None)
            full["worksheet_context"] = contexts.get(container, fallback)
    return json.dumps({"context": segment.context, "full_context_sha256": _digest(full)}, ensure_ascii=False, sort_keys=True)
