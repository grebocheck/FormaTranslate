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
from forma.documents.ocr_diagnostics import ocr_review_issues
from forma.documents.ocr_policy import resolve_scan_provider
from forma.documents.ooxml import OfficeDocument
from forma.persistence.prepared import PreparedStage, worker_identity
from forma.providers.client import JobControl


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def effective_scan_provider(settings) -> str:
    return resolve_scan_provider(settings, local_finder=find_scan_converter, abbyy_finder=find_abbyy)


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
    executable = (find_scan_converter(settings.scan_converter_path) if provider == "forma"
                  else find_abbyy(settings.abbyy_path))
    stage = PreparedStage(source, source_sha256, source_language, page_count, provider,
                          worker_identity(executable, provider, settings, control))
    with control.document_slot("ocr:" + stage.key):
        cached = stage.load()
        if cached:
            try:
                prepared = _validate_prepared(cached, source, source_sha256, source_language,
                                              page_count, provider, reused=True)
            except FormaError:
                # Invalid cache metadata never authorizes API work or substitutes
                # an unbound document. A new local conversion may repair it.
                pass
            else:
                control.check()
                progress(100, "OCR · Використовуємо збережений DOCX")
                return prepared
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
        # Conversion returning means a complete artifact has been published.
        # Preserve it even when Stop arrives just before its final event; the
        # control check below still prevents any following translation request.
        prepared = _validate_prepared(output, source, source_sha256, source_language, page_count, provider)
        # First recognition can populate local model files. Capture their final
        # bounded inventory even when Stop arrived after conversion completed;
        # this token is used only for local metadata, never provider requests.
        final_identity = worker_identity(executable, provider, settings, JobControl(1))
        if final_identity != stage.context["engine_identity"]:
            stage = PreparedStage(source, source_sha256, source_language, page_count, provider, final_identity)
        stage.save(prepared.source)
        control.check()
        return prepared


def _validate_prepared(output, source, source_sha256, source_language, page_count, provider, *, reused=False):
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
    issues = ocr_review_issues(manifest)
    if reused:
        issues.append(Issue("ocr_preparation_reused", "Використано перевірений DOCX після попереднього OCR.", severity="info"))
    return PreparedPDF(output.resolve(), {
        "kind": "ocr", "provider": provider,
        "original_source": str(source.resolve()), "original_source_sha256": source_sha256,
        "prepared_source": str(output.resolve()), "prepared_source_sha256": prepared_hash,
        "source_language": source_language, "page_count": page_count, "coverage": coverage,
        "reused": reused,
    }, issues)
