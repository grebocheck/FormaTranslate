"""Bounded editorial revisions and private, source-bound paid-work recovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from forma.core.models import plain_text
from forma.persistence.files import atomic_json
from forma.settings.connection import connection_identity

_STRONG = frozenset({"untranslated_prose", "remaining_source_text", "repeated_text", "short_translation"})
_LATIN = frozenset({"en", "de", "fr", "es", "it", "pt", "pl", "cs", "sk", "nl", "sv", "da", "fi", "ro", "tr"})
_CYRILLIC = frozenset({"uk", "ru", "bg", "be", "mk"})


def actionable_issues(segment, candidate, issues, options):
    """Select revision hints; uncertain findings never become export errors."""
    selected = [issue for issue in issues if issue.severity != "error" and issue.code in _STRONG]
    if not any(issue.code == "unchanged" for issue in issues):
        return selected
    before, after = segment.plain.strip(), plain_text(candidate).strip()
    if any(term.strip().casefold() == before.casefold() and target.strip().casefold() == after.casefold()
           for term, target in options.glossary.items()):
        return selected
    source = options.source_language.lower().split("-")[0]
    target = options.target_language.lower().split("-")[0]
    if source == target:
        return selected
    cross_script = (
        target in _LATIN and source in _CYRILLIC | {"auto"} and re.search(r"[А-Яа-яІіЇїЄєҐґЁё]", before)
        or target in _CYRILLIC and source in _LATIN | {"auto"} and re.search(r"[A-Za-zÀ-ž]", before)
    )
    substantial = len(before.split()) >= 8 and sum(char.isalpha() for char in before) >= 45
    if cross_script or substantial:
        selected.extend(issue for issue in issues if issue.code == "unchanged")
    return selected


def completion_marker(segment, candidate, options, settings):
    identity = {
        "version": 1, "source": segment.source, "context": segment.context, "candidate": candidate,
        "source_language": options.source_language, "target_language": options.target_language,
        "domain": options.domain, "instructions": options.instructions, "glossary": options.glossary,
        "connection": connection_identity(settings.base_url, settings.api_mode).key,
        "model": settings.model, "review_model": settings.review_model,
        "reasoning_effort": settings.reasoning_effort, "response_format": settings.response_format,
        "max_output_tokens": settings.max_output_tokens,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def needs_editorial(segment, candidate, options, settings, issues, completed_marker=""):
    return bool(options.review and actionable_issues(segment, candidate, issues, options)
                and completed_marker != completion_marker(segment, candidate, options, settings))


class EditorialCache:
    """Provisional finals stay outside public translations and memory futures."""

    def __init__(self, fragments, options, settings):
        self.fragments, self.options, self.settings = fragments, options, settings

    def _marker(self, segment, candidate):
        return completion_marker(segment, candidate, self.options, self.settings)

    def _family(self, segment):
        context = segment.context
        for _ in range(8):
            try:
                data = json.loads(context)
            except (ValueError, TypeError, RecursionError):
                break
            if not isinstance(data, dict) or set(data) != {
                "context", "paragraph_validation_errors", "paragraph_repair_revisions",
            } or not isinstance(data["context"], str):
                break
            context = data["context"]
        return self._marker(replace(segment, context=context), "")

    def recover(self, segment):
        with self.fragments.lock:
            journal = self.fragments.state.get("editorial_revisions", {})
            entry = journal.get(self._marker(segment, ""), {}) if isinstance(journal, dict) else {}
            candidate = entry.get("candidate") if isinstance(entry, dict) else None
            if isinstance(candidate, str) and entry.get("marker") == self._marker(segment, candidate):
                return candidate, entry.get("completed") is True
        return None

    def completed_marker(self, segment, candidate):
        saved = self.recover(segment)
        return self._marker(segment, candidate) if saved == (candidate, True) else ""

    def save(self, segment, candidate, *, completed, outcome="attempted"):
        with self.fragments.lock:
            journal = self.fragments.state.get("editorial_revisions")
            if not isinstance(journal, dict):
                journal = self.fragments.state["editorial_revisions"] = {}
            binding, family = self._marker(segment, ""), self._family(segment)
            families = self.fragments.state.get("editorial_families")
            if not isinstance(families, dict):
                families = self.fragments.state["editorial_families"] = {}
            previous = families.get(family)
            old = journal.get(previous) if isinstance(previous, str) else None
            if previous != binding and isinstance(old, dict) and old.get("family") == family:
                journal.pop(previous, None)
            families[family] = binding
            journal[binding] = {
                "candidate": candidate, "marker": self._marker(segment, candidate), "completed": completed,
                "outcome": outcome if completed else "pending", "family": family,
            }
            if self.fragments.path is not None:
                atomic_json(self.fragments.path, self.fragments.state)
