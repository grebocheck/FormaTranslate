from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from forma.localization import catalog
from forma.presentation.backend import Backend
from forma.settings.config import Settings, load_preferences, save_preferences


@pytest.fixture
def make_backend(qapp, monkeypatch):
    # Startup locale checks need neither credentials nor external worker probes.
    monkeypatch.setattr(Backend, "_read_settings", lambda _: Settings())
    monkeypatch.setattr(Backend, "_refresh_worker_availability", lambda _: None)
    backends = []

    def create():
        backend = Backend()
        backends.append(backend)
        return backend

    yield create
    for backend in backends:
        backend._preview.shutdown()
        backend.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def choose_startup_preference(monkeypatch, origin, preference):
    if origin == "environment":
        save_preferences({"ui_language": "de"})
        monkeypatch.setenv("FORMA_UI_LANGUAGE", preference)
    else:
        save_preferences({"ui_language": preference})
        monkeypatch.delenv("FORMA_UI_LANGUAGE", raising=False)


@pytest.mark.parametrize("origin", ("environment", "preferences"))
@pytest.mark.parametrize("system_locale", ("en_US", "uk_UA"))
@pytest.mark.parametrize(
    "preference,expected",
    (("NL_BE", "nl"), ("sk_SK", "sk"), ("pt_BR", "pt-BR"), ("en_GB", "en")),
)
def test_regional_startup_choice_overrides_system_language(
    monkeypatch, make_backend, origin, system_locale, preference, expected,
):
    choose_startup_preference(monkeypatch, origin, preference)
    monkeypatch.setattr(catalog, "_system_locale_name", lambda: system_locale)

    backend = make_backend()

    assert backend.uiLanguage == expected
    assert catalog.current_language() == expected
    # Resolving an existing preference does not rewrite user settings at startup.
    assert load_preferences()["ui_language"] == (
        "de" if origin == "environment" else preference
    )


@pytest.mark.parametrize("origin", ("environment", "preferences"))
@pytest.mark.parametrize("preference", ("system", "unsupported-region"))
@pytest.mark.parametrize("system_locale,expected", (("en_US", "en"), ("uk_UA", "uk")))
def test_system_and_unsupported_startup_preferences_follow_system_language(
    monkeypatch, make_backend, origin, preference, system_locale, expected,
):
    choose_startup_preference(monkeypatch, origin, preference)
    monkeypatch.setattr(catalog, "_system_locale_name", lambda: system_locale)

    backend = make_backend()

    assert backend.uiLanguage == "system"
    assert catalog.current_language() == expected


def test_locale_matching_keeps_fallback_decisions_at_the_call_site():
    assert catalog.match_language("en_GB") == "en"
    assert catalog.match_language("nl-BE-u-nu-latn") == "nl"
    assert catalog.match_language("sk_SK_u_ca_gregory") == "sk"
    assert catalog.match_language("system") is None
    assert catalog.match_language("unsupported-region") is None
    assert catalog.resolve_language("unsupported-region") == "en"
