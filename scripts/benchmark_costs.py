"""Compare recorded runs without contacting a provider or loading credentials.

Example: uv run python scripts/benchmark_costs.py cold.forma.json warm.forma.json
         --output output/cost-comparison.json

Run translations separately with the chosen settings and review their quality.
This command measures existing ledger records, not linguistic accuracy.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from forma.accounting.context import opaque_id
from forma.settings.config import data_directory

MAX_REPORT_BYTES = 64 * 1024 * 1024


def compare_recorded_costs(reports, ledger_path):
    result = {
        "paid_api_calls": 0, "database_read_only": True, "runs": [],
        "limits": [
            "Local estimates are not provider invoices or linguistic quality scores.",
            "Missing ledger rows do not prove a free run; compare report token counts and reuse.",
            "Historic unattributed requests cannot be assigned to a report retroactively.",
            "Changing source, instructions, glossary, model or cache state changes the comparison.",
        ],
    }
    path = Path(ledger_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise ValueError("The ledger has no supported operation attribution")
        seen = set()
        for index, report in enumerate(reports, 1):
            with Path(report).open("rb") as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError("Report exceeds the supported size")
            metadata = json.loads(raw)
            run_id, document_id = metadata.get("usage_run_id"), metadata.get("usage_document_id")
            if not opaque_id(run_id) or not opaque_id(document_id):
                raise ValueError("A report has no valid operation identifiers")
            identity = run_id, document_id
            if identity in seen:
                raise ValueError("The same operation appears more than once")
            seen.add(identity)
            accounts = db.execute(
                "SELECT COUNT(DISTINCT account) FROM requests WHERE run_id=? AND document_id=?", identity,
            ).fetchone()[0]
            if accounts > 1:
                raise ValueError("Operation identifiers span multiple accounts")
            stages = [dict(row) for row in db.execute(
                "SELECT stage,COUNT(*) requests,COALESCE(SUM(input),0) input_tokens,"
                "COALESCE(SUM(cached),0) cached_tokens,COALESCE(SUM(cache_write),0) write_tokens,"
                "COALESCE(SUM(output),0) output_tokens,COALESCE(SUM(cost),0) estimated_nanodollars,"
                "SUM(cost IS NULL) unpriced,SUM(uncertain>0) uncertain "
                "FROM requests WHERE run_id=? AND document_id=? GROUP BY stage ORDER BY stage", identity,
            )]
            fields = ("requests", "input_tokens", "cached_tokens", "write_tokens", "output_tokens",
                      "estimated_nanodollars", "unpriced", "uncertain")
            totals = {field: sum(stage[field] for stage in stages) for field in fields}
            totals["estimated_usd"] = totals["estimated_nanodollars"] / 1_000_000_000
            totals["cost_status"] = (
                "no_recorded_requests" if not totals["requests"] else
                "incomplete" if totals["unpriced"] or totals["uncertain"] else "estimated"
            )
            # Only bounded numbers leave a report; no document text, filenames,
            # settings, user instructions, glossary or credentials are copied.
            counts = {}
            for field in ("input_tokens", "output_tokens", "cached_segments", "memory_segments",
                          "repeated_segments", "draft_memory_segments", "adapted_segments"):
                value = metadata.get(field)
                counts[field] = value if type(value) is int and value >= 0 else None
            result["runs"].append({"case": index, "usage_run_id": run_id,
                                   "usage_document_id": document_id, "report_counts": counts,
                                   "recorded_totals": totals, "stages": stages})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--ledger", type=Path, default=data_directory(create=False) / "usage.sqlite3")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare_recorded_costs(args.reports, args.ledger)
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError) as exc:
        parser.error(f"Could not compare recorded runs ({type(exc).__name__}); check the reports and ledger")
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        protected = [args.ledger.resolve(), *(path.resolve() for path in args.reports)]
        if args.output.resolve() in protected:
            parser.error("The output must not overwrite a report or the ledger")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
