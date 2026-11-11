"""Bounded model excerpts with full bindings for the selected Office facts."""

from __future__ import annotations

import json
import re

from forma.documents.ooxml import S, W
from forma.memory.context import _ancestor, _cells, _digest


def excerpt(text: str, limit: int = 700) -> str:
    if len(text) <= limit:
        return text
    side = max(0, (limit - 3) // 2)
    return text[:side] + " … " + text[-side:] if side else "…"[:limit]


def _packed(binding, facts) -> str:
    return json.dumps({
        "selected_context_sha256": _digest(binding), "facts": facts,
    }, ensure_ascii=False, sort_keys=True)


def _word_row(document, row):
    cache = getattr(document, "_memory_readable_rows", None)
    if cache is None:
        cache = document._memory_readable_rows = {}
    if row not in cache:
        cells, full = _cells(row), []
        for cell in cells:
            paragraphs = []
            for paragraph in cell.iter(f"{{{W}}}p"):
                if _ancestor(paragraph, "tc") is not cell:
                    continue
                paragraphs.append("".join(
                    node.text or "" for node in paragraph.iter(f"{{{W}}}t")
                    if _ancestor(node, "p") is paragraph
                    and _ancestor(node, "tc") is cell
                    and not any(parent.tag == f"{{{W}}}del" for parent in node.iterancestors())
                ))
            full.append("\n".join(paragraphs))
        cache[row] = cells, [excerpt(value, 240) for value in full], _digest(full)
    return cache[row]


def _selected_cells(document, row, column):
    cells, values, binding = _word_row(document, row)
    selected = sorted({0, 1, column - 2, column - 1, column, column + 1, column + 2, len(cells) - 1})
    return binding, [
        {"column": index + 1, "text": values[index]}
        for index in selected if 0 <= index < len(cells)
    ]


def _word_context(document, segment):
    target = document.targets[segment.id]
    cell = _ancestor(target.nodes[0], "tc")
    row = _ancestor(cell, "tr") if cell is not None else None
    table = _ancestor(cell, "tbl") if cell is not None else None
    if row is not None and table is not None:
        cached = getattr(document, "_memory_readable_cells", None)
        if cached is None:
            cached = document._memory_readable_cells = {}
        if cell not in cached:
            column = _word_row(document, row)[0].index(cell)
            header = next(item for item in table.iter(f"{{{W}}}tr") if _ancestor(item, "tbl") is table)
            own_hash, own = _selected_cells(document, row, column)
            header_hash, first = _selected_cells(document, header, column)
            cached[cell] = _packed([own_hash, header_hash], {
                "current_column": column + 1, "current_row": own, "first_table_row": first,
            })
        return cached[cell]
    indices = getattr(document, "_memory_segment_indices", None)
    if indices is None:
        indices = document._memory_segment_indices = {item.id: i for i, item in enumerate(document.segments)}
    index = indices[segment.id]
    facts = [
        {"side": side, "text": document.segments[position].plain}
        for side, position in (("previous", index - 1), ("next", index + 1))
        if 0 <= position < len(document.segments)
        and document.targets[document.segments[position].id].part == target.part
    ]
    return _packed(facts, [{**fact, "text": excerpt(fact["text"])} for fact in facts])


def _xlsx_contexts(document):
    cache = getattr(document, "_memory_readable_xlsx", None)
    if cache is not None:
        return cache
    names = document._sheet_names(document._tree("xl/workbook.xml"))
    part = "xl/sharedStrings.xml"
    items = list(document._tree(part).iter(f"{{{S}}}si")) if part in document.parts else []
    shared = ["".join(n.text or "" for n in document._spreadsheet_text_nodes(item)) for item in items]
    cache, uses = {}, {}
    for part in document.parts:
        if part not in names and not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", part):
            continue
        rows, columns, positions, records, coordinates = {}, {}, {}, {}, {}
        for cell in document._tree(part).iter(f"{{{S}}}c"):
            kind, address = cell.get("t", ""), cell.get("r", "")
            if kind == "s":
                value = shared[document._shared_index(cell, shared)]
            elif kind == "inlineStr":
                value = "".join(n.text or "" for n in document._spreadsheet_text_nodes(cell))
            else:
                value = cell.findtext(f"{{{S}}}v", "")
            formula = cell.find(f"{{{S}}}f")
            records[cell] = {"cell": address, "value": value, "formula": None if formula is None else formula.text}
            match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", address)
            if match and value:
                letters, number = match.groups()
                column = 0
                for letter in letters:
                    column = column * 26 + ord(letter) - ord("A") + 1
                coordinates[cell] = int(number), column
                rows.setdefault(int(number), []).append(cell)
                columns.setdefault(column, []).append(cell)
        for groups, axis in ((rows, 1), (columns, 0)):
            for cells in groups.values():
                cells.sort(key=lambda cell: coordinates[cell][axis])
                positions.update(((cell, axis), i) for i, cell in enumerate(cells))
        for cell in records:
            if cell.get("t") not in {"s", "inlineStr"} or cell.find(f"{{{S}}}f") is not None:
                continue
            full = {"sheet": names.get(part, part), "cell": cell.get("r", "?")}
            if cell in coordinates:
                row, column = coordinates[cell]
                for label, cells, axis in (("row", rows[row], 1), ("column", columns[column], 0)):
                    index = positions[cell, axis]
                    selected = sorted({0, 1, index - 2, index - 1, index + 1, index + 2} - {index})
                    full[label] = [records[cells[j]] for j in selected if 0 <= j < len(cells)]
            rendered = {
                key: [{**record, "value": excerpt(record["value"], 240),
                       "formula": excerpt(record["formula"], 120) if record["formula"] else None}
                      for record in value] if isinstance(value, list) else value
                for key, value in full.items()
            }
            value = _digest(full), json.dumps(rendered, ensure_ascii=False, sort_keys=True)
            if cell.get("t") == "s":
                uses.setdefault(document._shared_index(cell, shared), []).append(value)
            else:
                cache[cell] = _packed(value[0], rendered)
    for index, values in uses.items():
        # Bind every occurrence even when only bounded examples fit the request.
        values.sort(key=lambda value: value[1])
        count = min(8, len(values))
        chosen = sorted({round(i * (len(values) - 1) / max(1, count - 1)) for i in range(count)})
        cache[items[index]] = _packed([value[0] for value in values], [
            excerpt(values[i][1], max(400, 4800 // count)) for i in chosen
        ])
    document._memory_readable_xlsx = cache
    return cache


def readable_context(document, segment) -> str:
    if document.path.suffix.lower() == ".docx":
        return _word_context(document, segment)
    target = document.targets[segment.id]
    container = next(target.nodes[0].iterancestors(f"{{{S}}}c"), None)
    if container is None:
        container = next(target.nodes[0].iterancestors(f"{{{S}}}si"), None)
    return _xlsx_contexts(document).get(container, segment.context)
