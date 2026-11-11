"""Memory workflow implementation for the Qt Backend facade.

This class deliberately contains no Qt descriptors or QObject inheritance.
Backend owns the Qt metaobject and supplies the shared desktop session state.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import QUrl

from forma.core.models import (
    FormaError,
    plain_text,
)
from forma.localization.catalog import translate_ui_message as tr
from forma.memory.importing import import_reference, prefer_review_segment
from forma.memory.store import MemoryConflict
from forma.settings.config import load_preferences, save_preferences


class MemoryWorkflow:
    def _glossary_context(self, *, refresh=False):
        if refresh and self._memory_project:
            project = self._memory.get_project(self._memory_project.id)
            changed = project.glossary_revision != self._memory_project.glossary_revision
            self._memory_project = project
            self._glossary = dict(project.glossary)
            if changed:
                self.changed.emit()
        return {
            "id": self._memory_project.id if self._memory_project else "standalone",
            "name": self._memory_project.name if self._memory_project else "",
            "revision": self._memory_project.glossary_revision if self._memory_project else None,
            "terms": dict(self._glossary),
        }

    def _apply_glossary_draft(self, context, terms):
        current = self._glossary_context()
        if context["id"] != current["id"]:
            raise FormaError("Вибраний проєкт змінився. Відкрийте глосарій потрібного проєкту.")
        if self._memory_project:
            self._memory_project = self._memory.update_glossary(
                self._memory_project.id,
                terms,
                expected_revision=context["revision"],
            )
            self._memory_projects = self._memory.list_projects()
        elif current["terms"] != context["terms"]:
            raise FormaError(
                "Глосарій змінився після відкриття. Скасуйте правки та відкрийте актуальний список."
            )
        self._glossary, self._glossary_name = dict(terms), ""
        self.changed.emit()
        return self._glossary_context()

    def memoryProjects(self):
        return [{"id": "", "name": tr("Вимкнено")}] + [
            self._memory_project_data(project) for project in self._memory_projects
        ]

    def _memory_notice(self, message, *, error=False):
        self._memory_message, self._memory_message_error = message, error
        self.changed.emit()

    def selectMemoryProject(self, project_id):
        if (
            self.busy
            or self._review_editing
            or self._review_saving
            or (project_id and not self._memory)
        ):
            return False
        try:
            project = self._memory.get_project(project_id) if project_id else None
            save_preferences({**load_preferences(), "memory_project_id": project_id})
            if self._memory_project is None:
                self._standalone_glossary = dict(self._glossary)
                self._standalone_glossary_name = self._glossary_name
            self._memory_project = project
            self._memory_conflict = {}
            self._memory_conflict_request = None
            self._settings = self._settings.with_overrides(memory_project_id=project_id)
            self._glossary = dict(project.glossary) if project else dict(self._standalone_glossary)
            self._glossary_name = "" if project else self._standalone_glossary_name
            if self._message == self._memory_message:
                self._message, self._message_details = "", []
            self._memory_message, self._memory_message_error = "", False
            self.changed.emit()
            return True
        except FormaError as exc:
            self._memory_notice(str(exc), error=True)
        except OSError:
            self._memory_notice("Не вдалося зберегти вибір проєкту пам’яті.", error=True)
        return False

    def createMemoryProject(self, name, source_language, target_language):
        if self.busy or self._review_editing or self._review_saving or not self._memory:
            return False
        if source_language == "auto":
            self._memory_notice("Для пам’яті проєкту виберіть мову оригіналу.", error=True)
            return False
        if source_language == target_language:
            self._memory_notice("Виберіть іншу мову перекладу.", error=True)
            return False
        try:
            project = self._memory.create_project(name, source_language, target_language)
            self._memory_projects = self._memory.list_projects()
            if self.selectMemoryProject(project.id):
                self._memory_notice("Проєкт пам’яті створено.")
                return True
        except FormaError as exc:
            self._memory_notice(str(exc), error=True)
        return False

    def prepareMemoryReference(self):
        if self.busy:
            return
        if self._memory:
            try:
                self._memory_projects = self._memory.list_projects()
                if self._memory_project:
                    self._memory_project = self._memory.get_project(self._memory_project.id)
                    self._glossary = dict(self._memory_project.glossary)
            except FormaError as exc:
                self._memory_message, self._memory_message_error = str(exc), True
        document = self._selected_document()
        if document and document.get("format") == "DOCX":
            if not self._memory_reference["source"]:
                self._memory_reference["source"] = document["path"]
            if not self._memory_reference["output"] and document.get("output"):
                self._memory_reference["output"] = document["output"]
            if not self._memory_reference["report"] and document.get("report"):
                self._memory_reference["report"] = document["report"]
        self.changed.emit()

    def setMemoryReferencePath(self, kind, value):
        if kind not in self._memory_reference or self.busy:
            return
        url = QUrl(value)
        self._memory_reference[kind] = url.toLocalFile() if url.isLocalFile() else value
        if kind == "output":
            adjacent = Path(self._memory_reference[kind] + ".forma.json")
            if adjacent.is_file():
                self._memory_reference["report"] = str(adjacent)
        self.changed.emit()

    def pickMemoryReference(self, kind):
        if kind not in self._memory_reference or self.busy:
            return
        report = kind == "report"
        self._pickers.choose(
            "Вибрати звіт еталона" if report else "Вибрати документ еталона",
            lambda paths: self.setMemoryReferencePath(kind, paths[0]),
            directory=self._memory_reference[kind] or self._folder,
            name_filter="Звіти Forma (*.forma.json)" if report else "Документи Word (*.docx)",
        )

    def importMemoryReference(self, preferred=False):
        if self.busy or not self._memory or not self._memory_project:
            return
        if not all(self._memory_reference.values()):
            self._memory_notice("Виберіть оригінал, переклад і звіт еталона.", error=True)
            return
        paths = {key: Path(value) for key, value in self._memory_reference.items()}
        memory, project_id = self._memory, self._memory_project.id
        self._memory_working, self._busy = True, True
        self._memory_message, self._memory_message_error = "", False

        def run(emit):
            summary = import_reference(
                memory,
                project_id,
                paths["source"],
                paths["output"],
                paths["report"],
                preferred=preferred,
            )
            emit("memory_reference", summary)

        def receive(kind, payload):
            if kind == "memory_reference":
                self._memory_notice(
                    f"Еталон додано: {payload['inserted']} фрагментів; схвалено: {payload['approved']}; "
                    f"довідкових: {payload['references']}."
                )
            else:
                self._memory_notice(payload, error=True)

        def finish():
            self._memory_working, self._busy = False, False
            self.changed.emit()

        self._launch(run, handler=receive, done=finish)
        self.changed.emit()

    def canRememberReviewWording(self):
        entry = self.selectedReviewEntry
        return bool(
            self._memory_project
            and not self.busy
            and not self._review_editing
            and not self._review_saving
            and entry.get("kind") == "segment"
            and (self._review_document() or {}).get("format") == "DOCX"
            and entry.get("canEdit")
            and entry.get("severity") != "error"
        )

    def rememberReviewWording(self):
        if not self.canRememberReviewWording or not self._memory:
            return
        document = dict(self._review_document() or {})
        context = self._review_context
        segment_id = self._review_selected
        override = self._review_edits.get(segment_id)
        memory, project_id = self._memory, self._memory_project.id
        request = {
            "project_id": project_id,
            "source": Path(document["path"]),
            "output": Path(document["output"]),
            "report": context["report"],
            "segment_id": segment_id,
            "translation_override": override,
            "expected_report_fingerprint": context["reportFingerprint"],
        }
        self._run_preferred_wording(memory, request)

    def _run_preferred_wording(self, memory, request):
        self._memory_working, self._busy = True, True
        self._memory_conflict = {}
        self._review_action_error = ""

        def run(emit):
            try:
                prefer_review_segment(memory, **request)
                emit("memory_preferred", None)
            except MemoryConflict as exc:
                expected_revision = getattr(exc, "expected_revision", None)
                expected_glossary = getattr(exc, "expected_glossary_revision", None)
                if (
                    getattr(exc, "reason", "") == "approved_conflict"
                    and type(expected_revision) is int
                    and type(expected_glossary) is int
                ):
                    emit(
                        "memory_conflict",
                        {
                            "active": True,
                            "previous": plain_text(getattr(exc, "current_target", "") or ""),
                            "replacement": plain_text(getattr(exc, "new_target", "") or ""),
                            "expected_revision": expected_revision,
                            "expected_glossary_revision": expected_glossary,
                        },
                    )
                else:
                    raise

        def receive(kind, payload):
            if kind == "memory_preferred":
                self._memory_conflict_request = None
                self._notice("Формулювання збережено для наступних запусків проєкту.")
            elif kind == "memory_conflict":
                self._memory_conflict = payload
                self._memory_conflict_request = dict(request)
                self.changed.emit()
            else:
                self._review_action_error = payload
                self.reviewEditChanged.emit()

        def finish():
            self._memory_working, self._busy = False, False
            self.changed.emit()

        self._launch(run, handler=receive, done=finish)
        self.changed.emit()

    def clearMemoryConflict(self):
        if self._memory_working:
            return
        self._memory_conflict = {}
        self._memory_conflict_request = None
        self.changed.emit()

    def replaceReviewWording(self):
        if (
            self.busy
            or not self._memory_conflict_request
            or not self._memory
            or not self._memory_conflict.get("active")
        ):
            return
        request = {
            **self._memory_conflict_request,
            "replace": True,
            "expected_revision": self._memory_conflict["expected_revision"],
            "expected_glossary_revision": self._memory_conflict["expected_glossary_revision"],
        }
        self._run_preferred_wording(self._memory, request)

    def loadGlossary(self, url):
        if self.busy:
            return
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
            if self._memory_project:
                self._memory_project = self._memory.update_glossary(
                    self._memory_project.id,
                    terms,
                    expected_revision=self._memory_project.glossary_revision,
                )
                self._memory_projects = self._memory.list_projects()
                self._memory_notice("Спільний глосарій проєкту оновлено.")
            self._glossary = terms
            self._glossary_name = path.name
            self.changed.emit()
        except (OSError, UnicodeError, csv.Error, FormaError) as exc:
            if self._memory_project:
                self._memory_notice(str(exc), error=True)
            else:
                self._notice(str(exc), "error")

    def clearGlossary(self):
        if self.busy:
            return
        if self._memory_project:
            try:
                self._memory_project = self._memory.update_glossary(
                    self._memory_project.id,
                    {},
                    expected_revision=self._memory_project.glossary_revision,
                )
                self._memory_projects = self._memory.list_projects()
                self._memory_notice("Спільний глосарій проєкту оновлено.")
            except FormaError as exc:
                self._memory_notice(str(exc), error=True)
                return
        self._glossary, self._glossary_name = {}, ""
        self.changed.emit()
