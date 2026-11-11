from __future__ import annotations

import hashlib
import json
import sys

import pytest
from docx import Document

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.editor import export_reviewed_copy
from forma.documents.ocr import convert_scan_pdf
from forma.documents.ooxml import OfficeDocument
from forma.providers.client import JobControl
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


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

    monkeypatch.setattr("forma.settings.config.Settings.load", forbidden)
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
