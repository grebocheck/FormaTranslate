"""Cancellable bridge from a frozen translation run to an external OCR editor."""

from __future__ import annotations

import hashlib
import threading
import uuid
from pathlib import Path

from forma.documents.abbyy import launch_abbyy_handoff


class AbbyyContinuation:
    """Only one editor handoff is presented at a time, including in mixed queues.

    No GUI objects cross worker boundaries. A result is accepted only for the
    current prompt; cancelling a run invalidates even an already open picker.
    The queue owns its settings, memory snapshot and JobControl while waiting.
    """

    def __init__(self, settings, folder, control):
        self.settings, self.folder, self.control = settings, Path(folder), control
        self._editor = threading.Lock()
        self._lock = threading.Lock()
        self._pending = None

    def request(self, document_id, source, language, control, emit, progress):
        while not self._editor.acquire(timeout=0.1):
            control.check()
        prompt = None
        try:
            control.check()
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            control.check()
            launch_abbyy_handoff(source, language, self.settings.abbyy_path)
            control.check()
            prompt = {
                "sourceId": document_id, "name": source.name, "path": str(source),
                "language": language, "sourceSha256": digest,
                "promptId": uuid.uuid4().hex, "showPrompt": True,
                "continueTranslation": True, "importing": False,
            }
            ready = threading.Event()
            with self._lock:
                self._pending = (prompt, ready, None)
            emit("ocr_handoff", dict(prompt))
            progress(0, "Очікуємо DOCX з ABBYY")
            while not ready.wait(0.1):
                control.check()
            control.check()
            with self._lock:
                return self._pending[2]
        finally:
            with self._lock:
                self._pending = None
            if prompt:
                emit("ocr_handoff_closed", {"id": document_id, "promptId": prompt["promptId"]})
            self._editor.release()

    def context(self, prompt_id):
        with self._lock:
            if self.control.cancelled.is_set() or not self._pending:
                return None
            prompt = self._pending[0]
            return dict(prompt) if prompt["promptId"] == prompt_id else None

    def complete(self, prompt_id, output):
        with self._lock:
            if self.control.cancelled.is_set() or not self._pending:
                return False
            prompt, ready, _ = self._pending
            if prompt["promptId"] != prompt_id or ready.is_set():
                return False
            self._pending = (prompt, ready, Path(output))
            ready.set()
            return True
