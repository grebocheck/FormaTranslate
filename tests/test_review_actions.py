from __future__ import annotations

import json
import threading

import pytest
from support import pump

from forma.documents.ooxml import OfficeDocument
from forma.persistence.review_store import ReviewStore


def test_confirm_and_reopen_persist_without_changing_document(reviewed, qapp, monkeypatch):
    import forma.documents.review as module

    original_check = module.translation_issues

    def check(*args):
        assert threading.current_thread() is not threading.main_thread()
        return original_check(*args)

    monkeypatch.setattr(module, "translation_issues", check)
    backend, result, segment = reviewed
    original, report = result.output.read_bytes(), result.report.read_bytes()
    assert backend.selectedReviewEntry["canAccept"]
    original_count = backend.historyEntries[0]["warningCount"]
    backend.confirmReviewEntry()
    assert backend.selectedReviewEntry["accepted"]
    assert backend.historyEntries[0]["warningCount"] == original_count - 1
    state = ReviewStore().load(result.report)
    assert segment.id in state["accepted"]
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    backend.selectReviewEntry(segment.id)
    assert backend.selectedReviewEntry["accepted"]
    backend.reopenReviewEntry()
    assert not backend.selectedReviewEntry["accepted"]
    assert backend.historyEntries[0]["warningCount"] == original_count
    assert not ReviewStore().load(result.report)["accepted"]
    assert result.output.read_bytes() == original and result.report.read_bytes() == report
    backend.setReviewFilter("issues")
    entries = backend.reviewEntries
    index = len(entries) // 2
    backend.selectReviewEntry(entries[index]["id"])
    backend.confirmReviewEntry()
    assert backend.selectedReviewId == entries[index + 1]["id"]
    last = backend.reviewEntries[-1]["id"]
    preceding = backend.reviewEntries[-2]["id"]
    backend.selectReviewEntry(last)
    backend.confirmReviewEntry()
    assert backend.selectedReviewId == preceding


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
    backend.setReviewFilter("issues")
    entry = next(s for s in result.segments if s.plain == "Operating conditions")
    entries = backend.reviewEntries
    index = next(i for i, item in enumerate(entries) if item["id"] == entry.id)
    backend.selectReviewEntry(entry.id)
    backend.beginReviewEdit()
    backend.updateReviewDraft("Умови експлуатації")
    assert backend.applyReviewEdit()
    assert backend.selectedReviewId == entries[index + 1]["id"]
    assert entry.id in ReviewStore().load(backend._review_context["report"])["edits"]


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
