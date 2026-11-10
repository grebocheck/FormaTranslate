from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.backend import Backend
from forma.config import Settings
from forma.i18n import CatalogTranslator
from forma.quick_controller import preview_url


@pytest.fixture
def quick_window(qapp, monkeypatch):
    monkeypatch.setattr(
        Backend, "_read_settings", lambda _: Settings(base_url="http://localhost/v1", model="test")
    )
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
    window.setProperty("page", 4)
    window.requestActivate()
    QTest.qWait(30)
    yield window, backend, warnings
    backend.quickTranslator._busy = False
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.removeTranslator(translator)


def item(window, name):
    found = window.findChild(QQuickItem, name)
    assert found is not None, name
    return found


def click(window, name):
    button = item(window, name)
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
    QTest.mouseClick(
        window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
    )
    QTest.qWait(10)


def test_text_paste_edits_and_language_switching_keep_draft_and_selection(qapp, quick_window):
    window, backend, warnings = quick_window
    quick = backend.quickTranslator
    source = item(window, "quickSourceText")
    source.forceActiveFocus()
    qapp.clipboard().setText("Pump: 16 bar\nKeep this paragraph.")
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert quick.sourceText == "Pump: 16 bar\nKeep this paragraph."
    assert not quick.hasImage
    quick.setSourceLanguage("en")
    quick.setTargetLanguage("uk")
    source.setProperty("cursorPosition", 5)
    quick.changed.emit()
    assert source.property("cursorPosition") == 5
    quick._translated_text = "Насос: 16 бар\nЗбережіть цей абзац."
    quick.changed.emit()
    click(window, "quickCopyText")
    assert qapp.clipboard().text() == quick.translatedText
    source.forceActiveFocus()
    source.setProperty("cursorPosition", 5)
    for language in ("en", "pl", "de", "uk"):
        backend.setUiLanguage(language)
        QTest.qWait(20)
        assert source.property("text") == quick.sourceText
        assert source.property("cursorPosition") == 5
        assert item(window, "quickSourceLanguage").property("currentIndex") == 1
        assert item(window, "quickTargetLanguage").property("currentIndex") == 1
        for name in (
            "translationTab", "quickTranslationTab", "reviewTab", "historyTab", "settingsButton",
            "quickTextMode", "quickImageMode", "quickTranslateButton", "quickCopyText",
        ):
            button = item(window, name)
            assert button.width() >= button.implicitWidth() - 1, (language, name)
            left = button.mapToScene(QPointF()).x()
            assert left >= 0 and left + button.width() <= window.width(), (language, name)
    click(window, "historyTab")
    assert window.property("page") == 3
    click(window, "quickTranslationTab")
    assert window.property("page") == 4
    assert source.property("text") == "Pump: 16 bar\nKeep this paragraph."
    quick._busy = True
    quick.changed.emit()
    assert source.property("readOnly")
    assert not item(window, "quickImageMode").property("enabled")
    assert not item(window, "quickTargetLanguage").property("enabled")
    assert item(window, "quickTranslateButton").property("enabled")
    assert not warnings, "\n".join(warnings)


def test_image_clipboard_preview_copy_and_save_use_image_page_only(qapp, quick_window, tmp_path):
    window, backend, warnings = quick_window
    quick = backend.quickTranslator
    original = QImage(640, 320, QImage.Format.Format_RGB32)
    original.fill(QColor("#d9e8ff"))
    qapp.clipboard().setImage(original)
    item(window, "quickSourceText").forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert not quick.hasImage
    click(window, "quickImageMode")
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(20)
    assert quick.hasImage and not quick.busy
    assert not backend.documents
    assert quick._image.size() == original.size()
    assert item(window, "quickTranslateButton").property("enabled")

    # Synthetic UI result: this test exercises presentation/export, not OCR quality.
    result = QImage(640, 320, QImage.Format.Format_RGB32)
    result.fill(QColor("#ecf8ed"))
    quick._image_result, quick._translated_url = result, preview_url(result)
    quick._image_source_text = "Safety instructions\nMaximum pressure: 16 bar"
    quick._image_translated_text = "Правила безпеки\nМаксимальний тиск: 16 бар"
    quick.changed.emit()
    QTest.qWait(20)
    preview = item(window, "quickImagePreview")
    assert preview.property("source").toString() == quick.translatedImageUrl
    assert item(window, "quickRecognizedText").property("selectByMouse")
    assert item(window, "quickImageTranslatedText").property("readOnly")
    click(window, "quickShowOriginal")
    assert preview.property("source").toString() == quick.originalImageUrl
    click(window, "quickShowTranslated")
    assert preview.property("source").toString() == quick.translatedImageUrl
    preview_width = preview.width()
    click(window, "quickZoomImage")
    assert preview.width() > preview_width
    click(window, "quickFitImage")
    assert preview.width() == pytest.approx(preview_width)
    click(window, "quickCopyImageText")
    assert qapp.clipboard().text() == quick.imageTranslatedText
    recognized = item(window, "quickRecognizedText")
    recognized.forceActiveFocus()
    qapp.clipboard().setImage(original)
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert quick.hasImageResult  # Read-only text controls do not paste a replacement photo.
    click(window, "quickCopyImage")
    assert qapp.clipboard().image() == result
    saved = tmp_path / "translated.png"
    backend._pickers.choose = lambda title, callback, **kwargs: callback([str(saved)])
    click(window, "quickSaveImage")
    assert QImage(str(saved)) == result
    for language in ("en", "pl", "de", "uk"):
        backend.setUiLanguage(language)
        QTest.qWait(20)
        assert quick.imageSourceText == "Safety instructions\nMaximum pressure: 16 bar"
        assert item(window, "quickImageViewport").width() >= 350
        assert item(window, "quickImageViewport").height() >= 140
        for name in ("quickTranslateButton", "quickCopyImage", "quickSaveImage"):
            button = item(window, name)
            assert button.width() >= button.implicitWidth() - 1
            assert button.mapToScene(QPointF(0, button.height())).y() <= window.height()
    quick._error = "Error details " * 100
    quick.changed.emit()
    QTest.qWait(20)
    assert item(window, "quickSaveImage").mapToScene(QPointF()).y() < window.height() - 40
    assert not window.grabWindow().isNull()
    assert not warnings, "\n".join(warnings)


def test_drop_image_does_not_add_document_or_start_request(quick_window, tmp_path):
    window, backend, warnings = quick_window
    quick = backend.quickTranslator
    image = QImage(240, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#e5eef8"))
    path = tmp_path / "photo.png"
    assert image.save(str(path))
    click(window, "quickImageMode")
    drop = item(window, "quickImageDrop")
    position = drop.mapToScene(QPointF(drop.width() / 2, drop.height() / 2))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    drag_event = QDragEnterEvent(
        position, Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QCoreApplication.sendEvent(window, drag_event)
    assert drag_event.isAccepted()
    drop_event = QDropEvent(
        position, Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QCoreApplication.sendEvent(window, drop_event)
    QTest.qWait(20)
    assert quick.hasImage and not quick.busy
    assert quick.imageName == "photo.png"
    assert not backend.documents
    assert not backend._threads
    assert not warnings, "\n".join(warnings)
