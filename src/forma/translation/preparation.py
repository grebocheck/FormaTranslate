"""Prepare scanned PDFs locally before handing their DOCX to the Office engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from forma.core.models import FormaError, Issue
from forma.documents.abbyy import convert_with_abbyy, find_abbyy
from forma.documents.ocr import (
    MAX_MANIFEST_BYTES,
    _validated_manifest,
    convert_scan_pdf,
    find_scan_converter,
)
from forma.documents.ooxml import OfficeDocument


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def effective_scan_provider(settings) -> str:
    """Automatic mode favors unattended OCR; explicit choices stay authoritative."""
    provider = settings.scan_provider
    if provider not in {"auto", "forma", "abbyy", "abbyy_extended"}:
        raise FormaError("Оберіть спосіб розпізнавання PDF у налаштуваннях OCR.")
    if provider == "abbyy_extended" and settings.abbyy_extended_cli_confirmed is not True:
        raise FormaError(
            "Автоматичне збереження DOCX потребує окремої ліцензії ABBYY Extended CLI. "
            "Використайте звичайний режим ABBYY або підтвердьте її наявність."
        )
    if provider != "auto":
        return provider
    if settings.scan_converter_path:
        return "forma"
    if find_scan_converter(settings.scan_converter_path):
        return "forma"
    return "abbyy" if find_abbyy(settings.abbyy_path) else "forma"


def _review_issues(manifest: dict) -> list[Issue]:
    """Only known findings are displayed; sidecar text is never a UI message."""
    issues = [Issue(
        "ocr_source_review",
        "Документ перекладено після OCR. Звірте розпізнаний текст, числа й таблиці з оригінальним PDF.",
    )]
    if manifest.get("provider") == "abbyy":
        issues.append(Issue(
            "ocr_coverage_not_verified",
            "ABBYY не підтверджує автоматично повноту сторінок. Звірте весь DOCX з оригінальним PDF.",
        ))
        return issues
    pages = manifest.get("pages", [])
    codes = {code for page in pages for code in page.get("warnings", []) if isinstance(code, str)}
    if "table_structure_unresolved" in codes:
        issues.append(Issue(
            "ocr_table_structure",
            "Частину таблиць збережено зображеннями та текстом, бо структуру не відновлено. Перевірте їх у DOCX.",
        ))
    if "unassigned_ocr_lines_recovered" in codes or any(
        type(page.get("recovered_lines")) is int and page["recovered_lines"] > 0 for page in pages
    ):
        issues.append(Issue(
            "ocr_recovered_lines",
            "Відновлені поза структурою рядки виділено жовтим у DOCX. Перевірте порядок і дублювання.",
        ))
    if "low_ocr_confidence" in codes:
        issues.append(Issue(
            "ocr_low_confidence",
            "Є фрагменти з низькою впевненістю OCR. Звірте їх зі сканом, особливо числа та коди.",
        ))
    return issues


@dataclass(frozen=True)
class PreparedPDF:
    source: Path
    provenance: dict
    issues: list[Issue]


def prepare_pdf_scan(
    source, folder, source_language, settings, control, progress, *, source_sha256,
    page_count, handoff=None,
) -> PreparedPDF:
    """Convert or await a reviewed import, then recheck its exact source binding."""
    source = Path(source)
    control.check()
    if source_language == "auto":
        raise FormaError("Для розпізнавання PDF виберіть мову оригіналу.")
    provider = effective_scan_provider(settings)
    if _digest(source) != source_sha256:
        raise FormaError("Вихідний PDF змінився під час підготовки DOCX. Повторіть операцію.")
    progress(1, "Готуємо розпізнавання PDF")
    if provider == "abbyy":
        if handoff is None:
            raise FormaError(
                "Для цього скану потрібне розпізнавання ABBYY. Відкрийте PDF в ABBYY, "
                "збережіть DOCX і додайте його для перекладу або виберіть автоматичний OCR у налаштуваннях."
            )
        output = handoff(source, source_language, control, progress)
    elif provider == "abbyy_extended":
        output = convert_with_abbyy(
            source, folder, source_language, settings.abbyy_path, control, progress,
            extended_cli_confirmed=settings.abbyy_extended_cli_confirmed,
        )
    else:
        output = convert_scan_pdf(
            source, folder, source_language, settings.scan_converter_path, control, progress,
        )
    control.check()
    output = Path(output)
    sidecar = output.with_suffix(".ocr.json")
    try:
        if output.suffix.lower() != ".docx" or output.is_symlink() or sidecar.is_symlink():
            raise ValueError("format")
        with sidecar.open("rb") as stream:
            encoded = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise ValueError("size")
        manifest = json.loads(encoded)
        if not isinstance(manifest, dict):
            raise ValueError("manifest")
        if provider == "forma":
            manifest = _validated_manifest(sidecar, output, source_sha256, source_language, page_count)
            coverage = "complete"
        else:
            if (
                manifest.get("schema_version") != 1 or manifest.get("provider") != "abbyy"
                or manifest.get("coverage") != "not_verified"
                or manifest.get("source_sha256") != source_sha256
                or manifest.get("output_sha256") != _digest(output)
                or manifest.get("source_language") != source_language
                or manifest.get("source_page_count") != page_count
            ):
                raise ValueError("identity")
            office = OfficeDocument(output)
            if not office.segments or not any(segment.plain.strip() for segment in office.segments):
                raise ValueError("empty")
            coverage = "not_verified"
        prepared_hash = _digest(output)
        if prepared_hash != manifest["output_sha256"]:
            raise ValueError("changed output")
        if _digest(source) != source_sha256:
            raise FormaError("Вихідний PDF змінився під час підготовки DOCX. Повторіть операцію.")
    except FormaError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise FormaError(
            "Експорт OCR зупинено: не підтверджено цілісність DOCX або його зв’язок з оригінальним PDF."
        ) from exc
    control.check()
    return PreparedPDF(output.resolve(), {
        "kind": "ocr", "provider": provider,
        "original_source": str(source.resolve()), "original_source_sha256": source_sha256,
        "prepared_source": str(output.resolve()), "prepared_source_sha256": prepared_hash,
        "source_language": source_language, "page_count": page_count, "coverage": coverage,
    }, _review_issues(manifest))
