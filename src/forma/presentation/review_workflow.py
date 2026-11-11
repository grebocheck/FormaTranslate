"""Review workflow implementation for the Qt Backend facade.

This class deliberately contains no Qt descriptors or QObject inheritance.
Backend owns the Qt metaobject and supplies the shared desktop session state.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from forma.core.models import (
    FormaError,
    Options,
)
from forma.documents.editor import edit_translation_markup, export_reviewed_copy
from forma.documents.review import actionable_count, build_review, load_report
from forma.localization.catalog import translate_ui_message as tr
from forma.persistence.review_store import ReviewStore
from forma.presentation.file_availability import inspect_files
from forma.translation.engine import blocking_issues


class ReviewWorkflow:
    def _review_document(self):
        if self._history_review and self._history_review["id"] == self._review_document_id:
            return self._history_review
        return next((d for d in self._documents if d["id"] == self._review_document_id), None)

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

    def reviewDocument(self):
        document = self._review_document()
        if not document:
            return {}
        return {
            **self._display_document(document),
            "outputExists": self._file_status.is_file(document.get("output")),
            "reportExists": bool(document.get("report"))
            and self._file_status.is_file(Path(document["report"]).with_suffix(".html")),
        }

    def reviewEntries(self):
        return [self._display_entry(entry) for entry in self._review_entries]

    def reviewSummary(self):
        return {**self._review_summary, "error": tr(self._review_summary.get("error", ""))}

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
        elif not document.get("path") or not self._file_status.is_file(document["path"]):
            reason = "Оригінальний файл недоступний. Він потрібен для збереження форматування."
        elif not document.get("output") or not self._file_status.is_file(document["output"]):
            reason = "Файл перекладу переміщено або видалено."
        return {
            **self._display_entry(entry),
            "canAccept": persisted and entry["severity"] != "error" and not self._review_saving,
            "canEdit": not reason and not self._review_saving,
            "editReason": tr(reason),
        }

    def beginReviewEdit(self):
        entry = self.selectedReviewEntry
        if self._review_editing or not entry.get("canEdit"):
            return
        self._review_editing = True
        self._review_draft_text = entry["translation"]
        self._review_edit_error = self._review_action_error = ""
        self.reviewEditChanged.emit()

    def updateReviewDraft(self, text):
        if self._review_editing:
            self._review_draft_text = text
            self._review_edit_error = ""
            self.reviewEditChanged.emit()

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

    def confirmReviewEntry(self):
        entry = self.selectedReviewEntry
        if self._review_editing or not entry.get("canAccept"):
            return
        accepted = {**self._review_accepted, entry["id"]: entry["fingerprint"]}
        self._change_review_state(accepted, dict(self._review_edits))

    def reopenReviewEntry(self):
        entry = self.selectedReviewEntry
        if self._review_editing or self._review_saving or not entry.get("accepted"):
            return
        accepted = dict(self._review_accepted)
        accepted.pop(entry["id"], None)
        self._change_review_state(accepted, dict(self._review_edits))

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

    def setReviewFilter(self, value):
        if not self._review_editing and not self._review_saving and value in ("issues", "all"):
            self._review_filter = value
            self._filter_review()

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

    def selectReviewEntry(self, entry_id):
        if self._review_editing or self._review_saving:
            return
        if any(entry["id"] == entry_id for entry in self._review_entries):
            self._review_selected = entry_id
            self.changed.emit()

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

    def selectReviewDocument(self, document_id):
        if self._review_editing or self._review_saving:
            return
        if not any(d["id"] == document_id for d in self.reviewDocuments):
            return
        self._review_document_id = document_id
        self._review_enabled = True
        self._review_search = ""
        self._reload_review()

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
                fileStatus=inspect_files(
                    (
                        document.get("path"),
                        document.get("output"),
                        report,
                        str(Path(report).with_suffix(".html")) if report else "",
                    )
                ),
            )
            emit("review", payload)

        def receive(kind, payload):
            if generation != self._review_generation:
                return
            if kind == "review":
                self._file_status.update(payload["fileStatus"], notify=False)
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

    def openReviewReport(self):
        document = self._review_document()
        if document and document.get("report"):
            self.openPath(str(Path(document["report"]).with_suffix(".html")))
