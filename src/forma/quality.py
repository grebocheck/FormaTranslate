from __future__ import annotations

import re
from collections import Counter

from lxml import etree

from forma.models import FormaError, Issue, plain_text

# Units and adjacent formatting runs need not be separated by whitespace (25kW,
# 10мм, or 999</r0><r1>pump). Word boundaries silently hid changed values there.
NUMBERS = re.compile(r"(?<!\d)[+-]?\d+(?:[.,]\d+)*(?:\s?%)?(?!\d)")
PROTECTED = re.compile(
    r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.\w+|\{\{[^{}]+\}\}|\{[\w.:-]+\}|"
    r"%\([^)]+\)[a-z]|\$\{[^{}]+\}|[\w.\-–]+\.(?:docx|xlsx|pdf|pptx|csv|txt)\b"
)
CODE_CANDIDATES = re.compile(r"\b[A-ZА-ЯІЇЄҐ0-9]+(?:[-_./,][A-ZА-ЯІЇЄҐ0-9]+)*\b")


def immutable_spans(text: str) -> list[tuple[int, int, str]]:
    spans = [(m.start(), m.end(), m.group()) for m in PROTECTED.finditer(text)]
    for match in CODE_CANDIDATES.finditer(text):
        value = match.group()
        if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
            continue
        if not value[0].isalpha() and not re.search(r"\d+[A-ZА-ЯІЇЄҐ]+\d", value):
            continue
        if not any(start <= match.start() < end for start, end, _ in spans):
            spans.append((match.start(), match.end(), value))
    return sorted(spans)


def _markup(value: str) -> etree._Element:
    if "<!" in value or "<?" in value:
        raise FormaError("Відповідь містить недопустиму розмітку.")
    try:
        return etree.fromstring(
            f"<root>{value}</root>".encode(),
            etree.XMLParser(resolve_entities=False, no_network=True),
        )
    except etree.XMLSyntaxError as exc:
        raise FormaError("Модель повернула пошкоджені позначки форматування.") from exc


def parse_translation(source: str, translated: str) -> dict[str, str]:
    if not isinstance(translated, str) or len(translated) > max(20000, len(source) * 12):
        raise FormaError("Некоректний розмір відповіді моделі.")
    original, result = _markup(source), _markup(translated)
    expected = [n.tag for n in original]
    actual = [n.tag for n in result]
    if expected != actual or len(actual) != len(set(actual)):
        raise FormaError("Модель змінила порядок або кількість позначок форматування.")
    if (result.text or "").strip():
        raise FormaError("Модель повернула текст поза фрагментами документа.")
    values = {}
    for node in result:
        if node.attrib or len(node) or (node.tail or "").strip():
            raise FormaError("Модель змінила структуру форматування фрагмента.")
        if not re.fullmatch(r"[rk]\d+", node.tag):
            raise FormaError("Модель додала невідому позначку форматування.")
        if node.tag.startswith("k") and (node.text or ""):
            raise FormaError("Модель змінила службовий елемент документа.")
        values[node.tag] = node.text or ""
    if plain_text(source).strip() and not plain_text(translated).strip():
        raise FormaError("Модель повернула порожній переклад.")
    return values


def protected_values(text: str) -> Counter:
    return Counter(value for _, _, value in immutable_spans(text))


def _unchanged_notation(text: str) -> bool:
    """Recognize compact notation that does not by itself need translation.

    This affects only the unchanged-text warning. It neither grants permission to
    change identifiers/numbers nor treats arbitrary uppercase words or names as safe.
    """
    text = text.strip()
    unquoted = text
    if len(text) >= 2 and (text[0], text[-1]) in {("«", "»"), ('"', '"'), ("'", "'"), ("`", "`")}:
        unquoted = text[1:-1].strip()
        if "\n" not in unquoted and re.fullmatch(
            r"[^<>]{1,260}\.(?:docx|xlsx|pdf|pptx|csv|txt)", unquoted, re.IGNORECASE
        ):
            return True
        if len(unquoted) <= 80 and re.fullmatch(r"[^\W\d_]+[-_]\d+(?:[-_/]\d+)*", unquoted):
            return True

    # Keep superscript exponents out of words (Python's \w includes e.g. ³).
    words = re.findall(r"[^\W\d_]+", "".join(c if c.isalpha() else " " for c in text))
    if not words:
        return True
    # International financial/engineering notation seen unchanged in real reports.
    # Other words/acronyms (including untranslated Cyrillic glossary keys) still warn.
    abbreviations = {"CAPEX", "OPEX", "NPV", "IRR", "SCADA", "PF", "DPF", "THD"}
    cyrillic_units = {"кВт", "МВт", "год", "м", "кВ", "Вт", "кВА", "квар"}
    units = {"kW", "MW", "kWh", "MWh", "kVA"} | cyrillic_units
    formula = any(operator in text for operator in "=×÷Σ∑")
    if cyrillic_units.intersection(words) and not formula:
        # Standalone units may need localization (кВт → kW, год → h).
        # Also prevent the model-code heuristic below from hiding '25 м / 30 м'.
        return False

    def math_atom(word):
        return word in abbreviations | units | {"sin", "cos", "tan", "log", "ln", "Σ"} or bool(
            re.fullmatch(r"[A-Za-zΑ-Ωα-ω](?:[ij]|[а-яієїґ]{1,5})?", word.lstrip("Σ∑Δ"))
        )

    if all(math_atom(word) for word in words) and (len(words) <= 3 or formula):
        return True

    # Spaced model/serial strings are not always covered by immutable_spans:
    # '250 QVD 570-54', 'Д 6300/27-3', 'Bosch GLM 80, № …'. Requiring numeric
    # components and compact code-like words avoids suppressing ordinary headings.
    if len(text) > 100:
        return False
    numbers = re.findall(r"\d+", text)
    if not numbers:
        return False
    serial = "№" in text or "#" in text
    compact = all(
        len(word) <= 1
        or (
            word.isupper()
            and (len(word) <= 3 or (len(word) <= 5 and not re.search(r"[AEIOUYАЕЄИІЇОУЮЯ]", word)))
        )
        or (len(word) <= 4 and word[:-1].isupper() and word[-1].islower())
        or (serial and (word.istitle() or (len(word) <= 5 and word.isupper())))
        for word in words
    )
    single_model = (
        len(words) == 1 and words[0].isupper() and not re.search(r"[AEIOUYАЕЄИІЇОУЮЯ]", words[0])
    )
    return compact and (len(numbers) >= 2 or single_model)


def numeric_values(text: str) -> Counter:
    # Different decimal separators are allowed, while the digits must be retained.
    normalized = text.replace("−", "-")
    return Counter(re.sub(r"\s", "", n).replace(",", ".") for n in NUMBERS.findall(normalized))


def numbers_match(source: str, translated: str) -> bool:
    before, after = numeric_values(source), numeric_values(translated)
    ordinals = {
        1: ("first", "one"),
        2: ("second", "two"),
        3: ("third", "three"),
        4: ("fourth", "four"),
        5: ("fifth", "five"),
        6: ("sixth", "six"),
        7: ("seventh", "seven"),
        8: ("eighth", "eight"),
        9: ("ninth", "nine"),
        10: ("tenth", "ten"),
        11: ("eleventh", "eleven"),
        12: ("twelfth", "twelve"),
    }
    for number, count in Counter(
        re.findall(r"\b(\d+)-(?:му|го|ї|й|ій|ий|е|а|ою)\b", source)
    ).items():
        missing = max(0, before[number] - after[number])
        words = ordinals.get(int(number), ())
        equivalents = sum(
            len(re.findall(rf"\b{word}\b", translated, re.IGNORECASE)) for word in words
        )
        equivalents += len(re.findall(rf"\b{number}(?:st|nd|rd|th)\b", translated, re.IGNORECASE))
        after[number] += min(count, missing, equivalents)
    for hour in re.findall(r"\b(\d{1,2})-ї\s+годин", source):
        if re.search(rf"\b{hour}:00\b", translated) and after["00"] > before["00"]:
            after["00"] -= 1
    return +before == +after


def _word_repetitions(text: str, *, candidate: bool) -> int:
    for start, end, _ in reversed(immutable_spans(text)):
        text = text[:start] + "|" * (end - start) + text[end:]
    pattern = r"(?<!\w)(?P<word>[^\W\d_]+)[ \t‐‑–-]+(?P<again>(?P=word))(?!\w)"
    count = 0
    for match in re.finditer(pattern, text, re.IGNORECASE):
        word, again = match.group("word", "again")
        if candidate and (len(word) < 5 or word.isupper() or (word.istitle() and again.istitle())):
            # Short grammatical words, unit symbols, acronyms and proper names
            # frequently repeat legitimately. Prefer missing a case to noisy warnings.
            continue
        count += 1
    return count


def _long_repetitions(text: str, *, minimum_chars: int = 80, anchor_size: int = 8) -> int:
    """Count adjacent exact copies, bounded to 256 tokens per repeated fragment."""
    tokens = re.findall(r"\w+|[^\w\s]", text.casefold())
    anchors, count, consumed = {}, 0, 0
    for index in range(max(0, len(tokens) - anchor_size + 1)):
        if index < consumed:
            continue
        anchor = tuple(tokens[index : index + anchor_size])
        previous = anchors.get(anchor)
        anchors[anchor] = index
        if previous is None:
            continue
        width = index - previous
        if not anchor_size <= width <= 256:
            continue
        fragment = tokens[previous:index]
        if sum(map(len, fragment)) < minimum_chars or tokens[index : index + width] != fragment:
            continue
        end = index
        while tokens[end : end + width] == fragment:
            count += 1
            end += width
        consumed, anchors = end, {}
    return count


def introduced_repetition(source: str, translated: str) -> bool:
    """A conservative editorial warning, never an instruction to remove source text.

    Count source repetitions as well, including shorter words in another language.
    Existing repetition is not evidence of a translation error. Numbers and punctuation
    remain part of long-fragment matching so similar measurements are not duplicates.
    """
    if source.strip() == translated.strip():
        return False
    # A source-language phrase may be shorter than its translated equivalent.
    return _word_repetitions(translated, candidate=True) > _word_repetitions(
        source, candidate=False
    ) or _long_repetitions(translated) > _long_repetitions(source, minimum_chars=30, anchor_size=3)


def check_segment(source: str, translated: str, segment_id: str = "") -> list[Issue]:
    try:
        parse_translation(source, translated)
    except FormaError as exc:
        return [Issue("formatting", str(exc), segment_id, "error")]
    before, after = plain_text(source), plain_text(translated)
    issues = []
    if not numbers_match(before, after):
        issues.append(
            Issue("numbers", "У перекладі змінилися числа або відсотки.", segment_id, "error")
        )
    if protected_values(before) != protected_values(after):
        issues.append(
            Issue("protected_values", "Змінилися адреси, коди або змінні.", segment_id, "error")
        )
    unprotected = before
    for start, end, _ in reversed(immutable_spans(before)):
        unprotected = unprotected[:start] + unprotected[end:]
    if (
        before.strip() == after.strip()
        and any(c.isalpha() for c in unprotected)
        and not _unchanged_notation(before)
    ):
        issues.append(
            Issue(
                "unchanged",
                "Текст залишився без змін; перевірте, чи потрібен переклад.",
                segment_id,
            )
        )
    if introduced_repetition(before, after):
        issues.append(
            Issue(
                "repeated_text",
                "У перекладі з’явився повтор слова або фрагмента. Перевірте, чи це не помилка редактури.",
                segment_id,
            )
        )
    if len(after) > max(90, len(before) * 2.5):
        issues.append(
            Issue(
                "expansion", "Переклад значно довший: перевірте переноси та розміщення.", segment_id
            )
        )
    elif len(before) > 80 and len(after) < len(before) * 0.3:
        issues.append(
            Issue(
                "short_translation", "Переклад значно коротший: можлива втрата змісту.", segment_id
            )
        )
    return issues


def check_language(
    source: str, translated: str, source_language: str, target_language: str, segment_id: str = ""
) -> list[Issue]:
    """Conservative script check, not language identification or a quality score.

    Only explicit Latin/Cyrillic language pairs are checked. Bibliographies, official
    names and foreign quotations can legitimately remain: script evidence only warns.
    """
    latin = {
        "en",
        "de",
        "fr",
        "es",
        "it",
        "pt",
        "pl",
        "cs",
        "sk",
        "nl",
        "sv",
        "da",
        "fi",
        "ro",
        "tr",
    }
    cyrillic = {"uk", "ru", "bg", "be", "mk"}
    source_lang, target_lang = (
        source_language.lower().split("-")[0],
        target_language.lower().split("-")[0],
    )
    if source_lang in cyrillic and target_lang in latin:
        pattern = r"[А-Яа-яІіЇїЄєҐґЁё]{2,}"
    elif source_lang in latin and target_lang in cyrillic:
        pattern = r"[A-Za-zÀ-ž]{2,}"
    else:
        return []
    before, after = plain_text(source), plain_text(translated)
    prose = after
    for start, end, _ in reversed(immutable_spans(after)):
        prose = prose[:start] + prose[end:]
    words = re.findall(pattern, prose)
    if len(words) < 8 or sum(map(len, words)) < 45:
        return []
    unchanged = before.strip() == after.strip()
    return [
        Issue(
            "untranslated_prose" if unchanged else "remaining_source_text",
            "Абзац залишився мовою оригіналу."
            if unchanged
            else "Частина тексту може залишатися мовою оригіналу; перевірте цей фрагмент.",
            segment_id,
            "warning",
        )
    ]
