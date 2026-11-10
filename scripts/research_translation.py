"""Compare reasoning effort on synthetic DOCX/XLSX with the configured model.

Default: prepare fixtures only. --live explicitly enables billable requests.
Drafts and edited translations come from the same pipeline run, not separate
generations. Structural checks are separate from the linguistic review rubric.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import runpy
import threading
from datetime import UTC, datetime
from pathlib import Path

from forma.comparison import compare_files
from forma.config import Settings
from forma.engine import TranslationEngine
from forma.models import FormaError, Options, plain_text
from forma.provider import Provider
from forma.reports import document_html
from forma.storage import atomic_bytes, atomic_json

ROOT = Path(__file__).resolve().parents[1]


class LimitedProvider(Provider):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = 0
        self.calls_lock = threading.Lock()

    def complete(self, *args, **kwargs):
        with self.calls_lock:
            if self.calls >= 16:
                raise FormaError("Research request budget reached; inspect saved results.")
            self.calls += 1
        return super().complete(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    helpers = runpy.run_path(str(ROOT / "scripts/check_quality.py"))
    folder = ROOT / "output/research/language"
    paths, manifest = helpers["prepare_fixtures"](folder / "input")
    if not args.live:
        print(f"Prepared {len(paths)} synthetic documents in {folder / 'input'}; no API calls.")
        return
    settings = Settings.load()
    settings.validate()
    # Keep the configured model. A small finite output/request budget bounds this
    # investigation without mutating the user's saved connection or preferences.
    settings = settings.with_overrides(
        review_model="",
        max_output_tokens=4096,
        timeout=min(settings.timeout, 90),
        parallel_documents=1,
        max_parallel_requests=min(settings.max_parallel_requests, 4),
    )
    run = folder / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir(parents=True, exist_ok=False)
    summary = {
        "model": settings.model,
        "dataset": manifest,
        "note": "Two synthetic files, one run per effort, same model and prompts; latency is observational, not a controlled production benchmark. Linguistic judgments require reviewing the bilingual report; structural success is not a language-quality score.",
        "source": "https://developers.openai.com/api/docs/guides/evaluation-best-practices",
        "documents": [],
    }
    previous_data = os.environ.get("FORMA_DATA_DIR")
    options = Options("uk", "en", "Технічна та ділова документація", True, True)
    try:
        for effort in ("default", "low", "none"):
            for source in paths:
                case = run / effort / source.stem
                case.mkdir(parents=True)
                os.environ["FORMA_DATA_DIR"] = str(case / "cache")
                provider = LimitedProvider(
                    settings.with_overrides(reasoning_effort="" if effort == "default" else effort)
                )
                record = {"source": source.name, "reasoning_effort": effort, "status": "failed"}
                try:
                    result = TranslationEngine(provider.settings, provider=provider).translate(
                        source, case, options
                    )
                    metrics = compare_files(source, [result.output])["candidates"][0]
                    states = list((case / "cache/checkpoints").glob("*.json"))
                    assert len(states) == 1
                    drafts = json.loads(states[0].read_text())["drafts"]
                    pairs = [
                        {
                            "id": segment.id,
                            "location": segment.location,
                            "source": segment.plain,
                            "draft": plain_text(drafts.get(segment.id, "")),
                            "reviewed": plain_text(result.translations[segment.id]),
                        }
                        for segment in result.segments
                    ]
                    atomic_json(case / "pairs.json", pairs)
                    rows = "".join(
                        "<tr>"
                        + "".join(
                            f"<td>{html.escape(pair[key])}</td>"
                            for key in ("source", "draft", "reviewed")
                        )
                        + "</tr>"
                        for pair in pairs
                    )
                    rubric = "".join(
                        f"<li>{html.escape(item)}</li>"
                        for item in manifest["human_review_checklist"]
                    )
                    atomic_bytes(
                        case / "review.html",
                        document_html(
                            "Forma — оцінювання перекладу",
                            f"<h1>{html.escape(source.name)} · {effort}</h1><ul>{rubric}</ul><table><tr><th>Оригінал</th><th>Чернетка</th><th>Після редактури</th></tr>{rows}</table>",
                        ),
                    )
                    record.update(
                        status="completed",
                        elapsed_seconds=round(result.elapsed_seconds, 3),
                        output=str(result.output.relative_to(run)),
                        review=str((case / "review.html").relative_to(run)),
                        segments=len(pairs),
                        edited_segments=sum(p["draft"] != p["reviewed"] for p in pairs),
                        structural_checks={
                            k: metrics[k]
                            for k in ("structure_ok", "numbers_preserved", "formulas_preserved")
                        },
                        input_tokens=provider.input_tokens,
                        output_tokens=provider.output_tokens,
                        reasoning_tokens=sum(r["reasoning_tokens"] for r in provider.usage_records),
                        calls=provider.calls,
                        usage=provider.usage_records,
                    )
                except (FormaError, OSError) as exc:
                    record["error"] = (
                        str(exc).replace(settings.api_key, "[redacted]")
                        if settings.api_key
                        else str(exc)
                    )
                finally:
                    provider.close()
                    summary["documents"].append(record)
                    atomic_json(run / "summary.json", summary)
                print(
                    json.dumps(
                        {k: v for k, v in record.items() if k not in ("usage", "output", "review")},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        if previous_data is None:
            os.environ.pop("FORMA_DATA_DIR", None)
        else:
            os.environ["FORMA_DATA_DIR"] = previous_data
    print(f"Results: {run / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
