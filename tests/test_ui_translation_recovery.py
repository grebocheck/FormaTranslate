"""A failed Office translation can be corrected without discarding paid work."""

import json
from pathlib import Path

import pytest
from docx import Document
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from support import pump

from forma.core.models import FormaError, Options, TranslationFailure, plain_text
from forma.documents.ooxml import OfficeDocument
from forma.presentation.backend import Backend
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


class RecoveryProvider:
    input_tokens = output_tokens = 0

    def __init__(self, *, forbid_requests=False):
        self.calls = []
        self.forbid_requests = forbid_requests

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append(payload)
        if self.forbid_requests:
            raise FormaError("Recovery unexpectedly requested a new translation")
        return {"segments": [
            {"id": item["id"], "text": item["text"].replace("Енергія", "Energy")
             .replace("Тиск 20", "Pressure 200").replace("кВт", "kW").replace("бар", "bar")}
            for item in payload["segments"]
        ]}


@pytest.fixture
def failed_translation(qapp, tmp_path, monkeypatch):
    source = tmp_path / "failed.docx"
    document = Document()
    document.add_paragraph("Енергія 10 кВт")
    document.add_paragraph("Тиск 20 бар")
    document.save(source)
    settings = Settings(api_key="offline-test-key", base_url="http://127.0.0.1:1/v1", model="fake",
                        max_parallel_requests=1, parallel_documents=1)
    options = Options("uk", "en", review=False, auto_glossary=False)
    provider = RecoveryProvider()
    folder = tmp_path / "output"
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=provider).translate(source, folder, options)
    assert [len(call["segments"]) for call in provider.calls] == [2, 1, 1]
    assert not list(folder.glob("*.docx"))
    monkeypatch.setattr(Backend, "_read_settings", lambda _: settings)
    resumed = RecoveryProvider(forbid_requests=True)
    launches = []

    def resume_engine(current_settings, control):
        launches.append(current_settings)
        return TranslationEngine(current_settings, control, provider=resumed)

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", resume_engine)
    backend = Backend()
    qapp.processEvents()
    backend._folder = str(folder)
    backend._documents = [{
        "id": "failed", "path": str(source), "name": source.name, "format": "DOCX",
        "status": "error", "statusText": "Потрібна увага", "output": "", "report": "",
        "sourceLanguage": "uk", "targetLanguage": "en", "model": "fake", "reviewModel": "",
        "diagnosticReport": str(caught.value.report),
    }]
    backend._selected = "failed"
    backend._persist_queue()
    backend.changed.emit()
    yield backend, source, resumed, launches
    backend.cancelReviewEdit()
    pump(qapp, lambda: not backend._threads)


def test_real_failure_controls_edit_validate_and_resume_without_api(qapp, failed_translation):
    backend, _, resumed, launches = failed_translation
    # A malformed last response is editable against the original formatting,
    # rather than using its damaged markup as the template for every correction.
    report = Path(backend.documents[0]["diagnosticReport"]).with_suffix(".json")
    diagnostic = json.loads(report.read_text())
    diagnostic["segments"][0]["candidate"] = plain_text(diagnostic["segments"][0]["candidate"])
    report.write_text(json.dumps(diagnostic))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)

    def click(name):
        button = window.findChild(QQuickItem, name)
        assert button is not None and button.isVisible() and button.isEnabled(), name
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         point.toPoint())
        QTest.qWait(10)

    try:
        click("recoverTranslationButton")
        pump(qapp, lambda: not backend._threads)
        assert window.property("page") == 1
        assert backend.reviewDocument["recovery"]
        assert backend.selectedReviewEntry["canEdit"]
        assert not backend.selectedReviewEntry["canAccept"]
        assert any(issue.code == "formatting" for issue in backend._review_context["result"].issues)
        export = window.findChild(QQuickItem, "reviewExportButton")
        assert export.isVisible() and not export.isEnabled()
        click("reviewEditButton")
        target = window.findChild(QQuickItem, "reviewTranslationText")
        target.setProperty("text", "Pressure 201 bar")
        QTest.qWait(10)
        click("reviewApplyButton")
        assert backend.reviewEditing and backend.reviewEditError
        assert not backend.reviewHasEdits and not launches
        target.setProperty("text", "Pressure 20 bar")
        QTest.qWait(10)
        click("reviewApplyButton")
        assert not backend.reviewEditing and backend.reviewHasEdits
        assert export.isEnabled()
        click("reviewExportButton")
        pump(qapp, lambda: not backend._threads)
        assert not backend.reviewActionError, backend.reviewActionError
        document = backend.documents[0]
        assert document["status"] == "done", document.get("error")
        assert launches and not resumed.calls
        assert {segment.plain for segment in OfficeDocument(document["output"]).segments} == {
            "Energy 10 kW", "Pressure 20 bar",
        }
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancelReviewEdit()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_recovery_draft_restores_but_changed_source_cannot_resume(qapp, failed_translation, monkeypatch):
    backend, source, resumed, launches = failed_translation
    backend.selectReviewDocument("failed")
    pump(qapp, lambda: not backend._threads)
    segment_id = backend.selectedReviewId
    backend.beginReviewEdit()
    backend.updateReviewDraft("Pressure 20 bar")
    assert backend.applyReviewEdit(), backend.reviewEditError
    restored = Backend()
    interrupted = None
    try:
        qapp.processEvents()
        assert restored.documents[0]["status"] == "error"
        assert not restored.reviewSummary["hasResult"] and not launches
        restored.selectReviewDocument("failed")
        pump(qapp, lambda: not restored._threads)
        assert restored.reviewDocument["recovery"] and restored.reviewHasEdits
        restored.setReviewFilter("all")
        restored.selectReviewEntry(segment_id)
        assert restored.selectedReviewEntry["translation"] == "Pressure 20 bar"
        launch = restored._launch

        def interrupt_before_worker(function, *, handler=None, done=None):
            if done == restored._finished:
                return
            return launch(function, handler=handler, done=done)

        monkeypatch.setattr(restored, "_launch", interrupt_before_worker)
        restored.exportReviewEdits()
        pump(qapp, lambda: not restored._threads)
        assert restored.busy and not launches
        queued = restored._library.load_queue()[0]
        assert queued["diagnosticReport"] == backend.documents[0]["diagnosticReport"]
        # Simulate interruption after approving corrections and persisting the
        # queued run, before the translation worker has begun.
        interrupted = Backend()
        pump(qapp, lambda: not interrupted._threads)
        interrupted.selectReviewDocument("failed")
        pump(qapp, lambda: not interrupted._threads)
        assert interrupted.reviewDocument["recovery"] and interrupted.reviewHasEdits
        document = Document(source)
        document.add_paragraph("Додано після перевірки")
        document.save(source)
        interrupted.exportReviewEdits()
        pump(qapp, lambda: not interrupted._threads)
        assert interrupted.reviewActionError
        assert interrupted.reviewHasEdits
        assert not launches and not resumed.calls
        assert not interrupted.documents[0].get("output")
    finally:
        if interrupted is not None:
            interrupted.cancelReviewEdit()
            pump(qapp, lambda: not interrupted._threads)
        restored._finished()
        restored.cancelReviewEdit()
        pump(qapp, lambda: not restored._threads)
