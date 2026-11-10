from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from forma.backend import Backend


def test_qml_loads_and_renders(tmp_path, qapp):
    app = qapp
    engine = QQmlApplicationEngine()
    backend = Backend()
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    app.processEvents()
    assert engine.rootObjects(), "\n".join(warnings)
    root = engine.rootObjects()[0]
    image = root.grabWindow()
    assert not image.isNull()
    assert image.width() >= 1180
    assert not warnings, "\n".join(warnings)
    root.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not warnings, "\n".join(warnings)
