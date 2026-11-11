from __future__ import annotations

import json

import pytest

from forma.core.models import FormaError
from forma.providers.presets import PRESETS, provider_presets
from forma.settings.config import ENV_FIELDS, Settings, load_preferences, save_preferences


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in [
        *ENV_FIELDS.values(),
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("mode", "key_name", "base"),
    [
        ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta"),
        ("gemini", "GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta"),
        ("anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
    ],
)
def test_native_environment_aliases_and_default_endpoint(tmp_path, mode, key_name, base):
    path = tmp_path / ".env"
    path.write_text(
        f"FORMA_API_MODE={mode}\n{key_name}=native-fixture-key\nOPENAI_MODEL=test-model\n"
    )
    settings = Settings.load(path)
    assert settings.base_url == base and settings.api_mode == mode
    assert settings.api_key == "native-fixture-key"
    settings.validate()
    assert "native-fixture-key" not in repr(settings)


def test_native_environment_key_does_not_cross_saved_endpoint(tmp_path):
    (tmp_path / ".env").write_text("FORMA_API_MODE=gemini\nGEMINI_API_KEY=native-fixture-key\n")
    save_preferences({"base_url": "https://api.anthropic.com/v1", "api_mode": "anthropic"})
    assert Settings.load().api_key == ""


@pytest.mark.parametrize("value", [-1, True, 3.1, "5", 6001, float("nan")])
def test_invalid_rpm_is_rejected(value):
    with pytest.raises(FormaError, match="від 0 до 6000"):
        Settings(requests_per_minute=value).validate(require_connection=False)


@pytest.mark.parametrize("value", ["-1", "true", "3.1", "6001", "nan"])
def test_invalid_environment_rpm_is_rejected(tmp_path, value):
    path = tmp_path / ".env"
    path.write_text("FORMA_REQUESTS_PER_MINUTE=" + value)
    with pytest.raises(FormaError, match="від 0 до 6000"):
        Settings.load(path)


def test_saved_preset_and_rpm_roundtrip_excludes_api_keys(tmp_path):
    save_preferences(
        {
            "provider_preset": "gemini-free",
            "requests_per_minute": 5,
            "api_key": "private-fixture-key",
            "GEMINI_API_KEY": "private-fixture-key",
        }
    )
    settings = Settings.load()
    assert settings.provider_preset == "gemini-free" and settings.requests_per_minute == 5
    assert "private-fixture-key" not in json.dumps(load_preferences())


def test_presets_are_valid_independent_copies():
    for preset in PRESETS[1:]:
        settings = Settings(
            base_url=preset["baseUrl"],
            model=preset["model"],
            api_mode=preset["apiMode"],
            provider_preset=preset["id"],
            requests_per_minute=preset["requestsPerMinute"],
            api_key="fixture",
        )
        settings.validate()
    free = next(p for p in PRESETS if p["id"] == "gemini-free")
    assert free["maxParallelRequests"] == free["parallelDocuments"] == 1
    assert free["requestsPerMinute"] == 5 and not free["reviewModel"]
    first = provider_presets()
    first[1]["model"] = "changed"
    assert provider_presets()[1]["model"] != "changed"


@pytest.mark.parametrize(
    "host,mode",
    [
        ("generativelanguage.googleapis.com/v1beta", "gemini"),
        ("api.anthropic.com/v1", "anthropic"),
    ],
)
def test_official_native_endpoint_needs_key_to_translate(host, mode):
    settings = Settings(base_url="https://" + host, model="test-model", api_mode=mode)
    settings.validate(require_connection=False)
    with pytest.raises(FormaError, match="API-ключ"):
        settings.validate()
