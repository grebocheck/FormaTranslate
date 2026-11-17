"""EPUB package validation, deterministic publication and immutable resources."""

from __future__ import annotations

import struct
from zipfile import ZIP_STORED, ZipFile

import pytest
from epub_support import UID, XHTML, epub_parts, make_epub, replace_epub_part

from forma.core.models import Cancelled, FormaError
from forma.documents.epub import EpubDocument, resolve_package_uri


def translated(document):
    return {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ")
            .replace("First chapter", "Перший розділ").replace("Second chapter", "Другий розділ")
            for segment in document.segments}


def encryption(algorithm="http://www.idpf.org/2008/embedding", resource="OPS/fonts/font.otf", extra=""):
    return ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            '<enc:EncryptedData><enc:EncryptionMethod Algorithm="' + algorithm + '"/>'
            '<enc:CipherData><enc:CipherReference URI="' + resource + '"/></enc:CipherData>' + extra
            + '</enc:EncryptedData></encryption>')


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_epub_native_roundtrip_metadata_navigation_assets_and_zip_determinism(tmp_path, version):
    source = make_epub(tmp_path / "source.epub", version=version, legacy_doctype=version == "2.0")
    document = EpubDocument(source)
    document.validate_package()
    assert document.engine == "forma-epub" and document.extraction_profile == 13
    assert document.spine == ("OPS/chapter.xhtml", "OPS/chapter2.xhtml")
    assert len({segment.location for segment in document.segments}) == len(document.segments)
    assert [segment.location for segment in document.segments] == [segment.location for segment in EpubDocument(source).segments]
    assert document.segments[0].location == "EPUB · document 1 · fragment 1"
    assert document.publication_identifier == UID
    assert "PRIVATE" not in " ".join(segment.id + segment.location + segment.source + segment.context for segment in document.segments)
    before = epub_parts(source)
    identity = tmp_path / "identity.epub"
    document.write({segment.id: segment.source for segment in document.segments}, identity)
    assert epub_parts(identity) == before
    first, second = tmp_path / "first.epub", tmp_path / "second.epub"
    document.write(translated(document), first)
    document.write(translated(document), second)
    assert first.read_bytes() == second.read_bytes()
    after = epub_parts(first)
    assert before.keys() == after.keys()
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"OPS/chapter.xhtml", "OPS/chapter2.xhtml", "OPS/toc.ncx"} | ({"OPS/nav.xhtml"} if version == "3.0" else set())
    assert before["OPS/package.opf"] == after["OPS/package.opf"]
    assert b"PRIVATE_BOOK_TITLE" in after["OPS/package.opf"] and UID.encode() in after["OPS/package.opf"]
    assert b"PRIVATE_CODE()" in after["OPS/chapter.xhtml"] and b"chapter2.xhtml#PRIVATE_ANCHOR" in after["OPS/chapter.xhtml"]
    assert b"PRIVATE_TITLE" in after["OPS/toc.ncx"]
    assert "Перший розділ".encode() in after["OPS/toc.ncx"]
    with ZipFile(first) as archive:
        mimetype = archive.infolist()[0]
        assert mimetype.filename == "mimetype" and mimetype.compress_type == ZIP_STORED and not mimetype.extra
    reopened = EpubDocument(first)
    assert document.structure_signature() == reopened.structure_signature()
    assert "42" in document.comparison_text()


def test_epub_auxiliary_xhtml_follows_spine_before_nav_and_remote_assets_are_never_fetched(tmp_path):
    auxiliary = f'<html xmlns="{XHTML}"><head><title>PRIVATE_AUX_TITLE</title></head><body><p>Hello footnote.</p></body></html>'
    source = make_epub(tmp_path / "source.epub", extra_parts={"OPS/notes.xhtml": auxiliary})
    replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b"</manifest>",
                      b'<item id="remote" href="https://not-a-real-provider.invalid/private-image.png" media-type="image/png"/></manifest>'))
    document = EpubDocument(source)
    assert document.content_order == ("OPS/chapter.xhtml", "OPS/chapter2.xhtml", "OPS/notes.xhtml", "OPS/nav.xhtml", "OPS/toc.ncx")
    assert "epub_remote_resources" in {issue.code for issue in document.issues}
    assert any("Hello footnote." in segment.plain for segment in document.segments)
    assert "not-a-real-provider" not in " ".join(segment.source + segment.context for segment in document.segments)
    output = tmp_path / "translated.epub"
    document.write(translated(document), output)
    assert "Вітаю footnote.".encode() in epub_parts(output)["OPS/notes.xhtml"]
    assert epub_parts(output)["OPS/package.opf"] == epub_parts(source)["OPS/package.opf"]


@pytest.mark.parametrize("algorithm", ["http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"])
def test_standard_font_obfuscation_is_preserved_without_changing_font_or_unique_identifier(tmp_path, algorithm):
    source = make_epub(tmp_path / "source.epub", extra_parts={
        "OPS/fonts/font.otf": bytes(range(256)) * 8,
        "META-INF/encryption.xml": encryption(algorithm),
    })
    document = EpubDocument(source)
    assert "epub_font_obfuscation" in {issue.code for issue in document.issues}
    output = tmp_path / "translated.epub"
    document.write(translated(document), output)
    before, after = epub_parts(source), epub_parts(output)
    for part in ("OPS/fonts/font.otf", "OPS/package.opf", "META-INF/encryption.xml"):
        assert before[part] == after[part]


@pytest.mark.parametrize(("algorithm", "resource", "extra"), [
    ("http://www.w3.org/2001/04/xmlenc#aes256-cbc", "OPS/fonts/font.otf", ""),
    ("http://www.idpf.org/2008/embedding", "OPS/chapter.xhtml", ""),
    ("http://www.idpf.org/2008/embedding", "OPS/fonts/missing.otf", ""),
    ("http://www.idpf.org/2008/embedding", "OPS/fonts/font.otf", "<enc:CipherValue>secret</enc:CipherValue>"),
    ("http://www.idpf.org/2008/embedding", "OPS/fonts/font.otf", "<enc:EncryptedKey/>"),
])
def test_encrypted_content_unknown_protection_and_invalid_font_targets_are_rejected(tmp_path, algorithm, resource, extra):
    source = make_epub(tmp_path / "source.epub", extra_parts={
        "OPS/fonts/font.otf": b"obfuscated-font", "META-INF/encryption.xml": encryption(algorithm, resource, extra),
    })
    with pytest.raises(FormaError, match="зашифрований|захист"):
        EpubDocument(source)


@pytest.mark.parametrize("href", ["../../outside.xhtml", "%2e%2e/%2e%2e/private", "https://host/chapter.xhtml", "file:///private", "//host/private", "chapter.xhtml?x=1", "chapter.xhtml#private", "bad%xx.xhtml", "bad%FF.xhtml", "bad%5cfile.xhtml", "/chapter.xhtml"])
def test_resource_uri_resolution_rejects_unsafe_or_ambiguous_references(href):
    with pytest.raises(FormaError):
        resolve_package_uri("OPS/package.opf", href)


@pytest.mark.parametrize(("href", "expected"), [("chapter%2Exhtml", "OPS/chapter.xhtml"), ("../shared/chapter.xhtml", "shared/chapter.xhtml"), ("notes%20and%20more.xhtml", "OPS/notes and more.xhtml")])
def test_resource_uri_resolution_supports_normal_relative_paths_and_utf8_percent_decoding(href, expected):
    assert resolve_package_uri("OPS/package.opf", href) == expected
    assert resolve_package_uri("OPS/chapter.xhtml", "#heading", allow_fragment=True) == "OPS/chapter.xhtml"


@pytest.mark.parametrize("mutation", ["multiple-roots", "bad-root", "missing-item", "duplicate-uri", "duplicate-id", "duplicate-spine", "foreign-spine", "remote-spine", "base-uri", "signature", "unsafe-zip"])
def test_epub_package_structure_must_be_unambiguous_before_content_extraction(tmp_path, mutation):
    source = make_epub(tmp_path / "source.epub")
    if mutation == "multiple-roots":
        replace_epub_part(source, "META-INF/container.xml", lambda raw: raw.replace(b"</rootfiles>",
                          b'<rootfile full-path="OPS/second.opf" media-type="application/oebps-package+xml"/></rootfiles>'))
    elif mutation == "bad-root":
        replace_epub_part(source, "META-INF/container.xml", lambda raw: raw.replace(b"OPS/package.opf", b"OPS/missing.opf"))
    elif mutation == "missing-item":
        replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'href="chapter2.xhtml"', b'href="missing.xhtml"'))
    elif mutation == "duplicate-uri":
        replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'href="chapter2.xhtml"', b'href="chapter%2Exhtml"'))
    elif mutation == "duplicate-id":
        replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'id="chapter2"', b'id="pub-id"'))
    elif mutation == "duplicate-spine":
        replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'<itemref idref="chapter2"/>', b'<itemref idref="chapter"/>'))
    elif mutation in {"foreign-spine", "remote-spine"}:
        if mutation == "foreign-spine":
            replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'href="chapter.xhtml" media-type="application/xhtml+xml"', b'href="chapter.xhtml" media-type="image/svg+xml"'))
        else:
            replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'href="chapter.xhtml"', b'href="https://remote.invalid/chapter.xhtml"'))
    elif mutation == "base-uri":
        replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b'<manifest>', b'<manifest xml:base="../">'))
    else:
        with ZipFile(source, "a") as archive:
            archive.writestr("META-INF/signatures.xml" if mutation == "signature" else "../outside.bin", b"invalid")
    with pytest.raises(FormaError):
        EpubDocument(source)


def test_archive_bytes_and_directory_metadata_are_bounded_before_zipfile_parses_them(tmp_path, monkeypatch):
    import forma.documents.epub as epub_module

    source = make_epub(tmp_path / "source.epub")
    monkeypatch.setattr(epub_module, "MAX_ARCHIVE_BYTES", source.stat().st_size - 1)
    monkeypatch.setattr(epub_module, "ZipFile", lambda *_a, **_kw: pytest.fail("oversized archive must fail before directory parsing"))
    with pytest.raises(FormaError, match="512 МБ"):
        EpubDocument(source)
    monkeypatch.setattr(epub_module, "MAX_ARCHIVE_BYTES", 512 * 1024**2)
    raw = bytearray(source.read_bytes())
    end = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", raw, end + 8, 20001, 20001)
    source.write_bytes(raw)
    with pytest.raises(FormaError, match="ZIP-метадані"):
        EpubDocument(source)


def test_package_xml_comments_do_not_change_manifest_or_reading_order(tmp_path):
    source = make_epub(tmp_path / "source.epub")
    replace_epub_part(source, "OPS/package.opf", lambda raw: raw.replace(b"<manifest>", b"<manifest><!-- resource list -->")
                      .replace(b'<spine toc="ncx">', b'<spine toc="ncx"><?example note?><!-- reading order -->'))
    assert EpubDocument(source).spine == ("OPS/chapter.xhtml", "OPS/chapter2.xhtml")


def test_crc_preflight_streams_unused_binary_assets_and_signature_binds_them(tmp_path, monkeypatch):
    source = make_epub(tmp_path / "source.epub", extra_parts={"OPS/media.bin": b"x" * (2 * 1024**2)})
    reads, original_read = [], ZipFile.read

    def observed(archive, name, *args, **kwargs):
        reads.append(getattr(name, "filename", name))
        return original_read(archive, name, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "read", observed)
    document = EpubDocument(source)
    document.validate_package()
    assert "OPS/media.bin" not in reads
    with ZipFile(source) as archive:
        info = archive.getinfo("OPS/media.bin")
    raw = bytearray(source.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", raw, info.header_offset + 26)
    offset = info.header_offset + 30 + name_length + extra_length
    raw[offset + info.compress_size // 2] ^= 0xFF
    source.write_bytes(raw)
    corrupted = EpubDocument(source)
    with pytest.raises(FormaError, match="EPUB"):
        corrupted.validate_package()


def test_epub_exclusive_atomic_output_cancellation_and_mutable_source_guards(tmp_path):
    source = make_epub(tmp_path / "source.epub")
    document = EpubDocument(source)
    values = translated(document)
    with pytest.raises(FormaError, match="всі фрагменти"):
        document.write({}, tmp_path / "incomplete.epub")
    with pytest.raises(FormaError, match="окремий"):
        document.write(values, source)
    existing = tmp_path / "existing.epub"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        document.write(values, existing)
    assert existing.read_bytes() == b"keep"
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 5:
            raise Cancelled()

    document.check_cancel = document.parts.check_cancel = cancel
    with pytest.raises(Cancelled):
        document.write(values, tmp_path / "cancelled.epub")
    assert not (tmp_path / "cancelled.epub").exists() and not list(tmp_path.glob(".forma-*"))
    document.check_cancel = document.parts.check_cancel = lambda: None
    replace_epub_part(source, "OPS/style.css", lambda raw: raw + b"changed")
    with pytest.raises(FormaError, match="Оригінал змінився"):
        document.validate_package()


def test_untranslated_numeric_content_and_publisher_ids_are_preserved_in_structure(tmp_path):
    first = make_epub(tmp_path / "first.epub")
    second = make_epub(tmp_path / "second.epub")
    replace_epub_part(second, "OPS/chapter.xhtml", lambda raw: raw.replace(b"<p>42</p>", b"<p>43</p>"))
    left, right = EpubDocument(first), EpubDocument(second)
    assert left.structure_signature() != right.structure_signature()
    assert "42" in left.comparison_text() and "43" in right.comparison_text()


@pytest.mark.parametrize("budget", ["MAX_CONTENT_BYTES", "MAX_UNPACKED", "MAX_ARCHIVE_BYTES"])
def test_epub_output_budgets_prevent_publishing_a_book_that_cannot_be_reopened(tmp_path, monkeypatch, budget):
    import forma.documents.epub as epub_module

    source = make_epub(tmp_path / "source.epub")
    document = EpubDocument(source)
    monkeypatch.setattr(epub_module, budget, 1)
    output = tmp_path / "too-large.epub"
    with pytest.raises(FormaError, match="завелик|512 МБ"):
        document.write(translated(document), output)
    assert not output.exists() and not list(tmp_path.glob(".forma-*"))
