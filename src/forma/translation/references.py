"""Conservative consistency checks for explicit appendix headings in Word.

Labels belong to the document: this check neither transliterates nor renumbers
them. Only unambiguous heading pairs participate; prose, ranges and subappendices
are outside its scope. Findings are warnings and never trigger provider calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass

from forma.core.models import Issue, plain_text

REFERENCE_CODES = frozenset({"reference_consistency", "reference_collision"})
_LETTER = r"(?:(?i:shch|zh|kh|ts|ch|sh)|[A-Za-zА-Яа-яЁёЇїІіЄєҐґ])"
_KEYWORDS = r"appendix|annexure|annexe|annex|додаток|приложение|załącznik|anhang|anlage|anexo"
_HEADING = re.compile(
    rf"^\s*(?i:{_KEYWORDS})\s+({_LETTER})(?!\w)"
)
_OTHER_REFERENCE = re.compile(rf"(?i:{_KEYWORDS})\s+{_LETTER}(?!\w)")
_SUBLABEL = rf"(?:\d+|{_LETTER}|[ivxlcdmIVXLCDM]+)(?!\w)"
_DASHES = r"\-‐‑‒–—−"
_COMPOSITE = re.compile(rf"^(?:[./{_DASHES}]\w|\s*[{_DASHES}/.,]\s*{_SUBLABEL})")
_ALTERNATIVE = re.compile(
    rf"^\s+(?i:or|and|або|та|і|й|или|и|et|ou|y|o|und|oder|i|lub)\s+{_LETTER}(?!\w)"
)
# Printed Cyrillic/Latin lookalikes are equivalent for this narrow label check.
# Do not apply transliteration: Г/D and Д/D can be a real target collision.
_LOOKALIKES = str.maketrans("АВЕІКМНОРСТХ", "ABEIKMHOPCTX")


@dataclass(frozen=True)
class ReferenceFinding:
    issue: Issue
    context_fingerprint: str


def _label(text, *, source=False):
    text = plain_text(text)
    match = _HEADING.match(text)
    if not match:
        return ""
    tail = text[match.end():]
    if _COMPOSITE.match(tail) or _ALTERNATIVE.match(tail) or _OTHER_REFERENCE.search(tail):
        return ""
    # A lower-case continuation without heading punctuation is usually prose
    # ("Appendix A contains ..."), not an independent appendix heading.
    if source and tail[:1].isspace() and tail.lstrip()[:1].islower():
        return ""
    return match.group(1).upper().translate(_LOOKALIKES)


def document_reference_findings(segments, translations):
    """Return one finding per inconsistent source label or colliding target.

    Dependency fingerprints bind acknowledgements to every participating heading,
    so an edit to a different reference cannot leave a stale approval hidden.
    """
    by_source, by_target = defaultdict(list), defaultdict(list)
    for segment in segments:
        part, separator, _ = segment.id.partition("#")
        if not separator or not part.startswith("word/"):
            continue
        source_label = _label(segment.source, source=True)
        target = translations.get(segment.id, "")
        target_label = _label(target)
        if not source_label or not target_label:
            continue
        item = (segment.id, segment.source, target, source_label, target_label)
        by_source[part, source_label].append(item)
        by_target[part, target_label].append(item)

    def finding(records, code, message):
        fingerprint = hashlib.sha256(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        return ReferenceFinding(Issue(code, message, records[0][0], "warning"), fingerprint)

    findings = []
    for records in by_source.values():
        if len({item[4] for item in records}) > 1:
            findings.append(finding(
                records, "reference_consistency",
                "Один додаток має різні позначення у перекладі. Узгодьте його позначення у змісті та заголовках.",
            ))
    for records in by_target.values():
        if len({item[3] for item in records}) > 1:
            findings.append(finding(
                records, "reference_collision",
                "Позначення різних додатків збігаються або виглядають однаково. Перевірте зміст і заголовки.",
            ))
    return findings


def check_document_references(segments, translations):
    return [finding.issue for finding in document_reference_findings(segments, translations)]
