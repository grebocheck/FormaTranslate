from __future__ import annotations

import json

from support import pump
from test_backend_review import add_history, completed_queue_document

from forma.persistence.session_store import SessionStore
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def settled(qapp, backend):
    pump(qapp, lambda: not backend._threads)


def test_shared_selection_preserves_per_document_navigation_and_pending_identity(qapp, monkeypatch, tmp_path, docx_file):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    document, _ = completed_queue_document(backend, tmp_path, docx_file)
    waiting = {"id": "waiting", "name": "pending.docx", "path": str(tmp_path / "pending.docx"),
               "format": "DOCX", "status": "ready", "statusText": "Ready"}
    backend._documents.append(waiting)
    backend.selectWorkspaceDocument(document["id"])
    backend.setWorkspaceMode("review")
    settled(qapp, backend)
    assert backend.reviewFilter == "all" and backend.selectedReviewId == "one"
    backend.setReviewSearch("rated")
    backend.selectReviewEntry("two")
    context, generation = backend._review_context, backend._review_generation
    backend.setWorkspaceMode("translation")
    backend.setWorkspaceMode("review")
    backend.selectReviewDocument(document["id"])
    assert backend._review_context is context and backend._review_generation == generation
    assert backend.selectedReviewId == "two" and backend.reviewSearch == "rated"
    backend.selectReviewDocument("waiting")
    assert backend.selectedId == backend.workspaceDocumentId == backend.reviewDocumentId == "waiting"
    assert backend.selectedDocument["id"] == "waiting" and not backend.selectedDocument["reviewAvailable"]
    assert not backend.reviewEntries and not backend.reviewSummary["hasResult"]
    assert backend.pendingDocumentCount == 1
    backend.selectDocument(document["id"])
    settled(qapp, backend)
    assert backend.selectedReviewId == "two" and backend.reviewSearch == "rated"
    assert backend.reviewDocumentId == backend.selectedId == document["id"]
    assert backend.requestClose()


def test_history_workspace_survives_restart_without_queue_mutation_or_provider(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    settled(qapp, backend)
    first = add_history(backend, tmp_path, "first")
    second = add_history(backend, tmp_path, "second")
    backend.reviewHistory("first")
    settled(qapp, backend)
    backend.setReviewFilter("issues")
    backend.reviewHistory("second")
    settled(qapp, backend)
    backend.setReviewSearch("rated")
    backend.selectReviewEntry("two")
    backend.reviewHistory("second")
    settled(qapp, backend)
    assert len(backend.workspaceDocuments) == 2
    assert backend.workspaceDocumentModel.rowCount() == 2 and backend.documentModel.rowCount() == 0
    assert backend.selectedDocument["history"] and backend.pendingDocumentCount == 0
    assert backend._library.load_queue() == []
    assert backend.requestClose()
    before = [first.read_bytes(), second.read_bytes()]
    restored = Backend()
    assert restored.workspaceDocumentId == restored.selectedId == restored.reviewDocumentId == "history:second"
    assert restored.workspaceMode == "review"
    assert len(restored.workspaceDocuments) == 2 and not restored.documents
    restored.activateReview()
    settled(qapp, restored)
    assert restored.reviewSearch == "rated" and restored.selectedReviewId == "two"
    restored.selectWorkspaceDocument("history:first")
    settled(qapp, restored)
    assert restored.reviewFilter == "issues"
    restored.closeWorkspaceDocument("history:first")
    settled(qapp, restored)
    assert len(restored.workspaceDocuments) == 1
    assert restored.workspaceDocumentId == "history:second"
    assert len(restored.historyEntries) == 2 and not restored._library.load_queue()
    assert [first.read_bytes(), second.read_bytes()] == before
    assert restored.requestClose()


def test_unapplied_editor_blocks_switch_and_applied_draft_survives_switch(reviewed, qapp, tmp_path):
    backend, result, segment = reviewed
    add_history(backend, tmp_path, "other")
    backend.beginReviewEdit()
    backend.updateReviewDraft("Введення теплолічильника в експлуатацію")
    selected = backend.workspaceDocumentId
    backend.reviewHistory("other")
    backend.setWorkspaceMode("translation")
    backend.closeWorkspaceDocument(selected)
    assert backend.workspaceDocumentId == selected and backend.workspaceMode == "review"
    assert backend.reviewEditing and backend.reviewDraftText == "Введення теплолічильника в експлуатацію"
    assert backend.applyReviewEdit()
    backend.reviewHistory("other")
    settled(qapp, backend)
    backend.selectWorkspaceDocument(selected)
    settled(qapp, backend)
    backend.selectReviewEntry(segment.id)
    assert backend.selectedReviewEntry["edited"]
    assert backend.selectedReviewEntry["translation"] == "Введення теплолічильника в експлуатацію"
    raw = SessionStore().path.read_text()
    assert "Введення теплолічильника в експлуатацію" not in raw
    assert str(result.source) not in raw and str(result.report) not in raw


def test_changed_report_cannot_restore_previous_run_navigation(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    report = add_history(backend, tmp_path)
    backend.reviewHistory("sample")
    settled(qapp, backend)
    backend.setReviewSearch("rated")
    backend.selectReviewEntry("two")
    assert backend.requestClose()
    data = json.loads(report.read_text())
    data["model"] = "another run"
    report.write_text(json.dumps(data))
    restored = Backend()
    restored.activateReview()
    settled(qapp, restored)
    assert restored.reviewFilter == "all" and restored.reviewSearch == ""
    assert restored.selectedReviewId == "one"
    assert restored.requestClose()


def test_workspace_progress_updates_existing_rows_without_reset(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend._documents = [{"id": "active", "name": "active.docx", "path": str(tmp_path / "active.docx"),
                           "format": "DOCX", "status": "translating", "startedAt": 1}]
    backend.changed.emit()
    model = backend.workspaceDocumentModel
    resets, changes = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda *_: changes.append(True))
    backend._handle_event("progress", {"id": "active", "percent": 25, "text": "Translating"})
    assert not resets and changes
    assert model.data(model.index(0), model.ROW_ROLE)["progress"] == 25
    settled(qapp, backend)


def test_workspace_options_are_validated_and_restore_without_changing_running_settings(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    before_settings = backend._settings
    options = {"sourceLanguage": "de", "targetLanguage": "pl", "domain": "Технічна документація",
               "review": False, "autoGlossary": False}
    assert backend.setWorkspaceOptions(options)
    assert not backend.setWorkspaceOptions({"api_key": "must-not-be-persisted"})
    assert not backend.setWorkspaceOptions({"review": "false"})
    assert not backend.setWorkspaceOptions({"domain": "translated UI label"})
    assert not backend.setWorkspaceOptions({"sourceLanguage": []})
    assert backend._settings is before_settings
    assert backend.workspaceOptions == options
    assert backend.requestClose()
    restored = Backend()
    assert restored.workspaceOptions == options
    settled(qapp, restored)
    assert "must-not-be-persisted" not in SessionStore().path.read_text()
    assert restored.requestClose()


def test_navigation_typing_defers_writes_until_close(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    add_history(backend, tmp_path)
    backend.reviewHistory("sample")
    settled(qapp, backend)
    backend._persist_workspace()
    saves = []
    original = backend._workspace_store.save

    def save(value):
        saves.append(value)
        original(value)

    monkeypatch.setattr(backend._workspace_store, "save", save)
    for text in ("r", "ra", "rat", "rated"):
        backend.setReviewSearch(text)
    assert saves == []
    assert backend.requestClose()
    assert len(saves) == 1
    assert SessionStore().load()["navigation"]["history:sample"]["search"] == "rated"


def test_workspace_report_actions_use_requested_id_and_readable_report(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    report = add_history(backend, tmp_path)
    backend.reviewHistory("sample")
    settled(qapp, backend)
    diagnostic = tmp_path / "failed.failure.html"
    backend._documents.append({"id": "failed", "path": str(tmp_path / "failed.docx"), "name": "failed.docx",
                               "format": "DOCX", "status": "error", "diagnosticReport": str(diagnostic)})
    opened = []
    monkeypatch.setattr(backend, "openPath", opened.append)
    backend.openWorkspaceReport("failed")
    backend.openWorkspaceReport("history:sample")
    backend.openWorkspaceReport("missing")
    assert opened == [str(diagnostic), str(report.with_suffix(".html"))]
    assert backend.workspaceDocumentId == "history:sample"
    assert backend.requestClose()


def test_returning_to_review_checks_external_replacement_without_reparsing_unchanged_report(
    qapp, monkeypatch, tmp_path,
):
    import threading

    from forma.presentation import review_workflow

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    report = add_history(backend, tmp_path)
    reads, probes = [], []
    original_read, original_signature = review_workflow.load_report, review_workflow._report_signature

    def read(path):
        reads.append(path)
        return original_read(path)

    def signature(path):
        assert threading.current_thread() is not threading.main_thread()
        probes.append(path)
        return original_signature(path)

    monkeypatch.setattr(review_workflow, "load_report", read)
    monkeypatch.setattr(review_workflow, "_report_signature", signature)
    backend.reviewHistory("sample")
    settled(qapp, backend)
    backend.setReviewSearch("rated")
    backend.selectReviewEntry("two")
    context, generation = backend._review_context, backend._review_generation
    backend.setWorkspaceMode("translation")
    backend.setWorkspaceMode("review")
    settled(qapp, backend)
    assert len(reads) == 1 and len(probes) >= 3
    assert backend._review_context is context and backend._review_generation == generation
    assert backend.selectedReviewId == "two" and backend.reviewSearch == "rated"
    backend.setWorkspaceMode("translation")
    data = json.loads(report.read_text())
    data["segments"][1]["translation"] = "<r0>Оновлений переклад потужності</r0>"
    replacement = report.with_suffix(".replacement")
    replacement.write_text(json.dumps(data))
    replacement.replace(report)
    backend.setWorkspaceMode("review")
    settled(qapp, backend)
    assert len(reads) == 2
    assert backend._review_context is not context
    assert any(entry["translation"] == "Оновлений переклад потужності" for entry in backend.reviewEntries)
    assert backend.reviewSearch == ""
    assert backend.requestClose()


def test_late_signature_probe_cannot_reload_a_different_selected_document(qapp, monkeypatch, tmp_path):
    import threading

    from forma.presentation import review_workflow

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    first = add_history(backend, tmp_path, "first")
    add_history(backend, tmp_path, "second")
    backend.reviewHistory("first")
    settled(qapp, backend)
    started, release = threading.Event(), threading.Event()
    original = review_workflow._report_signature
    calls = []

    def signature(path):
        if path == first:
            calls.append(path)
            started.set()
            assert release.wait(5)
            return None  # The old report disappeared while another was selected.
        return original(path)

    monkeypatch.setattr(review_workflow, "_report_signature", signature)
    try:
        backend.activateReview()
        assert started.wait(2)
        backend.activateReview()
        backend.activateReview()
        backend.reviewHistory("second")
        pump(qapp, lambda: backend._review_context is not None and backend.reviewDocumentId == "history:second")
        context, generation = backend._review_context, backend._review_generation
        release.set()
        settled(qapp, backend)
        assert len(calls) == 1
        assert backend._review_context is context and backend._review_generation == generation
        assert backend.reviewDocumentId == "history:second" and not backend.reviewSummary["error"]
    finally:
        release.set()
        settled(qapp, backend)
    assert backend.requestClose()
