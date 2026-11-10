"""Measure queue concurrency against a local delayed API, without paid calls.

uv run python scripts/benchmark_parallel.py
This tests scheduling and document integrity, not model/translation quality.
"""

from __future__ import annotations

import json
import os
import runpy
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from forma.comparison import compare_files
from forma.config import Settings
from forma.jobs import run_documents
from forma.models import Options
from forma.provider import JobControl

ROOT = Path(__file__).resolve().parents[1]


class LocalAPI(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][1][
                "content"
            ]
        )
        with self.server.stats_lock:
            self.server.active += 1
            self.server.requests += 1
            self.server.peak = max(self.server.peak, self.server.active)
        try:
            time.sleep(0.1)
            translated = {
                "segments": [
                    {
                        "id": s["id"],
                        "text": s["text"]
                        .replace("Heat meter", "Wärmezähler")
                        .replace("reading", "Messwert"),
                    }
                    for s in payload["segments"]
                ]
            }
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(translated)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 60},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with self.server.stats_lock:
                self.server.active -= 1


def make_documents(folder):
    folder.mkdir(parents=True, exist_ok=True)
    generate = runpy.run_path(str(ROOT / "scripts/make_examples.py"))["make_docx"]
    template = folder / "template.docx"
    generate(template)
    with ZipFile(template) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    template.unlink()
    documents = []
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for index in range(6):
        tree = etree.fromstring(parts["word/document.xml"])
        body = tree.find(namespace + "body")
        for child in list(body):
            if child.tag != namespace + "sectPr":
                body.remove(child)
        for number in range(40):
            paragraph = etree.Element(namespace + "p")
            run = etree.SubElement(paragraph, namespace + "r")
            etree.SubElement(
                run, namespace + "t"
            ).text = f"Heat meter {index + 1}, reading {number + 1}: 16 bar."
            body.insert(number, paragraph)
        path = folder / f"meters-{index + 1}.docx"
        with ZipFile(path, "w") as archive:
            for name, data in parts.items():
                archive.writestr(
                    name, etree.tostring(tree) if name == "word/document.xml" else data
                )
        documents.append({"id": str(index), "path": str(path)})
    return documents


def main():
    output = ROOT / "output/performance"
    documents = make_documents(output / "input")
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
    server.daemon_threads = True
    server.stats_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous_data = os.environ.get("FORMA_DATA_DIR")
    measurements = []
    try:
        for label, documents_at_once, requests_at_once in [
            ("sequential", 1, 1),
            ("parallel", 2, 4),
        ]:
            server.active = server.peak = server.requests = 0
            events = []
            settings = Settings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="local-benchmark",
                parallel_documents=documents_at_once,
                max_parallel_requests=requests_at_once,
            )
            with tempfile.TemporaryDirectory(prefix="forma-benchmark-") as cache:
                os.environ["FORMA_DATA_DIR"] = cache
                start = time.perf_counter()
                run_documents(
                    documents,
                    output / label,
                    Options("en", "de", review=True, auto_glossary=False),
                    settings,
                    JobControl(max_parallel_requests=requests_at_once),
                    lambda *event, events=events: events.append(event),
                )
                elapsed = time.perf_counter() - start
            results = [payload["result"] for kind, payload in events if kind == "result"]
            assert len(results) == len(documents), [p for k, p in events if k == "file_error"]
            assert server.peak <= requests_at_once
            assert all(
                compare_files(r.source, [r.output])["candidates"][0]["structure_ok"]
                for r in results
            )
            measurements.append(
                {
                    "scenario": label,
                    "seconds": round(elapsed, 3),
                    "documents": len(results),
                    "requests": server.requests,
                    "peak_active_requests": server.peak,
                    "total_tokens": sum(r.input_tokens + r.output_tokens for r in results),
                    "structure_ok": True,
                }
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if previous_data is None:
            os.environ.pop("FORMA_DATA_DIR", None)
        else:
            os.environ["FORMA_DATA_DIR"] = previous_data
    assert measurements[0]["requests"] == measurements[1]["requests"]
    assert measurements[0]["total_tokens"] == measurements[1]["total_tokens"]
    report = {
        "scope": "Six synthetic DOCX files, 40 paragraphs each, review enabled, local API with 100 ms delay; not a real model speed or language quality benchmark.",
        "measurements": measurements,
        "speedup": round(measurements[0]["seconds"] / measurements[1]["seconds"], 2),
    }
    (output / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
