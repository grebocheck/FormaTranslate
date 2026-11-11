from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.presentation.backend import Backend
from forma.settings.config import Settings


def load_pdf_window(qapp, monkeypatch, pdf_file):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings(model="ui-test"))
    backend = Backend()
    backend.addFiles([str(pdf_file)])
    for _ in range(100):
        QTest.qWait(10)
        if not backend._threads:
            break
    assert not backend._threads
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects(), "\n".join(warnings)
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    qapp.processEvents()
    return backend, engine, window, warnings


def close_window(qapp, backend, engine, window):
    backend._translation_active = False
    backend._busy = False
    backend._abbyy_handoff = {}
    backend.changed.emit()
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_pdf_preparation_is_secondary_and_does_not_offer_pause(qapp, monkeypatch, pdf_file):
    backend, engine, window, warnings = load_pdf_window(qapp, monkeypatch, pdf_file)
    try:
        prepare_options = window.findChild(QQuickItem, "pdfPreparationOptionsButton")
        recognize = window.findChild(QQuickItem, "recognizePdfButton")
        translate = window.findChild(QQuickItem, "translateButton")
        cancel = window.findChild(QQuickItem, "cancelButton")
        assert prepare_options.isVisible()
        assert not recognize.isVisible()
        prepare_options.clicked.emit()
        qapp.processEvents()
        assert recognize.isVisible()

        # An explicitly configured converter remains editable in Auto mode,
        # including when it is missing and an installed ABBYY is available.
        backend._worker_availability.update(scanConverterReady=False, abbyyReady=True)
        backend._worker_probe_generation += 1
        backend.changed.emit()
        converter = window.findChild(QQuickItem, "scanConverterPathField")
        converter.setProperty("text", "missing/forma-ocr")
        qapp.processEvents()
        assert window.property("showLocalOcrSettings")

        document = backend._documents[0]
        document.update(status="translating", phase="preparing")
        backend._translation_active = True
        backend._busy = True
        backend.changed.emit()
        qapp.processEvents()
        assert translate.property("text") == "Підготовка…"
        assert not translate.isEnabled()
        assert cancel.isVisible() and cancel.isEnabled()

        # The same primary control becomes Pause only once translation starts.
        document["phase"] = "translating"
        backend.changed.emit()
        qapp.processEvents()
        assert translate.property("text") == "Пауза"
        assert translate.isEnabled()

        # A mixed queue can be paused while Office is active and reach an OCR
        # stage before that pause takes effect. Resume must stay accessible.
        document["phase"] = "preparing"
        backend._paused = True
        backend.changed.emit()
        qapp.processEvents()
        assert translate.property("text") == "Продовжити"
        assert translate.isEnabled()
        assert not warnings, "\n".join(warnings)
    finally:
        close_window(qapp, backend, engine, window)


def test_abbyy_import_can_resume_busy_run_and_wait_remains_dismissible(
    qapp, monkeypatch, pdf_file
):
    backend, engine, window, warnings = load_pdf_window(qapp, monkeypatch, pdf_file)
    try:
        document = backend._documents[0]
        document.update(status="translating", phase="waiting_ocr")
        backend._busy = True
        backend._translation_active = True
        backend._abbyy_handoff = {
            "sourceId": document["id"],
            "name": document["name"],
            "continueTranslation": True,
            "importing": False,
        }
        backend.changed.emit()
        dialog = window.findChild(QObject, "abbyyHandoffDialog")
        dialog.setProperty("visible", True)
        qapp.processEvents()
        reviewed = window.findChild(QQuickItem, "abbyyResultReviewed")
        reviewed.setProperty("checked", True)
        confirm = window.findChild(QQuickItem, "confirmImportAbbyyResultButton")
        later = window.findChild(QQuickItem, "closeAbbyyHandoffButton")
        text = window.findChild(QQuickItem, "abbyyContinuationText")
        instructions = window.findChild(QQuickItem, "abbyyReviewInstructions")
        assert confirm.property("text") == "Імпортувати й продовжити"
        assert confirm.isEnabled()
        assert text.property("text") == "Після імпорту переклад продовжиться автоматично."
        assert "замінить PDF" not in instructions.property("text")
        assert later.isEnabled()

        # Import cannot be started a second time while its first result is loading.
        backend._abbyy_handoff["importing"] = True
        backend.changed.emit()
        qapp.processEvents()
        assert not confirm.isEnabled()
        later.clicked.emit()
        qapp.processEvents()
        assert not dialog.property("visible")
        assert window.findChild(QQuickItem, "cancelButton").isEnabled()
        assert not warnings, "\n".join(warnings)
    finally:
        close_window(qapp, backend, engine, window)


def test_completed_pdf_does_not_require_source_language_for_pending_office(
    qapp, monkeypatch, pdf_file, docx_file,
):
    backend, engine, window, warnings = load_pdf_window(qapp, monkeypatch, pdf_file)
    try:
        backend._documents[0]["status"] = "done"
        backend.addFiles([str(docx_file)])
        for _ in range(100):
            QTest.qWait(10)
            if not backend._threads:
                break
        assert not backend._threads
        source = window.findChild(QQuickItem, "sourceLanguage")
        translate = window.findChild(QQuickItem, "translateButton")
        assert source.property("currentIndex") == 0
        assert not window.property("needsSourceLanguage")
        assert translate.isEnabled()

        # The all-complete fallback reruns every file, including the PDF.
        backend._documents[1]["status"] = "done"
        backend.changed.emit()
        qapp.processEvents()
        assert window.property("needsSourceLanguage")
        assert not translate.isEnabled()
        assert not warnings, "\n".join(warnings)
    finally:
        close_window(qapp, backend, engine, window)
