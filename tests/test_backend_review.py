from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtTest import QTest

from forma.backend import Backend
from forma.config import Settings
from forma.models import Issue, TranslationResult


def pump(app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


def add_history(backend, folder, name="sample"):
    report = folder / f"{name}.docx.forma.json"
    report.write_text(
        json.dumps(
            {
                "engine": "forma-ooxml",
                "options": {"source_language": "en", "target_language": "uk"},
                "segments": [
                    {
                        "id": "one",
                        "location": "Paragraph 1",
                        "source": "<r0>Pressure 16 bar</r0>",
                        "translation": "<r0>Тиск 15 бар</r0>",
                    },
                    {
                        "id": "two",
                        "location": "Paragraph 2",
                        "source": "<r0>Rated power</r0>",
                        "translation": "<r0>Номінальна потужність</r0>",
                    },
                ],
                "issues": [
                    {
                        "code": "fields_preserved",
                        "message": "Fields preserved",
                        "severity": "warning",
                    }
                ],
            }
        )
    )
    backend._library.add_history(
        {
            "id": name,
            "name": name + ".docx",
            "format": "DOCX",
            "source": str(folder / f"{name}.docx"),
            "report": str(report),
            "output": str(folder / f"{name}.uk.docx"),
            "status": "done",
        }
    )
    backend._history = backend._library.list_history()
    return report


@pytest.fixture
def review_backend(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    qapp.processEvents()  # Finish the queued startup callback before testing side effects.
    yield backend
    pump(qapp, lambda: not backend._threads)


def completed_queue_document(backend, folder, docx_file):
    report = add_history(backend, folder)
    entry = backend._history_entry("sample")
    Path(entry["source"]).write_bytes(docx_file.read_bytes())
    Path(entry["output"]).touch()
    document = {
        "id": "queue-sample",
        "path": entry["source"],
        "name": "sample.docx",
        "format": "DOCX",
        "status": "done",
        "statusText": "Готово із зауваженнями",
        "report": str(report),
        "output": entry["output"],
        "warningCount": 9,
        "progress": 100,
        "tokens": "123",
        "startedAt": 1234,
        "elapsedSeconds": 5,
    }
    backend._documents = [document]
    backend._selected = document["id"]
    backend._persist_queue()
    return document, report


def test_history_review_filter_search_and_navigation_without_queue_changes(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    report = add_history(backend, tmp_path)
    before = report.read_bytes()
    backend.reviewHistory("sample")
    pump(qapp, lambda: not backend._threads)
    assert not backend.documents
    assert not backend.busy
    assert backend.reviewSummary["attention"] == 1
    assert backend.reviewSummary["notes"] == 1
    assert backend.reviewSummary["segments"] == 2
    assert backend.reviewDocumentId == "history:sample"
    assert backend.selectedReviewEntry["id"] == "one"
    assert backend.selectedReviewEntry["severity"] == "error"
    backend.setReviewFilter("all")
    assert len(backend.reviewEntries) == 3
    backend.selectReviewEntry("one")
    backend.stepReviewEntry(1)
    assert backend.selectedReviewId == "two"
    backend.stepReviewEntry(1)
    assert backend.selectedReviewId == "two"
    backend.setReviewSearch("НОМІНАЛЬНА")
    assert len(backend.reviewEntries) == 1
    backend.setReviewSearch("not in this report")
    assert backend.reviewEntries == [] and backend.selectedReviewEntry == {}
    backend.setReviewSearch("")
    backend.setReviewFilter("issues")
    assert backend.selectedReviewId == "one"
    assert report.read_bytes() == before  # Re-checking never rewrites archival reports.
    assert backend._library.load_queue() == []


def test_review_reads_only_one_report_at_a_time_and_ignores_stale_result(
    qapp, tmp_path, monkeypatch
):
    import forma.backend as module

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    add_history(backend, tmp_path, "first")
    add_history(backend, tmp_path, "second")
    original = module.load_report
    entered, release = threading.Event(), threading.Event()
    calls = []

    def delayed(path):
        calls.append(path.name)
        if path.name.startswith("first"):
            entered.set()
            assert release.wait(3)
        return original(path)

    monkeypatch.setattr(module, "load_report", delayed)
    try:
        backend.reviewHistory("first")
        pump(qapp, entered.is_set)
        backend.reviewHistory("second")
        assert len(backend._threads) == 1
        assert backend.reviewSummary["loading"]
        release.set()
        pump(qapp, lambda: not backend._threads)
        assert len(calls) == 2
        assert backend.reviewDocument["name"] == "second.docx"
        assert backend.reviewSummary["hasResult"]
        assert backend.reviewSummary["attention"] == 1
    finally:
        release.set()
        pump(qapp, lambda: not backend._threads)


def test_missing_report_is_actionable_local_error(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    report = add_history(backend, tmp_path)
    report.unlink()
    backend.reviewHistory("sample")
    pump(qapp, lambda: not backend._threads)
    assert backend.reviewSummary["error"]
    assert not backend.reviewSummary["loading"]
    assert not backend.reviewSummary["hasResult"]
    assert not backend.documents


def test_successful_completion_does_not_create_banner_or_erase_another_error(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    document = {
        "id": "test",
        "path": str(tmp_path / "source.docx"),
        "name": "source.docx",
        "format": "DOCX",
        "status": "translating",
    }
    backend._documents = [document]
    result = TranslationResult(
        Path(document["path"]),
        tmp_path / "result.docx",
        [],
        {},
        [Issue("fields_preserved", "Fields preserved")],
        report=tmp_path / "result.docx.forma.json",
    )
    backend._handle_event("result", {"id": "test", "result": result})
    assert backend.message == ""
    assert document["warningCount"] == 0
    assert document["statusText"] == "Готово"
    backend._notice("Another document failed", "error")
    backend._handle_event("result", {"id": "test", "result": result})
    assert backend.message == "Another document failed"
    assert backend.messageKind == "error"
    qapp.processEvents()
    pump(qapp, lambda: not backend._threads)


@pytest.mark.parametrize("in_flight", [False, True])
def test_requeue_clears_old_review_and_ignores_in_flight_results(
    qapp, tmp_path, monkeypatch, review_backend, docx_file, in_flight
):
    import forma.backend as module

    backend = review_backend
    document, report = completed_queue_document(backend, tmp_path, docx_file)
    output = Path(document["output"])
    original = module.load_report
    entered, release = threading.Event(), threading.Event()
    calls = []

    def delayed(path):
        calls.append(path)
        entered.set()
        if in_flight:
            assert release.wait(3)
        return original(path)

    monkeypatch.setattr(module, "load_report", delayed)
    try:
        backend.selectReviewDocument(document["id"])
        if in_flight:
            pump(qapp, entered.is_set)
            assert backend.reviewSummary["loading"]
        else:
            pump(qapp, lambda: not backend._threads)
            assert backend.reviewSummary["hasResult"]
            assert backend.reviewSummary["attention"] == 1
        generation = backend._review_generation

        backend.requeueHistory("sample")
        assert backend._review_generation > generation
        assert document["report"] == "" and document["output"] == ""
        assert document["warningCount"] == 0
        assert document["progress"] == 0 and document["tokens"] == ""
        assert document["startedAt"] == 0 and document["elapsedSeconds"] == 0
        assert not backend.reviewSummary["hasResult"]
        assert not backend.reviewSummary["loading"]
        assert not backend.reviewEntries

        release.set()
        pump(qapp, lambda: not backend._threads)
        assert document["status"] == "ready"
        assert document["warningCount"] == 0
        assert not backend.reviewSummary["hasResult"]
        assert not backend.reviewEntries
        backend.activateReview()
        assert not backend._threads
        assert calls == [report]
        assert backend._library.load_queue()[0]["report"] == ""
        assert backend._library.list_history()[0]["report"] == str(report)
        assert report.is_file() and output.is_file()
    finally:
        release.set()
        pump(qapp, lambda: not backend._threads)


def test_history_review_never_writes_queue_snapshot(
    qapp, tmp_path, monkeypatch, review_backend, docx_file
):
    backend = review_backend
    completed_queue_document(backend, tmp_path, docx_file)
    before = backend._library.load_queue()

    def forbidden():
        pytest.fail("An archival review must not persist or alter the document queue")

    monkeypatch.setattr(backend, "_persist_queue", forbidden)
    backend.reviewHistory("sample")
    pump(qapp, lambda: not backend._threads)
    assert backend.reviewSummary["hasResult"]
    assert backend.reviewSummary["warningCount"] == 1
    assert backend._library.load_queue() == before
    assert backend._documents[0]["warningCount"] == 9


@pytest.mark.parametrize("in_flight", [False, True])
def test_forgotten_history_disappears_from_review_and_cannot_be_restored_by_pending_read(
    qapp, tmp_path, monkeypatch, review_backend, in_flight
):
    import forma.backend as module

    backend = review_backend
    report = add_history(backend, tmp_path)
    before = report.read_bytes()
    original = module.load_report
    entered, release = threading.Event(), threading.Event()

    def delayed(path):
        entered.set()
        if in_flight:
            assert release.wait(3)
        return original(path)

    monkeypatch.setattr(module, "load_report", delayed)
    try:
        backend.reviewHistory("sample")
        if in_flight:
            pump(qapp, entered.is_set)
        else:
            pump(qapp, lambda: not backend._threads)
            assert backend.reviewSummary["hasResult"]
        backend.forgetHistory("sample")
        assert backend.reviewDocumentId == ""
        assert backend._history_review is None
        assert backend.reviewDocuments == []
        assert not backend.reviewEntries
        assert not backend.reviewSummary["hasResult"]
        assert not backend.reviewSummary["loading"]

        release.set()
        pump(qapp, lambda: not backend._threads)
        assert backend.reviewDocumentId == ""
        assert backend.reviewDocuments == []
        assert backend.historyEntries == []
        assert backend._library.list_history() == []
        assert not backend.reviewSummary["hasResult"]
        assert report.read_bytes() == before
    finally:
        release.set()
        pump(qapp, lambda: not backend._threads)


def test_restored_completed_report_loads_only_when_review_is_requested(
    qapp, tmp_path, monkeypatch, review_backend, docx_file
):
    import forma.backend as module

    document, report = completed_queue_document(review_backend, tmp_path, docx_file)
    original = module.load_report
    calls = []

    def observed(path):
        calls.append(path)
        return original(path)

    def no_translation(*args, **kwargs):
        pytest.fail("Restoring or reviewing the queue must not launch a translation")

    monkeypatch.setattr(module, "load_report", observed)
    monkeypatch.setattr(module, "TranslationEngine", no_translation)
    restored = Backend()
    try:
        qapp.processEvents()
        QTest.qWait(20)
        assert restored.documents[0]["status"] == "done"
        assert calls == [] and not restored._threads
        assert not restored.reviewSummary["hasResult"]
        restored.selectDocument(document["id"])
        qapp.processEvents()
        assert calls == [] and not restored._threads

        restored.activateReview()
        pump(qapp, lambda: not restored._threads)
        assert calls == [report]
        assert restored.reviewDocumentId == document["id"]
        assert restored.reviewSummary["hasResult"]
        assert restored.reviewSummary["attention"] == 1
        assert not restored.busy
    finally:
        pump(qapp, lambda: not restored._threads)
