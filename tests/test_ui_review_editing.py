from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from test_review_actions import pump
from test_review_actions import reviewed as reviewed


def test_real_review_confirm_edit_apply_and_export_controls(qapp, reviewed):
    backend, result, segment = reviewed
    engine = QQmlApplicationEngine()
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
        click("reviewApplyButton")
        assert not backend.reviewEditing and backend.reviewHasEdits
        assert target.property("readOnly")
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
