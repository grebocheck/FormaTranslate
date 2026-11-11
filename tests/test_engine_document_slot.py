"""Prepared OCR sources and raw Office files share checkpoint ownership."""
from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_engine import FakeProvider, local_settings

from forma.core.models import Cancelled, FormaError, Options
from forma.providers.client import JobControl
from forma.translation.engine import TranslationEngine, file_digest


class ObservedControl(JobControl):
    """Expose arrival at the real slot without replacing its synchronization."""

    def __init__(self):
        super().__init__(1)
        self.second_arrived = threading.Event()
        self._arrivals_lock = threading.Lock()
        self._arrivals = 0

    @contextmanager
    def document_slot(self, source_hash):
        with self._arrivals_lock:
            self._arrivals += 1
            if self._arrivals == 2:
                self.second_arrived.set()
        with super().document_slot(source_hash):
            yield


def test_identical_office_sources_share_checkpoint_without_duplicate_paid_work(
    docx_file, tmp_path,
):
    copy = tmp_path / "recognized.ocr.docx"
    shutil.copyfile(docx_file, copy)
    control = ObservedControl()
    first_started, release_first = threading.Event(), threading.Event()

    class BlockedProvider(FakeProvider):
        def complete(self, *args, **kwargs):
            first_started.set()
            assert release_first.wait(5)
            return super().complete(*args, **kwargs)

    first_provider, second_provider = BlockedProvider(), FakeProvider()
    options = Options(source_language="en", target_language="uk", review=False, auto_glossary=False)
    first = TranslationEngine(local_settings(), control, provider=first_provider)
    second = TranslationEngine(local_settings(), control, provider=second_provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        earlier = pool.submit(first.translate, docx_file, tmp_path / "out", options)
        try:
            assert first_started.wait(3)
            later = pool.submit(second.translate, copy, tmp_path / "out", options)
            assert control.second_arrived.wait(3)
            assert not second_provider.calls
            assert not later.done()
        finally:
            release_first.set()
        generated, reused = earlier.result(timeout=5), later.result(timeout=5)

    assert first_provider.calls and not second_provider.calls
    assert generated.cached_segments == 0
    assert reused.cached_segments == len(reused.segments)
    assert generated.translations == reused.translations
    assert generated.output != reused.output
    assert not control._documents_in_use


def test_cancellation_while_waiting_for_checkpoint_releases_ownership_cleanly(
    docx_file, tmp_path,
):
    copy = tmp_path / "recognized.ocr.docx"
    shutil.copyfile(docx_file, copy)
    control = ObservedControl()
    first_started = threading.Event()

    class UntilCancelledProvider(FakeProvider):
        def complete(self, *args, **kwargs):
            first_started.set()
            assert control.cancelled.wait(5)
            control.check()
            raise AssertionError("Cancelled provider must not return translations")

    owner_provider, waiting_provider = UntilCancelledProvider(), FakeProvider()
    options = Options(source_language="en", target_language="uk", review=False, auto_glossary=False)
    owner = TranslationEngine(local_settings(), control, provider=owner_provider)
    waiting = TranslationEngine(local_settings(), control, provider=waiting_provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        earlier = pool.submit(owner.translate, docx_file, tmp_path / "out", options)
        try:
            assert first_started.wait(3)
            later = pool.submit(waiting.translate, copy, tmp_path / "out", options)
            assert control.second_arrived.wait(3)
        finally:
            control.cancelled.set()
        with pytest.raises(Cancelled):
            earlier.result(timeout=5)
        with pytest.raises(Cancelled):
            later.result(timeout=5)

    assert not waiting_provider.calls
    assert not control._documents_in_use
    assert not list((tmp_path / "out").glob("*.docx"))


def test_source_changed_while_waiting_never_uses_stale_checkpoint_ownership(
    docx_file, tmp_path,
):
    control = ObservedControl()
    provider = FakeProvider()
    engine = TranslationEngine(local_settings(), control, provider=provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with control.document_slot(file_digest(docx_file)):
            later = pool.submit(engine.translate, docx_file, tmp_path / "out", Options())
            assert control.second_arrived.wait(3)
            # ZIP still opens with appended bytes; only its exact identity changes.
            docx_file.write_bytes(docx_file.read_bytes() + b"\nchanged while waiting\n")
        with pytest.raises(FormaError, match="Оригінал змінився"):
            later.result(timeout=5)
    assert not provider.calls
    assert not control._documents_in_use
    assert not list((tmp_path / "out").glob("*.docx"))
