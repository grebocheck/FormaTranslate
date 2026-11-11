"""Settings workflow implementation for the Qt Backend facade.

This class deliberately contains no Qt descriptors or QObject inheritance.
Backend owns the Qt metaobject and supplies the shared desktop session state.
"""

from __future__ import annotations

import os
import threading

from forma.core.models import (
    FormaError,
    validate_instructions,
)
from forma.documents.abbyy import find_abbyy
from forma.documents.ocr import find_scan_converter
from forma.documents.pdf import find_babeldoc
from forma.localization.catalog import available_languages, set_language
from forma.localization.catalog import translate_ui_message as tr
from forma.providers.catalog import list_models
from forma.providers.client import Provider
from forma.settings.config import load_preferences, save_preferences
from forma.settings.credentials import (
    CredentialError,
    canonical_endpoint,
    delete_api_key,
    load_api_key,
    save_api_key,
)


class SettingsWorkflow:
    def setUiLanguage(self, code):
        if code not in {item["code"] for item in available_languages()} or code == self._ui_language:
            return
        try:
            save_preferences({**load_preferences(), "ui_language": code})
        except OSError:
            self._notice("Не вдалося зберегти налаштування.", "error")
            return
        self._ui_language = code
        set_language(code)
        self.languageChanged.emit()
        # A localized search can stop matching after a switch. Keep the draft
        # attached to its selected fragment until editing/export has finished.
        if self._review_editing or self._review_saving:
            self.changed.emit()
        else:
            self._filter_review()
        self.reviewEditChanged.emit()

    def setUiTheme(self, code):
        if code not in {"system", "light", "dark"} or code == self._ui_theme:
            return
        try:
            save_preferences({**load_preferences(), "ui_theme": code})
        except OSError:
            self._notice("Не вдалося зберегти налаштування.", "error")
            return
        self._ui_theme = code
        self.uiThemeChanged.emit()

    def clearModels(self):
        self._models_generation += 1
        self._model_choices = []
        self._models_message = ""
        self.changed.emit()

    def refreshModels(self, values):
        if self._models_busy or self._closing:
            return
        try:
            base = str(values.get("baseUrl", self._settings.base_url)).strip()
            key = str(values.get("apiKey", "")).strip()
            endpoint = canonical_endpoint(base)
            try:
                same = endpoint == canonical_endpoint(self._settings.base_url)
            except FormaError:
                same = False
            if not key:
                if same:
                    key = self._settings.api_key
                else:
                    try:
                        key = load_api_key(base)
                    except CredentialError:
                        key = ""  # Local compatible providers may not need authentication.
            settings = self._settings.with_overrides(
                base_url=base,
                api_key=key or "",
                api_mode=str(values.get("apiMode", self._settings.api_mode)),
            )
            settings.validate(require_connection=False)
        except (FormaError, CredentialError) as exc:
            self._models_message = str(exc)
            self.changed.emit()
            return
        self.clearModels()
        generation = self._models_generation
        self._models_busy = True
        self._models_message = "Отримуємо список моделей…"

        def fetch(emit):
            emit("models", list_models(settings))

        def receive(kind, payload):
            if generation != self._models_generation:
                return
            if kind == "models":
                self._model_choices = payload
                self._models_message = (
                    f"Моделей: {len(payload)}. Оберіть текстову модель; назву також можна ввести вручну."
                    if payload
                    else "Список порожній. Введіть назву моделі вручну."
                )
            else:
                self._models_message = payload
            self.changed.emit()

        def finish():
            self._models_busy = False
            self.changed.emit()

        self._launch(fetch, handler=receive, done=finish)
        self.changed.emit()

    def settings(self):
        s = self._settings
        return {
            "baseUrl": s.base_url,
            "model": s.model,
            "apiMode": s.api_mode,
            "responseFormat": s.response_format,
            "reviewModel": s.review_model,
            "reasoningEffort": s.reasoning_effort,
            "maxOutputTokens": s.max_output_tokens,
            "timeout": s.timeout,
            "maxParallelRequests": s.max_parallel_requests,
            "parallelDocuments": s.parallel_documents,
            "providerPreset": s.provider_preset,
            "requestsPerMinute": s.requests_per_minute,
            "babeldocPath": s.babeldoc_path,
            "scanConverterPath": s.scan_converter_path,
            "scanProvider": s.scan_provider,
            "abbyyPath": s.abbyy_path,
            "abbyyExtendedCliConfirmed": s.abbyy_extended_cli_confirmed,
            "scanProviderEffective": ("abbyy" if self._worker_availability.get("abbyyReady") else "forma") if s.scan_provider == "auto" else s.scan_provider,
            "envPath": str(s.env_path),
            "keyPresent": bool(s.api_key),
            "keyStored": s.key_stored,
            "credentialStatus": tr(s.credential_status),
            **self._worker_availability,
        }

    def _refresh_worker_availability(self, *, force=False):
        """Filesystem/PATH discovery never runs in a QML property getter.

        Settings bindings are evaluated dozens of times for one document click.
        A slow or unavailable Windows PATH directory must not stall that click.
        These flags are hints; actual translation/OCR validates its executable.
        """
        if self._closing:
            return
        paths = self._settings.babeldoc_path, self._settings.scan_converter_path, self._settings.abbyy_path
        key = (*paths, os.environ.get("PATH", ""))
        if key == self._worker_probe_key and (not force or self._worker_probe_running):
            return
        changed_paths = key != self._worker_probe_key
        self._worker_probe_key = key
        self._worker_probe_generation += 1
        generation = self._worker_probe_generation
        self._worker_probe_running = True
        if changed_paths:
            self._worker_availability = {"pdfReady": False, "scanConverterReady": False, "abbyyReady": False, "abbyyDetectedPath": ""}
        checks = (
            ("pdfReady", find_babeldoc, paths[0]),
            ("scanConverterReady", find_scan_converter, paths[1]),
            ("abbyyReady", find_abbyy, paths[2]),
        )

        def probe():
            available = {"abbyyDetectedPath": ""}
            for name, finder, path in checks:
                try:
                    found = finder(path)
                    available[name] = bool(found)
                    if name == "abbyyReady" and isinstance(found, str):
                        available["abbyyDetectedPath"] = found
                except Exception:
                    available[name] = False
            try:
                if not self._closing:
                    self.workerAvailabilityReady.emit(generation, available)
            except RuntimeError:
                pass  # The application may already have destroyed this QObject.

        # A readiness hint must not hold up closing or join the translation job
        # list. This thread performs local filesystem lookups only.
        threading.Thread(target=probe, name="forma-worker-discovery", daemon=True).start()

    def _receive_worker_availability(self, generation, available):
        if generation != self._worker_probe_generation or self._closing:
            return
        self._worker_probe_running = False
        self._worker_availability = available
        self.changed.emit()

    def refreshWorkerAvailability(self):
        self._refresh_worker_availability(force=True)

    def saveTranslationInstructions(self, value):
        if self.busy:
            return False
        try:
            validate_instructions(value)
            save_preferences({**load_preferences(), "translation_instructions": value})
        except FormaError as exc:
            self._instructions_error = str(exc)
        except OSError:
            self._instructions_error = "Не вдалося зберегти власні інструкції."
        else:
            self._settings = self._settings.with_overrides(translation_instructions=value)
            self._instructions_error = ""
            self.changed.emit()
            return True
        self.changed.emit()
        return False

    def saveSettings(self, values):
        if self.busy:
            return False
        old = self._settings
        try:
            prefs = {
                key: str(values.get(ui, getattr(old, key))).strip()
                for key, ui in (
                    ("base_url", "baseUrl"),
                    ("model", "model"),
                    ("api_mode", "apiMode"),
                    ("response_format", "responseFormat"),
                    ("review_model", "reviewModel"),
                    ("babeldoc_path", "babeldocPath"),
                    ("scan_converter_path", "scanConverterPath"),
                    ("scan_provider", "scanProvider"),
                    ("abbyy_path", "abbyyPath"),
                    ("reasoning_effort", "reasoningEffort"),
                    ("provider_preset", "providerPreset"),
                )
            }
            raw_timeout = values.get("timeout", old.timeout)
            if isinstance(raw_timeout, bool):
                raise ValueError
            prefs["timeout"] = float(raw_timeout)
            prefs["max_output_tokens"] = int(
                str(values.get("maxOutputTokens", old.max_output_tokens))
            )
            for key, ui in (
                ("max_parallel_requests", "maxParallelRequests"),
                ("parallel_documents", "parallelDocuments"),
                ("requests_per_minute", "requestsPerMinute"),
            ):
                prefs[key] = int(str(values.get(ui, getattr(old, key))))
            prefs["abbyy_extended_cli_confirmed"] = values.get("abbyyExtendedCliConfirmed", old.abbyy_extended_cli_confirmed)
            candidate = old.with_overrides(**prefs)
            candidate.validate(require_connection=False)
            try:
                same_endpoint = canonical_endpoint(old.base_url) == canonical_endpoint(
                    candidate.base_url
                )
            except FormaError:
                same_endpoint = False
            supplied = str(values.get("apiKey", "")).strip()
            remember = bool(values.get("rememberKey", True))
            remove = bool(values.get("removeKey", False))
            notice = "Налаштування збережено."
            warning = False
            key, mode, key_source, stored = "", "none", "", False
            status = "API-ключ не задано"
            if remove:
                if not same_endpoint:
                    raise FormaError(
                        "Спочатку збережіть адресу API, потім видаліть ключ цього підключення."
                    )
                try:
                    delete_api_key(old.base_url)
                except CredentialError:
                    if old.key_stored or load_preferences().get("credential_mode") == "keyring":
                        raise
                    warning = True
                notice = "Ключ видалено з підключення. Інші налаштування збережено."
                if warning:
                    notice = "Ключ прибрано з поточного підключення. Сховище ОС недоступне; його вміст не змінено."
            else:
                loaded_from_vault = False
                key = supplied or (old.api_key if same_endpoint else "")
                if not key and not same_endpoint:
                    try:
                        key = load_api_key(candidate.base_url)
                        loaded_from_vault = bool(key)
                    except CredentialError:
                        pass
                if key and remember:
                    try:
                        save_api_key(candidate.base_url, key)
                    except CredentialError:
                        mode, key_source = "session", "session"
                        status = "Ключ діє до закриття програми: сховище ОС недоступне"
                        notice = "Налаштування збережено. Сховище ОС недоступне, тому ключ діє лише до закриття програми."
                        warning = True
                    else:
                        mode, key_source, stored = "keyring", "keyring", True
                        status = "Ключ збережено у сховищі облікових даних ОС"
                elif key:
                    try:
                        delete_api_key(candidate.base_url)
                    except CredentialError:
                        if (old.key_stored and same_endpoint) or loaded_from_vault:
                            raise
                    mode, key_source = "session", "session"
                    status = "Ключ діє лише до закриття програми"
                    notice = "Налаштування збережено. Ключ діє лише до закриття програми."
                elif (
                    same_endpoint
                    and remember
                    and load_preferences().get("credential_mode") == "keyring"
                ):
                    # A locked/unavailable vault is not a request to forget its entry.
                    mode, status = "keyring", old.credential_status
            prefs["credential_mode"] = mode
            save_preferences({**load_preferences(), **prefs})
            self._settings = candidate.with_overrides(
                api_key=key, key_source=key_source, key_stored=stored, credential_status=status
            )
            self._refresh_worker_availability()
            self._usage.refresh()
            self._api_status = "Налаштування збережено"
            self._notice(notice, "warning" if warning else "success")
            return True
        except (ValueError, TypeError):
            self._notice("Перевірте числові параметри підключення та паралельної обробки.", "error")
        except FormaError as exc:
            self._notice(str(exc), "error")
        except OSError:
            self._notice("Не вдалося зберегти налаштування у папці даних застосунку.", "error")
        return False

    def reloadEnv(self):
        if self.busy:
            return
        try:
            self._settings = self._read_settings()
            self._refresh_worker_availability(force=True)
            self._usage.refresh()
            self._api_status = "Конфігурацію оновлено"
            self._notice("Налаштування перечитано. Збережені параметри застосунку мають пріоритет.")
        except FormaError as exc:
            self._notice(str(exc), "error")

    def testConnection(self):
        if self.busy:
            return
        try:
            self._settings.validate()
        except FormaError as exc:
            self._notice(str(exc), "error")
            return
        self._busy = True
        self._api_status = "Перевіряємо API…"
        settings = self._settings

        def ping(emit):
            provider = Provider(settings)
            try:
                emit("api_ok", provider.ping())
            finally:
                provider.close()

        self._launch(ping, done=self._finished)
        self.changed.emit()
