"""Caption edits preserve real line/tab boundaries and zero-width formatting."""

from __future__ import annotations

import pytest

from forma.core.models import FormaError, Segment
from forma.documents.srt import SrtDocument
from forma.documents.subtitle_editor import edit_caption_markup
from forma.documents.subtitles import candidate_issues, caption_plain_text
from forma.documents.vtt import VttDocument
from forma.translation.quality import parse_translation


def caption(tmp_path, kind, body):
    source = tmp_path / ("source." + kind)
    header = "1\r\n00:00:01,000 --> 00:00:09,000\r\n" if kind == "srt" else "WEBVTT\r\n\r\nprivate-identifier\r\n00:00:01.000 --> 00:00:09.000\r\n"
    source.write_bytes((header + body + "\r\n").encode())
    document = SrtDocument(source) if kind == "srt" else VttDocument(source)
    return source, document, document.segments[0]


@pytest.mark.parametrize("kind", ["srt", "vtt"])
def test_styled_two_line_edit_exports_without_formatting_markers_becoming_lines(tmp_path, kind):
    source, document, segment = caption(tmp_path, kind, "<i>Hello</i> world\r\nNext line")
    edited = "Вітаю друже\nНаступний рядок"
    markup = edit_caption_markup(segment, segment.source, edited)
    assert caption_plain_text(segment, markup) == edited
    assert list(parse_translation(segment.source, markup)) == list(parse_translation(segment.source, segment.source))
    assert not candidate_issues(segment, markup)
    output = tmp_path / ("edited." + kind)
    document.write({segment.id: markup}, output)
    reopened = type(document)(output)
    assert caption_plain_text(reopened.segments[0]) == edited
    assert reopened.structure_signature() == document.structure_signature()
    assert output.read_bytes().count(b"\r\n") == source.read_bytes().count(b"\r\n")


def test_vtt_omitted_voice_closing_and_empty_prefix_remain_safe_during_edit(tmp_path):
    _, document, segment = caption(tmp_path, "vtt", "<v Roger O'Brien><b>Hello</b> world\r\nNext line")
    markup = edit_caption_markup(segment, segment.source, "Вітаю світ\nНаступний рядок")
    assert parse_translation(segment.source, markup)["r0"] == ""
    output = tmp_path / "edited.vtt"
    document.write({segment.id: markup}, output)
    assert "<v Roger O'Brien>" in output.read_text()
    assert caption_plain_text(VttDocument(output).segments[0]) == "Вітаю світ\nНаступний рядок"


def test_tabs_and_html_breaks_are_visible_boundaries_while_styles_are_not(tmp_path):
    _, document, segment = caption(tmp_path, "srt", "<b>Hello</b>\tworld<br>Next")
    edited = "Привіт\tсвіт\nДалі"
    markup = edit_caption_markup(segment, segment.source, edited)
    assert caption_plain_text(segment, markup) == edited
    for invalid in ("Привіт світ\nДалі", "Привіт\nсвіт\tДалі", "Привіт\tсвіт Далі"):
        with pytest.raises(FormaError, match="переносів"):
            edit_caption_markup(segment, segment.source, invalid)
    output = tmp_path / "edited.srt"
    document.write({segment.id: markup}, output)
    assert "\t" in output.read_text() and "<br>" in output.read_text()


def test_visible_edit_can_repair_bad_voice_prefix_by_returning_words_to_real_text_run(tmp_path):
    _, document, segment = caption(tmp_path, "vtt", "<v Speaker>Hello world")
    bad = "<r0>Bad prefix </r0><k0/><r1>world</r1>"
    assert candidate_issues(segment, bad)
    markup = edit_caption_markup(segment, bad, "Bad prefix world")
    assert parse_translation(segment.source, markup)["r0"] == ""
    assert caption_plain_text(segment, markup) == "Bad prefix world"
    output = tmp_path / "fixed.vtt"
    document.write({segment.id: markup}, output)


def test_insertions_around_pinned_whitespace_are_assigned_to_text_bearing_runs(tmp_path):
    _, document, segment = caption(tmp_path, "srt", "Hello<i> </i>world")
    markup = edit_caption_markup(segment, segment.source, "Hello brave world")
    values = parse_translation(segment.source, markup)
    assert values["r1"] == " "
    assert caption_plain_text(segment, markup) == "Hello brave world"
    output = tmp_path / "edited.srt"
    document.write({segment.id: markup}, output)
    assert "<i> </i>" in output.read_text()
    _, _, segment = caption(tmp_path, "srt", "Hello<i>  </i>world")
    with pytest.raises(FormaError):
        edit_caption_markup(segment, segment.source, "Hello world")


@pytest.mark.parametrize("body", ["Hello\r\n \r\nworld", "Hello\r\n\t\r\nworld", "Hello\r\n&#32;\r\nworld"])
def test_vtt_original_space_tab_and_entity_only_lines_survive_visible_edits(tmp_path, body):
    _, document, segment = caption(tmp_path, "vtt", body)
    edited = caption_plain_text(segment).replace("Hello", "Вітаю").replace("world", "світ")
    markup = edit_caption_markup(segment, segment.source, edited)
    output = tmp_path / "edited.vtt"
    document.write({segment.id: markup}, output)
    assert caption_plain_text(VttDocument(output).segments[0]) == edited


def test_editor_bounds_unicode_and_new_empty_lines_are_safe_format_errors(tmp_path):
    _, _, segment = caption(tmp_path, "srt", "Hello\r\nworld")
    with pytest.raises(FormaError, match="завеликий"):
        edit_caption_markup(segment, segment.source, "A" * 20001)
    with pytest.raises(FormaError, match="порожній рядок"):
        edit_caption_markup(segment, segment.source, "\nВітаю світ")
    with pytest.raises(FormaError, match="Unicode"):
        edit_caption_markup(segment, segment.source, "\ud800\nworld")
    forged = Segment("subtitle-v11:1:l0.0.0.0", "Cue 1", "<r0>Hello</r0><k999999999999/>")
    with pytest.raises(FormaError):
        edit_caption_markup(forged, forged.source, "Hello")


def test_ruby_empty_gaps_are_pinned_while_real_text_runs_remain_editable(tmp_path):
    _, document, segment = caption(tmp_path, "vtt", "<ruby>漢<rt>kan</rt></ruby> Hello")
    markup = edit_caption_markup(segment, segment.source, "字zi Вітаю")
    assert parse_translation(segment.source, markup)["r3"] == ""
    assert not candidate_issues(segment, markup)
    output = tmp_path / "edited.vtt"
    document.write({segment.id: markup}, output)


def test_multiline_srt_style_anchor_does_not_shift_entity_only_line_safety_mask(tmp_path):
    _, document, segment = caption(tmp_path, "srt", '<font\r\ncolor="#fff">Hello</font>\r\n&#32;\r\nworld')
    assert not candidate_issues(segment, segment.source)
    assert edit_caption_markup(segment, segment.source, caption_plain_text(segment)) == segment.source
    output = tmp_path / "identity.srt"
    document.write({segment.id: segment.source}, output)
    assert output.read_bytes() == document.path.read_bytes()
