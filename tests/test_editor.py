from __future__ import annotations

import hashlib
import json
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.shared import RGBColor
from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment

from forma.core.models import FormaError, Options, TranslationResult, plain_text
from forma.documents.editor import edit_translation_markup, export_reviewed_copy
from forma.documents.ooxml import NS, OfficeDocument
from forma.documents.review import load_report
from forma.translation.quality import parse_translation
from forma.translation.reports import write_translation_report


def package(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def rewrite_part(path, part, change):
    with ZipFile(path) as archive:
        infos, comment = archive.infolist(), archive.comment
        data = {info.filename: archive.read(info) for info in infos}
    data[part] = change(data[part])
    with ZipFile(path, "w") as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(info, data[info.filename])


def write_translation(source, *, legacy_ids=False, extraction_profile=2):
    office = OfficeDocument(source, extraction_profile=extraction_profile)
    replacements = {
        "Потужність ": "Power of ",
        "насоса": "pump",
        " кВт": " kW",
        "Перевірити": "Check",
        "Перед запуском.": "Before starting.",
        "Інший перекладений абзац.": "Another translated paragraph.",
        "Витрата": "Flow",
        "Примітка": "Comment",
        "Текст": "Text",
    }
    translations = {}
    for segment in office.segments:
        translated = segment.source
        for old, new in replacements.items():
            translated = translated.replace(old, new)
        translations[segment.id] = translated
    output = source.with_name(source.stem + ".en" + source.suffix)
    office.write(translations, output, decimal_comma_to_point=True)
    result = TranslationResult(source, output, office.segments, translations, office.issues,
                               extraction_profile=office.extraction_profile)
    write_translation_report(
        result, Options(source_language="uk", target_language="en"), "test-original-model"
    )
    if legacy_ids:
        data = json.loads(result.report.read_text())
        data["engine"] = "forma-corpus"
        for index, segment in enumerate(data["segments"]):
            # Deliberately unlike extraction indexes, as in older corpus protocols.
            segment["id"] = f"legacy-{1000 - index}"
        result.report.write_text(json.dumps(data))
    return output, result.report


@pytest.fixture
def office_translation(tmp_path):
    source = tmp_path / "source.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Потужність ")
    paragraph.add_run("насоса").bold = True
    paragraph.add_run(" 25 кВт, HM-200.").italic = True
    paragraph.add_run().add_tab()
    paragraph.add_run("Перевірити")
    paragraph.add_run().add_break()
    paragraph.add_run("Перед запуском.")
    document.add_paragraph("Інший перекладений абзац.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Витрата"
    table.cell(0, 1).text = "10,5"
    field = OxmlElement("w:fldSimple")
    field.set(f"{{{NS['w']}}}instr", "PAGE")
    run, value = OxmlElement("w:r"), OxmlElement("w:t")
    value.text = "7"
    run.append(value)
    field.append(run)
    document.add_paragraph()._p.append(field)
    document.save(source)
    with ZipFile(source, "a") as archive:
        archive.writestr("custom/untouched.bin", b"unchanged binary asset")
    output, report = write_translation(source, legacy_ids=True)
    result, _ = load_report(report)
    first = result.segments[0]
    return source, output, report, first.id, result.translations[first.id]


@pytest.mark.parametrize(
    ("original", "edited"),
    [
        ("<r0>The </r0><r1>pump</r1><r2> works.</r2>", "The efficient pump operates."),
        ("<r0>Alpha</r0><r1> beta</r1><r2> gamma</r2>", "Alfa gamma"),
        ("<r0>One </r0><r1>two</r1><r2> three</r2>", "Replacement text"),
        ("<r0></r0><r1>Text</r1><r2></r2>", "Inserted text & <literal>"),
        ("<r0>One</r0><k0/><r1>Two</r1><k1/><r2>Three</r2>", "First\nSecond\nThird"),
        ("<r0>One\nTwo</r0><r1> three</r1>", "First\nSecond and third"),
        ("<k0/><r0>Text</r0><k1/>", "\nEdited text\n"),
    ],
)
def test_plain_edit_preserves_run_and_control_identities(original, edited):
    result = edit_translation_markup(original, edited)
    assert plain_text(result) == edited
    assert list(parse_translation(original, result)) == list(parse_translation(original, original))
    assert sum(len(value) for value in parse_translation(original, result).values()) <= len(edited)


def test_plain_edit_keeps_unchanged_bold_word_in_its_run():
    original = "<r0>The </r0><r1>pump</r1><r2> works.</r2>"
    result = edit_translation_markup(original, "The efficient pump operates.")
    assert parse_translation(original, result) == {
        "r0": "The efficient ",
        "r1": "pump",
        "r2": " operates.",
    }
    assert edit_translation_markup(original, plain_text(original)) == original


@pytest.mark.parametrize(
    ("original", "edited"),
    [
        ("<r0>One</r0><k0/><r1>Two</r1>", "Merged"),
        ("<r0>One</r0>", "Added\nline"),
        ("<r0>One</r0><k0/><k1/><r1>Two</r1>", "One\nInserted\nTwo"),
    ],
)
def test_edit_cannot_delete_add_or_invent_control_regions(original, edited):
    with pytest.raises(FormaError):
        edit_translation_markup(original, edited)


def test_docx_export_uses_current_copy_preserves_styles_controls_and_other_edits(
    office_translation,
):
    source, output, report, segment_id, markup = office_translation
    document = Document(output)
    document.paragraphs[1].runs[0].text = "An externally improved paragraph."
    document.paragraphs[0].runs[1].font.color.rgb = RGBColor(12, 34, 56)
    document.save(output)
    # python-docx discards unknown package members, so add an output-only asset.
    with ZipFile(output, "a") as archive:
        archive.writestr("custom/current-only.bin", b"output manual asset")
    source_bytes, output_bytes, report_bytes = (
        source.read_bytes(),
        output.read_bytes(),
        report.read_bytes(),
    )
    before = package(output)
    edited = edit_translation_markup(markup, plain_text(markup).replace("pump", "efficient pump"))
    result = export_reviewed_copy(source, output, report, {segment_id: edited})
    after = package(result.output)
    assert source.read_bytes() == source_bytes and output.read_bytes() == output_bytes
    assert report.read_bytes() == report_bytes
    for name, data in before.items():
        if name != "word/document.xml":
            assert after[name] == data
    current, exported = Document(output), Document(result.output)
    assert exported.paragraphs[0].text == current.paragraphs[0].text.replace(
        "pump", "efficient pump"
    )
    assert exported.paragraphs[0].runs[1].bold
    assert exported.paragraphs[0].runs[1].font.color.rgb == RGBColor(12, 34, 56)
    assert exported.paragraphs[1].text == "An externally improved paragraph."
    assert exported.tables[0].cell(0, 1).text == "10.5"
    for tag in ("tab", "br", "fldSimple"):
        old = etree.fromstring(before["word/document.xml"]).findall(f".//w:{tag}", NS)
        new = etree.fromstring(after["word/document.xml"]).findall(f".//w:{tag}", NS)
        assert [etree.tostring(node) for node in old] == [etree.tostring(node) for node in new]
    saved, _ = load_report(result.report)
    assert saved.translations[segment_id] == edited
    assert "An externally improved paragraph." in " ".join(
        map(plain_text, saved.translations.values())
    )
    metadata = json.loads(result.report.read_text())
    assert metadata["output"] == str(result.output)
    assert metadata["parent_report"] == str(report)
    assert metadata["manual_edits"] == [segment_id]
    assert metadata["model"] == "test-original-model" and metadata["engine"] == "forma-corpus"
    assert result.input_tokens == result.output_tokens == 0
    assert result.usage_note == "Ручна редактура без нових запитів до API."
    assert result.report.with_suffix(".html").is_file()


def test_xlsx_export_preserves_rich_text_formula_values_styles_and_comments(tmp_path):
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Показники"
    sheet["A1"] = CellRichText("Потужність ", TextBlock(InlineFont(b=True), "насоса"), " 25 кВт")
    sheet["A2"] = "Витрата"
    sheet["B2"] = 10.5
    sheet["B2"].number_format = "0.000"
    sheet["C2"] = "=B2*2"
    sheet["A2"].comment = Comment("Примітка 25", "Author")
    sheet.merge_cells("A4:B4")
    workbook.save(source)
    output, report = write_translation(source)
    previous, _ = load_report(report)
    first = next(segment for segment in previous.segments if segment.location.endswith("!A1"))
    edited = edit_translation_markup(
        previous.translations[first.id], "Power of efficient pump 25 kW"
    )
    before = package(output)
    result = export_reviewed_copy(source, output, report, {first.id: edited}, tmp_path / "reviewed")
    after = package(result.output)
    for name, data in before.items():
        if name != "xl/worksheets/sheet1.xml":
            assert after[name] == data
    final = load_workbook(result.output, rich_text=True).active
    assert str(final["A1"].value) == "Power of efficient pump 25 kW"
    assert any(isinstance(item, TextBlock) and item.font.b for item in final["A1"].value)
    assert final["B2"].value == 10.5 and final["B2"].number_format == "0.000"
    assert final["C2"].value == "=B2*2"
    assert final["A2"].comment.text == "Comment 25"
    assert str(final.merged_cells) == "A4:B4"


@pytest.mark.parametrize("change", ["number", "identifier", "formatting"])
def test_export_refuses_blocking_changes_without_creating_a_copy(office_translation, change):
    source, output, report, segment_id, markup = office_translation
    changed = {
        "number": markup.replace("25", "250"),
        "identifier": markup.replace("HM-200", "HX-200"),
        "formatting": markup.replace("<k0/>", ""),
    }[change]
    before = set(source.parent.iterdir())
    with pytest.raises(FormaError):
        export_reviewed_copy(source, output, report, {segment_id: changed})
    assert set(source.parent.iterdir()) == before


@pytest.mark.parametrize("stale", ["source", "output", "structure", "report"])
def test_export_refuses_stale_source_target_structure_and_report(office_translation, stale):
    source, output, report, segment_id, markup = office_translation
    fingerprint = hashlib.sha256(report.read_bytes()).hexdigest()
    if stale == "source":
        document = Document(source)
        document.paragraphs[1].runs[0].text = "Змінений оригінал."
        document.save(source)
    elif stale == "output":
        document = Document(output)
        document.paragraphs[0].runs[1].text = "externally changed pump"
        document.save(output)
    elif stale == "structure":
        document = Document(output)
        document.paragraphs[1].add_run(" additional run")
        document.save(output)
    else:
        report.write_text(report.read_text() + "\n")
    before = set(source.parent.iterdir())
    with pytest.raises(FormaError):
        export_reviewed_copy(
            source, output, report, {segment_id: markup}, expected_report_fingerprint=fingerprint
        )
    assert set(source.parent.iterdir()) == before


def test_export_uses_new_names_and_never_overwrites_existing_files(office_translation):
    source, output, report, segment_id, markup = office_translation
    edited = edit_translation_markup(markup, plain_text(markup).replace("pump", "efficient pump"))
    first = export_reviewed_copy(source, output, report, {segment_id: edited})
    first_bytes = first.output.read_bytes(), first.report.read_bytes()
    second = export_reviewed_copy(source, output, report, {segment_id: edited})
    assert first.output != second.output and first.report != second.report
    assert (first.output.read_bytes(), first.report.read_bytes()) == first_bytes
    # The freshly produced report is itself editable using the original source.
    updated = edit_translation_markup(edited, plain_text(edited).replace("efficient", "reliable"))
    third = export_reviewed_copy(source, first.output, first.report, {segment_id: updated})
    assert load_report(third.report)[0].translations[segment_id] == updated


def test_duplicate_source_mapping_is_rejected_even_with_distinct_report_ids(office_translation):
    source, output, report, segment_id, markup = office_translation
    data = json.loads(report.read_text())
    data["segments"][1].update(
        location=data["segments"][0]["location"], source=data["segments"][0]["source"]
    )
    report.write_text(json.dumps(data))
    with pytest.raises(FormaError, match="Оригінал"):
        export_reviewed_copy(source, output, report, {segment_id: markup})


def test_changes_during_export_do_not_publish_a_stale_copy(office_translation, monkeypatch):
    source, output, report, segment_id, markup = office_translation
    original_write = OfficeDocument.write

    def racing_write(self, translations, destination, **kwargs):
        original_write(self, translations, destination, **kwargs)
        report.write_text(report.read_text() + "\n")

    monkeypatch.setattr(OfficeDocument, "write", racing_write)
    before = set(source.parent.iterdir())
    with pytest.raises(FormaError, match="під час експорту"):
        export_reviewed_copy(source, output, report, {segment_id: markup})
    assert set(source.parent.iterdir()) == before


def test_empty_short_translation_cannot_be_exported(tmp_path):
    source = tmp_path / "heading.docx"
    document = Document()
    document.add_paragraph("Текст")
    document.save(source)
    output, report = write_translation(source)
    segment = load_report(report)[0].segments[0]
    before = set(tmp_path.iterdir())
    with pytest.raises(FormaError, match="порожнім"):
        export_reviewed_copy(source, output, report, {segment.id: "<r0></r0>"})
    assert set(tmp_path.iterdir()) == before


@pytest.fixture
def shared_translation(tmp_path):
    from forma.documents.ooxml import R, S

    source = tmp_path / "shared.xlsx"
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row></sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<sst xmlns="{S}"><si><r><t>Потужність </t></r><r><rPr><b/></rPr><t>насоса</t></r></si><si><t>Витрата</t></si></sst>',
    }
    with ZipFile(source, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    output, report = write_translation(source, extraction_profile=1)
    previous = load_report(report)[0]
    segment = previous.segments[0]
    markup = edit_translation_markup(previous.translations[segment.id], "Power of efficient pump")
    return source, output, report, segment.id, markup


def test_shared_string_edit_preserves_reuse_and_all_worksheet_bytes(shared_translation):
    source, output, report, segment_id, markup = shared_translation
    before = package(output)
    result = export_reviewed_copy(source, output, report, {segment_id: markup})
    after = package(result.output)
    assert after["xl/worksheets/sheet1.xml"] == before["xl/worksheets/sheet1.xml"]
    actual = OfficeDocument(result.output, extraction_profile=1)
    first = next(
        segment for segment in actual.segments if segment.plain == "Power of efficient pump"
    )
    assert first.location == "Data!A1, Data!B1"


def test_per_cell_shared_string_edit_does_not_change_another_use(shared_translation):
    from forma.documents.ooxml import S

    source = shared_translation[0].with_name("per-cell.xlsx")
    source.write_bytes(shared_translation[0].read_bytes())
    output, report = write_translation(source)
    previous, _ = load_report(report)
    assert previous.extraction_profile == 2
    segment = next(item for item in previous.segments if item.location == "Data!A1")
    edited = edit_translation_markup(previous.translations[segment.id], "Power of efficient pump")
    result = export_reviewed_copy(source, output, report, {segment.id: edited})
    # Resolve actual persisted cell references independently from application IDs.
    archive = package(result.output)
    strings = etree.fromstring(archive["xl/sharedStrings.xml"])
    values = ["".join(item.itertext()) for item in strings.findall(f"{{{S}}}si")]
    sheet = etree.fromstring(archive["xl/worksheets/sheet1.xml"])
    actual = {cell.get("r"): values[int(cell.findtext(f"{{{S}}}v"))]
              for cell in sheet.iter(f"{{{S}}}c") if cell.get("t") == "s"}
    assert actual == {"A1": "Power of efficient pump", "B1": "Power of pump", "C1": "Flow"}
    reopened, _ = load_report(result.report)
    assert reopened.extraction_profile == 2
    again = export_reviewed_copy(source, result.output, result.report, {segment.id: edited})
    assert package(again.output)["xl/worksheets/sheet1.xml"] == archive["xl/worksheets/sheet1.xml"]


@pytest.mark.parametrize("remapping", ["shared_reference", "sheet_name"])
def test_shared_string_export_refuses_changed_reference_mapping(shared_translation, remapping):
    source, output, report, segment_id, markup = shared_translation
    if remapping == "shared_reference":
        rewrite_part(
            output,
            "xl/worksheets/sheet1.xml",
            lambda data: data.replace(b"<v>0</v>", b"<v>1</v>", 1),
        )
    else:
        rewrite_part(
            output, "xl/workbook.xml", lambda data: data.replace(b'name="Data"', b'name="Moved"')
        )
    before = set(source.parent.iterdir())
    with pytest.raises(FormaError):
        export_reviewed_copy(source, output, report, {segment_id: markup})
    assert set(source.parent.iterdir()) == before


def test_publication_collision_never_overwrites_and_removes_only_own_partial_files(
    office_translation, monkeypatch
):
    import forma.documents.editor as editor

    source, output, report, segment_id, markup = office_translation
    original_atomic = editor.atomic_bytes
    competitor = output.with_name(output.stem + ".reviewed.docx.forma.json")

    def racing_publish(path, data, **kwargs):
        if path == competitor:
            competitor.write_bytes(b"other process report")
        return original_atomic(path, data, **kwargs)

    monkeypatch.setattr(editor, "atomic_bytes", racing_publish)
    with pytest.raises(FormaError):
        export_reviewed_copy(source, output, report, {segment_id: markup})
    assert competitor.read_bytes() == b"other process report"
    assert not competitor.with_name(competitor.name.removesuffix(".forma.json")).exists()


def test_legacy_report_with_omitted_toc_heading_still_edits_correct_body(tmp_path):
    source = tmp_path / "legacy-toc.docx"
    document = Document()
    paragraph = document.add_paragraph("ДОДАТКИ")
    paragraph.add_run().add_tab()
    paragraph.add_run("40")
    document.add_paragraph("Перед запуском.")
    document.save(source)
    output, report = write_translation(source)
    data = json.loads(report.read_text())
    assert len(data["segments"]) == 2
    omitted = data["segments"].pop(0)
    data["issues"] = [i for i in data["issues"] if i.get("segment_id") != omitted["id"]]
    data["options"]["instructions"] = "Use British spelling."
    report.write_text(json.dumps(data))
    body = data["segments"][0]
    result = export_reviewed_copy(source, output, report, {body["id"]: "<r0>Before start-up.</r0>"})
    content = Document(result.output)
    assert content.paragraphs[0].text == "ДОДАТКИ\t40"
    assert content.paragraphs[1].text == "Before start-up."
    assert json.loads(result.report.read_text())["options"]["instructions"] == "Use British spelling."
    # The compatibility exception must not accept arbitrary missing prose.
    data["segments"] = [omitted]
    report.write_text(json.dumps(data))
    with pytest.raises(FormaError, match="повному набору"):
        export_reviewed_copy(source, output, report, {omitted["id"]: omitted["source"]})
