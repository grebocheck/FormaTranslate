"""Explicit, document-scoped corrections for a blocked Office translation.

Failure reports are diagnostics, never completed translations. Approved corrections
live separately from paid-work checkpoints so GUI edits cannot race a running batch.
Every consumer rechecks the current source, policy, profile and hard invariants.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from dataclasses import asdict, fields
from pathlib import Path

from forma.core.models import FormaError, Issue, Options, Segment, TranslationResult
from forma.documents.ooxml import OfficeDocument
from forma.persistence.files import atomic_json
from forma.settings.config import data_directory
from forma.settings.connection import connection_identity

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 100000
_HEX = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.RLock()
_READ_ERROR = "Не вдалося прочитати дані відновлення перекладу. Збережені правки не змінено."
_CHANGED_ERROR = "Оригінал або звіт змінився. Відкрийте проблемні фрагменти знову."
_SETTINGS_ERROR = "Налаштування перекладу змінилися. Відновіть початкове підключення, моделі та правила перед продовженням."


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _valid_hash(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _read(path):
    if not stat.S_ISREG(Path(path).stat().st_mode):
        raise ValueError
    with Path(path).open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique), hashlib.sha256(raw).hexdigest()


def _options(raw):
    expected = {item.name for item in fields(Options)}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError
    if any(type(raw[key]) is not bool for key in ("review", "auto_glossary")):
        raise ValueError
    if any(not isinstance(value, str) for key, value in raw.items() if key not in {"review", "auto_glossary", "glossary"}):
        raise ValueError
    glossary = raw["glossary"]
    if not isinstance(glossary, dict) or len(glossary) > MAX_ENTRIES or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in glossary.items()
    ):
        raise ValueError
    options = Options(**raw)
    options.validate()
    return options


def _policy(options):
    values = asdict(options)
    values.pop("pdf_mode", None)
    return _hash(values)


def settings_signature(settings):
    """Hash only checkpoint-relevant connection fields; never store credentials."""
    return _hash({
        "base": connection_identity(settings.base_url, settings.api_mode).root,
        "model": settings.model, "mode": settings.api_mode,
        "format": settings.response_format, "review_model": settings.review_model,
        "reasoning_effort": settings.reasoning_effort,
    })


def validate_recovery_settings(meta, settings):
    from forma.translation.engine import checkpoint_fingerprint

    if not meta.get("can_resume") or meta.get("settings_signature") != settings_signature(settings):
        raise FormaError(_SETTINGS_ERROR)
    try:
        expected = checkpoint_fingerprint(
            meta["source_sha256"], _options(meta["options"]), settings,
            memory_policy=meta["memory_policy"], memory_generation=meta["memory_generation"],
            document_profile=meta["extraction_profile"],
        )
    except (ValueError, TypeError, KeyError):
        raise FormaError(_SETTINGS_ERROR) from None
    if expected != meta["checkpoint_id"]:
        raise FormaError(_SETTINGS_ERROR)


def validate_recovery_checkpoint(expected, checkpoint):
    if expected and (not _valid_hash(expected) or expected != checkpoint.stem):
        raise FormaError(_SETTINGS_ERROR)


def attach_recovery(failure, source, options, settings, checkpoint, source_hash, profile, *, memory_policy="", memory_generation=""):
    failure.recovery = {
        "checkpoint_id": checkpoint.stem, "source_sha256": source_hash,
        "extraction_profile": profile, "options": asdict(options),
        "settings_signature": settings_signature(settings),
        "memory_policy": memory_policy, "memory_generation": memory_generation,
    }


def _metadata(data, fingerprint):
    raw = data.get("recovery")
    if not isinstance(raw, dict) or set(raw) != {
        "checkpoint_id", "source_sha256", "extraction_profile", "options", "settings_signature",
        "memory_policy", "memory_generation",
    }:
        raise ValueError
    if any(not _valid_hash(raw[key]) for key in ("checkpoint_id", "source_sha256", "settings_signature")):
        raise ValueError
    if type(raw["extraction_profile"]) is not int or raw["extraction_profile"] not in {1, 2}:
        raise ValueError
    options = _options(raw["options"])
    requested = _options(data["requested_options"])
    for key in ("memory_policy", "memory_generation"):
        if (options.memory_project_id and not _valid_hash(raw[key])) or (not options.memory_project_id and raw[key] != ""):
            raise ValueError
    effective_policy, requested_policy = asdict(options), asdict(requested)
    for value in (effective_policy, requested_policy):
        value.pop("pdf_mode")
        if options.memory_project_id:
            value.pop("glossary")
            value.pop("auto_glossary")
    if effective_policy != requested_policy:
        raise ValueError
    preparation = data.get("preparation", {})
    if not isinstance(preparation, dict):
        raise ValueError
    if data.get("source_sha256") != raw["source_sha256"]:
        raise ValueError
    if options.source_language != requested.source_language or options.target_language != requested.target_language:
        raise ValueError
    return options, {
        **raw, "requested_options": asdict(requested), "report_fingerprint": fingerprint,
        "source": data["source"], "original_source": preparation.get("original_source", data["source"]),
        "can_resume": True,
    }


def load_failure_report(path):
    """Read current candidates without accessing referenced sources or providers.

    Legacy diagnostics remain readable, but cannot approve unbound corrections.
    The SHA is identical to ReviewStore's fingerprint of the same JSON bytes.
    """
    from forma.documents.review import _parse_preparation, refresh_segment_checks

    path = Path(path)
    if path.suffix.lower() == ".html":
        path = path.with_suffix(".json")
    try:
        data, fingerprint = _read(path)
        if (
            not isinstance(data, dict) or data.get("kind") != "translation_validation_failure"
            or data.get("exported") is not False or type(data.get("schema_version")) is not int
            or data.get("schema_version") not in {1, 2}
            or data.get("stage") not in {"translation", "review", "preflight"}
            or not isinstance(data.get("source"), str) or not 0 < len(data["source"]) <= 8192
            or "\x00" in data["source"] or Path(data["source"]).suffix.lower() not in {".docx", ".xlsx"}
        ):
            raise ValueError
        entries = data.get("segments")
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_ENTRIES:
            raise ValueError
        segments, translations, issues, seen = [], {}, [], set()
        for entry in entries:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) for key in ("id", "location", "source")):
                raise ValueError
            sid, candidate = entry["id"], entry.get("candidate")
            if not sid or len(sid) > 1024 or sid in seen or (candidate is not None and not isinstance(candidate, str)):
                raise ValueError
            context = entry.get("context", "")
            if not isinstance(context, str):
                raise ValueError
            seen.add(sid)
            segments.append(Segment(sid, entry["location"], entry["source"], context))
            translations[sid] = candidate or ""
            findings = entry.get("issues", [])
            if not isinstance(findings, list) or len(findings) > MAX_ENTRIES:
                raise ValueError
            for finding in findings:
                if not isinstance(finding, dict) or any(not isinstance(finding.get(key), str) for key in ("code", "message", "segment_id", "severity")):
                    raise ValueError
                issues.append(Issue(**{key: finding[key] for key in ("code", "message", "segment_id", "severity")}))
        if data["schema_version"] == 2:
            options, meta = _metadata(data, fingerprint)
        else:
            options = Options(source_language=data.get("source_language", "auto"), target_language=data.get("target_language", "uk"))
            options.validate()
            meta = {"can_resume": False, "report_fingerprint": fingerprint, "source": data["source"], "original_source": data["source"]}
        result = TranslationResult(
            Path(data["source"]), None, segments, translations, [], report=path,
            source_sha256=data.get("source_sha256", ""), extraction_profile=meta.get("extraction_profile", 1),
        )
        # Old OCR reports lacked the source hash required by current provenance.
        if data.get("preparation") and data["schema_version"] == 2:
            result.preparation = _parse_preparation(data, options)
        meta["failure_issues"] = issues
        return refresh_segment_checks(result, options), options, meta
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, KeyError):
        raise FormaError(_READ_ERROR) from None


def _path(checkpoint_id):
    if not _valid_hash(checkpoint_id):
        raise ValueError
    return data_directory() / "recovery" / f"{checkpoint_id}.json"


def _binding(checkpoint_id, source_hash, options, profile):
    return {
        "schema_version": 1, "checkpoint_id": checkpoint_id, "source_sha256": source_hash,
        "extraction_profile": profile, "options_sha256": _policy(options),
    }


def _saved(path, binding):
    try:
        data, _ = _read(path)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or set(data) != {*binding, "edits"} or any(data.get(key) != value for key, value in binding.items()):
        raise ValueError
    edits = data["edits"]
    if not isinstance(edits, dict) or len(edits) > MAX_ENTRIES:
        raise ValueError
    for sid, value in edits.items():
        if not isinstance(sid, str) or not isinstance(value, dict) or set(value) != {"source", "target"} or any(not isinstance(item, str) for item in value.values()):
            raise ValueError
    return edits


def _checked(edits, segments, options):
    from forma.translation.engine import blocking_issues

    by_id = {segment.id: segment for segment in segments}
    checked = {}
    for sid, value in edits.items():
        segment = by_id.get(sid)
        if segment is None or value["source"] != segment.source:
            raise FormaError(_CHANGED_ERROR)
        errors = blocking_issues(segment, value["target"], options)
        if errors:
            raise FormaError("Правку не збережено: " + " ".join(issue.message for issue in errors))
        checked[sid] = value["target"]
    return checked


def load_recovery(checkpoint, source_hash, options, profile, segments):
    """Return only explicit final corrections in this exact checkpoint scope."""
    try:
        binding = _binding(checkpoint.stem, source_hash, options, profile)
        with _LOCK:
            return _checked(_saved(_path(checkpoint.stem), binding), segments, options)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise FormaError(_READ_ERROR) from None


def save_recovery_edits(report, edits, *, expected_report_fingerprint):
    """Approve only supplied, hard-valid candidates; never write an arbitrary path."""
    from forma.translation.engine import file_digest

    report = Path(report)
    if report.suffix.lower() == ".html":
        report = report.with_suffix(".json")
    try:
        result, options, meta = load_failure_report(report)
        if not meta["can_resume"] or meta["report_fingerprint"] != expected_report_fingerprint:
            raise FormaError(_CHANGED_ERROR)
        if not isinstance(edits, dict) or not edits or len(edits) > MAX_ENTRIES or any(not isinstance(value, str) for value in edits.values()):
            raise ValueError
        source = Path(meta["source"])
        if file_digest(source) != meta["source_sha256"]:
            raise FormaError(_CHANGED_ERROR)
        preparation = result.preparation
        if preparation and file_digest(Path(preparation["original_source"])) != preparation["original_source_sha256"]:
            raise FormaError(_CHANGED_ERROR)
        document = OfficeDocument(source, extraction_profile=meta["extraction_profile"])
        report_segments = {segment.id: segment for segment in result.segments}
        if set(edits) - report_segments.keys():
            raise ValueError
        proposed = {sid: {"source": report_segments[sid].source, "target": value} for sid, value in edits.items()}
        _checked(proposed, document.segments, options)
        if document.extraction_profile != meta["extraction_profile"]:
            raise FormaError(_CHANGED_ERROR)
        binding = _binding(meta["checkpoint_id"], meta["source_sha256"], options, meta["extraction_profile"])
        with _LOCK:
            path = _path(meta["checkpoint_id"])
            saved = _saved(path, binding)
            _checked(saved, document.segments, options)
            data = {**binding, "edits": {**saved, **proposed}}
            if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_BYTES:
                raise ValueError
            if _read(report)[1] != expected_report_fingerprint or file_digest(source) != meta["source_sha256"]:
                raise FormaError(_CHANGED_ERROR)
            if preparation and file_digest(Path(preparation["original_source"])) != preparation["original_source_sha256"]:
                raise FormaError(_CHANGED_ERROR)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                path.parent.chmod(0o700)
            atomic_json(path, data)
        return len(proposed)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise FormaError(_READ_ERROR) from None
