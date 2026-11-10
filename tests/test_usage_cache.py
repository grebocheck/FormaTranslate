"""Billing regressions from official cache documentation and sanitized API usage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from forma.config import Settings
from forma.usage import UsageLedger, cache_write_counts, estimate, scope


def config():
    return Settings(model="gpt-6.1-terra", api_key="test-only")


def data(total=1502, cached=0, written=1499, output=14, mode="chat", **details):
    return {
        "usage": {
            "prompt_tokens" if mode == "chat" else "input_tokens": total,
            "completion_tokens" if mode == "chat" else "output_tokens": output,
            "prompt_tokens_details" if mode == "chat" else "input_tokens_details": {
                "cached_tokens": cached,
                "cache_write_tokens": written,
                **details,
            },
        }
    }


@pytest.mark.parametrize("mode", ["chat", "responses"])
def test_actual_usage_cache_write_rate_replaces_ordinary_input_and_balance(mode):
    cfg = config().with_overrides(api_mode=mode)
    ledger = UsageLedger()
    ledger.set_balance(cfg, "1")
    ledger.record(cfg, data(mode=mode))
    totals = ledger.summary(cfg)
    # Real sanitized response: 3 ordinary tokens + 1499 writes * 1.25 + 14 output * 5.
    assert totals["today"]["cost"] == 1_946_750
    assert totals["today"]["uncertain"] == totals["today"]["unknown"] == 0
    assert totals["today"]["cache_write"] == 1499
    assert totals["balance"]["remaining"] == 998_053_250
    ledger.record(cfg, data(cached=1499, written=0, mode=mode))
    # Cache hit costs 3 * 1 + 1499 * .05 + 14 * 5. Reasoning is already in output.
    assert ledger.summary(cfg)["today"]["cost"] == 2_094_700
    assert ledger.summary(cfg)["models"][0]["cached"] == 1499


def test_explicit_lifetime_buckets_and_long_context_are_disjoint():
    cfg = config()
    body = data(
        total=12000,
        cached=4000,
        written=7000,
        output=0,
        cache_write_tokens_details={"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000},
    )
    writes = cache_write_counts("responses", body)
    assert writes == (7000, 3000, 4000)
    assert estimate(cfg, cfg.model, (12000, 4000, 0), None, None, 0, writes)[0] == 14_950_000
    assert (
        estimate(cfg, cfg.model, (300000, 100000, 1000), None, None, 0, (150000, 100000, 50000))[0]
        == 617_500_000
    )


@pytest.mark.parametrize(
    "details",
    [
        {"cache_write_tokens": -1},
        {"cache_write_tokens": True},
        {"cache_write_tokens": 10000},
        {"cache_write_tokens_details": {"ttl_30m_tokens": 1499}},
        {"cache_write_tokens_details": {"ttl_30m_tokens": 1499, "ttl_12h_tokens": 1}},
        {"cache_write_tokens_details": []},
    ],
)
def test_invalid_write_counts_never_create_a_false_price(details):
    cfg, ledger = config(), UsageLedger()
    body = data()
    body["usage"]["prompt_tokens_details"].update(details)
    ledger.record(cfg, body)
    totals = ledger.summary(cfg)["today"]
    assert totals["unknown"] == 1 and totals["cost"] == 0


def test_missing_write_detail_is_a_lower_bound_and_hides_balance():
    cfg, ledger = config(), UsageLedger()
    ledger.set_balance(cfg, "10")
    body = data()
    del body["usage"]["prompt_tokens_details"]["cache_write_tokens"]
    ledger.record(cfg, body)
    summary = ledger.summary(cfg)
    assert summary["today"]["cost"] == 1_572_000
    assert summary["today"]["uncertain"] == 1
    assert summary["balance"]["remaining"] is None
    ledger.set_balance(cfg, "9")
    assert ledger.summary(cfg)["balance"]["remaining"] == 9_000_000_000


def test_reported_write_without_known_provider_tariff_is_not_regular_input():
    cfg = config().with_overrides(base_url="https://other.invalid/v1")
    ledger = UsageLedger()
    ledger.set_tariff(cfg, ("1", ".05", "5"))
    ledger.record(cfg, data())
    assert ledger.summary(cfg)["today"]["unknown"] == 1
    cfg = cfg.with_overrides(api_mode="anthropic", model="claude-test")
    ledger.set_tariff(cfg, ("1", ".05", "5"))
    ledger.record(
        cfg,
        {
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 1499,
                "cache_read_input_tokens": 0,
                "output_tokens": 14,
            }
        },
    )
    assert ledger.summary(cfg)["today"]["unknown"] == 2


def test_migrate_v1_preserves_history_tariffs_baselines_and_marks_missing_details():
    cfg, ledger = config(), UsageLedger()
    account = scope(cfg)
    with sqlite3.connect(ledger.path) as db:
        db.executescript("""
          CREATE TABLE requests(id INTEGER PRIMARY KEY, account TEXT NOT NULL, at REAL NOT NULL,
            model TEXT NOT NULL, status INTEGER NOT NULL, input INTEGER, cached INTEGER,
            output INTEGER, cost INTEGER, price_source TEXT NOT NULL);
          CREATE INDEX requests_scope ON requests(account, at);
          CREATE TABLE tariffs(account TEXT, model TEXT, input TEXT, cached TEXT, output TEXT,
            PRIMARY KEY(account, model));
          CREATE TABLE balances(account TEXT PRIMARY KEY, amount INTEGER, after_id INTEGER, at REAL);
          PRAGMA user_version=1;
        """)
        for model, price in (
            (cfg.model, "openai_standard_2026-11-10"),
            ("gpt-4.1-mini", "openai_standard_2026-11-10"),
            ("custom-model", "manual"),
        ):
            db.execute(
                "INSERT INTO requests VALUES(NULL, ?, 9999999999, ?, 200, 1502, 0, 14, 1572000, ?)",
                (account, model, price),
            )
        db.execute("INSERT INTO tariffs VALUES(?,?,?,?,?)", (account, cfg.model, "1", ".05", "5"))
        db.execute("INSERT INTO balances VALUES(?, 1000000000, 0, 1)", (account,))
    # Concurrent first readers must not race ALTER TABLE or erase history.
    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = list(pool.map(lambda _: UsageLedger().summary(cfg), range(16)))
    for summary in summaries:
        assert summary["today"]["requests"] == 3
        assert summary["today"]["cost"] == 4_716_000
        assert summary["today"]["uncertain"] == 2
        assert summary["today"]["unbounded"] == 1
        assert summary["balance"]["remaining"] is None
        assert summary["tariff"] == ["1", ".05", "5"]
    with ledger.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            db.execute("SELECT count(*) FROM requests WHERE cache_write IS NOT NULL").fetchone()[0]
            == 0
        )
    ledger.record(cfg, data())
    assert ledger.summary(cfg)["today"]["cost"] == 6_662_750
