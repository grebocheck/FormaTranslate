from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.presentation.backend import Backend
from forma.settings.config import Settings


def contrast(a, b):
    def luminance(color):
        rgb = [color.redF(), color.greenF(), color.blueF()]
        rgb = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb
        ]
        return sum(
            value * weight for value, weight in zip(rgb, (0.2126, 0.7152, 0.0722), strict=True)
        )

    light, dark = sorted((luminance(QColor(a)), luminance(QColor(b))), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_tabs_are_clickable_and_buttons_stay_legible_at_minimum_window_size(qapp, monkeypatch):
    monkeypatch.setattr(
        Backend,
        "_read_settings",
        lambda _: Settings(base_url="http://localhost/v1", model="local-test"),
    )
    backend = Backend()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    try:
        QTest.qWait(50)
        start = window.findChild(QQuickItem, "translateButton")
        assert not start.property("enabled")
        assert contrast(start.property("foreground"), "#edf0f4") >= 4.5
        for index, name in enumerate(
            ("translationTab", "reviewTab", "settingsButton", "historyTab")
        ):
            tab = window.findChild(QQuickItem, name)
            assert tab is not None and tab.property("iconName")
            point = tab.mapToScene(QPointF(tab.width() / 2, tab.height() / 2))
            QTest.mouseClick(
                window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
            )
            qapp.processEvents()
            assert window.property("page") == index
            assert tab.property("checked")
            assert tab.width() >= tab.implicitWidth() - 1
        window.setProperty("page", 2)
        window.setProperty("performanceOptionsOpen", True)
        QTest.qWait(30)
        requests = window.findChild(QQuickItem, "parallelRequestsField")
        assert requests.property("text") == "8"
        for name in ("saveSettingsButton", "testConnectionButton", "performanceOptionsButton"):
            button = window.findChild(QQuickItem, name)
            assert button.property("iconName")
            assert button.width() >= button.implicitWidth() - 1
        save = window.findChild(QQuickItem, "saveSettingsButton")
        assert contrast(save.property("foreground"), window.property("accent")) >= 4.5
        assert not window.grabWindow().isNull()
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_model_picker_preserves_manual_input_and_accepts_keyboard_selection(qapp, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="saved-model"))
    backend = Backend()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    try:
        window.setProperty("page", 2)
        picker = window.findChild(QQuickItem, "modelField")
        assert picker.property("text") == "saved-model"
        picker.setProperty("text", "custom-provider/model:version")
        backend._model_choices = ["catalogue-model", "second-model"]
        backend.changed.emit()
        QTest.qWait(30)
        assert picker.property("text") == "custom-provider/model:version"
        field = window.findChild(QQuickItem, "modelFieldInput")
        field.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Down)
        QTest.keyClick(window, Qt.Key.Key_Return)
        QTest.qWait(20)
        assert picker.property("text") == "catalogue-model"
        backend.clearModels()
        assert picker.property("text") == "catalogue-model"
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_review_groups_findings_searches_pairs_and_keeps_text_selectable(
    qapp, docx_file, monkeypatch
):
    from forma.core.models import Issue, Segment, TranslationResult

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.addFiles([str(docx_file)])
    for _ in range(100):
        QTest.qWait(10)
        if not backend._threads:
            break
    assert not backend._threads
    document = backend._documents[0]
    segments = [
        Segment("first", "Paragraph 1", "<r0>Pressure: 16 bar</r0>"),
        Segment("second", "Paragraph 2", "<r0>Power: 25 kW</r0>"),
    ]
    document.update(
        status="done",
        sourceLanguage="en",
        targetLanguage="uk",
        result=TranslationResult(
            docx_file,
            None,
            segments,
            {"first": "<r0>Тиск: 15 бар</r0>", "second": "<r0>Потужність: 20 кВт</r0>"},
            [
                Issue("fields_preserved", "Fields preserved"),
                Issue("custom_finding", "Verify specification", "first"),
            ],
        ),
    )
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    try:
        assert window.findChild(QQuickItem, "comparisonTab") is None
        window.setProperty("page", 1)
        for _ in range(100):
            QTest.qWait(10)
            if not backend._threads:
                break
        assert backend.reviewSummary["attention"] == 2
        assert len(backend.reviewEntries) == 2
        assert backend.selectedReviewEntry["issueCount"] == 2
        source = window.findChild(QQuickItem, "reviewSourceText")
        translation = window.findChild(QQuickItem, "reviewTranslationText")
        assert source.property("readOnly") and source.property("selectByMouse")
        assert translation.property("readOnly") and translation.property("selectByMouse")
        assert "16" in source.property("text") and "15" in translation.property("text")
        next_button = window.findChild(QQuickItem, "reviewNextButton")
        point = next_button.mapToScene(QPointF(next_button.width() / 2, next_button.height() / 2))
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
        )
        QTest.qWait(10)
        assert backend.selectedReviewId == "second"
        assert "Потужність" in translation.property("text")
        backend.setReviewSearch("Тиск")
        assert len(backend.reviewEntries) == 1
        assert backend.selectedReviewId == "first"
        backend._notice("Готово", "success")
        notice = window.findChild(QQuickItem, "noticeBanner")
        assert not notice.property("visible")
        backend._notice("Document failed", "error")
        assert notice.property("visible")
        assert not warnings, "\n".join(warnings)
    finally:
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
