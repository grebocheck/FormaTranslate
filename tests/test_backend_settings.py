from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine

from forma.presentation.backend import Backend
from forma.settings import credentials
from forma.settings.config import ENV_FIELDS, load_preferences

ENDPOINT = "https://translate.example.org/v1"


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
    backend = Backend()
    assert backend.saveSettings(
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
    restarted = Backend()
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
    backend = Backend()
    assert backend.saveSettings({"baseUrl": ENDPOINT, "model": "first", "apiKey": "stored-key"})
    assert backend.saveSettings({"model": "second", "apiKey": ""})
    assert backend._settings.api_key == "stored-key"
    assert backend.settings["model"] == "second"
    assert backend.saveSettings({"baseUrl": "https://other.example.org/v1", "apiKey": ""})
    assert not backend._settings.api_key
    assert not backend.settings["keyPresent"]
    assert not Backend().settings["keyPresent"]
    assert credentials.load_api_key(ENDPOINT) == "stored-key"


def test_remove_key_deletes_vault_entry_and_does_not_restore_env(store, tmp_path):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY=legacy-key\nOPENAI_BASE_URL={ENDPOINT}\n")
    backend = Backend()
    assert backend.saveSettings({"baseUrl": ENDPOINT, "model": "model", "apiKey": "stored-key"})
    assert backend.saveSettings({"removeKey": True})
    assert credentials.load_api_key(ENDPOINT) == ""
    assert not backend.settings["keyPresent"]
    assert load_preferences()["credential_mode"] == "none"
    assert not Backend().settings["keyPresent"]


def test_unavailable_vault_uses_session_key_and_reports_it_honestly(store, monkeypatch, tmp_path):
    backend = Backend()

    def unavailable():
        raise RuntimeError("backend error includes private-session-key")

    monkeypatch.setattr(credentials, "_native_backend", unavailable)
    assert backend.saveSettings(
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
    assert not Backend().settings["keyPresent"]


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
    backend = Backend()
    assert backend.saveSettings({"baseUrl": ENDPOINT, "model": "model", "apiKey": "old-key"})
    before = backend._settings
    saved = (tmp_path / "appdata/preferences.json").read_bytes()
    vault = dict(store.values)
    assert not backend.saveSettings({**invalid, "apiKey": "new-key"})
    assert backend._settings == before
    assert store.values == vault
    assert (tmp_path / "appdata/preferences.json").read_bytes() == saved
    assert backend.messageKind == "error"


def test_session_only_choice_removes_existing_key_of_new_endpoint(store):
    other = "https://other.example.org/v1"
    credentials.save_api_key(other, "previously-remembered-key")
    backend = Backend()
    assert backend.saveSettings({"baseUrl": ENDPOINT, "model": "model", "apiKey": "first-key"})
    assert backend.saveSettings({"baseUrl": other, "apiKey": "", "rememberKey": False})
    assert backend._settings.api_key == "previously-remembered-key"
    assert not backend.settings["keyStored"]
    assert credentials.load_api_key(other) == ""
    assert not Backend().settings["keyPresent"]


def test_removal_failure_does_not_claim_key_was_deleted(store, monkeypatch, tmp_path):
    backend = Backend()
    assert backend.saveSettings({"baseUrl": ENDPOINT, "model": "model", "apiKey": "kept-key"})
    before = backend._settings
    saved = (tmp_path / "appdata/preferences.json").read_bytes()

    def fail_delete(*args):
        raise RuntimeError("private backend failure")

    monkeypatch.setattr(store, "delete_password", fail_delete)
    assert not backend.saveSettings({"removeKey": True})
    assert backend._settings == before
    assert (tmp_path / "appdata/preferences.json").read_bytes() == saved
    assert backend.messageKind == "error"
    assert credentials.load_api_key(ENDPOINT) == "kept-key"


def test_qml_settings_save_clears_typed_secret_and_does_not_reload_it(store, qapp):
    backend = Backend()
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
        qapp.processEvents()
        assert backend._settings.api_key == "qml-private-key"
        assert backend.settings["keyStored"]
        assert key.property("text") == ""
        assert root.property("keyVisible") is False
        model.setProperty("text", "changed-with-blank-key")
        save.clicked.emit()
        qapp.processEvents()
        assert backend._settings.api_key == "qml-private-key"
        assert Backend().settings["model"] == "changed-with-blank-key"
        assert not warnings, "\n".join(warnings)
    finally:
        root.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
