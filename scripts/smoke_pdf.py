"""Exercise the installed BabelDOC with a local fake API; no translation quality claim."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from forma.core.models import Options
from forma.documents.pdf import inspect_pdf, translate_pdf
from forma.providers.client import JobControl
from forma.settings.config import Settings

FIXTURE_TRANSLATIONS = {
    "Heat meter commissioning": "Введення теплолічильника в експлуатацію",
    "Technical brief / Device HM-200 / Revision 2.1": "Технічна довідка / Прилад HM-200 / Редакція 2.1",
    "Operating conditions": "Умови експлуатації",
    "The maximum operating pressure must not exceed 16 bar.": "Максимальний робочий тиск не повинен перевищувати 16 бар.",
    "Maintain a flow rate of 2.5 cubic metres per hour.": "Підтримуйте витрату 2.5 кубічних метрів на годину.",
    "Do not disconnect the sensor while the system is pressurized.": "Не від’єднуйте датчик, доки система перебуває під тиском.",
    "Measurement uncertainty: 2% at nominal operating conditions.": "Невизначеність вимірювання: 2% за номінальних умов експлуатації.",
    "Check all electrical connections before commissioning.": "Перед введенням в експлуатацію перевірте всі електричні з’єднання.",
    "Record the serial number and the initial reading.": "Запишіть серійний номер і початковий показ.",
    "NORTHLINE / Engineering documentation / Page 1": "NORTHLINE / Інженерна документація / Сторінка 1",
}


def fixture_translation(text):
    normalized = " ".join(text.split())
    if normalized not in FIXTURE_TRANSLATIONS:
        raise ValueError("This fake API supports only the technical-brief PDF fixture")
    return FIXTURE_TRANSLATIONS[normalized]


def synthetic_completion(request):
    """Honor BabelDOC's native batch/single formats using fixed fixture translations."""
    prompt = request["messages"][-1]["content"]
    batch_marker = "## Here is the input:"
    single_marker = "Now translate the following text:"
    if batch_marker in prompt:
        items = json.loads(prompt.rsplit(batch_marker, 1)[1].strip())
        return (
            json.dumps(
                [
                    {"id": item["id"], "output": fixture_translation(item["input"])}
                    for item in items
                ],
                ensure_ascii=False,
            ),
            "batch",
        )
    if single_marker in prompt:
        return fixture_translation(prompt.rsplit(single_marker, 1)[1].strip()), "single"
    if "expert multilingual terminologist" in prompt:
        return "[]", "glossary"
    raise ValueError("Unrecognized BabelDOC synthetic request format")


class LocalAPI(BaseHTTPRequestHandler):
    lock = threading.Lock()
    active = peak = calls = 0
    batch_calls = single_calls = glossary_calls = 0

    @classmethod
    def reset_stats(cls):
        with cls.lock:
            cls.active = cls.peak = cls.calls = 0
            cls.batch_calls = cls.single_calls = cls.glossary_calls = 0

    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            type(self).active += 1
            type(self).calls += 1
            type(self).peak = max(type(self).peak, type(self).active)
        # Make overlap visible without contacting a paid provider.
        time.sleep(0.35)
        try:
            content, kind = synthetic_completion(request)
            with self.lock:
                attribute = f"{kind}_calls"
                setattr(type(self), attribute, getattr(type(self), attribute) + 1)
            status = 200
            response = {
                "id": "local-smoke",
                "object": "chat.completion",
                "model": "local-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        except (KeyError, ValueError, TypeError):
            status, response = 400, {"error": {"message": "Unsupported synthetic fixture request"}}
        data = json.dumps(response, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            with self.lock:
                type(self).active -= 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=4)
    workers = parser.parse_args().workers
    source = Path(__file__).resolve().parents[1] / "examples/technical-brief.pdf"
    LocalAPI.reset_stats()
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    updates = []

    def progress(percent, message):
        updates.append((percent, message))
        print(f"{percent}% {message}", flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="forma-pdf-smoke-") as folder:
            result = translate_pdf(
                source,
                Path(folder),
                Options(source_language="en", review=False, auto_glossary=False),
                Settings(
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model=f"local-test-{uuid.uuid4().hex[:8]}",
                    api_key="local-test-key",
                    max_parallel_requests=workers,
                    parallel_documents=1,
                ),
                JobControl(max_parallel_requests=workers),
                progress,
            )
            assert result.output.is_file()
            assert any(8 < percent < 95 for percent, _ in updates), "No live engine progress"
            assert LocalAPI.calls > 0, "Smoke test unexpectedly used only cached translations"
            assert LocalAPI.batch_calls > 0, "Native PDF batch requests were not exercised"
            stats = json.loads(
                result.output.with_suffix(result.output.suffix + ".api.json").read_text()
            )
            assert stats["peak_concurrent_requests"] <= workers, "PDF request budget exceeded"
            text = "\n".join(page["text"] for page in inspect_pdf(result.output)["pages"])
            assert len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", text)) >= 150, "Output is not Ukrainian"
            assert all(value in text for value in ("HM-200", "16", "2.5", "2%"))
            print(
                f"Local fake API: {LocalAPI.calls} requests ({LocalAPI.batch_calls} batches, "
                f"{LocalAPI.single_calls} single paragraphs); "
                f"peak {stats['peak_concurrent_requests']}/{workers} concurrent; "
                f"{result.elapsed_seconds:.2f} seconds overall."
            )
            print(
                "PASS: real BabelDOC + fake local API. This checks integration, not translation quality."
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
