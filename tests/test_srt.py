"""Source-bound SubRip translation and strict cue/format validation."""

from __future__ import annotations

import codecs
import copy
import html
import json

import pytest

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.srt import SrtDocument
from forma.documents.subtitles import candidate_issues, caption_plain_text
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


def source_text(payload="Hello world", *, first_id="7", start="00:00:01,000", end="00:00:03,000"):
    return f"{first_id}\r\n{start} --> {end}\r\n{payload}\r\n\r\n"


@pytest.mark.parametrize(("encoding", "bom"), [
    ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE),
])
def test_srt_encoding_rich_tags_entities_clocks_indices_and_eol_roundtrip(tmp_path, encoding, bom):
    raw = source_text('{\\an8}<font color="#fff" face="A>B"><i>Hello</i></font> world &amp; people\r\nNext line')
    raw += "12\n00:00:02,000 --> 00:00:05,000 X1:1 X2:2 Y1:3 Y2:4\nOther text\n"
    source = tmp_path / "source.srt"
    source.write_bytes(bom + raw.encode(encoding))
    document = SrtDocument(source)
    assert document.extraction_profile == 11 and document.engine == "forma-text"
    assert len(document.segments) == 2
    assert document.cues[1].start_ms < document.cues[0].end_ms  # Overlap remains legal.
    identity = tmp_path / "identity.srt"
    document.write({segment.id: segment.source for segment in document.segments}, identity)
    assert identity.read_bytes() == source.read_bytes()
    values = {segment.id: segment.source.replace("Hello", "Вітаю").replace("world", "світ") for segment in document.segments}
    output = tmp_path / "translated.srt"
    document.write(values, output)
    assert output.read_bytes() == bom + raw.replace("Hello", "Вітаю").replace("world", "світ").encode(encoding)
    assert document.structure_signature() == SrtDocument(output).structure_signature()
    second = tmp_path / "second.srt"
    document.write(values, second)
    assert output.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("raw", [
    source_text(start="00:60:00,000"), source_text(start="00:00:60,000"),
    source_text(start="00:00:01.000"), source_text(start="00:00:03,000", end="00:00:02,000"),
    source_text(start="00:00:03,000", end="00:00:03,000"),
    source_text(start="00:00:02,000") + source_text(first_id="9", start="00:00:01,000"),
    "Hello\n00:00:01,000 --> 00:00:02,000\nCaption\n", "1\nHello\nCaption\n",
    "1\n00:00:01,000 --> 00:00:02,000\n\n", source_text("Hello --> malicious timing"),
    "1\n00:00:01,000 --> 00:00:02,000 X1:1 X1:2 Y1:3 Y2:4\nCaption\n",
    "1\n00:00:01,000 --> 00:00:02,000\nCaption\n2\n00:00:03,000 --> 00:00:04,000\nOther",
])
def test_srt_rejects_malformed_structure_before_provider_requests(tmp_path, raw):
    source = tmp_path / "source.srt"
    source.write_text(raw)

    class NoCalls:
        calls = []

        def complete(self, *args, **kwargs):
            self.calls.append(args)
            pytest.fail("malformed subtitles must fail before a provider call")

    provider = NoCalls()
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline-test", model="offline-test")
    with pytest.raises(FormaError, match="SRT"):
        TranslationEngine(settings, provider=provider).translate(source, tmp_path / "out", Options("en", "uk", review=False, auto_glossary=False))
    assert not provider.calls


@pytest.mark.parametrize("injected", ["Hello\nnew line", "Hello\rnew line", "Hello --> timing", "Hello\tshift", "Hello\u2028line", "Hello\x00hidden"])
def test_subtitle_candidate_validation_and_writer_reject_injection(tmp_path, injected):
    source = tmp_path / "source.srt"
    source.write_text(source_text())
    document = SrtDocument(source)
    segment = document.segments[0]
    translated = "<r0>" + html.escape(injected, quote=False) + "</r0>"
    issues = candidate_issues(segment, translated)
    assert issues and issues[0].code == "formatting" and issues[0].severity == "error"
    with pytest.raises(FormaError):
        document.write({segment.id: translated}, tmp_path / "invalid.srt")
    assert not (tmp_path / "invalid.srt").exists()


def test_srt_new_literal_markup_is_encoded_and_entities_need_not_keep_spelling_when_changed(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(source_text("Hello &apos;friend&apos; &amp; world"))
    document = SrtDocument(source)
    segment = document.segments[0]
    output = tmp_path / "translated.srt"
    translated = "<r0>" + html.escape("Вітаю <i>друже</i> & світ", quote=False) + "</r0>"
    assert not candidate_issues(segment, translated)
    document.write({segment.id: translated}, output)
    assert "&lt;i&gt;друже&lt;/i&gt;" in output.read_text()
    assert document.structure_signature() == SrtDocument(output).structure_signature()


def test_srt_unknown_extensions_are_preserved_and_omitted_from_context(tmp_path):
    raw = source_text('<script data-key="private_attribute">private_code()</script>')
    raw += source_text("Hello world", first_id="9", start="00:00:04,000", end="00:00:06,000")
    source = tmp_path / "source.srt"
    source.write_text(raw)
    document = SrtDocument(source)
    assert len(document.segments) == 1
    assert "private" not in " ".join(segment.source + segment.context for segment in document.segments)
    assert "subtitle_unsupported_markup" in {issue.code for issue in document.issues}
    output = tmp_path / "translated.srt"
    document.write({segment.id: segment.source.replace("Hello", "Вітаю") for segment in document.segments}, output)
    assert '<script data-key="private_attribute">private_code()</script>' in output.read_text()


def test_subtitle_layout_uses_actual_lines_instead_of_every_format_anchor(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(source_text('<b><i><u>Hello</u></i></b>', end="00:00:11,000"))
    document = SrtDocument(source)
    assert document.segments[0].plain.count("\n") > 2
    values = {segment.id: segment.source for segment in document.segments}
    assert not document.layout_issues(values)
    source = tmp_path / "long.srt"
    source.write_text(source_text("A long readable caption " * 3 + "\r\nSecond\r\nThird", end="00:00:01,100"))
    document = SrtDocument(source)
    codes = {issue.code for issue in document.layout_issues({segment.id: segment.source for segment in document.segments})}
    assert codes == document.layout_issue_codes


def test_srt_rich_empty_slots_numeric_cues_and_protected_signatures(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(source_text("<i>Hello</i> world") + source_text("12345", first_id="9", start="00:00:04,000", end="00:00:06,000"))
    document = SrtDocument(source)
    assert len(document.segments) == 1 and "12345" in document.comparison_text()
    segment = document.segments[0]
    assert segment.source == "<r0></r0><k0/><r1>Hello</r1><k1/><r2> world</r2>"
    output = tmp_path / "out.srt"
    document.write({segment.id: "<r0></r0><k0/><r1></r1><k1/><r2>Вітаю світ</r2>"}, output)
    assert document.structure_signature() == SrtDocument(output).structure_signature()
    modified = tmp_path / "modified.srt"
    modified.write_text(source.read_text().replace("12345", "12346"))
    assert document.structure_signature() != SrtDocument(modified).structure_signature()


@pytest.mark.parametrize("numbers", [("0", "1"), ("7", "7"), ("9", "3"), ("001", "200")])
def test_srt_nonstandard_counters_are_preserved_without_affecting_cue_association(tmp_path, numbers):
    source = tmp_path / "source.srt"
    raw = source_text("Hello world", first_id=numbers[0]) + source_text("Other text", first_id=numbers[1], start="00:00:04,000", end="00:00:06,000")
    source.write_text(raw)
    document = SrtDocument(source)
    assert [segment.id.split(":l")[0] for segment in document.segments] == ["subtitle-v11:1", "subtitle-v11:2"]
    assert "srt_counter_preserved" in {issue.code for issue in document.issues}
    output = tmp_path / "out.srt"
    document.write({segment.id: segment.source.replace("Hello", "Вітаю") for segment in document.segments}, output)
    assert output.read_text().replace("\n", "\r\n") == raw.replace("Hello", "Вітаю")


def test_srt_boundaries_cancellation_and_original_are_checked(tmp_path, monkeypatch):
    import forma.documents.subtitles as subtitle_module

    source = tmp_path / "source.srt"
    source.write_text(source_text("<b></b>" * 130 + "Hello"))
    with pytest.raises(FormaError, match="розмітки"):
        SrtDocument(source)
    source.write_text(source_text("A" * 20001))
    with pytest.raises(FormaError, match="20 000"):
        SrtDocument(source)
    source.write_text(source_text())
    monkeypatch.setattr(subtitle_module, "MAX_CUES", 0)
    with pytest.raises(FormaError, match="забагато реплік"):
        SrtDocument(source)
    monkeypatch.setattr(subtitle_module, "MAX_CUES", 100000)
    document = SrtDocument(source)
    values = {segment.id: segment.source for segment in document.segments}
    with pytest.raises(FormaError, match="окремий"):
        document.write(values, source)
    existing = tmp_path / "existing.srt"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        document.write(values, existing)
    assert existing.read_bytes() == b"keep"

    def cancel():
        raise Cancelled()

    document.check_cancel = cancel
    with pytest.raises(Cancelled):
        document.write(values, tmp_path / "cancelled.srt")
    assert not (tmp_path / "cancelled.srt").exists()
    assert not list(tmp_path.glob(".forma-*"))


def test_srt_injection_enters_provider_retry_and_metadata_never_enters_requests(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(source_text("Hello world", first_id="123456789"))

    class Provider:
        input_tokens = output_tokens = 0

        def __init__(self):
            self.calls = []

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls.append(copy.deepcopy(payload))
            return {"segments": [{"id": item["id"], "text": "<r0>Hello\nInjected</r0>" if len(self.calls) == 1 else "<r0>Вітаю світ</r0>"}
                                 for item in payload["segments"]]}

    provider = Provider()
    settings = Settings(base_url="http://127.0.0.1:1/v1", api_key="offline-test", model="subtitle-retry", max_parallel_requests=1)
    result = TranslationEngine(settings, provider=provider).translate(source, tmp_path / "output", Options("en", "uk", review=False, auto_glossary=False))
    assert len(provider.calls) == 2
    assert "validation_errors" in provider.calls[1]
    requests = json.dumps(provider.calls, ensure_ascii=False)
    assert "123456789" not in requests and "00:00:01" not in requests and "00:00:03" not in requests
    assert "Вітаю світ" in result.output.read_text()
    assert "123456789" in result.output.read_text()


@pytest.mark.parametrize("translated", [
    "<r0></r0><k0/><r1>Вітаю світ</r1>",
    "<r0>Вітаю світ</r0><k0/><r1></r1>",
    "<r0>  </r0><k0/><r1>Вітаю світ</r1>",
])
def test_srt_empty_physical_first_and_last_lines_are_format_errors_before_export(tmp_path, translated):
    source = tmp_path / "source.srt"
    source.write_text(source_text("Hello\r\nworld"))
    document = SrtDocument(source)
    segment = document.segments[0]
    issues = candidate_issues(segment, translated)
    assert issues and issues[0].code == "formatting" and "порожній рядок" in issues[0].message
    with pytest.raises(FormaError, match="порожній рядок"):
        document.write({segment.id: translated}, tmp_path / "invalid.srt")


def test_srt_style_only_physical_line_can_stay_nonblank_after_word_redistribution(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(source_text("<i>Hello</i>\r\nworld"))
    document = SrtDocument(source)
    segment = document.segments[0]
    translated = "<r0></r0><k0/><r1></r1><k1/><r2></r2><k2/><r3>Вітаю світ</r3>"
    assert not candidate_issues(segment, translated)
    output = tmp_path / "translated.srt"
    document.write({segment.id: translated}, output)
    assert "<i></i>\nВітаю світ" in output.read_text()


def test_srt_short_cue_letter_loss_is_rejected_but_fragment_partial_data_is_not(tmp_path):
    from dataclasses import replace

    source = tmp_path / "source.srt"
    source.write_text(source_text("Hello"))
    segment = SrtDocument(source).segments[0]
    issues = candidate_issues(segment, "<r0>!</r0>")
    assert issues and issues[0].code == "formatting" and "втратив текст" in issues[0].message
    fragment = replace(segment, id=segment.id + "::fragment:0:1:abcd", source="<r0>123</r0>")
    assert not candidate_issues(fragment, "<r0>123</r0>")


def test_subtitle_plain_rendering_uses_actual_lines_tabs_and_format_anchor_kinds(tmp_path):
    from dataclasses import replace

    source = tmp_path / "source.srt"
    source.write_text(source_text('{\\an8}<b><i>Hello</i></b>\tworld<br>Next\r\nLast'))
    document = SrtDocument(source)
    segment = document.segments[0]
    assert caption_plain_text(segment) == "Hello\tworld\nNext\nLast"
    translated = segment.source.replace("Hello", "Вітаю").replace("world", "світ")
    assert caption_plain_text(segment, translated) == "Вітаю\tсвіт\nNext\nLast"
    assert all(len(group) <= 64 for group in segment.id.split(":l")[1].split("."))
    fragment = replace(segment, id=segment.id + "::fragment:0:1:abcd")
    assert caption_plain_text(fragment, translated) == caption_plain_text(segment, translated)


@pytest.mark.parametrize("marker", ["k9999999999999999999999999999", "k256", "r999999999999", "k٠"])
def test_subtitle_untrusted_report_anchor_names_are_bounded_before_mask_shifts(marker):
    from forma.core.models import Segment

    markup = f"<r0>Hello</r0><{marker}/>"
    segment = Segment("subtitle-v11:1:l0.0.0.0", "Cue 1", markup)
    issues = candidate_issues(segment, markup)
    assert issues and issues[0].code == "formatting" and issues[0].severity == "error"
    assert isinstance(caption_plain_text(segment), str)


def test_subtitle_physical_line_amplification_and_diagnostic_fallback_are_bounded(tmp_path, monkeypatch):
    import forma.documents.subtitles as subtitle_module

    source = tmp_path / "source.srt"
    source.write_text("\n" * 10 + source_text())
    monkeypatch.setattr(subtitle_module, "MAX_SUBTITLE_LINES", 5)
    with pytest.raises(FormaError, match="фізичних рядків"):
        SrtDocument(source)
    from forma.core.models import Segment

    segment = Segment("subtitle-v11:1:l0.0.0.0", "Cue 1", "<r0>Hello</r0>")
    assert caption_plain_text(segment, "") == ""
    assert caption_plain_text(segment, "<bad>") == ""
    assert caption_plain_text(segment, 123) == ""


def test_srt_oversized_numeric_entity_is_preserved_without_uncaught_integer_conversion(tmp_path):
    source = tmp_path / "source.srt"
    raw = source_text("Hello &#" + "9" * 5000 + ";")
    source.write_text(raw)
    document = SrtDocument(source)
    assert not document.segments
    assert "subtitle_unsupported_markup" in {issue.code for issue in document.issues}
    output = tmp_path / "identity.srt"
    document.write({}, output)
    assert output.read_bytes() == source.read_bytes()
