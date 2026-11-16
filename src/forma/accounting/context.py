"""Private request attribution, independent of prompts and provider protocols.

Only opaque operation IDs and a bounded vocabulary may enter the ledger. A
context belongs to the calling thread/task; batch workers establish their own
scope at the actual request boundary rather than sharing mutable provider state.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

STAGES = frozenset({
    "draft", "review", "revision", "repair", "glossary", "photo", "text", "pdf",
    "connection", "unknown",
})
REASONS = frozenset({
    "initial", "validation_retry", "targeted_revision", "transport_retry",
    "format_retry", "capability_retry", "unknown", "legacy",
})
_CONTEXT = ContextVar("forma_usage_context", default=None)


def opaque_id(value) -> str:
    """Accept generated UUID hex IDs, never filenames, content or account keys."""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else ""


def _choice(value, allowed, fallback="unknown"):
    return value if isinstance(value, str) and value in allowed else fallback


def current_attribution(*, attempt=1, reason=None) -> dict:
    value = _CONTEXT.get() or {}
    selected_reason = value.get("reason", "initial") if reason is None else reason
    return {
        "run_id": opaque_id(value.get("run_id")),
        "document_id": opaque_id(value.get("document_id")),
        "stage": _choice(value.get("stage"), STAGES),
        "reason": _choice(selected_reason, REASONS),
        "attempt": attempt if type(attempt) is int and 1 <= attempt <= 100 else 0,
    }


@contextmanager
def usage_context(*, run_id=None, document_id=None, stage=None, reason=None):
    """Temporarily bind safe metadata; nested scopes retain unspecified fields."""
    previous = _CONTEXT.get() or {}
    values = dict(previous)
    for name, value in (("run_id", run_id), ("document_id", document_id),
                        ("stage", stage), ("reason", reason)):
        if value is not None:
            values[name] = value
    # Sanitize before making metadata available to any consumer.
    values["run_id"] = opaque_id(values.get("run_id"))
    values["document_id"] = opaque_id(values.get("document_id"))
    values["stage"] = _choice(values.get("stage"), STAGES)
    values["reason"] = _choice(values.get("reason", "initial"), REASONS)
    token = _CONTEXT.set(values)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def usage_operation(function):
    """Give each public translation invocation independent, opaque ledger IDs."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with usage_context(run_id=uuid4().hex, document_id=uuid4().hex,
                           stage="unknown", reason="initial"):
            return function(*args, **kwargs)
    return wrapped


def operation_ids() -> dict:
    """Public report IDs allow local cost lookup without filenames in the ledger."""
    context = current_attribution()
    return {"usage_run_id": context["run_id"], "usage_document_id": context["document_id"]}
