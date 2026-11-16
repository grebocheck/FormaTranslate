from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from support import pump
from test_preview_controller import FakePreviewSession, add_document, settled

from forma.core.models import Cancelled, FormaError
from forma.presentation.backend import Backend
from forma.settings.config import Settings


class ContinuousSession(FakePreviewSession):
    def __init__(self, cache, *, counts=None):
        super().__init__(cache, counts=counts)
        self.descriptions = []
        self.windows = []

    def describe(self, path, cancel):
        assert threading.current_thread() is not threading.main_thread()
        self.descriptions.append(path)
        count = self.counts.get(path.name, 96)
        return SimpleNamespace(page_count=count, page_sizes=tuple((612.0, 792.0) for _ in range(count)))

    def retain_pages(self, pages):
        assert threading.current_thread() is not threading.main_thread()
        self.windows.append(pages)

    def render(self, path, page, width, cancel):
        assert threading.current_thread() is not threading.main_thread()
        self.calls.append((path, page, width))
        image = self.cache / f"{path.name}-{page}-{width}.png"
        image.write_bytes(b"local test image")
        return SimpleNamespace(image_path=image, page_count=self.counts.get(path.name, 96),
                               page_index=page, width=width, height=round(width * 792 / 612))


@pytest.fixture
def continuous(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    backend = Backend()
    renderer = ContinuousSession(tmp_path / "continuous-cache")
    backend._preview._session_factory = lambda: renderer
    yield backend, renderer
    backend._preview.close()
    settled(qapp, backend)


def row(backend, page):
    model = backend.previewPageModel
    return model.data(model.index(page - 1), model.ROW_ROLE)


def test_continuous_metadata_is_lazy_and_range_is_bounded_incremental_and_stable(continuous, qapp, tmp_path):
    backend, renderer = continuous
    add_document(backend, tmp_path)
    resets, updates, changes = [], [], []
    backend.previewPageModel.modelReset.connect(lambda: resets.append(True))
    backend.previewPageModel.dataChanged.connect(lambda *_: updates.append(True))
    backend.previewChanged.connect(lambda: changes.append(True))
    backend.openDocumentPreview("paired", 901)
    settled(qapp, backend)
    assert backend.previewPageModel.rowCount() == backend.previewState["pageCount"] == 96
    assert len(renderer.descriptions) == 2 and not renderer.calls
    assert row(backend, 40)["source"]["width"] == 612
    backend.requestDocumentPreviewRange("paired", 40, 96, 901)
    settled(qapp, backend)
    assert len(renderer.calls) == len(renderer.windows[-1]) == 12
    assert {call[1] for call in renderer.calls} == set(range(39, 45))
    page = row(backend, 40)["source"]
    assert (page["width"], page["height"], page["imageWidth"]) == (612, 792, 901)
    assert row(backend, 46)["source"]["imageUrl"] == ""
    assert not resets and len(updates) == 6
    before = len(changes), len(updates)
    backend.requestDocumentPreviewRange("paired", 40, 45, 901)
    backend.openDocumentPreview("paired", 901)
    settled(qapp, backend)
    # Identical cached images must not emit previewChanged and restart QML's viewport timer.
    assert (len(changes), len(updates)) == before and len(renderer.descriptions) == 2
    backend.requestDocumentPreviewRange("paired", 80, 82, 901)
    settled(qapp, backend)
    assert row(backend, 40)["source"]["imageUrl"] == ""
    assert row(backend, 80)["source"]["imageUrl"]
    assert backend._preview._pages.loaded == {80, 81, 82}
    assert len(renderer.windows[-1]) == 6


def test_paired_continuous_preview_never_repeats_shorter_document(continuous, qapp, tmp_path):
    backend, renderer = continuous
    renderer.counts["original.pdf"] = 2
    add_document(backend, tmp_path)
    backend.openDocumentPreview("paired", 800)
    settled(qapp, backend)
    backend.requestDocumentPreviewRange("paired", 2, 4, 800)
    settled(qapp, backend)
    assert backend.previewState["source"]["pageCount"] == 2
    assert row(backend, 3)["source"]["status"] == "unavailable"
    assert not row(backend, 3)["source"]["imageUrl"]
    assert row(backend, 3)["translation"]["imageUrl"]
    assert [page for path, page, _ in renderer.calls if path.name == "original.pdf"] == [1]


def test_continuous_file_replacement_reloads_geometry_and_clears_old_rasters(continuous, qapp, tmp_path):
    backend, renderer = continuous
    document = add_document(backend, tmp_path, output=False)
    backend.openDocumentPreview("paired", 800)
    settled(qapp, backend)
    assert backend.previewState["translation"]["status"] == "unavailable"
    backend.requestDocumentPreviewRange("paired", 1, 2, 800)
    settled(qapp, backend)
    assert row(backend, 1)["source"]["imageUrl"]
    Path(document["path"]).write_bytes(b"updated source PDF bytes")
    renderer.counts["original.pdf"] = 12
    backend.openDocumentPreview("paired", 800)
    settled(qapp, backend)
    assert backend.previewPageModel.rowCount() == 12
    assert row(backend, 1)["source"]["imageUrl"] == ""
    assert len(renderer.descriptions) == 2 and len(renderer.calls) == 2
    Path(document["path"]).unlink()
    backend.openDocumentPreview("paired", 800)
    settled(qapp, backend)
    assert backend.previewState["source"]["status"] == "unavailable"
    assert backend.previewPageModel.rowCount() == 0


def test_continuous_fast_scroll_coalesces_and_discards_cancelled_page_images(continuous, qapp, tmp_path, monkeypatch):
    backend, renderer = continuous
    add_document(backend, tmp_path)
    backend.openDocumentPreview("original", 800)
    settled(qapp, backend)
    started, cancelled = threading.Event(), threading.Event()
    original = renderer.render

    def render(path, page, width, cancel):
        if not started.is_set():
            started.set()
            assert cancel.wait(5)
            cancelled.set()
            raise Cancelled()
        return original(path, page, width, cancel)

    monkeypatch.setattr(renderer, "render", render)
    backend.requestDocumentPreviewRange("original", 1, 6, 800)
    assert started.wait(2)
    backend.requestDocumentPreviewRange("original", 1, 6, 800)
    assert not cancelled.is_set()
    backend.requestDocumentPreviewRange("original", 40, 45, 800)
    backend.requestDocumentPreviewRange("original", 80, 85, 800)
    settled(qapp, backend)
    assert cancelled.is_set()
    assert {page for _, page, _ in renderer.calls} == set(range(79, 85))
    assert row(backend, 1)["source"]["imageUrl"] == ""
    assert row(backend, 40)["source"]["imageUrl"] == ""
    assert row(backend, 80)["source"]["imageUrl"]


def test_continuous_description_errors_leave_other_side_readable(continuous, qapp, tmp_path, monkeypatch):
    backend, renderer = continuous
    add_document(backend, tmp_path)
    original = renderer.describe

    def describe(path, cancel):
        if path.name == "original.pdf":
            raise FormaError("Не вдалося підготувати перегляд документа.")
        return original(path, cancel)

    monkeypatch.setattr(renderer, "describe", describe)
    backend.openDocumentPreview("paired", 800)
    settled(qapp, backend)
    backend.requestDocumentPreviewRange("paired", 1, 2, 800)
    settled(qapp, backend)
    assert row(backend, 1)["source"]["status"] == "error"
    assert row(backend, 1)["translation"]["status"] == "ready"
    assert len(renderer.calls) == 2


def test_continuous_real_pdf_restores_from_files_and_reaps_worker_cache(qapp, tmp_path, monkeypatch, pdf_file):
    from PySide6.QtGui import QImage

    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    original = Backend()
    document = add_document(original, tmp_path, output=False)
    Path(document["path"]).write_bytes(pdf_file.read_bytes())
    original._persist_queue()
    settled(qapp, original)
    assert original.requestClose()
    restored = Backend()
    restored.openDocumentPreview("original", 600)
    settled(qapp, restored)
    assert restored.previewPageModel.rowCount() > 0
    assert not restored._documents[0]["segments"] and "result" not in restored._documents[0]
    restored.requestDocumentPreviewRange("original", 1, 1, 600)
    settled(qapp, restored)
    page = row(restored, 1)["source"]
    assert page["status"] == "ready", page["message"]
    image = Path(QUrl(page["imageUrl"]).toLocalFile())
    assert not QImage(str(image)).isNull()
    restored._preview.shutdown()
    assert restored._preview.is_closed and not image.exists()
    assert not restored._threads and not restored.previewPageModel.rowCount()


def test_continuous_close_cancels_metadata_and_does_not_publish_late_rows(continuous, qapp, tmp_path, monkeypatch):
    backend, renderer = continuous
    add_document(backend, tmp_path)
    started = threading.Event()

    def describe(path, cancel):
        started.set()
        assert cancel.wait(5)
        raise Cancelled()

    monkeypatch.setattr(renderer, "describe", describe)
    backend.openDocumentPreview("original", 800)
    assert started.wait(2)
    assert not backend.requestClose()
    pump(qapp, lambda: backend._preview.is_closed and not backend._threads)
    assert not backend.previewPageModel.rowCount() and renderer.closed
