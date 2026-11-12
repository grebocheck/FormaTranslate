"""Blocked responses remain inspectable without exporting invalid Office files."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from docx import Document

from forma.core.models import FormaError, Options, TranslationFailure
from forma.localization.catalog import set_language
from forma.persistence.library import TranslationLibrary
from forma.persistence.review_store import ReviewStore
from forma.providers.client import JobControl
from forma.settings.config import Settings, data_directory
from forma.translation.engine import TranslationEngine
from forma.translation.jobs import run_documents
from forma.translation.recovery import (
    load_failure_report,
    save_recovery_edits,
    validate_recovery_settings,
)


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


def test_manual_recovery_reuses_paid_work_after_restart_without_another_request(tmp_path):
    source, settings, options = case(tmp_path)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=Provider()).translate(source, tmp_path / "output", options)
    report = caught.value.report.with_suffix(".json")
    original = report.read_bytes()
    result, effective, meta = load_failure_report(report)
    assert meta["can_resume"] and effective == options
    assert meta["report_fingerprint"] == ReviewStore().load(report)["reportFingerprint"]
    validate_recovery_settings(meta, settings)
    segment = result.segments[0]
    invalid = result.translations[segment.id]
    with pytest.raises(FormaError, match="числа"):
        save_recovery_edits(report, {segment.id: invalid}, expected_report_fingerprint=meta["report_fingerprint"])
    assert not list((data_directory() / "recovery").glob("*.json"))
    corrected = invalid.replace("200", "20")
    assert save_recovery_edits(report, {segment.id: corrected}, expected_report_fingerprint=meta["report_fingerprint"]) == 1
    provider = Provider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "output", Options(**meta["requested_options"]))
    assert not provider.calls and resumed.cached_segments == 2
    assert [paragraph.text for paragraph in Document(resumed.output).paragraphs] == ["Energy 10 kW", "Pressure 20 bar"]
    assert report.read_bytes() == original


def test_recovery_rejects_stale_reports_sources_and_changed_model_before_any_api(tmp_path):
    source, settings, options = case(tmp_path)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=Provider()).translate(source, tmp_path / "output", options)
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    corrections = {result.segments[0].id: result.translations[result.segments[0].id].replace("200", "20")}
    original = report.read_bytes()
    report.write_bytes(original + b"\n")
    with pytest.raises(FormaError, match="звіт змінився"):
        save_recovery_edits(report, corrections, expected_report_fingerprint=meta["report_fingerprint"])
    report.write_bytes(original)
    with pytest.raises(FormaError, match="Налаштування"):
        validate_recovery_settings(meta, settings.with_overrides(model="another-model"))
    tampered = {**meta, "options": {**meta["options"], "domain": "A different document policy"}}
    with pytest.raises(FormaError, match="Налаштування"):
        validate_recovery_settings(tampered, settings)
    provider = Provider(repair=True)
    engine = TranslationEngine(settings.with_overrides(model="another-model"), provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    with pytest.raises(FormaError, match="Налаштування"):
        engine.translate(source, tmp_path / "output", options)
    assert not provider.calls
    changed = Document(source)
    changed.paragraphs[1].text = "Тиск 30 бар"
    changed.save(source)
    with pytest.raises(FormaError, match="Оригінал"):
        save_recovery_edits(report, corrections, expected_report_fingerprint=meta["report_fingerprint"])
    assert not list((data_directory() / "recovery").glob("*.json"))


def test_manually_corrected_review_failure_is_final_and_does_not_reopen_editor(tmp_path):
    source, settings, original_options = case(tmp_path)
    document = Document(source)
    document.paragraphs[1].text += " потребує окремої перевірки фахівцем перед початком наступного робочого випробування обладнання"
    document.save(source)
    options = replace(original_options, review=True)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=Provider()).translate(source, tmp_path / "output", options)
    result, _, meta = load_failure_report(caught.value.report)
    assert json.loads(result.report.read_text())["stage"] == "review"
    sid = result.segments[0].id
    # Remaining source prose is a visible warning, not a reason to rebill a
    # deliberate, hard-valid manual correction through the automatic editor.
    corrected = result.translations[sid].replace("200", "20")
    save_recovery_edits(result.report, {sid: corrected}, expected_report_fingerprint=meta["report_fingerprint"])
    provider = Provider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "output", options)
    assert not provider.calls
    assert "Pressure 20" in Document(resumed.output).paragraphs[1].text
    assert any(issue.severity == "warning" and issue.segment_id == sid for issue in resumed.issues)


def test_recovery_never_trusts_damaged_saved_markup_or_an_incompatible_profile(tmp_path):
    source, settings, options = case(tmp_path)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=Provider()).translate(source, tmp_path / "output", options)
    result, _, meta = load_failure_report(caught.value.report)
    sid = result.segments[0].id
    correction = {sid: result.translations[sid].replace("200", "20")}
    report = result.report
    original = report.read_bytes()
    incompatible = json.loads(original)
    incompatible["recovery"]["extraction_profile"] = 2
    report.write_text(json.dumps(incompatible))
    changed_meta = load_failure_report(report)[2]
    with pytest.raises(FormaError, match="Оригінал"):
        save_recovery_edits(report, correction, expected_report_fingerprint=changed_meta["report_fingerprint"])
    report.write_bytes(original)
    save_recovery_edits(report, correction, expected_report_fingerprint=meta["report_fingerprint"])
    saved = data_directory() / "recovery" / f"{meta['checkpoint_id']}.json"
    damaged = json.loads(saved.read_text())
    damaged["edits"][sid]["target"] = correction[sid].replace("20", "200")
    saved.write_text(json.dumps(damaged))
    damaged_bytes = saved.read_bytes()
    provider = Provider(repair=True)
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    with pytest.raises(FormaError, match="числа"):
        engine.translate(source, tmp_path / "output", options)
    assert not provider.calls
    with pytest.raises(FormaError, match="числа"):
        save_recovery_edits(report, correction, expected_report_fingerprint=meta["report_fingerprint"])
    assert saved.read_bytes() == damaged_bytes
