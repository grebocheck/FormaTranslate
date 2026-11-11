"""API keys in native OS credential stores, never in application JSON or .env."""

from __future__ import annotations

import hashlib
import sys
from urllib.parse import urlsplit, urlunsplit

from forma.core.models import FormaError

SERVICE = "Forma"


class CredentialError(FormaError):
    """A credential-store error safe to show without backend exception details."""


def canonical_endpoint(base_url: str) -> str:
    """Match the provider's effective API root, retaining scheme, port and path."""
    try:
        parsed = urlsplit(base_url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port
    except ValueError:
        raise FormaError(
            "Вкажіть коректну адресу API без пароля, параметрів чи фрагментів."
        ) from None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parsed.scheme, port) not in {("https", 443), ("http", 80)}:
        host += f":{port}"
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        path = path.removesuffix(suffix)
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _account(base_url: str) -> str:
    return "api-key:" + hashlib.sha256(canonical_endpoint(base_url).encode()).hexdigest()


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


def load_api_key(base_url: str) -> str:
    account = _account(base_url)
    try:
        key = _native_backend().get_password(SERVICE, account)
        return key if isinstance(key, str) else ""
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося прочитати ключ із системного сховища. Розблокуйте сховище або введіть ключ знову."
        ) from None


def save_api_key(base_url: str, key: str) -> None:
    account = _account(base_url)
    if not isinstance(key, str) or not key.strip():
        raise CredentialError("Введіть API-ключ перед збереженням.")
    try:
        _native_backend().set_password(SERVICE, account, key.strip())
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося зберегти ключ у системному сховищі. Ключ можна використати лише до закриття застосунку."
        ) from None


def delete_api_key(base_url: str) -> None:
    account = _account(base_url)
    try:
        backend = _native_backend()
        if backend.get_password(SERVICE, account) is not None:
            backend.delete_password(SERVICE, account)
    except CredentialError:
        raise
    except Exception:
        raise CredentialError(
            "Не вдалося видалити ключ із системного сховища. Розблокуйте сховище й повторіть дію."
        ) from None
