"""Restore a tracked Office completion over an older terminal queue snapshot.

Only history supplies candidates. Referenced files and report hashes are checked
in a worker; no folder search, provider request or document edit is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from forma.core.models import FormaError, Options
from forma.documents.review import parse_report_data, refresh_segment_checks
from forma.persistence.review_store import ReviewStore
from forma.presentation.file_availability import inspect_files
from forma.translation.engine import file_digest

_IDENTITY = ("sourceLanguage", "targetLanguage", "model", "reviewModel")
_HASH = re.compile(r"[0-9a-f]{64}")


def _path(value):
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _candidate(document, history):
    matching = [
        record for record in history
        if record.get("source") and _path(record["source"]) == _path(document["path"])
        and all(record.get(key, "") == document.get(key, "") for key in _IDENTITY)
    ]
    if not matching or not all(document.get(key) for key in _IDENTITY[:-1]):
        return None
    record = max(matching, key=lambda item: item.get("finishedAt", 0))
    finished = record.get("finishedAt", 0)
    failed = max((item.get("finishedAt", 0) for item in matching if item.get("status") != "done"), default=0)
    started = document.get("startedAt", 0)
    threshold = started + document.get("elapsedSeconds", 0) * 1000 if started else 0
    if (
        record.get("status") != "done" or finished <= max(failed, threshold)
        or not (failed or started)
    ):
        return None
    return record


def _report_bytes(path):
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError
    return raw


def _validated_completion(document, record):
    source, output, report = (Path(document["path"]), Path(record.get("output", "")),
                              Path(record.get("report", "")))
    if source.suffix.lower() not in {".docx", ".xlsx"} or output.suffix.lower() != source.suffix.lower():
        return None
    raw = _report_bytes(report)
    data = json.loads(raw)
    if (
        not isinstance(data, dict) or data.get("engine") not in {"forma-ooxml", "forma-corpus"}
        or not isinstance(data.get("source"), str) or _path(data["source"]) != _path(source)
        or not isinstance(data.get("output"), str) or _path(data["output"]) != _path(output)
        or data.get("model") != document["model"]
        or any(not isinstance(data.get(key), str) or not _HASH.fullmatch(data[key])
               for key in ("source_sha256", "output_sha256"))
    ):
        return None
    options = Options(**data["options"])
    options.validate()
    if (
        options.source_language != document["sourceLanguage"]
        or options.target_language != document["targetLanguage"]
        or type(options.review) is not bool or options.review != bool(document.get("reviewModel"))
        or file_digest(source) != data["source_sha256"]
        or file_digest(output) != data["output_sha256"]
    ):
        return None
    result, options = parse_report_data(data, report)
    if not result.segments or any(issue.severity == "error" for issue in refresh_segment_checks(result, options).issues):
        return None
    diagnostic = document.get("diagnosticReport")
    if diagnostic and ReviewStore().load(Path(diagnostic).with_suffix(".json"))["edits"]:
        # Persisted drafts are lazy-loaded by Review. Do not hide their only
        # queue link merely because another process completed a newer attempt.
        return None
    if hashlib.sha256(_report_bytes(report)).digest() != hashlib.sha256(raw).digest():
        return None
    warnings = record.get("warningCount", 0)
    return {
        "status": "done", "statusText": "Готово · перегляньте зауваження" if warnings else "Готово",
        "output": str(output), "report": str(report), "diagnosticReport": "", "error": "",
        "progress": 100, "runId": "", "warningCount": warnings,
        "elapsedSeconds": record.get("elapsedSeconds", 0), "segmentCount": len(result.segments),
        "tokens": f"{record.get('inputTokens', 0) + record.get('outputTokens', 0):,}",
    }


def restore_history_completions(backend):
    """One startup reconciliation; ordinary completed rows keep lazy loading."""
    documents = [dict(document) for document in backend._documents if document["status"] in {"error", "cancelled"}]
    if not documents or not backend._library or backend._closing:
        return
    # Credential loading can make busy true at startup. Wait for that existing
    # asynchronous operation rather than racing it or polling the event loop.
    if backend._settings_loading:
        def after_credentials():
            if backend._closing or not backend._settings_loading:
                backend.changed.disconnect(after_credentials)
                if not backend._closing:
                    restore_history_completions(backend)
        backend.changed.connect(after_credentials)
        return
    library, initial_history = backend._library, backend._history
    review_generation = backend._review_generation

    def read(emit):
        history = library.list_history()
        updates = []
        for document in documents:
            try:
                record = _candidate(document, history)
                patch = _validated_completion(document, record) if record else None
                if patch:
                    updates.append((document, record, patch))
            except (OSError, ValueError, TypeError, KeyError, RecursionError, FormaError):
                continue
        current_history = library.list_history()
        updates = [(document, patch) for document, record, patch in updates
                   if _candidate(document, current_history) == record]
        emit("session_completions", (current_history, updates, inspect_files(
            path for document, patch in updates
            for path in (document["path"], patch["output"], patch["report"], str(Path(patch["report"]).with_suffix(".html")))
        )))

    def receive(kind, payload):
        if (
            kind != "session_completions" or backend._closing or backend.busy
            or backend._review_editing or backend._review_saving or backend._review_edits
            or backend._review_generation != review_generation or backend._history is not initial_history
        ):
            return
        history, updates, files = payload
        changed = False
        for before, patch in updates:
            current = next((document for document in backend._documents if document["id"] == before["id"]), None)
            if current == before:
                current.update(patch)
                changed = True
        if changed:
            backend._history, backend._history_cache = history, None
            backend._file_status.update(files, notify=False)
            backend._persist_queue()
            backend.changed.emit()

    backend._launch(read, handler=receive)
