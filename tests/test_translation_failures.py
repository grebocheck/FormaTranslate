"""Blocked responses remain inspectable without exporting invalid Office files."""

from __future__ import annotations

import json

import pytest
from docx import Document

from forma.core.models import Options, TranslationFailure
from forma.localization.catalog import set_language
from forma.persistence.library import TranslationLibrary
from forma.providers.client import JobControl
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine
from forma.translation.jobs import run_documents


class Provider:
    input_tokens = output_tokens = 0

    def __init__(self, *, repair=False, missing=False):
        self.calls = []
        self.repair = repair
        self.missing = missing

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append(payload)
        segments = []
        for item in payload["segments"]:
            if "Тиск" in item["text"] and self.missing:
                continue
            text = item["text"].replace("Енергія", "Energy").replace("Тиск", "Pressure").replace("кВт", "kW").replace("бар", "bar")
            if "Тиск" in item["text"] and not self.repair:
                text = text.replace("20", "200")
            segments.append({"id": item["id"], "text": text})
        return {"segments": segments}


def case(tmp_path):
    source = tmp_path / "synthetic.docx"
    document = Document()
    document.add_paragraph("Енергія 10 кВт")
    document.add_paragraph("Тиск 20 бар")
    document.save(source)
    settings = Settings(api_key="offline-fake-secret", base_url="http://127.0.0.1:1/v1", model="fake", max_parallel_requests=1, parallel_documents=1)
    options = Options("uk", "en", review=False, auto_glossary=False)
    return source, settings, options


def test_failure_has_actionable_private_diagnostic_and_resume_reuses_valid_work(tmp_path):
    source, settings, options = case(tmp_path)
    provider = Provider()
    output = tmp_path / "output"
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=provider).translate(source, output, options)
    failure = caught.value
    assert "word/document.xml#" not in str(failure)
    assert "абзац" in failure.segments[0].location
    assert not list(output.glob("*.docx"))
    assert failure.report.exists()
    data = json.loads(failure.report.with_suffix(".json").read_text())
    assert data["kind"] == "translation_validation_failure" and data["exported"] is False
    assert data["stage"] == "translation"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["numbers"] == {
        "source": ["20"], "translation": ["200"], "missing": ["20"], "unexpected": ["200"],
    }
    assert "offline-fake-secret" not in failure.report.read_text()
    assert "offline-fake-secret" not in failure.report.with_suffix(".json").read_text()
    library = TranslationLibrary()
    library.save_queue([{"id": "failed", "path": str(source), "format": "DOCX", "status": "error",
                         "diagnosticReport": str(failure.report)}])
    assert TranslationLibrary().load_queue()[0]["diagnosticReport"] == str(failure.report)
    feedback = provider.calls[1]["validation_feedback"][0]
    assert feedback["numbers"]["missing"] == ["20"]
    assert feedback["numbers"]["unexpected"] == ["200"]
    assert [len(call["segments"]) for call in provider.calls] == [2, 1, 1]
    repaired = Provider(repair=True)
    result = TranslationEngine(settings, provider=repaired).translate(source, output, options)
    assert result.cached_segments == 1
    assert len(repaired.calls) == 1 and len(repaired.calls[0]["segments"]) == 1
    assert result.output.exists()


def test_queue_preserves_diagnostic_link_for_failed_document(tmp_path):
    source, settings, options = case(tmp_path)
    events = []
    run_documents(
        [{"id": "synthetic", "path": str(source)}], tmp_path / "output", options, settings,
        JobControl(1), lambda *event: events.append(event),
        engine_factory=lambda settings, control: TranslationEngine(settings, control, provider=Provider()),
    )
    failed = [payload for kind, payload in events if kind == "file_error"]
    assert len(failed) == 1 and failed[0]["diagnosticReport"].endswith(".html")
    assert not [payload for kind, payload in events if kind == "result"]


def test_missing_candidate_has_explicit_diagnostic_instead_of_empty_translation(tmp_path):
    source, settings, options = case(tmp_path)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=Provider(missing=True)).translate(source, tmp_path / "output", options)
    data = json.loads(caught.value.report.with_suffix(".json").read_text())
    assert data["segments"][0]["candidate"] is None
    assert data["segments"][0]["issues"][0]["code"] == "missing_segment"
    assert "Модель не повернула фрагмент." in caught.value.report.read_text()


def test_diagnostic_json_is_canonical_while_html_follows_interface_language(tmp_path):
    source, settings, options = case(tmp_path)
    reports = []
    try:
        for language in ("uk", "en", "pl", "de"):
            set_language(language)
            with pytest.raises(TranslationFailure) as caught:
                TranslationEngine(settings, provider=Provider()).translate(source, tmp_path / "output", options)
            reports.append(json.loads(caught.value.report.with_suffix(".json").read_text()))
            assert f'<html lang="{language}">' in caught.value.report.read_text()
        assert all(report == reports[0] for report in reports)
    finally:
        set_language("uk")
