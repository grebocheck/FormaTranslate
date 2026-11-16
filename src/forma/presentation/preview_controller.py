"""Asynchronous page previews; requests and private render caches stay local."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, Signal

from forma.core.models import Cancelled, FormaError
from forma.localization.catalog import translate_ui_message as tr

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
        self._state = _state(PreviewRequest("", "", "", "original", 1, 1000))

    def state(self):
        return {**self._state, **{side: {**self._state[side], "message": tr(self._state[side]["message"])}
                                  for side in SIDES}}

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

        def run(emit):
            try:
                rendered = self._render(request, cancel, previous, emit)
                if not cancel.is_set():
                    emit("preview", rendered)
            except Cancelled as exc:
                if not cancel.is_set():
                    emit("error", str(exc) or "Не вдалося підготувати перегляд документа.")

        def receive(kind, payload):
            if (self._closed or generation != self._generation or request != self._request
                    or not self._matches_document(request)):
                return
            if kind == "preview":
                self._completed = payload
                self._update(payload[2])
            elif kind == "preview_loading":
                self._update(_state(request, loading=True))
            elif kind == "error":
                state = _state(request)
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
