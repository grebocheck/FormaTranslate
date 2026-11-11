"""Local candidate retrieval tests; a near match is never a finished translation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

import forma.memory.similarity as similarity
from forma.core.models import Options
from forma.memory.similarity import MemoryCandidateIndex, eligible_source
from forma.memory.store import (
    MemoryHit,
    TranslationMemory,
    exact_fingerprint,
    policy_fingerprint,
)

SOURCE = (
    "<r0>The contractor shall replace the heating equipment at Alpha Street and deliver "
    "the final inspection report within 45 calendar days after written acceptance. "
    "The engineer must review the installation drawings and verify every safety "
    "condition before the equipment is placed into normal operation.</r0>"
)
POLICY = "1" * 64
GENERATION = "2" * 64


def hit(source=SOURCE, *, target=None, context="Building heating", role="body", status="machine_final", index=1, policy=POLICY):
    target = target or source.replace("The contractor", "Підрядник")
    return MemoryHit(
        index, "synthetic-project", exact_fingerprint(policy, source, context, role), source,
        target, context, role, policy, GENERATION, status, index, 0,
    )


@pytest.mark.parametrize("source", ["", "<r0>Short heading</r0>", "<r0>" + "x" * 100 + "</r0>", SOURCE.replace("</r0>", "</r1>"), SOURCE.replace("<r0>", '<r0 style="bad">')])
def test_short_uninformative_or_malformed_source_is_not_eligible(source):
    assert not eligible_source(source)
    assert MemoryCandidateIndex([hit()]).find(source, "body") is None


def test_changed_names_numbers_and_dates_can_supply_only_a_candidate():
    previous = hit()
    current = SOURCE.replace("Alpha Street", "Bravo Street").replace("45", "60")
    assert eligible_source(current)
    candidate = MemoryCandidateIndex([previous]).find(current, "body")
    assert candidate == previous
    assert candidate.source_markup != current and "45" in candidate.target
    # The returned object deliberately retains the OLD numbers/source. The
    # caller must re-edit it, rather than treat the target as current output.


def test_small_sentence_change_remains_an_editor_candidate():
    previous = hit()
    current = SOURCE.replace("review the installation drawings", "approve the installation drawings")
    assert MemoryCandidateIndex([previous]).find(current, "body") == previous


def test_major_rewrite_never_qualifies_as_a_near_match():
    current = (
        "<r0>The legal adviser shall prepare the property lease documentation for a "
        "different client and calculate rental charges before the next financial "
        "reporting period. The accounting department will inspect supporting invoices "
        "and issue a separate payment notice after all contractual approvals.</r0>"
    )
    assert eligible_source(current)
    assert MemoryCandidateIndex([hit()]).find(current, "body") is None


def test_source_role_and_full_marker_sequence_must_match():
    index = MemoryCandidateIndex([hit()])
    assert index.find(SOURCE, "table/header") is None
    # Build the two-run form explicitly so both it and the one-run original
    # are valid protocol sources with the same visible words.
    content = SOURCE.removeprefix("<r0>").removesuffix("</r0>")
    left, right = content.split(" and deliver ", 1)
    split = f"<r0>{left}</r0><k0/><r1> and deliver {right}</r1>"
    assert eligible_source(split)
    assert index.find(split, "body") is None


def test_equal_similarity_with_different_targets_is_ambiguous():
    first = hit(context="Engineering interpretation", target=SOURCE.replace("The contractor", "Технічний підрядник"), index=1)
    second = hit(context="Legal interpretation", target=SOURCE.replace("The contractor", "Юридична сторона"), index=2, status="human_approved")
    assert MemoryCandidateIndex([first, second]).find(SOURCE.replace("45", "60"), "body") is None


def test_equal_similarity_and_same_target_can_prefer_a_human_revision():
    first = hit(context="Machine context", index=1)
    second = hit(context="Approved context", index=2, status="human_approved", target=first.target)
    assert MemoryCandidateIndex([first, second]).find(SOURCE.replace("45", "60"), "body") == second


@pytest.mark.parametrize("status", ["draft", "reference"])
def test_drafts_and_reference_imports_cannot_enter_the_index(status):
    assert MemoryCandidateIndex([hit(status=status)]).find(SOURCE, "body") is None


def test_corrupted_source_identity_or_target_markers_are_not_candidates():
    changed_source = replace(hit(), source_markup=SOURCE.replace("Alpha", "Other"))
    changed_target = replace(hit(), target="<r99>Broken</r99>")
    assert MemoryCandidateIndex([changed_source, changed_target]).find(SOURCE, "body") is None


def test_case_and_whitespace_normalization_affects_scoring_but_not_saved_payload():
    previous = hit()
    current = SOURCE.replace("Alpha Street", "ALPHA  STREET").replace("45 calendar", "45\ncalendar")
    assert MemoryCandidateIndex([previous]).find(current, "body") == previous
    assert previous.source_markup == SOURCE


def _context(full, excerpt="Identical short prompt context"):
    return json.dumps({"context": excerpt, "full_context_sha256": hashlib.sha256(full.encode()).hexdigest()})


def test_changed_prose_requires_the_same_full_surrounding_context_when_supplied():
    original_context = _context("Full neighboring paragraph: permission is required")
    changed_context = _context("Full neighboring paragraph: permission is not required")
    previous = hit(context=original_context)
    current = SOURCE.replace("Alpha", "Bravo").replace("45", "60")
    index = MemoryCandidateIndex([previous])
    assert index.find(current, "body", context=original_context) == previous
    assert index.find(current, "body", context=changed_context) is None
    assert index.find(current, "body", context="not canonical JSON") is None
    assert index.find(current, "body", context='{"context":"short excerpt only"}') is None


def test_identical_marked_source_cannot_bypass_an_exact_context_miss():
    previous = hit(context=_context("Old neighbors"))
    index = MemoryCandidateIndex([previous])
    assert index.find(SOURCE, "body") is None
    assert index.find(SOURCE, "body", context=previous.context) is None
    assert index.find(SOURCE, "body", context=_context("Different neighbors")) is None


def test_context_filter_applies_before_the_shortlist_cutoff():
    stable = _context("Actual stable surroundings")
    distractors = [hit(SOURCE.replace("Alpha", f"Name{index}"), index=index + 1, context=_context(f"Other surroundings {index}")) for index in range(100)]
    previous = hit(context=stable, index=200)
    index = MemoryCandidateIndex([*distractors, previous])
    assert index.find(SOURCE.replace("Alpha", "Bravo").replace("45", "60"), "body", context=stable) == previous


def test_shared_index_accepts_new_final_without_rebuilding_its_existing_pool():
    context = _context("Stable adjacent paragraphs")
    first = hit(context=context)
    index = MemoryCandidateIndex([first])
    newly_completed = hit(SOURCE.replace("Alpha", "Bravo"), context=context, index=2)
    current = SOURCE.replace("Alpha", "Bravo").replace("45", "60")
    assert index.find(current, "body", context=context) == first
    index.add(newly_completed)
    assert index.find(current, "body", context=context) == newly_completed


def test_add_is_idempotent_by_entry_id_for_constructor_and_completion_overlap():
    context = _context("Stable neighboring paragraphs")
    previous = hit(context=context)
    index = MemoryCandidateIndex([previous, previous])
    index.add(previous)
    # An existing immutable ID must not acquire a second conflicting candidate
    # if pool loading and completion notifications overlap.
    index.add(replace(previous, target=SOURCE.replace("The contractor", "Unexpected duplicate target")))
    assert index.find(SOURCE.replace("45", "60"), "body", context=context) == previous


def test_add_does_not_publish_a_provisional_draft():
    context = _context("Same actual surroundings")
    index = MemoryCandidateIndex([])
    index.add(hit(context=context, status="draft"))
    assert index.find(SOURCE.replace("45", "60"), "body", context=context) is None


def test_long_repetitive_or_oversized_sources_are_bounded():
    oversized = "<r0>" + "contract requirements and engineering documents " * 300 + "</r0>"
    repetitive = "<r0>" + "contractor " * 150 + "</r0>"
    assert not eligible_source(oversized)
    assert not eligible_source(repetitive)
    assert similarity._score("a" * 4000 + "b" * 4000, "c" * 4000 + "d" * 4000) is None


def test_lexical_index_caps_expensive_matching_for_4000_entries_and_50_changed_paragraphs(monkeypatch):
    previous = [hit(SOURCE.replace("Alpha", f"District{index}"), index=index + 1) for index in range(4000)]
    index = MemoryCandidateIndex(previous)
    calls = []
    original = similarity.SequenceMatcher

    def matcher(*args, **kwargs):
        assert kwargs["autojunk"] is False
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(similarity, "SequenceMatcher", matcher)
    for current in range(50):
        before = len(calls)
        candidate = index.find(SOURCE.replace("Alpha", f"District{current}").replace("45 calendar", "60 calendar"), "body")
        assert candidate == previous[current]
        assert 0 < len(calls) - before <= similarity.MAX_SHORTLIST
    assert len(calls) <= 50 * similarity.MAX_SHORTLIST


def _record(policy, source=SOURCE, *, context="Building heating", role="body", target=None):
    return {
        "key": exact_fingerprint(policy, source, context, role), "source_markup": source,
        "target": target or source.replace("The contractor", "Підрядник"),
        "context": context, "role": role, "policy": policy,
    }


def test_pool_freezes_human_preference_and_includes_only_latest_exact_generation_finals(tmp_path):
    memory = TranslationMemory(tmp_path / "memory.sqlite3")
    project = memory.create_project("Candidate project", "en", "uk")
    policy = policy_fingerprint(Options(source_language="en", target_language="uk"), {})
    preferred_record = _record(policy)
    preferred = memory.approve(project.id, **preferred_record)
    frozen = memory.snapshot(project.id)
    newer = memory.approve(project.id, **{**preferred_record, "target": SOURCE.replace("The contractor", "Новий затверджений підрядник")}, replace=True, expected_revision=frozen.approved_revision)
    memory.save_machine(project.id, **preferred_record, generation_profile=GENERATION)
    machine_record = _record(policy, SOURCE.replace("Alpha", "Second"))
    memory.save_machine(project.id, **machine_record, generation_profile=GENERATION)
    latest = memory.save_machine(project.id, **{**machine_record, "target": machine_record["target"].replace("Підрядник", "Оновлений підрядник")}, generation_profile=GENERATION)
    memory.save_machine(project.id, **{**machine_record, "target": machine_record["target"].replace("Підрядник", "Інша модель")}, generation_profile="3" * 64)
    memory.save_machine(project.id, **_record(policy, SOURCE.replace("Alpha", "Draft")), generation_profile=GENERATION, status="draft")
    memory.save_reference(project.id, **_record(policy, SOURCE.replace("Alpha", "Reference")))
    pool = {value.entry_key: value for value in memory.candidate_pool(frozen, policy, GENERATION)}
    assert pool == {preferred.entry_key: preferred, latest.entry_key: latest}
    current = {value.entry_key: value for value in memory.candidate_pool(memory.snapshot(project.id), policy, GENERATION)}
    assert current[preferred.entry_key] == newer


def test_pool_enforces_project_policy_and_source_integrity(tmp_path):
    memory = TranslationMemory(tmp_path / "memory.sqlite3")
    project = memory.create_project("Main", "en", "uk")
    other = memory.create_project("Other", "en", "uk")
    policy = policy_fingerprint(Options(source_language="en", target_language="uk"), {})
    other_policy = policy_fingerprint(Options(source_language="en", target_language="uk", domain="Legal"), {})
    valid = memory.save_machine(project.id, **_record(policy), generation_profile=GENERATION)
    memory.save_machine(other.id, **_record(policy), generation_profile=GENERATION)
    memory.save_machine(project.id, **_record(other_policy), generation_profile=GENERATION)
    assert memory.candidate_pool(memory.snapshot(project.id), policy, GENERATION) == [valid]
    with sqlite3.connect(memory.path) as database:
        database.execute("UPDATE entries SET source_markup=? WHERE id=?", (SOURCE.replace("Alpha", "Tampered"), valid.entry_id))
    assert memory.candidate_pool(memory.snapshot(project.id), policy, GENERATION) == []
