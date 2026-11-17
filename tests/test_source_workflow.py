"""Real native text adapters use offline translation, review and persistence."""
from __future__ import annotations

import json

import pytest
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


CASES = [
    (".txt", 5, "Hello world 25 kW\r\n\r\nOther text.\r\n"),
    (".md", 14, "---\ntitle: Protected metadata\n---\n# Hello world\n\nHello **world** 25 kW and `code()` [link](https://example.invalid).\n"),
    (".html", 7, '<!DOCTYPE html><html><head><meta charset="utf-8"><script>private_code()</script></head><body><p>Hello <b>world</b> 25 kW.</p></body></html>'),
]


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_native_translation_report_manual_edit_and_structure(tmp_path, suffix, profile, text):
    source = tmp_path / ("source" + suffix)
    source.write_bytes(text.encode())
    before = source.read_bytes()
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "results", Options("en", "uk", review=False, auto_glossary=False),
    )
    assert result.engine == "forma-text" and result.extraction_profile == profile
    assert source.read_bytes() == before
    saved, _ = load_report(result.report)
    assert saved.engine == "forma-text" and saved.extraction_profile == profile
    payload = json.loads(result.report.read_text())
    assert payload["source"] == str(source) and payload["output"] == str(result.output)
    assert compare_files(source, [result.output])["candidates"][0]["structure_ok"]
    fragment = next(item for item in result.segments if "Hello" in item.plain)
    edited = export_reviewed_copy(source, result.output, result.report,
                                 {fragment.id: result.translations[fragment.id].replace("Вітаю", "Привіт")})
    assert "Привіт" in edited.output.read_text()
    assert "Вітаю" in result.output.read_text()
    assert edited.engine == "forma-text" and edited.extraction_profile == profile
    assert compare_files(source, [edited.output])["candidates"][0]["structure_ok"]
    assert "private_code" not in " ".join(item.plain + item.context for item in result.segments)


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_native_memory_reuses_identical_source_and_binds_all_document_content(tmp_path, suffix, profile, text):
    source = tmp_path / ("source" + suffix)
    source.write_text(text, newline="")
    memory = TranslationMemory()
    project = memory.create_project("Native documents", "en", "uk")
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=project.id)
    first = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    assert first.memory_project_id == project.id
    for checkpoint in (data_directory() / "checkpoints").glob("*.json"):
        checkpoint.unlink()
    provider = OfflineProvider()
    second = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)
    assert not provider.calls and second.memory_segments == len(second.segments)
    original = open_document(source)
    previous_context = scoped_segment_context(original, original.segments[0])
    # A conservative full-source binding prevents reuse under changed hidden/code context.
    source.write_text(text + ("\n<!-- changed -->" if suffix == ".html" else "\nChanged ending\n"), newline="")
    changed = open_document(source)
    assert scoped_segment_context(changed, changed.segments[0]) != previous_context


@pytest.mark.parametrize("suffix,profile,text", CASES)
def test_native_queue_review_and_restart(qapp, monkeypatch, tmp_path, suffix, profile, text):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *a, **k: "")

    class OfflineEngine(TranslationEngine):
        def __init__(self, settings, control):
            super().__init__(settings, control, provider=OfflineProvider())

    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", OfflineEngine)
    source = tmp_path / ("source" + suffix)
    source.write_text(text, newline="")
    backend, restored = Backend(), None
    try:
        settle(qapp, backend)
        backend.setOutputFolder(str(tmp_path / "out"))
        backend.addFiles([source.as_uri()])
        pump(qapp, lambda: backend.documents and backend.documents[0]["status"] == "ready")
        assert not backend.documentCapabilities(suffix)["page_preview"]
        backend.start("en", "uk", "Загальна тематика", False, False)
        pump(qapp, lambda: not backend.busy and backend.documents[0]["status"] == "done")
        settle(qapp, backend)
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        entry = next(item for item in backend.reviewEntries if item["kind"] == "segment")
        backend.selectReviewEntry(entry["id"])
        assert backend.selectedReviewEntry["canEdit"]
        row = TranslationLibrary().load_queue()[0]
        assert row["format"] == suffix[1:].upper()
        restored = Backend()
        settle(qapp, restored)
        assert restored.documents[0]["status"] == "done"
        assert restored.documents[0]["report"] == row["report"]
    finally:
        for instance in (backend, restored):
            if instance is not None:
                instance.cancel()
                settle(qapp, instance)


def test_native_failed_number_validation_can_be_corrected_and_resumed(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Hello 25 kW\n")
    settings, options = local_settings(), Options("en", "uk", review=False, auto_glossary=False)

    def corrupt(items, _):
        return {"segments": [{**item, "text": item["text"].replace("25", "250")} for item in items]}

    with pytest.raises(TranslationFailure) as caught:
        TranslationEngine(settings, provider=OfflineProvider(corrupt)).translate(source, tmp_path / "out", options)
    report = caught.value.report.with_suffix(".json")
    result, _, meta = load_failure_report(report)
    assert meta["can_resume"] and result.engine == "forma-text" and result.extraction_profile == 5
    edits = {item.id: result.translations[item.id].replace("250", "25") for item in result.segments}
    save_recovery_edits(report, edits, expected_report_fingerprint=meta["report_fingerprint"])
    provider = OfflineProvider()
    engine = TranslationEngine(settings, provider=provider)
    engine.expected_recovery_checkpoint = meta["checkpoint_id"]
    resumed = engine.translate(source, tmp_path / "out", options)
    assert resumed.output.is_file() and not provider.calls


def test_native_manual_export_rejects_stale_output_and_rebound_engine(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Hello world\n")
    result = TranslationEngine(local_settings(), provider=OfflineProvider()).translate(
        source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
    )
    payload = json.loads(result.report.read_text())
    payload["engine"] = "forma-ooxml"
    with pytest.raises(FormaError):
        parse_report_data(payload, result.report)
    result.output.write_text(result.output.read_text() + "External edit\n")
    with pytest.raises(FormaError, match="змінився"):
        export_reviewed_copy(source, result.output, result.report, {result.segments[0].id: "<r0>Привіт світ</r0>"})


def test_numeric_only_text_is_rejected_before_provider_work(tmp_path):
    source = tmp_path / "values.txt"
    source.write_text("25\n42\n")
    provider = OfflineProvider()
    with pytest.raises(FormaError, match="немає підтримуваного тексту"):
        TranslationEngine(local_settings(), provider=provider).translate(
            source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False),
        )
    assert not provider.calls


@pytest.mark.parametrize("suffix,text", [
    (".txt", "Hello\n25\n"), (".md", "# Hello\n\n25\n"),
    (".html", "<p>Hello</p><p>25</p>"),
])
def test_comparison_includes_numeric_only_visible_text(tmp_path, suffix, text):
    source, target = tmp_path / ("source" + suffix), tmp_path / ("target" + suffix)
    source.write_text(text)
    target.write_text(text.replace("25", "26"))
    result = compare_files(source, [target])["candidates"][0]
    assert not result["numbers_preserved"] and not result["structure_ok"]
