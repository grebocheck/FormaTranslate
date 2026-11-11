"""Verified, offline imports into a project's private translation memory."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from forma.core.models import FormaError, Options, Segment, TranslationResult, plain_text
from forma.documents.editor import _locate_target, _shape, _target_markup
from forma.documents.ooxml import OfficeDocument
from forma.documents.review import parse_report_data
from forma.translation.engine import blocking_issues, use_english_decimals
from forma.translation.quality import parse_translation

MAX_REPORT_BYTES = 64 * 1024 * 1024
_INVALID_REPORT = "Не вдалося перевірити звіт Forma для пам’яті перекладів."
_MISMATCH = "Оригінал, переклад і звіт не відповідають одне одному. Виберіть файли з одного перекладу."
_UNSUPPORTED = "Імпорт пам’яті підтримує лише зв’язані DOCX і звіт Forma."
_STALE = "Вхідні файли змінилися під час перевірки. Повторіть імпорт."
_UNKNOWN_POLICY = (
    "У цьому звіті немає повного глосарію перекладу. "
    "Імпортуйте його як довідковий або створіть новий звіт."
)
_PROJECT_POLICY = "Мови або глосарій звіту не відповідають проєкту пам’яті."
_EXPLICIT_LANGUAGE = "Для затвердження пам’яті потрібен звіт із явно вказаними мовами."
_INVALID_TRANSLATION = "Запис не додано: перевірте числа, коди й форматування перекладу."
_REPLACE_CONTEXT = "Щоб замінити перевірений переклад, спочатку перегляньте актуальний конфлікт."
_MEMORY_CHANGED = "Пам’ять змінилася після показу конфлікту. Повторіть дію та перевірте варіанти."
_INTERNAL_CONFLICT = "У звіті є різні переклади одного фрагмента з однаковим контекстом. Затвердьте потрібний варіант окремо."
_EMPTY = "У звіті немає фрагментів для пам’яті перекладів."


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _glossary(value) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(text, str) for key, text in value.items()
    ):
        raise FormaError(_INVALID_REPORT)
    return dict(value)


def _options(metadata: dict, parsed: Options) -> tuple[Options, bool]:
    saved = metadata["options"]
    required = {"source_language", "target_language", "domain", "review", "auto_glossary", "glossary", "instructions"}
    for field in ("domain", "instructions"):
        if field in saved:
            if not isinstance(saved[field], str):
                raise FormaError(_INVALID_REPORT)
            setattr(parsed, field, saved[field])
    for field in ("review", "auto_glossary"):
        if field in saved:
            if type(saved[field]) is not bool:
                raise FormaError(_INVALID_REPORT)
            setattr(parsed, field, saved[field])
    if "glossary" in saved:
        parsed.glossary = _glossary(saved["glossary"])
    parsed.validate()
    return parsed, required <= saved.keys()


@dataclass
class _Verified:
    source: Path
    output: Path
    report: Path
    original: OfficeDocument
    previous: TranslationResult
    options: Options
    metadata: dict
    bindings: list[tuple[str, Segment]]
    source_hash: str
    output_hash: str
    report_hash: str
    full_policy: bool

    def check_unchanged(self):
        try:
            unchanged = (
                _digest(self.source) == self.source_hash
                and _digest(self.output) == self.output_hash
                and _digest(self.report) == self.report_hash
            )
        except OSError as exc:
            raise FormaError(_STALE) from exc
        if not unchanged:
            raise FormaError(_STALE)


def _canonical_xml(data: bytes) -> bytes:
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    return etree.tostring(root, method="c14n")


def _verify_legacy_package(source: Path, current: OfficeDocument, translations: dict, options: Options):
    """Without file hashes, reproduce the complete package relation explicitly.

    This also checks non-segment numeric cells, protected field values, images,
    and omitted XML which a segment-only comparison cannot authenticate.
    """
    expected_source = OfficeDocument(source)
    with tempfile.TemporaryDirectory(prefix="forma-memory-verify-") as temporary:
        expected_path = Path(temporary) / "expected.docx"
        expected_source.write(translations, expected_path, decimal_comma_to_point=use_english_decimals(options))
        expected = OfficeDocument(expected_path)
    if set(expected.parts) != set(current.parts):
        raise FormaError(_MISMATCH)
    for name, data in expected.parts.items():
        present = current.parts[name]
        if data == present:
            continue
        if name.endswith((".xml", ".rels")) and _canonical_xml(data) == _canonical_xml(present):
            continue
        raise FormaError(_MISMATCH)


def _verify(source, output, report, expected_report_fingerprint=None) -> _Verified:
    source, output, report = Path(source), Path(output), Path(report)
    if (
        source.suffix.lower() != ".docx"
        or output.suffix.lower() != ".docx"
        or not report.name.lower().endswith(".forma.json")
        or source.resolve() == output.resolve()
    ):
        raise FormaError(_UNSUPPORTED)
    try:
        with report.open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise FormaError(_INVALID_REPORT)
        report_hash = hashlib.sha256(raw).hexdigest()
        if expected_report_fingerprint is not None and report_hash != expected_report_fingerprint:
            raise FormaError(_STALE)
        metadata = json.loads(raw, object_pairs_hook=_unique_object)
        previous, parsed_options = parse_report_data(metadata, report)
        options, full_policy = _options(metadata, parsed_options)
        if previous.engine not in {"forma-ooxml", "forma-corpus"}:
            raise FormaError(_UNSUPPORTED)
        if not previous.segments:
            raise FormaError(_EMPTY)
        if not isinstance(metadata.get("output"), str) or not metadata["output"]:
            raise FormaError(_INVALID_REPORT)
        source_hash, output_hash = _digest(source), _digest(output)
        for field, actual in (("source_sha256", source_hash), ("output_sha256", output_hash)):
            if field in metadata and metadata[field] != actual:
                raise FormaError(_MISMATCH)
        original, current = OfficeDocument(source), OfficeDocument(output)
        by_source = {}
        for segment in original.segments:
            identity = (segment.location, segment.source)
            if identity in by_source:
                raise FormaError(_MISMATCH)
            by_source[identity] = segment
        reported = [(segment.location, segment.source) for segment in previous.segments]
        if len(set(reported)) != len(reported) or set(reported) != set(by_source):
            raise FormaError(_MISMATCH)
        parts = {original.targets[segment.id].part for segment in original.segments}
        for part in parts:
            if part not in current.parts or _shape(original, part) != _shape(current, part):
                raise FormaError(_MISMATCH)
        bindings, by_original_id = [], {}
        for old in previous.segments:
            segment = by_source[(old.location, old.source)]
            target = _locate_target(original, current, original.targets[segment.id])
            present = _target_markup(current, target)
            reported_target = previous.translations[old.id]
            if parse_translation(segment.source, present) != parse_translation(segment.source, reported_target):
                raise FormaError(_MISMATCH)
            if not plain_text(present).strip() or blocking_issues(segment, present, options):
                raise FormaError(_INVALID_TRANSLATION)
            bindings.append((old.id, segment))
            by_original_id[segment.id] = reported_target
        if not {"source_sha256", "output_sha256"} <= metadata.keys():
            _verify_legacy_package(source, current, by_original_id, options)
        verified = _Verified(source, output, report, original, previous, options, metadata,
                             bindings, source_hash, output_hash, report_hash, full_policy)
        verified.check_unchanged()
        return verified
    except FormaError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, etree.LxmlError) as exc:
        raise FormaError(_INVALID_REPORT) from exc


def _policy(verified: _Verified, snapshot, preferred: bool) -> tuple[str, bool]:
    from forma.memory.store import policy_fingerprint

    options = verified.options
    if (
        options.target_language.lower() != snapshot.target_language.lower()
        or options.source_language.lower() not in {snapshot.source_language.lower(), "auto"}
    ):
        raise FormaError(_PROJECT_POLICY)
    if verified.metadata.get("effective_glossary") is not None:
        effective = _glossary(verified.metadata["effective_glossary"])
        known = verified.full_policy
    else:
        effective = options.glossary
        known = verified.full_policy and not options.auto_glossary
    if preferred and options.source_language.lower() == "auto":
        raise FormaError(_EXPLICIT_LANGUAGE)
    if preferred and not known:
        raise FormaError(_UNKNOWN_POLICY)
    if preferred and effective != snapshot.glossary:
        raise FormaError(_PROJECT_POLICY)
    return policy_fingerprint(options, effective), known


def _entries(verified: _Verified, policy: str, known_policy: bool, overrides=None) -> list[dict]:
    from forma.memory.context import adaptation_context, scoped_segment_context, segment_role
    from forma.memory.similarity import eligible_source
    from forma.memory.store import exact_fingerprint

    entries = []
    for old_id, segment in verified.bindings:
        if overrides is not None and old_id not in overrides:
            continue
        target = verified.previous.translations[old_id] if overrides is None else overrides[old_id]
        if not isinstance(target, str) or not plain_text(target).strip():
            raise FormaError(_INVALID_TRANSLATION)
        parse_translation(segment.source, target)
        if blocking_issues(segment, target, verified.options):
            raise FormaError(_INVALID_TRANSLATION)
        context = scoped_segment_context(verified.original, segment)
        role = segment_role(verified.original, segment)
        key = exact_fingerprint(policy, segment.source, context, role)
        entries.append({
            "key": key, "source_markup": segment.source, "target": target,
            "context": context, "role": role, "policy": policy,
            "provenance": {
                "kind": "manual_preference" if overrides is not None else "verified_reference_import",
                "origin": "forma_report", "segment_id": old_id,
                "source_sha256": verified.source_hash, "output_sha256": verified.output_hash,
                "report_sha256": verified.report_hash, "policy_complete": known_policy,
            },
        })
        retrieval = adaptation_context(verified.original, segment) if eligible_source(segment.source) else None
        if retrieval is not None:
            entries[-1]["provenance"]["adaptation_context_sha256"] = retrieval
    return entries


def _save(memory, project_id, snapshot, verified, entries, *, preferred, replace,
          expected_revision, expected_glossary_revision):
    from forma.memory.store import MemoryConflict

    if replace and (type(expected_revision) is not int or type(expected_glossary_revision) is not int):
        raise FormaError(_REPLACE_CONTEXT)
    if preferred:
        selected = {}
        for entry in entries:
            previous = selected.setdefault(entry["key"], entry["target"])
            if previous != entry["target"]:
                raise FormaError(_INTERNAL_CONFLICT)
    verified.check_unchanged()
    try:
        hits = memory.save_batch(
            project_id, entries, preferred=preferred, replace=replace,
            expected_revision=snapshot.approved_revision if expected_revision is None else expected_revision,
            expected_glossary_revision=snapshot.glossary_revision if expected_glossary_revision is None else expected_glossary_revision,
        )
    except MemoryConflict as exc:
        if exc.reason != "approved_conflict":
            raise FormaError(_MEMORY_CHANGED) from exc
        exc.expected_revision = exc.current_revision
        exc.expected_glossary_revision = exc.current_glossary_revision
        exc.new_target = next((entry["target"] for entry in entries if entry["key"] == exc.entry_key), "")
        raise
    count = len(hits)
    return {"inserted": count, "approved": count if preferred else 0, "references": 0 if preferred else count}


def import_reference(memory, project_id, source: Path, output: Path, report: Path, *,
                     preferred: bool = False, replace: bool = False,
                     expected_revision: int | None = None,
                     expected_glossary_revision: int | None = None) -> dict:
    """Import a verified pair; only an explicit preferred action approves it."""
    if type(preferred) is not bool or type(replace) is not bool:
        raise FormaError(_INVALID_REPORT)
    snapshot = memory.snapshot(project_id)
    verified = _verify(source, output, report)
    policy, known = _policy(verified, snapshot, preferred)
    entries = _entries(verified, policy, known)
    return _save(memory, project_id, snapshot, verified, entries, preferred=preferred, replace=replace,
                 expected_revision=expected_revision, expected_glossary_revision=expected_glossary_revision)


def prefer_review_segment(memory, project_id, source: Path, output: Path, report: Path,
                          segment_id: str, translation_override: str | None = None, *,
                          expected_report_fingerprint: str | None = None,
                          replace: bool = False, expected_revision: int | None = None,
                          expected_glossary_revision: int | None = None) -> dict:
    """Approve one explicit reviewed target, including a validated unsaved draft."""
    if type(replace) is not bool or not isinstance(segment_id, str):
        raise FormaError(_INVALID_REPORT)
    snapshot = memory.snapshot(project_id)
    verified = _verify(source, output, report, expected_report_fingerprint)
    if segment_id not in verified.previous.translations:
        raise FormaError(_MISMATCH)
    policy, known = _policy(verified, snapshot, True)
    target = verified.previous.translations[segment_id] if translation_override is None else translation_override
    entries = _entries(verified, policy, known, {segment_id: target})
    return _save(memory, project_id, snapshot, verified, entries, preferred=True, replace=replace,
                 expected_revision=expected_revision, expected_glossary_revision=expected_glossary_revision)
