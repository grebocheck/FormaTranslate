from __future__ import annotations

import hashlib
import json
from pathlib import Path

from test_backend_ocr import settle

import forma.presentation.ocr_workflow as workflow
from forma.presentation.backend import Backend
from forma.settings.config import Settings


def prepare(qapp, monkeypatch, pdf_file, tmp_path):
    monkeypatch.setattr(Backend, '_read_settings', lambda _: Settings())
    monkeypatch.setattr(workflow, 'find_abbyy', lambda _: '/installed/FineReaderOCR.exe')
    launches = []
    monkeypatch.setattr(workflow, 'launch_abbyy_handoff', lambda *args: launches.append(args))
    monkeypatch.setattr(workflow, 'convert_scan_pdf', lambda *_: (_ for _ in ()).throw(AssertionError('Must use the installed provider')))
    backend = Backend()
    backend.setOutputFolder(str(tmp_path / 'converted'))
    backend.addFiles([str(pdf_file)])
    settle(qapp, backend)
    original_id = backend.selectedDocument['id']
    backend.recognizeSelectedPdf('uk')
    settle(qapp, backend)
    assert len(launches) == 1 and backend.selectedDocument['id'] == original_id
    assert backend.selectedDocument['format'] == 'PDF'
    assert not backend.busy and backend.abbyyHandoff['showPrompt']
    assert backend.abbyyHandoff['sourceSha256'] == hashlib.sha256(pdf_file.read_bytes()).hexdigest()
    return backend


def test_auto_detected_abbyy_is_assisted_and_manual_result_requires_review(qapp, monkeypatch, pdf_file, docx_file, tmp_path):
    backend = prepare(qapp, monkeypatch, pdf_file, tmp_path)
    original_pdf, original_docx = pdf_file.read_bytes(), docx_file.read_bytes()
    callbacks = []
    monkeypatch.setattr(backend._pickers, 'choose', lambda title, callback, **kwargs: callbacks.append(callback))
    backend.dismissAbbyyHandoff()
    assert backend.abbyyHandoff['path'] == str(pdf_file) and not backend.abbyyHandoff['showPrompt']
    backend.importAbbyyResult(False)
    assert not callbacks and backend.selectedDocument['format'] == 'PDF'
    backend.importAbbyyResult(True)
    callbacks[0]([str(docx_file)])
    settle(qapp, backend)
    assert backend.selectedDocument['format'] == 'DOCX' and not backend.abbyyHandoff
    output = Path(backend.selectedDocument['path'])
    assert output != docx_file and output.read_bytes() == original_docx
    metadata = json.loads(output.with_suffix('.ocr.json').read_text())
    assert metadata['provider'] == 'abbyy' and metadata['coverage'] == 'not_verified'
    assert pdf_file.read_bytes() == original_pdf and docx_file.read_bytes() == original_docx
    assert backend.messageKind == 'warning' and not backend.translating


def test_changed_pdf_cannot_be_paired_with_previous_abbyy_handoff(qapp, monkeypatch, pdf_file, docx_file, tmp_path):
    backend = prepare(qapp, monkeypatch, pdf_file, tmp_path)
    callbacks = []
    monkeypatch.setattr(backend._pickers, 'choose', lambda title, callback, **kwargs: callbacks.append(callback))
    pdf_file.write_bytes(pdf_file.read_bytes() + b'\nchanged\n')
    backend.importAbbyyResult(True)
    callbacks[0]([str(docx_file)])
    settle(qapp, backend)
    assert backend.selectedDocument['format'] == 'PDF'
    assert backend.messageKind == 'error' and not backend.busy
    assert not list((tmp_path/'converted').glob('*.docx'))


def test_invalid_or_unlicensed_provider_never_starts_a_converter(qapp, monkeypatch, pdf_file):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid configuration must not select a different engine")

    monkeypatch.setattr(workflow, "find_abbyy", unexpected)
    monkeypatch.setattr(workflow, "convert_scan_pdf", unexpected)
    monkeypatch.setattr(workflow, "convert_with_abbyy", unexpected)
    backend = Backend()
    backend.addFiles([str(pdf_file)])
    settle(qapp, backend)
    for provider in ("unknown-engine", "abbyy_extended"):
        backend._settings = Settings(scan_provider=provider)
        backend.recognizeSelectedPdf("uk")
        assert not backend.busy and not backend._threads
        assert backend.messageKind == "error" and backend.selectedDocument["format"] == "PDF"


def test_handoff_hash_remains_bound_when_pdf_changes_inside_import(qapp, monkeypatch, pdf_file, docx_file, tmp_path):
    backend = prepare(qapp, monkeypatch, pdf_file, tmp_path)
    original_import = workflow.import_abbyy_result
    callbacks = []
    monkeypatch.setattr(backend._pickers, "choose", lambda title, callback, **kwargs: callbacks.append(callback))

    def mutate_between_checks(*args, **kwargs):
        pdf_file.write_bytes(pdf_file.read_bytes() + b"\nchanged after outer check\n")
        return original_import(*args, **kwargs)

    monkeypatch.setattr(workflow, "import_abbyy_result", mutate_between_checks)
    backend.importAbbyyResult(True)
    callbacks[0]([str(docx_file)])
    settle(qapp, backend)
    assert backend.selectedDocument["format"] == "PDF" and backend.messageKind == "error"
    assert not list((tmp_path / "converted").glob("*.docx"))


def test_background_detection_exposes_exact_path_without_ui_rescans_or_override(qapp, monkeypatch):
    import time

    from PySide6.QtTest import QTest

    from forma.settings.config import load_preferences

    detected = r'E:\Program Files\ABBYY FineReader 16\FineReaderOCR.exe'
    calls = []

    def find(configured):
        calls.append(configured)
        return detected

    monkeypatch.setattr('forma.presentation.settings_workflow.find_abbyy', find)
    backend = Backend()
    deadline = time.monotonic() + 5
    while backend._worker_probe_running and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    settle(qapp, backend)
    assert backend.settings['abbyyReady']
    assert backend.settings['abbyyDetectedPath'] == detected
    assert backend.settings['scanProviderEffective'] == 'abbyy'
    assert backend.settings['abbyyPath'] == ''
    for _ in range(100):
        assert backend.settings['abbyyDetectedPath'] == detected
    assert calls == ['']
    assert load_preferences().get('abbyy_path', '') == ''
