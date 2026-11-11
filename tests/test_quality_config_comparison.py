from __future__ import annotations

import json
from copy import deepcopy
from zipfile import ZipFile

import pytest
from lxml import etree
from openpyxl import Workbook, load_workbook

from forma.core.models import FormaError
from forma.documents.comparison import compare_files, write_comparison
from forma.documents.ooxml import R, S
from forma.settings.config import Settings, save_preferences
from forma.translation.quality import check_segment, parse_translation


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
    source = "<r0>Power 25kW; gap 10мм; pressure 1,25bar; energy 7kWh; flow 12.50m³/h; output 8mW</r0>"
    target = "<r0>Потужність 25кВт; зазор 10mm; тиск 1.25bar; енергія 7 кВт∙год; подача 12,50 м³/год; вихід 8 мВт</r0>"
    assert not [issue for issue in check_segment(source, target) if issue.severity == "error"]
    assert not [issue for issue in check_segment("<r0> кВт </r0>", "<r0> kW </r0>") if issue.severity == "error"]
    assert not [issue for issue in check_segment("<r0>м³/год</r0>", "<r0>m³/h</r0>") if issue.severity == "error"]


def test_verified_quantity_unit_conflicts_keep_prefix_dimension_and_pairing():
    def conflict(source, target):
        issues = check_segment(f"<r0>{source}</r0>", f"<r0>{target}</r0>", "quantity")
        return [(issue.code, issue.severity, issue.segment_id) for issue in issues]

    assert conflict("Power 5kW", "Power 5MW") == [("quantity_units", "error", "quantity")]
    assert conflict("Power 5kW", "Energy 5kWh") == [("quantity_units", "error", "quantity")]
    assert conflict("кВт", "MW") == [("quantity_units", "error", "quantity")]
    assert conflict("kW", "kWh") == [("quantity_units", "error", "quantity")]
    assert conflict("Primary 5kW; reserve 10MW", "Primary 10kW; reserve 5MW") == [
        ("quantity_units", "error", "quantity"),
    ]


def test_unit_validation_does_not_guess_words_currencies_or_identifier_fragments():
    source = "<r0>5kW; 5 megawatts; amount 20 грн; model K5MW and abc5kW</r0>"
    target = "<r0>5 kilowatts; 5MW; сума 20 UAH; модель K5MW та abc5kW</r0>"
    assert not [issue for issue in check_segment(source, target) if issue.severity == "error"]
    assert not check_segment("<r0>кВт</r0>", "<r0>kilowatts</r0>")
    assert not check_segment("<r0>kW label</r0>", "<r0>MW label</r0>")
    assert not [issue for issue in check_segment("<r0>Power 5kW</r0>", "<r0>Power 5</r0>") if issue.code == "quantity_units"]
    source = "<r0>Flow 6 m³ per hour; reserve 9 м³ за годину</r0>"
    target = "<r0>Подача 6 м³/год; резерв 9 m³/h</r0>"
    assert not [issue for issue in check_segment(source, target) if issue.severity == "error"]
    issues = check_segment("<r0>See https://example.org/5kW</r0>", "<r0>Див. https://example.org/5MW</r0>")
    assert [issue.code for issue in issues] == ["protected_values"]


def test_ukrainian_ordinals_and_hour_notation_preserve_value():
    from forma.translation.quality import numbers_match

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


def test_xlsx_comparison_resolves_shared_uses_without_hiding_cell_or_rich_text_damage(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(("Витрата 5", 12.5, "=B1*2"))
    sheet.append(("Витрата 5",))
    sheet["B1"].number_format = "0.00"
    base = tmp_path / "inline-base.xlsx"
    book.save(base)
    with ZipFile(base) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    # Worksheet relationships, rather than conventional storage filenames, bind cells.
    worksheet_part = "xl/custom-sheet.xml"
    worksheet = etree.fromstring(original.pop("xl/worksheets/sheet1.xml"))
    for address in ("A1", "A2"):
        cell = worksheet.find(f'.//{{{S}}}c[@r="{address}"]')
        cell.remove(cell.find(f"{{{S}}}is"))
        cell.set("t", "s")
        etree.SubElement(cell, f"{{{S}}}v").text = "0"
    worksheet.find(f'.//{{{S}}}c[@r="C1"]/{{{S}}}v').text = "25"
    original[worksheet_part] = etree.tostring(worksheet)
    sst = etree.fromstring((
        f'<sst xmlns="{S}" count="2" uniqueCount="1"><si><r><rPr><b/></rPr>'
        '<t>Витрата</t></r><r><t xml:space="preserve"> 5</t></r></si></sst>'
    ).encode())
    original["xl/sharedStrings.xml"] = etree.tostring(sst)
    content_type = "http://schemas.openxmlformats.org/package/2006/content-types"
    types = etree.fromstring(original["[Content_Types].xml"])
    for item in types:
        if item.get("PartName") == "/xl/worksheets/sheet1.xml":
            item.set("PartName", "/" + worksheet_part)
    etree.SubElement(types, f"{{{content_type}}}Override", PartName="/xl/sharedStrings.xml",
                     ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml")
    original["[Content_Types].xml"] = etree.tostring(types)
    rels = etree.fromstring(original["xl/_rels/workbook.xml.rels"])
    for item in rels:
        if item.get("Type") == R + "/worksheet":
            item.set("Target", "custom-sheet.xml")
    etree.SubElement(rels, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
                     Id="rIdShared", Type=R + "/sharedStrings", Target="sharedStrings.xml")
    original["xl/_rels/workbook.xml.rels"] = etree.tostring(rels)

    def save(name, parts):
        path = tmp_path / (name + ".xlsx")
        with ZipFile(path, "w") as archive:
            for part, data in parts.items():
                archive.writestr(part, data)
        return path

    source = save("shared-source", original)
    translated = deepcopy(sst[0])
    translated.find(f"{{{S}}}r/{{{S}}}t").text = "Flow"
    sst.append(translated)
    orphan = etree.SubElement(sst, f"{{{S}}}si")
    etree.SubElement(orphan, f"{{{S}}}t").text = "Unused cached value 999"
    sst.set("uniqueCount", "3")
    for address in ("A1", "A2"):
        worksheet.find(f'.//{{{S}}}c[@r="{address}"]/{{{S}}}v').text = "1"
    cloned = {**original, "xl/sharedStrings.xml": etree.tostring(sst),
              worksheet_part: etree.tostring(worksheet)}
    clone_path = save("appended-shared-clone", cloned)
    inline_sheet = deepcopy(worksheet)
    for address in ("A1", "A2"):
        cell = inline_sheet.find(f'.//{{{S}}}c[@r="{address}"]')
        cell.remove(cell.find(f"{{{S}}}v"))
        cell.set("t", "inlineStr")
        payload = deepcopy(translated)
        payload.tag = f"{{{S}}}is"
        cell.append(payload)
    types.remove(types[-1])
    rels.remove(rels[-1])
    inline = {name: value for name, value in original.items() if name != "xl/sharedStrings.xml"}
    inline.update({worksheet_part: etree.tostring(inline_sheet),
                   "[Content_Types].xml": etree.tostring(types),
                   "xl/_rels/workbook.xml.rels": etree.tostring(rels)})
    inline_path = save("equivalent-inline", inline)
    assert load_workbook(clone_path).active["A1"].value == "Flow 5"
    assert load_workbook(inline_path).active["A2"].value == "Flow 5"
    unchanged = compare_files(source, [clone_path, inline_path])["candidates"]
    assert all(item["structure_ok"] and item["numbers_preserved"] and item["formulas_preserved"]
               and item["text_changed"] for item in unchanged)

    lost_style = deepcopy(sst)
    lost_style[1].find(f"{{{S}}}r/{{{S}}}rPr").remove(lost_style[1].find(f"{{{S}}}r/{{{S}}}rPr/{{{S}}}b"))
    style_path = save("lost-rich-style", {**cloned, "xl/sharedStrings.xml": etree.tostring(lost_style)})
    changed_formula = deepcopy(worksheet)
    changed_formula.find(f'.//{{{S}}}c[@r="C1"]/{{{S}}}f').text = "B1*3"
    formula_path = save("changed-formula", {**cloned, worksheet_part: etree.tostring(changed_formula)})
    changed_cache = deepcopy(worksheet)
    changed_cache.find(f'.//{{{S}}}c[@r="C1"]/{{{S}}}v').text = "123"
    cache_path = save("changed-cache", {**cloned, worksheet_part: etree.tostring(changed_cache)})
    changed_numeric = deepcopy(worksheet)
    changed_numeric.find(f'.//{{{S}}}c[@r="B1"]/{{{S}}}v').text = "99"
    numeric_path = save("changed-number", {**cloned, worksheet_part: etree.tostring(changed_numeric)})
    changed_cell_style = deepcopy(worksheet)
    changed_cell_style.find(f'.//{{{S}}}c[@r="A1"]').set("s", "1")
    cell_style_path = save("changed-cell-style", {**cloned, worksheet_part: etree.tostring(changed_cell_style)})
    damaged = compare_files(source, [style_path, formula_path, cache_path, numeric_path, cell_style_path])["candidates"]
    assert all(not item["structure_ok"] for item in damaged)
    assert not damaged[1]["formulas_preserved"]
    assert damaged[2]["formulas_preserved"]  # Equal formula source does not excuse a changed cached result.

    # Even an unusual shared-string formula cache is immutable display data.
    formula_strings = deepcopy(sst)
    formula_strings[2].find(f"{{{S}}}t").text = "Cached result 7"
    formula_sheet = deepcopy(worksheet)
    row = formula_sheet.find(f'.//{{{S}}}row[@r="1"]')
    cached = etree.SubElement(row, f"{{{S}}}c", r="D1", t="s")
    etree.SubElement(cached, f"{{{S}}}f").text = '"Cached result 7"'
    etree.SubElement(cached, f"{{{S}}}v").text = "2"
    formula_parts = {**cloned, "xl/sharedStrings.xml": etree.tostring(formula_strings),
                     worksheet_part: etree.tostring(formula_sheet)}
    formula_source = save("shared-formula-cache", formula_parts)
    formula_strings[2].find(f"{{{S}}}t").text = "Changed cached text 7"
    formula_other = save("changed-shared-formula-cache", {**formula_parts, "xl/sharedStrings.xml": etree.tostring(formula_strings)})
    assert not compare_files(formula_source, [formula_other])["candidates"][0]["structure_ok"]


def test_script_evidence_never_blocks_bibliography_or_official_names():
    from forma.translation.quality import check_language

    source = "<r0>Smith, J. The International Journal of Advanced Manufacturing Technology. Springer Nature, London.</r0>"
    issues = check_language(source, source, "en", "uk")
    assert issues and all(i.severity == "warning" for i in issues)
    assert not check_language("<r0>Код ТН-42</r0>", "<r0>Code ТН-42</r0>", "uk", "en")
