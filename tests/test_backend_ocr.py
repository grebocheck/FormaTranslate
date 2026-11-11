from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from support import settle

import forma.presentation.ocr_workflow as backend_module
from forma.core.models import Cancelled, FormaError
from forma.localization.catalog import translate
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def pdf_backend(qapp, monkeypatch, pdf_file, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    backend.setOutputFolder(str(tmp_path / "converted"))
    backend.addFiles([str(pdf_file)])
    settle(qapp, backend)
    return backend


def test_scan_conversion_replaces_pdf_queue_entry_without_provider_or_translation(
    qapp, monkeypatch, pdf_file, docx_file, tmp_path
):
    backend = pdf_backend(qapp, monkeypatch, pdf_file, tmp_path)
    original = pdf_file.read_bytes()
    calls = []

    def convert(source, folder, source_language, configured, control, progress):
        calls.append((source, source_language, configured))
        assert not control.cancelled.is_set()
        progress(40, "Готуємо розпізнавання PDF")
        folder.mkdir(parents=True)
        output = folder / "scan.ocr.docx"
        shutil.copyfile(docx_file, output)
        return output

    monkeypatch.setattr(backend_module, "convert_scan_pdf", convert)
    monkeypatch.setattr(
        "forma.presentation.settings_workflow.Provider",
        lambda *_: (_ for _ in ()).throw(AssertionError("OCR must not create an API provider")),
    )
    backend.recognizeSelectedPdf("en")
    assert backend.busy and backend.recognizingScan and not backend.translating
    settle(qapp, backend)
    assert calls == [(pdf_file, "en", "")]
    assert not backend.busy and not backend.recognizingScan
    assert pdf_file.read_bytes() == original
    assert len(backend.documents) == 1
    prepared = backend.selectedDocument
    assert prepared["format"] == "DOCX"
    assert prepared["status"] == "ready"
    assert prepared["segmentCount"] > 0
    assert prepared["output"] == ""
    assert not backend.settings["model"] and not backend.settings["keyPresent"]
    assert "Перекласти" in backend.message
    assert backend.messageKind == "warning"


def test_scan_conversion_requires_explicit_language_without_starting_task(
    qapp, monkeypatch, pdf_file, tmp_path
):
    backend = pdf_backend(qapp, monkeypatch, pdf_file, tmp_path)
    before = backend.selectedDocument["path"]
    monkeypatch.setattr(
        backend_module,
        "convert_scan_pdf",
        lambda *_: (_ for _ in ()).throw(AssertionError("Must validate language before starting")),
    )
    backend.recognizeSelectedPdf("auto")
    assert not backend.busy
    assert backend.selectedDocument["path"] == before
    assert "мову оригіналу" in backend.message


def test_scan_cancellation_preserves_original_queue_and_does_not_offer_pause(
    qapp, monkeypatch, pdf_file, tmp_path
):
    backend = pdf_backend(qapp, monkeypatch, pdf_file, tmp_path)
    started = threading.Event()
    before = backend.selectedDocument.copy()

    def convert(source, folder, source_language, configured, control, progress):
        started.set()
        assert control.cancelled.wait(5)
        raise Cancelled("cancelled")

    monkeypatch.setattr(backend_module, "convert_scan_pdf", convert)
    backend.recognizeSelectedPdf("en")
    assert started.wait(2)
    backend.togglePause()
    assert not backend.paused and not backend._control.paused.is_set()
    backend.cancel()
    settle(qapp, backend)
    assert backend.selectedDocument == before
    assert len(backend.documents) == 1
    assert not backend.busy and not backend.recognizingScan
    assert "зупинено" in backend.message


def test_scan_converter_failure_keeps_original_pdf_available(
    qapp, monkeypatch, pdf_file, tmp_path
):
    backend = pdf_backend(qapp, monkeypatch, pdf_file, tmp_path)

    def convert(*_):
        raise FormaError("OCR worker unavailable")

    monkeypatch.setattr(backend_module, "convert_scan_pdf", convert)
    backend.recognizeSelectedPdf("uk")
    settle(qapp, backend)
    assert backend.selectedDocument["path"] == str(pdf_file)
    assert backend.selectedDocument["status"] == "ready"
    assert backend.message == "OCR worker unavailable"
    assert backend.messageKind == "error"


def test_scan_converter_setting_persists_without_provider_credentials(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings.load())
    monkeypatch.chdir(tmp_path)
    backend = Backend()
    configured = str(Path("converter path") / "forma-ocr")
    assert backend.saveSettings({"scanConverterPath": configured})
    assert backend.settings["scanConverterPath"] == configured
    assert Backend().settings["scanConverterPath"] == configured


def test_scan_findings_are_visible_and_switch_language_without_exposing_manifest_text(
    qapp, monkeypatch, pdf_file, docx_file, tmp_path
):
    backend = pdf_backend(qapp, monkeypatch, pdf_file, tmp_path)

    def convert(source, folder, source_language, configured, control, progress):
        folder.mkdir(parents=True)
        output = folder / "scan.ocr.docx"
        shutil.copyfile(docx_file, output)
        output.with_suffix(".ocr.json").write_text(
            json.dumps(
                {
                    "text": "private recognized text",
                    "pages": [
                        {
                            "warnings": ["table_structure_unresolved", "private recognized text"],
                            "recovered_lines": 2,
                        },
                        {"warnings": ["low_ocr_confidence"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return output

    monkeypatch.setattr(backend_module, "convert_scan_pdf", convert)
    backend.recognizeSelectedPdf("en")
    settle(qapp, backend)
    messages = [
        "Частину таблиць збережено зображеннями та текстом, бо структуру не відновлено. "
        "Виправте їх у DOCX перед перекладом.",
        "Відновлені поза структурою рядки виділено жовтим у DOCX. "
        "Перевірте порядок і дублювання.",
        "Є фрагменти з низькою впевненістю OCR. Звірте їх зі сканом, особливо числа та коди.",
    ]
    for language in ("uk", "en", "pl", "de"):
        backend.setUiLanguage(language)
        assert all(translate(message) in backend.message for message in messages)
        assert "private recognized text" not in backend.message
        assert backend.messageKind == "warning"
    backend.dismissMessage()
    assert not backend.message
    backend._notice("Готово")
    assert backend.message == translate("Готово")


def test_scan_findings_ignore_missing_or_malformed_sidecar(tmp_path):
    output = tmp_path / "scan.docx"
    assert backend_module._scan_review_warnings(output) == []
    output.with_suffix(".ocr.json").write_bytes(b"{malformed")
    assert backend_module._scan_review_warnings(output) == []
