"""Local review of current results and saved reports; never calls a provider."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from forma.core.models import (
    INFORMATION_CODES,
    ISSUE_EXPLANATIONS,
    FormaError,
    Issue,
    Options,
    Segment,
    TranslationResult,
    plain_text,
)
from forma.translation.engine import translation_issues

# These describe the workflow, not a defect in the translated document.
SEGMENT_CHECK_CODES = {
    "formatting",
    "numbers",
    "protected_values",
    "missing_text",
    "quantity_units",
    "unchanged",
    "expansion",
    "short_translation",
    "repeated_text",
    "untranslated_prose",
    "remaining_source_text",
    "missing_translation",
}


def actionable_count(issues: list[Issue]) -> int:
    return sum(
        issue.severity == "error"
        or (issue.severity != "info" and issue.code not in INFORMATION_CODES)
        for issue in issues
    )


def load_report(path: Path) -> tuple[TranslationResult, Options]:
    """Read a bounded report, not arbitrary objects or external references."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise FormaError(
            "Не вдалося прочитати звіт. Перевірте, чи файл існує та є коректним звітом Forma."
        ) from None
    result, options = parse_report_data(data, path)
    return refresh_segment_checks(result, options), options


def _parse_preparation(data: dict, options: Options) -> dict:
    """Read a bounded provenance record without opening any referenced path."""
    preparation = data.get("preparation", {})
    if not isinstance(preparation, dict) or len(preparation) > 32:
        raise ValueError("preparation")
    if not preparation:
        return {}
    required = {
        "kind", "provider", "original_source", "original_source_sha256", "prepared_source",
        "prepared_source_sha256", "source_language", "page_count", "coverage",
    }
    if not required <= preparation.keys():
        raise ValueError("preparation fields")
    clean = {key: preparation[key] for key in required}
    if "reused" in preparation:
        if type(preparation["reused"]) is not bool:
            raise ValueError("preparation reuse")
        clean["reused"] = preparation["reused"]
    if (
        clean["kind"] != "ocr" or clean["provider"] not in {"forma", "abbyy", "abbyy_extended"}
        or clean["coverage"] != ("complete" if clean["provider"] == "forma" else "not_verified")
        or type(clean["page_count"]) is not int or not 1 <= clean["page_count"] <= 200
        or clean["source_language"] != options.source_language
    ):
        raise ValueError("preparation identity")
    for key in ("original_source_sha256", "prepared_source_sha256"):
        if not isinstance(clean[key], str) or not re.fullmatch(r"[0-9a-f]{64}", clean[key]):
            raise ValueError("preparation hash")
    for key, suffix in (("original_source", ".pdf"), ("prepared_source", ".docx")):
        value = clean[key]
        if (
            not isinstance(value, str) or not 0 < len(value) <= 8192
            or any(ord(char) < 32 for char in value) or value.startswith(("//", "\\\\"))
            or Path(value).suffix.lower() != suffix
            or not (
                Path(value).is_absolute()
                or (re.fullmatch(r"[A-Za-z]:", PureWindowsPath(value).drive) and PureWindowsPath(value).root)
            )
        ):
            raise ValueError("preparation path")
    if data.get("source") != clean["prepared_source"] or data.get("source_sha256") != clean["prepared_source_sha256"]:
        raise ValueError("preparation source binding")
    return clean


def parse_report_data(data: dict, path: Path) -> tuple[TranslationResult, Options]:
    """Validate already-read report data without reading paths or using a provider."""
    try:
        if (
            not isinstance(data, dict)
            or not {"segments", "issues", "engine", "options"} <= data.keys()
        ):
            raise ValueError
        raw_segments, raw_issues = data.get("segments", []), data.get("issues", [])
        if not isinstance(raw_segments, list) or not isinstance(raw_issues, list):
            raise ValueError
        if len(raw_segments) > 100000 or len(raw_issues) > 100000:
            raise ValueError
        segments, translations, seen = [], {}, set()
        for item in raw_segments:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str)
                for key in ("id", "location", "source", "translation")
            ):
                raise ValueError
            if not item["id"] or item["id"] in seen:
                raise ValueError
            seen.add(item["id"])
            segments.append(Segment(item["id"], item["location"], item["source"]))
            translations[item["id"]] = item["translation"]
        issues = []
        for item in raw_issues:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key, ""), str)
                for key in ("code", "message", "segment_id", "severity")
            ):
                raise ValueError
            if not item.get("code") or not item.get("message"):
                raise ValueError
            issues.append(
                Issue(
                    item["code"],
                    item["message"],
                    item.get("segment_id", ""),
                    item.get("severity", "warning"),
                )
            )
        raw_options = data.get("options", {})
        if not isinstance(raw_options, dict):
            raise ValueError
        source_language = raw_options.get("source_language", "auto")
        target_language = raw_options.get("target_language", "uk")
        if not isinstance(source_language, str) or not isinstance(target_language, str):
            raise ValueError
        options = Options(source_language=source_language, target_language=target_language)
        options.validate()
        engine = data.get("engine", "forma-ooxml")
        if engine not in ("forma-ooxml", "forma-corpus", "babeldoc"):
            raise ValueError
        extraction_profile = data.get("extraction_profile", 1)
        if type(extraction_profile) is not int or extraction_profile not in {1, 2}:
            raise ValueError("extraction profile")
        if extraction_profile == 2 and engine != "forma-ooxml":
            raise ValueError("extraction engine")
        if extraction_profile == 2 and (
            not isinstance(data.get("source"), str)
            or PureWindowsPath(data["source"]).suffix.lower() != ".xlsx"
        ):
            raise ValueError("extraction format")
        result = TranslationResult(
            path, None, segments, translations, issues, engine=engine, report=path,
            extraction_profile=extraction_profile,
        )
        result.preparation = _parse_preparation(data, options)
        if result.preparation:
            if engine not in {"forma-ooxml", "forma-corpus"}:
                raise ValueError("preparation engine")
            result.source = Path(result.preparation["prepared_source"])
            result.source_sha256 = result.preparation["prepared_source_sha256"]
        return result, options
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, FormaError):
        raise FormaError(
            "Не вдалося прочитати звіт. Перевірте, чи файл існує та є коректним звітом Forma."
        ) from None


def entry_fingerprint(entry: dict) -> str:
    fields = {
        key: entry[key] for key in ("id", "source", "translation", "messages", "severity", "kind")
    }
    return hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def refresh_segment_checks(result: TranslationResult, options: Options) -> TranslationResult:
    """Return current local findings without mutating the report or its result.

    Only deterministic findings for present Office segments are replaced. Keep
    unknown findings/IDs and document guidance, including explicit failures.
    """
    issues = list(result.issues)
    if result.engine != "babeldoc":
        # Re-evaluate old heuristic findings with today's rules. Structural and
        # document-level findings remain; the original report is never rewritten.
        segment_ids = {segment.id for segment in result.segments}
        issues = [
            issue
            for issue in issues
            if issue.code not in SEGMENT_CHECK_CODES or issue.segment_id not in segment_ids
        ]
        for segment in result.segments:
            translated = result.translations.get(segment.id, "")
            if not plain_text(translated).strip() and segment.plain.strip():
                issues.append(
                    Issue(
                        "missing_translation",
                        "Для цього фрагмента відсутній переклад.",
                        segment.id,
                        "error",
                    )
                )
            else:
                issues.extend(translation_issues(segment, translated, options))
    return replace(result, issues=[
        replace(issue, severity="info")
        if issue.code in INFORMATION_CODES and issue.severity != "error" else issue
        for issue in issues
    ])


def build_review(result: TranslationResult, options: Options, *, edits=None, accepted=None, checks_current=False) -> dict:
    edits, accepted = edits or {}, accepted or {}
    if edits:
        result = replace(result, translations={**result.translations, **edits})
    # Background report loading already refreshed the saved translations. Edits
    # always need fresh checks; callers cannot use the flag to bypass them.
    if edits or not checks_current:
        result = refresh_segment_checks(result, options)
    issues = result.issues
    entries = {
        segment.id: {
            "id": segment.id,
            "location": segment.location,
            "source": segment.plain,
            "translation": plain_text(result.translations.get(segment.id, "")),
            "messages": [],
            "severity": "",
            "kind": "segment",
            "issueCount": 0,
        }
        for segment in result.segments
    }
    document_entries, merged = {}, {}
    priority = {"": 0, "info": 1, "warning": 2, "error": 3}
    warning_count = 0
    for issue in issues:
        severity = (
            "info"
            if issue.code in INFORMATION_CODES and issue.severity != "error"
            else issue.severity
        )
        if severity not in ("info", "warning", "error"):
            severity = "warning"
        if issue.code in ISSUE_EXPLANATIONS and issue.severity != "error":
            issue = replace(issue, message=ISSUE_EXPLANATIONS[issue.code])
        key = (issue.code, issue.segment_id, issue.message)
        if key not in merged or priority[severity] > priority[merged[key][1]]:
            merged[key] = (issue, severity)
    for issue, severity in merged.values():
        if issue.segment_id in entries:
            entry = entries[issue.segment_id]
        else:
            entry_id = "document:" + issue.code
            entry = document_entries.setdefault(
                entry_id,
                {
                    "id": entry_id,
                    "location": "Документ",
                    "source": "",
                    "translation": "",
                    "messages": [],
                    "severity": "",
                    "kind": "document",
                    "issueCount": 0,
                },
            )
        entry["messages"].append(issue.message)
        if severity != "info":
            entry["issueCount"] += 1
            warning_count += 1
        if priority[severity] > priority[entry["severity"]]:
            entry["severity"] = severity
    all_entries = [*document_entries.values(), *entries.values()]
    for entry in all_entries:
        entry["fingerprint"] = entry_fingerprint(entry)
        entry["accepted"] = (
            entry["severity"] != "error" and accepted.get(entry["id"]) == entry["fingerprint"]
        )
        entry["edited"] = entry["id"] in edits
    return {
        "entries": all_entries,
        "summary": {
            "segments": len(result.segments),
            "attention": sum(
                entry["issueCount"] > 0 and not entry["accepted"] for entry in all_entries
            ),
            "notes": sum(entry["severity"] == "info" for entry in all_entries),
            "warningCount": sum(
                entry["issueCount"] for entry in all_entries if not entry["accepted"]
            ),
            "accepted": sum(entry["accepted"] for entry in all_entries),
            "edited": sum(entry["edited"] for entry in all_entries),
            "loading": False,
            "error": "",
            "hasResult": True,
        },
    }
