"""EPUB books retain package structure through real offline app workflows."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from epub_support import epub_parts, make_epub, replace_epub_part
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

VERSIONS = ["2.0", "3.0"]


def translated_items(items, _):
    values = []
    for item in items:
        text = item["text"]
        for original, translated in (("Hello", "Вітаю"), ("world", "світ"),
                                     ("First chapter", "Перший розділ"), ("Second chapter", "Другий розділ"),
                                     ("Contents", "Зміст")):
            text = text.replace(original, translated)
        values.append({**item, "text": text})
    return {"segments": values}


class OfflineProvider(TextProvider):
    input_tokens = output_tokens = 0

    def __init__(self, transform=None, **kwargs):
        super().__init__(transform or translated_items, **kwargs)


def source_book(tmp_path, version):
    return make_epub(tmp_path / "source.epub", version=version, legacy_doctype=version == "2.0")


@pytest.mark.parametrize("version", VERSIONS)
def test_epub_translation_report_manual_copy_and_native_resources(tmp_path, version):
    source = source_book(tmp_path, version)
    before, parts = source.read_bytes(), epub_parts(source)
    provider = OfflineProvider()
    result = TranslationEngine(local_settings(), provider=provider).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    assert result.engine == "forma-epub" and result.extraction_profile == 13
    assert "PRIVATE_" not in json.dumps(provider.calls)
    assert source.read_bytes() == before
    saved, _ = load_report(result.report)
    assert saved.engine == "forma-epub" and saved.extraction_profile == 13
    translated = epub_parts(result.output)
    assert translated.keys() == parts.keys()
    for name in ("mimetype", "META-INF/container.xml", "OPS/package.opf", "OPS/style.css", "OPS/images/cover.png"):
        assert translated[name] == parts[name]
    assert b"PRIVATE_SCRIPT" in translated["OPS/chapter.xhtml"] and b"PRIVATE_CODE" in translated["OPS/chapter.xhtml"]
    assert b"PRIVATE_HIDDEN" in translated["OPS/chapter.xhtml"] and b"PRIVATE_ALT" in translated["OPS/chapter.xhtml"]
    assert "Вітаю" in translated["OPS/chapter.xhtml"].decode()
    assert "Перший розділ" in translated["OPS/toc.ncx"].decode()
    assert b"PRIVATE_TITLE" in translated["OPS/toc.ncx"]
    if version == "3.0":
        assert "Перший розділ" in translated["OPS/nav.xhtml"].decode()
    assert compare_files(source, [result.output])["candidates"][0]["structure_ok"]
    assert "Hello world 25 kW.\nSecond line." in result.report.with_suffix(".html").read_text()
    fragment = next(segment for segment in result.segments if "world" in segment.plain)
    edited = export_reviewed_copy(source, result.output, result.report,
                                 {fragment.id: result.translations[fragment.id].replace("Вітаю", "Привіт")})
    assert "Привіт" in epub_parts(edited.output)["OPS/chapter.xhtml"].decode()
    assert compare_files(source, [edited.output])["candidates"][0]["structure_ok"]
    assert result.output.read_bytes() != edited.output.read_bytes()
    payload = json.loads(result.report.read_text())
    for engine in ("forma-text", "forma-odf", "forma-ooxml"):
        payload["engine"] = engine
        with pytest.raises(FormaError):
            parse_report_data(payload, result.report)
    replace_epub_part(result.output, "OPS/style.css", lambda data: data + b" /* external change */")
    with pytest.raises(FormaError, match="змінився"):
        export_reviewed_copy(source, result.output, result.report, {fragment.id: result.translations[fragment.id]})


@pytest.mark.parametrize("version", VERSIONS)
def test_epub_memory_reuses_exact_source_and_binds_publisher_metadata(tmp_path, version):
    source = source_book(tmp_path, version)
    project = TranslationMemory().create_project("Books", "en", "uk")
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=project.id)
    first = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    assert first.memory_project_id == project.id
    for checkpoint in (data_directory() / "checkpoints").glob("*.json"):
        checkpoint.unlink()
    provider = OfflineProvider()
    second = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert not provider.calls and second.memory_segments == len(first.segments)
    original = open_document(source)
    context = scoped_segment_context(original, original.segments[0])
    assert "PRIVATE_" not in context
    replace_epub_part(source, "OPS/package.opf", lambda data: data.replace(b"PRIVATE_BOOK_TITLE", b"CHANGED_BOOK_TITLE"))
    changed = open_document(source)
    assert changed.segments[0].source == original.segments[0].source
    assert scoped_segment_context(changed, changed.segments[0]) != context
    provider = OfflineProvider()
    changed_result = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert provider.calls and changed_result.memory_segments == 0


@pytest.mark.parametrize("version", VERSIONS)
def test_epub_queue_plain_edit_export_and_restart(qapp, monkeypatch, tmp_path, version):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *args, **kwargs: "")
    providers = []

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            provider = OfflineProvider()
            providers.append(provider)
            super().__init__(settings, control, provider=provider)

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = source_book(tmp_path, version)
    backend, restored = Backend(), None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "out"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        caps = backend.documentCapabilities("EPUB")
        assert caps["manual_export"] and caps["memory"] and caps["comparison"] and caps["recovery"]
        assert not caps["page_preview"] and not caps["memory_import"] and not caps["memory_approval"]
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(row for row in backend.reviewEntries if "Hello world" in row["source"])
        backend.selectReviewEntry(entry["id"])
        assert backend.selectedReviewEntry["canEdit"]
        assert backend.selectedReviewEntry["source"] == "Hello world 25 kW.\nSecond line."
        output = Path(backend.reviewDocument["output"])
        before = output.read_bytes()
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
        assert corrected != output and "Привіт" in epub_parts(corrected)["OPS/chapter.xhtml"].decode()
        assert output.read_bytes() == before
        assert compare_files(source, [corrected])["candidates"][0]["structure_ok"]
        assert sum(len(provider.calls) for provider in providers) == calls
        queued = TranslationLibrary().load_queue()[0]
        assert queued["format"] == "EPUB"
        restored = Backend()
        settle(qapp, restored)
        assert restored.documents[0]["status"] == "done" and restored.documents[0]["report"] == queued["report"]
    finally:
        for instance in (backend, restored):
            if instance is not None:
                instance.cancel()
                settle(qapp, instance)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("corruption", ["numbers", "text_loss"])
def test_epub_invalid_candidate_can_be_corrected_and_resumed(tmp_path, version, corruption):
    source = source_book(tmp_path, version)

    def corrupt(items, _):
        return {"segments": [{**item, "text": item["text"].replace("25", "250") if corruption == "numbers"
                              else re.sub(r"(<r\d+>).*?(</r\d+>)", r"\1!\2", item["text"], flags=re.S)} for item in items]}

    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)
    failed_provider = OfflineProvider(corrupt)
    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=failed_provider).translate(source, tmp_path / "out", options)
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    assert meta["can_resume"] and result.engine == "forma-epub" and result.extraction_profile == 13
    assert any(issue.code == ("numbers" if corruption == "numbers" else "formatting") for issue in result.issues)
    edits = {segment.id: translated_items([{"id": segment.id, "text": segment.source}], 0)["segments"][0]["text"]
             for segment in result.segments}
    save_recovery_edits(report, edits, expected_report_fingerprint=meta["report_fingerprint"])
    provider = OfflineProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "out", options)
    assert resumed.output.is_file()
    attempted = {item["id"] for _, payload, _ in failed_provider.calls for item in payload["segments"]}
    requested_again = {item["id"] for _, payload, _ in provider.calls for item in payload["segments"]}
    assert not attempted & requested_again


@pytest.mark.parametrize("version", VERSIONS)
def test_epub_glossary_and_editorial_requests_exclude_private_metadata(tmp_path, version):
    class EditorialProvider(OfflineProvider):
        def complete(self, instruction, payload, schema, **kwargs):
            if "terms" in schema["properties"]:
                self.calls.append((instruction, copy.deepcopy(payload), kwargs))
                return {"terms": [{"source": "world", "target": "світ"}]}
            return super().complete(instruction, payload, schema, **kwargs)

    source = source_book(tmp_path, version)
    provider = EditorialProvider(review_model="offline-editor")
    settings = replace(local_settings(), review_model="offline-editor")
    result = TranslationEngine(settings, provider=provider).translate(
        source, tmp_path / "out", Options("en", "uk", review=True, auto_glossary=True),
    )
    assert result.output.is_file() and len(provider.calls) >= 3
    assert "PRIVATE_" not in json.dumps(provider.calls)


def test_epub_invalid_package_rejected_before_provider(tmp_path):
    source = source_book(tmp_path, "3.0")
    replace_epub_part(source, "mimetype", lambda _: b"application/not-epub")
    provider = OfflineProvider()
    with pytest.raises(FormaError):
        TranslationEngine(local_settings(), provider=provider).translate(
            source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
        )
    assert not provider.calls


def test_epub_comparison_detects_numeric_text_and_untouched_metadata_changes(tmp_path):
    source = source_book(tmp_path, "3.0")
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    replace_epub_part(result.output, "OPS/chapter.xhtml", lambda data: data.replace(b">42<", b">43<"))
    metrics = compare_files(source, [result.output])["candidates"][0]
    assert not metrics["numbers_preserved"] and not metrics["structure_ok"]
    replace_epub_part(result.output, "OPS/package.opf", lambda data: data.replace(b"PRIVATE_BOOK_TITLE", b"CHANGED_BOOK_TITLE"))
    assert not compare_files(source, [result.output])["candidates"][0]["structure_ok"]
