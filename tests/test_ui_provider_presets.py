from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.backend import Backend
from forma.config import Settings
from forma.i18n import CatalogTranslator


@pytest.fixture
def settings_window(qapp, monkeypatch):
    monkeypatch.setattr(
        Backend,
        "_read_settings",
        lambda _: Settings(
            base_url="https://existing.invalid/v1", model="existing-model", api_key="old-secret"
        ),
    )
    backend = Backend()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "src/forma/ui/Main.qml")))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    window.setProperty("page", 2)
    qapp.processEvents()
    yield backend, window, warnings
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.removeTranslator(translator)


def select_preset(backend, window, identifier):
    index = next(i for i, preset in enumerate(backend.providerPresets) if preset["id"] == identifier)
    selector = window.findChild(QQuickItem, "providerPresetSelector")
    selector.activated.emit(index)
    return selector


def test_preset_changes_only_draft_and_clears_cross_provider_secret(settings_window, monkeypatch):
    backend, window, warnings = settings_window
    monkeypatch.setattr(backend, "_launch", lambda *args: pytest.fail("Preset started a job"))
    key = window.findChild(QQuickItem, "apiKeyField")
    key.setProperty("text", "typed-private-key")
    window.setProperty("keyVisible", True)
    backend._model_choices = ["old-catalog-model"]
    backend.changed.emit()
    select_preset(backend, window, "gemini-free")
    assert window.property("providerPresetId") == "gemini-free"
    assert window.findChild(QQuickItem, "apiModeSelector").property("currentText") == "gemini"
    assert window.findChild(QQuickItem, "baseUrlField").property("text") == (
        "https://generativelanguage.googleapis.com/v1beta"
    )
    assert window.findChild(QQuickItem, "requestsPerMinuteField").property("text") == "5"
    assert window.findChild(QQuickItem, "parallelRequestsField").property("text") == "1"
    assert window.findChild(QQuickItem, "parallelDocumentsField").property("text") == "1"
    assert key.property("text") == ""
    assert key.property("placeholderText") == "Вставте API-ключ"
    assert window.property("keyVisible") is False
    assert window.property("savedEndpointSelected") is False
    assert backend.modelChoices == []
    assert backend._settings.model == "existing-model"
    assert backend._settings.api_key == "old-secret"
    assert backend._settings.base_url == "https://existing.invalid/v1"
    description = window.findChild(QQuickItem, "providerPresetDescription").property("text")
    assert "не вимикає білінг" in description
    assert not warnings, "\n".join(warnings)


def test_language_changes_keep_preset_manual_model_and_quota_drafts(settings_window):
    backend, window, warnings = settings_window
    selector = select_preset(backend, window, "gemini-free")
    model = window.findChild(QQuickItem, "modelField")
    model.setProperty("text", "my-gemini-version")
    rpm = window.findChild(QQuickItem, "requestsPerMinuteField")
    rpm.setProperty("text", "3")
    key = window.findChild(QQuickItem, "apiKeyField")
    key.setProperty("text", "unsaved-gemini-secret")
    for language in ("en", "pl", "de", "uk"):
        backend.setUiLanguage(language)
        QTest.qWait(20)
        assert window.property("providerPresetId") == "gemini-free"
        assert selector.property("currentText") == "gemini-free"
        assert model.property("text") == "my-gemini-version"
        assert rpm.property("text") == "3"
        assert key.property("text") == "unsaved-gemini-secret"
        save = window.findChild(QQuickItem, "saveSettingsButton")
        assert key.mapToScene(QPointF(0, key.height())).y() < save.mapToScene(QPointF(0, 0)).y()
        assert not window.grabWindow().isNull()
    select_preset(backend, window, "custom")
    assert model.property("text") == "my-gemini-version"
    assert rpm.property("text") == "3"
    assert key.property("text") == ""
    assert not warnings, "\n".join(warnings)


def test_anthropic_preset_saves_native_mode_and_reloads_without_exposing_key(
    settings_window, monkeypatch
):
    backend, window, warnings = settings_window
    select_preset(backend, window, "anthropic")
    key = window.findChild(QQuickItem, "apiKeyField")
    key.setProperty("text", "anthropic-test-secret")
    window.findChild(QQuickItem, "rememberKey").setProperty("checked", False)
    window.findChild(QQuickItem, "saveSettingsButton").clicked.emit()
    assert backend._settings.api_mode == "anthropic"
    assert backend._settings.provider_preset == "anthropic"
    assert backend._settings.base_url == "https://api.anthropic.com/v1"
    assert backend._settings.api_key == "anthropic-test-secret"
    assert backend._settings.requests_per_minute == 0
    assert backend._settings.reasoning_effort == ""
    assert window.property("providerPresetId") == "anthropic"
    assert window.findChild(QQuickItem, "apiModeSelector").property("currentText") == "anthropic"
    assert key.property("text") == ""
    assert not warnings, "\n".join(warnings)
