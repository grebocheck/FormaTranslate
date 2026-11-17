"""Native ODF package, rich-text and source-binding invariants."""

from __future__ import annotations

import struct
from zipfile import ZIP_STORED, ZipFile

import pytest
from lxml import etree
from odf_support import make_hidden_layer_odf
from support import TextProvider, local_settings, make_odf

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.odf import DRAW, TEXT, OpenDocument
from forma.translation.engine import TranslationEngine


def package(path):
    with ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def rewrite(path, name, transform):
    with ZipFile(path) as archive:
        infos, comment = archive.infolist(), archive.comment
        parts = {info.filename: archive.read(info) for info in infos}
    parts[name] = transform(parts[name])
    with ZipFile(path, "w") as archive:
        archive.comment = comment
        for info in infos:
            archive.writestr(info, parts[info.filename])


@pytest.mark.parametrize(("extension", "profile"), [("odt", 15), ("odp", 16)])
def test_odf_rich_slots_controls_identity_and_deterministic_native_export(tmp_path, extension, profile):
    source = make_odf(tmp_path / ("source." + extension))
    document = OpenDocument(source)
    assert document.engine == "forma-odf" and document.extraction_profile == profile
    document.validate_package()
    first = document.segments[0]
    assert {"r0", "r1"} <= {node.tag for node in etree.fromstring(("<root>" + first.source + "</root>").encode())}
    assert first.source.count("<k") == 4  # spaces, tab, explicit break, protected page field
    translations = {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ")
                    for segment in document.segments}
    first_output = tmp_path / ("first." + extension)
    second_output = tmp_path / ("second." + extension)
    document.write(translations, first_output)
    document.write(translations, second_output)
    assert first_output.read_bytes() == second_output.read_bytes()
    before, after = package(source), package(first_output)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {"content.xml"}
    assert document.structure_signature() == OpenDocument(first_output).structure_signature()
    content = etree.fromstring(after["content.xml"])
    assert content.find(f".//{{{TEXT}}}page-number").text == "7"
    assert content.find(f".//{{{TEXT}}}s").get(f"{{{TEXT}}}c") == "2"
    with ZipFile(first_output) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == ZIP_STORED
        assert not archive.infolist()[0].extra
    identity = tmp_path / ("identity." + extension)
    document.write({segment.id: segment.source for segment in document.segments}, identity)
    assert package(identity) == before


def test_odt_translates_safe_notes_headers_footers_and_preserves_hidden_deletions_code_and_fields(tmp_path):
    body = ('<text:p>Visible <text:span text:style-name="Hidden">private hidden</text:span> tail '
            '<office:annotation><text:p>private annotation</text:p></office:annotation> ending</text:p>'
            '<text:p>Note<text:note text:id="ft1" text:note-class="footnote"><text:note-citation>7</text:note-citation>'
            '<text:note-body><text:p>Footnote text</text:p></text:note-body></text:note></text:p>'
            '<text:p>private paragraph<text:hidden-paragraph text:is-hidden="true" text:condition="true"/></text:p>'
            '<text:p text:style-name="Code">private code</text:p>'
            '<text:tracked-changes><text:changed-region text:id="c1"><text:deletion><text:p>private deletion</text:p>'
            '</text:deletion></text:changed-region></text:tracked-changes>')
    source = make_odf(tmp_path / "source.odt", body=body)
    rewrite(source, "content.xml", lambda raw: raw.replace(b"<office:automatic-styles>",
            b'<office:automatic-styles><style:style style:name="Hidden" style:family="text">'
            b'<style:text-properties text:display="none"/></style:style>'))
    document = OpenDocument(source)
    assert {"Footnote text", "Header text", "Footer text"} <= {segment.plain for segment in document.segments}
    assert "private" not in " ".join(segment.source + segment.context for segment in document.segments)
    assert "odf_hidden_preserved" in {issue.code for issue in document.issues}
    output = tmp_path / "out.odt"
    document.write({segment.id: segment.source.replace("Visible", "Видимий").replace("Footnote", "Виноска")
                    for segment in document.segments}, output)
    for protected in (b"private hidden", b"private annotation", b"private paragraph", b"private code", b"private deletion"):
        assert protected in package(output)["content.xml"]
    assert document.structure_signature() == OpenDocument(output).structure_signature()


def test_odf_hidden_style_does_not_poison_visible_parent_and_header_instances_are_unique(tmp_path):
    source = make_odf(tmp_path / "source.odt", body='<text:p text:style-name="HiddenChild">private hidden</text:p>'
                      '<text:p text:style-name="VisibleParent">Visible parent</text:p>')
    rewrite(source, "content.xml", lambda raw: raw.replace(b"<office:automatic-styles>",
            b'<office:automatic-styles><style:style style:name="VisibleParent" style:family="paragraph"/>'
            b'<style:style style:name="HiddenChild" style:family="paragraph" style:parent-style-name="VisibleParent">'
            b'<style:text-properties text:display="none"/></style:style>'))
    rewrite(source, "styles.xml", lambda raw: raw.replace(b"</office:master-styles>",
            b'<style:master-page style:name="Second" style:page-layout-name="pm1"><style:header><text:p>Header text</text:p>'
            b'</style:header><style:footer style:display="false"><text:p>private footer</text:p></style:footer>'
            b'</style:master-page></office:master-styles>'))
    document = OpenDocument(source)
    assert "Visible parent" in {segment.plain for segment in document.segments}
    assert "private" not in " ".join(segment.source + segment.context for segment in document.segments)
    headers = [segment.location for segment in document.segments if segment.plain == "Header text"]
    assert len(headers) == len(set(headers)) == 2


def test_odf_empty_rich_slots_remain_bound_when_text_moves_between_runs(tmp_path):
    source = make_odf(tmp_path / "source.odt", body='<text:p><text:span text:style-name="Strong">Hello</text:span> world</text:p>')
    document = OpenDocument(source)
    assert document.segments[0].source == '<r0></r0><r1>Hello</r1><r2> world</r2>'
    output = tmp_path / "out.odt"
    document.write({document.segments[0].id: '<r0></r0><r1></r1><r2>Вітаю світ</r2>',
                    **{segment.id: segment.source for segment in document.segments[1:]}}, output)
    assert document.structure_signature() == OpenDocument(output).structure_signature()


def test_odp_visible_groups_tables_and_scopes_are_independent_of_private_notes(tmp_path):
    source = make_odf(tmp_path / "source.odp")
    document = OpenDocument(source)
    assert {"Grouped text", "Category", "Energy"} <= {segment.plain for segment in document.segments}
    assert "Private" not in " ".join(segment.source + segment.context for segment in document.segments)
    assert {"odf_notes_preserved", "odf_masters_preserved", "odf_presentation_layout"} <= {issue.code for issue in document.issues}
    translations = {segment.id: segment.source for segment in document.segments}
    first = document.segments[0]
    translations[first.id] = first.source.replace("Hello", "Дуже довгий переклад " * 20)
    assert any(issue.code == "odf_text_fit" for issue in document.layout_issues(translations))


def test_odp_unnamed_graphic_shapes_respect_hidden_default_graphic_style(tmp_path):
    source = make_odf(tmp_path / "source.odp", body='<draw:page draw:name="Slide1"><draw:frame>'
                      '<draw:text-box><text:p>private default-hidden shape</text:p></draw:text-box></draw:frame></draw:page>')
    rewrite(source, "content.xml", lambda raw: raw.replace(b"<office:automatic-styles>",
            b'<office:automatic-styles><style:default-style style:family="graphic">'
            b'<style:graphic-properties draw:display="none"/></style:default-style>'))
    document = OpenDocument(source)
    assert not document.segments
    assert "private" not in document.comparison_text()


@pytest.mark.parametrize("extension,profile,legacy", [("odt", 15, 8), ("odp", 16, 10)])
def test_odf_hidden_named_layers_never_enter_provider_payloads_and_remain_in_output(
    tmp_path, extension, profile, legacy,
):
    source = make_hidden_layer_odf(tmp_path / ("source." + extension))
    original = source.read_bytes()
    document = OpenDocument(source)
    assert document.extraction_profile == profile
    assert "PRIVATE" not in " ".join(segment.source + segment.context for segment in document.segments)
    assert "PRIVATE" not in document.comparison_text()
    assert {"Screen label", "Printer label", "Unassigned visible label"} <= {
        segment.plain for segment in document.segments
    }
    assert "odf_hidden_preserved" in {issue.code for issue in document.issues}

    provider = TextProvider()
    provider.input_tokens = provider.output_tokens = 0
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source, tmp_path / "results", Options("en", "uk", review=False, auto_glossary=False),
    )
    assert provider.calls
    assert "PRIVATE" not in repr(provider.calls)
    current = OpenDocument(result.output)
    assert document.structure_signature() == current.structure_signature()
    assert any("Вітаю світ" in segment.plain for segment in current.segments)
    before, after = package(source), package(result.output)
    assert all(after[name] == raw for name, raw in before.items() if name != "content.xml")

    def private_objects(raw):
        tree = etree.fromstring(raw)
        return [etree.tostring(node, method="c14n") for node in tree.iter()
                if node.get(f"{{{DRAW}}}layer") == "private"]

    assert private_objects(before["content.xml"]) == private_objects(after["content.xml"])
    assert source.read_bytes() == original

    # Explicit legacy extraction reconstructs completed reports without shifting
    # the old sequential IDs or removing their previously recorded source text.
    previous = OpenDocument(source, extraction_profile=legacy)
    assert previous.segments[0].plain == "PRIVATE HIDDEN LAYER"
    assert previous.segments[0].id == f"odf-v{legacy}:content.xml#0"
    assert previous.segments[1].plain == "Hello world 25 kW"
    assert previous.segments[1].id == f"odf-v{legacy}:content.xml#1"
    assert len(previous.segments) == len(document.segments) + 2
    assert document.segments[0].id == f"odf-v{profile}:content.xml#0"
    identity = tmp_path / ("legacy-identity." + extension)
    previous.write({segment.id: segment.source for segment in previous.segments}, identity)
    assert package(identity) == before


@pytest.mark.parametrize("mutation", ["type", "missing", "encryption", "signature", "unsafe", "duplicate"])
def test_odf_rejects_invalid_or_unsupported_packages(tmp_path, mutation):
    source = make_odf(tmp_path / "source.odt")
    if mutation == "type":
        rewrite(source, "mimetype", lambda raw: raw.replace(b"text", b"presentation"))
    elif mutation == "missing":
        rewrite(source, "META-INF/manifest.xml", lambda raw: raw.replace(b'full-path="content.xml"', b'full-path="missing.xml"'))
    elif mutation == "encryption":
        rewrite(source, "META-INF/manifest.xml", lambda raw: raw.replace(b"</manifest:manifest>",
                b'<manifest:encryption-data/></manifest:manifest>'))
    else:
        with ZipFile(source, "a") as archive:
            if mutation == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr("content.xml", b"invalid")
            else:
                archive.writestr({"signature": "META-INF/documentsignatures.xml", "unsafe": "../outside.bin"}[mutation], b"invalid")
    with pytest.raises(FormaError):
        OpenDocument(source)


def test_odf_crc_preflight_streams_unused_media_and_cancelled_export_cleans_up(tmp_path, monkeypatch):
    source = make_odf(tmp_path / "source.odt", extra_parts={"Pictures/media.bin": b"x" * (2 * 1024**2)})
    original_read = ZipFile.read
    reads = []

    def observed(archive, name, *args, **kwargs):
        reads.append(getattr(name, "filename", name))
        return original_read(archive, name, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "read", observed)
    document = OpenDocument(source)
    assert "Pictures/media.bin" not in reads
    document.validate_package()
    assert "Pictures/media.bin" not in reads  # Streamed, never expanded into parts cache.

    def cancelled():
        raise Cancelled()

    document.check_cancel = document.parts.check_cancel = cancelled
    with pytest.raises(Cancelled):
        document.write({segment.id: segment.source for segment in document.segments}, tmp_path / "cancelled.odt")
    assert not (tmp_path / "cancelled.odt").exists()
    assert not list(tmp_path.glob(".forma-*"))
    # Corrupt a compressed unused member while retaining its CRC metadata.
    with ZipFile(source) as archive:
        info = archive.getinfo("Pictures/media.bin")
    raw = bytearray(source.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", raw, info.header_offset + 26)
    offset = info.header_offset + 30 + name_length + extra_length
    raw[offset + max(0, info.compress_size // 2)] ^= 0xFF
    source.write_bytes(raw)
    corrupted = OpenDocument(source)
    with pytest.raises(FormaError, match="OpenDocument"):
        corrupted.validate_package()


def test_odf_protected_numeric_paragraphs_and_styles_remain_in_signature(tmp_path):
    first = make_odf(tmp_path / "first.odt", body='<text:p>Hello</text:p><text:p>12345</text:p>')
    second = make_odf(tmp_path / "second.odt", body='<text:p>Bonjour</text:p><text:p>12346</text:p>')
    left, right = OpenDocument(first), OpenDocument(second)
    assert left.structure_signature() != right.structure_signature()
    assert "12345" in left.comparison_text()


def test_odf_original_protection_and_existing_destination(tmp_path):
    source = make_odf(tmp_path / "source.odt")
    document = OpenDocument(source)
    translations = {segment.id: segment.source for segment in document.segments}
    with pytest.raises(FormaError, match="окремий"):
        document.write(translations, source)
    destination = tmp_path / "existing.odt"
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        document.write(translations, destination)
    assert destination.read_bytes() == b"keep"
    rewrite(source, "custom/untouched.bin", lambda raw: raw + b"changed")
    with pytest.raises(FormaError, match="Оригінал змінився"):
        document.validate_package()


def test_odf_rejects_wrong_profile(tmp_path):
    source = make_odf(tmp_path / "source.odt")
    with pytest.raises(ValueError):
        OpenDocument(source, extraction_profile=10)


def test_odf_repeat_space_expansion_is_bounded_before_large_contexts(tmp_path, monkeypatch):
    import forma.documents.odf as odf_module

    source = make_odf(tmp_path / "source.odt", body='<text:p>A<text:s text:c="20000"/><text:s text:c="20000"/></text:p>')
    with pytest.raises(FormaError, match="26 000"):
        OpenDocument(source)
    source = make_odf(tmp_path / "bounded.odt", body='<text:p>A<text:s text:c="20"/></text:p><text:p>B<text:s text:c="20"/></text:p>')
    monkeypatch.setattr(odf_module, "MAX_EXPANDED_TEXT", 32)
    with pytest.raises(FormaError, match="розгорнутого тексту"):
        OpenDocument(source)
