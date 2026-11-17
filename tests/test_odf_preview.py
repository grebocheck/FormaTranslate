"""Only a private, inactive ODF copy may enter the installed renderer."""
from __future__ import annotations

import threading
import zipfile
from base64 import b64decode

import pytest
from lxml import etree
from pypdf import PdfReader
from PySide6.QtGui import QImage
from support import TextProvider, local_settings, make_odf

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.preview import PreviewSession
from forma.documents.preview_odf import MANIFEST, prepare_odf
from forma.documents.preview_office import find_libreoffice
from forma.translation.engine import TranslationEngine


def preview_source(path, *, content=None, extra=None):
    kind = "text" if path.suffix == ".odt" else "presentation"
    mime = "application/vnd.oasis.opendocument." + kind
    content = content or '''<office:document-content
        xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
        xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0"
        xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.3">
        <office:scripts><script:script>PRIVATE_SCRIPT</script:script></office:scripts>
        <office:body><office:text><text:p>Visible <text:a xlink:href="https://private.invalid">link label</text:a> tail</text:p>
        <draw:frame><draw:image xlink:href="Pictures/safe.png"/></draw:frame>
        <draw:frame><draw:image xlink:href="https://private.invalid/image.png"/></draw:frame>
        <draw:frame xml:base="https://private.invalid/"><draw:image xlink:href="Pictures/safe.svg"/></draw:frame>
        <draw:object xlink:href="Object1"/><draw:plugin xlink:href="file:///private"/>
        <text:section-source xlink:href="https://private.invalid/section"/>
        <text:script>PRIVATE_INLINE_SCRIPT</text:script>
        <office:event-listeners><script:event-listener script:macro-name="private"/></office:event-listeners>
        </office:text></office:body></office:document-content>'''
    parts = {"content.xml": content.encode(), "Pictures/safe.png": b64decode(
                 "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII="),
             "Pictures/safe.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://private.invalid"/></svg>',
             "Basic/Standard/Module.xml": b"PRIVATE_SCRIPT", "Scripts/python/run.py": b"PRIVATE_SCRIPT",
             "Object1/content.xml": b"PRIVATE_OBJECT", "settings.xml": b"PRIVATE_SETTINGS"}
    parts.update(extra or {})
    paths = ["/", *parts]
    manifest = etree.Element(f"{{{MANIFEST}}}manifest", nsmap={"manifest": MANIFEST})
    for name in paths:
        etree.SubElement(manifest, f"{{{MANIFEST}}}file-entry", {
            f"{{{MANIFEST}}}full-path": name,
            f"{{{MANIFEST}}}media-type": mime if name == "/" else "",
        })
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mime, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", etree.tostring(manifest))
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def test_private_odf_preview_removes_active_and_external_content_keeps_cached_labels(tmp_path):
    source = preview_source(tmp_path / "source.odt")
    before = source.read_bytes()
    destination = tmp_path / "private.odt"
    prepare_odf(source, destination, lambda: None)
    assert source.read_bytes() == before
    with zipfile.ZipFile(destination) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED and not first.extra
        assert set(archive.namelist()) == {"mimetype", "META-INF/manifest.xml", "content.xml", "Pictures/safe.png"}
        data = archive.read("content.xml")
        assert b"Visible " in data and b"link label" in data and b" tail" in data
        assert b"Pictures/safe.png" in data
        for protected in (b"private.invalid", b"PRIVATE_", b"file:///", b"safe.svg", b"Object1", b"xml:base"):
            assert protected not in data
        manifest = archive.read("META-INF/manifest.xml")
        assert b"Basic/" not in manifest and b"settings.xml" not in manifest and b"safe.svg" not in manifest


@pytest.mark.parametrize("target", ["../safe.png", "file:///safe.png", "//server/share.png", "Pictures/%252e%252e/a.png", "Pictures/safe.png?url=x"])
def test_private_odf_preview_rejects_nonpackage_resource_targets(tmp_path, target):
    content = f'''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:xlink="http://www.w3.org/1999/xlink">
        <draw:image xlink:href="{target}"/></office:document-content>'''
    source = preview_source(tmp_path / "source.odt", content=content)
    destination = tmp_path / "private.odt"
    prepare_odf(source, destination, lambda: None)
    with zipfile.ZipFile(destination) as archive:
        assert b"xlink:href=" not in archive.read("content.xml")


def test_private_odf_preview_freezes_dde_macros_and_expression_fields(tmp_path):
    content = '''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
        <text:dde-connection-decls><text:dde-connection-decl office:dde-topic="file:///private"
        office:dde-application="remote-program" office:automatic-update="true"/></text:dde-connection-decls>
        <text:p>Start <text:execute-macro text:name="macro:///private">cached macro label</text:execute-macro>
        <text:expression text:formula="of:=WEBSERVICE(&quot;https://private.invalid&quot;)">cached expression</text:expression>
        <text:dde-connection text:connection-name="private">cached DDE</text:dde-connection> end</text:p>
        </office:document-content>'''
    source = preview_source(tmp_path / "source.odt", content=content)
    with zipfile.ZipFile(source) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    parts["META-INF/manifest.xml"] = parts["META-INF/manifest.xml"].replace(
        b"<manifest:manifest ", b'<manifest:manifest xml:base="https://private.invalid/" ')
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    destination = tmp_path / "private.odt"
    prepare_odf(source, destination, lambda: None)
    with zipfile.ZipFile(destination) as archive:
        data = archive.read("content.xml")
        assert all(value in data for value in (b"cached macro label", b"cached expression", b"cached DDE", b" end"))
        assert all(value not in data for value in (b"dde-topic", b"dde-connection", b"execute-macro", b"WEBSERVICE", b"file:///", b"macro:///"))
        assert b"xml:base" not in archive.read("META-INF/manifest.xml")


def test_private_odf_preview_rejects_svg_disguised_as_raster_or_font(tmp_path):
    content = '''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:xlink="http://www.w3.org/1999/xlink">
        <draw:image xlink:href="Pictures/disguised.png"/></office:document-content>'''
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///private"/></svg>'
    source = preview_source(tmp_path / "source.odt", content=content, extra={
        "Pictures/disguised.png": svg, "Pictures/disguised.jpg": svg, "Fonts/disguised.ttf": svg,
        "Fonts/svg.otf": b"OTTO\x00\x01" + b"\0" * 6 + b"SVG " + b"\0" * 12,
    })
    destination = tmp_path / "private.odt"
    prepare_odf(source, destination, lambda: None)
    with zipfile.ZipFile(destination) as archive:
        assert "Pictures/safe.png" in archive.namelist()
        assert not any("disguised" in name or name == "Fonts/svg.otf" for name in archive.namelist())
        assert b"xlink:href=" not in archive.read("content.xml")


def test_odf_preview_dtd_and_manifest_encryption_fail_before_converter(tmp_path):
    source = preview_source(tmp_path / "source.odt", content='<!DOCTYPE root [<!ENTITY x "private">]><root>&x;</root>')
    destination = tmp_path / "private.odt"
    with pytest.raises(FormaError, match="OpenDocument"):
        prepare_odf(source, destination, lambda: None)
    assert not destination.exists()
    preview_source(source)
    with zipfile.ZipFile(source) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = etree.fromstring(parts["META-INF/manifest.xml"])
    etree.SubElement(manifest[1], f"{{{MANIFEST}}}encryption-data")
    parts["META-INF/manifest.xml"] = etree.tostring(manifest)
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(FormaError, match="OpenDocument"):
        prepare_odf(source, destination, lambda: None)
    assert not destination.exists()


def test_odf_preview_cancellation_removes_partial_copy(tmp_path):
    source = preview_source(tmp_path / "source.odt")
    destination = tmp_path / "private.odt"
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 3:
            raise Cancelled("cancel")

    with pytest.raises(Cancelled):
        prepare_odf(source, destination, cancel)
    assert not destination.exists()


@pytest.mark.parametrize("suffix", [".odt", ".odp"])
def test_odf_preview_missing_converter_has_actionable_error(tmp_path, suffix):
    source = preview_source(tmp_path / ("source" + suffix))
    instance = PreviewSession(converter="", cache_parent=tmp_path)
    try:
        with pytest.raises(FormaError, match="установіть LibreOffice"):
            instance.describe(source, threading.Event())
        assert not list(instance._root.iterdir())
    finally:
        instance.close()


@pytest.mark.parametrize("suffix", [".odt", ".odp"])
def test_preview_session_routes_odf_through_inactive_copy_before_converter(tmp_path, monkeypatch, suffix):
    from forma.documents import preview

    source = preview_source(tmp_path / ("source" + suffix))
    instance = PreviewSession(converter="offline-converter", cache_parent=tmp_path)
    observed = []

    def convert(arguments, directory, timeout, cancel, *, external=False):
        assert external
        with zipfile.ZipFile(arguments[-1]) as archive:
            assert b"private.invalid" not in archive.read("content.xml")
            assert "Scripts/python/run.py" not in archive.namelist()
        observed.append(arguments)
        if suffix == ".odp":
            export_filter = arguments[arguments.index("--convert-to") + 1]
            assert '"ExportHiddenSlides"' in export_filter and '"ExportNotesPages"' in export_filter
        (directory / "preview.pdf").write_bytes(b"stand-in converted PDF")

    def wrong_parser(*args):
        raise AssertionError("OpenDocument entered OOXML renderer preparation")

    monkeypatch.setattr(instance, "_run", convert)
    monkeypatch.setattr(preview, "prepare_office", wrong_parser)
    try:
        instance._pdf(source, preview.file_signature(source), threading.Event())
        assert len(observed) == 1
    finally:
        instance.close()


@pytest.mark.skipif(not find_libreoffice(), reason="Optional installed LibreOffice")
@pytest.mark.parametrize("suffix", [".odt", ".odp"])
def test_installed_libreoffice_renders_source_and_translated_odf(tmp_path, suffix):
    source = make_odf(tmp_path / ("source" + suffix))
    before = source.read_bytes()

    class OfflineProvider(TextProvider):
        input_tokens = output_tokens = 0

    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    output_before = result.output.read_bytes()
    instance = PreviewSession(cache_parent=tmp_path)
    try:
        for path, expected in ((source, "Hello"), (result.output, "Вітаю")):
            layout = instance.describe(path, threading.Event())
            assert layout.page_count >= 1
            page = instance.render(path, 0, 500, threading.Event())
            assert page.page_count == layout.page_count and not QImage(str(page.image_path)).isNull()
            from forma.documents.preview import file_signature

            _, pdf = instance._documents[file_signature(path)]
            assert expected in " ".join(page.extract_text() for page in PdfReader(pdf).pages)
        assert source.read_bytes() == before and result.output.read_bytes() == output_before
        assert not list(instance._root.rglob("*.odt")) and not list(instance._root.rglob("*.odp"))
    finally:
        instance.close()
