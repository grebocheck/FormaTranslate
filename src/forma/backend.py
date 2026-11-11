from __future__ import annotations

import csv
import json
import os
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Property, QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from forma import __version__
from forma.config import Settings, load_preferences, save_preferences
from forma.credentials import (
    CredentialError,
    canonical_endpoint,
    delete_api_key,
    load_api_key,
    save_api_key,
)
from forma.editor import edit_translation_markup, export_reviewed_copy
from forma.engine import TranslationEngine, blocking_issues
from forma.file_dialogs import FilePickers
from forma.i18n import available_languages, set_language
from forma.i18n import translate_ui_message as tr
from forma.jobs import DocumentQueue, run_documents
from forma.library import TranslationLibrary
from forma.model_catalog import list_models
from forma.models import (
    MAX_TRANSLATION_INSTRUCTIONS,
    Cancelled,
    FormaError,
    Options,
    plain_text,
    validate_instructions,
)
from forma.ocr import convert_scan_pdf, find_scan_converter
from forma.ooxml import OfficeDocument
from forma.pdf import find_babeldoc, inspect_pdf
from forma.provider import JobControl, Provider
from forma.provider_presets import provider_presets
from forma.quick_controller import QuickTranslator
from forma.review import actionable_count, build_review, load_report
from forma.review_store import ReviewStore
from forma.usage_controller import UsageController


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


class TaskThread(QThread):
    event = Signal(str, object)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task

    def run(self):
        try:
            self.task(self.event.emit)
        except FormaError as exc:
            self.event.emit("error", str(exc))
        except Exception as exc:
            self.event.emit(
                "error",
                f"Не вдалося завершити дію ({type(exc).__name__}). Перевірте документ і налаштування.",
            )


class Backend(QObject):
    changed = Signal()
    languageChanged = Signal()
    reviewEditChanged = Signal()
    closedReady = Signal()

    def __init__(self):
        super().__init__()
        preference = os.environ.get("FORMA_UI_LANGUAGE") or load_preferences().get(
            "ui_language", "system"
        )
        self._ui_language = (
            preference if preference in {"system", "uk", "en", "pl", "de"} else "system"
        )
        set_language(self._ui_language)
        self._documents = []
        self._selected = ""
        self._threads: list[TaskThread] = []
        self._busy = False
        self._translation_active = False
        self._scan_active = False
        self._scan_progress = 0
        self._scan_message = ""
        self._paused = False
        self._control = None
        self._message = ""
        self._message_details = []
        self._message_kind = "info"
        self._instructions_error = ""
        self._api_status = "Очікує налаштування"
        self._settings = self._read_settings()
        self._usage = UsageController(self, settings=lambda: self._settings)
        self.languageChanged.connect(self._usage.changed.emit)
        self._folder = load_preferences().get("output_folder") or str(
            Path.home() / "Documents" / "Forma"
        )
        self._glossary = {}
        self._glossary_name = ""
        self._closing = False
        self._pickers = FilePickers(self)
        self._queue = None
        self._run_options = None
        self._history = []
        self._history_loading = False
        self._model_choices = []
        self._models_busy = False
        self._models_message = ""
        self._models_generation = 0
        self._review_document_id = ""
        self._history_review = None
        self._review_generation = 0
        self._review_enabled = False
        self._review_running = False
        self._review_pending = False
        self._review_all_entries = []
        self._review_entries = []
        self._review_selected = ""
        self._review_filter = "issues"
        self._review_search = ""
        self._review_summary = self._empty_review_summary()
        self._review_context = None
        self._review_accepted = {}
        self._review_edits = {}
        self._review_editing = False
        self._review_draft_text = ""
        self._review_edit_error = ""
        self._review_action_error = ""
        self._review_saving = False
        self._quick = QuickTranslator(
            self,
            settings=lambda: self._settings,
            launch=self._launch,
            available=lambda: not self._busy and not self._closing and not self._review_saving,
            pickers=self._pickers,
            output_folder=lambda: self._folder,
        )
        self._quick.activityChanged.connect(self.changed.emit)
        self.changed.connect(self._quick.changed.emit)
        self._library = None
        self._restored_ids = set()
        try:
            self._library = TranslationLibrary()
            self._documents = self._library.load_queue()
            self._history = self._library.list_history()
            for document in self._documents:
                document["segments"] = []
                if document["status"] in ("queued", "translating", "paused"):
                    recorded = next(
                        (entry for entry in self._history if entry["id"] == document.get("runId")),
                        None,
                    )
                    if recorded:
                        document.update(
                            status=recorded["status"],
                            statusText="Відновлено з історії",
                            runId="",
                            output=recorded.get("output", ""),
                            report=recorded.get("report", ""),
                            elapsedSeconds=recorded.get("elapsedSeconds", 0),
                            warningCount=recorded.get("warningCount", 0),
                            error=recorded.get("error", ""),
                        )
                    else:
                        document.update(
                            status="interrupted", statusText="Перервано закриттям застосунку"
                        )
                        self._record_history(document)
                if document["status"] in ("ready", "loading", "interrupted"):
                    document.update(status="loading", statusText="Перевіряємо документ", runId="")
            self._selected = self._documents[0]["id"] if self._documents else ""
            self._restored_ids = {d["id"] for d in self._documents if d["status"] == "loading"}
            QTimer.singleShot(0, self._restore_queue)
        except FormaError as exc:
            self._library = None
            self._notice(str(exc), "error")

    def _read_settings(self):
        return Settings.load()

    @Property(QObject, constant=True)
    def usage(self):
        return self._usage

    @Property(QObject, constant=True)
    def quickTranslator(self):
        return self._quick

    @Property(str, notify=languageChanged)
    def uiLanguage(self):
        return self._ui_language

    @Property("QVariantList", notify=languageChanged)
    def uiLanguages(self):
        return available_languages()

    @Property(str, constant=True)
    def appVersion(self):
        return __version__

    @Slot(str)
    def setUiLanguage(self, code):
        if code not in {"system", "uk", "en", "pl", "de"} or code == self._ui_language:
            return
        try:
            save_preferences({**load_preferences(), "ui_language": code})
        except OSError:
            self._notice("Не вдалося зберегти налаштування.", "error")
            return
        self._ui_language = code
        set_language(code)
        self.languageChanged.emit()
        # A localized search can stop matching after a switch. Keep the draft
        # attached to its selected fragment until editing/export has finished.
        if self._review_editing or self._review_saving:
            self.changed.emit()
        else:
            self._filter_review()
        self.reviewEditChanged.emit()

    @staticmethod
    def _display_document(document):
        # Localize only UI metadata. Never translate document text, paths, IDs,
        # persisted review fingerprints, or the canonical worker messages.
        return {
            k: tr(v) if k in {"statusText", "error", "size"} and isinstance(v, str) else v
            for k, v in document.items()
            if k not in {"segments", "result"}
        }

    @staticmethod
    def _display_entry(entry):
        return {
            **entry,
            "location": tr(entry.get("location", "")),
            "messages": [tr(message) for message in entry.get("messages", [])],
        }

    @staticmethod
    def _empty_review_summary():
        return {
            "segments": 0,
            "attention": 0,
            "notes": 0,
            "loading": False,
            "error": "",
            "hasResult": False,
            "accepted": 0,
            "edited": 0,
        }

    def _review_document(self):
        if self._history_review and self._history_review["id"] == self._review_document_id:
            return self._history_review
        return next((d for d in self._documents if d["id"] == self._review_document_id), None)

    @Property("QVariantList", notify=changed)
    def reviewDocuments(self):
        documents = [
            {"id": d["id"], "name": d["name"], "status": d["status"]} for d in self._documents
        ]
        if self._history_review:
            documents.append(
                {
                    "id": self._history_review["id"],
                    "name": self._history_review["name"] + tr(" · з історії"),
                    "status": "done",
                }
            )
        return documents

    @Property("QVariantMap", notify=changed)
    def reviewDocument(self):
        document = self._review_document()
        if not document:
            return {}
        return {
            **self._display_document(document),
            "outputExists": bool(document.get("output")) and Path(document["output"]).is_file(),
            "reportExists": bool(document.get("report"))
            and Path(document["report"]).with_suffix(".html").is_file(),
        }

    @Property(str, notify=changed)
    def reviewDocumentId(self):
        return self._review_document_id

    @Property("QVariantList", notify=changed)
    def reviewEntries(self):
        return [self._display_entry(entry) for entry in self._review_entries]

    @Property("QVariantMap", notify=changed)
    def reviewSummary(self):
        return {**self._review_summary, "error": tr(self._review_summary.get("error", ""))}

    @Property(str, notify=changed)
    def reviewFilter(self):
        return self._review_filter

    @Property(str, notify=changed)
    def reviewSearch(self):
        return self._review_search

    @Property(str, notify=changed)
    def selectedReviewId(self):
        return self._review_selected

    @Property("QVariantMap", notify=changed)
    def selectedReviewEntry(self):
        entry = next(
            (entry for entry in self._review_entries if entry["id"] == self._review_selected), {}
        )
        if not entry:
            return {}
        document = self._review_document() or {}
        persisted = bool(self._review_context and self._review_context.get("reportFingerprint"))
        reason = ""
        if entry["kind"] != "segment":
            reason = "Це примітка до документа. Її можна позначити як переглянуту."
        elif document.get("format") not in ("DOCX", "XLSX"):
            reason = "Редагування тексту PDF зі збереженням верстки поки недоступне."
        elif not persisted:
            reason = "Для збереження правок потрібен доступний звіт Forma."
        elif not document.get("path") or not Path(document["path"]).is_file():
            reason = "Оригінальний файл недоступний. Він потрібен для збереження форматування."
        elif not document.get("output") or not Path(document["output"]).is_file():
            reason = "Файл перекладу переміщено або видалено."
        return {
            **self._display_entry(entry),
            "canAccept": persisted and entry["severity"] != "error" and not self._review_saving,
            "canEdit": not reason and not self._review_saving,
            "editReason": tr(reason),
        }

    @Property(bool, notify=reviewEditChanged)
    def reviewEditing(self):
        return self._review_editing

    @Property(str, notify=reviewEditChanged)
    def reviewDraftText(self):
        return self._review_draft_text

    @Property(str, notify=reviewEditChanged)
    def reviewEditError(self):
        return tr(self._review_edit_error)

    @Property(str, notify=reviewEditChanged)
    def reviewActionError(self):
        return tr(self._review_action_error)

    @Property(bool, notify=reviewEditChanged)
    def reviewSaving(self):
        return self._review_saving

    @Property(bool, notify=changed)
    def reviewHasEdits(self):
        return bool(self._review_edits)

    @Slot()
    def beginReviewEdit(self):
        entry = self.selectedReviewEntry
        if self._review_editing or not entry.get("canEdit"):
            return
        self._review_editing = True
        self._review_draft_text = entry["translation"]
        self._review_edit_error = self._review_action_error = ""
        self.reviewEditChanged.emit()

    @Slot(str)
    def updateReviewDraft(self, text):
        if self._review_editing:
            self._review_draft_text = text
            self._review_edit_error = ""
            self.reviewEditChanged.emit()

    @Slot()
    def cancelReviewEdit(self):
        self._review_editing = False
        self._review_draft_text = self._review_edit_error = ""
        self.reviewEditChanged.emit()

    def _save_review_state(self, accepted, edits):
        context = self._review_context
        if not context or not context.get("reportFingerprint"):
            raise FormaError("Не вдалося зберегти перевірку: звіт недоступний або змінений.")
        ReviewStore().save(
            context["report"], accepted, edits, expected_fingerprint=context["reportFingerprint"]
        )
        self._review_accepted, self._review_edits = accepted, edits

    def _rebuild_review(self):
        context = self._review_context
        payload = build_review(
            context["result"],
            context["options"],
            edits=self._review_edits,
            accepted=self._review_accepted,
        )
        self._review_all_entries, self._review_summary = payload["entries"], payload["summary"]
        self._sync_review_counts()
        self._filter_review()

    def _sync_review_counts(self):
        document, context = self._review_document(), self._review_context
        if not document or not context:
            return
        # History describes the saved file. Unexported text drafts do not remove
        # its findings; acknowledgements apply only to exactly matching content.
        summary = self._review_summary
        if self._review_edits:
            summary = build_review(
                context["result"], context["options"], accepted=self._review_accepted
            )["summary"]
        count = summary["warningCount"]
        document["warningCount"] = count
        if any(d is document for d in self._documents):
            if document["status"] == "done":
                document["statusText"] = "Готово · перегляньте зауваження" if count else "Готово"
            self._persist_queue()
        report = str(context["report"]) if context.get("report") else ""
        for entry in self._history:
            if report and entry.get("report") == report and entry.get("warningCount") != count:
                entry["warningCount"] = count
                if self._library:
                    try:
                        self._library.add_history(entry)
                    except FormaError as exc:
                        self._notice(str(exc), "error")

    @Slot(result=bool)
    def applyReviewEdit(self):
        if not self._review_editing or not self._review_context:
            return False
        try:
            context, entry_id = self._review_context, self._review_selected
            result, options = context["result"], context["options"]
            segment = next(s for s in result.segments if s.id == entry_id)
            current = self._review_edits.get(entry_id, result.translations.get(entry_id, ""))
            revised = edit_translation_markup(current, self._review_draft_text)
            errors = blocking_issues(segment, revised, options)
            if errors:
                raise FormaError(
                    "Правку не збережено: " + " ".join(issue.message for issue in errors)
                )
            if not self._review_draft_text.strip() and segment.plain.strip():
                raise FormaError("Переклад фрагмента не може бути порожнім.")
            edits, accepted = dict(self._review_edits), dict(self._review_accepted)
            if revised == result.translations.get(entry_id, ""):
                edits.pop(entry_id, None)
            else:
                edits[entry_id] = revised
            accepted.pop(entry_id, None)
            self._save_review_state(accepted, edits)
            self.cancelReviewEdit()
            self._review_action_error = ""
            self._rebuild_review()
            return True
        except (FormaError, StopIteration) as exc:
            self._review_edit_error = (
                str(exc) or "Фрагмент більше недоступний. Відкрийте документ знову."
            )
            self.reviewEditChanged.emit()
            return False

    @Slot()
    def discardReviewEdit(self):
        entry_id = self._review_selected
        if self._review_editing or self._review_saving or entry_id not in self._review_edits:
            return
        edits, accepted = dict(self._review_edits), dict(self._review_accepted)
        edits.pop(entry_id, None)
        accepted.pop(entry_id, None)
        self._change_review_state(accepted, edits)

    def _change_review_state(self, accepted, edits):
        try:
            self._save_review_state(accepted, edits)
            self._review_action_error = ""
            self._rebuild_review()
        except FormaError as exc:
            self._review_action_error = str(exc)
        self.reviewEditChanged.emit()

    @Slot()
    def confirmReviewEntry(self):
        entry = self.selectedReviewEntry
        if self._review_editing or not entry.get("canAccept"):
            return
        accepted = {**self._review_accepted, entry["id"]: entry["fingerprint"]}
        self._change_review_state(accepted, dict(self._review_edits))

    @Slot()
    def reopenReviewEntry(self):
        entry = self.selectedReviewEntry
        if self._review_editing or self._review_saving or not entry.get("accepted"):
            return
        accepted = dict(self._review_accepted)
        accepted.pop(entry["id"], None)
        self._change_review_state(accepted, dict(self._review_edits))

    @Slot()
    def exportReviewEdits(self):
        if (
            self._review_editing
            or self._review_saving
            or not self._review_edits
            or not self._review_context
        ):
            return
        document = dict(self._review_document() or {})
        context = self._review_context
        if (
            not context.get("reportFingerprint")
            or not document.get("path")
            or not document.get("output")
        ):
            return
        self._review_saving = True
        self._review_action_error = ""
        edits, accepted = dict(self._review_edits), dict(self._review_accepted)
        options = context["options"]

        def export(emit):
            result = export_reviewed_copy(
                Path(document["path"]),
                Path(document["output"]),
                context["report"],
                edits,
                expected_report_fingerprint=context["reportFingerprint"],
            )
            emit("review_export", result)

        def receive(kind, payload):
            if kind != "review_export":
                self._review_action_error = payload
                self.reviewEditChanged.emit()
                return
            result = payload
            problems = []
            try:
                fresh = build_review(result, options)
                transferred = {
                    entry["id"]: entry["fingerprint"]
                    for entry in fresh["entries"]
                    if accepted.get(entry["id"]) == entry["fingerprint"]
                    and entry["severity"] != "error"
                }
                state = ReviewStore().load(result.report)
                ReviewStore().save(
                    result.report, transferred, {}, expected_fingerprint=state["reportFingerprint"]
                )
                # Applied corrections now live in the new copy. Keep acknowledgements
                # of unchanged entries on the original; never approve changed old text.
                remaining = {
                    entry_id: fingerprint
                    for entry_id, fingerprint in accepted.items()
                    if entry_id not in edits
                }
                ReviewStore().save(
                    context["report"],
                    remaining,
                    {},
                    expected_fingerprint=context["reportFingerprint"],
                )
            except FormaError as exc:
                problems.append(str(exc))
            entry = {
                "id": uuid.uuid4().hex,
                "name": result.output.name,
                "format": result.output.suffix[1:].upper(),
                "source": str(result.source),
                "output": str(result.output),
                "report": str(result.report),
                "sourceLanguage": options.source_language,
                "targetLanguage": options.target_language,
                "model": document.get("model", ""),
                "reviewModel": document.get("reviewModel", ""),
                "status": "done",
                "finishedAt": int(time.time() * 1000),
                "elapsedSeconds": result.elapsed_seconds,
                "inputTokens": 0,
                "outputTokens": 0,
                "warningCount": actionable_count(result.issues),
                "error": "",
            }
            try:
                if self._library:
                    self._library.add_history(entry)
                    self._history = self._library.list_history()
                else:
                    problems.append("Історія недоступна.")
            except FormaError as exc:
                problems.append(str(exc))
            self._history_review = {
                **entry,
                "id": "history:" + entry["id"],
                "path": str(result.source),
            }
            self._review_document_id = self._history_review["id"]
            self._review_saving = False
            self._reload_review()
            if problems:
                self._notice(
                    "Копію збережено: " + str(result.output) + ". " + " ".join(problems), "warning"
                )

        def finish():
            self._review_saving = False
            self.reviewEditChanged.emit()
            self.changed.emit()

        self._launch(export, handler=receive, done=finish)
        self.reviewEditChanged.emit()
        self.changed.emit()

    @Slot(str)
    def setReviewFilter(self, value):
        if not self._review_editing and not self._review_saving and value in ("issues", "all"):
            self._review_filter = value
            self._filter_review()

    @Slot(str)
    def setReviewSearch(self, value):
        if self._review_editing or self._review_saving:
            return
        self._review_search = value[:500]
        self._filter_review()

    def _filter_review(self):
        query = self._review_search.casefold().strip()
        self._review_entries = [
            entry
            for entry in self._review_all_entries
            if (
                self._review_filter == "all"
                or (entry["issueCount"] > 0 and not entry.get("accepted"))
            )
            and (
                not query
                or query
                in " ".join(
                    (
                        entry["source"],
                        entry["translation"],
                        tr(entry["location"]),
                        *(tr(m) for m in entry["messages"]),
                    )
                ).casefold()
            )
        ]
        if not any(entry["id"] == self._review_selected for entry in self._review_entries):
            self._review_selected = self._review_entries[0]["id"] if self._review_entries else ""
        self.changed.emit()

    @Slot(str)
    def selectReviewEntry(self, entry_id):
        if self._review_editing or self._review_saving:
            return
        if any(entry["id"] == entry_id for entry in self._review_entries):
            self._review_selected = entry_id
            self.changed.emit()

    @Slot(int)
    def stepReviewEntry(self, delta):
        index = next(
            (
                i
                for i, entry in enumerate(self._review_entries)
                if entry["id"] == self._review_selected
            ),
            -1,
        )
        target = index + (1 if delta > 0 else -1)
        if 0 <= target < len(self._review_entries):
            self.selectReviewEntry(self._review_entries[target]["id"])

    @Slot(str)
    def selectReviewDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        if not any(d["id"] == document_id for d in self.reviewDocuments):
            return
        self._review_document_id = document_id
        self._review_enabled = True
        self._review_search = ""
        self._reload_review()

    @Slot()
    def activateReview(self):
        if self._review_editing or self._review_saving:
            return
        self._review_enabled = True
        if not self._review_document() and self.reviewDocuments:
            self._review_document_id = self.reviewDocuments[0]["id"]
        self._reload_review()

    def _reload_review(self):
        if self._review_editing or self._review_saving:
            return
        self._review_generation += 1
        generation = self._review_generation
        self._review_all_entries, self._review_entries, self._review_selected = [], [], ""
        self._review_summary = self._empty_review_summary()
        self._review_context = None
        self._review_accepted, self._review_edits = {}, {}
        self._review_action_error = ""
        self.reviewEditChanged.emit()
        self._review_pending = False
        document = self._review_document()
        if not document or self._closing or not self._review_enabled:
            self.changed.emit()
            return
        result, report = document.get("result"), document.get("report", "")
        if not result and not report:
            self.changed.emit()
            return
        self._review_summary["loading"] = True
        if self._review_running:
            self._review_pending = True
            self.changed.emit()
            return
        self._review_running = True
        options = Options(
            source_language=document.get("sourceLanguage") or "auto",
            target_language=document.get("targetLanguage") or "uk",
        )

        def read(emit):
            state = {"reportFingerprint": "", "accepted": {}, "edits": {}}
            state_error = ""
            if report:
                try:
                    state = ReviewStore().load(Path(report))
                except FormaError as exc:
                    state_error = str(exc)
                current_result, current_options = load_report(Path(report))
                if state["reportFingerprint"]:
                    verified = ReviewStore().load(Path(report))
                    if state["reportFingerprint"] != verified["reportFingerprint"]:
                        raise FormaError("Звіт змінився під час читання. Відкрийте його знову.")
            else:
                current_result, current_options = result, options
            segment_ids = {s.id for s in current_result.segments}
            if not set(state["edits"]) <= segment_ids:
                raise FormaError(
                    "Чернетка містить невідомі фрагменти. Збережені правки не змінено."
                )
            payload = build_review(
                current_result, current_options, edits=state["edits"], accepted=state["accepted"]
            )
            payload.update(
                context={
                    "result": current_result,
                    "options": current_options,
                    "report": Path(report) if report else None,
                    "reportFingerprint": state["reportFingerprint"],
                },
                state=state,
                stateError=state_error,
            )
            emit("review", payload)

        def receive(kind, payload):
            if generation != self._review_generation:
                return
            if kind == "review":
                self._review_context = payload["context"]
                self._review_accepted, self._review_edits = (
                    payload["state"]["accepted"],
                    payload["state"]["edits"],
                )
                self._review_action_error = payload["stateError"]
                self.reviewEditChanged.emit()
                self._review_all_entries = payload["entries"]
                self._review_summary = payload["summary"]
                self._sync_review_counts()
                self._filter_review()
            else:
                self._review_summary.update(loading=False, error=payload)
                self.changed.emit()

        def finish():
            self._review_running = False
            if self._review_pending and not self._closing:
                self._reload_review()

        self._launch(read, handler=receive, done=finish)
        self.changed.emit()

    @Slot(str)
    def reviewHistory(self, entry_id):
        if self._review_editing or self._review_saving:
            return
        entry = self._history_entry(entry_id)
        if not entry or not entry.get("report"):
            return
        self._history_review = {
            **entry,
            "id": "history:" + entry_id,
            "path": entry.get("source", ""),
            "status": "done",
        }
        self.selectReviewDocument(self._history_review["id"])

    @Slot()
    def openReviewReport(self):
        document = self._review_document()
        if document and document.get("report"):
            self.openPath(str(Path(document["report"]).with_suffix(".html")))

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
        except FormaError as exc:
            self._notice(str(exc), "error")

    @Property(bool, notify=changed)
    def canAddDocuments(self):
        return not self._closing and (not self._busy or self._translation_active)

    @Property("QVariantList", notify=changed)
    def historyEntries(self):
        return [
            {
                **entry,
                "error": tr(entry.get("error", "")),
                "sourceExists": bool(entry.get("source")) and Path(entry["source"]).is_file(),
                "outputExists": bool(entry.get("output")) and Path(entry["output"]).is_file(),
                "reportExists": bool(entry.get("report"))
                and Path(entry["report"]).with_suffix(".html").is_file(),
                "reviewAvailable": bool(entry.get("report")) and Path(entry["report"]).is_file(),
            }
            for entry in self._history
        ]

    @Property(bool, notify=changed)
    def historyLoading(self):
        return self._history_loading

    @Property("QStringList", notify=changed)
    def modelChoices(self):
        return self._model_choices

    @Property(bool, notify=changed)
    def modelsBusy(self):
        return self._models_busy

    @Property(str, notify=changed)
    def modelsMessage(self):
        return tr(self._models_message)

    @Slot()
    def clearModels(self):
        self._models_generation += 1
        self._model_choices = []
        self._models_message = ""
        self.changed.emit()

    @Slot("QVariantMap")
    def refreshModels(self, values):
        if self._models_busy or self._closing:
            return
        try:
            base = str(values.get("baseUrl", self._settings.base_url)).strip()
            key = str(values.get("apiKey", "")).strip()
            endpoint = canonical_endpoint(base)
            try:
                same = endpoint == canonical_endpoint(self._settings.base_url)
            except FormaError:
                same = False
            if not key:
                if same:
                    key = self._settings.api_key
                else:
                    try:
                        key = load_api_key(base)
                    except CredentialError:
                        key = ""  # Local compatible providers may not need authentication.
            settings = self._settings.with_overrides(
                base_url=base,
                api_key=key or "",
                api_mode=str(values.get("apiMode", self._settings.api_mode)),
            )
            settings.validate(require_connection=False)
        except (FormaError, CredentialError) as exc:
            self._models_message = str(exc)
            self.changed.emit()
            return
        self.clearModels()
        generation = self._models_generation
        self._models_busy = True
        self._models_message = "Отримуємо список моделей…"

        def fetch(emit):
            emit("models", list_models(settings))

        def receive(kind, payload):
            if generation != self._models_generation:
                return
            if kind == "models":
                self._model_choices = payload
                self._models_message = (
                    f"Моделей: {len(payload)}. Оберіть текстову модель; назву також можна ввести вручну."
                    if payload
                    else "Список порожній. Введіть назву моделі вручну."
                )
            else:
                self._models_message = payload
            self.changed.emit()

        def finish():
            self._models_busy = False
            self.changed.emit()

        self._launch(fetch, handler=receive, done=finish)
        self.changed.emit()

    @Slot()
    def openOutputFolder(self):
        try:
            folder = Path(self._folder).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            self.openPath(str(folder))
        except OSError:
            self._notice("Не вдалося відкрити папку результатів.", "error")

    def _history_entry(self, entry_id):
        return next((entry for entry in self._history if entry["id"] == entry_id), None)

    @Slot(str)
    def openHistoryOutput(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry:
            self.openPath(entry.get("output", ""))

    @Slot(str)
    def openHistoryFolder(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry and entry.get("output"):
            self.openPath(str(Path(entry["output"]).parent))

    @Slot(str)
    def openHistoryReport(self, entry_id):
        entry = self._history_entry(entry_id)
        if entry and entry.get("report"):
            self.openPath(str(Path(entry["report"]).with_suffix(".html")))

    @Slot(str)
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

    @Slot(str)
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

    @Slot()
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

    def setWindow(self, window):
        self._pickers.window = window

    @Slot()
    def pickDocuments(self):
        if self.canAddDocuments:
            self._pickers.choose(
                "Додати документи",
                self.addFiles,
                multiple=True,
                name_filter="Документи (*.docx *.xlsx *.pdf)",
            )

    @Slot()
    def pickOutputFolder(self):
        if not self._busy:
            self._pickers.choose(
                "Папка перекладів",
                lambda paths: self.setOutputFolder(paths[0]),
                directory=self._folder,
                folder=True,
            )

    @Slot()
    def pickGlossary(self):
        if not self._busy:
            self._pickers.choose(
                "Глосарій CSV",
                lambda paths: self.loadGlossary(paths[0]),
                name_filter="Глосарій (*.csv)",
            )

    def _notice(self, message, kind="info", *, details=()):
        self._message, self._message_kind = message, kind
        self._message_details = list(details)
        self.changed.emit()

    def _launch(self, task, handler=None, done=None):
        thread = TaskThread(task, self)
        thread.event.connect(handler or self._handle_event)

        def cleanup():
            self._threads.remove(thread)
            if done:
                done()
            thread.deleteLater()
            if self._closing and not self._threads:
                self.closedReady.emit()

        thread.finished.connect(cleanup)
        self._threads.append(thread)
        thread.start()

    @Property("QVariantList", notify=changed)
    def documents(self):
        return [
            {
                **self._display_document(d),
                "canRemove": self._can_remove(d),
                "canMove": self._can_remove(d) and d["status"] in ("loading", "ready", "queued"),
            }
            for d in self._documents
        ]

    @Property(str, notify=changed)
    def selectedId(self):
        return self._selected

    def _selected_document(self):
        return next((d for d in self._documents if d["id"] == self._selected), None)

    @Property("QVariantMap", notify=changed)
    def selectedDocument(self):
        document = self._selected_document()
        return self._display_document(document) if document else {}

    @Property("QVariantList", notify=changed)
    def segments(self):
        document = self._selected_document()
        if not document:
            return []
        result = document.get("result")
        return [
            {
                "location": tr(s.location),
                "source": s.plain,
                "translation": plain_text(result.translations.get(s.id, "")) if result else "",
            }
            for s in document.get("segments", [])
        ]

    @Property("QVariantList", notify=changed)
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

    @Property("QVariantMap", notify=changed)
    def settings(self):
        s = self._settings
        return {
            "baseUrl": s.base_url,
            "model": s.model,
            "apiMode": s.api_mode,
            "responseFormat": s.response_format,
            "reviewModel": s.review_model,
            "reasoningEffort": s.reasoning_effort,
            "maxOutputTokens": s.max_output_tokens,
            "timeout": s.timeout,
            "maxParallelRequests": s.max_parallel_requests,
            "parallelDocuments": s.parallel_documents,
            "providerPreset": s.provider_preset,
            "requestsPerMinute": s.requests_per_minute,
            "babeldocPath": s.babeldoc_path,
            "scanConverterPath": s.scan_converter_path,
            "envPath": str(s.env_path),
            "keyPresent": bool(s.api_key),
            "keyStored": s.key_stored,
            "credentialStatus": tr(s.credential_status),
            "pdfReady": bool(find_babeldoc(s.babeldoc_path)),
            "scanConverterReady": bool(find_scan_converter(s.scan_converter_path)),
        }

    @Property(str, notify=changed)
    def translationInstructions(self):
        return self._settings.translation_instructions

    @Property(int, constant=True)
    def maximumInstructionsLength(self):
        return MAX_TRANSLATION_INSTRUCTIONS

    @Property(str, notify=changed)
    def instructionsError(self):
        return tr(self._instructions_error)

    @Slot(str, result=bool)
    def saveTranslationInstructions(self, value):
        if self.busy:
            return False
        try:
            validate_instructions(value)
            save_preferences({**load_preferences(), "translation_instructions": value})
        except FormaError as exc:
            self._instructions_error = str(exc)
        except OSError:
            self._instructions_error = "Не вдалося зберегти власні інструкції."
        else:
            self._settings = self._settings.with_overrides(translation_instructions=value)
            self._instructions_error = ""
            self.changed.emit()
            return True
        self.changed.emit()
        return False

    @Property("QVariantList", notify=languageChanged)
    def providerPresets(self):
        return provider_presets()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy or self._quick.busy

    @Property(bool, notify=changed)
    def paused(self):
        return self._paused

    @Property(bool, notify=changed)
    def translating(self):
        return self._translation_active

    @Property(bool, notify=changed)
    def recognizingScan(self):
        return self._scan_active

    @Property(int, notify=changed)
    def scanProgress(self):
        return self._scan_progress

    @Property(str, notify=changed)
    def scanMessage(self):
        return tr(self._scan_message)

    @Property(str, notify=changed)
    def queueSummary(self):
        active = sum(d["status"] == "translating" for d in self._documents)
        queued = sum(d["status"] == "queued" for d in self._documents)
        return tr(f"Перекладаємо: {active} · у черзі: {queued}")

    @Property(str, notify=changed)
    def message(self):
        if not self._message:
            return ""
        return " ".join(tr(message) for message in (self._message, *self._message_details))

    @Property(str, notify=changed)
    def messageKind(self):
        return self._message_kind

    @Property(str, notify=changed)
    def apiStatus(self):
        return tr(self._api_status)

    @Property(str, notify=changed)
    def outputFolder(self):
        return self._folder

    @Property(str, notify=changed)
    def glossaryName(self):
        return self._glossary_name

    @Property(int, notify=changed)
    def glossaryCount(self):
        return len(self._glossary)

    @Slot(str)
    def selectDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        self._selected = document_id
        self._review_document_id = document_id
        self._review_search = ""
        self._reload_review()
        self.changed.emit()

    @Slot()
    def dismissMessage(self):
        self._message = ""
        self.changed.emit()

    @Slot("QVariantList")
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
                    "tokens": "",
                    "warningCount": 0,
                }
            )
            if not self._selected:
                self._selected = did

            self._inspect_document(self._documents[-1])
        self._persist_queue()
        self.changed.emit()

    @Slot(str)
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

    @Slot(str)
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

    @Slot(str, int)
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

    @Slot()
    def clearCompleted(self):
        for document in self._documents[:]:
            if document["status"] == "done":
                self.removeDocument(document["id"])

    @Slot(str)
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

    @Slot(str)
    def loadGlossary(self, url):
        value = QUrl(url)
        path = Path(value.toLocalFile() if value.isLocalFile() else url)
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or not {"source", "target"} <= set(reader.fieldnames):
                    raise FormaError("CSV має містити стовпці source і target.")
                terms = {
                    row["source"].strip(): row["target"].strip()
                    for row in reader
                    if row.get("source") and row.get("target")
                }
            self._glossary = terms
            self._glossary_name = path.name
            self.changed.emit()
        except (OSError, UnicodeError, csv.Error, FormaError) as exc:
            self._notice(str(exc), "error")

    @Slot()
    def clearGlossary(self):
        self._glossary, self._glossary_name = {}, ""
        self.changed.emit()

    @Slot("QVariantMap", result=bool)
    def saveSettings(self, values):
        if self.busy:
            return False
        old = self._settings
        try:
            prefs = {
                key: str(values.get(ui, getattr(old, key))).strip()
                for key, ui in (
                    ("base_url", "baseUrl"),
                    ("model", "model"),
                    ("api_mode", "apiMode"),
                    ("response_format", "responseFormat"),
                    ("review_model", "reviewModel"),
                    ("babeldoc_path", "babeldocPath"),
                    ("scan_converter_path", "scanConverterPath"),
                    ("reasoning_effort", "reasoningEffort"),
                    ("provider_preset", "providerPreset"),
                )
            }
            raw_timeout = values.get("timeout", old.timeout)
            if isinstance(raw_timeout, bool):
                raise ValueError
            prefs["timeout"] = float(raw_timeout)
            prefs["max_output_tokens"] = int(
                str(values.get("maxOutputTokens", old.max_output_tokens))
            )
            for key, ui in (
                ("max_parallel_requests", "maxParallelRequests"),
                ("parallel_documents", "parallelDocuments"),
                ("requests_per_minute", "requestsPerMinute"),
            ):
                prefs[key] = int(str(values.get(ui, getattr(old, key))))
            candidate = old.with_overrides(**prefs)
            candidate.validate(require_connection=False)
            try:
                same_endpoint = canonical_endpoint(old.base_url) == canonical_endpoint(
                    candidate.base_url
                )
            except FormaError:
                same_endpoint = False
            supplied = str(values.get("apiKey", "")).strip()
            remember = bool(values.get("rememberKey", True))
            remove = bool(values.get("removeKey", False))
            notice = "Налаштування збережено."
            warning = False
            key, mode, key_source, stored = "", "none", "", False
            status = "API-ключ не задано"
            if remove:
                if not same_endpoint:
                    raise FormaError(
                        "Спочатку збережіть адресу API, потім видаліть ключ цього підключення."
                    )
                try:
                    delete_api_key(old.base_url)
                except CredentialError:
                    if old.key_stored or load_preferences().get("credential_mode") == "keyring":
                        raise
                    warning = True
                notice = "Ключ видалено з підключення. Інші налаштування збережено."
                if warning:
                    notice = "Ключ прибрано з поточного підключення. Сховище ОС недоступне; його вміст не змінено."
            else:
                loaded_from_vault = False
                key = supplied or (old.api_key if same_endpoint else "")
                if not key and not same_endpoint:
                    try:
                        key = load_api_key(candidate.base_url)
                        loaded_from_vault = bool(key)
                    except CredentialError:
                        pass
                if key and remember:
                    try:
                        save_api_key(candidate.base_url, key)
                    except CredentialError:
                        mode, key_source = "session", "session"
                        status = "Ключ діє до закриття програми: сховище ОС недоступне"
                        notice = "Налаштування збережено. Сховище ОС недоступне, тому ключ діє лише до закриття програми."
                        warning = True
                    else:
                        mode, key_source, stored = "keyring", "keyring", True
                        status = "Ключ збережено у сховищі облікових даних ОС"
                elif key:
                    try:
                        delete_api_key(candidate.base_url)
                    except CredentialError:
                        if (old.key_stored and same_endpoint) or loaded_from_vault:
                            raise
                    mode, key_source = "session", "session"
                    status = "Ключ діє лише до закриття програми"
                    notice = "Налаштування збережено. Ключ діє лише до закриття програми."
                elif (
                    same_endpoint
                    and remember
                    and load_preferences().get("credential_mode") == "keyring"
                ):
                    # A locked/unavailable vault is not a request to forget its entry.
                    mode, status = "keyring", old.credential_status
            prefs["credential_mode"] = mode
            save_preferences({**load_preferences(), **prefs})
            self._settings = candidate.with_overrides(
                api_key=key, key_source=key_source, key_stored=stored, credential_status=status
            )
            self._usage.refresh()
            self._api_status = "Налаштування збережено"
            self._notice(notice, "warning" if warning else "success")
            return True
        except (ValueError, TypeError):
            self._notice("Перевірте числові параметри підключення та паралельної обробки.", "error")
        except FormaError as exc:
            self._notice(str(exc), "error")
        except OSError:
            self._notice("Не вдалося зберегти налаштування у папці даних застосунку.", "error")
        return False

    @Slot()
    def reloadEnv(self):
        if self.busy:
            return
        try:
            self._settings = self._read_settings()
            self._usage.refresh()
            self._api_status = "Конфігурацію оновлено"
            self._notice("Налаштування перечитано. Збережені параметри застосунку мають пріоритет.")
        except FormaError as exc:
            self._notice(str(exc), "error")

    @Slot()
    def testConnection(self):
        if self.busy:
            return
        try:
            self._settings.validate()
        except FormaError as exc:
            self._notice(str(exc), "error")
            return
        self._busy = True
        self._api_status = "Перевіряємо API…"
        settings = self._settings

        def ping(emit):
            provider = Provider(settings)
            try:
                emit("api_ok", provider.ping())
            finally:
                provider.close()

        self._launch(ping, done=self._finished)
        self.changed.emit()

    @Slot(str, str, str, bool, bool)
    def start(self, source_language, target_language, domain, review, auto_glossary):
        if self.busy or self._review_editing or self._review_saving:
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
            tokens="",
            warningCount=0,
            error="",
        )

    @Slot()
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

    @Slot()
    def cancel(self):
        if self._control:
            self._control.cancelled.set()
            self._control.paused.clear()
            self._notice(
                "Зупиняємо розпізнавання PDF…"
                if self._scan_active
                else "Зупиняємо після поточного API-запиту. Перевірені фрагменти залишаться для продовження."
            )

    @Slot(str)
    def openPath(self, path):
        if path:
            target = Path(path).expanduser().resolve()
            if not target.exists():
                self._notice("Файл або папку переміщено чи видалено.", "warning")
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
                self._notice("Операційна система не змогла відкрити файл або папку.", "warning")

    @Slot()
    def openSelectedReport(self):
        doc = self._selected_document()
        if doc and doc.get("report"):
            self.openPath(str(Path(doc["report"]).with_suffix(".html")))

    @Slot(result=bool)
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
        self.cancel()
        self._notice("Завершуємо поточну операцію перед закриттям…")
        return False

    @Slot(str, object)
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
                )
                self._record_history(doc)
                if self._review_document_id == doc["id"]:
                    self._reload_review()
        if kind != "progress":
            self._persist_queue()
        self.changed.emit()
