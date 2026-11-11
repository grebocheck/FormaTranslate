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
from forma.memory.store import TranslationMemory, generation_fingerprint
from forma.providers.client import JobControl
from forma.settings.config import Settings, data_directory
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
    assert sum(len(call["segments"]) for call in provider.calls) == 6  # Lead, body and ending x 2 passes.
    assert first.repeated_segments == 2
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


def test_proven_native_url_alias_preserves_paid_machine_memory(case, tmp_path):
    store, settings, options = case
    original_url = settings.base_url + "/messages"
    settings = settings.with_overrides(api_mode="anthropic", base_url=original_url,
                                      legacy_connection=("anthropic", original_url))
    first = TranslationEngine(settings, provider=OfflineProvider()).translate(
        document(tmp_path / "legacy.docx"), tmp_path / "out", options,
    )
    old_generation = generation_fingerprint(settings, protocol=PROTOCOL_VERSION, review=True, legacy=True)
    with store._connect(write=True) as db:
        db.execute("UPDATE entries SET generation_profile=? WHERE status='machine_final'", (old_generation,))
        old_ids = {row[0] for row in db.execute("SELECT id FROM entries WHERE status='machine_final'")}
    for path in (data_directory() / "checkpoints").glob("*.json"):
        path.unlink()  # Isolate database compatibility from document-checkpoint reuse.
    equivalent = settings.with_overrides(base_url=settings.base_url.removesuffix("/messages"))
    provider = OfflineProvider(failure=True)
    result = TranslationEngine(equivalent, provider=provider).translate(
        document(tmp_path / "equivalent.docx"), tmp_path / "out", options,
    )
    assert not provider.calls and result.memory_segments == len(first.segments)
    with store._connect() as db:
        assert old_ids <= {row[0] for row in db.execute("SELECT id FROM entries WHERE generation_profile=?", (old_generation,))}
    foreign = MemoryRun(options, equivalent.with_overrides(api_mode="chat"), protocol=PROTOCOL_VERSION)
    assert foreign.legacy_generations == ()
    custom = equivalent.with_overrides(api_mode="chat", base_url=original_url,
                                      legacy_connection=("chat", original_url))
    assert MemoryRun(options, custom, protocol=PROTOCOL_VERSION).legacy_generations == ()
    assert generation_fingerprint(custom, protocol=PROTOCOL_VERSION, review=True) != generation_fingerprint(
        custom, protocol=PROTOCOL_VERSION, review=True, legacy=True,
    )


def test_only_changed_contexts_require_new_requests(case, tmp_path):
    _, settings, options = case
    source = document(tmp_path / "baseline.docx")
    TranslationEngine(settings, provider=OfflineProvider()).translate(source, tmp_path / "out", options)
    changed = document(tmp_path / "changed.docx", ("Energy",) * 4 + ("Pressure",))
    provider = OfflineProvider()
    result = TranslationEngine(settings, provider=provider).translate(changed, tmp_path / "out", options)
    assert result.memory_segments == 3
    assert [len(call["segments"]) for call in provider.calls] == [2, 2]


def test_v1_machine_and_approved_wording_get_one_scoped_review_then_v2_exact_reuse(tmp_path):
    from forma.core.models import plain_text
    from forma.memory.context import segment_context, segment_role
    from forma.memory.store import exact_fingerprint

    store = TranslationMemory()
    project = store.create_project("Scoped synthetic topics", "en", "uk")
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline", model="offline", max_parallel_requests=1, parallel_documents=1)
    options = Options("en", "uk", review=True, auto_glossary=False, memory_project_id=project.id)

    def source(name, title):
        doc = Document()
        doc.core_properties.comments = name
        doc.add_paragraph(title, style="Title")
        for text in ("Fixed first note", "Before value", "Charge", "Current", "Power 15 kW", "After value", "Fixed last note"):
            doc.add_paragraph(text)
        path = tmp_path / f"{name}.docx"
        doc.save(path)
        return path

    old = source("financial", "Financial")
    office = OfficeDocument(old)
    run = MemoryRun(options, settings, protocol=PROTOCOL_VERSION, memory=store)
    generation = generation_fingerprint(settings, protocol=PROTOCOL_VERSION, review=True, context_version=1)
    assert generation not in run.legacy_generations and generation in run.revalidation_generations
    originals = []
    for word, target in (("Charge", "Плата 99 kWh"), ("Current", "Поточний 999 kWh"), ("Power 15 kW", "Потужність 99 kW")):
        segment = next(item for item in office.segments if item.plain == word)
        context, role = segment_context(office, segment), segment_role(office, segment)
        record = dict(key=exact_fingerprint(run.policy, segment.source, context, role), source_markup=segment.source, target=f"<r0>{target}</r0>", context=context, role=role, policy=run.policy)
        originals.append(store.approve(project.id, **record) if word == "Current" else store.save_machine(project.id, **record, generation_profile=generation, status="draft" if word == "Power 15 kW" else "machine_final"))

    class ScopedProvider:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.calls = []

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append(payload)
            assert payload["document_context"] == ""
            values = []
            for item in payload["segments"]:
                text = plain_text(item["text"])
                scope = json.loads(item["neighboring_context"])
                assert scope["memory_context_version"] == 2 and scope["role_sha256"]
                target = {"Charge": "Заряд", "Current": "Струм", "Power 15 kW": "Потужність 15 kW"}.get(text, "Довідковий текст")
                if text in {"Charge", "Current", "Power 15 kW"}:
                    assert "Electrical" in scope["scope_prompt"]
                    assert item["id"] in payload["drafts"]
                    assert payload["memory_references"][item["id"]]["legacy_context_revalidation"]
                values.append({"id": item["id"], "text": f"<r0>{target}</r0>"})
            return {"segments": values}

    provider = ScopedProvider()
    current = source("electrical", "Electrical")
    old_checkpoint = data_directory() / "checkpoints" / (checkpoint_fingerprint(
        hashlib.sha256(current.read_bytes()).hexdigest(), options, settings,
        memory_policy=run.policy, memory_generation=generation,
    ) + ".json")
    old_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    old_bytes = json.dumps({"translations": {
        next(item.id for item in office.segments if item.plain == "Charge"): "<r0>Плата</r0>",
        next(item.id for item in office.segments if item.plain == "Current"): "<r0>Поточний</r0>",
    }}).encode()
    old_checkpoint.write_bytes(old_bytes)
    result = TranslationEngine(settings, provider=provider).translate(current, tmp_path / "out", options)
    assert old_checkpoint.read_bytes() == old_bytes  # Historical checkpoint was not adopted/relabelled.
    for word, translated in (("Charge", "Заряд"), ("Current", "Струм"), ("Power 15 kW", "Потужність 15 kW")):
        segment = next(item for item in result.segments if item.plain == word)
        assert result.translations[segment.id] == f"<r0>{translated}</r0>"
        assert sum(any(item["id"] == segment.id for item in payload["segments"]) for payload in provider.calls) == 1
    assert result.adapted_segments == 3
    with store._connect() as database:
        assert [database.execute("SELECT status FROM entries WHERE id=?", (entry.entry_id,)).fetchone()[0] for entry in originals] == ["machine_final", "human_approved", "draft"]
    again = ScopedProvider()
    repeated = TranslationEngine(settings, provider=again).translate(source("repeat", "Electrical"), tmp_path / "out", options)
    assert not again.calls and repeated.memory_segments == len(repeated.segments)


def test_unstyled_document_lead_disambiguates_without_binding_unrelated_later_body(tmp_path):
    from forma.core.models import plain_text

    store = TranslationMemory()
    project = store.create_project("Unstyled synthetic topics", "en", "uk")
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline", model="offline", max_parallel_requests=1, parallel_documents=1)
    options = Options("en", "uk", review=False, auto_glossary=False, memory_project_id=project.id)

    class ContextProvider:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.charge_requests = 0

        def complete(self, instruction, payload, schema, **kwargs):
            assert payload["document_context"] == ""
            values = []
            for item in payload["segments"]:
                if plain_text(item["text"]) == "Charge":
                    self.charge_requests += 1
                    # Inspect only actual supplied readable context, not hashes,
                    # filenames or a test-only semantic label.
                    readable = json.loads(item["neighboring_context"])["scope_prompt"]
                    target = "Заряд" if "Electrical domain" in readable else "Плата"
                else:
                    target = "Довідковий текст"
                values.append({"id": item["id"], "text": f"<r0>{target}</r0>"})
            return {"segments": values}

    def translate(name, lead, later):
        doc = Document()
        for text in (lead, "Fixed note", "Before value", "Charge", "After value", "Last note", later):
            doc.add_paragraph(text)  # The original reproduction has no Title/Heading style.
        source = tmp_path / f"{name}.docx"
        doc.save(source)
        provider = ContextProvider()
        result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
        sid = next(item.id for item in result.segments if item.plain == "Charge")
        return result.translations[sid], provider.charge_requests

    assert translate("financial", "Financial domain", "Original later body") == ("<r0>Плата</r0>", 1)
    assert translate("electrical", "Electrical domain", "Original later body") == ("<r0>Заряд</r0>", 1)
    assert translate("unrelated", "Electrical domain", "Changed unrelated later body") == ("<r0>Заряд</r0>", 0)


def shared_scope_workbook(path, finance_unit="UAH"):
    from zipfile import ZipFile

    from lxml import etree
    from openpyxl import Workbook

    from forma.documents.ooxml import S

    book = Workbook()
    book.active.title = "Finance"
    book.create_sheet("Flow")
    book.properties.description = path.name
    for sheet, unit in ((book["Finance"], finance_unit), (book["Flow"], "m³/h")):
        sheet.append(["Назва", "Одиниця"])
        sheet.append(["Витрата", unit])
    book.save(path)
    with ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    for part in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"):
        tree = etree.fromstring(parts[part])
        cell = tree.find(f".//{{{S}}}c[@r='A2']")
        cell.set("t", "s")
        for child in list(cell):
            cell.remove(child)
        etree.SubElement(cell, f"{{{S}}}v").text = "0"
        parts[part] = etree.tostring(tree)
    parts["xl/sharedStrings.xml"] = f'<sst xmlns="{S}" count="2" uniqueCount="1"><si><t>Витрата</t></si></sst>'.encode()
    types = etree.fromstring(parts["[Content_Types].xml"])
    etree.SubElement(types, f"{{{types.nsmap[None]}}}Override", PartName="/xl/sharedStrings.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml")
    parts["[Content_Types].xml"] = etree.tostring(types)
    relations = etree.fromstring(parts["xl/_rels/workbook.xml.rels"])
    etree.SubElement(relations, f"{{{relations.nsmap[None]}}}Relationship", Id="rIdSharedScope", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings", Target="/xl/sharedStrings.xml")
    parts["xl/_rels/workbook.xml.rels"] = etree.tostring(relations)
    with ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def test_shared_string_legacy_reference_is_revalidated_per_use_then_reused_by_cell(tmp_path):
    from openpyxl import load_workbook

    from forma.core.models import plain_text
    from forma.memory.context import scoped_segment_context, segment_role
    from forma.memory.store import exact_fingerprint

    store = TranslationMemory()
    project = store.create_project("Shared physical uses", "uk", "en")
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline", model="offline", max_parallel_requests=1, parallel_documents=1)
    options = Options("uk", "en", review=True, auto_glossary=False, memory_project_id=project.id)
    source = shared_scope_workbook(tmp_path / "shared.xlsx")
    previous = OfficeDocument(source, extraction_profile=1)
    old_segment = next(item for item in previous.segments if item.plain == "Витрата")
    run = MemoryRun(options, settings, protocol=PROTOCOL_VERSION, memory=store)
    old_context, old_role = scoped_segment_context(previous, old_segment), segment_role(previous, old_segment)
    old = store.save_machine(project.id, exact_fingerprint(run.policy, old_segment.source, old_context, old_role), old_segment.source, "<r0>Expenditure</r0>", old_context, old_role, run.policy, run.generation)

    class PerUseProvider:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.uses = []
            self.calls = 0

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls += 1
            values = []
            for item in payload["segments"]:
                visible = plain_text(item["text"])
                if visible == "Витрата":
                    scope = json.loads(item["neighboring_context"])
                    facts = json.loads(scope["context"])["facts"]
                    assert scope["extraction_profile"] == 2
                    self.uses.append((facts["sheet"], item["id"] in payload.get("drafts", {})))
                    readable = json.dumps(facts, ensure_ascii=False)
                    assert "m³/h" in readable or "UAH" in readable or "EUR" in readable
                    target = "Flow rate" if "m³/h" in readable else "Expenditure"
                else:
                    target = {"Назва": "Name", "Одиниця": "Unit"}.get(visible, visible)
                values.append({"id": item["id"], "text": f"<r0>{target}</r0>"})
            return {"segments": values}

    provider = PerUseProvider()
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", options)
    assert sorted(provider.uses) == [("Finance", True), ("Flow", True)]  # One editor pass per physical use.
    assert result.extraction_profile == 2 and result.adapted_segments == 2
    book = load_workbook(result.output)
    assert book["Finance"]["A2"].value == "Expenditure" and book["Flow"]["A2"].value == "Flow rate"
    with store._connect() as database:
        assert database.execute("SELECT status FROM entries WHERE id=?", (old.entry_id,)).fetchone()[0] == "machine_final"
    repeated = PerUseProvider()
    TranslationEngine(settings, provider=repeated).translate(shared_scope_workbook(tmp_path / "repeat.xlsx"), tmp_path / "out", options)
    assert not repeated.uses and repeated.calls == 0
    changed = PerUseProvider()
    TranslationEngine(settings, provider=changed).translate(shared_scope_workbook(tmp_path / "finance-changed.xlsx", "EUR"), tmp_path / "out", options)
    assert changed.uses and all(sheet == "Finance" for sheet, _draft in changed.uses)


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
    assert sum(len(call["segments"]) for provider in providers for call in provider.calls) == 6
    assert all(len(result.translations) == 5 for result in results)
    assert sum(result.repeated_segments for result in results) == 7


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
    assert sum(len(call["segments"]) for provider in providers for call in provider.calls) == 6
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
    options, settings = Options(auto_glossary=False), Settings()
    old_options = asdict(options)
    old_options.pop("memory_project_id")
    old_options.pop("pdf_mode")  # Routing was absent from the original protocol-5 checkpoint.
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
