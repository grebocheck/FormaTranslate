"""One frozen project policy and one in-flight result per key in a document queue."""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future

from forma.core.models import FormaError, Options
from forma.memory.store import (
    TranslationMemory,
    exact_fingerprint,
    generation_fingerprint,
    policy_fingerprint,
)
from forma.settings.connection import legacy_connection_urls, legacy_route_compatible


class MemoryRun:
    def __init__(self, options: Options, settings, *, protocol: int, memory=None):
        self.memory = memory or TranslationMemory()
        self.snapshot = self.memory.snapshot(options.memory_project_id)
        if (
            options.source_language.lower() != self.snapshot.source_language
            or options.target_language.lower() != self.snapshot.target_language
        ):
            raise FormaError("Мови перекладу мають відповідати вибраному проєкту пам’яті.")
        self.glossary = dict(self.snapshot.glossary)
        self.policy = policy_fingerprint(options, self.glossary)
        self.generation = generation_fingerprint(settings, protocol=protocol, review=options.review)
        self.legacy_generations = tuple(dict.fromkeys(
            fingerprint for url in legacy_connection_urls(settings)
            if legacy_route_compatible(url, settings.api_mode)
            if (fingerprint := generation_fingerprint(
                settings.with_overrides(base_url=url), protocol=protocol, review=options.review, legacy=True,
            )) != self.generation
        ))
        # Scope-v1 profiles are reference-only. They never enter normal exact
        # lookup, connection adoption or file-checkpoint migration aliases.
        self.revalidation_generations = tuple(dict.fromkeys((
            generation_fingerprint(settings, protocol=protocol, review=options.review, context_version=1),
            *(generation_fingerprint(
                settings.with_overrides(base_url=url), protocol=protocol, review=options.review,
                legacy=True, context_version=1,
            ) for url in (settings.base_url, *legacy_connection_urls(settings))
              if legacy_route_compatible(url, settings.api_mode)),
        )))
        self._lock = threading.Lock()
        self._work: dict[str, Future] = {}
        self._profile_one_possible = None
        self._candidate_lock = threading.RLock()
        self._candidate_index = None

    def lookup_many(self, keys, *, include_draft=False):
        found = self.memory.lookup_many(self.snapshot, keys, self.generation, include_draft=include_draft)
        canonical_finals = {key for key, hit in found.items() if hit.status != "draft"}
        for generation in self.legacy_generations:
            pending = [key for key in keys if key not in canonical_finals]
            for key, hit in self.memory.lookup_many(self.snapshot, pending, generation, include_draft=include_draft).items():
                previous = found.get(key)
                priority = {"draft": 0, "machine_final": 1, "human_approved": 2}
                if previous is None or (priority[hit.status], hit.entry_id) > (priority[previous.status], previous.entry_id):
                    found[key] = hit
        return found

    def adopt(self, hit):
        """Copy a validated equivalent-connection final; never relabel its old row."""
        if hit.generation_profile == self.generation or hit.status == "human_approved":
            return hit
        if (hit.project_id != self.snapshot.project_id or hit.policy != self.policy
                or hit.generation_profile not in self.legacy_generations or hit.status != "machine_final"):
            raise FormaError("Запис пам’яті не відповідає поточному фрагменту. Перевірте цілісність бази пам’яті.")
        return self.memory.save_machine(
            hit.project_id, hit.entry_key, hit.source_markup, hit.target, hit.context, hit.role,
            hit.policy, self.generation, provenance={
                **hit.provenance, "kind": "connection_identity_migration", "source_entry_id": hit.entry_id,
            },
        )

    def legacy_revalidation_many(self, records, *, context_version=1):
        """Exact old local/source proof permits one paid review, never output."""
        expected = {
            exact_fingerprint(self.policy, source, context, role): (source, context, role)
            for source, context, role in records.values()
        }
        generations = self.revalidation_generations
        if context_version == 2:
            # Only a real profile-one projection may use the former scoped
            # generation as a reference. Current per-use records stay excluded.
            for _source, context, _role in records.values():
                value = json.loads(context)
                if value.get("memory_context_version") != 2 or value.get("extraction_profile", 1) != 1:
                    raise FormaError("Запис пам’яті не відповідає поточному фрагменту. Перевірте цілісність бази пам’яті.")
            generations = (self.generation, *self.legacy_generations)
        elif context_version != 1:
            raise ValueError("Unsupported legacy context profile")
        found = {}
        for generation in generations:
            pending = [key for key in expected if key not in found or found[key].status != "human_approved"]
            for key, hit in self.memory.lookup_many(self.snapshot, pending, generation, include_draft=True).items():
                source, context, role = expected[key]
                if (hit.project_id != self.snapshot.project_id or hit.policy != self.policy
                        or hit.source_markup != source or hit.context != context or hit.role != role
                        or hit.status not in {"human_approved", "machine_final", "draft"}
                        or hit.status != "human_approved" and hit.generation_profile not in generations):
                    raise FormaError("Запис пам’яті не відповідає поточному фрагменту. Перевірте цілісність бази пам’яті.")
                previous = found.get(key)
                priority = {"draft": 0, "machine_final": 1, "human_approved": 2}
                if previous is None or (priority[hit.status], hit.entry_id) > (priority[previous.status], previous.entry_id):
                    found[key] = hit
        return {
            current: found[key] for current, (source, context, role) in records.items()
            if (key := exact_fingerprint(self.policy, source, context, role)) in found
        }

    def needs_profile_one_projection(self):
        with self._lock:
            if self._profile_one_possible is None:
                self._profile_one_possible = self.memory.may_have_profile_one_references(
                    self.snapshot, self.policy,
                    (*self.revalidation_generations, self.generation, *self.legacy_generations),
                )
            return self._profile_one_possible

    def similar(self, source_markup, role, context, *, adaptation_context=None):
        """Share one bounded-search index across the queue's document workers."""
        from forma.memory.similarity import MemoryCandidateIndex, eligible_source

        if not eligible_source(source_markup):
            return None
        with self._candidate_lock:
            if self._candidate_index is None:
                candidates = {hit.entry_key: hit for hit in self.memory.candidate_pool(
                    self.snapshot, self.policy, self.generation,
                )}
                for generation in self.legacy_generations:
                    for hit in self.memory.candidate_pool(self.snapshot, self.policy, generation):
                        candidates.setdefault(hit.entry_key, hit)
                self._candidate_index = MemoryCandidateIndex(candidates.values())
            return self._candidate_index.find(source_markup, role, context=context, adaptation_context=adaptation_context)

    def claim(self, key: str) -> tuple[bool, Future]:
        with self._lock:
            if key in self._work:
                return False, self._work[key]
            future = Future()
            self._work[key] = future
            return True, future

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._work

    def complete(self, key: str, value) -> None:
        if (
            value.project_id != self.snapshot.project_id or value.entry_key != key
            or value.policy != self.policy or value.generation_profile != self.generation
            or value.status != "machine_final"
        ):
            raise FormaError("Запис пам’яті не відповідає поточному фрагменту. Перевірте цілісність бази пам’яті.")
        # The database transaction has already committed. New paragraphs become
        # adaptation references for later files without reloading the full pool.
        with self._candidate_lock:
            if self._candidate_index is not None:
                self._candidate_index.add(value)
        with self._lock:
            future = self._work[key]
            if not future.done():
                future.set_result(value)

    def fail(self, key: str, error: BaseException) -> None:
        with self._lock:
            future = self._work.get(key)
            if future is not None and not future.done():
                future.set_exception(error)
                # Current waiters retain their future; a later file may retry.
                del self._work[key]
