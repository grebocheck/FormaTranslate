"""Settings validation and worker-only persistence; no Qt objects or signals."""

from __future__ import annotations

from dataclasses import dataclass, field

from forma.core.models import FormaError
from forma.settings.config import Settings, load_preferences, update_preferences
from forma.settings.connection import connection_identity, legacy_connection_urls
from forma.settings.credentials import CredentialError, CredentialTransaction


@dataclass(frozen=True)
class SettingsSaveRequest:
    old: Settings = field(repr=False)
    candidate: Settings = field(repr=False)
    preferences: dict
    supplied_key: str = field(repr=False)
    remember: bool
    remove: bool


@dataclass(frozen=True)
class SettingsSaveResult:
    settings: Settings = field(repr=False)
    notice: str
    kind: str


def settings_save_request(old, values):
    """Validate form values without touching the disk or OS credential store."""
    prefs = {
        key: str(values.get(ui, getattr(old, key))).strip()
        for key, ui in (
            ("base_url", "baseUrl"), ("model", "model"), ("api_mode", "apiMode"),
            ("response_format", "responseFormat"), ("review_model", "reviewModel"),
            ("babeldoc_path", "babeldocPath"), ("scan_converter_path", "scanConverterPath"),
            ("scan_provider", "scanProvider"), ("abbyy_path", "abbyyPath"),
            ("reasoning_effort", "reasoningEffort"), ("provider_preset", "providerPreset"),
        )
    }
    timeout = values.get("timeout", old.timeout)
    if isinstance(timeout, bool):
        raise ValueError
    prefs["timeout"] = float(timeout)
    prefs["max_output_tokens"] = int(str(values.get("maxOutputTokens", old.max_output_tokens)))
    for key, ui in (("max_parallel_requests", "maxParallelRequests"),
                    ("parallel_documents", "parallelDocuments"), ("requests_per_minute", "requestsPerMinute")):
        prefs[key] = int(str(values.get(ui, getattr(old, key))))
    prefs["abbyy_extended_cli_confirmed"] = values.get("abbyyExtendedCliConfirmed", old.abbyy_extended_cli_confirmed)
    candidate = old.with_overrides(**prefs)
    candidate.validate(require_connection=False)
    remove = bool(values.get("removeKey", False))
    if remove and not _same_connection(old, candidate):
        raise FormaError("Спочатку збережіть адресу API, потім видаліть ключ цього підключення.")
    return SettingsSaveRequest(old, candidate, prefs, str(values.get("apiKey", "")).strip(),
                               bool(values.get("rememberKey", True)), remove)


def _same_connection(left, right):
    try:
        return connection_identity(left.base_url, left.api_mode) == connection_identity(right.base_url, right.api_mode)
    except FormaError:
        return False


def persist_settings(request):
    """Commit a validated request in a worker, compensating vault changes on failure."""
    old, candidate = request.old, request.candidate
    previous = load_preferences()
    prefs = dict(request.preferences)
    same = _same_connection(old, candidate)
    vault = CredentialTransaction(candidate.base_url, candidate.api_mode,
                                  legacy_urls=legacy_connection_urls(candidate))
    try:
        key, mode, source, stored = "", "none", "", False
        status, notice, warning = "API-ключ не задано", "Налаштування збережено.", False
        if request.remove:
            try:
                vault.delete()
            except CredentialError:
                if old.key_stored or previous.get("credential_mode") == "keyring":
                    raise
                warning = True
            candidate = candidate.with_overrides(legacy_connection=None)
            notice = ("Ключ прибрано з поточного підключення. Сховище ОС недоступне; його вміст не змінено."
                      if warning else "Ключ видалено з підключення. Інші налаштування збережено.")
        else:
            loaded = False
            lookup_unavailable = False
            key = request.supplied_key or (old.api_key if same else "")
            if not key and (not same or previous.get("credential_mode") == "keyring"):
                try:
                    key = vault.load()
                    loaded = bool(key)
                except CredentialError:
                    vault.rollback()
                    lookup_unavailable = True
            if key and request.remember:
                try:
                    vault.save(key)
                except CredentialError:
                    vault.rollback()
                    mode = source = "session"
                    status = "Ключ діє до закриття програми: сховище ОС недоступне"
                    notice = "Налаштування збережено. Сховище ОС недоступне, тому ключ діє лише до закриття програми."
                    warning = True
                else:
                    mode = source = "keyring"
                    stored = True
                    status = "Ключ збережено у сховищі облікових даних ОС"
            elif key:
                try:
                    vault.delete()
                except CredentialError:
                    if (old.key_stored and same) or loaded:
                        raise
                candidate = candidate.with_overrides(legacy_connection=None)
                mode = source = "session"
                status = "Ключ діє лише до закриття програми"
                notice = "Налаштування збережено. Ключ діє лише до закриття програми."
            elif same and request.remember and previous.get("credential_mode") == "keyring":
                mode, status = "keyring", old.credential_status
                source = "unavailable_keyring" if lookup_unavailable else ""
        prefs["credential_mode"] = mode
        prefs["connection_identity"] = connection_identity(candidate.base_url, candidate.api_mode).key
        legacy = candidate.legacy_connection
        prefs["legacy_connection_mode"] = legacy[0] if legacy else ""
        prefs["legacy_connection_base"] = legacy[1] if legacy else ""
        update_preferences(prefs)
        settings = candidate.with_overrides(api_key=key, key_source=source, key_stored=stored, credential_status=status)
        return SettingsSaveResult(settings, notice, "warning" if warning else "success")
    except BaseException:
        vault.rollback()
        raise
