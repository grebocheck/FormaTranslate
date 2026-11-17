"""Report-bound edits of text-source documents without rewriting their markup."""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

from forma.core.models import FormaError, Options, TranslationResult
from forma.documents.adapters import open_document
from forma.documents.review import SEGMENT_CHECK_CODES, parse_report_data
from forma.persistence.files import atomic_json, publish_artifacts
from forma.translation.engine import file_digest, translation_issues
from forma.translation.references import REFERENCE_CODES, check_document_references
from forma.translation.reports import write_translation_report

_CHANGED = "Оригінал, переклад або звіт змінився. Відкрийте актуальні файли перед збереженням копії."


def export_source_copy(source, output, report, edits, folder, expected_fingerprint, started):
    # Keep the same filename convention as Office manual copies.
    from forma.documents.editor import _unique_destination

    with report.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise FormaError("Звіт завеликий для безпечного читання.")
    report_hash = hashlib.sha256(raw).hexdigest()
    if expected_fingerprint is not None and expected_fingerprint != report_hash:
        raise FormaError(_CHANGED)
    try:
        metadata = json.loads(raw)
        previous, _ = parse_report_data(metadata, report)
        options = Options(**metadata["options"])
        options.validate()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise FormaError("Не вдалося прочитати звіт Forma.") from None
    source_hash, output_hash = file_digest(source), file_digest(output)
    if (previous.engine not in {"forma-text", "forma-odf", "forma-epub"} or metadata.get("source_sha256") != source_hash
            or metadata.get("output_sha256") != output_hash):
        raise FormaError(_CHANGED)
    original = open_document(source, extraction_profile=previous.extraction_profile)
    original.validate_package()
    by_id = {item.id: item for item in original.segments}
    if (not set(edits) <= previous.translations.keys()
            or len(by_id) != len(previous.segments)):
        raise FormaError(_CHANGED)
    for saved in previous.segments:
        original_segment = by_id.get(saved.id)
        if (original_segment is None or original_segment.source != saved.source
                or original_segment.location != saved.location):
            raise FormaError(_CHANGED)
    translations = {**previous.translations, **edits}
    rechecked_codes = SEGMENT_CHECK_CODES | set(getattr(original, "layout_issue_codes", ()))
    issues = [item for item in previous.issues
              if item.code not in rechecked_codes and item.code not in REFERENCE_CODES]
    issues.extend(item for segment in original.segments
                  for item in translation_issues(segment, translations[segment.id], options))
    issues.extend(original.layout_issues(translations))
    issues.extend(check_document_references(original.segments, translations))
    if any(item.severity == "error" for item in issues):
        raise FormaError("Експорт зупинено: перевірте числа, коди й форматування відредагованих фрагментів.")
    folder.mkdir(parents=True, exist_ok=True)
    final = _unique_destination(output, folder)
    with tempfile.TemporaryDirectory(prefix=".forma-edit-", dir=folder) as temporary:
        expected = Path(temporary) / ("expected" + output.suffix)
        original.write(previous.translations, expected)
        if file_digest(expected) != output_hash:
            raise FormaError(_CHANGED)
        stage = Path(temporary) / final.name
        original.write(translations, stage)
        result = TranslationResult(
            source, stage, original.segments, translations, issues, engine=previous.engine,
            elapsed_seconds=time.monotonic() - started,
            usage_note="Ручна редактура без нових запитів до API.",
            memory_project_id=options.memory_project_id, extraction_profile=previous.extraction_profile,
            source_sha256=source_hash, output_sha256=file_digest(stage),
            translation_profile=dict(previous.translation_profile),
        )
        glossary = metadata.get("effective_glossary")
        if isinstance(glossary, dict) and all(isinstance(key, str) and isinstance(value, str)
                                              for key, value in glossary.items()):
            result.effective_glossary = dict(glossary)
        automatic = metadata.get("automatic_glossary")
        if metadata.get("automatic_glossary_policy") == 2 and isinstance(automatic, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in automatic.items()
        ):
            result.automatic_glossary = dict(automatic)
        model = metadata.get("model")
        write_translation_report(result, options, model if isinstance(model, str) else "Невідома модель")
        payload = json.loads(result.report.read_text(encoding="utf-8"))
        payload.update(output=str(final), parent_report=str(report), manual_edits=list(edits))
        atomic_json(result.report, payload)
        if (file_digest(source) != source_hash or file_digest(output) != output_hash
                or file_digest(report) != report_hash):
            raise FormaError(_CHANGED)
        final_report = final.with_name(final.name + ".forma.json")
        publish_artifacts({final: stage, final_report: result.report,
                           final_report.with_suffix(".html"): result.report.with_suffix(".html")})
        result.output, result.report = final, final_report
    return result
