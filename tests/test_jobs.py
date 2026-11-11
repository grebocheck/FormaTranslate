from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forma.core.models import FormaError, Options
from forma.providers.client import JobControl
from forma.settings.config import Settings
from forma.translation.jobs import document_resources, run_documents


def documents(tmp_path, count=4):
    result = []
    for i in range(count):
        path = tmp_path / f"document-{i}.docx"
        path.write_bytes(f"document {i}".encode())
        result.append({"id": str(i), "path": str(path)})
    return result


def test_documents_overlap_with_a_bound_and_file_error_does_not_stop_queue(tmp_path):
    docs = documents(tmp_path)
    settings = Settings(base_url="http://localhost/v1", model="test", parallel_documents=2)
    control = JobControl(max_parallel_requests=4)
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = 0
    events = []

    class Engine:
        def __init__(self, settings, shared_control):
            assert shared_control is control
            assert settings.parallel_documents == 2

        def translate(self, source, *_):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                rendezvous.wait(timeout=3)
                if source.stem == "document-0":
                    raise FormaError("Invalid document")
                return source
            finally:
                with lock:
                    active -= 1

    run_documents(
        docs,
        tmp_path,
        Options(),
        settings,
        control,
        lambda *e: events.append(e),
        engine_factory=Engine,
    )
    assert peak == 2
    assert sum(kind == "result" for kind, _ in events) == 3
    assert [p["id"] for kind, p in events if kind == "file_error"] == ["0"]


def test_cancel_does_not_start_waiting_documents_and_marks_them_cancelled(tmp_path):
    docs = documents(tmp_path)
    settings = Settings(base_url="http://localhost/v1", model="test", parallel_documents=1)
    control = JobControl()
    started = threading.Event()
    events = []

    class Engine:
        def __init__(self, *_):
            pass

        def translate(self, *_):
            started.set()
            assert control.cancelled.wait(3)
            control.check()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            run_documents,
            docs,
            tmp_path,
            Options(),
            settings,
            control,
            lambda *e: events.append(e),
            engine_factory=Engine,
        )
        assert started.wait(3)
        control.cancelled.set()
        result.result(timeout=3)
    assert sum(kind == "progress" for kind, _ in events) == 1
    assert {p["id"] for kind, p in events if kind == "cancelled"} == {"0", "1", "2", "3"}


def test_pause_prevents_new_documents_until_resumed(tmp_path):
    control = JobControl()
    control.paused.set()
    started = threading.Event()

    class Engine:
        def __init__(self, *_):
            pass

        def translate(self, *_):
            started.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            run_documents,
            documents(tmp_path, 1),
            tmp_path,
            Options(),
            Settings(),
            control,
            lambda *_: None,
            engine_factory=Engine,
        )
        try:
            assert not started.wait(0.15)
            control.paused.clear()
            assert started.wait(3)
            result.result(timeout=3)
        finally:
            control.cancelled.set()
            control.paused.clear()


def test_cancelled_queue_does_not_read_files(tmp_path, monkeypatch):
    import forma.translation.jobs

    control = JobControl()
    control.cancelled.set()
    events = []

    def unexpected_read(*_):
        raise AssertionError("Cancelled documents must remain unread")

    monkeypatch.setattr(forma.translation.jobs, "document_resources", unexpected_read)
    run_documents(
        documents(tmp_path), tmp_path, Options(), Settings(), control, lambda *e: events.append(e)
    )
    assert sum(kind == "cancelled" for kind, _ in events) == 4


def test_duplicate_content_and_output_names_do_not_race(tmp_path):
    docs = documents(tmp_path, 3)
    # Same content with another name shares its checkpoint.
    Path(docs[1]["path"]).write_bytes(Path(docs[0]["path"]).read_bytes())
    other = tmp_path / "other"
    other.mkdir()
    same_name = other / "document-0.docx"
    same_name.write_bytes(b"different content, colliding output name")
    docs.append({"id": "3", "path": str(same_name)})
    lock = threading.Lock()
    occupied = set()
    overlap = threading.Barrier(2)
    finished = []

    class Engine:
        def __init__(self, *_):
            pass

        def translate(self, source, *_):
            keys = document_resources(source)
            with lock:
                assert occupied.isdisjoint(keys)
                occupied.update(keys)
            try:
                # The independent document can run while conflicting copies wait.
                if source in (Path(docs[0]["path"]), Path(docs[2]["path"])):
                    overlap.wait(timeout=3)
                return source
            finally:
                with lock:
                    occupied.difference_update(keys)

    run_documents(
        docs,
        tmp_path,
        Options(),
        Settings(),
        JobControl(),
        lambda *e: finished.append(e),
        engine_factory=Engine,
    )
    assert sum(kind == "result" for kind, _ in finished) == 4, finished
    assert not any(kind == "file_error" for kind, _ in finished)


def test_live_queue_add_reorder_remove_and_close(tmp_path):
    from forma.translation.jobs import DocumentQueue

    docs = documents(tmp_path, 5)
    queue = DocumentQueue(docs[:3])
    entered, release = threading.Event(), threading.Event()
    translated = []

    class Engine:
        def __init__(self, *_):
            pass

        def translate(self, source, *_):
            translated.append(source.name)
            if source.name == "document-0.docx":
                entered.set()
                assert release.wait(3)
            return source

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_documents,
            queue,
            tmp_path,
            Options(),
            Settings(parallel_documents=1),
            JobControl(),
            lambda *_: None,
            engine_factory=Engine,
        )
        try:
            assert entered.wait(3)
            assert not queue.remove("0")  # Already handed to a worker.
            assert queue.add(docs[3])
            assert not queue.add(docs[3])
            assert queue.remove("1")
            queue.reorder(["3", "2"])
            release.set()
            future.result(timeout=3)
        finally:
            release.set()
    assert translated == ["document-0.docx", "document-3.docx", "document-2.docx"]
    assert not queue.add(docs[4])  # Closed atomically: GUI must keep it ready for next run.


def test_live_queue_can_expand_concurrency_after_start(tmp_path):
    from forma.translation.jobs import DocumentQueue

    docs = documents(tmp_path, 2)
    queue = DocumentQueue(docs[:1])
    entered, both = threading.Event(), threading.Barrier(2)

    class Engine:
        def __init__(self, *_):
            pass

        def translate(self, source, *_):
            entered.set()
            both.wait(timeout=3)
            return source

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_documents,
            queue,
            tmp_path,
            Options(),
            Settings(parallel_documents=2),
            JobControl(),
            lambda *_: None,
            engine_factory=Engine,
        )
        assert entered.wait(3)
        assert queue.add(docs[1])
        future.result(timeout=4)


def test_late_documents_keep_the_captured_configuration_and_pdf_route(tmp_path):
    from forma.translation.jobs import DocumentQueue, RunContext

    docs = documents(tmp_path, 2)
    docs[1]["path"] = str(tmp_path / "scan.pdf")
    Path(docs[1]["path"]).write_bytes(b"Offline route fixture")
    docs[1]["pdfMode"] = "ocr"
    options = Options(glossary={"term": "original"})
    settings = Settings(model="captured", parallel_documents=1)
    context = RunContext.capture(options, settings, tmp_path)
    queue = DocumentQueue(docs[:1])
    entered, release = threading.Event(), threading.Event()
    seen = []

    class Engine:
        def __init__(self, configured, *_):
            self.model = configured.model

        def translate(self, source, folder, effective, *_):
            seen.append((self.model, dict(effective.glossary), effective.pdf_mode))
            if source.suffix == ".docx":
                entered.set()
                assert release.wait(3)
            return source

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_documents, queue, tmp_path, options, settings.with_overrides(model="changed"), JobControl(), lambda *_: None,
            engine_factory=Engine, run_context=context,
        )
        try:
            assert entered.wait(3)
            options.glossary["term"] = "changed"
            assert queue.add(docs[1])
            release.set()
            future.result(timeout=3)
        finally:
            release.set()
    assert seen == [("captured", {"term": "original"}, "auto"), ("captured", {"term": "original"}, "ocr")]
