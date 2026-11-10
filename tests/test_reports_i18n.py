from __future__ import annotations

import json

from lxml import html

from forma.i18n import current_language, set_language
from forma.models import Issue, Options, Segment, TranslationResult
from forma.reports import write_translation_report


def test_report_localizes_only_html_presentation_and_keeps_json_and_document_text(tmp_path):
    segment = Segment("s1", "document · абзац 1", "<r0>Налаштування &amp; &lt;script&gt; 25</r0>")
    target = "<r0>Перевірка &amp; &lt;b&gt; 25</r0>"
    result = TranslationResult(
        tmp_path / "Налаштування.docx",
        tmp_path / "translated.docx",
        [segment],
        {segment.id: target},
        [Issue("numbers", "У перекладі змінилися числа або відсотки.", segment.id, "error")],
        input_tokens=1234,
        output_tokens=567,
        cached_segments=8,
        elapsed_seconds=1.25,
    )
    previous_language = current_language()
    try:
        set_language("uk")
        write_translation_report(
            result, Options(source_language="uk", target_language="en"), "model<&>"
        )
        canonical = result.report.read_bytes()
        ukrainian = html.fromstring(result.report.with_suffix(".html").read_bytes())
        assert ukrainian.get("lang") == "uk"
        assert ukrainian.find(".//h1").text == "forma. / Звіт перекладу"
        for language, title, heading in [
            ("en", "forma. / Translation report", "Review"),
            ("pl", "forma. / Raport tłumaczenia", "Weryfikacja"),
            ("de", "forma. / Übersetzungsbericht", "Prüfung"),
        ]:
            set_language(language)
            write_translation_report(
                result, Options(source_language="uk", target_language="en"), "model<&>"
            )
            assert result.report.read_bytes() == canonical
            page = html.fromstring(result.report.with_suffix(".html").read_bytes())
            assert page.get("lang") == language
            assert page.find(".//h1").text == title
            assert page.find(".//h2").text == heading
            assert page.find(".//h1").getnext().text == "Налаштування.docx"
            row = page.get_element_by_id("segment-s1")
            assert row.find("td").text_content().endswith("Налаштування & <script> 25")
            assert row.findall("td")[1].text_content() == "Перевірка & <b> 25"
            assert not page.findall(".//script") and not page.findall(".//b")
            assert "1,234" in page.find_class("meta")[0].text_content()
            assert "Токени" not in page.find_class("meta")[0].text_content()
            assert "У перекладі змінилися" not in page.find(".//li").text_content()
            assert "абзац" not in row.find("td/small").text_content()
        payload = json.loads(canonical)
        assert payload["segments"][0]["location"] == segment.location
        assert payload["segments"][0]["source"] == segment.source
        assert payload["segments"][0]["translation"] == target
        assert payload["issues"][0]["message"] == result.issues[0].message
    finally:
        set_language(previous_language)


def test_report_localizes_success_and_manual_usage_note(tmp_path):
    result = TranslationResult(
        tmp_path / "source.docx",
        tmp_path / "copy.docx",
        [],
        {},
        [],
        usage_note="Ручна редактура без нових запитів до API.",
    )
    previous_language = current_language()
    try:
        set_language("en")
        write_translation_report(result, Options(), "model")
        page = html.fromstring(result.report.with_suffix(".html").read_bytes())
        assert page.find(".//li").text == "Automated checks passed."
        assert "API" in page.find_class("meta")[0].text_content()
        assert "Ручна редактура" not in page.find_class("meta")[0].text_content()
        assert json.loads(result.report.read_text())["usage_note"] == result.usage_note
    finally:
        set_language(previous_language)
