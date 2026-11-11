"""Private paragraph fragments with reversible mappings to the original r/k stream."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from lxml import etree

from forma.core.models import FormaError, Segment, plain_text
from forma.persistence.files import atomic_json
from forma.translation.locks import lock_markup
from forma.translation.quality import NUMBERS, immutable_spans, parse_translation
from forma.translation.quantities import unit_spans

FRAGMENT_VERSION = 1
FRAGMENT_MARKUP_CHARACTERS = 6000
MAX_ATOMIC_MARKUP_CHARACTERS = 26000
MAX_FRAGMENT_PARTS = 128
MAX_FRAGMENT_DEPTH = 6
MAX_REJECTED_SNAPSHOTS = 3


class FragmentError(FormaError):
    def __init__(self):
        super().__init__("Не вдалося безпечно поділити фрагмент. Перегляньте звіт про проблемні фрагменти.")


@dataclass(frozen=True)
class Fragment:
    segment: Segment = field(repr=False)
    start: int
    end: int
    mappings: tuple[tuple[str, int, int], ...]
    fixed: bool


@dataclass(frozen=True)
class FragmentPlan:
    parent: Segment = field(repr=False)
    root: Segment = field(repr=False)
    root_offset: int
    limit: int
    source_hash: str
    context_hash: str
    fingerprint: str
    parts: tuple[Fragment, ...]

    def assemble(self, values, *, target_language=""):
        """Restore every original tag once; only split whitespace bypasses the model."""
        root = _tree(self.parent.source)
        by_tag = {node.tag: [] for node in root if node.tag.startswith("r")}
        total = len(self.parent.plain)
        previous = None
        spaced_target = bool(target_language) and target_language.lower().split("-")[0] not in {"zh", "ja", "th", "lo", "km", "my"}
        for part in self.parts:
            parsed = parse_translation(part.segment.source, values[part.segment.id])
            tags = [tag for tag in parsed if tag.startswith("r")]
            if part.start:
                for tag in tags:
                    parsed[tag] = parsed[tag].lstrip()
                    if parsed[tag]:
                        break
            if part.end < total:
                for tag in reversed(tags):
                    parsed[tag] = parsed[tag].rstrip()
                    if parsed[tag]:
                        break
            if previous is not None and previous.end == part.start and spaced_target:
                for tag, _, _ in reversed(previous.mappings):
                    if by_tag[tag] and by_tag[tag][-1][2]:
                        start, end, text = by_tag[tag][-1]
                        by_tag[tag][-1] = start, end, text + " "
                        break
            for tag, start, end in part.mappings:
                by_tag[tag].append((start, end, parsed[tag]))
            previous = part
        result = []
        for node in root:
            if node.tag.startswith("k"):
                result.append(f"<{node.tag}/>")
                continue
            source, cursor, chunks = node.text or "", 0, []
            for start, end, translated in sorted(by_tag[node.tag]):
                if start < cursor or source[cursor:start].strip():
                    raise FragmentError()
                chunks.extend((source[cursor:start], translated))
                cursor = end
            if source[cursor:].strip():
                raise FragmentError()
            chunks.append(source[cursor:])
            result.append(f"<{node.tag}>{escape(''.join(chunks))}</{node.tag}>")
        return "".join(result)


def _tree(source):
    parse_translation(source, source)
    return etree.fromstring(f"<root>{source}</root>".encode(), etree.XMLParser(resolve_entities=False, no_network=True))


def _positions(tree):
    positions, text, cursor = [], [], 0
    for node in tree:
        value = "\n" if node.tag.startswith("k") else node.text or ""
        positions.append((node.tag, value, cursor, cursor + len(value)))
        text.append(value)
        cursor += len(value)
    return positions, "".join(text)


def _slice(positions, start, end):
    parts, mappings = [], []
    for tag, value, first, last in positions:
        if first >= end or last <= start:
            continue
        if tag.startswith("k"):
            parts.append(f"<{tag}/>")
        else:
            left, right = max(0, start - first), min(len(value), end - first)
            parts.append(f"<{tag}>{escape(value[left:right])}</{tag}>")
            mappings.append((tag, left, right))
    return "".join(parts), tuple(mappings)


def _boundaries(text):
    protected = immutable_spans(text)
    intervals = sorted(
        [(start, end) for start, end, _ in protected]
        + [(match.start(), match.end()) for match in NUMBERS.finditer(text)]
        + [(unit.number.start() if unit.number is not None else unit.start, unit.end)
           for unit in unit_spans(text, NUMBERS, protected)]
    )
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    ends = [end for _, end in merged]
    result = []
    for match in re.finditer(r"\s+", text):
        index = bisect_left(ends, match.start() + 1)
        if index < len(merged) and merged[index][0] < match.end():
            continue
        if match.start() and match.end() < len(text):
            result.append((match.start(), match.end()))
    for match in re.finditer(r"[。！？]", text):
        cut = match.end()
        index = bisect_left(ends, cut + 1)
        if index < len(merged) and merged[index][0] < cut:
            continue
        if cut < len(text) and not text[cut].isspace():
            result.append((cut, cut))
    return sorted(set(result))


def _fixed(source):
    prose = plain_text(source)
    for begin, finish, _ in reversed(immutable_spans(prose)):
        prose = prose[:begin] + prose[finish:]
    return not any(char.isalpha() for char in prose)


def fragment_plan(parent, *, limit=FRAGMENT_MARKUP_CHARACTERS, root=None, root_offset=0):
    """Split at whitespace or CJK sentence endings, never inside tokens/values.

    Each model fragment retains original tag names. Whitespace at a split is
    restored locally, so trimmed responses cannot concatenate adjacent words.
    Unbroken atoms over the hard envelope are rejected rather than cut blindly.
    """
    locked = lock_markup(parent.source)[0]
    if len(locked) <= limit:
        return None
    tree = _tree(parent.source)
    positions, plain = _positions(tree)
    boundaries = _boundaries(plain)
    ranges, cursor = [], 0
    while cursor < len(plain):
        tail = _slice(positions, cursor, len(plain))[0]
        if len(lock_markup(tail)[0]) <= limit:
            ranges.append((cursor, len(plain)))
            break
        candidates = [(start, end) for start, end in boundaries if start > cursor]
        left, right, found = 0, len(candidates) - 1, -1
        while left <= right:
            middle = (left + right) // 2
            size = len(lock_markup(_slice(positions, cursor, candidates[middle][0])[0])[0])
            if size <= limit:
                found, left = middle, middle + 1
            else:
                right = middle - 1
        if found < 0:
            cut = candidates[0] if candidates else (len(plain), len(plain))
            atom = _slice(positions, cursor, cut[0])[0]
            if len(lock_markup(atom)[0]) > MAX_ATOMIC_MARKUP_CHARACTERS and not _fixed(atom):
                raise FragmentError()
        else:
            cut = candidates[found]
            # Prefer a complete sentence near the end of the available envelope.
            for candidate in reversed(candidates[:found + 1]):
                if plain[candidate[0] - 1] in ".!?;。！？" and candidate[0] - cursor >= (cut[0] - cursor) * 0.6:
                    cut = candidate
                    break
        if cut[0] <= cursor:
            raise FragmentError()
        ranges.append((cursor, cut[0]))
        cursor = cut[1]
        if len(ranges) > MAX_FRAGMENT_PARTS:
            raise FragmentError()
    if len(ranges) <= 1:
        return None
    root = root or parent
    root_plain = root.plain
    parts = []
    for start, end in ranges:
        source, mappings = _slice(positions, start, end)
        first, last = root_offset + start, root_offset + end
        sid = f"{root.id}::fragment:{first}:{last}:{hashlib.sha256(source.encode()).hexdigest()[:16]}"
        context = json.dumps({
            "context": root.context,
            "paragraph_before": root_plain[max(0, first - 700):first],
            "paragraph_after": root_plain[last:last + 700],
        }, ensure_ascii=False)
        segment = Segment(sid, root.location, source, context)
        parts.append(Fragment(segment, start, end, mappings, _fixed(source)))
    source_hash = hashlib.sha256(parent.source.encode()).hexdigest()
    context_hash = hashlib.sha256(root.context.encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps({
        "version": FRAGMENT_VERSION, "source": source_hash, "limit": limit, "ranges": ranges,
        "context": context_hash, "root_offset": root_offset,
    }, sort_keys=True).encode()).hexdigest()
    return FragmentPlan(parent, root, root_offset, limit, source_hash, context_hash, fingerprint, tuple(parts))


class FragmentCache:
    """Journal fragments under the existing private file checkpoint and lock."""

    def __init__(self, state=None, path=None, lock=None, *, plans=None):
        import threading

        self.state, self.path = state if state is not None else {}, path
        self.lock = lock if lock is not None else threading.RLock()
        self.plans = plans or {}
        self._origins, self._registered, self._counts = {}, set(), {}

    def plan(self, segment, *, limit=None):
        root, offset, depth = self._origins.get(segment.id, (segment, 0, 0))
        if depth >= MAX_FRAGMENT_DEPTH:
            return None
        source_hash = hashlib.sha256(segment.source.encode()).hexdigest()
        with self.lock:
            journal = self.state.get("fragment_stages", {})
            record = journal.get(segment.id, {}) if isinstance(journal, dict) else {}
            active = record.get("active") if isinstance(record, dict) else None
            records = record.get("plans", {}) if isinstance(record, dict) else {}
            previous = records.get(active, {}) if isinstance(records, dict) else {}
            if limit is None and isinstance(previous, dict) and previous.get("source_sha256") == source_hash:
                stored_limit = previous.get("limit")
                if type(stored_limit) is int and 128 <= stored_limit <= FRAGMENT_MARKUP_CHARACTERS:
                    limit = stored_limit
        limit = limit if limit is not None else FRAGMENT_MARKUP_CHARACTERS
        if limit < 128:
            return None
        ready = self.plans.get(segment.id) if depth == 0 else None
        if (ready is not None and ready.limit == limit and ready.source_hash == source_hash
                and ready.context_hash == hashlib.sha256(root.context.encode()).hexdigest()):
            plan = ready
        else:
            plan = fragment_plan(segment, limit=limit, root=root, root_offset=offset)
        if plan is None:
            return None
        with self.lock:
            registration = segment.id, plan.fingerprint
            if registration not in self._registered:
                count = self._counts.get(root.id, 1) + len(plan.parts) - 1
                if count > MAX_FRAGMENT_PARTS:
                    raise FragmentError()
                self._counts[root.id] = count
                self._registered.add(registration)
            for part in plan.parts:
                self._origins[part.segment.id] = root, offset + part.start, depth + 1
        return plan

    def load(self, plan, stage):
        with self.lock:
            journal = self.state.get("fragment_stages", {})
            parent = journal.get(plan.parent.id, {}) if isinstance(journal, dict) else {}
            plans = parent.get("plans", {}) if isinstance(parent, dict) else {}
            entry = plans.get(plan.fingerprint, {}) if isinstance(plans, dict) else {}
            values = entry.get(stage, {}) if isinstance(entry, dict) else {}
            return dict(values) if isinstance(values, dict) else {}

    def save(self, plan, stage, values):
        with self.lock:
            entry = self._entry(plan)
            saved = entry.get(stage)
            if not isinstance(saved, dict):
                saved = entry[stage] = {}
            saved.update(values)
            if self.path is not None:
                atomic_json(self.path, self.state)

    def _entry(self, plan):
        journal = self.state.get("fragment_stages")
        if not isinstance(journal, dict):
            journal = self.state["fragment_stages"] = {}
        parent = journal.get(plan.parent.id)
        if not isinstance(parent, dict):
            parent = journal[plan.parent.id] = {}
        plans = parent.get("plans")
        if not isinstance(plans, dict):
            plans = parent["plans"] = {}
        entry = plans.get(plan.fingerprint)
        if not isinstance(entry, dict):
            entry = plans[plan.fingerprint] = {}
        entry.update(source_sha256=plan.source_hash, version=FRAGMENT_VERSION, limit=plan.limit,
                     parts=[part.segment.id for part in plan.parts])
        parent["active"] = plan.fingerprint
        return entry

    def feedback(self, plan, stage):
        with self.lock:
            entry = self._entry(plan)
            raw = entry.get("paragraph_feedback", {})
            values = raw.get(stage, []) if isinstance(raw, dict) else []
            return [value for value in values[:30] if isinstance(value, str) and len(value) <= 1000] if isinstance(values, list) else []

    def reject(self, plan, stage, issues):
        """Keep rejected paid work as history, never as an active reusable final."""
        with self.lock:
            self._entry(plan)
            journal, pending, seen = self.state["fragment_stages"], [(plan.parent.id, plan.fingerprint)], set()
            messages = list(dict.fromkeys(issue.message for issue in issues))
            traversed = 0
            while pending and traversed < MAX_FRAGMENT_PARTS * MAX_FRAGMENT_DEPTH:
                traversed += 1
                parent_id, fingerprint = pending.pop()
                if (parent_id, fingerprint) in seen:
                    continue
                seen.add((parent_id, fingerprint))
                parent = journal.get(parent_id, {})
                plans = parent.get("plans", {}) if isinstance(parent, dict) else {}
                entry = plans.get(fingerprint) if isinstance(plans, dict) else None
                if not isinstance(entry, dict):
                    continue
                previous = entry.get(stage)
                history = entry.get("rejected")
                if not isinstance(history, list):
                    history = entry["rejected"] = []
                if isinstance(previous, dict) and previous:
                    history.append({"stage": stage, "values": previous, "issues": messages})
                    count = entry.get("rejection_count", 0)
                    entry["rejection_count"] = min(count + 1, 2**31 - 1) if type(count) is int and count >= 0 else 1
                entry["rejected"] = history[-MAX_REJECTED_SNAPSHOTS:]
                entry[stage] = {}
                feedback = entry.get("paragraph_feedback")
                if not isinstance(feedback, dict):
                    feedback = entry["paragraph_feedback"] = {}
                feedback[stage] = messages
                parts = entry.get("parts", [])
                for child_id in parts[:MAX_FRAGMENT_PARTS] if isinstance(parts, list) else []:
                    child = journal.get(child_id, {}) if isinstance(child_id, str) else {}
                    if isinstance(child, dict) and isinstance(child.get("active"), str):
                        pending.append((child_id, child["active"]))
            if self.path is not None:
                atomic_json(self.path, self.state)

    def accept(self, plan, stage):
        with self.lock:
            entry = self._entry(plan)
            feedback = entry.get("paragraph_feedback")
            if isinstance(feedback, dict) and feedback.pop(stage, None) and self.path is not None:
                atomic_json(self.path, self.state)
