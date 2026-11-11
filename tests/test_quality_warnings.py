from __future__ import annotations

from xml.sax.saxutils import escape

import pytest

from forma.translation.quality import check_language, check_segment


def marked(text: str) -> str:
    return f"<r0>{escape(text)}</r0>"


@pytest.mark.parametrize(
    "text",
    [
        "IRR, %",
        "CAPEX",
        "cos φ",
        "THD I, %f",
        "Pгідр/P, %",
        "Δz",
        "24-НДН (Д500-325)",
        "16 НДн (400Д190)",
        "1 д 1250/63 а",
        "ТСЗП 250",
        "Bosch GLM 80, № 508033684",
        "«Ергомера-125»",
        "«1.ГНС опитувальник липень 2026(1).docx»",
        "eбаз = ΣEм / ΣVм = 55 047 008 / 353 243,5 = 155,83304 кВт·год/1000 м³.",
        "Ei,м = Eм × (Pi × ti,м) / Σ(Pj × tj,м).",
    ],
)
def test_unchanged_notation_from_real_reports_does_not_warn(text):
    assert check_segment(marked(text), marked(text)) == []


@pytest.mark.parametrize(
    "text",
    [
        "АСКОЕ",
        "CAPEX and project benefits",
        "IRR, % — внутрішня норма дохідності",
        "WATER 25 30",
        "СУМА 25 30",
        "Тиск 25 бар",
        "Annual Report",
        "Appendix-1",
        "John Smith",
        "год",
        "25 м / 30 м",
    ],
)
def test_unchanged_prose_unknown_abbreviations_and_names_still_need_review(text):
    issues = check_segment(marked(text), marked(text), "s1")
    assert [(i.code, i.severity, i.segment_id) for i in issues] == [("unchanged", "warning", "s1")]


@pytest.mark.parametrize(
    ("source", "translated", "expected"),
    [
        ("IRR, 12%", "IRR, 21%", "numbers"),
        ("Д 6300/27-3", "Д 6300/28-3", "numbers"),
        ("ТН-42", "TN-42", "protected_values"),
        ("Report_2026.docx", "Report_2026.pdf", "protected_values"),
        ("e = 25kW", "e = 250kW", "numbers"),
    ],
)
def test_notation_detection_does_not_relax_blocking_guards(source, translated, expected):
    issues = check_segment(marked(source), marked(translated))
    assert any(i.code == expected and i.severity == "error" for i in issues)


def test_notation_still_requires_unchanged_formatting():
    issues = check_segment("<r0>IRR, </r0><r1>%</r1>", "<r0>IRR, %</r0>")
    assert [(i.code, i.severity) for i in issues] == [("formatting", "error")]


def test_length_warnings_and_script_evidence_remain_reviewable():
    source = "Перед запуском насоса потрібно перевірити стан усіх клапанів та виміряти тиск води. "
    long_source = marked(source * 2)
    shortened = check_segment(long_source, marked("Check the pump."))
    expanded = check_segment(
        marked("Перевірте насос."), marked("Check the pump before operation. " * 4)
    )
    assert any(i.code == "short_translation" and i.severity == "warning" for i in shortened)
    assert any(i.code == "expansion" and i.severity == "warning" for i in expanded)
    assert [(i.code, i.severity) for i in check_language(long_source, long_source, "uk", "en")] == [
        ("untranslated_prose", "warning")
    ]
    assert not check_language(marked("ТН-42"), marked("Station ТН-42"), "uk", "en")


@pytest.mark.parametrize(
    ("source", "target", "missing"),
    [
        ("The amount payable before the completion deadline is 25000", "25000", True),
        ("Передайте підписаний примірник договору на acceptance@example.org", "acceptance@example.org", True),
        ("No. 5", "№ 5", False),
        ("Amount in euros: 5", "€ 5", False),
        ("Належить сплатити 25000", "Amount payable: 25000", False),
    ],
)
def test_remaining_numbers_or_identifiers_do_not_hide_lost_prose(source, target, missing):
    from forma.core.models import Options, Segment
    from forma.translation.engine import blocking_issues

    segment = Segment("s1", "paragraph", marked(source))
    result = marked(target)
    errors = blocking_issues(segment, result, Options())
    assert any(issue.code == "missing_text" for issue in errors) is missing
    # The bilingual editorial pass may repair an incomplete, structurally usable draft.
    assert not blocking_issues(segment, result, Options(), draft=True)
