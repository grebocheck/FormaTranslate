from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import time
import zipfile

import pytest
from docx import Document
from lxml import etree
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from PySide6.QtGui import QImage

from forma.core.models import Cancelled, FormaError
from forma.documents import preview
from forma.documents.preview import PreviewSession
from forma.documents.preview_office import prepare_docx


def pdf(path, *, color="1 0 0", pages=2):
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=300 if index == 0 else 600, height=600 if index == 0 else 300)
        stream = DecodedStreamObject()
        stream.set_data(f"q {color} rg 30 30 100 100 re f Q".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


@pytest.fixture
def session(tmp_path):
    instance = PreviewSession(cache_parent=tmp_path)
    yield instance
    instance.close()


def test_real_isolated_pdf_pages_keep_geometry_paper_and_page_count(session, tmp_path):
    source = pdf(tmp_path / "source.pdf")
    original = source.read_bytes()
    first = session.render(source, 0, 600, threading.Event())
    second = session.render(source, 1, 600, threading.Event())
    missing = session.render(source, 2, 600, threading.Event())
    assert (first.width, first.height, first.page_count) == (600, 1200, 2)
    assert (second.width, second.height) == (600, 300)
    assert missing.image_path is None and missing.page_count == 2 and missing.page_index == 2
    assert (missing.width, missing.height) == (0, 0)
    image = QImage(str(first.image_path))
    assert image.pixelColor(5, 5).name() == "#ffffff"
    assert image.pixelColor(100, 1100).name() == "#ff0000"
    assert source.read_bytes() == original
    assert first.image_path.parent.is_relative_to(session._root)


def test_cache_uses_identity_and_stat_invalidation(session, tmp_path, monkeypatch):
    source = pdf(tmp_path / "source.pdf")
    calls = []
    run = session._run
    monkeypatch.setattr(session, "_run", lambda *args, **kwargs: (calls.append(args[0]), run(*args, **kwargs))[1])
    first = session.render(source, 0, 500, threading.Event())
    assert session.render(source, 0, 500, threading.Event()) is first
    assert len(calls) == 1
    original_stat = source.stat()
    replacement = pdf(tmp_path / "replacement.pdf", color="0 0 1")
    replacement.replace(source)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    changed = session.render(source, 0, 500, threading.Event())
    assert len(calls) == 2 and changed.image_path != first.image_path
    assert QImage(str(changed.image_path)).pixelColor(80, 920).name() == "#0000ff"


def test_width_is_bounded_and_independent_files_never_share_preview(session, tmp_path):
    first = session.render(pdf(tmp_path / "one.pdf"), 0, 50000, threading.Event())
    second = session.render(pdf(tmp_path / "two.pdf", color="0 0 1"), 0, 50000, threading.Event())
    assert first.width <= 2400 and first.height <= 4000
    assert first.image_path != second.image_path
    assert first.image_path.read_bytes() != second.image_path.read_bytes()


def test_cache_eviction_keeps_both_displayed_images_alive(session, tmp_path, monkeypatch):
    source = pdf(tmp_path / "source.pdf")
    first = session.render(source, 0, 600, threading.Event())
    cached_folder, _ = session._documents[preview.file_signature(source)]
    # A converted DOCX PDF can dominate disk usage. Evict its reusable PDF while
    # preserving the source image already returned for a pending paired display.
    (cached_folder / "converted.pdf").write_bytes(b"x" * 70000)
    monkeypatch.setattr(preview, "MAX_CACHE_BYTES", 40000)
    second = session.render(pdf(tmp_path / "translation.pdf", color="0 0 1"), 0, 600, threading.Event())
    assert preview._disk_bytes(session._root) <= 40000
    assert not cached_folder.exists()
    assert first.image_path.is_file() and second.image_path.is_file()
    assert not QImage(str(first.image_path)).isNull()
    assert not QImage(str(second.image_path)).isNull()
    assert session.render(source, 0, 600, threading.Event()) is first


def test_missing_invalid_and_password_protected_pdfs_have_actionable_errors(session, tmp_path):
    with pytest.raises(FormaError, match="недоступний"):
        session.render(tmp_path / "missing.pdf", 0, 600, threading.Event())
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a PDF")
    with pytest.raises(FormaError, match="PDF"):
        session.render(invalid, 0, 600, threading.Event())
    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt("secret")
    writer.write(locked)
    with pytest.raises(FormaError, match="паролем"):
        session.render(locked, 0, 600, threading.Event())


def test_docx_missing_converter_does_not_change_source(tmp_path, docx_file):
    before = docx_file.read_bytes()
    instance = PreviewSession(converter="", cache_parent=tmp_path)
    try:
        with pytest.raises(FormaError, match="установіть LibreOffice"):
            instance.render(docx_file, 0, 600, threading.Event())
        assert docx_file.read_bytes() == before
        assert not list(instance._root.iterdir())
    finally:
        instance.close()


def converter_script(tmp_path, body):
    script = tmp_path / "converter"
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o700)
    return str(script)


@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX executable")
@pytest.mark.parametrize("body, message", [("raise SystemExit(4)\n", "побудувати"), ("pass\n", "не створив PDF")])
def test_failed_converter_keeps_no_partial_cache(tmp_path, docx_file, body, message):
    instance = PreviewSession(converter=converter_script(tmp_path, body), cache_parent=tmp_path)
    try:
        with pytest.raises(FormaError, match=message):
            instance.render(docx_file, 0, 600, threading.Event())
        assert not list(instance._root.iterdir())
    finally:
        instance.close()


@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX executable")
@pytest.mark.parametrize("operation", ["timeout", "cancel", "close"])
def test_converter_timeout_cancellation_and_close_reap_process(tmp_path, docx_file, operation):
    pid_file = tmp_path / "pid"
    converter = converter_script(tmp_path, f"import os,time\nfrom pathlib import Path\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n")
    instance = PreviewSession(converter=converter, cache_parent=tmp_path, conversion_timeout=.25 if operation == "timeout" else 10)
    cancel = threading.Event()
    errors = []

    def render():
        try:
            instance.render(docx_file, 0, 600, cancel)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=render)
    thread.start()
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert pid_file.exists()
    if operation == "cancel":
        cancel.set()
    if operation == "close":
        instance.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], FormaError if operation == "timeout" else Cancelled)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    instance.close()
    assert not instance._root.exists()


def test_cancelled_request_never_launches_worker(session, tmp_path, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(preview, "start_worker", lambda *a, **k: pytest.fail("unexpected worker"))
    with pytest.raises(Cancelled):
        session.render(pdf(tmp_path / "source.pdf"), 0, 600, cancel)


def test_stale_source_is_not_published(session, tmp_path, monkeypatch):
    source = pdf(tmp_path / "source.pdf")
    run = session._run

    def change_after_render(*args, **kwargs):
        run(*args, **kwargs)
        pdf(source, color="0 0 1")

    monkeypatch.setattr(session, "_run", change_after_render)
    with pytest.raises(Cancelled, match="змінився"):
        session.render(source, 0, 500, threading.Event())
    assert not session._pages and not list(session._root.rglob("*.png"))


def test_private_docx_copy_removes_external_loading_but_preserves_cached_text(tmp_path):
    source = tmp_path / "linked.docx"
    doc = Document()
    doc.add_paragraph("Visible cached content")
    doc.save(source)
    with zipfile.ZipFile(source) as archive:
        parts = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    relationships = etree.fromstring(parts["word/_rels/document.xml.rels"])
    etree.SubElement(relationships, "Relationship", Id="remote", Type="image", Target="https://example.invalid/private.png", TargetMode="External")
    parts["word/_rels/document.xml.rels"] = etree.tostring(relationships)
    tree = etree.fromstring(parts["word/document.xml"])
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraph = tree.find(f"{ns}body/{ns}p")
    for value in ["INCLUDE", "PICTURE https://example.invalid/private.png"]:
        etree.SubElement(etree.SubElement(paragraph, ns + "r"), ns + "instrText").text = value
    parts["word/document.xml"] = etree.tostring(tree)
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "private.docx"
    prepare_docx(source, output, lambda: None)
    with zipfile.ZipFile(output) as archive:
        assert b"example.invalid" not in archive.read("word/_rels/document.xml.rels")
        content = archive.read("word/document.xml")
        assert b"example.invalid" not in content and b"Visible cached content" in content
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.skipif(not shutil.which("libreoffice"), reason="Optional installed LibreOffice")
def test_installed_libreoffice_converts_once_and_worker_receives_no_credentials(tmp_path, monkeypatch):
    source = tmp_path / "two.docx"
    document = Document()
    document.add_heading("Installation guide", 0)
    document.add_page_break()
    document.add_paragraph("Second page")
    document.save(source)
    before = source.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret-never-forward")
    launch = preview.start_worker
    commands = []

    def checked(arguments, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["cwd"].is_relative_to(instance._root)
        commands.append(arguments)
        return launch(arguments, **kwargs)

    monkeypatch.setattr(preview, "start_worker", checked)
    instance = PreviewSession(cache_parent=tmp_path)
    try:
        first = instance.render(source, 0, 600, threading.Event())
        second = instance.render(source, 1, 600, threading.Event())
        assert first.page_count == second.page_count == 2
        assert not QImage(str(first.image_path)).isNull()
        assert first.image_path.read_bytes() != second.image_path.read_bytes()
        assert sum("--convert-to" in command for command in commands) == 1
        assert not list(instance._root.rglob("*.docx")) and not list(instance._root.rglob("registrymodifications.xcu"))
        assert source.read_bytes() == before
    finally:
        instance.close()
