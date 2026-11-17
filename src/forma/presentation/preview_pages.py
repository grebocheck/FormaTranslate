"""Incremental geometry and bounded viewport images for continuous page previews."""
from __future__ import annotations

import math
from pathlib import Path

from forma.core.formats import PREVIEW_SUFFIXES
from forma.core.models import Cancelled, FormaError
from forma.localization.catalog import translate_ui_message as tr
from forma.presentation.document_model import DocumentListModel

SIDES = ("source", "translation")


class PreviewPageModel(DocumentListModel):
    def data(self, index, role=DocumentListModel.ROW_ROLE):
        row = super().data(index, role)
        if row is None:
            return None
        return {**row, **{side: {**row[side], "message": tr(row[side]["message"])} for side in SIDES}}

    def retranslate(self):
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1), [self.ROW_ROLE])


class ContinuousPages:
    def __init__(self, parent):
        self.model = PreviewPageModel(parent)
        self.metadata = None
        self.identity = None
        self.loaded = set()

    def clear(self):
        self.model.sync([])
        self.metadata = self.identity = None
        self.loaded.clear()

    @staticmethod
    def base(request):
        return request.document_id, request.source, request.translation, request.mode

    @staticmethod
    def _summary(request, side, status="idle", message="", count=0):
        return {"status": status, "message": message, "pageCount": count,
                "format": Path(getattr(request, side)).suffix[1:].upper()}

    def describe(self, request, cancel, session, signature, previous):
        signatures = {side: signature(getattr(request, side)) for side in SIDES if request.active(side)}
        identity = self.base(request), signatures
        if (previous and previous["identity"] == identity
                and all(previous[side]["status"] != "error" for side in SIDES)):
            return previous
        metadata = {"identity": identity, "sizes": {}, "pageCount": 0}
        for side in SIDES:
            metadata[side] = self._summary(request, side)
            metadata["sizes"][side] = ()
            if not request.active(side):
                continue
            if cancel.is_set():
                raise Cancelled()
            path = getattr(request, side)
            if not path:
                metadata[side] = self._summary(request, side, "unavailable", (
                    "Переклад ще не створено." if side == "translation" else "Виберіть документ для перегляду."
                ))
            elif Path(path).suffix.lower() not in PREVIEW_SUFFIXES:
                metadata[side] = self._summary(request, side, "unavailable",
                                               "Попередній перегляд сторінок доступний для PDF, DOCX, PPTX, ODT і ODP.")
            elif signatures[side] is None:
                metadata[side] = self._summary(request, side, "unavailable",
                                               "Файл недоступний. Відкрийте його знову або додайте файл.")
            else:
                try:
                    description = session().describe(Path(path), cancel)
                    sizes = tuple(tuple(float(value) for value in size) for size in description.page_sizes)
                    if (type(description.page_count) is not int or not 1 <= description.page_count <= 10000
                            or len(sizes) != description.page_count
                            or any(len(size) != 2 or not all(math.isfinite(value) and value > 0 for value in size)
                                   for size in sizes)):
                        raise ValueError
                    metadata[side] = self._summary(request, side, "ready", count=description.page_count)
                    metadata["sizes"][side] = sizes
                    metadata["pageCount"] = max(metadata["pageCount"], description.page_count)
                except Cancelled:
                    raise
                except FormaError as exc:
                    metadata[side] = self._summary(request, side, "error", str(exc))
                except (OSError, ValueError, TypeError):
                    metadata[side] = self._summary(request, side, "error", "Не вдалося підготувати перегляд документа.")
        if signatures != {side: signature(getattr(request, side)) for side in signatures}:
            raise FormaError("Файл змінився під час підготовки перегляду. Відкрийте його знову.")
        return metadata

    @staticmethod
    def row(metadata, page):
        row = {"id": f"page:{page}", "page": page}
        for side in SIDES:
            summary = metadata[side]
            sizes = metadata["sizes"][side]
            present = page <= len(sizes)
            width, height = sizes[page - 1] if present else (0, 0)
            row[side] = {**summary, "page": page, "width": width, "height": height,
                         "imageUrl": "", "imageWidth": 0, "imageHeight": 0}
            if present:
                row[side]["status"] = "idle"
            elif summary["status"] == "ready":
                row[side].update(status="unavailable", message="У цьому документі немає цієї сторінки.")
        return row

    def install(self, metadata):
        changed = metadata != self.metadata
        self.metadata = metadata
        if changed:
            self.identity = metadata["identity"]
            self.loaded.clear()
            self.model.sync([self.row(metadata, page) for page in range(1, metadata["pageCount"] + 1)])
        return changed

    def retain_range(self, first, last):
        if not self.metadata:
            return
        wanted = set(range(first, last + 1))
        for page in self.loaded - wanted:
            self.model.update_row(self.row(self.metadata, page))
        self.loaded.intersection_update(wanted)

    def render(self, request, metadata, cancel, session, signature):
        indices = range(request.first, min(request.last, metadata["pageCount"]) + 1)
        retained = [(Path(getattr(request, side)), page - 1, request.width)
                    for page in indices for side in SIDES
                    if request.active(side) and page <= metadata[side]["pageCount"]]
        if retained:
            session().retain_pages(retained)
        rows = []
        for page in indices:
            row = self.row(metadata, page)
            for side in SIDES:
                if not request.active(side) or page > metadata[side]["pageCount"]:
                    continue
                if cancel.is_set():
                    raise Cancelled()
                try:
                    image = session().render(Path(getattr(request, side)), page - 1, request.width, cancel)
                    if image.image_path is None:
                        row[side].update(status="unavailable", message="У цьому документі немає цієї сторінки.")
                    else:
                        row[side].update(status="ready", imageUrl=Path(image.image_path).absolute().as_uri(),
                                         imageWidth=image.width, imageHeight=image.height)
                except Cancelled:
                    raise
                except FormaError as exc:
                    row[side].update(status="error", message=str(exc))
                except (OSError, ValueError):
                    row[side].update(status="error", message="Не вдалося підготувати перегляд документа.")
            rows.append(row)
        if cancel.is_set():
            raise Cancelled()
        expected = metadata["identity"][1]
        if expected != {side: signature(getattr(request, side)) for side in expected}:
            raise FormaError("Файл змінився під час підготовки перегляду. Відкрийте його знову.")
        return rows

    def publish(self, rows):
        for row in rows:
            self.model.update_row(row)
            self.loaded.add(row["page"])

    @staticmethod
    def state(request, metadata=None, *, loading=False):
        return {"documentId": request.document_id, "mode": request.mode, "page": 1, "width": request.width,
                "busy": loading, "continuous": True, "pageCount": metadata["pageCount"] if metadata else 0,
                **{side: dict(metadata[side]) if metadata else ContinuousPages._summary(
                    request, side, "loading" if loading and request.active(side) else "idle",
                ) for side in SIDES}}
