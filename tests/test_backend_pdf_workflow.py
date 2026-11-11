"""One Translate action keeps its queue identity across OCR and editor handoffs."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from support import FakeProvider, pump, settle

from forma.core.models import FormaError
from forma.presentation.backend import Backend
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


def setup_backend(qapp, monkeypatch, scan, tmp_path, *, manual=False):
    def native_pdf(*_):
        raise FormaError("Offline fixture: native PDF engine unavailable")

    monkeypatch.setattr("forma.documents.pdf.translate_pdf", native_pdf)
    settings = Settings(
        model="offline-test", base_url="http://localhost/v1", max_parallel_requests=1,
        parallel_documents=1, scan_provider="abbyy" if manual else "forma",
    )
    monkeypatch.setattr(Backend, "_read_settings", lambda _: settings)
    provider = FakeProvider()
    monkeypatch.setattr(
        "forma.presentation.documents_workflow.TranslationEngine",
        lambda configured, control: TranslationEngine(configured, control, provider=provider),
    )
    launches = []
    monkeypatch.setattr("forma.translation.handoff.launch_abbyy_handoff", lambda *args: launches.append(args))
    backend = Backend()
    backend.setOutputFolder(str(tmp_path / "out"))
    backend.addFiles([str(scan)])
    settle(qapp, backend)
    return backend, provider, launches


def test_translate_prepares_scan_and_translates_without_replacing_selected_pdf(
    qapp, monkeypatch, scan, tmp_path, local_converter,
):
    backend, provider, _ = setup_backend(qapp, monkeypatch, scan, tmp_path)
    original_id, original = backend.selectedDocument["id"], scan.read_bytes()
    backend.start("en", "uk", "General", False, False)
    settle(qapp, backend, timeout=10)
    assert provider.calls
    document = backend.selectedDocument
    assert document["id"] == original_id and document["path"] == str(scan)
    assert document["status"] == "done", document.get("error")
    assert Path(document["output"]).suffix == ".docx"
    assert scan.read_bytes() == original
    metadata = json.loads(Path(document["report"]).read_text())
    assert metadata["preparation"]["original_source"] == str(scan)
    assert metadata["source"].endswith(".docx")
    assert not backend.busy and not backend.translationPreparing

    # The queue remains the PDF; Office review uses the recognized DOCX.
    backend.activateReview()
    settle(qapp, backend)
    backend.setReviewFilter("all")
    segment = next(row for row in backend.reviewEntries if row["kind"] == "segment")
    backend.selectReviewEntry(segment["id"])
    assert backend.reviewDocument["format"] == "DOCX"
    assert backend.selectedReviewEntry["canEdit"]
    assert backend.createMemoryProject("OCR review", "en", "uk")
    assert backend.canRememberReviewWording


def test_assisted_import_resumes_same_run_and_does_not_rebind_to_selection(
    qapp, monkeypatch, scan, pdf_file, docx_file, tmp_path,
):
    backend, provider, launches = setup_backend(qapp, monkeypatch, scan, tmp_path, manual=True)
    original_id = backend.selectedDocument["id"]
    callbacks = []
    monkeypatch.setattr(backend._pickers, "choose", lambda title, callback, **kwargs: callbacks.append(callback))
    backend.start("en", "uk", "General", False, False)
    try:
        pump(qapp, lambda: bool(backend.abbyyHandoff))
        assert backend.busy and backend.translationPreparing
        assert backend.abbyyHandoff["continueTranslation"] and len(launches) == 1
        assert not provider.calls
        backend.togglePause()
        assert not backend.paused
        # Changing current selection never changes the pending editor's source.
        backend.addFiles([str(pdf_file)])
        pump(qapp, lambda: len(backend.documents) == 2 and backend.documents[1]["status"] != "loading")
        backend.selectDocument(backend.documents[1]["id"])
        backend.importAbbyyResult(True)
        callbacks[0]([str(docx_file)])
        pump(qapp, lambda: next(d for d in backend.documents if d["id"] == original_id)["status"] == "done")
        assert provider.calls and not backend.abbyyHandoff
        original_document = next(d for d in backend.documents if d["id"] == original_id)
        assert original_document["path"] == str(scan)
        assert Path(original_document["output"]).suffix == ".docx"
    finally:
        backend.cancel()
        settle(qapp, backend)


def test_cancelled_editor_run_cannot_resume_from_an_open_file_picker(
    qapp, monkeypatch, scan, docx_file, tmp_path,
):
    backend, provider, _ = setup_backend(qapp, monkeypatch, scan, tmp_path, manual=True)
    callbacks = []
    monkeypatch.setattr(backend._pickers, "choose", lambda title, callback, **kwargs: callbacks.append(callback))
    backend.start("en", "uk", "General", False, False)
    try:
        pump(qapp, lambda: bool(backend.abbyyHandoff))
        backend.importAbbyyResult(True)
        assert callbacks
        backend.cancel()
        settle(qapp, backend)
        callbacks[0]([str(docx_file)])
        settle(qapp, backend)
        assert backend.selectedDocument["status"] == "cancelled"
        assert not provider.calls and not backend.abbyyHandoff and not backend.busy
        assert not list((tmp_path / "out").glob("*.docx"))
    finally:
        backend.cancel()
        settle(qapp, backend)


def test_cancel_between_ocr_and_translation_never_starts_provider(
    qapp, monkeypatch, scan, tmp_path,
):
    from forma.core.models import Cancelled

    backend, provider, _ = setup_backend(qapp, monkeypatch, scan, tmp_path)
    started = threading.Event()

    def convert(source, folder, language, configured, control, progress):
        progress(20, "Готуємо розпізнавання PDF")
        started.set()
        assert control.cancelled.wait(5)
        raise Cancelled("Stopped")

    monkeypatch.setattr("forma.translation.preparation.convert_scan_pdf", convert)
    backend.start("en", "uk", "General", False, False)
    try:
        assert started.wait(3)
        pump(qapp, lambda: backend.translationPreparing)
        backend.cancel()
        settle(qapp, backend)
        assert backend.selectedDocument["status"] == "cancelled"
        assert backend.selectedDocument["path"] == str(scan)
        assert not provider.calls
    finally:
        backend.cancel()
        settle(qapp, backend)
