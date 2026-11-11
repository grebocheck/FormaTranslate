"""One credential-free description of an effective provider connection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from forma.core.models import FormaError


@dataclass(frozen=True)
class ConnectionIdentity:
    family: str
    root: str

    @property
    def key(self) -> str:
        return f"{self.family}:{self.root}"


def _url(base_url: str):
    try:
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError
        port = parsed.port
    except (ValueError, TypeError, AttributeError):
        raise FormaError(
            "Вкажіть коректну адресу API без пароля, параметрів чи фрагментів."
        ) from None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parsed.scheme, port) not in {("https", 443), ("http", 80)}:
        host += f":{port}"
    return parsed, host


def connection_identity(base_url: str, api_mode: str = "chat") -> ConnectionIdentity:
    parsed, host = _url(base_url)
    path = parsed.path.rstrip("/")
    if api_mode == "anthropic":
        path = path.removesuffix("/messages") or "/v1"
        family = "anthropic"
    elif api_mode == "gemini":
        path = re.sub(r"/models/[^/]+:generateContent$", "", path) or "/v1beta"
        family = "gemini"
    elif api_mode in {"chat", "responses"}:
        for suffix in ("/chat/completions", "/responses"):
            path = path.removesuffix(suffix)
        family = "openai"
    else:
        raise FormaError("Режим API має бути chat, responses, gemini або anthropic.")
    return ConnectionIdentity(family, urlunsplit((parsed.scheme.lower(), host, path, "", "")))


def legacy_endpoint(base_url: str) -> str:
    """Pre-mode-aware identity, used only for an explicitly saved old profile."""
    parsed, host = _url(base_url)
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        path = path.removesuffix(suffix)
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def legacy_request_root(base_url: str, api_mode: str = "chat") -> str:
    """Effective request target before protocol-aware path normalization."""
    parsed, host = _url(base_url)
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages"):
        path = path.removesuffix(suffix)
    if api_mode == "gemini":
        path = re.sub(r"/models/[^/]+:generateContent$", "", path)
    if not path:
        path = {"anthropic": "/v1", "gemini": "/v1beta"}.get(api_mode, "")
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def legacy_route_compatible(base_url: str, api_mode: str = "chat") -> bool:
    return legacy_request_root(base_url, api_mode) == connection_identity(base_url, api_mode).root


def legacy_connection_urls(settings) -> tuple[str, ...]:
    """Do not guess the mode of an untyped credential belonging to another profile."""
    legacy = getattr(settings, "legacy_connection", None)
    if not legacy or len(legacy) != 2:
        return ()
    mode, original = legacy
    try:
        current = connection_identity(settings.base_url, settings.api_mode)
        previous = connection_identity(original, mode)
    except FormaError:
        return ()
    if previous != current:
        return ()
    candidates = [original, current.root]
    if current.family == "anthropic":
        candidates.append(current.root + "/messages")
    parsed = urlsplit(current.root)
    default = {"anthropic": "/v1", "gemini": "/v1beta"}.get(current.family)
    if default and parsed.path == default:
        candidates.append(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    return tuple(dict.fromkeys(candidates))
