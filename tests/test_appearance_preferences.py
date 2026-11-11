from __future__ import annotations

import pytest

from forma.core.models import FormaError
from forma.presentation.backend import Backend
from forma.settings.config import Settings, load_preferences


def test_theme_is_system_by_default_and_switching_preserves_session_state(qapp, monkeypatch):
    monkeypatch.delenv("FORMA_UI_LANGUAGE", raising=False)
    monkeypatch.setattr(Backend, '_read_settings', lambda _: Settings.load())
    backend = Backend()
    assert backend.uiTheme == 'system'
    backend._selected = 'selected-document'
    backend._review_editing = True
    backend._review_draft_text = 'Unapplied review wording'
    backend.setUiLanguage('fr')
    backend.setUiTheme('dark')
    backend.setUiLanguage('es')
    assert backend._selected == 'selected-document'
    assert backend._review_editing and backend._review_draft_text == 'Unapplied review wording'
    assert load_preferences()['ui_theme'] == 'dark'
    assert load_preferences()['ui_language'] == 'es'
    restarted = Backend()
    assert restarted.uiTheme == 'dark' and restarted.uiLanguage == 'es'
    backend.setUiTheme('light')
    backend.setUiTheme('system')
    assert backend.uiTheme == 'system' and load_preferences()['ui_theme'] == 'system'


def test_invalid_theme_and_failed_save_keep_previous_choice(qapp, monkeypatch):
    backend = Backend()
    backend.setUiTheme('dark')
    changes = []
    backend.uiThemeChanged.connect(lambda: changes.append(backend.uiTheme))
    backend.setUiTheme('not-a-theme')
    backend.setUiTheme('dark')
    assert not changes

    def fail(_):
        raise OSError('unavailable preferences')

    monkeypatch.setattr('forma.presentation.settings_workflow.save_preferences', fail)
    backend.setUiTheme('light')
    assert backend.uiTheme == 'dark' and not changes
    assert backend.messageKind == 'error'


@pytest.mark.parametrize('invalid', ['yes', 1, None])
def test_extended_abbyy_requires_literal_license_confirmation(invalid):
    with pytest.raises(FormaError, match='явним вибором'):
        Settings(scan_provider='abbyy_extended', abbyy_extended_cli_confirmed=invalid).validate(require_connection=False)
