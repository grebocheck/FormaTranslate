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
    assert settings.review_model == ""  # Native profiles retain same-model editorial fallback.
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
    assert settings.model == settings.review_model == ""
    assert "private-fixture-key" not in json.dumps(load_preferences())
    for identifier in ("lmstudio", "ollama"):
        save_preferences({"provider_preset": identifier})
        settings = Settings.load()
        assert settings.provider_preset == identifier
        assert settings.model == settings.review_model == ""


def test_presets_are_valid_independent_copies():
    for preset in PRESETS[1:]:
        settings = Settings(
            base_url=preset["baseUrl"],
            model=preset["model"],
            review_model=preset["reviewModel"],
            api_mode=preset["apiMode"],
            provider_preset=preset["id"],
            requests_per_minute=preset["requestsPerMinute"],
            api_key="fixture",
        )
        settings.validate(require_connection=False)
        if preset["model"]:
            settings.validate()
        else:
            with pytest.raises(FormaError, match="модель"):
                settings.validate()
    free = next(p for p in PRESETS if p["id"] == "gemini-free")
    assert free["maxParallelRequests"] == free["parallelDocuments"] == 1
    assert free["requestsPerMinute"] == 5 and not free["reviewModel"]
    openai = next(p for p in PRESETS if p["id"] == "openai")
    assert (openai["model"], openai["reviewModel"]) == ("gpt-6-luna", "gpt-6.1-terra")
    assert all(not p["reviewModel"] for p in PRESETS if p["id"] in {"gemini", "gemini-free", "anthropic"})
    local = {p["id"]: p for p in PRESETS if p["id"] in {"lmstudio", "ollama"}}
    assert local["lmstudio"]["baseUrl"] == "http://localhost:1234/v1"
    assert local["ollama"]["baseUrl"] == "http://localhost:11434/v1"
    for preset in local.values():
        assert preset["apiMode"] == "chat" and preset["responseFormat"] == "auto"
        assert preset["model"] == preset["reviewModel"] == preset["reasoningEffort"] == ""
        assert preset["maxParallelRequests"] == preset["parallelDocuments"] == 1
        assert preset["maxOutputTokens"] == preset["requestsPerMinute"] == 0
    first = provider_presets()
    first[1]["model"] = "changed"
    assert provider_presets()[1]["model"] != "changed"


def test_openai_model_defaults_fill_only_an_unconfigured_official_profile(tmp_path):
    fresh = Settings.load()
    assert (fresh.model, fresh.review_model, fresh.provider_preset) == ("gpt-6-luna", "gpt-6.1-terra", "openai")
    assert Settings().model == Settings().review_model == ""  # Neutral programmatic construction.

    save_preferences({"base_url": "https://API.OPENAI.COM:443/v1/responses/", "api_mode": "responses"})
    assert (Settings.load().model, Settings.load().review_model) == (fresh.model, fresh.review_model)
    save_preferences({"model": "chosen-model"})
    assert (Settings.load().model, Settings.load().review_model) == ("chosen-model", "")
    save_preferences({"model": "chosen-model", "review_model": "chosen-editor"})
    assert (Settings.load().model, Settings.load().review_model) == ("chosen-model", "chosen-editor")
    save_preferences({"model": "", "review_model": ""})
    assert Settings.load().model == Settings.load().review_model == ""

    for base, mode in (
        ("http://127.0.0.1:1234/v1", "chat"),
        ("https://proxy.example/v1", "responses"),
        ("https://api.openai.com/v1/custom", "chat"),
        ("https://generativelanguage.googleapis.com/v1beta", "gemini"),
        ("https://api.anthropic.com/v1", "anthropic"),
    ):
        save_preferences({"base_url": base, "api_mode": mode})
        loaded = Settings.load()
        assert loaded.model == loaded.review_model == "", (base, mode)

    path = tmp_path / "explicit.env"
    path.write_text("OPENAI_MODEL=explicit-model\n")
    assert (Settings.load(path).model, Settings.load(path).review_model) == ("explicit-model", "")
    path.write_text("FORMA_REVIEW_MODEL=\n")
    assert (Settings.load(path).model, Settings.load(path).review_model) == ("gpt-6-luna", "")
    path.write_text("OPENAI_MODEL=\nFORMA_REVIEW_MODEL=\n")
    assert Settings.load(path).model == Settings.load(path).review_model == ""


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
