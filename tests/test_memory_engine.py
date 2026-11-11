"""Offline, real-Office tests of reuse, queue coordination and provenance."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from docx import Document

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.editor import export_reviewed_copy
from forma.documents.ooxml import OfficeDocument
from forma.memory.importing import prefer_review_segment
from forma.memory.session import MemoryRun
from forma.memory.store import TranslationMemory
from forma.providers.client import JobControl
from forma.settings.config import Settings
from forma.translation.engine import PROTOCOL_VERSION, TranslationEngine, checkpoint_fingerprint
from forma.translation.jobs import DocumentQueue, run_documents


def document(path, words=("Energy",) * 5):
    doc = Document()
    doc.core_properties.comments = path.name  # Distinct file hash; same source contexts.
    for word in words:
        doc.add_paragraph(word)
    doc.save(path)
    return path


class OfflineProvider:
    def __init__(self, control=None, *, failure=False):
        self.calls = []
        self.input_tokens = self.output_tokens = 0
        self.control = control or JobControl(1)
        self.failure = failure

    def complete(self, instruction, payload, schema, **kwargs):
        with self.control.request_slots():
            self.calls.append(payload)
            if self.failure:
                raise FormaError("Synthetic offline failure")
            self.input_tokens += 10
            self.output_tokens += 5
            return {"segments": [
                {"id": item["id"], "text": item["text"].replace("Energy", "Енергія").replace("Pressure", "Тиск")}
                for item in payload["segments"]
            ]}


@pytest.fixture
def case():
    store = TranslationMemory()
    project = store.create_project("Synthetic series", "en", "uk", {"Energy": "Енергія"})
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="fake-offline-key", model="offline-test", max_parallel_requests=1, parallel_documents=1)
    options = Options(source_language="en", target_language="uk", review=True, memory_project_id=project.id)
    return store, settings, options


def run(case):
    store, settings, options = case
    return MemoryRun(options, settings, protocol=PROTOCOL_VERSION, memory=store)


def test_each_exact_group_is_translated_and_edited_once_then_reused_across_files(case, tmp_path):
    _, settings, options = case
    source = document(tmp_path / "first.docx")
    provider = OfflineProvider()
    first = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert len(provider.calls) == 2
    assert sum(len(call["segments"]) for call in provider.calls) == 4  # 2 contexts x 2 passes
    assert first.repeated_segments == 3
    assert first.memory_segments == first.cached_segments == 0
    assert all(call["glossary"] == {"Energy": "Енергія"} for call in provider.calls)
    assert all("terms" not in call for call in provider.calls)
    copy = document(tmp_path / "second.docx")
    assert source.read_bytes() != copy.read_bytes()
    # No Provider is even constructed when the entire second file is reusable.
    second = TranslationEngine(settings).translate(copy, tmp_path / "out", options)
    assert second.memory_segments == len(second.segments) == 5
    assert second.input_tokens == second.output_tokens == 0
    assert not second.repeated_segments and not second.cached_segments
    report = json.loads(second.report.read_text())
    assert len(report["memory_references"]) == 5
    assert report["effective_glossary"] == {"Energy": "Енергія"}
    assert report["source_sha256"] == hashlib.sha256(copy.read_bytes()).hexdigest()
    assert report["output_sha256"] == hashlib.sha256(second.output.read_bytes()).hexdigest()
    assert "fake-offline-key" not in second.report.read_text()


def test_only_changed_contexts_require_new_requests(case, tmp_path):
    _, settings, options = case
    source = document(tmp_path / "baseline.docx")
    TranslationEngine(settings, provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    changed = document(tmp_path / "changed.docx", ("Energy",) * 4 + ("Pressure",))
    provider = OfflineProvider()
    result = TranslationEngine(settings, provider=provider).translate(changed, tmp_path / "out", options)
    assert result.memory_segments == 3
    assert [len(call["segments"]) for call in provider.calls] == [2, 2]


def test_parallel_owners_finish_before_waiting_even_with_reversed_claims_and_one_slot(case, tmp_path):
    _, settings, options = case
    memory = run(case)
    barrier = threading.Barrier(2)
    owner_threads = set()
    claim_lock = threading.Lock()
    claim = memory.claim

    def interleave(key):
        owner, future = claim(key)
        rendezvous = False
        with claim_lock:
            thread = threading.get_ident()
            if owner and thread not in owner_threads:
                owner_threads.add(thread)
                rendezvous = True
        if rendezvous:
            barrier.wait(timeout=5)
        return owner, future

    memory.claim = interleave
    control = JobControl(1)
    providers = [OfflineProvider(control), OfflineProvider(control)]
    sources = [document(tmp_path / f"parallel-{index}.docx") for index in range(2)]
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(TranslationEngine(settings, control, provider=provider, memory_run=memory).translate,
                               source, tmp_path / "out", options)
                   for source, provider in zip(sources, providers, strict=True)]
        results = [future.result(timeout=10) for future in futures]
    assert len(owner_threads) == 2
    assert sum(len(call["segments"]) for provider in providers for call in provider.calls) == 4
    assert all(len(result.translations) == 5 for result in results)
    assert sum(result.repeated_segments for result in results) == 8


def test_failed_owner_unblocks_waiter_and_later_file_can_retry(case, tmp_path):
    _, settings, options = case
    memory = run(case)
    source = document(tmp_path / "failure.docx", ("Energy",))
    started, release = threading.Event(), threading.Event()
    failing = OfflineProvider(failure=True)
    complete = failing.complete

    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return complete(*args, **kwargs)

    failing.complete = blocked
    borrowed = threading.Event()
    claim = memory.claim

    def observe(key):
        owner, future = claim(key)
        if not owner:
            borrowed.set()
        return owner, future

    memory.claim = observe
    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(TranslationEngine(settings, provider=failing, memory_run=memory).translate, source, tmp_path / "out", options)
        assert started.wait(5)
        waiter_provider = OfflineProvider()
        waiter = pool.submit(TranslationEngine(settings, provider=waiter_provider, memory_run=memory).translate,
                             document(tmp_path / "waiter.docx", ("Energy",)), tmp_path / "out", options)
        assert borrowed.wait(5)
        release.set()
        for future in (owner, waiter):
            with pytest.raises(FormaError):
                future.result(timeout=10)
    assert not waiter_provider.calls
    retry = TranslationEngine(settings, provider=OfflineProvider(), memory_run=memory).translate(source, tmp_path / "out", options)
    assert retry.output.exists()


def test_queue_shares_memory_and_records_only_actual_provider_work(case, tmp_path):
    _, settings, options = case
    settings = settings.with_overrides(max_parallel_requests=2, parallel_documents=2)
    control = JobControl(2)
    providers = []

    def factory(settings, shared_control):
        provider = OfflineProvider(shared_control)
        providers.append(provider)
        return TranslationEngine(settings, shared_control, provider=provider)

    docs = [{"id": str(index), "path": str(document(tmp_path / f"queue-{index}.docx"))} for index in range(3)]
    events = []
    run_documents(docs, tmp_path / "out", options, settings, control, lambda *event: events.append(event), engine_factory=factory)
    results = [payload["result"] for kind, payload in events if kind == "result"]
    assert len(results) == 3
    assert not [payload for kind, payload in events if kind == "file_error"]
    assert sum(len(call["segments"]) for provider in providers for call in provider.calls) == 4
    assert sum(result.input_tokens for result in results) == sum(provider.input_tokens for provider in providers)


def test_cancellation_unblocks_shared_waiter_without_requesting_again(case, tmp_path):
    _, settings, options = case
    memory, control = run(case), JobControl(1)
    started, waiting = threading.Event(), threading.Event()
    owner_provider, waiter_provider = OfflineProvider(control), OfflineProvider(control)

    def stop_with_control(*args, **kwargs):
        started.set()
        assert control.cancelled.wait(5)
        control.check()

    owner_provider.complete = stop_with_control
    claim = memory.claim

    def observe(key):
        owned, future = claim(key)
        if not owned:
            waiting.set()
        return owned, future

    memory.claim = observe
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(TranslationEngine(settings, control, provider=owner_provider, memory_run=memory).translate,
                            document(tmp_path / "cancel-owner.docx", ("Energy",)), tmp_path / "out", options)
        assert started.wait(5)
        second = pool.submit(TranslationEngine(settings, control, provider=waiter_provider, memory_run=memory).translate,
                             document(tmp_path / "cancel-waiter.docx", ("Energy",)), tmp_path / "out", options)
        assert waiting.wait(5)
        control.cancelled.set()
        for future in (first, second):
            with pytest.raises(Cancelled):
                future.result(timeout=5)
    assert not waiter_provider.calls
    assert not list((tmp_path / "out").glob("*.docx"))


def test_missing_project_fails_all_queued_files_before_provider_start(case, tmp_path):
    _, settings, _ = case
    options = Options(source_language="en", target_language="uk", memory_project_id="a" * 32)
    docs = DocumentQueue([{"id": str(index), "path": str(tmp_path / f"unused-{index}.docx")} for index in range(2)])
    events = []

    def forbidden(*args):
        pytest.fail("Invalid memory must fail before provider creation")

    run_documents(docs, tmp_path / "out", options, settings, JobControl(), lambda *event: events.append(event), engine_factory=forbidden)
    assert {payload["id"] for kind, payload in events if kind == "file_error"} == {"0", "1"}
    assert not docs.snapshot()


def test_manual_copy_retains_policy_but_only_explicit_preference_changes_future_output(case, tmp_path):
    store, settings, options = case
    source = document(tmp_path / "manual.docx", ("Energy",))
    first = TranslationEngine(settings, provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    sid = first.segments[0].id
    edited = export_reviewed_copy(source, first.output, first.report, {sid: "<r0>Переважна енергія</r0>"}, tmp_path / "edited")
    metadata = json.loads(edited.report.read_text())
    assert metadata["effective_glossary"] == {"Energy": "Енергія"}
    assert metadata["memory_project_id"] == options.memory_project_id
    assert sid not in metadata["memory_references"]
    assert not store.snapshot(options.memory_project_id).approved_revision
    summary = prefer_review_segment(store, options.memory_project_id, source, edited.output, edited.report, sid)
    assert summary["approved"] == 1
    preferred = TranslationEngine(settings.with_overrides(model="different-model")).translate(source, tmp_path / "out", options)
    assert preferred.translations[sid] == "<r0>Переважна енергія</r0>"
    assert preferred.memory_segments == 1


def test_legacy_checkpoint_identity_stays_compatible_without_memory():
    options, settings = Options(), Settings()
    old_options = asdict(options)
    old_options.pop("memory_project_id")
    identity = {"protocol": 5, "file": "c" * 64, "base": settings.base_url,
                "model": settings.model, "mode": settings.api_mode, "format": settings.response_format,
                "review_model": settings.review_model, "reasoning_effort": settings.reasoning_effort,
                "options": old_options}
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert checkpoint_fingerprint("c" * 64, options, settings) == expected


def test_xlsx_memory_reuse_is_checked_with_real_workbook(case, tmp_path):
    from openpyxl import Workbook

    _, settings, options = case
    sources = []
    for index in range(2):
        workbook = Workbook()
        workbook.active["A1"] = "Energy"
        workbook.properties.title = f"Synthetic {index}"
        path = tmp_path / f"sheet-{index}.xlsx"
        workbook.save(path)
        sources.append(path)
    first = TranslationEngine(settings, provider=OfflineProvider()).translate(sources[0], tmp_path / "out", options)
    second = TranslationEngine(settings).translate(sources[1], tmp_path / "out", options)
    assert second.memory_segments == 1
    assert OfficeDocument(second.output).segments[0].plain == "Енергія"
    assert first.translations == second.translations
