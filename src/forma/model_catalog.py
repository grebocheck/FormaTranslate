"""Read the configured provider's model catalogue without making a completion."""

from __future__ import annotations

import json
import unicodedata

import httpx

from forma.config import Settings
from forma.credentials import canonical_endpoint
from forma.models import FormaError

MAX_RESPONSE_BYTES = 1024 * 1024


def list_models(settings: Settings) -> list[str]:
    settings.validate(require_connection=False)
    endpoint = canonical_endpoint(settings.base_url) + "/models"
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    try:
        with (
            httpx.Client(
                headers=headers,
                timeout=min(settings.timeout, 20),
                follow_redirects=False,
            ) as client,
            client.stream("GET", endpoint) as response,
        ):
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
            if length and length.isdecimal() and int(length) > MAX_RESPONSE_BYTES:
                raise FormaError(
                    "Список моделей перевищує допустимий розмір. Введіть модель вручну."
                )
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise FormaError(
                        "Список моделей перевищує допустимий розмір. Введіть модель вручну."
                    )
                body.extend(chunk)
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
        raise FormaError(
            "Не вдалося з’єднатися з провайдером для списку моделей. Перевірте адресу та мережу або введіть модель вручну."
        ) from None
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError
        identifiers = set()
        for item in payload["data"]:
            identifier = item.get("id") if isinstance(item, dict) else None
            if (
                not isinstance(identifier, str)
                or not identifier.strip()
                or len(identifier) > 256
                or any(unicodedata.category(character).startswith("C") for character in identifier)
            ):
                raise ValueError
            identifiers.add(identifier)
        return sorted(identifiers, key=lambda value: (value.casefold(), value))
    except (ValueError, TypeError, UnicodeError):
        raise FormaError(
            "Провайдер повернув некоректний список моделей. Введіть назву моделі вручну."
        ) from None
