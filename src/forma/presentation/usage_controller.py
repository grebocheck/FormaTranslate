"""Presentation boundary for the local usage ledger; no billing credentials."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from forma.accounting.usage import PRICE_DATE, UsageLedger, money, official_openai, scope
from forma.core.models import FormaError
from forma.localization.catalog import translate as tr

BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"


class UsageController(QObject):
    changed = Signal()

    def __init__(self, parent, settings):
        super().__init__(parent)
        self._settings = settings
        self._summary = {}
        self._error = ""
        self._scope = ""
        self.refresh()

    @Property("QVariantMap", notify=changed)
    def summary(self):
        result = dict(self._summary)
        result["error"] = tr(self._error)
        return result

    @Slot()
    def refresh(self):
        settings = self._settings()
        try:
            data = UsageLedger().summary(settings)
            account = scope(settings)
            if account != self._scope or self._summary.get("unavailable"):
                self._error = ""
                self._scope = account
            for row in (data["today"], data["month"], *data["models"]):
                row["costText"] = money(row["cost"])
                row["costLabel"] = (
                    "≥ " if (row["unknown"] or row["uncertain"]) and not row["unbounded"] else "≈ "
                ) + row["costText"]
                row["writeText"] = (
                    "—" if row["uncertain"] or row["unknown"] else f"{row['cache_write']:,}"
                )
                row["tokensText"] = f"{row['input']:,} / {row['output']:,}"
            balance = data.get("balance")
            if balance:
                balance["text"] = money(None if data["incomplete"] else balance["remaining"])
                balance["date"] = datetime.fromtimestamp(balance["at"]).strftime("%Y-%m-%d %H:%M")
            data["openai"] = official_openai(settings)
            data["model"] = settings.model
            data["priceDate"] = PRICE_DATE
            data["configured"] = bool(settings.model)
            if data != self._summary:
                self._summary = data
                self.changed.emit()
        except (OSError, sqlite3.Error, ValueError, FormaError):
            self._summary = {"unavailable": True}
            self._error = "Статистика витрат недоступна. Перевірте доступ до сховища застосунку."
            self.changed.emit()

    @Slot(str, result=bool)
    def saveBalance(self, value):
        try:
            UsageLedger().set_balance(self._settings(), value if value.strip() else None)
        except ValueError:
            self._error = "Вкажіть невід’ємну суму в USD, не більшу за 1 000 000."
        except (OSError, sqlite3.Error, FormaError):
            self._error = "Не вдалося зберегти дані витрат."
        else:
            self._error = ""
            self.refresh()
            self.changed.emit()
            return True
        self.changed.emit()
        return False

    @Slot(str, str, str, result=bool)
    def saveTariff(self, incoming, cached, outgoing):
        values = (incoming, cached, outgoing)
        try:
            UsageLedger().set_tariff(
                self._settings(), values if any(v.strip() for v in values) else None
            )
        except ValueError:
            self._error = "Заповніть усі три тарифи невід’ємними числами або очистіть усі поля."
        except (OSError, sqlite3.Error, FormaError):
            self._error = "Не вдалося зберегти дані витрат."
        else:
            self._error = ""
            self.refresh()
            self.changed.emit()
            return True
        self.changed.emit()
        return False

    @Slot()
    def openBilling(self):
        # A normal project key cannot retrieve the remaining credit balance.
        if official_openai(self._settings()):
            if not QDesktopServices.openUrl(QUrl(BILLING_URL)):
                self._error = "Не вдалося відкрити браузер."
                self.changed.emit()
