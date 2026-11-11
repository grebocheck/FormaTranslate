from __future__ import annotations

import hashlib
import json

import pytest

from forma.core.models import FormaError
from forma.settings import credentials
from forma.settings.config import ENV_FIELDS, Settings, load_preferences, save_preferences
from forma.settings.connection import legacy_connection_urls


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
def store(monkeypatch, tmp_path):
    for name in [*ENV_FIELDS.values(), "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    store = MemoryStore()
    monkeypatch.setattr(credentials, "_native_backend", lambda: store)
    return store


def test_endpoint_bound_keys_survive_restart_without_env(store):
    endpoint = "https://api.example.org/v1"
    credentials.save_api_key(endpoint, "sample-private-key")
    save_preferences(
        {
            "base_url": endpoint,
            "model": "translation-model",
            "credential_mode": "keyring",
            "timeout": 90,
            "max_output_tokens": 8000,
            "reasoning_effort": "low",
            "api_key": "sample-private-key",
        }
    )
    loaded = Settings.load()
    loaded.validate()
    assert loaded.api_key == "sample-private-key"
    assert loaded.key_stored and loaded.key_source == "keyring"
    assert loaded.max_output_tokens == 8000 and loaded.timeout == 90
    assert "sample-private-key" not in repr(loaded)
    assert "api_key" not in load_preferences()
    assert credentials.load_api_key("https://api.example.org/v2") == ""
    assert credentials.load_api_key("http://api.example.org/v1") == ""
    assert credentials.load_api_key("https://other.example.org/v1") == ""
    assert (
        credentials.load_api_key("https://API.EXAMPLE.ORG:443/v1/chat/completions/")
        == "sample-private-key"
    )


def test_remove_key_suppresses_legacy_env_fallback(store, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=old-env-key\nOPENAI_MODEL=legacy-model\n")
    endpoint = "https://api.openai.com/v1"
    credentials.save_api_key(endpoint, "current-key")
    credentials.delete_api_key(endpoint)
    credentials.delete_api_key(endpoint)  # Missing credentials are already removed.
    save_preferences({"model": "chosen-model", "credential_mode": "none"})
    loaded = Settings.load()
    assert not loaded.api_key and not loaded.key_stored
    assert loaded.model == "chosen-model"


def test_saved_preferences_override_env_and_endpoint_changes_never_reuse_env_key(store, tmp_path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=old-env-key\nOPENAI_MODEL=legacy-model\n"
        "OPENAI_BASE_URL=https://api.openai.com/v1\nFORMA_REQUEST_TIMEOUT=180\n"
    )
    save_preferences(
        {"base_url": "https://different.example.org/v1", "model": "chosen-model", "timeout": 60}
    )
    loaded = Settings.load()
    assert loaded.base_url == "https://different.example.org/v1"
    assert loaded.model == "chosen-model" and loaded.timeout == 60
    assert not loaded.api_key


def test_explicit_env_file_is_deterministic_legacy_override(store, tmp_path):
    path = tmp_path / "profile.env"
    path.write_text("OPENAI_API_KEY=explicit-env-key\nOPENAI_MODEL=explicit-model\n")
    save_preferences(
        {
            "base_url": "https://different.example.org/v1",
            "model": "chosen-model",
            "credential_mode": "none",
        }
    )
    loaded = Settings.load(path)
    assert loaded.model == "explicit-model" and loaded.api_key == "explicit-env-key"
    assert loaded.base_url == "https://api.openai.com/v1"


def test_session_mode_does_not_persist_key_or_resurrect_env(store, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=legacy-key\n")
    save_preferences(
        {"credential_mode": "session", "model": "chosen-model", "api_key": "in-memory-only"}
    )
    loaded = Settings.load()
    assert not loaded.api_key and not loaded.key_stored
    assert "сесії" in loaded.credential_status
    assert "in-memory-only" not in (tmp_path / "appdata/preferences.json").read_text()
    assert not store.values


def test_store_failure_is_safe_and_does_not_create_plaintext_fallback(store, monkeypatch, tmp_path):
    def unavailable():
        raise RuntimeError("backend echoed private-sample-key")

    monkeypatch.setattr(credentials, "_native_backend", unavailable)
    for action in (
        lambda: credentials.save_api_key("https://api.example.org/v1", "private-sample-key"),
        lambda: credentials.load_api_key("https://api.example.org/v1"),
        lambda: credentials.delete_api_key("https://api.example.org/v1"),
    ):
        with pytest.raises(credentials.CredentialError) as caught:
            action()
        assert "private-sample-key" not in str(caught.value)
    save_preferences({"base_url": "https://api.example.org/v1", "credential_mode": "keyring"})
    loaded = Settings.load()
    assert not loaded.api_key and not loaded.key_stored
    assert "сховища" in loaded.credential_status
    assert not any("private-sample-key" in p.read_text() for p in tmp_path.rglob("*.json"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1, True])
def test_invalid_timeout_rejected(value):
    with pytest.raises(FormaError, match="Час очікування"):
        Settings(timeout=value).validate(require_connection=False)


@pytest.mark.parametrize("value", [-1, 1.5, float("nan"), float("inf"), True])
def test_invalid_token_limit_rejected(value):
    with pytest.raises(FormaError, match="Ліміт"):
        Settings(max_output_tokens=value).validate(require_connection=False)


@pytest.mark.parametrize(
    "address",
    [
        "https://example.org:invalid/v1",
        "https://user:password@example.org/v1",
        "https://example.org/v1?key=secret",
        "file:///tmp/data",
        "https://[broken",
    ],
)
def test_invalid_endpoints_rejected_before_key_lookup(address, store):
    with pytest.raises(FormaError):
        credentials.load_api_key(address)
    assert not store.values


def test_preferences_are_allowlisted_atomic_json_and_accept_numeric_values(store, tmp_path):
    save_preferences(
        {
            "model": "model",
            "timeout": 30.5,
            "max_output_tokens": 2000,
            "apiKey": "secret",
            "api_key": "secret",
            "other": "secret",
        }
    )
    path = tmp_path / "appdata/preferences.json"
    data = json.loads(path.read_text())
    assert data == {"model": "model", "timeout": 30.5, "max_output_tokens": 2000}
    assert not list(path.parent.glob(".forma-*"))


def test_incomplete_settings_can_be_saved_but_not_used_for_connection():
    Settings().validate(require_connection=False)
    with pytest.raises(FormaError, match="модель"):
        Settings().validate()


def test_deeply_nested_preferences_do_not_prevent_startup(store, tmp_path):
    path = tmp_path / "appdata/preferences.json"
    path.parent.mkdir(exist_ok=True)
    content = '{"model":' + "[" * 20000 + "0" + "]" * 20000 + "}"
    path.write_text(content)
    assert load_preferences() == {}
    assert Settings.load().model == ""
    assert path.read_text() == content


@pytest.mark.parametrize("mode,root,old_url", [
    ("anthropic", "https://native.example/v1", "https://native.example/v1/messages"),
    ("gemini", "https://native.example/v1beta", "https://native.example/v1beta/models/example:generateContent"),
])
def test_saved_legacy_native_profile_migrates_key_without_crossing_protocols(store, mode, root, old_url):
    old_account = "api-key:" + hashlib.sha256(old_url.encode()).hexdigest()
    store.set_password(credentials.SERVICE, old_account, "native-profile-key")
    # A newly selected protocol may not guess the owner of an untyped key.
    assert credentials.load_api_key(old_url, mode) == ""
    save_preferences({"base_url": old_url, "api_mode": mode, "model": "test", "credential_mode": "keyring"})
    loaded = Settings.load()
    assert loaded.api_key == "native-profile-key" and loaded.key_stored
    assert credentials.load_api_key(root, mode) == "native-profile-key"
    assert credentials.load_api_key(root, "chat") == ""
    assert legacy_connection_urls(loaded.with_overrides(api_mode="chat")) == ()
    assert store.get_password(credentials.SERVICE, old_account) == "native-profile-key"


def test_custom_chat_message_path_is_distinct_from_native_connection(store):
    root = "https://custom.example/v1"
    credentials.save_api_key(root, "chat-key")
    assert credentials.load_api_key(root + "/responses", "responses") == "chat-key"
    assert credentials.load_api_key(root + "/messages", "chat") == ""
    assert credentials.load_api_key(root + "/messages", "anthropic") == ""
    old_url = root + "/messages"
    old_account = "api-key:" + hashlib.sha256(old_url.encode()).hexdigest()
    store.set_password(credentials.SERVICE, old_account, "custom-profile-key")
    save_preferences({"base_url": old_url, "api_mode": "chat", "model": "test", "credential_mode": "keyring"})
    assert Settings.load().api_key == "custom-profile-key"
    assert credentials.load_api_key(root, "chat") == "chat-key"
    assert credentials.load_api_key(old_url, "anthropic") == ""
