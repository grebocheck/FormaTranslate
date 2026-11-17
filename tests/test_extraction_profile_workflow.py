"""Corrected extraction surfaces must not reuse indexed legacy hidden text."""
from __future__ import annotations

import json
from zipfile import ZipFile

import pytest
from odf_support import make_hidden_layer_odf
from support import TextProvider, local_settings, pump, settle
from ui_support import forbid_provider_requests

from forma.core.models import FormaError, Options, TranslationFailure
from forma.documents.adapters import open_document
from forma.documents.editor import export_reviewed_copy
from forma.documents.review import load_report
from forma.localization.catalog import LANGUAGE_METADATA, translate_ui_message
from forma.memory.store import TranslationMemory
from forma.persistence.library import TranslationLibrary
from forma.presentation.backend import Backend
from forma.translation.engine import TranslationEngine
from forma.translation.recovery import (
    load_failure_report,
    save_recovery_edits,
    validate_recovery_settings,
)

SOURCE = (
    'Visible prose <span style="display:none">Confidential prose 25</span> ends here.\n\n'
    'Hello world 25.\n'
)


class Provider(TextProvider):
    input_tokens = output_tokens = 0


@pytest.fixture(params=[(".md", 6, 14), (".markdown", 6, 14), (".odt", 8, 15), (".odp", 10, 16)])
def document_case(request, tmp_path):
    suffix, legacy, current = request.param
    source = tmp_path / ("source" + suffix)
    if suffix in {".md", ".markdown"}:
        source.write_text(SOURCE)
        private = "Confidential prose 25"
    else:
        make_hidden_layer_odf(source)
        private = "PRIVATE HIDDEN"
    return source, legacy, current, private


def legacy_run(monkeypatch, source, output, options, provider, profile):
    """Create the exact old format through a fake provider, without changing files."""
    def legacy_open(path, *, extraction_profile=None, check_cancel=lambda: None):
        return open_document(path, extraction_profile=profile, check_cancel=check_cancel)

    with monkeypatch.context() as patch:
        patch.setattr("forma.translation.engine.open_document", legacy_open)
        patch.setattr("forma.memory.engine.open_document", legacy_open)
        return TranslationEngine(local_settings(), provider=provider).translate(source, output, options)


@pytest.mark.parametrize("use_memory", [False, True], ids=["checkpoint", "project-memory"])
def test_current_extraction_does_not_reuse_legacy_work(tmp_path, monkeypatch, use_memory, document_case):
    source, legacy, current, private = document_case
    original = source.read_bytes()
    memory_id = TranslationMemory().create_project("Formats", "en", "uk").id if use_memory else ""
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=memory_id)
    old = legacy_run(monkeypatch, source, tmp_path / "out", options, Provider(), legacy)
    assert old.extraction_profile == legacy
    assert any(private in item.plain for item in old.segments)

    provider = Provider()
    new = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "out", options)

    assert new.extraction_profile == current and new.cached_segments == new.memory_segments == 0
    assert provider.calls
    assert private not in json.dumps(provider.calls)
    if source.suffix in {".md", ".markdown"}:
        assert all(item.id.startswith("text-v14:") for item in new.segments)
        assert '<span style="display:none">Confidential prose 25</span>' in new.output.read_text()
    else:
        with ZipFile(new.output) as package:
            assert private.encode() in package.read("content.xml")
    assert source.read_bytes() == original


def test_legacy_completed_report_remains_editable(tmp_path, monkeypatch, document_case):
    source, legacy, _, _ = document_case
    original = source.read_bytes()
    options = Options("en", "uk", review=False, auto_glossary=False)
    old = legacy_run(monkeypatch, source, tmp_path / "out", options, Provider(), legacy)
    before = old.output.read_bytes(), old.report.read_bytes()
    fragment = next(item for item in old.segments if "Hello" in item.plain)

    edited = export_reviewed_copy(source, old.output, old.report, {
        fragment.id: old.translations[fragment.id].replace("Вітаю", "Привіт"),
    })

    assert edited.extraction_profile == legacy
    assert load_report(edited.report)[0].extraction_profile == legacy
    assert "Привіт" in edited.translations[fragment.id]
    assert (old.output.read_bytes(), old.report.read_bytes()) == before
    assert source.read_bytes() == original


def test_legacy_failure_is_readable_without_resuming_hidden_surface(qapp, tmp_path, monkeypatch, document_case):
    source, legacy, current, private = document_case
    options = Options("en", "uk", review=False, auto_glossary=False)

    def wrong_numbers(segments, _):
        return {"segments": [{**item, "text": item["text"].replace("25", "250")} for item in segments]}

    with pytest.raises(TranslationFailure) as caught:
        legacy_run(monkeypatch, source, tmp_path / "out", options, Provider(wrong_numbers), legacy)
    failure = caught.value
    before = failure.report.read_bytes()
    result, _, meta = load_failure_report(failure.report)

    assert result.extraction_profile == legacy and result.segments
    assert meta["can_resume"] is False and meta["retired_extraction"]
    with pytest.raises(FormaError):
        validate_recovery_settings(meta, local_settings())
    segment = result.segments[0]
    with pytest.raises(FormaError):
        save_recovery_edits(failure.report, {segment.id: segment.source},
                            expected_report_fingerprint=meta["report_fingerprint"])
    assert failure.report.read_bytes() == before

    provider = Provider()
    new = TranslationEngine(local_settings(), provider=provider).translate(source, tmp_path / "new", options)
    assert new.extraction_profile == current
    assert private not in json.dumps(provider.calls)

    # Real presentation state must also prohibit edits/resumption and avoid
    # promising that the newly versioned run will reuse an older indexed cache.
    forbid_provider_requests(monkeypatch)
    monkeypatch.setattr(Backend, "_read_settings", lambda _: local_settings())
    monkeypatch.setattr(Backend, "_refresh_worker_availability", lambda _: None)
    TranslationLibrary().save_queue([{
        "id": "legacy-failure", "path": str(source), "name": source.name,
        "format": source.suffix[1:].upper(), "status": "error", "statusText": "Потрібна увага",
        "diagnosticReport": str(failure.report), "output": "", "report": "",
    }])
    backend = Backend()
    try:
        settle(qapp, backend)
        backend.activateReview()
        pump(qapp, lambda: bool(backend.reviewEntries))
        fragment = next(item for item in backend.reviewEntries if item["kind"] == "segment")
        backend.selectReviewEntry(fragment["id"])
        monkeypatch.setattr(backend, "_start_documents", lambda *a, **kw: pytest.fail("legacy recovery must not start"))
        reason = (
            "Цей звіт доступний лише для перегляду. Запустіть новий переклад, "
            "щоб застосувати виправлену обробку прихованого тексту."
        )
        for language in LANGUAGE_METADATA:
            backend.setUiLanguage(language)
            assert backend.reviewDocument["recovery"] and not backend.reviewDocument["canResume"]
            assert not backend.selectedReviewEntry["canEdit"]
            assert backend.reviewDocument["recoveryReason"] == translate_ui_message(reason)
            assert backend.selectedReviewEntry["editReason"] == translate_ui_message(reason)
            backend.beginReviewEdit()
            backend.exportReviewEdits()
            assert not backend.reviewEditing and not backend._review_saving
        assert failure.report.read_bytes() == before
    finally:
        backend.cancel()
        settle(qapp, backend)
