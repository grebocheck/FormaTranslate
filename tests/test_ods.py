"""Native spreadsheet values and physical repetitions remain source-bound."""
from __future__ import annotations

import json
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from lxml import etree
from support import make_odf

from forma.core.models import FormaError
from forma.documents.odf import DRAW, OFFICE, STYLE, TABLE, TEXT, OpenDocument


def package(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def cell(text="", *, attributes="", kind="string", paragraph=None):
    value_type = f' office:value-type="{kind}"' if kind is not None else ""
    paragraph = f'<text:p>{escape(text)}</text:p>' if paragraph is None else paragraph
    return f'<table:table-cell{value_type} {attributes}>{paragraph}</table:table-cell>'


def sheet(body, *, attributes="", name="Sheet1"):
    return f'<table:table table:name="{name}" {attributes}>{body}</table:table>'


def row(body, *, attributes=""):
    return f'<table:table-row {attributes}>{body}</table:table-row>'


def styles(body):
    return (f'<office:document-styles xmlns:office="{OFFICE}" xmlns:style="{STYLE}" '
            f'xmlns:table="{TABLE}" xmlns:text="{TEXT}"><office:styles>{body}</office:styles></office:document-styles>')


def values(document):
    return {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ")
            for segment in document.segments}


def content(path):
    return etree.fromstring(package(path)["content.xml"])


def test_typed_cells_formulas_caches_and_rich_text_are_preserved(tmp_path):
    rich = ('<text:p>Hello <text:span text:style-name="Strong">world</text:span> tail'
            '<text:s text:c="2"/>space<text:tab/>tab<text:line-break/>line '
            '<text:a xlink:type="simple" xlink:href="https://example.invalid/private">label</text:a>'
            ' page <text:page-number>7</text:page-number> end</text:p>')
    native = [
        cell("Private numeric label", kind="float", attributes='office:value="25.5"'),
        cell("Private percent label", kind="percentage", attributes='office:value="0.5"'),
        cell("Private currency label", kind="currency", attributes='office:value="12.50" office:currency="USD"'),
        cell("Private date label", kind="date", attributes='office:date-value="2026-11-17"'),
        cell("Private time label", kind="time", attributes='office:time-value="PT01H30M"'),
        cell("Private boolean label", kind="boolean", attributes='office:boolean-value="true"'),
        cell("Private formula cache", attributes='table:formula="of:=SECRET()" office:string-value="Private formula cache"'),
        cell("Private number cache", kind="float", attributes='table:formula="of:=1+2" office:value="3"'),
    ]
    source = make_odf(tmp_path / "source.ods", body=sheet(row(cell(paragraph=rich) + "".join(native))))
    document = OpenDocument(source)
    assert document.extraction_profile == 9 and document.engine == "forma-odf"
    assert len(document.segments) == 1
    assert not any("Private" in item.source + item.context for item in document.segments)
    assert document.segments[0].source.count("<k") == 4
    before = package(source)
    identity = tmp_path / "identity.ods"
    document.write({item.id: item.source for item in document.segments}, identity)
    assert package(identity) == before
    output = tmp_path / "translated.ods"
    document.write(values(document), output)
    after = package(output)
    assert {name for name in before if before[name] != after[name]} == {"content.xml"}
    original_cells = content(source).findall(f".//{{{TABLE}}}table-cell")
    translated_cells = content(output).findall(f".//{{{TABLE}}}table-cell")
    for original, translated in zip(original_cells[1:], translated_cells[1:], strict=True):
        assert etree.tostring(original, method="c14n") == etree.tostring(translated, method="c14n")
    assert "Вітаю" in after["content.xml"].decode()
    assert b'text:c="2"' in after["content.xml"]
    assert b'https://example.invalid/private' in after["content.xml"]
    assert OpenDocument(output).structure_signature() == document.structure_signature()


def test_verified_string_value_is_mirrored_but_unpaired_values_are_preserved(tmp_path):
    body = sheet(row(
        cell("Hello world", attributes='office:string-value="Hello world"')
        + cell("Private mismatched display", attributes='office:string-value="Different actual value"')
        + cell(attributes='office:string-value="Attribute only"', paragraph="")
        + cell("123", attributes='office:string-value="123"')
    ))
    source = make_odf(tmp_path / "source.ods", body=body)
    document = OpenDocument(source)
    assert [item.plain for item in document.segments] == ["Hello world"]
    assert any(issue.code == "ods_unpaired" for issue in document.issues)
    output = tmp_path / "translated.ods"
    document.write(values(document), output)
    cells = content(output).findall(f".//{{{TABLE}}}table-cell")
    assert cells[0].get(f"{{{OFFICE}}}string-value") == "Вітаю світ"
    assert cells[1].get(f"{{{OFFICE}}}string-value") == "Different actual value"
    assert cells[2].get(f"{{{OFFICE}}}string-value") == "Attribute only"
    assert cells[3].get(f"{{{OFFICE}}}string-value") == "123"
    assert OpenDocument(output).structure_signature() == document.structure_signature()


def test_billion_row_and_cell_repetitions_are_aggregate_ranges_without_expansion(tmp_path):
    body = sheet('<table:table-column table:number-columns-repeated="1000000000"/>'
                 + row(cell("Hello", attributes='table:number-columns-repeated="1000000000"'),
                       attributes='table:number-rows-repeated="1000000000"')
                 + row(cell("Other text")))
    source = make_odf(tmp_path / "repeated.ods", body=body)
    document = OpenDocument(source)
    assert len(document.segments) == 2
    first, second = [json.loads(item.context) for item in document.segments]
    assert first["row_range"] == first["column_range"] == [1, 1_000_000_000]
    assert first["row_repeats"] == first["column_repeats"] == 1_000_000_000
    assert second["row_range"] == [1_000_000_001, 1_000_000_001]
    assert all(len(item.context) < 4000 for item in document.segments)
    assert any(issue.code == "ods_repeated" for issue in document.issues)
    output = tmp_path / "translated.ods"
    document.write(values(document), output)
    tree = content(output)
    assert len(tree.findall(f".//{{{TABLE}}}table-row")) == 2
    assert len(tree.findall(f".//{{{TABLE}}}table-cell")) == 2
    assert tree.find(f".//{{{TABLE}}}table-row").get(f"{{{TABLE}}}number-rows-repeated") == "1000000000"
    assert tree.find(f".//{{{TABLE}}}table-cell").get(f"{{{TABLE}}}number-columns-repeated") == "1000000000"


def test_hidden_groups_rows_columns_and_shared_hidden_repetitions_do_not_leak(tmp_path):
    columns = ('<table:table-column-group table:display="false"><table:table-column/></table:table-column-group>'
               '<table:table-column table:number-columns-repeated="2"/>')
    rows = ('<table:table-row-group table:display="false">' + row(cell("Private group")) + '</table:table-row-group>'
            + row(cell("Private collapsed"), attributes='table:visibility="collapse"')
            + row(cell("Private filtered"), attributes='table:visibility="filter"')
            + row(cell("Private mixed repetition", attributes='table:number-columns-repeated="2"') + cell("Hello")))
    source = make_odf(tmp_path / "hidden.ods", body=sheet(columns + rows))
    document = OpenDocument(source)
    assert [item.plain for item in document.segments] == ["Hello"]
    assert all("Private" not in item.context for item in document.segments)
    facts = json.loads(document.segments[0].context)
    assert facts["row_range"] == [4, 4] and facts["column_range"] == [3, 3]
    assert any(issue.code == "ods_hidden" for issue in document.issues)


def test_inherited_hidden_sheet_and_cell_styles_are_respected(tmp_path):
    stylesheet = styles(
        '<style:style style:name="HiddenSheet" style:family="table"><style:table-properties table:display="false"/></style:style>'
        '<style:style style:name="InheritedSheet" style:family="table" style:parent-style-name="HiddenSheet"/>'
        '<style:style style:name="ShownSheet" style:family="table" style:parent-style-name="HiddenSheet"><style:table-properties table:display="true"/></style:style>'
        '<style:style style:name="SecretCell" style:family="table-cell"><style:table-cell-properties style:cell-protect="hidden-and-protected"/></style:style>'
    )
    body = (sheet(row(cell("Private sheet")), name="PrivateSheet", attributes='table:style-name="InheritedSheet"')
            + sheet('<table:table-column table:default-cell-style-name="SecretCell"/><table:table-column/>'
                    + row(cell("Private column default") + cell("Hello")), name="Shown", attributes='table:style-name="ShownSheet"'))
    source = make_odf(tmp_path / "styles.ods", body=body, extra_parts={"styles.xml": stylesheet})
    document = OpenDocument(source)
    assert [item.plain for item in document.segments] == ["Hello"]
    assert all("Private" not in item.context for item in document.segments)
    assert json.loads(document.segments[0].context)["sheet"] == "Shown"


def test_column_default_can_override_hidden_global_cell_default(tmp_path):
    stylesheet = styles(
        '<style:default-style style:family="table-cell"><style:table-cell-properties style:cell-protect="hidden-and-protected"/></style:default-style>'
        '<style:style style:name="Visible" style:family="table-cell"><style:table-cell-properties style:cell-protect="none"/></style:style>'
    )
    body = sheet('<table:table-column table:default-cell-style-name="Visible"/>'
                 + row(cell("Hello") + cell("Private default")))
    document = OpenDocument(make_odf(tmp_path / "default.ods", body=body, extra_parts={"styles.xml": stylesheet}))
    assert [item.plain for item in document.segments] == ["Hello"]
    assert all("Private" not in item.context for item in document.segments)


def test_code_cell_styles_and_row_defaults_are_not_sent_to_provider(tmp_path):
    stylesheet = styles(
        '<style:style style:name="Source_20_Text" style:family="table-cell"/>'
        '<style:style style:name="InheritedCode" style:family="table-cell" style:parent-style-name="Source_20_Text"/>'
    )
    body = sheet(row(cell("Private explicit code", attributes='table:style-name="InheritedCode"') + cell("Hello"))
                 + row(cell("Private row code"), attributes='table:default-cell-style-name="Source_20_Text"'))
    document = OpenDocument(make_odf(tmp_path / "code.ods", body=body, extra_parts={"styles.xml": stylesheet}))
    assert [item.plain for item in document.segments] == ["Hello"]
    assert all("Private" not in item.context for item in document.segments)


def test_headers_context_only_uses_visible_text_valued_cells(tmp_path):
    header = '<table:table-header-rows>' + row(cell("Category") + cell("Energy")) + '</table:table-header-rows>'
    body = sheet(header + row(cell("Hello") + cell("Private formula", attributes='table:formula="of:=SECRET()"')))
    document = OpenDocument(make_odf(tmp_path / "context.ods", body=body))
    hello = next(item for item in document.segments if item.plain == "Hello")
    context = json.loads(hello.context)
    assert context["header_kind"] == "declared"
    assert {item["text"] for item in context["headers"]} == {"Category", "Energy"}
    assert "Private" not in hello.context and "SECRET" not in hello.context


def test_notes_drawings_nested_tables_and_code_are_preserved_without_prompt_leak(tmp_path):
    code_style = styles('<style:style style:name="Source_20_Text" style:family="paragraph"/>')
    nested = sheet(row(cell("Private nested table")), name="Nested")
    paragraph = ('<text:p>Hello <text:span text:style-name="Source_20_Text">Private inline code</text:span> world</text:p>'
                 '<text:p text:style-name="Source_20_Text">Private paragraph code</text:p>')
    body = sheet(row(cell(paragraph=paragraph + '<office:annotation><text:p>Private note</text:p></office:annotation>' + nested))
                 + '<table:shapes><draw:frame><draw:object xlink:href="Object 1"/></draw:frame></table:shapes>')
    source = make_odf(tmp_path / "objects.ods", body=body, extra_parts={"styles.xml": code_style})
    document = OpenDocument(source)
    assert len(document.segments) == 1
    assert all("Private" not in item.source + item.context for item in document.segments)
    assert any(issue.code == "ods_complex" for issue in document.issues)
    output = tmp_path / "translated.ods"
    document.write(values(document), output)
    original, translated = content(source), content(output)
    for tag in [f"{{{OFFICE}}}annotation", f"{{{DRAW}}}object"]:
        assert etree.tostring(original.find(f".//{tag}"), method="c14n") == etree.tostring(translated.find(f".//{tag}"), method="c14n")
    assert "Private inline code" in package(output)["content.xml"].decode()


@pytest.mark.parametrize("attribute,value,target", [
    ("number-rows-repeated", "0", "row"), ("number-rows-repeated", "-1", "row"),
    ("number-columns-repeated", "1.5", "cell"), ("number-columns-repeated", "bad", "column"),
    ("number-rows-spanned", "0", "cell"), ("number-columns-spanned", "-2", "cell"),
    ("number-columns-repeated", "9" * 100, "cell"),
])
def test_invalid_or_unbounded_repeat_and_merge_counters_are_rejected(tmp_path, attribute, value, target):
    attr = f'table:{attribute}="{value}"'
    column = f'<table:table-column {attr}/>' if target == "column" else ""
    cells = cell("Hello", attributes=attr if target == "cell" else "")
    body = sheet(column + row(cells, attributes=attr if target == "row" else ""))
    with pytest.raises(FormaError, match="лічильники"):
        OpenDocument(make_odf(tmp_path / "bad.ods", body=body))


def test_repeated_rows_with_vertical_merges_and_style_cycles_are_rejected(tmp_path):
    body = sheet(row(cell("Hello", attributes='table:number-rows-spanned="2"'), attributes='table:number-rows-repeated="2"'))
    with pytest.raises(FormaError, match="лічильники"):
        OpenDocument(make_odf(tmp_path / "bad.ods", body=body))
    cycle = styles('<style:style style:name="A" style:family="table-cell" style:parent-style-name="B"/>'
                   '<style:style style:name="B" style:family="table-cell" style:parent-style-name="A"/>')
    body = sheet(row(cell("Hello", attributes='table:style-name="A"')))
    with pytest.raises(FormaError, match="структуру"):
        OpenDocument(make_odf(tmp_path / "cycle.ods", body=body, extra_parts={"styles.xml": cycle}))


def test_malformed_translation_and_external_hrefs_cannot_change_markup(tmp_path):
    paragraph = '<text:p>Hello<text:s/>world <text:a xlink:href="https://example.invalid/unchanged">label</text:a></text:p>'
    source = make_odf(tmp_path / "source.ods", body=sheet(row(cell(paragraph=paragraph))))
    document = OpenDocument(source)
    item = document.segments[0]
    output = tmp_path / "bad.ods"
    with pytest.raises(FormaError):
        document.write({item.id: '<r0><script>new markup</script></r0>'}, output)
    assert not output.exists()
    with pytest.raises(FormaError):
        document.write({item.id: item.source.replace('<k0/>', '')}, output)
    assert not output.exists()


def test_comparison_includes_native_and_numeric_only_values_without_provider_segments(tmp_path):
    source = make_odf(tmp_path / "values.ods", body=sheet(row(
        cell("Visible number", kind="float", attributes='office:value="25.5"')
        + cell("123") + cell("Private", kind="float", attributes='office:value="999" table:visibility="collapse"')
    )))
    document = OpenDocument(source)
    assert document.segments == []
    assert "25.5" in document.comparison_text() and "123" in document.comparison_text()
    assert "999" not in document.comparison_text()
