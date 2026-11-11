"""Local worker discovery must never block document-selection bindings."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from support import pump

import forma.presentation.settings_workflow as module
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def settled(qapp, backend):
    deadline = time.monotonic() + 5
    while backend._worker_probe_running and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(5)
    assert not backend._worker_probe_running


def test_slow_path_probe_does_not_run_in_qml_getters_or_document_clicks(qapp, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def pdf(path):
        if threading.current_thread() is threading.main_thread():
            pytest.fail("Worker discovery must not block a QML getter")
        calls.append(("pdf", path))
        started.set()
        assert release.wait(5)
        return "/synthetic/babeldoc"

    def ocr(path):
        calls.append(("ocr", path))
        return "/synthetic/forma-ocr"

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    monkeypatch.setattr(module, "find_babeldoc", pdf)
    monkeypatch.setattr(module, "find_scan_converter", ocr)
    backend = Backend()
    engine = QQmlApplicationEngine()
    window = None
    try:
        assert started.wait(2)
        engine.rootContext().setContextProperty("backend", backend)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        qapp.processEvents()
        for _ in range(25):
            assert not backend.settings["pdfReady"]
            backend.selectDocument("")
            backend.changed.emit()  # Translation progress has the same binding notifications.
            qapp.processEvents()
        assert calls == [("pdf", "")]
        assert not backend.busy
        release.set()
        settled(qapp, backend)
        assert backend.settings["pdfReady"] and backend.settings["scanConverterReady"]
        assert calls == [("pdf", ""), ("ocr", "")]
        window.setProperty("page", 2)
        settled(qapp, backend)
        assert calls == [("pdf", ""), ("ocr", ""), ("pdf", ""), ("ocr", "")]
    finally:
        release.set()
        settled(qapp, backend)
        if window:
            window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_path_changes_ignore_old_background_results(qapp, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def pdf(path):
        if path == "old":
            started.set()
            assert release.wait(5)
            return ""
        return "/synthetic/new-babeldoc"

    def ocr(path):
        if not path:
            finished.set()
            return ""
        return "/synthetic/new-ocr"

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(babeldoc_path="old"))
    monkeypatch.setattr(module, "find_babeldoc", pdf)
    monkeypatch.setattr(module, "find_scan_converter", ocr)
    backend = Backend()
    try:
        assert started.wait(2)
        assert backend.saveSettings({"babeldocPath": "new", "scanConverterPath": "new-ocr"})
        pump(qapp, lambda: not backend.settingsSaving)
        settled(qapp, backend)
        assert backend.settings["pdfReady"] and backend.settings["scanConverterReady"]
        release.set()
        assert finished.wait(2)
        QTest.qWait(30)
        assert backend.settings["pdfReady"] and backend.settings["scanConverterReady"]
    finally:
        release.set()


def test_settings_reads_cache_availability_until_paths_or_explicit_refresh(qapp, monkeypatch):
    calls = []

    def probe(kind, path):
        calls.append((kind, path))
        return path

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    monkeypatch.setattr(module, "find_babeldoc", lambda path: probe("pdf", path))
    monkeypatch.setattr(module, "find_scan_converter", lambda path: probe("ocr", path))
    backend = Backend()
    settled(qapp, backend)
    assert len(calls) == 2
    for _ in range(20):
        assert "pdfReady" in backend.settings
    assert len(calls) == 2
    assert backend.saveSettings({"model": "synthetic-local-model"})
    pump(qapp, lambda: not backend.settingsSaving)
    assert len(calls) == 2
    assert backend.saveSettings({"babeldocPath": "local-pdf", "scanConverterPath": "local-ocr"})
    pump(qapp, lambda: not backend.settingsSaving)
    settled(qapp, backend)
    assert len(calls) == 4
    assert backend.settings["pdfReady"] and backend.settings["scanConverterReady"]
    backend.refreshWorkerAvailability()
    settled(qapp, backend)
    assert len(calls) == 6


def test_history_and_review_bindings_never_stat_files_on_gui_thread(qapp, monkeypatch):
    from forma.presentation import file_availability

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    settled(qapp, backend)
    qapp.processEvents()
    started, release = threading.Event(), threading.Event()
    checked = []
    existing = True

    def inspect(paths):
        assert threading.current_thread() is not threading.main_thread()
        checked.extend(paths)
        started.set()
        assert release.wait(5)
        return dict.fromkeys(paths, existing)

    monkeypatch.setattr(file_availability, "inspect_files", inspect)
    backend._history = [
        {
            "id": str(i),
            "source": f"/source/{i}.docx",
            "output": f"/output/{i}.docx",
            "report": f"/output/{i}.forma.json",
            "error": "",
        }
        for i in range(100)
    ]
    backend._history_review = {**backend._history[0], "path": "/source/0.docx", "format": "DOCX"}
    backend._review_document_id = "0"
    backend._review_entries = [{"id": "one", "kind": "segment", "severity": "warning"}]
    backend._review_selected = "one"
    backend._review_context = {"reportFingerprint": "synthetic"}

    def forbidden(_):
        if threading.current_thread() is threading.main_thread():
            pytest.fail("QML bindings must not inspect files on the GUI thread")
        return True

    monkeypatch.setattr(Path, "is_file", forbidden)
    try:
        for _ in range(20):
            assert len(backend.historyEntries) == 100
            assert backend.reviewDocument
            assert backend.selectedReviewEntry
            backend.changed.emit()
        assert started.wait(2)
        # One worker remains in flight; repeated bindings do not spawn more.
        assert len(checked) == 1
        release.set()
        deadline = time.monotonic() + 5
        while (
            backend._file_status._inflight or backend._file_status._pending
        ) and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(5)
        assert len(checked) == len(set(checked)) == 400
        assert all(row["outputExists"] and row["reportExists"] for row in backend.historyEntries)
        existing = False
        backend.refreshFileAvailability()
        deadline = time.monotonic() + 5
        while backend._file_status._inflight and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(5)
        assert all(not row["outputExists"] for row in backend.historyEntries)
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while backend._file_status._inflight and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(5)


def test_large_document_presentation_reuses_work_and_updates_for_results_and_language(
    qapp, monkeypatch
):
    from forma.core.models import Segment, TranslationResult
    from forma.presentation import documents_workflow

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.setUiLanguage("uk")
    segments = [
        Segment(str(i), f"word/document.xml · абзац {i + 1}", "<r0>Source</r0>")
        for i in range(1600)
    ]
    source = Path("synthetic.docx")
    document = {"id": "one", "segments": segments, "status": "ready", "name": "synthetic.docx"}
    backend._documents = [document]
    backend._selected = "one"
    calls = []
    original = documents_workflow.plain_text

    def count(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(documents_workflow, "plain_text", count)
    assert len(backend.segments) == 1600
    result = TranslationResult(
        source, source, segments, {s.id: "<r0>Translation</r0>" for s in segments}, []
    )
    document["result"] = result
    assert backend.segments[0]["translation"] == "Translation"
    for _ in range(20):
        assert backend.segments[-1]["translation"] == "Translation"
    assert len(calls) == 1600
    backend.setUiLanguage("en")
    assert "paragraph" in backend.segments[0]["location"]
    assert len(calls) == 3200
    document["result"] = TranslationResult(source, source, segments, {"0": "<r0>Revised</r0>"}, [])
    assert backend.segments[0]["translation"] == "Revised"
    assert len(calls) == 4800
    backend._documents = []
    assert backend.segments == []


def test_file_status_cache_is_bounded(qapp):
    from forma.presentation.file_availability import MAX_PATHS, FileAvailability

    cache = FileAvailability()
    cache.update({f"/synthetic/{i}": True for i in range(MAX_PATHS + 200)})
    assert len(cache._values) == MAX_PATHS


def test_history_presentation_reuses_work_and_refreshes_metadata_and_language(qapp, monkeypatch):
    from forma.presentation import history_workflow

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.setUiLanguage("uk")
    backend._history = [{"id": str(i), "source": "/same.docx", "error": ""} for i in range(100)]
    backend._file_status.update({"/same.docx": False})
    calls = []
    original = history_workflow.tr

    def count(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(history_workflow, "tr", count)
    for _ in range(20):
        assert not backend.historyEntries[0]["sourceExists"]
    assert len(calls) == 100
    backend._file_status.update({"/same.docx": True})
    assert backend.historyEntries[0]["sourceExists"]
    assert len(calls) == 200
    backend.setUiLanguage("en")
    assert len(backend.historyEntries) == 100
    assert len(calls) == 300
    backend._history = []
    assert backend.historyEntries == []


@pytest.mark.parametrize("prime", [False, True])
def test_file_status_creation_during_probe_preserves_refresh_or_newer_review_status(
    qapp, monkeypatch, prime
):
    from forma.presentation import file_availability

    started, release = threading.Event(), threading.Event()
    snapshots = []
    exists = False

    def inspect(paths):
        snapshot = exists
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            started.set()
            assert release.wait(5)
        return dict.fromkeys(paths, snapshot)

    monkeypatch.setattr(file_availability, "inspect_files", inspect)
    cache = file_availability.FileAvailability()
    try:
        assert not cache.is_file("/synthetic/new.docx")
        assert started.wait(2)
        exists = True
        if prime:
            cache.update({"/synthetic/new.docx": True})
        else:
            for _ in range(20):
                cache.request(("/synthetic/new.docx",), force=True)
        release.set()
        deadline = time.monotonic() + 5
        while (cache._inflight or cache._pending) and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(5)
        assert cache.is_file("/synthetic/new.docx")
        assert snapshots == ([False] if prime else [False, True])
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while cache._inflight and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(5)
