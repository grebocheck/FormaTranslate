"""Asynchronous page previews; requests and private render caches stay local."""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, Signal

from forma.core.models import Cancelled, FormaError
from forma.localization.catalog import translate_ui_message as tr
from forma.presentation.preview_pages import ContinuousPages

SIDES = ("source", "translation")
MODES = {"original", "translation", "paired"}


def _new_session():
    from forma.documents.preview import PreviewSession

    return PreviewSession()


def _signature(path):
    if not path:
        return None
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PreviewRequest:
    document_id: str
    source: str
    translation: str
    mode: str
    page: int
    width: int
    kind: str = "page"
    first: int = 1
    last: int = 1

    def active(self, side):
        return self.mode == "paired" or side == ("source" if self.mode == "original" else "translation")


def _side(request, side, *, status="idle", message=""):
    path = getattr(request, side)
    return {"status": status, "message": message, "page": request.page, "pageCount": 0,
            "imageUrl": "", "width": 0, "height": 0, "format": Path(path).suffix[1:].upper() if path else ""}


def _state(request, *, loading=False):
    return {"documentId": request.document_id, "mode": request.mode, "page": request.page,
            "width": request.width, "busy": loading,
            **{side: _side(request, side, status="loading" if loading and request.active(side) else "idle")
               for side in SIDES}}


class PreviewController(QObject):
    changed = Signal()
    closed = Signal()

    def __init__(self, parent, *, selected, launch):
        super().__init__(parent)
        self._selected, self._launch = selected, launch
        self._session_factory = _new_session
        self._session = None
        self._request = None
        self._completed = None
        self._pending = None
        self._running = False
        self._active_request = None
        self._cancel = None
        self._generation = 0
        self._closed = False
        self._cleaning = False
        self._pages = ContinuousPages(self)
        self._probe_after_range = False
        self._state = _state(PreviewRequest("", "", "", "original", 1, 1000))

    def state(self):
        return {**self._state, **{side: {**self._state[side], "message": tr(self._state[side]["message"])}
                                  for side in SIDES}}

    def retranslate(self):
        self._pages.model.retranslate()
        self.changed.emit()

    def _work_session(self):
        if self._session is None:
            self._session = self._session_factory()
        return self._session

    @staticmethod
    def _continuous_key(request):
        return ContinuousPages.base(request), request.width

    def _continuous_request(self, mode, width, *, kind="metadata", first=1, last=1):
        if self._closed or mode not in MODES or type(width) is not int:
            return None
        document = self._selected() or {}
        return PreviewRequest(document.get("id", ""), document.get("path", ""), document.get("output", ""),
                              mode, 1, max(256, min(width, 2400)), kind, first, last)

    def open(self, mode, width):
        request = self._continuous_request(mode, width)
        if request is None:
            return
        if (self._request and self._request.kind != "page"
                and self._continuous_key(self._request) == self._continuous_key(request)):
            if self._running:
                self._probe_after_range |= self._active_request.kind == "range"
            elif request.document_id:
                self._start(request, self._generation)
            return
        self._submit_continuous(request)

    def request_range(self, mode, first, last, width):
        if type(first) is not int or type(last) is not int:
            return
        first = max(1, min(first, 10000))
        request = self._continuous_request(mode, width, kind="range", first=first,
                                           last=max(first, min(last, first + 5, 10000)))
        if request is None:
            return
        if self._running and request == self._active_request and request == self._request:
            return
        self._submit_continuous(request)

    def _submit_continuous(self, request):
        metadata = self._pages.metadata
        same_document = metadata and metadata["identity"][0] == ContinuousPages.base(request)
        if not same_document:
            self._pages.clear()
            metadata = None
        if request != self._request:
            self._generation += 1
            self._request = request
        if request.kind == "range":
            self._pages.retain_range(request.first, request.last)
        self._update(ContinuousPages.state(request, metadata, loading=bool(request.document_id and not metadata)))
        if not request.document_id:
            if self._cancel:
                self._cancel.set()
            self._pending = None
            return
        if self._running:
            self._cancel.set()
            self._pending = request, self._generation
            return
        self._start(request, self._generation)

    def _matches_document(self, request):
        document = self._selected() or {}
        return (document.get("id", ""), document.get("path", ""), document.get("output", "")) == (
            request.document_id, request.source, request.translation,
        )

    def _update(self, state):
        if self._state != state:
            self._state = state
            self.changed.emit()

    def request(self, mode, page, width):
        if self._closed or mode not in MODES or type(page) is not int or type(width) is not int:
            return
        self._pages.clear()
        document = self._selected() or {}
        request = PreviewRequest(document.get("id", ""), document.get("path", ""), document.get("output", ""),
                                 mode, max(1, min(page, 100_000)), max(256, min(width, 2400)))
        if self._running and request == self._active_request and request == self._request:
            return
        if request != self._request:
            self._generation += 1
            self._request = request
            self._update(_state(request, loading=bool(request.document_id)))
        if not request.document_id:
            if self._cancel:
                self._cancel.set()
            self._pending = None
            self._update(_state(request))
            return
        if self._running:
            self._cancel.set()
            self._pending = (request, self._generation)
            return
        self._start(request, self._generation)

    def _render(self, request, cancel, previous, emit):
        for _attempt in range(2):
            if cancel.is_set():
                raise Cancelled()
            signatures = {side: _signature(getattr(request, side)) for side in SIDES if request.active(side)}
            if (previous and previous[0] == request and previous[1] == signatures
                    and all(path.is_file() for path in previous[3])
                    and all(previous[2][side]["status"] != "error" for side in SIDES)):
                return previous
            emit("preview_loading", None)
            state, images = _state(request), []
            for side in SIDES:
                if not request.active(side):
                    continue
                if cancel.is_set():
                    raise Cancelled()
                path = getattr(request, side)
                if not path:
                    state[side] = _side(request, side, status="unavailable", message=(
                        "Переклад ще не створено." if side == "translation" else "Виберіть документ для перегляду."
                    ))
                elif Path(path).suffix.lower() not in {".pdf", ".docx"}:
                    state[side] = _side(request, side, status="unavailable",
                                        message="Попередній перегляд сторінок доступний для PDF і DOCX.")
                elif signatures[side] is None:
                    state[side] = _side(request, side, status="unavailable",
                                        message="Файл недоступний. Відкрийте його знову або додайте файл.")
                else:
                    try:
                        if self._session is None:
                            self._session = self._session_factory()
                        result = self._session.render(Path(path), request.page - 1, request.width, cancel)
                        if cancel.is_set():
                            raise Cancelled()
                        if result.image_path is None:
                            state[side] = {**_side(request, side, status="unavailable",
                                                  message="У цьому документі немає цієї сторінки."),
                                           "pageCount": result.page_count}
                        else:
                            image = Path(result.image_path)
                            images.append(image)
                            state[side] = {**_side(request, side, status="ready"), "pageCount": result.page_count,
                                           "imageUrl": image.absolute().as_uri(), "width": result.width,
                                           "height": result.height}
                    except Cancelled:
                        raise
                    except FormaError as exc:
                        state[side] = _side(request, side, status="error", message=str(exc))
                    except (OSError, ValueError):
                        state[side] = _side(request, side, status="error",
                                            message="Не вдалося підготувати перегляд документа.")
            if cancel.is_set():
                raise Cancelled()
            if signatures == {side: _signature(getattr(request, side)) for side in signatures}:
                return request, signatures, state, images
            previous = None
        raise FormaError("Файл змінився під час підготовки перегляду. Відкрийте його знову.")

    def _start(self, request, generation):
        self._running = True
        self._active_request = request
        self._cancel = cancel = threading.Event()
        previous = self._completed
        previous_metadata = self._pages.metadata

        def run(emit):
            try:
                if request.kind == "page":
                    rendered = self._render(request, cancel, previous, emit)
                    if not cancel.is_set():
                        emit("preview", rendered)
                else:
                    metadata = self._pages.describe(request, cancel, self._work_session, _signature, previous_metadata)
                    if cancel.is_set():
                        raise Cancelled()
                    emit("preview_metadata", metadata)
                    if request.kind == "range":
                        rows = self._pages.render(request, metadata, cancel, self._work_session, _signature)
                        if not cancel.is_set():
                            emit("preview_range", rows)
            except Cancelled as exc:
                if not cancel.is_set():
                    emit("error", str(exc) or "Не вдалося підготувати перегляд документа.")

        def receive(kind, payload):
            same_request = request == self._request or (
                request.kind == "metadata" and self._request and self._request.kind != "page"
                and self._continuous_key(request) == self._continuous_key(self._request)
            )
            if (self._closed or generation != self._generation or not same_request
                    or not self._matches_document(request)):
                return
            if kind == "preview":
                self._completed = payload
                self._update(payload[2])
            elif kind == "preview_loading":
                self._update(_state(request, loading=True))
            elif kind == "preview_metadata":
                metadata_changed = self._pages.install(payload)
                state = ContinuousPages.state(request, payload)
                notify = metadata_changed and state == self._state
                self._update(state)
                if notify:
                    self.changed.emit()
            elif kind == "preview_range":
                self._pages.publish(payload)
            elif kind == "error":
                state = _state(request) if request.kind == "page" else ContinuousPages.state(request)
                if request.kind != "page":
                    self._pages.clear()
                for side in SIDES:
                    if request.active(side):
                        state[side] = _side(request, side, status="error", message=payload)
                self._update(state)

        def finish():
            self._running = False
            self._active_request = None
            self._cancel = None
            if self._closed:
                self._cleanup()
                return
            pending, self._pending = self._pending, None
            if pending and pending[0] == self._request and self._matches_document(pending[0]):
                self._start(*pending)
            elif self._probe_after_range and self._request and self._request.kind != "page":
                self._probe_after_range = False
                self._start(replace(self._request, kind="metadata"), self._generation)

        self._launch(run, handler=receive, done=finish)

    @property
    def is_closed(self):
        return self._closed and not self._running and not self._cleaning and self._session is None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._pending = self._completed = None
        self._pages.clear()
        if self._cancel:
            self._cancel.set()
        self._update(_state(self._request or PreviewRequest("", "", "", "original", 1, 1000)))
        if not self._running:
            self._cleanup()

    def _cleanup(self):
        session, self._session = self._session, None
        if session is None:
            if not self._cleaning:
                self.closed.emit()
            return
        self._cleaning = True

        def run(emit):
            session.close()

        def finish():
            self._cleaning = False
            self.closed.emit()

        self._launch(run, handler=lambda *_: None, done=finish)

    def shutdown(self):
        """Reap cleanup on application exit, including screenshot/quit paths."""
        self.close()
        if not self.is_closed:
            loop = QEventLoop()
            self.closed.connect(loop.quit)
            loop.exec()
            self.closed.disconnect(loop.quit)
