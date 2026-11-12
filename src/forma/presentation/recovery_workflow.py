"""Manual resolution of blocked Office fragments, followed by normal translation.

Recovery never exports a partial document or writes into a live checkpoint. The
engine consumes the separate, validated corrections under its document lock.
"""

from __future__ import annotations

from pathlib import Path

from forma.core.models import FormaError, Options
from forma.translation.recovery import save_recovery_edits, validate_recovery_settings


class RecoveryWorkflow:
    def _can_resume_recovery(self):
        context = self._review_context or {}
        metadata = context.get("recovery") or {}
        return bool(
            metadata.get("can_resume")
            and context.get("reportFingerprint")
            and self._review_all_entries
            and not self.busy
            and not self._review_editing
            and not self._review_saving
            and not any(entry["severity"] == "error" for entry in self._review_all_entries)
        )

    def _resume_recovery(self):
        if not self._can_resume_recovery() or self._closing:
            return
        context = self._review_context
        metadata = context["recovery"]
        document = self._review_document()
        if not document or not any(document is item for item in self._documents):
            return
        try:
            settings = self._settings
            settings.validate()
            validate_recovery_settings(metadata, settings)
            options = Options(**metadata["requested_options"])
            options.validate()
            if Path(document["path"]).resolve() != Path(metadata["original_source"]).resolve():
                raise FormaError("Оригінал змінився. Відкрийте документ і повторіть переклад.")
        except (FormaError, TypeError, ValueError, OSError) as exc:
            self._review_action_error = str(exc)
            self.reviewEditChanged.emit()
            self.changed.emit()
            return
        values = {
            segment.id: self._review_edits.get(
                segment.id, context["result"].translations.get(segment.id, "")
            )
            for segment in context["result"].segments
        }
        self._review_saving = True
        self._review_action_error = ""
        self.reviewEditChanged.emit()
        self.changed.emit()

        def prepare(emit):
            save_recovery_edits(
                context["report"], values,
                expected_report_fingerprint=context["reportFingerprint"],
            )
            emit("recovery_ready", None)

        def receive(kind, payload):
            if kind != "recovery_ready":
                self._review_action_error = payload
                self.reviewEditChanged.emit()
                return
            if self._closing:
                return
            self._review_saving = False
            self._start_documents(
                [document], options, settings=settings,
                recovery_checkpoint=metadata["checkpoint_id"],
            )
            self.recoveryStarted.emit()

        def finish():
            self._review_saving = False
            self.reviewEditChanged.emit()
            self.changed.emit()

        self._launch(prepare, handler=receive, done=finish)
