from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FORMA_UI_LANGUAGE", "uk")
    from forma.localization.catalog import set_language

    set_language("uk")
    yield
    set_language("uk")


@pytest.fixture(scope="session")
def generators():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/make_examples.py"))


@pytest.fixture
def docx_file(tmp_path, generators):
    path = tmp_path / "brief.docx"
    generators["make_docx"](path)
    return path


@pytest.fixture
def xlsx_file(tmp_path, generators):
    path = tmp_path / "checklist.xlsx"
    generators["make_xlsx"](path)
    return path


@pytest.fixture
def pdf_file(tmp_path, generators):
    path = tmp_path / "brief.pdf"
    generators["make_pdf"](path)
    return path


@pytest.fixture
def scan(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "synthetic-scan.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=595, height=842)
    writer.write(path)
    return path



@pytest.fixture
def local_converter(tmp_path, monkeypatch):
    """Exercise real subprocess IO/cancellation with a bounded, offline stand-in."""
    import subprocess
    import sys

    script = tmp_path / "fake_ocr.py"
    script.write_text(
        '''import argparse, hashlib, json, os, sys, time
from pathlib import Path
from docx import Document
from pypdf import PdfReader
p = argparse.ArgumentParser()
p.add_argument('--input', type=Path)
p.add_argument('--output', type=Path)
p.add_argument('--source-language')
a = p.parse_args()
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY'))
mode = os.environ.get('FORMA_TEST_OCR_MODE', '')
print(json.dumps({'type': 'progress', 'stage': ['not-a-stage'], 'value': 1}), flush=True)
print(json.dumps({'type': 'error', 'code': []}), flush=True)
print(json.dumps({'type': 'progress', 'stage': 'recognizing', 'value': 20}), flush=True)
if mode == 'wait': time.sleep(30)
if mode == 'fail':
    print('Private recognized text must not appear in UI')
    sys.exit(1)
d = Document()
d.add_paragraph('Heat meter 42')
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).merge(t.cell(0, 1)).text = 'sensor'
t.cell(1, 0).text = 'sensor 1'
t.cell(1, 1).text = 'sensor 2'
d.add_page_break()
d.add_paragraph('Heat meter 43')
d.save(a.output)
pages = [{'index': i, 'text_chars': 40, 'status': 'complete', 'tables': int(i == 1), 'warnings': []} for i in range(1, len(PdfReader(a.input).pages) + 1)]
manifest = {'schema_version': 1, 'engine': {'name': 'offline-fake'}, 'source_language': a.source_language, 'page_count': len(pages), 'pages': pages, 'source_sha256': hashlib.sha256(a.input.read_bytes()).hexdigest(), 'output_sha256': hashlib.sha256(a.output.read_bytes()).hexdigest()}
if mode == 'missing': manifest['pages'].pop()
if mode == 'duplicate': manifest['pages'][1]['index'] = 1
if mode == 'hash': manifest['output_sha256'] = '0' * 64
if mode == 'bool': manifest['pages'][0]['index'] = True
if mode == 'zero_page': manifest['pages'][0]['text_chars'] = 0
if mode == 'false_blank': manifest['pages'][0]['status'] = 'blank'
if mode == 'source_changed': a.input.write_bytes(b'changed while recognizing')
a.output.with_suffix('.ocr.json').write_text(json.dumps(manifest), encoding='utf-8')
''',
        encoding="utf-8",
    )
    real_popen = subprocess.Popen

    def launch(arguments, **kwargs):
        assert arguments[0] == "offline-converter"
        return real_popen([sys.executable, str(script), *arguments[1:]], **kwargs)

    monkeypatch.setattr("forma.documents.ocr.find_scan_converter", lambda _: "offline-converter")
    monkeypatch.setattr("forma.documents.ocr.subprocess.Popen", launch)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "synthetic-private-key")



@pytest.fixture
def reviewed(qapp, monkeypatch, docx_file, tmp_path):
    from support import pump

    from forma.core.models import Options, TranslationResult
    from forma.documents.ooxml import OfficeDocument
    from forma.presentation.backend import Backend
    from forma.settings.config import Settings
    from forma.translation.reports import write_translation_report

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    source = OfficeDocument(docx_file)
    translations = {segment.id: segment.source for segment in source.segments}
    output = tmp_path / "translated.uk.docx"
    source.write(translations, output)
    result = TranslationResult(docx_file, output, source.segments, translations, source.issues)
    write_translation_report(result, Options("en", "uk"), "fixture-model")
    backend = Backend()
    record = {
        "id": "attempt",
        "name": docx_file.name,
        "format": "DOCX",
        "source": str(docx_file),
        "output": str(output),
        "report": str(result.report),
        "status": "done",
        "sourceLanguage": "en",
        "targetLanguage": "uk",
        "model": "fixture-model",
    }
    backend._library.add_history(record)
    backend._history = backend._library.list_history()
    backend.reviewHistory("attempt")
    pump(qapp, lambda: not backend._threads)
    backend.setReviewFilter("all")
    segment = next(s for s in source.segments if s.plain == "Heat meter commissioning")
    backend.selectReviewEntry(segment.id)
    yield backend, result, segment
    backend.cancelReviewEdit()
    pump(qapp, lambda: not backend._threads)
