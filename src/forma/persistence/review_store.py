"""Private, local review decisions and explicit translation drafts.

Each report has its own snapshot. Drafts intentionally contain user-edited document
text; credentials and application settings are not part of this storage format.
The report and translated document are never modified here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path

from forma.core.models import FormaError
from forma.settings.config import data_directory

MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 100000
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_READ_ERROR = (
    "Не вдалося прочитати збережену перевірку. Перевірте доступ до звіту та сховища перевірок."
)
_SAVE_ERROR = (
    "Не вдалося зберегти перевірку. Перевірте доступ до звіту та сховища перевірок."
)
_CHANGED_ERROR = "Звіт змінився. Відкрийте перевірку знову перед збереженням змін."


def _valid_fingerprint(value) -> bool:
    return isinstance(value, str) and _FINGERPRINT.fullmatch(value) is not None


def _valid_id(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 1024
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _maps(accepted, edits) -> tuple[dict[str, str], dict[str, str]]:
    if (
        not isinstance(accepted, dict)
        or not isinstance(edits, dict)
        or len(accepted) > MAX_ENTRIES
        or len(edits) > MAX_ENTRIES
    ):
        raise ValueError
    if any(not _valid_id(key) or not _valid_fingerprint(value) for key, value in accepted.items()):
        raise ValueError
    if any(not _valid_id(key) or not isinstance(value, str) for key, value in edits.items()):
        raise ValueError
    return dict(accepted), dict(edits)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _regular(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError


def _report_fingerprint(path: Path) -> str:
    _regular(path)
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError
        while chunk := stream.read(min(65536, MAX_REPORT_BYTES - total + 1)):
            total += len(chunk)
            if total > MAX_REPORT_BYTES:
                raise ValueError
            digest.update(chunk)
    return digest.hexdigest()


class ReviewStore:
    """A directory of atomic JSON snapshots keyed by canonical report path.

    ``path`` is a storage directory, not a JSON filename. Pass the fingerprint
    returned by ``load`` to ``save(expected_fingerprint=...)`` so an edit cannot
    silently attach to a report that changed after the user opened it.
    """

    def __init__(self, path: Path | None = None):
        try:
            self.path = Path(path) if path is not None else data_directory() / "reviews"
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                self.path.chmod(0o700)
        except (OSError, TypeError, ValueError):
            raise FormaError(_READ_ERROR) from None

    def _paths(self, report: Path) -> tuple[Path, Path]:
        canonical = Path(report).expanduser().resolve(strict=True)
        identity = os.path.normcase(str(canonical))
        key = hashlib.sha256(os.fsencode(identity)).hexdigest()
        return canonical, self.path / f"{key}.json"

    @staticmethod
    def _read_state(path: Path) -> dict | None:
        try:
            _regular(path)
            with path.open("rb") as stream:
                raw = stream.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "reportFingerprint", "accepted", "edits"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not _valid_fingerprint(data["reportFingerprint"])
        ):
            raise ValueError
        accepted, edits = _maps(data["accepted"], data["edits"])
        return {"reportFingerprint": data["reportFingerprint"], "accepted": accepted, "edits": edits}

    def load(self, report: Path) -> dict:
        """Return current fingerprint and decisions, empty when absent or stale."""
        try:
            canonical, state_path = self._paths(report)
            state = self._read_state(state_path)
            fingerprint = _report_fingerprint(canonical)
            if state is not None and state["reportFingerprint"] == fingerprint:
                return state
            return {"reportFingerprint": fingerprint, "accepted": {}, "edits": {}}
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, RuntimeError):
            raise FormaError(_READ_ERROR) from None

    def save(
        self,
        report: Path,
        accepted: dict[str, str],
        edits: dict[str, str],
        *,
        expected_fingerprint: str | None = None,
    ) -> None:
        """Save a complete snapshot, preserving corrupt state for recovery."""
        temporary = None
        try:
            accepted, edits = _maps(accepted, edits)
            if expected_fingerprint is not None and not _valid_fingerprint(expected_fingerprint):
                raise ValueError
            canonical, state_path = self._paths(report)
            fingerprint = _report_fingerprint(canonical)
            if expected_fingerprint is not None and fingerprint != expected_fingerprint:
                raise FormaError(_CHANGED_ERROR)
            self._read_state(state_path)  # A damaged snapshot must never be silently replaced.
            data = json.dumps(
                {
                    "version": 1,
                    "reportFingerprint": fingerprint,
                    "accepted": accepted,
                    "edits": edits,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(data) > MAX_STATE_BYTES:
                raise ValueError
            fd, name = tempfile.mkstemp(prefix=".review-", suffix=".tmp", dir=self.path)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if _report_fingerprint(canonical) != fingerprint:
                raise FormaError(_CHANGED_ERROR)
            os.replace(temporary, state_path)
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, RuntimeError):
            raise FormaError(_SAVE_ERROR) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
