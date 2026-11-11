"""Conservative source candidates for a new bilingual edit, never direct reuse.

The index contains one project/policy pool supplied by TranslationMemory. A
returned target is only a draft reference: the caller must edit it against the
complete current source/context and apply normal final export validation.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from forma.core.models import FormaError, plain_text
from forma.memory.store import MemoryHit, exact_fingerprint
from forma.translation.quality import parse_translation

MIN_VISIBLE_CHARS = 80
MAX_VISIBLE_CHARS = 8000
MIN_WORDS = 8
MAX_WORDS = 1000
MIN_SIMILARITY = 0.90
MAX_CHANGED_SOURCE_FRACTION = 0.15
MAX_SHORTLIST = 32
MAX_QUERY_TERMS = 32
MAX_MARKUP_CHARS = 48000
MAX_MATCHER_PRODUCT = 8_000_000
AMBIGUITY_EPSILON = 1e-6

_WORDS = re.compile(r"\w+", re.UNICODE)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class _Source:
    text: str
    words: frozenset[str]
    markers: tuple[str, ...]


def _source(markup) -> _Source | None:
    if not isinstance(markup, str) or len(markup) > MAX_MARKUP_CHARS:
        return None
    text = " ".join(plain_text(markup).split()).casefold()
    words = _WORDS.findall(text)
    unique = frozenset(words)
    if not MIN_VISIBLE_CHARS <= len(text) <= MAX_VISIBLE_CHARS or not MIN_WORDS <= len(words) <= MAX_WORDS:
        return None
    # Long strings consisting almost entirely of repeated tokens can make a
    # character matcher quadratic while offering little semantic evidence.
    if len(words) > 100 and len(unique) < len(words) * 0.1:
        return None
    try:
        markers = tuple(parse_translation(markup, markup))
    except (FormaError, TypeError, ValueError, UnicodeError):
        return None
    return _Source(text, unique, markers)


def eligible_source(source_markup) -> bool:
    """Cheap shared gate before the caller loads any project candidate pool."""
    return _source(source_markup) is not None


def _context_digest(context) -> str | None:
    if not isinstance(context, str) or len(context) > 65536:
        return None
    try:
        value = json.loads(context)
    except (ValueError, RecursionError):
        return None
    digest = value.get("full_context_sha256") if isinstance(value, dict) else None
    return digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) else None


def _score(left: str, right: str) -> tuple[float, float] | None:
    if left == right:
        return 1.0, 0.0
    total = len(left) + len(right)
    if 2 * min(len(left), len(right)) / total < MIN_SIMILARITY:
        return None
    prefix = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
    suffix = 0
    limit = min(len(left), len(right)) - prefix
    while suffix < limit and left[len(left) - suffix - 1] == right[len(right) - suffix - 1]:
        suffix += 1
    a = left[prefix:len(left) - suffix if suffix else len(left)]
    b = right[prefix:len(right) - suffix if suffix else len(right)]
    if len(a) * len(b) > MAX_MATCHER_PRODUCT:
        return None
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    matched = prefix + suffix + sum(block.size for block in matcher.get_matching_blocks())
    ratio = 2 * matched / total
    old_changes = new_changes = 0
    for operation, first, last, other_first, other_last in matcher.get_opcodes():
        if operation != "equal":
            old_changes += last - first
            new_changes += other_last - other_first
    changed = max(old_changes, new_changes) / max(len(left), len(right))
    return ratio, changed


class MemoryCandidateIndex:
    """Reusable lexical inverted index; at most 32 expensive comparisons/query."""

    def __init__(self, hits):
        self._entries: list[tuple[MemoryHit, _Source]] = []
        self._postings: dict[tuple, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        self._contexts: dict[str, set[int]] = defaultdict(set)
        self._sources: dict[str, set[int]] = defaultdict(set)
        self._seen_entry_ids: set[int] = set()
        for hit in hits:
            self.add(hit)

    def add(self, hit: MemoryHit) -> None:
        """Append a newly completed final; callers serialize shared find/add access."""
        if hit.entry_id in self._seen_entry_ids or hit.status not in {"human_approved", "machine_final"}:
            return
        source = _source(hit.source_markup)
        if source is None:
            return
        try:
            if exact_fingerprint(hit.policy, hit.source_markup, hit.context, hit.role) != hit.entry_key:
                return
            parse_translation(hit.source_markup, hit.target)
        except (FormaError, TypeError, ValueError, UnicodeError):
            return
        self._seen_entry_ids.add(hit.entry_id)
        index = len(self._entries)
        self._entries.append((hit, source))
        self._sources[hit.source_markup].add(index)
        context = _context_digest(hit.context)
        if context is not None:
            self._contexts[context].add(index)
        postings = self._postings[(hit.role, source.markers)]
        for word in source.words:
            postings[word].add(index)

    def find(self, source_markup, role, *, context=None) -> MemoryHit | None:
        source = _source(source_markup)
        if source is None:
            return None
        postings = self._postings.get((role, source.markers))
        if not postings:
            return None
        allowed = None
        if context is not None:
            digest = _context_digest(context)
            if digest is None:
                return None
            allowed = self._contexts.get(digest)
            if not allowed:
                return None
        excluded = self._sources.get(source_markup, ())
        scoped_postings = {}
        for word in source.words:
            if word not in postings:
                continue
            members = postings[word] if allowed is None else postings[word] & allowed
            members = members.difference(excluded) if excluded else members
            if members:
                scoped_postings[word] = members
        terms = sorted(scoped_postings, key=lambda word: (len(scoped_postings[word]), word))[:MAX_QUERY_TERMS]
        counts = Counter()
        for word in terms:
            counts.update(scoped_postings[word])
        # Refine a bounded lexical shortlist before character matching. Rarity
        # ranking avoids scanning every stored source for each current paragraph.
        rough = counts.most_common(MAX_SHORTLIST * 4)
        ranked = sorted(
            (index for index, _count in rough),
            key=lambda index: (
                len(source.words & self._entries[index][1].words) / len(source.words | self._entries[index][1].words),
                counts[index],
                self._entries[index][0].status == "human_approved",
                self._entries[index][0].entry_id,
            ),
            reverse=True,
        )[:MAX_SHORTLIST]
        candidates = []
        for index in ranked:
            hit, previous = self._entries[index]
            score = _score(previous.text, source.text)
            if score is None:
                continue
            similarity, changed = score
            if similarity >= MIN_SIMILARITY and changed <= MAX_CHANGED_SOURCE_FRACTION:
                candidates.append((similarity, hit.status == "human_approved", hit.entry_id, hit))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:3], reverse=True)
        best = candidates[0]
        if any(abs(candidate[0] - best[0]) <= AMBIGUITY_EPSILON and candidate[3].target != best[3].target for candidate in candidates[1:]):
            return None
        return best[3]
