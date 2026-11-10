from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from forma.backend import Backend
from forma.config import Settings
from forma.i18n import CatalogTranslator
from forma.usage import UsageLedger
from forma.usage_controller import BILLING_URL


@pytest.fixture
def usage_window(qapp, monkeypatch):
    monkeypatch.setattr(
        Backend, "_read_settings", lambda _: Settings(model="gpt-4.1-mini", api_key="fake-key")
    )
    backend = Backend()
    translator = CatalogTranslator(qapp)
    qapp.installTranslator(translator)
    engine = QQmlApplicationEngine()
    backend.languageChanged.connect(engine.retranslate)
    engine.rootContext().setContextProperty("backend", backend)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(v) for v in values))
    engine.load(QUrl.fromLocalFile(str(Path("src/forma/ui/Main.qml").resolve())))
    assert engine.rootObjects()
    window = engine.rootObjects()[0]
    window.setWidth(960)
    window.setHeight(680)
    QTest.qWait(20)
    yield backend, window, warnings
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.removeTranslator(translator)


def test_usage_panel_live_languages_minimum_size_and_drafts(usage_window):
    backend, window, warnings = usage_window
    button = window.findChild(QQuickItem, "usageButton")
    button.clicked.emit()
    panel = window.findChild(QObject, "usagePanel")
    assert panel.property("visible")
    balance = window.findChild(QQuickItem, "usageBalanceInput")
    rate = window.findChild(QQuickItem, "usageInputRate")
    balance.setProperty("text", "10,25")
    rate.setProperty("text", "0.15")
    backend._quick._source_text = "User draft"
    for language in ("uk", "en", "pl", "de"):
        backend.setUiLanguage(language)
        QTest.qWait(30)
        assert button.width() >= button.implicitWidth() - 1
        assert button.mapToScene(QPointF(button.width(), 0)).x() <= window.width()
        assert panel.property("width") <= window.width() - 32
        assert panel.property("height") <= window.height() - 32
        assert balance.property("text") == "10,25"
        assert rate.property("text") == "0.15"
        assert backend._quick._source_text == "User draft"
        assert not window.grabWindow().isNull()
    assert not warnings, "\n".join(warnings)


def test_controller_updates_usage_balance_rates_and_safe_billing_link(usage_window, monkeypatch):
    backend, _, _ = usage_window
    usage = backend.usage
    assert usage.saveBalance("5")
    assert usage.saveTariff("1", "0.5", "3")
    ledger = UsageLedger()
    ledger.record(backend._settings, {"usage": {"input_tokens": 100, "output_tokens": 50}})
    usage.refresh()
    assert usage.summary["today"]["cost"] == 250_000
    assert usage.summary["balance"]["text"] == "$4.99975"
    assert not usage.saveBalance("NaN")
    assert usage.summary["error"]
    assert not usage.saveTariff("1", "", "2")
    assert usage.saveBalance("")
    assert usage.summary["balance"] is None
    opened = []
    monkeypatch.setattr(
        "forma.usage_controller.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    usage.openBilling()
    assert opened == [BILLING_URL]
    backend._settings = backend._settings.with_overrides(base_url="https://custom.invalid/v1")
    usage.refresh()
    usage.openBilling()
    assert opened == [BILLING_URL]
    assert usage.summary["today"]["requests"] == 0
    assert usage.summary["tariff"] == []


def test_controller_handles_corrupt_storage_without_exposing_paths(usage_window):
    backend, _, _ = usage_window
    ledger = UsageLedger()
    ledger.path.write_bytes(b"corrupt secret file")
    backend.usage.refresh()
    assert backend.usage.summary["unavailable"]
    assert "secret" not in backend.usage.summary["error"]
    assert str(ledger.path) not in backend.usage.summary["error"]


def test_cache_details_and_incomplete_history_are_visible_without_false_balance(usage_window):
    backend, window, warnings = usage_window
    backend._settings = backend._settings.with_overrides(model="gpt-6.1-terra")
    ledger = UsageLedger()
    ledger.set_balance(backend._settings, "5")
    ledger.record(
        backend._settings,
        {
            "usage": {
                "input_tokens": 1502,
                "output_tokens": 14,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1499},
            }
        },
    )
    backend.usage.refresh()
    assert backend.usage.summary["today"]["costLabel"] == "≈ $0.001947"
    assert backend.usage.summary["models"][0]["writeText"] == "1,499"
    assert backend.usage.summary["balance"]["text"] == "$4.998053"
    ledger.record(backend._settings, {"usage": {"input_tokens": 1502, "output_tokens": 14}})
    backend.usage.refresh()
    assert backend.usage.summary["today"]["costLabel"].startswith("≥ ")
    assert backend.usage.summary["balance"]["text"] == "—"
    assert backend.usage.summary["models"][0]["writeText"] == "—"
    window.findChild(QQuickItem, "usageButton").clicked.emit()
    window.findChild(QQuickItem, "usageModelsToggle").setProperty("checked", True)
    for language in ("uk", "en", "pl", "de"):
        backend.setUiLanguage(language)
        QTest.qWait(20)
        assert not window.grabWindow().isNull()
    assert not warnings, "\n".join(warnings)
