"""Subtitles retain timing and metadata through real offline app workflows."""
from __future__ import annotations

import codecs
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from subtitle_support import CASES, write_source
from support import TextProvider, local_settings, pump, settle

from forma.core.models import FormaError, Options, TranslationFailure
from forma.documents.adapters import open_document
from forma.documents.comparison import compare_files
from forma.documents.editor import export_reviewed_copy
from forma.documents.review import load_report, parse_report_data
from forma.memory.context import scoped_segment_context
from forma.memory.store import TranslationMemory
from forma.persistence.library import TranslationLibrary
from forma.presentation.backend import Backend
from forma.settings.config import data_directory
from forma.translation.engine import TranslationEngine
from forma.translation.recovery import load_failure_report, save_recovery_edits


class OfflineProvider(TextProvider):
    input_tokens = output_tokens = 0


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_subtitle_translation_manual_copy_report_and_protected_structure(tmp_path, suffix, profile, text):
    source = write_source(tmp_path, suffix, text)
    before = source.read_bytes()
    provider = OfflineProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    assert result.engine == "forma-text" and result.extraction_profile == profile and len(result.segments) == 2
    assert "PRIVATE_" not in json.dumps(provider.calls) and "Private comment" not in json.dumps(provider.calls)
    assert source.read_bytes() == before
    translated = result.output.read_bytes()
    assert translated.startswith(codecs.BOM_UTF8) and translated.count(b"\r\n") == before.count(b"\r\n")
    assert "Вітаю" in translated.decode("utf-8-sig")
    original = open_document(source)
    assert original.structure_signature() == open_document(result.output).structure_signature()
    assert compare_files(source, [result.output])["candidates"][0]["structure_ok"]
    assert "Hello world 25 kW.\nSecond caption line." in result.report.with_suffix(".html").read_text()
    saved, _ = load_report(result.report)
    assert saved.engine == "forma-text" and saved.extraction_profile == profile
    fragment = next(item for item in result.segments if "world" in item.plain)
    edited = export_reviewed_copy(source, result.output, result.report,
                                 {fragment.id: result.translations[fragment.id].replace("Вітаю", "Привіт")})
    assert edited.engine == "forma-text" and edited.extraction_profile == profile
    assert "Привіт" in edited.output.read_text(encoding="utf-8-sig")
    assert result.output.read_bytes() == translated
    assert compare_files(source, [edited.output])["candidates"][0]["structure_ok"]
    payload = json.loads(result.report.read_text())
    payload["engine"] = "forma-ooxml"
    with pytest.raises(FormaError):
        parse_report_data(payload, result.report)
    replacement = b"00:00:02,250" if suffix == ".srt" else b"00:02.250"
    existing = b"00:00:01,250" if suffix == ".srt" else b"00:01.250"
    result.output.write_bytes(translated.replace(existing, replacement))
    with pytest.raises(FormaError, match="змінився"):
        export_reviewed_copy(source, result.output, result.report, {fragment.id: result.translations[fragment.id]})


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_subtitle_memory_is_exact_and_binds_source_timings(tmp_path, suffix, profile, text):
    source = write_source(tmp_path, suffix, text)
    project = TranslationMemory().create_project("Subtitles", "en", "uk")
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=project.id)
    first = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    for checkpoint in (data_directory() / "checkpoints").glob("*.json"):
        checkpoint.unlink()
    provider = OfflineProvider()
    second = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert not provider.calls and second.memory_segments == len(first.segments)
    original = open_document(source)
    context = scoped_segment_context(original, original.segments[0])
    assert "PRIVATE_" not in context
    source.write_bytes(source.read_bytes().replace(b"01,250", b"02,250").replace(b"01.250", b"02.250"))
    changed = open_document(source)
    assert changed.segments[0].source == original.segments[0].source
    assert scoped_segment_context(changed, changed.segments[0]) != context
    provider = OfflineProvider()
    translated = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert provider.calls and translated.memory_segments == 0


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_subtitle_queue_review_and_restart(qapp, monkeypatch, tmp_path, suffix, profile, text):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *a, **k: "")

    providers = []

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            provider = OfflineProvider()
            providers.append(provider)
            super().__init__(settings, control, provider=provider)

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = write_source(tmp_path, suffix, text)
    backend, restored = Backend(), None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "out"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        assert backend.segments[0]["source"] == "Hello world 25 kW.\nSecond caption line."
        caps = backend.documentCapabilities(suffix)
        assert not caps["page_preview"] and not caps["memory_import"] and not caps["memory_approval"]
        assert caps["manual_export"] and caps["memory"] and caps["recovery"] and caps["comparison"]
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(item for item in backend.reviewEntries if item["kind"] == "segment")
        backend.selectReviewEntry(entry["id"])
        assert backend.selectedReviewEntry["canEdit"]
        assert backend.selectedReviewEntry["source"] == "Hello world 25 kW.\nSecond caption line."
        row = TranslationLibrary().load_queue()[0]
        assert row["format"] == suffix[1:].upper()
        output = Path(backend.reviewDocument["output"])
        previous = output.read_bytes()
        calls = sum(len(provider.calls) for provider in providers)
        backend.beginReviewEdit()
        draft = backend.reviewDraftText.replace("Вітаю", "Привіт")
        assert draft.count("\n") == 1 and "Привіт" in draft
        backend.updateReviewDraft(draft)
        assert backend.applyReviewEdit(), backend.reviewEditError
        backend.exportReviewEdits()
        pump(qapp, lambda: not backend.reviewSaving)
        settle(qapp, backend)
        corrected = Path(backend.reviewDocument["output"])
        assert corrected != output and corrected.is_file()
        assert "Привіт" in corrected.read_text(encoding="utf-8-sig")
        assert output.read_bytes() == previous
        assert compare_files(source, [corrected])["candidates"][0]["structure_ok"]
        assert sum(len(provider.calls) for provider in providers) == calls
        restored = Backend()
        settle(qapp, restored)
        assert restored.documents[0]["status"] == "done" and restored.documents[0]["report"] == row["report"]
    finally:
        for instance in (backend, restored):
            if instance is not None:
                instance.cancel()
                settle(qapp, instance)


@pytest.mark.parametrize("suffix,profile,text", CASES)
@pytest.mark.parametrize("corruption", ["numbers", "newline"])
def test_subtitle_invalid_candidate_can_be_corrected_and_resumed(tmp_path, suffix, profile, text, corruption):
    source = write_source(tmp_path, suffix, text)
    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)

    def corrupt(items, _):
        before, after = ("25", "250") if corruption == "numbers" else ("Hello", "Hello\n\n")
        return {"segments": [{**item, "text": item["text"].replace(before, after)} for item in items]}

    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=OfflineProvider(corrupt)).translate(source, tmp_path / "out", options)
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    assert meta["can_resume"] and result.engine == "forma-text" and result.extraction_profile == profile
    assert any(issue.code == ("numbers" if corruption == "numbers" else "formatting") for issue in result.issues)
    edits = {item.id: result.translations[item.id].replace("250", "25").replace("Hello\n\n", "Вітаю")
             for item in result.segments}
    save_recovery_edits(report, edits, expected_report_fingerprint=meta["report_fingerprint"])
    provider = OfflineProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "out", options)
    assert resumed.output.is_file() and not provider.calls


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_subtitle_glossary_and_editorial_requests_exclude_metadata(tmp_path, suffix, profile, text):
    class EditorialProvider(OfflineProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            if "terms" in schema["properties"]:
                self.calls.append((instruction, copy.deepcopy(payload), kwargs))
                return {"terms": [{"source": "world", "target": "світ"}]}
            return super().complete(instruction, payload, schema, **kwargs)

    source = write_source(tmp_path, suffix, text)
    provider = EditorialProvider(review_model="offline-editor")
    settings = replace(local_settings(), review_model="offline-editor")
    result = TranslationEngine(settings, provider=provider).translate(
        source, tmp_path / "out", Options("en", "uk", review=True, auto_glossary=True),
    )
    assert result.output.is_file() and len(provider.calls) >= 3
    assert "PRIVATE_" not in json.dumps(provider.calls) and "Private comment" not in json.dumps(provider.calls)


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_subtitle_invalid_source_is_rejected_before_provider(tmp_path, suffix, profile, text):
    source = write_source(tmp_path, suffix, text.replace("04,500", "00,500").replace("04.500", "00.500"))
    provider = OfflineProvider()
    with pytest.raises(FormaError):
        TranslationEngine(local_settings(), provider=provider).translate(
            source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
        )
    assert not provider.calls


@pytest.mark.parametrize("suffix", [".srt", ".vtt"])
def test_subtitle_manual_copy_replaces_resolved_readability_warnings(tmp_path, suffix):
    body = "Hello world with a caption that is deliberately long enough to need a local reading speed warning."
    translated = "Вітаю, світе! Цю довгу репліку потрібно перевірити під час відтворення відео перед використанням."
    header = "1\n00:00:00,000 --> 00:00:02,000\n" if suffix == ".srt" else "WEBVTT\n\n00:00.000 --> 00:02.000\n"
    source = write_source(tmp_path, suffix, header + body + "\n")

    def transform(items, _):
        return {"segments": [{**item, "text": item["text"].replace(body, translated)} for item in items]}

    provider = OfflineProvider(transform)
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    codes = open_document(source).layout_issue_codes
    assert {"subtitle_line_length", "subtitle_reading_speed"} <= {issue.code for issue in result.issues}
    assert len(provider.calls) == 1
    previous = result.output.read_bytes()
    edited = export_reviewed_copy(source, result.output, result.report, {result.segments[0].id: "<r0>Привіт.</r0>"})
    assert not any(issue.code in codes for issue in edited.issues)
    assert result.output.read_bytes() == previous and len(provider.calls) == 1
    saved, _ = load_report(edited.report)
    assert not any(issue.code in codes for issue in saved.issues)


@pytest.mark.parametrize("suffix", [".srt", ".vtt"])
@pytest.mark.parametrize("corruption", ["blank_line", "lost_text"])
def test_subtitle_line_loss_fails_with_recoverable_report_before_publication(tmp_path, suffix, corruption):
    header = "1\n00:00:00,000 --> 00:00:02,000\n" if suffix == ".srt" else "WEBVTT\n\n00:00.000 --> 00:02.000\n"
    body = "Hello world\nSecond line" if corruption == "blank_line" else "Hello world"
    source = write_source(tmp_path, suffix, header + body + "\n")

    def corrupt(items, _):
        replacement = "" if corruption == "blank_line" else "!"
        return {"segments": [{**item, "text": item["text"].replace("Hello world", replacement)} for item in items]}

    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=OfflineProvider(corrupt)).translate(source, tmp_path / "out", options)
    assert not list((tmp_path / "out").glob("*" + suffix))
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    assert meta["can_resume"] and any(issue.code == "formatting" for issue in result.issues)
    segment = result.segments[0]
    corrected = segment.source.replace("Hello world", "Вітаю світ").replace("Second line", "Другий рядок")
    save_recovery_edits(report, {segment.id: corrected}, expected_report_fingerprint=meta["report_fingerprint"])
    provider = OfflineProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "out", options)
    assert resumed.output.is_file() and not provider.calls
