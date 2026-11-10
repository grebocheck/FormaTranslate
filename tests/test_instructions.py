import json
from dataclasses import replace

import pytest
from test_engine import FakeProvider, local_settings
from test_quick_translation import TextProvider

from forma.config import Settings, load_preferences
from forma.engine import TranslationEngine
from forma.instructions import translation_prompt
from forma.models import MAX_TRANSLATION_INSTRUCTIONS, FormaError, Options
from forma.pdf_api import PDFAPI


def test_rules_are_explicit_preferences_and_never_replace_formatting_contract():
    rules = 'Use British spelling.\nДОДАТКИ → APPENDICES. "quoted" <r0> remains text.'
    prompt = translation_prompt("Preserve rN tags and return JSON.", rules)
    assert prompt.startswith("Preserve rN tags and return JSON.")
    assert json.loads(prompt.splitlines()[-1])["translation_preferences"] == rules
    assert "protected identifiers" in prompt
    assert translation_prompt("Base", " \n") == "Base"


@pytest.mark.parametrize(
    "value", [None, 123, "a" * (MAX_TRANSLATION_INSTRUCTIONS + 1), "bad\x00text"]
)
def test_invalid_rules_rejected_before_translation(value):
    with pytest.raises(FormaError):
        Options(instructions=value).validate()
    with pytest.raises(FormaError):
        Settings(translation_instructions=value).validate(require_connection=False)
    with pytest.raises(FormaError):
        translation_prompt("Base", value)


def test_office_rules_reach_glossary_translation_editor_and_partition_cache(docx_file, tmp_path):
    rules = "Use British English; keep formal technical wording."
    options = Options(instructions=rules)
    provider = FakeProvider()
    first = TranslationEngine(local_settings(), provider=provider).translate(
        docx_file, tmp_path, options
    )
    assert len(provider.calls) >= 3
    assert all(rules in prompt for prompt, _ in provider.calls)
    assert json.loads(first.report.read_text())["options"]["instructions"] == rules
    cached = FakeProvider()
    TranslationEngine(local_settings(), provider=cached).translate(docx_file, tmp_path, options)
    assert not cached.calls
    changed = FakeProvider()
    TranslationEngine(local_settings(), provider=changed).translate(
        docx_file, tmp_path, replace(options, instructions="Use American English.")
    )
    assert changed.calls


def test_quick_text_rules_reach_both_passes_without_becoming_translated_content():
    from forma.quick_translation import translate_text

    provider = TextProvider()
    provider.settings = provider.settings.with_overrides(
        translation_instructions="Use formal wording."
    )
    assert translate_text(provider, "Hello", "en", "uk") == "Вітаю"
    assert len(provider.calls) == 2
    assert all(
        '"translation_preferences": "Use formal wording."' in call[0] for call in provider.calls
    )
    assert all("Use formal wording" not in json.dumps(call[1]) for call in provider.calls)


def test_pdf_cache_identity_changes_with_rules_without_exposing_them():
    settings = local_settings()
    first = PDFAPI(settings, 1)
    second = PDFAPI(settings.with_overrides(translation_instructions="Private customer rules"), 1)
    third = PDFAPI(settings.with_overrides(translation_instructions="Different wording"), 1)
    try:
        assert len({first.cache_model, second.cache_model, third.cache_model}) == 3
        assert "Private" not in second.cache_model
        assert "customer" not in second.cache_model
    finally:
        for bridge in (first, second, third):
            bridge.loop.close()


def test_backend_saves_rules_without_touching_connection_and_restarts(qapp, monkeypatch):
    from forma.backend import Backend

    config = local_settings().with_overrides(api_key="session-private-key")
    monkeypatch.setattr(Backend, "_read_settings", lambda _: config)
    backend = Backend()
    rules = "ДОДАТКИ → APPENDICES\nUse British spelling."
    assert backend.saveTranslationInstructions(rules)
    assert backend._settings.api_key == "session-private-key"
    assert backend.translationInstructions == rules
    assert load_preferences()["translation_instructions"] == rules
    assert "api_key" not in load_preferences()
    assert Settings.load().translation_instructions == rules
    assert not backend.saveTranslationInstructions("x" * 8001)
    assert backend.translationInstructions == rules
    assert backend.instructionsError
    assert backend.saveTranslationInstructions("")
    assert Settings.load().translation_instructions == ""
