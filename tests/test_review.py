from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from forma.models import FormaError, Issue, Options, Segment, TranslationResult
from forma.review import (
    actionable_count,
    build_review,
    entry_fingerprint,
    load_report,
    parse_report_data,
)


def report_data(*, engine="forma-ooxml"):
    return {
        "source": "/previous/computer/source.docx",
        "output": "/previous/computer/translated.docx",
        "engine": engine,
        "options": {"source_language": "uk", "target_language": "en"},
        "segments": [
            {
                "id": "document:1",
                "location": "Абзац 1",
                "source": "<r0>Насосна станція</r0>",
                "translation": "<r0>Pumping station</r0>",
                "context": "Old context is optional for local review.",
            }
        ],
        "issues": [],
    }


def write_report(tmp_path, data):
    path = tmp_path / "moved.forma.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def make_result(segments=(), translations=None, issues=(), *, engine="forma-ooxml"):
    return TranslationResult(
        Path("missing-original.docx"),
        None,
        list(segments),
        dict(translations or {}),
        list(issues),
        engine=engine,
    )


@pytest.mark.parametrize("engine", ["forma-ooxml", "forma-corpus", "babeldoc"])
def test_reading_moved_report_is_local_and_does_not_require_source_or_output(
    tmp_path, monkeypatch, engine
):
    def no_network(*args, **kwargs):
        pytest.fail("Reviewing an existing report must not contact an API")

    monkeypatch.setattr(httpx, "Client", no_network)
    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    data = report_data(engine=engine)
    data["api_key"] = "ignored-private-marker"
    data["base_url"] = "https://untrusted.invalid/v1"
    data["options"]["glossary"] = {"old glossary": "not a provider request"}
    path = write_report(tmp_path, data)
    before = path.read_bytes()

    result, options = load_report(path)
    view = build_review(result, options)

    assert result.report == path
    assert result.engine == engine
    assert options.source_language == "uk"
    assert options.target_language == "en"
    assert view["entries"][0]["source"] == "Насосна станція"
    assert view["entries"][0]["translation"] == "Pumping station"
    assert view["summary"]["hasResult"]
    assert view["summary"]["warningCount"] == 0
    assert "ignored-private-marker" not in json.dumps(view)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"segments": "bad"},
        {"issues": {}},
        {"segments": [None]},
        {"segments": [{"id": "a", "source": "source", "translation": "target"}]},
        {
            "segments": [
                {"id": "", "source": "source", "translation": "target", "location": "1"}
            ]
        },
        {"issues": [{"code": "error", "message": ["invalid"]}]},
        {"issues": [{"code": "", "message": "invalid"}]},
        {"options": []},
        {"options": {"source_language": "uk", "target_language": "private-invalid-code!"}},
        {"options": {"source_language": False, "target_language": "en"}},
        {"engine": "unknown-private-provider"},
    ],
)
def test_malformed_schema_raises_only_safe_forma_error(tmp_path, data):
    if isinstance(data, dict):
        data = {**report_data(), **data}
    path = write_report(tmp_path, data)
    with pytest.raises(FormaError) as caught:
        load_report(path)
    assert "Не вдалося прочитати звіт" in str(caught.value)
    assert "private" not in str(caught.value)
    assert str(path) not in str(caught.value)


@pytest.mark.parametrize("missing", ["segments", "issues", "engine", "options"])
def test_report_must_include_explicit_schema_fields(tmp_path, missing):
    data = report_data()
    del data[missing]
    with pytest.raises(FormaError):
        load_report(write_report(tmp_path, data))


def test_duplicate_segment_ids_are_rejected_instead_of_overwriting_translation(tmp_path):
    data = report_data()
    duplicate = copy.deepcopy(data["segments"][0])
    duplicate["translation"] = "<r0>A different translation</r0>"
    data["segments"].append(duplicate)
    with pytest.raises(FormaError):
        load_report(write_report(tmp_path, data))


@pytest.mark.parametrize("content", [b'{"private-key": invalid}', b"\xff\xfe", b"[" * 2000])
def test_malformed_json_encoding_and_nesting_are_safe(tmp_path, content):
    path = tmp_path / "private-source.forma.json"
    path.write_bytes(content)
    with pytest.raises(FormaError) as caught:
        load_report(path)
    assert "private" not in str(caught.value)
    assert "Traceback" not in str(caught.value)


def test_oversized_report_is_rejected_before_parsing_contents(tmp_path, monkeypatch):
    path = tmp_path / "oversized.forma.json"
    with path.open("wb") as output:
        output.truncate(64 * 1024 * 1024 + 1)

    def must_not_parse(*args, **kwargs):
        pytest.fail("An oversized report must be rejected before JSON parsing")

    monkeypatch.setattr(json, "loads", must_not_parse)
    with pytest.raises(FormaError):
        load_report(path)


@pytest.mark.parametrize("field", ["segments", "issues"])
def test_excessive_item_count_is_rejected(tmp_path, field):
    data = report_data()
    data[field] = [{}] * 100001
    with pytest.raises(FormaError):
        load_report(write_report(tmp_path, data))


def test_missing_or_unreadable_report_has_no_raw_os_error(tmp_path, monkeypatch):
    path = tmp_path / "missing-private-name.forma.json"
    with pytest.raises(FormaError) as caught:
        load_report(path)
    assert "missing-private-name" not in str(caught.value)

    def unavailable(*args, **kwargs):
        raise PermissionError("backend-private-path-and-token")

    monkeypatch.setattr(Path, "open", unavailable)
    with pytest.raises(FormaError) as caught:
        load_report(path)
    assert "backend-private" not in str(caught.value)


def test_old_segment_warnings_are_recomputed_but_structural_and_error_findings_remain(tmp_path):
    data = report_data(engine="forma-corpus")
    data["issues"] = [
        asdict(Issue("unchanged", "Old heuristic warning", "document:1")),
        asdict(Issue("numbers", "Old heuristic error", "document:1", "error")),
        asdict(Issue("tracked_changes", "Перевірте виправлення у документі.")),
        asdict(Issue("export_failed", "Не вдалося зберегти зображення.", severity="error")),
        asdict(Issue("unchanged", "Finding for a segment absent from this report", "absent:1")),
        asdict(Issue("fields_preserved", "Поля збережено.")),
    ]
    path = write_report(tmp_path, data)
    before = path.read_bytes()
    result, options = load_report(path)
    original_issues = copy.deepcopy(result.issues)
    view = build_review(result, options)
    messages = [message for entry in view["entries"] for message in entry["messages"]]
    entries = {entry["id"]: entry for entry in view["entries"]}

    assert "Old heuristic warning" not in messages
    assert "Old heuristic error" not in messages
    assert "Finding for a segment absent from this report" in messages
    assert entries["document:tracked_changes"]["severity"] == "info"
    assert "Track Changes" in entries["document:tracked_changes"]["messages"][0]
    assert entries["document:export_failed"]["severity"] == "error"
    assert entries["document:fields_preserved"]["severity"] == "info"
    assert entries["document:1"]["issueCount"] == 0
    assert view["summary"]["warningCount"] == 2
    assert view["summary"]["attention"] == 2
    assert view["summary"]["notes"] == 2
    assert result.issues == original_issues
    assert path.read_bytes() == before


def test_current_checks_find_new_number_and_missing_translation_errors():
    result = make_result(
        [
            Segment("a", "A1", "<r0>Потужність 20 кВт</r0>"),
            Segment("b", "A2", "<r0>Насосна станція</r0>"),
            Segment("c", "A3", "<r0>Резервний насос</r0>"),
        ],
        {"a": "<r0>Power 25 kW</r0>", "b": "", "c": "<r0>Standby pump</r0>"},
    )
    view = build_review(result, Options(source_language="uk", target_language="en"))
    entries = {entry["id"]: entry for entry in view["entries"]}

    assert entries["a"]["severity"] == "error"
    assert any("числа" in message for message in entries["a"]["messages"])
    assert entries["b"]["severity"] == "error"
    assert any("відсутній переклад" in message for message in entries["b"]["messages"])
    assert entries["c"]["messages"] == []
    assert view["summary"]["segments"] == 3
    assert view["summary"]["attention"] == 2


def test_old_notation_warning_clears_while_unlocalized_acronym_remains_for_review():
    result = make_result(
        [Segment("irr", "B4", "<r0>IRR, %</r0>"), Segment("term", "A6", "<r0>АСКОЕ</r0>")],
        {"irr": "<r0>IRR, %</r0>", "term": "<r0>АСКОЕ</r0>"},
        [Issue("unchanged", "Legacy unchanged warning", item) for item in ("irr", "term")],
        engine="forma-corpus",
    )
    view = build_review(result, Options(source_language="uk", target_language="en"))
    entries = {entry["id"]: entry for entry in view["entries"]}
    assert entries["irr"]["messages"] == []
    assert entries["term"]["issueCount"] == 1
    assert entries["term"]["severity"] == "warning"
    assert view["summary"]["warningCount"] == 1


def test_issues_group_by_segment_dedupe_and_keep_highest_severity():
    result = make_result(
        [Segment("a", "A1", "Source"), Segment("b", "A2", "Other")],
        {"a": "Target", "b": "Different"},
        [
            Issue("external_note", "Review this table.", "a"),
            Issue("external_note", "Review this table.", "a"),
            Issue("external_error", "The image was not exported.", "a", "error"),
            Issue("external_note", "Review this table.", "b"),
            Issue("metadata", "A document note.", severity="info"),
            Issue("metadata", "A document note.", severity="info"),
        ],
        engine="babeldoc",
    )
    view = build_review(result, Options())
    entries = {entry["id"]: entry for entry in view["entries"]}

    assert entries["a"]["messages"] == ["Review this table.", "The image was not exported."]
    assert entries["a"]["severity"] == "error"
    assert entries["a"]["issueCount"] == 2
    assert entries["b"]["issueCount"] == 1
    assert entries["document:metadata"]["issueCount"] == 0
    assert view["summary"]["warningCount"] == 3
    assert view["summary"]["attention"] == 2
    assert view["summary"]["notes"] == 1


@pytest.mark.parametrize("segment_id", ["a", ""])
@pytest.mark.parametrize(
    "severities",
    [("warning", "error"), ("error", "warning"), ("info", "error"), ("error", "info")],
)
def test_identical_issue_deduplication_never_discards_higher_severity(segment_id, severities):
    result = make_result(
        [Segment("a", "A1", "Source")],
        {"a": "Target"},
        [Issue("external_check", "Same finding.", segment_id, value) for value in severities],
        engine="babeldoc",
    )
    view = build_review(result, Options())
    finding = next(entry for entry in view["entries"] if entry["messages"])
    assert finding["messages"] == ["Same finding."]
    assert finding["severity"] == "error"
    assert finding["issueCount"] == 1
    assert view["summary"]["warningCount"] == 1
    assert view["summary"]["attention"] == 1
    assert view["summary"]["notes"] == 0


@pytest.mark.parametrize(
    "code", ["fields_preserved", "external_engine", "pdf_visual_review", "shared_memory"]
)
def test_explicit_error_is_not_demoted_by_normally_informational_code(code):
    error = Issue(code, "This operation explicitly failed.", severity="error")
    assert actionable_count([error]) == 1
    result = make_result(
        issues=[Issue(code, error.message), error],
        engine="babeldoc",
    )
    view = build_review(result, Options())
    assert len(view["entries"]) == 1
    assert view["entries"][0]["severity"] == "error"
    assert view["entries"][0]["messages"] == [error.message]
    assert view["summary"]["warningCount"] == 1
    assert view["summary"]["attention"] == 1
    assert view["summary"]["notes"] == 0


def test_workflow_information_is_kept_without_inflating_warning_counts():
    informational = [
        Issue(code, f"Note for {code}.")
        for code in ("fields_preserved", "external_engine", "pdf_visual_review", "shared_memory")
    ]
    issues = [
        *informational,
        Issue("embedded_images", "Images need visual review."),
        Issue("info_detail", "Helpful information.", severity="info"),
        Issue("failed", "An actual error.", severity="error"),
    ]
    assert actionable_count(issues) == 2
    view = build_review(make_result(issues=issues, engine="babeldoc"), Options())
    assert len(view["entries"]) == 7
    assert view["summary"]["segments"] == 0
    assert view["summary"]["warningCount"] == 2
    assert view["summary"]["attention"] == 2
    assert view["summary"]["notes"] == 5
    assert all(entry["kind"] == "document" for entry in view["entries"])


def test_pdf_report_does_not_invent_missing_translation_errors_for_external_segments(tmp_path):
    data = report_data(engine="babeldoc")
    data["segments"][0]["translation"] = ""
    data["issues"] = [asdict(Issue("pdf_visual_review", "Перевірте верстку PDF."))]
    result, options = load_report(write_report(tmp_path, data))
    view = build_review(result, options)
    assert view["summary"]["warningCount"] == 0
    assert view["summary"]["notes"] == 1
    assert view["entries"][0]["messages"] == ["Перевірте верстку PDF."]


def test_document_markup_stays_plain_data_for_the_ui(tmp_path):
    data = report_data(engine="babeldoc")
    literal = '<img src="https://untrusted.invalid/image">'
    data["segments"][0]["source"] = (
        '<r0>&lt;img src="https://untrusted.invalid/image"&gt;</r0>'
    )
    data["segments"][0]["translation"] = "<r0>&lt;b&gt;Literal text&lt;/b&gt;</r0><k1/>Next line"
    data["segments"][0]["location"] = '<a href="file:///private">Location</a>'
    data["issues"] = [asdict(Issue("external_note", literal, "document:1"))]
    result, options = load_report(write_report(tmp_path, data))
    entry = build_review(result, options)["entries"][0]
    assert entry["source"] == literal
    assert entry["translation"] == "<b>Literal text</b>\nNext line"
    assert entry["messages"] == [literal]
    assert entry["location"] == '<a href="file:///private">Location</a>'
    assert isinstance(entry["source"], str)


def test_unknown_severity_is_actionable_instead_of_crashing():
    result = make_result(issues=[Issue("external_check", "Check this.", severity="unexpected")])
    view = build_review(result, Options())
    assert view["entries"][0]["severity"] == "warning"
    assert view["summary"]["warningCount"] == 1


def test_parsing_already_loaded_report_never_opens_paths_or_uses_network(monkeypatch, tmp_path):
    data = report_data()
    before = copy.deepcopy(data)

    def forbidden(*args, **kwargs):
        pytest.fail("Parsing existing report data must be a local, pure operation")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    result, options = parse_report_data(data, tmp_path / "not-on-disk.forma.json")
    assert result.report == tmp_path / "not-on-disk.forma.json"
    assert result.translations["document:1"] == "<r0>Pumping station</r0>"
    assert options.source_language == "uk" and options.target_language == "en"
    assert data == before


def test_pure_report_parser_rejects_duplicate_ids():
    data = report_data()
    data["segments"].append(copy.deepcopy(data["segments"][0]))
    with pytest.raises(FormaError):
        parse_report_data(data, Path("not-on-disk.forma.json"))


@pytest.mark.parametrize(
    ("draft", "expected_severity", "plain"),
    [
        ("<r0>Тиск 16 бар</r0>", "", "Тиск 16 бар"),
        ("<r0>Тиск 19 бар</r0>", "error", "Тиск 19 бар"),
        ("Тиск 16 бар", "error", "Тиск 16 бар"),
        ("", "error", ""),
    ],
)
def test_edited_translation_displays_plain_text_and_is_revalidated_without_mutating_result(
    draft, expected_severity, plain
):
    result = make_result(
        [Segment("one", "A1", "<r0>Pressure 16 bar</r0>")],
        {"one": "<r0>Тиск 15 бар</r0>"},
    )
    original = copy.deepcopy(result)
    edits = {"one": draft}
    view = build_review(
        result, Options(source_language="en", target_language="uk"), edits=edits
    )
    entry = view["entries"][0]
    assert entry["translation"] == plain
    assert entry["edited"]
    assert entry["severity"] == expected_severity
    assert view["summary"]["edited"] == 1
    assert view["summary"]["attention"] == int(expected_severity == "error")
    assert bool(entry["messages"]) == (expected_severity == "error")
    assert result == original
    assert edits == {"one": draft}


def test_editing_protected_identifier_keeps_a_blocking_validation_error():
    result = make_result(
        [Segment("one", "A1", "<r0>Pump HM-200</r0>")],
        {"one": "<r0>Насос HM-200</r0>"},
    )
    view = build_review(
        result,
        Options(source_language="en", target_language="uk"),
        edits={"one": "<r0>Насос OTHER-200</r0>"},
    )
    entry = view["entries"][0]
    assert entry["edited"] and entry["severity"] == "error"
    assert any("коди" in message for message in entry["messages"])
    assert view["summary"]["attention"] == 1


def warning_result():
    return make_result(
        [Segment("one", "A1", "<r0>Pumping station</r0>")],
        {"one": "<r0>Насосна станція</r0>"},
        [Issue("terminology_review", "Перевірте назву обладнання.", "one")],
    )


def test_matching_acknowledgement_removes_attention_but_keeps_finding_and_entry():
    result = warning_result()
    options = Options(source_language="en", target_language="uk")
    original = build_review(result, options)
    entry = original["entries"][0]
    fingerprint = entry_fingerprint(entry)
    assert fingerprint == entry["fingerprint"]
    assert len(fingerprint) == 64
    assert original["summary"]["attention"] == 1

    view = build_review(result, options, accepted={"one": fingerprint})
    accepted = view["entries"][0]
    assert len(view["entries"]) == 1
    assert accepted["accepted"] and accepted["severity"] == "warning"
    assert accepted["messages"] == entry["messages"]
    assert accepted["issueCount"] == 1
    assert accepted["source"] == entry["source"]
    assert accepted["translation"] == entry["translation"]
    assert view["summary"]["accepted"] == 1
    assert view["summary"]["attention"] == 0
    assert view["summary"]["warningCount"] == 0
    assert entry_fingerprint(accepted) == fingerprint


@pytest.mark.parametrize("changed_field", ["source", "translation", "finding"])
def test_changed_text_or_finding_invalidates_previously_matching_acknowledgement(changed_field):
    result = warning_result()
    options = Options(source_language="en", target_language="uk")
    old_entry = build_review(result, options)["entries"][0]
    edits = {}
    if changed_field == "source":
        result.segments[0].source = "<r0>Auxiliary pumping station</r0>"
    elif changed_field == "translation":
        edits["one"] = "<r0>Допоміжна насосна станція</r0>"
    else:
        result.issues[0].message = "Додатково перевірте відповідність технічному глосарію."
    view = build_review(
        result, options, edits=edits, accepted={"one": old_entry["fingerprint"]}
    )
    entry = view["entries"][0]
    assert entry["fingerprint"] != old_entry["fingerprint"]
    assert not entry["accepted"]
    assert view["summary"]["accepted"] == 0
    assert view["summary"]["attention"] == 1
    assert view["summary"]["warningCount"] == 1


def test_acknowledgement_for_different_id_or_fingerprint_does_not_hide_warning():
    result = warning_result()
    options = Options(source_language="en", target_language="uk")
    original = build_review(result, options)["entries"][0]
    view = build_review(
        result, options, accepted={"other": original["fingerprint"], "one": "0" * 64}
    )
    assert not view["entries"][0]["accepted"]
    assert view["summary"]["attention"] == 1


@pytest.mark.parametrize("code", ["external_failure", "tracked_changes"])
def test_explicit_error_cannot_be_approved_even_when_fingerprint_matches(code):
    message = "Пошкоджено дані документа. Потрібне виправлення."
    result = make_result(issues=[Issue(code, message, severity="error")])
    original = build_review(result, Options())["entries"][0]
    view = build_review(result, Options(), accepted={original["id"]: original["fingerprint"]})
    entry = view["entries"][0]
    assert entry["severity"] == "error"
    assert entry["messages"] == [message]
    assert not entry["accepted"]
    assert view["summary"]["accepted"] == 0
    assert view["summary"]["attention"] == 1
    assert view["summary"]["warningCount"] == 1


def test_track_changes_acknowledgement_explains_word_revisions_without_modifying_documents(tmp_path):
    source = tmp_path / "revisions.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:del w:id="1"><w:r><w:delText>Deleted</w:delText></w:r></w:del>'
            '<w:ins w:id="2"><w:r><w:t>Current text</w:t></w:r></w:ins></w:p></w:body>'
            '</w:document>',
        )
    output = tmp_path / "revisions.uk.docx"
    output.write_bytes(source.read_bytes())
    data = report_data()
    data["source"], data["output"] = str(source), str(output)
    data["issues"] = [asdict(Issue("tracked_changes", "Old ambiguous warning"))]
    report = write_report(tmp_path, data)
    before = {path: path.read_bytes() for path in (source, output, report)}
    result, options = load_report(report)
    original = build_review(result, options)
    finding = next(entry for entry in original["entries"] if entry["kind"] == "document")
    assert finding["severity"] == "info"
    message = finding["messages"][0]
    assert "Word (Track Changes)" in message
    assert "позначені як видалені" in message
    assert "не приймає та не відхиляє" in message
    assert "перекласти окремо" in message
    assert actionable_count(result.issues) == 0

    confirmed = build_review(
        result, options, accepted={finding["id"]: finding["fingerprint"]}
    )
    confirmed_finding = next(
        entry for entry in confirmed["entries"] if entry["id"] == finding["id"]
    )
    assert confirmed_finding["accepted"]
    assert confirmed_finding["messages"] == [message]
    assert confirmed["summary"]["accepted"] == 1
    assert confirmed["summary"]["attention"] == 0
    assert confirmed["summary"]["notes"] == 1
    assert result.issues[0].message == "Old ambiguous warning"
    for path, contents in before.items():
        assert path.read_bytes() == contents
