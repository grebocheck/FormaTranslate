"""Redirected Windows ANSI streams must carry complete UTF-8 CLI output."""
from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest
from ui_support import forbid_provider_requests

from forma import __main__ as cli
from forma.settings.config import Settings


@pytest.fixture(autouse=True)
def no_provider_requests(monkeypatch):
    attempted = forbid_provider_requests(monkeypatch)
    yield
    assert not attempted


def redirected_main(monkeypatch, arguments):
    output, errors = io.BytesIO(), io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(errors, encoding="cp1252", errors="strict")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["forma", *map(str, arguments)])
        patch.setattr(sys, "stdout", stdout)
        patch.setattr(sys, "stderr", stderr)
        try:
            result = cli.main()
        except SystemExit as exc:
            result = exc.code
        stdout.flush()
        stderr.flush()
        return result, output.getvalue(), errors.getvalue()


def test_redirected_inspect_json_contains_original_unicode_text(monkeypatch, tmp_path):
    source = tmp_path / "Джерело Łódź №25.txt"
    source.write_text("Теплолічильник 25 кВт\n", encoding="utf-8")
    result, output, errors = redirected_main(monkeypatch, ["inspect", source])
    assert result == 0 and not errors
    assert "Теплолічильник".encode() in output
    report = json.loads(output.decode("utf-8"))
    assert report["segments"][0]["text"] == "Теплолічильник 25 кВт"


def test_redirected_compare_json_keeps_unicode_paths_and_notes(monkeypatch, tmp_path):
    source, translated = tmp_path / "Оригінал №25.txt", tmp_path / "Переклад №25.txt"
    for path in (source, translated):
        path.write_text("Теплолічильник 25 кВт\n", encoding="utf-8")
    report_path = tmp_path / "Порівняння №25"
    result, output, errors = redirected_main(monkeypatch, ["compare", source, translated, "--report", report_path])
    assert result == 0 and not errors
    comparison = json.loads(output.decode("utf-8"))
    assert comparison["source"] == str(source)
    assert comparison["candidates"][0]["file"] == str(translated)
    assert comparison["candidates"][0]["structure_ok"] and comparison["candidates"][0]["numbers_preserved"]
    assert "Структурні метрики".encode() in output
    assert json.loads(report_path.with_suffix(".json").read_text(encoding="utf-8")) == comparison


@pytest.mark.parametrize("suffix,expected", [(".unknown", "Непідтримуваний формат документа"), (".txt", "Відсутній файл")])
def test_redirected_errors_preserve_localized_message_and_unicode_path(monkeypatch, tmp_path, suffix, expected):
    source = tmp_path / ("Відсутній файл №25" + suffix)
    result, output, errors = redirected_main(monkeypatch, ["inspect", source])
    assert result == 1 and not output
    assert expected in errors.decode("utf-8")
    assert b"UnicodeEncodeError" not in errors


def test_argument_parser_configures_streams_before_unicode_usage_error(monkeypatch, tmp_path):
    result, output, errors = redirected_main(monkeypatch, ["inspect", tmp_path / "Джерело.txt", "--невідома-опція"])
    assert result == 2 and not output
    assert "--невідома-опція" in errors.decode("utf-8")


def test_preview_worker_unicode_argument_error_stays_before_settings_or_gui(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("Preview argument parsing must not load settings or the GUI")

    monkeypatch.setattr(Settings, "load", unexpected)
    monkeypatch.setattr("forma.app.main", unexpected)
    result, output, errors = redirected_main(monkeypatch, [
        "preview-worker", "--source", tmp_path / "Джерело.pdf",
        "--metadata", tmp_path / "Метадані.json", "--невідома-опція",
    ])
    assert result == 2 and not output
    assert "--невідома-опція" in errors.decode("utf-8")


def test_translation_progress_and_result_path_use_utf8_not_json_escaping(monkeypatch, tmp_path):
    destination = tmp_path / "Переклади — Łódź" / "Документ №25.uk.txt"
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, env_path=None: Settings()))

    class OfflineEngine:
        def __init__(self, settings):
            pass

        def translate(self, source, output, options, progress):
            progress(50, "Перекладаємо документ — 25 кВт")
            return SimpleNamespace(output=destination)

    monkeypatch.setattr("forma.translation.engine.TranslationEngine", OfflineEngine)
    result, output, errors = redirected_main(monkeypatch, ["translate", tmp_path / "Джерело.txt", "--no-review"])
    assert result == 0
    assert output.decode("utf-8").strip() == str(destination)
    assert "50% Перекладаємо документ — 25 кВт" in errors.decode("utf-8")


class CaptureWithoutReconfigure(io.StringIO):
    def reconfigure(self, **kwargs):
        raise io.UnsupportedOperation("Capture stream owns its encoding")


@pytest.mark.parametrize("factory", [io.StringIO, CaptureWithoutReconfigure])
def test_cli_preserves_capture_streams_without_console_attributes(monkeypatch, tmp_path, factory):
    source = tmp_path / "Джерело.txt"
    source.write_text("Теплолічильник 25 кВт\n", encoding="utf-8")
    output, errors = factory(), factory()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["forma", "inspect", str(source)])
        patch.setattr(sys, "stdout", output)
        patch.setattr(sys, "stderr", errors)
        result = cli.main()
        assert sys.stdout is output and sys.stderr is errors
    assert result == 0 and not errors.getvalue()
    assert json.loads(output.getvalue())["segments"][0]["text"] == "Теплолічильник 25 кВт"


@pytest.mark.parametrize("arguments", [[], ["gui", "Документ №25.txt"]])
@pytest.mark.parametrize("stream", [None, io.StringIO()])
def test_default_and_explicit_gui_leave_missing_or_capture_stdio_untouched(monkeypatch, arguments, stream):
    calls = []
    monkeypatch.setattr("forma.app.main", lambda files, screenshot: calls.append((files, screenshot)) or 17)
    monkeypatch.setattr(cli, "_configure_cli_streams", lambda: pytest.fail("GUI must not configure CLI streams"))
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["forma", *arguments])
        patch.setattr(sys, "stdout", stream)
        patch.setattr(sys, "stderr", stream)
        result = cli.main()
        assert sys.stdout is stream and sys.stderr is stream
    assert result == 17
    assert calls == [(["Документ №25.txt"] if arguments else [], None)]


def test_windowed_cli_tolerates_missing_standard_streams(monkeypatch, tmp_path):
    source = tmp_path / "Джерело.txt"
    source.write_text("Теплолічильник 25 кВт\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["forma", "inspect", str(source)])
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", None)
        result = cli.main()
        assert sys.stdout is None and sys.stderr is None
    assert result == 0
