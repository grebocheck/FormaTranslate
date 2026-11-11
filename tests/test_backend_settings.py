from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from support import pump

from forma.presentation.backend import Backend
from forma.settings import credentials
from forma.settings.config import ENV_FIELDS, load_preferences, save_preferences

ENDPOINT = "https://translate.example.org/v1"


def ready_backend():
    backend = Backend()
    pump(QCoreApplication.instance(), lambda: not backend.settingsLoading and not backend._threads)
    return backend


def save(backend, values):
    pump(QCoreApplication.instance(), lambda: not backend.settingsLoading and not backend._threads)
    accepted = backend.saveSettings(values)
    pump(QCoreApplication.instance(), lambda: not backend.settingsSaving and not backend._threads)
    return accepted


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        del self.values[service, username]


@pytest.fixture
def store(qapp, monkeypatch, tmp_path):
    for name in [*ENV_FIELDS.values(), "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    monkeypatch.setattr(credentials, "_native_backend", lambda: store)
    return store


def test_gui_key_is_private_and_settings_survive_restart_over_env(store, tmp_path):
    (tmp_path / ".env").write_text(
        f"OPENAI_API_KEY=legacy-env-key\nOPENAI_MODEL=legacy-model\nOPENAI_BASE_URL={ENDPOINT}\n"
    )
    backend = ready_backend()
    assert save(backend,
        {
            "baseUrl": ENDPOINT,
            "model": "chosen-model",
            "apiKey": "gui-private-key",
            "rememberKey": True,
            "reviewModel": "editor-model",
            "timeout": "90.5",
            "maxOutputTokens": "4000",
            "reasoningEffort": "low",
            "parallelDocuments": "3",
            "maxParallelRequests": "6",
        }
    )
    assert backend.settings["keyPresent"] and backend.settings["keyStored"]
    assert "gui-private-key" not in json.dumps(backend.settings)
    assert "gui-private-key" not in backend.message
    assert "gui-private-key" not in (tmp_path / "appdata/preferences.json").read_text()
    restarted = ready_backend()
    assert restarted._settings.api_key == "gui-private-key"
    assert restarted.settings["model"] == "chosen-model"
    assert restarted.settings["reviewModel"] == "editor-model"
    assert restarted.settings["timeout"] == 90.5
    assert restarted.settings["maxOutputTokens"] == 4000
    assert restarted.settings["reasoningEffort"] == "low"
    assert restarted.settings["parallelDocuments"] == 3
    assert restarted.settings["maxParallelRequests"] == 6
    assert restarted.settings["keyStored"]


def test_blank_key_field_preserves_key_and_switching_endpoint_does_not_reuse_it(store):
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "first", "apiKey": "stored-key"})
    assert save(backend, {"model": "second", "apiKey": ""})
    assert backend._settings.api_key == "stored-key"
    assert backend.settings["model"] == "second"
    assert save(backend, {"baseUrl": "https://other.example.org/v1", "apiKey": ""})
    assert not backend._settings.api_key
    assert not backend.settings["keyPresent"]
    assert not ready_backend().settings["keyPresent"]
    assert credentials.load_api_key(ENDPOINT) == "stored-key"


def test_equivalent_native_address_keeps_key_and_restart_profile(store):
    backend = ready_backend()
    assert save(backend, {"apiMode": "anthropic", "baseUrl": "https://native.example/v1/messages", "model": "test", "apiKey": "native-key"})
    assert save(backend, {"baseUrl": "https://native.example", "apiKey": ""})
    assert backend._settings.api_key == "native-key"
    assert ready_backend()._settings.api_key == "native-key"
    assert save(backend, {"apiMode": "chat", "apiKey": ""})
    assert backend._settings.api_key == ""


def test_remove_key_deletes_vault_entry_and_does_not_restore_env(store, tmp_path):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY=legacy-key\nOPENAI_BASE_URL={ENDPOINT}\n")
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "model", "apiKey": "stored-key"})
    assert save(backend, {"removeKey": True})
    assert credentials.load_api_key(ENDPOINT) == ""
    assert not backend.settings["keyPresent"]
    assert load_preferences()["credential_mode"] == "none"
    assert not ready_backend().settings["keyPresent"]


def test_unavailable_vault_uses_session_key_and_reports_it_honestly(store, monkeypatch, tmp_path):
    backend = ready_backend()

    def unavailable():
        raise RuntimeError("backend error includes private-session-key")

    monkeypatch.setattr(credentials, "_native_backend", unavailable)
    assert save(backend,
        {
            "baseUrl": ENDPOINT,
            "model": "model",
            "apiKey": "private-session-key",
            "rememberKey": True,
        }
    )
    assert backend._settings.api_key == "private-session-key"
    assert backend.settings["keyPresent"] and not backend.settings["keyStored"]
    assert backend.messageKind == "warning"
    assert "до закриття" in backend.message
    assert "до закриття" in backend.settings["credentialStatus"]
    assert "private-session-key" not in json.dumps(backend.settings)
    assert "private-session-key" not in backend.message
    assert "private-session-key" not in (tmp_path / "appdata/preferences.json").read_text()
    assert load_preferences()["credential_mode"] == "session"
    assert not ready_backend().settings["keyPresent"]


@pytest.mark.parametrize(
    "invalid",
    [
        {"timeout": "nan"},
        {"timeout": "inf"},
        {"timeout": -1},
        {"timeout": True},
        {"maxOutputTokens": "1.5"},
        {"maxOutputTokens": -1},
        {"maxOutputTokens": True},
        {"parallelDocuments": 0},
        {"parallelDocuments": 5},
        {"parallelDocuments": True},
        {"maxParallelRequests": 17},
        {"maxParallelRequests": "1.5"},
        {"maxParallelRequests": -1},
    ],
)
def test_invalid_numeric_save_has_no_settings_or_vault_side_effects(store, invalid, tmp_path):
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "model", "apiKey": "old-key"})
    before = backend._settings
    saved = (tmp_path / "appdata/preferences.json").read_bytes()
    vault = dict(store.values)
    assert not save(backend, {**invalid, "apiKey": "new-key"})
    assert backend._settings == before
    assert store.values == vault
    assert (tmp_path / "appdata/preferences.json").read_bytes() == saved
    assert backend.messageKind == "error"


def test_session_only_choice_removes_existing_key_of_new_endpoint(store):
    other = "https://other.example.org/v1"
    credentials.save_api_key(other, "previously-remembered-key")
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "model", "apiKey": "first-key"})
    assert save(backend, {"baseUrl": other, "apiKey": "", "rememberKey": False})
    assert backend._settings.api_key == "previously-remembered-key"
    assert not backend.settings["keyStored"]
    assert credentials.load_api_key(other) == ""
    assert not ready_backend().settings["keyPresent"]


def test_removal_failure_does_not_claim_key_was_deleted(store, monkeypatch, tmp_path):
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "model", "apiKey": "kept-key"})
    before = backend._settings
    saved = (tmp_path / "appdata/preferences.json").read_bytes()

    def fail_delete(*args):
        raise RuntimeError("private backend failure")

    monkeypatch.setattr(store, "delete_password", fail_delete)
    assert save(backend, {"removeKey": True})  # Valid request queued; vault failure arrives asynchronously.
    assert backend._settings == before
    assert (tmp_path / "appdata/preferences.json").read_bytes() == saved
    assert backend.messageKind == "error"
    assert credentials.load_api_key(ENDPOINT) == "kept-key"


def test_qml_settings_save_clears_typed_secret_and_does_not_reload_it(store, qapp, monkeypatch, tmp_path):
    backend = ready_backend()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    try:
        root.setProperty("page", 2)
        qapp.processEvents()
        base = root.findChild(QObject, "baseUrlField")
        model = root.findChild(QObject, "modelField")
        key = root.findChild(QObject, "apiKeyField")
        save = root.findChild(QObject, "saveSettingsButton")
        assert all(item is not None for item in (base, model, key, save))
        assert key.property("text") == ""
        assert root.property("keyVisible") is False
        base.setProperty("text", ENDPOINT)
        model.setProperty("text", "qml-configured-model")
        key.setProperty("text", "qml-private-key")
        save.clicked.emit()
        pump(qapp, lambda: not backend.settingsSaving and not backend._threads)
        assert backend._settings.api_key == "qml-private-key"
        assert backend.settings["keyStored"]
        assert key.property("text") == ""
        assert root.property("keyVisible") is False
        model.setProperty("text", "changed-with-blank-key")
        save.clicked.emit()
        pump(qapp, lambda: not backend.settingsSaving and not backend._threads)
        assert backend._settings.api_key == "qml-private-key"
        assert ready_backend().settings["model"] == "changed-with-blank-key"
        before = backend._settings
        saved = (tmp_path / "appdata/preferences.json").read_bytes()
        vault = dict(store.values)

        def fail_preferences(values):
            raise OSError("untrusted error may contain qml-draft-key")

        monkeypatch.setattr("forma.presentation.settings_io.update_preferences", fail_preferences)
        model.setProperty("text", "unsaved-model-draft")
        key.setProperty("text", "qml-draft-key")
        save.clicked.emit()
        pump(qapp, lambda: not backend.settingsSaving and not backend._threads)
        assert backend._settings is before
        assert store.values == vault
        assert (tmp_path / "appdata/preferences.json").read_bytes() == saved
        assert key.property("text") == "qml-draft-key"
        assert model.property("text") == "unsaved-model-draft"
        assert backend.messageKind == "error" and "qml-draft-key" not in backend.message
        assert not warnings, "\n".join(warnings)
    finally:
        root.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_settings_vault_wait_keeps_gui_responsive_and_preserves_concurrent_theme(store, qapp, monkeypatch):
    backend = ready_backend()
    assert save(backend, {"baseUrl": ENDPOINT, "model": "old-model", "apiKey": "old-key"})
    before = backend._settings
    entered, release = threading.Event(), threading.Event()
    worker_threads, ticks, completed = [], [], []
    original_set = store.set_password

    def slow_set(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        original_set(*args)

    monkeypatch.setattr(store, "set_password", slow_set)
    backend.settingsSaved.connect(lambda: completed.append(True))
    try:
        assert backend.saveSettings({"model": "new-model", "apiKey": "new-key"})
        pump(qapp, entered.is_set)
        assert backend.settingsSaving and backend.busy and backend._settings is before
        assert not backend.saveSettings({"model": "overlapping-save"})
        QTimer.singleShot(0, lambda: ticks.append(True))
        backend.setUiTheme("dark")
        pump(qapp, lambda: bool(ticks))
        assert backend.uiTheme == "dark" and load_preferences()["ui_theme"] == "dark"
        assert not completed
    finally:
        release.set()
        pump(qapp, lambda: not backend.settingsSaving and not backend._threads)
    assert worker_threads and all(value != threading.get_ident() for value in worker_threads)
    assert completed == [True]
    assert backend._settings.api_key == "new-key" and backend.settings["model"] == "new-model"
    assert load_preferences()["ui_theme"] == "dark"


def test_startup_and_reload_track_saved_key_lookup_in_worker(store, qapp, monkeypatch):
    credentials.save_api_key(ENDPOINT, "saved-key")
    save_preferences({"base_url": ENDPOINT, "model": "test-model", "credential_mode": "keyring"})
    entered, release = threading.Event(), threading.Event()
    worker_threads = []
    original_get = store.get_password

    def slow_get(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original_get(*args)

    monkeypatch.setattr(store, "get_password", slow_get)
    backend = Backend()
    assert backend.settingsLoading and backend.busy and not backend._settings.api_key
    assert backend._threads  # Close/settle can track startup work before the first event-loop tick.
    try:
        pump(qapp, entered.is_set)
        assert backend.settingsLoading and not backend._settings.api_key
    finally:
        release.set()
        pump(qapp, lambda: not backend.settingsLoading and not backend._threads)
    assert backend._settings.api_key == "saved-key"

    assert worker_threads and all(value != threading.get_ident() for value in worker_threads)
    entered.clear()
    release.clear()
    try:
        backend.reloadEnv()
        assert backend.settingsLoading and backend.busy
        pump(qapp, entered.is_set)
    finally:
        release.set()
        pump(qapp, lambda: not backend.settingsLoading and not backend._threads)
    assert backend._settings.api_key == "saved-key"

    def unavailable_get(*args):
        raise RuntimeError("vault is locked")

    monkeypatch.setattr(store, "get_password", unavailable_get)
    backend.reloadEnv()
    pump(qapp, lambda: not backend.settingsLoading and not backend._threads)
    assert backend._settings.key_source == "unavailable_keyring"
    assert not backend._usage.available and not backend._usage.working
    assert save(backend, {"model": "still-locked"})
    assert backend._settings.key_source == "unavailable_keyring"
    assert not backend._usage.available
