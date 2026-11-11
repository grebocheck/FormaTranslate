from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.settings.config import Settings, load_preferences


@pytest.fixture
def instructions_window(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="fixture-model"))
    backend = Backend()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    QTest.qWait(20)
    yield backend, window, warnings
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.removeTranslator(translator)


def test_rules_draft_survives_live_languages_and_saves_without_affecting_other_inputs(
    instructions_window,
):
    backend, window, warnings = instructions_window
    window.findChild(QQuickItem, "documentInstructionsButton").clicked.emit()
    dialog = window.findChild(QObject, "instructionsDialog")
    editor = window.findChild(QQuickItem, "translationInstructionsEditor")
    save = window.findChild(QQuickItem, "saveInstructionsButton")
    rules = "ДОДАТКИ → APPENDICES\nKeep {reference} and HM-200.\n<r0> is literal text."
    editor.setProperty("text", rules)
    model = window.findChild(QQuickItem, "modelField")
    model.setProperty("text", "unsaved-model")
    for language in ("en", "pl", "de", "uk"):
        backend.setUiLanguage(language)
        QTest.qWait(30)
        assert editor.property("text") == rules
        assert model.property("text") == "unsaved-model"
        assert dialog.property("width") <= window.width() - 32
        assert dialog.property("height") <= window.height() - 32
        assert save.width() >= save.implicitWidth() - 1
        assert not window.grabWindow().isNull()
    save.clicked.emit()
    assert not dialog.property("visible")
    assert backend.translationInstructions == rules
    assert load_preferences()["translation_instructions"] == rules
    window.setProperty("page", 4)
    quick = window.findChild(QQuickItem, "quickInstructionsButton")
    assert "•" in quick.property("text")
    quick.clicked.emit()
    assert editor.property("text") == rules
    editor.setProperty("text", "discarded changes")
    window.findChild(QQuickItem, "cancelInstructionsButton").clicked.emit()
    assert backend.translationInstructions == rules
    quick.clicked.emit()
    editor.setProperty("text", "")
    save.clicked.emit()
    assert backend.translationInstructions == ""
    assert "•" not in quick.property("text")
    assert not warnings, "\n".join(warnings)


def test_length_limit_and_failed_save_keep_draft_and_saved_rules(instructions_window, monkeypatch):
    backend, window, warnings = instructions_window
    assert backend.saveTranslationInstructions("Saved rules")
    window.findChild(QQuickItem, "documentInstructionsButton").clicked.emit()
    editor = window.findChild(QQuickItem, "translationInstructionsEditor")
    save = window.findChild(QQuickItem, "saveInstructionsButton")
    editor.setProperty("text", "x" * 8001)
    assert not save.property("enabled")
    editor.setProperty("text", "New rules")
    assert save.property("enabled")

    def fail(_):
        raise OSError("private filesystem path")

    monkeypatch.setattr("forma.presentation.settings_workflow.save_preferences", fail)
    save.clicked.emit()
    assert window.findChild(QObject, "instructionsDialog").property("visible")
    assert editor.property("text") == "New rules"
    assert backend.translationInstructions == "Saved rules"
    assert "private" not in backend.instructionsError
    assert backend.instructionsError
    assert not warnings, "\n".join(warnings)
