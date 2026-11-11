from pathlib import Path
from time import monotonic

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma import __version__
from forma.backend import Backend
from forma.config import Settings, load_preferences
from forma.i18n import CatalogTranslator, current_language


def test_switch_language_persists_without_changing_document_or_review_state(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend._documents = [
        {
            "id": "test",
            "name": "Документ.docx",
            "path": "/example/Документ.docx",
            "status": "ready",
            "statusText": "Готово",
            "error": "",
            "segments": [],
        }
    ]
    backend._selected = "test"
    entry = {
        "id": "part",
        "kind": "segment",
        "location": "Документ",
        "source": "Налаштування",
        "translation": "Settings",
        "messages": [],
        "fingerprint": "unchanged-fingerprint",
        "issueCount": 0,
        "severity": "",
        "accepted": True,
    }
    backend._review_all_entries = [entry]
    backend._review_filter = "all"
    backend._review_search = "Документ"
    backend._review_editing = True
    backend._review_draft_text = "User text — не перекладати"
    backend._filter_review()
    backend.setUiLanguage("de")
    assert current_language() == "de"
    assert "0" in backend.queueSummary and "Перекладаємо" not in backend.queueSummary
    assert backend.documents[0]["statusText"] != "Готово"
    assert backend.documents[0]["name"] == "Документ.docx"
    assert backend.selectedDocument["path"] == "/example/Документ.docx"
    assert backend._documents[0]["statusText"] == "Готово"
    assert backend.reviewEntries[0]["source"] == "Налаштування"
    assert backend.reviewEntries[0]["translation"] == "Settings"
    assert backend.reviewEntries[0]["fingerprint"] == "unchanged-fingerprint"
    assert backend.reviewEntries[0]["accepted"]
    assert backend.reviewDraftText == "User text — не перекладати"
    assert backend.reviewEditing
    assert backend.selectedReviewId == "part"
    assert load_preferences()["ui_language"] == "de"
    assert backend.appVersion == __version__
    backend.setUiLanguage("invalid")
    assert backend.uiLanguage == "de"
    monkeypatch.delenv("FORMA_UI_LANGUAGE")
    restarted = Backend()
    assert restarted.uiLanguage == "de"
    assert current_language() == "de"
    QTest.qWait(10)


def test_live_qml_switch_keeps_choices_and_unsaved_settings(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="example-model"))
    backend = Backend()
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
        window.setProperty("page", 2)
        model = window.findChild(QQuickItem, "modelField")
        model.setProperty("text", "unsaved-provider/model")
        scan_converter = window.findChild(QQuickItem, "scanConverterPathField")
        scan_converter.setProperty("text", "/unsaved OCR/forma-ocr")
        selector = window.findChild(QQuickItem, "interfaceLanguageSelector")
        assert selector is not None
        source = window.findChild(QQuickItem, "sourceLanguage")
        target = window.findChild(QQuickItem, "targetLanguage")
        reasoning = window.findChild(QQuickItem, "reasoningSelect")
        source.setProperty("currentIndex", 3)
        target.setProperty("currentIndex", 2)
        reasoning.setProperty("currentIndex", 2)
        for language in ("en", "pl", "de", "uk"):
            backend.setUiLanguage(language)
            QTest.qWait(30)
            assert model.property("text") == "unsaved-provider/model"
            assert scan_converter.property("text") == "/unsaved OCR/forma-ocr"
            assert source.property("currentIndex") == 3
            assert target.property("currentIndex") == 2
            assert reasoning.property("currentIndex") == 2
            tab = window.findChild(QQuickItem, "translationTab")
            assert tab.property("text")
            assert tab.width() >= tab.implicitWidth() - 1
            assert backend.uiLanguage == language
            assert not window.grabWindow().isNull()
        window.findChild(QQuickItem, "scanSettingsButton").clicked.emit()
        deadline = monotonic() + 1
        while monotonic() < deadline:
            QTest.qWait(20)
            position = scan_converter.mapToScene(QPointF(0, 0))
            if position.y() > 108 and position.y() + scan_converter.height() < window.height() - 80:
                break
        assert window.property("page") == 2
        assert window.property("pdfOptionsOpen")
        assert scan_converter.property("activeFocus")
        position = scan_converter.mapToScene(QPointF(0, 0))
        assert position.y() > 108
        assert position.y() + scan_converter.height() < window.height() - 80
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
