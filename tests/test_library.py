from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from forma.core.models import FormaError
from forma.persistence.library import TranslationLibrary


def queue_item(identifier="job-1", **values):
    return {
        "id": identifier,
        "path": f"/documents/{identifier}.docx",
        "name": f"{identifier}.docx",
        "format": "DOCX",
        "status": "queued",
        "statusText": "У черзі",
        "progress": 0,
        **values,
    }


def history_item(identifier="run-1", **values):
    return {
        "id": identifier,
        "name": "report.en.docx",
        "format": "DOCX",
        "source": "/missing/source.docx",
        "output": "/missing/output.en.docx",
        "report": "",
        "status": "done",
        "finishedAt": 10,
        **values,
    }


def test_queue_snapshot_preserves_order_and_survives_restart(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = TranslationLibrary(path)
    first, second = queue_item("first"), queue_item("second", status="translating", progress=40)
    library.save_queue([second, first])
    assert TranslationLibrary(path).load_queue() == [second, first]
    library.save_queue([first])
    assert library.load_queue() == [first]
    library.save_queue([])
    assert library.load_queue() == []


def test_metadata_allowlist_excludes_content_credentials_and_settings(tmp_path):
    path = tmp_path / "library.sqlite3"
    library = TranslationLibrary(path)
    private = {
        "api_key": "private-sample-key",
        "apiKey": "private-sample-key",
        "baseUrl": "https://private.provider.invalid/v1",
        "settings": {"api_key": "nested-key"},
        "segments": [{"source": "private document contents"}],
        "result": object(),
        "options": {"glossary": {"private": "document terms"}},
    }
    library.save_queue([queue_item(**private)])
    library.add_history(history_item(**private))
    for row in [*library.load_queue(), *library.list_history()]:
        assert not row.keys() & private.keys()
    persisted = path.read_bytes()
    for marker in (b"private-sample-key", b"nested-key", b"private.provider", b"private document"):
        assert marker not in persisted


def test_failed_snapshot_does_not_erase_previous_queue(tmp_path):
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    original = queue_item()
    library.save_queue([original])
    for invalid in (
        [queue_item(progress=101)],
        [queue_item(progress=True)],
        [queue_item(elapsedSeconds=float("nan"))],
        [queue_item(status="unknown")],
        [queue_item(), queue_item()],
        [queue_item(path="https://example.org/file.docx")],
    ):
        with pytest.raises(FormaError):
            library.save_queue(invalid)
        assert library.load_queue() == [original]


def test_history_upserts_stable_id_deduplicates_report_and_keeps_missing_paths(tmp_path):
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    report = str(tmp_path / "missing.en.docx.forma.json")
    library.add_history(history_item("older", finishedAt=1, status="cancelled"))
    library.add_history(history_item("newer", finishedAt=20, report=report))
    library.add_history(history_item("newer", finishedAt=21, report=report, warningCount=2))
    library.add_history(
        history_item("imported-duplicate", finishedAt=22, report=report, warningCount=3)
    )
    history = TranslationLibrary(library.path).list_history()
    assert [row["id"] for row in history] == ["newer", "older"]
    assert history[0]["warningCount"] == 3
    assert history[0]["source"] == "/missing/source.docx"
    with pytest.raises(FormaError):
        library.add_history(history_item(status="translating"))


def test_remove_history_only_removes_metadata(tmp_path):
    source, output, report = [
        tmp_path / name for name in ("source.docx", "output.docx", "output.docx.forma.json")
    ]
    for path in (source, output, report):
        path.write_text("untouched")
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    library.add_history(history_item(source=str(source), output=str(output), report=str(report)))
    library.remove_history("run-1")
    assert not library.list_history()
    assert all(path.read_text() == "untouched" for path in (source, output, report))


def report_data(**values):
    return {
        "source": "/original/source.docx",
        "output": "/old/location/translated.docx",
        "options": {
            "source_language": "uk",
            "target_language": "en",
            "glossary": {"private": "text"},
        },
        "model": "translation-model",
        "elapsed_seconds": 12.25,
        "input_tokens": 100,
        "output_tokens": 50,
        "issues": [{"message": "Check layout"}],
        "segments": [{"source": "Private source", "translation": "Private translation"}],
        **values,
    }


def test_import_report_prefers_adjacent_output_and_deduplicates(tmp_path):
    output = tmp_path / "translated.docx"
    output.write_bytes(b"fixture")
    report = tmp_path / "translated.docx.forma.json"
    report.write_text(json.dumps(report_data()), encoding="utf-8")
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    assert library.import_report(report)
    assert library.import_report(report)
    history = library.list_history()
    assert len(history) == 1
    row = history[0]
    assert row["output"] == str(output)
    assert row["report"] == str(report)
    assert row["sourceLanguage"] == "uk" and row["targetLanguage"] == "en"
    assert row["elapsedSeconds"] == 12.25
    assert row["inputTokens"] == 100 and row["warningCount"] == 1
    assert "segments" not in row and "options" not in row
    assert b"Private translation" not in library.path.read_bytes()


def test_import_retains_missing_output_reference(tmp_path):
    report = tmp_path / "translated.pdf.forma.json"
    report.write_text(json.dumps(report_data(output="missing.pdf")), encoding="utf-8")
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    assert library.import_report(report)
    assert library.list_history()[0]["output"] == str(tmp_path / "missing.pdf")


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "[]",
        "null",
        "{}",
        json.dumps(report_data(output="image.png")),
        json.dumps(report_data(options=[])),
        json.dumps(report_data(input_tokens="wrong type")),
        json.dumps(report_data(output="https://example.org/private.docx")),
    ],
)
def test_import_rejects_malformed_or_unsupported_reports(tmp_path, content):
    report = tmp_path / "file.docx.forma.json"
    report.write_text(content, encoding="utf-8")
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    assert not library.import_report(report)
    assert not library.list_history()
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text(json.dumps(report_data()), encoding="utf-8")
    assert not library.import_report(unrelated)


def test_corrupted_database_is_reported_and_not_overwritten(tmp_path):
    path = tmp_path / "library.sqlite3"
    data = b"broken database; keep these original bytes"
    path.write_bytes(data)
    with pytest.raises(FormaError):
        TranslationLibrary(path)
    assert path.read_bytes() == data


def test_unknown_database_schema_is_not_reinitialized(tmp_path):
    path = tmp_path / "library.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(data TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preserve')")
    with pytest.raises(FormaError):
        TranslationLibrary(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT data FROM unrelated").fetchone()[0] == "preserve"


def test_concurrent_distinct_history_writes_are_not_lost(tmp_path):
    library = TranslationLibrary(tmp_path / "library.sqlite3")

    def add(index):
        library.add_history(history_item(f"run-{index}", finishedAt=index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, range(40)))
    history = library.list_history()
    assert len(history) == 40
    assert {row["id"] for row in history} == {f"run-{i}" for i in range(40)}
    assert [row["finishedAt"] for row in history] == list(range(39, -1, -1))


def test_invalid_persisted_record_surfaces_error_without_silent_reset(tmp_path):
    library = TranslationLibrary(tmp_path / "library.sqlite3")
    with sqlite3.connect(library.path) as connection:
        connection.execute("INSERT INTO queue VALUES (0, 'bad', 'not json')")
    with pytest.raises(FormaError):
        library.load_queue()
    with sqlite3.connect(library.path) as connection:
        assert connection.execute("SELECT payload FROM queue").fetchone()[0] == "not json"


def test_default_data_directory_failure_has_safe_message(monkeypatch):
    def unavailable():
        raise OSError('private filesystem diagnostics')

    monkeypatch.setattr('forma.persistence.library.data_directory', unavailable)
    with pytest.raises(FormaError) as error:
        TranslationLibrary()
    assert 'private filesystem' not in str(error.value)
    assert 'бібліотеку' in str(error.value)
