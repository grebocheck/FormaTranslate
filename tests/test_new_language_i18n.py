"""New locale choices survive a real preference reload without rewriting user values."""
from __future__ import annotations

import pytest
from support import settle
from ui_support import forbid_provider_requests

from forma.localization.catalog import current_language
from forma.presentation.backend import Backend
from forma.settings.config import ENV_FIELDS, Settings, load_preferences, update_preferences


@pytest.mark.parametrize("language,theme", (("nl", "dark"), ("sk", "light")))
def test_new_interface_language_persists_with_canonical_preferences(qapp, monkeypatch, tmp_path, language, theme):
    provider_requests = forbid_provider_requests(monkeypatch)
    for name in (*ENV_FIELDS.values(), "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings.load())
    monkeypatch.setattr("forma.presentation.settings_workflow.load_api_key", lambda *args, **kwargs: "")
    update_preferences({
        "base_url": "http://localhost/v1", "provider_preset": "custom",
        "model": "Instellingen/Nastavenia:25", "review_model": "editor/model-{language}",
    })
    instructions = "Instellingen / Nastavenia — keep {project} unchanged."
    backend = Backend()
    settle(qapp, backend)
    try:
        assert backend.saveTranslationInstructions(instructions)
        backend.setWorkspaceOptions({
            "sourceLanguage": "en", "targetLanguage": "uk", "domain": "Технічна документація",
            "review": True, "autoGlossary": False,
        })
        options = dict(backend.workspaceOptions)
        backend.setUiTheme(theme)
        backend.setUiLanguage(language)
        assert backend.uiLanguage == language
        assert backend.translationInstructions == instructions
        assert backend.workspaceOptions == options
        assert backend.settings["model"] == "Instellingen/Nastavenia:25"
        assert backend.settings["reviewModel"] == "editor/model-{language}"
        assert load_preferences()["ui_language"] == language
        assert load_preferences()["ui_theme"] == theme
    finally:
        backend.cancel()
        settle(qapp, backend)
    monkeypatch.delenv("FORMA_UI_LANGUAGE", raising=False)
    restarted = Backend()
    try:
        settle(qapp, restarted)
        assert restarted.uiLanguage == current_language() == language
        assert restarted.uiTheme == theme
        assert restarted.translationInstructions == instructions
        assert restarted.workspaceOptions == options
        assert restarted.settings["model"] == "Instellingen/Nastavenia:25"
        assert restarted.settings["reviewModel"] == "editor/model-{language}"
        assert not provider_requests
    finally:
        restarted.cancel()
        settle(qapp, restarted)
