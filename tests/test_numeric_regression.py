"""Number-format regressions use synthetic text, never client document contents."""

from __future__ import annotations

import json

import pytest

from forma.translation.quality import (
    check_segment,
    number_differences,
    numbers_match,
    numeric_values,
)


@pytest.mark.parametrize("space", [" ", "\u00a0", "\u202f", "\u2009"])
def test_space_grouped_thousands_are_one_value_with_exact_decimal_precision(space):
    source = f"Обсяг 74{space}256,031 м³; енергія 18{space}307,420 кВт·год; 23{space}060–25{space}470 м³/добу."
    target = "Volume 74256.031 m³; energy 18307.420 kWh; 23060–25470 m³/day."
    assert numbers_match(source, target)
    assert numeric_values(source) == numeric_values(target)
    assert number_differences(source, target)["missing"] == []
    assert number_differences(source, target)["unexpected"] == []


@pytest.mark.parametrize(
    "source,target",
    [
        ("Обсяг 74 256,031 м³", "Volume 74256.031 m³"),
        ("Знижка −1\u202f234,50 %", "Discount -1234.50%"),
        ("Збільшення +1\u00a0234,50 %", "Increase +1234.50%"),
        ("Обсяг 1 234 567,00 м³", "Volume 1234567.00 m³"),
    ],
)
def test_well_formed_grouping_is_equivalent_in_normal_markup_validation(source, target):
    assert not [issue for issue in check_segment(f"<r0>{source}</r0>", f"<r0>{target}</r0>") if issue.code == "numbers"]


@pytest.mark.parametrize(
    "target",
    ["74256.03", "74256.0310", "74256.310", "7425.6031", "74256031", "-74256.031", "+74256.031", "74256.031%"],
)
def test_grouping_never_allows_changed_digits_precision_sign_or_percent(target):
    assert not numbers_match("Обсяг 74 256,031 м³", f"Volume {target} m³")


@pytest.mark.parametrize(
    "source,target",
    [
        ("Значення 12 34 56", "Values 123456"),
        ("Значення 1234 567", "Values 1234567"),
        ("Значення 1\t234", "Values 1234"),
        ("Значення 1\n234", "Values 1234"),
        ("Значення 12; 345", "Values 12345"),
        ("Значення 1 234 56", "Values 123456"),
        ("Значення 1 234,50 і 1 234,50", "Values 1234.50"),
    ],
)
def test_distinct_values_controls_and_malformed_groups_are_not_collapsed(source, target):
    assert not numbers_match(source, target)


def test_number_evidence_preserves_duplicates_and_identifies_the_actual_difference():
    evidence = number_differences("Витрата 1 234,500; 1 234,500; 8%", "Flow 1234.500; 1234.50; 8%")
    assert evidence == {
        "source": ["1234.500", "1234.500", "8%"],
        "translation": ["1234.500", "1234.50", "8%"],
        "missing": ["1234.500"],
        "unexpected": ["1234.50"],
    }
    assert json.loads(json.dumps(evidence)) == evidence


def test_numeric_evidence_uses_the_same_ordinal_and_hour_exceptions_as_validation():
    source = "До 13-ї години, тариф по 2-му класу: 3,00585 грн"
    target = "Before 13:00, tariff for the second class: 3.00585 UAH"
    assert numbers_match(source, target)
    assert number_differences(source, target)["missing"] == []
    assert number_differences(source, target)["unexpected"] == []


def test_recognized_cubic_unit_spelling_preserves_coefficients_and_identifier_digits():
    assert numbers_match("Flow 12,50m3/h", "Flow 12.50 m³/h")
    assert numbers_match("m^3/h", "м³/год")
    assert numbers_match("Flow 5m3/hour", "Flow 5m3/h")
    assert not numbers_match("Flow 3m3/h", "Flow 4m³/h")
    assert not numbers_match("Model AM3/H", "Model AM³/H")
    assert not numbers_match("Model m3-500", "Model m³-500")
    assert not numbers_match("File m3/h.docx", "File m³/h.docx")
