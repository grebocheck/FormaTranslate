"""Continuous page previews render a viewport and keep the reading area free."""
from pathlib import Path

from pypdf import PdfWriter
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
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
        writer.add_blank_page(width=595, height=842 + index % 3 * 20)
    writer.write(path)


def test_continuous_document_scrolling_modes_and_restored_session(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="offline"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Viewing documents must not call a translation provider")

    monkeypatch.setattr("forma.providers.client.Provider.complete", forbidden)
    source, output = tmp_path / "source.pdf", tmp_path / "translation.pdf"
    _pdf(source, 24)
    _pdf(output, 22)
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

    def row(page):
        model = backend.previewPageModel
        return model.data(model.index(page - 1), model.ROW_ROLE) or {}

    def wait_page(page, mode="paired"):
        side = "translation" if mode == "translation" else "source"
        pump(qapp, lambda: backend.previewState.get("mode") == mode
             and row(page).get(side, {}).get("status") == "ready", timeout=20)
        QTest.qWait(70)

    def mode(value):
        selector = item(window, "documentPreviewMode")
        index = ["original", "translation", "paired"].index(value)
        selector.setProperty("currentIndex", index)
        selector.activated.emit(index)
        wait_page(1, value)

    try:
        wait_page(1)
        assert backend.previewPageModel.rowCount() == 24
        assert backend.previewState["source"]["pageCount"] == 24
        assert backend.previewState["translation"]["pageCount"] == 22
        # All pages exist in the scroll model; distant pages have not been rasterized.
        assert not row(24)["source"]["imageUrl"]
        viewport = item(window, "documentContinuousPreview")
        assert viewport.property("contentHeight") > viewport.height() * 10
        viewport.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_PageDown)
        QTest.qWait(150)
        assert viewport.property("contentY") > 0
        wait_page(2)

        # A page number jumps within the same continuous document.
        field = click(window, "documentPreviewPageField")
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(window, Qt.Key_2)
        QTest.keyClick(window, Qt.Key_4)
        QTest.keyClick(window, Qt.Key_Return)
        wait_page(24)
        assert field.property("text") == "24"
        assert viewport.property("contentY") > viewport.height() * 10
        assert row(24)["translation"]["status"] == "unavailable"
        assert not row(24)["translation"]["imageUrl"]
        item(window, "documentPreviewZoom").setProperty("currentIndex", 4)
        QTest.qWait(250)
        wait_page(24)
        assert item(window, "documentPagePreview").property("currentPage") == 24
        item(window, "documentPreviewZoom").setProperty("currentIndex", 0)
        QTest.qWait(200)
        mode("original")
        assert backend.previewPageModel.rowCount() == 24
        mode("translation")
        assert backend.previewPageModel.rowCount() == 22
        mode("paired")

        # Translation controls share the top card; the preview reaches the bottom.
        button = item(window, "translateButton")
        panel = item(window, "documentPreviewPanel")
        assert button.mapToScene(QPointF()).y() < panel.mapToScene(QPointF()).y()
        assert panel.mapToScene(QPointF()).y() + panel.height() > window.height() - 70
        sidebar_row = item(window, "workspaceDocumentRow_" + backend.workspaceDocumentId)
        sidebar_list = item(window, "workspaceDocumentList")
        assert sidebar_row.height() <= 72
        assert sidebar_list.width() - sidebar_row.width() >= 16
        selected = backend.workspaceDocumentId
        for language in ("en", "uk", "pl", "de", "fr", "es"):
            backend.setUiLanguage(language)
            backend.setUiTheme("dark" if language in {"en", "de", "es"} else "light")
            window.setWidth(960)
            QTest.qWait(30)
            assert backend.workspaceDocumentId == selected
            assert backend.workspaceView["previewMode"] == "paired"
            assert item(window, "documentPreviewMode").isVisible()
        wait_page(1)
        assert not warnings, "\n".join(warnings)
    finally:
        backend._preview.shutdown()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
