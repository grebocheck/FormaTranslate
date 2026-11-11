"""Local interface catalogs. Document content and API requests are never translated here."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from functools import lru_cache
from string import Formatter
from threading import RLock
from types import MappingProxyType

from PySide6.QtCore import QLocale, QTranslator

from forma.core.resources import resource_path

_LANGUAGES = {
    "uk": "Українська", "en": "English", "pl": "Polski", "de": "Deutsch",
    "fr": "Français", "es": "Español",
}
SUPPORTED_LANGUAGES = frozenset(_LANGUAGES)
_LOCK = RLock()
_language = "uk"
_FORMATTER = Formatter()
_TRANSLATIONS: OrderedDict[tuple[str, str], str] = OrderedDict()


def _cached(language: str, source: str) -> str | None:
    with _LOCK:
        key = language, source
        value = _TRANSLATIONS.get(key)
        if value is not None:
            _TRANSLATIONS.move_to_end(key)
        return value


def _remember(language: str, source: str, translated: str) -> str:
    with _LOCK:
        _TRANSLATIONS[language, source] = translated
        _TRANSLATIONS.move_to_end((language, source))
        if len(_TRANSLATIONS) > 4096:
            _TRANSLATIONS.popitem(last=False)
    return translated


def _system_locale_name() -> str:
    return QLocale.system().name()


def resolve_language(choice: str) -> str:
    value = _system_locale_name() if choice == "system" else choice
    language = str(value).lower().replace("-", "_").split("_")[0]
    return language if language in _LANGUAGES else "en"


def set_language(choice: str) -> str:
    global _language
    language = resolve_language(choice)
    with _LOCK:
        _language = language
    return language


def current_language() -> str:
    with _LOCK:
        return _language


def available_languages() -> list[dict[str, str]]:
    return [
        {"code": "system", "label": translate("Як у системі")},
        *({"code": code, "label": label} for code, label in _LANGUAGES.items()),
    ]


@lru_cache(maxsize=1)
def _catalogs():
    folder = resource_path("locales")
    catalogs = {}
    for language in _LANGUAGES:
        entries = {}
        # UI wording takes precedence where a short label is also a backend message.
        paths = [*sorted(folder.glob(f"{language}_*.json")), folder / f"{language}.json"]
        for path in paths:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in data.items()
            ):
                raise ValueError(f"Invalid interface catalog: {path.name}")
            entries.update(data)
        catalogs[language] = MappingProxyType(entries)
    return MappingProxyType(catalogs)


@lru_cache(maxsize=1)
def _source_keys() -> frozenset[str]:
    return frozenset(key for catalog in _catalogs().values() for key in catalog)


@lru_cache(maxsize=1)
def _patterns() -> tuple[tuple[re.Pattern, str], ...]:
    patterns = []
    for source in _source_keys():
        pieces, fields, literal_length = [], set(), 0
        try:
            for literal, field, format_spec, conversion in _FORMATTER.parse(source):
                pieces.append(re.escape(literal))
                literal_length += len(literal)
                if field is None:
                    continue
                if not re.fullmatch(r"[A-Za-z_]\w*", field) or format_spec or conversion:
                    break
                pieces.append(f"(?P={field})" if field in fields else f"(?P<{field}>[\\s\\S]*?)")
                fields.add(field)
            else:
                if fields and literal_length:
                    patterns.append((literal_length, source, re.compile("".join(pieces))))
        except ValueError:
            continue
    # A specific status wins over a broader template, deterministically.
    return tuple(
        (pattern, source)
        for _, source, pattern in sorted(patterns, key=lambda item: (-item[0], item[1]))
    )


def _format(text: str, values: dict) -> str:
    if not values:
        return text
    try:
        return text.format_map(values)
    except (KeyError, ValueError, AttributeError, IndexError, TypeError):
        return text


def _match(source: str):
    # Bound pattern work for unknown fields accidentally containing a whole document.
    if len(source) > 20000:
        return None
    for pattern, key in _patterns():
        matched = pattern.fullmatch(source)
        if matched:
            return key, matched.groupdict()
    return None


def translate(source: str, **values) -> str:
    """Translate a known UI string or template, preserving all captured values.

    Unknown strings stay unchanged. Callers must pass UI fields only: arbitrary
    document text could coincidentally equal a known interface label.
    """
    language = current_language()
    catalog = _catalogs()[language]
    if source in _source_keys():
        return _format(catalog.get(source, source), values)
    if language != "uk" and not values:
        matched = _match(source)
        if matched:
            key, captured = matched
            return _format(catalog.get(key, key), captured)
    return _format(source, values)


_API_HINTS = frozenset(
    {
        "Перевірте модель, режим API та підтримку JSON у налаштуваннях.",
        "Перевірте API-ключ у налаштуваннях підключення.",
        "Ключ не має доступу до цієї моделі.",
        "Перевірте адресу API та назву моделі.",
        "Перевірте налаштування провайдера.",
    }
)
_REVIEW_ERRORS = frozenset(
    {
        "Відповідь містить недопустиму розмітку.",
        "Модель повернула пошкоджені позначки форматування.",
        "Некоректний розмір відповіді моделі.",
        "Модель змінила порядок або кількість позначок форматування.",
        "Модель повернула текст поза фрагментами документа.",
        "Модель змінила структуру форматування фрагмента.",
        "Модель додала невідому позначку форматування.",
        "Модель змінила службовий елемент документа.",
        "Модель повернула порожній переклад.",
        "У перекладі змінилися числа або відсотки.",
        "Змінилися адреси, коди або змінні.",
        "Модель змінила захищений ідентифікатор або посилання.",
    }
)


def translate_ui_message(source: str) -> str:
    """Translate explicit, known UI compositions without recursing into user values."""
    if len(source) > 20000:
        return source
    with _LOCK:
        # Keep all parts of a composition in one language if another thread switches.
        language = _language
        if language == "uk":
              return translate(source)
        cached = _cached(language, source)
        if cached is not None:
            return cached
        translated = _translate_composition(source)
        if translated is not None:
            return _remember(language, source, translated)
        translated = translate(source)
        return _remember(language, source, translated) if translated != source else source


def _translate_composition(source: str) -> str | None:
    pdf = re.fullmatch(
        r"PDF · (?P<phase>[^\n]+?)(?P<counter> · \d+(?:\.\d+)?/\d+(?:\.\d+)?)?"
        r" · (?P<elapsed>\d+) с(?P<waiting> · очікуємо оновлення від рушія)?",
        source,
    )
    if pdf:
        from forma.documents.pdf_progress import STAGES

        phases = set(STAGES.values()) | {
            "Готуємо PDF-рушій; перший запуск може потребувати завантаження ресурсів",
            "Завантажуємо ресурси PDF-рушія",
            "API обмежив частоту запитів; рушій очікує та повторює запит",
            "Рушій повторює мережевий запит після помилки",
        }
        if pdf["phase"] in phases:
            return translate(
                "PDF · {message} · {elapsed} с",
                message=translate(pdf["phase"]) + (pdf["counter"] or ""),
                elapsed=pdf["elapsed"],
            ) + (translate(pdf["waiting"]) if pdf["waiting"] else "")
    api = re.fullmatch(r"API повернув HTTP (?P<status>\d{3})\. (?P<hint>[^\n]+)", source)
    if api and api["hint"] in _API_HINTS:
        return translate(
            "API повернув HTTP {status_code}. {value1}",
            status_code=api["status"],
            value1=translate(api["hint"]),
        )
    limit = re.fullmatch(
        r"(?P<label>Паралельні запити|Паралельні документи): вкажіть ціле число від 1 до (?P<limit>\d+)\.",
        source,
    )
    if limit:
        return translate(
            "{label}: вкажіть ціле число від 1 до {limit}.",
            label=translate(limit["label"]),
            limit=limit["limit"],
        )
    review_prefix = "Правку не збережено: "
    if source.startswith(review_prefix):
        remaining, messages = source[len(review_prefix) :], []
        while remaining:
            message = next(
                (
                    message
                    for message in sorted(_REVIEW_ERRORS, key=lambda item: (-len(item), item))
                    if remaining == message or remaining.startswith(message + " ")
                ),
                None,
            )
            if message is None:
                break
            messages.append(translate(message))
            remaining = remaining[len(message) :].lstrip(" ")
        if messages and not remaining:
            return translate(review_prefix) + " ".join(messages)
    wrappers = (
        (
            "Сторінки зі зображеннями та майже без текстового шару: ",
            ". Написи всередині зображень можуть залишитися мовою оригіналу. "
            "Якщо це скановані сторінки з текстом, потрібен OCR перед перекладом.",
            r"\d+(?:, \d+)*",
        ),
        (
            "Текст сторінок залишився без змін: ",
            ". Перевірте, чи перекладено ці сторінки; текст міг уже бути цільовою мовою.",
            r"\d+(?:, \d+)*",
        ),
        (
            "API-статистика поточного запуску: ",
            ". Повторно використані переклади з кешу не витрачають токени; "
            "перервані запити можуть бути відсутні в обліку.",
            r"[^\n]{1,300}",
        ),
    )
    for prefix, suffix, pattern in wrappers:
        if source.startswith(prefix) and source.endswith(suffix):
            value = source[len(prefix) : -len(suffix)]
            if re.fullmatch(pattern, value):
                return translate(prefix) + value + translate(suffix)
    return None


class CatalogTranslator(QTranslator):
    def isEmpty(self) -> bool:
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if sourceText in _source_keys() or _match(sourceText):
            return translate(sourceText)
        # PySide converts None to a null QString, which lets Qt continue fallback.
        # Returning Python '' would instead produce a blank label.
        return None
