"""Read available models without making a completion or following remote links."""

from __future__ import annotations

import json
import unicodedata
from urllib.parse import urlencode

import httpx

from forma.config import Settings
from forma.models import FormaError

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PAGES = 20


def _identifier(value) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or any(unicodedata.category(c).startswith("C") for c in value)
    ):
        raise ValueError
    return value


def _page(client, endpoint, remaining):
    with client.stream("GET", endpoint) as response:
        status = response.status_code
        if status in {404, 405}:
            raise FormaError(
                f"Провайдер не підтримує список моделей (HTTP {status}). Введіть назву моделі вручну."
            )
        if status in {401, 403}:
            raise FormaError(
                f"Немає доступу до списку моделей (HTTP {status}). Перевірте API-ключ і його дозволи."
            )
        if not 200 <= status < 300:
            raise FormaError(
                f"Не вдалося отримати список моделей (HTTP {status}). Введіть модель вручну або повторіть пізніше."
            )
        length = response.headers.get("content-length")
        if length and length.isdecimal() and int(length) > remaining:
            raise FormaError("Список моделей перевищує допустимий розмір. Введіть модель вручну.")
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            if len(body) + len(chunk) > remaining:
                raise FormaError(
                    "Список моделей перевищує допустимий розмір. Введіть модель вручну."
                )
            body.extend(chunk)
    return json.loads(body), len(body)


def list_models(settings: Settings) -> list[str]:
    from forma.api_protocol import api_base, request_headers

    settings.validate(require_connection=False)
    endpoint = api_base(settings) + "/models"
    identifiers, cursors = set(), set()
    remaining, cursor = MAX_RESPONSE_BYTES, ""
    try:
        with httpx.Client(
            headers=request_headers(settings),
            timeout=min(settings.timeout, 20),
            follow_redirects=False,
        ) as client:
            for _ in range(MAX_PAGES):
                params = {}
                if settings.api_mode == "gemini":
                    params = {"pageSize": 1000, **({"pageToken": cursor} if cursor else {})}
                elif settings.api_mode == "anthropic":
                    params = {"limit": 1000, **({"after_id": cursor} if cursor else {})}
                url = endpoint + ("?" + urlencode(params) if params else "")
                payload, size = _page(client, url, remaining)
                remaining -= size
                field = "models" if settings.api_mode == "gemini" else "data"
                if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
                    raise ValueError
                for item in payload[field]:
                    if not isinstance(item, dict):
                        raise ValueError
                    if settings.api_mode == "gemini":
                        identifier = _identifier(item.get("name"))
                        methods = item.get("supportedGenerationMethods", [])
                        if not isinstance(methods, list):
                            raise ValueError
                        if "generateContent" not in methods:
                            continue
                        if not identifier.startswith("models/"):
                            raise ValueError
                        identifier = _identifier(identifier.removeprefix("models/"))
                    else:
                        identifier = _identifier(item.get("id"))
                        if settings.api_mode == "anthropic":
                            if item.get("lifecycle") == "retired":
                                continue
                            capabilities = item.get("capabilities") or {}
                            if not isinstance(capabilities, dict):
                                raise ValueError
                    identifiers.add(identifier)
                cursor = ""
                if settings.api_mode == "gemini":
                    cursor = payload.get("nextPageToken") or ""
                elif settings.api_mode == "anthropic":
                    more = payload.get("has_more", False)
                    if not isinstance(more, bool):
                        raise ValueError
                    if more:
                        cursor = _identifier(payload.get("last_id"))
                if not cursor:
                    return sorted(identifiers, key=lambda value: (value.casefold(), value))
                # Treat the cursor as opaque query data, never as a URL.
                if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in cursors:
                    raise ValueError
                cursors.add(cursor)
            raise FormaError("Список моделей перевищує допустимий розмір. Введіть модель вручну.")
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
        raise FormaError(
            "Не вдалося з’єднатися з провайдером для списку моделей. Перевірте адресу та мережу або введіть модель вручну."
        ) from None
    except (ValueError, TypeError):
        raise FormaError(
            "Провайдер повернув некоректний список моделей. Введіть назву моделі вручну."
        ) from None
