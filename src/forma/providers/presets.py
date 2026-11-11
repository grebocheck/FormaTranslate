"""Editable connection defaults; a preset never enables billing or sends a request."""

from __future__ import annotations

# Model availability/pricing verified 2026-11-10. Quotas belong to the provider's
# project/account; RPM here is a local pacing choice, never a promised free quota.
PRESETS = (
    {
        "id": "custom",
        "label": "Власне підключення / OpenAI",
        "description": "Вкажіть адресу, модель і параметри свого API.",
    },
    {
        "id": "gemini-free",
        "label": "Google Gemini — Free Tier",
        "description": "Для проєкту Free Tier у Google AI Studio: 1 запит одночасно, до 5 за хвилину. Квоти й оплату визначає Google; пресет не вимикає білінг. Безплатні запити можуть використовуватися Google для покращення продуктів.",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "apiMode": "gemini",
        "model": "gemini-3.5-flash-lite",
        "reviewModel": "",
        "responseFormat": "auto",
        "reasoningEffort": "",
        "maxOutputTokens": 16384,
        "maxParallelRequests": 1,
        "parallelDocuments": 1,
        "requestsPerMinute": 5,
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "description": "Пряме підключення до Gemini API. Модель і ліміти можна змінити під свій проєкт Google AI Studio.",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "apiMode": "gemini",
        "model": "gemini-3.8-flash",
        "reviewModel": "",
        "responseFormat": "auto",
        "reasoningEffort": "",
        "maxOutputTokens": 16384,
        "maxParallelRequests": 4,
        "parallelDocuments": 2,
        "requestsPerMinute": 0,
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "description": "Пряме підключення до Claude API. Потрібен API-ключ Anthropic; доступ і оплату визначає ваш обліковий запис API.",
        "baseUrl": "https://api.anthropic.com/v1",
        "apiMode": "anthropic",
        "model": "claude-sonnet-5-5",
        "reviewModel": "",
        "responseFormat": "auto",
        "reasoningEffort": "",
        "maxOutputTokens": 16384,
        "maxParallelRequests": 4,
        "parallelDocuments": 2,
        "requestsPerMinute": 0,
    },
)
PRESET_IDS = frozenset(preset["id"] for preset in PRESETS)


def provider_presets() -> list[dict]:
    from forma.localization.catalog import translate

    return [
        {
            **preset,
            "label": translate(preset["label"]),
            "description": translate(preset["description"]),
        }
        for preset in PRESETS
    ]
