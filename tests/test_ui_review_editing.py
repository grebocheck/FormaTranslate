from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from support import pump

from forma.localization.catalog import CatalogTranslator


def test_real_review_confirm_edit_apply_and_export_controls(qapp, reviewed):
    backend, result, segment = reviewed
    engine = QQmlApplicationEngine()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    backend.languageChanged.connect(engine.retranslate)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)

    def click(name):
        button = window.findChild(QQuickItem, name)
        assert button is not None and button.property("visible") and button.property("enabled"), (
            name
        )
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point.toPoint()
        )
        QTest.qWait(10)

    try:
        window.setProperty("page", 1)
        pump(qapp, lambda: not backend._threads)
        backend.selectReviewEntry(segment.id)
        QTest.qWait(20)
        export = window.findChild(QQuickItem, "reviewExportButton")
        assert export.isVisible() and not export.isEnabled()
        click("reviewConfirmButton")
        assert backend.selectedReviewEntry["accepted"]
        click("reviewReopenButton")
        assert not backend.selectedReviewEntry["accepted"]
        click("reviewEditButton")
        target = window.findChild(QQuickItem, "reviewTranslationText")
        assert backend.reviewEditing and not target.property("readOnly")
        assert not window.findChild(QQuickItem, "reviewDocumentSelector").property("enabled")
        target.setProperty("text", "Введення теплолічильника в експлуатацію")
        QTest.qWait(10)
        assert backend.reviewDraftText == target.property("text")
        selected = backend.selectedReviewId
        target.setProperty("cursorPosition", 8)
        for language, theme in (("uk", "light"), ("en", "light"), ("pl", "dark"),
                                ("de", "dark"), ("fr", "light"), ("es", "dark")):
            backend.setUiLanguage(language)
            backend.setUiTheme(theme)
            QTest.qWait(20)
            assert backend.reviewEditing and backend.selectedReviewId == selected
            assert target.property("text") == backend.reviewDraftText == "Введення теплолічильника в експлуатацію"
            assert target.property("cursorPosition") == 8
            apply = window.findChild(QQuickItem, "reviewApplyButton")
            cancel = window.findChild(QQuickItem, "reviewCancelEditButton")
            assert target.height() >= 140
            assert abs(apply.mapToScene(QPointF()).y() - cancel.mapToScene(QPointF()).y()) < 1
            assert cancel.mapToScene(QPointF(cancel.width(), cancel.height())).x() <= window.width() - 20
            assert export.isVisible() and not export.isEnabled()
        click("reviewApplyButton")
        assert not backend.reviewEditing and backend.reviewHasEdits
        assert target.property("readOnly")
        # Secondary undo restores the generated text; it never writes the file.
        click("reviewMoreActionsButton")
        click("reviewDiscardEditButton")
        assert not backend.reviewHasEdits and not export.isEnabled()
        click("reviewEditButton")
        target.setProperty("text", "Введення теплолічильника в експлуатацію")
        QTest.qWait(10)
        click("usageButton")
        modal = window.findChild(QObject, "usagePanel")
        assert modal.property("visible") and not target.hasActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(10)
        assert backend.reviewEditing and not backend.reviewHasEdits
        QMetaObject.invokeMethod(modal, "close")
        QTest.qWait(10)
        target.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(10)
        assert not backend.reviewEditing and backend.reviewHasEdits
        click("reviewExportButton")
        pump(qapp, lambda: not backend._threads)
        assert not backend.reviewActionError
        assert backend.reviewDocument["output"] != str(result.output)
        assert Path(backend.reviewDocument["output"]).is_file()
        assert not backend.reviewHasEdits
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancelReviewEdit()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)
