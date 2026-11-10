from __future__ import annotations

import pytest

from forma.quality import check_segment, introduced_repetition


def repeated_issues(source, target):
    return [
        issue
        for issue in check_segment(source, target, "paragraph-1")
        if issue.code == "repeated_text"
    ]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("<r0>Частотне регулювання</r0>", "<r0>Variable-variable-frequency control</r0>"),
        (
            "<r0>Частотне </r0><r1>регулювання</r1>",
            "<r0>Variable-</r0><r1>variable-frequency control</r1>",
        ),
        ("<r0>Перевірте тиск.</r0>", "<r0>Check the pressure pressure.</r0>"),
    ],
)
def test_new_repeated_word_is_a_located_warning_only(source, target):
    issues = repeated_issues(source, target)
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].segment_id == "paragraph-1"
    assert not [issue for issue in check_segment(source, target) if issue.severity == "error"]


def test_adjacent_long_copy_across_runs_is_flagged_without_changing_text():
    source = "<r0>У звіті наведено умови роботи станції. </r0><r1>За ними оцінюють споживання електроенергії.</r1>"
    fragment = (
        "The report describes the operating conditions of the pumping station. "
        "These conditions are used to assess its electricity consumption."
    )
    target = f"<r0>{fragment}</r0><r1>{fragment}</r1>"
    original_target = target
    assert repeated_issues(source, target)
    assert target == original_target


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("Це дуже дуже важливо.", "This is extremely extremely important."),
        ("Баден-Баден", "Baden-Baden"),
        ("Назва гурту", "Duran Duran"),
        ("Одиниці: кВт кВт", "Units: kilowatt kilowatt"),
        ("Написи CONTROL CONTROL", "Labels CONTROL CONTROL"),
        ("Код ABC-12-ABC-12", "Code ABC-12-ABC-12"),
        ("Документ pressure-pressure.docx", "Document pressure-pressure.docx"),
        (
            "Посилання https://example.org/pressure-pressure",
            "Link https://example.org/pressure-pressure",
        ),
        ("Ідентифікатор {pressure-pressure}", "Identifier {pressure-pressure}"),
        ("Це було вже в минулому.", "They had had that experience."),
        ("Я знав, що той показник правильний.", "I knew that that indicator was correct."),
    ],
)
def test_source_repetitions_names_units_codes_and_short_grammar_are_not_flagged(source, target):
    assert not introduced_repetition(source, target)


def test_existing_long_repetition_is_retained_even_when_translation_is_longer():
    source = "Звіт показує щомісячні витрати електроенергії для насосної станції."
    target = "The report presents the monthly electricity consumption figures recorded for the pumping station."
    assert not introduced_repetition(f"{source} {source}", f"{target} {target}")
    assert not introduced_repetition(f"{target} {target}", f"{target} {target}")


def test_similar_measurements_with_different_numbers_are_not_duplicate_fragments():
    first = "The recorded electricity consumption of the pumping station during the reporting period was 125 kWh."
    second = first.replace("125", "126")
    assert not introduced_repetition(
        "Вимірювання за два періоди: 125 та 126 кВт·год.", f"{first} {second}"
    )


def test_repetition_warning_does_not_replace_numeric_or_format_errors():
    issues = check_segment("<r0>Тиск 25bar</r0>", "<r0>Pressure pressure 250bar</r0>")
    assert any(i.code == "numbers" and i.severity == "error" for i in issues)
    assert any(i.code == "repeated_text" and i.severity == "warning" for i in issues)
    broken = check_segment("<r0>Тиск</r0>", "<r1>Pressure pressure</r1>")
    assert broken[0].code == "formatting" and broken[0].severity == "error"
