from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from platformdirs import user_data_path

from forma.models import FormaError, validate_instructions
from forma.storage import atomic_json

STRING_PREFERENCES = {
    "base_url",
    "model",
    "api_mode",
    "response_format",
    "review_model",
    "babeldoc_path",
    "scan_converter_path",
    "reasoning_effort",
    "credential_mode",
    "output_folder",
    "ui_language",
    "provider_preset",
    "translation_instructions",
}
NUMBER_PREFERENCES = {
    "max_output_tokens",
    "timeout",
    "max_parallel_requests",
    "parallel_documents",
    "requests_per_minute",
}
ENV_FIELDS = {
    "base_url": "OPENAI_BASE_URL",
    "model": "OPENAI_MODEL",
    "api_mode": "FORMA_API_MODE",
    "response_format": "FORMA_RESPONSE_FORMAT",
    "review_model": "FORMA_REVIEW_MODEL",
    "reasoning_effort": "FORMA_REASONING_EFFORT",
    "max_output_tokens": "FORMA_MAX_OUTPUT_TOKENS",
    "timeout": "FORMA_REQUEST_TIMEOUT",
    "babeldoc_path": "FORMA_BABELDOC_PATH",
    "scan_converter_path": "FORMA_SCAN_CONVERTER_PATH",
    "max_parallel_requests": "FORMA_MAX_PARALLEL_REQUESTS",
    "parallel_documents": "FORMA_PARALLEL_DOCUMENTS",
    "provider_preset": "FORMA_PROVIDER_PRESET",
    "requests_per_minute": "FORMA_REQUESTS_PER_MINUTE",
}


def data_directory() -> Path:
    path = Path(os.environ.get("FORMA_DATA_DIR") or user_data_path("Forma", "Forma"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_env_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / ".env"
    return Path.cwd() / ".env"


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    api_mode: str = "chat"
    response_format: str = "auto"
    review_model: str = ""
    translation_instructions: str = ""
    reasoning_effort: str = ""
    max_output_tokens: int = 0
    timeout: float = 180
    max_parallel_requests: int = 8
    parallel_documents: int = 2
    provider_preset: str = "custom"
    requests_per_minute: int = 0
    babeldoc_path: str = ""
    scan_converter_path: str = ""
    env_path: Path = field(default_factory=default_env_path)
    key_source: str = ""
    key_stored: bool = False
    credential_status: str = ""

    @classmethod
    def load(cls, env_path: Path | None = None) -> Settings:
        from forma.credentials import CredentialError, canonical_endpoint, load_api_key

        path = env_path or default_env_path()
        values = {**dotenv_values(path), **os.environ}
        # An explicit CLI --env is a reproducible override; the default GUI/CLI
        # path instead respects parameters saved by the user in the application.
        preferences = load_preferences() if env_path is None else {}
        defaults = cls()
        environment_mode = values.get("FORMA_API_MODE") or defaults.api_mode
        environment_base = values.get("OPENAI_BASE_URL") or {
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
            "anthropic": "https://api.anthropic.com/v1",
        }.get(environment_mode, defaults.base_url)
        fields = {
            name: preferences.get(name, values.get(env_name) or getattr(defaults, name))
            for name, env_name in ENV_FIELDS.items()
        }
        fields["base_url"] = preferences.get("base_url", environment_base)
        fields["translation_instructions"] = preferences.get("translation_instructions", "")
        try:
            timeout = float(fields["timeout"])
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise FormaError("Час очікування API має бути додатним скінченним числом.") from None
        try:
            raw_tokens = fields["max_output_tokens"]
            max_tokens = int(raw_tokens)
            if (
                isinstance(raw_tokens, bool)
                or str(max_tokens) != str(raw_tokens).strip()
                or max_tokens < 0
            ):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise FormaError("Ліміт вихідних токенів має бути цілим невід’ємним числом.") from None
        fields.update(timeout=timeout, max_output_tokens=max_tokens)
        try:
            raw_rpm = fields["requests_per_minute"]
            rpm = int(raw_rpm)
            if (
                isinstance(raw_rpm, bool)
                or str(rpm) != str(raw_rpm).strip()
                or not 0 <= rpm <= 6000
            ):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise FormaError("Запити за хвилину: вкажіть ціле число від 0 до 6000.") from None
        fields["requests_per_minute"] = rpm
        for name, limit, label in (
            ("max_parallel_requests", 16, "Паралельні запити"),
            ("parallel_documents", 4, "Паралельні документи"),
        ):
            raw = fields[name]
            try:
                value = int(raw)
                if (
                    isinstance(raw, bool)
                    or str(value) != str(raw).strip()
                    or not 1 <= value <= limit
                ):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise FormaError(f"{label}: вкажіть ціле число від 1 до {limit}.") from None
            fields[name] = value
        key, source, stored, status = "", "", False, "Ключ ще не задано"
        mode = preferences.get("credential_mode")
        if mode == "keyring":
            try:
                key = load_api_key(fields["base_url"])
                stored = bool(key)
                source = "keyring" if key else ""
                status = (
                    "Ключ збережено в системному сховищі"
                    if key
                    else "У системному сховищі немає ключа для цієї адреси API"
                )
            except CredentialError as exc:
                status = str(exc)
        elif mode == "session":
            status = "Ключ попередньої сесії не зберігався; введіть його знову"
        elif mode != "none":
            try:
                same_endpoint = canonical_endpoint(fields["base_url"]) == canonical_endpoint(
                    environment_base
                )
            except FormaError:
                same_endpoint = False
            if same_endpoint:
                native_key = {
                    "gemini": values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY"),
                    "anthropic": values.get("ANTHROPIC_API_KEY"),
                }.get(environment_mode)
                key = native_key or values.get("OPENAI_API_KEY") or ""
                source = "environment" if key else ""
                status = "Ключ завантажено зі змінних середовища або .env" if key else status
        return cls(
            **fields,
            api_key=key,
            env_path=path,
            key_source=source,
            key_stored=stored,
            credential_status=status,
        )

    def validate(self, *, require_connection: bool = True) -> None:
        from forma.credentials import canonical_endpoint
        from forma.provider_presets import PRESET_IDS

        validate_instructions(self.translation_instructions)
        if self.provider_preset not in PRESET_IDS:
            raise FormaError("Невідомий пресет провайдера. Оберіть його знову в налаштуваннях.")
        if (
            isinstance(self.requests_per_minute, bool)
            or not isinstance(self.requests_per_minute, int)
            or not 0 <= self.requests_per_minute <= 6000
        ):
            raise FormaError("Запити за хвилину: вкажіть ціле число від 0 до 6000.")

        for value, limit, label in (
            (self.max_parallel_requests, 16, "Паралельні запити"),
            (self.parallel_documents, 4, "Паралельні документи"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= limit:
                raise FormaError(f"{label}: вкажіть ціле число від 1 до {limit}.")

        canonical_endpoint(self.base_url)
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
            raise FormaError("Вкажіть коректну адресу API, наприклад https://api.openai.com/v1.")
        if parsed.query or parsed.fragment:
            raise FormaError("Адреса API не має містити query-параметрів або фрагментів.")
        if require_connection and not self.model.strip():
            raise FormaError("Вкажіть модель у налаштуваннях застосунку.")
        if (
            require_connection
            and parsed.hostname
            in {
                "api.openai.com",
                "api.anthropic.com",
                "generativelanguage.googleapis.com",
            }
            and not self.api_key
        ):
            raise FormaError("Введіть API-ключ у налаштуваннях підключення.")
        if self.api_mode not in ("chat", "responses", "gemini", "anthropic"):
            raise FormaError("Режим API має бути chat, responses, gemini або anthropic.")
        if self.response_format not in ("auto", "strict", "json", "plain"):
            raise FormaError("Формат відповіді має бути auto, strict, json або plain.")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise FormaError("Час очікування API має бути додатним скінченним числом.")
        if self.reasoning_effort not in (
            "",
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise FormaError("Некоректний рівень міркування моделі.")
        if (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or self.max_output_tokens < 0
        ):
            raise FormaError("Ліміт вихідних токенів має бути цілим невід’ємним числом.")

    def with_overrides(self, **kwargs) -> Settings:
        return replace(self, **kwargs)


def save_preferences(values: dict) -> None:
    safe = _safe_preferences(values)
    path = data_directory() / "preferences.json"
    atomic_json(path, safe)


def _safe_preferences(values: dict) -> dict:
    safe = {}
    for key, value in values.items():
        if key in STRING_PREFERENCES and isinstance(value, str):
            if key != "credential_mode" or value in {"keyring", "session", "none"}:
                safe[key] = value
        elif (
            key in NUMBER_PREFERENCES
            and isinstance(value, (int, float, str))
            and not isinstance(value, bool)
        ):
            if not isinstance(value, float) or math.isfinite(value):
                safe[key] = value
    return safe


def load_preferences() -> dict:
    path = data_directory() / "preferences.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        return _safe_preferences(values) if isinstance(values, dict) else {}
    except (OSError, ValueError):
        return {}
