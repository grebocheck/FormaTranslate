"""Document workflow implementation for the Qt Backend facade.

This class deliberately contains no Qt descriptors or QObject inheritance.
Backend owns the Qt metaobject and supplies the shared desktop session state.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from forma.core.models import (
    Cancelled,
    FormaError,
    Options,
    plain_text,
)
from forma.documents.ocr import convert_scan_pdf
from forma.documents.ooxml import OfficeDocument
from forma.documents.pdf import inspect_pdf
from forma.documents.review import actionable_count
from forma.localization.catalog import current_language
from forma.localization.catalog import translate_ui_message as tr
from forma.providers.client import JobControl
from forma.settings.config import load_preferences, save_preferences
from forma.translation.engine import TranslationEngine
from forma.translation.jobs import DocumentQueue, run_documents


def _scan_review_warnings(output: Path) -> list[str]:
    """Expose only known OCR findings; never display arbitrary manifest content."""
    try:
        with output.with_suffix(".ocr.json").open("rb") as stream:
            raw = stream.read(50 * 1024 * 1024 + 1)
        if len(raw) > 50 * 1024 * 1024:
            return []
        data = json.loads(raw)
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            return []
    except (OSError, ValueError, UnicodeError, RecursionError):
        return []
    codes = {
        warning
        for page in pages
        if isinstance(page, dict) and isinstance(page.get("warnings"), list)
        for warning in page["warnings"]
        if isinstance(warning, str)
    }
    recovered = any(
        isinstance(page, dict)
        and type(page.get("recovered_lines")) is int
        and page["recovered_lines"] > 0
        for page in pages
    )
    messages = []
    if "table_structure_unresolved" in codes:
        messages.append(
            "Частину таблиць збережено зображеннями та текстом, бо структуру не відновлено. "
            "Виправте їх у DOCX перед перекладом."
        )
    if recovered or "unassigned_ocr_lines_recovered" in codes:
        messages.append(
            "Відновлені поза структурою рядки виділено жовтим у DOCX. "
            "Перевірте порядок і дублювання."
        )
    if "low_ocr_confidence" in codes:
        messages.append(
            "Є фрагменти з низькою впевненістю OCR. Звірте їх зі сканом, особливо числа та коди."
        )
    return messages


class DocumentWorkflow:
    def documents(self):
        return [
            {
                **self._display_document(d),
                "canRemove": self._can_remove(d),
                "canMove": self._can_remove(d) and d["status"] in ("loading", "ready", "queued"),
            }
            for d in self._documents
        ]

    def _selected_document(self):
        return next((d for d in self._documents if d["id"] == self._selected), None)

    def selectedDocument(self):
        document = self._selected_document()
        return self._display_document(document) if document else {}

    def segments(self):
        document = self._selected_document()
        if not document:
            self._segments_cache = None
            return []
        result = document.get("result")
        segments = document.get("segments", [])
        language = current_language()
        cached = self._segments_cache
        # Worker results replace whole snapshots; keep only the selected snapshot.
        if (
            cached
            and cached[0] is document
            and cached[1] is segments
            and cached[2] is result
            and cached[3] == language
        ):
            return cached[4]
        rows = [
            {
                "location": tr(s.location),
                "source": s.plain,
                "translation": plain_text(result.translations.get(s.id, "")) if result else "",
            }
            for s in segments
        ]
        self._segments_cache = (document, segments, result, language, rows)
        return rows

    def issues(self):
        document = self._selected_document()
        result = document.get("result") if document else None
        if not result:
            return []
        segments = {s.id: s for s in result.segments}
        return [
            {
                "message": tr(i.message),
                "severity": i.severity,
                "code": i.code,
                "segmentId": i.segment_id,
                "location": tr(segments[i.segment_id].location) if i.segment_id in segments else "",
                "source": segments[i.segment_id].plain if i.segment_id in segments else "",
                "translation": plain_text(result.translations.get(i.segment_id, "")),
            }
            for i in result.issues
        ]

    def selectDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        self._selected = document_id
        self._review_document_id = document_id
        self._review_search = ""
        self._reload_review()
        self.changed.emit()

    def addFiles(self, urls):
        if not self.canAddDocuments:
            return
        for value in urls:
            url = QUrl(value)
            path = (
                Path(url.toLocalFile() if url.isLocalFile() else str(value)).expanduser().resolve()
            )
            if path.suffix.lower() not in (".docx", ".xlsx", ".pdf") or not path.is_file():
                self._notice("Додайте документ у форматі DOCX, XLSX або PDF.", "error")
                continue
            if any(d["path"] == str(path) for d in self._documents):
                continue
            did = uuid.uuid4().hex
            self._documents.append(
                {
                    "id": did,
                    "path": str(path),
                    "name": path.name,
                    "format": path.suffix[1:].upper(),
                    "size": f"{path.stat().st_size / 1024:.0f} КБ",
                    "status": "loading",
                    "statusText": "Читаємо документ",
                    "progress": 0,
                    "startedAt": 0,
                    "elapsedSeconds": 0,
                    "segmentCount": 0,
                    "error": "",
                    "output": "",
                    "segments": [],
                    "report": "",
                    "diagnosticReport": "",
                    "tokens": "",
                    "warningCount": 0,
                }
            )
            if not self._selected:
                self._selected = did

            self._inspect_document(self._documents[-1])
        self._persist_queue()
        self.changed.emit()

    def recognizeSelectedPdf(self, source_language):
        if self.busy or self._closing or self._review_editing or self._review_saving:
            return
        document = next((d for d in self._documents if d["id"] == self._selected), None)
        if not document or document.get("format") != "PDF" or document["status"] == "loading":
            return
        if source_language == "auto":
            self._notice("Для розпізнавання PDF виберіть мову оригіналу.", "warning")
            return
        self._busy, self._scan_active = True, True
        self._scan_progress = 0
        self._scan_message = "Готуємо розпізнавання PDF"
        self._message = ""
        self._control = JobControl(1)
        source = Path(document["path"])
        folder = Path(self._folder)
        configured = self._settings.scan_converter_path
        control = self._control
        converted = []

        def run(emit):
            try:
                output = convert_scan_pdf(
                    source,
                    folder,
                    source_language,
                    configured,
                    control,
                    lambda percent, message: emit("scan_progress", (percent, message)),
                )
                emit("scan_result", (output, _scan_review_warnings(output)))
            except Cancelled:
                emit("scan_cancelled", None)

        def receive(kind, payload):
            if self._closing:
                return
            if kind == "scan_progress":
                self._scan_progress, self._scan_message = payload
                self.changed.emit()
            elif kind == "scan_result":
                converted.append(payload)
            elif kind == "scan_cancelled":
                self._notice("Розпізнавання PDF зупинено. Оригінал залишився без змін.")
            else:
                self._notice(payload, "error")

        def finish():
            self._busy, self._scan_active = False, False
            self._scan_progress = 0
            self._scan_message = ""
            if converted and not self._closing:
                output, warnings = converted[0]
                self.removeDocument(document["id"])
                self.addFiles([str(output)])
                prepared = next(
                    (d for d in self._documents if d["path"] == str(output.resolve())), None
                )
                if prepared:
                    self.selectDocument(prepared["id"])
                self._notice(
                    "DOCX після OCR додано до черги. Відкрийте оригінал, перевірте текст і таблиці, "
                    "потім натисніть «Перекласти».",
                    "warning",
                    details=warnings,
                )
            self.changed.emit()

        self._launch(run, handler=receive, done=finish)
        self.changed.emit()

    def _restore_queue(self):
        if self._closing:
            return
        for document in self._documents:
            if document["id"] in self._restored_ids and document["status"] == "loading":
                self._inspect_document(document)
        self._restored_ids.clear()
        self._persist_queue()
        if not self._review_document_id and self._selected:
            self._review_document_id = self._selected

    def _inspect_document(self, document):
        path, did = Path(document["path"]), document["id"]

        def inspect(emit):
            try:
                if path.suffix.lower() == ".pdf":
                    info = inspect_pdf(path)
                    emit(
                        "inspected",
                        {"id": did, "segments": [], "count": len(info["pages"]), "pdf": True},
                    )
                else:
                    source = OfficeDocument(path)
                    emit(
                        "inspected",
                        {
                            "id": did,
                            "segments": source.segments,
                            "count": len(source.segments),
                            "pdf": False,
                        },
                    )
            except Exception as exc:
                emit(
                    "file_error",
                    {
                        "id": did,
                        "error": str(exc)
                        if isinstance(exc, FormaError)
                        else "Не вдалося прочитати документ. Перевірте, чи файл існує.",
                    },
                )

        self._launch(inspect)

    def _can_remove(self, document):
        if self._closing or (self._busy and not self._translation_active):
            return False
        if document["status"] == "translating":
            return False
        if document["status"] == "queued" and self._queue:
            return self._queue.contains(document["id"])
        return True

    def removeDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        document = next((d for d in self._documents if d["id"] == document_id), None)
        if not document or not self._can_remove(document):
            return
        if document["status"] == "queued" and self._queue and not self._queue.remove(document_id):
            return  # The scheduler has already claimed this file.
        self._documents.remove(document)
        if self._selected == document_id:
            self._selected = self._documents[0]["id"] if self._documents else ""
        if self._review_document_id == document_id:
            self._review_document_id = self._selected
            self._reload_review()
        self._persist_queue()
        self.changed.emit()

    def moveDocument(self, document_id, delta):
        movable = [
            d
            for d in self._documents
            if self._can_remove(d) and d["status"] in ("ready", "loading", "queued")
        ]
        current = next((i for i, d in enumerate(movable) if d["id"] == document_id), -1)
        target = current + (1 if delta > 0 else -1)
        if current < 0 or not 0 <= target < len(movable):
            return
        a, b = self._documents.index(movable[current]), self._documents.index(movable[target])
        self._documents[a], self._documents[b] = self._documents[b], self._documents[a]
        if self._queue:
            self._queue.reorder([d["id"] for d in self._documents])
        self._persist_queue()
        self.changed.emit()

    def clearCompleted(self):
        for document in self._documents[:]:
            if document["status"] == "done":
                self.removeDocument(document["id"])

    def setOutputFolder(self, url):
        value = QUrl(url)
        self._folder = value.toLocalFile() if value.isLocalFile() else url
        try:
            save_preferences({**load_preferences(), "output_folder": self._folder})
        except OSError:
            self._notice(
                "Папку вибрано для цієї сесії; не вдалося зберегти її в налаштуваннях.", "warning"
            )
        self.changed.emit()

    def start(self, source_language, target_language, domain, review, auto_glossary):
        if self.busy or self._review_editing or self._review_saving:
            return
        if self._memory_project and (
            source_language != self._memory_project.source_language
            or target_language != self._memory_project.target_language
        ):
            self._notice("Мови перекладу мають відповідати вибраному проєкту пам’яті.", "error")
            return
        try:
            self._settings.validate()
        except FormaError as exc:
            self._notice(str(exc), "error")
            return
        pending = [d for d in self._documents if d["status"] in ("ready", "error", "cancelled")]
        if not pending and self._documents and all(d["status"] == "done" for d in self._documents):
            pending = list(self._documents)
        if not pending:
            self._notice("Додайте документи або дочекайтеся завершення їх читання.")
            return
        self._busy, self._paused, self._translation_active = True, False, True
        self._control = JobControl(max_parallel_requests=self._settings.max_parallel_requests)
        options = Options(
            source_language,
            target_language,
            domain,
            review,
            auto_glossary,
            dict(self._glossary),
            instructions=self._settings.translation_instructions,
            memory_project_id=self._settings.memory_project_id,
        )
        self._run_options = options
        for document in pending:
            self._prepare_run(document)
        self._queue = DocumentQueue(pending)
        queue = self._queue
        self._persist_queue()
        self._reload_review()
        settings, control, folder = self._settings, self._control, Path(self._folder)
        self._message = ""

        def run(emit):
            run_documents(
                queue, folder, options, settings, control, emit, engine_factory=TranslationEngine
            )

        self._launch(run, done=self._finished)
        self.changed.emit()

    def _finished(self):
        self._busy, self._paused, self._translation_active = False, False, False
        self._queue = None
        self.changed.emit()

    def _prepare_run(self, document):
        options = self._run_options
        document.pop("result", None)
        document.update(
            runId=uuid.uuid4().hex,
            sourceLanguage=options.source_language,
            targetLanguage=options.target_language,
            model=self._settings.model,
            reviewModel=(self._settings.review_model or self._settings.model)
            if options.review
            else "",
            status="queued",
            statusText="У черзі",
            startedAt=0,
            elapsedSeconds=0,
            progress=0,
            output="",
            report="",
            diagnosticReport="",
            tokens="",
            warningCount=0,
            error="",
            memorySegments=0,
            repeatedSegments=0,
            adaptedSegments=0,
        )

    def togglePause(self):
        if self._control and self._busy and self._translation_active:
            self._paused = not self._paused
            if self._paused:
                self._control.paused.set()
                self._notice("Пауза діє після поточного запиту; для PDF — перед наступним файлом.")
            else:
                self._control.paused.clear()
                self._message = ""
            self.changed.emit()

    def cancel(self):
        if self._control:
            self._control.cancelled.set()
            self._control.paused.clear()
            self._notice(
                "Зупиняємо розпізнавання PDF…"
                if self._scan_active
                else "Зупиняємо після поточного API-запиту. Перевірені фрагменти залишаться для продовження."
            )

    def openPath(self, path):
        if path:
            target = Path(path).expanduser().resolve()
            if not target.exists():
                self._notice("Файл або папку переміщено чи видалено.", "warning")
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
                self._notice("Операційна система не змогла відкрити файл або папку.", "warning")

    def openSelectedReport(self):
        doc = self._selected_document()
        if doc and doc.get("report"):
            self.openPath(str(Path(doc["report"]).with_suffix(".html")))

    def openFailureReport(self):
        document = self._selected_document()
        value = document.get("diagnosticReport") if document else ""
        if not isinstance(value, str) or not value:
            return
        try:
            path = Path(value).expanduser().resolve()
            if path.suffix.lower() != ".html" or not path.is_file():
                raise OSError
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError
        except (OSError, ValueError):
            self._notice(
                "Не вдалося відкрити звіт помилок. Повторіть переклад, щоб створити новий звіт.",
                "warning",
            )

    def requestClose(self):
        if self._review_editing and not self.applyReviewEdit():
            self._review_edit_error += " Застосуйте або скасуйте правку перед закриттям."
            self.reviewEditChanged.emit()
            return False
        self._persist_queue()
        if not self._threads:
            return True
        self._closing = True
        self._quick.cancel()
        self._glossary_editor.cancel()
        self.cancel()
        self._notice("Завершуємо поточну операцію перед закриттям…")
        return False

    def _handle_event(self, kind, payload):
        if kind == "error":
            for document in self._documents:
                if document["status"] in ("queued", "translating"):
                    document.update(status="error", statusText="Потрібна увага", error=payload)
                    self._record_history(document)
            self._api_status = "Дія не завершена"
            self._notice(payload, "error")
        elif kind == "api_ok":
            self._api_status = payload
            self._notice(payload, "success")
        else:
            doc = next((d for d in self._documents if d["id"] == payload.get("id")), None)
            if not doc:
                return
            if kind == "inspected":
                if doc["status"] != "loading":
                    return
                doc.update(
                    segments=payload["segments"],
                    segmentCount=payload["count"],
                    status="ready",
                    error="",
                    statusText=(
                        f"{payload['count']} стор. · готовий"
                        if payload["pdf"]
                        else f"{payload['count']} фрагментів · готовий"
                    ),
                )
                if (
                    self._translation_active
                    and self._queue
                    and not self._control.cancelled.is_set()
                ):
                    self._prepare_run(doc)
                    if not self._queue.add(doc):
                        doc.update(
                            status="ready", statusText="Готовий до наступного запуску", runId=""
                        )
                if not self._review_document_id and self._selected == doc["id"]:
                    self._review_document_id = doc["id"]
            elif kind == "queued":
                doc.update(
                    status="queued",
                    statusText="У черзі",
                    progress=0,
                    error="",
                    startedAt=0,
                    elapsedSeconds=0,
                )
            elif kind == "progress":
                if not doc.get("startedAt"):
                    doc["startedAt"] = int(time.time() * 1000)
                    self._persist_queue()
                doc.update(
                    progress=payload["percent"],
                    statusText=payload["text"],
                    status="translating",
                    error="",
                )
            elif kind in ("file_error", "cancelled"):
                if doc.get("startedAt"):
                    doc["elapsedSeconds"] = max(0, int(time.time() - doc["startedAt"] / 1000))
                doc.update(
                    status="cancelled" if kind == "cancelled" else "error",
                    statusText="Зупинено" if kind == "cancelled" else "Потрібна увага",
                    error=payload["error"],
                    diagnosticReport=payload.get("diagnosticReport", "")
                    if kind == "file_error"
                    else "",
                )
                self._notice(payload["error"], "error" if kind == "file_error" else "info")
                self._record_history(doc)
            elif kind == "result":
                result = payload["result"]
                warnings = actionable_count(result.issues)
                doc.update(
                    result=result,
                    segments=result.segments,
                    status="done",
                    progress=100,
                    statusText="Готово · перегляньте зауваження" if warnings else "Готово",
                    output=str(result.output),
                    report=str(result.report),
                    tokens=f"{result.input_tokens + result.output_tokens:,}"
                    if result.engine != "babeldoc"
                    else "—",
                    warningCount=warnings,
                    elapsedSeconds=int(result.elapsed_seconds),
                    memorySegments=getattr(result, "memory_segments", 0),
                    repeatedSegments=getattr(result, "repeated_segments", 0),
                    adaptedSegments=getattr(result, "adapted_segments", 0),
                )
                self._record_history(doc)
                if self._review_document_id == doc["id"]:
                    self._reload_review()
        if kind != "progress":
            self._persist_queue()
        self.changed.emit()
