"""Rendered document pages remain usable across modes, navigation and restart."""
from pathlib import Path

from pypdf import PdfWriter
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from support import pump
from ui_support import click, item

from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def _pdf(path, pages):
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=595, height=842 + index * 20)
    writer.write(path)


def test_pdf_pages_modes_zoom_and_unequal_lengths_after_restart(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="offline"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Viewing documents must not call a translation provider")

    monkeypatch.setattr("forma.providers.client.Provider.complete", forbidden)
    source, output = tmp_path / "source.pdf", tmp_path / "translation.pdf"
    _pdf(source, 3)
    _pdf(output, 2)
    original = Backend()
    original.addFiles([str(source)])
    pump(qapp, lambda: not original._threads)
    original._documents[0].update(status="done", output=str(output))
    original._persist_queue()
    original.requestClose()
    pump(qapp, lambda: not original._threads)
    backend = Backend()
    pump(qapp, lambda: not backend._threads)
    assert "result" not in backend._documents[0]
    backend.setWorkspaceView({"previewMode": "paired"})
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
    window.setWidth(1220)
    window.setHeight(820)
    progress_timer = QTimer(window)
    progress_timer.setInterval(50)
    progress_timer.timeout.connect(backend.changed.emit)

    def wait_page(page, mode="paired"):
        pump(qapp, lambda: backend.previewState["page"] == page
             and backend.previewState["mode"] == mode
             and not backend.previewState["busy"]
             and backend.previewState["source" if mode != "translation" else "translation"]["status"] == "ready",
             timeout=15)
        QTest.qWait(50)

    def mode(value):
        selector = item(window, "documentPreviewMode")
        index = ["original", "translation", "paired"].index(value)
        selector.setProperty("currentIndex", index)
        selector.activated.emit(index)
        wait_page(1, value)

    try:
        wait_page(1)
        assert backend.previewState["source"]["pageCount"] == 3
        assert backend.previewState["translation"]["pageCount"] == 2
        assert item(window, "documentOriginalPreview").isVisible()
        assert item(window, "documentTranslationPreview").isVisible()
        page_field = item(window, "documentPreviewPageField")
        progress_timer.start()  # Background progress must not starve the preview debounce.
        page_field.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_2)
        QTest.keyClick(window, Qt.Key.Key_Return)
        wait_page(2)
        progress_timer.stop()
        assert page_field.property("text") == "2" and page_field.isEnabled()
        click(window, "previewPreviousPageButton")
        wait_page(1)
        click(window, "previewNextPageButton")
        wait_page(2)
        assert item(window, "documentPreviewPageField").property("text") == "2"
        click(window, "previewNextPageButton")
        wait_page(3)
        assert backend.previewState["translation"]["status"] == "unavailable"
        assert not backend.previewState["translation"]["imageUrl"]
        assert not item(window, "previewNextPageButton").isEnabled()

        mode("original")
        assert not item(window, "documentTranslationPreview").isVisible()
        mode("translation")
        assert not item(window, "documentOriginalPreview").isVisible()
        mode("paired")
        # Zoom exposes scrollable page detail. Paired panes scroll together.
        item(window, "documentPreviewZoom").setProperty("currentIndex", 4)
        pump(qapp, lambda: not backend._threads, timeout=15)
        QTest.qWait(200)
        wait_page(1)
        source_view = item(window, "documentOriginalPreviewViewport")
        translated_view = item(window, "documentTranslationPreviewViewport")
        source_view.setProperty("contentY", 100.0)
        QTest.qWait(20)
        assert translated_view.property("contentY") > 0
        selected = backend.workspaceDocumentId
        for language in ("en", "uk", "pl", "de", "fr", "es"):
            backend.setUiLanguage(language)
            backend.setUiTheme("dark" if language in {"en", "de", "es"} else "light")
            window.setWidth(960)
            QTest.qWait(30)
            assert backend.workspaceDocumentId == selected
            assert backend.workspaceView["previewMode"] == "paired"
            assert item(window, "documentPreviewMode").isVisible()
            assert item(window, "documentTranslationPreview").width() > 220
        assert not warnings, "\n".join(warnings)
    finally:
        progress_timer.stop()
        backend._preview.shutdown()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
