from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from docx import Document
from pypdf import PdfWriter

from forma.config import Settings
from forma.editor import export_reviewed_copy
from forma.engine import TranslationEngine
from forma.models import Cancelled, FormaError, Options
from forma.ocr import convert_scan_pdf
from forma.ooxml import OfficeDocument
from forma.provider import JobControl


@pytest.fixture
def scan(tmp_path):
    path = tmp_path / "synthetic-scan.pdf"
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=595, height=842)
    writer.write(path)
    return path


@pytest.fixture
def local_converter(tmp_path, monkeypatch):
    """Exercise real subprocess IO/cancellation with a bounded, offline stand-in."""
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

    monkeypatch.setattr("forma.ocr.find_scan_converter", lambda _: "offline-converter")
    monkeypatch.setattr("forma.ocr.subprocess.Popen", launch)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "synthetic-private-key")


def prepare(scan, tmp_path, control=None, progress=lambda *_: None):
    return convert_scan_pdf(scan, tmp_path / "out", "en", "", control or JobControl(), progress)


def test_conversion_is_local_preserves_original_and_never_overwrites(scan, tmp_path, local_converter):
    original = scan.read_bytes()
    progress = []
    output = prepare(scan, tmp_path, progress=lambda *event: progress.append(event))
    second = prepare(scan, tmp_path)
    assert second != output and second.exists()
    assert scan.read_bytes() == original
    manifest = json.loads(output.with_suffix(".ocr.json").read_text())
    assert manifest["original_pdf"] == str(scan.resolve())
    assert manifest["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert manifest["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert "synthetic-private-key" not in output.with_suffix(".ocr.json").read_text()
    assert progress[-1][0] == 100
    table = Document(output).tables[0]
    assert table.cell(0, 0)._tc is table.cell(0, 1)._tc


@pytest.mark.parametrize("mode", ["missing", "duplicate", "hash", "bool", "source_changed", "zero_page", "false_blank"])
def test_partial_or_mismatched_conversion_is_never_published(
    scan, tmp_path, local_converter, monkeypatch, mode
):
    monkeypatch.setenv("FORMA_TEST_OCR_MODE", mode)
    with pytest.raises(FormaError):
        prepare(scan, tmp_path)
    assert not list((tmp_path / "out").glob("*"))


def test_cancel_stops_local_subprocess_and_keeps_queue_source(scan, tmp_path, local_converter, monkeypatch):
    monkeypatch.setenv("FORMA_TEST_OCR_MODE", "wait")
    control = JobControl()

    def cancel(percent, _message):
        if percent >= 20:
            control.cancelled.set()

    with pytest.raises(Cancelled):
        prepare(scan, tmp_path, control, cancel)
    assert scan.exists() and not list((tmp_path / "out").glob("*"))


def test_cancel_at_final_validation_does_not_publish(scan, tmp_path, local_converter):
    control = JobControl()

    def cancel(percent, _message):
        if percent == 95:
            control.cancelled.set()

    with pytest.raises(Cancelled):
        prepare(scan, tmp_path, control, cancel)
    assert not list((tmp_path / "out").glob("*"))


def test_diagnostics_never_become_user_facing_document_text(scan, tmp_path, local_converter, monkeypatch):
    monkeypatch.setenv("FORMA_TEST_OCR_MODE", "fail")
    with pytest.raises(FormaError) as error:
        prepare(scan, tmp_path)
    assert "Private recognized" not in str(error.value)
    assert "synthetic-private-key" not in str(error.value)


def test_ocr_cli_prepares_docx_without_loading_provider_settings(
    scan, tmp_path, local_converter, monkeypatch, capsys
):
    from forma.__main__ import main

    monkeypatch.setattr(sys, "argv", [
        "forma", "ocr", str(scan), "--source", "en", "--output", str(tmp_path / "out"),
        "--converter", "offline-converter",
    ])

    def forbidden(*_args, **_kwargs):
        pytest.fail("An explicit local OCR converter must not load provider credentials")

    monkeypatch.setattr("forma.config.Settings.load", forbidden)
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip().endswith(".docx")
    assert "100%" in captured.err
    assert "synthetic-private-key" not in captured.out + captured.err


def test_converted_docx_uses_normal_translation_reports_and_manual_review(
    scan, tmp_path, local_converter, monkeypatch
):
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "data"))
    source = prepare(scan, tmp_path)

    class FakeProvider:
        input_tokens = 5
        output_tokens = 5

        def complete(self, _instruction, payload, _schema, **_kwargs):
            return {
                "segments": [
                    {"id": item["id"], "text": item["text"].replace("Heat meter", "Лічильник тепла").replace("sensor", "датчик")}
                    for item in payload["segments"]
                ]
            }

    result = TranslationEngine(Settings(model="offline-fake", api_key="synthetic-private-key"), provider=FakeProvider()).translate(
        source, tmp_path / "translated", Options(source_language="en", review=True, auto_glossary=False)
    )
    assert result.source == source
    segment = next(s for s in result.segments if "Heat meter 42" in s.plain)
    edited = result.translations[segment.id].replace("Лічильник тепла", "Теплолічильник")
    reviewed = export_reviewed_copy(source, result.output, result.report, {segment.id: edited})
    assert "Теплолічильник 42" in " ".join(s.plain for s in OfficeDocument(reviewed.output).segments)
    table = Document(reviewed.output).tables[0]
    assert table.cell(0, 0)._tc is table.cell(0, 1)._tc
    assert scan.exists() and source.exists() and result.output.exists()
