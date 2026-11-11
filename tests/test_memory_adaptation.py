"""Near matches always require a real editorial request; no paid API is used."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from docx import Document

from forma.core.models import FormaError, Options
from forma.memory.session import MemoryRun
from forma.memory.store import TranslationMemory
from forma.settings.config import Settings
from forma.translation.engine import PROTOCOL_VERSION, TranslationEngine

SOURCE = (
    "Виконавчий комітет Зеленівської громади зобов’язується забезпечити постачання "
    "обладнання до 15 листопада 2026 року відповідно до затверджених умов договору."
)
TARGET = (
    "The executive committee of the Zelenivka community undertakes to ensure delivery "
    "of the equipment by 15 November 2026 in accordance with the approved contract terms."
)


def source_file(path, text):
    doc = Document()
    doc.core_properties.comments = path.name
    doc.add_paragraph(text)
    doc.save(path)
    return path


class AdaptationProvider:
    def __init__(self, *, retain_old_numbers=False):
        self.calls = []
        self.input_tokens = self.output_tokens = 0
        self.retain_old_numbers = retain_old_numbers

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append((instruction, payload))
        self.input_tokens += 10
        self.output_tokens += 5
        values = []
        for segment in payload["segments"]:
            text = TARGET
            if "Березівської" in segment["text"]:
                text = text.replace("Zelenivka", "Berezivka")
            if "20 листопада" in segment["text"] and not self.retain_old_numbers:
                text = text.replace("15 November", "20 November")
            if "не зобов’язується" in segment["text"]:
                text = text.replace("undertakes", "does not undertake")
            values.append({"id": segment["id"], "text": f"<r0>{text}</r0>"})
        return {"segments": values}


@pytest.fixture
def base(tmp_path):
    store = TranslationMemory()
    project = store.create_project("Synthetic communities", "uk", "en")
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="fake-offline-key", model="synthetic", max_parallel_requests=1, parallel_documents=1)
    options = Options("uk", "en", auto_glossary=False, memory_project_id=project.id)
    source = source_file(tmp_path / "template.docx", SOURCE)
    provider = AdaptationProvider()
    initial = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert len(provider.calls) == 2
    return store, settings, options, initial


def test_changed_community_and_date_use_one_mandatory_editor_pass_then_exact_reuse(base, tmp_path):
    store, settings, options, initial = base
    source = source_file(tmp_path / "changed.docx", SOURCE.replace("Зеленівської", "Березівської").replace("15 листопада", "20 листопада"))
    provider = AdaptationProvider()
    adapted = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert len(provider.calls) == 1
    instruction, payload = provider.calls[0]
    assert "bilingual editor" in instruction and "CURRENT source" in instruction
    sid = adapted.segments[0].id
    assert payload["drafts"][sid] == initial.translations[sid]
    reference = payload["memory_references"][sid]
    assert "Зеленівської" in reference["previous_source"]
    assert reference["changed_source_spans"]
    assert "neighboring_context" in payload["segments"][0]
    assert adapted.translations[sid] == "<r0>" + TARGET.replace("Zelenivka", "Berezivka").replace("15 November", "20 November") + "</r0>"
    assert adapted.adapted_segments == 1
    assert adapted.memory_segments == adapted.cached_segments == 0
    assert adapted.input_tokens == 10
    data = json.loads(adapted.report.read_text())
    assert data["adapted_segments"] == 1
    assert data["memory_references"][sid]["source_entry_id"] > 0
    assert not store.snapshot(options.memory_project_id).approved_revision
    current = next(entry for entry in store.list_entries(options.memory_project_id) if entry.target == adapted.translations[sid])
    assert current.status == "machine_final"
    assert current.provenance["kind"] == "adaptation"
    assert current.provenance["source_entry_id"] > 0
    copy = source_file(tmp_path / "repeat.docx", adapted.segments[0].plain)
    repeated = TranslationEngine(settings).translate(copy, tmp_path / "out", options)
    assert repeated.memory_segments == 1 and repeated.adapted_segments == 0
    assert repeated.input_tokens == repeated.output_tokens == 0


def test_old_numeric_values_cannot_survive_adaptation(base, tmp_path):
    _, settings, options, _ = base
    source = source_file(tmp_path / "new-date.docx", SOURCE.replace("15 листопада", "20 листопада"))
    provider = AdaptationProvider(retain_old_numbers=True)
    with pytest.raises(FormaError, match="трьох спроб"):
        TranslationEngine(settings, provider=provider).translate(source, tmp_path / "failed", options)
    assert len(provider.calls) == 3
    assert all("drafts" in payload and "memory_references" in payload for _, payload in provider.calls)
    assert not list((tmp_path / "failed").glob("*.docx"))


def test_changed_negation_is_present_in_full_editor_source_and_final_text(base, tmp_path):
    _, settings, options, _ = base
    source = source_file(tmp_path / "negation.docx", SOURCE.replace("зобов’язується", "не зобов’язується"))
    provider = AdaptationProvider()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert len(provider.calls) == 1 and result.adapted_segments == 1
    assert "не зобов’язується" in provider.calls[0][1]["segments"][0]["text"]
    assert "does not undertake" in next(iter(result.translations.values()))


def test_no_review_never_turns_near_match_into_an_unverified_cache_hit(base, tmp_path):
    _, settings, options, _ = base
    options = replace(options, review=False)
    baseline = source_file(tmp_path / "no-review-base.docx", SOURCE)
    TranslationEngine(settings, provider=AdaptationProvider()).translate(baseline, tmp_path / "out", options)
    changed = source_file(tmp_path / "no-review-changed.docx", SOURCE.replace("Зеленівської", "Березівської"))
    provider = AdaptationProvider()
    result = TranslationEngine(settings, provider=provider).translate(changed, tmp_path / "out", options)
    assert len(provider.calls) == 1
    assert "drafts" not in provider.calls[0][1]
    assert result.adapted_segments == result.memory_segments == 0


def test_queue_builds_one_shared_index_and_adds_newly_completed_references(base, tmp_path, monkeypatch):
    store, settings, options, _ = base
    project = store.create_project("New synthetic queue", "uk", "en")
    options = replace(options, memory_project_id=project.id)
    memory = MemoryRun(options, settings, protocol=PROTOCOL_VERSION, memory=store)
    pool = store.candidate_pool
    loads = []

    def load(*args):
        loads.append(True)
        return pool(*args)

    monkeypatch.setattr(store, "candidate_pool", load)
    first_source = source_file(tmp_path / "fresh-first.docx", SOURCE)
    first_provider = AdaptationProvider()
    TranslationEngine(settings, provider=first_provider, memory_run=memory).translate(first_source, tmp_path / "out", options)
    assert len(first_provider.calls) == 2
    changed_source = source_file(tmp_path / "fresh-changed.docx", SOURCE.replace("Зеленівської", "Березівської"))
    provider = AdaptationProvider()
    changed = TranslationEngine(settings, provider=provider, memory_run=memory).translate(changed_source, tmp_path / "out", options)
    assert len(provider.calls) == 1 and changed.adapted_segments == 1
    assert loads == [True]
