from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from lxml import etree

from forma.comparison import compare_files, office_signature
from forma.models import FormaError
from forma.ooxml import OfficeDocument, W


def translated(document):
    return {
        s.id: s.source.replace("Heat meter", "Лічильник тепла")
        .replace("commissioning", "введення в експлуатацію")
        .replace("sensor", "датчик")
        for s in document.segments
    }


@pytest.mark.parametrize("fixture", ["docx_file", "xlsx_file"])
def test_identity_retains_every_member_byte(fixture, request, tmp_path):
    source = request.getfixturevalue(fixture)
    before = source.read_bytes()
    document = OfficeDocument(source)
    output = tmp_path / f"copy{source.suffix}"
    document.write({s.id: s.source for s in document.segments}, output)
    with ZipFile(source) as a, ZipFile(output) as b:
        assert a.namelist() == b.namelist()
        assert all(a.read(name) == b.read(name) for name in a.namelist())
    assert source.read_bytes() == before


@pytest.mark.parametrize("fixture", ["docx_file", "xlsx_file"])
def test_translation_preserves_nontext_structure(fixture, request, tmp_path):
    source = request.getfixturevalue(fixture)
    document = OfficeDocument(source)
    output = tmp_path / f"translated{source.suffix}"
    document.write(translated(document), output)
    a, b = office_signature(source), office_signature(output)
    assert a["parts"] == b["parts"]
    assert a["formulas"] == b["formulas"]
    assert a["text"] != b["text"]
    with ZipFile(source) as original, ZipFile(output) as result:
        for name in original.namelist():
            if name.endswith(".rels") or "styles" in name or "theme" in name or "/media/" in name:
                assert original.read(name) == result.read(name)
    metric = compare_files(source, [output])["candidates"][0]
    assert metric["structure_ok"]
    assert metric["numbers_preserved"]


def test_fields_hyperlink_and_table(docx_file):
    document = OfficeDocument(docx_file)
    assert any("Installation manual" in s.plain for s in document.segments)
    assert any("Temperature range" in s.plain for s in document.segments)
    assert not any(s.plain == "1" for s in document.segments)
    mixed = next(s for s in document.segments if "maximum operating" in s.plain)
    assert "<r0>" in mixed.source and "<r2>" in mixed.source
    assert all(
        issue.severity == "info" for issue in document.issues if issue.code == "fields_preserved"
    )


def test_rejects_bad_mapping_before_any_output(docx_file, tmp_path):
    document = OfficeDocument(docx_file)
    values = translated(document)
    values[next(iter(values))] += "<r999>Injected</r999>"
    output = tmp_path / "invalid.docx"
    with pytest.raises(FormaError):
        document.write(values, output)
    assert not output.exists()
    with pytest.raises(FormaError):
        document.write({}, output)
    with pytest.raises(FormaError):
        document.write(translated(document), docx_file)


def test_never_overwrites_existing_output(docx_file, tmp_path):
    document = OfficeDocument(docx_file)
    output = tmp_path / "existing.docx"
    output.write_bytes(b"important file")
    with pytest.raises(FileExistsError):
        document.write(translated(document), output)
    assert output.read_bytes() == b"important file"


def test_shared_strings_are_translated_once(tmp_path):
    path = tmp_path / "shared.xlsx"
    parts = {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        "xl/sharedStrings.xml": b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><r><t>Flow </t></r><r><t>sensor</t></r></si></sst>',
        "xl/worksheets/sheet1.xml": b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c><c r="C1"><f>SUM(A2:B2)</f><v>123</v></c></row></sheetData></worksheet>',
        "xl/media/image1.bin": b"binary image survives",
    }
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    document = OfficeDocument(path)
    assert len(document.segments) == 1
    assert "A1" in document.segments[0].location and "B1" in document.segments[0].location
    output = tmp_path / "shared.uk.xlsx"
    document.write(translated(document), output)
    with ZipFile(output) as archive:
        assert archive.read("xl/worksheets/sheet1.xml") == parts["xl/worksheets/sheet1.xml"]
        assert archive.read("xl/media/image1.bin") == parts["xl/media/image1.bin"]


def test_nested_textbox_is_not_translated_twice(docx_file, tmp_path):
    with ZipFile(docx_file) as archive:
        parts = {n: archive.read(n) for n in archive.namelist()}
    tree = etree.fromstring(parts["word/document.xml"])
    body = tree.find(f"{{{W}}}body")
    paragraph = etree.SubElement(body, f"{{{W}}}p")
    run = etree.SubElement(paragraph, f"{{{W}}}r")
    box = etree.SubElement(run, f"{{{W}}}txbxContent")
    inner = etree.SubElement(box, f"{{{W}}}p")
    inner_run = etree.SubElement(inner, f"{{{W}}}r")
    etree.SubElement(inner_run, f"{{{W}}}t").text = "Textbox content"
    parts["word/document.xml"] = etree.tostring(tree)
    path = tmp_path / "textbox.docx"
    with ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    document = OfficeDocument(path)
    assert sum("Textbox content" in s.plain for s in document.segments) == 1


def test_rejects_dtd_and_signed_documents(tmp_path):
    from forma.ooxml import parse_xml

    with pytest.raises(FormaError):
        parse_xml(b'<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>')
    signed = tmp_path / "signed.docx"
    with ZipFile(signed, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_xmlsignatures/sig1.xml", "<Signature/>")
    with pytest.raises(FormaError, match="підпис"):
        OfficeDocument(signed)


def test_toc_and_reference_text_are_translated_but_page_fields_are_not(tmp_path):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    paragraph = doc.add_paragraph()

    def field_node(kind):
        run = OxmlElement("w:r")
        node = OxmlElement("w:fldChar")
        node.set(qn("w:fldCharType"), kind)
        run.append(node)
        paragraph._p.append(run)

    def instruction(text):
        run = OxmlElement("w:r")
        node = OxmlElement("w:instrText")
        node.text = text
        run.append(node)
        paragraph._p.append(run)

    field_node("begin")
    instruction(' TOC \\o "1-2" ')
    field_node("separate")
    paragraph.add_run("Умови роботи")
    field_node("begin")
    instruction(" PAGEREF _Toc123 ")
    field_node("separate")
    paragraph.add_run("7")
    field_node("end")
    field_node("end")
    source = tmp_path / "toc.docx"
    doc.save(source)
    office = OfficeDocument(source)
    assert [s.plain for s in office.segments] == ["Умови роботи"]
    assert (
        next(issue for issue in office.issues if issue.code == "fields_preserved").severity
        == "info"
    )
    output = tmp_path / "toc.en.docx"
    office.write(
        {s.id: s.source.replace("Умови роботи", "Operating conditions") for s in office.segments},
        output,
    )
    with ZipFile(output) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        assert root.xpath("//w:instrText/text()", namespaces={"w": W}) == [
            ' TOC \\o "1-2" ',
            " PAGEREF _Toc123 ",
        ]
        assert "7" in root.xpath("//w:t/text()", namespaces={"w": W})


def test_numeric_only_word_table_cells_receive_english_decimal_points(tmp_path):
    from docx import Document

    doc = Document()
    row = doc.add_table(rows=1, cols=4).rows[0]
    for cell, text in zip(row.cells, ["Показник", "910,154", "-0,023", "1 174,270"], strict=True):
        cell.text = text
    source = tmp_path / "numeric-table.docx"
    doc.save(source)
    office = OfficeDocument(source)
    output = tmp_path / "numeric-table.en.docx"
    office.write(
        {s.id: s.source.replace("Показник", "Indicator") for s in office.segments},
        output,
        decimal_comma_to_point=True,
    )
    assert [c.text for c in Document(output).tables[0].rows[0].cells] == [
        "Indicator",
        "910.154",
        "-0.023",
        "1 174.270",
    ]
    result = compare_files(source, [output])["candidates"][0]
    assert result["structure_ok"] and result["numbers_preserved"]


def test_word_table_context_contains_header_and_numeric_row_values(tmp_path):
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    for row, values in zip(
        table.rows,
        [
            ("Equipment", "Operating pressure", "Unit"),
            ("Pump", "12.5", "bar"),
            ("Compressor", "8.0", "bar"),
        ],
        strict=True,
    ):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    source = tmp_path / "table-context.docx"
    doc.save(source)
    office = OfficeDocument(source)
    segment = next(s for s in office.segments if s.plain == "Compressor")
    assert "Table row 3, cell 1" in segment.context
    assert "Compressor | 8.0 | bar" in segment.context
    assert "Equipment | Operating pressure | Unit" in segment.context
    output = tmp_path / "table-context-copy.docx"
    office.write({s.id: s.source for s in office.segments}, output)
    with ZipFile(source) as a, ZipFile(output) as b:
        assert all(a.read(name) == b.read(name) for name in a.namelist())


def test_shared_strings_use_named_worksheet_context_not_storage_order(tmp_path):
    from forma.ooxml import R, S

    path = tmp_path / "context.xlsx"
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Electrical" sheetId="1" r:id="rId1"/><sheet name="Shipping" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/custom.xml"/><Relationship Id="rId2" Type="{R}/worksheet" Target="/xl/worksheets/sheet2.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{S}"><si><t>Unrelated cached text</t></si><si><t>Load</t></si></sst>',
        "xl/worksheets/custom.xml": f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Equipment</t></is></c><c r="B1" t="inlineStr"><is><t>Power in kW</t></is></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42.5</v></c><c r="C2"><f>B2*2</f><v>85</v></c></row></sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Warehouse action</t></is></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="inlineStr"><is><t>Pallets</t></is></c></row></sheetData></worksheet>',
    }
    with ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    office = OfficeDocument(path)
    shared = next(s for s in office.segments if s.plain == "Load")
    assert shared.location == "Electrical!A2, Shipping!A2"
    assert "Worksheet: Electrical; cell A2" in shared.context
    assert "B2=42.5" in shared.context and "A1=Equipment" in shared.context
    assert "Worksheet: Shipping; cell A2" in shared.context
    assert "Warehouse action" in shared.context and "Pallets" in shared.context
    assert "Unrelated cached text" not in shared.context
    power = next(s for s in office.segments if s.plain == "Power in kW")
    assert power.location == "Electrical!B1"
    assert "B2=42.5" in power.context
    output = tmp_path / "context.uk.xlsx"
    office.write({s.id: s.source.replace("Load", "Навантаження") for s in office.segments}, output)
    with ZipFile(output) as archive:
        for part in parts:
            if part != "xl/sharedStrings.xml":
                assert archive.read(part) == parts[part].encode()


@pytest.mark.parametrize("index", ["-1", "1", "invalid"])
def test_invalid_shared_string_reference_fails_instead_of_omitting_text(tmp_path, index):
    from forma.ooxml import S

    path = tmp_path / "broken.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}"/>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><t>Text</t></si></sst>')
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="s"><v>{index}</v></c></row></sheetData></worksheet>',
        )
    with pytest.raises(FormaError, match="A1"):
        OfficeDocument(path)


@pytest.mark.parametrize("relocate", [False, True])
def test_xlsx_comments_follow_relationships_and_preserve_other_parts(tmp_path, relocate):
    from openpyxl import Workbook, load_workbook
    from openpyxl.comments import Comment

    source = tmp_path / "comments.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Показники"
    sheet["A1"] = "Витрата"
    sheet["B1"] = 12.5
    sheet["B2"] = "=B1*2"
    sheet["A1"].comment = Comment("Об'єм рідини за одиницю часу", "Engineer")
    workbook.save(source)
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    comment_part = "xl/comments/comment1.xml"
    assert comment_part in parts
    if relocate:
        renamed = "xl/annotations/notes.xml"
        parts[renamed] = parts.pop(comment_part)
        relations = "xl/worksheets/_rels/sheet1.xml.rels"
        parts[relations] = parts[relations].replace(
            b"/xl/comments/comment1.xml", b"../annotations/notes.xml"
        )
        parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
            b"/xl/comments/comment1.xml", b"/xl/annotations/notes.xml"
        )
        comment_part = renamed
        with ZipFile(source, "w") as archive:
            for name, value in parts.items():
                archive.writestr(name, value)
    office = OfficeDocument(source)
    comment = next(s for s in office.segments if s.plain == "Об'єм рідини за одиницю часу")
    assert comment.id.startswith(comment_part)
    assert comment.location == "Примітка Показники!A1"
    assert "Worksheet: Показники; comment on cell A1" in comment.context
    output = tmp_path / "comments.en.xlsx"
    office.write(
        {
            s.id: s.source.replace("Об'єм рідини за одиницю часу", "Liquid volume per unit time")
            for s in office.segments
        },
        output,
    )
    with ZipFile(output) as archive:
        for name, value in parts.items():
            if name != comment_part:
                assert archive.read(name) == value
    result = load_workbook(output)
    assert result.active["A1"].comment.text == "Liquid volume per unit time"
    assert result.active["A1"].comment.author == "Engineer"
    assert result.active["B1"].value == 12.5
    assert result.active["B2"].value == "=B1*2"


@pytest.mark.parametrize("extension", ["docx", "xlsx"])
def test_uppercase_descriptive_labels_reach_translation_while_codes_remain_intact(
    tmp_path, extension
):
    from docx import Document
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font

    from forma.config import Settings
    from forma.engine import TranslationEngine
    from forma.models import Options

    translations = {
        "IN-SERVICE": "В ЕКСПЛУАТАЦІЇ",
        "COST-BENEFIT": "ВИТРАТИ ТА ВИГОДИ",
        "NON-COMPLIANCE": "НЕВІДПОВІДНІСТЬ",
    }
    protected = [
        "HM-200",
        "ТН-42",
        "140Д70",
        "ACT-2026-01.docx",
        "https://example.test/manual",
        "contact@example.test",
        "{order_id}",
    ]
    labels = [*translations, *protected]
    source = tmp_path / f"labels.{extension}"
    if extension == "docx":
        document = Document()
        for label in labels:
            document.add_paragraph().add_run(label).bold = True
        document.save(source)
    else:
        workbook = Workbook()
        sheet = workbook.active
        for row, label in enumerate(labels, 1):
            sheet.cell(row, 1, label).font = Font(bold=True)
        sheet["B1"] = 17.5
        sheet["B2"] = "=B1*2"
        workbook.save(source)
    before_bytes = source.read_bytes()
    extracted = OfficeDocument(source)
    assert [segment.plain for segment in extracted.segments] == list(translations)

    class LabelProvider:
        input_tokens = 10
        output_tokens = 5

        def complete(self, instruction, payload, schema, **kwargs):
            segments = []
            for segment in payload["segments"]:
                text = segment["text"]
                for original, translated in translations.items():
                    text = text.replace(original, translated)
                segments.append({"id": segment["id"], "text": text})
            return {"segments": segments}

    result = TranslationEngine(
        Settings(base_url="http://localhost/v1", model="fixture-model"), provider=LabelProvider()
    ).translate(
        source,
        tmp_path / "output",
        Options(source_language="en", target_language="uk", auto_glossary=False),
    )
    assert source.read_bytes() == before_bytes
    assert len(result.translations) == len(translations)
    if extension == "docx":
        output = Document(result.output)
        assert [p.text for p in output.paragraphs] == [*translations.values(), *protected]
        assert all(p.runs[0].bold for p in output.paragraphs)
    else:
        output = load_workbook(result.output).active
        assert [output.cell(row, 1).value for row in range(1, len(labels) + 1)] == [
            *translations.values(),
            *protected,
        ]
        assert all(output.cell(row, 1).font.bold for row in range(1, len(labels) + 1))
        assert output["B1"].value == 17.5 and output["B2"].value == "=B1*2"
    metrics = compare_files(source, [result.output])["candidates"][0]
    assert (
        metrics["structure_ok"] and metrics["formulas_preserved"] and metrics["numbers_preserved"]
    )
