"""Independent integration boundaries using real DOCX packages and a fake provider."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from forma.core.models import FormaError, Options
from forma.documents.ooxml import OfficeDocument
from forma.memory.context import scoped_segment_context, segment_role
from forma.memory.session import MemoryRun
from forma.memory.store import TranslationMemory, exact_fingerprint
from forma.settings.config import Settings, data_directory
from forma.translation.engine import (
    PROTOCOL_VERSION,
    TranslationEngine,
    checkpoint_fingerprint,
    file_digest,
)


class Provider:
    input_tokens = 12
    output_tokens = 8

    def __init__(self, word="Енергія", *, keep_bad_draft=False):
        self.calls = []
        self.word = word
        self.keep_bad_draft = keep_bad_draft

    def complete(self, instruction, payload, _schema, **kwargs):
        self.calls.append((instruction, deepcopy(payload), kwargs))
        return {"segments": [
            {
                "id": segment["id"],
                "text": payload["drafts"][segment["id"]]
                if self.keep_bad_draft and "drafts" in payload
                else segment["text"].replace("Energy", self.word).replace("Context", "Контекст").replace("Baseline", "Базовий"),
            }
            for segment in payload["segments"]
        ]}


@pytest.fixture
def case(tmp_path):
    memory = TranslationMemory(tmp_path / "memory.sqlite3")
    project = memory.create_project("Synthetic boundary project", "en", "uk")
    settings = Settings(
        api_key="fake-offline-key", base_url="http://127.0.0.1:1/v1", model="synthetic-model",
        max_parallel_requests=1, parallel_documents=1,
    )
    options = Options(source_language="en", target_language="uk", review=False, auto_glossary=False, memory_project_id=project.id)
    return SimpleNamespace(memory=memory, project=project, settings=settings, options=options, root=tmp_path)


def make_doc(path, text="Energy output 12.5 kWh", *, style=None, split=False, list_level=None):
    document = Document()
    paragraph = document.add_paragraph(style=style)
    if split:
        paragraph.add_run("Energy").bold = True
        paragraph.add_run(text.removeprefix("Energy")).italic = True
    else:
        paragraph.add_run(text)
    if list_level is not None:
        numbering = OxmlElement("w:numPr")
        level = OxmlElement("w:ilvl")
        level.set(qn("w:val"), str(list_level))
        identifier = OxmlElement("w:numId")
        identifier.set(qn("w:val"), "42")
        numbering.extend([level, identifier])
        paragraph._p.get_or_add_pPr().append(numbering)
    document.save(path)
    return path


def run_for(case, *, options=None, settings=None):
    return MemoryRun(options or case.options, settings or case.settings, protocol=PROTOCOL_VERSION, memory=case.memory)


def translate(case, source, provider, *, options=None, settings=None, run=None):
    options, settings = options or case.options, settings or case.settings
    return TranslationEngine(settings, provider=provider, memory_run=run or run_for(case, options=options, settings=settings)).translate(
        source, case.root / "output", options,
    )


def entry_for(source, run):
    document = OfficeDocument(source)
    segment = next(item for item in document.segments if "Energy" in item.plain)
    context, role = scoped_segment_context(document, segment), segment_role(document, segment)
    return {
        "key": exact_fingerprint(run.policy, segment.source, context, role),
        "source_markup": segment.source, "context": context, "role": role, "policy": run.policy,
    }


def request_sources(provider):
    return [segment["text"] for _instruction, payload, _kwargs in provider.calls for segment in payload["segments"]]


def test_changed_numeric_or_negated_source_never_reuses_old_exact_entry(case):
    first = make_doc(case.root / "first.docx", "Energy is required at 12.5 kWh")
    translate(case, first, Provider())
    for index, text in enumerate(("Energy is required at 15.5 kWh", "Energy is not required at 12.5 kWh")):
        source = make_doc(case.root / f"changed-{index}.docx", text)
        provider = Provider()
        result = translate(case, source, provider)
        assert provider.calls and result.memory_segments == 0
        assert text in "".join(request_sources(provider))


def test_full_neighbor_context_beyond_prompt_excerpt_changes_exact_reuse(case):
    documents, records = [], []
    for index, ending in enumerate(("permission is required", "permission is not required")):
        path = case.root / f"context-{index}.docx"
        document = Document()
        document.add_paragraph("Context " * 150 + ending)
        document.add_paragraph("Energy output 12.5 kWh")
        document.save(path)
        office = OfficeDocument(path)
        segment = next(item for item in office.segments if "Energy" in item.plain)
        documents.append((path, segment))
        records.append(entry_for(path, run_for(case)))
    assert documents[0][1].context == documents[1][1].context
    assert records[0]["context"] != records[1]["context"]
    translate(case, documents[0][0], Provider())
    provider = Provider()
    translate(case, documents[1][0], provider)
    assert any("Energy output" in value for value in request_sources(provider))


@pytest.mark.parametrize("change", ["marker_partition", "paragraph_role", "list_level"])
def test_same_plain_text_with_different_office_role_or_marker_partition_misses(case, change):
    first = make_doc(case.root / "ordinary.docx", list_level=0 if change == "list_level" else None)
    second = make_doc(
        case.root / "different.docx", split=change == "marker_partition",
        style="Heading 1" if change == "paragraph_role" else None,
        list_level=2 if change == "list_level" else None,
    )
    assert OfficeDocument(first).segments[0].plain == OfficeDocument(second).segments[0].plain
    before, after = entry_for(first, run_for(case)), entry_for(second, run_for(case))
    assert before["key"] != after["key"]
    translate(case, first, Provider())
    provider = Provider()
    result = translate(case, second, provider)
    assert provider.calls and result.memory_segments == 0


@pytest.mark.parametrize("change", [{"domain": "Different engineering domain"}, {"instructions": "Use concise engineering prose."}])
def test_changed_project_policy_uses_fresh_machine_generation(case, change):
    source = make_doc(case.root / "policy.docx")
    translate(case, source, Provider())
    options = replace(case.options, **change)
    provider = Provider()
    result = translate(case, source, provider, options=options)
    assert provider.calls and result.memory_segments == 0 and result.cached_segments == 0


def test_changed_project_glossary_has_new_policy_while_active_snapshot_stays_frozen(case):
    source = make_doc(case.root / "glossary.docx")
    active = run_for(case)
    first = translate(case, source, Provider(), run=active)
    case.memory.update_glossary(case.project.id, {"Energy": "Енергетичний ресурс"}, expected_revision=0)
    still_active = translate(case, source, Provider(), run=active)
    assert still_active.translations == first.translations
    provider = Provider()
    latest = translate(case, source, provider)
    assert provider.calls and latest.memory_segments == 0
    assert provider.calls[0][1]["glossary"] == {"Energy": "Енергетичний ресурс"}


@pytest.mark.parametrize("setting_change", [{"model": "another-synthetic-model"}, {"max_output_tokens": 512}])
def test_machine_entries_require_same_generation_but_human_preference_survives_model_change(case, setting_change):
    source = make_doc(case.root / "models.docx")
    translate(case, source, Provider())
    second_settings = case.settings.with_overrides(**setting_change)
    second_provider = Provider("Другий переклад енергії")
    result = translate(case, source, second_provider, settings=second_settings)
    assert second_provider.calls and result.memory_segments == 0
    record = entry_for(source, run_for(case))
    approved = case.memory.approve(case.project.id, **record, target=record["source_markup"].replace("Energy", "Узгоджена енергія"))
    final_provider = Provider()
    final = translate(case, source, final_provider, settings=case.settings.with_overrides(model="third-model"))
    assert not final_provider.calls
    assert set(final.translations.values()) == {approved.target}
    assert final.memory_segments == 1


def test_new_human_preference_overrides_checkpoint_only_for_next_snapshot(case):
    source = make_doc(case.root / "preferred.docx")
    active = run_for(case)
    first = translate(case, source, Provider(), run=active)
    record = entry_for(source, active)
    preferred = case.memory.approve(case.project.id, **record, target=record["source_markup"].replace("Energy", "Затверджена енергія"))
    old_provider = Provider()
    old = translate(case, source, old_provider, run=active)
    assert old.translations == first.translations and old.memory_revision == 0
    assert not old_provider.calls
    new_provider = Provider()
    new = translate(case, source, new_provider)
    assert not new_provider.calls
    assert set(new.translations.values()) == {preferred.target}
    assert new.memory_revision == preferred.revision


@pytest.mark.parametrize("checkpoint_state", ["missing", "malformed", "invalid_translation"])
def test_project_memory_remains_usable_without_a_valid_file_checkpoint(case, checkpoint_state):
    source = make_doc(case.root / "checkpoint.docx")
    first = translate(case, source, Provider())
    checkpoints = list((data_directory() / "checkpoints").glob("*.json"))
    assert len(checkpoints) == 1
    if checkpoint_state == "missing":
        checkpoints[0].unlink()
    elif checkpoint_state == "malformed":
        checkpoints[0].write_text("not valid JSON")
    else:
        checkpoints[0].write_text(json.dumps({"translations": {first.segments[0].id: "<r999>999</r999>"}}))
    provider = Provider()
    result = translate(case, source, provider)
    assert not provider.calls and result.memory_segments == 1
    assert result.translations == first.translations


def test_draft_memory_requires_editor_and_is_validated_before_export(case):
    source = make_doc(case.root / "draft.docx")
    options = replace(case.options, review=True)
    run = run_for(case, options=options)
    record = entry_for(source, run)
    case.memory.save_machine(case.project.id, **record, target=record["source_markup"].replace("12.5", "999"), generation_profile=run.generation, status="draft")
    provider = Provider()
    result = translate(case, source, provider, options=options, run=run)
    assert len(provider.calls) == 1 and "drafts" in provider.calls[0][1]
    assert "999" in next(iter(provider.calls[0][1]["drafts"].values()))
    assert all("999" not in value and "12.5" in value for value in result.translations.values())


def test_editor_cannot_export_uncorrected_numeric_draft(case):
    source = make_doc(case.root / "bad-draft.docx")
    options = replace(case.options, review=True)
    run = run_for(case, options=options)
    record = entry_for(source, run)
    case.memory.save_machine(case.project.id, **record, target=record["source_markup"].replace("12.5", "999"), generation_profile=run.generation, status="draft")
    provider = Provider(keep_bad_draft=True)
    with pytest.raises(FormaError):
        translate(case, source, provider, options=options, run=run)
    assert len(provider.calls) == 3 and all("drafts" in payload for _instruction, payload, _kwargs in provider.calls)
    assert not list((case.root / "output").glob("*.docx"))


@pytest.mark.parametrize("target", ["<r99>Bad markup 12.5</r99>", "<r0>Змінене число 999 kWh</r0>"])
def test_corrupt_approved_entry_cannot_export_even_when_storage_accepts_it(case, target):
    source = make_doc(case.root / "corrupt-approved.docx")
    record = entry_for(source, run_for(case))
    case.memory.approve(case.project.id, **record, target=target)
    provider = Provider()
    with pytest.raises(FormaError):
        translate(case, source, provider)
    assert not provider.calls
    assert not list((case.root / "output").glob("*.docx"))


def test_corrupt_machine_entry_is_revalidated_and_replaced_with_valid_work(case):
    source = make_doc(case.root / "corrupt-machine.docx")
    run = run_for(case)
    record = entry_for(source, run)
    case.memory.save_machine(case.project.id, **record, target="<r0>Змінене число 999 kWh</r0>", generation_profile=run.generation)
    provider = Provider()
    result = translate(case, source, provider, run=run)
    assert provider.calls
    assert all("999" not in value and "12.5" in value for value in result.translations.values())


def test_selected_project_rejects_a_different_language_pair_before_requests(case):
    with pytest.raises(FormaError):
        run_for(case, options=replace(case.options, target_language="de"))


@pytest.mark.parametrize("changes", [{"domain": "Changed domain"}, {"instructions": "Different instructions"}, {"target_language": "de"}])
def test_injected_run_cannot_cross_frozen_language_or_policy_boundary(case, changes):
    source = make_doc(case.root / "frozen-policy.docx")
    active = run_for(case)
    translate(case, source, Provider(), run=active)
    provider = Provider()
    with pytest.raises(FormaError):
        translate(case, source, provider, options=replace(case.options, **changes), run=active)
    assert not provider.calls


def test_injected_run_cannot_reuse_old_generation_profile_with_new_settings(case):
    source = make_doc(case.root / "frozen-model.docx")
    active = run_for(case)
    translate(case, source, Provider(), run=active)
    provider = Provider()
    with pytest.raises(FormaError):
        translate(case, source, provider, settings=case.settings.with_overrides(model="changed-model"), run=active)
    assert not provider.calls


def test_malformed_checkpoint_before_first_translation_does_not_become_trusted(case):
    source = make_doc(case.root / "first-corrupt-checkpoint.docx")
    run = run_for(case)
    effective = replace(case.options, glossary=dict(run.glossary), auto_glossary=False)
    fingerprint = checkpoint_fingerprint(
        file_digest(source), effective, case.settings, memory_policy=run.policy, memory_generation=run.generation,
    )
    checkpoint = data_directory() / "checkpoints" / f"{fingerprint}.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"translations": [], "drafts": {"wrong-id": "<r0>999</r0>"}}))
    provider = Provider()
    result = translate(case, source, provider, run=run)
    assert provider.calls and result.cached_segments == 0
    assert all("999" not in value for value in result.translations.values())


def test_checkpoint_seeding_publishes_shared_promise_before_another_file_preflight(case, monkeypatch):
    first_source = make_doc(case.root / "checkpoint-owner.docx")
    second_source = case.root / "waiting-file.docx"
    second_document = Document(first_source)
    second_document.core_properties.title = "Different package identity, identical translation unit"
    second_document.save(second_source)
    assert file_digest(first_source) != file_digest(second_source)
    seeded = translate(case, first_source, Provider())
    old_entry_id = next(iter(seeded.memory_references.values()))["entry_id"]
    # Simulate a lost machine database while the first document's valid paid
    # checkpoint survives. The second package has no checkpoint of its own.
    with sqlite3.connect(case.memory.path) as database:
        database.execute("DELETE FROM entries")
    shared = run_for(case)
    record = entry_for(first_source, shared)
    assert entry_for(second_source, shared)["key"] == record["key"]
    assert case.memory.lookup(shared.snapshot, record["key"], shared.generation) is None
    seeding_started, allow_seeding, waiter_waiting = threading.Event(), threading.Event(), threading.Event()
    original_save = case.memory.save_machine

    def pause_checkpoint_seed(*args, **kwargs):
        if kwargs.get("provenance", {}).get("kind") == "checkpoint":
            seeding_started.set()
            assert allow_seeding.wait(5), "The test did not release checkpoint publication"
        return original_save(*args, **kwargs)

    def waiting_progress(_value, message):
        if "Очікуємо спільний переклад" in message:
            waiter_waiting.set()

    monkeypatch.setattr(case.memory, "save_machine", pause_checkpoint_seed)
    first_provider, second_provider = Provider(), Provider()
    first_engine = TranslationEngine(case.settings, provider=first_provider, memory_run=shared)
    second_engine = TranslationEngine(case.settings, provider=second_provider, memory_run=shared)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first_future = workers.submit(first_engine.translate, first_source, case.root / "output", case.options)
        assert seeding_started.wait(5), "Checkpoint owner did not reach publication"
        second_future = workers.submit(second_engine.translate, second_source, case.root / "output", case.options, waiting_progress)
        try:
            assert waiter_waiting.wait(5), "The second document generated instead of awaiting the checkpoint owner"
            assert not first_provider.calls and not second_provider.calls
        finally:
            allow_seeding.set()
        first_result, second_result = first_future.result(timeout=5), second_future.result(timeout=5)
    assert first_result.translations == second_result.translations == seeded.translations
    assert not first_provider.calls and not second_provider.calls
    owner_reference = next(iter(first_result.memory_references.values()))
    waiting_reference = next(iter(second_result.memory_references.values()))
    assert owner_reference["entry_id"] == waiting_reference["entry_id"] != old_entry_id
    assert waiting_reference["kind"] == "repeat"
    assert second_result.repeated_segments == 1
