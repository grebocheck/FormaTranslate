"""Attribution keeps paid attempts explainable without retaining document data."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from forma.accounting.context import current_attribution, usage_context
from forma.accounting.usage import UsageLedger, record_attempt, scope
from forma.settings.config import Settings


def config():
    return Settings(model="gpt-4.1-mini", api_key="private-attribution-key")


def usage():
    return {"usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def test_nested_context_and_parallel_operations_are_isolated():
    ledger, settings = UsageLedger(), config()
    ledger.summary(settings)  # Separate migration from concurrent record writes.
    barrier = threading.Barrier(2)
    identities = [(uuid4().hex, uuid4().hex) for _ in range(2)]

    def run(identity):
        run_id, document_id = identity
        with usage_context(run_id=run_id, document_id=document_id, stage="draft"):
            barrier.wait(timeout=5)
            ledger.record(settings, usage())
            with usage_context(stage="repair", reason="validation_retry"):
                ledger.record(settings, usage(), attempt=2, reason="transport_retry")
            assert current_attribution()["stage"] == "draft"
            ledger.record(settings, None, status=0)
        assert current_attribution()["run_id"] == ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, identities))
    for run_id, document_id in identities:
        summary = ledger.operation_summary(settings, run_id, document_id)
        assert summary["totals"]["requests"] == 3
        assert summary["totals"]["unknown"] == 1
        assert summary["totals"]["transport_retries"] == 1
        assert {row["stage"] for row in summary["stages"]} == {"draft", "repair"}
        assert ledger.operation_summary(replace(settings, api_key="other"), run_id)["totals"]["requests"] == 0
    assert ledger.summary(settings)["month"]["requests"] == 6


def test_attribution_rejects_arbitrary_metadata_and_credentials():
    ledger, settings = UsageLedger(), config()
    with usage_context(run_id="private-attribution-key", document_id="/private/document.docx",
                       stage="PRIVATE SOURCE", reason={"prompt": "PRIVATE CONTENT"}):
        record_attempt(settings, usage(), attempt=True)
    # Even an accidentally supplied key that looks like an ID is rejected.
    secret = "b" * 32
    with usage_context(run_id=secret, document_id=secret, stage="review"):
        ledger.record(replace(settings, api_key=secret), usage())
    with ledger.connect() as db:
        rows = db.execute("SELECT run_id,document_id,stage,attempt,reason FROM requests ORDER BY id").fetchall()
        assert tuple(rows[0]) == ("", "", "unknown", 0, "unknown")
        assert tuple(rows[1]) == ("", "", "review", 1, "initial")
    for forbidden in (settings.api_key, "/private/document.docx", "PRIVATE SOURCE", "PRIVATE CONTENT", secret):
        assert forbidden.encode() not in ledger.path.read_bytes()
    with pytest.raises(ValueError):
        ledger.operation_summary(settings, "filename.docx")


def test_v2_migration_preserves_money_and_keeps_old_requests_unattributed():
    settings, ledger = config(), UsageLedger()
    with sqlite3.connect(ledger.path) as db:
        db.executescript("""
          CREATE TABLE requests(id INTEGER PRIMARY KEY, account TEXT NOT NULL, at REAL NOT NULL,
            model TEXT NOT NULL, status INTEGER NOT NULL, input INTEGER, cached INTEGER,
            output INTEGER, cost INTEGER, price_source TEXT NOT NULL, cache_write INTEGER,
            write_long INTEGER, service_tier TEXT, uncertain INTEGER NOT NULL DEFAULT 0);
          CREATE INDEX requests_scope ON requests(account,at);
          CREATE TABLE tariffs(account TEXT, model TEXT, input TEXT, cached TEXT, output TEXT,
            PRIMARY KEY(account,model));
          CREATE TABLE balances(account TEXT PRIMARY KEY, amount INTEGER, after_id INTEGER, at REAL);
          PRAGMA user_version=2;
        """)
        db.execute("INSERT INTO requests VALUES(NULL,?,9999999999,?,200,100,0,10,56000,'manual',0,0,NULL,0)",
                   (scope(settings), settings.model))
        db.execute("INSERT INTO tariffs VALUES(?,?,?,?,?)", (scope(settings), settings.model, "1", ".5", "3"))
        db.execute("INSERT INTO balances VALUES(?,1000000000,0,1)", (scope(settings),))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: UsageLedger().summary(settings), range(8)))
    for summary in results:
        assert summary["today"]["cost"] == 56000
        assert summary["balance"]["remaining"] == 999944000
        assert summary["tariff"] == ["1", ".5", "3"]
        assert summary["stages"][0]["stage"] == "unknown"
    with ledger.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert tuple(db.execute("SELECT run_id,document_id,stage,attempt,reason FROM requests").fetchone()) == (
            "", "", "unknown", 0, "legacy",
        )
    # New attributed work neither adopts nor reprices old rows.
    run_id, document_id = uuid4().hex, uuid4().hex
    with usage_context(run_id=run_id, document_id=document_id, stage="review"):
        ledger.record(settings, usage())
    assert ledger.operation_summary(settings, run_id)["totals"]["requests"] == 1
    assert ledger.summary(settings)["month"]["cost"] == 186000


@pytest.mark.parametrize("memory", [False, True])
def test_office_worker_requests_join_report_ids_and_cache_reuse_is_free(docx_file, tmp_path, memory):
    from support import FakeProvider, local_settings

    from forma.core.models import Options
    from forma.memory.store import TranslationMemory
    from forma.translation.engine import TranslationEngine

    settings = local_settings().with_overrides(max_parallel_requests=4)
    options = Options(source_language="en", auto_glossary=not memory)
    if memory:
        project = TranslationMemory().create_project("Synthetic", "en", "uk")
        options = replace(options, memory_project_id=project.id)

    class AccountingProvider(FakeProvider):
        def complete(self, *args, **kwargs):
            record_attempt(settings, usage(), status=200)
            return super().complete(*args, **kwargs)

    first = TranslationEngine(settings, provider=AccountingProvider()).translate(docx_file, tmp_path / "out", options)
    metadata = json.loads(first.report.read_text())
    profile = metadata["translation_profile"]
    assert profile["model"] == settings.model
    assert profile["review_model"] == (settings.review_model or settings.model)
    assert "base_url" not in profile and "api_key" not in profile
    assert settings.base_url not in first.report.read_text()
    ledger = UsageLedger()
    operation = ledger.operation_summary(settings, metadata["usage_run_id"], metadata["usage_document_id"])
    assert operation["totals"]["requests"] > 1
    stages = {row["stage"] for row in operation["stages"]}
    assert {"draft", "review"}.issubset(stages)
    assert "unknown" not in stages
    assert ("glossary" in stages) is not memory
    second = TranslationEngine(settings, provider=AccountingProvider()).translate(docx_file, tmp_path / "out", options)
    assert second.usage_run_id != first.usage_run_id
    assert second.usage_document_id != first.usage_document_id
    assert ledger.operation_summary(settings, second.usage_run_id)["totals"]["requests"] == 0
