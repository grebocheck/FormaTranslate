"""Local OCR and assisted external-editor workflows; no Qt descriptors."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from forma.core.models import Cancelled, FormaError
from forma.documents.abbyy import (
    convert_with_abbyy,
    find_abbyy,
    import_abbyy_result,
    launch_abbyy_handoff,
)
from forma.documents.ocr import convert_scan_pdf, find_scan_converter
from forma.documents.ocr_diagnostics import ocr_warning_messages
from forma.documents.ocr_policy import resolve_scan_provider, validate_scan_provider
from forma.providers.client import JobControl


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _scan_review_warnings(output: Path) -> list[str]:
    """Expose only known OCR findings; never display arbitrary manifest content."""
    try:
        with output.with_suffix(".ocr.json").open("rb") as stream:
            raw = stream.read(50 * 1024 * 1024 + 1)
        if len(raw) > 50 * 1024 * 1024:
            return []
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return []
    return ocr_warning_messages(data) if isinstance(data, dict) else []


class OcrWorkflow:
    def _selected_scan_document(self, source_language):
        if self.busy or self._closing or self._review_editing or self._review_saving:
            return None
        document = next((d for d in self._documents if d["id"] == self._selected), None)
        if not document or document.get("format") != "PDF" or document["status"] == "loading":
            return None
        if source_language == "auto":
            self._notice("Для розпізнавання PDF виберіть мову оригіналу.", "warning")
            return None
        return document

    def recognizeSelectedPdf(self, source_language):
        document = self._selected_scan_document(source_language)
        if document:
            self._recognize_pdf(document, source_language, self._settings.scan_provider)

    def openSelectedInAbbyy(self, source_language):
        document = self._selected_scan_document(source_language)
        if document:
            self._recognize_pdf(document, source_language, "abbyy")

    def _recognize_pdf(self, document, source_language, provider):
        try:
            validate_scan_provider(
                provider, extended_cli_confirmed=self._settings.abbyy_extended_cli_confirmed,
            )
        except FormaError as exc:
            self._notice(str(exc), "error")
            return
        source = Path(document["path"])
        folder, settings = Path(self._folder), self._settings

        def work(emit, control):
            effective = resolve_scan_provider(
                settings.with_overrides(scan_provider=provider),
                local_finder=find_scan_converter, abbyy_finder=find_abbyy,
            )
            if effective == "abbyy":
                fingerprint = _digest(source)
                control.check()
                launch_abbyy_handoff(source, source_language, settings.abbyy_path)
                emit("abbyy_handoff", {"sourceId": document["id"], "name": source.name,
                     "path": str(source), "language": source_language, "sourceSha256": fingerprint,
                     "promptId": uuid.uuid4().hex, "showPrompt": True})
                return
            if effective == "abbyy_extended":
                output = convert_with_abbyy(source, folder, source_language, settings.abbyy_path,
                    control, lambda percent, message: emit("scan_progress", (percent, message)),
                    extended_cli_confirmed=settings.abbyy_extended_cli_confirmed)
            else:
                output = convert_scan_pdf(source, folder, source_language, settings.scan_converter_path,
                    control, lambda percent, message: emit("scan_progress", (percent, message)))
            emit("scan_result", (output, _scan_review_warnings(output)))

        self._start_scan_task(document, work)

    def _start_scan_task(self, document, work):
        self._busy, self._scan_active = True, True
        self._scan_progress = 0
        self._scan_message = "Готуємо розпізнавання PDF"
        self._message = ""
        self._control = JobControl(1)
        control = self._control
        converted = []

        def run(emit):
            try:
                work(emit, control)
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
            elif kind == "abbyy_handoff":
                self._abbyy_handoff = payload
                self._notice("ABBYY запущено. Відкрийте PDF у редакторі, збережіть його як DOCX і поверніть файл у Forma.")
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
                prepared = next((d for d in self._documents if d["path"] == str(output.resolve())), None)
                if prepared:
                    self.selectDocument(prepared["id"])
                if self._abbyy_handoff.get("sourceId") == document["id"]:
                    self._abbyy_handoff = {}
                self._notice("DOCX після OCR додано до черги. Відкрийте оригінал, перевірте текст і таблиці, "
                    "потім натисніть «Перекласти».", "warning", details=warnings)
            self.changed.emit()

        self._launch(run, handler=receive, done=finish)
        self.changed.emit()

    def copyAbbyySourcePath(self):
        if self._abbyy_handoff.get("path"):
            QGuiApplication.clipboard().setText(self._abbyy_handoff["path"])

    def dismissAbbyyHandoff(self):
        if self._abbyy_handoff:
            self._abbyy_handoff["showPrompt"] = False
            self.changed.emit()

    def importAbbyyResult(self, reviewed):
        handoff = dict(self._abbyy_handoff)
        chained = handoff.get("continueTranslation") is True
        continuation = self._pdf_continuation if chained else None
        if (self.busy and not chained) or self._closing or self._review_editing or self._review_saving:
            return
        if chained and (handoff.get("importing") or not continuation or not continuation.context(handoff.get("promptId"))):
            return
        if not reviewed:
            self._notice("Перед імпортом звірте DOCX із PDF: усі сторінки, текст, числа та таблиці.", "warning")
            return
        document = next((d for d in self._documents if d["id"] == handoff.get("sourceId")), None)
        if not document:
            self._notice("Вихідний PDF уже видалено з черги. Виберіть PDF і повторіть відкриття ABBYY.", "warning")
            return

        def selected(paths):
            if (self.busy and not chained) or self._closing or self._review_editing or self._review_saving:
                return
            if chained:
                self._import_translation_ocr(continuation, handoff, paths)
                return
            if not any(d["id"] == document["id"] for d in self._documents):
                return
            source, folder = Path(handoff["path"]), Path(self._folder)

            def work(emit, control):
                if _digest(source) != handoff["sourceSha256"]:
                    raise FormaError("PDF змінився після відкриття ABBYY. Повторіть розпізнавання оновленого документа.")
                output = import_abbyy_result(source, Path(paths[0]), folder, handoff["language"], control,
                                              expected_source_sha256=handoff["sourceSha256"])
                emit("scan_result", (output, _scan_review_warnings(output)))

            self._start_scan_task(document, work)

        self._pickers.choose("Імпортувати DOCX з ABBYY", selected, directory=self._folder,
                             name_filter="Документи Word (*.docx)")

    def _import_translation_ocr(self, continuation, handoff, paths):
        """Validate an editor result without restarting or replacing its queue run."""
        prompt_id = handoff["promptId"]
        if not paths or self._pdf_continuation is not continuation or not continuation.context(prompt_id):
            return
        if self._abbyy_handoff.get("promptId") != prompt_id or self._abbyy_handoff.get("importing"):
            return
        self._abbyy_handoff["importing"] = True
        self.changed.emit()

        def run(emit):
            source = Path(handoff["path"])
            output = import_abbyy_result(
                source, Path(paths[0]), continuation.folder, handoff["language"], continuation.control,
                expected_source_sha256=handoff["sourceSha256"],
            )
            if continuation.complete(prompt_id, output):
                emit("imported", None)

        def receive(kind, payload):
            if self._closing or self._pdf_continuation is not continuation:
                return
            if kind == "error" and not continuation.control.cancelled.is_set():
                self._notice(payload, "error")

        def finish():
            if self._abbyy_handoff.get("promptId") == prompt_id:
                self._abbyy_handoff["importing"] = False
                self.changed.emit()

        self._launch(run, handler=receive, done=finish)
