from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QLibraryInfo,
    QLocale,
    QTimer,
    QTranslator,
    QUrl,
)
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from forma import __version__
from forma.backend import Backend
from forma.i18n import CatalogTranslator, current_language


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
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "ui" / "forma.svg")))
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
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "ui" / "Main.qml")))
    if not engine.rootObjects():
        return 1
    backend.setWindow(engine.rootObjects()[0])
    backend.closedReady.connect(app.quit)
    if files:
        backend.addFiles([QUrl.fromLocalFile(str(Path(p).resolve())) for p in files])
    if screenshot:

        def capture():
            if backend._threads:
                QTimer.singleShot(100, capture)
                return
            app.exit(0 if engine.rootObjects()[0].grabWindow().save(screenshot) else 2)

        QTimer.singleShot(1800, capture)
    result = app.exec()
    # Destroy QML bindings while their Python context object still exists.
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if translator:
        app.removeTranslator(translator)
    app.removeTranslator(catalog_translator)
    return result
