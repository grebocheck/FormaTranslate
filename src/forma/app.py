from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QLibraryInfo,
    QLocale,
    Qt,
    QTimer,
    QTranslator,
    QUrl,
)
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from forma import __version__
from forma.core.resources import resource_path
from forma.localization.catalog import CatalogTranslator, current_language
from forma.presentation.backend import Backend


def _preview_image_status(window):
    """QML images decode asynchronously after the preview worker has finished."""
    status = "ready"
    items = [window.contentItem()]
    while items:
        item = items.pop()
        items.extend(item.childItems())
        if (item.objectName() not in {"documentOriginalPreviewImage", "documentTranslationPreviewImage"}
                or not item.isVisible() or not item.property("captureRelevant")):
            continue
        value = item.property("captureStatus")  # QQuickImage's private enum has no Python converter.
        if value == 3:  # Image.Error
            return "error"
        if value != 1:  # Image.Ready
            status = "loading"
    return status


def install_qt_translations(app: QApplication) -> QTranslator | None:
    """Translate Qt's widget fallback; native OS dialogs keep their platform style."""
    translator = QTranslator(app)
    if translator.load(
        QLocale(current_language()),
        "qtbase",
        "_",
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
    ):
        app.installTranslator(translator)
        return translator
    return None


def main(files: list[str] | None = None, screenshot: str | None = None) -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Forma")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Forma")
    font_path = resource_path("ui", "fonts", "InterVariable.ttf")
    if font_path.is_file() and QFontDatabase.addApplicationFont(str(font_path)) >= 0:
        app.setFont(QFont("Inter", 10))
    app.setWindowIcon(QIcon(str(resource_path("ui", "forma.svg"))))
    backend = Backend()
    translator = install_qt_translations(app)
    catalog_translator = CatalogTranslator(app)
    app.installTranslator(catalog_translator)
    engine = QQmlApplicationEngine()

    def retranslate():
        nonlocal translator
        if translator:
            app.removeTranslator(translator)
        translator = install_qt_translations(app)
        engine.retranslate()

    backend.languageChanged.connect(retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(resource_path("ui", "Main.qml"))))
    if not engine.rootObjects():
        backend._preview.shutdown()
        return 1
    backend.setWindow(engine.rootObjects()[0])
    backend.closedReady.connect(app.quit)
    if files:
        backend.addFiles([QUrl.fromLocalFile(str(Path(p).resolve())) for p in files])
    if screenshot:
        window = engine.rootObjects()[0]
        capture_state = {"finished": False, "waiting_since": None, "awaiting_frame": False, "frame_ready": False}

        def rendered():
            if capture_state["awaiting_frame"] and not capture_state["finished"]:
                capture_state["frame_ready"] = True
                QTimer.singleShot(0, capture)

        window.frameSwapped.connect(rendered, Qt.ConnectionType.QueuedConnection)

        def capture():
            if capture_state["finished"]:
                return
            if backend._threads:
                capture_state.update(waiting_since=None, awaiting_frame=False, frame_ready=False)
                QTimer.singleShot(100, capture)
                return
            if capture_state["waiting_since"] is None:
                capture_state["waiting_since"] = time.monotonic()
            status = _preview_image_status(window)
            if status == "error" or time.monotonic() - capture_state["waiting_since"] > 10:
                capture_state["finished"] = True
                app.exit(2)
                return
            if status != "ready":
                capture_state.update(awaiting_frame=False, frame_ready=False)
                QTimer.singleShot(100, capture)
                return
            if not capture_state["awaiting_frame"]:
                capture_state["awaiting_frame"] = True
                window.update()
            if not capture_state["frame_ready"]:
                QTimer.singleShot(100, capture)
                return
            capture_state["finished"] = True
            app.exit(0 if window.grabWindow().save(screenshot) else 2)

        QTimer.singleShot(1800, capture)
    result = app.exec()
    if screenshot:
        capture_state["finished"] = True
    # Destroy QML bindings while their Python context object still exists.
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    backend._preview.shutdown()
    if translator:
        app.removeTranslator(translator)
    app.removeTranslator(catalog_translator)
    return result
