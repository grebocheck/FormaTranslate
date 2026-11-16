from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from support import pump

from forma.core.models import Cancelled, FormaError
from forma.presentation.backend import Backend
from forma.settings.config import Settings


class FakePreviewSession:
    def __init__(self, cache, *, counts=None):
        self.cache = cache
        self.cache.mkdir()
        self.counts = counts or {}
        self.calls = []
        self.closed = False

    def render(self, path, page, width, cancel):
        assert threading.current_thread() is not threading.main_thread()
        assert not self.closed
        self.calls.append((path, page, width))
        count = self.counts.get(path.name, 3)
        if page >= count:
            return SimpleNamespace(image_path=None, page_count=count, page_index=page, width=0, height=0)
        image = self.cache / f"{len(self.calls)}.png"
        image.write_bytes(b"offline test image")
        return SimpleNamespace(image_path=image, page_count=count, page_index=page, width=width, height=width * 2)

    def close(self):
        assert threading.current_thread() is not threading.main_thread()
        for image in self.cache.iterdir():
            image.unlink()
        self.cache.rmdir()
        self.closed = True


def add_document(backend, tmp_path, *, name="original", output=True, suffix=".pdf"):
    source = tmp_path / f"{name}{suffix}"
    source.write_bytes(b"local fake source")
    translated = tmp_path / f"{name}.uk{suffix}"
    if output:
        translated.write_bytes(b"local fake output")
    document = {"id": name, "name": source.name, "path": str(source), "output": str(translated) if output else "",
                "format": suffix[1:].upper(), "status": "done", "statusText": "Готово", "segments": []}
    backend._documents.append(document)
    backend.selectWorkspaceDocument(name)
    backend._persist_queue()
    return document


def settled(qapp, backend):
    pump(qapp, lambda: not backend._threads and not backend._file_status._inflight and not backend._file_status._pending)


@pytest.fixture
def preview_backend(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    renderer = FakePreviewSession(tmp_path / "preview-cache", counts={"original.pdf": 2, "original.uk.pdf": 3})
    backend._preview._session_factory = lambda: renderer
    yield backend, renderer
    backend._preview.close()
    settled(qapp, backend)


def test_paired_preview_uses_independent_page_counts_without_repeating_last_page(preview_backend, qapp, tmp_path):
    backend, renderer = preview_backend
    add_document(backend, tmp_path)
    settled(qapp, backend)
    changed = []
    backend.changed.connect(lambda: changed.append(True))
    backend.requestDocumentPreview("paired", 3, 900)
    settled(qapp, backend)
    state = backend.previewState
    assert state["source"]["status"] == "unavailable" and state["source"]["pageCount"] == 2
    assert state["source"]["imageUrl"] == "" and state["source"]["page"] == 3
    assert state["translation"]["status"] == "ready" and state["translation"]["pageCount"] == 3
    assert state["translation"]["width"] == 900 and state["translation"]["height"] == 1800
    assert all(call[1] == 2 for call in renderer.calls)
    # Preview completion has its own signal; it cannot recursively trigger requests.
    assert not changed


def test_missing_output_keeps_source_and_file_replacement_invalidates_cache(preview_backend, qapp, tmp_path):
    backend, renderer = preview_backend
    document = add_document(backend, tmp_path, output=False)
    backend.requestDocumentPreview("paired", 1, 800)
    settled(qapp, backend)
    assert backend.previewState["source"]["status"] == "ready"
    assert backend.previewState["translation"]["status"] == "unavailable"
    assert backend.previewState["translation"]["imageUrl"] == ""
    assert len(renderer.calls) == 1
    backend.requestDocumentPreview("paired", 1, 800)
    settled(qapp, backend)
    assert len(renderer.calls) == 1
    Path(document["path"]).write_bytes(b"modified local source content")
    backend.requestDocumentPreview("paired", 1, 800)
    settled(qapp, backend)
    assert len(renderer.calls) == 2
    translated = tmp_path / "new-output.pdf"
    translated.write_bytes(b"new local output")
    document["output"] = str(translated)
    backend.requestDocumentPreview("translation", 1, 800)
    settled(qapp, backend)
    assert backend.previewState["source"]["status"] == "idle"
    assert backend.previewState["translation"]["status"] == "ready"
    translated.unlink()
    backend.requestDocumentPreview("translation", 1, 800)
    settled(qapp, backend)
    assert backend.previewState["translation"]["status"] == "unavailable"
    assert backend.previewState["translation"]["imageUrl"] == ""


@pytest.mark.parametrize("suffix", [".docx", ".pdf"])
def test_completed_queue_restores_page_preview_directly_from_files(qapp, tmp_path, monkeypatch, suffix):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    original = Backend()
    document = add_document(original, tmp_path, suffix=suffix)
    assert original.requestClose()
    restored = Backend()
    settled(qapp, restored)
    assert not restored._documents[0]["segments"] and "result" not in restored._documents[0]
    renderer = FakePreviewSession(tmp_path / "restored-cache")
    restored._preview._session_factory = lambda: renderer

    def no_review():
        pytest.fail("Page preview must not activate report review")

    monkeypatch.setattr(restored, "activateReview", no_review)
    restored.requestDocumentPreview("paired", 1, 1000)
    settled(qapp, restored)
    assert restored.previewState["source"]["status"] == restored.previewState["translation"]["status"] == "ready"
    assert [str(call[0]) for call in renderer.calls] == [document["path"], document["output"]]
    restored._preview.close()
    settled(qapp, restored)
    assert renderer.closed


def test_preview_coalesces_requests_and_cancels_before_new_document(preview_backend, qapp, tmp_path, monkeypatch):
    backend, renderer = preview_backend
    document = add_document(backend, tmp_path)
    started, cancelled = threading.Event(), threading.Event()
    calls = []
    original = renderer.render

    def render(path, page, width, cancel):
        calls.append((path, page, width))
        if len(calls) == 1:
            started.set()
            assert cancel.wait(5)
            cancelled.set()
            raise Cancelled()
        return original(path, page, width, cancel)

    monkeypatch.setattr(renderer, "render", render)
    backend.requestDocumentPreview("original", 1, 800)
    assert started.wait(2)
    backend.requestDocumentPreview("original", 1, 800)
    assert not cancelled.is_set()
    other = add_document(backend, tmp_path, name="other")
    backend.requestDocumentPreview("original", 2, 1000)
    backend.requestDocumentPreview("paired", 1, 1200)
    settled(qapp, backend)
    assert cancelled.is_set()
    assert backend.previewState["documentId"] == other["id"]
    assert backend.previewState["mode"] == "paired"
    assert len(calls) == 3 and all(call[0] != Path(document["path"]) for call in calls[1:])


def test_close_cancels_active_render_and_reaps_private_cache(preview_backend, qapp, tmp_path, monkeypatch):
    backend, renderer = preview_backend
    add_document(backend, tmp_path)
    started = threading.Event()

    def render(path, page, width, cancel):
        started.set()
        assert cancel.wait(5)
        raise Cancelled()

    monkeypatch.setattr(renderer, "render", render)
    backend.requestDocumentPreview("original", 1, 800)
    assert started.wait(2)
    assert not backend.requestClose()
    settled(qapp, backend)
    assert backend._preview.is_closed and renderer.closed and not renderer.cache.exists()
    assert backend.previewState["source"]["imageUrl"] == ""


def test_preview_errors_are_independent_and_do_not_expose_document_contents(preview_backend, qapp, tmp_path, monkeypatch):
    backend, renderer = preview_backend
    add_document(backend, tmp_path)
    original = renderer.render

    def render(path, page, width, cancel):
        if path.name == "original.pdf":
            raise FormaError("Не вдалося підготувати перегляд документа.")
        return original(path, page, width, cancel)

    monkeypatch.setattr(renderer, "render", render)
    backend.requestDocumentPreview("paired", 1, 9999)
    settled(qapp, backend)
    assert backend.previewState["source"]["status"] == "error"
    assert backend.previewState["translation"]["status"] == "ready"
    assert backend.previewState["width"] == 2400
    assert renderer.calls[0][2] == 2400
    url = backend.previewState["translation"]["imageUrl"]
    assert Path(QUrl(url).toLocalFile()).is_file()
    assert backend.message == ""


def test_real_pdf_preview_restores_without_cached_segments_and_cleans_up(qapp, tmp_path, monkeypatch, pdf_file):
    from PySide6.QtGui import QImage

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    document = add_document(backend, tmp_path)
    Path(document["path"]).write_bytes(pdf_file.read_bytes())
    backend._persist_queue()
    settled(qapp, backend)
    assert backend.requestClose()
    restored = Backend()
    settled(qapp, restored)
    assert not restored._documents[0]["segments"]
    restored.requestDocumentPreview("original", 1, 600)
    settled(qapp, restored)
    page = restored.previewState["source"]
    assert page["status"] == "ready", page["message"]
    assert page["pageCount"] > 0 and page["width"] == 600
    image = Path(QUrl(page["imageUrl"]).toLocalFile())
    assert not QImage(str(image)).isNull()
    restored._preview.shutdown()
    assert restored._preview.is_closed and not image.exists()
    assert not restored._threads


def test_external_change_cancellation_never_leaves_loading_forever(preview_backend, qapp, tmp_path, monkeypatch):
    backend, renderer = preview_backend
    add_document(backend, tmp_path)

    def render(*args):
        raise Cancelled("Документ змінився під час підготовки перегляду.")

    monkeypatch.setattr(renderer, "render", render)
    backend.requestDocumentPreview("original", 1, 800)
    settled(qapp, backend)
    assert backend.previewState["source"]["status"] == "error"
    assert not backend.previewState["busy"]
