"""Glossary help is translated while term drafts and editing state stay intact."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

import forma.presentation.glossary_controller as glossary_module
from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def visual_item(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


def test_minimum_window_and_language_switch_keep_glossary_draft_focus_and_values(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Opening/editing/localizing a glossary must not call the API")

    monkeypatch.setattr(glossary_module, "Provider", forbidden)
    backend = Backend()
    assert backend.createMemoryProject("Synthetic — {project}", "uk", "en")
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
    try:
        window.findChild(QObject, "editGlossaryButton").clicked.emit()
        QTest.qWait(30)
        editor = backend.glossaryEditor
        editor.addTerm()
        editor.setTerm(0, "source", "Налаштування")
        editor.setTerm(0, "target", "Назва громади — {name} 12,50")
        editor._notice("Запропоновано {count} нових термінів. Перевірте їх і застосуйте глосарій.", count=12)
        window.findChild(QObject, "glossaryCsvOptionsButton").clicked.emit()
        view = window.findChild(QQuickItem, "glossaryTermList")
        view.forceLayout()
        QTest.qWait(20)
        source = visual_item(view.property("contentItem"), "glossarySourceTerm")
        target = visual_item(view.property("contentItem"), "glossaryTargetTerm")
        assert source is not None and target is not None
        source.forceActiveFocus()
        source.setProperty("cursorPosition", 4)
        expected = [dict(row) for row in editor.rows.rows]
        dialog = window.findChild(QObject, "glossaryDialog")
        for language in ("en", "pl", "de", "fr", "es", "uk"):
            backend.setUiLanguage(language)
            QTest.qWait(20)
            assert dialog.property("visible") and dialog.property("csvOpen")
            assert source is visual_item(view.property("contentItem"), "glossarySourceTerm")
            assert source.property("activeFocus") and source.property("cursorPosition") == 4
            assert source.property("text") == "Налаштування"
            assert target.property("text") == "Назва громади — {name} 12,50"
            assert editor.rows.rows == expected and editor.dirty
            button = window.findChild(QQuickItem, "applyGlossaryButton")
            position = button.mapToScene(QPointF(0, 0))
            assert 0 <= position.x() and position.x() + button.width() <= window.width()
            assert 0 <= position.y() and position.y() + button.height() <= window.height()
        window.findChild(QObject, "closeGlossaryButton").clicked.emit()
        window.findChild(QObject, "editGlossaryButton").clicked.emit()
        QTest.qWait(20)
        assert editor.rows.rows == expected and editor.dirty
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
