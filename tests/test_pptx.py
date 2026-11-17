"""Presentation support preserves unsupported surfaces and report-bound editing."""

from __future__ import annotations

import json
from zipfile import ZipFile

import pytest
from lxml import etree
from support import make_pptx

from forma.core.models import Cancelled, FormaError, Options, TranslationResult
from forma.documents.comparison import compare_files
from forma.documents.editor import export_reviewed_copy
from forma.documents.ooxml import A, OfficeDocument
from forma.documents.pptx import P
from forma.documents.review import load_report
from forma.memory.context import scoped_segment_context, segment_role
from forma.translation.reports import write_translation_report


def package(path):
    with ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def rewrite(path, member, transform):
    parts = package(path)
    parts[member] = transform(parts[member])
    with ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def test_presentation_order_groups_tables_fields_and_untouched_parts(tmp_path):
    source = make_pptx(tmp_path / "source.pptx", extra_parts={
        "ppt/notesSlides/notesSlide1.xml": f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><a:t>Private speaker note</a:t></p:notes>',
        "ppt/slideMasters/slideMaster1.xml": f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}"><a:t>Master text</a:t></p:sldMaster>',
        "ppt/media/image1.png": b"fake raster bytes stay untouched",
    })
    office = OfficeDocument(source)
    assert office.extraction_profile == 4
    assert office.presentation_slides == ("ppt/slides/slide9.xml", "ppt/slides/slide2.xml")
    assert office.segments[0].plain == "Hello"
    assert {segment.plain for segment in office.segments} >= {"Grouped text", "Category", "Energy"}
    assert all(segment.id.startswith("pptx-v4:") for segment in office.segments)
    rich = office.segments[1]
    assert rich.source.endswith("<k0/><k1/>")
    assert "Private speaker note" not in " ".join(segment.plain for segment in office.segments)
    assert {issue.code for issue in office.issues} >= {"pptx_notes_preserved", "pptx_masters_preserved", "pptx_fields_preserved"}
    output = tmp_path / "translated.pptx"
    translations = {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ") for segment in office.segments}
    office.write(translations, output)
    before, after = package(source), package(output)
    assert before.keys() == after.keys()
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"ppt/slides/slide9.xml"}
    tree = etree.fromstring(after["ppt/slides/slide9.xml"])
    assert tree.find(f".//{{{A}}}fld/{{{A}}}t").text == "1"
    assert tree.find(f".//{{{A}}}rPr").get("sz") == "1800"
    assert compare_files(source, [output])["candidates"][0]["structure_ok"]
    identity = tmp_path / "identity.pptx"
    office.write({segment.id: segment.source for segment in office.segments}, identity)
    assert package(identity) == before


def test_pptx_manual_edit_is_bound_to_report_and_detects_slide_reordering(tmp_path):
    source = make_pptx(tmp_path / "source.pptx")
    office = OfficeDocument(source)
    translations = {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ") for segment in office.segments}
    output = tmp_path / "translated.pptx"
    office.write(translations, output)
    result = TranslationResult(source, output, office.segments, translations, office.issues, extraction_profile=4)
    write_translation_report(result, Options(source_language="en", target_language="uk"), "offline-test")
    saved, _ = load_report(result.report)
    assert saved.extraction_profile == 4
    first = office.segments[0]
    def restyle(raw):
        tree = etree.fromstring(raw)
        shape = tree.find(f".//{{{P}}}sp")
        shape.find(f"{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}off").set("x", "90000")
        shape.find(f".//{{{A}}}rPr").set("b", "1")
        return etree.tostring(tree)

    rewrite(output, "ppt/slides/slide9.xml", restyle)
    edited = export_reviewed_copy(source, output, result.report, {first.id: "<r0>Привіт</r0>"})
    assert OfficeDocument(edited.output).segments[0].plain == "Привіт"
    assert package(edited.output)["custom/untouched.bin"] == package(source)["custom/untouched.bin"]
    shape = etree.fromstring(package(edited.output)["ppt/slides/slide9.xml"]).find(f".//{{{P}}}sp")
    assert shape.find(f"{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}off").get("x") == "90000"
    assert shape.find(f".//{{{A}}}rPr").get("b") == "1"
    rewrite(output, "ppt/presentation.xml", lambda raw: raw.replace(b'r:id="rFirst"', b'r:id="temp"')
            .replace(b'r:id="rSecond"', b'r:id="rFirst"').replace(b'r:id="temp"', b'r:id="rSecond"'))
    with pytest.raises(FormaError, match="Структура"):
        export_reviewed_copy(source, output, result.report, {first.id: "<r0>Привіт</r0>"})


def test_pptx_full_slide_title_and_table_context_bind_memory(tmp_path):
    title = "Long title " + "a" * 900
    before = OfficeDocument(make_pptx(tmp_path / "before.pptx", title=title + " included"))
    after = OfficeDocument(make_pptx(tmp_path / "after.pptx", title=title + " NOT included"))
    left = next(segment for segment in before.segments if segment.plain == "Energy")
    right = next(segment for segment in after.segments if segment.plain == "Energy")
    identity = json.loads(scoped_segment_context(before, left))
    assert identity["extraction_profile"] == 4
    assert "first_row" in identity["context"]
    assert identity["scope_sha256"] != json.loads(scoped_segment_context(after, right))["scope_sha256"]
    assert segment_role(before, left) == segment_role(after, right)
    assert identity["scope_prompt"].startswith("Slide 1;")
    body = before.segments[1]
    assert segment_role(before, body) != segment_role(before, left)


def test_pptx_same_slide_labels_bind_memory_without_other_slide_coupling(tmp_path):
    before_path = make_pptx(tmp_path / "before.pptx", title="", body="Open")
    changed_path = make_pptx(tmp_path / "changed.pptx", title="", body="Open")
    unrelated_path = make_pptx(tmp_path / "unrelated.pptx", title="", body="Open", second_title="Changed other slide")
    rewrite(changed_path, "ppt/slides/slide9.xml", lambda raw: raw.replace(b"Grouped text", b"Circuit breaker"))

    def context(path):
        document = OfficeDocument(path)
        segment = next(item for item in document.segments if item.plain.strip() == "Open")
        return scoped_segment_context(document, segment)

    assert context(before_path) != context(changed_path)
    assert context(before_path) == context(unrelated_path)


def test_pptx_hidden_group_text_is_preserved_and_omitted_from_provider_context(tmp_path):
    source = make_pptx(tmp_path / "source.pptx")
    rewrite(source, "ppt/slides/slide9.xml", lambda raw: raw.replace(b'id="4" name="Group"', b'id="4" name="Group" hidden="1"'))
    document = OfficeDocument(source)
    assert "Grouped text" not in " ".join(segment.source + segment.context for segment in document.segments)
    assert "pptx_hidden_shapes_preserved" in {issue.code for issue in document.issues}
    output = tmp_path / "translated.pptx"
    document.write({segment.id: segment.source.replace("Hello", "Вітаю") for segment in document.segments}, output)
    assert b"Grouped text" in package(output)["ppt/slides/slide9.xml"]


def test_pptx_unsupported_graphics_notes_and_master_text_stay_out_of_prompts(tmp_path):
    source = make_pptx(tmp_path / "source.pptx", extra_parts={
        "ppt/diagrams/data1.xml": f'<data xmlns:a="{A}"><a:p><a:r><a:t>Private SmartArt data</a:t></a:r></a:p></data>',
        "ppt/notesSlides/notesSlide1.xml": f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>Private speaker note</a:t></a:r></a:p></p:notes>',
        "ppt/slideMasters/slideMaster1.xml": f'<p:sldMaster xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>Private master text</a:t></a:r></a:p></p:sldMaster>',
    })
    graphic = ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="99" name="Diagram"/></p:nvGraphicFramePr>'
               '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
               '<dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>'
               '<a:p><a:r><a:t>Private unsupported graphic</a:t></a:r></a:p>'
               '</a:graphicData></a:graphic></p:graphicFrame>')
    rewrite(source, "ppt/slides/slide9.xml", lambda raw: raw.replace(b"</p:spTree>", graphic.encode() + b"</p:spTree>"))
    document = OfficeDocument(source)
    assert "pptx_smartart_preserved" in {issue.code for issue in document.issues}
    for segment in document.segments:
        assert "Private" not in segment.source + segment.context + scoped_segment_context(document, segment)


def test_pptx_layout_warning_is_an_estimate_and_never_changes_sizes(tmp_path):
    source = make_pptx(tmp_path / "source.pptx")
    office = OfficeDocument(source)
    translations = {segment.id: segment.source for segment in office.segments}
    first = office.segments[0]
    translations[first.id] = "<r0>" + "Довгий переклад " * 25 + "</r0>"
    issues = office.layout_issues(translations)
    assert any(issue.code == "pptx_text_fit" and issue.segment_id == first.id for issue in issues)
    output = tmp_path / "translated.pptx"
    office.write(translations, output)
    before, after = (etree.fromstring(package(path)["ppt/slides/slide9.xml"]) for path in (source, output))
    assert [dict(node.attrib) for node in before.iter(f"{{{A}}}ext")] == [dict(node.attrib) for node in after.iter(f"{{{A}}}ext")]
    assert [dict(node.attrib) for node in before.iter(f"{{{A}}}rPr")] == [dict(node.attrib) for node in after.iter(f"{{{A}}}rPr")]


@pytest.mark.parametrize("target", ["https://invalid/slide.xml", "../outside.xml", "slides/missing.xml"])
def test_pptx_rejects_invalid_slide_relationships(tmp_path, target):
    source = make_pptx(tmp_path / "source.pptx")
    rewrite(source, "ppt/_rels/presentation.xml.rels", lambda raw: raw.replace(b"slides/slide9.xml", target.encode()))
    with pytest.raises(FormaError, match="зв’язки"):
        OfficeDocument(source)


def test_office_lazy_media_and_cancelled_export_leave_no_artifacts(tmp_path, monkeypatch):
    source = make_pptx(tmp_path / "source.pptx", extra_parts={"ppt/media/large.bin": b"x" * (3 * 1024**2)})
    reads = []
    read = ZipFile.read

    def observed(archive, name, *args, **kwargs):
        reads.append(getattr(name, "filename", name))
        return read(archive, name, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "read", observed)
    office = OfficeDocument(source)
    assert "ppt/media/large.bin" not in reads
    assert not isinstance(office.parts, dict)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 5:
            raise Cancelled()

    office.check_cancel = cancel
    office.parts.check_cancel = cancel
    with pytest.raises(Cancelled):
        office.write({segment.id: segment.source for segment in office.segments}, tmp_path / "cancelled.pptx")
    assert not (tmp_path / "cancelled.pptx").exists()
    assert not list(tmp_path.glob(".forma-*"))


def test_office_lazy_document_rejects_source_mutation(tmp_path):
    source = make_pptx(tmp_path / "source.pptx")
    office = OfficeDocument(source)
    rewrite(source, "custom/untouched.bin", lambda raw: raw + b"changed")
    with pytest.raises(FormaError, match="Оригінал змінився"):
        office.write({segment.id: segment.source for segment in office.segments}, tmp_path / "changed.pptx")
    assert not (tmp_path / "changed.pptx").exists()
