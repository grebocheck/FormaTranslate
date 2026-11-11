"""Shared offline providers and event-loop waits; never a collected test module."""
from __future__ import annotations

import copy
import time

from forma.core.models import Cancelled
from forma.providers.client import JobControl
from forma.settings.config import Settings


def pump(app, predicate, timeout=8):
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate(), "Desktop operation did not finish"


def settle(app, backend, timeout=5):
    pump(app, lambda: not backend._threads, timeout=timeout)


class FakeProvider:
    input_tokens = 120
    output_tokens = 80

    def __init__(self, *, invalid=False, interrupt_after=None, control=None):
        self.calls = []
        self.invalid = invalid
        self.interrupt_after = interrupt_after
        self.control = control

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append((instruction, payload))
        if self.interrupt_after is not None and len(self.calls) > self.interrupt_after:
            raise Cancelled("Interrupted")
        if "terms" in schema["properties"]:
            return {"terms": [{"source": "heat meter", "target": "лічильник тепла"}]}
        segments = [
            {
                "id": s["id"],
                "text": s["text"]
                .replace("Heat meter", "Лічильник тепла")
                .replace("sensor", "датчик"),
            }
            for s in payload["segments"]
        ]
        if self.invalid:
            for segment in segments:
                segment["text"] += "<r999>hallucination 999</r999>"
        return {"segments": segments}



def local_settings():
    return Settings(
        base_url="http://localhost/v1",
        model="test-model",
        max_parallel_requests=1,
        parallel_documents=1,
    )



class TextProvider:
    def __init__(self, transform=None, *, review_model=""):
        self.control = JobControl()
        self.settings = Settings(model="translator", review_model=review_model)
        self.calls = []
        self.transform = transform

    def complete(self, instruction, payload, schema, **kwargs):
        self.calls.append((instruction, copy.deepcopy(payload), kwargs))
        segments = [
            {"id": item["id"], "text": item.get("draft", item.get("text", ""))}
            for item in payload["segments"]
        ]
        if self.transform:
            return self.transform(segments, len(self.calls))
        for segment in segments:
            segment["text"] = segment["text"].replace("Hello", "Вітаю").replace("world", "світ")
        return {"segments": segments}

