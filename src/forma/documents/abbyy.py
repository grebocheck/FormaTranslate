"""Use a separately installed ABBYY OCR Editor without assuming its license.

Ordinary FineReader is a manual handoff. Only an explicitly confirmed Extended
CLI license enables /out automation. ABBYY's output has no trusted page-level
manifest, so neither path fabricates Docling's coverage attestation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from pypdf import PdfReader

from forma.core.models import Cancelled, FormaError
from forma.documents.bundled_runtime import start_worker
from forma.documents.ooxml import OfficeDocument
from forma.persistence.files import atomic_bytes
from forma.providers.client import JobControl

LANGUAGES = {"en": "English", "uk": "Ukrainian", "pl": "Polish", "de": "German"}
MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
MAX_SOURCE_PAGES = 200
MAX_REPORT_BYTES = 8 * 1024 * 1024
_EXECUTABLE = "FineReaderOCR.exe"
_APP_PATH = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{_EXECUTABLE}"
_MISSING = "Не знайдено ABBYY FineReader OCR Editor. Укажіть FineReaderOCR.exe у налаштуваннях."
_LAUNCH_ERROR = "Не вдалося запустити ABBYY. Перевірте встановлення та шлях до FineReaderOCR.exe."
_RESULT_ERROR = "Перевірте DOCX з ABBYY: файл має бути збережений, непорожній та придатний для читання."


def _executable_at(value: str) -> str:
    path = Path(value).expanduser()
    if path.name.casefold() != _EXECUTABLE.casefold():
        return ""
    try:
        return str(path.resolve()) if path.is_file() else ""
    except OSError:
        return ""


def _registered_paths() -> list[str]:
    """Read the fixed Windows App Paths keys, never walk installed programs."""
    if sys.platform != "win32":
        return []
    import winreg

    values = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, _APP_PATH, 0, winreg.KEY_READ | view) as key:
                    value, kind = winreg.QueryValueEx(key, "")
                    if isinstance(value, str) and kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                        values.append(os.path.expandvars(value.strip().strip('"')))
            except OSError:
                continue
    return values


def find_abbyy(configured: str = "") -> str:
    """Return a candidate executable; its presence never certifies CLI capability.

    Call discovery from a background worker, rather than a QML property getter.
    A stale explicit path is an error, not permission to select another engine.
    """
    if configured:
        return _executable_at(configured)
    if sys.platform != "win32":
        return ""
    candidates = _registered_paths()
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.extend(str(Path(base) / f"ABBYY FineReader {version}" / _EXECUTABLE)
                              for version in (16, 15))
    for candidate in dict.fromkeys(candidates):
        found = _executable_at(candidate)
        if found:
            return found
    return ""


def _language(value: str) -> str:
    if value not in LANGUAGES:
        raise FormaError(
            "Для ABBYY виберіть мову оригіналу: англійську, українську, польську або німецьку."
        )
    return LANGUAGES[value]


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() not in {
        "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
    }}


def launch_abbyy_handoff(source: Path, source_language: str, configured: str = "") -> None:
    """Start the OCR Editor; the user opens the PDF, selects language and saves.

    No undocumented input-only conversion or /send MSWord dependency is assumed.
    The selected file remains in Forma's queue until a DOCX is imported explicitly.
    """
    _language(source_language)
    if Path(source).suffix.lower() != ".pdf" or not Path(source).is_file():
        raise FormaError("Для розпізнавання виберіть наявний PDF.")
    executable = find_abbyy(configured)
    if not executable:
        raise FormaError(_MISSING)
    try:
        process = start_worker(
            [executable], env=_environment(), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise FormaError(_LAUNCH_ERROR) from exc
    # The editor belongs to the user after launch. Reap the launcher without
    # tying its lifetime to an OCR cancel button or the Forma window.
    threading.Thread(target=process.wait, name="forma-abbyy-launcher", daemon=True).start()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_info(source: Path, language: str) -> tuple[str, int]:
    _language(language)
    try:
        if source.suffix.lower() != ".pdf" or not source.is_file():
            raise FormaError("Для розпізнавання виберіть наявний PDF.")
        if source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise FormaError("PDF завеликий для OCR: ліміт 200 МБ.")
        before = _digest(source)
        reader = PdfReader(source)
        if reader.is_encrypted and not reader.decrypt(""):
            raise FormaError("PDF захищений паролем. Створіть незахищену копію.")
        pages = len(reader.pages)
        if not 1 <= pages <= MAX_SOURCE_PAGES:
            raise FormaError("Для OCR підтримуються PDF від 1 до 200 сторінок.")
        if _digest(source) != before:
            raise FormaError("Вихідний PDF змінився під час підготовки DOCX. Повторіть операцію.")
        return before, pages
    except FormaError:
        raise
    except Exception as exc:
        raise FormaError("Не вдалося прочитати PDF для OCR.") from exc


def _recognized_bytes(document: Path) -> bytes:
    try:
        if document.suffix.lower() != ".docx" or document.is_symlink() or not document.is_file():
            raise ValueError("format")
        with document.open("rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
            raise ValueError("size")
        # Validate the exact bytes we will publish. A user may import a file
        # while Word/ABBYY still has it open, so never reopen it after validation.
        with tempfile.TemporaryDirectory(prefix="forma-abbyy-check-") as temporary:
            snapshot = Path(temporary) / "result.docx"
            snapshot.write_bytes(data)
            office = OfficeDocument(snapshot)
            if not office.segments or not any(segment.plain.strip() for segment in office.segments):
                raise ValueError("empty")
        return data
    except Exception as exc:
        raise FormaError(_RESULT_ERROR) from exc


def _publish(
    source: Path, document: Path, folder: Path, language: str, control: JobControl,
    source_hash: str, source_pages: int, workflow: str,
) -> Path:
    data = _recognized_bytes(document)
    control.check()
    if _digest(source) != source_hash:
        raise FormaError("Вихідний PDF змінився під час підготовки DOCX. Повторіть операцію.")
    metadata = {
        "schema_version": 1, "provider": "abbyy", "workflow": workflow,
        "coverage": "not_verified", "source_language": language,
        "original_pdf": str(source.resolve()), "source_sha256": source_hash,
        "source_page_count": source_pages, "output_sha256": hashlib.sha256(data).hexdigest(),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    count = 1
    while True:
        control.check()
        suffix = "" if count == 1 else f".{count}"
        output = folder / f"{source.stem}.abbyy{suffix}.docx"
        sidecar = output.with_suffix(".ocr.json")
        if output.exists() or sidecar.exists():
            count += 1
            continue
        try:
            atomic_bytes(sidecar, encoded, overwrite=False)
        except FileExistsError:
            count += 1
            continue
        try:
            control.check()
            atomic_bytes(output, data, overwrite=False)
        except FileExistsError:
            sidecar.unlink(missing_ok=True)
            count += 1
            continue
        except BaseException:
            sidecar.unlink(missing_ok=True)
            raise
        return output


def import_abbyy_result(
    source: Path, document: Path, folder: Path, source_language: str, control: JobControl,
    *, expected_source_sha256: str | None = None,
) -> Path:
    """Copy a manually chosen source/result pair; UI obtains user review first."""
    control.check()
    source = Path(source)
    source_hash, pages = _source_info(source, source_language)
    if expected_source_sha256 is not None and source_hash != expected_source_sha256:
        raise FormaError("PDF змінився після відкриття ABBYY. Повторіть розпізнавання оновленого документа.")
    return _publish(source, Path(document), Path(folder), source_language, control,
                    source_hash, pages, "manual")


def _stop_owned_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    # FineReader may forward work to an already open editor. Never taskkill by
    # executable name or kill its process tree: it may contain unrelated work.
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def convert_with_abbyy(
    source: Path, folder: Path, source_language: str, configured: str,
    control: JobControl, progress, *, extended_cli_confirmed: bool = False,
    timeout_seconds: float = 1800,
) -> Path:
    """Automate the documented /out protocol only after explicit license setup."""
    if extended_cli_confirmed is not True:
        raise FormaError(
            "Автоматичне збереження через ABBYY потребує Extended CLI-enabled license. "
            "Підтвердіть її в налаштуваннях або скористайтесь ручним режимом."
        )
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("ABBYY timeout must be finite and positive")
    control.check()
    source, folder = Path(source), Path(folder)
    source_hash, pages = _source_info(source, source_language)
    executable = find_abbyy(configured)
    if not executable:
        raise FormaError(_MISSING)
    with tempfile.TemporaryDirectory(prefix="forma-abbyy-", ignore_cleanup_errors=True) as temporary:
        document = Path(temporary) / "recognized.docx"
        report = Path(temporary) / "conversion-report.txt"
        progress(1, "ABBYY · Розпізнаємо PDF")
        control.check()
        try:
            process = start_worker(
                [executable, str(source.resolve()), "/lang", _language(source_language),
                 "/out", str(document), "/report", str(report)],
                env=_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise FormaError(_LAUNCH_ERROR) from exc
        started = time.monotonic()
        try:
            while process.poll() is None:
                if control.cancelled.is_set():
                    raise Cancelled("Розпізнавання ABBYY зупинено. Оригінал залишився без змін.")
                if time.monotonic() - started > timeout_seconds:
                    raise FormaError(
                        "Перевищено час очікування ABBYY. Перевірте вікно програми, ліцензію та повторіть конвертацію."
                    )
                if report.exists() and report.stat().st_size > MAX_REPORT_BYTES:
                    raise FormaError("ABBYY створив забагато діагностики. Перевірте програму та повторіть конвертацію.")
                control.cancelled.wait(0.1)
            control.check()
            if process.returncode or not document.is_file():
                raise FormaError(
                    "ABBYY завершився без готового DOCX. Перевірте Extended CLI-enabled license або збережіть DOCX вручну."
                )
            progress(95, "ABBYY · Перевіряємо DOCX")
            output = _publish(source, document, folder, source_language, control,
                              source_hash, pages, "extended_cli")
        finally:
            _stop_owned_process(process)
    progress(100, "DOCX з ABBYY готовий · звірте всі сторінки з PDF")
    return output
