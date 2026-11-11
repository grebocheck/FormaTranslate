"""Synthetic context changes must invalidate exact memory identities."""

from __future__ import annotations

from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from lxml import etree
from openpyxl import Workbook

from forma.documents.ooxml import OfficeDocument, S
from forma.memory.context import segment_context, segment_role


def identity(path, text="Energy"):
    document = OfficeDocument(path)
    segment = next(item for item in document.segments if item.plain == text)
    return segment.source, segment.context, segment_context(document, segment), segment_role(document, segment)


def workbook(path, header, *, unrelated=10):
    book = Workbook()
    for address, value in {
        "A1": header, "A2": "Category", "A3": "General note",
        "A4": "Before value", "A5": "Energy", "A6": "After value", "Z100": unrelated,
    }.items():
        book.active[address] = value
    book.save(path)
    return path


def test_xlsx_full_header_negation_beyond_prompt_excerpt_changes_identity(tmp_path):
    prefix = "Long condition: " + "x" * 180
    before = identity(workbook(tmp_path / "before.xlsx", prefix + " must be included"))
    after = identity(workbook(tmp_path / "after.xlsx", prefix + " must NOT be included"))
    assert before[:2] == after[:2]  # Marked source and visible prompt are unchanged.
    assert before[3] == after[3]
    assert before[2] != after[2]


def test_xlsx_unrelated_numeric_cell_does_not_invalidate_selected_context(tmp_path):
    before = identity(workbook(tmp_path / "before.xlsx", "Energy category", unrelated=10))
    after = identity(workbook(tmp_path / "after.xlsx", "Energy category", unrelated=20))
    assert before == after


def shared_workbook(path, last_value):
    rows = "".join(
        f'<row r="{index}"><c r="A{index}" t="s"><v>0</v></c>'
        f'<c r="B{index}"><v>{last_value if index == 4 else 10}</v></c></row>'
        for index in range(1, 5)
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}"/>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><t>Energy</t></si></sst>')
        archive.writestr(
            "xl/worksheets/sheet1.xml", f'<worksheet xmlns="{S}"><sheetData>{rows}</sheetData></worksheet>',
        )
    return path


def test_xlsx_shared_string_includes_uses_beyond_three_prompt_examples(tmp_path):
    before = identity(shared_workbook(tmp_path / "before.xlsx", 10))
    after = identity(shared_workbook(tmp_path / "after.xlsx", 20))
    assert before[:2] == after[:2]
    assert before[2] != after[2]


def test_xlsx_implicit_cell_addresses_use_conservative_worksheet_identity(tmp_path):
    identities = []
    for index, value in enumerate((10, 20)):
        path = tmp_path / f"implicit-{index}.xlsx"
        with ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}"/>')
            archive.writestr("xl/worksheets/sheet1.xml", (
                f'<worksheet xmlns="{S}"><sheetData><row r="1">'
                '<c t="inlineStr"><is><t>Energy</t></is></c>'
                f'<c><v>{value}</v></c></row></sheetData></worksheet>'
            ))
        identities.append(identity(path))
    before, after = identities
    assert before[:2] == after[:2]
    assert before[2] != after[2]


@pytest.mark.parametrize("boundary", ["paragraph", "break", "tab"])
def test_docx_full_table_context_preserves_boundaries_beyond_prompt_excerpt(tmp_path, boundary):
    identities = []
    for changed in (False, True):
        document = Document()
        table = document.add_table(rows=1, cols=2)
        paragraph = table.cell(0, 0).paragraphs[0]
        prefix = "Long condition: " + "x" * 180 + " require"
        if not changed:
            paragraph.add_run(prefix + "approval")
        else:
            paragraph.add_run(prefix)
            if boundary == "paragraph":
                table.cell(0, 0).add_paragraph("approval")
            else:
                run = paragraph.add_run()
                run._r.append(OxmlElement("w:br" if boundary == "break" else "w:tab"))
                paragraph.add_run("approval")
        table.cell(0, 1).text = "Energy"
        path = tmp_path / f"{changed}.docx"
        document.save(path)
        identities.append(identity(path))
    before, after = identities
    assert before[:2] == after[:2]
    assert before[3] == after[3]
    assert before[2] != after[2]


def test_docx_merged_cells_preserve_logical_header_mapping(tmp_path):
    identities = []
    for first_span in (False, True):
        document = Document()
        table = document.add_table(rows=2, cols=3)
        for cell, text in zip(table.rows[0].cells, ("Count", "Weight", "Temperature"), strict=True):
            cell.text = text
        cells = table.rows[1].cells
        cells[0].text, cells[2].text = "Before", "Energy"
        if first_span:
            cells[0].merge(cells[1])
        else:
            cells[1].merge(cells[2])
        path = tmp_path / f"{first_span}.docx"
        document.save(path)
        identities.append(identity(path))
    before, after = identities
    assert before[:2] == after[:2]
    assert before[3] == after[3]  # Physical second cell in both rows.
    assert before[2] != after[2]  # Its logical column/span changed.


def test_docx_xml_comments_and_processing_instructions_do_not_change_identity(tmp_path):
    identities = []
    for comments in (False, True):
        document = Document()
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Before"
        table.cell(0, 1).text = "Energy"
        properties = (
            table.rows[0]._tr.get_or_add_trPr(),
            table.cell(0, 0)._tc.get_or_add_tcPr(),
            table.cell(0, 1).paragraphs[0]._p.get_or_add_pPr(),
        )
        if comments:
            for item in properties:
                item.append(etree.Comment("valid XML metadata"))
                item.append(etree.ProcessingInstruction("synthetic", "metadata"))
        path = tmp_path / f"comments-{comments}.docx"
        document.save(path)
        identities.append(identity(path))
    assert identities[0] == identities[1]
