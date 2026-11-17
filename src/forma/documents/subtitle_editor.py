"""Map visible subtitle edits to existing runs without treating style tags as lines."""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from forma.core.models import FormaError
from forma.documents.subtitles import (
    _PLAN,
    MAX_CUE_CHARACTERS,
    MAX_CUE_SOURCE_CHARACTERS,
    _bounded_keys,
    candidate_issues,
    caption_plain_text,
)
from forma.translation.quality import parse_translation

_FORMAT = "Редактуру не вдалося безпечно розподілити між стилями фрагмента."
_LINES = "Збережіть кількість переносів рядка: вони позначають наявні табуляції, переноси або інші службові елементи документа."


def _keep_whitespace(bindings, result, pinned):
    """Move diff insertions out of immutable whitespace into adjacent text runs."""
    for index, (key, _) in enumerate(bindings):
        if key not in pinned or result[key] == pinned[key]:
            continue
        value, fixed = result[key], pinned[key]
        left = next((tag for tag, _ in reversed(bindings[:index]) if tag not in pinned), None)
        right = next((tag for tag, _ in bindings[index + 1:] if tag not in pinned), None)
        occurrences = [match.start() for match in re.finditer(re.escape(fixed), value)] if fixed else []
        for offset in occurrences:
            prefix, suffix = value[:offset], value[offset + len(fixed):]
            if prefix and left is None or suffix and right is None:
                continue
            if prefix:
                result[left] += prefix
            if suffix:
                result[right] = suffix + result[right]
            result[key] = fixed
            break
        else:
            raise FormaError(_FORMAT)
    return result


def edit_caption_markup(segment, original_markup, edited_plain):
    """Preserve physical/display breaks, tabs, zero-width anchors and empty runs."""
    if (not isinstance(edited_plain, str) or len(edited_plain) > MAX_CUE_CHARACTERS
            or not isinstance(original_markup, str) or len(original_markup) > MAX_CUE_SOURCE_CHARACTERS * 4
            or not isinstance(segment.source, str) or len(segment.source) > MAX_CUE_SOURCE_CHARACTERS * 4):
        raise FormaError("Текст редактури завеликий або має некоректний формат.")
    plan = _PLAN.fullmatch(segment.id)
    if plan is None:
        raise FormaError("Не вдалося безпечно зіставити текст зі структурою документа.")
    try:
        source = parse_translation(segment.source, segment.source)
        original = parse_translation(segment.source, original_markup)
    except UnicodeError as exc:
        raise FormaError("Переклад містить некоректні символи Unicode.") from exc
    if not _bounded_keys(source) or not _bounded_keys(original):
        raise FormaError(_FORMAT)
    edited_plain = edited_plain.replace("\r\n", "\n").replace("\r", "\n")
    if edited_plain == caption_plain_text(segment, original_markup) and not candidate_issues(segment, original_markup):
        return original_markup
    breaks = int(plan.group(3) or plan.group(1), 16)
    tabs = int(plan.group(4) or "0", 16)
    pinned = {key: value for key, value in source.items() if key.startswith("r") and not value.strip()}
    values = {key: value if key in pinned else "" for key, value in source.items() if key.startswith("r")}
    zones, boundaries = [[]], []
    for key in source:
        if key.startswith("r"):
            # Empty structural gaps have no diff ownership. Real source runs
            # remain eligible even when their current translation is empty.
            if key not in pinned or pinned[key]:
                zones[-1].append((key, pinned.get(key, original[key])))
        elif breaks & (1 << int(key[1:])) or tabs & (1 << int(key[1:])):
            boundaries.append("\n" if breaks & (1 << int(key[1:])) else "\t")
            zones.append([])
    pieces = re.split(r"([\n\t])", edited_plain)
    if pieces[1::2] != boundaries:
        raise FormaError(_LINES)
    # Kept lazy: the general editor dispatches here, and importing its diff
    # helper at module import time would make that relationship cyclic.
    from forma.documents.editor import _edit_zone

    for bindings, edited in zip(zones, pieces[::2], strict=True):
        result = _keep_whitespace(bindings, _edit_zone(bindings, edited), pinned)
        if any(key in pinned and value != pinned[key] for key, value in result.items()):
            raise FormaError(_FORMAT)
        values.update(result)
    translated = "".join(f"<{key}>{escape(values[key])}</{key}>" if key.startswith("r") else f"<{key}/>" for key in source)
    try:
        parse_translation(segment.source, translated)
        issues = candidate_issues(segment, translated)
    except UnicodeError as exc:
        raise FormaError("Переклад містить некоректні символи Unicode.") from exc
    if issues:
        raise FormaError(issues[0].message)
    if caption_plain_text(segment, translated) != edited_plain:
        raise FormaError(_FORMAT)
    return translated
