from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path

import pytest
from docx import Document
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from support import settle

import forma.presentation.backend as backend_module
from forma.core.models import FormaError, Options
from forma.documents.ooxml import OfficeDocument
from forma.localization.catalog import CatalogTranslator
from forma.presentation import documents_workflow, memory_workflow, settings_workflow
from forma.presentation.backend import Backend
from forma.settings.config import ENV_FIELDS, Settings, load_preferences


@pytest.fixture
def memory_backend(qapp, monkeypatch, tmp_path):
    for name in (*ENV_FIELDS.values(), "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings.load())
    backend = Backend()
    qapp.processEvents()
    yield backend
    settle(qapp, backend)


def reference_files(tmp_path):
    source = tmp_path / "source.docx"
    office = Document()
    office.add_paragraph("Потужність насоса 16 кВт")
    office.add_paragraph("Рекомендовано провести перевірку.")
    office.save(source)
    document = OfficeDocument(source)
    translations = {
        document.segments[0].id: "<r0>Pump power 16 kW</r0>",
        document.segments[1].id: "<r0>An inspection is recommended.</r0>",
    }
    output = tmp_path / "source.en.docx"
    document.write(translations, output)
    report = output.with_name(output.name + ".forma.json")
    report.write_text(
        json.dumps(
            {
                "source": str(source), "output": str(output), "engine": "forma-ooxml",
                "options": asdict(Options("uk", "en", auto_glossary=False)),
                "effective_glossary": {}, "model": "fake-model", "issues": [],
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "segments": [
                    {**asdict(segment), "translation": translations[segment.id]}
                    for segment in document.segments
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source, output, report, document.segments[0].id


def prepare_review(qapp, backend, files):
    source, output, report, segment_id = files
    document = {
        "id": "reference", "path": str(source), "name": source.name,
        "format": "DOCX", "status": "done", "statusText": "Готово",
        "output": str(output), "report": str(report), "segments": [],
        "sourceLanguage": "uk", "targetLanguage": "en",
    }
    backend._documents = [document]
    backend._selected = document["id"]
    backend.activateReview()
    backend.setReviewFilter("all")
    settle(qapp, backend)
    backend.selectReviewEntry(segment_id)
    assert backend.canRememberReviewWording


def test_memory_project_selection_and_shared_glossary_survive_restart(memory_backend, tmp_path):
    backend = memory_backend
    standalone = tmp_path / "standalone.csv"
    standalone.write_text("source,target\nтермін,term\n", encoding="utf-8")
    backend.loadGlossary(str(standalone))
    assert not backend.createMemoryProject("Energy", "auto", "en")
    assert "мову оригіналу" in backend.memoryMessage
    assert backend.createMemoryProject("Energy — {name}", "uk", "en")
    selected = backend.memoryProject
    assert selected["name"] == "Energy — {name}"
    assert backend.glossaryCount == 0
    shared = tmp_path / "project.csv"
    shared.write_text("source,target\nнасос,pump\n", encoding="utf-8")
    backend.loadGlossary(str(shared))
    assert backend.memoryProject["glossaryRevision"] > selected["glossaryRevision"]
    assert backend._memory.get_project(selected["id"]).glossary == {"насос": "pump"}
    assert load_preferences()["memory_project_id"] == selected["id"]
    assert "насос" not in json.dumps(load_preferences(), ensure_ascii=False)
    restarted = Backend()
    assert restarted.memoryProject["id"] == selected["id"]
    assert restarted._glossary == {"насос": "pump"}
    assert backend.selectMemoryProject("")
    assert backend._glossary == {"термін": "term"}


def test_project_delete_runs_off_thread_and_resets_only_selected_memory(qapp, memory_backend, tmp_path, monkeypatch):
    backend = memory_backend
    standalone = tmp_path / "standalone.csv"
    standalone.write_text("source,target\nтермін,term\n", encoding="utf-8")
    backend.loadGlossary(str(standalone))
    backend.createMemoryProject("Delete this project", "uk", "en")
    selected = backend.memoryProject["id"]
    other = backend._memory.create_project("Keep this project", "uk", "en")
    backend.prepareMemoryReference()
    files = reference_files(tmp_path)
    before = [path.read_bytes() for path in files[:3]]
    for kind, path in zip(("source", "output", "report"), files, strict=False):
        backend.setMemoryReferencePath(kind, str(path))
    backend._library.add_history({"id": "retained", "name": "source.docx", "format": "DOCX", "status": "done", "output": str(files[1])})
    history = backend._library.list_history()
    actual_delete = backend._memory.delete_project
    started, release = threading.Event(), threading.Event()

    def delete(project_id):
        assert threading.current_thread() is not threading.main_thread()
        started.set()
        assert release.wait(3)
        actual_delete(project_id)

    monkeypatch.setattr(backend._memory, "delete_project", delete)
    backend._review_editing = True
    backend.deleteMemoryProject(selected)
    assert not started.is_set() and not backend.memoryWorking
    backend._review_editing = False
    try:
        backend.deleteMemoryProject(selected)
        assert backend.memoryWorking and backend.busy
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        QTest.qWait(20)
        assert started.is_set() and heartbeat
        assert not backend.selectMemoryProject(other.id)
        backend.deleteMemoryProject(other.id)
    finally:
        release.set()
        settle(qapp, backend)
    assert not backend.memoryProject and not backend._settings.memory_project_id
    assert load_preferences()["memory_project_id"] == ""
    assert backend._glossary == {"термін": "term"}
    assert not any(backend.memoryReference.values())
    assert not backend.memoryConflict
    assert backend._memory.get_project(other.id).id == other.id
    assert backend._library.list_history() == history
    assert [path.read_bytes() for path in files[:3]] == before
    restarted = Backend()
    try:
        assert not restarted.memoryProject and not restarted._settings.memory_project_id
        assert [value["id"] for value in restarted.memoryProjects] == ["", other.id]
    finally:
        settle(qapp, restarted)


def test_delete_failure_preserves_project_and_stale_preference_is_safe_after_restart(qapp, memory_backend, monkeypatch):
    backend = memory_backend
    backend.createMemoryProject("Delete after retry", "uk", "en")
    selected = backend.memoryProject["id"]
    actual_delete = backend._memory.delete_project

    def fail_delete(project_id):
        raise FormaError("Simulated database failure")

    monkeypatch.setattr(backend._memory, "delete_project", fail_delete)
    backend.deleteMemoryProject(selected)
    settle(qapp, backend)
    assert backend.memoryProject["id"] == selected
    assert backend._memory.get_project(selected).id == selected
    assert load_preferences()["memory_project_id"] == selected
    assert backend.memoryMessageError
    monkeypatch.setattr(backend._memory, "delete_project", actual_delete)

    def fail_preferences(values):
        raise OSError("Simulated preference failure")

    monkeypatch.setattr(memory_workflow, "update_preferences", fail_preferences)
    backend.deleteMemoryProject(selected)
    settle(qapp, backend)
    assert not backend.memoryProject and not backend._settings.memory_project_id
    assert backend.memoryMessageError and "Проєкт видалено" in backend.memoryMessage
    assert load_preferences()["memory_project_id"] == selected
    restarted = Backend()
    try:
        assert not restarted.memoryProject and not restarted._settings.memory_project_id
        assert restarted.memoryMessageError and "недоступний" in restarted.memoryMessage
        assert "недоступний" in restarted.message
    finally:
        settle(qapp, restarted)
    assert not backend.settings["keyPresent"]


def test_memory_start_captures_selected_project_without_mutating_auto_glossary(
    qapp, memory_backend, monkeypatch, docx_file
):
    backend = memory_backend
    backend._settings = backend._settings.with_overrides(
        model="fake-model", base_url="http://localhost:9999/v1"
    )
    backend.createMemoryProject("Energy", "uk", "en")
    backend.addFiles([str(docx_file)])
    settle(qapp, backend)
    captured = []
    monkeypatch.setattr(documents_workflow, "run_documents", lambda queue, folder, options, *_args, **_kw: captured.append(options))
    backend.start("uk", "en", "Загальна тематика", True, True)
    settle(qapp, backend)
    assert len(captured) == 1
    assert captured[0].memory_project_id == backend.memoryProject["id"]
    assert captured[0].auto_glossary
    backend.start("auto", "en", "Загальна тематика", True, True)
    assert len(captured) == 1
    assert "Мови перекладу" in backend.message


def test_reference_import_is_offline_explicit_and_does_not_modify_files(
    qapp, memory_backend, tmp_path, monkeypatch
):
    backend = memory_backend
    files = reference_files(tmp_path)
    backend.createMemoryProject("Energy", "uk", "en")
    before = [path.read_bytes() for path in files[:3]]
    for kind, path in zip(("source", "output", "report"), files, strict=False):
        backend.setMemoryReferencePath(kind, str(path))
    monkeypatch.setattr(settings_workflow, "Provider", lambda *_: pytest.fail("Reference import must not use API"))
    backend.importMemoryReference(False)
    assert backend.memoryWorking and backend.busy
    settle(qapp, backend)
    assert not backend.memoryMessageError
    assert "довідкових: 2" in backend.memoryMessage
    assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision == 0
    backend.importMemoryReference(True)
    settle(qapp, backend)
    assert not backend.memoryMessageError
    assert "схвалено: 2" in backend.memoryMessage
    assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision > 0
    assert [path.read_bytes() for path in files[:3]] == before


def test_manual_preference_and_conflict_require_separate_explicit_replacement(
    qapp, memory_backend, tmp_path
):
    backend = memory_backend
    files = reference_files(tmp_path)
    backend.createMemoryProject("Energy", "uk", "en")
    prepare_review(qapp, backend, files)
    before = [path.read_bytes() for path in files[:3]]
    backend.rememberReviewWording()
    settle(qapp, backend)
    first_revision = backend._memory.snapshot(backend.memoryProject["id"]).approved_revision
    assert first_revision > 0
    assert not backend._review_accepted
    backend.beginReviewEdit()
    backend.updateReviewDraft("Rated pump power 16 kW")
    assert backend.applyReviewEdit()
    backend.rememberReviewWording()
    settle(qapp, backend)
    assert backend.memoryConflict["active"]
    assert backend.memoryConflict["previous"] == "Pump power 16 kW"
    assert backend.memoryConflict["replacement"] == "Rated pump power 16 kW"
    assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision == first_revision
    backend.replaceReviewWording()
    settle(qapp, backend)
    assert not backend.memoryConflict
    assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision > first_revision
    assert [path.read_bytes() for path in files[:3]] == before
    assert backend._review_edits and not backend._review_accepted


def test_manual_preference_replacement_does_not_refresh_a_stale_approval_token(
    qapp, memory_backend, tmp_path
):
    backend = memory_backend
    files = reference_files(tmp_path)
    backend.createMemoryProject("Energy", "uk", "en")
    prepare_review(qapp, backend, files)
    backend.rememberReviewWording()
    settle(qapp, backend)
    backend.beginReviewEdit()
    backend.updateReviewDraft("Rated pump power 16 kW")
    assert backend.applyReviewEdit()
    backend.rememberReviewWording()
    settle(qapp, backend)
    token = backend.memoryConflict["expected_revision"]
    source, output, report, _ = files
    other_segment = OfficeDocument(source).segments[1].id
    memory_workflow.prefer_review_segment(
        backend._memory, backend.memoryProject["id"], source, output, report, other_segment
    )
    current = backend._memory.snapshot(backend.memoryProject["id"]).approved_revision
    assert current > token
    backend.replaceReviewWording()
    settle(qapp, backend)
    assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision == current
    assert not backend.memoryConflict
    assert "Пам’ять змінилася" in backend.reviewActionError


def test_missing_selected_memory_is_visible_and_can_be_disabled_without_database(
    qapp, monkeypatch
):
    project_id = "a" * 32
    monkeypatch.setattr(
        Backend, "_read_settings", lambda _: Settings(memory_project_id=project_id)
    )

    def unavailable():
        raise FormaError("Could not access local translation memory. Check its location and database integrity.")

    monkeypatch.setattr(backend_module, "TranslationMemory", unavailable)
    backend = Backend()
    assert backend._settings.memory_project_id == project_id
    assert backend.memoryMessageError and backend.messageKind == "warning"
    assert backend.message
    assert backend.selectMemoryProject("")
    assert not backend._settings.memory_project_id
    assert not backend.message
    assert load_preferences()["memory_project_id"] == ""


def test_preferred_dialog_async_close_preserves_conflict_and_replacement(
    qapp, memory_backend, tmp_path
):
    backend = memory_backend
    files = reference_files(tmp_path)
    backend.createMemoryProject("Energy", "uk", "en")
    prepare_review(qapp, backend, files)
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setProperty("page", 1)
    settle(qapp, backend)
    backend.selectReviewEntry(files[3])
    dialog = window.findChild(QObject, "rememberWordingDialog")
    try:
        remember = window.findChild(QObject, "rememberReviewWordingButton")
        confirm = window.findChild(QObject, "confirmRememberWordingButton")
        remember.clicked.emit()
        assert dialog.property("visible")
        confirm.clicked.emit()
        settle(qapp, backend)
        assert not dialog.property("visible")
        first_revision = backend._memory.snapshot(backend.memoryProject["id"]).approved_revision
        assert first_revision > 0
        backend.beginReviewEdit()
        backend.updateReviewDraft("Rated pump power 16 kW")
        assert backend.applyReviewEdit()
        remember.clicked.emit()
        confirm.clicked.emit()
        settle(qapp, backend)
        QTest.qWait(30)
        assert dialog.property("visible") and backend.memoryConflict["active"], (
            backend.memoryConflict, backend.reviewActionError
        )
        previous = window.findChild(QQuickItem, "previousMemoryWordingText")
        replacement = window.findChild(QQuickItem, "replacementMemoryWordingText")
        assert previous.property("text") == "Pump power 16 kW"
        assert replacement.property("text") == "Rated pump power 16 kW"
        original_theme = backend.uiTheme
        for theme in ("light", "dark"):
            backend.setUiTheme(theme)
            QTest.qWait(100)
            assert previous.property("color") == window.property("ink")
            assert replacement.property("color") == window.property("ink")
        backend.setUiTheme(original_theme)
        tokens = (
            backend.memoryConflict["expected_revision"],
            backend.memoryConflict["expected_glossary_revision"],
        )
        for language in ("en", "pl", "de", "uk"):
            backend.setUiLanguage(language)
            QTest.qWait(20)
            assert previous.property("text") == "Pump power 16 kW"
            assert replacement.property("text") == "Rated pump power 16 kW"
            assert tokens == (
                backend.memoryConflict["expected_revision"],
                backend.memoryConflict["expected_glossary_revision"],
            )
        window.findChild(QObject, "cancelRememberWordingButton").clicked.emit()
        QTest.qWait(30)
        assert not backend.memoryConflict and not dialog.property("visible")
        assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision == first_revision
        remember.clicked.emit()
        confirm.clicked.emit()
        settle(qapp, backend)
        assert dialog.property("visible") and backend.memoryConflict["active"]
        confirm.clicked.emit()
        settle(qapp, backend)
        QTest.qWait(30)
        assert not backend.memoryConflict and not dialog.property("visible")
        assert backend._memory.snapshot(backend.memoryProject["id"]).approved_revision > first_revision
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)


def test_memory_dialog_drafts_project_and_delete_confirmation_survive_language_switches(
    qapp, memory_backend
):
    backend = memory_backend
    backend.createMemoryProject("Energy — {name}", "uk", "en")
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)

    def click(name):
        button = window.findChild(QQuickItem, name)
        assert button is not None and button.isVisible() and button.isEnabled(), name
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint())
        QTest.qWait(10)

    try:
        window.findChild(QQuickItem, "manageMemoryButton").clicked.emit()
        QTest.qWait(30)
        name = window.findChild(QQuickItem, "memoryProjectNameField")
        name.setProperty("text", "Unsaved — {draft}")
        backend.setMemoryReferencePath("source", "/unsaved/Документ.docx")
        approved = window.findChild(QQuickItem, "memoryReferencePreferred")
        approved.setProperty("checked", True)
        project_id = backend.memoryProject["id"]
        click("deleteMemoryProjectButton")
        confirmation = window.findChild(QObject, "deleteMemoryProjectDialog")
        assert confirmation.property("opened")
        for language in ("en", "pl", "de", "fr", "es", "uk"):
            backend.setUiLanguage(language)
            QTest.qWait(30)
            assert name.property("text") == "Unsaved — {draft}"
            assert approved.property("checked")
            assert backend.memoryReference["source"] == "/unsaved/Документ.docx"
            assert backend.memoryProject["name"] == "Energy — {name}"
            assert confirmation.property("projectId") == project_id
            assert confirmation.property("projectName") == "Energy — {name}"
            assert window.findChild(QQuickItem, "sourceLanguage").property("currentIndex") == 2
            assert window.findChild(QQuickItem, "targetLanguage").property("currentIndex") == 0
            assert not window.grabWindow().isNull()
        click("cancelDeleteMemoryProjectButton")
        assert backend._memory.get_project(project_id).name == "Energy — {name}"
        assert not backend.memoryWorking
        backend.setMemoryReferencePath("output", "/another/reference.en.docx")
        assert not approved.property("checked")
        other = backend._memory.create_project("Another selected project", "uk", "en")
        backend.prepareMemoryReference()
        click("deleteMemoryProjectButton")
        assert backend.selectMemoryProject(other.id)
        assert confirmation.property("projectId") == project_id
        click("confirmDeleteMemoryProjectButton")
        settle(qapp, backend)
        assert not confirmation.property("opened")
        assert backend.memoryProject["id"] == other.id
        assert project_id not in {project["id"] for project in backend.memoryProjects}
        window.findChild(QObject, "closeMemoryDialogButton").clicked.emit()
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
