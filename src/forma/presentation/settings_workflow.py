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
from forma.documents.ocr_policy import choose_scan_provider
from forma.documents.pdf import find_babeldoc
from forma.localization.catalog import available_languages, set_language
from forma.localization.catalog import translate_ui_message as tr
from forma.presentation.settings_io import persist_settings, settings_save_request
from forma.providers.catalog import list_models
from forma.providers.client import Provider
from forma.settings.config import update_preferences
from forma.settings.connection import connection_identity, legacy_connection_urls
from forma.settings.credentials import (
    CredentialError,
    load_api_key,
)


class SettingsWorkflow:
    def setUiLanguage(self, code):
        if code not in {item["code"] for item in available_languages()} or code == self._ui_language:
            return
        try:
            update_preferences({"ui_language": code})
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
            update_preferences({"ui_theme": code})
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
        if self._models_busy or self._settings_loading or self._settings_saving or self._closing:
            return
        try:
            base = str(values.get("baseUrl", self._settings.base_url)).strip()
            key = str(values.get("apiKey", "")).strip()
            api_mode = str(values.get("apiMode", self._settings.api_mode))
            endpoint = connection_identity(base, api_mode)
            try:
                same = endpoint == connection_identity(self._settings.base_url, self._settings.api_mode)
            except FormaError:
                same = False
            if not key:
                if same:
                    key = self._settings.api_key
            settings = self._settings.with_overrides(
                base_url=base,
                api_key=key or "",
                api_mode=api_mode,
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
            configured = settings
            if not configured.api_key and not same:
                try:
                    key = load_api_key(base, api_mode, legacy_urls=legacy_connection_urls(configured))
                except CredentialError:
                    key = ""  # Local compatible providers may not need authentication.
                configured = configured.with_overrides(api_key=key)
            emit("models", list_models(configured))

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
            "scanProviderEffective": choose_scan_provider(
                s.scan_provider, configured_converter=bool(s.scan_converter_path),
                local_ready=self._worker_availability.get("scanConverterReady", False),
                abbyy_ready=self._worker_availability.get("abbyyReady", False),
            ),
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
            update_preferences({"translation_instructions": value})
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
        if self.busy or self._closing:
            return False
        try:
            request = settings_save_request(self._settings, values)
        except (ValueError, TypeError):
            self._notice("Перевірте числові параметри підключення та паралельної обробки.", "error")
            return False
        except FormaError as exc:
            self._notice(str(exc), "error")
            return False
        self._settings_saving = True
        completed = []  # Private worker result; never emit a key-bearing Qt payload.

        def save(emit):
            try:
                completed.append(persist_settings(request))
            except OSError:
                raise FormaError("Не вдалося зберегти налаштування у папці даних застосунку.") from None

        def receive(kind, payload):
            if kind == "error":
                self._notice(payload, "error")

        def finish():
            self._settings_saving = False
            if completed:
                result = completed.pop()
                self._settings = result.settings
                self._api_status = "Налаштування збережено"
                self._notice(result.notice, result.kind)
                if not self._closing:
                    self._refresh_worker_availability()
                    self._usage.refresh()
                self.settingsSaved.emit()
            self.changed.emit()

        self._launch(save, handler=receive, done=finish)
        self.changed.emit()
        return True

    def _load_saved_credentials(self):
        if not self._settings_loading or self._closing:
            return
        snapshot = self._settings
        completed = []

        def load(emit):
            try:
                key = load_api_key(snapshot.base_url, snapshot.api_mode,
                                   legacy_urls=legacy_connection_urls(snapshot))
                status = ("Ключ збережено в системному сховищі" if key
                          else "У системному сховищі немає ключа для цієї адреси API")
                completed.append((key, status, False))
            except CredentialError as exc:
                completed.append(("", str(exc), True))

        def receive(kind, payload):
            if kind == "error":
                completed.append(("", payload, True))
                self._notice(payload, "error")

        def finish():
            self._settings_loading = False
            if completed and self._settings is snapshot:
                key, status, unavailable = completed.pop()
                self._settings = snapshot.with_overrides(
                    api_key=key, key_source="unavailable_keyring" if unavailable else "keyring" if key else "",
                    key_stored=bool(key),
                    credential_status=status,
                )
                if not self._closing:
                    self._usage.refresh()
            self.changed.emit()

        self._launch(load, handler=receive, done=finish)

    def reloadEnv(self):
        if self.busy:
            return
        try:
            self._settings = self._read_settings()
            self._settings_loading = self._settings.key_source == "pending_keyring"
            if self._settings_loading:
                self._load_saved_credentials()
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
