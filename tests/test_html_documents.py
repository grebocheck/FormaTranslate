"""Static HTML translation preserves source syntax and never renders a page."""
from __future__ import annotations

import codecs
from xml.sax.saxutils import escape

import pytest

from forma.core.models import Cancelled, FormaError
from forma.documents.html import HtmlDocument


def document(tmp_path, text, *, encoding="utf-8", bom=b"", suffix=".html"):
    path = tmp_path / ("source" + suffix)
    path.write_bytes(bom + text.encode(encoding))
    return HtmlDocument(path)


def translations(source, replacements=None):
    replacements = replacements or {}
    return {segment.id: "<r0>" + escape(replacements.get(segment.plain, segment.plain)) + "</r0>"
            for segment in source.segments}


def test_identity_keeps_markup_attributes_comments_entities_and_line_endings(tmp_path):
    text = ('<!DOCTYPE HTML>\r\n<HTML><HEAD><meta charset="utf-8"><title>Private heading</title></HEAD>\r\n'
            '<body><p  data-url = \'https://example.invalid/?a=1&amp;b=2\' title="Protected title">'
            ' \tHello &AMP; <b>world</b>&nbsp;! <!-- comment &amp; -->\r\n</p>'
            '<script>const secret = "<p>Private script</p>";</script></body></HTML>')
    source = document(tmp_path, text)
    assert [segment.plain for segment in source.segments] == ["Hello &", "world"]
    assert source.extraction_profile == 7 and source.engine == "forma-text"
    assert source.layout_issues(translations(source)) == []
    output = tmp_path / "identity.htm"
    source.write(translations(source), output)
    assert output.read_bytes() == source.raw
    source.validate_package()
    assert any(issue.code == "html_static_text" for issue in source.issues)


def test_translated_text_is_escaped_and_other_source_bytes_stay_identical(tmp_path):
    text = '<p class = "greeting" title="Unchanged title">  Hello &amp; <b>world</b>&nbsp;! </p>'
    source = document(tmp_path, text, suffix=".htm")
    output = tmp_path / "translated.htm"
    source.write(translations(source, {"Hello &": "Bonjour &", "world": "monde <entier>"}), output)
    assert output.read_text() == '<p class = "greeting" title="Unchanged title">  Bonjour &amp; <b>monde &lt;entier&gt;</b>&nbsp;! </p>'
    assert HtmlDocument(output).structure_signature() == source.structure_signature()


@pytest.mark.parametrize("tag", ["head", "title", "script", "style", "code", "pre", "textarea", "template", "noscript", "iframe", "object", "svg", "math", "xmp", "noembed", "noframes"])
def test_code_metadata_foreign_and_inert_surfaces_are_absent_from_segments_and_context(tmp_path, tag):
    text = f'<{tag}>Private sentinel</{tag}><p>Hello world</p>'
    source = document(tmp_path, text)
    assert [item.plain for item in source.segments] == ["Hello world"]
    assert all("Private sentinel" not in item.context for item in source.segments)
    output = tmp_path / "translated.html"
    source.write(translations(source, {"Hello world": "Bonjour le monde"}), output)
    assert output.read_text() == f'<{tag}>Private sentinel</{tag}><p>Bonjour le monde</p>'


@pytest.mark.parametrize("attributes", [
    'hidden', 'hidden="until-found"', 'inert', 'aria-hidden="true"', 'translate="no"',
    'style="display: none"', 'style="color: red; DISPLAY: NONE !important;"',
    'style="visibility: hidden"', 'style="content-visibility: hidden"',
    'style="display: /* hidden comment */ none"',
])
def test_explicitly_hidden_elements_are_not_sent_to_translation(tmp_path, attributes):
    source = document(tmp_path, f'<div {attributes}><b>Private sentinel</b></div><p>Hello</p>')
    assert [item.plain for item in source.segments] == ["Hello"]
    assert all("Private sentinel" not in item.context for item in source.segments)


def test_raw_text_exclusions_do_not_parse_apparent_child_markup(tmp_path):
    source = document(tmp_path, '<textarea><b>Private without an end tag</textarea><p>Hello</p>')
    assert [item.plain for item in source.segments] == ["Hello"]


def test_optional_end_tags_and_custom_elements_are_supported(tmp_path):
    text = '<!doctype html><html><head><meta charset=utf-8><body><p>Hello<p>Other<ul><li>First<li>Second</ul><table><tr><td>Cell<td>Other cell</table><custom-item>Custom text</custom-item>'
    source = document(tmp_path, text)
    assert [item.plain for item in source.segments] == ["Hello", "Other", "First", "Second", "Cell", "Other cell", "Custom text"]
    output = tmp_path / "identity.html"
    source.write(translations(source), output)
    assert output.read_bytes() == source.raw


def test_omitted_head_end_tag_and_empty_foreign_elements_are_supported(tmp_path):
    source = document(tmp_path, '<html><head><title>Private title</title><main>Hello<svg/><math/></main></html>')
    assert [item.plain for item in source.segments] == ["Hello"]
    assert all("Private title" not in item.context for item in source.segments)


def test_raw_text_areas_keep_literal_declarations_unchanged(tmp_path):
    source = document(tmp_path, '<textarea><!DOCTYPE other> &lt;private&gt;</textarea><p>Hello</p>')
    assert [item.plain for item in source.segments] == ["Hello"]


@pytest.mark.parametrize("text", [
    '<div><b>Text</div></b>', '<div>Unclosed', '<div title="private>Text', '<div foo=bar',
    '</unknown>Text', '<p hidden hidden="false">Text</p>', '<div/>Text', '<!-- unfinished',
    '<!DOCTYPE html [<!ENTITY secret SYSTEM "file:///private">]><p>Text</p>',
])
def test_ambiguous_or_malformed_markup_is_rejected_before_export(tmp_path, text):
    with pytest.raises(FormaError, match="розмітку HTML"):
        document(tmp_path, text)


def test_literal_comparison_signs_and_encoded_markup_are_plain_text(tmp_path):
    source = document(tmp_path, '<p>Value &lt;limit&gt; is &lt; 25 and 1 < 2.</p>')
    assert source.segments[0].plain == "Value <limit> is < 25 and 1 < 2."
    output = tmp_path / "identity.html"
    source.write(translations(source), output)
    assert output.read_bytes() == source.raw


def test_urls_identifiers_and_template_expressions_are_immutable_source_ranges(tmp_path):
    text = ('<p>Hello &#123;&#123;user&#125;&#125; &amp; ${value} {% if ready %}there{% endif %} '
            'https://example.test/a?x=1&amp;y=2 email@example.test AB-123 end</p>')
    source = document(tmp_path, text)
    assert [item.plain for item in source.segments] == ["Hello", "there", "end"]
    for item in source.segments:
        assert all(protected not in item.context for protected in (
            "{{user}}", "${value}", "{% if ready %}", "https://example.test", "email@example.test", "AB-123",
        ))
    output = tmp_path / "translated.html"
    source.write(translations(source, {"Hello": "Bonjour", "there": "ici", "end": "fin"}), output)
    assert output.read_text() == text.replace("Hello", "Bonjour").replace("there", "ici").replace(" end</p>", " fin</p>")


def test_urls_with_entity_encoded_delimiters_are_protected(tmp_path):
    text = '<p>Visit https:&#47;&#47;example.test&#47;path then return</p>'
    source = document(tmp_path, text)
    assert [item.plain for item in source.segments] == ["Visit", "then return"]
    output = tmp_path / "translated.html"
    source.write(translations(source, {"Visit": "Ouvrir", "then return": "puis revenir"}), output)
    assert output.read_text() == text.replace("Visit", "Ouvrir").replace("then return", "puis revenir")


def test_non_http_uri_schemes_remain_immutable(tmp_path):
    source = document(tmp_path, '<p>Visit ftp://example.test/a mailto:user@example.test tel:+380123456789 then return</p>')
    assert [item.plain for item in source.segments] == ["Visit", "then return"]


def test_literal_escaped_script_text_stays_inert_and_actual_new_markup_is_rejected(tmp_path):
    source = document(tmp_path, '<p>Hello</p>')
    segment = source.segments[0]
    output = tmp_path / "translated.html"
    with pytest.raises(FormaError):
        source.write({segment.id: '<r0><script>alert(1)</script></r0>'}, output)
    assert not output.exists()
    source.write(translations(source, {"Hello": '<script>alert("hello")</script>'}), output)
    assert output.read_text() == '<p>&lt;script&gt;alert("hello")&lt;/script&gt;</p>'
    assert '<script>' not in output.read_text()


def test_signature_keeps_attributes_code_whitespace_and_urls_protected(tmp_path):
    source = document(tmp_path, '<p title="Title">Hello https://example.test</p><script>const value=1</script>')
    target = tmp_path / "external.html"
    target.write_text(source.text.replace("Hello", "Bonjour"))
    assert HtmlDocument(target).structure_signature() == source.structure_signature()
    for old, new in [('title="Title"', 'title="Changed"'), ('value=1', 'value=2'), ('https://example.test', 'https://changed.test')]:
        target.write_text(source.text.replace(old, new))
        assert HtmlDocument(target).structure_signature() != source.structure_signature()


def test_comparison_text_includes_numeric_nodes_and_excludes_hidden_and_code(tmp_path):
    source = document(tmp_path, '<p>Hello</p><p>25 &amp; 42</p><p hidden>999</p><code>888</code><script>777</script>')
    assert [item.plain for item in source.segments] == ["Hello"]
    assert source.comparison_text() == "Hello\n25 & 42"


@pytest.mark.parametrize("encoding,bom,declaration", [
    ("utf-8", b"", "utf-8"), ("utf-8", codecs.BOM_UTF8, "utf8"),
    ("utf-16-le", codecs.BOM_UTF16_LE, "utf-16"), ("utf-16-be", codecs.BOM_UTF16_BE, "utf-16be"),
])
def test_declared_unicode_encoding_and_bom_are_preserved(tmp_path, encoding, bom, declaration):
    source = document(tmp_path, f'<meta charset="{declaration}"><p>Hello</p>', encoding=encoding, bom=bom)
    output = tmp_path / "translated.html"
    source.write(translations(source, {"Hello": "Вітаю"}), output)
    assert output.read_bytes() == bom + f'<meta charset="{declaration}"><p>Вітаю</p>'.encode(encoding)


@pytest.mark.parametrize("text,encoding,bom", [
    ('<meta charset="windows-1252"><p>Hello</p>', "utf-8", b""),
    ('<meta charset="utf-16"><p>Hello</p>', "utf-8", b""),
    ('<meta charset="utf-8"><p>Hello</p>', "utf-16-le", codecs.BOM_UTF16_LE),
    ('<meta charset="unknown-encoding"><p>Hello</p>', "utf-8", b""),
    ('<meta charset="utf-8"><meta charset="windows-1251"><p>Hello</p>', "utf-8", b""),
    ('<p>Hello</p>', "utf-32-le", codecs.BOM_UTF32_LE),
])
def test_conflicting_unknown_legacy_and_unsupported_html_encodings_are_rejected(tmp_path, text, encoding, bom):
    with pytest.raises(FormaError, match="Кодування HTML"):
        document(tmp_path, text, encoding=encoding, bom=bom)


def test_http_equiv_charset_is_checked_and_comments_do_not_declare_encodings(tmp_path):
    source = document(tmp_path, '<!-- <meta charset=windows-1251> --><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"><p>Hello</p>')
    assert source.segments[0].plain == "Hello"
    with pytest.raises(FormaError, match="Кодування HTML"):
        document(tmp_path, '<meta http-equiv="Content-Type" content="text/html; charset=windows-1251"><p>Hello</p>')


def test_source_mutation_incomplete_translation_and_collision_never_publish(tmp_path):
    source = document(tmp_path, '<p>Hello</p><p>World</p>')
    output = tmp_path / "translated.html"
    with pytest.raises(FormaError, match="не всі фрагменти"):
        source.write({}, output)
    assert not output.exists()
    output.write_bytes(b"existing user data")
    with pytest.raises(FileExistsError):
        source.write(translations(source), output)
    assert output.read_bytes() == b"existing user data"
    output.unlink()
    source.path.write_text('<p>Changed</p>')
    with pytest.raises(FormaError, match="Оригінал змінився"):
        source.write(translations(source), output)
    assert not output.exists()


def test_cancellation_and_excessive_nesting_are_rejected(tmp_path):
    source = document(tmp_path, '<p>Hello</p>')

    def cancel():
        raise Cancelled()

    source.check_cancel = cancel
    with pytest.raises(Cancelled):
        source.write(translations(source), tmp_path / "cancelled.html")
    assert not (tmp_path / "cancelled.html").exists()
    with pytest.raises(FormaError, match="розмітку HTML"):
        document(tmp_path, '<div>' * 513 + 'Hello' + '</div>' * 513)


@pytest.mark.parametrize("in_attribute", [False, True])
def test_unbounded_numeric_entity_does_not_escape_as_a_raw_parser_exception(tmp_path, in_attribute):
    entity = "&#" + "9" * 5000 + ";"
    text = f'<p title="{entity}">Hello</p>' if in_attribute else f'<p>Hello {entity}</p>'
    with pytest.raises(FormaError, match="розмітку HTML"):
        document(tmp_path, text)
