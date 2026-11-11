from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.presentation.backend import Backend
from forma.settings.config import Settings


def contrast(first, second):
    def luminance(color):
        color = QColor(color)
        values = (color.redF(), color.greenF(), color.blueF())
        return sum(
            (value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4) * weight
            for value, weight in zip(values, (0.2126, 0.7152, 0.0722), strict=True)
        )

    bright, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


def test_live_themes_preserve_work_and_follow_system_palette(qapp, docx_file, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="saved-model"))
    backend = Backend()
    backend.addFiles([str(docx_file)])
    for _ in range(100):
        QTest.qWait(10)
        if not backend._threads:
            break
    assert not backend._threads
    selected = backend.selectedId
    backend.quickTranslator.setSourceText("An unsaved source paragraph")
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects(), "\n".join(warnings)
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    window.setProperty("page", 2)
    model = window.findChild(QQuickItem, "modelField")
    key = window.findChild(QQuickItem, "apiKeyField")
    selector = window.findChild(QQuickItem, "interfaceThemeSelector")
    model.setProperty("text", "unsaved-model")
    key.setProperty("text", "fake-ui-test-key")
    backgrounds = []
    try:
        for theme in ("light", "dark", "system"):
            backend.setUiTheme(theme)
            assert backend.uiTheme == theme
            QTest.qWait(150)
            assert selector.property("currentIndex") == ("system", "light", "dark").index(theme)
            assert contrast(window.property("ink"), window.property("bg")) >= 4.5
            assert contrast(window.property("muted"), window.property("bg")) >= 4.5
            save = window.findChild(QQuickItem, "saveSettingsButton")
            assert contrast(save.property("foreground"), save.property("background").property("color")) >= 4.5
            assert model.property("text") == "unsaved-model"
            assert key.property("text") == "fake-ui-test-key"
            assert backend.selectedId == selected
            assert backend.quickTranslator.sourceText == "An unsaved source paragraph"
            assert window.property("font").family() == "Inter"
            backgrounds.append(window.property("bg"))
            assert not window.grabWindow().isNull()
        assert backgrounds[0] != backgrounds[1]
        hints = QGuiApplication.styleHints()
        original = hints.colorScheme()
        try:
            from PySide6.QtCore import Qt

            hints.setColorScheme(Qt.ColorScheme.Light)
            QTest.qWait(100)
            expected = backgrounds[1] if hints.colorScheme() == Qt.ColorScheme.Dark else backgrounds[0]
            assert window.property("bg") == expected
            hints.setColorScheme(Qt.ColorScheme.Dark)
            QTest.qWait(100)
            expected = backgrounds[1] if hints.colorScheme() == Qt.ColorScheme.Dark else backgrounds[0]
            assert window.property("bg") == expected
        finally:
            hints.setColorScheme(original)
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
