"""Bounded local navigation metadata for one automatically saved workspace."""
from __future__ import annotations

import json
import re
from pathlib import Path

from forma.core.models import FormaError
from forma.persistence.files import atomic_json
from forma.settings.config import data_directory

MAX_DOCUMENTS = 128
MAX_HISTORY = 64
MAX_BYTES = 256 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
DEFAULT_OPTIONS = {"sourceLanguage": "auto", "targetLanguage": "en", "domain": "Загальна тематика",
                   "review": True, "autoGlossary": True}
LANGUAGES = {"en", "uk", "de", "pl", "fr", "es", "it", "pt", "zh", "ja"}
DOMAINS = {"Загальна тематика", "Технічна документація", "Ділові документи", "Наукові матеріали", "Юридичні документи"}


def clean_options(options):
    if (not isinstance(options, dict) or set(options) != set(DEFAULT_OPTIONS)
            or options["sourceLanguage"] not in LANGUAGES | {"auto"} or options["targetLanguage"] not in LANGUAGES
            or options["domain"] not in DOMAINS
            or any(type(options[key]) is not bool for key in ("review", "autoGlossary"))):
        raise ValueError
    return dict(options)


def _identifier(value, *, empty=False):
    return isinstance(value, str) and (bool(value) or empty) and len(value) <= 512 and all(ord(c) >= 32 for c in value)


def _clean(data):
    fields = {"version", "selected", "mode", "history", "navigation"}
    if (not isinstance(data, dict) or set(data) not in (fields, fields | {"options"})
            or type(data["version"]) is not int or data["version"] != 1
            or not _identifier(data["selected"], empty=True) or data["mode"] not in {"translation", "review"}):
        raise ValueError
    history, navigation = data["history"], data["navigation"]
    if (not isinstance(history, list) or len(history) > MAX_HISTORY
            or not all(_identifier(value) for value in history) or len(set(history)) != len(history)
            or not isinstance(navigation, dict) or len(navigation) > MAX_DOCUMENTS):
        raise ValueError
    for key, state in navigation.items():
        if (not _identifier(key) or not isinstance(state, dict)
                or set(state) != {"filter", "search", "entry", "reportKey", "fingerprint"}
                or state["filter"] not in {"all", "issues"}
                or not isinstance(state["search"], str) or len(state["search"]) > 500
                or not _identifier(state["entry"], empty=True)
                or any(not isinstance(state[name], str) or state[name] and not _HASH.fullmatch(state[name])
                       for name in ("reportKey", "fingerprint"))):
            raise ValueError
    return {**data, "options": clean_options(data.get("options", DEFAULT_OPTIONS))}


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else data_directory(create=False) / "workspace.json"

    def load(self):
        try:
            if self.path.is_symlink():
                raise ValueError
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(MAX_BYTES + 1)
            except FileNotFoundError:
                return {"version": 1, "selected": "", "mode": "translation", "history": [], "navigation": {},
                        "options": dict(DEFAULT_OPTIONS)}
            if len(raw) > MAX_BYTES:
                raise ValueError
            return _clean(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise FormaError("Не вдалося відновити робочу сесію. Збережені дані не змінено.") from None

    def save(self, data):
        try:
            data = _clean(data)
            self.load()  # Preserve malformed or unsupported snapshots for recovery.
            if len(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_BYTES:
                raise ValueError
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_json(self.path, data)
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, FormaError):
            raise FormaError("Не вдалося зберегти робочу сесію. Попередні дані не змінено.") from None
