"""Shared document selection and lightweight navigation, independent of jobs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from forma.core.models import FormaError
from forma.persistence.session_store import (
    DEFAULT_OPTIONS,
    MAX_DOCUMENTS,
    MAX_HISTORY,
    SessionStore,
    clean_options,
)


class SessionWorkflow:
    def _initialize_workspace(self):
        self._workspace_history = {}
        self._workspace_navigation = {}
        self._workspace_mode = "translation"
        self._workspace_options = dict(DEFAULT_OPTIONS)
        self._workspace_store = None
        self._workspace_saved = None
        self._review_view_id = ""
        self._review_request_key = None
        self._review_probe_running = False
        self._review_probe_pending = False
        self._review_restore = {}
        try:
            store = SessionStore()
            saved = store.load()
            self._workspace_store, self._workspace_saved = store, saved
            self._workspace_navigation = dict(saved["navigation"])
            self._workspace_mode = saved["mode"]
            self._workspace_options = dict(saved["options"])
            for entry_id in saved["history"]:
                entry = self._history_entry(entry_id)
                if entry and entry.get("report"):
                    self._open_workspace_history(entry)
            if self._workspace_document(saved["selected"]):
                self._selected = saved["selected"]
        except FormaError as exc:
            self._notice(str(exc), "warning")
        self._review_document_id = self._selected
        if self._selected in self._workspace_history:
            self._history_review = self._workspace_history[self._selected]
        self._restore_review_navigation()

    @staticmethod
    def _review_report_key(document):
        report = document.get("report") or (
            str(Path(document["diagnosticReport"]).with_suffix(".json")) if document.get("diagnosticReport") else ""
        )
        return hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(report)))).hexdigest() if report else ""

    def _workspace_document(self, document_id=None):
        document_id = self._selected if document_id is None else document_id
        document = next((item for item in self._documents if item["id"] == document_id), None)
        if document:
            return document
        document = getattr(self, "_workspace_history", {}).get(document_id)
        if document:
            return document
        if self._history_review and self._history_review["id"] == document_id:
            return self._history_review
        return None

    def _workspace_row(self, document):
        historical = document["id"].startswith("history:")
        if historical:
            row = {**self._display_document(document), "canRemove": not self._review_editing and not self._review_saving,
                   "canMove": False, "size": ""}
        else:
            row = self._document_row(document)
        report = self._workspace_report_path(document)
        return {**row, "history": historical, "reportOpenPath": report,
                "reportExists": self._file_status.is_file(report),
                "sourceExists": self._file_status.is_file(document.get("path")),
                "outputExists": self._file_status.is_file(document.get("output")),
                "reviewAvailable": bool(document.get("report") or document.get("diagnosticReport") or document.get("result"))}

    @staticmethod
    def _workspace_report_path(document):
        return str(Path(document["report"]).with_suffix(".html")) if document.get("report") else document.get("diagnosticReport", "")

    def openWorkspaceReport(self, document_id):
        document = self._workspace_document(document_id)
        if document:
            self.openPath(self._workspace_report_path(document))

    def setWorkspaceOptions(self, options):
        if not isinstance(options, dict) or set(options) - set(DEFAULT_OPTIONS) or self._closing:
            return False
        try:
            value = clean_options({**self._workspace_options, **options})
        except (ValueError, TypeError):
            return False
        if value != self._workspace_options:
            self._workspace_options = value
            self._persist_workspace()
            self.changed.emit()
        return True

    def workspaceDocuments(self):
        return [self._workspace_row(document) for document in [*self._documents, *self._workspace_history.values()]]

    def pendingDocumentCount(self):
        return sum(document["status"] in {"ready", "error", "cancelled"} for document in self._documents)

    def _open_workspace_history(self, entry):
        document = {**entry, "id": "history:" + entry["id"], "path": entry.get("source", ""),
                    "status": "done", "history": True}
        self._workspace_history[document["id"]] = document
        while len(self._workspace_history) > MAX_HISTORY:
            oldest = next(key for key in self._workspace_history if key != self._selected)
            self._workspace_history.pop(oldest)
        return document

    def _remember_review_navigation(self):
        context = self._review_context
        document_id = self._review_view_id
        if not document_id or not context or self._review_summary.get("loading"):
            return
        document = self._workspace_document(document_id)
        if not document:
            return
        # A newly completed/requeued document must not inherit its previous run's view.
        report = str(context.get("report") or "")
        if report and self._review_report_key({"report": report}) != self._review_report_key(document):
            return
        self._workspace_navigation.pop(document_id, None)
        self._workspace_navigation[document_id] = {
            "filter": self._review_filter, "search": self._review_search[:500], "entry": self._review_selected,
            "reportKey": self._review_report_key(document), "fingerprint": context.get("reportFingerprint", ""),
        }
        while len(self._workspace_navigation) > MAX_DOCUMENTS:
            self._workspace_navigation.pop(next(iter(self._workspace_navigation)))

    def _restore_review_navigation(self):
        document = self._workspace_document(self._review_document_id) or {}
        saved = self._workspace_navigation.get(self._review_document_id, {})
        if saved.get("reportKey") != self._review_report_key(document):
            saved = {}
        self._review_filter = saved.get("filter", "all")
        self._review_search = saved.get("search", "")
        self._review_restore = dict(saved)

    def _persist_workspace(self):
        if not getattr(self, "_workspace_store", None):
            return
        self._remember_review_navigation()
        data = {"version": 1, "selected": self._selected, "mode": self._workspace_mode,
                "options": dict(self._workspace_options),
                "history": [key.removeprefix("history:") for key in self._workspace_history],
                "navigation": {key: dict(value) for key, value in self._workspace_navigation.items()}}
        if data == self._workspace_saved:
            return
        try:
            self._workspace_store.save(data)
            self._workspace_saved = data
        except FormaError as exc:
            self._notice(str(exc), "warning")

    def selectWorkspaceDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        document = self._workspace_document(document_id)
        if not document:
            return
        if self._selected == self._review_document_id == document_id:
            if self._workspace_mode == "review":
                self.activateReview()
            return
        self._remember_review_navigation()
        self._selected = self._review_document_id = document_id
        self._history_review = document if document_id.startswith("history:") else None
        self._review_view_id = ""
        self._restore_review_navigation()
        self._reload_review()
        self._persist_workspace()
        self.changed.emit()

    def setWorkspaceMode(self, mode):
        if mode not in {"translation", "review"} or self._review_editing or self._review_saving:
            return
        self._workspace_mode = mode
        if mode == "review":
            self.activateReview()
        self._persist_workspace()
        self.changed.emit()

    def closeWorkspaceDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        if document_id not in self._workspace_history:
            self.removeDocument(document_id)
            return
        self._remember_review_navigation()
        self._workspace_history.pop(document_id)
        if self._history_review and self._history_review["id"] == document_id:
            self._history_review = None
        if self._selected == document_id:
            self._select_workspace_neighbor()
        self._persist_workspace()
        self.changed.emit()

    def _select_workspace_neighbor(self):
        self._selected = ""
        self._review_document_id = ""
        self._review_view_id = ""
        rows = [*self._documents, *self._workspace_history.values()]
        if rows:
            self.selectWorkspaceDocument(rows[0]["id"])
        else:
            self._reload_review()
