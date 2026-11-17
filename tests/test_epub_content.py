"""EPUB content remains inert XML with byte-preserved markup and metadata."""
from __future__ import annotations

import codecs
import gc
import html
import struct
import weakref
from zipfile import ZIP_STORED, BadZipFile, ZipFile

import pytest
from epub_support import CHAPTER, NAV, NCX, NCX_DOCTYPE, XHTML_DOCTYPE, make_epub, replace_epub_part
from lxml import etree

from forma.core.models import Cancelled, FormaError
from forma.documents.epub import EpubDocument
from forma.documents.epub_content import EpubContent, candidate_issues, validate_content_xml

XHTML = "http://www.w3.org/1999/xhtml"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"


def xhtml(body, *, head="<title>PRIVATE_TITLE</title>", doctype="", declaration="", prefix=""):
    q = prefix + ":" if prefix else ""
    xmlns = f'xmlns:{prefix}' if prefix else 'xmlns'
    return (declaration + doctype + f'<{q}html {xmlns}="{XHTML}"><{q}head>{head}</{q}head>'
            f'<{q}body>{body}</{q}body></{q}html>').encode("utf-8")


def values(document):
    return {segment.id: segment.source for segment in document.segments}


@pytest.mark.parametrize("role,raw,count", [("spine", CHAPTER, 6), ("nav", NAV, 3), ("ncx", NCX, 2)])
def test_epub_content_identity_and_rich_changes_preserve_metadata_bytes(role, raw, count):
    document = EpubContent(raw.encode(), part="PRIVATE_DIRECTORY/PRIVATE_CHAPTER.xhtml", role=role)
    assert len(document.segments) == count
    assert all(segment.id.startswith("epub-v13:") for segment in document.segments)
    model_input = str([(segment.id, segment.location, segment.source, segment.context) for segment in document.segments])
    assert "PRIVATE" not in model_input
    assert document.write(values(document)) == raw.encode()
    translations = {key: value.replace("Hello", "Вітаю").replace("chapter", "розділ").replace("First", "Перший") for key, value in values(document).items()}
    output = document.write(translations)
    assert EpubContent(output, part=document.part, role=role).structure_signature() == document.structure_signature()
    assert (b"PRIVATE_NAV_TITLE" if role == "nav" else b"PRIVATE_TITLE") in output
    assert document.write(translations) == output


def test_epub_one_paragraph_uses_r_for_styles_and_k_only_for_real_br():
    raw = xhtml('<p id="PRIVATE_ID">Hello <b>beautiful <i>world</i></b> and <a href="private.xhtml#x">friends</a>.<br/>Next line.</p>')
    document = EpubContent(raw, part="x.xhtml")
    assert len(document.segments) == 1
    segment = document.segments[0]
    assert segment.source.count("<k") == 1 and segment.plain == "Hello beautiful world and friends.\nNext line."
    assert "private.xhtml" not in segment.source + segment.context
    output = document.write({segment.id: segment.source.replace("Hello", "Вітаю")})
    assert output == raw.replace(b"Hello", "Вітаю".encode())


def test_epub_prefix_namespaces_comments_pi_and_unchanged_prose_entities_are_exact():
    raw = xhtml('<h:p>Hello<!-- PRIVATE_COMMENT --><h:em>world</h:em> &#169; &#13; &amp; friends.</h:p>', prefix="h",
                head='<h:title>PRIVATE</h:title>', declaration='<?xml version="1.0" encoding="UTF-8"?>\r\n<?xml-stylesheet href="private.css"?>\r\n')
    document = EpubContent(raw, part="x.xhtml")
    assert document.segments[0].plain == "Helloworld © \n & friends."
    assert document.write(values(document)) == raw
    output = document.write({key: value.replace("Hello", "Вітаю") for key, value in values(document).items()})
    assert b"<!-- PRIVATE_COMMENT -->" in output and b'<?xml-stylesheet href="private.css"?>' in output


@pytest.mark.parametrize("reference,visible", [("&nbsp;", "\u00a0"), ("&copy;", "©"), ("&euro;", "€"), ("&Alpha;", "Α"), ("&#x1F680;", "🚀")])
def test_epub2_trusted_xhtml_dtd_resolves_standard_names_offline_without_rewriting(reference, visible, monkeypatch):
    import forma.documents.epub_content as module

    real_parser = etree.XMLParser

    class NoExternal(etree.Resolver):
        def resolve(self, *args):
            pytest.fail("EPUB parsing must never load a DTD or external entity")

    def parser(*args, **kwargs):
        result = real_parser(*args, **kwargs)
        result.resolvers.add(NoExternal())
        return result

    monkeypatch.setattr(module.etree, "XMLParser", parser)
    raw = xhtml("<p>Hello " + reference + " world.</p>", doctype=XHTML_DOCTYPE + "\n")
    document = EpubContent(raw, part="chapter.xhtml")
    assert visible in document.segments[0].plain
    assert document.write(values(document)) == raw


@pytest.mark.parametrize("doctype", [
    '<!DOCTYPE html SYSTEM "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">',
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "https://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
])
def test_epub_common_trusted_external_doctypes_remain_literal(doctype):
    raw = xhtml("<p>Hello &copy; world.</p>", doctype=doctype)
    document = EpubContent(raw, part="x.xhtml")
    assert document.write(values(document)) == raw


@pytest.mark.parametrize("doctype,body", [
    ('<!DOCTYPE html [<!ENTITY private "PRIVATE">]>', "<p>Hello &private;</p>"),
    ('<!DOCTYPE html [<!ENTITY private SYSTEM "file:///etc/passwd">]>', "<p>Hello &private;</p>"),
    ('<!DOCTYPE html [<!ENTITY % remote SYSTEM "https://private.invalid/entity"> %remote;]>', "<p>Hello</p>"),
    ('<!DOCTYPE html SYSTEM "https://private.invalid/xhtml.dtd">', "<p>Hello</p>"),
    ('<!DOCTYPE html PUBLIC "PRIVATE" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">', "<p>Hello</p>"),
    ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "file:///xhtml11.dtd">', "<p>Hello</p>"),
    ("", "<p>Hello &nbsp;</p>"), ("<!DOCTYPE html>", "<p>Hello &copy;</p>"),
    (XHTML_DOCTYPE, "<p>Hello &custom;</p>"), (XHTML_DOCTYPE, "<p>Hello &NotEqualTilde;</p>"),
])
def test_epub_rejects_custom_internal_and_unresolved_entities(doctype, body):
    with pytest.raises(FormaError):
        EpubContent(xhtml(body, doctype=doctype), part="x.xhtml")


@pytest.mark.parametrize("raw", [
    b"<html><body><p>Hello</p></body></html>",
    xhtml("<p>Hello</b>"), xhtml("<p>Hello & broken</p>"),
    xhtml("<p>Hello &#0;</p>"), xhtml("<p>Hello &#xD800;</p>"),
    xhtml("<p>Hello &#" + "9" * 5000 + ";</p>"),
    xhtml("<p>Hello</p>", doctype=XHTML_DOCTYPE + XHTML_DOCTYPE),
    xhtml("<p>Hello</p>" + XHTML_DOCTYPE),
    xhtml("<p>Hello<br>PRIVATE</br></p>"),
    xhtml("<p>Hello</p>", declaration='<?xml version="1.1"?>'),
    xhtml('<p hidden="a" hidden="b">Hello</p>'),
])
def test_epub_malformed_xml_has_a_safe_preflight_error(raw):
    with pytest.raises(FormaError):
        EpubContent(raw, part="x.xhtml")


@pytest.mark.parametrize("encoding,bom,label", [
    ("utf-8", b"", "UTF-8"), ("utf-8", codecs.BOM_UTF8, "utf8"),
    ("utf-16-le", codecs.BOM_UTF16_LE, "UTF-16"), ("utf-16-be", codecs.BOM_UTF16_BE, "UTF-16BE"),
])
def test_epub_encoding_bom_declaration_and_crlf_survive_edits(encoding, bom, label):
    text = xhtml("<p>Hello\r\nworld &amp; friends.</p>", declaration=f'<?xml\nversion="1.0" encoding="{label}"?>\r\n').decode()
    raw = bom + text.encode(encoding)
    document = EpubContent(raw, part="x.xhtml")
    assert document.write(values(document)) == raw
    output = document.write({key: value.replace("Hello", "Вітаю") for key, value in values(document).items()})
    assert output == bom + text.replace("Hello", "Вітаю").encode(encoding)


@pytest.mark.parametrize("raw", [
    codecs.BOM_UTF32_LE + xhtml("<p>Hello</p>").decode().encode("utf-32-le"),
    codecs.BOM_UTF16_LE + xhtml("<p>Hello</p>", declaration='<?xml version="1.0" encoding="UTF-8"?>').decode().encode("utf-16-le"),
    xhtml("<p>Hello</p>", declaration='<?xml version="1.0" encoding="UTF-16"?>'),
    xhtml("<p>Hello</p>", declaration='<?xml version="1.0" encoding="ISO-8859-1"?>'),
    b"\xff" + xhtml("<p>Hello</p>"),
])
def test_epub_rejects_encoding_guesses_or_contradictions(raw):
    with pytest.raises(FormaError):
        EpubContent(raw, part="x.xhtml")


def test_epub_hidden_code_cdata_foreign_templates_and_ids_never_enter_prompts():
    raw = xhtml(
        '<p>Hello <code>PRIVATE_CODE()</code> world.</p>'
        '<p hidden="hidden">PRIVATE_HIDDEN</p><p style="display:/* x */none">PRIVATE_CSS_HIDDEN</p>'
        '<p aria-hidden="true">PRIVATE_ARIA</p><div inert="inert">PRIVATE_INERT</div>'
        '<p translate="no">PRIVATE_TRANSLATE</p><pre>PRIVATE_PRE</pre>'
        '<p style="white-space:pre-wrap">PRIVATE_PRESERVE</p><p xml:space="preserve">PRIVATE_XML_SPACE</p>'
        '<p>Read <![CDATA[PRIVATE_CDATA]]> safely.</p>'
        '<p>See <svg xmlns="http://www.w3.org/2000/svg"><text>PRIVATE_SVG</text></svg> today.</p>'
        '<p>Use <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>PRIVATE_MATH</mi></math> carefully.</p>'
        '<p>Dear {{PRIVATE_TEMPLATE}}, use ${PRIVATE_VARIABLE} and {% PRIVATE_STATEMENT %} now.</p>'
        '<p>Cross {{<em>PRIVATE_STYLED</em>}} boundary.</p>'
        '<p>Read https://pri<em>vate.invalid</em>/PRIVATE_URL then contact PRIVATE_NAME&#64;example.invalid.</p>'
        '<p>Keep PRIVATE_A42 intact.</p>'
    )
    document = EpubContent(raw, part="private.xhtml")
    model_input = str([(segment.source, segment.context) for segment in document.segments])
    assert "PRIVATE" not in model_input and "vate.invalid" not in model_input and "example.invalid" not in model_input
    assert all("<k" not in segment.source for segment in document.segments)
    assert document.write(values(document)) == raw
    changed = document.write({key: value.replace("Hello", "Вітаю").replace("Read", "Читайте") for key, value in values(document).items()})
    assert changed.count(b"PRIVATE_") == raw.count(b"PRIVATE_")


def test_epub_hidden_navigation_labels_remain_reader_navigation_data():
    raw = xhtml('<p hidden="hidden">PRIVATE_PROSE</p>'
                '<nav xmlns:e="http://www.idpf.org/2007/ops" e:type="toc" hidden="hidden">'
                '<h1>Contents</h1><ol style="display:none"><li><a href="PRIVATE.xhtml">First chapter</a></li>'
                '<li translate="no"><a href="x.xhtml">PRIVATE_LABEL</a></li></ol></nav>'
                '<nav hidden="hidden"><p>PRIVATE_UNRECOGNIZED_NAV</p></nav>')
    document = EpubContent(raw, part="nav.xhtml", role="nav")
    assert [segment.plain for segment in document.segments] == ["Contents", "First chapter"]
    assert "PRIVATE" not in str([(segment.source, segment.context) for segment in document.segments])
    assert not EpubContent(raw, part="ordinary.xhtml", role="spine").segments
    assert document.write(values(document)) == raw


def test_epub_nested_blocks_keep_source_order_and_do_not_join_separated_prose():
    raw = xhtml('Before<div>Middle<p>Deep</p>End middle</div>After<p>Last</p>')
    document = EpubContent(raw, part="x.xhtml")
    assert [segment.plain for segment in document.segments] == ["Before", "Middle", "Deep", "End middle", "After", "Last"]
    assert document.comparison_text().splitlines() == ["Before", "Middle", "Deep", "End middle", "After", "Last"]
    assert document.write(values(document)) == raw


def test_epub_ncx_labels_only_from_real_navigation_trees_are_eligible():
    raw = (f'{NCX_DOCTYPE}<ncx xmlns="{NCX_NS}"><docTitle><text>PRIVATE_TITLE</text>'
           '<navPoint><navLabel><text>PRIVATE_FAKE_NAVIGATION</text></navLabel></navPoint></docTitle>'
           '<navMap><navPoint><navLabel><text>Chapter one</text></navLabel></navPoint>'
           '<navPoint translate="no"><navLabel><text>PRIVATE_TRANSLATE</text></navLabel></navPoint></navMap>'
           '<pageList><pageTarget><navLabel><text>Page 42</text></navLabel></pageTarget></pageList>'
           '<navList><navTarget><navLabel><text>Figures</text></navLabel></navTarget></navList></ncx>').encode()
    document = EpubContent(raw, part="toc.ncx", role="ncx")
    assert [segment.plain for segment in document.segments] == ["Chapter one", "Page 42", "Figures"]
    assert "PRIVATE" not in str([(segment.source, segment.context) for segment in document.segments])
    assert document.write(values(document)) == raw


def test_epub_numeric_only_content_is_compared_and_bound_to_structure():
    raw = xhtml("<p>Hello world.</p><p>42</p>")
    document = EpubContent(raw, part="x.xhtml")
    assert len(document.segments) == 1 and "42" in document.comparison_text()
    assert EpubContent(raw.replace(b"42", b"43"), part="x.xhtml").structure_signature() != document.structure_signature()


@pytest.mark.parametrize("value", ["12345", "Hello https://new.invalid/private", "Hello {{new}}", "Hello ${variable}", "Hello\x00world", "Hello\ud800world"])
def test_epub_pure_candidate_checks_reject_lost_text_protected_syntax_and_invalid_unicode(value):
    document = EpubContent(xhtml("<p>Hello world</p>"), part="x.xhtml")
    segment = document.segments[0]
    candidate = "<r0>" + html.escape(value, quote=False) + "</r0>"
    assert candidate_issues(segment, candidate)
    with pytest.raises(FormaError):
        document.write({segment.id: candidate})


def test_epub_new_literal_markup_is_escaped_and_rich_style_redistribution_is_valid():
    document = EpubContent(xhtml("<p>Hello <em>world</em>.</p>"), part="x.xhtml")
    segment = document.segments[0]
    candidate = '<r0>Вітаю &lt;script&gt;літерал&lt;/script&gt; </r0><r1>світ</r1><r2>.</r2>'
    assert not candidate_issues(segment, candidate)
    output = document.write({segment.id: candidate})
    assert b"&lt;script&gt;" in output and validate_content_xml(output).find(f".//{{{XHTML}}}script") is None
    assert EpubContent(output, part="x.xhtml").structure_signature() == document.structure_signature()


def test_epub_bounds_cancellation_and_complete_translation_map(monkeypatch):
    import forma.documents.epub_content as module

    with pytest.raises(FormaError, match="завеликий"):
        EpubContent(xhtml("<p>" + "Hello " * 4000 + "</p>"), part="x.xhtml")
    with pytest.raises(FormaError, match="стилів"):
        EpubContent(xhtml("<p>" + "<b>Hello</b>" * 257 + "</p>"), part="x.xhtml")
    monkeypatch.setattr(module, "MAX_CONTENT_TOKENS", 3)
    with pytest.raises(FormaError):
        EpubContent(xhtml("<p>Hello</p>"), part="x.xhtml")
    monkeypatch.setattr(module, "MAX_CONTENT_TOKENS", 200000)

    def cancel():
        raise Cancelled()

    with pytest.raises(Cancelled):
        EpubContent(xhtml("<p>Hello</p>"), part="x.xhtml", check_cancel=cancel)
    document = EpubContent(xhtml("<p>Hello</p>"), part="x.xhtml")
    with pytest.raises(FormaError, match="фрагменти"):
        document.write({})
    document.check_cancel = cancel
    with pytest.raises(Cancelled):
        document.write(values(document))


@pytest.mark.parametrize("part,closing", [("OPS/package.opf", b"</metadata>"), ("OPS/chapter.xhtml", b"</body>"), ("OPS/toc.ncx", b"</ncx>")])
def test_epub_embedded_xml_signatures_rejected_before_translation(tmp_path, part, closing):
    path = make_epub(tmp_path / "signed.epub")
    signature = b'<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:SignedInfo/></ds:Signature>'
    replace_epub_part(path, part, lambda raw: raw.replace(closing, signature + closing))
    with pytest.raises(FormaError, match="цифровий підпис"):
        EpubDocument(path)


def test_epub_literal_escaped_cdata_and_comment_signatures_are_inert_book_content():
    signature = '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"/>'
    raw = xhtml('<p>Hello ' + html.escape(signature, quote=False) + ' world.</p>'
                '<p>Read <![CDATA[' + signature + ']]> safely.</p><!--' + signature + '-->')
    document = EpubContent(raw, part="x.xhtml")
    assert document.segments and document.write(values(document)) == raw


def test_completed_epub_content_releases_lexical_tokens_and_entity_offset_maps(monkeypatch):
    import forma.documents.epub_content as module

    original, observed = module._tokens, []

    def tokens(*args):
        for token in original(*args):
            observed.append(weakref.ref(token))
            yield token

    monkeypatch.setattr(module, "_tokens", tokens)
    raw = xhtml('<p>Hello <em>world &amp; friends</em> &#169;.</p>' * 50)
    document = EpubContent(raw, part="x.xhtml")
    gc.collect()
    assert observed and all(reference() is None for reference in observed)
    assert all(not run.maps and not run.map_starts for targets in document.targets.values() for run in targets)
    assert document.write(values(document)) == raw


def test_epub_facade_sanitizes_stored_unused_asset_crc_errors_and_cleans_output(tmp_path):
    path = make_epub(tmp_path / "source.epub", extra_parts={"OPS/unused.bin": b"original opaque asset"})
    with ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with ZipFile(path, "w") as archive:
        for info, raw in entries:
            info.compress_type = ZIP_STORED
            archive.writestr(info, raw)
    with ZipFile(path) as archive:
        info = archive.getinfo("OPS/unused.bin")
    raw = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", raw, info.header_offset + 26)
    raw[info.header_offset + 30 + name_length + extra_length] ^= 1
    path.write_bytes(raw)
    document = EpubDocument(path)
    with pytest.raises(FormaError, match="цілісність"):
        document.structure_signature()
    output = tmp_path / "invalid.epub"
    with pytest.raises(FormaError, match="цілісність"):
        document.write(values(document), output)
    assert not output.exists() and not list(tmp_path.glob(".forma-*"))


@pytest.mark.parametrize("operation", ["structure_signature", "write"])
def test_epub_cancellation_wins_a_concurrent_zip_transport_error(tmp_path, monkeypatch, operation):
    path = make_epub(tmp_path / "source.epub", extra_parts={"OPS/unused.bin": b"opaque asset"})
    document = EpubDocument(path)
    original, cancelled = ZipFile.open, False

    def check_cancel():
        if cancelled:
            raise Cancelled()

    def open_resource(archive, name, *args, **kwargs):
        nonlocal cancelled
        if getattr(name, "filename", name) == "OPS/unused.bin":
            cancelled = True
            raise BadZipFile("simultaneous offline transport error")
        return original(archive, name, *args, **kwargs)

    document.check_cancel = check_cancel
    monkeypatch.setattr(ZipFile, "open", open_resource)
    with pytest.raises(Cancelled):
        if operation == "write":
            document.write(values(document), tmp_path / "cancelled.epub")
        else:
            document.structure_signature()
    assert not (tmp_path / "cancelled.epub").exists() and not list(tmp_path.glob(".forma-*"))
