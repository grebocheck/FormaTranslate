"""Local, metadata-only API usage ledger. Estimates are never provider invoices.

One row per upstream attempt, before parsing/validation/cancellation. No prompts,
responses, URLs, response IDs or credentials are stored. Monetary values use
integer nanodollars; rates are USD per million tokens. Historical estimates keep
then-current prices instead of changing when a tariff is edited.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from forma.config import data_directory
from forma.credentials import canonical_endpoint
from forma.models import FormaError

PRICE_DATE = "2026-11-10"
PRICE_URL = "https://developers.openai.com/api/docs/pricing"
# Standard processing: input, cached input, output. Cache writes replace the
# ordinary input rate (1.25x for 30m, 2.5x for 12h); they are not an additive fee.
OPENAI_RATES = {
    "gpt-6-astra": ("10", "1", "50"),
    "gpt-6.1-sol": ("2", ".10", "10"),
    "gpt-6.1-terra": ("1", ".05", "5"),
    "gpt-6-luna": (".10", ".01", ".50"),
    "gpt-6-sol": ("2", ".20", "10"),
    "gpt-5.6-sol": ("4", ".40", "20"),
    "gpt-5.6-terra": ("2", ".20", "12"),
    "gpt-5.6-luna": (".20", ".02", "1.20"),
    "gpt-5.5": ("5", ".50", "30"),
    "gpt-5.5-pro": ("30", "30", "180"),
    "gpt-5.4": ("2.5", ".25", "15"),
    "gpt-5.4-pro": ("30", "30", "180"),
    "gpt-5.4-mini": (".75", ".075", "4.5"),
    "gpt-5.4-nano": (".20", ".02", "1.25"),
    "gpt-5.2": ("1.75", ".175", "14"),
    "gpt-5.2-pro": ("21", "21", "168"),
    "gpt-5.1": ("1.25", ".125", "10"),
    "gpt-5": ("1.25", ".125", "10"),
    "gpt-5-mini": (".25", ".025", "2"),
    "gpt-5-nano": (".05", ".005", ".40"),
    "gpt-5-pro": ("15", "15", "120"),
    "gpt-4.1": ("2", ".50", "8"),
    "gpt-4.1-mini": (".40", ".10", "1.60"),
    "gpt-4o": ("2.5", "1.25", "10"),
    "gpt-4o-mini": (".15", ".075", ".60"),
    "o3": ("2", ".50", "8"),
    "o3-pro": ("20", "20", "80"),
}
LONG_CONTEXT = {
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
}
CACHE_WRITE_MODELS = {name for name in OPENAI_RATES if name.startswith(("gpt-6", "gpt-5.6-"))}
_LOCK = threading.Lock()
_FAILURES: set[tuple[str, str]] = set()


def scope(settings) -> str:
    endpoint = canonical_endpoint(settings.base_url)
    return hashlib.sha256((endpoint + "\0" + settings.api_key).encode()).hexdigest()


def official_openai(settings) -> bool:
    return canonical_endpoint(settings.base_url) == "https://api.openai.com/v1"


def model_name(value, key="") -> str:
    # Never persist unbounded or arbitrary text returned by a provider.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,160}", value):
        return "unknown"
    return "unknown" if key and key in value else value


def amount(value, *, maximum="1000000") -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
        if not parsed.is_finite() or not 0 <= parsed <= Decimal(maximum):
            raise ValueError
        return parsed.quantize(Decimal(".000001"))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid monetary amount") from None


def money(nano: int | None) -> str:
    if nano is None:
        return "—"
    value = Decimal(nano) / 1_000_000_000
    if value and abs(value) < Decimal(".000001"):
        return "< $0.000001" if value > 0 else "> -$0.000001"
    whole, fraction = f"{value:,.6f}".split(".")
    return "$" + whole + "." + fraction.rstrip("0").ljust(2, "0")


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def token_counts(mode: str, data: dict) -> tuple[int, int, int] | None:
    """Input includes cached input; output already includes reasoning tokens."""
    usage = data.get("usageMetadata" if mode == "gemini" else "usage")
    if not isinstance(usage, dict):
        return None
    if mode == "gemini":
        incoming = _count(usage.get("promptTokenCount"))
        cached = _count(usage.get("cachedContentTokenCount", 0))
        outgoing = _count(usage.get("candidatesTokenCount", 0))
        thinking = _count(usage.get("thoughtsTokenCount", 0))
        if outgoing is not None and thinking is not None:
            outgoing += thinking
        else:
            outgoing = None
    elif mode == "anthropic":
        incoming = _count(usage.get("input_tokens"))
        cached = _count(usage.get("cache_read_input_tokens", 0))
        writes = _count(usage.get("cache_creation_input_tokens", 0))
        outgoing = _count(usage.get("output_tokens"))
        if incoming is not None and cached is not None and writes is not None:
            incoming += cached + writes
        else:
            incoming = None
    else:
        incoming = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
        outgoing = _count(usage.get("output_tokens", usage.get("completion_tokens")))
        details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
        cached = _count(details.get("cached_tokens", 0)) if isinstance(details, dict) else None
    if incoming is None or outgoing is None or cached is None or cached > incoming:
        return None
    return incoming, cached, outgoing


def cache_write_counts(mode: str, data: dict):
    """Read disjoint write buckets; None means malformed, a null total is missing.

    Forma sends no extended TTL: a total without lifetime detail uses the default
    30m lifetime. For Responses, explicit lifetime buckets take precedence.
    """
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    if mode == "gemini":
        return 0, 0, 0  # Forma does not create Gemini explicit context caches.
    if mode == "anthropic":
        total = _count(usage.get("cache_creation_input_tokens", 0))
        return (total, total, 0) if total is not None else None
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
    if not isinstance(details, dict):
        return None
    if "cache_write_tokens" not in details:
        return None, None, None
    total = _count(details["cache_write_tokens"])
    if total is None:
        return None
    lifetimes = details.get("cache_write_tokens_details")
    if lifetimes is None:
        return total, total, 0
    if not isinstance(lifetimes, dict):
        return None
    short, long = (_count(lifetimes.get(key)) for key in ("ttl_30m_tokens", "ttl_12h_tokens"))
    if short is None or long is None or short + long != total:
        return None
    return total, short, long


def estimate(settings, model, counts, tier, override, timestamp, writes=(0, 0, 0)):
    if counts is None:
        return None, "missing_usage"
    if writes is None or (writes[0] is not None and writes[0] + counts[1] > counts[0]):
        return None, "invalid_usage"
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    openai = official_openai(settings)
    write_pricing = openai and name in CACHE_WRITE_MODELS
    if override is not None:
        rates, source = tuple(map(Decimal, override)), "manual"
    else:
        if not openai or tier not in (None, "default", "standard"):
            return None, "unknown_tariff"
        if name not in OPENAI_RATES:
            return None, "unknown_tariff"
        if name == "gpt-5.6-sol" and timestamp >= 1796083200:  # 2026-12-01 UTC
            return None, "expired_tariff"
        rates = tuple(map(Decimal, OPENAI_RATES[name]))
        if name in LONG_CONTEXT and counts[0] > 272_000:
            rates = rates[0] * 2, rates[1] * 2, rates[2] * Decimal("1.5")
        source = "openai_standard_" + PRICE_DATE
    incoming, cached, outgoing = counts
    written, short, long = writes
    if written and not write_pricing:
        # A three-part manual tariff does not establish another provider's write fee.
        return None, "unknown_write_tariff"
    if written is None and write_pricing:
        # Keep only a lower bound; do not invent a zero write count.
        source += "_cache_unknown"
    written, short, long = written or 0, short or 0, long or 0
    cost = (
        (incoming - cached - written) * rates[0]
        + cached * rates[1]
        + short * rates[0] * Decimal("1.25")
        + long * rates[0] * Decimal("2.5")
        + outgoing * rates[2]
    ) * 1000
    return int(cost.to_integral_value(rounding=ROUND_HALF_UP)), source


class UsageLedger:
    def __init__(self, path=None):
        self.path = path or data_directory() / "usage.sqlite3"

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1, 2):
                        raise sqlite3.DatabaseError("Unsupported usage database version")
                    if version == 0:
                        for statement in (
                            "CREATE TABLE requests (id INTEGER PRIMARY KEY, account TEXT NOT NULL, at REAL NOT NULL, "
                            "model TEXT NOT NULL, status INTEGER NOT NULL, input INTEGER, cached INTEGER, output INTEGER, "
                            "cost INTEGER, price_source TEXT NOT NULL)",
                            "CREATE INDEX requests_scope ON requests(account, at)",
                            "CREATE TABLE tariffs (account TEXT NOT NULL, model TEXT NOT NULL, input TEXT NOT NULL, "
                            "cached TEXT NOT NULL, output TEXT NOT NULL, PRIMARY KEY(account, model))",
                            "CREATE TABLE balances (account TEXT PRIMARY KEY, amount INTEGER NOT NULL, after_id INTEGER NOT NULL, at REAL NOT NULL)",
                        ):
                            db.execute(statement)
                    if version in (0, 1):
                        for definition in (
                            "cache_write INTEGER",
                            "write_long INTEGER",
                            "service_tier TEXT",
                            "uncertain INTEGER NOT NULL DEFAULT 0",
                        ):
                            db.execute("ALTER TABLE requests ADD COLUMN " + definition)
                        # Version 1 discarded cache-write usage. Never silently relabel
                        # its old estimates as complete or reconstruct missing counts.
                        db.execute(
                            "UPDATE requests SET uncertain=CASE WHEN price_source='manual' THEN 2 ELSE 1 END "
                            "WHERE cost IS NOT NULL AND (price_source='manual' OR "
                            "(price_source LIKE 'openai_standard_%' AND "
                            "(model LIKE 'gpt-6%' OR model LIKE 'gpt-5.6-%')))"
                        )
                        db.execute("PRAGMA user_version=2")
            with db:
                yield db

    def record(self, settings, raw, *, model="", status=200, timestamp=None):
        now = time.time() if timestamp is None else timestamp
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, UnicodeError):
            data = None
        data = data if isinstance(data, dict) else {}
        requested = model_name(model or settings.model, settings.api_key)
        returned = model_name(
            data.get("model", data.get("modelVersion", requested)), settings.api_key
        )
        counts = token_counts(settings.api_mode, data)
        writes = cache_write_counts(settings.api_mode, data)
        tier = data.get("service_tier")
        safe_tier = (
            tier
            if tier
            in (
                None,
                "default",
                "standard",
                "flex",
                "priority",
                "fast",
                "ultrafast",
                "auto",
                "scale",
            )
            else "unknown"
        )
        account = scope(settings)
        with self.connect() as db:
            tariff = db.execute(
                "SELECT input,cached,output FROM tariffs WHERE account=? AND model=?",
                (account, requested),
            ).fetchone()
            cost, source = estimate(settings, returned, counts, safe_tier, tariff, now, writes)
            # Rejected requests without usage do not consume inference tokens.
            if counts is None and status in (400, 401, 403, 404, 413, 422, 429):
                cost, source = 0, "rejected"
            db.execute(
                "INSERT INTO requests(account,at,model,status,input,cached,output,cost,price_source,cache_write,write_long,service_tier,uncertain) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    account,
                    now,
                    returned,
                    status,
                    *(counts or (None, None, None)),
                    cost,
                    source,
                    writes[0] if writes else None,
                    writes[2] if writes else None,
                    safe_tier,
                    int(source.endswith("_cache_unknown")),
                ),
            )

    def set_tariff(self, settings, values):
        parsed = tuple(str(amount(v)) for v in values) if values is not None else None
        if parsed is not None and len(parsed) != 3:
            raise ValueError("Expected three rates")
        account, model = scope(settings), model_name(settings.model, settings.api_key)
        with self.connect() as db:
            if parsed is None:
                db.execute("DELETE FROM tariffs WHERE account=? AND model=?", (account, model))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO tariffs VALUES(?,?,?,?,?)", (account, model, *parsed)
                )

    def set_balance(self, settings, value):
        parsed = int(amount(value) * 1_000_000_000) if value is not None else None
        account = scope(settings)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if parsed is None:
                db.execute("DELETE FROM balances WHERE account=?", (account,))
            else:
                last_id = db.execute("SELECT COALESCE(MAX(id),0) FROM requests").fetchone()[0]
                db.execute(
                    "INSERT OR REPLACE INTO balances VALUES(?,?,?,?)",
                    (account, parsed, last_id, time.time()),
                )

    @staticmethod
    def _totals(db, account, condition, parameter):
        row = db.execute(
            "SELECT COUNT(*) requests, COALESCE(SUM(input),0) input, "
            "COALESCE(SUM(cached),0) cached, COALESCE(SUM(output),0) output, "
            "COALESCE(SUM(cost),0) cost, COALESCE(SUM(cost IS NULL),0) unknown, "
            "COALESCE(SUM(uncertain>0),0) uncertain, COALESCE(SUM(uncertain=2),0) unbounded, COALESCE(SUM(cache_write),0) cache_write "
            f"FROM requests WHERE account=? AND {condition}",
            (account, parameter),
        ).fetchone()
        return dict(row)

    def summary(self, settings, now=None):
        local_now = now or datetime.now().astimezone()
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Calendar midnights in the OS timezone, including DST transitions.
        if now is None:
            start_today = datetime.combine(local_now.date(), datetime.min.time()).timestamp()
            start_month = datetime.combine(
                local_now.date() - timedelta(days=29), datetime.min.time()
            ).timestamp()
        else:
            start_today, start_month = today.timestamp(), (today - timedelta(days=29)).timestamp()
        account = scope(settings)
        with self.connect() as db:
            db.execute("BEGIN")
            result = {
                "today": self._totals(db, account, "at>=?", start_today),
                "month": self._totals(db, account, "at>=?", start_month),
            }
            balance = db.execute("SELECT * FROM balances WHERE account=?", (account,)).fetchone()
            result["balance"] = None
            if balance:
                since = self._totals(db, account, "id>?", balance["after_id"])
                result["balance"] = {
                    "remaining": None
                    if since["unknown"] or since["uncertain"]
                    else balance["amount"] - since["cost"],
                    "at": balance["at"],
                    "unknown": since["unknown"] + since["uncertain"],
                }
            tariff = db.execute(
                "SELECT input,cached,output FROM tariffs WHERE account=? AND model=?",
                (account, model_name(settings.model, settings.api_key)),
            ).fetchone()
            result["tariff"] = list(tariff) if tariff else []
            result["models"] = [
                dict(row)
                for row in db.execute(
                    "SELECT model, COUNT(*) requests, COALESCE(SUM(input),0) input, "
                    "COALESCE(SUM(cached),0) cached, COALESCE(SUM(output),0) output, "
                    "COALESCE(SUM(cache_write),0) cache_write, COALESCE(SUM(cost),0) cost, "
                    "COALESCE(SUM(uncertain>0),0) uncertain, COALESCE(SUM(uncertain=2),0) unbounded, COALESCE(SUM(cost IS NULL),0) unknown "
                    "FROM requests WHERE account=? AND at>=? GROUP BY model ORDER BY SUM(cost) DESC",
                    (account, start_month),
                )
            ]
        with _LOCK:
            result["incomplete"] = (str(self.path), account) in _FAILURES
        return result


def record_attempt(settings, raw=None, *, model="", status=0):
    """Accounting must never abort a translation; failures remain visible in UI."""
    try:
        UsageLedger().record(settings, raw, model=model, status=status)
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, FormaError):
        try:
            failure = (str(data_directory() / "usage.sqlite3"), scope(settings))
            with _LOCK:
                # Bound session diagnostics; no credential or response contents.
                if len(_FAILURES) < 512:
                    _FAILURES.add(failure)
        except (OSError, ValueError, FormaError):
            pass
