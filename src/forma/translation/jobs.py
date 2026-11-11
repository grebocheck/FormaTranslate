"""A bounded document queue; workers never touch GUI objects."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from forma.core.models import Cancelled, FormaError, TranslationFailure
from forma.memory.session import MemoryRun
from forma.translation.engine import PROTOCOL_VERSION, TranslationEngine


class DocumentQueue:
    """Atomic handoff between the GUI and scheduler; claimed files cannot be removed."""

    def __init__(self, documents=()):
        self._lock = threading.Lock()
        self._pending = [dict(d) for d in documents]
        self._closed = False

    def snapshot(self):
        with self._lock:
            return list(self._pending)

    def add(self, document):
        with self._lock:
            if self._closed or any(d["id"] == document["id"] for d in self._pending):
                return False
            self._pending.append(dict(document))
            return True

    def remove(self, document_id):
        with self._lock:
            for i, document in enumerate(self._pending):
                if document["id"] == document_id:
                    self._pending.pop(i)
                    return True
            return False

    def contains(self, document_id):
        with self._lock:
            return any(d["id"] == document_id for d in self._pending)

    def reorder(self, ids):
        with self._lock:
            positions = {did: i for i, did in enumerate(ids)}
            self._pending.sort(key=lambda d: positions.get(d["id"], len(positions)))

    def finish_if_empty(self):
        with self._lock:
            if self._pending:
                return False
            self._closed = True
            return True

    def cancel_pending(self):
        with self._lock:
            pending, self._pending = self._pending, []
            self._closed = True
            return pending


def document_resources(source: Path, control=None) -> set[tuple[str, str]]:
    # Serialize matching output basenames and identical inputs. PDF scans now
    # feed Office checkpoints too; parallel copies must not race that paid work.
    resources = {("output", source.name.casefold())}
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while True:
            if control:
                control.check()
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    resources.add(("content", digest.hexdigest()))
    return resources


def run_documents(
    documents, folder, options, settings, control, emit, *, engine_factory=None, pdf_handoff=None,
):
    dynamic = isinstance(documents, DocumentQueue)
    pending = documents if dynamic else DocumentQueue(documents)
    initial = pending.snapshot()
    engine_factory = engine_factory or TranslationEngine
    workers = min(settings.parallel_documents, settings.max_parallel_requests)
    if not dynamic:
        workers = min(workers, max(1, len(initial)))
    # Split the request allowance fairly between simultaneous documents. All
    # engines additionally share the same JobControl request budget.
    settings = settings.with_overrides(parallel_documents=workers)
    resource_cache = {}
    for document in initial:
        emit("queued", {"id": document["id"]})
    try:
        memory = MemoryRun(options, settings, protocol=PROTOCOL_VERSION) if options.memory_project_id else None
    except FormaError as exc:
        for document in pending.cancel_pending():
            emit("file_error", {"id": document["id"], "error": str(exc)})
        return

    def translate(document, document_settings):
        did = document["id"]
        try:
            control.check()
            emit("progress", {"id": did, "percent": 0, "text": "Починаємо переклад"})
            engine = engine_factory(document_settings, control)
            if memory is not None and isinstance(engine, TranslationEngine):
                engine.memory_run = memory
            if isinstance(engine, TranslationEngine):
                engine.preparation_progress = lambda percent, text: emit(
                    "progress", {"id": did, "percent": percent, "text": text, "phase": "preparing"}
                )
                if pdf_handoff is not None:
                    engine.pdf_handoff = lambda source, language, shared_control, progress: pdf_handoff.request(
                        did, source, language, shared_control, emit, progress,
                    )
            result = engine.translate(
                Path(document["path"]),
                folder,
                options,
                lambda percent, text: emit(
                    "progress", {"id": did, "percent": percent, "text": text, "phase": "translating"}
                ),
            )
            emit("result", {"id": did, "result": result})
        except Cancelled as exc:
            emit("cancelled", {"id": did, "error": str(exc)})
        except TranslationFailure as exc:
            emit("file_error", {"id": did, "error": str(exc),
                                "diagnosticReport": str(exc.report) if exc.report else ""})
        except FormaError as exc:
            emit("file_error", {"id": did, "error": str(exc)})
        except Exception:
            # Keep other documents running; never expose raw provider exceptions
            # (which may carry request headers or document content) in the UI.
            emit(
                "file_error",
                {
                    "id": did,
                    "error": "Не вдалося перекласти документ. Перевірте файл і налаштування.",
                },
            )

    active = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="forma-document") as pool:
        while True:
            if control.cancelled.is_set():
                for document in pending.cancel_pending():
                    emit(
                        "cancelled",
                        {"id": document["id"], "error": "Зупинено до початку перекладу."},
                    )
            elif not control.paused.is_set():
                occupied = set().union(*active.values()) if active else set()
                for document in pending.snapshot():
                    if len(active) >= workers:
                        break
                    did = document["id"]
                    # Prepare only as a worker becomes available, so large queues
                    # begin translating immediately and cancelled files stay unread.
                    if did not in resource_cache:
                        try:
                            control.check()
                            resource_cache[did] = document_resources(
                                Path(document["path"]), control
                            )
                        except Cancelled:
                            break
                        except OSError:
                            if pending.remove(did):
                                emit(
                                    "file_error",
                                    {"id": did, "error": "Не вдалося прочитати документ."},
                                )
                            continue
                    resources = resource_cache[did]
                    if occupied.isdisjoint(resources) and pending.remove(did):
                        # A lone file keeps the full request budget. Files added
                        # later share the global semaphore with it; creating an
                        # expandable worker pool must not halve single-file speed.
                        expected = min(workers, len(active) + 1 + len(pending.snapshot()))
                        document_settings = settings.with_overrides(parallel_documents=expected)
                        active[pool.submit(translate, document, document_settings)] = resources
                        occupied.update(resources)
            if active:
                finished, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in finished:
                    future.result()
                    del active[future]
            if not active:
                if pending.finish_if_empty():
                    break
                control.cancelled.wait(0.1)
