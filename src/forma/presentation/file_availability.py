"""Bounded background file-status hints for QML; actions validate files again."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

MAX_PATHS = 8192
BATCH_SIZE = 256


def inspect_files(paths):
    """Run only in a worker, including when bundled into a larger review read."""
    values = {}
    for path in dict.fromkeys(str(path) for path in paths if path):
        try:
            values[path] = Path(path).is_file()
        except (OSError, ValueError):
            values[path] = False
    return values


class FileAvailability(QObject):
    changed = Signal()
    ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.revision = 0
        self._values = OrderedDict()
        self._pending = OrderedDict()
        self._inflight = set()
        self._primed = set()
        self.ready.connect(self._receive)

    def is_file(self, path):
        if not path:
            return False
        path = str(path)
        if path not in self._values:
            self.request((path,))
        return self._values.get(path, False)

    def request(self, paths, *, force=False):
        for path in paths:
            if not path:
                continue
            path = str(path)
            if not force and (path in self._inflight or path in self._values):
                continue
            if len(self._pending) < MAX_PATHS:
                self._pending[path] = None
        self._start()

    def refresh(self):
        self.request((*self._values, *self._inflight, *self._pending), force=True)

    def update(self, values, *, notify=True):
        # A review worker may publish newer status while an older probe runs.
        self._primed.update(self._inflight.intersection(values))
        changed = False
        for path, exists in values.items():
            changed |= path not in self._values or self._values[path] != exists
            self._values[path] = bool(exists)
            self._values.move_to_end(path)
        while len(self._values) > MAX_PATHS:
            self._values.popitem(last=False)
        if changed:
            self.revision += 1
            if notify:
                self.changed.emit()

    def _start(self):
        if self._inflight or not self._pending:
            return
        paths = tuple(list(self._pending)[:BATCH_SIZE])
        for path in paths:
            del self._pending[path]
        self._inflight = set(paths)
        inspect = inspect_files  # Capture dependencies before starting the thread.

        def read():
            values = inspect(paths)
            try:
                self.ready.emit(values)
            except RuntimeError:
                pass  # The window may already have been destroyed.

        threading.Thread(target=read, name="forma-file-status", daemon=True).start()

    @Slot(object)
    def _receive(self, values):
        values = {path: exists for path, exists in values.items() if path not in self._primed}
        self._inflight.clear()
        self._primed.clear()
        self.update(values)
        self._start()
