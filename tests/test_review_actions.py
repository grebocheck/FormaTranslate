from __future__ import annotations

import json
import time

import pytest
from PySide6.QtTest import QTest

from forma.backend import Backend
from forma.config import Settings
from forma.models import Options, TranslationResult
from forma.ooxml import OfficeDocument
from forma.reports import write_translation_report
from forma.review_store import ReviewStore


def pump(app, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def reviewed(qapp, monkeypatch, docx_file, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    source = OfficeDocument(docx_file)
    translations = {segment.id: segment.source for segment in source.segments}
    output = tmp_path / "translated.uk.docx"
    source.write(translations, output)
    result = TranslationResult(docx_file, output, source.segments, translations, source.issues)
    write_translation_report(result, Options("en", "uk"), "fixture-model")
    backend = Backend()
    record = {
        "id": "attempt",
        "name": docx_file.name,
        "format": "DOCX",
        "source": str(docx_file),
        "output": str(output),
        "report": str(result.report),
        "status": "done",
        "sourceLanguage": "en",
        "targetLanguage": "uk",
        "model": "fixture-model",
    }
    backend._library.add_history(record)
    backend._history = backend._library.list_history()
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    backend.setReviewFilter("all")
    segment = next(s for s in source.segments if s.plain == "Heat meter commissioning")
    backend.selectReviewEntry(segment.id)
    yield backend, result, segment
    backend.cancelReviewEdit()
    pump(qapp, lambda: not backend._threads)


def test_confirm_and_reopen_persist_without_changing_document(reviewed, qapp):
    backend, result, segment = reviewed
    original, report = result.output.read_bytes(), result.report.read_bytes()
    assert backend.selectedReviewEntry["canAccept"]
    backend.confirmReviewEntry()
    assert backend.selectedReviewEntry["accepted"]
    state = ReviewStore().load(result.report)
    assert segment.id in state["accepted"]
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    backend.selectReviewEntry(segment.id)
    assert backend.selectedReviewEntry["accepted"]
    backend.reopenReviewEntry()
    assert not backend.selectedReviewEntry["accepted"]
    assert not ReviewStore().load(result.report)["accepted"]
    assert result.output.read_bytes() == original and result.report.read_bytes() == report


def test_edit_applies_draft_then_exports_separate_copy_without_api(reviewed, qapp, monkeypatch):
    import httpx

    def no_api(*args, **kwargs):
        pytest.fail("Manual review must not call an API")

    monkeypatch.setattr(httpx, "Client", no_api)
    backend, result, segment = reviewed
    before = result.output.read_bytes()
    backend.beginReviewEdit()
    assert backend.reviewEditing
    backend.updateReviewDraft("Введення теплолічильника в експлуатацію")
    backend.selectReviewDocument("another")
    backend.setReviewFilter("issues")
    assert backend.reviewFilter == "all"
    assert backend.applyReviewEdit(), backend.reviewEditError
    assert not backend.reviewEditing
    assert backend.selectedReviewEntry["edited"]
    assert backend.reviewHasEdits
    assert result.output.read_bytes() == before
    assert segment.id in ReviewStore().load(result.report)["edits"]
    backend.confirmReviewEntry()
    backend.exportReviewEdits()
    pump(qapp, lambda: not backend._threads)
    assert not backend.reviewActionError, backend.reviewActionError
    assert not backend.reviewSaving
    assert not backend.reviewHasEdits
    assert backend.reviewDocument["output"] != str(result.output)
    edited = OfficeDocument(backend.reviewDocument["output"])
    assert any(s.plain == "Введення теплолічильника в експлуатацію" for s in edited.segments)
    assert result.output.read_bytes() == before
    assert len(backend.historyEntries) == 2
    assert not backend.documents
    assert backend.historyEntries[0]["inputTokens"] == 0
    assert not ReviewStore().load(result.report)["edits"]
    backend.selectReviewEntry(segment.id)
    assert backend.selectedReviewEntry["accepted"]


def test_numeric_change_is_rejected_and_typing_survives_failed_close(reviewed):
    backend, result, _ = reviewed
    segment = next(s for s in result.segments if "16 bar" in s.plain)
    backend.selectReviewEntry(segment.id)
    backend.beginReviewEdit()
    backend.updateReviewDraft(segment.plain.replace("16 bar", "60 bar"))
    assert not backend.applyReviewEdit()
    assert backend.reviewEditing and backend.reviewEditError
    assert not backend.reviewHasEdits
    assert not backend.requestClose()
    assert backend.reviewDraftText.endswith("2.5 m³/h.")
    backend.cancelReviewEdit()
    assert not backend.reviewEditing
    assert not ReviewStore().load(result.report)["edits"]


def test_draft_restores_after_reopen_and_can_be_discarded(reviewed, qapp):
    backend, result, segment = reviewed
    backend.beginReviewEdit()
    backend.updateReviewDraft("Введення теплолічильника в експлуатацію")
    assert backend.applyReviewEdit()
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    backend.selectReviewEntry(segment.id)
    assert backend.selectedReviewEntry["edited"]
    assert backend.selectedReviewEntry["translation"] == "Введення теплолічильника в експлуатацію"
    backend.discardReviewEdit()
    assert not backend.reviewHasEdits
    assert backend.selectedReviewEntry["translation"] == segment.plain
    assert not ReviewStore().load(result.report)["edits"]


def test_changed_report_rejects_approval_and_save_without_overwriting_state(reviewed):
    backend, result, _ = reviewed
    raw = json.loads(result.report.read_text())
    raw["model"] = "changed-model"
    result.report.write_text(json.dumps(raw))
    backend.confirmReviewEntry()
    assert not backend.selectedReviewEntry["accepted"]
    assert backend.reviewActionError
    backend.beginReviewEdit()
    backend.updateReviewDraft("Введення теплолічильника в експлуатацію")
    assert not backend.applyReviewEdit()
    assert "змінився" in backend.reviewEditError
