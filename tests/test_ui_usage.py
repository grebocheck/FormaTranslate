import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy, QTest
from support import pump

from forma.accounting.usage import UsageLedger
from forma.localization.catalog import CatalogTranslator
from forma.presentation.backend import Backend
from forma.presentation.usage_controller import BILLING_URL
from forma.settings.config import Settings


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
    pump(qapp, lambda: bool(backend.usage.summary.get("today")) and not backend.usage.working)
    yield backend, window, warnings
    pump(qapp, lambda: not backend._threads)
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


def test_controller_updates_usage_balance_rates_and_safe_billing_link(usage_window, monkeypatch, qapp):
    backend, _, _ = usage_window
    usage = backend.usage
    assert usage.saveBalance("5")
    assert usage.saveTariff("1", "0.5", "3")
    pump(qapp, lambda: not usage.working)
    ledger = UsageLedger()
    ledger.record(backend._settings, {"usage": {"input_tokens": 100, "output_tokens": 50}})
    usage.refresh()
    pump(qapp, lambda: not usage.working)
    assert usage.summary["today"]["cost"] == 250_000
    assert usage.summary["balance"]["text"] == "$4.99975"
    assert not usage.saveBalance("NaN")
    assert usage.summary["error"]
    assert not usage.saveTariff("1", "", "2")
    assert usage.saveBalance("")
    pump(qapp, lambda: not usage.working)
    assert usage.summary["balance"] is None
    opened = []
    monkeypatch.setattr(
        "forma.presentation.usage_controller.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    usage.openBilling()
    assert opened == [BILLING_URL]
    backend._settings = backend._settings.with_overrides(base_url="https://custom.invalid/v1")
    usage.refresh()
    pump(qapp, lambda: not usage.working)
    usage.openBilling()
    assert opened == [BILLING_URL]
    assert usage.summary["today"]["requests"] == 0
    assert usage.summary["tariff"] == []


def test_controller_handles_corrupt_storage_without_exposing_paths(usage_window, qapp):
    backend, _, _ = usage_window
    ledger = UsageLedger()
    ledger.path.write_bytes(b"corrupt secret file")
    backend.usage.refresh()
    pump(qapp, lambda: not backend.usage.working)
    assert backend.usage.summary["unavailable"]
    assert "secret" not in backend.usage.summary["error"]
    assert str(ledger.path) not in backend.usage.summary["error"]
    committed = QSignalSpy(backend.usage.saved)
    assert backend.usage.saveBalance("2")  # Validation accepts; storage commits asynchronously.
    pump(qapp, lambda: not backend.usage.working)
    assert committed.count() == 0
    assert backend.usage.summary["error"]


def test_cache_details_and_incomplete_history_are_visible_without_false_balance(usage_window, qapp):
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
    pump(qapp, lambda: not backend.usage.working)
    assert backend.usage.summary["today"]["costLabel"] == "≈ $0.001947"
    assert backend.usage.summary["models"][0]["writeText"] == "1,499"
    assert backend.usage.summary["balance"]["text"] == "$4.998053"
    ledger.record(backend._settings, {"usage": {"input_tokens": 1502, "output_tokens": 14}})
    backend.usage.refresh()
    pump(qapp, lambda: not backend.usage.working)
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


def test_usage_read_keeps_ui_responsive_and_discards_previous_account(usage_window, qapp, monkeypatch):
    from PySide6.QtCore import QTimer

    backend, _, _ = usage_window
    usage = backend.usage
    ledger = UsageLedger()
    ledger.record(backend._settings, {"usage": {"input_tokens": 1, "output_tokens": 1}})
    original = UsageLedger.summary
    entered, release = threading.Event(), threading.Event()
    old_key = backend._settings.api_key
    calls, visible, ui_events = [], [], []

    def blocked(self, settings):
        calls.append(settings.api_key == old_key)
        if settings.api_key == old_key:
            entered.set()
            assert release.wait(5)
        return original(self, settings)

    monkeypatch.setattr(UsageLedger, "summary", blocked)
    usage.refresh()
    try:
        assert entered.wait(2)
        QTimer.singleShot(0, lambda: ui_events.append(True))
        qapp.processEvents()
        assert ui_events and usage.working
        backend._settings = backend._settings.with_overrides(api_key="another-offline-account")
        usage.changed.connect(lambda: visible.append(usage.summary.get("today", {}).get("requests")))
        for _ in range(10):
            usage.refresh()
        assert not usage.summary.get("today")
        release.set()
        pump(qapp, lambda: not usage.working)
        assert usage.summary["today"]["requests"] == 0
        assert all(value in (None, 0) for value in visible)
        assert calls == [True, False]  # Ten pending refreshes coalesce.
    finally:
        release.set()
        pump(qapp, lambda: not usage.working)


def test_write_failure_survives_queued_refresh_and_preserves_newer_validation(
    usage_window, qapp, monkeypatch,
):
    backend, _, _ = usage_window
    usage = backend.usage
    entered, release = threading.Event(), threading.Event()
    committed = QSignalSpy(usage.saved)

    def blocked_failure(self, settings, value):
        entered.set()
        assert release.wait(5)
        raise OSError("Private storage failure")

    monkeypatch.setattr(UsageLedger, "set_balance", blocked_failure)
    try:
        assert usage.saveBalance("10")
        assert entered.wait(2)
        usage.refresh()  # Same-account timer/panel refresh while the write is pending.
        release.set()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 0
        assert usage.summary["error"] == "Не вдалося зберегти дані витрат."
        assert usage.summary["balance"] is None

        entered.clear()
        release.clear()
        assert usage.saveBalance("11")
        assert entered.wait(2)
        usage.refresh()
        assert not usage.saveBalance("NaN")
        validation_error = usage.summary["error"]
        release.set()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 0
        assert usage.summary["error"] == validation_error
        assert "Private" not in usage.summary["error"]
    finally:
        release.set()
        pump(qapp, lambda: not usage.working)


def test_committed_write_keeps_new_ui_draft_and_read_failure_is_not_a_write_failure(
    usage_window, qapp, monkeypatch,
):
    backend, window, warnings = usage_window
    usage = backend.usage
    window.findChild(QQuickItem, "usageButton").clicked.emit()
    pump(qapp, lambda: not usage.working)
    editor = window.findChild(QQuickItem, "usageEditBalanceButton")
    value = window.findChild(QQuickItem, "usageBalanceInput")
    save = window.findChild(QQuickItem, "usageSaveBalanceButton")
    editor.setProperty("checked", True)
    value.setProperty("text", "10")
    value.textEdited.emit()
    entered, release = threading.Event(), threading.Event()
    original = UsageLedger.set_balance
    committed = QSignalSpy(usage.saved)

    def blocked(self, settings, amount):
        entered.set()
        assert release.wait(5)
        return original(self, settings, amount)

    monkeypatch.setattr(UsageLedger, "set_balance", blocked)
    try:
        save.clicked.emit()
        assert entered.wait(2)
        value.setProperty("text", "11")
        value.textEdited.emit()
        release.set()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 1
        assert editor.property("checked") and value.property("text") == "11"
        assert UsageLedger().summary(backend._settings)["balance"]["remaining"] == 10_000_000_000

        # A read failure after the next commit must still acknowledge the save.
        def failed_read(*_):
            raise OSError("Private summary error")

        monkeypatch.setattr(UsageLedger, "summary", failed_read)
        save.clicked.emit()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 2 and not editor.property("checked")
        assert usage.summary["unavailable"]
        assert "Статистика" in usage.summary["error"]
        assert "Private" not in usage.summary["error"]
        assert not warnings, "\n".join(warnings)
    finally:
        release.set()
        pump(qapp, lambda: not usage.working)


def test_usage_does_not_open_a_placeholder_account_while_credentials_load(usage_window, qapp):
    backend, _, _ = usage_window
    usage = backend.usage
    backend._settings_loading = True
    try:
        usage.refresh()
        assert usage.working
        assert not usage.available and not usage.summary.get("today")
        assert not usage.saveBalance("10")
        assert not usage.saveTariff("1", "2", "3")
        assert not backend._threads
        backend._settings_loading = False
        backend._settings = backend._settings.with_overrides(
            key_source="unavailable_keyring", credential_status="Сховище ключів недоступне",
        )
        usage.refresh()
        assert not usage.working and not usage.available
        assert not usage.summary.get("today") and usage.summary["error"]
        assert not usage.saveBalance("10")
    finally:
        backend._settings_loading = False
        backend._settings = backend._settings.with_overrides(key_source="session")
        usage.refresh()
        pump(qapp, lambda: not usage.working)


def test_pending_commit_and_editor_drafts_follow_account_and_model_context(
    usage_window, qapp, monkeypatch,
):
    backend, window, warnings = usage_window
    usage, original_settings = backend.usage, backend._settings
    window.findChild(QQuickItem, "usageButton").clicked.emit()
    pump(qapp, lambda: not usage.working)
    balance = window.findChild(QQuickItem, "usageBalanceInput")
    balance_editor = window.findChild(QQuickItem, "usageEditBalanceButton")
    tariff_editor = window.findChild(QQuickItem, "usageEditTariffButton")
    rates = [window.findChild(QQuickItem, name) for name in (
        "usageInputRate", "usageCacheRate", "usageOutputRate",
    )]
    save = window.findChild(QQuickItem, "usageSaveBalanceButton")
    balance_editor.setProperty("checked", True)
    balance.setProperty("text", "10")
    tariff_editor.setProperty("checked", True)
    for field, value in zip(rates, ("1", "2", "3"), strict=True):
        field.setProperty("text", value)
    entered, release = threading.Event(), threading.Event()
    original_write = UsageLedger.set_balance
    committed = QSignalSpy(usage.saved)

    def blocked(self, settings, value):
        entered.set()
        assert release.wait(5)
        return original_write(self, settings, value)

    monkeypatch.setattr(UsageLedger, "set_balance", blocked)
    try:
        save.clicked.emit()
        assert entered.wait(2)
        backend._settings = original_settings.with_overrides(api_key="second-offline-account")
        usage.refresh()
        assert not balance_editor.property("checked") and balance.property("text") == ""
        assert not tariff_editor.property("checked")
        assert [field.property("text") for field in rates] == ["", "", ""]
        release.set()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 0 and usage.summary["balance"] is None
        assert UsageLedger().summary(original_settings)["balance"]["remaining"] == 10_000_000_000

        backend._settings = original_settings
        usage.refresh()
        pump(qapp, lambda: not usage.working)
        assert balance_editor.property("checked") and balance.property("text") == "10"
        assert tariff_editor.property("checked")
        assert [field.property("text") for field in rates] == ["1", "2", "3"]

        entered.clear()
        release.clear()
        balance.setProperty("text", "11")
        balance.textEdited.emit()
        save.clicked.emit()
        assert entered.wait(2)
        backend._settings = original_settings.with_overrides(model="another-offline-model")
        usage.refresh()
        assert balance.property("text") == "11"  # Balance belongs to the account, not its model.
        assert not tariff_editor.property("checked")
        assert [field.property("text") for field in rates] == ["", "", ""]
        release.set()
        pump(qapp, lambda: not usage.working)
        assert committed.count() == 1 and not balance_editor.property("checked")
        assert usage.summary["balance"]["text"] == "$11.00"
        assert usage.summary["tariff"] == []
        backend._settings = original_settings
        usage.refresh()
        pump(qapp, lambda: not usage.working)
        assert tariff_editor.property("checked")
        assert [field.property("text") for field in rates] == ["1", "2", "3"]
        assert not warnings, "\n".join(warnings)
    finally:
        release.set()
        pump(qapp, lambda: not usage.working)
