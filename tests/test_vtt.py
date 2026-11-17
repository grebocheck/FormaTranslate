"""Static WebVTT: visible caption edits cannot change timing or opaque metadata."""
from __future__ import annotations

import codecs
import html

import pytest
from subtitle_support import CASES, write_source

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.comparison import compare_files
from forma.documents.subtitles import candidate_issues
from forma.documents.vtt import VttDocument
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


def cue(payload="Hello world", *, identifier="", start="00:01.000", end="00:04.000", settings=""):
    return (identifier + "\n" if identifier else "") + f"{start} --> {end}" + (" " + settings if settings else "") + "\n" + payload + "\n\n"


def source(tmp_path, text, *, prefix="WEBVTT\n\n", bom=b""):
    path = tmp_path / "source.vtt"
    path.write_bytes(bom + (prefix + text).encode("utf-8"))
    return path


def values(document):
    return {segment.id: segment.source for segment in document.segments}


def test_vtt_rich_roundtrip_preserves_every_metadata_byte_and_excludes_it_from_context(tmp_path):
    _, _, text = next(item for item in CASES if item[0] == ".vtt")
    text = text.replace("Hello again.", "<ruby>Hello<rt>reading</ruby> <00:06.000>again &amp; friends.")
    path = write_source(tmp_path, ".vtt", text)
    document = VttDocument(path)
    assert document.extraction_profile == 12 and document.engine == "forma-text"
    assert len(document.segments) == 2
    model_input = "\n".join(segment.source + segment.context for segment in document.segments)
    assert "PRIVATE_" not in model_input and "Private comment" not in model_input
    assert "00:06.000" not in model_input and "align:start" not in model_input
    assert "Hello" in model_input and "reading" in model_input
    identity = tmp_path / "identity.vtt"
    document.write(values(document), identity)
    assert identity.read_bytes() == path.read_bytes()
    translations = {key: value.replace("Hello", "Вітаю").replace("world", "світ") for key, value in values(document).items()}
    output = tmp_path / "translated.vtt"
    document.write(translations, output)
    assert output.read_bytes() == codecs.BOM_UTF8 + text.replace("Hello", "Вітаю").replace("world", "світ").encode("utf-8")
    assert VttDocument(output).structure_signature() == document.structure_signature()
    second = tmp_path / "second.vtt"
    document.write(translations, second)
    assert second.read_bytes() == output.read_bytes()


@pytest.mark.parametrize("reference,visible", [
    ("&amp;", "&"), ("&copy;", "©"), ("&#169;", "©"), ("&#x1F680;", "🚀"),
    ("&NotEqualTilde;", "≂̸"), ("&nbsp;", "\u00a0"), ("&unknown;", "&unknown;"),
])
def test_vtt_current_character_references_decode_like_html_and_identity_keeps_spelling(tmp_path, reference, visible):
    path = source(tmp_path, cue("Hello " + reference + " world"))
    document = VttDocument(path)
    assert document.comparison_text() == "Hello " + visible + " world"
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()


def test_vtt_changed_text_escapes_literal_markup_without_creating_new_tags(tmp_path):
    path = source(tmp_path, cue("Hello &apos;friend&apos; &amp; world"))
    document = VttDocument(path)
    segment = document.segments[0]
    translated = "<r0>" + html.escape("Вітаю <script>літерал</script> & світ", quote=False) + "</r0>"
    assert not candidate_issues(segment, translated)
    output = tmp_path / "out.vtt"
    document.write({segment.id: translated}, output)
    assert "&lt;script&gt;літерал&lt;/script&gt;" in output.read_text()
    assert VttDocument(output).comparison_text() == "Вітаю <script>літерал</script> & світ"
    assert VttDocument(output).structure_signature() == document.structure_signature()


@pytest.mark.parametrize("payload", [
    "<v Roger O'Brien>Hello world", '<v Roger "B" &amp; Єва>Hello world</v>',
    "<v.voice Roger><b><i><u>Hello</u></i></b> world</v>",
    "<c.white.bg_black>Hello world</c>", "<c.a'b>Hello world</c>", "<lang en-US>Hello world</lang>",
    "<ruby>Hello<rt>reading</rt></ruby>", "<ruby>Hello<rt>reading</ruby>",
    "<ruby>Hello<rt>reading</rt>world<rt>next</ruby>",
    "Hello <00:02.000>world <00:03.000>again", "<v Roger>Hello\nworld",
])
def test_vtt_supported_inline_syntax_preserves_tags_and_annotations(tmp_path, payload):
    path = source(tmp_path, cue(payload))
    document = VttDocument(path)
    assert len(document.segments) == 1
    output = tmp_path / "identity.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()
    assert "Roger" not in document.segments[0].context


@pytest.mark.parametrize("payload", [
    "<script>Hello</script>", "<a href=https://private.invalid>Hello</a>",
    "<i class=private>Hello</i>", "<b>Hello</i>", "<b><i>Hello</b></i>",
    "<b>Hello", "Hello</b>", "<I>Hello</I>", "<i/>Hello", "<b >Hello</b>",
    "<c.>Hello</c>", "<c.a..b>Hello</c>", "<c.a&amp;b>Hello</c>",
    "<v>Hello</v>", "<v   >Hello</v>", "Hello <v Roger>world", "<lang>Hello</lang>",
    "<lang en US>Hello</lang>", "<lang en_US>Hello</lang>", "<rt>Hello</rt>",
    "<ruby>Hello</ruby>", "<ruby><i><rt>Hello</rt></i></ruby>",
    "<ruby>Hello<rt>reading</rt>unannotated base</ruby>",
    "<ruby>Hello<rt>reading</rt><b>unannotated base</b></ruby>",
    "Hello <00:01.000>world", "Hello <00:04.000>world", "Hello <00:05.000>world",
    "Hello <00:03.000>world <00:02.000>again", "Hello <00:02.000>world <00:02.000>again",
    "Hello <00:60.000>world", "Hello <1:02.000>world", "Hello <not closed", "Hello --> world",
])
def test_vtt_rejects_unsupported_or_ambiguous_payload_markup(tmp_path, payload):
    path = source(tmp_path, cue(payload))
    with pytest.raises(FormaError):
        VttDocument(path)


@pytest.mark.parametrize("start,end", [
    ("00:01.000", "00:01.000"), ("00:03.000", "00:02.000"),
    ("00:60.000", "01:00.000"), ("60:01.000", "61:01.000"),
    ("0:01.000", "00:04.000"), ("00:01,000", "00:04.000"),
    ("00:01.00", "00:04.000"), ("00:00:01.0000", "00:00:04.000"),
    ("-00:01.000", "00:04.000"), ("0000000000:00:01.000", "0000000000:00:04.000"),
])
def test_vtt_rejects_unsafe_clocks(tmp_path, start, end):
    with pytest.raises(FormaError):
        VttDocument(source(tmp_path, cue(start=start, end=end)))


def test_vtt_overlapping_cues_and_long_hour_clocks_are_legal_but_starts_remain_ordered(tmp_path):
    text = cue("Hello world", start="100:00:01.000", end="100:00:08.000")
    text += cue("Other caption", start="100:00:02.000", end="100:00:04.000")
    document = VttDocument(source(tmp_path, text))
    assert document.cues[0].start_ms == 360001000
    assert len(document.segments) == 2 and document.cues[1].start_ms < document.cues[0].end_ms
    with pytest.raises(FormaError):
        VttDocument(source(tmp_path, cue(start="00:02.000") + cue(start="00:01.000")))


@pytest.mark.parametrize("settings", [
    "vertical:rl line:-1,start position:25%,line-left size:50% align:start region:PRIVATE_REGION",
    "vertical:lr line:100%,end position:0%,line-right size:0% align:right",
    "line:2 position:50%,center size:100.0% align:center future-setting:PRIVATE_VALUE",
])
def test_vtt_settings_are_immutable_and_excluded_from_model_inputs(tmp_path, settings):
    path = source(tmp_path, cue(settings=settings))
    document = VttDocument(path)
    assert "PRIVATE_" not in str([(segment.source, segment.context) for segment in document.segments])
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("settings", [
    "align:start align:end", "vertical:horizontal", "align:auto", "line:auto", "line:101%",
    "line:1,line-left", "position:1", "position:-1%", "position:50%,start", "size:100.1%",
    "size:NaN%", "size:50", "region:", "opaque", "future:value future:other",
])
def test_vtt_invalid_or_duplicate_settings_rejected(tmp_path, settings):
    with pytest.raises(FormaError):
        VttDocument(source(tmp_path, cue(settings=settings)))


def test_vtt_regions_notes_header_metadata_and_late_reserved_cue_ids(tmp_path):
    prefix = "WEBVTT producer header\nKind: captions\nLanguage: en\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\n\n"
    blocks = ("REGION\nid:PRIVATE_REGION\nwidth:45.5%\nlines:3\nregionanchor:0%,100%\nviewportanchor:10%,90%\nscroll:up\n\n"
              "STYLE\n::cue { background-image: url(https://private.invalid/image.png); }\n\n"
              "NOTE PRIVATE_NOTE\nPrivate comment.\n\n")
    path = source(tmp_path, blocks + cue() + "NOTE after cues\nPrivate later comment.\n\n"
                  + cue("Other caption", identifier="STYLE", start="00:05.000", end="00:08.000")
                  + cue("Last caption", identifier="REGION", start="00:09.000", end="00:12.000"), prefix=prefix)
    document = VttDocument(path)
    assert len(document.segments) == 3 and [cue.identifier for cue in document.cues] == ["", "STYLE", "REGION"]
    model_input = str([(segment.source, segment.context) for segment in document.segments])
    assert "PRIVATE_" not in model_input and "Private" not in model_input and "url(" not in model_input
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("text,prefix", [
    (cue(), "webvtt\n\n"), (cue(), "WEBVTTsuffix\n\n"), (cue(), "WEBVTT\n"),
    (cue(), "WEBVTT\nnot a header field\n\n"), (cue(), "WEBVTT\nKind: metadata\n\n"),
    (cue(), "WEBVTT\nKind: METADATA\n\n"), (cue(), "WEBVTT\n\f\n"),
    ("NOTE hello --> world\n\n" + cue(), "WEBVTT\n\n"),
    ("STYLE\n::cue { content: '-->'; }\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nwidth:50%\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nid:r\nwidth:101%\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nid:r\nregionanchor:0%,101%\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nid:r\nlines:-1\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nid:r\nscroll:down\n\n" + cue(), "WEBVTT\n\n"),
    ("REGION\nid:r\n\nREGION\nid:r\n\n" + cue(), "WEBVTT\n\n"),
    (cue() + "STYLE\n::cue { color:red; }\n\n", "WEBVTT\n\n"),
    (cue(identifier="same") + cue(identifier="same", start="00:05.000", end="00:08.000"), "WEBVTT\n\n"),
    ("unexpected block\n\n" + cue(), "WEBVTT\n\n"),
    ("PRIVATE_ID\n\n" + cue(), "WEBVTT\n\n"),
])
def test_vtt_invalid_blocks_and_metadata_rejected(tmp_path, text, prefix):
    with pytest.raises(FormaError):
        VttDocument(source(tmp_path, text, prefix=prefix))


@pytest.mark.parametrize("encoding,bom", [
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE),
])
def test_vtt_requires_utf8_even_when_other_unicode_encoding_is_unambiguous(tmp_path, encoding, bom):
    path = tmp_path / "source.vtt"
    path.write_bytes(bom + ("WEBVTT\n\n" + cue()).encode(encoding))
    with pytest.raises(FormaError, match="UTF-8"):
        VttDocument(path)


@pytest.mark.parametrize("ending", ["\r\n", "\r", "\n"])
@pytest.mark.parametrize("bom", [b"", codecs.BOM_UTF8])
def test_vtt_utf8_bom_and_physical_line_endings_are_preserved(tmp_path, ending, bom):
    path = source(tmp_path, cue().replace("\n", ending), prefix=("WEBVTT\n\n").replace("\n", ending), bom=bom)
    document = VttDocument(path)
    output = tmp_path / "out.vtt"
    document.write({key: value.replace("Hello", "Вітаю") for key, value in values(document).items()}, output)
    assert output.read_bytes() == path.read_bytes().replace(b"Hello", "Вітаю".encode())
    path.write_bytes(b"WEBVTT\n\n\xff")
    with pytest.raises(FormaError):
        VttDocument(path)


def test_vtt_empty_and_numeric_only_cues_remain_protected_but_numbers_are_compared(tmp_path):
    text = cue("", start="00:00.000", end="00:01.000")
    text += cue("12345", start="00:02.000", end="00:03.000")
    text += cue(start="00:04.000", end="00:05.000")
    path = source(tmp_path, text)
    document = VttDocument(path)
    assert len(document.cues) == 3 and len(document.segments) == 1
    assert "12345" in document.comparison_text()
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()
    modified = source(tmp_path, text.replace("12345", "12346"))
    assert VttDocument(modified).structure_signature() != document.structure_signature()
    empty = VttDocument(source(tmp_path, ""))
    assert not empty.segments and not empty.cues


def test_vtt_comparison_detects_numeric_only_cue_changes(tmp_path):
    path = source(tmp_path, cue("42"))
    candidate = tmp_path / "candidate.vtt"
    candidate.write_bytes(path.read_bytes().replace(b"42", b"43"))
    result = compare_files(path, [candidate])["candidates"][0]
    assert not result["numbers_preserved"] and not result["structure_ok"]


@pytest.mark.parametrize("header", ["WEBVTT", "WEBVTT\n", "WEBVTT\r\n", "WEBVTT\n\n", "WEBVTT producer\nLanguage: en\n"])
def test_vtt_empty_header_has_no_translatable_cues(tmp_path, header):
    document = VttDocument(source(tmp_path, "", prefix=header))
    assert not document.cues and not document.segments and not document.comparison_text()


def test_vtt_header_annotation_is_opaque_even_when_it_contains_an_arrow(tmp_path):
    path = source(tmp_path, cue(), prefix="WEBVTT private --> producer annotation\n\n")
    document = VttDocument(path)
    assert "private" not in document.segments[0].source + document.segments[0].context
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()


def test_vtt_control_entities_make_whole_cue_opaque_without_context_leaks(tmp_path):
    path = source(tmp_path, cue("PRIVATE_CAPTION &#10; data") + cue("Hello world", start="00:05.000", end="00:08.000"))
    document = VttDocument(path)
    assert len(document.segments) == 1
    assert "PRIVATE_" not in document.segments[0].source + document.segments[0].context
    assert "subtitle_unsupported_markup" in {issue.code for issue in document.issues}
    output = tmp_path / "out.vtt"
    document.write(values(document), output)
    assert output.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("payload", ["Hello\n \nworld", "Hello\n\t\nworld", " \nHello", "Hello\n \t \nworld", "Hello\n&#32;\nworld"])
def test_vtt_original_nonempty_whitespace_lines_keep_their_exact_bytes(tmp_path, payload):
    path = source(tmp_path, cue(payload))
    document = VttDocument(path)
    for segment in document.segments:
        assert not candidate_issues(segment, segment.source)
    identity = tmp_path / "identity.vtt"
    document.write(values(document), identity)
    assert identity.read_bytes() == path.read_bytes()
    output = tmp_path / "translated.vtt"
    document.write({key: value.replace("Hello", "Вітаю") for key, value in values(document).items()}, output)
    assert output.read_bytes() == path.read_bytes().replace(b"Hello", "Вітаю".encode())


@pytest.mark.parametrize("payload,before,after", [
    ("<v Roger>Hello world", "<r0></r0>", "<r0>Вітаю </r0>"),
    ("  <v Roger>Hello world", "<r0>  </r0>", "<r0>Новий текст </r0>"),
    ("<ruby>Hello<rt>reading</rt></ruby>", "<r3></r3>", "<r3>новий текст</r3>"),
])
def test_vtt_empty_structural_gaps_cannot_gain_words_before_export(tmp_path, payload, before, after):
    document = VttDocument(source(tmp_path, cue(payload)))
    segment = document.segments[0]
    assert before in segment.source
    candidate = segment.source.replace(before, after)
    assert candidate_issues(segment, candidate)
    output = tmp_path / "invalid.vtt"
    with pytest.raises(FormaError):
        document.write({segment.id: candidate}, output)
    assert not output.exists()


def test_vtt_oversized_numeric_reference_has_a_sanitized_preflight_error(tmp_path):
    with pytest.raises(FormaError, match="WebVTT"):
        VttDocument(source(tmp_path, cue("Hello &#" + "9" * 5000 + ";")))


@pytest.mark.parametrize("injected", ["Hello\nworld", "Hello\rworld", "Hello\tworld", "Hello --> world", "Hello\u2028world", "Hello\x00world"])
def test_vtt_candidate_checks_and_publication_reject_boundary_or_control_injection(tmp_path, injected):
    document = VttDocument(source(tmp_path, cue()))
    segment = document.segments[0]
    translated = "<r0>" + html.escape(injected, quote=False) + "</r0>"
    assert candidate_issues(segment, translated)
    output = tmp_path / "invalid.vtt"
    with pytest.raises(FormaError):
        document.write({segment.id: translated}, output)
    assert not output.exists()


def test_vtt_preserves_rich_anchor_topology_but_allows_word_redistribution(tmp_path):
    document = VttDocument(source(tmp_path, cue("<i>Hello</i> world")))
    segment = document.segments[0]
    assert segment.source == "<r0></r0><k0/><r1>Hello</r1><k1/><r2> world</r2>"
    output = tmp_path / "out.vtt"
    document.write({segment.id: "<r0></r0><k0/><r1></r1><k1/><r2>Вітаю світ</r2>"}, output)
    assert VttDocument(output).structure_signature() == document.structure_signature()
    for invalid in ["<r0>Вітаю</r0>", "<r0></r0><k1/><r1>Вітаю</r1><k0/><r2>світ</r2>"]:
        with pytest.raises(FormaError):
            document.write({segment.id: invalid}, tmp_path / "invalid.vtt")


def test_vtt_layout_counts_visible_lines_without_style_or_timestamp_anchors(tmp_path):
    document = VttDocument(source(tmp_path, cue("<b><i>Hello</i></b> <00:02.000>world", end="00:11.000")))
    assert not document.layout_issues(values(document))
    long = VttDocument(source(tmp_path, cue("Long readable caption " * 3 + "\nSecond\nThird", end="00:01.100")))
    assert {issue.code for issue in long.layout_issues(values(long))} == long.layout_issue_codes
    output = tmp_path / "long.vtt"
    long.write(values(long), output)
    assert output.read_bytes() == long.raw  # Warnings never retime or rewrap captions.


def test_vtt_bounds_source_changes_cancellation_and_no_overwrite(tmp_path, monkeypatch):
    import forma.documents.vtt as vtt_module

    with pytest.raises(FormaError, match="розмітк"):
        VttDocument(source(tmp_path, cue("<b>" * 129 + "Hello" + "</b>" * 129)))
    with pytest.raises(FormaError, match="розмітки"):
        VttDocument(source(tmp_path, cue("<b></b>" * 130 + "Hello")))
    with pytest.raises(FormaError, match="20 000"):
        VttDocument(source(tmp_path, cue("A" * 20001)))
    with pytest.raises(FormaError, match="розмітки"):
        VttDocument(source(tmp_path, cue("A" * 65537)))
    path = source(tmp_path, cue())
    monkeypatch.setattr(vtt_module, "MAX_CUES", 0)
    with pytest.raises(FormaError, match="забагато реплік"):
        VttDocument(path)
    monkeypatch.setattr(vtt_module, "MAX_CUES", 100000)
    document = VttDocument(path)
    with pytest.raises(FormaError, match="окремий"):
        document.write(values(document), path)
    existing = tmp_path / "existing.vtt"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        document.write(values(document), existing)
    assert existing.read_bytes() == b"keep"
    path.write_bytes(path.read_bytes().replace(b"world", b"WORLD"))
    with pytest.raises(FormaError, match="змінився"):
        document.write(values(document), tmp_path / "mutated.vtt")
    assert not (tmp_path / "mutated.vtt").exists()

    def cancel():
        raise Cancelled()

    with pytest.raises(Cancelled):
        VttDocument(path, check_cancel=cancel)
    document = VttDocument(path)
    document.check_cancel = cancel
    with pytest.raises(Cancelled):
        document.write(values(document), tmp_path / "cancelled.vtt")
    assert not (tmp_path / "cancelled.vtt").exists()
    assert not list(tmp_path.glob(".forma-*"))


@pytest.mark.parametrize("payload", ["<b>Hello</i>", "Hello <00:05.000>world"])
def test_vtt_invalid_payload_is_rejected_before_provider_requests(tmp_path, payload):
    path = source(tmp_path, cue(payload))

    class NoCalls:
        def complete(self, *args, **kwargs):
            pytest.fail("invalid WebVTT must fail before a provider call")

    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline-test", model="offline-test")
    with pytest.raises(FormaError, match="WebVTT"):
        TranslationEngine(settings, provider=NoCalls()).translate(path, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False))
