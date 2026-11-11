"""Conservative numeric-unit bindings, without interpreting arbitrary prose or codes."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

_SPACE = r"[ \u00a0\u202f\u2009]*"
_PRODUCT = _SPACE + r"[·⋅∙×*]?" + _SPACE
_PREFIXES = (("", ""), ("m", "м"), ("k", "к"), ("M", "М"), ("G", "Г"))


def _unit_patterns():
    # Long compound units precede their power prefix. Prefix case is significant.
    for prefix, ukrainian in _PREFIXES:
        yield prefix + "Wh", rf"{prefix}W{_PRODUCT}h|{ukrainian}Вт{_PRODUCT}год"
    for denominator, localized in (("h", "год"), ("s", "с"), ("min", "хв")):
        yield "m³/" + denominator, rf"[mм](?:³|\^?3){_SPACE}/{_SPACE}(?:{denominator}|{localized})"
    yield "m³", r"[mм](?:³|\^?3)"
    for prefix, ukrainian in _PREFIXES:
        for symbol, localized in (("W", "Вт"), ("V", "В"), ("Pa", "Па")):
            yield prefix + symbol, rf"{prefix}{symbol}|{ukrainian}{localized}"
    yield "bar", "bar|бар"


_UNITS = dict(enumerate(_unit_patterns()))
_UNIT = re.compile("|".join(f"(?P<u{index}>{pattern})" for index, (_, pattern) in _UNITS.items()))
_CUBIC = re.compile(r"[mм](?:³|\^?3)")
_COMPOUND_TAIL = re.compile(_SPACE + r"(?:[/·⋅∙×*•]|(?:per|на|за)\b)", re.IGNORECASE)


@dataclass(frozen=True)
class UnitSpan:
    start: int
    end: int
    canonical: str
    number: re.Match | None


def _checked_span(text, match, canonical, number_ends, protected, *, base_only=False):
    start, end = match.span()
    if start and (text[start - 1].isalpha() or text[start - 1] == "_"):
        return None
    if end < len(text) and (text[end].isalnum() or text[end] in "_^"):
        return None
    if not base_only and _COMPOUND_TAIL.match(text, end):
        return None
    if end + 1 < len(text) and text[end] in ".-–" and text[end + 1].isalnum():
        return None
    protected_end = end
    if base_only and end < len(text) and text[end] == "/":
        while protected_end < len(text) and not text[protected_end].isspace():
            protected_end += 1
    if any(left < protected_end and start < right for left, right, _ in protected):
        return None
    cursor = start
    while cursor and text[cursor - 1] in " \u00a0\u202f\u2009":
        cursor -= 1
    number = number_ends.get(cursor)
    if number and number.start() and (text[number.start() - 1].isalnum() or text[number.start() - 1] == "_"):
        return None
    if start and text[start - 1].isdigit() and number is None:
        return None
    return UnitSpan(start, end, canonical, number)


def unit_spans(text: str, numbers: re.Pattern, protected) -> list[UnitSpan]:
    """Recognize whole unit tokens, excluding identifiers and ambiguous compounds."""
    number_ends = {match.end(): match for match in numbers.finditer(text)}
    return [
        unit for match in _UNIT.finditer(text)
        if (unit := _checked_span(text, match, _UNITS[int(match.lastgroup[1:])][0], number_ends, protected))
    ]


def canonicalize_unit_exponents(text: str, numbers: re.Pattern, protected) -> str:
    """Remove no numeric evidence except the spelling of a recognized cubic unit."""
    number_ends = {match.end(): match for match in numbers.finditer(text)}
    for match in reversed(list(_CUBIC.finditer(text))):
        unit = _checked_span(text, match, "m³", number_ends, protected, base_only=True)
        if unit:
            text = text[:unit.start] + re.sub(r"\^?3", "³", match.group()) + text[unit.end:]
    return text


def quantity_bindings(text: str, numbers: re.Pattern, protected) -> dict[str, Counter]:
    bindings = defaultdict(Counter)
    for unit in unit_spans(text, numbers, protected):
        if unit.number is not None:
            value = re.sub(r"\s", "", unit.number.group()).replace(",", ".")
            bindings[value][unit.canonical] += 1
    return bindings


def standalone_unit(text: str, numbers: re.Pattern, protected) -> str | None:
    """A whole unit-only cell is verifiable without inferring a neighboring value."""
    start, end = len(text) - len(text.lstrip()), len(text.rstrip())
    units = unit_spans(text, numbers, protected)
    if len(units) == 1 and units[0].start == start and units[0].end == end:
        return units[0].canonical
    return None


def verified_unit_conflict(before, after, before_numbers, after_numbers) -> bool:
    # Every occurrence of a relevant coefficient must be bound on both sides.
    # A repeated value with a spelled-out/unknown unit cannot establish a conflict.
    return any(
        before[value] != after[value]
        and sum(before[value].values()) == before_numbers[value]
        == after_numbers[value] == sum(after[value].values())
        for value in before.keys() & after.keys()
    )
