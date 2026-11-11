from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from pypdf import PdfReader

from forma.core.models import Cancelled, FormaError, Issue, Options, TranslationResult
from forma.documents.bundled_runtime import find_bundled, start_worker, worker_environment
from forma.documents.pdf_api import PDFAPI
from forma.documents.pdf_progress import PDFProgress
from forma.persistence.files import atomic_bytes, atomic_json, available_output
from forma.providers.client import JobControl
from forma.settings.config import Settings, data_directory
from forma.translation.instructions import translation_prompt


def find_babeldoc(configured: str = "") -> str:
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else (shutil.which(configured) or "")
    bundled = find_bundled("babeldoc")
    if bundled:
        return bundled
    found = shutil.which("babeldoc")
    if found:
        return found
    for folder in (Path.home() / ".local/bin", Path.home() / ".cargo/bin"):
        for executable in ("babeldoc", "babeldoc.exe"):
            path = folder / executable
            if path.is_file():
                return str(path)
    return ""


def inspect_pdf(path: Path) -> dict:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise FormaError("PDF захищений паролем. Створіть незахищену копію.")
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            letters = sum(character.isalpha() for character in text)
            pages.append(
                {
                    "width": float(page.mediabox.width),
                    "height": float(page.mediabox.height),
                    "text": text,
                    # Only inspect images on sparse pages: an illustration alone
                    # is not evidence of a scan.
                    "image_without_text": letters < 12 and bool(len(page.images)),
                }
            )
        return {
            "pages": pages,
            "scanned": not any(any(c.isalpha() for c in p["text"]) for p in pages),
        }
    except FormaError:
        raise
    except Exception as exc:
        raise FormaError(
            "Не вдалося прочитати PDF. Перевірте, чи файл відкривається у PDF-переглядачі."
        ) from exc


def needs_ocr(info: dict) -> bool:
    """Use the same scan threshold for queue planning and paid PDF dispatch."""
    pages = info["pages"]
    image_pages = sum(bool(page["image_without_text"]) for page in pages)
    return bool(info["scanned"] or (image_pages >= 2 and image_pages > len(pages) / 2))


def _check_pdf_language(before, after, options):
    """Reject a wholly wrong script, while allowing names, references and mixed pages."""
    latin_languages = {
        "en",
        "de",
        "fr",
        "es",
        "it",
        "pt",
        "pl",
        "cs",
        "sk",
        "nl",
        "sv",
        "da",
        "fi",
        "ro",
        "tr",
    }
    cyrillic_languages = {"uk", "ru", "bg", "be", "mk"}
    source = options.source_language.lower().split("-")[0]
    target = options.target_language.lower().split("-")[0]
    latin, cyrillic = r"[A-Za-zÀ-ž]", r"[А-Яа-яІіЇїЄєҐґЁёЀ-ӿ]"
    if source in latin_languages and target in cyrillic_languages:
        source_pattern, target_pattern = latin, cyrillic
    elif source in cyrillic_languages and target in latin_languages:
        source_pattern, target_pattern = cyrillic, latin
    else:
        return
    original = "\n".join(page["text"] for page in before["pages"])
    translated = "\n".join(page["text"] for page in after["pages"])
    source_letters = len(re.findall(source_pattern, original))
    original_target_letters = len(re.findall(target_pattern, original))
    remaining = len(re.findall(source_pattern, translated))
    target_letters = len(re.findall(target_pattern, translated))
    if (
        source_letters >= 200
        and source_letters > original_target_letters * 4
        and remaining >= 200
        and target_letters < remaining * 0.01
    ):
        raise FormaError(
            "Експорт PDF зупинено: документ майже повністю залишився мовою оригіналу. "
            "Перевірте модель і мову перекладу. Неперекладений файл не збережено."
        )


def _run_babeldoc(arguments, logfile, settings, control, progress, workers, api=None):
    # Reserve the entire external engine's worker allowance atomically. Other
    # documents share the same JobControl and cannot exceed the queue's budget.
    progress(5, "PDF · Очікуємо вільні запити для обробки документа")
    with control.request_slots(workers), logfile.open("wb", buffering=0) as log:
        # Rich suppresses live frames when redirected to a regular file. These
        # documented overrides affect only this subprocess; logs stay private.
        environment = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "TTY_COMPATIBLE": "1",
            "TTY_INTERACTIVE": "1",
            "COLUMNS": "240",
            "LINES": "80",
            "TERM": "xterm",
            "NO_COLOR": "1",
        }
        monitor = PDFProgress(progress)
        try:
            process = start_worker(
                arguments,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=worker_environment(arguments[0], environment),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise FormaError("Не вдалося запустити PDF-рушій. Перевірте шлях до BabelDOC.") from exc
        try:
            with logfile.open("rb") as events:
                while process.poll() is None:
                    if api:
                        api.check_error()
                    monitor.feed(events.read(65536))
                    monitor.tick()
                    if control.cancelled.wait(0.2):
                        raise Cancelled("Переклад PDF зупинено. Внутрішній кеш BabelDOC збережено.")
                    # The bridge pauses new upstream requests; let in-flight
                    # responses finish while keeping process cancellation live.
                while chunk := events.read(65536):
                    monitor.feed(chunk)
                monitor.feed(b"", final=True)
            if api:
                api.check_error()
            if process.returncode:
                with logfile.open("rb") as diagnostics:
                    diagnostics.seek(max(0, logfile.stat().st_size - 30000))
                    diagnostic = diagnostics.read().decode("utf-8", errors="replace")
                if settings.api_key:
                    diagnostic = diagnostic.replace(settings.api_key, "[REDACTED]")
                diagnostic_path = data_directory() / "logs" / f"pdf-{uuid.uuid4().hex[:8]}.log"
                atomic_bytes(diagnostic_path, diagnostic.encode("utf-8"))
                raise FormaError(
                    "BabelDOC завершився з помилкою. Перевірте модель, ключ, адресу API "
                    f"та ресурси рушія (babeldoc --warmup). Діагностика: {diagnostic_path}"
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def translate_pdf(
    source: Path, folder: Path, options: Options, settings: Settings, control: JobControl, progress
) -> TranslationResult:
    options.validate()
    executable = find_babeldoc(settings.babeldoc_path)
    if not executable:
        raise FormaError(
            "Для PDF потрібен BabelDOC. Встановіть його командою «uv tool install --python 3.12 BabelDOC==0.7.1» "
            "і вкажіть шлях у налаштуваннях або FORMA_BABELDOC_PATH у .env."
        )
    if options.source_language == "auto":
        raise FormaError("Для PDF виберіть мову оригіналу: BabelDOC потребує явної мови.")
    if settings.api_mode not in ("chat", "gemini", "anthropic"):
        raise FormaError("BabelDOC використовує Chat Completions. Виберіть режим chat для PDF.")
    start = time.monotonic()
    progress(2, "Перевіряємо PDF")
    before = inspect_pdf(source)
    issues = [
        Issue(
            "external_engine",
            "PDF перекладено зовнішнім рушієм BabelDOC. Редакторська перевірка Forma для Office до PDF не застосовується.",
            severity="info",
        ),
        Issue(
            "pdf_visual_review",
            "Перевірте верстку PDF візуально: автоматично перевірено кількість, розміри сторінок "
            "та збереження текстового шару. Це не перевірка повноти чи точності перекладу.",
            severity="info",
        ),
    ]
    if before["scanned"]:
        raise FormaError(
            "У PDF немає текстового шару для перекладу. Для скану спочатку виконайте OCR; "
            "порожні або суто графічні документи перекладати немає з чого."
        )
    image_pages = [
        str(index) for index, page in enumerate(before["pages"], 1) if page["image_without_text"]
    ]
    if needs_ocr(before):
        raise FormaError(
            "Більшість сторінок PDF — зображення без текстового шару. "
            "Спочатку скористайтеся «Скан PDF → DOCX», перевірте розпізнаний текст і перекладіть DOCX."
        )
    if image_pages:
        issues.append(
            Issue(
                "pdf_image_text",
                "Сторінки зі зображеннями та майже без текстового шару: "
                + ", ".join(image_pages)
                + ". Написи всередині зображень можуть залишитися мовою оригіналу. "
                "Якщо це скановані сторінки з текстом, потрібен OCR перед перекладом.",
            )
        )
    with tempfile.TemporaryDirectory(prefix="forma-pdf-") as temp, ExitStack() as resources:
        root = Path(temp)
        out = root / "output"
        out.mkdir()
        workers = min(
            control.max_parallel_requests,
            max(1, settings.max_parallel_requests // settings.parallel_documents),
        )
        api = resources.enter_context(
            PDFAPI(
                settings.with_overrides(translation_instructions=options.instructions),
                workers,
                control=control,
            )
        )
        # Secrets go in a private temporary file, never into process arguments.
        config = {
            "openai": True,
            "openai-model": api.cache_model,
            "openai-base-url": api.base_url,
            "openai-api-key": api.token,
            "lang-in": options.source_language,
            "lang-out": options.target_language,
            "output": str(out),
            "no-dual": True,
            "qps": workers,
            "pool-max-workers": workers,
            "term-pool-max-workers": workers,
            "report-interval": 0.5,
            "translate-table-text": True,
            "no-send-temperature": True,
            "custom-system-prompt": translation_prompt(
                "You are a precise professional translator. Translate all human-readable text into "
                + (
                    {"uk": "Ukrainian", "en": "English"}.get(
                        options.target_language, options.target_language
                    )
                )
                + f" ({options.target_language}). Preserve all numbers and identifiers. Domain: "
                + options.domain,
                options.instructions,
            ),
        }
        if not options.auto_glossary:
            config["no-auto-extract-glossary"] = True
        if options.glossary:
            import csv

            glossary = root / "glossary.csv"
            with glossary.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["source", "target"])
                writer.writerows(options.glossary.items())
            config["glossary-files"] = str(glossary)
        config_path = root / "config.toml"
        config_path.write_text(
            "[babeldoc]\n"
            + "\n".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in config.items()
            ),
            encoding="utf-8",
        )
        os.chmod(config_path, 0o600)
        logfile = root / "engine.log"
        control.check()
        _run_babeldoc(
            [executable, "--config", str(config_path), "--files", str(source.resolve())],
            logfile,
            settings.with_overrides(api_key=api.token),
            control,
            progress,
            workers,
            api,
        )
        control.check()
        candidates = sorted(out.glob("*.pdf"))
        mono = [p for p in candidates if "mono" in p.name.lower()]
        if len(mono) == 1:
            candidate = mono[0]
        elif len(candidates) == 1:
            candidate = candidates[0]
        else:
            raise FormaError("BabelDOC не створив однозначний файл результату.")
        progress(95, "Перевіряємо сторінки результату")
        after = inspect_pdf(candidate)
        if len(before["pages"]) != len(after["pages"]):
            raise FormaError("Експорт PDF зупинено: змінилася кількість сторінок.")
        unchanged_pages = []
        for number, (a, b) in enumerate(zip(before["pages"], after["pages"], strict=True), 1):
            if abs(a["width"] - b["width"]) > 1 or abs(a["height"] - b["height"]) > 1:
                raise FormaError("Експорт PDF зупинено: змінилися розміри сторінок.")
            original = re.sub(r"\s+", "", a["text"])
            translated = re.sub(r"\s+", "", b["text"])
            # Blank/illustrated source pages may legitimately remain without text.
            # A page that used to contain text must not silently lose it.
            if original and not translated:
                raise FormaError(f"Експорт PDF зупинено: на сторінці {number} зник текстовий шар.")
            if len(original) >= 200 and len(translated) < len(original) * 0.1:
                raise FormaError(
                    f"Експорт PDF зупинено: на сторінці {number} залишилося менше 10% "
                    "тексту. Потрібна перевірка результату рушія."
                )
            if (
                options.source_language.lower() != options.target_language.lower()
                and sum(c.isalpha() for c in original) >= 30
                and original == translated
            ):
                unchanged_pages.append(str(number))
        if unchanged_pages:
            issues.append(
                Issue(
                    "pdf_unchanged_pages",
                    "Текст сторінок залишився без змін: "
                    + ", ".join(unchanged_pages)
                    + ". Перевірте, чи перекладено ці сторінки; текст міг уже бути цільовою мовою.",
                )
            )
        _check_pdf_language(before, after, options)
        control.check()
        folder.mkdir(parents=True, exist_ok=True)
        output = available_output(source, options.target_language, folder)
        atomic_bytes(output, candidate.read_bytes(), overwrite=False)
        statistics = api.summary()
        statistics_path = output.with_suffix(output.suffix + ".api.json")
        atomic_json(statistics_path, statistics)
    result = TranslationResult(
        source,
        output,
        [],
        {},
        issues,
        input_tokens=statistics["input_tokens"],
        output_tokens=statistics["output_tokens"],
        engine="babeldoc",
        elapsed_seconds=time.monotonic() - start,
        usage_note="API-статистика поточного запуску: "
        + statistics_path.name
        + ". Повторно використані переклади з кешу не витрачають токени; "
        "перервані запити можуть бути відсутні в обліку.",
    )
    from forma.translation.reports import write_translation_report

    write_translation_report(result, options, settings.model)
    progress(100, "PDF готовий · перевірте верстку")
    return result
