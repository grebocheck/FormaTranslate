from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QKeySequence
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from support import pump
from ui_support import click as click_control
from ui_support import forbid_provider_requests, item

from forma.localization.catalog import LANGUAGE_METADATA, CatalogTranslator


def test_real_review_confirm_edit_apply_and_export_controls(qapp, reviewed, monkeypatch):
    provider_requests = forbid_provider_requests(monkeypatch)
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
        return click_control(window, name)

    try:
        window.setProperty("page", 1)
        pump(qapp, lambda: not backend._threads)
        backend.selectReviewEntry(segment.id)
        QTest.qWait(20)
        export = window.findChild(QQuickItem, "reviewExportButton")
        assert not export.isVisible() and not export.isEnabled()
        click("reviewConfirmButton")
        assert backend.selectedReviewEntry["accepted"]
        click("reviewReopenButton")
        assert not backend.selectedReviewEntry["accepted"]
        click("reviewEditButton")
        target = item(window, "reviewTranslationText")
        assert backend.reviewEditing and not target.property("readOnly")
        assert not window.findChild(QQuickItem, "workspaceDocumentList").property("enabled")
        target.setProperty("text", "Введення теплолічильника в експлуатацію")
        QTest.qWait(10)
        assert backend.reviewDraftText == target.property("text")
        selected = backend.selectedReviewId
        artifacts = {path: path.read_bytes() for path in (result.source, result.output, result.report)}
        target.forceActiveFocus()
        target.setProperty("cursorPosition", 8)
        for _ in range(3):
            QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        for language, theme in ((code, theme) for theme in ("light", "dark") for code in LANGUAGE_METADATA):
            backend.setUiLanguage(language)
            backend.setUiTheme(theme)
            QTest.qWait(20)
            assert backend.reviewEditing and backend.selectedReviewId == selected
            assert target.property("text") == backend.reviewDraftText == "Введення теплолічильника в експлуатацію"
            assert target.property("cursorPosition") == 11
            assert target.property("selectionStart") == 8 and target.property("selectionEnd") == 11
            apply = window.findChild(QQuickItem, "reviewApplyButton")
            cancel = window.findChild(QQuickItem, "reviewCancelEditButton")
            assert target.height() >= 140
            assert abs(apply.mapToScene(QPointF()).y() - cancel.mapToScene(QPointF()).y()) < 1
            assert cancel.mapToScene(QPointF(cancel.width(), cancel.height())).x() <= window.width() - 16
            assert not export.isVisible() and not export.isEnabled()
        assert all(path.read_bytes() == original for path, original in artifacts.items())
        click("reviewApplyButton")
        assert not backend.reviewEditing and backend.reviewHasEdits
        assert target.property("readOnly")
        assert export.isVisible() and export.isEnabled()
        # Secondary undo restores the generated text; it never writes the file.
        click("reviewMoreActionsButton")
        click("reviewDiscardEditButton")
        assert not backend.reviewHasEdits and not export.isVisible()
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
        assert not provider_requests
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancelReviewEdit()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.removeTranslator(translator)


def test_long_review_draft_uses_common_scroll_and_retains_editor(qapp, reviewed):
    backend, _, segment = reviewed
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    try:
        window.setProperty("page", 1)
        pump(qapp, lambda: not backend._threads)
        backend.selectReviewEntry(segment.id)
        QTest.qWait(20)
        viewport = item(window, "reviewEntryList")
        assert viewport.isVisible() and viewport.property("count") > 1
        # The list presents document context without opening another navigator.
        assert viewport.property("contentHeight") > viewport.height()
        click_control(window, "reviewEditButton")
        target = item(window, "reviewTranslationText")
        draft = "\n".join(f"Technical sentence {number} with terminology." for number in range(60))
        target.setProperty("text", draft)
        target.forceActiveFocus()
        QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.MoveToEndOfDocument))
        QTest.qWait(30)
        assert target.height() >= target.property("contentHeight")
        assert target.height() > viewport.height() * 2
        assert viewport.property("contentY") > 0
        cursor = target.property("cursorRectangle")
        cursor_bottom = target.mapToItem(viewport, QPointF(cursor.x(), cursor.bottom())).y()
        assert 0 <= cursor_bottom <= viewport.height()
        assert backend.reviewDraftText == draft
        assert item(window, "reviewTranslationText") is target
        # Moving far away in the common viewport cannot destroy the current editor.
        viewport.setProperty("contentY", viewport.property("contentHeight") - viewport.height())
        QTest.qWait(30)
        assert item(window, "reviewTranslationText") is target
        assert backend.selectedReviewId == segment.id and backend.reviewDraftText == draft
        assert not item(window, "reviewNextButton").isVisible()
        assert not item(window, "reviewSearchField").isEnabled()
        click_control(window, "reviewCancelEditButton")
        assert not backend.reviewEditing and not backend.reviewHasEdits
        assert not warnings, "\n".join(warnings)
    finally:
        backend.cancelReviewEdit()
        pump(qapp, lambda: not backend._threads)
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
