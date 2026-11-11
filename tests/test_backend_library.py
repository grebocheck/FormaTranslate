from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import pump

from forma.core.models import Cancelled, FormaError, TranslationResult
from forma.documents.ooxml import OfficeDocument
from forma.persistence.files import available_output
from forma.presentation.backend import Backend
from forma.settings.config import ENV_FIELDS, Settings
from forma.settings.credentials import CredentialError


class FakeEngine:
    calls = []
    outcomes = {}

    def __init__(self, settings, control):
        self.settings = settings
        self.control = control

    def translate(self, source, folder, options, progress):
        self.calls.append(source)
        progress(25, "Test translation")
        outcome = self.outcomes.get(source.name)
        if outcome == "error":
            raise FormaError("Fixture translation failed")
        if outcome == "cancelled":
            raise Cancelled("Fixture translation cancelled")
        folder.mkdir(parents=True, exist_ok=True)
        output = available_output(source, options.target_language, folder)
        shutil.copyfile(source, output)
        report = output.with_name(output.name + ".forma.json")
        report.write_text(
            json.dumps(
                {
                    "source": str(source),
                    "output": str(output),
                    "options": {
                        "source_language": options.source_language,
                        "target_language": options.target_language,
                    },
                    "model": self.settings.model,
                }
            )
        )
        report.with_suffix(".html").write_text("Fixture bilingual report")
        return TranslationResult(
            source,
            output,
            [],
            {},
            [],
            input_tokens=11,
            output_tokens=7,
            elapsed_seconds=0.25,
            report=report,
        )


@pytest.fixture
def factory(qapp, monkeypatch, tmp_path):
    for name in [*ENV_FIELDS.values(), "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forma.presentation.documents_workflow.TranslationEngine", FakeEngine)
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda _: "")
    FakeEngine.calls, FakeEngine.outcomes = [], {}
    instances = []

    def create():
        backend = Backend()
        backend._settings = Settings(base_url="http://localhost/v1", model="fixture-model")
        instances.append(backend)
        return backend

    yield create
    for backend in instances:
        backend.cancel()
    pump(qapp, lambda: all(not backend._threads for backend in instances))


@pytest.fixture
def inspection_batch(factory, qapp, monkeypatch, tmp_path):
    release = threading.Event()
    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "calls": []}

    def inspect(path):
        with lock:
            state["calls"].append(path)
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            assert release.wait(10), "Inspection must be released by its test"
            return SimpleNamespace(segments=[])
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr("forma.presentation.documents_workflow.OfficeDocument", inspect)
    paths = [tmp_path / f"inspection-{index}.docx" for index in range(12)]
    for path in paths:
        path.write_bytes(b"Offline fixture parsed by the blocked stand-in")
    backend = factory()
    try:
        yield backend, paths, state, release
    finally:
        release.set()
        pump(qapp, lambda: not backend._threads)
        assert state["active"] == 0


def test_inspection_honors_single_document_limit_and_removal_skips_waiting_files(
    inspection_batch, qapp,
):
    backend, paths, state, release = inspection_batch
    backend._settings = backend._settings.with_overrides(parallel_documents=1)
    backend.addFiles([str(path) for path in paths])
    pump(qapp, lambda: state["active"] == 1)
    assert len(backend._threads) == 1 and len(backend._inspection_pending) == 11
    backend._inspect_document(backend._documents[0])
    assert len(backend._inspection_pending) == 11  # Repeated restore/inspection is coalesced.
    for document_id in list(backend._inspection_pending):
        backend.removeDocument(document_id)
    assert len(backend.documents) == 1 and not backend._inspection_pending
    release.set()
    pump(qapp, lambda: not backend._threads)
    assert state["calls"] == [paths[0]] and state["peak"] == 1
    assert backend.documents[0]["status"] == "ready"


def test_closing_drops_waiting_inspections_and_drains_only_two_active_parsers(
    inspection_batch, qapp,
):
    backend, paths, state, release = inspection_batch
    backend._settings = backend._settings.with_overrides(parallel_documents=8)
    backend.addFiles([str(path) for path in paths])
    pump(qapp, lambda: state["active"] == 2)
    assert len(backend._threads) == 2 and len(backend._inspection_pending) == 10
    closed = []
    backend.closedReady.connect(lambda: closed.append(True))
    assert not backend.requestClose()
    assert not backend._inspection_pending and not closed
    release.set()
    pump(qapp, lambda: not backend._threads)
    assert set(state["calls"]) == set(paths[:2]) and state["peak"] == 2
    assert not backend._inspection_active and closed == [True]


def test_queue_survives_restart_without_starting_api_and_missing_files_are_safe(
    factory, qapp, docx_file, tmp_path
):
    second = tmp_path / "second.docx"
    shutil.copyfile(docx_file, second)
    backend = factory()
    backend.addFiles([str(docx_file), str(second)])
    pump(qapp, lambda: not backend._threads)
    assert all(d["status"] == "ready" for d in backend.documents)
    backend.moveDocument(backend.documents[1]["id"], -1)
    original_ids = [d["id"] for d in backend.documents]
    second.unlink()
    restarted = factory()
    pump(
        qapp,
        lambda: (
            len(restarted.documents) == 2
            and all(d["status"] != "loading" for d in restarted.documents)
        ),
    )
    assert [d["id"] for d in restarted.documents] == original_ids
    assert restarted.documents[0]["status"] == "error"
    assert restarted.documents[1]["status"] == "ready"
    assert not FakeEngine.calls
    assert not restarted.busy


def test_restoration_does_not_inspect_newly_added_files_twice(
    factory, qapp, monkeypatch, docx_file
):
    calls = []

    def inspect(path):
        calls.append(path)
        return OfficeDocument(path)

    monkeypatch.setattr("forma.presentation.documents_workflow.OfficeDocument", inspect)
    backend = factory()
    backend.addFiles([str(docx_file)])
    pump(qapp, lambda: not backend._threads)
    assert calls == [docx_file]


def test_interrupted_attempt_becomes_history_once_and_needs_manual_restart(
    factory, qapp, docx_file
):
    backend = factory()
    backend._library.save_queue(
        [
            {
                "id": "job",
                "path": str(docx_file),
                "name": docx_file.name,
                "format": "DOCX",
                "status": "translating",
                "runId": "interrupted-attempt",
                "startedAt": 1,
                "sourceLanguage": "uk",
                "targetLanguage": "en",
                "model": "old-model",
            }
        ]
    )
    restarted = factory()
    pump(qapp, lambda: restarted.documents[0]["status"] == "ready")
    assert not FakeEngine.calls
    assert len(restarted.historyEntries) == 1
    assert restarted.historyEntries[0]["id"] == "interrupted-attempt"
    assert restarted.historyEntries[0]["status"] == "interrupted"
    again = factory()
    pump(qapp, lambda: again.documents[0]["status"] == "ready")
    assert len(again.historyEntries) == 1


def test_restart_keeps_committed_completion_when_queue_snapshot_was_interrupted(
    factory, qapp, docx_file, tmp_path
):
    backend = factory()
    qapp.processEvents()
    output = tmp_path / "finished.en.docx"
    shutil.copyfile(docx_file, output)
    report = output.with_name(output.name + ".forma.json")
    report.write_text("{}")
    backend._library.save_queue(
        [
            {
                "id": "job",
                "path": str(docx_file),
                "name": docx_file.name,
                "format": "DOCX",
                "status": "translating",
                "runId": "committed-attempt",
                "progress": 90,
            }
        ]
    )
    backend._library.add_history(
        {
            "id": "committed-attempt",
            "name": docx_file.name,
            "format": "DOCX",
            "source": str(docx_file),
            "output": str(output),
            "report": str(report),
            "status": "done",
            "finishedAt": 100,
            "elapsedSeconds": 12,
            "inputTokens": 55,
            "outputTokens": 22,
        }
    )
    restarted = factory()
    qapp.processEvents()
    assert restarted.documents[0]["status"] == "done"
    assert restarted.documents[0]["output"] == str(output)
    assert restarted.documents[0]["runId"] == ""
    assert len(restarted.historyEntries) == 1
    assert restarted.historyEntries[0]["status"] == "done"
    assert restarted.historyEntries[0]["inputTokens"] == 55
    assert not FakeEngine.calls


def test_terminal_attempts_enter_history_and_repeat_creates_new_attempt(
    factory, qapp, docx_file, tmp_path
):
    backend = factory()
    backend.setOutputFolder(str(tmp_path / "output"))
    backend.addFiles([str(docx_file)])
    pump(qapp, lambda: not backend._threads)
    backend.start("uk", "en", "General", True, False)
    pump(qapp, lambda: not backend.busy)
    pump(qapp, lambda: backend.historyEntries[0]["reportExists"])
    first = backend.historyEntries[0]
    assert first["status"] == "done" and first["outputExists"] and first["reportExists"]
    assert first["inputTokens"] == 11 and first["outputTokens"] == 7
    assert first["sourceLanguage"] == "uk" and first["targetLanguage"] == "en"
    backend.start("uk", "en", "General", True, False)
    pump(qapp, lambda: not backend.busy)
    assert len(backend.historyEntries) == 2
    assert len({entry["id"] for entry in backend.historyEntries}) == 2
    assert len({entry["output"] for entry in backend.historyEntries}) == 2
    restarted = factory()
    assert {entry["id"] for entry in restarted.historyEntries} == {
        entry["id"] for entry in backend.historyEntries
    }


@pytest.mark.parametrize("outcome", ["error", "cancelled"])
def test_failed_or_cancelled_attempt_is_saved_once(factory, qapp, docx_file, outcome):
    FakeEngine.outcomes[docx_file.name] = outcome
    backend = factory()
    backend.addFiles([str(docx_file)])
    pump(qapp, lambda: not backend._threads)
    backend.start("uk", "en", "General", False, False)
    pump(qapp, lambda: not backend.busy)
    assert len(backend.historyEntries) == 1
    entry = backend.historyEntries[0]
    assert entry["status"] == outcome
    assert entry["error"] and not entry["output"]
    document = backend.documents[0]
    backend._handle_event(
        "cancelled" if outcome == "cancelled" else "file_error",
        {"id": document["id"], "error": "Repeated signal"},
    )
    assert len(backend.historyEntries) == 1
    assert backend.historyEntries[0]["id"] == entry["id"]


def test_forget_preserves_files_and_folder_actions_use_saved_paths(
    factory, qapp, docx_file, tmp_path, monkeypatch
):
    backend = factory()
    folder = tmp_path / "output"
    backend.setOutputFolder(str(folder))
    backend.addFiles([str(docx_file)])
    pump(qapp, lambda: not backend._threads)
    backend.start("uk", "en", "General", False, False)
    pump(qapp, lambda: not backend.busy)
    entry = backend.historyEntries[0]
    opened = []
    monkeypatch.setattr(backend, "openPath", opened.append)
    backend.openOutputFolder()
    backend.openHistoryOutput(entry["id"])
    backend.openHistoryFolder(entry["id"])
    backend.openHistoryReport(entry["id"])
    assert opened == [
        str(folder),
        entry["output"],
        str(folder),
        str(Path(entry["report"]).with_suffix(".html")),
    ]
    backend.forgetHistory(entry["id"])
    assert not backend.historyEntries
    assert all(Path(path).is_file() for path in (entry["source"], entry["output"], entry["report"]))


def test_report_import_deduplicates_existing_history(factory, qapp, docx_file, tmp_path):
    folder = tmp_path / "old-output"
    folder.mkdir()
    output = folder / "old.en.docx"
    shutil.copyfile(docx_file, output)
    report = output.with_name(output.name + ".forma.json")
    report.write_text(
        json.dumps(
            {
                "source": str(docx_file),
                "output": str(output),
                "options": {"source_language": "uk", "target_language": "en"},
                "model": "older-model",
            }
        )
    )
    backend = factory()
    backend.setOutputFolder(str(folder))
    backend.importHistory()
    pump(qapp, lambda: not backend.historyLoading)
    assert len(backend.historyEntries) == 1
    identifier = backend.historyEntries[0]["id"]
    backend.importHistory()
    pump(qapp, lambda: not backend.historyLoading)
    assert [entry["id"] for entry in backend.historyEntries] == [identifier]


def test_catalog_uses_unsaved_endpoint_without_reusing_key_or_saving_preferences(
    factory, qapp, monkeypatch, tmp_path
):
    backend = factory()
    backend._settings = backend._settings.with_overrides(api_key="old-provider-key")
    original = backend._settings
    preference_file = tmp_path / "appdata/preferences.json"
    before = preference_file.read_bytes() if preference_file.exists() else None
    calls = []

    def catalogue(settings):
        calls.append(settings)
        return ["available-model"]

    monkeypatch.setattr("forma.presentation.settings_workflow.list_models", catalogue)
    backend.refreshModels({"baseUrl": "http://another-provider/v1", "apiKey": ""})
    pump(qapp, lambda: not backend.modelsBusy)
    assert len(calls) == 1
    assert calls[0].base_url == "http://another-provider/v1" and not calls[0].api_key
    assert backend.modelChoices == ["available-model"]
    assert backend._settings == original
    assert (preference_file.read_bytes() if preference_file.exists() else None) == before


def test_stale_catalog_response_is_ignored_after_clear(factory, qapp, monkeypatch):
    backend = factory()
    started, release = threading.Event(), threading.Event()

    def catalogue(_):
        started.set()
        assert release.wait(4)
        return ["obsolete-model"]

    monkeypatch.setattr("forma.presentation.settings_workflow.list_models", catalogue)
    try:
        backend.refreshModels({})
        pump(qapp, started.is_set)
        backend.clearModels()
        release.set()
        pump(qapp, lambda: not backend.modelsBusy)
        assert backend.modelChoices == [] and backend.modelsMessage == ""
    finally:
        release.set()


def test_catalog_failure_keeps_manual_settings(factory, qapp, monkeypatch):
    backend = factory()
    original = backend._settings

    def unavailable(_):
        raise FormaError("Провайдер не підтримує список моделей; введіть назву вручну.")

    monkeypatch.setattr("forma.presentation.settings_workflow.list_models", unavailable)
    backend.refreshModels({"baseUrl": "http://another-provider/v1", "apiKey": "draft-key"})
    pump(qapp, lambda: not backend.modelsBusy)
    assert backend._settings == original
    assert "вручну" in backend.modelsMessage
    assert not backend.busy


def test_catalog_can_query_new_local_endpoint_without_available_vault(factory, qapp, monkeypatch):
    backend = factory()
    backend._settings = backend._settings.with_overrides(api_key="old-provider-key")
    calls = []

    def unavailable_vault(_):
        raise CredentialError("Сховище недоступне")

    def catalogue(settings):
        calls.append(settings)
        return ["local-model"]

    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", unavailable_vault)
    monkeypatch.setattr("forma.presentation.settings_workflow.list_models", catalogue)
    backend.refreshModels({"baseUrl": "http://localhost:11434/v1", "apiKey": ""})
    pump(qapp, lambda: not backend.modelsBusy)
    assert len(calls) == 1 and not calls[0].api_key
    assert backend.modelChoices == ["local-model"]


def test_catalog_can_query_corrected_endpoint_without_saving_it(factory, qapp, monkeypatch):
    backend = factory()
    backend._settings = backend._settings.with_overrides(base_url="invalid-old-endpoint")
    calls = []

    def catalogue(settings):
        calls.append(settings.base_url)
        return ["available-model"]

    monkeypatch.setattr("forma.presentation.settings_workflow.list_models", catalogue)
    backend.refreshModels({"baseUrl": "http://localhost:11434/v1"})
    pump(qapp, lambda: not backend.modelsBusy)
    assert calls == ["http://localhost:11434/v1"]
    assert backend._settings.base_url == "invalid-old-endpoint"
