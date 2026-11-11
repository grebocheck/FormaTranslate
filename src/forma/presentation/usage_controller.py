"""Asynchronous presentation boundary for the local, metadata-only usage ledger."""
from __future__ import annotations

import hashlib
import sqlite3
from collections import deque
from datetime import datetime

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from forma.accounting.usage import PRICE_DATE, UsageLedger, amount, money, official_openai, scope
from forma.core.models import FormaError
from forma.localization.catalog import translate as tr
from forma.settings.config import data_directory

BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"


class UsageController(QObject):
    changed = Signal()
    saved = Signal(str)

    def __init__(self, parent, settings, launch, closing, available=lambda: True, loading=lambda: False):
        super().__init__(parent)
        self._settings, self._launch, self._closing = settings, launch, closing
        self._available = available
        self._loading = loading
        self._ledger_path = data_directory(create=False) / "usage.sqlite3"
        self._summary, self._error, self._view = {}, "", None
        self._generation = self._error_revision = 0
        self._active, self._refresh_pending = None, None
        self._writes = deque()

    @Property(bool, notify=changed)
    def working(self):
        return self._loading() or bool(self._active or self._writes or self._refresh_pending)

    @Property(bool, notify=changed)
    def available(self):
        return self._available()

    @Property(str, notify=changed)
    def accountContext(self):
        if not self._available():
            return ""
        try:
            return scope(self._settings())
        except FormaError:
            return ""

    @Property(str, notify=changed)
    def tariffContext(self):
        account = self.accountContext
        if not account:
            return ""
        return hashlib.sha256((account + "\0" + self._settings().model).encode()).hexdigest()

    @Property("QVariantMap", notify=changed)
    def summary(self):
        if not self._available():
            return {"error": tr(self._settings().credential_status)}
        try:
            settings = self._settings()
            if self._view != (scope(settings), settings.model):
                return {"error": tr(self._error)}
        except FormaError:
            return {"unavailable": True, "error": tr(self._error)}
        return {**self._summary, "error": tr(self._error)}

    def _capture(self, kind, value):
        settings = self._settings()
        view = scope(settings), settings.model
        if view != self._view:
            self._view, self._summary, self._error = view, {}, ""
            self._error_revision += 1
            self.changed.emit()
        self._generation += 1
        return self._generation, view, settings, kind, value, self._error_revision

    @Slot()
    def refresh(self):
        if self._closing():
            return
        if not self._available():
            self.changed.emit()
            return
        try:
            job = self._capture("refresh", None)
        except FormaError:
            self._fail("refresh")
            return
        # Timer and panel-open requests coalesce into the latest captured view.
        self._refresh_pending = job
        self._start_next()

    def _start_next(self):
        if self._active:
            return
        if self._writes:
            job = self._writes.popleft()
        elif self._refresh_pending and not self._closing():
            job, self._refresh_pending = self._refresh_pending, None
        else:
            self._refresh_pending = None
            return
        self._active = job
        self.changed.emit()
        generation, view, settings, kind, value, error_revision = job

        def run(emit):
            committed = False
            try:
                self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
                ledger = UsageLedger(self._ledger_path)
                if kind == "balance":
                    ledger.set_balance(settings, value)
                elif kind == "tariff":
                    ledger.set_tariff(settings, value)
                if kind != "refresh":
                    committed = True
                    emit("usage_saved", None)
                emit("usage", self._format(ledger.summary(settings), settings))
            except (OSError, sqlite3.Error, ValueError, FormaError):
                emit("usage_error", committed)

        def receive(event, payload):
            try:
                current = self._settings()
                current_view = scope(current), current.model
            except FormaError:
                return
            same_view = current_view == view
            relevant_write = current_view[0] == view[0] if kind == "balance" else same_view
            if event == "usage_saved":
                if relevant_write:
                    self.saved.emit(kind)
            elif event == "usage":
                if same_view and generation == self._generation:
                    self._summary = payload
                    if error_revision == self._error_revision:
                        self._error = ""
            elif (kind != "refresh" and payload is not True and relevant_write
                  and (error_revision == self._error_revision or not self._error)):
                # A newer refresh does not supersede the outcome of a write.
                # Bump the revision so its queued read cannot erase this error.
                self._error_revision += 1
                self._fail(kind)
            elif same_view and generation == self._generation and error_revision == self._error_revision:
                self._fail("refresh" if payload is True else kind)
            self.changed.emit()

        def finish():
            self._active = None
            self._start_next()
            self.changed.emit()

        # Backend tracks this worker so a pending write finishes before closing.
        self._launch(run, handler=receive, done=finish)

    @staticmethod
    def _format(data, settings):
        for row in (data["today"], data["month"], *data["models"]):
            row["costText"] = money(row["cost"])
            row["costLabel"] = (
                "≥ " if (row["unknown"] or row["uncertain"]) and not row["unbounded"] else "≈ "
            ) + row["costText"]
            row["writeText"] = "—" if row["uncertain"] or row["unknown"] else f"{row['cache_write']:,}"
            row["tokensText"] = f"{row['input']:,} / {row['output']:,}"
        balance = data.get("balance")
        if balance:
            balance["text"] = money(None if data["incomplete"] else balance["remaining"])
            balance["date"] = datetime.fromtimestamp(balance["at"]).strftime("%Y-%m-%d %H:%M")
        data.update(openai=official_openai(settings), model=settings.model, priceDate=PRICE_DATE,
                    configured=bool(settings.model))
        return data

    def _fail(self, kind):
        if kind == "refresh":
            self._summary = {"unavailable": True}
            self._error = "Статистика витрат недоступна. Перевірте доступ до сховища застосунку."
        else:
            self._error = "Не вдалося зберегти дані витрат."
        self.changed.emit()

    def _save(self, kind, value):
        if self._closing() or not self._available():
            return False
        try:
            job = self._capture(kind, value)
        except FormaError:
            self._fail(kind)
            return False
        self._error = ""
        self._writes.append(job)
        if self._refresh_pending and self._refresh_pending[1] == job[1]:
            self._refresh_pending = None  # The write publishes a fresh summary itself.
        self._start_next()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def saveBalance(self, value):
        try:
            if value.strip():
                amount(value)
        except ValueError:
            self._error = "Вкажіть невід’ємну суму в USD, не більшу за 1 000 000."
            self._error_revision += 1
            self.changed.emit()
            return False
        return self._save("balance", value if value.strip() else None)

    @Slot(str, str, str, result=bool)
    def saveTariff(self, incoming, cached, outgoing):
        values = (incoming, cached, outgoing)
        try:
            if any(value.strip() for value in values):
                for value in values:
                    amount(value)
        except ValueError:
            self._error = "Заповніть усі три тарифи невід’ємними числами або очистіть усі поля."
            self._error_revision += 1
            self.changed.emit()
            return False
        return self._save("tariff", values if any(value.strip() for value in values) else None)

    @Slot()
    def openBilling(self):
        # A normal project key cannot retrieve the remaining credit balance.
        if official_openai(self._settings()):
            if not QDesktopServices.openUrl(QUrl(BILLING_URL)):
                self._error = "Не вдалося відкрити браузер."
                self.changed.emit()
