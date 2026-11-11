"""Editable, reflowing DOCX from layout blocks; never use table screenshots as text."""

from __future__ import annotations

import io
import math
import re
from collections import Counter

MAX_TABLE_ROWS = 1000
MAX_TABLE_COLUMNS = 100
MAX_TABLE_CELLS = 20000


def clean_text(value: str) -> str:
    # WordprocessingML cannot represent these control characters.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)


def table_cells(markup: str) -> tuple[int, int, list[tuple[int, int, int, int, str]]]:
    """Validate HTML and return a rectangular grid, including rowspan/colspan.

    The upstream 3.7 Word exporter drops span attributes. Build genuine Word
    merged cells instead. Malformed/oversized tables fail rather than lose cells.
    """
    from lxml import html

    if len(markup) > 4 * 1024 * 1024:
        raise ValueError("Table HTML exceeds the supported size")
    root = html.fromstring(markup, parser=html.HTMLParser(no_network=True))
    tables = ([root] if root.tag == "table" else []) + root.xpath(".//table")
    if len(tables) != 1:
        raise ValueError("Expected one table")
    table = tables[0]
    rows = table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
    if not rows or len(rows) > MAX_TABLE_ROWS:
        raise ValueError("Invalid table row count")
    occupied: set[tuple[int, int]] = set()
    cells = []
    width = 0
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.xpath("./td|./th"):
            while (row_index, column) in occupied:
                column += 1
            try:
                rowspan = int(cell.get("rowspan", "1"))
                colspan = int(cell.get("colspan", "1"))
            except ValueError as exc:
                raise ValueError("Invalid table span") from exc
            if (
                not 1 <= rowspan <= len(rows) - row_index
                or not 1 <= colspan <= MAX_TABLE_COLUMNS
                or column + colspan > MAX_TABLE_COLUMNS
            ):
                raise ValueError("Table span is outside its grid")
            covered = {
                (r, c)
                for r in range(row_index, row_index + rowspan)
                for c in range(column, column + colspan)
            }
            if occupied.intersection(covered):
                raise ValueError("Overlapping table cells")
            occupied.update(covered)
            if len(occupied) > MAX_TABLE_CELLS:
                raise ValueError("Table has too many cells")
            for br in cell.xpath(".//br"):
                br.tail = "\n" + (br.tail or "")
            text = clean_text("".join(cell.itertext())).strip()
            cells.append((row_index, column, rowspan, colspan, text))
            column += colspan
            width = max(width, column)
    if not cells or width == 0:
        raise ValueError("Empty table")
    if len(rows) * width > MAX_TABLE_CELLS:
        raise ValueError("Table grid has too many cells")
    # Missing trailing cells are valid HTML, but retain the rectangular Word grid.
    return len(rows), width, cells


def flatten_table(markup: str) -> str:
    """Keep cell/row boundaries even when malformed spans prevent a native table."""
    from lxml import html

    try:
        root = html.fromstring(markup, parser=html.HTMLParser(no_network=True))
    except (ValueError, TypeError):
        return clean_text(markup)
    rows = root.xpath(".//tr")
    if not rows:
        return clean_text(root.text_content())
    return "\n".join(
        "\t".join(clean_text(cell.text_content()).strip() for cell in row.xpath("./td|./th"))
        for row in rows
    )


def add_table(document, markup: str, *, font_size: float = 9, uncertain=False, column_widths=None) -> int:
    from docx.shared import Emu, Pt

    rows, columns, cells = table_cells(markup)
    table = document.add_table(rows=rows, cols=columns)
    table.style = "Table Grid"
    table.autofit = True
    if column_widths is not None:
        if len(column_widths) != columns or not all(
            isinstance(value, (int, float)) and math.isfinite(value) and value > 0
            for value in column_widths
        ):
            raise ValueError("Invalid table column widths")
        section = document.sections[-1]
        usable = section.page_width - section.left_margin - section.right_margin
        total = sum(column_widths)
        table.autofit = False
        for index, value in enumerate(column_widths):
            width = Emu(int(usable * value / total))
            table.columns[index].width = width
            for row in table.rows:
                row.cells[index].width = width
    for row, column, rowspan, colspan, text in cells:
        cell = table.cell(row, column)
        if rowspan > 1 or colspan > 1:
            cell = cell.merge(table.cell(row + rowspan - 1, column + colspan - 1))
        cell.text = text
        for paragraph in cell.paragraphs:
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.space_before = Pt(1)
            for run in paragraph.runs:
                run.font.size = Pt(font_size)
                if uncertain:
                    from docx.enum.text import WD_COLOR_INDEX

                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    return sum(len(text) for *_, text in cells)


def _set_page(section, width_points: float, height_points: float) -> None:
    from docx.shared import Pt

    section.page_width = Pt(width_points)
    section.page_height = Pt(height_points)
    section.top_margin = section.bottom_margin = Pt(32)
    section.left_margin = section.right_margin = Pt(36)
    section.header_distance = section.footer_distance = Pt(12)


def _bookmark(paragraph, page_index: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
    start.set(qn("w:id"), str(page_index))
    start.set(qn("w:name"), f"forma_ocr_page_{page_index}")
    end.set(qn("w:id"), str(page_index))
    paragraph._p.append(start)
    paragraph._p.append(end)


class DocumentWriter:
    def __init__(self):
        from docx import Document
        from docx.shared import Pt, RGBColor

        self.document = Document()
        normal = self.document.styles["Normal"]
        normal.font.name = "Times New Roman"
        normal.font.size = Pt(10)
        normal.paragraph_format.space_after = Pt(3)
        normal.paragraph_format.line_spacing = 1.0
        for name, size in (("Heading 1", 14), ("Heading 2", 12)):
            heading = self.document.styles[name]
            heading.font.name = "Times New Roman"
            heading.font.size = Pt(size)
            heading.font.bold = True
            heading.font.color.rgb = RGBColor(0, 0, 0)
            heading.paragraph_format.space_before = Pt(4)
            heading.paragraph_format.space_after = Pt(3)
        self.page_count = 0
        self.text_chars = 0
        self.export_tokens = Counter()

    def page(self, blocks: list[dict], *, width: float, height: float, image, recognized_texts=(), blank=False) -> dict:
        from docx.enum.section import WD_SECTION_START
        from docx.shared import Pt

        before_text_count = len(self.document._element.xpath(".//w:t"))

        self.page_count += 1
        document = self.document
        section = (
            document.sections[0]
            if self.page_count == 1
            else document.add_section(WD_SECTION_START.NEW_PAGE)
        )
        _set_page(section, width, height)
        anchor = document.add_paragraph()
        anchor.paragraph_format.space_after = Pt(0)
        anchor.paragraph_format.space_before = Pt(0)
        anchor.paragraph_format.line_spacing = Pt(1)
        anchor.paragraph_format.keep_with_next = True
        anchor.add_run().font.size = Pt(1)
        _bookmark(anchor, self.page_count)
        result = {"index": self.page_count, "text_chars": 0, "tables": 0, "images": 0, "warnings": []}
        for block in blocks:
            label = block["label"]
            text = clean_text(block.get("text", ""))
            if label == "table":
                try:
                    result["text_chars"] += add_table(
                        document, text, uncertain=block.get("uncertain", False),
                        column_widths=block.get("column_widths"),
                    )
                    result["tables"] += 1
                except ValueError:
                    # Preserve a problematic table visually and retain its OCR as
                    # editable text. The manifest makes this fallback explicit.
                    self._image(block, image, width, height)
                    result["images"] += 1
                    flattened = flatten_table(text)
                    document.add_paragraph(clean_text(flattened))
                    result["text_chars"] += len(flattened)
                    result["warnings"].append("table_structure_unresolved")
            elif label in {"image", "seal", "chart", "formula", "header_image", "footer_image"}:
                self._image(block, image, width, height)
                result["images"] += 1
                result["warnings"].append("graphic_text_not_editable")
                if block.get("source_crop"):
                    result["warnings"].append(block["source_crop"])
                if block.get("graphic_crop_expanded"):
                    result["warnings"].append("graphic_crop_expanded")
            elif text.strip():
                style = "Heading 1" if label == "doc_title" else (
                    "Heading 2" if label in {"paragraph_title", "figure_table_chart_title"} else None
                )
                paragraph = document.add_paragraph(text, style=style)
                if label in {"header", "footer", "number", "footnote"}:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.font.size = Pt(8)
                if label == "recovered_text" or block.get("uncertain"):
                    from docx.enum.text import WD_COLOR_INDEX

                    for run in paragraph.runs:
                        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                result["text_chars"] += len(text)
        if not blocks and not blank:
            # Preserve pages with no detected structure for visual review; never
            # silently represent an unrecognized scan as a blank page.
            self._image({"bbox": [0, 0, image.width, image.height]}, image, width, height)
            result["images"] += 1
            result["warnings"].append("no_text_detected")
        if not result["text_chars"] and not blank:
            raise ValueError(f"No editable text was recognized on nonblank page {self.page_count}")
        result["status"] = "blank" if blank else "complete"
        reference = text_tokens("\n".join(recognized_texts))
        serialized = text_tokens("\n".join(
            element.text or "" for element in document._element.xpath(".//w:t")[before_text_count:]
        ))
        missing = reference - serialized
        if missing:
            raise ValueError(f"DOCX export lost recognized words on page {self.page_count}")
        result["recognized_tokens"] = sum(reference.values())
        result["retained_tokens"] = sum(reference.values()) - sum(missing.values())
        result["exported_tokens"] = sum(serialized.values())
        self.export_tokens.update(serialized)
        result["warnings"] = sorted(set(result["warnings"]))
        self.text_chars += result["text_chars"]
        return result

    def _image(self, block, page_image, width_points, height_points):
        from docx.shared import Pt

        x0, y0, x1, y1 = block["bbox"]
        bbox = (
            max(0, int(x0)), max(0, int(y0)),
            min(page_image.width, int(x1)), min(page_image.height, int(y1)),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError("Invalid graphic rectangle")
        crop = page_image.crop(bbox)
        stream = io.BytesIO()
        crop.save(stream, "PNG")
        stream.seek(0)
        width = min(width_points - 72, (bbox[2] - bbox[0]) / page_image.width * width_points)
        height = width * crop.height / crop.width
        if height > height_points - 72:
            width *= (height_points - 72) / height
        paragraph = self.document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.add_run().add_picture(stream, width=Pt(max(1, width)))

    def save(self, path) -> None:
        from docx import Document

        if not self.text_chars:
            raise ValueError("No editable text was recognized in this PDF")
        self.document.save(path)
        reopened = Document(path)
        saved_tokens = text_tokens("\n".join(element.text or "" for element in reopened._element.xpath(".//w:t")))
        if saved_tokens != self.export_tokens:
            raise ValueError("DOCX export changed recognized words while serializing")


def text_tokens(text: str) -> Counter:
    # Retain signs, decimal separators and symbols as well as words. Checking
    # only \w would accept -100 -> 100 or 1.00 -> 1,00 during table recovery.
    return Counter(re.findall(r"\w+|[^\w\s]", text.casefold()))
