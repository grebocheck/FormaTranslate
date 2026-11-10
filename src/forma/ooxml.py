"""Surgical OOXML editing: untouched ZIP members retain their original bytes."""

from __future__ import annotations

import io
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

from lxml import etree

from forma.models import ISSUE_EXPLANATIONS, FormaError, Issue, Segment
from forma.quality import immutable_spans
from forma.storage import atomic_bytes

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
NS = {"w": W, "a": A, "s": S}
MAX_UNPACKED = 512 * 1024 * 1024
MAX_XML = 64 * 1024 * 1024


def parse_xml(data: bytes) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    tree = etree.parse(io.BytesIO(data), parser)
    if tree.docinfo.doctype:
        raise FormaError("XML із DTD не підтримується.")
    return tree


def tag_name(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


@dataclass
class Target:
    part: str
    nodes: list[etree._Element]
    markers: list[etree._Element] = field(default_factory=list)


class OfficeDocument:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.parts: dict[str, bytes] = {}
        self.trees: dict[str, etree._ElementTree] = {}
        self._orders: dict[str, dict] = {}
        self.segments: list[Segment] = []
        self.targets: dict[str, Target] = {}
        self.issues: list[Issue] = []
        if self.path.suffix.lower() not in (".docx", ".xlsx"):
            raise FormaError("Цей рушій підтримує DOCX і XLSX.")
        try:
            with ZipFile(self.path) as archive:
                infos = archive.infolist()
                if sum(x.file_size for x in infos) > MAX_UNPACKED or len(infos) > 20000:
                    raise FormaError(
                        "Документ завеликий для безпечного розпакування (ліміт 512 МБ)."
                    )
                if len({x.filename for x in infos}) != len(infos):
                    raise FormaError("Документ містить дублікати частин ZIP.")
                self.infos = infos
                self.zip_comment = archive.comment
                self.parts = {x.filename: archive.read(x) for x in infos}
        except (BadZipFile, RuntimeError) as exc:
            raise FormaError("Файл пошкоджений, зашифрований або не є документом Office.") from exc
        if "[Content_Types].xml" not in self.parts:
            raise FormaError("У файлі відсутня структура Office Open XML.")
        if any(name.startswith("_xmlsignatures/") for name in self.parts):
            raise FormaError(
                "Документ має цифровий підпис. Для перекладу створіть непідписану копію."
            )
        try:
            self._extract()
        except etree.XMLSyntaxError as exc:
            raise FormaError("У документі знайдено некоректний XML.") from exc

    def _tree(self, part: str) -> etree._ElementTree:
        if len(self.parts[part]) > MAX_XML:
            raise FormaError(f"XML-частина документа завелика: {part}")
        if part not in self.trees:
            self.trees[part] = parse_xml(self.parts[part])
        return self.trees[part]

    def _add(
        self,
        part: str,
        location: str,
        nodes: list,
        markers: list | None = None,
        *,
        context: str = "",
    ) -> None:
        if not nodes or not any(c.isalpha() for n in nodes for c in (n.text or "")):
            return
        # Use the same protected-value rules as translation validation. Uppercase
        # descriptive labels such as IN-SERVICE are words, not equipment codes.
        plain = "".join(n.text or "" for n in nodes).strip()
        if any(start == 0 and end == len(plain) for start, end, _ in immutable_spans(plain)):
            return
        marker_nodes = markers or []
        node_ids = {id(n): i for i, n in enumerate(nodes)}
        marker_ids = {id(n): i for i, n in enumerate(marker_nodes)}
        if part not in self._orders:
            self._orders[part] = {n: i for i, n in enumerate(self._tree(part).iter())}
        order = self._orders[part]
        events = sorted([*nodes, *marker_nodes], key=lambda n: order[n])
        source = "".join(
            f"<r{node_ids[id(n)]}>{escape(n.text or '')}</r{node_ids[id(n)]}>"
            if id(n) in node_ids
            else f"<k{marker_ids[id(n)]}/>"
            for n in events
        )
        sid = f"{part}#{len(self.segments)}"
        self.segments.append(Segment(sid, location, source, context))
        self.targets[sid] = Target(part, nodes, marker_nodes)

    def _extract(self) -> None:
        if self.path.suffix.lower() == ".docx":
            if "word/document.xml" not in self.parts:
                raise FormaError("У DOCX відсутня основна частина документа.")
            for part in self.parts:
                if re.fullmatch(
                    r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml", part
                ):
                    self._word(part)
                elif re.fullmatch(r"word/(charts|diagrams)/[^/]+\.xml", part):
                    self._drawing(part)
        else:
            self._excel()
        for i, segment in enumerate(self.segments):
            if segment.context:
                continue
            neighbors = self.segments[max(0, i - 1) : i] + self.segments[i + 1 : i + 2]
            # ZIP part boundaries and shared-string storage order are not reading order.
            segment.context = "\n".join(
                n.plain[:700]
                for n in neighbors
                if self.targets[n.id].part == self.targets[segment.id].part
            )

    def _word(self, part: str) -> None:
        tree = self._tree(part)
        if etree.QName(tree.getroot()).namespace != W:
            raise FormaError(
                "Strict OOXML поки не підтримується. Збережіть копію як звичайний DOCX."
            )
        text_fields = {"TOC", "TOA", "REF", "HYPERLINK", "INDEX"}
        field_stack = []
        protected = set()
        for node in tree.iter():
            if node.tag == f"{{{W}}}fldChar":
                kind = node.get(f"{{{W}}}fldCharType")
                if kind == "begin":
                    field_stack.append({"instruction": "", "result": False})
                elif kind == "separate" and field_stack:
                    field_stack[-1]["result"] = True
                elif kind == "end" and field_stack:
                    field_stack.pop()
            elif node.tag == f"{{{W}}}instrText" and field_stack:
                field_stack[-1]["instruction"] += node.text or ""
            if node.tag == f"{{{W}}}t" and any(
                not field["result"]
                or not field["instruction"].strip()
                or field["instruction"].strip().split()[0].upper() not in text_fields
                for field in field_stack
            ):
                protected.add(node)
        if protected:
            self.issues.append(
                Issue(
                    "fields_preserved",
                    "Коди полів і номери сторінок збережено; видимий текст змісту та посилань перекладається.",
                    severity="info",
                )
            )
        if tree.find(".//w:ins", NS) is not None or tree.find(".//w:del", NS) is not None:
            self.issues.append(
                Issue(
                    "tracked_changes",
                    ISSUE_EXPLANATIONS["tracked_changes"],
                    severity="info",
                )
            )
        table_contexts = self._word_table_contexts(tree)
        for index, paragraph in enumerate(tree.iter(f"{{{W}}}p"), 1):
            nodes, markers = [], []
            for node in paragraph.iter():
                nearest = next(node.iterancestors(f"{{{W}}}p"), None)
                if node is paragraph or nearest is not paragraph:
                    continue
                if node.tag == f"{{{W}}}t":
                    if node in protected or any(
                        a.tag == f"{{{W}}}del"
                        or (
                            a.tag == f"{{{W}}}fldSimple"
                            and (a.get(f"{{{W}}}instr", "").strip().split() or [""])[0].upper()
                            not in text_fields
                        )
                        for a in node.iterancestors()
                    ):
                        continue
                    nodes.append(node)
                elif node.tag in {
                    f"{{{W}}}{name}"
                    for name in ("tab", "br", "cr", "footnoteReference", "endnoteReference", "sym")
                }:
                    markers.append(node)
            cell = next(paragraph.iterancestors(f"{{{W}}}tc"), None)
            self._add(
                part,
                f"{Path(part).stem} · абзац {index}",
                nodes,
                markers,
                context=table_contexts.get(cell, ""),
            )
        self._drawing(part)

    @staticmethod
    def _word_table_contexts(tree: etree._ElementTree) -> dict:
        contexts = {}
        for table in tree.iter(f"{{{W}}}tbl"):
            rows = [
                row
                for row in table.iter(f"{{{W}}}tr")
                if next(row.iterancestors(f"{{{W}}}tbl"), None) is table
            ]
            first_row = ""
            for row_index, row in enumerate(rows, 1):
                cells = [
                    cell
                    for cell in row.iter(f"{{{W}}}tc")
                    if next(cell.iterancestors(f"{{{W}}}tr"), None) is row
                ]
                values = []
                for cell in cells:
                    text = " ".join(
                        "".join(
                            n.text or ""
                            for n in paragraph.iter(f"{{{W}}}t")
                            if next(n.iterancestors(f"{{{W}}}tc"), None) is cell
                            and next(n.iterancestors(f"{{{W}}}p"), None) is paragraph
                            and not any(a.tag == f"{{{W}}}del" for a in n.iterancestors())
                        )
                        for paragraph in cell.iter(f"{{{W}}}p")
                        if next(paragraph.iterancestors(f"{{{W}}}tc"), None) is cell
                    )
                    values.append(" ".join(text.split())[:160])
                row_text = " | ".join(values)[:1000]
                if row_index == 1:
                    first_row = row_text
                for cell_index, cell in enumerate(cells, 1):
                    lines = [f"Table row {row_index}, cell {cell_index}: {row_text}"]
                    if row_index > 1 and first_row:
                        lines.append(f"Table first row: {first_row}")
                    contexts[cell] = "\n".join(lines)
        return contexts

    def _drawing(self, part: str) -> None:
        for index, paragraph in enumerate(self._tree(part).iter(f"{{{A}}}p"), 1):
            self._add(part, f"{Path(part).stem} · напис {index}", list(paragraph.iter(f"{{{A}}}t")))

    def _excel(self) -> None:
        if "xl/workbook.xml" not in self.parts:
            raise FormaError("У XLSX відсутня книга.")
        workbook = self._tree("xl/workbook.xml")
        if etree.QName(workbook.getroot()).namespace != S:
            raise FormaError(
                "Strict OOXML поки не підтримується. Збережіть копію як звичайний XLSX."
            )
        sheet_names = self._sheet_names(workbook)
        shared_part = "xl/sharedStrings.xml"
        shared_items = (
            list(self._tree(shared_part).iter(f"{{{S}}}si")) if shared_part in self.parts else []
        )
        shared_values = [
            "".join(n.text or "" for n in self._spreadsheet_text_nodes(item))
            for item in shared_items
        ]
        shared_locations: dict[int, list[str]] = {}
        shared_contexts: dict[int, dict[str, list[str]]] = {}
        comment_sheets: dict[str, str] = {}
        for part in self.parts:
            if part in sheet_names or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", part):
                tree = self._tree(part)
                name = sheet_names.get(part, Path(part).stem)
                for comment_part in self._related_parts(part, "comments"):
                    comment_sheets[comment_part] = name
                contexts = self._sheet_contexts(tree, name, shared_values)
                for cell in tree.iter(f"{{{S}}}c"):
                    if cell.find(f"{{{S}}}f") is not None:
                        continue
                    address = f"{name}!{cell.get('r', '?')}"
                    if cell.get("t") == "inlineStr":
                        inline = cell.find(f"{{{S}}}is")
                        if inline is not None:
                            self._add(
                                part,
                                address,
                                self._spreadsheet_text_nodes(inline),
                                context=contexts[cell],
                            )
                    elif cell.get("t") == "s":
                        index = self._shared_index(cell, shared_values)
                        shared_locations.setdefault(index, []).append(address)
                        examples = shared_contexts.setdefault(index, {}).setdefault(part, [])
                        if len(examples) < 3:
                            examples.append(contexts[cell])
        for index, item in enumerate(shared_items):
            locations = shared_locations.get(index)
            if locations:
                # Show distinct worksheets first, then other uses of the same shared string.
                groups = list(shared_contexts[index].values())
                examples = [group[i] for i in range(3) for group in groups if len(group) > i][:3]
                self._add(
                    shared_part,
                    ", ".join(locations[:4]),
                    self._spreadsheet_text_nodes(item),
                    context="\n\n".join(examples)[:3000],
                )
        for part in self.parts:
            if re.fullmatch(r"xl/(drawings|charts)/[^/]+\.xml", part):
                self._drawing(part)
            elif part in comment_sheets or re.fullmatch(
                r"xl/(?:comments[^/]*|comments/[^/]+)\.xml", part
            ):
                for comment in self._tree(part).iter(f"{{{S}}}comment"):
                    address = comment.get("ref", "")
                    sheet = comment_sheets.get(part, "")
                    self._add(
                        part,
                        f"Примітка {sheet + '!' if sheet else ''}{address}",
                        self._spreadsheet_text_nodes(comment),
                        context=f"Worksheet: {sheet}; comment on cell {address}" if sheet else "",
                    )
        self.issues.append(
            Issue(
                "sheet_names_preserved",
                "Назви аркушів, формули, числові комірки та кеші діаграм залишено без змін.",
            )
        )

    def _related_parts(self, part: str, relationship_type: str) -> list[str]:
        directory, filename = posixpath.split(part)
        relationships = f"{directory}/_rels/{filename}.rels"
        if relationships not in self.parts:
            return []
        targets = []
        for relation in self._tree(relationships).getroot():
            if relation.get("TargetMode") == "External" or not relation.get("Type", "").endswith(
                f"/{relationship_type}"
            ):
                continue
            target = relation.get("Target", "")
            resolved = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else f"{directory}/{target}"
            )
            if resolved in self.parts:
                targets.append(resolved)
        return targets

    def _sheet_names(self, workbook: etree._ElementTree) -> dict[str, str]:
        relationships = "xl/_rels/workbook.xml.rels"
        if relationships not in self.parts:
            return {}
        targets = {}
        for relation in self._tree(relationships).getroot():
            if relation.get("TargetMode") == "External" or not relation.get("Type", "").endswith(
                "/worksheet"
            ):
                continue
            target = relation.get("Target", "")
            part = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            )
            if part in self.parts:
                targets[relation.get("Id")] = part
        return {
            targets[sheet.get(f"{{{R}}}id")]: sheet.get("name", "Аркуш")
            for sheet in workbook.iter(f"{{{S}}}sheet")
            if sheet.get(f"{{{R}}}id") in targets
        }

    @staticmethod
    def _spreadsheet_text_nodes(element: etree._Element) -> list:
        return [
            node
            for node in element.iter(f"{{{S}}}t")
            if not any(a.tag == f"{{{S}}}rPh" for a in node.iterancestors())
        ]

    @staticmethod
    def _shared_index(cell: etree._Element, values: list[str]) -> int:
        try:
            index = int(cell.findtext(f"{{{S}}}v", ""))
            if not 0 <= index < len(values):
                raise ValueError
        except ValueError as exc:
            raise FormaError(
                f"Пошкоджене посилання на текст у комірці {cell.get('r', '?')} XLSX."
            ) from exc
        return index

    def _sheet_contexts(
        self, tree: etree._ElementTree, name: str, shared_values: list[str]
    ) -> dict:
        rows, columns, displays = {}, {}, {}
        coordinates = {}
        for cell in tree.iter(f"{{{S}}}c"):
            address = cell.get("r", "")
            match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", address)
            if cell.get("t") == "s":
                value = shared_values[self._shared_index(cell, shared_values)]
            elif cell.get("t") == "inlineStr":
                value = "".join(n.text or "" for n in self._spreadsheet_text_nodes(cell))
            else:
                value = cell.findtext(f"{{{S}}}v", "")
            displays[cell] = f"{address or '?'}={value[:160]}"
            if not match or not value:
                continue
            column, row = match.groups()
            col_index = 0
            for letter in column:
                col_index = col_index * 26 + ord(letter) - ord("A") + 1
            coordinates[cell] = (int(row), col_index)
            rows.setdefault(int(row), []).append(cell)
            columns.setdefault(col_index, []).append(cell)
        positions = {}
        for groups, coordinate in ((rows, 1), (columns, 0)):
            for cells in groups.values():
                cells.sort(key=lambda cell: coordinates[cell][coordinate])
                for i, cell in enumerate(cells):
                    positions[cell, coordinate] = i
        contexts = {}
        for cell in tree.iter(f"{{{S}}}c"):
            # Numeric/formula cells provide useful context but are never translation targets.
            # Avoid materializing a context string for every value in a large numeric workbook.
            if cell.get("t") not in {"inlineStr", "s"} or cell.find(f"{{{S}}}f") is not None:
                continue
            lines = [f"Worksheet: {name}; cell {cell.get('r', '?')}"]
            if cell in coordinates:
                row, column = coordinates[cell]
                for label, cells, coordinate in (
                    ("Same row", rows[row], 1),
                    ("Same column", columns[column], 0),
                ):
                    i = positions[cell, coordinate]
                    selected = sorted({0, 1, i - 2, i - 1, i + 1, i + 2} - {i})
                    values = [displays[cells[j]] for j in selected if 0 <= j < len(cells)]
                    if values:
                        lines.append(f"{label}: " + " | ".join(values)[:1000])
            contexts[cell] = "\n".join(lines)
        return contexts

    def write(
        self, translations: dict[str, str], output: Path, *, decimal_comma_to_point: bool = False
    ) -> None:
        from forma.quality import parse_translation

        if output.resolve() == self.path.resolve():
            raise FormaError("Результат має зберігатися в окремий файл.")
        if set(translations) != {s.id for s in self.segments}:
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        # Validate the whole result before changing any XML nodes.
        parsed = {s.id: parse_translation(s.source, translations[s.id]) for s in self.segments}
        modified = set()
        for segment in self.segments:
            target = self.targets[segment.id]
            for index, node in enumerate(target.nodes):
                text = parsed[segment.id][f"r{index}"]
                if text != (node.text or ""):
                    node.text = text
                    if text != text.strip():
                        node.set(XML_SPACE, "preserve")
                    modified.add(target.part)
        if decimal_comma_to_point and self.path.suffix.lower() == ".docx":
            # Numeric-only table cells are not language-model segments. Localize their display
            # explicitly for a known decimal-comma source; never reinterpret XLSX numbers.
            numeric_cell = re.compile(r"^\s*\(?[+−-]?(?:\d|[ \u00a0\u202f])+[,]\d+(?:\s*%)?\)?\s*$")
            for part, tree in self.trees.items():
                for node in tree.iter(f"{{{W}}}t"):
                    text = node.text or ""
                    if (
                        numeric_cell.fullmatch(text)
                        and next(node.iterancestors(f"{{{W}}}tc"), None) is not None
                    ):
                        node.text = text.replace(",", ".")
                        modified.add(part)
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.comment = self.zip_comment
            for info in self.infos:
                data = self.parts[info.filename]
                if info.filename in modified:
                    original = data
                    data = etree.tostring(
                        self.trees[info.filename],
                        encoding="UTF-8",
                        xml_declaration=original.lstrip().startswith(b"<?xml"),
                        standalone=self.trees[info.filename].docinfo.standalone,
                    )
                archive.writestr(info, data)
        atomic_bytes(output, buffer.getvalue(), overwrite=False)
