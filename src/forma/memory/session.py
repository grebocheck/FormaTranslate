"""One frozen project policy and one in-flight result per key in a document queue."""

from __future__ import annotations

import threading
from concurrent.futures import Future

from forma.core.models import FormaError, Options
from forma.memory.store import (
    TranslationMemory,
    generation_fingerprint,
    policy_fingerprint,
)


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
        self._lock = threading.Lock()
        self._work: dict[str, Future] = {}
        self._candidate_lock = threading.RLock()
        self._candidate_index = None

    def similar(self, source_markup, role, context):
        """Share one bounded-search index across the queue's document workers."""
        from forma.memory.similarity import MemoryCandidateIndex, eligible_source

        if not eligible_source(source_markup):
            return None
        with self._candidate_lock:
            if self._candidate_index is None:
                self._candidate_index = MemoryCandidateIndex(self.memory.candidate_pool(
                    self.snapshot, self.policy, self.generation,
                ))
            return self._candidate_index.find(source_markup, role, context=context)

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
