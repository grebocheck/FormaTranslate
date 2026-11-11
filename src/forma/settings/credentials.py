"""API keys in native OS credential stores, never in application JSON or .env."""

from __future__ import annotations

import hashlib
import sys

from forma.core.models import FormaError
from forma.settings.connection import connection_identity, legacy_endpoint

SERVICE = "Forma"


class CredentialError(FormaError):
    """A credential-store error safe to show without backend exception details."""


def canonical_endpoint(base_url: str, api_mode: str = "chat") -> str:
    return connection_identity(base_url, api_mode).root


def _account(base_url: str, api_mode: str = "chat") -> str:
    return "api-key:v2:" + hashlib.sha256(connection_identity(base_url, api_mode).key.encode()).hexdigest()


def _legacy_accounts(base_url, api_mode, urls):
    current = connection_identity(base_url, api_mode)
    return tuple(dict.fromkeys(
        "api-key:" + hashlib.sha256(legacy_endpoint(url).encode()).hexdigest()
        for url in urls if connection_identity(url, api_mode) == current
    ))


def _native_backend():
    # Do not use keyring.get_keyring(): users may have insecure third-party
    # file-based plugins installed. Only the OS-backed implementations are used.
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            backend = WinVaultKeyring()
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            backend = Keyring()
        elif sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring

            backend = Keyring()
        else:
            raise RuntimeError
        if backend.priority <= 0:
            raise RuntimeError
        return backend
    except Exception:
        raise CredentialError(
            "Системне сховище ключів недоступне. Ключ можна використати лише до закриття застосунку."
        ) from None


def load_api_key(base_url: str, api_mode: str = "chat", *, legacy_urls=(), _backend=None) -> str:
    account = _account(base_url, api_mode)
    try:
        backend = _backend if _backend is not None else _native_backend()
        key = backend.get_password(SERVICE, account)
        if isinstance(key, str):
            return key
        # Untyped old records are considered only for an explicitly saved old
        # profile, never merely because another protocol happens to use the URL.
        keys = {value for old in _legacy_accounts(base_url, api_mode, legacy_urls)
                if isinstance(value := backend.get_password(SERVICE, old), str) and value}
        if len(keys) > 1:
            raise CredentialError("Не вдалося прочитати ключ із системного сховища. Розблокуйте сховище або введіть ключ знову.")
        if keys:
            key = next(iter(keys))
            backend.set_password(SERVICE, account, key)
            return key
        return ""
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося прочитати ключ із системного сховища. Розблокуйте сховище або введіть ключ знову."
        ) from None


def save_api_key(base_url: str, key: str, api_mode: str = "chat", *, _backend=None) -> None:
    account = _account(base_url, api_mode)
    if not isinstance(key, str) or not key.strip():
        raise CredentialError("Введіть API-ключ перед збереженням.")
    try:
        backend = _backend if _backend is not None else _native_backend()
        backend.set_password(SERVICE, account, key.strip())
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося зберегти ключ у системному сховищі. Ключ можна використати лише до закриття застосунку."
        ) from None


def delete_api_key(base_url: str, api_mode: str = "chat", *, legacy_urls=(), _backend=None) -> None:
    account = _account(base_url, api_mode)
    try:
        backend = _backend if _backend is not None else _native_backend()
        current = backend.get_password(SERVICE, account)
        old_values = {old: value for old in _legacy_accounts(base_url, api_mode, legacy_urls)
                      if (value := backend.get_password(SERVICE, old)) is not None}
        if current is None and len(set(old_values.values())) > 1:
            raise CredentialError("Не вдалося прочитати ключ із системного сховища. Розблокуйте сховище або введіть ключ знову.")
        if current is not None:
            backend.delete_password(SERVICE, account)
        for old, value in old_values.items():
            if current is None or value == current:
                backend.delete_password(SERVICE, old)
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося видалити ключ із системного сховища. Розблокуйте сховище й повторіть дію."
        ) from None


class CredentialTransaction:
    """Worker-only compensating transaction for a vault plus atomic preferences.

    Capture before the first mutation. A failed capture never turns into an
    untracked write; a later preferences failure can restore every touched key.
    No backend exceptions or secret-bearing snapshots leave this object.
    """

    def __init__(self, base_url, api_mode, *, legacy_urls=()):
        self.base_url, self.api_mode, self.legacy_urls = base_url, api_mode, legacy_urls
        self._backend = None
        self._before = {}
        self._failure = None

    def _prepare(self):
        if self._failure:
            raise self._failure
        if self._backend is None:
            try:
                backend = _native_backend()
                accounts = (_account(self.base_url, self.api_mode), *_legacy_accounts(self.base_url, self.api_mode, self.legacy_urls))
                before = {account: backend.get_password(SERVICE, account) for account in accounts}
            except Exception:
                self._failure = CredentialError("Системне сховище ключів недоступне. Ключ можна використати лише до закриття застосунку.")
                raise self._failure from None
            self._backend, self._before = backend, before
        return self._backend

    def load(self):
        return load_api_key(self.base_url, self.api_mode, legacy_urls=self.legacy_urls, _backend=self._prepare())

    def save(self, key):
        save_api_key(self.base_url, key, self.api_mode, _backend=self._prepare())

    def delete(self):
        delete_api_key(self.base_url, self.api_mode, legacy_urls=self.legacy_urls, _backend=self._prepare())

    def rollback(self):
        if self._backend is None:
            return
        try:
            for account, previous in self._before.items():
                current = self._backend.get_password(SERVICE, account)
                if current == previous:
                    continue
                if previous is None:
                    if current is not None:
                        self._backend.delete_password(SERVICE, account)
                else:
                    self._backend.set_password(SERVICE, account, previous)
        except Exception:
            raise CredentialError("Не вдалося зберегти ключ у системному сховищі. Ключ можна використати лише до закриття застосунку.") from None
