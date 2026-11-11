from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from forma.core.models import FormaError, Options
from forma.memory.store import (
    MemoryConflict,
    TranslationMemory,
    exact_fingerprint,
    generation_fingerprint,
    policy_fingerprint,
)
from forma.settings.config import Settings


@pytest.fixture
def memory(tmp_path):
    return TranslationMemory(tmp_path / "private" / "memory.sqlite3")


@pytest.fixture
def project(memory):
    return memory.create_project("Building renovation", "en", "uk", {"heat pump": "тепловий насос"})


@pytest.fixture
def record():
    policy = policy_fingerprint(Options(source_language="en", target_language="uk"), {})
    source, context, role = "<r0>Annual energy 12.5 kWh</r0>", "Table: annual consumption", "table/body/cell"
    return {
        "key": exact_fingerprint(policy, source, context, role),
        "source_markup": source,
        "target": "<r0>Річна енергія 12.5 kWh</r0>",
        "context": context,
        "role": role,
        "policy": policy,
    }


@pytest.fixture
def profile():
    return generation_fingerprint(Settings(model="synthetic-model"), protocol=12, review=True)


def another(record, text="Other exact source"):
    value = {**record, "source_markup": f"<r0>{text}</r0>"}
    value["key"] = exact_fingerprint(value["policy"], value["source_markup"], value["context"], value["role"])
    return value


def test_project_language_binding_and_private_isolation(memory, project):
    second = memory.create_project("Same client", "EN", "DE")
    assert second.source_language == "en" and second.target_language == "de"
    assert memory.get_project(project.id) == project
    assert {value.id for value in memory.list_projects()} == {project.id, second.id}
    with pytest.raises(FormaError, match="explicit"):
        memory.create_project("Automatic", "auto", "uk")
    with pytest.raises(FormaError, match="not found"):
        memory.get_project("missing")


def test_snapshots_are_deeply_immutable_and_asdict_json_compatible(memory, project):
    snapshot = memory.snapshot(project.id)
    with pytest.raises(FrozenInstanceError):
        snapshot.approved_revision = 55
    for mutate in (
        lambda: snapshot.glossary.__setitem__("heat pump", "changed"),
        lambda: snapshot.glossary.update({"x": "y"}),
        lambda: snapshot.glossary.clear(),
        lambda: snapshot.glossary.pop("heat pump"),
    ):
        with pytest.raises(TypeError):
            mutate()
    mutable = snapshot.glossary.copy()
    mutable["heat pump"] = "changed"
    assert snapshot.glossary["heat pump"] == "тепловий насос"
    assert deepcopy(snapshot) == snapshot
    assert json.loads(json.dumps(asdict(snapshot)))["glossary"] == dict(snapshot.glossary)
    assert "glossary" in asdict(project) and "_glossary" not in asdict(project)


def test_exact_identity_preserves_markup_context_role_and_whitespace(record):
    key = record["key"]
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    for source, context, role in (
        ("<r1>Annual energy 12.5 kWh</r1>", record["context"], record["role"]),
        (record["source_markup"] + " ", record["context"], record["role"]),
        (record["source_markup"], "another table", record["role"]),
        (record["source_markup"], record["context"], "table/header/cell"),
    ):
        assert exact_fingerprint(record["policy"], source, context, role) != key


def test_policy_excludes_generation_workflow_but_tracks_all_translation_rules():
    options = Options(source_language="uk", target_language="en", domain="Heating", instructions="Use the supplied terminology.")
    glossary = {"term": "translation", "other": "other translation"}
    key = policy_fingerprint(options, glossary)
    assert policy_fingerprint(replace(options, review=False, auto_glossary=False), dict(reversed(list(glossary.items())))) == key
    for changed in (
        replace(options, source_language="de"), replace(options, target_language="pl"),
        replace(options, domain="Architecture"), replace(options, instructions=options.instructions + " "),
    ):
        assert policy_fingerprint(changed, glossary) != key
    assert policy_fingerprint(options, {"term": "different"}) != key


def test_generation_fingerprint_excludes_credentials_pacing_and_memory_project():
    settings = Settings(model="model-A", api_key="never-persist-this-key")
    key = generation_fingerprint(settings, protocol=1, review=True)
    harmless = settings.with_overrides(api_key="rotated", timeout=999, max_parallel_requests=1, requests_per_minute=5, memory_project_id="other")
    assert generation_fingerprint(harmless, protocol=1, review=True) == key
    for changes in (
        {"model": "model-B"}, {"base_url": "https://other.invalid/v1"}, {"api_mode": "responses"},
        {"response_format": "json"}, {"review_model": "editor"}, {"reasoning_effort": "high"}, {"max_output_tokens": 100},
    ):
        assert generation_fingerprint(settings.with_overrides(**changes), protocol=1, review=True) != key
    assert generation_fingerprint(settings, protocol=2, review=True) != key
    assert generation_fingerprint(settings, protocol=1, review=False) != key


def test_final_machine_entries_are_shared_after_snapshot_and_are_project_scoped(memory, project, record, profile):
    snapshot = memory.snapshot(project.id)
    other = memory.create_project("Other context", "en", "uk")
    first = memory.save_machine(project.id, **record, generation_profile=profile)
    assert memory.lookup(snapshot, record["key"], profile) == first
    assert memory.lookup(memory.snapshot(other.id), record["key"], profile) is None
    assert memory.lookup(snapshot, record["key"], "f" * 64) is None
    assert memory.lookup(snapshot, record["key"], profile, preferred_only=True) is None
    assert memory.save_machine(project.id, **record, generation_profile=profile).entry_id == first.entry_id


def test_drafts_never_masquerade_as_finals_and_do_not_override_existing_final(memory, project, record, profile):
    snapshot = memory.snapshot(project.id)
    draft = memory.save_machine(project.id, **record, generation_profile=profile, status="draft")
    assert memory.lookup(snapshot, record["key"], profile) is None
    assert memory.lookup(snapshot, record["key"], profile, include_draft=True) == draft
    final = memory.save_machine(project.id, **record, generation_profile=profile)
    memory.save_machine(project.id, **{**record, "target": "<r0>Later draft</r0>"}, generation_profile=profile, status="draft")
    assert memory.lookup(snapshot, record["key"], profile, include_draft=True) == final
    with pytest.raises(FormaError):
        memory.save_machine(project.id, **record, generation_profile=profile, status="human_approved")


def test_machine_versions_retain_history_without_overwriting(memory, project, record, profile):
    first = memory.save_machine(project.id, **record, generation_profile=profile)
    second = memory.save_machine(project.id, **{**record, "target": "<r0>Revised machine text</r0>"}, generation_profile=profile)
    assert second.revision > first.revision
    assert memory.history(project.id, record["key"]) == [second, first]
    assert memory.lookup(memory.snapshot(project.id), record["key"], profile) == second


def test_human_preference_survives_model_changes_and_is_frozen_by_snapshot(memory, project, record, profile):
    old = memory.snapshot(project.id)
    machine = memory.save_machine(project.id, **record, generation_profile=profile)
    approved = memory.approve(project.id, **record)
    frozen = memory.snapshot(project.id)
    assert memory.lookup(old, record["key"], profile) == machine
    assert memory.lookup(frozen, record["key"], "a" * 64) == approved
    revised = memory.approve(project.id, **{**record, "target": "<r0>Human correction</r0>"}, replace=True, expected_revision=frozen.approved_revision)
    assert memory.lookup(frozen, record["key"], "a" * 64) == approved
    assert memory.lookup(memory.snapshot(project.id), record["key"], "a" * 64) == revised
    assert [entry.target for entry in memory.history(project.id, record["key"]) if entry.status == "human_approved"] == [revised.target, approved.target]


def test_approval_conflicts_require_explicit_current_revision_and_keep_old_version(memory, project, record):
    first = memory.approve(project.id, **record)
    changed = {**record, "target": "<r0>Conflicting approval</r0>"}
    for kwargs in ({}, {"replace": True}):
        with pytest.raises(MemoryConflict) as error:
            memory.approve(project.id, **changed, **kwargs)
        assert error.value.current_target == first.target and error.value.current_revision == first.revision
    with pytest.raises(MemoryConflict, match="changed"):
        memory.approve(project.id, **changed, replace=True, expected_revision=0)
    assert len(memory.history(project.id, record["key"])) == 1
    assert memory.approve(project.id, **record).entry_id == first.entry_id
    assert memory.snapshot(project.id).approved_revision == first.revision


def test_reference_import_variants_remain_non_reusable_until_explicit_approval(memory, project, record, profile):
    variants = [record, {**record, "target": "<r0>Alternative imported text</r0>"}]
    references = memory.save_batch(project.id, variants)
    assert len(references) == 2 and {value.status for value in references} == {"reference"}
    snapshot = memory.snapshot(project.id)
    assert memory.lookup(snapshot, record["key"], profile, include_draft=True) is None
    assert memory.lookup(snapshot, record["key"], profile, preferred_only=True) is None
    assert snapshot.approved_revision == 0


def test_preferred_batch_is_atomic_when_a_later_entry_conflicts(memory, project, record):
    memory.approve(project.id, **record)
    before = memory.snapshot(project.id)
    fresh = another(record)
    conflicting = {**record, "target": "<r0>Different</r0>"}
    with pytest.raises(MemoryConflict) as error:
        memory.save_batch(project.id, [fresh, conflicting], preferred=True, expected_revision=before.approved_revision)
    assert error.value.current_revision == before.approved_revision
    assert memory.history(project.id, fresh["key"]) == []
    assert memory.snapshot(project.id) == before


def test_preferred_batch_checks_one_global_watermark_and_rejects_internal_conflicts(memory, project, record):
    first_snapshot = memory.snapshot(project.id)
    hits = memory.save_batch(project.id, [record, another(record)], preferred=True, expected_revision=0, expected_glossary_revision=0)
    assert [hit.revision for hit in hits] == [1, 2]
    with pytest.raises(MemoryConflict):
        memory.save_batch(project.id, [another(record, "Third")], preferred=True, expected_revision=first_snapshot.approved_revision)
    with pytest.raises(FormaError, match="conflicting preferred"):
        memory.save_batch(project.id, [record, {**record, "target": "<r0>Different</r0>"}], preferred=True, replace=True, expected_revision=2)
    assert memory.snapshot(project.id).approved_revision == 2


def test_glossary_revisions_are_frozen_audited_and_compare_and_swap_safe(memory, project, record):
    before = memory.snapshot(project.id)
    changed = memory.update_glossary(project.id, {"heat pump": "теплонасос"}, expected_revision=0)
    assert changed.glossary_revision == 1
    assert before.glossary == {"heat pump": "тепловий насос"}
    assert memory.update_glossary(project.id, dict(changed.glossary)).glossary_revision == 1
    with pytest.raises(MemoryConflict) as error:
        memory.update_glossary(project.id, {}, expected_revision=0)
    assert error.value.current_glossary_revision == 1
    with pytest.raises(MemoryConflict):
        memory.save_batch(project.id, [record], preferred=True, expected_revision=0, expected_glossary_revision=0)
    assert not memory.list_entries(project.id)
    with sqlite3.connect(memory.path) as db:
        assert db.execute("SELECT COUNT(*) FROM glossaries WHERE project_id=?", (project.id,)).fetchone()[0] == 2


def test_identity_mismatch_or_malformed_values_cannot_poison_exact_store(memory, project, record, profile):
    for changed in ({"key": "0" * 64}, {"context": "changed"}, {"source_markup": "changed"}, {"target": ""}, {"policy": "short"}, {"context": "x" * (64 * 1024 + 1)}):
        with pytest.raises(FormaError):
            memory.save_machine(project.id, **{**record, **changed}, generation_profile=profile)
    assert not memory.list_entries(project.id)


def test_provenance_is_allowlisted_and_never_serializes_arbitrary_settings(memory, project, record, profile):
    secret = "credential-must-not-reach-sqlite-9281"
    hit = memory.save_machine(project.id, **record, generation_profile=profile, provenance={
        "model": "test-model", "segment_id": "word/document.xml#4", "report_fingerprint": "d" * 64,
        "adaptation_context_sha256": "a" * 64,
        "api_key": secret, "settings": {"api_key": secret}, "report": {"secret": secret}, "unknown": secret,
    })
    assert set(hit.provenance) == {"model", "segment_id", "report_fingerprint", "adaptation_context_sha256"}
    invalid = memory.approve(project.id, **record, provenance={"adaptation_context_sha256": 123})
    assert "adaptation_context_sha256" not in invalid.provenance
    with sqlite3.connect(memory.path) as db:
        stored = db.execute("SELECT provenance FROM entries").fetchone()[0]
    assert secret not in stored and "api_key" not in stored


def test_default_store_is_separate_and_readable_errors_do_not_recreate_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path / "data"))
    memory = TranslationMemory()
    assert memory.path.name == "translation-memory.sqlite3"
    corrupted = tmp_path / "corrupted.sqlite3"
    original = b"private malformed database, never destroy me"
    corrupted.write_bytes(original)
    with pytest.raises(FormaError, match="translation memory"):
        TranslationMemory(corrupted).list_projects()
    assert corrupted.read_bytes() == original


def test_foreign_schema_is_not_modified(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE private_source (value TEXT)")
        db.execute("INSERT INTO private_source VALUES('private row')")
    with pytest.raises(FormaError):
        TranslationMemory(path).list_projects()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("private_source",)]
        assert db.execute("SELECT value FROM private_source").fetchone()[0] == "private row"


def test_wal_mode_and_private_file_permissions(memory, project):
    with sqlite3.connect(memory.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    if os.name != "nt":
        assert memory.path.stat().st_mode & 0o777 == 0o600


def test_concurrent_first_use_initialization_and_project_creation(tmp_path):
    path = tmp_path / "parallel.sqlite3"
    barrier = threading.Barrier(8)

    def create(index):
        memory = TranslationMemory(path)
        barrier.wait()
        return memory.create_project(f"Project {index}", "en", "uk")

    with ThreadPoolExecutor(max_workers=8) as pool:
        projects = list(pool.map(create, range(8)))
    assert len({project.id for project in projects}) == 8
    assert len(TranslationMemory(path).list_projects()) == 8


def test_concurrent_approval_cannot_silently_overwrite_another_human(memory, project, record):
    barrier = threading.Barrier(2)

    def approve(target):
        barrier.wait()
        try:
            return memory.approve(project.id, **{**record, "target": target}, expected_revision=0)
        except MemoryConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(approve, ["<r0>First</r0>", "<r0>Second</r0>"]))
    assert sum(isinstance(value, MemoryConflict) for value in result) == 1
    assert len(memory.history(project.id, record["key"])) == 1
    assert memory.snapshot(project.id).approved_revision == 1


def test_parallel_machine_writes_retain_all_validated_versions(memory, project, record, profile):
    def save(index):
        return memory.save_machine(project.id, **{**record, "target": f"<r0>Version {index}</r0>"}, generation_profile=profile)

    with ThreadPoolExecutor(max_workers=6) as pool:
        hits = list(pool.map(save, range(18)))
    assert len({hit.entry_id for hit in hits}) == 18
    assert len(memory.history(project.id, record["key"])) == 18


def test_bulk_lookup_preserves_frozen_preference_and_final_before_draft_priority(memory, project, record, profile):
    machine_record = another(record, "Machine-only source")
    draft_record = another(record, "Draft-only source")
    reference_record = another(record, "Reference-only source")
    preferred = memory.approve(project.id, **record)
    frozen = memory.snapshot(project.id)
    memory.approve(project.id, **{**record, "target": "<r0>New human revision</r0>"}, replace=True, expected_revision=frozen.approved_revision)
    machine = memory.save_machine(project.id, **machine_record, generation_profile=profile)
    memory.save_machine(project.id, **{**machine_record, "target": "<r0>Later provisional draft</r0>"}, generation_profile=profile, status="draft")
    draft = memory.save_machine(project.id, **draft_record, generation_profile=profile, status="draft")
    memory.save_reference(project.id, **reference_record)
    keys = [record["key"], machine_record["key"], draft_record["key"], reference_record["key"], "f" * 64]
    assert memory.lookup_many(frozen, keys, profile) == {record["key"]: preferred, machine_record["key"]: machine}
    assert memory.lookup_many(frozen, keys, profile, include_draft=True) == {
        record["key"]: preferred, machine_record["key"]: machine, draft_record["key"]: draft,
    }
    assert memory.lookup_many(frozen, keys, "different profile ignored", preferred_only=True) == {record["key"]: preferred}


def test_bulk_lookup_chunks_more_than_400_keys_in_one_short_connection(memory, project, record, profile, monkeypatch):
    records = [another(record, f"Synthetic source {index}") for index in range(407)]
    for value in records:
        memory.save_machine(project.id, **value, generation_profile=profile)
    snapshot = memory.snapshot(project.id)
    connections = []
    original = memory._connect

    @contextmanager
    def limited_connection(**kwargs):
        connections.append(kwargs)
        with original(**kwargs) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            yield db

    monkeypatch.setattr(memory, "_connect", limited_connection)
    keys = [value["key"] for value in records]
    hits = memory.lookup_many(snapshot, [*keys, keys[0]], profile)
    assert len(connections) == 1
    assert list(hits) == keys
    assert len(hits) == 407 and all(hit.status == "machine_final" for hit in hits.values())
    # The bulk reader has closed its transaction before returning to API work.
    with sqlite3.connect(memory.path, timeout=0.1) as db:
        db.execute("BEGIN IMMEDIATE")
        db.rollback()


def test_bulk_lookup_preserves_project_and_generation_boundaries(memory, project, record, profile):
    other = memory.create_project("Other project", "en", "uk")
    memory.save_machine(project.id, **record, generation_profile=profile)
    assert memory.lookup_many(memory.snapshot(other.id), [record["key"]], profile) == {}
    assert memory.lookup_many(memory.snapshot(project.id), [record["key"]], "0" * 64) == {}
    with pytest.raises(FormaError):
        memory.lookup_many(memory.snapshot(project.id), ["not-a-full-key"], profile)
    assert memory.lookup_many(memory.snapshot(project.id), [], profile) == {}
