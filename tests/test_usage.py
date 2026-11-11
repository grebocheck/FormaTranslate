import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from forma.accounting.usage import (
    UsageLedger,
    amount,
    estimate,
    money,
    official_openai,
    record_attempt,
    token_counts,
)
from forma.core.models import Cancelled, FormaError
from forma.providers.client import JobControl, Provider
from forma.settings.config import Settings


def settings(**kwargs):
    return Settings(model="gpt-4.1-mini", api_key="private-test-key", **kwargs)


def response(incoming=1000, cached=200, outgoing=100, **extra):
    return {
        "usage": {
            "prompt_tokens": incoming,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens": outgoing,
            "completion_tokens_details": {"reasoning_tokens": 80},
        },
        **extra,
    }


def test_prices_count_cache_and_reasoning_once_and_preserve_recorded_tariff():
    config, ledger = settings(), UsageLedger()
    ledger.record(config, response())
    # 800 * .40 + 200 * .10 + 100 * 1.60, USD / 1M
    assert ledger.summary(config)["today"]["cost"] == 500_000
    ledger.set_tariff(config, ("1", ".5", "3"))
    ledger.record(config, response())
    result = ledger.summary(config)["today"]
    assert result["cost"] == 1_700_000
    assert result["input"] == 2000 and result["cached"] == 400 and result["output"] == 200
    assert result["unknown"] == 0
    ledger.set_tariff(config, None)
    assert ledger.summary(config)["tariff"] == []
    assert ledger.summary(config)["today"]["cost"] == result["cost"]


@pytest.mark.parametrize(
    "model,tier,endpoint",
    [
        ("gpt-4.1-mini", "priority", "https://api.openai.com/v1"),
        ("gpt-4.1-mini", "flex", "https://api.openai.com/v1"),
        ("gpt-4.1-mini", None, "https://proxy.invalid/v1"),
        ("gpt-4.1-mini", None, "https://api.openai.com.evil.invalid/v1"),
        ("gpt-4.1-mini", None, "https://api.openai.com/v1/custom"),
        ("gpt-4.1-mini-unknown", None, "https://api.openai.com/v1"),
        ("gpt-5-research", None, "https://api.openai.com/v1"),
    ],
)
def test_unknown_tariff_is_not_free_or_automatically_openai(model, tier, endpoint):
    config = Settings(model=model, base_url=endpoint)
    ledger = UsageLedger()
    ledger.record(config, response(service_tier=tier))
    totals = ledger.summary(config)["today"]
    assert totals["cost"] == 0 and totals["unknown"] == 1 and totals["requests"] == 1


def test_snapshot_and_long_context_rates_and_promo_expiry():
    cfg = settings()
    assert official_openai(replace(cfg, base_url="https://API.OPENAI.COM:443/v1/responses/"))
    cost, _ = estimate(cfg, "gpt-5.4-2026-03-05", (300_000, 100_000, 1000), None, None, 0)
    assert cost == 1_072_500_000
    assert estimate(cfg, "gpt-5.6-sol", (10, 0, 10), None, None, 1796083200)[0] is None
    assert estimate(cfg, "gpt-5.6-sol", (10, 0, 10), None, None, 1796083199)[0] == 240_000


def test_account_and_endpoint_isolation_and_no_credentials_or_content_on_disk():
    ledger, cfg = UsageLedger(), settings()
    ledger.record(
        cfg, response(model="private-test-key", text="SECRET DOCUMENT", id="secret-response")
    )
    assert ledger.summary(cfg)["today"]["unknown"] == 1
    assert ledger.summary(replace(cfg, api_key="other-key"))["today"]["requests"] == 0
    assert ledger.summary(replace(cfg, base_url="https://other.invalid"))["today"]["requests"] == 0
    raw = ledger.path.read_bytes()
    for secret in (b"private-test-key", b"SECRET DOCUMENT", b"secret-response", b"api.openai.com"):
        assert secret not in raw


def test_native_legacy_aliases_preserve_history_balance_and_tariff_conflicts():
    cfg = Settings(base_url="https://native.example/v1/messages", api_mode="anthropic", model="claude-test",
                   api_key="native-ledger-key", legacy_connection=("anthropic", "https://native.example/v1/messages"))
    urls = ("https://native.example/v1", "https://native.example/v1/messages")
    aliases = [hashlib.sha256((url + "\0" + cfg.api_key).encode()).hexdigest() for url in urls]
    ledger, now = UsageLedger(), datetime.now(UTC).timestamp()
    with ledger.connect() as db:
        for index, account in enumerate(aliases, 1):
            db.execute("INSERT INTO requests(account,at,model,status,input,cached,output,cost,price_source) VALUES(?,?,?,?,?,?,?,?,?)",
                       (account, now, cfg.model, 200, 10, 0, 2, index * 1000, "manual"))
            db.execute("INSERT INTO tariffs VALUES(?,?,?,?,?)", (account, cfg.model, str(index), "0", "3"))
            db.execute("INSERT INTO balances VALUES(?,?,?,?)", (account, index * 1_000_000, 1, now + index))
    first = ledger.summary(cfg)
    assert first["today"]["requests"] == 2 and first["today"]["cost"] == 3000
    assert first["balance"]["remaining"] == 2_000_000 - 2000  # Latest original balance wins.
    assert first["tariff"] == []  # Conflicting untimestamped rates are not guessed.
    equivalent = replace(cfg, base_url="https://native.example", legacy_connection=None)
    assert ledger.summary(equivalent)["today"] == first["today"]
    ledger.set_tariff(equivalent, ("5", "1", "8"))
    assert list(map(Decimal, ledger.summary(equivalent)["tariff"])) == list(map(Decimal, ("5", "1", "8")))
    assert ledger.summary(replace(equivalent, api_key="other-key"))["today"]["requests"] == 0
    assert ledger.summary(replace(equivalent, api_mode="chat"))["today"]["requests"] == 0
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
        assert {row[0] for row in db.execute("SELECT account FROM requests")} == set(aliases)
        assert db.execute("SELECT COUNT(*) FROM balances").fetchone()[0] == 2
    ledger.set_balance(equivalent, None)
    ledger.set_tariff(equivalent, None)
    assert ledger.summary(equivalent)["balance"] is None
    assert ledger.summary(equivalent)["tariff"] == []


def test_calendar_periods_and_parallel_requests_persist():
    cfg, ledger = settings(), UsageLedger()
    now = datetime(2026, 11, 10, 18, tzinfo=UTC)
    ledger.record(cfg, response(), timestamp=(now - timedelta(days=31)).timestamp())
    ledger.record(cfg, response(), timestamp=(now - timedelta(days=1)).timestamp())
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(
            pool.map(lambda _: ledger.record(cfg, response(), timestamp=now.timestamp()), range(80))
        )
    totals = UsageLedger().summary(cfg, now)
    assert totals["today"]["requests"] == 80
    assert totals["month"]["requests"] == 81
    assert totals["month"]["cost"] == 81 * 500_000


def test_balance_counts_only_future_rows_and_hides_incomplete_estimates():
    cfg, ledger = settings(), UsageLedger()
    ledger.record(cfg, response())
    ledger.set_balance(cfg, "10,25")
    assert ledger.summary(cfg)["balance"]["remaining"] == 10_250_000_000
    ledger.record(cfg, response())
    assert ledger.summary(cfg)["balance"]["remaining"] == 10_249_500_000
    ledger.record(cfg, {}, status=504)
    assert ledger.summary(cfg)["balance"]["remaining"] is None
    ledger.set_balance(cfg, "8")
    assert ledger.summary(cfg)["balance"]["remaining"] == 8_000_000_000
    ledger.set_balance(cfg, None)
    assert ledger.summary(cfg)["balance"] is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1000001", "", "1e10000", "1.2.3"])
def test_invalid_financial_input(value):
    with pytest.raises(ValueError):
        amount(value)


def test_small_amounts_do_not_round_to_zero():
    assert money(1) == "< $0.000001"
    assert money(1200) == "$0.000001"
    assert money(None) == "—"
    assert money(-10_000_000_000) == "$-10.00"
    assert amount("0,25") == Decimal(".25")


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": True, "output_tokens": 0},
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 10, "output_tokens": 0, "input_tokens_details": "bad"},
    ],
)
def test_missing_or_malformed_usage_is_unknown(usage):
    assert token_counts("responses", {"usage": usage}) is None


def test_native_token_usage_and_zero_are_distinct_from_missing():
    assert token_counts("responses", {"usage": {"input_tokens": 0, "output_tokens": 0}}) == (
        0,
        0,
        0,
    )
    assert token_counts(
        "gemini",
        {
            "usageMetadata": {
                "promptTokenCount": 20,
                "cachedContentTokenCount": 3,
                "candidatesTokenCount": 8,
                "thoughtsTokenCount": 5,
            }
        },
    ) == (20, 3, 13)
    assert token_counts(
        "anthropic",
        {
            "usage": {
                "input_tokens": 20,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
                "output_tokens": 8,
            }
        },
    ) == (25, 3, 8)


def test_provider_records_malformed_refused_and_cancelled_responses():
    cfg, ledger = settings(), UsageLedger()
    control = JobControl()

    def handler(_):
        control.cancelled.set()
        return httpx.Response(200, json=response(choices=[{"message": {"content": "not JSON"}}]))

    provider = Provider(cfg, control=control, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Cancelled):
            provider.complete("Translate", {}, {})
    finally:
        provider.close()
    assert ledger.summary(cfg)["today"]["cost"] == 500_000
    for body in ("bad", {"refusal": "refused"}):
        provider = Provider(
            cfg,
            transport=httpx.MockTransport(
                lambda _, body=body: httpx.Response(
                    200, json=response(choices=[{"message": {"content": body}}])
                )
            ),
        )
        try:
            with pytest.raises(FormaError):
                provider.complete("Translate", {}, {})
        finally:
            provider.close()
    assert ledger.summary(cfg)["today"]["requests"] == 3


def test_provider_retries_each_actual_attempt_and_marks_network_charge_unknown(monkeypatch):
    cfg, ledger = settings(), UsageLedger()
    control = JobControl()
    monkeypatch.setattr(control, "delay", lambda _: None)
    attempts = iter(("network", "server", "ok"))

    def handler(request):
        event = next(attempts)
        if event == "network":
            raise httpx.ReadTimeout("private", request=request)
        return httpx.Response(
            500 if event == "server" else 200,
            json=response(choices=[{"message": {"content": json.dumps({"ok": True})}}]),
        )

    provider = Provider(cfg, control=control, transport=httpx.MockTransport(handler))
    try:
        assert provider.complete("Test", {}, {}) == {"ok": True}
    finally:
        provider.close()
    totals = ledger.summary(cfg)["today"]
    assert totals["requests"] == 3 and totals["unknown"] == 1 and totals["cost"] == 1_000_000


def test_unwritable_ledger_does_not_break_translation_and_remains_visible(monkeypatch):
    cfg, ledger = settings(), UsageLedger()
    with monkeypatch.context() as patch:
        patch.setattr(
            UsageLedger,
            "record",
            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("private")),
        )
        record_attempt(cfg, response())
    assert ledger.summary(cfg)["incomplete"]


def test_future_database_version_is_never_reset():
    ledger = UsageLedger()
    with sqlite3.connect(ledger.path) as db:
        db.execute("PRAGMA user_version=9")
    with pytest.raises(sqlite3.DatabaseError):
        ledger.summary(settings())
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9


def test_protocol_failure_records_unknown_charge_before_propagating():
    cfg = settings()

    def handler(request):
        raise httpx.RemoteProtocolError("connection closed", request=request)

    provider = Provider(cfg, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.RemoteProtocolError):
            provider.complete("Test", {}, {})
    finally:
        provider.close()
    totals = UsageLedger().summary(cfg)["today"]
    assert totals["requests"] == 1 and totals["unknown"] == 1
