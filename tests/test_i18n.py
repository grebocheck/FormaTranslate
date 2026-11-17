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

from forma.localization import catalog as i18n

NON_UK_LANGUAGES = tuple(code for code in i18n.LANGUAGE_METADATA if code != "uk")


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
        ("fr_CA", "fr"),
        ("es-MX", "es"),
        ("ja_JP", "en"),
        ("C", "en"),
        ("it_IT", "it"),
        ("pt_BR", "pt-BR"),
        ("PT-br", "pt-BR"),
        ("pt", "pt-BR"),
        ("pt_PT", "pt-BR"),
        ("cs_CZ", "cs"),
        ("ro-RO", "ro"),
        ("nl_NL", "nl"),
        ("NL_be", "nl"),
        ("nl-BE-u-nu-latn", "nl"),
        ("sk_SK", "sk"),
        ("sk-CZ", "sk"),
        ("sk_SK_u_ca_gregory", "sk"),
        ("sl_SI", "en"),
    ],
)
def test_system_locale_and_explicit_choice(monkeypatch, locale, expected):
    monkeypatch.setattr(i18n, "_system_locale_name", lambda: locale)
    assert i18n.resolve_language("system") == expected
    assert i18n.set_language("system") == expected
    assert i18n.current_language() == expected


def test_language_switching_and_self_named_options():
    assert i18n.resolve_language("uk") == "uk"
    assert i18n.resolve_language("not-supported") == "en"
    expected = {"uk": "Налаштування", "en": "Settings", "pl": "Ustawienia", "de": "Einstellungen", "fr": "Paramètres", "es": "Ajustes", "it": "Impostazioni", "pt-BR": "Configurações", "cs": "Nastavení", "ro": "Setări", "nl": "Instellingen", "sk": "Nastavenia"}
    for language, label in expected.items():
        assert i18n.set_language(language) == language
        assert i18n.translate("Налаштування") == label
        choices = i18n.available_languages()
        assert [choice["code"] for choice in choices] == ["system", *i18n.LANGUAGE_METADATA]
        assert [choice["label"] for choice in choices[1:]] == [
            metadata.label for metadata in i18n.LANGUAGE_METADATA.values()
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
    assert i18n.translate("document · абзац 123") == "document · paragraph 123"
    # Matching is anchored: prose containing a status-like phrase stays untouched.
    unknown = "Цитата у документі: Готово: " + name
    assert i18n.translate(unknown) == unknown
    unknown_markup = "<r0>Іваненко &amp; Partners — 25 кВт</r0>"
    assert i18n.translate(unknown_markup) == unknown_markup


def test_ukrainian_boundary_translates_known_english_storage_errors_only():
    i18n.set_language("uk")
    assert i18n.translate_ui_message("Invalid translation memory entry or policy.") == (
        "Некоректний запис або правила пам’яті перекладів."
    )
    value = "Document text — {project} 12,50 is not a UI message"
    assert i18n.translate_ui_message(value) == value
    assert i18n.translate_ui_message("Готово: Налаштування {project}.docx") == (
        "Готово: Налаштування {project}.docx"
    )


def test_catalogs_cover_every_qml_key_and_preserve_placeholders():
    qml_keys = {
        json.loads(match.group(1))
        for path in Path("src/forma/ui").rglob("*.qml")
        for match in re.finditer(r'qsTr\(("(?:[^"\\]|\\.)*")\)', path.read_text(encoding="utf-8"))
    }
    assert qml_keys
    formatter = Formatter()
    catalogs = i18n._catalogs()
    non_uk_keys = [set(catalogs[language]) for language in NON_UK_LANGUAGES]
    assert all(keys == non_uk_keys[0] for keys in non_uk_keys[1:])
    for language in NON_UK_LANGUAGES:
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
            if i18n.LANGUAGE_METADATA[language].script == "Latn":
                assert not re.search(r"[А-Яа-яІіЇїЄєҐґ]", translated), (language, source, translated)
            if "uv tool install" in source:
                assert "uv tool install --python 3.12 BabelDOC==0.7.1" in translated




def message_literals(filenames, *, include_fragments=False):
    """Collect presentation messages while keeping f-string fragments explicit."""
    keys = set()
    for filename in filenames:
        tree = ast.parse((Path("src/forma") / filename).read_text(encoding="utf-8"))
        fragments = set() if include_fragments else {
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
    return keys


@pytest.mark.parametrize(
    "boundary,filenames,languages",
    [
        ("quick", ("presentation/quick_controller.py", "translation/text.py", "translation/images.py"), NON_UK_LANGUAGES),
        ("ocr", ("documents/ocr.py", "documents/abbyy.py", "documents/ocr_policy.py", "documents/ocr_diagnostics.py", "presentation/ocr_workflow.py", "translation/preparation.py", "translation/handoff.py"), NON_UK_LANGUAGES),
        ("memory", ("memory/importing.py", "memory/session.py", "memory/engine.py"), (*NON_UK_LANGUAGES, "uk")),
        ("glossary", ("presentation/glossary_controller.py", "translation/failures.py"), (*NON_UK_LANGUAGES, "uk")),
        ("provider", ("settings/config.py", "providers/presets.py", "providers/protocol.py"), NON_UK_LANGUAGES),
        ("workspace", ("persistence/session_store.py", "presentation/session_workflow.py"), (*NON_UK_LANGUAGES, "uk")),
        ("preview", ("documents/preview.py", "documents/preview_office.py", "documents/preview_worker.py", "presentation/preview_controller.py"), (*NON_UK_LANGUAGES, "uk")),
        ("stability", ("persistence/instance.py",), (*NON_UK_LANGUAGES, "uk")),
        ("review", ("presentation/review_workflow.py",), NON_UK_LANGUAGES),
        ("text-formats", ("documents/adapters.py", "documents/text.py", "documents/markdown.py", "documents/html.py", "documents/source_editor.py"), (*NON_UK_LANGUAGES, "uk")),
        ("odf", ("documents/odf.py", "documents/odf_spreadsheet.py", "documents/preview_odf.py"), (*NON_UK_LANGUAGES, "uk")),
        ("subtitles", ("documents/subtitles.py", "documents/srt.py", "documents/vtt.py", "documents/subtitle_editor.py"), (*NON_UK_LANGUAGES, "uk")),
        ("epub", ("documents/epub.py", "documents/epub_content.py"), (*NON_UK_LANGUAGES, "uk")),
    ],
    ids=("quick", "ocr", "memory", "glossary", "provider", "workspace", "preview", "stability", "review", "text-formats", "odf", "subtitles", "epub"),
)
def test_python_workflow_messages_have_complete_interface_translations(boundary, filenames, languages):
    keys = message_literals(filenames, include_fragments=boundary == "quick")
    if boundary == "memory":
        from forma.memory import store

        keys.update((store._ERROR, store._INVALID, str(store.MemoryConflict())))
        tree = ast.parse(Path("src/forma/memory/store.py").read_text(encoding="utf-8"))
        keys.update(
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"FormaError", "MemoryConflict"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )
    elif boundary == "glossary":
        keys.add(
            "Експорт зупинено: переклад не пройшов перевірку після трьох спроб. "
            "Перегляньте звіт про проблемні фрагменти та повторіть переклад."
        )
    elif boundary == "workspace":
        from forma.persistence.session_store import DOMAINS

        # These stored prompt values stay canonical; their presentation labels
        # are covered independently by the QML catalog test.
        keys -= DOMAINS
    elif boundary == "stability":
        from forma.persistence.review_store import _STATE_CHANGED_ERROR
        from forma.presentation.review_workflow import _recovery_readonly_reason

        keys.add(_STATE_CHANGED_ERROR)
        keys.update(_recovery_readonly_reason(metadata) for metadata in ({}, {"retired_extraction": True}))
    assert keys
    for language in languages:
        assert keys <= i18n._catalogs()[language].keys(), (
            boundary, language, sorted(keys - i18n._catalogs()[language].keys())
        )









def test_workflow_templates_interpolate_counts_in_all_languages():
    source = "Додано {count} термінів до чернетки. Наявні переклади збережено."
    for language in tuple(i18n.LANGUAGE_METADATA):
        i18n.set_language(language)
        message = i18n.translate(source, count=12)
        assert "12" in message and "{count}" not in message
        if i18n.LANGUAGE_METADATA[language].script == "Latn":
            assert not re.search(r"[А-Яа-яІіЇїЄєҐґ]", message)




def test_concurrent_language_changes_return_only_complete_catalog_values():
    expected = {i18n._catalogs()[code].get("Налаштування", "Налаштування") for code in i18n.LANGUAGE_METADATA}

    def work(index):
        i18n.set_language(tuple(i18n.LANGUAGE_METADATA)[index % len(i18n.LANGUAGE_METADATA)])
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


@pytest.mark.parametrize("language", NON_UK_LANGUAGES)
def test_explicit_nested_ui_messages_translate_only_known_components(language):
    i18n.set_language(language)
    name = "Налаштування {name} — Łódź/École 12,50.docx"
    message = i18n.translate("Готово: {name}", name=name)
    assert message.endswith(name) and message != "Готово: " + name
    assert i18n.translate_ui_message("Назва громади — {project} 12,50") == "Назва громади — {project} 12,50"
    note = "API-статистика поточного запуску: " + name + (
        ". Повторно використані переклади з кешу не витрачають токени; "
        "перервані запити можуть бути відсутні в обліку."
    )
    assert name in i18n.translate_ui_message(note)
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
        if i18n.LANGUAGE_METADATA[language].script == "Latn":
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


def test_language_metadata_and_regional_preference_round_trip():
    assert i18n.SUPPORTED_LANGUAGES == frozenset(i18n.LANGUAGE_METADATA)
    for code, metadata in i18n.LANGUAGE_METADATA.items():
        assert i18n.resolve_language(metadata.locale) == code
        assert i18n.resolve_language(code) == code
        assert metadata.direction in {"ltr", "rtl"}
        assert metadata.label.strip()
    with pytest.raises(TypeError):
        i18n.LANGUAGE_METADATA["xx"] = i18n.LANGUAGE_METADATA["en"]
    assert i18n.set_language("pt_BR") == "pt-BR"
    assert i18n.current_language() == "pt-BR"
    assert i18n.translate("Налаштування") == "Configurações"


@pytest.mark.parametrize(
    "language,categories",
    [
        ("uk", ("many", "one", "few", "many", "many", "one")),
        ("pl", ("many", "one", "few", "many", "many", "many")),
        ("cs", ("other", "one", "few", "other", "other", "other")),
        ("ro", ("few", "one", "few", "few", "few", "other")),
        ("en", ("other", "one", "other", "other", "other", "other")),
        ("de", ("other", "one", "other", "other", "other", "other")),
        ("fr", ("one", "one", "other", "other", "other", "other")),
        ("es", ("other", "one", "other", "other", "other", "other")),
        ("it", ("other", "one", "other", "other", "other", "other")),
        ("pt-BR", ("one", "one", "other", "other", "other", "other")),
        ("nl", ("other", "one", "other", "other", "other", "other")),
        ("sk", ("other", "one", "few", "other", "other", "other")),
    ],
)
def test_integer_plural_categories(language, categories):
    counts = (0, 1, 2, 5, 11, 21)
    assert tuple(i18n.plural_category(count, language) for count in counts) == categories
    assert tuple(i18n.plural_category(str(count), language) for count in counts) == categories


@pytest.mark.parametrize(
    "count,nl_category,sk_category",
    [(0, "other", "other"), (1, "one", "one"), (2, "other", "few"),
     (3, "other", "few"), (4, "other", "few"), (5, "other", "other"),
     (11, "other", "other"), (12, "other", "other"), (21, "other", "other"),
     (22, "other", "other"), (24, "other", "other"), (101, "other", "other"),
     (102, "other", "other"), (1_000_000, "other", "other")],
)
def test_dutch_and_slovak_integer_plural_boundaries(count, nl_category, sk_category):
    assert i18n.plural_category(count, "nl") == nl_category
    assert i18n.plural_category(count, "sk") == sk_category
    assert i18n.plural_category(str(count), "nl") == nl_category
    assert i18n.plural_category(str(count), "sk") == sk_category


@pytest.mark.parametrize("language", ("fr", "es", "it", "pt-BR"))
def test_integer_million_plural_category(language):
    assert i18n.plural_category(1_000_000, language) == "many"
    assert i18n.plural_category(2_000_000, language) == "many"
    assert i18n.plural_category(1_000_001, language) == "other"


@pytest.mark.parametrize("count", (-1, 1.5, 1.0, True, None, "1.0", "-1", "1" * 100))
def test_invalid_interface_counts_are_rejected(count):
    with pytest.raises(ValueError, match="non-negative integers"):
        i18n.translate_count("{count} фрагментів · готовий", count)


@pytest.mark.parametrize(
    "language,nouns",
    [
        ("uk", ("фрагментів", "фрагмент", "фрагменти", "фрагментів", "фрагментів", "фрагмент")),
        ("pl", ("fragmentów", "fragment", "fragmenty", "fragmentów", "fragmentów", "fragmentów")),
        ("cs", ("fragmentů", "fragment", "fragmenty", "fragmentů", "fragmentů", "fragmentů")),
        ("ro", ("fragmente", "fragment", "fragmente", "fragmente", "fragmente", "de fragmente")),
        ("nl", ("fragmenten", "fragment", "fragmenten", "fragmenten", "fragmenten", "fragmenten")),
        ("sk", ("fragmentov", "fragment", "fragmenty", "fragmentov", "fragmentov", "fragmentov")),
    ],
)
def test_plural_wording_at_canonical_message_boundary(language, nouns):
    source = "{count} фрагментів · готовий"
    i18n.set_language(language)
    for count, noun in zip((0, 1, 2, 5, 11, 21), nouns, strict=True):
        displayed = i18n.translate_count(source, count)
        assert displayed.startswith(f"{count} {noun} · ")
        assert i18n.translate_ui_message(source.format(count=count)) == displayed
    # Counts are presentation only; the canonical source remains unchanged.
    assert source == "{count} фрагментів · готовий"


def test_plural_catalogs_cover_registered_languages_and_preserve_fields(qapp):
    catalogs = i18n._plural_catalogs()
    assert set(catalogs) == i18n.SUPPORTED_LANGUAGES
    expected_keys = {
        f"{message}.{category}"
        for message in ("segments_ready", "pages_ready", "terms_added")
        for category in ("one", "few", "many", "other")
    }
    translator = i18n.CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    try:
        for language, catalog in catalogs.items():
            assert set(catalog) == expected_keys
            assert all(value.strip() and value.count("{count}") == 1 for value in catalog.values())
            i18n.set_language(language)
            for count in (0, 1, 2, 5, 11, 21):
                for template, qt_source in (
                    ("{count} фрагментів · готовий", "%n фрагментів · готовий"),
                    ("{count} стор. · готовий", "%n стор. · готовий"),
                ):
                    expected = i18n.translate_count(template, count)
                    assert str(count) in expected and "{" not in expected
                    assert QCoreApplication.translate("Counts", qt_source, None, count) == expected
                added = i18n.translate(
                    "Додано {count} термінів до чернетки. Наявні переклади збережено.", count=count,
                )
                assert str(count) in added and "{count}" not in added
    finally:
        qapp.removeTranslator(translator)


def test_pptx_messages_and_canonical_location_translate_in_every_language():
    source = "Slide {slide} · shape {shape} · paragraph {paragraph}"
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        shown = i18n.translate(source, slide=2, shape=5, paragraph=11)
        assert i18n.translate_ui_message("Slide 2 · shape 5 · paragraph 11") == shown
        assert re.findall(r"\d+", shown) == ["2", "5", "11"]
        if language != "en":
            assert "shape" not in shown
        keys = message_literals(("documents/pptx.py",))
        assert keys <= i18n._catalogs()[language].keys(), sorted(keys - i18n._catalogs()[language].keys())


def test_registered_document_filter_preserves_extensions_in_every_language():
    from forma.core.formats import DOCUMENT_FILTER

    extensions = DOCUMENT_FILTER.partition("(")[2].removesuffix(")")
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        displayed = i18n.translate("Документи ({extensions})", extensions=extensions)
        assert i18n.translate_ui_message(DOCUMENT_FILTER) == displayed
        assert displayed.endswith(f"({extensions})")
        assert re.findall(r"\*\.[a-z]+", displayed) == re.findall(r"\*\.[a-z]+", DOCUMENT_FILTER)


@pytest.mark.parametrize(
    "template,values,canonical",
    [
        ("Line {line} · fragment {fragment}", {"line": 4, "fragment": 2}, "Line 4 · fragment 2"),
        ("HTML · text {index} · line {line}", {"index": 7, "line": 11}, "HTML · text 7 · line 11"),
    ],
)
def test_text_format_locations_translate_without_changing_canonical_values(template, values, canonical):
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        displayed = i18n.translate(template, **values)
        assert i18n.translate_ui_message(canonical) == displayed
        assert re.findall(r"\d+", displayed) == re.findall(r"\d+", canonical)
        assert "{" not in displayed


@pytest.mark.parametrize(
    "template,values,canonical",
    [
        ("body · paragraph {paragraph}", {"paragraph": 3}, "body · paragraph 3"),
        *[
            (f"{role} {{instance}} · paragraph {{paragraph}}", {"instance": 2, "paragraph": 3},
             f"{role} 2 · paragraph 3")
            for role in ("header", "header-left", "header-first", "footer", "footer-left", "footer-first")
        ],
        ("Slide {slide} · paragraph {paragraph}", {"slide": 2, "paragraph": 3}, "Slide 2 · paragraph 3"),
        (
            "ODS · sheet {sheet_index} · rows {row_start}-{row_end} · columns {column_start}-{column_end} · paragraph {paragraph_index}",
            {"sheet_index": 2, "row_start": 1, "row_end": 5, "column_start": 3, "column_end": 4, "paragraph_index": 1},
            "ODS · sheet 2 · rows 1-5 · columns 3-4 · paragraph 1",
        ),
    ],
)
def test_odf_roles_and_ranges_localize_without_changing_canonical_addresses(template, values, canonical):
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        displayed = i18n.translate(template, **values)
        assert i18n.translate_ui_message(canonical) == displayed
        assert re.findall(r"\d+", displayed) == re.findall(r"\d+", canonical)
        assert "{" not in displayed
        if language != "en":
            assert not re.search(r"\bparagraph\b", displayed)


def test_caption_ordinal_location_translates_without_rewriting_identifiers():
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        assert i18n.translate_ui_message("Cue 42") == i18n.translate("Cue {cue}", cue=42)
        assert "42" in i18n.translate_ui_message("Cue 42")
        # User-supplied SRT counters and VTT identifiers are not UI messages.
        identifier = "PRIVATE_ID — Налаштування 42"
        assert i18n.translate_ui_message(identifier) == identifier


def test_epub_document_addresses_translate_without_rewriting_resource_paths():
    source = "EPUB · document {document} · fragment {fragment}"
    for language in i18n.LANGUAGE_METADATA:
        i18n.set_language(language)
        displayed = i18n.translate(source, document=3, fragment=11)
        assert i18n.translate_ui_message("EPUB · document 3 · fragment 11") == displayed
        assert re.findall(r"\d+", displayed) == ["3", "11"]
        assert "{" not in displayed
        resource = "OPS/Налаштування {document}/chapter 3.xhtml#part11"
        assert i18n.translate_ui_message(resource) == resource
