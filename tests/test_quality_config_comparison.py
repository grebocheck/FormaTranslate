from __future__ import annotations

import json

import pytest

from forma.comparison import compare_files, write_comparison
from forma.config import Settings, save_preferences
from forma.models import FormaError
from forma.quality import check_segment, parse_translation


def test_markup_and_protected_values():
    source = "<r0>Pressure: 16 bar &amp; code HM-200; {serial}; https://example.com</r0><k0/><r1>Check.</r1>"
    translated = "<r0>Тиск: 16 bar &amp; код HM-200; {serial}; https://example.com</r0><k0/><r1>Перевірте.</r1>"
    assert not check_segment(source, translated)
    assert "&" in parse_translation(source, translated)["r0"]
    for broken in [
        translated.replace("16", "18"),
        translated.replace("{serial}", "{номер}"),
        translated.replace("HM-200", "HM-201"),
    ]:
        assert any(i.severity == "error" for i in check_segment(source, broken))
    for broken in [
        translated.replace("<k0/>", ""),
        translated + "outside",
        translated.replace("<r0>", '<r0 style="x">'),
    ]:
        with pytest.raises(FormaError):
            parse_translation(source, broken)


def test_decimal_point_cannot_move():
    assert any(
        i.severity == "error"
        for i in check_segment("<r0>Flow 1.6 m3</r0>", "<r0>Витрата 16 m3</r0>")
    )


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("<r0>Power 25kW</r0>", "<r0>Потужність 250кВт</r0>"),
        ("<r0>Gap 10мм</r0>", "<r0>Gap 100mm</r0>"),
        ("<r0>Pressure 1,25bar</r0>", "<r0>Тиск 12.5bar</r0>"),
        ("<r0>Indicator 0: </r0><r1>pump</r1>", "<r0>Indicator 0: 999</r0><r1>pump</r1>"),
    ],
)
def test_numbers_adjacent_to_units_or_formatting_boundaries_are_not_hidden(source, translated):
    assert any(issue.code == "numbers" for issue in check_segment(source, translated))


def test_unit_translation_and_decimal_locale_keep_adjacent_numbers_valid():
    source = "<r0>Power 25kW; gap 10мм; pressure 1,25bar</r0>"
    target = "<r0>Потужність 25кВт; зазор 10mm; тиск 1.25bar</r0>"
    assert not [issue for issue in check_segment(source, target) if issue.severity == "error"]


def test_ukrainian_ordinals_and_hour_notation_preserve_value():
    from forma.quality import numbers_match

    assert numbers_match(
        "Тариф по 2-му класу 3,00585 грн", "Tariff for the second class: 3.00585 UAH"
    )
    assert numbers_match("До 13-ї години, 60 м³/год", "Before 13:00, 60 m³/h")
    assert not numbers_match("До 13-ї години, 60 м³/год", "Before 12:00, 60 m³/h")
    assert not numbers_match(
        "Тариф по 2-му класу 3,00585 грн", "Tariff for the third class: 3.00585 UAH"
    )


def test_config_reads_env_without_leaking_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=super-secret\nOPENAI_MODEL=custom-model\n", encoding="utf-8")
    settings = Settings.load(path)
    settings.validate()
    assert settings.api_key == "super-secret"
    assert "super-secret" not in repr(settings)
    save_preferences({"model": "custom-model", "api_key": "super-secret"})
    preferences = tmp_path / "appdata/preferences.json"
    assert "super-secret" not in preferences.read_text()


def test_comparison_pdf_and_html_escaping(pdf_file, tmp_path):
    result = compare_files(pdf_file, [pdf_file])
    assert result["candidates"][0]["structure_ok"]
    result["candidates"][0]["name"] = "<script>alert(1)</script>.pdf"
    path = tmp_path / "report"
    write_comparison(result, path)
    assert "<script>" not in path.with_suffix(".html").read_text()
    assert json.loads(path.with_suffix(".json").read_text())["candidates"]


def test_script_evidence_never_blocks_bibliography_or_official_names():
    from forma.quality import check_language

    source = "<r0>Smith, J. The International Journal of Advanced Manufacturing Technology. Springer Nature, London.</r0>"
    issues = check_language(source, source, "en", "uk")
    assert issues and all(i.severity == "warning" for i in issues)
    assert not check_language("<r0>Код ТН-42</r0>", "<r0>Code ТН-42</r0>", "uk", "en")
