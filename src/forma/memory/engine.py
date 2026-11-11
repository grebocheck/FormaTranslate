"""Exact project memory around the ordinary Office translator and its validators."""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace

from forma.core.models import FormaError, Segment, TranslationResult
from forma.documents.ooxml import OfficeDocument
from forma.memory.context import segment_context, segment_role
from forma.memory.session import MemoryRun
from forma.memory.store import exact_fingerprint, generation_fingerprint, policy_fingerprint
from forma.persistence.files import atomic_json, available_output
from forma.providers.client import Provider
from forma.translation.engine import (
    PROTOCOL_VERSION,
    batched,
    blocking_issues,
    document_context,
    file_digest,
    load_checkpoint,
    translation_issues,
    use_english_decimals,
)


def _changed_spans(previous, current):
    """A linear-time bounding diff; full sources remain the editing authority.

    Repeated formatting tags can make a character sequence matcher quadratic.
    This navigation aid keeps the entire region between the first/last change.
    """
    prefix = 0
    for old, new in zip(previous, current, strict=False):
        if old != new:
            break
        prefix += 1
    suffix = 0
    limit = min(len(previous), len(current)) - prefix
    while suffix < limit and previous[len(previous) - suffix - 1] == current[len(current) - suffix - 1]:
        suffix += 1
    old = previous[prefix:len(previous) - suffix if suffix else len(previous)]
    new = current[prefix:len(current) - suffix if suffix else len(current)]
    return [{"previous": old, "current": new}] if old or new else []


def translate_with_memory(engine, source, folder, options, progress):
    started = time.monotonic()
    settings, control = engine.settings, engine.control
    run = engine.memory_run or MemoryRun(options, settings, protocol=PROTOCOL_VERSION)
    if run.snapshot.project_id != options.memory_project_id:
        raise FormaError("Проєкт пам’яті змінився після початку черги. Запустіть переклад знову.")
    if (
        policy_fingerprint(options, run.glossary) != run.policy
        or generation_fingerprint(settings, protocol=PROTOCOL_VERSION, review=options.review) != run.generation
    ):
        raise FormaError("Правила або модель перекладу змінилися після початку черги. Запустіть переклад знову.")
    options = replace(options, glossary=dict(run.glossary), auto_glossary=False)
    options.validate()
    progress(1, "Читаємо структуру документа")
    source_hash = file_digest(source)
    document = OfficeDocument(source)
    if file_digest(source) != source_hash:
        raise FormaError("Оригінал змінився під час читання. Повторіть переклад.")
    if not document.segments:
        raise FormaError("У документі немає тексту для перекладу. Текст усередині зображень потребує OCR.")
    if any(len(segment.source) > 26000 for segment in document.segments):
        raise FormaError("Документ містить абзац понад 26 000 символів. Розділіть його перед перекладом.")
    checkpoint, state = load_checkpoint(
        source_hash, options, settings, memory_policy=run.policy, memory_generation=run.generation,
        legacy_generations=run.legacy_generations,
    )
    translations, drafts, kinds, references = {}, {}, {}, {}
    draft_hits = set()
    for segment in document.segments:
        for stage, destination in (("translations", translations), ("drafts", drafts)):
            values = state.get(stage)
            candidate = values.get(segment.id) if isinstance(values, dict) else None
            if isinstance(candidate, str) and not blocking_issues(
                segment, candidate, options, draft=stage == "drafts" and options.review
            ):
                destination[segment.id] = candidate
                if stage == "translations":
                    kinds[segment.id] = "checkpoint"
                    saved = state.get("memory_references", {})
                    entry = saved.get(segment.id) if isinstance(saved, dict) else None
                    if (
                        isinstance(entry, dict)
                        and type(entry.get("entry_id")) is int and entry["entry_id"] > 0
                        and type(entry.get("revision")) is int and entry["revision"] >= 0
                        and entry.get("status") in {"machine_final", "human_approved"}
                    ):
                        references[segment.id] = {
                            "entry_id": entry["entry_id"], "revision": entry["revision"],
                            "status": entry["status"], "kind": "checkpoint",
                        }

    groups, keys, contexts, roles = {}, {}, {}, {}
    for segment in document.segments:
        context, role = segment_context(document, segment), segment_role(document, segment)
        key = exact_fingerprint(run.policy, segment.source, context, role)
        groups.setdefault(key, []).append(segment)
        keys[segment.id], contexts[key], roles[key] = key, context, role

    lock = threading.RLock()
    owned, borrowed = {}, {}
    claimed = set()
    adaptations = {}
    provider = engine.provider
    stopped = threading.Event()

    def check_identity(hit, members):
        key = keys[members[0].id]
        if (
            hit.project_id != run.snapshot.project_id or hit.entry_key != key
            or hit.source_markup != members[0].source or hit.context != contexts[key]
            or hit.role != roles[key] or hit.policy != run.policy
        ):
            raise FormaError("Запис пам’яті не відповідає поточному фрагменту. Перевірте цілісність бази пам’яті.")

    def remember(hit, members, kind):
        check_identity(hit, members)
        for member in members:
            if blocking_issues(member, hit.target, options):
                raise FormaError("Переклад із пам’яті не пройшов перевірку форматування, чисел або кодів.")
        hit = run.adopt(hit)
        for member in members:
            translations[member.id] = hit.target
            kinds[member.id] = kind
            references[member.id] = {
                "entry_id": hit.entry_id, "revision": hit.revision, "status": hit.status, "kind": kind,
            }
        return hit

    def persist():
        state["translations"], state["drafts"] = translations, drafts
        state["memory_references"] = references
        atomic_json(checkpoint, state)

    def save_stage(values, *, reviewed):
        with lock:
            for sid, candidate in values.items():
                key = keys[sid]
                members = groups[key]
                destination = translations if reviewed else drafts
                for member in members:
                    if blocking_issues(member, candidate, options, draft=not reviewed and options.review):
                        raise FormaError("Повторний фрагмент не пройшов перевірку перекладу.")
                    destination[member.id] = candidate
                # Preserve validated paid work even if a later unit/job fails.
                persist()
                previous = adaptations.get(sid)
                provenance = {"source_sha256": source_hash, "segment_id": sid, "model": settings.model}
                if previous is not None:
                    provenance.update(kind="adaptation", source_entry_id=previous.entry_id)
                hit = run.memory.save_machine(
                    run.snapshot.project_id, key, members[0].source, candidate,
                    contexts[key], roles[key], run.policy, run.generation,
                    status="machine_final" if reviewed else "draft",
                    provenance=provenance,
                )
                if reviewed:
                    for member in members:
                        kind = ("adapted" if previous is not None else "generated") if member.id == sid else "repeat"
                        kinds[member.id] = kind
                        references[member.id] = {
                            "entry_id": hit.entry_id, "revision": hit.revision, "status": hit.status, "kind": kind,
                        }
                        if previous is not None:
                            references[member.id]["source_entry_id"] = previous.entry_id
                    persist()
                    run.complete(key, hit)

    def report_stage(message):
        with lock:
            progress(5 + int(88 * len(translations) / len(document.segments)), message)

    try:
        progress(3, "Шукаємо точні переклади в пам’яті проєкту")
        available = run.lookup_many(groups, include_draft=options.review)
        for key, members in groups.items():
            control.check()
            representative = members[0]
            # Explicit preferred revisions always precede older machine checkpoints.
            hit = available.get(key)
            if hit is not None:
                check_identity(hit, members)
            if hit is not None and hit.status == "human_approved":
                remember(hit, members, "memory")
                continue
            cached = {translations[member.id] for member in members if member.id in translations}
            if len(cached) > 1:
                raise FormaError("У кеші є різні переклади одного точного фрагмента. Виберіть еталонний варіант у пам’яті проєкту.")
            # Publish cached results through the same coordination as new work.
            # A different file may have read the database just before this file
            # seeds its checkpoint; it must still await this shared result.
            owner, future = run.claim(key)
            if not owner:
                borrowed[key] = future
                continue
            claimed.add(key)
            if len(cached) == 1:
                value = next(iter(cached))
                hit = run.memory.save_machine(
                    run.snapshot.project_id, key, representative.source, value,
                    contexts[key], roles[key], run.policy, run.generation,
                    provenance={"kind": "checkpoint", "source_sha256": source_hash, "segment_id": representative.id},
                )
                for member in members:
                    if member.id not in translations:
                        translations[member.id] = value
                        kinds[member.id] = "repeat"
                    references[member.id] = {
                        "entry_id": hit.entry_id, "revision": hit.revision,
                        "status": hit.status, "kind": kinds[member.id],
                    }
                run.complete(key, hit)
                continue
            if hit is not None and hit.status != "draft" and not blocking_issues(representative, hit.target, options):
                hit = remember(hit, members, "memory")
                run.complete(key, hit)
                continue
            if hit is not None and hit.status == "draft" and not blocking_issues(representative, hit.target, options, draft=True):
                for member in members:
                    drafts[member.id] = hit.target
                    kinds[member.id] = "draft_memory"
                    draft_hits.add(member.id)
            if options.review and representative.id not in drafts:
                previous = run.similar(representative.source, roles[key], contexts[key])
                if previous is not None and not blocking_issues(
                    Segment("reference", "", previous.source_markup), previous.target, options,
                ):
                    adaptations[representative.id] = previous
            owned[key] = representative
        with lock:
            persist()
        pending = list(owned.values())
        if pending:
            provider = provider or Provider(settings, control)
        common = {
            "source_language": options.source_language,
            "target_language": options.target_language,
            "domain": options.domain,
            "glossary": dict(run.glossary),
            "document_context": document_context(document.segments),
            "number_format": "Use decimal points, preserving all digits and precision; do not add thousands separators."
            if use_english_decimals(options) else "Preserve source numeric notation.",
        }

        def process_batch(batch):
            if stopped.is_set():
                return
            try:
                control.check()
                report_stage(f"Перекладаємо · пакет із {len(batch)} фрагментів")
                with lock:
                    untranslated = [segment for segment in batch if segment.id not in drafts and segment.id not in adaptations]
                if untranslated:
                    engine._stage(
                        untranslated, common, options, provider,
                        lambda values: save_stage(values, reviewed=not options.review),
                        progress=report_stage,
                    )
                if options.review:
                    control.check()
                    report_stage(f"Редакторська перевірка · {len(batch)} фрагментів")
                    with lock:
                        batch_drafts = {
                            segment.id: adaptations[segment.id].target if segment.id in adaptations else drafts[segment.id]
                            for segment in batch
                        }
                    adaptation_references = {}
                    for segment in batch:
                        previous = adaptations.get(segment.id)
                        if previous is None:
                            continue
                        adaptation_references[segment.id] = {
                            "previous_source": previous.source_markup,
                            "previous_context": previous.context,
                            "changed_source_spans": _changed_spans(previous.source_markup, segment.source),
                        }
                    if adaptation_references:
                        report_stage("Адаптуємо переклад до змін у фрагменті")
                    engine._stage(
                        batch, common, options, provider,
                        lambda values: save_stage(values, reviewed=True),
                        drafts=batch_drafts, memory_references=adaptation_references, progress=report_stage,
                    )
            except BaseException:
                stopped.set()
                raise

        # Process every owned unit before awaiting shared units. Claiming keys
        # for not-yet-started document workers or waiting inside the batch pool
        # could otherwise create cycles with a one-request budget.
        workers = max(1, min(settings.max_parallel_requests, control.max_parallel_requests) // settings.parallel_documents)
        batches = iter(batched(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="forma-memory-batch") as pool:
            active = set()

            def fill_workers():
                while len(active) < workers and not stopped.is_set():
                    control.check()
                    batch = next(batches, None)
                    if batch is None:
                        break
                    active.add(pool.submit(process_batch, batch))

            try:
                fill_workers()
                while active:
                    completed, active = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        future.result()
                    control.check()
                    fill_workers()
            except BaseException:
                stopped.set()
                for future in active:
                    future.cancel()
                raise
        for key, future in borrowed.items():
            report_stage("Очікуємо спільний переклад фрагмента")
            while not future.done():
                control.check()
                control.cancelled.wait(0.1)
            control.check()
            with lock:
                remember(future.result(), groups[key], "repeat")
                persist()
        control.check()
        if len(translations) != len(document.segments):
            raise FormaError("Експорт зупинено: переклад містить не всі фрагменти.")
        issues = [
            *document.issues,
            *(issue for segment in document.segments for issue in translation_issues(segment, translations[segment.id], options)),
        ]
        folder.mkdir(parents=True, exist_ok=True)
        output = available_output(source, options.target_language, folder)
        progress(96, "Зберігаємо документ та звіт")
        control.check()
        if file_digest(source) != source_hash:
            raise FormaError("Оригінал змінився під час перекладу. Повторіть запуск із поточним файлом.")
        document.write(translations, output, decimal_comma_to_point=use_english_decimals(options))
        result = TranslationResult(
            source, output, document.segments, translations, issues,
            input_tokens=provider.input_tokens if provider is not None else 0,
            output_tokens=provider.output_tokens if provider is not None else 0,
            cached_segments=sum(kind == "checkpoint" for kind in kinds.values()),
            elapsed_seconds=time.monotonic() - started,
            memory_project_id=run.snapshot.project_id,
            memory_segments=sum(kind == "memory" for kind in kinds.values()),
            repeated_segments=sum(kind == "repeat" for kind in kinds.values()),
            draft_memory_segments=len(draft_hits),
            adapted_segments=sum(kind == "adapted" for kind in kinds.values()),
            memory_revision=run.snapshot.approved_revision,
            memory_references=references,
            effective_glossary=dict(run.glossary),
            source_sha256=source_hash,
            output_sha256=file_digest(output),
        )
        from forma.translation.reports import write_translation_report

        write_translation_report(result, options, settings.model)
        progress(100, "Переклад готовий")
        return result
    finally:
        error = FormaError("Спільний переклад фрагмента не завершено. Повторіть переклад документа.")
        for key in claimed:
            run.fail(key, error)
        if engine.provider is None and provider is not None:
            provider.close()
