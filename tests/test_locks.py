import pytest

from forma.core.models import ValidationError, plain_text
from forma.translation.locks import lock_draft, lock_markup, unlock_markup
from forma.translation.quality import check_segment, protected_values


def test_cyrillic_station_codes_and_urls_round_trip():
    source = "<r0>Станція ПНС-2, насос 140Д70 та HM-200; https://example.com/?a=1&amp;b=2</r0>"
    locked, keys = lock_markup(source)
    assert "ПНС-2" not in locked
    assert "140Д70" not in locked
    assert len(keys) == 4
    assert unlock_markup(locked, keys) == source
    draft = source.replace("Станція", "Station").replace("насос", "pump").replace("та", "and")
    assert unlock_markup(lock_draft(draft, keys), keys) == draft
    with pytest.raises(ValidationError):
        unlock_markup(locked.replace("__FORMA_LOCK_0__", "PS-2"), keys)


def test_english_hyphenated_headings_are_not_codes():
    assert not protected_values("ENERGY-AUDIT REPORT")
    assert not check_segment("<r0>Енергетичний аудит</r0>", "<r0>ENERGY-AUDIT REPORT</r0>")
    assert any(
        i.severity == "error"
        for i in check_segment("<r0>Станція НС-20</r0>", "<r0>Station NS-20</r0>")
    )


def test_decimal_normalization_preserves_equipment_tags_and_file_references():
    from forma.translation.locks import english_decimals

    source = "<r0>РУ-0,4: 2,95811 грн; «Звіт_Демо_v2.docx»</r0>"
    locked, keys = lock_markup(source)
    result = unlock_markup(english_decimals(locked), keys)
    assert "РУ-0,4" in result
    assert "2.95811" in result
    assert "Звіт_Демо_v2.docx" in result
    assert not any(i.severity == "error" for i in check_segment(source, result))


def test_invalid_model_markup_is_a_repairable_error():
    from forma.translation.locks import english_decimals

    with pytest.raises(ValidationError):
        english_decimals("<r2>bad</r3>")


def test_curated_terms_preserve_original_identifiers_and_references():
    from forma.translation.terminology import curate_english

    text = "<r0>ТСН 200 кВа, КВПіА, ТН-1А; Звіт_Демо.docx</r0>"
    result, changes = curate_english(text)
    assert "200 kVA" in plain_text(result) and "I&C" in plain_text(result)
    assert "ТН-1А" in result and "Звіт_Демо.docx" in result
    assert changes
