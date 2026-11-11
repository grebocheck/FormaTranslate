"""Private, project-scoped exact translation memory; never an API credential store.

Approved decisions and glossary revisions are immutable. A job snapshot freezes
those decisions while allowing newly completed exact machine translations to be
shared by documents in the same queue. Connections and transactions are short;
no connection belongs to a provider request or a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from forma.core.models import FormaError
from forma.settings.config import data_directory

TM_PROTOCOL = 1
_SCHEMA_VERSION = 1
_APPLICATION_ID = 0x46544D31
_MAX_TEXT = 1024 * 1024
_MAX_CONTEXT = 64 * 1024
_ERROR = "Could not access local translation memory. Check its location and database integrity."
_INVALID = "Invalid translation memory entry or policy."
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_STATUSES = frozenset({"draft", "machine_final", "human_approved", "reference"})
_PROVENANCE_KEYS = frozenset({
    "kind", "origin", "model", "segment_id", "document_id", "import_id", "source_entry_id",
    "source_sha256", "output_sha256", "report_sha256", "report_fingerprint", "revision",
})


class _FrozenDict(dict):
    """A JSON/asdict-compatible dictionary whose ordinary mutation is disabled."""

    def _immutable(self, *_args, **_kwargs):
        raise TypeError("Translation memory snapshots are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, _memo):
        return self

    def __hash__(self):
        return hash(tuple(sorted(self.items())))


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    source_language: str
    target_language: str
    glossary_revision: int
    glossary: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "glossary", _FrozenDict(self.glossary))


@dataclass(frozen=True)
class MemorySnapshot:
    project_id: str
    source_language: str
    target_language: str
    approved_revision: int
    glossary_revision: int
    glossary: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "glossary", _FrozenDict(self.glossary))


@dataclass(frozen=True)
class MemoryHit:
    entry_id: int
    project_id: str
    entry_key: str
    source_markup: str
    target: str
    context: str
    role: str
    policy: str
    generation_profile: str
    status: str
    revision: int
    created_at: float
    provenance: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "provenance", _FrozenDict(self.provenance))

    @property
    def id(self):
        return self.entry_id


class MemoryConflict(FormaError):
    def __init__(
        self,
        message="Translation memory has a different approved translation. Choose whether to replace it.",
        *,
        current_revision=0,
        current_target="",
        entry_key="",
        reason="approved_conflict",
        current_glossary_revision=0,
    ):
        super().__init__(message)
        self.current_revision = current_revision
        self.current_target = current_target
        self.entry_key = entry_key
        self.reason = reason
        self.current_glossary_revision = current_glossary_revision


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value) -> str:
    try:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise FormaError(_INVALID) from None


def _glossary(value) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 5000:
        raise FormaError(_INVALID)
    result = {}
    for source, target in value.items():
        if not isinstance(source, str) or not isinstance(target, str) or not source.strip() or not target.strip() or len(source) > 2000 or len(target) > 4000:
            raise FormaError(_INVALID)
        result[source] = target
    if len(_json(result).encode("utf-8")) > 4 * 1024 * 1024:
        raise FormaError(_INVALID)
    return result


def _language(value) -> str:
    if not isinstance(value, str) or not _LANGUAGE.fullmatch(value.lower()) or value.lower() == "auto":
        raise FormaError("Translation memory projects require explicit source and target languages.")
    return value.lower()


def _digest(value) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise FormaError(_INVALID)
    return value


def _text(value, maximum=_MAX_TEXT, *, empty=True) -> str:
    try:
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= maximum and (empty or bool(value.strip()))
    except UnicodeError:
        valid = False
    if not valid:
        raise FormaError(_INVALID)
    return value


def _revision(value):
    if value is not None and (type(value) is not int or value < 0):
        raise FormaError(_INVALID)
    return value


def policy_fingerprint(options, glossary: dict) -> str:
    """Policy identity is independent of model, project, review and auto glossary."""
    source = _text(options.source_language, 64).lower()
    target = _text(options.target_language, 64).lower()
    decimal_point = source.split("-")[0] in {"uk", "ru", "de", "fr", "pl", "it", "es", "pt"} and target.split("-")[0] == "en"
    return _fingerprint({
        "memory_protocol": TM_PROTOCOL,
        "source_language": source,
        "target_language": target,
        "domain": _text(options.domain, _MAX_CONTEXT),
        "instructions": _text(options.instructions, _MAX_CONTEXT),
        "glossary": _glossary(glossary),
        "numeric_convention": "decimal_point" if decimal_point else "preserve_source",
    })


def generation_fingerprint(settings, *, protocol: int, review: bool, legacy: bool = False) -> str:
    """Hash an explicit allowlist; never serialize Settings or its API key."""
    from forma.settings.connection import connection_identity, legacy_route_compatible

    values = {
        "memory_protocol": TM_PROTOCOL,
        "translation_protocol": protocol,
        "review": review,
        **{name: getattr(settings, name) for name in (
            "base_url", "model", "api_mode", "response_format", "review_model", "reasoning_effort", "max_output_tokens",
        )},
        "base_url": settings.base_url if legacy else connection_identity(settings.base_url, settings.api_mode).root,
    }
    if not legacy and not legacy_route_compatible(settings.base_url, settings.api_mode):
        values["connection_route"] = 2
    return _fingerprint(values)


def exact_fingerprint(policy: str, source_markup: str, context: str, role: str = "") -> str:
    """Full marked-up source, context and role are significant; never normalize."""
    return _fingerprint({
        "policy": _digest(policy),
        "source_markup": _text(source_markup, empty=False),
        "context": _text(context, _MAX_CONTEXT),
        "role": _text(role, 8192),
    })


def _provenance(value) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise FormaError(_INVALID)
    safe = {}
    for key in _PROVENANCE_KEYS.intersection(value):
        item = value[key]
        if type(item) is int and 0 <= item <= 2**63 - 1:
            safe[key] = item
        elif isinstance(item, str) and len(item) <= 500 and "\n" not in item:
            if key.endswith(("sha256", "fingerprint")) and not _DIGEST.fullmatch(item):
                continue
            safe[key] = item
    return _json(safe)


class TranslationMemory:
    def __init__(self, db_path: Path | str | None = None):
        try:
            self.path = Path(db_path) if db_path is not None else data_directory() / "translation-memory.sqlite3"
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except (OSError, TypeError, ValueError):
            raise FormaError(_ERROR) from None

    @staticmethod
    def _schema(db):
        # Read both header values in one SQLite snapshot during concurrent
        # first-use initialization; separate PRAGMAs could straddle a commit.
        version, application = db.execute("SELECT user_version,application_id FROM pragma_user_version,pragma_application_id").fetchone()
        if version == _SCHEMA_VERSION and application == _APPLICATION_ID:
            return
        if version != 0 or application != 0:
            raise sqlite3.DatabaseError("Unsupported translation memory schema")
        db.execute("BEGIN IMMEDIATE")
        try:
            version, application = db.execute("SELECT user_version,application_id FROM pragma_user_version,pragma_application_id").fetchone()
            if version == _SCHEMA_VERSION and application == _APPLICATION_ID:
                db.commit()
                return
            if version != 0 or application != 0 or db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                raise sqlite3.DatabaseError("Unknown translation memory schema")
            for statement in (
                "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, source_language TEXT NOT NULL, target_language TEXT NOT NULL, glossary_revision INTEGER NOT NULL, approved_revision INTEGER NOT NULL, created_at REAL NOT NULL)",
                "CREATE TABLE glossaries (project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL, glossary TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(project_id,revision))",
                "CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id), entry_key TEXT NOT NULL, source_markup TEXT NOT NULL, target TEXT NOT NULL, context TEXT NOT NULL, role TEXT NOT NULL, policy TEXT NOT NULL, generation_profile TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','machine_final','human_approved','reference')), approved_revision INTEGER, created_at REAL NOT NULL, provenance TEXT NOT NULL)",
                "CREATE UNIQUE INDEX approved_versions ON entries(project_id,approved_revision) WHERE status='human_approved'",
                "CREATE INDEX preferred_lookup ON entries(project_id,entry_key,status,approved_revision DESC)",
                "CREATE INDEX machine_lookup ON entries(project_id,entry_key,generation_profile,status,id DESC)",
            ):
                db.execute(statement)
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @contextmanager
    def _connect(self, *, write=False):
        db = None
        try:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            self._schema(db)
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError, OverflowError):
            if db is not None:
                db.rollback()
            raise FormaError(_ERROR) from None
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _project_row(db, project_id):
        row = db.execute("SELECT * FROM projects WHERE id=?", (_text(project_id, 128, empty=False),)).fetchone()
        if row is None:
            raise FormaError("Translation memory project was not found.")
        return row

    @staticmethod
    def _project(db, row):
        glossary = db.execute("SELECT glossary FROM glossaries WHERE project_id=? AND revision=?", (row["id"], row["glossary_revision"])).fetchone()
        if glossary is None:
            raise sqlite3.DatabaseError("Missing project glossary")
        return Project(row["id"], row["name"], row["source_language"], row["target_language"], row["glossary_revision"], _glossary(json.loads(glossary[0])))

    @staticmethod
    def _hit(row):
        return MemoryHit(
            row["id"], row["project_id"], row["entry_key"], row["source_markup"], row["target"], row["context"], row["role"], row["policy"], row["generation_profile"], row["status"],
            row["approved_revision"] if row["status"] == "human_approved" else row["id"],
            row["created_at"], json.loads(row["provenance"]),
        )

    def create_project(self, name, source_language, target_language, glossary=None) -> Project:
        name = _text(name, 800, empty=False).strip()
        source_language, target_language = _language(source_language), _language(target_language)
        glossary = _glossary({} if glossary is None else glossary)
        project_id, now = uuid.uuid4().hex, time.time()
        with self._connect(write=True) as db:
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", (project_id, name, source_language, target_language, 0, 0, now))
            db.execute("INSERT INTO glossaries VALUES(?,?,?,?)", (project_id, 0, _json(glossary), now))
            return self._project(db, self._project_row(db, project_id))

    def list_projects(self) -> list[Project]:
        with self._connect() as db:
            return [self._project(db, row) for row in db.execute("SELECT * FROM projects ORDER BY created_at,id")]

    def get_project(self, project_id) -> Project:
        with self._connect() as db:
            return self._project(db, self._project_row(db, project_id))

    def update_glossary(self, project_id, glossary, expected_revision=None) -> Project:
        glossary, expected_revision = _glossary(glossary), _revision(expected_revision)
        with self._connect(write=True) as db:
            row = self._project_row(db, project_id)
            if expected_revision is not None and row["glossary_revision"] != expected_revision:
                raise MemoryConflict("The project glossary changed. Refresh it before saving.", reason="glossary_changed", current_revision=row["approved_revision"], current_glossary_revision=row["glossary_revision"])
            current = self._project(db, row)
            if dict(current.glossary) == glossary:
                return current
            revision = row["glossary_revision"] + 1
            db.execute("INSERT INTO glossaries VALUES(?,?,?,?)", (project_id, revision, _json(glossary), time.time()))
            db.execute("UPDATE projects SET glossary_revision=? WHERE id=?", (revision, project_id))
            return self._project(db, self._project_row(db, project_id))

    def snapshot(self, project_id) -> MemorySnapshot:
        with self._connect() as db:
            row = self._project_row(db, project_id)
            project = self._project(db, row)
            return MemorySnapshot(project.id, project.source_language, project.target_language, row["approved_revision"], project.glossary_revision, project.glossary)

    def lookup(self, snapshot, key, generation_profile, include_draft=False, preferred_only=False) -> MemoryHit | None:
        return self.lookup_many(
            snapshot, [key], generation_profile,
            include_draft=include_draft, preferred_only=preferred_only,
        ).get(key)

    def lookup_many(self, snapshot, keys, generation_profile, include_draft=False, preferred_only=False) -> dict[str, MemoryHit]:
        """Resolve exact entries in one bounded read transaction, without API work.

        Four hundred keys per SQL statement stays below conservative SQLite
        parameter limits. Aggregating latest IDs/revisions avoids loading every
        historical version into Python when a project has a long review history.
        """
        try:
            keys = tuple(dict.fromkeys(_digest(key) for key in keys))
        except TypeError:
            raise FormaError(_INVALID) from None
        if not preferred_only:
            _digest(generation_profile)
        if not keys:
            return {}
        found = {}
        with self._connect() as db:
            project = self._project_row(db, snapshot.project_id)
            if project["source_language"] != snapshot.source_language or project["target_language"] != snapshot.target_language or not 0 <= snapshot.approved_revision <= project["approved_revision"]:
                raise FormaError(_INVALID)
            for offset in range(0, len(keys), 400):
                chunk = keys[offset:offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = db.execute(
                    "SELECT entry.* FROM entries AS entry JOIN ("
                    "SELECT entry_key,MAX(approved_revision) AS revision FROM entries "
                    f"WHERE project_id=? AND entry_key IN ({placeholders}) "
                    "AND status='human_approved' AND approved_revision<=? GROUP BY entry_key"
                    ") AS preferred ON entry.project_id=? AND entry.entry_key=preferred.entry_key "
                    "AND entry.approved_revision=preferred.revision AND entry.status='human_approved'",
                    (snapshot.project_id, *chunk, snapshot.approved_revision, snapshot.project_id),
                )
                for row in rows:
                    found[row["entry_key"]] = self._hit(row)
                if preferred_only:
                    continue
                for status in (("machine_final", "draft") if include_draft else ("machine_final",)):
                    pending = tuple(key for key in chunk if key not in found)
                    if not pending:
                        break
                    pending_placeholders = ",".join("?" for _ in pending)
                    rows = db.execute(
                        "SELECT * FROM entries WHERE id IN (SELECT MAX(id) FROM entries "
                        f"WHERE project_id=? AND entry_key IN ({pending_placeholders}) "
                        "AND generation_profile=? AND status=? GROUP BY entry_key)",
                        (snapshot.project_id, *pending, generation_profile, status),
                    )
                    for row in rows:
                        found[row["entry_key"]] = self._hit(row)
        return {key: found[key] for key in keys if key in found}

    def candidate_pool(self, snapshot, policy, generation_profile) -> list[MemoryHit]:
        """Latest scoped finals for editor-only similarity, never drafts/references.

        Human choices are frozen by the run watermark. Current exact-generation
        machine finals are included only when that key has no visible preferred
        choice. Existing project indexes bound the scan to this local project.
        """
        _digest(policy)
        _digest(generation_profile)
        found = {}
        with self._connect() as db:
            project = self._project_row(db, snapshot.project_id)
            if project["source_language"] != snapshot.source_language or project["target_language"] != snapshot.target_language or not 0 <= snapshot.approved_revision <= project["approved_revision"]:
                raise FormaError(_INVALID)
            preferred = db.execute(
                "SELECT * FROM entries WHERE id IN (SELECT MAX(id) FROM entries "
                "WHERE project_id=? AND policy=? AND status='human_approved' AND approved_revision<=? "
                "GROUP BY entry_key)",
                (snapshot.project_id, policy, snapshot.approved_revision),
            )
            for row in preferred:
                found[row["entry_key"]] = self._hit(row)
            machine = db.execute(
                "SELECT * FROM entries WHERE id IN (SELECT MAX(id) FROM entries "
                "WHERE project_id=? AND policy=? AND generation_profile=? AND status='machine_final' "
                "GROUP BY entry_key)",
                (snapshot.project_id, policy, generation_profile),
            )
            for row in machine:
                if row["entry_key"] not in found:
                    found[row["entry_key"]] = self._hit(row)
        result = []
        for hit in found.values():
            try:
                intact = exact_fingerprint(hit.policy, hit.source_markup, hit.context, hit.role) == hit.entry_key
            except FormaError:
                intact = False
            if intact:
                result.append(hit)
        return result

    @staticmethod
    def _entry(key, source_markup, target, context, role, policy, provenance):
        _digest(key)
        _digest(policy)
        _text(source_markup, empty=False)
        _text(target, empty=False)
        _text(context, _MAX_CONTEXT)
        _text(role, 8192)
        if exact_fingerprint(policy, source_markup, context, role) != key:
            raise FormaError(_INVALID)
        return {"key": key, "source_markup": source_markup, "target": target, "context": context, "role": role, "policy": policy, "provenance": _provenance(provenance)}

    @staticmethod
    def _insert(db, project_id, entry, status, generation_profile="", approved_revision=None):
        cursor = db.execute("INSERT INTO entries(project_id,entry_key,source_markup,target,context,role,policy,generation_profile,status,approved_revision,created_at,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (project_id, entry["key"], entry["source_markup"], entry["target"], entry["context"], entry["role"], entry["policy"], generation_profile, status, approved_revision, time.time(), entry["provenance"]))
        return TranslationMemory._hit(db.execute("SELECT * FROM entries WHERE id=?", (cursor.lastrowid,)).fetchone())

    @staticmethod
    def _expected(row, expected_revision, expected_glossary_revision):
        if expected_revision is not None and row["approved_revision"] != expected_revision:
            raise MemoryConflict("Approved translations changed. Refresh the project before saving.", reason="stale_revision", current_revision=row["approved_revision"], current_glossary_revision=row["glossary_revision"])
        if expected_glossary_revision is not None and row["glossary_revision"] != expected_glossary_revision:
            raise MemoryConflict("The project glossary changed. Refresh it before saving.", reason="glossary_changed", current_revision=row["approved_revision"], current_glossary_revision=row["glossary_revision"])

    @staticmethod
    def _approve(db, project_id, entry, *, replace, expected_revision):
        project = TranslationMemory._project_row(db, project_id)
        current = db.execute("SELECT * FROM entries WHERE project_id=? AND entry_key=? AND status='human_approved' ORDER BY approved_revision DESC LIMIT 1", (project_id, entry["key"])).fetchone()
        if current is not None:
            if current["target"] == entry["target"]:
                return TranslationMemory._hit(current)
            if not replace or expected_revision is None:
                raise MemoryConflict(current_revision=project["approved_revision"], current_target=current["target"], entry_key=entry["key"], current_glossary_revision=project["glossary_revision"])
        revision = project["approved_revision"] + 1
        result = TranslationMemory._insert(db, project_id, entry, "human_approved", approved_revision=revision)
        db.execute("UPDATE projects SET approved_revision=? WHERE id=?", (revision, project_id))
        return result

    def save_machine(self, project_id, key, source_markup, target, context, role, policy, generation_profile, *, status="machine_final", provenance=None) -> MemoryHit:
        if status not in {"draft", "machine_final"}:
            raise FormaError(_INVALID)
        _digest(generation_profile)
        entry = self._entry(key, source_markup, target, context, role, policy, provenance)
        with self._connect(write=True) as db:
            self._project_row(db, project_id)
            latest = db.execute("SELECT * FROM entries WHERE project_id=? AND entry_key=? AND generation_profile=? AND status=? ORDER BY id DESC LIMIT 1", (project_id, key, generation_profile, status)).fetchone()
            if latest is not None and latest["target"] == target:
                return self._hit(latest)
            return self._insert(db, project_id, entry, status, generation_profile)

    def approve(self, project_id, key, source_markup, target, context, role, policy, *, replace=False, expected_revision=None, expected_glossary_revision=None, provenance=None) -> MemoryHit:
        entry = self._entry(key, source_markup, target, context, role, policy, provenance)
        expected_revision, expected_glossary_revision = _revision(expected_revision), _revision(expected_glossary_revision)
        with self._connect(write=True) as db:
            project = self._project_row(db, project_id)
            self._expected(project, expected_revision, expected_glossary_revision)
            return self._approve(db, project_id, entry, replace=replace, expected_revision=expected_revision)

    def save_reference(self, project_id, key, source_markup, target, context, role, policy, *, provenance=None) -> MemoryHit:
        return self.save_batch(project_id, [{"key": key, "source_markup": source_markup, "target": target, "context": context, "role": role, "policy": policy, "provenance": provenance}])[0]

    def save_batch(self, project_id, entries, *, preferred=False, replace=False, expected_revision=None, expected_glossary_revision=None) -> list[MemoryHit]:
        if not isinstance(entries, (list, tuple)) or len(entries) > 100000:
            raise FormaError(_INVALID)
        parsed, targets = [], {}
        for item in entries:
            if not isinstance(item, Mapping):
                raise FormaError(_INVALID)
            try:
                entry = self._entry(item["key"], item["source_markup"], item["target"], item["context"], item["role"], item["policy"], item.get("provenance"))
            except KeyError:
                raise FormaError(_INVALID) from None
            if preferred and entry["key"] in targets and targets[entry["key"]] != entry["target"]:
                raise FormaError("This import contains conflicting preferred translations for the same exact segment.")
            targets[entry["key"]] = entry["target"]
            parsed.append(entry)
        expected_revision, expected_glossary_revision = _revision(expected_revision), _revision(expected_glossary_revision)
        with self._connect(write=True) as db:
            project = self._project_row(db, project_id)
            self._expected(project, expected_revision, expected_glossary_revision)
            result = []
            try:
                for entry in parsed:
                    if preferred:
                        result.append(self._approve(db, project_id, entry, replace=replace, expected_revision=expected_revision))
                    else:
                        latest = db.execute("SELECT * FROM entries WHERE project_id=? AND entry_key=? AND status='reference' ORDER BY id DESC LIMIT 1", (project_id, entry["key"])).fetchone()
                        if latest is not None and latest["target"] == entry["target"] and latest["provenance"] == entry["provenance"]:
                            result.append(self._hit(latest))
                        else:
                            result.append(self._insert(db, project_id, entry, "reference"))
            except MemoryConflict as conflict:
                # This transaction will roll back earlier batch approvals; do
                # not expose their provisional watermark as a replacement token.
                conflict.current_revision = project["approved_revision"]
                conflict.current_glossary_revision = project["glossary_revision"]
                raise
            return result

    def list_entries(self, project_id, *, status=None, limit=100, offset=0) -> list[MemoryHit]:
        if status is not None and status not in _STATUSES:
            raise FormaError(_INVALID)
        if type(limit) is not int or not 1 <= limit <= 10000 or type(offset) is not int or offset < 0:
            raise FormaError(_INVALID)
        with self._connect() as db:
            self._project_row(db, project_id)
            rows = db.execute("SELECT * FROM entries WHERE project_id=?" + (" AND status=?" if status else "") + " ORDER BY id DESC LIMIT ? OFFSET ?", (project_id, *((status,) if status else ()), limit, offset))
            return [self._hit(row) for row in rows]

    def history(self, project_id, key, *, limit=100) -> list[MemoryHit]:
        _digest(key)
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise FormaError(_INVALID)
        with self._connect() as db:
            self._project_row(db, project_id)
            return [self._hit(row) for row in db.execute("SELECT * FROM entries WHERE project_id=? AND entry_key=? ORDER BY id DESC LIMIT ?", (project_id, key, limit))]
