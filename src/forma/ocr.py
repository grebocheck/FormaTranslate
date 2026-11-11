"""Local scan preparation; the optional OCR runtime stays outside the desktop build."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from pypdf import PdfReader

from forma.models import Cancelled, FormaError
from forma.ooxml import OfficeDocument
from forma.provider import JobControl
from forma.storage import atomic_bytes

MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_PAGES = 200
MAX_MANIFEST_BYTES = 50 * 1024 * 1024
OCR_LANGUAGES = frozenset({"en", "uk", "pl", "de"})

_STAGES = {
    "loading": "OCR · Завантажуємо локальні моделі",
    "rendering": "OCR · Готуємо сторінки",
    "recognizing": "OCR · Розпізнаємо текст і таблиці",
    "exporting": "OCR · Відновлюємо DOCX",
    "validating": "OCR · Перевіряємо повноту документа",
}
_ERRORS = {
    "dependencies_missing": "Не встановлено залежності OCR. Перевстановіть окремий рушій forma-ocr за інструкцією docs/PDF.md.",
    "empty_page": "OCR не розпізнав текст на сторінці. Перевірте якість скану та мову оригіналу.",
    "unsupported_language": "Для OCR виберіть мову оригіналу: англійську, українську, польську або німецьку.",
}


def find_scan_converter(configured: str = "") -> str:
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else (shutil.which(configured) or "")
    found = shutil.which("forma-ocr")
    if found:
        return found
    for folder in (Path.home() / ".local/bin", Path.home() / ".cargo/bin"):
        for name in ("forma-ocr", "forma-ocr.exe"):
            path = folder / name
            if path.is_file():
                return str(path)
    return ""


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # The local inference runtime may have worker children, so kill its tree.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except OSError:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def _run_converter(arguments: list[str], logfile: Path, control: JobControl, progress) -> None:
    # No provider credentials are needed for local inference or public model downloads.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"}
    }
    environment.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    failure = ""
    with logfile.open("wb", buffering=0) as log:
        try:
            process = subprocess.Popen(
                arguments,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise FormaError("Не вдалося запустити OCR. Перевірте шлях до forma-ocr.") from exc
        pending = b""
        last_progress = 0
        last_event = time.monotonic()
        try:
            with logfile.open("rb") as events:
                while True:
                    # OCR has a dedicated Cancel action. It does not consume API slots.
                    if control.cancelled.is_set():
                        raise Cancelled("Розпізнавання PDF зупинено. Вихідний файл не змінено.")
                    if time.monotonic() - last_event > 1800:
                        raise FormaError("OCR не повідомляє про прогрес понад 30 хвилин. Перевірте рушій і повторіть розпізнавання.")
                    if logfile.stat().st_size > 32 * 1024 * 1024:
                        raise FormaError("OCR створив забагато діагностики. Перевірте рушій і повторіть розпізнавання.")
                    chunk = events.read(65536)
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if len(line) > 4096:
                            continue
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError, RecursionError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "error" and isinstance(event.get("code"), str):
                            failure = _ERRORS.get(event["code"], "")
                        value, stage = event.get("value"), event.get("stage")
                        if (
                            event.get("type") == "progress"
                            and type(value) is int
                            and isinstance(stage, str)
                            and stage in _STAGES
                        ):
                            last_event = time.monotonic()
                            last_progress = max(last_progress, min(94, max(0, value)))
                            progress(last_progress, _STAGES[stage])
                    # Bound partial lines from third-party diagnostic output.
                    if len(pending) > 4096:
                        pending = b""
                    if process.poll() is not None and not chunk:
                        break
                    if not chunk:
                        control.cancelled.wait(0.1)
            if process.returncode:
                raise FormaError(
                    failure
                    or "OCR завершився з помилкою. Перевірте встановлення forma-ocr, доступ до локальних моделей і якість PDF."
                )
        finally:
            _stop_process(process)


def _validated_manifest(
    path: Path, document: Path, source_hash: str, language: str, page_count: int
) -> dict:
    try:
        if path.is_symlink() or document.is_symlink():
            raise ValueError("symlink")
        if path.stat().st_size > MAX_MANIFEST_BYTES or document.stat().st_size > MAX_PDF_BYTES:
            raise ValueError("size")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 1
            or manifest.get("source_sha256") != source_hash
            or manifest.get("output_sha256") != _digest(document)
            or manifest.get("source_language") != language
            or type(manifest.get("page_count")) is not int
            or manifest.get("page_count") != page_count
        ):
            raise ValueError("identity")
        pages = manifest.get("pages")
        if not isinstance(pages, list) or len(pages) != page_count:
            raise ValueError("coverage")
        for number, page in enumerate(pages, 1):
            if (
                not isinstance(page, dict)
                or type(page.get("index")) is not int
                or page.get("index") != number
                or type(page.get("text_chars")) is not int
                or page["text_chars"] < 0
                or page.get("status") not in ("complete", "blank")
                or (page["text_chars"] == 0 and page.get("status") != "blank")
                or (page["text_chars"] > 0 and page.get("status") != "complete")
                or type(page.get("tables")) is not int
                or page["tables"] < 0
                or not isinstance(page.get("warnings"), list)
                or not all(isinstance(warning, str) for warning in page["warnings"])
            ):
                raise ValueError("page")
        office = OfficeDocument(document)
        if not office.segments or not any(page["text_chars"] for page in pages):
            raise ValueError("empty document")
        return manifest
    except (OSError, ValueError, TypeError, KeyError, RecursionError, FormaError) as exc:
        raise FormaError(
            "Експорт OCR зупинено: не підтверджено цілісність DOCX або обробку всіх сторінок."
        ) from exc


def _publish(document: Path, manifest: dict, source: Path, folder: Path, control: JobControl) -> Path:
    manifest = {**manifest, "original_pdf": str(source.resolve())}
    metadata = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    data = document.read_bytes()
    count = 1
    while True:
        control.check()
        suffix = "" if count == 1 else f".{count}"
        output = folder / f"{source.stem}.ocr{suffix}.docx"
        sidecar = output.with_suffix(".ocr.json")
        if output.exists() or sidecar.exists():
            count += 1
            continue
        try:
            atomic_bytes(sidecar, metadata, overwrite=False)
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


def convert_scan_pdf(
    source: Path,
    folder: Path,
    source_language: str,
    configured: str,
    control: JobControl,
    progress,
) -> Path:
    """Prepare an explicit, reviewable OCR source; never start translation requests."""
    control.check()
    source, folder = Path(source), Path(folder)
    if source.suffix.lower() != ".pdf":
        raise FormaError("Для розпізнавання виберіть PDF.")
    if source_language not in OCR_LANGUAGES:
        raise FormaError(_ERRORS["unsupported_language"])
    executable = find_scan_converter(configured)
    if not executable:
        raise FormaError(
            "Для сканів потрібен окремий локальний рушій forma-ocr. Встановіть його за інструкцією docs/PDF.md і вкажіть шлях у налаштуваннях."
        )
    try:
        if source.stat().st_size > MAX_PDF_BYTES:
            raise FormaError("PDF завеликий для OCR: ліміт 200 МБ.")
        reader = PdfReader(source)
        if reader.is_encrypted and not reader.decrypt(""):
            raise FormaError("PDF захищений паролем. Створіть незахищену копію.")
        page_count = len(reader.pages)
        if not 1 <= page_count <= MAX_PAGES:
            raise FormaError("Для OCR підтримуються PDF від 1 до 200 сторінок.")
        source_hash = _digest(source)
    except FormaError:
        raise
    except Exception as exc:
        raise FormaError("Не вдалося прочитати PDF для OCR.") from exc
    progress(1, _STAGES["loading"])
    with tempfile.TemporaryDirectory(prefix="forma-ocr-") as temp:
        root = Path(temp)
        document = root / "recognized.docx"
        _run_converter(
            [
                executable,
                "--input", str(source.resolve()),
                "--output", str(document),
                "--source-language", source_language,
            ],
            root / "engine.log",
            control,
            progress,
        )
        control.check()
        progress(95, _STAGES["validating"])
        manifest = _validated_manifest(
            document.with_suffix(".ocr.json"), document, source_hash, source_language, page_count
        )
        # Detect replacement/editing of the input while the separate runtime was working.
        if _digest(source) != source_hash:
            raise FormaError("Вихідний PDF змінився під час OCR. Повторіть розпізнавання.")
        control.check()
        folder.mkdir(parents=True, exist_ok=True)
        output = _publish(document, manifest, source, folder, control)
    progress(100, "OCR готовий · перевірте DOCX перед перекладом")
    return output
