"""History workflow implementation for the Qt Backend facade.

This class deliberately contains no Qt descriptors or QObject inheritance.
Backend owns the Qt metaobject and supplies the shared desktop session state.
"""

from __future__ import annotations

import time
from pathlib import Path

from forma.core.models import (
    FormaError,
)
from forma.localization.catalog import current_language
from forma.localization.catalog import translate_ui_message as tr


class HistoryWorkflow:
    def _persist_queue(self):
        if self._library:
            try:
                self._library.save_queue(self._documents)
            except FormaError as exc:
                self._notice(str(exc), "error")

    def _record_history(self, document):
        if not self._library or not document.get("runId"):
            return
        result = document.get("result") if document["status"] == "done" else None
        record = {
            "id": document["runId"],
            "name": document["name"],
            "format": document["format"],
            "source": document["path"],
            "output": document.get("output", "") if result else "",
            "report": document.get("report", "") if result else "",
            "status": document["status"],
            "sourceLanguage": document.get("sourceLanguage", ""),
            "targetLanguage": document.get("targetLanguage", ""),
            "model": document.get("model", ""),
            "reviewModel": document.get("reviewModel", ""),
            "startedAt": document.get("startedAt", 0),
            "finishedAt": int(time.time() * 1000),
            "elapsedSeconds": document.get("elapsedSeconds", 0),
            "inputTokens": result.input_tokens if result else 0,
            "outputTokens": result.output_tokens if result else 0,
            "warningCount": document.get("warningCount", 0) if result else 0,
            "error": document.get("error", ""),
        }
        try:
            self._library.add_history(record)
            self._history = self._library.list_history()
            document["runId"] = ""
            self._file_status.request(
                (record["source"], record["output"], record["report"]), force=True
            )
        except FormaError as exc:
            self._notice(str(exc), "error")

    def historyEntries(self):
        language, revision = current_language(), self._file_status.revision
        cached = self._history_cache
        if cached and cached[0] is self._history and cached[1:3] == (language, revision):
            return cached[3]
        rows = [
            {
                **entry,
                "error": tr(entry.get("error", "")),
                "sourceExists": self._file_status.is_file(entry.get("source")),
                "outputExists": self._file_status.is_file(entry.get("output")),
                "reportExists": bool(entry.get("report"))
                and self._file_status.is_file(Path(entry["report"]).with_suffix(".html")),
                "reviewAvailable": self._file_status.is_file(entry.get("report")),
            }
            for entry in self._history
        ]
        self._history_cache = (self._history, language, revision, rows)
        return rows

    def openOutputFolder(self):
        try:
            folder = Path(self._folder).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            self.openPath(str(folder))
        except OSError:
            self._notice("Не вдалося відкрити папку результатів.", "error")

    def _history_entry(self, entry_id):
        return next((entry for entry in self._history if entry["id"] == entry_id), None)

    def openHistoryOutput(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry:
            self.openPath(entry.get("output", ""))

    def openHistoryFolder(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry and entry.get("output"):
            self.openPath(str(Path(entry["output"]).parent))

    def openHistoryReport(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry and entry.get("report"):
            self.openPath(str(Path(entry["report"]).with_suffix(".html")))

    def requeueHistory(self, entry_id):
        if self._review_editing or self._review_saving:
            return
        entry = self._history_entry(entry_id)
        if not entry or not self.canAddDocuments:
            return
        if not entry.get("source") or not Path(entry["source"]).is_file():
            self._notice(
                "Оригінал переміщено чи видалено. Додайте файл із нового розташування.", "warning"
            )
            return
        existing = next((d for d in self._documents if d["path"] == entry.get("source")), None)
        if existing:
            self._selected = existing["id"]
            if existing["status"] in ("done", "error", "cancelled") and self._can_remove(existing):
                existing.update(
                    status="loading",
                    statusText="Перевіряємо документ",
                    runId="",
                    error="",
                    output="",
                    report="",
                    diagnosticReport="",
                    warningCount=0,
                    progress=0,
                    tokens="",
                    startedAt=0,
                    elapsedSeconds=0,
                )
                existing.pop("result", None)
                if self._review_document_id == existing["id"]:
                    self._reload_review()
                self._inspect_document(existing)
            else:
                self._notice("Документ уже є в черзі.")
                return
        else:
            self.addFiles([entry.get("source", "")])
        self._persist_queue()
        self._notice("Документ додано до черги. Застосовуються поточні налаштування перекладу.")

    def forgetHistory(self, entry_id):
        if self._review_editing or self._review_saving:
            return
        if self._library:
            try:
                self._library.remove_history(entry_id)
                self._history = self._library.list_history()
                if self._history_review and self._history_review["id"] == "history:" + entry_id:
                    forgotten = self._history_review["id"]
                    self._history_review = None
                    if self._review_document_id == forgotten:
                        self._review_document_id = self._selected
                        self._reload_review()
                self.changed.emit()
            except FormaError as exc:
                self._notice(str(exc), "error")

    def importHistory(self):
        if not self._library or self._history_loading or self._closing:
            return
        self._history_loading = True
        library, folder = self._library, Path(self._folder)

        def read(emit):
            count = 0
            for path in folder.glob("*.forma.json"):
                count += int(library.import_report(path))
            emit("history", count)

        def receive(kind, payload):
            if kind == "history":
                try:
                    self._history = library.list_history()
                    self._notice(
                        f"Опрацьовано звітів: {payload}. Повторні записи об’єднано.", "success"
                    )
                except FormaError as exc:
                    self._notice(str(exc), "error")
            else:
                self._notice(payload, "error")

        def finish():
            self._history_loading = False
            self.changed.emit()

        self._launch(read, handler=receive, done=finish)
        self.changed.emit()
