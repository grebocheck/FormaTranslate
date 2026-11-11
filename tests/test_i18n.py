from __future__ import annotations

import ast
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Formatter

import pytest
from PySide6.QtCore import QCoreApplication, QTranslator

from forma import i18n


@pytest.fixture(autouse=True)
def restore_language():
    previous = i18n.current_language()
    yield
    i18n.set_language(previous)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en_US", "en"),
        ("uk_UA", "uk"),
        ("pl_PL", "pl"),
        ("de-DE", "de"),
        ("fr_FR", "en"),
        ("C", "en"),
    ],
)
def test_system_locale_and_explicit_choice(monkeypatch, locale, expected):
    monkeypatch.setattr(i18n, "_system_locale_name", lambda: locale)
    assert i18n.resolve_language("system") == expected
    assert i18n.set_language("system") == expected
    assert i18n.current_language() == expected
    assert i18n.resolve_language("uk") == "uk"
    assert i18n.resolve_language("not-supported") == "en"


def test_language_switching_and_self_named_options():
    expected = {"uk": "Налаштування", "en": "Settings", "pl": "Ustawienia", "de": "Einstellungen"}
    for language, label in expected.items():
        assert i18n.set_language(language) == language
        assert i18n.translate("Налаштування") == label
        choices = i18n.available_languages()
        assert [choice["code"] for choice in choices] == ["system", "uk", "en", "pl", "de"]
        assert [choice["label"] for choice in choices[1:]] == [
            "Українська",
            "English",
            "Polski",
            "Deutsch",
        ]
        assert choices[0]["label"] == i18n.translate("Як у системі")


def test_qt_unknown_sources_use_fallback_and_known_ukrainian_is_visible(qapp):
    class EarlierTranslator(QTranslator):
        def translate(self, context, sourceText, disambiguation=None, n=-1):
            return "Earlier translation" if sourceText == "Qt stock text" else None

    earlier, catalog = EarlierTranslator(), i18n.CatalogTranslator()
    qapp.installTranslator(earlier)
    qapp.installTranslator(catalog)
    try:
        i18n.set_language("en")
        assert QCoreApplication.translate("Main", "Налаштування") == "Settings"
        assert QCoreApplication.translate("Qt", "Qt stock text") == "Earlier translation"
        assert QCoreApplication.translate("Qt", "Unrecognized text") == "Unrecognized text"
        assert catalog.translate("Qt", "Unrecognized text") is None
        i18n.set_language("uk")
        assert QCoreApplication.translate("Main", "Налаштування") == "Налаштування"
    finally:
        qapp.removeTranslator(catalog)
        qapp.removeTranslator(earlier)


def test_dynamic_templates_preserve_unicode_newlines_paths_and_literal_braces():
    i18n.set_language("en")
    name = "Звіт {name} — München/Łódź\n[23].docx"
    expected = "Done: " + name
    assert i18n.translate("Готово: {name}", name=name) == expected
    assert i18n.translate("Готово: " + name) == expected
    assert i18n.translate("Готово: {name}") == "Done: {name}"
    # Matching is anchored: prose containing a status-like phrase stays untouched.
    unknown = "Цитата у документі: Готово: " + name
    assert i18n.translate(unknown) == unknown
    unknown_markup = "<r0>Іваненко &amp; Partners — 25 кВт</r0>"
    assert i18n.translate(unknown_markup) == unknown_markup


def test_formatted_status_and_canonical_locations_are_presentation_only():
    source = "document · абзац 123"
    i18n.set_language("en")
    assert i18n.translate(source) == "document · paragraph 123"
    assert source == "document · абзац 123"
    assert i18n.translate("Готово: звіт.docx").endswith("звіт.docx")
    assert i18n.translate("Версія %1") == "Version %1"


def test_catalogs_cover_every_qml_key_and_preserve_placeholders():
    qml_keys = {
        json.loads(match.group(1))
        for path in Path("src/forma/ui").glob("*.qml")
        for match in re.finditer(r'qsTr\(("(?:[^"\\]|\\.)*")\)', path.read_text(encoding="utf-8"))
    }
    assert qml_keys
    formatter = Formatter()
    catalogs = i18n._catalogs()
    non_uk_keys = [set(catalogs[language]) for language in ("en", "pl", "de")]
    assert non_uk_keys[0] == non_uk_keys[1] == non_uk_keys[2]
    for language in ("en", "pl", "de"):
        catalog = catalogs[language]
        assert qml_keys <= catalog.keys(), sorted(qml_keys - catalog.keys())
        for source, translated in catalog.items():
            assert translated.strip(), (language, source)
            source_fields = Counter(
                field for _, field, _, _ in formatter.parse(source) if field is not None
            )
            target_fields = Counter(
                field for _, field, _, _ in formatter.parse(translated) if field is not None
            )
            assert target_fields == source_fields, (language, source, translated)
            assert Counter(re.findall(r"%\d+|%n", translated)) == Counter(
                re.findall(r"%\d+|%n", source)
            )
            assert not re.search(r"[А-Яа-яІіЇїЄєҐґ]", translated), (language, source, translated)
            if "uv tool install" in source:
                assert "uv tool install --python 3.12 BabelDOC==0.7.1" in translated


def test_quick_translator_messages_are_available_in_every_interface_language():
    keys = set()
    for filename in ("quick_controller.py", "quick_translation.py", "image_translation.py"):
        tree = ast.parse((Path("src/forma") / filename).read_text(encoding="utf-8"))
        keys.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(r"[А-Яа-яІіЇїЄєҐґ]", node.value)
        )
    assert keys
    for language in ("en", "pl", "de"):
        assert keys <= i18n._catalogs()[language].keys(), (
            language,
            sorted(keys - i18n._catalogs()[language].keys()),
        )


def test_ocr_messages_are_available_in_every_interface_language():
    tree = ast.parse(Path("src/forma/ocr.py").read_text(encoding="utf-8"))
    keys = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"[А-Яа-яІіЇїЄєҐґ]", node.value)
    }
    for language in ("en", "pl", "de"):
        assert keys <= i18n._catalogs()[language].keys(), (
            language,
            sorted(keys - i18n._catalogs()[language].keys()),
        )


def test_provider_presets_and_protocol_messages_have_translations():
    keys = set()
    for filename in ("config.py", "provider_presets.py", "api_protocol.py"):
        tree = ast.parse((Path("src/forma") / filename).read_text(encoding="utf-8"))
        # Interpolated legacy config errors use separately translated templates;
        # their fragments are not standalone presentation messages.
        fragments = {
            id(child)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for child in ast.walk(node)
        }
        keys.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and id(node) not in fragments
            and isinstance(node.value, str)
            and re.search(r"[А-Яа-яІіЇїЄєҐґ]", node.value)
        )
    assert keys
    for language in ("en", "pl", "de"):
        assert keys <= i18n._catalogs()[language].keys(), (
            language,
            sorted(keys - i18n._catalogs()[language].keys()),
        )


def test_concurrent_language_changes_return_only_complete_catalog_values():
    expected = {"Налаштування", "Settings", "Ustawienia", "Einstellungen"}

    def work(index):
        i18n.set_language(("uk", "en", "pl", "de")[index % 4])
        return i18n.translate("Налаштування")

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(work, range(200)))
    assert set(values) <= expected
    with pytest.raises(TypeError):
        i18n._catalogs()["en"]["Налаштування"] = "Changed"


def test_very_large_unknown_text_bypasses_dynamic_matching():
    i18n.set_language("en")
    source = "Готово: " + "Довгий невідомий текст " * 2000
    assert i18n.translate(source) == source


@pytest.mark.parametrize("language", ["en", "pl", "de"])
def test_explicit_nested_ui_messages_translate_only_known_components(language):
    i18n.set_language(language)
    messages = [
        "PDF · Перекладаємо абзаци · 3/10 · 45 с · очікуємо оновлення від рушія",
        "PDF · Готуємо PDF-рушій; перший запуск може потребувати завантаження ресурсів · 0 с",
        "API повернув HTTP 401. Перевірте API-ключ у налаштуваннях підключення.",
        "API повернув HTTP 418. Перевірте налаштування провайдера.",
        "Паралельні запити: вкажіть ціле число від 1 до 16.",
        "Паралельні документи: вкажіть ціле число від 1 до 4.",
        "Правку не збережено: У перекладі змінилися числа або відсотки. Змінилися адреси, коди або змінні.",
        "Текст сторінок залишився без змін: 2, 3. Перевірте, чи перекладено ці сторінки; текст міг уже бути цільовою мовою.",
        "Сторінки зі зображеннями та майже без текстового шару: 1, 7. Написи всередині зображень можуть залишитися мовою оригіналу. Якщо це скановані сторінки з текстом, потрібен OCR перед перекладом.",
    ]
    for source in messages:
        translated = i18n.translate_ui_message(source)
        assert translated != source
        assert not re.search(r"[А-Яа-яІіЇїЄєҐґ]", translated), (language, translated)
        assert re.findall(r"\d+", translated) == re.findall(r"\d+", source)


def test_nested_helpers_never_translate_arbitrary_values_that_match_ui_labels():
    i18n.set_language("en")
    # A phase must be in the PDF-stage allowlist, not merely in the UI catalog.
    assert "Готово" in i18n.translate_ui_message("PDF · Готово · 3 с")
    assert "Готово" in i18n.translate_ui_message("API повернув HTTP 400. Готово")
    unknown = "Правку не збережено: Налаштування"
    assert i18n.translate_ui_message(unknown) == unknown
    partly_known = "Правку не збережено: У перекладі змінилися числа або відсотки. Налаштування"
    assert i18n.translate_ui_message(partly_known) == partly_known
    source = (
        "API-статистика поточного запуску: Налаштування {name}.pdf.api.json"
        ". Повторно використані переклади з кешу не витрачають токени; "
        "перервані запити можуть бути відсутні в обліку."
    )
    translated = i18n.translate_ui_message(source)
    assert "Налаштування {name}.pdf.api.json" in translated
    assert "Повторно" not in translated
    i18n.set_language("uk")
    assert i18n.translate_ui_message(source) == source


def test_ui_cache_is_bounded_language_specific_and_skips_unknown_content(monkeypatch):
    i18n._TRANSLATIONS.clear()
    i18n.set_language("en")
    for index in range(4200):
        i18n.translate_ui_message(f"document · абзац {index}")
    assert len(i18n._TRANSLATIONS) == 4096
    assert ("en", "document · абзац 0") not in i18n._TRANSLATIONS
    source = "document · абзац 4199"
    expected = i18n.translate_ui_message(source)
    with monkeypatch.context() as patch:
        patch.setattr(i18n, "_match", lambda _: pytest.fail("Warm UI cache rescanned templates"))
        assert i18n.translate_ui_message(source) == expected
    unknown = "Невідомий вміст документа з номером 4199."
    assert i18n.translate_ui_message(unknown) == unknown
    assert ("en", unknown) not in i18n._TRANSLATIONS
    huge = "Готово: " + "a" * 20001
    assert i18n.translate_ui_message(huge) == huge
    assert ("en", huge) not in i18n._TRANSLATIONS
    i18n.set_language("pl")
    assert i18n.translate_ui_message(source) != expected
    assert ("pl", source) in i18n._TRANSLATIONS


def test_base_template_translation_cannot_poison_nested_ui_message_cache():
    i18n.set_language("en")
    source = "PDF · Перекладаємо абзаци · 2 с"
    assert "Перекладаємо абзаци" in i18n.translate(source)
    assert "Translating paragraphs" in i18n.translate_ui_message(source)
    assert "Перекладаємо абзаци" in i18n.translate(source)
