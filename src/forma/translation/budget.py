"""Application request envelopes, independent of any model's tokenization."""

from __future__ import annotations

import json

from forma.core.models import Issue, TranslationFailure
from forma.translation.fragments import FragmentError, fragment_plan

STAGE_INPUT_BYTES = 64 * 1024


def stage_input_size(instruction, payload, schema):
    """Count the serialized stage inputs, not tokens or an HTTP/model limit.

    Include shared context, drafts, references and repair feedback as actually
    supplied by the stage. Native wire envelopes can differ from this envelope.
    """
    return len(json.dumps(
        {"instruction": instruction, "payload": payload, "schema": schema}, ensure_ascii=False,
    ).encode("utf-8"))


def validate_segment_sizes(segments):
    """Plan supported paragraphs; reject unbroken oversized atoms before paying."""
    plans, oversized = {}, []
    for segment in segments:
        try:
            plan = fragment_plan(segment)
        except FragmentError:
            oversized.append(segment)
        else:
            if plan is not None:
                plans[segment.id] = plan
    if not oversized:
        return plans
    issues = [Issue(
        "request_size",
        f"Символів тексту: {len(segment.plain)}; символів розмітки: {len(segment.source)}; "
        f"позначок форматування: {segment.source.count('<r') + segment.source.count('<k')}.",
        segment.id, "error",
    ) for segment in oversized]
    raise TranslationFailure(
        "Фрагмент завеликий для одного запиту. Перегляньте звіт про проблемні фрагменти.",
        oversized, {}, issues, stage="preflight",
    )
