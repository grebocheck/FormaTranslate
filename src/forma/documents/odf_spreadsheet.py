"""ODS string cells with bounded physical ranges, never expanded repetitions."""
from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from forma.core.models import FormaError
from forma.documents.odf import OFFICE, STYLE, TABLE, TEXT

_STRUCTURE = "ODS містить неоднозначну структуру таблиці або стилів."
_COUNTERS = "ODS містить некоректні лічильники повторів або об’єднання клітинок."
_FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
_ROW_CONTAINERS = {f"{{{TABLE}}}{name}" for name in ("table-rows", "table-header-rows", "table-row-group")}
_COLUMN_CONTAINERS = {f"{{{TABLE}}}{name}" for name in ("table-columns", "table-header-columns", "table-column-group")}
_NATIVE_ATTRIBUTES = {f"{{{OFFICE}}}{name}" for name in ("value", "date-value", "time-value", "boolean-value", "currency")}
_PARAGRAPHS = {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}
_CODE_STYLES = {"source text", "preformatted text", "code", "html preformatted"}


def _positive(node, attribute):
    value = node.get(f"{{{TABLE}}}{attribute}", "1").strip()
    if not re.fullmatch(r"\+?[0-9]+", value):
        raise FormaError(_COUNTERS)
    digits = value.lstrip("+").lstrip("0")
    if not digits or len(digits) > 32:
        raise FormaError(_COUNTERS)
    return int(digits)


def _displayed(node):
    visibility = node.get(f"{{{TABLE}}}visibility", "visible")
    if visibility not in {"visible", "collapse", "filter"}:
        raise FormaError(_STRUCTURE)
    display = node.get(f"{{{TABLE}}}display", "true")
    if display not in {"true", "false", "1", "0"}:
        raise FormaError(_STRUCTURE)
    return visibility == "visible" and display in {"true", "1"}


class _Styles:
    def __init__(self, document):
        self.styles, self.defaults, self.cache = {}, {}, {}
        roots = []
        if "styles.xml" in document.parts:
            roots.append(document.tree("styles.xml"))
        roots.append(document.tree("content.xml"))
        for root in roots:
            seen = set()
            for node in root.iter():
                if node.tag not in {f"{{{STYLE}}}style", f"{{{STYLE}}}default-style"}:
                    continue
                document.check_cancel()
                family = node.get(f"{{{STYLE}}}family", "")
                if family not in {"table", "table-row", "table-column", "table-cell"}:
                    continue
                if node.tag == f"{{{STYLE}}}default-style":
                    self.defaults[family] = node
                    continue
                key = family, node.get(f"{{{STYLE}}}name", "")
                if not key[1] or key in seen:
                    raise FormaError(_STRUCTURE)
                seen.add(key)
                self.styles[key] = node

    @staticmethod
    def _properties(node):
        result = {}
        if node is not None:
            for child in node:
                if isinstance(child.tag, str) and child.tag.startswith(f"{{{STYLE}}}") and child.tag.endswith("-properties"):
                    result.update(child.attrib)
        return result

    def hidden(self, family, name=""):
        key = family, name
        if key not in self.cache:
            chain, seen, current, code = [], set(), name, False
            while current:
                if current in seen or len(seen) >= 64:
                    raise FormaError(_STRUCTURE)
                seen.add(current)
                if family == "table-cell" and current.replace("_20_", " ").casefold() in _CODE_STYLES:
                    code = True
                node = self.styles.get((family, current))
                if node is None:
                    break
                if family == "table-cell" and node.get(f"{{{STYLE}}}display-name", "").casefold() in _CODE_STYLES:
                    code = True
                chain.append(node)
                current = node.get(f"{{{STYLE}}}parent-style-name", "")
            properties = self._properties(self.defaults.get(family))
            for node in reversed(chain):
                properties.update(self._properties(node))
            self.cache[key] = (
                code or properties.get(f"{{{TABLE}}}display") in {"false", "0"}
                or properties.get(f"{{{TEXT}}}display") in {"none", "false", "0"}
                or properties.get(f"{{{_FO}}}visibility") in {"hidden", "collapse"}
                or "hidden-and-protected" in properties.get(f"{{{STYLE}}}cell-protect", "")
            )
        return self.cache[key]


def _physical_nodes(document, parent, local, containers, *, hidden=False, header=False):
    for node in parent:
        document.check_cancel()
        if node.tag == f"{{{TABLE}}}{local}":
            yield node, hidden or not _displayed(node), header
        elif node.tag in containers:
            yield from _physical_nodes(
                document, node, local, containers,
                hidden=hidden or not _displayed(node),
                header=header or node.tag == f"{{{TABLE}}}table-header-rows",
            )


@dataclass(frozen=True)
class _Column:
    start: int
    end: int
    hidden: bool
    default_hidden: bool


class _Columns:
    def __init__(self, document, sheet, styles):
        self.ranges, position = [], 1
        self.default_hidden = styles.hidden("table-cell", "")
        self.repeated = False
        for node, hidden, _ in _physical_nodes(document, sheet, "table-column", _COLUMN_CONTAINERS):
            repeats = _positive(node, "number-columns-repeated")
            self.repeated |= repeats > 1
            end = position + repeats - 1
            self.ranges.append(_Column(
                position, end,
                hidden or styles.hidden("table-column", node.get(f"{{{TABLE}}}style-name", "")),
                styles.hidden("table-cell", node.get(f"{{{TABLE}}}default-cell-style-name", "")),
            ))
            position = end + 1
        self.starts = [item.start for item in self.ranges]
        self.ends = [item.end for item in self.ranges]

    def hidden(self, start, end, *, use_default=False):
        left, right = bisect_left(self.ends, start), bisect_right(self.starts, end)
        uncovered = not self.ends or end > self.ends[-1]
        return (use_default and uncovered and self.default_hidden) or any(self.ranges[index].hidden or (use_default and self.ranges[index].default_hidden)
                   for index in range(left, right))


@dataclass(frozen=True)
class _Cell:
    paragraphs: tuple
    text: str
    start: int
    end: int
    repeats: int
    row_span: int
    column_span: int


@dataclass(frozen=True)
class _Row:
    cells: tuple[_Cell, ...]
    start: int
    end: int
    repeats: int
    header: bool


def _rows(document, sheet, styles, columns, skipped):
    result, row_position, repeated = [], 1, columns.repeated
    for row, hidden, header in _physical_nodes(document, sheet, "table-row", _ROW_CONTAINERS):
        repeats = _positive(row, "number-rows-repeated")
        repeated |= repeats > 1
        row_end = row_position + repeats - 1
        hidden |= styles.hidden("table-row", row.get(f"{{{TABLE}}}style-name", ""))
        row_default = row.get(f"{{{TABLE}}}default-cell-style-name", "")
        cells, position = [], 1
        for cell in row:
            document.check_cancel()
            if cell.tag not in {f"{{{TABLE}}}table-cell", f"{{{TABLE}}}covered-table-cell"}:
                continue
            count = _positive(cell, "number-columns-repeated")
            row_span = _positive(cell, "number-rows-spanned")
            column_span = _positive(cell, "number-columns-spanned")
            if repeats > 1 and row_span > 1:
                raise FormaError(_COUNTERS)
            repeated |= count > 1
            end = position + count - 1
            cell_style = cell.get(f"{{{TABLE}}}style-name", "") or row_default
            excluded = (
                hidden or not _displayed(cell) or not document.eligible(cell)
                or (bool(cell_style) and styles.hidden("table-cell", cell_style))
                or columns.hidden(position, end + column_span - 1, use_default=not cell_style)
            )
            if excluded:
                skipped.add("hidden")
            elif cell.tag == f"{{{TABLE}}}table-cell":
                kind = cell.get(f"{{{OFFICE}}}value-type", "")
                formula = f"{{{TABLE}}}formula" in cell.attrib
                if formula or kind not in {"", "string"} or _NATIVE_ATTRIBUTES.intersection(cell.attrib):
                    values = [cell.get(name) for name in sorted(_NATIVE_ATTRIBUTES | {f"{{{OFFICE}}}string-value"}) if name in cell.attrib]
                    if not values:
                        values = [document.paragraph_text(paragraph) for paragraph in cell if paragraph.tag in _PARAGRAPHS]
                    document.spreadsheet_comparison_values.extend(value for value in values if value)
                else:
                    paragraphs = tuple(child for child in cell if child.tag in _PARAGRAPHS)
                    if any(not document.eligible(paragraph) for paragraph in paragraphs):
                        skipped.add("hidden")
                    text = "\n".join(document.paragraph_text(paragraph) for paragraph in paragraphs)
                    if any(char.isalpha() for char in text):
                        if document.register_string_value(cell, paragraphs):
                            cells.append(_Cell(paragraphs, text, position, end, count, row_span, column_span))
                        else:
                            skipped.add("unpaired")
                    else:
                        scalar = cell.get(f"{{{OFFICE}}}string-value")
                        if scalar is not None and (not text or scalar != text):
                            skipped.add("unpaired")
                        if text:
                            document.spreadsheet_comparison_values.append(text)
                        elif scalar is not None and not paragraphs:
                            document.spreadsheet_comparison_values.append(scalar)
                    if any(child.tag not in _PARAGRAPHS for child in cell):
                        skipped.add("complex")
            position = end + 1
        if cells:
            result.append(_Row(tuple(cells), row_position, row_end, repeats, header))
        row_position = row_end + 1
    return result, repeated


def _record(cell):
    return {"columns": [cell.start, cell.end], "text": cell.text[:180]}


def _context(sheet, row, cell_index, headers, header_positions):
    cell = row.cells[cell_index]
    neighbors = sorted({0, 1, cell_index - 2, cell_index - 1, cell_index, cell_index + 1, cell_index + 2})
    neighbor_records = [_record(row.cells[index]) for index in neighbors if 0 <= index < len(row.cells)]
    header_cells = headers.cells
    starts, ends = header_positions
    left, right = bisect_left(ends, cell.start), bisect_right(starts, cell.end)
    selected = sorted({0, 1, left, left + 1, right - 2, right - 1})
    return json.dumps({
        "format": "ods", "sheet": sheet[:140],
        "row_range": [row.start, row.end], "column_range": [cell.start, cell.end],
        "row_repeats": row.repeats, "column_repeats": cell.repeats,
        "merged_rows": cell.row_span, "merged_columns": cell.column_span,
        "repetition_policy": "One physical cell; one translation applies to every repeated use.",
        "row_text": neighbor_records,
        "header_row_range": [headers.start, headers.end],
        "header_kind": "declared" if headers.header else "first_visible_text_row",
        "headers": [_record(header_cells[index]) for index in selected if 0 <= index < len(header_cells)],
        "headers_truncated": right - left > 4,
    }, ensure_ascii=False, sort_keys=True)


def extract_spreadsheet(document):
    root = document.tree("content.xml")
    spreadsheet = root.find(f"{{{OFFICE}}}body/{{{OFFICE}}}spreadsheet")
    if spreadsheet is None:
        raise FormaError(_STRUCTURE)
    document.spreadsheet_comparison_values = []
    styles, skipped, repeated = _Styles(document), set(), False
    for sheet_index, sheet in enumerate(spreadsheet.findall(f"{{{TABLE}}}table"), 1):
        document.check_cancel()
        if not _displayed(sheet) or styles.hidden("table", sheet.get(f"{{{TABLE}}}style-name", "")):
            skipped.add("hidden")
            continue
        columns = _Columns(document, sheet, styles)
        rows, has_repeated = _rows(document, sheet, styles, columns, skipped)
        repeated |= has_repeated
        if not rows:
            continue
        headers = next((row for row in rows if row.header), rows[0])
        header_positions = ([item.start for item in headers.cells], [item.end for item in headers.cells])
        name = sheet.get(f"{{{TABLE}}}name", str(sheet_index))
        for row in rows:
            for index, cell in enumerate(row.cells):
                context = _context(name, row, index, headers, header_positions)
                for paragraph_index, paragraph in enumerate(cell.paragraphs, 1):
                    location = (f"ODS · sheet {sheet_index} · rows {row.start}-{row.end} · "
                                f"columns {cell.start}-{cell.end} · paragraph {paragraph_index}")
                    document.add_paragraph("content.xml", paragraph, location, context=context, role="spreadsheet-cell")
    for node in spreadsheet.iter():
        if isinstance(node.tag, str) and node.tag.rsplit("}", 1)[-1] in {"annotation", "object", "object-ole", "chart", "frame", "shapes"}:
            skipped.add("complex")
    document.add_issue("ods_cells", "ODS: перекладається текст видимих рядкових клітинок. Формули, числові значення, дати, логічні значення, назви аркушів і структура таблиць збережені.")
    if repeated:
        document.add_issue("ods_repeated", "Повторені рядки та клітинки ODS не розгортаються: один переклад застосовується до всіх повторів спільної клітинки.")
    messages = {
        "hidden": "Приховані клітинки ODS і спільні клітинки, що охоплюють приховані стовпці, залишено без перекладу.",
        "unpaired": "Клітинки ODS з непідтвердженим або відсутнім текстовим представленням значення залишено без перекладу.",
        "complex": "Примітки, діаграми, вкладені об’єкти та складний вміст клітинок ODS залишено без перекладу.",
    }
    for code in sorted(skipped):
        document.add_issue("ods_" + code, messages[code])
