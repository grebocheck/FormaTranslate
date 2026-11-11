from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot

from forma import __version__
from forma.core.models import (
    MAX_TRANSLATION_INSTRUCTIONS,
    FormaError,
)
from forma.localization.catalog import available_languages, set_language
from forma.localization.catalog import translate_ui_message as tr
from forma.memory.store import TranslationMemory
from forma.persistence.library import TranslationLibrary
from forma.presentation.documents_workflow import DocumentWorkflow
from forma.presentation.file_availability import FileAvailability
from forma.presentation.file_dialogs import FilePickers
from forma.presentation.glossary_controller import GlossaryController
from forma.presentation.history_workflow import HistoryWorkflow
from forma.presentation.memory_workflow import MemoryWorkflow
from forma.presentation.quick_controller import QuickTranslator
from forma.presentation.review_workflow import ReviewWorkflow
from forma.presentation.settings_workflow import SettingsWorkflow
from forma.presentation.usage_controller import UsageController
from forma.providers.presets import provider_presets
from forma.settings.config import Settings, load_preferences


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


class Backend(
    QObject, ReviewWorkflow, HistoryWorkflow, SettingsWorkflow, MemoryWorkflow, DocumentWorkflow
):
    changed = Signal()
    languageChanged = Signal()
    reviewEditChanged = Signal()
    closedReady = Signal()
    workerAvailabilityReady = Signal(int, object)

    def __init__(self):
        super().__init__()
        preference = os.environ.get("FORMA_UI_LANGUAGE") or load_preferences().get(
            "ui_language", "system"
        )
        self._ui_language = (
            preference if preference in {"system", "uk", "en", "pl", "de"} else "system"
        )
        set_language(self._ui_language)
        self._file_status = FileAvailability(self)
        self._file_status.changed.connect(self.changed.emit)
        self._segments_cache = None
        self._history_cache = None
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
        self._worker_availability = {"pdfReady": False, "scanConverterReady": False}
        self._worker_probe_key = None
        self._worker_probe_generation = 0
        self._worker_probe_running = False
        self.workerAvailabilityReady.connect(self._receive_worker_availability)
        self._usage = UsageController(self, settings=lambda: self._settings)
        self.languageChanged.connect(self._usage.changed.emit)
        self._folder = load_preferences().get("output_folder") or str(
            Path.home() / "Documents" / "Forma"
        )
        self._glossary = {}
        self._glossary_name = ""
        self._standalone_glossary = {}
        self._standalone_glossary_name = ""
        self._memory = None
        self._memory_projects = []
        self._memory_project = None
        self._memory_working = False
        self._memory_message = ""
        self._memory_message_error = False
        self._memory_reference = {"source": "", "output": "", "report": ""}
        self._memory_conflict = {}
        self._memory_conflict_request = None
        try:
            self._memory = TranslationMemory()
            self._memory_projects = self._memory.list_projects()
            if self._settings.memory_project_id:
                self._memory_project = self._memory.get_project(self._settings.memory_project_id)
                self._glossary = dict(self._memory_project.glossary)
        except FormaError as exc:
            self._memory_message, self._memory_message_error = str(exc), True
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
        self._glossary_editor = GlossaryController(
            self,
            snapshot=lambda: self._glossary_context(refresh=True),
            apply=self._apply_glossary_draft,
            documents=lambda: [
                d["path"]
                for d in self._documents
                if d.get("format") in {"DOCX", "XLSX"} and d.get("status") != "loading"
            ],
            settings=lambda: self._settings,
            available=lambda: (
                not self.busy
                and not self._closing
                and not self._review_editing
                and not self._review_saving
            ),
            launch=self._launch,
            pickers=self._pickers,
        )
        self._glossary_editor.activityChanged.connect(self.changed.emit)
        self.changed.connect(self._glossary_editor.changed.emit)
        self.languageChanged.connect(self._glossary_editor.changed.emit)
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
        if self._settings.memory_project_id and self._memory_message_error and not self._message:
            self._notice(self._memory_message, "warning")
        self._refresh_worker_availability()

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
        return SettingsWorkflow.setUiLanguage(self, code)

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

    @Property("QVariantList", notify=changed)
    def reviewDocuments(self):
        return ReviewWorkflow.reviewDocuments(self)

    @Property("QVariantMap", notify=changed)
    def reviewDocument(self):
        return ReviewWorkflow.reviewDocument(self)

    @Property(str, notify=changed)
    def reviewDocumentId(self):
        return self._review_document_id

    @Property("QVariantList", notify=changed)
    def reviewEntries(self):
        return ReviewWorkflow.reviewEntries(self)

    @Property("QVariantMap", notify=changed)
    def reviewSummary(self):
        return ReviewWorkflow.reviewSummary(self)

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
        return ReviewWorkflow.selectedReviewEntry(self)

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
        return ReviewWorkflow.beginReviewEdit(self)

    @Slot(str)
    def updateReviewDraft(self, text):
        return ReviewWorkflow.updateReviewDraft(self, text)

    @Slot()
    def cancelReviewEdit(self):
        return ReviewWorkflow.cancelReviewEdit(self)

    @Slot(result=bool)
    def applyReviewEdit(self):
        return ReviewWorkflow.applyReviewEdit(self)

    @Slot()
    def discardReviewEdit(self):
        return ReviewWorkflow.discardReviewEdit(self)

    @Slot()
    def confirmReviewEntry(self):
        return ReviewWorkflow.confirmReviewEntry(self)

    @Slot()
    def reopenReviewEntry(self):
        return ReviewWorkflow.reopenReviewEntry(self)

    @Slot()
    def exportReviewEdits(self):
        return ReviewWorkflow.exportReviewEdits(self)

    @Slot(str)
    def setReviewFilter(self, value):
        return ReviewWorkflow.setReviewFilter(self, value)

    @Slot(str)
    def setReviewSearch(self, value):
        return ReviewWorkflow.setReviewSearch(self, value)

    @Slot(str)
    def selectReviewEntry(self, entry_id):
        return ReviewWorkflow.selectReviewEntry(self, entry_id)

    @Slot(int)
    def stepReviewEntry(self, delta):
        return ReviewWorkflow.stepReviewEntry(self, delta)

    @Slot(str)
    def selectReviewDocument(self, document_id):
        return ReviewWorkflow.selectReviewDocument(self, document_id)

    @Slot()
    def activateReview(self):
        return ReviewWorkflow.activateReview(self)

    @Slot(str)
    def reviewHistory(self, entry_id):
        return ReviewWorkflow.reviewHistory(self, entry_id)

    @Slot()
    def openReviewReport(self):
        return ReviewWorkflow.openReviewReport(self)

    @Property(bool, notify=changed)
    def canAddDocuments(self):
        return not self._closing and (not self._busy or self._translation_active)

    @Property("QVariantList", notify=changed)
    def historyEntries(self):
        return HistoryWorkflow.historyEntries(self)

    @Slot()
    def refreshFileAvailability(self):
        self._file_status.refresh()

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
        return SettingsWorkflow.clearModels(self)

    @Slot("QVariantMap")
    def refreshModels(self, values):
        return SettingsWorkflow.refreshModels(self, values)

    @Slot()
    def openOutputFolder(self):
        return HistoryWorkflow.openOutputFolder(self)

    @Slot(str)
    def openHistoryOutput(self, entry_id):
        return HistoryWorkflow.openHistoryOutput(self, entry_id)

    @Slot(str)
    def openHistoryFolder(self, entry_id):
        return HistoryWorkflow.openHistoryFolder(self, entry_id)

    @Slot(str)
    def openHistoryReport(self, entry_id):
        return HistoryWorkflow.openHistoryReport(self, entry_id)

    @Slot(str)
    def requeueHistory(self, entry_id):
        return HistoryWorkflow.requeueHistory(self, entry_id)

    @Slot(str)
    def forgetHistory(self, entry_id):
        return HistoryWorkflow.forgetHistory(self, entry_id)

    @Slot()
    def importHistory(self):
        return HistoryWorkflow.importHistory(self)

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
        return DocumentWorkflow.documents(self)

    @Property(str, notify=changed)
    def selectedId(self):
        return self._selected

    @Property("QVariantMap", notify=changed)
    def selectedDocument(self):
        return DocumentWorkflow.selectedDocument(self)

    @Property("QVariantList", notify=changed)
    def segments(self):
        return DocumentWorkflow.segments(self)

    @Property("QVariantList", notify=changed)
    def issues(self):
        return DocumentWorkflow.issues(self)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        return SettingsWorkflow.settings(self)

    @Slot(int, object)
    def _receive_worker_availability(self, generation, available):
        return SettingsWorkflow._receive_worker_availability(self, generation, available)

    @Slot()
    def refreshWorkerAvailability(self):
        return SettingsWorkflow.refreshWorkerAvailability(self)

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
        return SettingsWorkflow.saveTranslationInstructions(self, value)

    @Property("QVariantList", notify=languageChanged)
    def providerPresets(self):
        return provider_presets()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy or self._quick.busy or self._glossary_editor.working

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

    @Property(QObject, constant=True)
    def glossaryEditor(self):
        return self._glossary_editor

    @staticmethod
    def _memory_project_data(project):
        if project is None:
            return {}
        return {
            "id": project.id,
            "name": project.name,
            "sourceLanguage": project.source_language,
            "targetLanguage": project.target_language,
            "glossaryRevision": project.glossary_revision,
            "glossaryCount": len(project.glossary),
        }

    @Property("QVariantList", notify=changed)
    def memoryProjects(self):
        return MemoryWorkflow.memoryProjects(self)

    @Property("QVariantMap", notify=changed)
    def memoryProject(self):
        return self._memory_project_data(self._memory_project)

    @Property(bool, notify=changed)
    def memoryWorking(self):
        return self._memory_working

    @Property(str, notify=changed)
    def memoryMessage(self):
        return tr(self._memory_message)

    @Property(bool, notify=changed)
    def memoryMessageError(self):
        return self._memory_message_error

    @Property("QVariantMap", notify=changed)
    def memoryReference(self):
        return dict(self._memory_reference)

    @Property("QVariantMap", notify=changed)
    def memoryConflict(self):
        return dict(self._memory_conflict)

    @Slot(str, result=bool)
    def selectMemoryProject(self, project_id):
        return MemoryWorkflow.selectMemoryProject(self, project_id)

    @Slot(str, str, str, result=bool)
    def createMemoryProject(self, name, source_language, target_language):
        return MemoryWorkflow.createMemoryProject(self, name, source_language, target_language)

    @Slot()
    def prepareMemoryReference(self):
        return MemoryWorkflow.prepareMemoryReference(self)

    @Slot(str, str)
    def setMemoryReferencePath(self, kind, value):
        return MemoryWorkflow.setMemoryReferencePath(self, kind, value)

    @Slot(str)
    def pickMemoryReference(self, kind):
        return MemoryWorkflow.pickMemoryReference(self, kind)

    @Slot(bool)
    def importMemoryReference(self, preferred=False):
        return MemoryWorkflow.importMemoryReference(self, preferred)

    @Property(bool, notify=changed)
    def canRememberReviewWording(self):
        return MemoryWorkflow.canRememberReviewWording(self)

    @Slot()
    def rememberReviewWording(self):
        return MemoryWorkflow.rememberReviewWording(self)

    @Slot()
    def clearMemoryConflict(self):
        return MemoryWorkflow.clearMemoryConflict(self)

    @Slot()
    def replaceReviewWording(self):
        return MemoryWorkflow.replaceReviewWording(self)

    @Slot(str)
    def selectDocument(self, document_id):
        return DocumentWorkflow.selectDocument(self, document_id)

    @Slot()
    def dismissMessage(self):
        self._message = ""
        self.changed.emit()

    @Slot("QVariantList")
    def addFiles(self, urls):
        return DocumentWorkflow.addFiles(self, urls)

    @Slot(str)
    def recognizeSelectedPdf(self, source_language):
        return DocumentWorkflow.recognizeSelectedPdf(self, source_language)

    @Slot(str)
    def removeDocument(self, document_id):
        return DocumentWorkflow.removeDocument(self, document_id)

    @Slot(str, int)
    def moveDocument(self, document_id, delta):
        return DocumentWorkflow.moveDocument(self, document_id, delta)

    @Slot()
    def clearCompleted(self):
        return DocumentWorkflow.clearCompleted(self)

    @Slot(str)
    def setOutputFolder(self, url):
        return DocumentWorkflow.setOutputFolder(self, url)

    @Slot(str)
    def loadGlossary(self, url):
        return MemoryWorkflow.loadGlossary(self, url)

    @Slot()
    def clearGlossary(self):
        return MemoryWorkflow.clearGlossary(self)

    @Slot("QVariantMap", result=bool)
    def saveSettings(self, values):
        return SettingsWorkflow.saveSettings(self, values)

    @Slot()
    def reloadEnv(self):
        return SettingsWorkflow.reloadEnv(self)

    @Slot()
    def testConnection(self):
        return SettingsWorkflow.testConnection(self)

    @Slot(str, str, str, bool, bool)
    def start(self, source_language, target_language, domain, review, auto_glossary):
        return DocumentWorkflow.start(
            self, source_language, target_language, domain, review, auto_glossary
        )

    @Slot()
    def togglePause(self):
        return DocumentWorkflow.togglePause(self)

    @Slot()
    def cancel(self):
        return DocumentWorkflow.cancel(self)

    @Slot(str)
    def openPath(self, path):
        return DocumentWorkflow.openPath(self, path)

    @Slot()
    def openSelectedReport(self):
        return DocumentWorkflow.openSelectedReport(self)

    @Slot()
    def openFailureReport(self):
        return DocumentWorkflow.openFailureReport(self)

    @Slot(result=bool)
    def requestClose(self):
        return DocumentWorkflow.requestClose(self)

    @Slot(str, object)
    def _handle_event(self, kind, payload):
        return DocumentWorkflow._handle_event(self, kind, payload)
