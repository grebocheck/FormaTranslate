"""The shared document workspace retains identity across modes and history."""

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from support import pump
from ui_support import click, item

from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.settings.config import Settings


@pytest.fixture
def workspace_window(qapp, reviewed, monkeypatch):
    backend, result, segment = reviewed

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Workspace navigation must not call the provider")

    monkeypatch.setattr("forma.providers.client.Provider.complete", forbidden)
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    QTest.qWait(20)
    yield backend, result, segment, window, warnings
    backend.cancelReviewEdit()
    pump(qapp, lambda: not backend._threads)
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.removeTranslator(translator)


def test_shared_sidebar_keeps_exact_selection_for_ready_and_reviewed_documents(
    qapp, workspace_window, xlsx_file,
):
    backend, _result, _segment, window, warnings = workspace_window
    backend.addFiles([str(xlsx_file)])
    pump(qapp, lambda: not backend._threads)
    ready_id = next(row["id"] for row in backend.documents if row["path"] == str(xlsx_file))
    history_id = "history:attempt"
    assert {row["id"] for row in backend.workspaceDocuments} == {history_id, ready_id}

    click(window, "translationTab")
    click(window, "workspaceDocumentRow_" + ready_id)
    click(window, "reviewTab")
    assert backend.workspaceDocumentId == backend.reviewDocumentId == ready_id
    assert backend.selectedDocument["id"] == ready_id
    assert not backend.reviewSummary["hasResult"]
    assert not backend.reviewEntries
    assert item(window, "translationTab").property("checked")
    click(window, "translationModeTab")
    assert window.property("page") == 0
    assert backend.workspaceDocumentId == ready_id

    click(window, "workspaceDocumentRow_" + history_id)
    click(window, "reviewTab")
    pump(qapp, lambda: not backend._threads)
    assert backend.workspaceDocumentId == backend.reviewDocumentId == history_id
    assert backend.reviewSummary["hasResult"]
    click(window, "quickTranslationTab")
    assert window.property("page") == 4
    click(window, "translationTab")
    assert window.property("page") == 1
    assert backend.workspaceDocumentId == history_id
    assert {row["id"] for row in backend.documents} == {ready_id}
    assert {row["id"] for row in backend._library.load_queue()} == {ready_id}
    assert not warnings, "\n".join(warnings)


def test_opened_history_documents_remain_available_and_keep_independent_review_views(
    qapp, workspace_window, tmp_path,
):
    backend, result, segment, window, warnings = workspace_window
    second_report = tmp_path / "second.report.json"
    second_report.write_bytes(result.report.read_bytes())
    original = backend._history_entry("attempt")
    backend._library.add_history({
        **original, "id": "second", "name": "Second historical document.docx",
        "report": str(second_report),
    })
    backend._history = backend._library.list_history()
    backend.reviewHistory("second")
    pump(qapp, lambda: not backend._threads)
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    click(window, "translationTab")
    click(window, "reviewTab")
    backend.setReviewFilter("all")
    backend.setReviewSearch("Heat meter commissioning")
    backend.selectReviewEntry(segment.id)
    first_entries = [row["id"] for row in backend.reviewEntries]
    assert first_entries

    click(window, "workspaceDocumentRow_history:second")
    pump(qapp, lambda: not backend._threads)
    assert backend.reviewSearch == ""
    backend.setReviewFilter("issues")
    click(window, "workspaceDocumentRow_history:attempt")
    pump(qapp, lambda: not backend._threads)
    assert backend.reviewFilter == "all"
    assert backend.reviewSearch == "Heat meter commissioning"
    assert backend.selectedReviewId == segment.id
    assert [row["id"] for row in backend.reviewEntries] == first_entries
    assert {row["id"] for row in backend.workspaceDocuments} == {"history:attempt", "history:second"}
    assert backend.documents == backend._library.load_queue() == []
    # An open menu keeps its original document even if selection changes before
    # the action. Closing a historical tab never removes its report or history.
    click(window, "workspaceFileActionsButton")
    backend.selectWorkspaceDocument("history:second")
    pump(qapp, lambda: not backend._threads)
    click(window, "removeDocumentButton")
    assert backend.workspaceDocumentId == "history:second"
    assert [row["id"] for row in backend.workspaceDocuments] == ["history:second"]
    assert {row["id"] for row in backend.historyEntries} == {"attempt", "second"}
    assert result.report.is_file() and second_report.is_file()
    assert not warnings, "\n".join(warnings)


def test_review_outline_is_explicit_and_file_actions_are_available_in_all_locales(
    qapp, workspace_window, monkeypatch,
):
    backend, result, segment, window, warnings = workspace_window
    opened = []
    monkeypatch.setattr(backend, "openPath", opened.append)
    click(window, "translationTab")
    click(window, "reviewTab")
    pump(qapp, lambda: not backend._threads)
    backend.selectReviewEntry(segment.id)
    source = item(window, "reviewSourceText")
    translation = item(window, "reviewTranslationText")
    assert not item(window, "reviewEntryList").isVisible()
    assert source.isVisible() and translation.isVisible()
    assert source.property("readOnly") and source.property("selectByMouse")
    assert translation.property("readOnly") and translation.property("selectByMouse")
    for language, theme in (("uk", "light"), ("en", "dark"), ("pl", "light"),
                            ("de", "dark"), ("fr", "light"), ("es", "dark")):
        backend.setUiLanguage(language)
        backend.setUiTheme(theme)
        QTest.qWait(20)
        assert backend.workspaceDocumentId == "history:attempt"
        assert backend.selectedReviewId == segment.id
        assert source.height() >= 140 and translation.height() >= 140
        click(window, "reviewOutlineButton")
        assert item(window, "reviewEntryList").isVisible()
        # Escape dismisses the navigator without changing the selected fragment.
        QTest.keyClick(window, Qt.Key_Escape)
        QTest.qWait(20)
        assert not item(window, "reviewEntryList").isVisible()
        assert backend.selectedReviewId == segment.id
        click(window, "workspaceFileActionsButton")
        assert item(window, "reviewOriginalButton").isVisible()
        assert item(window, "reviewOutputButton").isEnabled()
        assert item(window, "reviewReportButton").isEnabled()
        QTest.keyClick(window, Qt.Key_Escape)
        QTest.qWait(20)
    click(window, "workspaceFileActionsButton")
    click(window, "reviewOriginalButton")
    assert opened == [str(result.source)]
    click(window, "workspaceFileActionsButton")
    click(window, "reviewReportButton")
    assert opened == [str(result.source), str(result.report.with_suffix(".html"))]
    assert not warnings, "\n".join(warnings)


def test_translation_choices_restore_with_the_document_session_after_restart(
    qapp, monkeypatch, docx_file,
):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="offline"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Restoring a workspace must not start translation")

    monkeypatch.setattr("forma.providers.client.Provider.complete", forbidden)
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    windows = []
    warnings = []

    def load(backend):
        engine = QQmlApplicationEngine()
        backend.languageChanged.connect(engine.retranslate)
        engine.rootContext().setContextProperty("backend", backend)
        engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
        engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.setWidth(960)
        window.setHeight(680)
        windows.append((backend, engine, window))
        QTest.qWait(20)
        return window

    def select(window, name, index):
        control = click(window, name)
        QTest.keyClick(window, Qt.Key_Home)
        for _ in range(index):
            QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Return)
        QTest.qWait(20)
        assert control.property("currentIndex") == index

    try:
        original = Backend()
        original.addFiles([str(docx_file)])
        pump(qapp, lambda: not original._threads)
        window = load(original)
        select(window, "sourceLanguage", 1)
        select(window, "targetLanguage", 1)
        click(window, "translationOptionsButton")
        select(window, "documentDomainSelector", 1)
        click(window, "reviewEnabled")
        click(window, "autoGlossaryEnabled")
        expected = {"sourceLanguage": "en", "targetLanguage": "uk",
                    "domain": "Технічна документація", "review": False, "autoGlossary": False}
        assert original.workspaceOptions == expected
        click(window, "closeTranslationOptionsButton")
        click(window, "reviewTab")
        selected = original.workspaceDocumentId
        window.close()
        QTest.qWait(20)

        restarted = Backend()
        pump(qapp, lambda: not restarted._threads)
        assert restarted.workspaceOptions == expected
        window = load(restarted)
        assert window.property("page") == 1
        assert restarted.workspaceDocumentId == selected
        assert restarted.workspaceMode == "review"
        assert restarted.workspaceOptions == expected
        assert not restarted.busy
        click(window, "translationModeTab")
        assert item(window, "sourceLanguage").property("currentIndex") == 1
        assert item(window, "targetLanguage").property("currentIndex") == 1
        click(window, "translationOptionsButton")
        assert item(window, "documentDomainSelector").property("currentIndex") == 1
        assert not item(window, "reviewEnabled").property("checked")
        assert not item(window, "autoGlossaryEnabled").property("checked")
        assert not warnings, "\n".join(warnings)
    finally:
        for backend, engine, window in windows:
            pump(qapp, lambda current=backend: not current._threads)
            window.close()
            engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
