"""Offline Office-pipeline research: concurrency, ordering, repair and resume.

The HTTP transport is an in-process simulator. This measures pipeline correctness
and request work, not language quality, model speed, real tokens or API prices.
Run before/after a change with separate --label values to preserve the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Font

from forma.core.models import Cancelled, FormaError, Options
from forma.documents.comparison import compare_files
from forma.documents.ooxml import OfficeDocument
from forma.providers.client import JobControl, Provider
from forma.settings.config import Settings
from forma.translation.engine import TranslationEngine


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def isolated_cache(path: Path):
    prior = os.environ.get("FORMA_DATA_DIR")
    os.environ["FORMA_DATA_DIR"] = str(path)
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("FORMA_DATA_DIR", None)
        else:
            os.environ["FORMA_DATA_DIR"] = prior


def fixtures(folder: Path) -> list[Path]:
    folder.mkdir(parents=True)
    docx = folder / "technical.uk.docx"
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Технічний звіт"
    doc.sections[0].footer.paragraphs[0].text = "Контрольний примірник"
    for index in range(56):
        paragraph = doc.add_paragraph()
        paragraph.add_run(f"Показник {index}: ").bold = index % 2 == 0
        paragraph.add_run("насос ТН-42; значення 1,25 бар.")
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Shading Accent 1"
    for cell, value in zip(table.rows[0].cells, ("Показник", "Значення", "Одиниця"), strict=True):
        cell.text = value
    for index in range(4):
        for cell, value in zip(
            table.add_row().cells, (f"Тиск {index}", "1,25", "бар"), strict=True
        ):
            cell.text = value
    doc.save(docx)
    xlsx = folder / "readings.uk.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Вимірювання"
    sheet.append(("Показник", "Значення", "Одиниця"))
    for index in range(32):
        sheet.append((f"Тиск {index}", 1.25 + index, "бар"))
        sheet.cell(index + 2, 1).font = Font(bold=index % 2 == 0)
        sheet.cell(index + 2, 2).number_format = "0.00"
    sheet["B35"] = "=SUM(B2:B33)"
    sheet.column_dimensions["A"].width = 40
    workbook.save(xlsx)
    return [docx, xlsx]


def translated(text: str) -> str:
    for original, target in {
        "Технічний звіт": "Technical report",
        "Контрольний примірник": "Controlled copy",
        "Показник": "Indicator",
        "Значення": "Value",
        "Одиниця": "Unit",
        "насос": "pump",
        "значення": "value",
        "Тиск": "Pressure",
        "бар": "bar",
    }.items():
        text = text.replace(original, target)
    return text


class SimulatedAPI:
    def __init__(self, control: JobControl, *, fault: str = "", cancel_stage: str = ""):
        self.control, self.fault, self.cancel_stage = control, fault, cancel_stage
        self.lock = threading.Lock()
        self.active = self.peak = self.started_after_cancel = 0
        self.fault_used = self.cancel_used = False
        self.requests = []
        self.response_order = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        segments = payload.get("segments", [])
        stage = "glossary" if not segments else "review" if "drafts" in payload else "translation"
        with self.lock:
            ordinal = len(self.requests)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started_after_cancel += int(self.control.cancelled.is_set())
            broken = (
                stage == "review"
                and self.fault
                and (not self.fault_used or self.fault.endswith("always"))
            )
            self.fault_used |= bool(broken)
            self.requests.append(
                {
                    "stage": stage,
                    "ids": [s["id"] for s in segments],
                    "repair": bool(payload.get("validation_errors")),
                    "payload_bytes": len(request.content),
                    "document_context_chars": len(payload.get("document_context", "")),
                    "local_context_chars": sum(
                        len(s.get("neighboring_context", "")) for s in segments
                    ),
                }
            )
        try:
            # Unequal response times and reversed ID order exercise independent completion.
            time.sleep(0.006 * (4 - ordinal % 4))
            values = [{"id": s["id"], "text": translated(s["text"])} for s in segments]
            if broken and values:
                if self.fault == "missing_once":
                    values.pop()
                elif self.fault == "duplicate_once":
                    values.append(dict(values[0]))
                elif self.fault.startswith("numeric"):
                    values[0]["text"] = values[0]["text"].replace("</r0>", " 999</r0>", 1)
                elif self.fault.startswith("markup"):
                    values[0]["text"] = values[0]["text"].replace("</r0>", "", 1)
            data = {"segments": list(reversed(values))} if segments else {"terms": []}
            with self.lock:
                self.response_order.append(ordinal)
                if stage == self.cancel_stage and not self.cancel_used:
                    self.cancel_used = True
                    self.control.cancelled.set()
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(data)}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 100 + 20 * len(segments),
                        "completion_tokens": 15 * len(values),
                    },
                },
            )
        finally:
            with self.lock:
                self.active -= 1


def execute(
    source: Path, root: Path, *, limit=1, fault="", cancel_stage="", cache=None, glossary=True
):
    control = JobControl(limit)
    api = SimulatedAPI(control, fault=fault, cancel_stage=cancel_stage)
    settings = Settings(
        base_url="http://offline.invalid/v1",
        model="offline-pipeline-research",
        max_parallel_requests=limit,
        parallel_documents=1,
    )
    provider = Provider(settings, control, transport=httpx.MockTransport(api))
    options = Options(
        source_language="uk", target_language="en", review=True, auto_glossary=glossary
    )
    cache = cache or root / "cache"
    record = {"request_limit": limit, "fault": fault, "cancel_stage": cancel_stage}
    started = time.monotonic()
    try:
        with isolated_cache(cache):
            result = TranslationEngine(settings, control, provider=provider).translate(
                source, root, options
            )
        metrics = compare_files(source, [result.output])["candidates"][0]
        record.update(
            {
                "status": "completed",
                "segments": len(result.segments),
                "cached_segments": result.cached_segments,
                "output": str(result.output),
                "output_sha256": hashlib.sha256(result.output.read_bytes()).hexdigest(),
                "checks": {
                    key: metrics[key]
                    for key in ("structure_ok", "numbers_preserved", "formulas_preserved")
                },
                "issues": [asdict(issue) for issue in result.issues],
            }
        )
    except Cancelled:
        record["status"] = "cancelled"
    except FormaError as exc:
        record.update(status="rejected", error=str(exc))
    finally:
        provider.close()
    checkpoints = list((cache / "checkpoints").glob("*.json"))
    state = json.loads(checkpoints[0].read_text()) if checkpoints else {}
    record.update(
        {
            "elapsed_seconds": round(time.monotonic() - started, 4),
            "requests": len(api.requests),
            "segments_sent": sum(len(r["ids"]) for r in api.requests),
            "repair_segments": sum(len(r["ids"]) for r in api.requests if r["repair"]),
            "payload_bytes": sum(r["payload_bytes"] for r in api.requests),
            "document_context_chars_sent": sum(r["document_context_chars"] for r in api.requests),
            "peak_requests": api.peak,
            "started_after_cancel": api.started_after_cancel,
            "response_order": api.response_order,
            "simulated_reported_input_tokens": provider.input_tokens,
            "simulated_reported_output_tokens": provider.output_tokens,
            "checkpoint_final_segments": len(state.get("translations", {})),
            "checkpoint_draft_segments": len(state.get("drafts", {})),
            "request_log": api.requests,
        }
    )
    write_json(root / "case.json", record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run")
    parser.add_argument("--output", type=Path, default=Path("output/research"))
    args = parser.parse_args()
    label = "".join(c for c in args.label if c.isalnum() or c in "-_")[:60] or "run"
    root = args.output / f"pipeline-{label}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:6]}"
    paths = fixtures(root / "input")
    report = {
        "label": args.label,
        "note": "Offline deterministic simulator, no network/API calls. Timing uses synthetic staggered latency. Token usage is simulated, not real model tokenization or cost. These checks do not measure linguistic quality.",
        "concurrency": [],
        "cache_copies": [],
        "faults": [],
        "cancellation": [],
    }
    for source in paths:
        kind = source.suffix[1:]
        expected_hash = None
        for limit in (1, 2, 4, 8):
            result = execute(source, root / kind / f"parallel-{limit}", limit=limit)
            expected_hash = expected_hash or result.get("output_sha256")
            result["identical_to_serial_output"] = result.get("output_sha256") == expected_hash
            report["concurrency"].append({"format": kind, **result})
        copy = source.with_name("duplicate" + source.suffix)
        shutil.copyfile(source, copy)
        report["cache_copies"].append(
            {
                "format": kind,
                **execute(
                    copy,
                    root / kind / "duplicate-copy",
                    limit=4,
                    cache=root / kind / "parallel-4/cache",
                ),
            }
        )
        for fault in (
            "missing_once",
            "duplicate_once",
            "numeric_once",
            "markup_once",
            "numeric_always",
            "markup_always",
        ):
            report["faults"].append(
                {
                    "format": kind,
                    **execute(source, root / kind / fault, fault=fault, glossary=False),
                }
            )
        for stage, limit in (("translation", 1), ("review", 1), ("review", 4)):
            folder = root / kind / f"cancel-{stage}-{limit}"
            interrupted = execute(
                source, folder / "interrupted", limit=limit, cancel_stage=stage, glossary=False
            )
            resumed = execute(
                source,
                folder / "resumed",
                limit=limit,
                cache=folder / "interrupted/cache",
                glossary=False,
            )
            report["cancellation"].append(
                {
                    "format": kind,
                    "stage": stage,
                    "limit": limit,
                    "interrupted": interrupted,
                    "resumed": resumed,
                }
            )
    report["source_segments"] = {p.name: len(OfficeDocument(p).segments) for p in paths}
    write_json(root / "summary.json", report)
    print(root / "summary.json")


if __name__ == "__main__":
    main()
