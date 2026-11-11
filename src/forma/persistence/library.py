"""Durable queue and translation history containing document metadata only."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from forma.core.models import INFORMATION_CODES, FormaError
from forma.settings.config import data_directory

SCHEMA_VERSION = 1
SUPPORTED_FORMATS = {"DOCX", "XLSX", "PDF"}
QUEUE_STATUSES = {
    "loading",
    "ready",
    "queued",
    "translating",
    "paused",
    "done",
    "error",
    "cancelled",
    "interrupted",
}
TERMINAL_STATUSES = {"done", "error", "cancelled", "interrupted"}
QUEUE_FIELDS = {
    "id",
    "path",
    "name",
    "format",
    "size",
    "status",
    "statusText",
    "progress",
    "segmentCount",
    "error",
    "output",
    "report",
    "diagnosticReport",
    "tokens",
    "warningCount",
    "startedAt",
    "elapsedSeconds",
    "runId",
    "sourceLanguage",
    "targetLanguage",
    "model",
    "reviewModel",
    "pdfRoute",
    "pdfMode",
}
HISTORY_FIELDS = {
    "id",
    "name",
    "format",
    "source",
    "output",
    "report",
    "sourceLanguage",
    "targetLanguage",
    "model",
    "reviewModel",
    "status",
    "startedAt",
    "finishedAt",
    "elapsedSeconds",
    "inputTokens",
    "outputTokens",
    "warningCount",
    "error",
}
INTEGER_FIELDS = {
    "progress",
    "segmentCount",
    "warningCount",
    "startedAt",
    "finishedAt",
    "inputTokens",
    "outputTokens",
}
PATH_FIELDS = {"path", "source", "output", "report", "diagnosticReport"}
_URL = re.compile(r"\b(?:https?|ftp)://\S+", re.IGNORECASE)
_FAILURE = "Не вдалося прочитати або зберегти бібліотеку перекладів. Перевірте доступ до папки даних та цілісність бази; наявні дані не перезаписано."


def _clean(record: dict, *, history: bool) -> dict:
    if not isinstance(record, dict):
        raise FormaError("Некоректний запис бібліотеки перекладів.")
    allowed = HISTORY_FIELDS if history else QUEUE_FIELDS
    clean = {}
    for key in allowed & record.keys():
        value = record[key]
        if key in INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                raise FormaError("Некоректне числове поле бібліотеки перекладів.")
            if key == "progress" and value > 100:
                raise FormaError("Некоректний відсоток виконання завдання.")
        elif key == "elapsedSeconds":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 10**10
            ):
                raise FormaError("Некоректний час виконання завдання.")
        else:
            if not isinstance(value, str) or "\x00" in value:
                raise FormaError("Некоректне текстове поле бібліотеки перекладів.")
            limit = 8192 if key in PATH_FIELDS else 4096 if key in {"error", "statusText"} else 1024
            if len(value) > limit:
                raise FormaError("Завелике текстове поле бібліотеки перекладів.")
            if key in {"error", "statusText"}:
                value = _URL.sub("[адресу приховано]", value)
            elif _URL.search(value):
                raise FormaError("Бібліотека зберігає лише локальні шляхи та метадані документів.")
        clean[key] = value
    if not clean.get("id") or len(clean["id"]) > 128:
        raise FormaError("Відсутній або некоректний ідентифікатор запису бібліотеки.")
    if not history and not clean.get("path"):
        raise FormaError("У завданні відсутній шлях до документа.")
    if clean.get("status") not in (TERMINAL_STATUSES if history else QUEUE_STATUSES):
        raise FormaError("Некоректний стан запису бібліотеки перекладів.")
    if clean.get("format", "").upper() not in SUPPORTED_FORMATS:
        raise FormaError("Непідтримуваний формат документа в бібліотеці.")
    clean["format"] = clean["format"].upper()
    if "pdfRoute" in clean and (
        clean["pdfRoute"] not in {"", "ocr", "direct"}
        or clean["pdfRoute"] and clean["format"] != "PDF"
    ):
        raise FormaError("Некоректний запис бібліотеки перекладів.")
    if "pdfMode" in clean and (
        clean["pdfMode"] not in {"auto", "ocr", "direct"} or clean["format"] != "PDF"
    ):
        raise FormaError("Некоректний запис бібліотеки перекладів.")
    return clean


def _report_key(value: str) -> str:
    # Absolute normalization also deduplicates relative reports from earlier app versions.
    return os.path.normcase(os.path.abspath(os.path.expanduser(value))) if value else ""


class TranslationLibrary:
    def __init__(self, path: Path | None = None):
        try:
            self.path = Path(path) if path is not None else data_directory() / "library.sqlite3"
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            with self._connection(check_version=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if version == 0 and not tables:
                    connection.execute(
                        "CREATE TABLE queue (position INTEGER PRIMARY KEY, document_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE history (id TEXT PRIMARY KEY, report TEXT NOT NULL DEFAULT '', finished_at INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE UNIQUE INDEX history_report ON history(report) WHERE report <> ''"
                    )
                    connection.execute("CREATE INDEX history_finished ON history(finished_at DESC)")
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION or tables != {"queue", "history"}:
                    raise FormaError(
                        "Невідома або пошкоджена версія бібліотеки перекладів. Базу не змінено."
                    )
                # Verify expected columns even for an externally modified v1 database.
                connection.execute("SELECT position, document_id, payload FROM queue LIMIT 0")
                connection.execute("SELECT id, report, finished_at, payload FROM history LIMIT 0")
        except OSError:
            raise FormaError(_FAILURE) from None

    @contextmanager
    def _connection(self, *, check_version=True):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            if (
                check_version
                and connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise FormaError("Змінилася версія бібліотеки перекладів. Базу не змінено.")
            with connection:
                yield connection
        except (sqlite3.Error, OSError):
            raise FormaError(_FAILURE) from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _decode(payload: str, *, history: bool) -> dict:
        try:
            return _clean(json.loads(payload), history=history)
        except (ValueError, TypeError, FormaError):
            raise FormaError(
                "Пошкоджений запис бібліотеки перекладів. Наявні дані не змінено."
            ) from None

    def load_queue(self) -> list[dict]:
        with self._connection() as connection:
            return [
                self._decode(row[0], history=False)
                for row in connection.execute("SELECT payload FROM queue ORDER BY position")
            ]

    def save_queue(self, documents: list[dict]) -> None:
        if not isinstance(documents, list):
            raise FormaError("Некоректна черга перекладу.")
        records = [_clean(record, history=False) for record in documents]
        if len({record["id"] for record in records}) != len(records):
            raise FormaError("У черзі повторюються ідентифікатори документів.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM queue")
            connection.executemany(
                "INSERT INTO queue(position, document_id, payload) VALUES (?, ?, ?)",
                [
                    (index, record["id"], json.dumps(record, ensure_ascii=False, allow_nan=False))
                    for index, record in enumerate(records)
                ],
            )

    def add_history(self, record: dict) -> None:
        clean = _clean(record, history=True)
        report = _report_key(clean.get("report", ""))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = (
                connection.execute("SELECT id FROM history WHERE report=?", (report,)).fetchone()
                if report
                else None
            )
            if existing:
                clean["id"] = existing[0]
            connection.execute(
                "INSERT INTO history(id, report, finished_at, payload) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET report=excluded.report, finished_at=excluded.finished_at, payload=excluded.payload",
                (
                    clean["id"],
                    report,
                    clean.get("finishedAt", 0),
                    json.dumps(clean, ensure_ascii=False, allow_nan=False),
                ),
            )

    def list_history(self) -> list[dict]:
        with self._connection() as connection:
            return [
                self._decode(row[0], history=True)
                for row in connection.execute(
                    "SELECT payload FROM history ORDER BY finished_at DESC, rowid DESC LIMIT 1000"
                )
            ]

    def remove_history(self, record_id: str) -> None:
        if not isinstance(record_id, str):
            raise FormaError("Некоректний ідентифікатор запису бібліотеки.")
        with self._connection() as connection:
            connection.execute("DELETE FROM history WHERE id=?", (record_id,))

    def import_report(self, report_path: Path) -> bool:
        path = Path(report_path).expanduser().absolute()
        if not path.name.lower().endswith(".forma.json"):
            return False
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                return False
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("options"), dict):
                return False
            adjacent = Path(str(path)[: -len(".forma.json")])
            output_value = data.get("output")
            if not isinstance(output_value, str) or _URL.search(output_value):
                return False
            output = adjacent if adjacent.is_file() else Path(output_value)
            if not output.is_absolute():
                output = path.parent / output
            file_format = output.suffix[1:].upper()
            if file_format not in SUPPORTED_FORMATS:
                return False
            source = data.get("source")
            if not isinstance(source, str) or not source:
                return False
            options = data["options"]
            record = {
                "id": "import-" + uuid.uuid5(uuid.NAMESPACE_URL, _report_key(str(path))).hex,
                "name": output.name,
                "format": file_format,
                "source": source,
                "output": str(output),
                "report": str(path),
                "sourceLanguage": options.get("source_language", "auto"),
                "targetLanguage": options.get("target_language", ""),
                "model": data.get("model", ""),
                "reviewModel": data.get("review_model", ""),
                "status": "done",
                "finishedAt": int(path.stat().st_mtime * 1000),
                "elapsedSeconds": data.get("elapsed_seconds", 0),
                "inputTokens": data.get("input_tokens", 0),
                "outputTokens": data.get("output_tokens", 0),
                "warningCount": sum(
                    isinstance(issue, dict)
                    and (
                        issue.get("severity") == "error"
                        or (
                            issue.get("severity") != "info"
                            and issue.get("code") not in INFORMATION_CODES
                        )
                    )
                    for issue in data.get("issues", [])
                )
                if isinstance(data.get("issues", []), list)
                else 0,
                "error": "",
            }
            _clean(record, history=True)
        except (OSError, UnicodeError, ValueError, TypeError, FormaError):
            return False
        self.add_history(record)
        return True
